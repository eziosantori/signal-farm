"""
Unit tests for signals/scanner.py.
No network calls, no file I/O. All data is synthetic.
"""
import sys
import os
import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timezone, time as dtime
import pytz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from signals.scanner import (
    build_ticker_list,
    is_market_open,
    get_last_signal,
    _parse_time,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

INSTRUMENTS = {
    "us_stocks": {
        "MSFT": {"yfinance": "MSFT", "description": "Microsoft", "best_variant": "A", "edge": "strong"},
        "AAPL": {"yfinance": "AAPL", "description": "Apple",     "best_variant": "A", "edge": "none"},
    },
    "crypto": {
        "BTCUSD": {"yfinance": "BTC-USD", "description": "Bitcoin", "best_variant": "B", "ccxt": "BTC/USDT"},
    },
    "forex": {
        "EURUSD": {"yfinance": "EURUSD=X", "description": "EUR/USD", "best_variant": "A"},
        "GBPUSD": {"yfinance": "GBPUSD=X", "description": "GBP/USD", "best_variant": "A"},
    },
}

WATCHLISTS = {
    "us_stocks": ["MSFT", "AAPL"],
    "crypto":    ["BTCUSD"],
    "forex": {
        "majors": ["EURUSD", "GBPUSD"],
    },
}

PROFILE_STOCKS = {
    "scan_hours": {
        "timezone": "US/Eastern",
        "weekdays": [0, 1, 2, 3, 4],
        "start": "09:30",
        "end":   "16:00",
    }
}

PROFILE_CRYPTO = {
    "scan_hours": {"always_open": True}
}

PROFILE_NO_HOURS = {}


def _make_signal_df(n=20, signal_at: list[int] | None = None):
    """Synthetic signal DataFrame. signal_at = list of bar indices where signal=True."""
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame({
        "signal":       False,
        "direction":    "LONG",
        "entry_price":  100.0,
        "stop":         98.0,
        "target":       104.0,
        "rr":           2.0,
        "signal_score": 75.0,
        "score_trend":  30.0,
        "score_momentum": 25.0,
        "score_entry":  20.0,
    }, index=idx)
    if signal_at:
        for i in signal_at:
            df.iloc[i, df.columns.get_loc("signal")] = True
    return df


# ---------------------------------------------------------------------------
# build_ticker_list
# ---------------------------------------------------------------------------

class TestBuildTickerList:
    def test_returns_flat_list(self):
        result = build_ticker_list(INSTRUMENTS, WATCHLISTS)
        assert isinstance(result, list)
        # MSFT, AAPL, BTCUSD, EURUSD, GBPUSD = 5
        assert len(result) == 5

    def test_nested_watchlist_flattened(self):
        """Forex watchlist is nested (majors/crosses) — should be flattened."""
        result = build_ticker_list(INSTRUMENTS, WATCHLISTS)
        canonicals = [r["canonical"] for r in result]
        assert "EURUSD" in canonicals
        assert "GBPUSD" in canonicals

    def test_asset_class_filter(self):
        result = build_ticker_list(INSTRUMENTS, WATCHLISTS, asset_classes=["us_stocks"])
        assert all(r["asset_class"] == "us_stocks" for r in result)
        assert len(result) == 2

    def test_ticker_field_uses_yfinance(self):
        result = build_ticker_list(INSTRUMENTS, WATCHLISTS, asset_classes=["crypto"])
        assert result[0]["ticker"] == "BTC-USD"

    def test_best_variant_from_instruments(self):
        result = build_ticker_list(INSTRUMENTS, WATCHLISTS, asset_classes=["crypto"])
        assert result[0]["best_variant"] == "B"

    def test_missing_symbol_skipped(self):
        watchlists_bad = {"us_stocks": ["MSFT", "UNKNOWN_SYM"]}
        result = build_ticker_list(INSTRUMENTS, watchlists_bad)
        canonicals = [r["canonical"] for r in result]
        assert "UNKNOWN_SYM" not in canonicals
        assert "MSFT" in canonicals

    def test_empty_asset_class_filter(self):
        result = build_ticker_list(INSTRUMENTS, WATCHLISTS, asset_classes=["precious_metals"])
        assert result == []


# ---------------------------------------------------------------------------
# is_market_open
# ---------------------------------------------------------------------------

class TestIsMarketOpen:
    def test_always_open(self):
        assert is_market_open(PROFILE_CRYPTO) is True

    def test_no_scan_hours_defaults_open(self):
        assert is_market_open(PROFILE_NO_HOURS) is True

    def test_us_stocks_open_during_session(self):
        # Wednesday 2024-01-03 10:00 ET = 15:00 UTC
        now = datetime(2024, 1, 3, 15, 0, 0, tzinfo=timezone.utc)
        assert is_market_open(PROFILE_STOCKS, now=now) is True

    def test_us_stocks_closed_before_open(self):
        # Wednesday 2024-01-03 08:00 ET = 13:00 UTC
        now = datetime(2024, 1, 3, 13, 0, 0, tzinfo=timezone.utc)
        assert is_market_open(PROFILE_STOCKS, now=now) is False

    def test_us_stocks_closed_after_close(self):
        # Wednesday 2024-01-03 17:00 ET = 22:00 UTC
        now = datetime(2024, 1, 3, 22, 0, 0, tzinfo=timezone.utc)
        assert is_market_open(PROFILE_STOCKS, now=now) is False

    def test_us_stocks_closed_on_weekend(self):
        # Saturday 2024-01-06 12:00 ET
        now = datetime(2024, 1, 6, 17, 0, 0, tzinfo=timezone.utc)
        assert is_market_open(PROFILE_STOCKS, now=now) is False

    def test_us_stocks_open_at_exact_open(self):
        # Wednesday 2024-01-03 09:30 ET = 14:30 UTC
        now = datetime(2024, 1, 3, 14, 30, 0, tzinfo=timezone.utc)
        assert is_market_open(PROFILE_STOCKS, now=now) is True

    def test_us_stocks_open_at_exact_close(self):
        # Wednesday 2024-01-03 16:00 ET = 21:00 UTC
        now = datetime(2024, 1, 3, 21, 0, 0, tzinfo=timezone.utc)
        assert is_market_open(PROFILE_STOCKS, now=now) is True

    def test_utc_profile(self):
        profile = {
            "scan_hours": {
                "timezone": "UTC",
                "weekdays": [0, 1, 2, 3, 4],
                "start": "00:00",
                "end":   "22:00",
            }
        }
        # Monday 2024-01-01 12:00 UTC → open
        now = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert is_market_open(profile, now=now) is True

        # Monday 2024-01-01 23:00 UTC → closed
        now_closed = datetime(2024, 1, 1, 23, 0, 0, tzinfo=timezone.utc)
        assert is_market_open(profile, now=now_closed) is False

    def test_unknown_timezone_falls_back_to_utc(self):
        profile = {
            "scan_hours": {
                "timezone": "Invalid/Zone",
                "weekdays": [0, 1, 2, 3, 4],
                "start": "00:00",
                "end":   "23:59",
            }
        }
        # Should not raise; returns a result
        now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
        result = is_market_open(profile, now=now)
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# _parse_time
# ---------------------------------------------------------------------------

class TestParseTime:
    def test_parses_hhmm(self):
        assert _parse_time("09:30") == dtime(9, 30)
        assert _parse_time("16:00") == dtime(16, 0)
        assert _parse_time("00:00") == dtime(0, 0)
        assert _parse_time("23:59") == dtime(23, 59)


# ---------------------------------------------------------------------------
# get_last_signal
# ---------------------------------------------------------------------------

class TestGetLastSignal:
    def test_returns_none_when_no_signal(self):
        df = _make_signal_df(n=10, signal_at=[])
        assert get_last_signal(df, lookback_bars=3) is None

    def test_returns_signal_on_last_bar(self):
        df = _make_signal_df(n=10, signal_at=[9])
        result = get_last_signal(df, lookback_bars=1)
        assert result is not None
        assert result["bars_ago"] == 0

    def test_returns_signal_within_lookback(self):
        df = _make_signal_df(n=10, signal_at=[7])   # 2 bars before last
        result = get_last_signal(df, lookback_bars=3)
        assert result is not None
        assert result["bars_ago"] == 2

    def test_no_signal_outside_lookback(self):
        df = _make_signal_df(n=10, signal_at=[5])   # 4 bars before last
        result = get_last_signal(df, lookback_bars=3)
        assert result is None

    def test_returns_most_recent_when_multiple(self):
        df = _make_signal_df(n=10, signal_at=[7, 8])
        result = get_last_signal(df, lookback_bars=5)
        assert result["bars_ago"] == 1   # bar 8 is 1 bar ago (last bar is 9)

    def test_returns_none_on_empty_df(self):
        df = pd.DataFrame()
        assert get_last_signal(df) is None

    def test_returns_none_on_missing_signal_column(self):
        df = pd.DataFrame({"close": [1, 2, 3]})
        assert get_last_signal(df) is None

    def test_result_has_entry_price(self):
        df = _make_signal_df(n=10, signal_at=[9])
        result = get_last_signal(df, lookback_bars=1)
        assert result["entry_price"] == pytest.approx(100.0)

    def test_lookback_1_misses_earlier_signal(self):
        df = _make_signal_df(n=10, signal_at=[8])   # 1 bar before last
        assert get_last_signal(df, lookback_bars=1) is None
        assert get_last_signal(df, lookback_bars=2) is not None
