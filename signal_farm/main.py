"""
MTF Signal Farm — CLI entry point.

Usage examples:
  python main.py backtest --asset us_stocks --variant A --ticker AAPL
  python main.py backtest --asset crypto --variant B --ticker BTC-USD --output json
  python main.py compare --asset us_stocks --ticker AAPL
  python main.py chart --asset us_stocks --variant A --ticker AAPL
"""
import argparse
import json
import logging
import os
import sys

# Force UTF-8 output on Windows (avoids cp1252 UnicodeEncodeError with box/score chars)
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Ensure signal_farm/ is on the path when run from project root
sys.path.insert(0, os.path.dirname(__file__))

# Load .env from project root (one level up from signal_farm/)
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

import yaml

from data_feed.provider import DataUnavailableError
from data_feed.provider_factory import get_provider
from signals.engine import generate_signals
from backtest.engine import run_backtest
from backtest.metrics import calc_metrics

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")


def load_configs():
    with open(os.path.join(CONFIG_DIR, "profiles.yaml"), encoding="utf-8") as f:
        profiles = yaml.safe_load(f)
    with open(os.path.join(CONFIG_DIR, "defaults.yaml"), encoding="utf-8") as f:
        defaults = yaml.safe_load(f)
    return profiles, defaults


def load_catalog():
    """Load instruments.yaml and watchlists.yaml."""
    with open(os.path.join(CONFIG_DIR, "instruments.yaml"), encoding="utf-8") as f:
        instruments = yaml.safe_load(f)
    with open(os.path.join(CONFIG_DIR, "watchlists.yaml"), encoding="utf-8") as f:
        watchlists = yaml.safe_load(f)
    return instruments, watchlists


def _apply_direction_override(args, profiles):
    """If --direction is specified, override allowed_directions in the profile copy."""
    import copy as _copy
    profiles = _copy.deepcopy(profiles)
    if hasattr(args, "direction") and args.direction:
        dirs = [d.strip().upper() for d in args.direction.split(",")]
        invalid = [d for d in dirs if d not in ("LONG", "SHORT")]
        if invalid:
            print(f"Error: invalid direction(s): {invalid}. Use LONG, SHORT, or LONG,SHORT")
            sys.exit(1)
        profiles[args.asset]["allowed_directions"] = dirs
    return profiles


def _resolve_provider_and_ticker(args):
    """
    Return (provider, effective_ticker) for backtest/compare commands.

    When --provider yfinance is set, bypasses the factory and looks up the
    yfinance ticker from instruments.yaml so Dukascopy is never attempted.
    Otherwise delegates to get_provider() as usual.
    """
    provider_arg = getattr(args, "provider", "auto")

    if provider_arg == "dukascopy":
        from data_feed.provider_factory import get_dukascopy_provider
        import yaml as _yaml
        instr_path = os.path.join(CONFIG_DIR, "instruments.yaml")
        with open(instr_path, encoding="utf-8") as f:
            instruments = _yaml.safe_load(f)
        instruments.pop("timeframes", None)
        # Dukascopy uses canonical ticker — just confirm it has a feed
        feed = None
        for section in instruments.values():
            if isinstance(section, dict) and args.ticker in section:
                feed = section[args.ticker].get("feed")
                break
        if not feed or feed == "~":
            print(f"Error: {args.ticker} has no Dukascopy feed ID in instruments.yaml")
            sys.exit(1)
        print(f"  [provider=dukascopy] feed: {feed}")
        return get_dukascopy_provider(), args.ticker

    if provider_arg != "yfinance":
        return get_provider(args.ticker), args.ticker

    from data_feed.yfinance_provider import YFinanceProvider
    import yaml as _yaml

    instr_path = os.path.join(CONFIG_DIR, "instruments.yaml")
    with open(instr_path, encoding="utf-8") as f:
        instruments = _yaml.safe_load(f)
    instruments.pop("timeframes", None)

    yf_ticker = None
    for section in instruments.values():
        if isinstance(section, dict) and args.ticker in section:
            yf_ticker = section[args.ticker].get("yfinance")
            break

    effective_ticker = yf_ticker or args.ticker
    if yf_ticker:
        print(f"  [provider=yfinance] using ticker: {yf_ticker}")

    return YFinanceProvider(), effective_ticker


