"""
Variant B — Keltner Breakout.

Entry logic:
  LONG:
    1. Within the last `keltner_lookback` bars, low touched keltner_mid
       (low <= keltner_mid * (1 + touch_tolerance))
    2. Current close > keltner_upper  (breakout)

  SHORT (mirror):
    1. Within lookback: high >= keltner_mid * (1 - touch_tolerance)
    2. Current close < keltner_lower

Stop:
  LONG:  min(keltner_mid, swing_low)
  SHORT: max(keltner_mid, swing_high)
"""
import numpy as np
import pandas as pd


def variant_b_signals(
    aligned_df: pd.DataFrame,
    direction: pd.Series,
    depth_ok: pd.Series,
    keltner_lookback: int = 15,
    touch_tolerance: float = 0.001,
    atr_mult: float = 1.5,
    rr_ratio: float = 2.0,
    rsi_long: tuple = (45, 75),
    rsi_short: tuple = (25, 55),
) -> pd.DataFrame:
    required = ["exec_close", "exec_low", "exec_high",
                "exec_keltner_mid", "exec_keltner_upper", "exec_keltner_lower",
                "exec_atr14"]
    for col in required:
        if col not in aligned_df.columns:
            raise KeyError(f"Variant B missing column: {col}")

    close = aligned_df["exec_close"]
    low = aligned_df["exec_low"]
    high = aligned_df["exec_high"]
    kc_mid = aligned_df["exec_keltner_mid"]
    kc_upper = aligned_df["exec_keltner_upper"]
    kc_lower = aligned_df["exec_keltner_lower"]
    atr = aligned_df["exec_atr14"]

    N = keltner_lookback

    # Touch detection
    long_touch_thresh = kc_mid * (1 + touch_tolerance)
    touched_long = (low <= long_touch_thresh).rolling(N).apply(
        lambda x: int(x.any()), raw=True
    ).fillna(0).astype(bool)

    short_touch_thresh = kc_mid * (1 - touch_tolerance)
    touched_short = (high >= short_touch_thresh).rolling(N).apply(
        lambda x: int(x.any()), raw=True
    ).fillna(0).astype(bool)

    swing_low = low.rolling(N).min()
    swing_high = high.rolling(N).max()

    # RSI filter — breakout must have momentum, not be overextended
    rsi = aligned_df.get("exec_rsi14", pd.Series(55.0, index=aligned_df.index))
    rsi_ok_long  = (rsi >= rsi_long[0])  & (rsi <= rsi_long[1])
    rsi_ok_short = (rsi >= rsi_short[0]) & (rsi <= rsi_short[1])

    long_signal = (
        (direction == "LONG")
        & depth_ok
        & touched_long
        & (close > kc_upper)
        & rsi_ok_long
        & kc_mid.notna()
    )

    short_signal = (
        (direction == "SHORT")
        & depth_ok
        & touched_short
        & (close < kc_lower)
        & rsi_ok_short
        & kc_mid.notna()
    )

    signal = long_signal | short_signal

    entry_price = close.where(signal, other=np.nan)

    stop_long = np.minimum(kc_mid, swing_low)
    stop_short = np.maximum(kc_mid, swing_high)
    stop = pd.Series(
        np.where(long_signal, stop_long, np.where(short_signal, stop_short, np.nan)),
        index=aligned_df.index,
    )

    risk = (entry_price - stop).abs()
    target_long = entry_price + rr_ratio * risk
    target_short = entry_price - rr_ratio * risk
    target = pd.Series(
        np.where(long_signal, target_long, np.where(short_signal, target_short, np.nan)),
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
