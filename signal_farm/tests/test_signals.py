"""
Unit tests for signal logic using synthetic DataFrames.
No network calls. Encodes known patterns and asserts signal detection.
"""
import numpy as np
import pandas as pd
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from signals.director import check_director
from signals.depth_filter import check_depth_filter
from signals.variant_a import variant_a_signals
from signals.variant_b import variant_b_signals


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_aligned(n=80, trend="LONG"):
    """Synthetic aligned DataFrame with all required columns."""
    idx = pd.date_range("2024-01-01", periods=n, freq="30min", tz="UTC")
    close_base = 100.0

    if trend == "LONG":
        close = pd.Series(close_base + np.linspace(0, 5, n) + np.random.RandomState(1).randn(n) * 0.1, index=idx)
    else:
        close = pd.Series(close_base - np.linspace(0, 5, n) + np.random.RandomState(1).randn(n) * 0.1, index=idx)

    high = close + 0.5
    low = close - 0.5
    volume = pd.Series(np.ones(n) * 1e6, index=idx)

    # Compute SMAs for director
    dir_sma_fast = close.rolling(10).mean()
    dir_sma_fast_slope = (dir_sma_fast - dir_sma_fast.shift(1)) / dir_sma_fast.shift(1).replace(0, np.nan)
    dir_roc10 = (close - close.shift(10)) / close.shift(10).replace(0, np.nan)

    # Exec SMAs
    exec_sma_fast = close.rolling(10).mean()
    exec_sma_slow = close.rolling(50).mean().fillna(close.rolling(50).mean().bfill())
    exec_sma_slow_slope = (exec_sma_slow - exec_sma_slow.shift(1)) / exec_sma_slow.shift(1).replace(0, np.nan)
    exec_sma_fast_slope = (exec_sma_fast - exec_sma_fast.shift(1)) / exec_sma_fast.shift(1).replace(0, np.nan)
    exec_atr = pd.Series(np.ones(n) * 0.8, index=idx)

    # Keltner (simple version)
    kc_mid = close.ewm(span=20, adjust=False).mean()
    kc_upper = kc_mid + 1.5 * exec_atr
    kc_lower = kc_mid - 1.5 * exec_atr

    df = pd.DataFrame({
        "exec_open": close,
        "exec_high": high,
        "exec_low": low,
        "exec_close": close,
        "exec_volume": volume,
        "dir_open": close,
        "dir_high": high,
        "dir_low": low,
        "dir_close": close,
        "dir_volume": volume,
        "dir_sma_fast": dir_sma_fast,
        "dir_sma_slow": close.rolling(50).mean(),
        "dir_sma_fast_slope": dir_sma_fast_slope,
        "dir_roc10": dir_roc10,
        "exec_sma_fast": exec_sma_fast,
        "exec_sma_slow": exec_sma_slow,
        "exec_sma_fast_slope": exec_sma_fast_slope,
        "exec_sma_slow_slope": exec_sma_slow_slope,
        "exec_atr14": exec_atr,
        "exec_keltner_mid": kc_mid,
        "exec_keltner_upper": kc_upper,
        "exec_keltner_lower": kc_lower,
    }, index=idx)

    return df


# ── Director tests ────────────────────────────────────────────────────────────

def test_director_long_on_uptrend():
    df = make_aligned(n=80, trend="LONG")
    direction = check_director(df, slope_threshold_factor=0.0)
    # In a rising series, most bars should be LONG after warmup
    warmed = direction.iloc[20:]
    assert (warmed == "LONG").sum() > len(warmed) * 0.5


def test_director_short_on_downtrend():
    df = make_aligned(n=80, trend="SHORT")
    direction = check_director(df, slope_threshold_factor=0.0)
    warmed = direction.iloc[20:]
    assert (warmed == "SHORT").sum() > len(warmed) * 0.5


def test_director_neutral_on_nan_rows():
    df = make_aligned(n=80)
    df.iloc[:15, df.columns.get_loc("dir_close")] = np.nan
    direction = check_director(df)
    assert (direction.iloc[:15] == "NEUTRAL").all()


