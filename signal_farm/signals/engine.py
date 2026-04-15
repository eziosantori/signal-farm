"""
Signal orchestrator: fetches data, aligns timeframes, computes all indicators,
runs director → depth_filter → variant logic, returns an enriched DataFrame.
"""
import logging
from typing import Dict, Any, Optional

import pandas as pd

from data_feed.provider import DataProvider
from data_feed.alignment import align_timeframes
from indicators.core import calc_sma, calc_sma_slope, calc_roc, calc_atr, calc_keltner, calc_rsi, calc_rel_volume
from signals.director import check_director
from signals.depth_filter import check_depth_filter
from signals.variant_a import variant_a_signals
from signals.variant_b import variant_b_signals
from signals.variant_c import variant_c_signals
from signals.scorer import score_signals, score_signals_detailed
from signals.context import add_market_context

logger = logging.getLogger(__name__)

VARIANTS = {"A", "B", "C"}


def _apply_session_filter(df: pd.DataFrame, sess_cfg: dict) -> pd.DataFrame:
    """
    Filter executor bars to the configured trading session.

    Converts UTC timestamps to the configured timezone (default: America/New_York)
    and keeps only bars whose time falls within [start_et, end_et).

    Parameters in sess_cfg:
        start_et   : "09:30"  (session open, local time)
        end_et     : "16:00"  (session close, local time)
        timezone   : "America/New_York"  (default)
    """
    import pytz
    from datetime import time as dtime

    tz_name = sess_cfg.get("timezone", "America/New_York")
    start_str = sess_cfg.get("start_et", "09:30")
    end_str   = sess_cfg.get("end_et",   "16:00")

    start_h, start_m = (int(x) for x in start_str.split(":"))
    end_h,   end_m   = (int(x) for x in end_str.split(":"))
    t_start = dtime(start_h, start_m)
    t_end   = dtime(end_h,   end_m)

    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        logger.warning("session_filter: unknown timezone %s — skipping filter", tz_name)
        return df

    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")

    local_times = idx.tz_convert(tz)
    local_time_only = local_times.time   # array of time objects

    mask = (local_time_only >= t_start) & (local_time_only < t_end)
    filtered = df[mask]

    n_removed = len(df) - len(filtered)
    if n_removed > 0:
        logger.info(
            "session_filter: kept %d/%d bars (%d pre/after-market removed)",
            len(filtered), len(df), n_removed,
        )
    return filtered


