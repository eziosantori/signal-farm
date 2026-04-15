import os
import time
import pickle
import hashlib
import logging
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from data_feed.provider import DataProvider, DataUnavailableError

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", ".cache")
ET = ZoneInfo("America/New_York")


def _cache_path(ticker: str, interval: str, period: str, as_of: str) -> str:
    key = f"{ticker}_{interval}_{period}_{as_of}"
    digest = hashlib.md5(key.encode()).hexdigest()
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{digest}.pkl")


def _as_of_key(interval: str) -> str:
    """Daily TFs cache per day; intraday cache per hour."""
    now = datetime.now()
    if interval in ("1d", "1wk", "1mo"):
        return now.strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d-%H")


class YFinanceProvider(DataProvider):
    def __init__(self, use_cache: bool = True, max_retries: int = 3):
        self.use_cache = use_cache
        self.max_retries = max_retries

    # yfinance hard limits for intraday intervals
    _MAX_PERIOD: dict = {
        "1m": "7d",
        "2m": "60d", "5m": "60d", "15m": "60d", "30m": "60d", "90m": "60d",
        "1h": "730d",
    }

    def get_ohlcv(self, ticker: str, interval: str, period: str) -> pd.DataFrame:
        # Cap period to yfinance's limit for intraday intervals
        max_period = self._MAX_PERIOD.get(interval)
        if max_period and period not in (None, ""):
            from data_feed.dukascopy_provider import _PERIOD_MAP
            req_days = _PERIOD_MAP.get(period, None)
            max_days = _PERIOD_MAP.get(max_period, None)
            if req_days and max_days and req_days > max_days:
                logger.warning(
                    "%s/%s: yfinance caps %s at %s for this interval — using %s",
                    ticker, interval, period, max_period, max_period,
                )
                period = max_period

        as_of = _as_of_key(interval)
        cache_file = _cache_path(ticker, interval, period, as_of)

        if self.use_cache and os.path.exists(cache_file):
            with open(cache_file, "rb") as f:
                logger.debug("Cache hit: %s", cache_file)
                return pickle.load(f)

        df = self._download_with_retry(ticker, interval, period)

        if self.use_cache:
            with open(cache_file, "wb") as f:
                pickle.dump(df, f)

        return df

    def _download_with_retry(self, ticker: str, interval: str, period: str) -> pd.DataFrame:
        last_exc = None
        for attempt in range(self.max_retries):
            try:
                raw = yf.download(
                    ticker,
                    period=period,
                    interval=interval,
                    auto_adjust=True,
                    progress=False,
                )
                if raw is None or raw.empty:
                    raise DataUnavailableError(
                        f"No data returned for {ticker} ({interval}, {period})"
                    )

                df = self._normalize(raw, ticker, interval)
                return df

            except DataUnavailableError:
                raise
            except Exception as exc:
                last_exc = exc
                wait = 2 ** attempt
                logger.warning(
                    "yfinance error for %s/%s (attempt %d/%d): %s — retrying in %ds",
                    ticker, interval, attempt + 1, self.max_retries, exc, wait,
                )
                time.sleep(wait)

        raise DataUnavailableError(
            f"Failed to fetch {ticker} ({interval}, {period}) after {self.max_retries} attempts: {last_exc}"
        )

    def _normalize(self, raw: pd.DataFrame, ticker: str, interval: str) -> pd.DataFrame:
        # yfinance may return MultiIndex columns when a single ticker is used
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        raw.columns = [c.lower() for c in raw.columns]

        required = {"open", "high", "low", "close"}
        missing = required - set(raw.columns)
        if missing:
            raise DataUnavailableError(
                f"Missing columns {missing} for {ticker} ({interval})"
            )

        if "volume" not in raw.columns:
            raw["volume"] = 0

        df = raw[["open", "high", "low", "close", "volume"]].copy()

        # Normalize index to UTC
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        df.index.name = "datetime"
        df = df.sort_index()
        df = df[~df.index.duplicated(keep="last")]
        df = df.dropna(subset=["open", "high", "low", "close"])

        # For US stocks 30min: filter to regular session only (09:30–16:00 ET)
        if interval == "30m":
            df = self._filter_session(df)

        return df

    def _filter_session(self, df: pd.DataFrame) -> pd.DataFrame:
        """Keep only bars within 09:30–16:00 ET."""
        df_et = df.copy()
        df_et.index = df_et.index.tz_convert(ET)
        mask = (
            (df_et.index.time >= __import__("datetime").time(9, 30))
            & (df_et.index.time <= __import__("datetime").time(16, 0))
        )
        result = df[mask].copy()
        result.index = result.index.tz_convert("UTC")
        return result