def _bar(score, max_score, width=10):
    """Render a simple ASCII progress bar."""
    filled = round((score / max_score) * width) if max_score > 0 else 0
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _safe(val, fmt=".1f", fallback="N/A"):
    try:
        if val != val:  # NaN check
            return fallback
        return format(val, fmt)
    except Exception:
        return fallback


def _print_trade_log(trade_log: "pd.DataFrame"):
    """Print each trade as a structured signal card."""
    has_sub   = "score_trend" in trade_log.columns
    has_ctx   = "ctx_rsi" in trade_log.columns
    has_mkt   = "ctx_market_label" in trade_log.columns

    for _, t in trade_log.iterrows():
        entry_str = t["entry_time"].strftime("%Y-%m-%d %H:%M") if hasattr(t["entry_time"], "strftime") else str(t["entry_time"])[:16]
        exit_str  = t["exit_time"].strftime("%Y-%m-%d %H:%M")  if hasattr(t["exit_time"],  "strftime") else str(t["exit_time"])[:16]
        pnl_str   = f"{t['pnl_r']:+.2f}R"
        outcome   = "✓" if t["pnl_r"] > 0 else "✗"

        print(f"\n  ┌─ {entry_str}  →  {exit_str}  [{t['exit_reason']}]  {outcome} {pnl_str}")
        print(f"  │  {t['direction']:<5}  Entry {t['entry_price']:>10.4f}  Stop {t['stop']:>10.4f}  Target {t['target']:>10.4f}")

        if has_sub:
            sc  = _safe(t.get("signal_score"), ".0f")
            st  = t.get("score_trend",    0)
            sm  = t.get("score_momentum", 0)
            se  = t.get("score_entry",    0)
            print(f"  │")
            print(f"  │  SCORE {sc}/100")
            print(f"  │    Trend     {_bar(st, 45)}  {_safe(st, '.0f'):>4}/45   ({t.get('ctx_trend_label','—')}  ROC {_safe(t.get('ctx_roc_pct'), '+.1f')}%)")
            print(f"  │    Momentum  {_bar(sm, 30)}  {_safe(sm, '.0f'):>4}/30   (RSI {_safe(t.get('ctx_rsi'), '.1f')})")
            print(f"  │    Entry     {_bar(se, 25)}  {_safe(se, '.0f'):>4}/25   (Vol {_safe(t.get('ctx_rel_vol'), '.1f')}x  ATR {_safe(t.get('ctx_atr_pct'), '.2f')}%)")
        elif "signal_score" in t:
            print(f"  │  Score: {_safe(t['signal_score'], '.1f')}/100")

        if has_ctx or has_mkt:
            regime    = t.get("ctx_regime", "—")
            setup     = t.get("ctx_setup_bars", "—")
            mkt_label = t.get("ctx_market_label", "")
            mkt_name  = t.get("ctx_market_name",  "")
            mkt_roc   = t.get("ctx_market_roc",   float("nan"))
            mkt_str   = f"  │  Context: {regime} | Setup {setup} bar"
            if mkt_label and mkt_name:
                mkt_str += f" | {mkt_name}: {mkt_label} ({_safe(mkt_roc*100 if mkt_roc==mkt_roc else float('nan'), '+.1f')}%)"
            print(mkt_str)

        print(f"  └{'─'*60}")


