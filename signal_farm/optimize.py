"""
Grid optimizer — pre-fetches data once per ticker, then sweeps parameter
combinations over the signal/backtest logic only (no repeated network calls).

Usage:
    python optimize.py
    python optimize.py --assets EURUSD:forex XAUUSD:precious_metals
    python optimize.py --variants A B C
    python optimize.py --assets BTCUSD:crypto --variants B --period 2y --top 15
"""
import argparse
import copy
import itertools
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import yaml


def _load_dotenv():
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


_load_dotenv()

from data_feed.provider_factory import get_provider
from signals.engine import prepare_aligned, apply_variant_signals
from backtest.engine import run_backtest
from backtest.metrics import calc_metrics

# ── Grid definitions ─────────────────────────────────────────────────────────
#
# rsi_tightening : inward shift (RSI points) from each side of both A and B zones.
#   0 = profile default, 3 = tighter, 6 = tightest.
#
# min_score : only signals with signal_score >= this value are taken.

GRID_A = {
    "pullback_lookback": [8, 10, 15],
    "atr_stop_mult":     [1.5, 2.0],
    "rsi_tightening":    [0, 3, 6],
    "min_score":         [0, 55, 62, 68],
}

GRID_B = {
    "keltner_lookback":  [10, 15, 20],
    "atr_stop_mult":     [1.5, 2.0],
    "rsi_tightening":    [0, 3, 6],
    "min_score":         [0, 55, 62, 68],
}

GRID_C = {
    "pullback_lookback": [8, 10],
    "keltner_lookback":  [12, 15],
    "atr_stop_mult":     [1.5, 2.0],
    "rsi_tightening":    [0, 3, 6],
    "min_score":         [0, 55, 62, 68],
}

# Extended grids for US stocks (2y Alpaca data):
# Win rate hovers ~31% → need wider stops + higher score gate + tighter RSI.
GRID_A_STOCKS = {
    "pullback_lookback": [10, 15, 20],
    "atr_stop_mult":     [1.5, 2.0, 2.5],
    "rsi_tightening":    [0, 3, 6, 9],
    "min_score":         [68, 72, 75, 78],
}

GRID_B_STOCKS = {
    "keltner_lookback":  [10, 15, 20],
    "atr_stop_mult":     [1.5, 2.0, 2.5],
    "rsi_tightening":    [0, 3, 6, 9],
    "min_score":         [68, 72, 75, 78],
}

GRIDS = {"A": GRID_A, "B": GRID_B, "C": GRID_C}

# Asset-class-specific overrides (used when --stock-grid flag is set)
GRIDS_STOCKS = {"A": GRID_A_STOCKS, "B": GRID_B_STOCKS, "C": GRID_C}

# Small focused grid: keep structural params tight, sweep stop width + score gate.
# 18 combos for A/B, 24 for C — fast run, no RSI tightening.
GRID_SMALL_A = {
    "pullback_lookback": [8, 10, 15],
    "atr_stop_mult":     [1.5, 2.0],
    "min_score":         [68, 72, 75],
}
GRID_SMALL_B = {
    "keltner_lookback":  [10, 15, 20],
    "atr_stop_mult":     [1.5, 2.0],
    "min_score":         [68, 72, 75],
}
GRID_SMALL_C = {
    "pullback_lookback": [8, 10],
    "keltner_lookback":  [10, 15],
    "atr_stop_mult":     [1.5, 2.0],
    "min_score":         [68, 72, 75],
}
GRIDS_SMALL = {"A": GRID_SMALL_A, "B": GRID_SMALL_B, "C": GRID_SMALL_C}

MIN_TRADES = 8


# ── Helpers ──────────────────────────────────────────────────────────────────

def _param_combinations(grid: dict):
    keys = list(grid.keys())
    for vals in itertools.product(*[grid[k] for k in keys]):
        yield dict(zip(keys, vals))


