"""
Deterministic backtest tests using synthetic signal DataFrames.
No network calls.
"""
import numpy as np
import pandas as pd
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backtest.engine import run_backtest
from backtest.metrics import calc_metrics


PROFILE = {
    "levels": 2,
    "max_concurrent_positions": 5,
    "max_per_sector": 2,
    "rr_ratio": 2.0,
    "atr_stop_mult": 1.5,
}

DEFAULTS = {
    "risk": {"equity": 100_000, "risk_pct": 0.01, "rr_ratio": 2.0},
    "backtest": {"default_period": "60d", "max_concurrent_positions": 5, "atr_stop_mult": 1.5},
    "director": {"slope_threshold_factor": 0.0001, "roc_threshold": 0.0},
    "signals": {"sma_touch_tolerance": 0.001, "pullback_lookback": 10, "keltner_lookback": 15},
}


def make_signal_df(n=200, inject_signal_at: list = None):
    """
    Build a synthetic executor DataFrame. Injects a LONG signal at specified bar indices.
    """
    idx = pd.date_range("2024-01-01", periods=n, freq="30min", tz="UTC")
    close = pd.Series(100 + np.linspace(0, 10, n), index=idx)

    df = pd.DataFrame({
        "exec_open": close * 0.999,
        "exec_high": close * 1.005,
        "exec_low": close * 0.995,
        "exec_close": close,
        "exec_volume": np.ones(n) * 1e6,
        "dir_sma_fast_slope": np.ones(n) * 0.001,
        "signal": False,
        "direction": "NEUTRAL",
        "entry_price": np.nan,
        "stop": np.nan,
        "target": np.nan,
        "rr": np.nan,
    }, index=idx)

    if inject_signal_at:
        for i in inject_signal_at:
            if i < n:
                price = close.iloc[i]
                stop = price * 0.98
                target = price * 1.04  # 2:1
                df.iloc[i, df.columns.get_loc("signal")] = True
                df.iloc[i, df.columns.get_loc("direction")] = "LONG"
                df.iloc[i, df.columns.get_loc("entry_price")] = price
                df.iloc[i, df.columns.get_loc("stop")] = stop
                df.iloc[i, df.columns.get_loc("target")] = target
                df.iloc[i, df.columns.get_loc("rr")] = 2.0

    return df


def test_no_signals_returns_empty_trade_log():
    df = make_signal_df(n=100, inject_signal_at=[])
    trade_log, equity_curve = run_backtest(df, "us_stocks", PROFILE, DEFAULTS, starting_equity=100_000)
    assert trade_log.empty
    assert len(equity_curve) == 100


def test_single_signal_creates_one_trade():
    df = make_signal_df(n=200, inject_signal_at=[20])
    trade_log, equity_curve = run_backtest(df, "us_stocks", PROFILE, DEFAULTS, starting_equity=100_000)
    assert len(trade_log) == 1


def test_trade_log_schema():
    df = make_signal_df(n=200, inject_signal_at=[20])
    trade_log, _ = run_backtest(df, "us_stocks", PROFILE, DEFAULTS, starting_equity=100_000)
    if not trade_log.empty:
        required_cols = {"entry_time", "exit_time", "direction", "entry_price", "exit_price",
                         "stop", "target", "pnl", "pnl_r", "exit_reason"}
        assert required_cols.issubset(set(trade_log.columns))


def test_equity_curve_starts_at_starting_equity():
    df = make_signal_df(n=200, inject_signal_at=[20])
    _, equity_curve = run_backtest(df, "us_stocks", PROFILE, DEFAULTS, starting_equity=100_000)
    assert equity_curve.iloc[0] == 100_000


def test_equity_curve_length_matches_signal_df():
    df = make_signal_df(n=200, inject_signal_at=[20, 50, 80])
    _, equity_curve = run_backtest(df, "us_stocks", PROFILE, DEFAULTS, starting_equity=100_000)
    assert len(equity_curve) == len(df)


def test_metrics_with_empty_trade_log():
    trade_log = pd.DataFrame()
    idx = pd.date_range("2024-01-01", periods=50, freq="D", tz="UTC")
    equity_curve = pd.Series(100_000.0, index=idx)
    m = calc_metrics(trade_log, equity_curve)
    assert m["total_trades"] == 0
    assert m["win_rate"] == 0.0


def test_metrics_schema():
    df = make_signal_df(n=200, inject_signal_at=[20, 50])
    trade_log, equity_curve = run_backtest(df, "us_stocks", PROFILE, DEFAULTS, starting_equity=100_000)
    m = calc_metrics(trade_log, equity_curve)
    required_keys = {"total_trades", "win_rate", "avg_rr", "profit_factor",
                     "max_drawdown_pct", "total_return_pct", "sharpe_ratio"}
    assert required_keys.issubset(set(m.keys()))


def test_max_concurrent_positions_respected():
    # Inject 10 signals at close bars — only max_concurrent (5) should be taken
    profile = {**PROFILE, "max_concurrent_positions": 3}
    df = make_signal_df(n=300, inject_signal_at=list(range(10, 110, 10)))
    trade_log, _ = run_backtest(df, "us_stocks", profile, DEFAULTS, starting_equity=100_000)
    assert len(trade_log) <= 3