def cmd_backtest(args, profiles, defaults):
    if args.asset not in profiles:
        print(f"Error: unknown asset class '{args.asset}'.")
        print(f"Available: {', '.join(sorted(profiles.keys()))}")
        sys.exit(1)

    profiles = _apply_direction_override(args, profiles)
    equity = args.equity
    risk_pct = args.risk_pct

    provider, effective_ticker = _resolve_provider_and_ticker(args)

    try:
        signal_df = generate_signals(
            ticker=effective_ticker,
            asset_class=args.asset,
            variant=args.variant,
            provider=provider,
            profiles=profiles,
            defaults=defaults,
            period_override=getattr(args, "period", None),
        )
    except DataUnavailableError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except KeyError as e:
        print(f"Configuration error: {e}")
        sys.exit(1)

    # Apply min-score filter: --min-score flag takes priority, else profile default
    min_score = getattr(args, "min_score", None)
    if min_score is None:
        profile_threshold = profiles[args.asset].get("min_score_threshold", 0)
        if profile_threshold > 0:
            min_score = profile_threshold
    if min_score is not None and min_score > 0 and "signal_score" in signal_df.columns:
        mask = signal_df["signal"] & (signal_df["signal_score"] < min_score)
        signal_df.loc[mask, "signal"] = False
        n_filtered = int(mask.sum())
        if n_filtered:
            print(f"  Score filter (≥{min_score}): removed {n_filtered} low-quality signals")

    n_signals = int(signal_df["signal"].sum())

    if n_signals == 0:
        print(f"No signals found for {args.ticker} | {args.asset} | Variant {args.variant} in period.")
        sys.exit(0)

    trade_log, equity_curve = run_backtest(
        signal_df=signal_df,
        asset_class=args.asset,
        profile=profiles[args.asset],
        defaults=defaults,
        starting_equity=equity,
        risk_pct=risk_pct,
    )

    metrics = calc_metrics(trade_log, equity_curve)

    if args.output == "json":
        out = {
            "ticker": args.ticker,
            "asset_class": args.asset,
            "variant": args.variant,
            "metrics": metrics,
            "trades": trade_log.to_dict(orient="records") if not trade_log.empty else [],
        }
        # Convert Timestamps to strings for JSON serialization
        for t in out["trades"]:
            for k, v in t.items():
                if hasattr(v, "isoformat"):
                    t[k] = v.isoformat()
        print(json.dumps(out, indent=2))

    elif args.output == "csv":
        if not trade_log.empty:
            print(trade_log.to_csv(index=False))

    else:  # table (default)
        period_start = signal_df.index[0].strftime("%Y-%m-%d")
        period_end = signal_df.index[-1].strftime("%Y-%m-%d")
        print(f"\n{'='*60}")
        print(f"  Backtest: {args.ticker} | {args.asset} | Variant {args.variant}")
        print(f"{'='*60}")
        print(f"  Period:           {period_start} → {period_end}")
        print(f"  Starting Equity:  ${equity:,.0f}")
        if min_score is not None:
            print(f"  Score Filter:     ≥ {min_score}/100")
        print(f"\n  PERFORMANCE SUMMARY")
        print(f"  {'Total Trades':<22}: {metrics['total_trades']}")
        print(f"  {'Win Rate':<22}: {metrics['win_rate']}%")
        print(f"  {'Avg R:R':<22}: {metrics['avg_rr']}")
        print(f"  {'Profit Factor':<22}: {metrics['profit_factor']}")
        print(f"  {'Max Drawdown':<22}: {metrics['max_drawdown_pct']}%")
        print(f"  {'Total Return':<22}: {metrics['total_return_pct']:+.2f}%")
        print(f"  {'Sharpe (ann.)':<22}: {metrics['sharpe_ratio']}")

        # Score distribution across executed trades
        if not trade_log.empty and "signal_score" in trade_log.columns:
            sc = trade_log["signal_score"].dropna()
            if len(sc) > 0:
                winners = trade_log[trade_log["pnl_r"] > 0]["signal_score"].dropna()
                losers  = trade_log[trade_log["pnl_r"] <= 0]["signal_score"].dropna()
                print(f"\n  SIGNAL SCORE DISTRIBUTION")
                print(f"  {'All trades':<22}: avg {sc.mean():.1f}  |  p25={sc.quantile(.25):.0f}  p50={sc.median():.0f}  p75={sc.quantile(.75):.0f}")
                if len(winners) and len(losers):
                    print(f"  {'Winners avg':<22}: {winners.mean():.1f}")
                    print(f"  {'Losers avg':<22}: {losers.mean():.1f}")

        if not trade_log.empty:
            print(f"\n  TRADE LOG ({len(trade_log)} trades)")
            _print_trade_log(trade_log)
        print()


