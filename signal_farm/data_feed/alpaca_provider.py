"""
AlpacaProvider — DataProvider implementation backed by Alpaca Markets API.

Uses alpaca-py (StockHistoricalDataClient) to fetch US stock OHLCV data.
Data is cached locally as Parquet files to avoid repeated API calls:
    .alpaca_data/<SYMBOL>/<TIMEFRAME>.parquet

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

# Cache freshness: only re-fetch if cached data is older than N hours
_CACHE_TTL_HOURS = 12


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
    Data is cached as Parquet files and re-used if fresh enough.

    Parameters
    ----------
    api_key     : Alpaca API key (default: ALPACA_API_KEY env var)
    secret_key  : Alpaca secret key (default: ALPACA_SECRET_KEY env var)
    data_dir    : Cache root directory
    feed        : "iex" (free tier) or "sip" (paid/paper). "sip" auto-detected
                  for paper/live accounts; leave None to let alpaca-py decide.
    force_refresh : If True, bypass cache and always fetch fresh data
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
        self._feed       = feed
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
        and UTC DatetimeIndex. Cached as Parquet on first fetch.
        """
        symbol = ticker.upper()
        cache_path = self._cache_path(symbol, interval)

        # Try cache first
        if not self._force and self._cache_valid(cache_path, period):
            df = self._load_cache(cache_path, period)
            if df is not None and not df.empty:
                logger.info("AlpacaProvider: cache hit  %s/%s", symbol, interval)
                return df
            logger.info("AlpacaProvider: cache stale %s/%s — re-fetching", symbol, interval)

        # Fetch from API
        df = self._fetch(symbol, interval, period)

        # Persist to cache
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        df.to_parquet(cache_path)
        logger.info("AlpacaProvider: cached %d bars → %s", len(df), cache_path)

        return df

    # ── Private helpers ───────────────────────────────────────────────────────

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
        """Download bars from Alpaca API and normalise to standard format."""
        from alpaca.data.requests import StockBarsRequest

        td = _PERIOD_MAP.get(period)
        if td is None:
            raise DataUnavailableError(
                f"Alpaca: unknown period '{period}'. Supported: {list(_PERIOD_MAP)}"
            )

        now   = datetime.now(tz=timezone.utc)
        start = now - td
        # Alpaca rejects requests past the market open buffer — end at yesterday
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
            "AlpacaProvider: fetching %s/%s  %s → %s",
            symbol, interval,
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
        )

        try:
            client = self._client_instance()
            request = StockBarsRequest(**req_kwargs)
            bars = client.get_stock_bars(request)
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
        Normalise alpaca-py response DataFrame to standard format.

        alpaca-py multi-symbol responses have a MultiIndex (symbol, timestamp).
        Single-symbol responses have a single DatetimeIndex on `timestamp`.
        """
        # Flatten multi-index if present
        if isinstance(df.index, pd.MultiIndex):
            if symbol in df.index.get_level_values(0):
                df = df.xs(symbol, level=0)
            else:
                df = df.reset_index(level=0, drop=True)

        # Rename alpaca columns to our standard
        rename_map = {
            "open":   "open",
            "high":   "high",
            "low":    "low",
            "close":  "close",
            "volume": "volume",
            # alpaca-py sometimes uses these
            "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

        # Keep only OHLCV
        keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[keep].copy()

        # Ensure UTC DatetimeIndex named 'timestamp'
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, utc=True)
        elif df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        df.index.name = "timestamp"
        df = df.sort_index()
        df = df.dropna(subset=["open", "close"])

        return df

    def _cache_path(self, symbol: str, interval: str) -> str:
        return os.path.join(self._data_dir, symbol, f"{interval}.parquet")

    def _cache_valid(self, path: str, period: str) -> bool:
        """
        True if cache file exists, is recent, AND covers the full requested period.

        A cache hit on 7d data will be invalid when 2y is requested, forcing
        a fresh fetch that overwrites the cache with the longer history.
        """
        if not os.path.exists(path):
            return False

        # Check freshness (TTL)
        mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
        age_hours = (datetime.now(tz=timezone.utc) - mtime).total_seconds() / 3600
        if age_hours > _CACHE_TTL_HOURS:
            return False

        # Check coverage: cached data must reach back at least 90% of period
        td = _PERIOD_MAP.get(period, timedelta(days=365))
        required_start = datetime.now(tz=timezone.utc) - td
        try:
            df = pd.read_parquet(path)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            earliest = df.index.min()
            # Allow 10% gap tolerance (weekends, holidays)
            tolerance = td * 0.10
            if earliest > required_start + tolerance:
                logger.info(
                    "AlpacaProvider: cache coverage miss %s  earliest=%s required=%s",
                    path, earliest.date(), required_start.date()
                )
                return False
        except Exception:
            return False

        return True

    def _load_cache(self, path: str, period: str) -> Optional[pd.DataFrame]:
        """Load Parquet cache and trim to requested period."""
        try:
            df = pd.read_parquet(path)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")

            td = _PERIOD_MAP.get(period, timedelta(days=365))
            cutoff = datetime.now(tz=timezone.utc) - td
            df = df[df.index >= cutoff]

            return df if not df.empty else None
        except Exception as exc:
            logger.warning("AlpacaProvider: cache read error %s: %s", path, exc)
            return None
