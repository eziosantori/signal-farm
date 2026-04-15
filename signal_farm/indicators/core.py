"""
Pure indicator functions. All inputs are pd.Series; all outputs are pd.Series.
No side effects, no global state.
"""
import numpy as np
import pandas as pd


def calc_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def calc_sma_slope(sma: pd.Series) -> pd.Series:
    """
    Slope of the SMA: absolute price change per bar (current - previous).
    Used with a price-proportional threshold: threshold = factor * price.
    """
    return sma - sma.shift(1)


def calc_roc(series: pd.Series, period: int) -> pd.Series:
    """Rate of change: (current - n_periods_ago) / n_periods_ago."""
    return (series - series.shift(period)) / series.shift(period).replace(0, np.nan)


def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat(
        [
            high - low,
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    RSI with Wilder smoothing (equivalent to EMA alpha=1/period).
    Returns values in [0, 100].
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def calc_rel_volume(volume: pd.Series, period: int = 20) -> pd.Series:
    """
    Relative volume: current bar volume divided by its rolling mean.
    Returns ~1.0 at average volume, >1 for above-average, <1 for below.
    """
    avg = volume.rolling(period, min_periods=5).mean()
    return volume / avg.replace(0, np.nan)


def calc_keltner(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    ema_period: int = 20,
    atr_period: int = 10,
    multiplier: float = 1.5,
) -> pd.DataFrame:
    """
    Returns a DataFrame with columns: keltner_mid, keltner_upper, keltner_lower.
    """
    mid = close.ewm(span=ema_period, adjust=False).mean()
    atr = calc_atr(high, low, close, period=atr_period)
    upper = mid + multiplier * atr
    lower = mid - multiplier * atr
    return pd.DataFrame(
        {"keltner_mid": mid, "keltner_upper": upper, "keltner_lower": lower},
        index=close.index,
    )