def _apply_rsi_tightening(profile: dict, tightening: int) -> dict:
    """Shift RSI zone bounds inward by `tightening` points on each side."""
    if tightening == 0:
        return profile
    rsi_cfg = copy.deepcopy(profile.get("rsi_filter", {}))
    for vkey in ("variant_a", "variant_b"):
        for side in ("long", "short"):
            zone = rsi_cfg.get(vkey, {}).get(side)
            if zone and len(zone) == 2:
                lo, hi = zone[0] + tightening, zone[1] - tightening
                if lo < hi:
                    rsi_cfg.setdefault(vkey, {})[side] = [lo, hi]
    profile["rsi_filter"] = rsi_cfg
    return profile


# ── Core grid runner ─────────────────────────────────────────────────────────

def run_grid(
    ticker: str,
    asset_class: str,
    variant: str,
    aligned_base: pd.DataFrame,      # pre-computed, reused across all combos
    base_profile: dict,
    defaults: dict,
    min_trades: int = MIN_TRADES,
    use_stock_grid: bool = False,
    use_small_grid: bool = False,
) -> pd.DataFrame:
    if use_small_grid:
        grid_map = GRIDS_SMALL
    elif use_stock_grid:
        grid_map = GRIDS_STOCKS
    else:
        grid_map = GRIDS
    grid = grid_map.get(variant, GRIDS[variant])
    combos = list(_param_combinations(grid))
    total = len(combos)

    print(f"\n{'='*62}")
    print(f"  {ticker} | {asset_class} | Variant {variant}  —  {total} combinations")
    print(f"{'='*62}", flush=True)

    results = []

    for idx, params in enumerate(combos, 1):
        profile = copy.deepcopy(base_profile)

        tightening = params.pop("rsi_tightening", 0)
        min_score  = params.pop("min_score", 0)

        for k, v in params.items():
            profile[k] = v
        profile = _apply_rsi_tightening(profile, tightening)

        try:
            sig_df = apply_variant_signals(aligned_base, profile, defaults, variant)
        except Exception as exc:
            print(f"  [SKIP] combo {idx}: {exc}")
            continue

        # Apply min_score gate
        if min_score > 0 and "signal_score" in sig_df.columns:
            sig_df = sig_df.copy()
            sig_df.loc[sig_df["signal_score"] < min_score, "signal"] = False

        raw_signals = int(sig_df["signal"].sum())

        trade_log, equity_curve = run_backtest(
            sig_df, asset_class, profile, defaults,
        )
        metrics = calc_metrics(trade_log, equity_curve)

        if metrics["total_trades"] < min_trades:
            continue

        row = {
            "rsi_tight": tightening,
            "min_score":  min_score,
            **params,
            "raw_sigs":  raw_signals,
            "trades":    metrics["total_trades"],
            "win%":      metrics["win_rate"],
            "PF":        metrics["profit_factor"],
            "MaxDD%":    metrics["max_drawdown_pct"],
            "Ret%":      metrics["total_return_pct"],
            "Sharpe":    metrics["sharpe_ratio"],
        }
        results.append(row)

        print(f"  [{idx:3d}/{total}]  valid: {len(results)}", end="\r", flush=True)

    print(flush=True)

    if not results:
        print("  No valid results (all combos had too few trades).")
        return pd.DataFrame()

    return pd.DataFrame(results).sort_values("Sharpe", ascending=False).reset_index(drop=True)


# ── Pretty printer ────────────────────────────────────────────────────────────

