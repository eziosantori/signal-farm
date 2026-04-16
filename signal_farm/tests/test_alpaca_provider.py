"""
Unit tests for AlpacaProvider fetch strategy and incremental append logic.

All tests are offline — no real Alpaca API calls are made.
AlpacaProvider is instantiated with fake credentials; actual fetch methods
are patched where needed.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_feed.alpaca_provider import (
    AlpacaProvider,
    _is_new_trading_day,
    _FULL_REFRESH_GAP_MINUTES,
    _INTERVAL_MINUTES,
    _FETCH_BUFFER_MINUTES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_provider(tmp_path) -> AlpacaProvider:
    """Return a provider with fake creds and a temp data dir (no real API calls)."""
    with patch.dict(os.environ, {
        "ALPACA_API_KEY": "fake_key",
        "ALPACA_SECRET_KEY": "fake_secret",
    }):
        return AlpacaProvider(data_dir=str(tmp_path))


def _make_df(n_bars: int, interval_minutes: int, end: datetime | None = None) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame with n_bars ending at `end` (default: now)."""
    if end is None:
        end = datetime.now(tz=timezone.utc)
    start = end - timedelta(minutes=interval_minutes * n_bars)
    idx = pd.date_range(start=start, periods=n_bars, freq=f"{interval_minutes}min", tz="UTC")
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000},
        index=idx,
    )


def _make_daily_df(n_days: int, end: datetime | None = None) -> pd.DataFrame:
    """Build a daily OHLCV DataFrame."""
    if end is None:
        end = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    idx = pd.date_range(end=end, periods=n_days, freq="D", tz="UTC")
    return pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000},
        index=idx,
    )


# ---------------------------------------------------------------------------
# _is_new_trading_day
# ---------------------------------------------------------------------------

class TestIsNewTradingDay:
    def test_same_day_returns_false(self):
        now  = datetime(2026, 4, 15, 14, 0, tzinfo=timezone.utc)
        last = datetime(2026, 4, 15, 9, 30, tzinfo=timezone.utc)
        assert _is_new_trading_day(last, now) is False

    def test_next_day_returns_true(self):
        now  = datetime(2026, 4, 16, 9, 35, tzinfo=timezone.utc)
        last = datetime(2026, 4, 15, 15, 30, tzinfo=timezone.utc)
        assert _is_new_trading_day(last, now) is True

    def test_multi_day_gap_returns_true(self):
        now  = datetime(2026, 4, 20, 9, 35, tzinfo=timezone.utc)
        last = datetime(2026, 4, 15, 15, 30, tzinfo=timezone.utc)
        assert _is_new_trading_day(last, now) is True

    def test_midnight_boundary(self):
        # 23:59 yesterday → 00:01 today
        now  = datetime(2026, 4, 16, 0, 1, tzinfo=timezone.utc)
        last = datetime(2026, 4, 15, 23, 59, tzinfo=timezone.utc)
        assert _is_new_trading_day(last, now) is True


# ---------------------------------------------------------------------------
# _fetch_strategy
# ---------------------------------------------------------------------------

