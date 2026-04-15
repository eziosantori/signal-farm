"""
Visualization: interactive Plotly HTML charts for backtest results.

Outputs saved to ./output/ relative to the project root.
"""
import os
from datetime import date
from typing import Any, Dict

import pandas as pd

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "output")


def _ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def plot_backtest(
    aligned_df: pd.DataFrame,
    trade_log: pd.DataFrame,
    profile: Dict[str, Any],
    variant: str,
    ticker: str,
) -> str:
    """
    Generate a 4-subplot Plotly HTML chart:
      1. Executor candlestick + SMA fast/slow + Keltner (B/C) + signal markers + trade levels
      2. Daily SMA fast slope bar chart (green/red)
      3. ROC 10 daily line
      4. ATR 14 executor

    Returns the path to the saved HTML file.
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        return _plot_matplotlib_fallback(aligned_df, trade_log, variant, ticker)

    _ensure_output_dir()

    today = date.today().isoformat()
    filename = f"{ticker}_{variant}_{today}.html"
    filepath = os.path.join(OUTPUT_DIR, filename)

    df = aligned_df.copy()
    x = df.index

    fig = make_subplots(
        rows=4, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.55, 0.15, 0.15, 0.15],
        subplot_titles=[
            f"{ticker} — Variant {variant} Executor TF",
            "Director SMA Fast Slope",
            "Director ROC 10",
            "Executor ATR 14",
        ],
    )

    # ── Row 1: Candlestick ────────────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=x,
        open=df["exec_open"],
        high=df["exec_high"],
        low=df["exec_low"],
        close=df["exec_close"],
        name="Price",
        increasing_line_color="#26a69a",
        decreasing_line_color="#ef5350",
        showlegend=False,
    ), row=1, col=1)

    if "exec_sma_fast" in df.columns:
        fig.add_trace(go.Scatter(x=x, y=df["exec_sma_fast"], name="SMA Fast",
                                 line=dict(color="royalblue", width=1.2)), row=1, col=1)

    if "exec_sma_slow" in df.columns:
        fig.add_trace(go.Scatter(x=x, y=df["exec_sma_slow"], name="SMA Slow",
                                 line=dict(color="orange", width=1.2)), row=1, col=1)

    if variant in ("B", "C") and "exec_keltner_upper" in df.columns:
        fig.add_trace(go.Scatter(x=x, y=df["exec_keltner_upper"], name="KC Upper",
                                 line=dict(color="purple", width=1, dash="dot")), row=1, col=1)
        fig.add_trace(go.Scatter(x=x, y=df["exec_keltner_mid"], name="KC Mid",
                                 line=dict(color="purple", width=1)), row=1, col=1)
        fig.add_trace(go.Scatter(x=x, y=df["exec_keltner_lower"], name="KC Lower",
                                 line=dict(color="purple", width=1, dash="dot"),
                                 fill="tonexty", fillcolor="rgba(128,0,128,0.05)"), row=1, col=1)

    # Signal markers and trade levels
    if not trade_log.empty:
        for _, trade in trade_log.iterrows():
            color = "green" if trade["direction"] == "LONG" else "red"
            symbol = "triangle-up" if trade["direction"] == "LONG" else "triangle-down"

            # Entry marker
            fig.add_trace(go.Scatter(
                x=[trade["entry_time"]], y=[trade["entry_price"]],
                mode="markers",
                marker=dict(symbol=symbol, color=color, size=10),
                name=f"{trade['direction']} entry",
                showlegend=False,
            ), row=1, col=1)

            # Exit marker
            fig.add_trace(go.Scatter(
                x=[trade["exit_time"]], y=[trade["exit_price"]],
                mode="markers",
                marker=dict(symbol="x", color=color, size=8),
                name="exit",
                showlegend=False,
            ), row=1, col=1)

            # Stop and target lines
            fig.add_shape(
                type="line",
                x0=trade["entry_time"], x1=trade["exit_time"],
                y0=trade["stop"], y1=trade["stop"],
                line=dict(color="red", width=1, dash="dash"),
                row=1, col=1,
            )
            fig.add_shape(
                type="line",
                x0=trade["entry_time"], x1=trade["exit_time"],
                y0=trade["target"], y1=trade["target"],
                line=dict(color="green", width=1, dash="dash"),
                row=1, col=1,
            )

    # ── Row 2: Director slope ─────────────────────────────────────────────────
    if "dir_sma_fast_slope" in df.columns:
        slope = df["dir_sma_fast_slope"].fillna(0)
        colors = ["#26a69a" if v >= 0 else "#ef5350" for v in slope]
        fig.add_trace(go.Bar(x=x, y=slope, name="Dir Slope",
                             marker_color=colors, showlegend=False), row=2, col=1)

    # ── Row 3: ROC 10 ─────────────────────────────────────────────────────────
    if "dir_roc10" in df.columns:
        fig.add_trace(go.Scatter(x=x, y=df["dir_roc10"], name="ROC 10",
                                 line=dict(color="steelblue", width=1), showlegend=False), row=3, col=1)
        fig.add_hline(y=0, line=dict(color="gray", width=0.8, dash="dot"), row=3, col=1)

    # ── Row 4: ATR 14 ─────────────────────────────────────────────────────────
    if "exec_atr14" in df.columns:
        fig.add_trace(go.Scatter(x=x, y=df["exec_atr14"], name="ATR 14",
                                 line=dict(color="goldenrod", width=1), showlegend=False), row=4, col=1)

    fig.update_layout(
        title=f"{ticker} | {profile.get('executor', {}).get('interval', '')} | Variant {variant}",
        xaxis_rangeslider_visible=False,
        height=900,
        template="plotly_dark",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )

    fig.write_html(filepath)
    return filepath


def plot_equity_curve(
    equity_curve: pd.Series,
    trade_log: pd.DataFrame,
    ticker: str,
    variant: str,
) -> str:
    """
    Generate an equity curve Plotly HTML chart.
    Returns the path to the saved HTML file.
    """
    try:
        import plotly.graph_objects as go
    except ImportError:
        return _equity_matplotlib_fallback(equity_curve, ticker, variant)

    _ensure_output_dir()

    today = date.today().isoformat()
    filename = f"{ticker}_{variant}_{today}_equity.html"
    filepath = os.path.join(OUTPUT_DIR, filename)

    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=equity_curve.index,
        y=equity_curve.values,
        mode="lines",
        name="Equity",
        line=dict(color="royalblue", width=2),
        fill="tozeroy",
        fillcolor="rgba(65,105,225,0.1)",
    ))

    # Mark trade exits
    if not trade_log.empty:
        win_trades = trade_log[trade_log["pnl_r"] > 0]
        loss_trades = trade_log[trade_log["pnl_r"] <= 0]

        for _, t in win_trades.iterrows():
            eq_val = equity_curve.asof(t["exit_time"]) if hasattr(equity_curve.index, "asof") else None
            if eq_val is not None:
                fig.add_trace(go.Scatter(
                    x=[t["exit_time"]], y=[eq_val],
                    mode="markers",
                    marker=dict(color="green", size=6, symbol="circle"),
                    showlegend=False,
                ))

        for _, t in loss_trades.iterrows():
            eq_val = equity_curve.asof(t["exit_time"]) if hasattr(equity_curve.index, "asof") else None
            if eq_val is not None:
                fig.add_trace(go.Scatter(
                    x=[t["exit_time"]], y=[eq_val],
                    mode="markers",
                    marker=dict(color="red", size=6, symbol="x"),
                    showlegend=False,
                ))

    fig.update_layout(
        title=f"{ticker} | Variant {variant} — Equity Curve",
        xaxis_title="Date",
        yaxis_title="Portfolio Value ($)",
        template="plotly_dark",
        height=400,
    )

    fig.write_html(filepath)
    return filepath


# ── Matplotlib fallbacks ──────────────────────────────────────────────────────

def _plot_matplotlib_fallback(aligned_df, trade_log, variant, ticker):
    import matplotlib.pyplot as plt
    _ensure_output_dir()
    today = date.today().isoformat()
    filename = f"{ticker}_{variant}_{today}.png"
    filepath = os.path.join(OUTPUT_DIR, filename)

    fig, axes = plt.subplots(4, 1, figsize=(14, 12), sharex=True)
    df = aligned_df

    axes[0].plot(df.index, df["exec_close"], label="Close", linewidth=0.8, color="white")
    if "exec_sma_fast" in df.columns:
        axes[0].plot(df.index, df["exec_sma_fast"], label="SMA Fast", linewidth=1, color="royalblue")
    if "exec_sma_slow" in df.columns:
        axes[0].plot(df.index, df["exec_sma_slow"], label="SMA Slow", linewidth=1, color="orange")
    axes[0].set_title(f"{ticker} Variant {variant}")
    axes[0].legend(fontsize=7)

    if "dir_sma_fast_slope" in df.columns:
        slope = df["dir_sma_fast_slope"].fillna(0)
        colors = ["green" if v >= 0 else "red" for v in slope]
        axes[1].bar(df.index, slope, color=colors, label="Dir Slope")

    if "dir_roc10" in df.columns:
        axes[2].plot(df.index, df["dir_roc10"], color="steelblue", linewidth=0.8)
        axes[2].axhline(0, color="gray", linewidth=0.5, linestyle="--")
        axes[2].set_ylabel("ROC 10")

    if "exec_atr14" in df.columns:
        axes[3].plot(df.index, df["exec_atr14"], color="goldenrod", linewidth=0.8)
        axes[3].set_ylabel("ATR 14")

    plt.tight_layout()
    plt.savefig(filepath, dpi=120)
    plt.close()
    return filepath


def _equity_matplotlib_fallback(equity_curve, ticker, variant):
    import matplotlib.pyplot as plt
    _ensure_output_dir()
    today = date.today().isoformat()
    filename = f"{ticker}_{variant}_{today}_equity.png"
    filepath = os.path.join(OUTPUT_DIR, filename)

    plt.figure(figsize=(12, 4))
    plt.plot(equity_curve.index, equity_curve.values, color="royalblue", linewidth=1.2)
    plt.fill_between(equity_curve.index, equity_curve.values, alpha=0.1, color="royalblue")
    plt.title(f"{ticker} | Variant {variant} — Equity Curve")
    plt.ylabel("Portfolio Value ($)")
    plt.tight_layout()
    plt.savefig(filepath, dpi=120)
    plt.close()
    return filepath
