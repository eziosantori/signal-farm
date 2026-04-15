"""
Backtest engine: event-driven, single chronological pass.

Design decisions:
- One pass over all bars in time order
- At each bar: (1) update exits for open positions, (2) process pending entry from
  previous bar's signal, (3) check for new signal and schedule next-bar entry
- Position limits are checked against CURRENTLY OPEN positions (not all historical)
- Entry fill: next bar open after signal bar
- Exit: first of stop hit, target hit, forced exit (director slope flip)
- No slippage or commissions in v1
"""
import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from risk_manager.sizing import calc_position_size, apply_correlation_filter

logger = logging.getLogger(__name__)


def _check_exit(
    trade: Dict[str, Any],
    bar_low: float,
    bar_high: float,
    bar_close: float,
    next_open: Optional[float],
    dir_slope: float,
) -> Optional[Dict[str, Any]]:
    """
    Check if an open position exits on the current bar.
    Returns an exit dict or None if still open.
    """
    direction = trade["direction"]
    stop = trade["stop"]
    target = trade["target"]

    if direction == "LONG":
        if bar_low <= stop:
            return {"price": stop, "reason": "stop"}
        if bar_high >= target:
            return {"price": target, "reason": "target"}
        if not np.isnan(dir_slope) and dir_slope < 0:
            price = next_open if next_open is not None else bar_close
            return {"price": price, "reason": "forced"}
    else:  # SHORT
        if bar_high >= stop:
            return {"price": stop, "reason": "stop"}
        if bar_low <= target:
            return {"price": target, "reason": "target"}
        if not np.isnan(dir_slope) and dir_slope > 0:
            price = next_open if next_open is not None else bar_close
            return {"price": price, "reason": "forced"}

    return None


