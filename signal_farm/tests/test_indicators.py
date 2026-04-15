"""Unit tests for indicators/core.py — no network calls, synthetic data only."""
import numpy as np
import pandas as pd
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from indicators.core import calc_sma, calc_sma_slope, calc_roc, calc_atr, calc_keltner


@pytest.fixture
def prices():
    np.random.seed(42)
    idx = pd.date_range("2024-01-01", periods=50, freq="D", tz="UTC")
    close = pd.Series(100 + np.cumsum(np.random.randn(50) * 0.5), index=idx)
    return close


@pytest.fixture
def ohlcv():
    np.random.seed(0)
    idx = pd.date_range("2024-01-01", periods=50, freq="D", tz="UTC")
    close = pd.Series(100 + np.cumsum(np.random.randn(50) * 0.5), index=idx)
    high = close + abs(np.random.randn(50) * 0.3)
    low = close - abs(np.random.randn(50) * 0.3)
    return high, low, close


def test_sma_warmup(prices):
    sma = calc_sma(prices, 10)
    assert sma.iloc[:9].isna().all(), "First 9 values should be NaN (warmup)"
    assert sma.iloc[9:].notna().all(), "From period 10 onward should be valid"


def test_sma_value(prices):
    sma = calc_sma(prices, 5)
    expected = prices.iloc[:5].mean()
    assert abs(sma.iloc[4] - expected) < 1e-10


def test_sma_slope_direction():
    idx = pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC")
    rising = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0], index=idx)
    sma = calc_sma(rising, 2)
    slope = calc_sma_slope(sma)
    assert (slope.dropna() > 0).all(), "Slope should be positive for rising SMA"


def test_roc_period(prices):
    roc = calc_roc(prices, 10)
    assert roc.iloc[:10].isna().all()
    # Manually check one value
    expected = (prices.iloc[10] - prices.iloc[0]) / prices.iloc[0]
    assert abs(roc.iloc[10] - expected) < 1e-10


def test_atr_non_negative(ohlcv):
    high, low, close = ohlcv
    atr = calc_atr(high, low, close, period=5)
    assert (atr.dropna() >= 0).all(), "ATR must be non-negative"


def test_atr_warmup(ohlcv):
    high, low, close = ohlcv
    atr = calc_atr(high, low, close, period=14)
    assert atr.iloc[:13].isna().all()


def test_keltner_columns(ohlcv):
    high, low, close = ohlcv
    kc = calc_keltner(high, low, close, ema_period=10, atr_period=5, multiplier=1.5)
    assert set(kc.columns) == {"keltner_mid", "keltner_upper", "keltner_lower"}


def test_keltner_ordering(ohlcv):
    high, low, close = ohlcv
    kc = calc_keltner(high, low, close).dropna()
    assert (kc["keltner_upper"] >= kc["keltner_mid"]).all()
    assert (kc["keltner_mid"] >= kc["keltner_lower"]).all()