def prepare_aligned(
    ticker: str,
    asset_class: str,
    provider: DataProvider,
    profiles: Dict[str, Any],
    defaults: Dict[str, Any],
    period_override: Optional[str] = None,
) -> tuple:
    """
    Fetch data, compute all indicators, run director + depth_filter.
    Returns (aligned_df, profile, defaults_copy) ready for apply_variant_signals().

    Separating this from signal generation allows the grid optimizer to call
    this once per ticker and then iterate over parameter combinations cheaply.
    """
    if asset_class not in profiles:
        raise KeyError(
            f"Unknown asset class '{asset_class}'. Available: {list(profiles.keys())}"
        )

    import copy as _copy
    profile = profiles[asset_class]
    levels = profile["levels"]

    dir_cfg  = _copy.copy(profile["director"])
    exec_cfg = _copy.copy(profile["executor"])
    filt_cfg = _copy.copy(profile["filter"]) if profile.get("filter") else None

    if period_override:
        dir_cfg["period"]  = period_override
        exec_cfg["period"] = period_override
        if filt_cfg:
            filt_cfg["period"] = period_override
        logger.info("Period override applied: all timeframes → %s", period_override)

    slope_factor = defaults["director"]["slope_threshold_factor"]

    # Fetch
    dir_df = provider.get_ohlcv(ticker, dir_cfg["interval"], dir_cfg["period"])
    filt_df = None
    if levels == 3 and filt_cfg:
        filt_df = provider.get_ohlcv(ticker, filt_cfg["interval"], filt_cfg["period"])
    exec_df = provider.get_ohlcv(ticker, exec_cfg["interval"], exec_cfg["period"])

    # Session filter — trim executor bars to the configured trading session.
    # Essential for US stocks with Alpaca data which includes pre/after-market bars.
    sess_cfg = profile.get("session_filter", {})
    if sess_cfg.get("enabled") and exec_cfg["interval"] not in ("1d", "1wk"):
        exec_df = _apply_session_filter(exec_df, sess_cfg)

    # Director indicators
    dir_df["sma_fast"]       = calc_sma(dir_df["close"], dir_cfg["sma_fast"])
    dir_df["sma_slow"]       = calc_sma(dir_df["close"], dir_cfg["sma_slow"])
    dir_df["sma_fast_slope"] = calc_sma_slope(dir_df["sma_fast"])
    dir_df["roc10"]          = calc_roc(dir_df["close"], dir_cfg["roc_period"])

    if levels == 3 and filt_cfg and filt_df is not None:
        filt_df["sma_fast"]       = calc_sma(filt_df["close"], filt_cfg["sma_fast"])
        filt_df["sma_fast_slope"] = calc_sma_slope(filt_df["sma_fast"])

    aligned = align_timeframes(dir_df, filt_df, exec_df)

    # Executor indicators
    long_cfg  = exec_cfg.get("long",  {"sma_fast": exec_cfg.get("sma_fast", 10),
                                        "sma_slow": exec_cfg.get("sma_slow", 50)})
    short_cfg = exec_cfg.get("short", {"sma_fast": exec_cfg.get("sma_fast", 10),
                                        "sma_slow": exec_cfg.get("sma_slow", 50)})

    for side, cfg in (("long", long_cfg), ("short", short_cfg)):
        sma_f = calc_sma(aligned["exec_close"], cfg["sma_fast"])
        sma_s = calc_sma(aligned["exec_close"], cfg["sma_slow"])
        aligned[f"exec_sma_fast_{side}"]       = sma_f
        aligned[f"exec_sma_slow_{side}"]       = sma_s
        aligned[f"exec_sma_fast_{side}_slope"] = calc_sma_slope(sma_f)
        aligned[f"exec_sma_slow_{side}_slope"] = calc_sma_slope(sma_s)

    aligned["exec_sma_fast"]       = aligned["exec_sma_fast_long"]
    aligned["exec_sma_slow"]       = aligned["exec_sma_slow_long"]
    aligned["exec_sma_fast_slope"] = aligned["exec_sma_fast_long_slope"]
    aligned["exec_sma_slow_slope"] = aligned["exec_sma_slow_long_slope"]

    aligned["exec_atr14"] = calc_atr(
        aligned["exec_high"], aligned["exec_low"], aligned["exec_close"],
        period=exec_cfg["atr_period"],
    )
    kc = calc_keltner(
        aligned["exec_high"], aligned["exec_low"], aligned["exec_close"],
        ema_period=exec_cfg["keltner_ema"],
        atr_period=exec_cfg["keltner_atr"],
        multiplier=exec_cfg["keltner_mult"],
    )
    aligned["exec_keltner_mid"]   = kc["keltner_mid"]
    aligned["exec_keltner_upper"] = kc["keltner_upper"]
    aligned["exec_keltner_lower"] = kc["keltner_lower"]

    rsi_period = profile.get("rsi_period", 14)
    aligned["exec_rsi14"]      = calc_rsi(aligned["exec_close"], period=rsi_period)
    aligned["exec_rel_volume"] = calc_rel_volume(aligned["exec_volume"])

    # Director + depth filter
    direction = check_director(aligned, slope_threshold_factor=slope_factor)

    allowed = set(profile.get("allowed_directions", ["LONG", "SHORT"]))
    blocked = {"LONG", "SHORT"} - allowed
    for d in blocked:
        direction[direction == d] = "NEUTRAL"

    depth_ok = check_depth_filter(aligned, direction, levels=levels)

    aligned["direction"] = direction
    aligned["depth_ok"]  = depth_ok

    # Market context — adds ctx_market_label, ctx_market_roc, ctx_market_name
    period = dir_cfg.get("period", "200d")
    aligned = add_market_context(aligned, profile, provider, period=period)

    return aligned, profile


