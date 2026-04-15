"""Unit tests for data_feed/alignment.py — no network calls."""
import numpy as np
import pandas as pd
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_feed.alignment import align_timeframes


def make_daily(n=60):
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    df = pd.DataFrame({
        "open": np.ones(n) * 100,
        "high": np.ones(n) * 102,
        "low": np.ones(n) * 98,
        "close": np.linspace(100, 110, n),
        "volume": np.ones(n) * 1e6,
    }, index=idx)
    return df


def make_intraday(daily_df, freq="30min"):
    """Generate 30-min bars covering the same calendar range."""
    idx = pd.date_range(daily_df.index[0], daily_df.index[-1] + pd.Timedelta(hours=16), freq=freq, tz="UTC")
    n = len(idx)
    df = pd.DataFrame({
        "open": np.ones(n) * 100,
        "high": np.ones(n) * 101,
        "low": np.ones(n) * 99,
        "close": np.linspace(100, 110, n),
        "volume": np.ones(n) * 1e4,
    }, index=idx)
    return df


def test_aligned_has_exec_and_dir_columns():
    dir_df = make_daily()
    exec_df = make_intraday(dir_df)
    aligned = align_timeframes(dir_df, None, exec_df)

    for col in ["exec_open", "exec_close", "dir_open", "dir_close"]:
        assert col in aligned.columns, f"Missing column: {col}"


def test_no_filter_columns_when_none():
    dir_df = make_daily()
    exec_df = make_intraday(dir_df)
    aligned = align_timeframes(dir_df, None, exec_df)
    filt_cols = [c for c in aligned.columns if c.startswith("filt_")]
    assert len(filt_cols) == 0


def test_filter_columns_present_when_provided():
    dir_df = make_daily()
    filt_df = make_intraday(dir_df, freq="4h")
    exec_df = make_intraday(dir_df, freq="1h")
    aligned = align_timeframes(dir_df, filt_df, exec_df)
    filt_cols = [c for c in aligned.columns if c.startswith("filt_")]
    assert len(filt_cols) > 0


def test_lookahead_prevention():
    """
    The first executor bar should see the director value that preceded it,
    not the director value from the same day (which would be lookahead).
    Because shift(1) is applied to director before merge, the first executor
    bar should have NaN in dir_* columns (no prior director bar exists).
    """
    dir_df = make_daily(n=30)
    exec_df = make_intraday(dir_df, freq="30min")
    aligned = align_timeframes(dir_df, None, exec_df)

    # The very first executor bar has no prior director bar → NaN
    first_row = aligned.iloc[0]
    assert pd.isna(first_row.get("dir_close", np.nan)), (
        "First executor bar should see NaN for dir_close (no prior director bar)"
    )


def test_index_is_utc():
    dir_df = make_daily()
    exec_df = make_intraday(dir_df)
    aligned = align_timeframes(dir_df, None, exec_df)
    assert str(aligned.index.tz) == "UTC"


def test_aligned_length_matches_executor():
    dir_df = make_daily()
    exec_df = make_intraday(dir_df)
    aligned = align_timeframes(dir_df, None, exec_df)
    assert len(aligned) == len(exec_df)