def cmd_compare(args, profiles, defaults):
    profiles = _apply_direction_override(args, profiles)
    active_dirs = profiles[args.asset].get("allowed_directions", ["LONG", "SHORT"])
    period_label = getattr(args, "period", None) or "default"
    min_score = getattr(args, "min_score", None)
    score_label = f"  score≥{min_score}" if min_score is not None else ""
    print(f"\nComparing all variants for {args.ticker} | {args.asset} | period: {period_label} | directions: {active_dirs}{score_label}\n")
    print(f"{'Variant':<10} {'Raw':>6} {'Kept':>6} {'Win%':>6} {'AvgRR':>7} {'PF':>6} {'MaxDD%':>8} {'Return%':>9} {'Sharpe':>7} {'AvgScore':>9}")
    print(f"{'-'*80}")

    provider, effective_ticker = _resolve_provider_and_ticker(args)

    for variant in ["A", "B", "C"]:
        try:
            signal_df = generate_signals(
                ticker=effective_ticker,
                asset_class=args.asset,
                variant=variant,
                provider=provider,
                profiles=profiles,
                defaults=defaults,
                period_override=getattr(args, "period", None),
            )

            raw_signals = int(signal_df["signal"].sum())

            # Apply score filter (flag > profile default > none)
            effective_min_score = min_score
            if effective_min_score is None:
                effective_min_score = profiles[args.asset].get("min_score_threshold", 0) or None
            if effective_min_score is not None and effective_min_score > 0 and "signal_score" in signal_df.columns:
                mask = signal_df["signal"] & (signal_df["signal_score"] < effective_min_score)
                signal_df.loc[mask, "signal"] = False

            kept_signals = int(signal_df["signal"].sum())

            if kept_signals == 0:
                print(f"  {'Variant ' + variant:<10} {raw_signals:>6} {'0':>6}  — no signals after score filter")
                continue

            trade_log, equity_curve = run_backtest(
                signal_df=signal_df,
                asset_class=args.asset,
                profile=profiles[args.asset],
                defaults=defaults,
                starting_equity=args.equity,
                risk_pct=args.risk_pct,
            )
            m = calc_metrics(trade_log, equity_curve)

            avg_score = (
                trade_log["signal_score"].mean()
                if not trade_log.empty and "signal_score" in trade_log.columns
                else float("nan")
            )
            score_str = f"{avg_score:>9.1f}" if avg_score == avg_score else f"{'N/A':>9}"

            print(
                f"  {'Variant ' + variant:<10} {raw_signals:>6} {kept_signals:>6} {m['win_rate']:>6.1f} "
                f"{m['avg_rr']:>7.2f} {m['profit_factor']:>6.2f} "
                f"{m['max_drawdown_pct']:>8.2f} {m['total_return_pct']:>+9.2f} {m['sharpe_ratio']:>7.2f} {score_str}"
            )
        except DataUnavailableError as e:
            print(f"  Variant {variant}: Data error — {e}")
    print()


def cmd_chart(args, profiles, defaults):
    from visualizer.charts import plot_backtest, plot_equity_curve

    if args.asset not in profiles:
        print(f"Error: unknown asset class '{args.asset}'. Available: {', '.join(sorted(profiles.keys()))}")
        sys.exit(1)

    provider = get_provider(args.ticker)

    try:
        signal_df = generate_signals(
            ticker=args.ticker,
            asset_class=args.asset,
            variant=args.variant,
            provider=provider,
            profiles=profiles,
            defaults=defaults,
        )
    except DataUnavailableError as e:
        print(f"Error: {e}")
        sys.exit(1)

    trade_log, equity_curve = run_backtest(
        signal_df=signal_df,
        asset_class=args.asset,
        profile=profiles[args.asset],
        defaults=defaults,
    )

    chart_path = plot_backtest(signal_df, trade_log, profiles[args.asset], args.variant, args.ticker)
    equity_path = plot_equity_curve(equity_curve, trade_log, args.ticker, args.variant)
    print(f"Chart saved to: {chart_path}")
    print(f"Equity curve:  {equity_path}")


