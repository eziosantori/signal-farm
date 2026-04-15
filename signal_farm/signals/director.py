"""
Director: determines daily trend bias from the director (daily) columns
of the aligned DataFrame.

Returns a pd.Series of "LONG", "SHORT", or "NEUTRAL" indexed on the
executor timestamps.
"""
import numpy as np
import pandas as pd


def check_director(aligned_df: pd.DataFrame, slope_threshold_factor: float = 0.0001) -> pd.Series:
    """
    Rules for LONG:
      - dir_sma_fast_slope > slope_threshold (proportional to price)
      - dir_close > dir_sma_fast
      - dir_roc10 > 0

    Rules for SHORT (mirror):
      - dir_sma_fast_slope < -slope_threshold
      - dir_close < dir_sma_fast
      - dir_roc10 < 0

    slope_threshold = slope_threshold_factor * dir_close  (price-proportional)
    """
    required = ["dir_close", "dir_sma_fast", "dir_sma_fast_slope", "dir_roc10"]
    for col in required:
        if col not in aligned_df.columns:
            raise KeyError(f"Director column missing from aligned_df: {col}")

    threshold = slope_threshold_factor * aligned_df["dir_close"]

    long_cond = (
        (aligned_df["dir_sma_fast_slope"] > threshold)
        & (aligned_df["dir_close"] > aligned_df["dir_sma_fast"])
        & (aligned_df["dir_roc10"] > 0)
    )

    short_cond = (
        (aligned_df["dir_sma_fast_slope"] < -threshold)
        & (aligned_df["dir_close"] < aligned_df["dir_sma_fast"])
        & (aligned_df["dir_roc10"] < 0)
    )

    direction = pd.Series("NEUTRAL", index=aligned_df.index, dtype=object)
    direction[long_cond] = "LONG"
    direction[short_cond] = "SHORT"

    # Rows where any director column is NaN → NEUTRAL
    has_nan = aligned_df[required].isna().any(axis=1)
    direction[has_nan] = "NEUTRAL"

    return direction