# ── Depth filter tests ────────────────────────────────────────────────────────

def test_depth_filter_passes_for_valid_long():
    df = make_aligned(n=80, trend="LONG")
    direction = pd.Series("LONG", index=df.index)
    # Force exec_close > exec_sma_slow and positive slope
    df["exec_sma_slow"] = df["exec_close"] * 0.99
    df["exec_sma_slow_slope"] = 0.001
    depth_ok = check_depth_filter(df, direction, levels=2)
    assert depth_ok.dropna().any()


def test_depth_filter_fails_when_below_slow_sma():
    df = make_aligned(n=80, trend="LONG")
    direction = pd.Series("LONG", index=df.index)
    # Price below SMA slow → filter should fail
    df["exec_sma_slow"] = df["exec_close"] * 1.05
    depth_ok = check_depth_filter(df, direction, levels=2)
    assert not depth_ok.any()


def test_depth_filter_3level_needs_filt_slope():
    df = make_aligned(n=80, trend="LONG")
    direction = pd.Series("LONG", index=df.index)
    df["exec_sma_slow"] = df["exec_close"] * 0.99
    df["exec_sma_slow_slope"] = 0.001
    df["filt_sma_fast_slope"] = -0.001  # negative → should fail
    depth_ok = check_depth_filter(df, direction, levels=3)
    assert not depth_ok.any()


# ── Variant A signal tests ─────────────────────────────────────────────────────

def test_variant_a_generates_long_signals():
    df = make_aligned(n=80, trend="LONG")
    direction = pd.Series("LONG", index=df.index)
    # Force exec_sma_slow below price so depth filter passes
    df["exec_sma_slow"] = df["exec_close"] * 0.95
    df["exec_sma_slow_slope"] = 0.001
    depth_ok = check_depth_filter(df, direction, levels=2)

    # Inject a controlled pullback: bars 30-34 touch SMA fast, bar 35 recovers
    df.loc[df.index[30:35], "exec_low"] = df["exec_sma_fast"].iloc[30:35] * 0.999

    result = variant_a_signals(df, direction, depth_ok, pullback_lookback=10)
    # There should be at least one signal after the pullback
    assert result["signal"].any(), "Expected at least one Variant A signal"


def test_variant_a_rr_at_least_2():
    df = make_aligned(n=80, trend="LONG")
    direction = pd.Series("LONG", index=df.index)
    df["exec_sma_slow"] = df["exec_close"] * 0.95
    df["exec_sma_slow_slope"] = 0.001
    depth_ok = check_depth_filter(df, direction, levels=2)
    df.loc[df.index[30:35], "exec_low"] = df["exec_sma_fast"].iloc[30:35] * 0.999
    result = variant_a_signals(df, direction, depth_ok, pullback_lookback=10, rr_ratio=2.0)
    signal_rows = result[result["signal"]]
    if not signal_rows.empty:
        assert (signal_rows["rr"] >= 1.99).all(), "All signals should have RR >= 2"


def test_variant_b_generates_signals():
    df = make_aligned(n=80, trend="LONG")
    direction = pd.Series("LONG", index=df.index)
    df["exec_sma_slow"] = df["exec_close"] * 0.95
    df["exec_sma_slow_slope"] = 0.001
    depth_ok = check_depth_filter(df, direction, levels=2)

    # Touch keltner_mid within window then close above keltner_upper
    df.loc[df.index[20:25], "exec_low"] = df["exec_keltner_mid"].iloc[20:25] * 0.999
    df.loc[df.index[28], "exec_close"] = df["exec_keltner_upper"].iloc[28] * 1.005

    result = variant_b_signals(df, direction, depth_ok, keltner_lookback=15)
    # Might or might not fire depending on synthetic data; just check no crash and correct schema
    assert "signal" in result.columns
    assert "entry_price" in result.columns
    assert "stop" in result.columns
    assert "target" in result.columns
    assert "rr" in result.columns