def cmd_scan(args, profiles, defaults):
    import time as _time
    from datetime import datetime, timezone
    from signals.scanner import build_ticker_list, is_market_open, scan_ticker

    instruments, watchlists = load_catalog()

    # Resolve asset class filter
    asset_filter = None
    if getattr(args, "asset", None):
        asset_filter = [a.strip() for a in args.asset.split(",")]
        for a in asset_filter:
            if a not in profiles:
                print(f"Error: unknown asset class '{a}'. Available: {', '.join(sorted(profiles))}")
                sys.exit(1)

    variant_override = getattr(args, "variant", None)
    min_score        = getattr(args, "min_score", None)
    skip_closed      = not getattr(args, "no_skip_closed", False)
    output_fmt       = getattr(args, "output", "table")
    period_override  = getattr(args, "period", None)

    watchlist_name = getattr(args, "watchlist", None)
    ticker_list = build_ticker_list(
        instruments, watchlists,
        asset_classes=asset_filter,
        watchlist_name=watchlist_name,
    )
    if not ticker_list:
        print("No tickers to scan.")
        sys.exit(0)

    now_utc = datetime.now(tz=timezone.utc)

    # Group by asset class to print section headers
    from itertools import groupby
    ticker_list.sort(key=lambda x: x["asset_class"])

    results      = []
    skipped_cls  = []
    errors       = []
    t0           = _time.time()

    # Pre-compute ecosystem state for US asset classes (cached internally; fast on repeat calls)
    _ecosystem_cache: dict = {}

    def _get_ecosystem(ac: str):
        if ac not in _ecosystem_cache:
            try:
                from signals.ecosystem_monitor import aggregate_ecosystem_state
                _ecosystem_cache[ac] = aggregate_ecosystem_state(ac)
            except Exception:
                _ecosystem_cache[ac] = None
        return _ecosystem_cache[ac]

    scanned_count = 0
    for asset_class, group in groupby(ticker_list, key=lambda x: x["asset_class"]):
        group = list(group)
        profile = profiles[asset_class]

        if skip_closed and not is_market_open(profile, now=now_utc):
            skipped_cls.append(asset_class)
            continue

        exec_interval = profile.get("executor", {}).get("interval", "1h")
        eco_state     = _get_ecosystem(asset_class)

        if output_fmt == "table":
            status = "OPEN"
            eco_suffix = ""
            if eco_state is not None and eco_state.label != "GRAY":
                eco_suffix = f"  🌡 Eco:{eco_state.label} {eco_state.size_multiplier:.1f}×"
            print(f"\n  {asset_class} (market {status} — {exec_interval} bars){eco_suffix}")

        for entry in group:
            canonical   = entry["canonical"]
            ticker      = entry["ticker"]         # yfinance ticker (live data)
            variant     = variant_override or entry["best_variant"]
            description = entry["description"]
            scanned_count += 1

            sig = scan_ticker(
                canonical=canonical,
                asset_class=asset_class,
                variant=variant,
                profiles=profiles,
                defaults=defaults,
                min_score=min_score,
                period_override=period_override,
                yfinance_ticker=ticker,
            )

            if sig is None:
                continue

            sig["description"]  = description
            sig["variant_used"] = variant

            # Attach ecosystem state to signal dict for notifier + history
            if eco_state is not None:
                sig["ecosystem_label"]          = eco_state.label
                sig["ecosystem_multiplier"]     = eco_state.size_multiplier
                sig["ecosystem_vix"]            = eco_state.vix_level
                sig["ecosystem_sector_score"]   = eco_state.sector_score
                sig["ecosystem_nas100_score"]   = eco_state.nas100_score
                sig["ecosystem_nas100_alignment"] = eco_state.nas100_alignment

            results.append(sig)

            if output_fmt == "table":
                _print_scan_row(sig)

    elapsed = _time.time() - t0

    if output_fmt == "json":
        import json as _json
        out = []
        for s in results:
            row = {k: (v.isoformat() if isinstance(v, pd.Timestamp) else v) for k, v in s.items()}
            out.append(row)
        print(_json.dumps(out, indent=2))
        return

    # Table footer
    if output_fmt == "table":
        print(f"\n{'='*60}")
        ts = now_utc.strftime("%Y-%m-%d %H:%M UTC")
        print(f"  Scan completed: {ts}")
        print(f"  Tickers scanned: {scanned_count} | Signals found: {len(results)} | {elapsed:.1f}s")
        if skipped_cls:
            print(f"  Skipped (market closed): {', '.join(skipped_cls)}")
        if not results:
            print("  No active signals.")
        print(f"{'='*60}\n")

    # Telegram notification
    notify    = getattr(args, "notify", False)
    dry_run   = getattr(args, "dry_run", False)
    if (notify or dry_run) and results:
        from notifier import send_signals
        sent = send_signals(results, dry_run=dry_run)
        if output_fmt == "table":
            label = "DRY RUN" if dry_run else "Telegram"
            print(f"  [{label}] {sent}/{len(results)} alerts sent.\n")