class TestFetchStrategy:
    @pytest.fixture
    def provider(self, tmp_path):
        return _make_provider(tmp_path)

    def _call(self, provider, cached_df, interval, now_offset_minutes=0):
        """Call _fetch_strategy with a controlled 'now'."""
        last_ts = cached_df.index.max()
        fake_now = last_ts + timedelta(minutes=now_offset_minutes)

        with patch("data_feed.alpaca_provider.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            return provider._fetch_strategy(cached_df, interval)

    # --- USE_CACHE ---

    def test_use_cache_when_bar_not_closed(self, provider):
        """5 min after a 30m bar → not enough time (30+2=32 min needed)."""
        df = _make_df(10, 30)
        result = self._call(provider, df, "30m", now_offset_minutes=5)
        assert result == "USE_CACHE"

    def test_use_cache_exact_interval_not_enough(self, provider):
        """Exactly 30 min after last bar, but buffer not included yet."""
        df = _make_df(10, 30)
        result = self._call(provider, df, "30m", now_offset_minutes=30)
        assert result == "USE_CACHE"

    def test_use_cache_for_1h_before_threshold(self, provider):
        df = _make_df(10, 60)
        result = self._call(provider, df, "1h", now_offset_minutes=60)  # 60 < 63
        assert result == "USE_CACHE"

    # --- INCREMENTAL ---

    def test_incremental_after_threshold(self, provider):
        """32 min after a 30m bar (30+2 buffer) → INCREMENTAL."""
        df = _make_df(10, 30)
        result = self._call(provider, df, "30m", now_offset_minutes=32)
        assert result == "INCREMENTAL"

    def test_incremental_for_1h_after_threshold(self, provider):
        df = _make_df(10, 60)
        result = self._call(provider, df, "1h", now_offset_minutes=63)
        assert result == "INCREMENTAL"

    def test_incremental_multiple_bars_missed_within_day(self, provider):
        """2h gap on 30m bars (4 bars missed) → still same day → INCREMENTAL."""
        df = _make_df(10, 30)
        result = self._call(provider, df, "30m", now_offset_minutes=120)
        assert result == "INCREMENTAL"

    # --- FULL ---

    def test_full_when_gap_exceeds_threshold(self, provider):
        """Gap > 1440 min (1 day) → FULL."""
        df = _make_df(10, 30)
        result = self._call(provider, df, "30m", now_offset_minutes=_FULL_REFRESH_GAP_MINUTES + 1)
        assert result == "FULL"

    def test_full_when_new_trading_day(self, provider):
        """Cache from yesterday, now is today → new trading day → FULL."""
        yesterday = datetime(2026, 4, 14, 15, 30, tzinfo=timezone.utc)
        df = _make_df(10, 30, end=yesterday)
        today_morning = datetime(2026, 4, 15, 9, 35, tzinfo=timezone.utc)

        with patch("data_feed.alpaca_provider.datetime") as mock_dt:
            mock_dt.now.return_value = today_morning
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = provider._fetch_strategy(df, "30m")

        assert result == "FULL"

    def test_full_gap_takes_priority_over_same_day(self, provider):
        """Even within same day, gap > 1440 min → FULL (shouldn't happen in practice)."""
        df = _make_df(10, 30)
        result = self._call(provider, df, "30m", now_offset_minutes=1500)
        assert result == "FULL"


# ---------------------------------------------------------------------------
# _cache_covers_period
# ---------------------------------------------------------------------------

class TestCacheCoversPeriod:
    @pytest.fixture
    def provider(self, tmp_path):
        return _make_provider(tmp_path)

    def test_sufficient_coverage_returns_true(self, provider):
        df = _make_daily_df(n_days=65)  # covers 60d + margin
        assert provider._cache_covers_period(df, "60d") is True

    def test_insufficient_coverage_returns_false(self, provider):
        df = _make_daily_df(n_days=30)  # only 30d
        assert provider._cache_covers_period(df, "200d") is False

    def test_tolerance_10pct(self, provider):
        """Cache that covers 91% of period should pass (tolerance = 10%)."""
        td = timedelta(days=60)
        # data starts 55 days ago (91.7% of 60d)
        end   = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=55)
        idx   = pd.date_range(start=start, end=end, freq="D", tz="UTC")
        df    = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0,
                               "close": 100.5, "volume": 1000}, index=idx)
        assert provider._cache_covers_period(df, "60d") is True

    def test_just_outside_tolerance(self, provider):
        """Cache that covers only 80% of period should fail."""
        end   = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=48)   # 80% of 60d
        idx   = pd.date_range(start=start, end=end, freq="D", tz="UTC")
        df    = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0,
                               "close": 100.5, "volume": 1000}, index=idx)
        assert provider._cache_covers_period(df, "60d") is False


# ---------------------------------------------------------------------------
# Incremental append logic
# ---------------------------------------------------------------------------

class TestIncrementalAppend:
    @pytest.fixture
    def provider(self, tmp_path):
        return _make_provider(tmp_path)

    def test_append_new_bars(self, provider, tmp_path):
        """New bars are appended and the merged result has more rows than the original."""
        existing  = _make_df(10, 30)
        # 2 genuinely new bars (distinct timestamps after the last existing bar)
        new_bars  = pd.DataFrame(
            {"open": 102.0, "high": 103.0, "low": 101.0, "close": 102.5, "volume": 2000},
            index=pd.DatetimeIndex([
                existing.index.max() + timedelta(minutes=30),
                existing.index.max() + timedelta(minutes=60),
            ], tz="UTC"),
        )

        with patch.object(provider, "_fetch_strategy", return_value="INCREMENTAL"), \
             patch.object(provider, "_fetch_since", return_value=new_bars), \
             patch.object(provider, "_load_cache_raw", return_value=existing), \
             patch.object(provider, "_cache_covers_period", return_value=True):

            result = provider.get_ohlcv("MSFT", "30m", "60d")

        assert len(result) == len(existing) + len(new_bars)

    def test_no_new_bars_returns_cache(self, provider):
        """If _fetch_since returns empty, the existing cache is returned untouched."""
        existing = _make_df(10, 30)

        with patch.object(provider, "_fetch_strategy", return_value="INCREMENTAL"), \
             patch.object(provider, "_fetch_since", return_value=pd.DataFrame()), \
             patch.object(provider, "_load_cache_raw", return_value=existing), \
             patch.object(provider, "_cache_covers_period", return_value=True):

            result = provider.get_ohlcv("MSFT", "30m", "60d")

        assert len(result) == len(existing)

    def test_deduplication_on_overlap(self, provider):
        """Overlapping bars from incremental fetch are deduplicated."""
        base     = _make_df(10, 30)
        # Overlap: last 2 bars of base + 2 new bars
        overlap  = _make_df(4, 30, end=base.index.max() + timedelta(minutes=60))

        with patch.object(provider, "_fetch_strategy", return_value="INCREMENTAL"), \
             patch.object(provider, "_fetch_since", return_value=overlap), \
             patch.object(provider, "_load_cache_raw", return_value=base), \
             patch.object(provider, "_cache_covers_period", return_value=True):

            result = provider.get_ohlcv("MSFT", "30m", "60d")

        # No duplicate timestamps
        assert result.index.duplicated().sum() == 0

    def test_full_fetch_when_no_cache(self, provider):
        """No cache file → _full_fetch_and_cache is called."""
        fresh = _make_df(100, 30)

        with patch.object(provider, "_load_cache_raw", return_value=None), \
             patch.object(provider, "_full_fetch_and_cache", return_value=fresh) as mock_full:

            result = provider.get_ohlcv("MSFT", "30m", "60d")

        mock_full.assert_called_once()
        assert len(result) == len(fresh)

    def test_full_fetch_on_coverage_miss(self, provider):
        """Cache exists but doesn't cover requested period → full fetch."""
        short_cache = _make_df(10, 30)   # too little history
        fresh       = _make_df(1000, 30)

        with patch.object(provider, "_load_cache_raw", return_value=short_cache), \
             patch.object(provider, "_cache_covers_period", return_value=False), \
             patch.object(provider, "_full_fetch_and_cache", return_value=fresh) as mock_full:

            result = provider.get_ohlcv("MSFT", "30m", "200d")

        mock_full.assert_called_once()

    def test_force_refresh_bypasses_cache(self, provider):
        """force_refresh=True always calls _full_fetch_and_cache."""
        provider._force = True
        fresh = _make_df(100, 30)

        with patch.object(provider, "_full_fetch_and_cache", return_value=fresh) as mock_full, \
             patch.object(provider, "_load_cache_raw") as mock_load:

            provider.get_ohlcv("MSFT", "30m", "60d")

        mock_full.assert_called_once()
        mock_load.assert_not_called()