def apply_variant_signals(
    aligned: pd.DataFrame,
    profile: Dict[str, Any],
    defaults: Dict[str, Any],
    variant: str,
) -> pd.DataFrame:
    """
    Run variant signal logic + scorer on a pre-computed aligned DataFrame.
    Returns aligned with signal/entry_price/stop/target/rr/signal_score columns.

    Use after prepare_aligned() when iterating over parameter combinations
    so data fetching and indicator computation happen only once.
    """
    if variant not in VARIANTS:
        raise ValueError(f"Unknown variant '{variant}'. Must be one of {VARIANTS}")

    rr_ratio    = profile.get("rr_ratio",       defaults["risk"]["rr_ratio"])
    atr_mult    = profile.get("atr_stop_mult",   defaults["backtest"]["atr_stop_mult"])
    pullback_lb = profile.get("pullback_lookback", defaults["signals"]["pullback_lookback"])
    keltner_lb  = profile.get("keltner_lookback",  defaults["signals"]["keltner_lookback"])
    touch_tol   = profile.get("sma_touch_tolerance", defaults["signals"]["sma_touch_tolerance"])

    direction = aligned["direction"]
    depth_ok  = aligned["depth_ok"]

    rsi_cfg     = profile.get("rsi_filter", {})
    rsi_a_long  = tuple(rsi_cfg.get("variant_a", {}).get("long",  [35, 65]))
    rsi_a_short = tuple(rsi_cfg.get("variant_a", {}).get("short", [35, 65]))
    rsi_b_long  = tuple(rsi_cfg.get("variant_b", {}).get("long",  [45, 75]))
    rsi_b_short = tuple(rsi_cfg.get("variant_b", {}).get("short", [25, 55]))

    common_kwargs = dict(
        aligned_df=aligned,
        direction=direction,
        depth_ok=depth_ok,
        touch_tolerance=touch_tol,
        atr_mult=atr_mult,
        rr_ratio=rr_ratio,
    )

    if variant == "A":
        sig_df = variant_a_signals(
            pullback_lookback=pullback_lb,
            rsi_long=rsi_a_long, rsi_short=rsi_a_short,
            **common_kwargs,
        )
    elif variant == "B":
        sig_df = variant_b_signals(
            keltner_lookback=keltner_lb,
            rsi_long=rsi_b_long, rsi_short=rsi_b_short,
            **common_kwargs,
        )
    else:
        sig_df = variant_c_signals(
            pullback_lookback=pullback_lb,
            keltner_lookback=keltner_lb,
            rsi_a_long=rsi_a_long, rsi_a_short=rsi_a_short,
            rsi_b_long=rsi_b_long, rsi_b_short=rsi_b_short,
            **common_kwargs,
        )

    out = aligned.copy()
    out["signal"]      = sig_df["signal"]
    out["entry_price"] = sig_df["entry_price"]
    out["stop"]        = sig_df["stop"]
    out["target"]      = sig_df["target"]
    out["rr"]          = sig_df["rr"]

    # Detailed scoring: sub-scores + context columns
    scored = score_signals_detailed(out, profile, defaults, variant=variant)
    for col in scored.columns:
        out[col] = scored[col]

    return out


def generate_signals(
    ticker: str,
    asset_class: str,
    variant: str,
    provider: DataProvider,
    profiles: Dict[str, Any],
    defaults: Dict[str, Any],
    period_override: Optional[str] = None,
) -> pd.DataFrame:
    """
    Main entry point for signal generation.

    Returns the aligned DataFrame enriched with:
      direction, depth_ok, signal, entry_price, stop, target, rr, signal_score
    """
    if variant not in VARIANTS:
        raise ValueError(f"Unknown variant '{variant}'. Must be one of {VARIANTS}")

    aligned, profile = prepare_aligned(
        ticker, asset_class, provider, profiles, defaults, period_override
    )
    aligned = apply_variant_signals(aligned, profile, defaults, variant)

    n_signals = aligned["signal"].sum()
    logger.info(
        "Signal generation complete: %s | %s | Variant %s → %d signals",
        ticker, asset_class, variant, n_signals,
    )
    return aligned
