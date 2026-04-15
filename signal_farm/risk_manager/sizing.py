"""
Risk management: position sizing, stop/target calculation, correlation filter.
All functions are pure (no side effects).
"""
from typing import List, Dict, Any
import numpy as np


def calc_position_size(
    equity: float,
    risk_pct: float,
    entry: float,
    stop: float,
    size_multiplier: float = 1.0,
) -> float:
    """
    Fixed fractional position sizing with optional ecosystem multiplier.

    Returns number of units (shares/contracts). Returns 0 if risk is zero or negative.

    Parameters
    ----------
    size_multiplier : scaling factor from EcosystemState (default 1.0 = no adjustment).
                      Range 0.5–2.0. Applied after the base size calculation so that
                      signal_score and risk parameters remain unchanged.
    """
    risk_amount = equity * risk_pct
    per_unit_risk = abs(entry - stop)
    if per_unit_risk <= 0:
        return 0.0
    base_size = risk_amount / per_unit_risk
    return base_size * size_multiplier


def calc_stop_loss(
    direction: str,
    entry: float,
    atr: float,
    swing_extreme: float,
    atr_mult: float = 1.5,
) -> float:
    """
    ATR-based stop with swing low/high as a floor/ceiling.

    LONG:  stop = max(swing_low, entry - atr_mult * atr)
    SHORT: stop = min(swing_high, entry + atr_mult * atr)
    """
    if direction == "LONG":
        return max(swing_extreme, entry - atr_mult * atr)
    elif direction == "SHORT":
        return min(swing_extreme, entry + atr_mult * atr)
    raise ValueError(f"Direction must be LONG or SHORT, got: {direction}")


def calc_take_profit(entry: float, stop: float, rr_ratio: float = 2.0) -> float:
    """target = entry ± rr_ratio * |entry - stop|"""
    risk = abs(entry - stop)
    if entry > stop:  # LONG
        return entry + rr_ratio * risk
    else:  # SHORT
        return entry - rr_ratio * risk


def apply_correlation_filter(
    open_positions: List[Dict[str, Any]],
    new_signal: Dict[str, Any],
    max_per_sector: int = 2,
    max_total: int = 5,
) -> bool:
    """
    Returns True if the new signal is allowed given current open positions.

    Checks:
    1. Total concurrent positions < max_total
    2. Same asset_class count < max_per_sector
    """
    if len(open_positions) >= max_total:
        return False

    same_class = sum(
        1 for p in open_positions
        if p.get("asset_class") == new_signal.get("asset_class")
    )
    if same_class >= max_per_sector:
        return False

    return True