# ---------------------------------------------------------------------------
# _fetch_since start/end calculation
# ---------------------------------------------------------------------------

class TestFetchSince:
    @pytest.fixture
    def provider(self, tmp_path):
        return _make_provider(tmp_path)

    def test_returns_empty_when_since_too_recent(self, provider):
        """If since + 1 bar >= now - 15min, returns empty immediately (no API call)."""
        # since is only 1 minute ago → start (since+30min) would be in the future
        since = datetime.now(tz=timezone.utc) - timedelta(minutes=1)
        # Guard fires before any alpaca import — no module needed
        result = provider._fetch_since("MSFT", "30m", since=since)
        assert result.empty

    def test_calls_alpaca_with_correct_start(self, provider):
        """Verifies that _fetch_since passes start = since + 1 bar to Alpaca."""
        since          = datetime(2026, 4, 14, 15, 0, tzinfo=timezone.utc)
        expected_start = since + timedelta(minutes=30)

        captured = {}

        class FakeStockBarsRequest:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        mock_bars = MagicMock()
        mock_bars.df = pd.DataFrame()
        mock_client = MagicMock()
        mock_client.get_stock_bars.return_value = mock_bars
        provider._client = mock_client

        # Patch _build_timeframe (avoids the full alpaca import chain in tests)
        with patch("data_feed.alpaca_provider._build_timeframe", return_value=MagicMock()), \
             patch("data_feed.alpaca_provider.StockBarsRequest", FakeStockBarsRequest, create=True):
            # StockBarsRequest is imported locally; patch the module-level binding
            import data_feed.alpaca_provider as mod
            original = getattr(mod, "StockBarsRequest", None)
            try:
                import importlib, types
                # inject FakeStockBarsRequest into the local import context
                fake_requests_mod = types.ModuleType("alpaca.data.requests")
                fake_requests_mod.StockBarsRequest = FakeStockBarsRequest
                with patch.dict("sys.modules", {"alpaca.data.requests": fake_requests_mod}):
                    provider._fetch_since("MSFT", "30m", since=since)
            finally:
                pass  # no cleanup needed for sys.modules (patch.dict restores it)

        assert captured.get("start") == expected_start


# ---------------------------------------------------------------------------
# _load_cache_raw
# ---------------------------------------------------------------------------

class TestLoadCacheRaw:
    @pytest.fixture
    def provider(self, tmp_path):
        return _make_provider(tmp_path)

    def test_returns_none_for_missing_file(self, provider, tmp_path):
        path = str(tmp_path / "missing.parquet")
        assert provider._load_cache_raw(path) is None

    def test_loads_existing_parquet(self, provider, tmp_path):
        df   = _make_df(50, 30)
        path = str(tmp_path / "test.parquet")
        df.to_parquet(path)
        result = provider._load_cache_raw(path)
        assert result is not None
        assert len(result) == 50

    def test_tz_localization(self, provider, tmp_path):
        """Cache files with naive index should be localized to UTC on load."""
        df = _make_df(10, 30)
        df_naive = df.copy()
        df_naive.index = df_naive.index.tz_localize(None)
        path = str(tmp_path / "naive.parquet")
        df_naive.to_parquet(path)
        result = provider._load_cache_raw(path)
        assert result.index.tz is not None

    def test_returns_none_for_corrupted_file(self, provider, tmp_path):
        path = str(tmp_path / "bad.parquet")
        with open(path, "w") as f:
            f.write("not a parquet file")
        assert provider._load_cache_raw(path) is None
