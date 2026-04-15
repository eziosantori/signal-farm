"""
DukascopyProvider — DataProvider implementation backed by dukascopy-node.

Uses `npx dukascopy-node` as a subprocess (requires Node.js in PATH).
Data is cached locally as Parquet files:
    .dukascopy_data/<SYMBOL>/<TIMEFRAME>.parquet

Instrument and timeframe mapping is read from config/instruments.yaml.

Usage
-----
    provider = DukascopyProvider()
    df = provider.get_ohlcv("EURUSD", "1h", "200d")
    df = provider.get_ohlcv("XAUUSD", "4h", "1y")
    df = provider.get_ohlcv("AAPL",   "30m", "60d")
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import yaml

from data_feed.provider import DataProvider, DataUnavailableError

logger = logging.getLogger(__name__)

# Default Parquet cache directory (relative to project root)
_DEFAULT_DATA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", ".dukascopy_data"
)
_INSTRUMENTS_YAML = os.path.join(
    os.path.dirname(__file__), "..", "config", "instruments.yaml"
)

# Period string → timedelta
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


class DukascopyProvider(DataProvider):
    """
    DataProvider that fetches historical OHLCV data via dukascopy-node.

    Parameters
    ----------
    data_dir        : Root directory for Parquet cache (default: .dukascopy_data/)
    price_type      : "bid" (default) or "ask"
    batch_pause_ms  : Pause between dukascopy-node batch requests in ms
    retries         : Download retries on failure
    npx_cmd         : Path/name of the npx executable
    force_refresh   : If True, ignore existing cache and re-download
    """

    def __init__(
        self,
        data_dir: str = _DEFAULT_DATA_DIR,
        price_type: str = "bid",
        batch_pause_ms: int = 1000,
        retries: int = 2,
        npx_cmd: str = "npx",
        force_refresh: bool = False,
    ) -> None:
        self.data_dir = os.path.abspath(data_dir)
        self.price_type = price_type
        self.batch_pause_ms = batch_pause_ms
        self.retries = retries
        self.npx_cmd = npx_cmd
        self.force_refresh = force_refresh

        self._instruments, self._timeframes = _load_catalog(_INSTRUMENTS_YAML)

    # ------------------------------------------------------------------
    # DataProvider interface
    # ------------------------------------------------------------------

    def get_ohlcv(self, ticker: str, interval: str, period: str) -> pd.DataFrame:
        """
        Fetch OHLCV data for `ticker` at `interval` granularity over `period`.

        Parameters
        ----------
        ticker   : Canonical symbol (e.g. "EURUSD", "XAUUSD", "AAPL", "BTCUSD")
                   Also accepts yfinance-style aliases (e.g. "BTC-USD" → "BTCUSD")
        interval : Timeframe string: "1m","5m","15m","30m","1h","4h","1d","1wk"
        period   : Look-back period string: "60d","200d","1y","2y","5y" etc.

        Returns
        -------
        DataFrame with lowercase columns [open, high, low, close, volume]
        and a UTC DatetimeIndex named "datetime".
        """
        symbol = self._resolve_symbol(ticker)
        dk_feed, dk_tf = self._resolve_feed_and_tf(symbol, interval)

        end = datetime.now(tz=timezone.utc)
        start = _parse_period(period, end)

        parquet_path = self._parquet_path(symbol, interval)
        existing = _load_parquet(parquet_path)

        start_naive = start.replace(tzinfo=None)
        end_naive = end.replace(tzinfo=None)

        # Decide what range (if any) needs to be fetched
        fetch_start = start
        fetch_end = end
        skip_fetch = False

        if existing is not None and not self.force_refresh:
            cache_oldest = existing.index.min()
            cache_newest = existing.index.max()

            needs_backfill = cache_oldest > start_naive + timedelta(hours=2)
            needs_forward  = cache_newest < end_naive - timedelta(hours=2)

            if not needs_backfill and not needs_forward:
                logger.debug("%s/%s fully cached — skipping download", symbol, interval)
                return _filter_and_normalize(existing, start, end)

            if needs_backfill:
                # Fetch from requested start up to the oldest cached bar
                fetch_start = start
                fetch_end = datetime(
                    cache_oldest.year, cache_oldest.month, cache_oldest.day,
                    tzinfo=timezone.utc,
                )
                logger.info(
                    "%s/%s cache starts at %s, backfilling from %s",
                    symbol, interval, cache_oldest.date(), fetch_start.date(),
                )
                if needs_forward:
                    # Both ends missing: simplest is to fetch the full range
                    fetch_end = end
                    logger.info("%s/%s also needs forward — fetching full range", symbol, interval)
            else:
                # Only forward fill needed
                fetch_start = datetime(
                    cache_newest.year, cache_newest.month, cache_newest.day,
                    tzinfo=timezone.utc,
                )
                logger.info("%s/%s cached to %s, fetching forward", symbol, interval, cache_newest.date())

        try:
            raw = self._fetch(dk_feed, dk_tf, fetch_start, fetch_end, symbol, interval)
        except DataUnavailableError as exc:
            if existing is not None:
                logger.warning(
                    "%s/%s: fetch failed (%s) — returning cached data", symbol, interval, exc
                )
                return _filter_and_normalize(existing, start, end)
            raise

        if raw is None or raw.empty:
            if existing is not None:
                logger.warning("%s/%s: no new data, returning cached", symbol, interval)
                return _filter_and_normalize(existing, start, end)
            raise DataUnavailableError(
                f"No data returned from Dukascopy for {symbol} ({interval}, {period})"
            )

        if existing is not None and not self.force_refresh:
            combined = pd.concat([existing, raw])
            combined = combined[~combined.index.duplicated(keep="last")]
            combined.sort_index(inplace=True)
        else:
            combined = raw

        os.makedirs(os.path.dirname(parquet_path), exist_ok=True)
        combined.to_parquet(parquet_path)
        logger.info(
            "%s/%s saved: %d bars (%s → %s)",
            symbol, interval, len(combined),
            combined.index.min().date(), combined.index.max().date(),
        )

        return _filter_and_normalize(combined, start, end)

    def list_instruments(self) -> list[dict]:
        """Return all supported instruments with their metadata."""
        out = []
        for section in self._instruments.values():
            for symbol, meta in section.items():
                out.append({"symbol": symbol, **meta})
        return sorted(out, key=lambda x: x["symbol"])

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_symbol(self, ticker: str) -> str:
        """Normalise ticker to canonical symbol (e.g. 'BTC-USD' → 'BTCUSD')."""
        # Common yfinance-style aliases
        normalized = (
            ticker.upper()
            .replace("-USD", "USD")
            .replace("-EUR", "EUR")
            .replace("=X", "")
        )
        # Search across all asset-class sections
        for section in self._instruments.values():
            if normalized in section:
                return normalized
        # Second pass: check yfinance field
        for section in self._instruments.values():
            for symbol, meta in section.items():
                if meta.get("yfinance") == ticker or meta.get("yfinance") == normalized:
                    return symbol
        raise DataUnavailableError(
            f"Instrument '{ticker}' not found in instruments.yaml. "
            f"Run `python main.py instruments` to see available symbols."
        )

    def _resolve_feed_and_tf(self, symbol: str, interval: str) -> tuple[str, str]:
        """Return (dukascopy feed ID, dukascopy timeframe string)."""
        # Find instrument metadata
        meta = None
        for section in self._instruments.values():
            if symbol in section:
                meta = section[symbol]
                break
        if meta is None:
            raise DataUnavailableError(f"No metadata for symbol '{symbol}'")

        feed = meta.get("feed")
        if not feed or feed == "~" or feed is None:
            raise DataUnavailableError(
                f"'{symbol}' has no Dukascopy feed ID — "
                f"use YFinanceProvider for this instrument."
            )

        dk_tf = self._timeframes.get(interval)
        if not dk_tf:
            available = ", ".join(self._timeframes.keys())
            raise DataUnavailableError(
                f"Unsupported interval '{interval}'. Available: {available}"
            )

        return feed, dk_tf

    def _fetch(
        self,
        feed_id: str,
        dk_tf: str,
        start: datetime,
        end: datetime,
        symbol: str,
        interval: str,
    ) -> Optional[pd.DataFrame]:
        """Call dukascopy-node subprocess and return parsed DataFrame."""
        date_from = start.strftime("%Y-%m-%d")
        date_to = end.strftime("%Y-%m-%d")

        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = [
                self.npx_cmd, "dukascopy-node",
                "-i",    feed_id,
                "-from", date_from,
                "-to",   date_to,
                "-t",    dk_tf,
                "-p",    self.price_type,
                "-f",    "json",
                "-v",
                "-in",
                "-s",
                "-dir",  tmpdir,
                "-bp",   str(self.batch_pause_ms),
                "-r",    str(self.retries),
            ]

            logger.debug("Dukascopy CMD: %s", " ".join(cmd))

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
            except FileNotFoundError:
                raise DataUnavailableError(
                    f"'{self.npx_cmd}' not found — install Node.js and ensure npx is in PATH"
                )
            except subprocess.TimeoutExpired:
                raise DataUnavailableError(
                    f"dukascopy-node timed out fetching {symbol}/{interval}"
                )

            if proc.returncode != 0:
                msg = (proc.stderr or proc.stdout or "").strip()[-500:]
                raise DataUnavailableError(
                    f"dukascopy-node failed for {symbol}/{interval}:\n{msg}"
                )

            json_files = sorted(
                [f for f in os.listdir(tmpdir) if f.endswith(".json")],
                key=lambda f: os.path.getmtime(os.path.join(tmpdir, f)),
            )
            if not json_files:
                return None

            return _parse_dukascopy_json(os.path.join(tmpdir, json_files[-1]))

    def _parquet_path(self, symbol: str, interval: str) -> str:
        tf_key = interval.replace("m", "M").replace("h", "H").replace("d", "D").replace("wk", "W")
        return os.path.join(self.data_dir, symbol.upper(), f"{tf_key}.parquet")


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _load_catalog(yaml_path: str) -> tuple[dict, dict]:
    """Load instruments.yaml and return (instruments_dict, timeframes_dict)."""
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    timeframes = data.pop("timeframes", {})
    return data, timeframes


def _parse_period(period: str, end: datetime) -> datetime:
    """Convert a period string like '200d' or '2y' to a start datetime."""
    if period in _PERIOD_MAP:
        return end - _PERIOD_MAP[period]
    # Try to parse numeric suffix
    try:
        if period.endswith("d"):
            return end - timedelta(days=int(period[:-1]))
        if period.endswith("y"):
            return end - timedelta(days=int(period[:-1]) * 365)
    except ValueError:
        pass
    raise DataUnavailableError(
        f"Unsupported period format '{period}'. "
        f"Use formats like: 60d, 200d, 1y, 2y, 5y"
    )


def _parse_dukascopy_json(json_path: str) -> pd.DataFrame:
    """Parse dukascopy-node JSON output → lowercase OHLCV DataFrame (naive UTC)."""
    with open(json_path, encoding="utf-8") as fh:
        rows = json.load(fh)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["timestamp"] = (
        pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        .dt.tz_localize(None)          # keep naive UTC (consistent with algo-farm)
    )
    df = df.set_index("timestamp")
    df.index.name = "datetime"
    df.columns = [c.lower() for c in df.columns]

    if "volume" not in df.columns:
        df["volume"] = 0.0

    return df[["open", "high", "low", "close", "volume"]]


def _load_parquet(path: str) -> Optional[pd.DataFrame]:
    """Load existing Parquet cache, return None if absent."""
    if not os.path.exists(path):
        return None
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.columns = [c.lower() for c in df.columns]
    return df


def _filter_and_normalize(
    df: pd.DataFrame,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """
    Filter DataFrame to [start, end] and convert index to UTC-aware DatetimeIndex.
    Output matches the DataProvider contract: lowercase columns, UTC index.
    """
    start_naive = start.replace(tzinfo=None)
    end_naive = end.replace(tzinfo=None)

    df = df[(df.index >= start_naive) & (df.index <= end_naive)].copy()

    # Convert to UTC-aware to match YFinanceProvider contract
    df.index = pd.DatetimeIndex(df.index).tz_localize("UTC")
    df.index.name = "datetime"

    return df
