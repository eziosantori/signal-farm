"""
Variant A — SMA Pullback.

Uses direction-specific SMA columns:
  LONG  → exec_sma_fast_long / exec_sma_slow_long
  SHORT → exec_sma_fast_short / exec_sma_slow_short

Entry logic:
  LONG:
    1. Within the last `pullback_lookback` bars, low touched exec_sma_fast_long
    2. Current close > exec_sma_fast_long (pullback recovered)
    3. exec_sma_fast_long_slope >= 0

  SHORT (mirror):
    1. Within lookback: high >= exec_sma_fast_short
    2. Current close < exec_sma_fast_short
    3. exec_sma_fast_short_slope <= 0

Stop:
  LONG:  max(swing_low_of_window, entry - atr_mult * atr14)
  SHORT: min(swing_high_of_window, entry + atr_mult * atr14)
"""
import numpy as np
import pandas as pd


def _resolve(df: pd.DataFrame, preferred: str, fallback: str) -> str:
    return preferred if preferred in df.columns else fallback


def variant_a_signals(
    aligned_df: pd.DataFrame,
    direction: pd.Series,
    depth_ok: pd.Series,
    pullback_lookback: int = 10,
    touch_tolerance: float = 0.001,
    atr_mult: float = 1.5,
    rr_ratio: float = 2.0,
    rsi_long: tuple = (35, 65),
    rsi_short: tuple = (35, 65),
) -> pd.DataFrame:
    """
    Returns a DataFrame with columns:
      signal (bool), entry_price, stop, target, rr
    """
    # Resolve direction-specific SMA column names
    sma_fast_long_col = _resolve(aligned_df, "exec_sma_fast_long", "exec_sma_fast")
    sma_fast_short_col = _resolve(aligned_df, "exec_sma_fast_short", "exec_sma_fast")
    slope_long_col = _resolve(aligned_df, "exec_sma_fast_long_slope", "exec_sma_fast_slope")
    slope_short_col = _resolve(aligned_df, "exec_sma_fast_short_slope", "exec_sma_fast_slope")

    required = ["exec_close", "exec_low", "exec_high", "exec_atr14",
                sma_fast_long_col, sma_fast_short_col,
                slope_long_col, slope_short_col]
    for col in required:
        if col not in aligned_df.columns:
            raise KeyError(f"Variant A missing column: {col}")

    close = aligned_df["exec_close"]
    low = aligned_df["exec_low"]
    high = aligned_df["exec_high"]
    atr = aligned_df["exec_atr14"]
    N = pullback_lookback

    sma_fast_long = aligned_df[sma_fast_long_col]
    sma_fast_short = aligned_df[sma_fast_short_col]
    slope_long = aligned_df[slope_long_col]
    slope_short = aligned_df[slope_short_col]

    # Rolling touch detection
    long_touch_thresh = sma_fast_long * (1 + touch_tolerance)
    touched_long = (low <= long_touch_thresh).rolling(N).apply(
        lambda x: int(x.any()), raw=True
    ).fillna(0).astype(bool)

    short_touch_thresh = sma_fast_short * (1 - touch_tolerance)
    touched_short = (high >= short_touch_thresh).rolling(N).apply(
        lambda x: int(x.any()), raw=True
    ).fillna(0).astype(bool)

    # Swing extremes over lookback window
    swing_low = low.rolling(N).min()
    swing_high = high.rolling(N).max()

    # RSI filter — only trade when RSI is in the pullback zone
    rsi = aligned_df.get("exec_rsi14", pd.Series(50.0, index=aligned_df.index))
    rsi_ok_long  = (rsi >= rsi_long[0])  & (rsi <= rsi_long[1])
    rsi_ok_short = (rsi >= rsi_short[0]) & (rsi <= rsi_short[1])

    long_signal = (
        (direction == "LONG")
        & depth_ok
        & touched_long
        & (close > sma_fast_long)
        & (slope_long >= 0)
        & rsi_ok_long
        & atr.notna()
        & sma_fast_long.notna()
    )

    short_signal = (
        (direction == "SHORT")
        & depth_ok
        & touched_short
        & (close < sma_fast_short)
        & (slope_short <= 0)
        & rsi_ok_short
        & atr.notna()
        & sma_fast_short.notna()
    )

    signal = long_signal | short_signal
    entry_price = close.where(signal, other=np.nan)

    stop_long = np.maximum(swing_low, close - atr_mult * atr)
    stop_short = np.minimum(swing_high, close + atr_mult * atr)
    stop = pd.Series(
        np.where(long_signal, stop_long, np.where(short_signal, stop_short, np.nan)),
        index=aligned_df.index,
    )

    risk = (entry_price - stop).abs()
    target = pd.Series(
        np.where(long_signal, entry_price + rr_ratio * risk,
                 np.where(short_signal, entry_price - rr_ratio * risk, np.nan)),
        index=aligned_df.index,
    )

    rr_actual = (target - entry_price).abs() / risk.replace(0, np.nan)

    return pd.DataFrame({
        "signal": signal,
        "entry_price": entry_price,
        "stop": stop,
        "target": target,
        "rr": rr_actual,
    }, index=aligned_df.index)
