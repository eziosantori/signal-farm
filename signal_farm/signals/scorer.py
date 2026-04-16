"""
Signal quality scorer — assigns a 0-100 score to every row in the aligned DataFrame.

Score breakdown (100 points total):
  Trend Strength    25pt  — Director daily SMA slope + ROC magnitude
  MTF Alignment     20pt  — Filter timeframe slope direction and strength
  RSI Quality       25pt  — RSI position within target zone (independent of hard filter)
  Entry Precision   15pt  — Trigger proximity + signal-bar candle quality
  Volume Context    10pt  — Relative volume confirms participation
  Volatility Ctx     5pt  — Direction-aware ATR context (momentum vs chaos)

Key design principle: each dimension adds INDEPENDENT information. The signal engine
already guarantees basic trend/alignment/entry/RSI conditions are met; the scorer
measures the DEGREE to which they are met, plus dimensions the engine ignores.

Changelog
---------
v2: Volatility now direction-aware (high vol in aligned trend = momentum, not risk).
    Stop Quality replaced by Confluence (depth_ok sustained over prior bars).
    Entry Precision adds signal-bar candle body quality (conviction bar).
v3: RSI Quality (25pt) added — measures how well-positioned RSI is within the zone.
    Volume Context (10pt) added — relative volume confirms institutional participation.
    Entry Precision reduced 20→15pt, Trend Strength 30→25pt, Volatility 20→5pt.
    Confluence removed (v2 showed near-zero discriminating power in backtests).

Usage
-----
    from signals.scorer import score_signals
    aligned_df["signal_score"] = score_signals(aligned_df, profile, defaults, variant="A")

Only rows where signal == True are meaningful, but the score is computed for all rows
so callers can inspect the full distribution and choose threshold dynamically.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def score_signals(
    aligned_df: pd.DataFrame,
    profile: dict,
    defaults: dict,
    variant: str = "A",
) -> pd.Series:
    """
    Compute a 0–100 quality score for every row in aligned_df.
    Returns a single Series (total score). See score_signals_detailed()
    for the full breakdown with sub-scores and context columns.
    """
    return score_signals_detailed(aligned_df, profile, defaults, variant)["signal_score"]


def score_signals_detailed(
    aligned_df: pd.DataFrame,
    profile: dict,
    defaults: dict,
    variant: str = "A",
) -> pd.DataFrame:
    """
    Compute the full scored + enriched signal DataFrame.

    Score breakdown (100 points, 3 categories)
    -------------------------------------------
    Trend     (0-45):  Trend Strength (25pt) + MTF Alignment (20pt)
    Momentum  (0-30):  RSI Quality (25pt)    + Volatility Context (5pt)
    Entry     (0-25):  Entry Precision (15pt) + Volume Context (10pt)

    Context columns (ctx_*)
    -----------------------
    ctx_trend_label   : "STRONG UP/DOWN", "MODERATE UP/DOWN", "WEAK", "NEUTRAL"
    ctx_roc_pct       : director ROC10 as percentage (e.g. 2.5 = +2.5%)
    ctx_rsi           : executor RSI14 value (e.g. 56.2)
    ctx_rel_vol       : relative volume (e.g. 1.8 = 80% above avg)
    ctx_atr_pct       : ATR14 as % of price (e.g. 0.74)
    ctx_regime        : TRENDING / RANGING / VOLATILE / QUIET
    ctx_setup_bars    : prior aligned bars (depth_ok sustained)

    Returns
    -------
    pd.DataFrame with columns: signal_score, score_trend, score_momentum,
    score_entry, ctx_trend_label, ctx_roc_pct, ctx_rsi, ctx_rel_vol,
    ctx_atr_pct, ctx_regime, ctx_setup_bars.
    """
    # ── Sub-scores ────────────────────────────────────────────────────────
    trend_s   = _trend_strength(aligned_df, defaults)       # 0-25
    mtf_s     = _mtf_alignment(aligned_df, profile)         # 0-20
    rsi_s     = _rsi_quality(aligned_df, profile, variant)  # 0-25
    vol_s     = _volatility_context(aligned_df)             # 0-5
    entry_s   = _entry_precision(aligned_df, variant)       # 0-15
    volume_s  = _volume_context(aligned_df, profile)        # 0-10

    score_trend    = (trend_s + mtf_s).clip(0, 45).round(1)
    score_momentum = (rsi_s  + vol_s).clip(0, 30).round(1)
    score_entry    = (entry_s + volume_s).clip(0, 25).round(1)
    signal_score   = (score_trend + score_momentum + score_entry).clip(0, 100).round(1)

    # ── Context columns ───────────────────────────────────────────────────
    ctx_roc_pct     = (aligned_df["dir_roc10"] * 100).round(2)
    ctx_rsi         = aligned_df.get("exec_rsi14",
                                     pd.Series(50.0, index=aligned_df.index)).round(1)
    ctx_rel_vol     = aligned_df.get("exec_rel_volume",
                                     pd.Series(np.nan, index=aligned_df.index)).round(2)
    ctx_atr_pct     = (aligned_df["exec_atr14"] /
                       aligned_df["exec_close"].replace(0, np.nan) * 100).round(3)

    # Regime from ATR percentile
    atr   = aligned_df["exec_atr14"]
    pct   = atr.rolling(120, min_periods=30).apply(
        lambda x: float(np.mean(x[:-1] < x[-1])) if len(x) > 1 else 0.5, raw=True
    )
    ctx_regime = pd.Series(
        np.select(
            [pct < 0.10, pct <= 0.50, pct <= 0.75],
            ["QUIET", "RANGING", "TRENDING"],
            default="VOLATILE",
        ),
        index=aligned_df.index,
    )

    # Setup bars: consecutive prior depth_ok bars
    depth = aligned_df.get("depth_ok", pd.Series(False, index=aligned_df.index))
    prior = depth.shift(1).fillna(value=0).astype(int)
    ctx_setup_bars = prior.rolling(5, min_periods=1).sum().astype(int)

    # Trend label from director slope + ROC
    factor    = defaults["director"]["slope_threshold_factor"]
    price     = aligned_df["dir_close"].replace(0, np.nan)
    threshold = factor * price
    slope     = aligned_df["dir_sma_fast_slope"]
    roc       = aligned_df["dir_roc10"]
    direction = aligned_df["direction"]
    is_long   = direction == "LONG"

    strong   = (slope.abs() > 3 * threshold) & (roc.abs() > 0.04)
    moderate = (slope.abs() > threshold)     & (roc.abs() > 0.01)

    ctx_trend_label = pd.Series(
        np.select(
            [
                is_long  & strong,
                is_long  & moderate,
                ~is_long & strong,
                ~is_long & moderate,
            ],
            ["STRONG UP", "MODERATE UP", "STRONG DOWN", "MODERATE DOWN"],
            default="WEAK",
        ),
        index=aligned_df.index,
    )
    # NEUTRAL where no direction
    neutral_mask = ~is_long & (direction != "SHORT")
    ctx_trend_label = ctx_trend_label.where(~neutral_mask, other="NEUTRAL")

    return pd.DataFrame({
        "signal_score":   signal_score,
        "score_trend":    score_trend,
        "score_momentum": score_momentum,
        "score_entry":    score_entry,
        "ctx_trend_label": ctx_trend_label,
        "ctx_roc_pct":    ctx_roc_pct,
        "ctx_rsi":        ctx_rsi,
        "ctx_rel_vol":    ctx_rel_vol,
        "ctx_atr_pct":    ctx_atr_pct,
        "ctx_regime":     ctx_regime,
        "ctx_setup_bars": ctx_setup_bars,
    }, index=aligned_df.index)


# ---------------------------------------------------------------------------
# 1. Trend Strength  (25 pt)
# ---------------------------------------------------------------------------

def _trend_strength(df: pd.DataFrame, defaults: dict) -> pd.Series:
    """
    25 pt — How strong and confirmed is the daily director trend?

    slope_score (0-12 pt)
      Normalises |dir_sma_fast_slope| by the price-proportional threshold.
      1× threshold ≈ 2.4 pt; 5× threshold = full 12 pt.

    roc_score (0-13 pt)
      |dir_roc10| in decimal form.
      2 % ROC ≈ 3.25 pt; 8 % ROC = full 13 pt.
    """
    factor = defaults["director"]["slope_threshold_factor"]   # 0.0001

    price = df["dir_close"].replace(0, np.nan)
    threshold = factor * price

    slope_ratio = (df["dir_sma_fast_slope"].abs() / threshold).clip(0, 5) / 5
    slope_score = slope_ratio * 12

    roc_abs = df["dir_roc10"].abs().clip(0, 0.08) / 0.08
    roc_score = roc_abs * 13

    return slope_score + roc_score


# ---------------------------------------------------------------------------
# 2. MTF Alignment  (20 pt)
# ---------------------------------------------------------------------------

def _mtf_alignment(df: pd.DataFrame, profile: dict) -> pd.Series:
    """
    20 pt — How strongly does the intermediate timeframe confirm the direction?

    3-level assets: uses filt_sma_fast_slope.
    2-level assets: uses exec_sma_slow slope (long-side).
    """
    direction = df["direction"]
    is_long  = direction == "LONG"
    is_short = direction == "SHORT"
    price    = df["exec_close"].replace(0, np.nan)
    factor   = 0.0001

    if "filt_sma_fast_slope" in df.columns:
        slope = df["filt_sma_fast_slope"]
    else:
        slope = df.get("exec_sma_slow_long_slope", df.get("exec_sma_slow_slope",
                        pd.Series(0.0, index=df.index)))

    in_dir = ((is_long & (slope > 0)) | (is_short & (slope < 0)))
    mag = (slope.abs() / (factor * price)).clip(0, 5) / 5

    base     = np.where(in_dir, 10.0, 0.0)
    strength = np.where(in_dir, mag * 10, 0.0)

    return pd.Series(base + strength, index=df.index, dtype=float)


# ---------------------------------------------------------------------------
# 3. RSI Quality  (25 pt)  — NEW v3
# ---------------------------------------------------------------------------

def _rsi_quality(df: pd.DataFrame, profile: dict, variant: str) -> pd.Series:
    """
    25 pt — How well-positioned is RSI within the target zone?

    The signal engine enforces RSI zone as a hard gate; this scorer rewards
    signals where RSI is closest to the "sweet spot" — the zone center most
    associated with the entry type (pullback center vs. momentum breakout center).

    Variant A (pullback): sweet spot is the middle of the pullback zone.
      Long:  center of rsi_a_long  (default 50)
      Short: center of rsi_a_short (default 50)

    Variant B (breakout): sweet spot skewed toward momentum.
      Long:  center of rsi_b_long  (default 60)
      Short: center of rsi_b_short (default 40)

    Variant C: blend of A and B sweet spots (arithmetic mean).

    Scoring: Gaussian decay from sweet spot.
      At sweet spot (dist=0):   25 pt
      At zone edge (dist=half): ~8 pt
      RSI unavailable:          12 pt (neutral)
    """
    rsi_cfg = profile.get("rsi_filter", {})
    rsi_a_long  = rsi_cfg.get("variant_a", {}).get("long",  [35, 65])
    rsi_a_short = rsi_cfg.get("variant_a", {}).get("short", [35, 65])
    rsi_b_long  = rsi_cfg.get("variant_b", {}).get("long",  [45, 75])
    rsi_b_short = rsi_cfg.get("variant_b", {}).get("short", [25, 55])

    # Sweet spots per variant
    sweet_a_long  = (rsi_a_long[0]  + rsi_a_long[1])  / 2
    sweet_a_short = (rsi_a_short[0] + rsi_a_short[1]) / 2
    sweet_b_long  = (rsi_b_long[0]  + rsi_b_long[1])  / 2
    sweet_b_short = (rsi_b_short[0] + rsi_b_short[1]) / 2

    if variant == "A":
        sweet_long  = sweet_a_long
        sweet_short = sweet_a_short
        half_long   = (rsi_a_long[1]  - rsi_a_long[0])  / 2
        half_short  = (rsi_a_short[1] - rsi_a_short[0]) / 2
    elif variant == "B":
        sweet_long  = sweet_b_long
        sweet_short = sweet_b_short
        half_long   = (rsi_b_long[1]  - rsi_b_long[0])  / 2
        half_short  = (rsi_b_short[1] - rsi_b_short[0]) / 2
    else:  # C — blend
        sweet_long  = (sweet_a_long  + sweet_b_long)  / 2
        sweet_short = (sweet_a_short + sweet_b_short) / 2
        half_long   = ((rsi_a_long[1]  - rsi_a_long[0])  + (rsi_b_long[1]  - rsi_b_long[0]))  / 4
        half_short  = ((rsi_a_short[1] - rsi_a_short[0]) + (rsi_b_short[1] - rsi_b_short[0])) / 4

    rsi = df.get("exec_rsi14", None)
    if rsi is None:
        return pd.Series(12.0, index=df.index)

    direction = df["direction"]
    is_long   = direction == "LONG"
    is_short  = direction == "SHORT"

    sweet = pd.Series(
        np.where(is_long, sweet_long, np.where(is_short, sweet_short, 50.0)),
        index=df.index,
    )
    half_width = pd.Series(
        np.where(is_long, half_long, np.where(is_short, half_short, 15.0)),
        index=df.index,
    )

    dist = (rsi - sweet).abs()
    # Gaussian decay: score = 25 * exp(-0.5 * (dist / half_width)^2)
    # at dist=0 → 25pt; at dist=half_width → 25*exp(-0.5) ≈ 15pt; at dist=2*half_width → ~3pt
    norm_dist = (dist / half_width.replace(0, np.nan)).clip(0, 3)
    gauss = np.exp(-0.5 * norm_dist ** 2)
    score = gauss * 25.0

    # Neutral rows (NEUTRAL direction) get midpoint
    score = np.where(~is_long & ~is_short, 12.0, score)

    return pd.Series(score, index=df.index, dtype=float)


# ---------------------------------------------------------------------------
# 4. Entry Precision  (15 pt)
# ---------------------------------------------------------------------------

def _entry_precision(df: pd.DataFrame, variant: str) -> pd.Series:
    """
    15 pt — How clean is the entry trigger? Two sub-scores:

    Trigger proximity (9 pt)
      Variant A: |exec_close - sma_fast| / atr14  (0 ATR=9pt, >=2 ATR=0pt)
      Variant B: (close - keltner_upper) / atr14  (fresh break=9pt, extended=0pt)
      Variant C: average of A and B proximity.

    Bar body quality (6 pt)
      Bullish bar for LONG (or bearish for SHORT), body >= 1 ATR = 6pt.
      Doji or counter-direction bar = 0pt.
    """
    direction = df["direction"]
    is_long   = direction == "LONG"
    atr       = df["exec_atr14"].replace(0, np.nan)
    close     = df["exec_close"]
    open_     = df["exec_open"]

    # ── Trigger proximity ────────────────────────────────────────────────────
    sma_fast = pd.Series(
        np.where(
            is_long,
            df.get("exec_sma_fast_long",  df.get("exec_sma_fast", close)),
            df.get("exec_sma_fast_short", df.get("exec_sma_fast", close)),
        ),
        index=df.index,
    )
    dist_a  = (close - sma_fast).abs() / atr
    prox_a  = (1.0 - (dist_a / 2.0).clip(0, 1)) * 9

    ku = df["exec_keltner_upper"]
    kl = df["exec_keltner_lower"]
    breach = pd.Series(
        np.where(is_long, (close - ku) / atr, (kl - close) / atr),
        index=df.index,
    )
    prox_b = pd.Series(
        np.where(breach > 0, (1.0 - (breach.clip(0, 2) / 2.0)) * 9, 0.0),
        index=df.index, dtype=float,
    )

    if variant == "A":
        prox_score = prox_a
    elif variant == "B":
        prox_score = prox_b
    else:
        prox_score = (prox_a + prox_b) / 2.0

    # ── Bar body quality ─────────────────────────────────────────────────────
    body      = (close - open_).abs() / atr
    right_dir = (is_long & (close > open_)) | (~is_long & (close < open_))
    body_score = pd.Series(
        np.where(right_dir, (body.clip(0, 1.0)) * 6, 0.0),
        index=df.index, dtype=float,
    )

    return prox_score + body_score


# ---------------------------------------------------------------------------
# 5. Volume Context  (10 pt)  — NEW v3
# ---------------------------------------------------------------------------

def _volume_context(df: pd.DataFrame, profile: dict) -> pd.Series:
    """
    10 pt — Does volume confirm participation?

    Uses exec_rel_volume (current bar volume / rolling 20-bar mean).
    Values > 1 indicate above-average participation.

    If volume_scorer is False in the profile (e.g. forex tick volume),
    returns a neutral 5 pt for all rows.

    rel_volume >= 2.0 → 10 pt (strong surge)
    rel_volume >= 1.5 →  8 pt
    rel_volume >= 1.2 →  6 pt
    rel_volume >= 1.0 →  4 pt (average)
    rel_volume <  1.0 →  2 pt (below average)
    """
    if not profile.get("volume_scorer", True):
        return pd.Series(5.0, index=df.index)

    rel_vol = df.get("exec_rel_volume", None)
    if rel_vol is None:
        return pd.Series(5.0, index=df.index)

    score = np.select(
        [
            rel_vol >= 2.0,
            rel_vol >= 1.5,
            rel_vol >= 1.2,
            rel_vol >= 1.0,
        ],
        [10.0, 8.0, 6.0, 4.0],
        default=2.0,
    )
    return pd.Series(score, index=df.index, dtype=float)


# ---------------------------------------------------------------------------
# 6. Volatility Context  (5 pt)  — condensed from v2's 20 pt
# ---------------------------------------------------------------------------

def _volatility_context(df: pd.DataFrame, window: int = 120) -> pd.Series:
    """
    5 pt — Direction-aware volatility quality (condensed weight in v3).

    High volatility aligned with trade direction = momentum (good).
    High volatility counter to direction = chaos risk (bad).
    Dead market = no momentum.
    """
    atr       = df["exec_atr14"]
    direction = df["direction"]
    is_long   = direction == "LONG"

    pct = atr.rolling(window, min_periods=30).apply(
        lambda x: float(np.mean(x[:-1] < x[-1])) if len(x) > 1 else 0.5,
        raw=True,
    )

    roc = df["dir_roc10"]
    roc_aligned = (is_long & (roc > 0)) | (~is_long & (roc < 0))
    atr_expanding = atr > atr.shift(10)

    low_vol    = pct < 0.10
    normal_vol = (pct >= 0.10) & (pct <= 0.75)
    high_vol   = pct > 0.75

    score = np.select(
        [
            low_vol,
            normal_vol &  roc_aligned,
            normal_vol & ~roc_aligned,
            high_vol   &  roc_aligned & atr_expanding,
            high_vol   &  roc_aligned & ~atr_expanding,
            high_vol   & ~roc_aligned,
        ],
        [0.0, 5.0, 2.5, 5.0, 3.25, 1.25],
        default=2.5,
    )
    return pd.Series(score, index=df.index, dtype=float)
