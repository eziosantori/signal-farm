"""
Performance metrics computed from a trade log and equity curve.
"""
import numpy as np
import pandas as pd
from typing import Dict, Any


def calc_metrics(trade_log: pd.DataFrame, equity_curve: pd.Series) -> Dict[str, Any]:
    """
    Parameters
    ----------
    trade_log : DataFrame with columns [direction, entry_price, exit_price, pnl_r, pnl_pct]
    equity_curve : Series of portfolio equity values indexed by datetime

    Returns
    -------
    dict with performance metrics
    """
    if trade_log.empty:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "avg_rr": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
            "total_return_pct": 0.0,
            "sharpe_ratio": 0.0,
        }

    wins = trade_log[trade_log["pnl_r"] > 0]
    losses = trade_log[trade_log["pnl_r"] <= 0]

    win_rate = len(wins) / len(trade_log) * 100

    avg_rr = trade_log["pnl_r"].mean()

    gross_profit = wins["pnl_r"].sum() if not wins.empty else 0.0
    gross_loss = abs(losses["pnl_r"].sum()) if not losses.empty else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown
    running_max = equity_curve.cummax()
    drawdown = (equity_curve - running_max) / running_max * 100
    max_drawdown_pct = drawdown.min()

    # Total return
    total_return_pct = (equity_curve.iloc[-1] / equity_curve.iloc[0] - 1) * 100

    # Sharpe ratio (annualized from daily returns)
    # Resample equity curve to daily for Sharpe calculation
    daily_equity = equity_curve.resample("D").last().dropna()
    daily_returns = daily_equity.pct_change().dropna()
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)
    else:
        sharpe = 0.0

    return {
        "total_trades": len(trade_log),
        "win_rate": round(win_rate, 1),
        "avg_rr": round(avg_rr, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "total_return_pct": round(total_return_pct, 2),
        "sharpe_ratio": round(sharpe, 2),
    }