def _print_scan_row(sig: dict):
    """Print one scan result as a compact card."""
    def _f(v, fmt=".4f"):
        if v is None or (isinstance(v, float) and v != v):
            return "N/A"
        try:
            return format(v, fmt)
        except Exception:
            return str(v)

    direction = sig.get("direction", "—")
    variant   = sig.get("variant_used", sig.get("variant", "?"))
    score     = sig.get("signal_score")
    score_s   = f"{score:.0f}" if score == score and score is not None else "N/A"
    bars_ago  = sig.get("bars_ago", 0)
    sig_time  = sig.get("signal_time")
    time_s    = sig_time.strftime("%Y-%m-%d %H:%M") if sig_time else "—"
    ctx_label = sig.get("ctx_trend_label") or "—"
    mkt_name  = sig.get("ctx_market_name") or ""
    mkt_lbl   = sig.get("ctx_market_label") or ""

    arrow = "^" if direction == "LONG" else "v" if direction == "SHORT" else "-"

    print(
        f"    {sig['canonical']:<12} {arrow} {direction:<5}  V:{variant}  "
        f"Score:{score_s:>4}  Entry:{_f(sig.get('entry_price')):>12}  "
        f"Stop:{_f(sig.get('stop')):>12}  T:{_f(sig.get('target')):>12}  "
        f"[{bars_ago}bar ago @ {time_s}]"
    )
    ctx_parts = [ctx_label]
    if mkt_name and mkt_lbl:
        ctx_parts.append(f"{mkt_name}: {mkt_lbl}")
    print(f"    {'':12}   {'':5}         {'context: ' + ' | '.join(ctx_parts)}")


def cmd_recap(args):
    from recapper import load_history, format_history_list, format_open_brief, format_close_brief, format_week_brief

    recap_type = getattr(args, "type", None)
    last_str   = getattr(args, "last", None)
    dry_run    = getattr(args, "dry_run", False)

    # Determine which signals to load
    if last_str:
        hours = _parse_hours(last_str)
        signals = load_history(hours=hours)
        message = format_history_list(signals)
    elif recap_type == "open":
        signals = load_history(hours=24)
        # Compute live ecosystem state for the open brief
        eco_state = None
        try:
            from signals.ecosystem_monitor import aggregate_ecosystem_state
            eco_state = aggregate_ecosystem_state("us_stocks")
        except Exception:
            pass
        message = format_open_brief(signals, ecosystem_state=eco_state)
    elif recap_type == "close":
        signals = load_history(hours=24)
        message = format_close_brief(signals)
    elif recap_type == "week":
        from recapper import _WEEK_HOURS
        signals = load_history(hours=_WEEK_HOURS)
        message = format_week_brief(signals)
    else:
        print("Error: specify --type open|close|week or --last <Nh>")
        sys.exit(1)

    if dry_run:
        # Strip HTML tags for terminal readability
        import re
        plain = re.sub(r"<[^>]+>", "", message)
        print("\n── RECAP DRY RUN ────────────────────────────")
        print(plain)
        print("─────────────────────────────────────────────\n")
        return

    # Send via Telegram
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        print("Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set.")
        print("Use --dry-run to preview without sending.")
        sys.exit(1)

    from notifier import _send_telegram
    ok = _send_telegram(token, chat_id, message)
    if ok:
        print("Recap sent successfully.")
    else:
        print("Error: failed to send recap. Check logs.")
        sys.exit(1)


