"""
OandaProvider — DataProvider backed by the Oanda v20 REST API.

Covers all CFD instruments: forex, indices, metals, energies, soft commodities.
Data is cached as Parquet files to avoid repeated API calls:
    .oanda_data/<SYMBOL>/<TIMEFRAME>.parquet

Credentials are read from environment variables:
    OANDA_API_KEY     — API token from Oanda account settings
    OANDA_ACCOUNT_ID  — Account ID (optional, only needed for trading)
    OANDA_ENV         — "practice" (default) or "live"

Oanda API limits:
    - Max 5000 candles per request → paginated automatically
    - Practice account: full historical data for all granularities
    - No volume for forex (tick count reported instead; treated as volume)

Usage
-----
    provider = OandaProvider()
    df = provider.get_ohlcv("EURUSD",  "30m", "2y")
    df = provider.get_ohlcv("NAS100",  "1h",  "2y")
    df = provider.get_ohlcv("XAUUSD",  "4h",  "1y")
    df = provider.get_ohlcv("BRENT",   "1d",  "5y")
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import requests

from data_feed.provider import DataProvider, DataUnavailableError

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", ".oanda_data"
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

# Our interval → Oanda granularity
_INTERVAL_MAP = {
    "1m":  "M1",
    "5m":  "M5",
    "15m": "M15",
    "30m": "M30",
    "1h":  "H1",
    "4h":  "H4",
    "1d":  "D",
    "1wk": "W",
}

# Canonical symbol → Oanda instrument name
# https://developer.oanda.com/rest-live-v20/instrument-df/
_SYMBOL_MAP: dict[str, str] = {
    # Forex majors
    "EURUSD": "EUR_USD",
    "GBPUSD": "GBP_USD",
    "USDJPY": "USD_JPY",
    "USDCHF": "USD_CHF",
    "AUDUSD": "AUD_USD",
    "USDCAD": "USD_CAD",
    "NZDUSD": "NZD_USD",
    # Forex crosses
    "EURGBP": "EUR_GBP",
    "EURJPY": "EUR_JPY",
    "GBPJPY": "GBP_JPY",
    "EURCHF": "EUR_CHF",
    "AUDJPY": "AUD_JPY",
    # Indices
    "US500":  "SPX500_USD",
    "NAS100": "NAS100_USD",
    "US30":   "US30_USD",
    "GER40":  "DE40_EUR",      # Oanda: DAX 40 (renamed from DE30)
    "UK100":  "UK100_GBP",
    "JPN225": "JP225_USD",
    "AUS200": "AU200_AUD",
    # Precious metals
    "XAUUSD": "XAU_USD",
    "XAGUSD": "XAG_USD",
    # Energies
    "BRENT":  "BCO_USD",
    "WTI":    "WTICO_USD",
    "NATGAS": "NATGAS_USD",
    # Industrial metals
    "COPPER": "COPPER_USD",
    # Soft commodities (availability varies by account region)
    "COCOA":   "COCOA_USD",
    "COFFEE":  "COFFEE_USD",
    "SOYBEAN": "SOYBEAN_USD",
    "SUGAR":   "SUGAR_USD",
    "COTTON":  "COTTON_USD",
    "WHEAT":   "WHEAT_USD",
    "CORN":    "CORN_USD",
}

_OANDA_BASE = {
    "practice": "https://api-fxpractice.oanda.com",
    "live":     "https://api-fxtrade.oanda.com",
}

_MAX_CANDLES  = 5000   # Oanda hard limit per request
_CACHE_TTL_HOURS = 12


class OandaProvider(DataProvider):
    """
    DataProvider backed by Oanda v20 REST API.

    Covers forex, indices (CFD), metals, energies, soft commodities.
    Requires OANDA_API_KEY env var. No account balance needed (data only).

    Parameters
    ----------
    api_key    : Oanda API token (default: OANDA_API_KEY env var)
    env        : "practice" or "live" (default: OANDA_ENV env var or "practice")
    data_dir   : Parquet cache root
    price      : "M" (mid), "B" (bid), "A" (ask) — default "M"
    force_refresh : Bypass cache
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        env: Optional[str] = None,
        data_dir: str = _DEFAULT_DATA_DIR,
        price: str = "M",
        force_refresh: bool = False,
    ):
        self._api_key     = api_key or os.environ.get("OANDA_API_KEY", "")
        self._env         = env    or os.environ.get("OANDA_ENV", "practice")
        self._data_dir    = os.path.abspath(data_dir)
        self._price       = price
        self._force       = force_refresh

        if not self._api_key:
            raise DataUnavailableError(
                "Oanda API key not found. Set OANDA_API_KEY environment variable "
                "or pass api_key directly. Get your key from: "
                "https://www.oanda.com/account/tpa/personal_token"
            )

        self._base_url = _OANDA_BASE.get(self._env, _OANDA_BASE["practice"])
        self._session  = None

    # ── Public API ────────────────────────────────────────────────────────────

    def get_ohlcv(self, ticker: str, interval: str, period: str) -> pd.DataFrame:
        symbol    = ticker.upper()
        cache_path = self._cache_path(symbol, interval)

        if not self._force and self._cache_valid(cache_path, period):
            df = self._load_cache(cache_path, period)
            if df is not None and not df.empty:
                logger.info("OandaProvider: cache hit  %s/%s", symbol, interval)
                return df

        df = self._fetch(symbol, interval, period)

        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        df.to_parquet(cache_path)
        logger.info("OandaProvider: cached %d bars → %s", len(df), cache_path)

        return df

    # ── Private helpers ───────────────────────────────────────────────────────

    def _session_instance(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type":  "application/json",
            })
        return self._session

    def _to_oanda_symbol(self, symbol: str) -> str:
        """Convert canonical symbol to Oanda instrument name."""
        if symbol in _SYMBOL_MAP:
            return _SYMBOL_MAP[symbol]
        # Generic fallback: try XXX_USD or XXX_YYY from 6-char pair
        if len(symbol) == 6:
            return f"{symbol[:3]}_{symbol[3:]}"
        raise DataUnavailableError(
            f"OandaProvider: no Oanda mapping for symbol '{symbol}'. "
            f"Supported: {list(_SYMBOL_MAP.keys())}"
        )

    def _fetch(self, symbol: str, interval: str, period: str) -> pd.DataFrame:
        """Paginate Oanda candle requests to cover the full period."""
        if interval not in _INTERVAL_MAP:
            raise DataUnavailableError(
                f"OandaProvider: unsupported interval '{interval}'. "
                f"Supported: {list(_INTERVAL_MAP)}"
            )

        oanda_sym  = self._to_oanda_symbol(symbol)
        granularity = _INTERVAL_MAP[interval]

        td = _PERIOD_MAP.get(period)
        if td is None:
            raise DataUnavailableError(f"OandaProvider: unknown period '{period}'")

        now   = datetime.now(tz=timezone.utc)
        since = now - td

        logger.info(
            "OandaProvider: fetching %s/%s  %s → %s",
            oanda_sym, granularity,
            since.strftime("%Y-%m-%d"),
            now.strftime("%Y-%m-%d"),
        )

        url     = f"{self._base_url}/v3/instruments/{oanda_sym}/candles"
        session = self._session_instance()
        all_rows = []
        cursor  = since

        while cursor < now:
            params = {
                "granularity":  granularity,
                "from":         cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to":           now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "count":        _MAX_CANDLES,
                "price":        self._price,
            }
            try:
                resp = session.get(url, params=params, timeout=30)
                resp.raise_for_status()
            except requests.HTTPError as exc:
                body = exc.response.text if exc.response else ""
                raise DataUnavailableError(
                    f"OandaProvider HTTP {exc.response.status_code} for "
                    f"{oanda_sym}/{granularity}: {body[:200]}"
                ) from exc
            except Exception as exc:
                raise DataUnavailableError(
                    f"OandaProvider request failed for {oanda_sym}: {exc}"
                ) from exc

            data    = resp.json()
            candles = data.get("candles", [])

            if not candles:
                break

            for c in candles:
                if not c.get("complete", True):
                    continue   # skip the in-progress bar
                mid = c.get("mid") or c.get("bid") or c.get("ask") or {}
                all_rows.append({
                    "timestamp": c["time"],
                    "open":      float(mid.get("o", 0)),
                    "high":      float(mid.get("h", 0)),
                    "low":       float(mid.get("l", 0)),
                    "close":     float(mid.get("c", 0)),
                    "volume":    float(c.get("volume", 0)),
                })

            last_time_str = candles[-1]["time"]
            last_dt = pd.Timestamp(last_time_str).tz_convert("UTC")

            if len(candles) < _MAX_CANDLES:
                break  # fetched everything up to now

            # Advance cursor past the last bar
            cursor = last_dt.to_pydatetime() + timedelta(seconds=1)
            time.sleep(0.2)   # polite rate limit

        if not all_rows:
            raise DataUnavailableError(
                f"OandaProvider: no data returned for {oanda_sym}/{granularity}/{period}"
            )

        df = pd.DataFrame(all_rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = (df
              .set_index("timestamp")
              .sort_index()
              .drop_duplicates()
              [["open", "high", "low", "close", "volume"]])

        # Trim to requested window
        since_ts = pd.Timestamp(since, tz="UTC")
        df = df[df.index >= since_ts]

        return df

    def _cache_path(self, symbol: str, interval: str) -> str:
        return os.path.join(self._data_dir, symbol, f"{interval}.parquet")

    def _cache_valid(self, path: str, period: str) -> bool:
        if not os.path.exists(path):
            return False
        mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
        age_h = (datetime.now(tz=timezone.utc) - mtime).total_seconds() / 3600
        if age_h > _CACHE_TTL_HOURS:
            return False
        # Coverage check
        td = _PERIOD_MAP.get(period, timedelta(days=365))
        required_start = datetime.now(tz=timezone.utc) - td
        try:
            df = pd.read_parquet(path)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            if df.index.min() > required_start + td * 0.10:
                return False
        except Exception:
            return False
        return True

    def _load_cache(self, path: str, period: str) -> Optional[pd.DataFrame]:
        try:
            df = pd.read_parquet(path)
            if df.index.tz is None:
                df.index = df.index.tz_localize("UTC")
            td = _PERIOD_MAP.get(period, timedelta(days=365))
            cutoff = datetime.now(tz=timezone.utc) - td
            df = df[df.index >= cutoff]
            return df if not df.empty else None
        except Exception as exc:
            logger.warning("OandaProvider: cache read error %s: %s", path, exc)
            return None
