"""
Dashboard data exporter: converts backtest results to JSON format for web visualization.
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from data_feed.provider_factory import get_provider
from signals.engine import generate_signals
from backtest.engine import run_backtest
from backtest.metrics import calc_metrics

logger = logging.getLogger(__name__)


def _max_consecutive(series: pd.Series) -> int:
    """Calculate max consecutive True values."""
    if series.empty or not series.any():
        return 0
    groups = (series != series.shift()).cumsum()
    return series.groupby(groups).sum().max()


def calc_extended_metrics(trade_log: pd.DataFrame, equity_curve: pd.Series) -> Dict[str, Any]:
    """
    Extended metrics beyond the base 7 returned by calc_metrics().

    Adds: wins, losses, avg_win_r, avg_loss_r, max_consecutive_wins/losses,
    avg_trade_duration_bars, expectancy_r
    """
    if trade_log.empty:
        return {
            "wins": 0,
            "losses": 0,
            "avg_win_r": 0.0,
            "avg_loss_r": 0.0,
            "max_consecutive_wins": 0,
            "max_consecutive_losses": 0,
            "avg_trade_duration_bars": 0.0,
            "expectancy_r": 0.0,
        }

    wins = trade_log[trade_log["pnl_r"] > 0]
    losses = trade_log[trade_log["pnl_r"] <= 0]

    avg_trade_duration_bars = (
        (trade_log["exit_time"] - trade_log["entry_time"]).dt.total_seconds().mean() / 1800
        if not trade_log.empty else 0.0
    )

    return {
        "wins": len(wins),
        "losses": len(losses),
        "avg_win_r": round(wins["pnl_r"].mean() if not wins.empty else 0.0, 2),
        "avg_loss_r": round(losses["pnl_r"].mean() if not losses.empty else 0.0, 2),
        "max_consecutive_wins": _max_consecutive(trade_log["pnl_r"] > 0),
        "max_consecutive_losses": _max_consecutive(trade_log["pnl_r"] <= 0),
        "avg_trade_duration_bars": round(avg_trade_duration_bars, 1),
        "expectancy_r": round(trade_log["pnl_r"].mean() if not trade_log.empty else 0.0, 2),
    }


def export_single_backtest(
    ticker: str,
    asset_class: str,
    variant: str,
    provider: Any,
    profiles: Dict[str, Any],
    defaults: Dict[str, Any],
    period: str = "2y",
    output_dir: str = "signal_farm/output/dashboard_data",
) -> str:
    """
    Export a single backtest to JSON.

    Returns the output file path.
    """
    os.makedirs(output_dir, exist_ok=True)

    logger.info(f"Exporting {ticker} {asset_class} variant {variant} {period}...")

    # Generate signals
    signal_df = generate_signals(
        ticker=ticker,
        asset_class=asset_class,
        variant=variant,
        provider=provider,
        profiles=profiles,
        defaults=defaults,
        period_override=period,
    )

    if signal_df.empty:
        logger.warning(f"No data for {ticker}")
        return None

    # Get profile
    profile = profiles[asset_class]

    # Run backtest
    trade_log, equity_curve = run_backtest(
        signal_df,
        asset_class=asset_class,
        profile=profile,
        defaults=defaults,
    )

    # Calculate metrics
    base_metrics = calc_metrics(trade_log, equity_curve)
    extended_metrics = calc_extended_metrics(trade_log, equity_curve)
    metrics = {**base_metrics, **extended_metrics}

    # Get variant label
    variant_label = profile.get("variant_labels", {}).get(variant, f"Variant {variant}")

    # Period dates
    period_start = signal_df.index[0].strftime("%Y-%m-%d")
    period_end = signal_df.index[-1].strftime("%Y-%m-%d")

    # Convert OHLC to JSON format (Unix timestamps)
    ohlc_data = []
    for idx, row in signal_df.iterrows():
        ohlc_data.append({
            "t": int(idx.timestamp()),
            "o": round(float(row["exec_open"]), 2),
            "h": round(float(row["exec_high"]), 2),
            "l": round(float(row["exec_low"]), 2),
            "c": round(float(row["exec_close"]), 2),
            "v": int(row.get("exec_volume", 0)),
        })

    # Extract indicators that exist in the variant
    indicators_map = {
        "sma_fast": "exec_sma_fast",
        "sma_slow": "exec_sma_slow",
        "keltner_upper": "exec_keltner_upper",
        "keltner_mid": "exec_keltner_mid",
        "keltner_lower": "exec_keltner_lower",
        "rsi": "exec_rsi14",
        "atr": "exec_atr14",
        "dir_slope": "dir_sma_fast_slope",
        "dir_roc": "dir_roc10",
    }

    indicators = {}
    for ind_name, col_name in indicators_map.items():
        if col_name in signal_df.columns:
            ind_series = signal_df[col_name].dropna()
            if not ind_series.empty:
                indicators[ind_name] = [
                    {"t": int(idx.timestamp()), "v": round(float(val), 4)}
                    for idx, val in ind_series.items()
                ]

    # Equity and drawdown curves
    equity_data = [
        {"t": int(idx.timestamp()), "v": round(float(val), 2)}
        for idx, val in equity_curve.items()
    ]

    # Calculate drawdown
    running_max = equity_curve.cummax()
    drawdown = (equity_curve - running_max) / running_max * 100
    drawdown_data = [
        {"t": int(idx.timestamp()), "v": round(float(val), 2)}
        for idx, val in drawdown.items()
    ]

    # Convert trades
    trades_data = []
    if not trade_log.empty:
        for _, trade in trade_log.iterrows():
            trade_dict = {
                "entry_time": int(trade["entry_time"].timestamp()),
                "exit_time": int(trade["exit_time"].timestamp()),
                "direction": str(trade["direction"]),
                "entry_price": round(float(trade["entry_price"]), 2),
                "exit_price": round(float(trade["exit_price"]), 2),
                "stop": round(float(trade["stop"]), 2),
                "target": round(float(trade["target"]), 2),
                "pnl_r": round(float(trade["pnl_r"]), 4),
                "pnl_pct": round(float(trade["pnl_pct"]), 2),
                "exit_reason": str(trade.get("exit_reason", "unknown")),
                "signal_score": int(trade.get("signal_score", 0)) if "signal_score" in trade else 0,
            }

            # Add score components if available
            for score_col in ["score_trend", "score_momentum", "score_entry"]:
                if score_col in trade:
                    trade_dict[score_col] = int(trade[score_col])

            trades_data.append(trade_dict)

    # Convert metrics to Python native types (numpy types not JSON serializable)
    def convert_metrics(m):
        return {k: (int(v) if isinstance(v, (int, np.integer)) else float(v) if isinstance(v, (float, np.floating)) else v) for k, v in m.items()}

    metrics = convert_metrics(metrics)

    # Extract readable params from profile
    exec_cfg = profile.get("executor", {})
    dir_cfg  = profile.get("director", {})
    rsi_filter = profile.get("rsi_filter", {})
    variant_key = f"variant_{variant.lower()}"
    params = {
        "director_interval":   dir_cfg.get("interval"),
        "executor_interval":   exec_cfg.get("interval"),
        "sma_fast":            exec_cfg.get("long", {}).get("sma_fast"),
        "sma_slow":            exec_cfg.get("long", {}).get("sma_slow"),
        "rsi_period":          profile.get("rsi_period"),
        "rsi_zone":            rsi_filter.get(variant_key, {}).get("long"),
        "atr_period":          exec_cfg.get("atr_period"),
        "atr_stop_mult":       profile.get("atr_stop_mult"),
        "keltner_ema":         exec_cfg.get("keltner_ema"),
        "keltner_atr":         exec_cfg.get("keltner_atr"),
        "keltner_mult":        exec_cfg.get("keltner_mult"),
        "rr_ratio":            profile.get("rr_ratio"),
        "min_score":           profile.get("min_score_threshold"),
        "max_concurrent":      profile.get("max_concurrent_positions"),
        "allowed_directions":  profile.get("allowed_directions"),
        "pullback_lookback":   profile.get("pullback_lookback"),
        "keltner_lookback":    profile.get("keltner_lookback"),
    }

    # Build JSON structure
    export_data = {
        "params": params,
        "meta": {
            "ticker": ticker,
            "asset_class": asset_class,
            "variant": variant,
            "variant_label": variant_label,
            "period": period,
            "period_start": period_start,
            "period_end": period_end,
            "bars_count": len(signal_df),
            "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
        },
        "metrics": metrics,
        "ohlc": ohlc_data,
        "indicators": indicators,
        "trades": trades_data,
        "equity": equity_data,
        "drawdown": drawdown_data,
    }

    # Save JSON
    output_file = os.path.join(output_dir, f"{ticker}_{variant}_{period}.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(export_data, f, indent=2)

    logger.info(f"Exported to {output_file}")
    return output_file


def export_batch(
    tickers: List[Dict[str, str]],
    provider: Any,
    profiles: Dict[str, Any],
    defaults: Dict[str, Any],
    output_dir: str = "signal_farm/output/dashboard_data",
) -> List[str]:
    """
    Export multiple backtests.

    tickers: list of dicts with keys: ticker, asset_class, variant, period
    Returns list of output files.
    """
    results = []
    for item in tickers:
        try:
            result = export_single_backtest(
                ticker=item["ticker"],
                asset_class=item["asset_class"],
                variant=item["variant"],
                provider=provider,
                profiles=profiles,
                defaults=defaults,
                period=item.get("period", "2y"),
                output_dir=output_dir,
            )
            if result:
                results.append(result)
        except Exception as e:
            logger.error(f"Failed to export {item['ticker']}: {e}")

    return results


def export_correlation_matrix(
    json_dir: str = "signal_farm/output/dashboard_data",
    output_path: str = "signal_farm/output/dashboard_data/correlation_matrix.json",
) -> str:
    """
    Compute correlation matrix from all exported equity curves.

    Aligns equity curves by date and computes Pearson correlation of daily returns.
    """
    import glob

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Find all JSON files
    json_files = glob.glob(os.path.join(json_dir, "*.json"))
    json_files = [f for f in json_files if "correlation" not in f]

    if not json_files:
        logger.warning("No backtest JSON files found")
        return None

    # Load all equity curves
    equity_curves = {}
    tickers = []

    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            meta = data.get("meta", {})
            ticker = meta.get("ticker")
            variant = meta.get("variant")
            key = f"{ticker}_{variant}"

            tickers.append(key)

            # Convert equity data to Series
            equity_list = data.get("equity", [])
            equity_dict = {int(point["t"]): point["v"] for point in equity_list}
            equity_curves[key] = pd.Series(equity_dict).sort_index()
        except Exception as e:
            logger.error(f"Failed to load {json_file}: {e}")

    if not equity_curves:
        logger.warning("No valid equity curves found")
        return None

    # Align all curves by timestamp
    all_timestamps = set()
    for series in equity_curves.values():
        all_timestamps.update(series.index)

    all_timestamps = sorted(all_timestamps)

    # Create aligned DataFrame
    aligned_df = pd.DataFrame(index=all_timestamps)
    for key, series in equity_curves.items():
        aligned_df[key] = series

    # Forward fill gaps
    aligned_df = aligned_df.ffill()

    # Calculate daily returns
    daily_returns = aligned_df.pct_change().dropna()

    # Correlation matrix
    corr_matrix = daily_returns.corr().values.tolist()

    # Rolling 30-day correlation
    rolling_30d = {}
    if len(aligned_df) >= 30:
        rolling_dates = []
        for i in range(30, len(aligned_df)):
            window = daily_returns.iloc[i-30:i]
            date = pd.Timestamp(aligned_df.index[i], unit='s').strftime("%Y-%m-%d")
            rolling_dates.append(date)

        rolling_pairs = {}
        for j, ticker1 in enumerate(tickers):
            for k, ticker2 in enumerate(tickers):
                if j < k:
                    pair_key = f"{ticker1}|{ticker2}"
                    rolling_pairs[pair_key] = []
                    for i in range(30, len(aligned_df)):
                        window = daily_returns.iloc[i-30:i]
                        corr_val = window[ticker1].corr(window[ticker2])
                        rolling_pairs[pair_key].append(round(corr_val, 3) if not np.isnan(corr_val) else 0.0)

        rolling_30d = {
            "dates": rolling_dates,
            "pairs": rolling_pairs,
        }

    # Build output
    output_data = {
        "tickers": tickers,
        "matrix": [[round(val, 3) for val in row] for row in corr_matrix],
        "rolling_30d": rolling_30d,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)

    logger.info(f"Correlation matrix exported to {output_path}")
    return output_path