def _parse_hours(last_str: str) -> float:
    """Parse strings like '24h', '2h', '48h' into a float number of hours."""
    last_str = last_str.strip().lower()
    if last_str.endswith("h"):
        try:
            return float(last_str[:-1])
        except ValueError:
            pass
    try:
        return float(last_str)
    except ValueError:
        print(f"Error: cannot parse --last value '{last_str}'. Use format like '24h' or '48h'.")
        sys.exit(1)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="MTF Signal Farm — Multi-Timeframe Backtest System",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── backtest ──
    bt = sub.add_parser("backtest", help="Run backtest for a single ticker/variant")
    bt.add_argument("--asset", required=True, help="Asset class profile (e.g. us_stocks)")
    bt.add_argument("--variant", required=True, choices=["A", "B", "C"], help="Signal variant")
    bt.add_argument("--ticker", required=True, help="Ticker symbol (e.g. AAPL)")
    bt.add_argument("--equity", type=float, default=100_000, help="Starting equity (default: 100000)")
    bt.add_argument("--risk-pct", type=float, default=0.01, dest="risk_pct", help="Risk per trade as fraction (default: 0.01 = 1%%)")
    bt.add_argument("--output", choices=["table", "json", "csv"], default="table", help="Output format")
    bt.add_argument("--direction", default=None,
                    help="Override allowed_directions: LONG, SHORT, or LONG,SHORT")
    bt.add_argument("--period", default=None,
                    help="Override backtest period for all timeframes (e.g. 60d, 1y, 2y)")
    bt.add_argument("--min-score", type=float, default=None, dest="min_score",
                    help="Minimum signal quality score (0-100) to trade. Default: no filter")
    bt.add_argument("--provider", choices=["auto", "yfinance", "dukascopy"], default="auto",
                    help="Force data provider. 'dukascopy' for full historical data (backtest), 'yfinance' for live")
    bt.add_argument("--verbose", action="store_true", help="Enable debug logging")

    # ── compare ──
    cmp = sub.add_parser("compare", help="Compare all variants side by side")
    cmp.add_argument("--asset", required=True)
    cmp.add_argument("--ticker", required=True)
    cmp.add_argument("--equity", type=float, default=100_000)
    cmp.add_argument("--risk-pct", type=float, default=0.01, dest="risk_pct")
    cmp.add_argument("--direction", default=None,
                    help="Override allowed_directions: LONG, SHORT, or LONG,SHORT")
    cmp.add_argument("--period", default=None,
                    help="Override backtest period for all timeframes (e.g. 60d, 1y, 2y)")
    cmp.add_argument("--min-score", type=float, default=None, dest="min_score",
                    help="Minimum signal quality score (0-100) to trade. Default: no filter")
    cmp.add_argument("--provider", choices=["auto", "yfinance", "dukascopy"], default="auto",
                    help="Force data provider. 'dukascopy' for full historical data (backtest), 'yfinance' for live")

    # ── chart ──
    ch = sub.add_parser("chart", help="Generate interactive HTML chart")
    ch.add_argument("--asset", required=True)
    ch.add_argument("--variant", required=True, choices=["A", "B", "C"])
    ch.add_argument("--ticker", required=True)

    # ── scan ──
    sc = sub.add_parser("scan", help="Scan active watchlist for current signals")
    sc.add_argument("--asset", default=None,
                    help="Comma-separated asset class(es) to scan (default: all open markets)")
    sc.add_argument("--variant", default=None, choices=["A", "B", "C"],
                    help="Override best_variant for all tickers (default: per-instrument best_variant)")
    sc.add_argument("--min-score", type=float, default=None, dest="min_score",
                    help="Minimum signal score (overrides profile default)")
    sc.add_argument("--watchlist", default=None, metavar="NAME",
                    help="Use a named watchlist subset (e.g. 'beta'). Defined in watchlists.yaml under named_watchlists.")
    sc.add_argument("--no-skip-closed", action="store_true", dest="no_skip_closed",
                    help="Scan all asset classes even if market is closed")
    sc.add_argument("--period", default=None,
                    help="Data period override for all fetches (e.g. 60d)")
    sc.add_argument("--output", choices=["table", "json"], default="table")
    sc.add_argument("--notify", action="store_true",
                    help="Send Telegram alerts for found signals (requires TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env)")
    sc.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="Print formatted Telegram messages without sending (for testing)")
    sc.add_argument("--verbose", action="store_true", help="Enable debug logging")

    # ── recap ──
    rc = sub.add_parser("recap", help="Send or preview a session recap from signal history")
    rc.add_argument("--type", choices=["open", "close", "week"], default=None,
                    help="open = pre-session brief, close = post-session brief, week = weekly recap (last 14 days)")
    rc.add_argument("--last", default=None, metavar="Nh",
                    help="List all signals from the last N hours (e.g. --last 24h)")
    rc.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="Preview the recap message without sending it to Telegram")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if hasattr(args, "verbose") and args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    profiles, defaults = load_configs()

    if args.command == "backtest":
        cmd_backtest(args, profiles, defaults)
    elif args.command == "compare":
        cmd_compare(args, profiles, defaults)
    elif args.command == "chart":
        cmd_chart(args, profiles, defaults)
    elif args.command == "scan":
        cmd_scan(args, profiles, defaults)
    elif args.command == "recap":
        cmd_recap(args)


if __name__ == "__main__":
    main()