def print_top(df: pd.DataFrame, top_n: int, ticker: str, variant: str):
    if df.empty:
        return

    top = df.head(top_n)
    metric_cols = ["raw_sigs", "trades", "win%", "PF", "MaxDD%", "Ret%", "Sharpe"]
    param_cols  = [c for c in df.columns if c not in metric_cols]

    print(f"\n  TOP {top_n} — {ticker} | Variant {variant}  (ranked by Sharpe)\n")

    col_w  = 11
    header = "  " + "".join(f"{c:>{col_w}}" for c in param_cols + metric_cols)
    print(header)
    print("  " + "-" * (len(header) - 2))

    for _, row in top.iterrows():
        vals = []
        for c in param_cols + metric_cols:
            v = row[c]
            vals.append(f"{v:>{col_w}.2f}" if isinstance(v, float) else f"{v:>{col_w}}")
        print("  " + "".join(vals))

    best = top.iloc[0]
    print(f"\n  Best: ", end="")
    print("  ".join(f"{c}={best[c]}" for c in param_cols))
    print(f"  >> Sharpe={best['Sharpe']:.2f}  PF={best['PF']:.2f}  "
          f"MaxDD={best['MaxDD%']:.1f}%  Ret={best['Ret%']:.1f}%  "
          f"Trades={int(best['trades'])}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="signal_farm grid optimizer")
    parser.add_argument(
        "--assets", nargs="+",
        default=["EURUSD:forex", "XAUUSD:precious_metals"],
        help="TICKER:asset_class pairs",
    )
    parser.add_argument(
        "--variants", nargs="+", default=["B"],
        choices=["A", "B", "C"],
    )
    parser.add_argument("--period", default="2y")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--min-trades", type=int, default=MIN_TRADES)
    parser.add_argument(
        "--stock-grid", action="store_true",
        help="Use the extended us_stocks grid (higher min_score, wider stops)",
    )
    parser.add_argument(
        "--small-grid", action="store_true",
        help="Use the small focused grid (18 combos per variant — fast run)",
    )
    parser.add_argument(
        "--direction", default=None, choices=["LONG", "SHORT", "LONG,SHORT"],
        help="Restrict allowed_directions in the profile (e.g. LONG for bull-only)",
    )
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))

    with open(os.path.join(script_dir, "config", "profiles.yaml")) as f:
        profiles = yaml.safe_load(f)
    with open(os.path.join(script_dir, "config", "defaults.yaml")) as f:
        defaults = yaml.safe_load(f)

    os.makedirs(os.path.join(script_dir, "output"), exist_ok=True)

    all_results = {}

    for asset_spec in args.assets:
        ticker, asset_class = asset_spec.split(":")
        provider = get_provider(ticker)

        print(f"\n>>> Fetching & preparing data for {ticker} ({asset_class}, {args.period})...")

        # Apply direction override before prepare_aligned so the director
        # only generates signals in the requested direction.
        if args.direction:
            profiles[asset_class] = copy.deepcopy(profiles[asset_class])
            profiles[asset_class]["allowed_directions"] = args.direction.split(",")

        aligned_base, base_profile = prepare_aligned(
            ticker=ticker,
            asset_class=asset_class,
            provider=provider,
            profiles=profiles,
            defaults=defaults,
            period_override=args.period,
        )
        print(f"    {len(aligned_base)} bars loaded, ready.\n")

        for variant in args.variants:
            df = run_grid(
                ticker=ticker,
                asset_class=asset_class,
                variant=variant,
                aligned_base=aligned_base,
                base_profile=base_profile,
                defaults=defaults,
                min_trades=args.min_trades,
                use_stock_grid=args.stock_grid,
                use_small_grid=args.small_grid,
            )

            print_top(df, args.top, ticker, variant)

            if not df.empty:
                key = f"{ticker}_{asset_class}_{variant}"
                all_results[key] = df
                out_path = os.path.join(
                    script_dir, "output", f"grid_{key}_{args.period}.csv"
                )
                df.to_csv(out_path, index=False)
                print(f"\n  Saved >> {out_path}")

    if len(all_results) > 1:
        print(f"\n{'='*62}")
        print("  CROSS-ASSET SUMMARY  (best combo per run, by Sharpe)")
        print(f"{'='*62}")
        for key, df in all_results.items():
            b = df.iloc[0]
            print(f"  {key:42s}  Sharpe={b['Sharpe']:5.2f}  PF={b['PF']:5.2f}  "
                  f"Ret={b['Ret%']:+.1f}%  Trades={int(b['trades'])}")


if __name__ == "__main__":
    main()
