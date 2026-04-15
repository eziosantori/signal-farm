"""
MTF alignment: merges director (daily) and optional filter (4H) onto the executor
timeframe using merge_asof with backward direction. Higher-TF values are shifted
by 1 period before merge so no future information leaks into the signal bar.
"""
import pandas as pd
from typing import Optional


def align_timeframes(
    director_df: pd.DataFrame,
    filter_df: Optional[pd.DataFrame],
    executor_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Merge director (and optionally filter) data onto executor bars.

    Lookahead prevention:
    - director and filter columns are shifted one period within their own
      index BEFORE the merge, so the value visible at executor bar T is the
      last director/filter bar that closed STRICTLY before T.

    Returns a single wide DataFrame indexed on executor timestamps with
    prefixed columns: dir_*, filt_* (if filter provided), exec_*.
    """
    def _reset(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
        """Reset index and ensure the datetime column is named 'datetime'."""
        out = df.copy()
        out.columns = [f"{prefix}{c}" for c in out.columns]
        out.index.name = "datetime"
        result = out.reset_index()
        # Normalise to ns precision so merge_asof keys are compatible across
        # providers (Dukascopy returns ms, yfinance returns ns).
        result["datetime"] = result["datetime"].astype("datetime64[ns, UTC]")
        return result

    exec_reset = _reset(executor_df, "exec_")

    dir_shifted = director_df.copy().shift(1).dropna(how="all")
    dir_reset = _reset(dir_shifted, "dir_")

    merged = pd.merge_asof(
        exec_reset.sort_values("datetime"),
        dir_reset.sort_values("datetime"),
        on="datetime",
        direction="backward",
    )

    if filter_df is not None:
        filt_shifted = filter_df.copy().shift(1).dropna(how="all")
        filt_reset = _reset(filt_shifted, "filt_")

        merged = pd.merge_asof(
            merged.sort_values("datetime"),
            filt_reset.sort_values("datetime"),
            on="datetime",
            direction="backward",
        )

    merged = merged.set_index("datetime").sort_index()
    return merged
