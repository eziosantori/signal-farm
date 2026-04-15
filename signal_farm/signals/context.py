"""
Market context — computes the macro regime for a reference instrument
and merges it (bar-by-bar) into the aligned executor DataFrame.

Each asset class defines a `market_context` block in profiles.yaml:

    market_context:
      ticker:      NAS100
      asset_class: indices_futures
      label:       NASDAQ

The context is computed on the reference instrument's 1d data and then
forward-filled into every executor bar, so historical backtests correctly
reflect the market regime at the time of each signal — not just today's.

Regime labels
-------------
  BULL      price > SMA20 > SMA50  AND  ROC20 >  5%
  BULL_MOD  price > SMA20 > SMA50  AND  ROC20 >  0%
  NEUTRAL   mixed (price between SMAs, or flat ROC)
  BEAR_MOD  price < SMA20 < SMA50  AND  ROC20 <  0%
  BEAR      price < SMA20 < SMA50  AND  ROC20 < -5%

Output columns added to aligned DataFrame
-----------------------------------------
  ctx_market_label  : str   — BULL / BULL_MOD / NEUTRAL / BEAR_MOD / BEAR
  ctx_market_roc    : float — ROC20 of reference instrument (decimal, e.g. 0.032)
  ctx_market_name   : str   — human label from profile (e.g. "NASDAQ")
"""
from __future__ import annotations

import logging
from typing import Any, Dict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _compute_regime(daily_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute daily regime label from a 1d OHLCV DataFrame.
    Returns a DataFrame with columns: [date, ctx_market_label, ctx_market_roc].
    """
    close = daily_df["close"]

    sma20 = close.rolling(20, min_periods=10).mean()
    sma50 = close.rolling(50, min_periods=25).mean()
    roc20 = close.pct_change(20)

    above_both = (close > sma20) & (sma20 > sma50)
    below_both = (close < sma20) & (sma20 < sma50)

    def _label(row_above, row_below, roc):
        if row_above:
            return "BULL" if roc > 0.05 else "BULL_MOD"
        elif row_below:
            return "BEAR" if roc < -0.05 else "BEAR_MOD"
        else:
            return "NEUTRAL"

    labels = pd.Series(
        [_label(a, b, r) for a, b, r in zip(above_both, below_both, roc20)],
        index=daily_df.index,
        name="ctx_market_label",
    )

    result = pd.DataFrame({
        "ctx_market_label": labels,
        "ctx_market_roc":   roc20.rename("ctx_market_roc"),
    })
    # Normalize index to date (no time component) for merging
    result.index = result.index.normalize()
    return result


def add_market_context(
    aligned: pd.DataFrame,
    profile: Dict[str, Any],
    provider,
    period: str = "2y",
) -> pd.DataFrame:
    """
    Fetch the reference instrument's 1d data, compute daily regime, and
    merge it into every bar of `aligned` via forward-fill.

    If the market_context block is missing or the fetch fails, adds neutral
    placeholder columns so downstream code never breaks.

    Parameters
    ----------
    aligned   : executor-level aligned DataFrame (output of align_timeframes)
    profile   : asset class profile dict (may contain 'market_context' key)
    provider  : DataProvider instance to fetch reference data
    period    : lookback period (should match the backtest period)

    Returns
    -------
    aligned DataFrame with ctx_market_label, ctx_market_roc, ctx_market_name added.
    """
    ctx_cfg = profile.get("market_context")

    if not ctx_cfg:
        aligned["ctx_market_label"] = "NEUTRAL"
        aligned["ctx_market_roc"]   = 0.0
        aligned["ctx_market_name"]  = "—"
        return aligned

    ref_ticker = ctx_cfg["ticker"]
    ref_label  = ctx_cfg.get("label", ref_ticker)

    aligned["ctx_market_name"] = ref_label

    # Optimisation: if the reference instrument is the same as the signal
    # instrument, we already have dir_* columns — no extra fetch needed.
    # dir_* columns are forward-filled into each executor bar, so we must
    # resample to daily frequency (last bar per day) before computing regime.
    ref_close = aligned.get("dir_close")
    if ref_close is not None and ctx_cfg.get("same_as_signal", False):
        raw_df = pd.DataFrame({
            "close": ref_close,
            "open":  aligned.get("dir_open", ref_close),
            "high":  aligned.get("dir_high", ref_close),
            "low":   aligned.get("dir_low",  ref_close),
        })
        # Resample to 1-day OHLCV so _compute_regime sees proper daily bars
        daily_df = raw_df.resample("1D").agg({
            "open":  "first",
            "high":  "max",
            "low":   "min",
            "close": "last",
        }).dropna(subset=["close"])
    else:
        try:
            # Use the correct provider for the reference ticker — it may differ
            # from the signal provider (e.g. MSFT→AlpacaProvider, NAS100→Dukascopy).
            from data_feed.provider_factory import get_provider as _get_provider
            ref_provider = _get_provider(ref_ticker)
            daily_df = ref_provider.get_ohlcv(ref_ticker, "1d", period)
        except Exception as exc:
            logger.warning("Market context fetch failed for %s: %s", ref_ticker, exc)
            aligned["ctx_market_label"] = "NEUTRAL"
            aligned["ctx_market_roc"]   = 0.0
            return aligned

    regime_df = _compute_regime(daily_df)

    # Merge into aligned: use asof on normalized date so every intraday
    # bar gets the regime as of that calendar day.
    aligned_dates = aligned.index.normalize()

    label_series = pd.Series(index=aligned.index, dtype=object)
    roc_series   = pd.Series(index=aligned.index, dtype=float)

    for ts, norm_date in zip(aligned.index, aligned_dates):
        # Find last regime row on or before this date
        past = regime_df[regime_df.index <= norm_date]
        if past.empty:
            label_series[ts] = "NEUTRAL"
            roc_series[ts]   = 0.0
        else:
            label_series[ts] = past["ctx_market_label"].iloc[-1]
            roc_series[ts]   = past["ctx_market_roc"].iloc[-1]

    aligned["ctx_market_label"] = label_series.fillna("NEUTRAL")
    aligned["ctx_market_roc"]   = roc_series.fillna(0.0)

    logger.info("Market context added: %s (%s)", ref_label, ref_ticker)
    return aligned
