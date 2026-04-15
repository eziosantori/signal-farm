"""
Depth filter: validates that the executor TF (and optionally the filter TF)
support the directional bias, using direction-specific SMA columns.

2-level profile: executor conditions only.
3-level profile: executor + filter conditions.
"""
import pandas as pd


def check_depth_filter(
    aligned_df: pd.DataFrame,
    direction: pd.Series,
    levels: int,
) -> pd.Series:
    """
    Returns a boolean Series. True means the depth filter passes.

    LONG depth filter (uses long-side slow SMA):
      - exec_close > exec_sma_slow_long  AND  exec_sma_slow_long_slope >= 0
      - (3-level only) filt_sma_fast_slope >= 0

    SHORT depth filter (uses short-side slow SMA):
      - exec_close < exec_sma_slow_short  AND  exec_sma_slow_short_slope <= 0
      - (3-level only) filt_sma_fast_slope <= 0
    """
    # Resolve column names — prefer asymmetric, fall back to unified
    sma_slow_long = _col(aligned_df, "exec_sma_slow_long", "exec_sma_slow")
    sma_slow_short = _col(aligned_df, "exec_sma_slow_short", "exec_sma_slow")
    sma_slow_long_slope = _col(aligned_df, "exec_sma_slow_long_slope", "exec_sma_slow_slope")
    sma_slow_short_slope = _col(aligned_df, "exec_sma_slow_short_slope", "exec_sma_slow_slope")

    long_exec = (
        (aligned_df["exec_close"] > aligned_df[sma_slow_long])
        & (aligned_df[sma_slow_long_slope] >= 0)
    )
    short_exec = (
        (aligned_df["exec_close"] < aligned_df[sma_slow_short])
        & (aligned_df[sma_slow_short_slope] <= 0)
    )

    if levels == 3:
        if "filt_sma_fast_slope" not in aligned_df.columns:
            raise KeyError("filt_sma_fast_slope missing — required for 3-level profile")
        filt_long = aligned_df["filt_sma_fast_slope"] >= 0
        filt_short = aligned_df["filt_sma_fast_slope"] <= 0
    else:
        filt_long = pd.Series(True, index=aligned_df.index)
        filt_short = pd.Series(True, index=aligned_df.index)

    depth_ok = (
        ((direction == "LONG") & long_exec & filt_long)
        | ((direction == "SHORT") & short_exec & filt_short)
    )

    # NaN in any required column → filter fails
    nan_check_cols = [sma_slow_long, sma_slow_short,
                      sma_slow_long_slope, sma_slow_short_slope]
    if levels == 3:
        nan_check_cols.append("filt_sma_fast_slope")
    has_nan = aligned_df[nan_check_cols].isna().any(axis=1)
    depth_ok[has_nan] = False

    return depth_ok


def _col(df: pd.DataFrame, preferred: str, fallback: str) -> str:
    """Return `preferred` if it exists in df, otherwise `fallback`."""
    return preferred if preferred in df.columns else fallback
