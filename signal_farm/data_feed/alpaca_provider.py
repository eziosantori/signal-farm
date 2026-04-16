"""
AlpacaProvider — DataProvider implementation backed by Alpaca Markets API.

Uses alpaca-py (StockHistoricalDataClient) to fetch US stock OHLCV data.
Data is cached locally as Parquet files to avoid repeated API calls:
    .alpaca_data/<SYMBOL>/<TIMEFRAME>.parquet

Fetch strategy (applied per symbol/interval on each get_ohlcv() call):

  FULL         — download full period from Alpaca, overwrite cache.
                 Triggered by: no cache, coverage miss, gap > 1 day,
                 new calendar day (UTC), or force_refresh=True.

  INCREMENTAL  — fetch only bars newer than the last cached bar and append.
                 Triggered when: gap >= interval + buffer AND no FULL condition.

  USE_CACHE    — return cached data as-is (no Alpaca call).
                 Triggered when gap < interval + buffer (new bar not yet available).

Credentials are read from environment variables:
    ALPACA_API_KEY      — Alpaca API key ID
    ALPACA_SECRET_KEY   — Alpaca API secret key

Free-tier accounts use the IEX feed (prices accurate, volume ~30% of true).
Paper/live accounts use the SIP feed (full consolidated tape).

Usage
-----
    provider = AlpacaProvider()
    df = provider.get_ohlcv("AAPL", "30m", "2y")
    df = provider.get_ohlcv("MSFT", "1d",  "5y")
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from data_feed.provider import DataProvider, DataUnavailableError

logger = logging.getLogger(__name__)

# Cache directory (relative to project root, sibling of signal_farm/)
_DEFAULT_DATA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", ".alpaca_data"
)

# Period string → timedelta (same mapping as DukascopyProvider)
_PERIOD_MAP: dict[str, timedelta] = {
    "7d":   timedelta(days=7),
    "14d":  timedelta(days=14),
    "30d":  timedelta(days=30),
    "60d":  timedelta(days=60),
    "90d":  timedelta(days=90),
    "120d": timedelta(days=120),
    "180d": timedelta(days=180),
    "200d": timedelta(days=200),
    "1y":   timedelta(days=365),
    "2y":   timedelta(days=730),
    "3y":   timedelta(days=1095),
    "5y":   timedelta(days=1825),
}

# Interval string → (TimeFrame multiplier, unit string)
_INTERVAL_MAP = {
    "1m":  (1,  "Minute"),
    "5m":  (5,  "Minute"),
    "15m": (15, "Minute"),
    "30m": (30, "Minute"),
    "1h":  (1,  "Hour"),
    "4h":  (4,  "Hour"),
    "1d":  (1,  "Day"),
}

# Bar duration in minutes — used to decide when a new bar should be available
_INTERVAL_MINUTES: dict[str, int] = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440,
}

# Extra buffer (minutes) added to bar duration before attempting an incremental fetch.
# Accounts for Alpaca ingestion latency after bar close.
_FETCH_BUFFER_MINUTES: dict[str, int] = {
    "1m":  1,
    "5m":  1,
    "15m": 2,
    "30m": 2,
    "1h":  3,
    "4h":  5,
    "1d":  30,   # daily bar fully available ~30 min after US market close
}

# If the gap between the last cached bar and now exceeds this, force a full re-fetch.
# 1440 min = 1 day: handles PC-off-overnight / multi-day downtime scenarios.
_FULL_REFRESH_GAP_MINUTES = 1440


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _is_new_trading_day(last_ts: datetime, now: datetime) -> bool:
    """True if last_ts and now fall on different UTC calendar days."""
    return last_ts.date() < now.date()


def _build_timeframe(interval: str):
    """Convert our interval string to an alpaca-py TimeFrame object."""
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    unit_map = {
        "Minute": TimeFrameUnit.Minute,
        "Hour":   TimeFrameUnit.Hour,
        "Day":    TimeFrameUnit.Day,
    }
    if interval not in _INTERVAL_MAP:
        raise DataUnavailableError(
            f"Alpaca: unsupported interval '{interval}'. "
            f"Supported: {list(_INTERVAL_MAP)}"
        )
    mult, unit_str = _INTERVAL_MAP[interval]
    return TimeFrame(mult, unit_map[unit_str])


class AlpacaProvider(DataProvider):
    """
    DataProvider backed by the Alpaca Markets historical data API.

    Supports US equities at any interval in _INTERVAL_MAP.
    Data is cached as Parquet files and updated incrementally on each call
    once the current bar has closed (interval + buffer elapsed).

    Parameters
    ----------
    api_key       : Alpaca API key (default: ALPACA_API_KEY env var)
    secret_key    : Alpaca secret key (default: ALPACA_SECRET_KEY env var)
    data_dir      : Cache root directory
    feed          : "iex" (free tier) or "sip" (paid/paper)
    force_refresh : If True, bypass cache and always do a full fetch
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        data_dir: str = _DEFAULT_DATA_DIR,
        feed: Optional[str] = None,
        force_refresh: bool = False,
    ):
        self._api_key    = api_key    or os.environ.get("ALPACA_API_KEY", "")
        self._secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY", "")
        self._data_dir   = os.path.abspath(data_dir)
        self._feed       = feed or os.environ.get("ALPACA_FEED", "iex")
        self._force      = force_refresh
        self._client     = None   # lazy init

        if not self._api_key or not self._secret_key:
            raise DataUnavailableError(
                "Alpaca credentials not found. Set ALPACA_API_KEY and "
                "ALPACA_SECRET_KEY environment variables, or pass them directly."
            )

    # ── Public API ────────────────────────────────────────────────────────────

    def get_ohlcv(self, ticker: str, interval: str, period: str) -> pd.DataFrame:
        """
        Fetch OHLCV for `ticker` at `interval` for `period` lookback.

        Returns DataFrame with columns [open, high, low, close, volume]
        and a UTC DatetimeIndex. Uses smart caching:

          - First call of the day → full fetch (clean slate)
          - Subsequent calls → incremental fetch of new bars only
          - No new bar expected yet → returns cache as-is (no API call)
          - Gap > 1 day or coverage miss → full fetch
        """
        symbol     = ticker.upper()
        cache_path = self._cache_path(symbol, interval)

        # force_refresh bypasses all cache logic
        if self._force:
            return self._full_fetch_and_cache(symbol, interval, period, cache_path)

        cached_df = self._load_cache_raw(cache_path)

        # No cache at all → full fetch
        if cached_df is None:
            logger.info("AlpacaProvider: no cache for %s/%s → full fetch", symbol, interval)
            return self._full_fetch_and_cache(symbol, interval, period, cache_path)

        # Coverage miss: cache exists but doesn't reach back far enough → full fetch
        if not self._cache_covers_period(cached_df, period):
            return self._full_fetch_and_cache(symbol, interval, period, cache_path)

        # Decide fetch strategy based on gap and calendar day
        strategy = self._fetch_strategy(cached_df, interval)

        if strategy == "FULL":
            return self._full_fetch_and_cache(symbol, interval, period, cache_path)

        if strategy == "INCREMENTAL":
            last_ts = cached_df.index.max()
            new_df  = self._fetch_since(symbol, interval, since=last_ts)
            if not new_df.empty:
                merged    = pd.concat([cached_df, new_df]).sort_index()
                # Deduplicate on index (timestamp), not on column values.
                # keep="last" ensures incremental bars overwrite stale cache rows
                # if the same bar appears in both (e.g. partial-close overlap).
                cached_df = merged[~merged.index.duplicated(keep="last")]
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                cached_df.to_parquet(cache_path)
                logger.info(
                    "AlpacaProvider: incremental %s/%s +%d bars → %d total",
                    symbol, interval, len(new_df), len(cached_df),
                )
            else:
                logger.debug("AlpacaProvider: incremental %s/%s — no new bars", symbol, interval)

        else:  # USE_CACHE
            logger.debug("AlpacaProvider: cache hit %s/%s (bar not yet closed)", symbol, interval)

        # Trim to requested period and return
        return self._trim_to_period(cached_df, period)

    # ── Fetch strategy ────────────────────────────────────────────────────────

    def _fetch_strategy(self, cached_df: pd.DataFrame, interval: str) -> str:
        """
        Decide how to refresh the cache given its current content.

        Returns one of:
          "FULL"        — discard cache, fetch full history
          "INCREMENTAL" — fetch only bars newer than last cached bar
          "USE_CACHE"   — no fetch needed; no new bar has closed yet
        """
        now     = datetime.now(tz=timezone.utc)
        last_ts = cached_df.index.max()

        # Ensure last_ts is tz-aware
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)

        gap_minutes = (now - last_ts).total_seconds() / 60

        # Gap > 1 day: PC was off / system downtime → start fresh
        if gap_minutes > _FULL_REFRESH_GAP_MINUTES:
            logger.info(
                "AlpacaProvider: gap %.0f min > %d → full refresh",
                gap_minutes, _FULL_REFRESH_GAP_MINUTES,
            )
            return "FULL"

        # New calendar day (UTC): morning clean slate
        if _is_new_trading_day(last_ts, now):
            logger.info("AlpacaProvider: new trading day → full refresh")
            return "FULL"

        # Not enough time for a new bar to have closed + been ingested by Alpaca
        interval_min = _INTERVAL_MINUTES.get(interval, 60)
        buffer_min   = _FETCH_BUFFER_MINUTES.get(interval, 5)
        min_gap      = interval_min + buffer_min

        if gap_minutes < min_gap:
            return "USE_CACHE"

        return "INCREMENTAL"

    # ── Private helpers ───────────────────────────────────────────────────────

    def _full_fetch_and_cache(
        self,
        symbol: str,
        interval: str,
        period: str,
        cache_path: str,
    ) -> pd.DataFrame:
        """Full fetch from Alpaca, overwrite cache, return the data."""
        df = self._fetch(symbol, interval, period)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        df.to_parquet(cache_path)
        logger.info(
            "AlpacaProvider: full fetch %s/%s → %d bars cached",
            symbol, interval, len(df),
        )
        return df

    def _fetch_since(
        self,
        symbol: str,
        interval: str,
        since: datetime,
    ) -> pd.DataFrame:
        """
        Fetch bars from (since + 1 bar) to now.

        Returns an empty DataFrame if no new bars are available or on error
        (caller falls back to existing cache).
        """
        interval_min = _INTERVAL_MINUTES.get(interval, 60)
        start = since + timedelta(minutes=interval_min)
        end   = datetime.now(tz=timezone.utc) - timedelta(minutes=15)

        # Guard before any import: no point calling Alpaca if window is empty
        if start >= end:
            return pd.DataFrame()

        from alpaca.data.requests import StockBarsRequest

        timeframe  = _build_timeframe(interval)
        req_kwargs: dict = dict(
            symbol_or_symbols=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
        )
        if self._feed:
            req_kwargs["feed"] = self._feed

        logger.info(
            "AlpacaProvider: incremental fetch %s/%s  %s → %s",
            symbol, interval,
            start.strftime("%Y-%m-%d %H:%M"),
            end.strftime("%Y-%m-%d %H:%M"),
        )

        try:
            client  = self._client_instance()
            request = StockBarsRequest(**req_kwargs)
            bars    = client.get_stock_bars(request)
            df      = bars.df
            if df is None or df.empty:
                return pd.DataFrame()
            return self._normalise(df, symbol)
        except Exception as exc:
            logger.warning(
                "AlpacaProvider: incremental fetch failed %s/%s: %s — using cache",
                symbol, interval, exc,
            )
            return pd.DataFrame()

    def _client_instance(self):
        """Lazy-initialise the alpaca-py client."""
        if self._client is None:
            from alpaca.data.historical import StockHistoricalDataClient
            self._client = StockHistoricalDataClient(
                api_key=self._api_key,
                secret_key=self._secret_key,
            )
        return self._client

    def _fetch(self, symbol: str, interval: str, period: str) -> pd.DataFrame:
        """Full download from Alpaca API, normalised to standard format."""
        from alpaca.data.requests import StockBarsRequest

        td = _PERIOD_MAP.get(period)
        if td is None:
            raise DataUnavailableError(
                f"Alpaca: unknown period '{period}'. Supported: {list(_PERIOD_MAP)}"
            )

        now   = datetime.now(tz=timezone.utc)
        start = now - td
        end   = now - timedelta(minutes=15)

        timeframe = _build_timeframe(interval)

        req_kwargs: dict = dict(
            symbol_or_symbols=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
        )
        if self._feed:
            req_kwargs["feed"] = self._feed

        logger.info(
            "AlpacaProvider: full fetch %s/%s  %s → %s",
            symbol, interval,
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
        )

        try:
            client  = self._client_instance()
            request = StockBarsRequest(**req_kwargs)
            bars    = client.get_stock_bars(request)
        except Exception as exc:
            raise DataUnavailableError(
                f"Alpaca fetch failed for {symbol}/{interval}: {exc}"
            ) from exc

        try:
            df = bars.df
        except Exception:
            df = pd.DataFrame()

        if df is None or df.empty:
            raise DataUnavailableError(
                f"Alpaca returned no data for {symbol}/{interval}/{period}"
            )

        return self._normalise(df, symbol)

    def _normalise(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """
        Normalise alpaca-py response DataFrame to standard OHLCV format.

        alpaca-py multi-symbol responses have a MultiIndex (symbol, timestamp).
        Single-symbol responses have a plain DatetimeIndex on `timestamp`.
        """
        # Flatten multi-index if present
        if isinstance(df.index, pd.MultiIndex):
            if symbol in df.index.get_level_values(0):
                df = df.xs(symbol, level=0)
            else:
                df = df.reset_index(level=0, drop=True)

        rename_map = {
            "open": "open", "high": "high", "low": "low",
            "close": "close", "volume": "volume",
            "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

        keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df   = df[keep].copy()

        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, utc=True)
        elif df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        df.index.name = "timestamp"
        df = df.sort_index().dropna(subset=["open", "close"])
        return df

    def _cache_path(self, symbol: str, interval: str) -> str:
        return os.path.join(self._data_dir, symbol, f"{interval}.parquet")

    def _load_cache_raw(self, path: str) -> Optional[pd.DataFrame]:
        """Load the full Parquet cache without any period trimming."""
        if not os.path.exists(path):
            return None
        try:
            df = pd.read_parquet(path)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            return df if not df.empty else None
        except Exception as exc:
            logger.warning("AlpacaProvider: cache read error %s: %s", path, exc)
            return None

    def _cache_covers_period(self, cached_df: pd.DataFrame, period: str) -> bool:
        """
        True if cached_df reaches back far enough to satisfy the requested period.
        Allows 10% tolerance for weekends and holidays.
        """
        td             = _PERIOD_MAP.get(period, timedelta(days=365))
        required_start = datetime.now(tz=timezone.utc) - td
        earliest       = cached_df.index.min()
        tolerance      = td * 0.10

        if earliest > required_start + tolerance:
            logger.info(
                "AlpacaProvider: coverage miss  earliest=%s required=%s",
                earliest.date(), required_start.date(),
            )
            return False
        return True

    def _trim_to_period(self, df: pd.DataFrame, period: str) -> pd.DataFrame:
        """Return df sliced to the last `period` of data."""
        td     = _PERIOD_MAP.get(period, timedelta(days=365))
        cutoff = datetime.now(tz=timezone.utc) - td
        return df[df.index >= cutoff]

    # ── Kept for backward compatibility (used by tests / external callers) ────

    def _load_cache(self, path: str, period: str) -> Optional[pd.DataFrame]:
        """Load Parquet cache trimmed to requested period."""
        df = self._load_cache_raw(path)
        if df is None:
            return None
        trimmed = self._trim_to_period(df, period)
        return trimmed if not trimmed.empty else None
