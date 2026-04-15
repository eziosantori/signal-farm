"""
Variant C — Hybrid (most selective).

Requires BOTH:
  - Variant A SMA touch (direction-specific SMA)
  - Variant B Keltner breakout

Stop: swing_low (LONG) / swing_high (SHORT) over max(pullback_lb, keltner_lb) bars.
"""
import numpy as np
import pandas as pd


def _resolve(df: pd.DataFrame, preferred: str, fallback: str) -> str:
    return preferred if preferred in df.columns else fallback


def variant_c_signals(
    aligned_df: pd.DataFrame,
    direction: pd.Series,
    depth_ok: pd.Series,
    pullback_lookback: int = 10,
    keltner_lookback: int = 15,
    touch_tolerance: float = 0.001,
    atr_mult: float = 1.5,
    rr_ratio: float = 2.0,
    rsi_a_long: tuple = (35, 65),
    rsi_a_short: tuple = (35, 65),
    rsi_b_long: tuple = (45, 75),
    rsi_b_short: tuple = (25, 55),
) -> pd.DataFrame:
    # Resolve direction-specific SMA columns
    sma_fast_long_col = _resolve(aligned_df, "exec_sma_fast_long", "exec_sma_fast")
    sma_fast_short_col = _resolve(aligned_df, "exec_sma_fast_short", "exec_sma_fast")
    slope_long_col = _resolve(aligned_df, "exec_sma_fast_long_slope", "exec_sma_fast_slope")
    slope_short_col = _resolve(aligned_df, "exec_sma_fast_short_slope", "exec_sma_fast_slope")

    required = ["exec_close", "exec_low", "exec_high",
                "exec_keltner_mid", "exec_keltner_upper", "exec_keltner_lower", "exec_atr14",
                sma_fast_long_col, sma_fast_short_col]
    for col in required:
        if col not in aligned_df.columns:
            raise KeyError(f"Variant C missing column: {col}")

    close = aligned_df["exec_close"]
    low = aligned_df["exec_low"]
    high = aligned_df["exec_high"]
    sma_fast_long = aligned_df[sma_fast_long_col]
    sma_fast_short = aligned_df[sma_fast_short_col]
    slope_long = aligned_df[slope_long_col]
    slope_short = aligned_df[slope_short_col]
    kc_mid = aligned_df["exec_keltner_mid"]
    kc_upper = aligned_df["exec_keltner_upper"]
    kc_lower = aligned_df["exec_keltner_lower"]

    N = max(pullback_lookback, keltner_lookback)

    # Variant A touch (direction-specific SMA)
    long_sma_touch = (low <= sma_fast_long * (1 + touch_tolerance)).rolling(pullback_lookback).apply(
        lambda x: int(x.any()), raw=True
    ).fillna(0).astype(bool)
    short_sma_touch = (high >= sma_fast_short * (1 - touch_tolerance)).rolling(pullback_lookback).apply(
        lambda x: int(x.any()), raw=True
    ).fillna(0).astype(bool)

    # Variant B touch of keltner_mid
    long_kc_touch = (low <= kc_mid * (1 + touch_tolerance)).rolling(keltner_lookback).apply(
        lambda x: int(x.any()), raw=True
    ).fillna(0).astype(bool)
    short_kc_touch = (high >= kc_mid * (1 - touch_tolerance)).rolling(keltner_lookback).apply(
        lambda x: int(x.any()), raw=True
    ).fillna(0).astype(bool)

    swing_low = low.rolling(N).min()
    swing_high = high.rolling(N).max()

    # RSI filter — C combines both A (pullback zone) and B (momentum zone):
    # Long: RSI must be above A's lower bound and below B's upper bound
    # Short: symmetric using B's lower bound and A's upper bound
    rsi = aligned_df.get("exec_rsi14", pd.Series(50.0, index=aligned_df.index))
    rsi_ok_long  = (rsi >= rsi_a_long[0])  & (rsi <= rsi_b_long[1])
    rsi_ok_short = (rsi >= rsi_b_short[0]) & (rsi <= rsi_a_short[1])

    long_signal = (
        (direction == "LONG") & depth_ok
        & long_sma_touch & long_kc_touch
        & (close > kc_upper)
        & (close > sma_fast_long)
        & (slope_long >= 0)
        & rsi_ok_long
        & sma_fast_long.notna() & kc_mid.notna()
    )

    short_signal = (
        (direction == "SHORT") & depth_ok
        & short_sma_touch & short_kc_touch
        & (close < kc_lower)
        & (close < sma_fast_short)
        & (slope_short <= 0)
        & rsi_ok_short
        & sma_fast_short.notna() & kc_mid.notna()
    )

    signal = long_signal | short_signal
    entry_price = close.where(signal, other=np.nan)

    stop = pd.Series(
        np.where(long_signal, swing_low, np.where(short_signal, swing_high, np.nan)),
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
