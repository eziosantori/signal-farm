"""
CcxtProvider — DataProvider backed by ccxt (Binance by default).

Fetches OHLCV data for crypto pairs via public endpoints — no API keys required.
Data is cached locally as Parquet files to avoid repeated network calls:
    .ccxt_data/<SYMBOL>/<TIMEFRAME>.parquet

Supports any ccxt-compatible exchange (default: Binance), which provides:
  - Historical OHLCV from 2017 for BTC/USDT
  - All major timeframes: 30m, 1h, 4h, 1d, etc.
  - Up to 1000 bars per request (paginated automatically)
  - No authentication required for market data

Usage
-----
    provider = CcxtProvider()
    df = provider.get_ohlcv("BTCUSD", "30m", "2y")
    df = provider.get_ohlcv("ETHUSD", "1d",  "2y")
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from data_feed.provider import DataProvider, DataUnavailableError

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", ".ccxt_data"
)

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

# Our interval → ccxt timeframe string
_INTERVAL_MAP = {
    "1m":  "1m",
    "5m":  "5m",
    "15m": "15m",
    "30m": "30m",
    "1h":  "1h",
    "4h":  "4h",
    "1d":  "1d",
    "1wk": "1w",
}

# Binance max bars per request
_BINANCE_LIMIT = 1000

# Cache TTL: re-fetch if file older than N hours
_CACHE_TTL_HOURS = 12

# Internal canonical → ccxt symbol mapping
# e.g. "BTCUSD" → "BTC/USDT", "ETHUSD" → "ETH/USDT"
def _to_ccxt_symbol(ticker: str) -> str:
    """Convert canonical symbol (BTCUSD) to ccxt pair (BTC/USDT)."""
    # Load from instruments.yaml if available
    try:
        import yaml
        _yaml = os.path.join(os.path.dirname(__file__), "..", "config", "instruments.yaml")
        with open(_yaml) as f:
            data = yaml.safe_load(f)
        data.pop("timeframes", None)
        for section in data.values():
            for sym, meta in section.items():
                if sym == ticker and meta.get("ccxt"):
                    return meta["ccxt"]
    except Exception:
        pass

    # Fallback: derive from ticker name
    if ticker.endswith("USD"):
        base = ticker[:-3]
        return f"{base}/USDT"
    return ticker


class CcxtProvider(DataProvider):
    """
    DataProvider backed by ccxt for crypto market data.

    Fetches public OHLCV data from Binance (or any ccxt exchange).
    No API keys required. Data cached as Parquet.

    Parameters
    ----------
    exchange_id  : ccxt exchange ID (default: "binance")
    data_dir     : Parquet cache root directory
    force_refresh: Bypass cache and always fetch fresh data
    """

    def __init__(
        self,
        exchange_id: str = "binance",
        data_dir: str = _DEFAULT_DATA_DIR,
        force_refresh: bool = False,
    ):
        self._exchange_id = exchange_id
        self._data_dir    = os.path.abspath(data_dir)
        self._force       = force_refresh
        self._exchange    = None   # lazy init

    # ── Public API ────────────────────────────────────────────────────────────

    def get_ohlcv(self, ticker: str, interval: str, period: str) -> pd.DataFrame:
        """
        Fetch OHLCV for `ticker` at `interval` for `period` lookback.

        Returns DataFrame with columns [open, high, low, close, volume]
        and UTC DatetimeIndex. Cached as Parquet.
        """
        symbol = ticker.upper()
        cache_path = self._cache_path(symbol, interval)

        if not self._force and self._cache_valid(cache_path, period):
            df = self._load_cache(cache_path, period)
            if df is not None and not df.empty:
                logger.info("CcxtProvider: cache hit  %s/%s", symbol, interval)
                return df

        df = self._fetch(symbol, interval, period)

        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        df.to_parquet(cache_path)
        logger.info("CcxtProvider: cached %d bars → %s", len(df), cache_path)

        return df

    # ── Private helpers ───────────────────────────────────────────────────────

    def _exchange_instance(self):
        if self._exchange is None:
            import ccxt
            cls = getattr(ccxt, self._exchange_id)
            self._exchange = cls({
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            })
        return self._exchange

    def _fetch(self, symbol: str, interval: str, period: str) -> pd.DataFrame:
        """Paginate Binance OHLCV requests to cover the full period."""
        if interval not in _INTERVAL_MAP:
            raise DataUnavailableError(
                f"CcxtProvider: unsupported interval '{interval}'. "
                f"Supported: {list(_INTERVAL_MAP)}"
            )
        ccxt_tf = _INTERVAL_MAP[interval]
        ccxt_sym = _to_ccxt_symbol(symbol)

        td = _PERIOD_MAP.get(period)
        if td is None:
            raise DataUnavailableError(f"CcxtProvider: unknown period '{period}'")

        now   = datetime.now(tz=timezone.utc)
        since = now - td

        # Convert interval to milliseconds for pagination
        tf_ms = self._timeframe_to_ms(ccxt_tf)

        exchange = self._exchange_instance()

        all_bars = []
        cursor_ms = int(since.timestamp() * 1000)
        end_ms    = int(now.timestamp() * 1000)

        logger.info(
            "CcxtProvider: fetching %s/%s from %s",
            ccxt_sym, ccxt_tf, since.strftime("%Y-%m-%d"),
        )

        while cursor_ms < end_ms:
            try:
                bars = exchange.fetch_ohlcv(
                    ccxt_sym, ccxt_tf,
                    since=cursor_ms,
                    limit=_BINANCE_LIMIT,
                )
            except Exception as exc:
                raise DataUnavailableError(
                    f"CcxtProvider fetch failed for {ccxt_sym}/{ccxt_tf}: {exc}"
                ) from exc

            if not bars:
                break

            all_bars.extend(bars)

            last_ts = bars[-1][0]
            if last_ts >= end_ms or len(bars) < _BINANCE_LIMIT:
                break

            cursor_ms = last_ts + tf_ms
            time.sleep(0.1)   # polite rate limit

        if not all_bars:
            raise DataUnavailableError(
                f"CcxtProvider: no data returned for {ccxt_sym}/{ccxt_tf}/{period}"
            )

        df = pd.DataFrame(
            all_bars,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.set_index("timestamp").sort_index()

        # Trim to requested period (remove any overshoot)
        since_ts = pd.Timestamp(since).tz_convert("UTC") if since.tzinfo else pd.Timestamp(since, tz="UTC")
        now_ts   = pd.Timestamp(now).tz_convert("UTC")   if now.tzinfo   else pd.Timestamp(now,   tz="UTC")
        df = df[df.index >= since_ts]
        df = df[df.index <= now_ts]
        df = df.drop_duplicates()

        return df

    @staticmethod
    def _timeframe_to_ms(tf: str) -> int:
        """Convert ccxt timeframe string to milliseconds."""
        units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
        for suffix, ms in units.items():
            if tf.endswith(suffix):
                return int(tf[:-1]) * ms
        return 60_000  # fallback: 1m

    def _cache_path(self, symbol: str, interval: str) -> str:
        return os.path.join(self._data_dir, symbol, f"{interval}.parquet")

    def _cache_valid(self, path: str, period: str) -> bool:
        """True if cache exists, is recent, and covers the full requested period."""
        if not os.path.exists(path):
            return False

        mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
        age_h = (datetime.now(tz=timezone.utc) - mtime).total_seconds() / 3600
        if age_h > _CACHE_TTL_HOURS:
            return False

        # Coverage check: cached data must reach back ~90% of the period
        td = _PERIOD_MAP.get(period, timedelta(days=365))
        required_start = datetime.now(tz=timezone.utc) - td
        try:
            df = pd.read_parquet(path)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            earliest = df.index.min()
            tolerance = td * 0.10
            if earliest > required_start + tolerance:
                logger.info(
                    "CcxtProvider: cache coverage miss %s  earliest=%s required=%s",
                    path, earliest.date(), required_start.date(),
                )
                return False
        except Exception:
            return False

        return True

    def _load_cache(self, path: str, period: str) -> Optional[pd.DataFrame]:
        """Load Parquet cache trimmed to requested period."""
        try:
            df = pd.read_parquet(path)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            td = _PERIOD_MAP.get(period, timedelta(days=365))
            cutoff = datetime.now(tz=timezone.utc) - td
            df = df[df.index >= cutoff]
            return df if not df.empty else None
        except Exception as exc:
            logger.warning("CcxtProvider: cache read error %s: %s", path, exc)
            return None