def run_backtest(
    signal_df: pd.DataFrame,
    asset_class: str,
    profile: Dict[str, Any],
    defaults: Dict[str, Any],
    starting_equity: float = 100_000,
    risk_pct: float = 0.01,
) -> tuple[pd.DataFrame, pd.Series]:
    """
    Single-pass event-driven backtester.

    Returns
    -------
    (trade_log DataFrame, equity_curve Series)
    """
    max_concurrent = profile.get(
        "max_concurrent_positions",
        defaults["backtest"]["max_concurrent_positions"],
    )
    max_per_sector = profile.get("max_per_sector", 2)

    if signal_df["signal"].sum() == 0:
        logger.info("No signals found — returning empty trade log")
        return pd.DataFrame(), pd.Series(starting_equity, index=signal_df.index)

    # Pre-extract numpy arrays for speed
    opens = signal_df["exec_open"].to_numpy()
    lows = signal_df["exec_low"].to_numpy()
    highs = signal_df["exec_high"].to_numpy()
    closes = signal_df["exec_close"].to_numpy()
    signals = signal_df["signal"].to_numpy()
    scores = (
        signal_df["signal_score"].to_numpy(dtype=float)
        if "signal_score" in signal_df.columns
        else np.full(len(signal_df), np.nan)
    )

    if "dir_sma_fast_slope" in signal_df.columns:
        dir_slopes = signal_df["dir_sma_fast_slope"].to_numpy(dtype=float)
    else:
        dir_slopes = np.full(len(signal_df), np.nan)

    # Context/sub-score columns to carry into trade_log
    _CTX_COLS = [
        "score_trend", "score_momentum", "score_entry",
        "ctx_trend_label", "ctx_roc_pct", "ctx_rsi",
        "ctx_rel_vol", "ctx_atr_pct", "ctx_regime", "ctx_setup_bars",
        "ctx_market_label", "ctx_market_roc", "ctx_market_name",
    ]
    _ctx_arrays = {
        col: signal_df[col].to_numpy() if col in signal_df.columns else None
        for col in _CTX_COLS
    }

    n = len(signal_df)
    index_list = signal_df.index.tolist()

    equity = starting_equity
    equity_curve_vals = np.full(n, np.nan)
    equity_curve_vals[0] = starting_equity

    open_positions: List[Dict[str, Any]] = []  # currently open trades
    trade_results: List[Dict[str, Any]] = []
    pending_entry: Optional[Dict[str, Any]] = None  # scheduled for next bar open

    for i in range(n):
        bar_low = lows[i]
        bar_high = highs[i]
        bar_close = closes[i]
        bar_open = opens[i]
        bar_ts = index_list[i]
        next_open = opens[i + 1] if i + 1 < n else None
        dir_slope = dir_slopes[i]

        # ── 1. Activate pending entry (from previous bar's signal) ──────────
        if pending_entry is not None:
            entry_price = bar_open
            pe = pending_entry
            pending_entry = None

            # Recalculate stop/target anchored to actual entry open
            orig_risk = abs(pe["orig_entry"] - pe["orig_stop"])
            if orig_risk > 0 and not np.isnan(entry_price):
                direction = pe["direction"]
                if direction == "LONG":
                    stop = entry_price - orig_risk
                    target = entry_price + abs(pe["orig_target"] - pe["orig_entry"])
                else:
                    stop = entry_price + orig_risk
                    target = entry_price - abs(pe["orig_target"] - pe["orig_entry"])

                size = calc_position_size(equity, risk_pct, entry_price, stop)
                if size > 0:
                    pos_ctx = {col: pe.get(col, np.nan) for col in _CTX_COLS}
                    open_positions.append({
                        "entry_time": bar_ts,
                        "signal_time": pe["signal_time"],
                        "direction": direction,
                        "entry_price": entry_price,
                        "stop": stop,
                        "target": target,
                        "size": size,
                        "asset_class": asset_class,
                        "signal_score": pe.get("signal_score", np.nan),
                        **pos_ctx,
                    })

        # ── 2. Check exits for all open positions ───────────────────────────
        still_open = []
        for pos in open_positions:
            exit_info = _check_exit(
                pos, bar_low, bar_high, bar_close, next_open, dir_slope
            )
            if exit_info:
                exit_price = exit_info["price"]
                direction = pos["direction"]
                if direction == "LONG":
                    pnl = (exit_price - pos["entry_price"]) * pos["size"]
                else:
                    pnl = (pos["entry_price"] - exit_price) * pos["size"]

                risk_amount = abs(pos["entry_price"] - pos["stop"]) * pos["size"]
                pnl_r = pnl / risk_amount if risk_amount > 0 else 0.0
                equity += pnl

                tr_ctx = {col: pos.get(col, np.nan) for col in _CTX_COLS}
                trade_results.append({
                    "entry_time": pos["entry_time"],
                    "exit_time": bar_ts,
                    "direction": direction,
                    "entry_price": round(pos["entry_price"], 4),
                    "exit_price": round(exit_price, 4),
                    "stop": round(pos["stop"], 4),
                    "target": round(pos["target"], 4),
                    "size": round(pos["size"], 4),
                    "pnl": round(pnl, 2),
                    "pnl_r": round(pnl_r, 2),
                    "pnl_pct": round(pnl / (equity - pnl) * 100, 4),
                    "exit_reason": exit_info["reason"],
                    "signal_score": round(pos.get("signal_score", np.nan), 1),
                    **tr_ctx,
                })
            else:
                still_open.append(pos)

        open_positions = still_open

        # ── 3. Record equity at this bar ────────────────────────────────────
        equity_curve_vals[i] = equity

        # ── 4. Check for new signal — schedule entry for next bar ───────────
        if signals[i] and i + 1 < n and pending_entry is None:
            row = signal_df.iloc[i]
            orig_entry = row["entry_price"]
            orig_stop = row["stop"]
            orig_target = row["target"]
            direction = row["direction"]

            if (
                not pd.isna(orig_stop)
                and not pd.isna(orig_target)
                and orig_entry != orig_stop
                and direction in ("LONG", "SHORT")
            ):
                new_sig = {"asset_class": asset_class}
                if apply_correlation_filter(
                    open_positions, new_sig,
                    max_per_sector=max_per_sector,
                    max_total=max_concurrent,
                ):
                    ctx_snap = {
                        col: (_ctx_arrays[col][i] if _ctx_arrays[col] is not None else np.nan)
                        for col in _CTX_COLS
                    }
                    pending_entry = {
                        "signal_time": bar_ts,
                        "direction": direction,
                        "orig_entry": orig_entry,
                        "orig_stop": orig_stop,
                        "orig_target": orig_target,
                        "signal_score": scores[i],
                        **ctx_snap,
                    }

    # ── Close any positions still open at end of data ───────────────────────
    last_close = closes[-1]
    last_ts = index_list[-1]
    for pos in open_positions:
        direction = pos["direction"]
        exit_price = last_close
        if direction == "LONG":
            pnl = (exit_price - pos["entry_price"]) * pos["size"]
        else:
            pnl = (pos["entry_price"] - exit_price) * pos["size"]

        risk_amount = abs(pos["entry_price"] - pos["stop"]) * pos["size"]
        pnl_r = pnl / risk_amount if risk_amount > 0 else 0.0
        equity += pnl

        eod_ctx = {col: pos.get(col, np.nan) for col in _CTX_COLS}
        trade_results.append({
            "entry_time": pos["entry_time"],
            "exit_time": last_ts,
            "direction": direction,
            "entry_price": round(pos["entry_price"], 4),
            "exit_price": round(exit_price, 4),
            "stop": round(pos["stop"], 4),
            "target": round(pos["target"], 4),
            "size": round(pos["size"], 4),
            "pnl": round(pnl, 2),
            "pnl_r": round(pnl_r, 2),
            "pnl_pct": round(pnl / max(equity - pnl, 1) * 100, 4),
            "exit_reason": "end_of_data",
            "signal_score": round(pos.get("signal_score", np.nan), 1),
            **eod_ctx,
        })

    equity_curve_vals[-1] = equity

    trade_log = pd.DataFrame(trade_results)
    if not trade_log.empty:
        trade_log = trade_log.sort_values("entry_time").reset_index(drop=True)

    equity_curve = pd.Series(
        equity_curve_vals, index=signal_df.index
    ).ffill().fillna(starting_equity)

    return trade_log, equity_curve
