"""
Scanner: scans the active watchlist and returns the most recent signal per ticker.

Public API
----------
build_ticker_list(instruments, watchlists, asset_classes=None) -> list[dict]
is_market_open(profile, now=None) -> bool
get_last_signal(signal_df, lookback_bars=1) -> pd.Series | None
scan_ticker(canonical, asset_class, variant, provider, profiles, defaults,
            min_score=None) -> dict | None
"""
from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any

import pandas as pd
import pytz

from data_feed.provider import DataUnavailableError
from data_feed.provider_factory import get_provider
from signals.engine import generate_signals

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ticker list builder
# ---------------------------------------------------------------------------

def build_ticker_list(
    instruments: dict,
    watchlists: dict,
    asset_classes: list[str] | None = None,
    watchlist_name: str | None = None,
) -> list[dict]:
    """
    Return a flat list of dicts, one per ticker in the active watchlists.

    Each dict has:
        canonical   : symbol key in instruments.yaml  (e.g. "MSFT", "XAUUSD")
        ticker      : yfinance ticker for live data    (e.g. "MSFT", "GC=F")
        asset_class : profile key                      (e.g. "us_stocks")
        description : human label
        best_variant: preferred variant ("A"/"B"/"C"), default "A"
        edge        : "strong"/"good"/"marginal"/"none"/None

    When `watchlist_name` is given (e.g. "beta"), the named list from
    watchlists["named_watchlists"][name] is used instead of the per-asset-class
    lists.  `asset_classes` filter still applies on top of that.
    """
    # --- Named watchlist path ---
    if watchlist_name:
        named = (watchlists.get("named_watchlists") or {}).get(watchlist_name)
        if named is None:
            available = list((watchlists.get("named_watchlists") or {}).keys())
            raise ValueError(
                f"Named watchlist '{watchlist_name}' not found. "
                f"Available: {available or '(none defined)'}"
            )
        return _build_from_named_list(named, instruments, asset_classes)

    # --- Default path: iterate per asset class ---
    result = []
    for asset_class, symbols in watchlists.items():
        if asset_class == "named_watchlists":
            continue
        if asset_classes and asset_class not in asset_classes:
            continue

        # Watchlist entries can be a flat list or a nested dict (forex majors/crosses)
        if isinstance(symbols, dict):
            flat_symbols = []
            for v in symbols.values():
                flat_symbols.extend(v if isinstance(v, list) else [v])
        else:
            flat_symbols = symbols

        instr_section = instruments.get(asset_class, {})

        for canonical in flat_symbols:
            entry = instr_section.get(canonical)
            if entry is None:
                logger.warning("scanner: %s not found in instruments.yaml — skipping", canonical)
                continue

            yf_ticker = entry.get("yfinance") or canonical
            result.append({
                "canonical":    canonical,
                "ticker":       yf_ticker,
                "asset_class":  asset_class,
                "description":  entry.get("description", canonical),
                "best_variant": entry.get("best_variant", "A"),
                "edge":         entry.get("edge"),
            })

    return result


def _build_from_named_list(
    canonicals: list,
    instruments: dict,
    asset_classes: list[str] | None,
) -> list[dict]:
    """Resolve a flat list of canonical symbols against instruments.yaml.

    Each entry can be either:
    - a plain string ``"MSFT"``  → uses best_variant from instruments.yaml
    - a dict ``{"canonical": "MSFT", "variant": "B"}``  → explicit variant override

    This allows a single ticker to appear multiple times with different variants
    (e.g. MSFT scanned with both Pullback/A and Breakout/B).
    """
    # Build a reverse map: canonical → (asset_class, entry)
    reverse: dict[str, tuple[str, dict]] = {}
    for section_key, section in instruments.items():
        if not isinstance(section, dict):
            continue
        for symbol, entry in section.items():
            if isinstance(entry, dict):
                reverse[symbol] = (entry.get("asset_class", section_key), entry)

    result = []
    for item in canonicals:
        # Resolve canonical symbol and optional variant override
        if isinstance(item, dict):
            canonical = item.get("canonical", "")
            variant_override = item.get("variant")
        else:
            canonical = str(item)
            variant_override = None

        if canonical not in reverse:
            logger.warning("scanner: %s not found in instruments.yaml — skipping", canonical)
            continue
        asset_class, entry = reverse[canonical]
        if asset_classes and asset_class not in asset_classes:
            continue
        yf_ticker = entry.get("yfinance") or canonical
        result.append({
            "canonical":    canonical,
            "ticker":       yf_ticker,
            "asset_class":  asset_class,
            "description":  entry.get("description", canonical),
            "best_variant": variant_override or entry.get("best_variant", "A"),
            "edge":         entry.get("edge"),
        })
    return result


# ---------------------------------------------------------------------------
# Market open check
# ---------------------------------------------------------------------------

def is_market_open(profile: dict, now: datetime | None = None) -> bool:
    """
    Return True if the current time falls within scan_hours for the profile.

    scan_hours config options:
        always_open: true          → always returns True
        timezone / weekdays / start / end  → time-window check
    """
    scan_hours = profile.get("scan_hours")
    if not scan_hours:
        return True  # no config → always scan

    if scan_hours.get("always_open"):
        return True

    tz_name  = scan_hours.get("timezone", "UTC")
    weekdays = scan_hours.get("weekdays", list(range(5)))  # Mon-Fri default
    start_s  = scan_hours.get("start", "00:00")
    end_s    = scan_hours.get("end",   "23:59")

    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        logger.warning("scanner: unknown timezone %r — treating as UTC", tz_name)
        tz = pytz.UTC

    if now is None:
        now = datetime.now(tz=pytz.UTC)
    local_now = now.astimezone(tz)

    if local_now.weekday() not in weekdays:
        return False

    t_start = _parse_time(start_s)
    t_end   = _parse_time(end_s)
    t_now   = local_now.time().replace(second=0, microsecond=0)

    return t_start <= t_now <= t_end


def _parse_time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


# ---------------------------------------------------------------------------
# Last-signal extractor
# ---------------------------------------------------------------------------

def get_last_signal(signal_df: pd.DataFrame, lookback_bars: int = 1) -> pd.Series | None:
    """
    Return the most recent row where signal == True within the last `lookback_bars`
    rows of signal_df, or None if no signal found.

    lookback_bars=1 → only the last completed bar.
    lookback_bars=3 → the last 3 bars (catches a signal that fired 1-2 bars ago).
    """
    if signal_df.empty or "signal" not in signal_df.columns:
        return None

    tail = signal_df.iloc[-lookback_bars:]
    hits = tail[tail["signal"] == True]  # noqa: E712
    if hits.empty:
        return None

    row = hits.iloc[-1].copy()
    row["bars_ago"] = len(signal_df) - 1 - signal_df.index.get_loc(hits.index[-1])
    return row


# ---------------------------------------------------------------------------
# Single-ticker scan
# ---------------------------------------------------------------------------

def scan_ticker(
    canonical: str,
    asset_class: str,
    variant: str,
    profiles: dict,
    defaults: dict,
    min_score: float | None = None,
    period_override: str | None = None,
    yfinance_ticker: str | None = None,
) -> dict | None:
    """
    Fetch + generate signals for one ticker and return the most recent signal
    as a dict, or None if no signal was found.

    Parameters
    ----------
    canonical       : canonical symbol key in instruments.yaml (e.g. "BTCUSD", "XAUUSD")
    yfinance_ticker : yfinance-format ticker (e.g. "BTC-USD", "GC=F").
                      Used only when the chosen provider is YFinanceProvider.
                      If None, canonical is used for all providers.
    """
    from data_feed.yfinance_provider import YFinanceProvider

    try:
        provider = get_provider(canonical)

        # Non-yfinance providers (Alpaca, ccxt, Dukascopy) expect canonical symbols.
        # YFinanceProvider expects the yfinance-format ticker (e.g. "GC=F", "BTC-USD").
        if isinstance(provider, YFinanceProvider) and yfinance_ticker:
            ticker_for_engine = yfinance_ticker
        else:
            ticker_for_engine = canonical

        signal_df = generate_signals(
            ticker=ticker_for_engine,
            asset_class=asset_class,
            variant=variant,
            provider=provider,
            profiles=profiles,
            defaults=defaults,
            period_override=period_override,
        )
    except DataUnavailableError as exc:
        logger.warning("scanner: %s — data unavailable: %s", canonical, exc)
        return None
    except Exception as exc:
        logger.warning("scanner: %s — unexpected error: %s", canonical, exc, exc_info=True)
        return None

    # Apply min_score filter (same logic as cmd_backtest)
    effective_min_score = min_score
    if effective_min_score is None:
        effective_min_score = profiles[asset_class].get("min_score_threshold") or None
    if effective_min_score and "signal_score" in signal_df.columns:
        mask = signal_df["signal"] & (signal_df["signal_score"] < effective_min_score)
        signal_df.loc[mask, "signal"] = False

    row = get_last_signal(signal_df, lookback_bars=3)
    if row is None:
        return None

    result: dict[str, Any] = {
        "canonical":   canonical,
        "ticker_used": ticker_for_engine,
        "asset_class": asset_class,
        "variant":     variant,
        "direction":   row.get("direction", "—"),
        "entry_price": row.get("entry_price"),
        "stop":        row.get("stop"),
        "target":      row.get("target"),
        "rr":          row.get("rr"),
        "signal_score":row.get("signal_score"),
        "score_trend": row.get("score_trend"),
        "score_momentum": row.get("score_momentum"),
        "score_entry": row.get("score_entry"),
        "bars_ago":    int(row.get("bars_ago", 0)),
        "signal_time": row.name if isinstance(row.name, pd.Timestamp) else None,
        "ctx_trend_label":  row.get("ctx_trend_label"),
        "ctx_regime":       row.get("ctx_regime"),
        "ctx_rsi":          row.get("ctx_rsi"),
        "ctx_roc_pct":      row.get("ctx_roc_pct"),
        "ctx_atr_pct":      row.get("ctx_atr_pct"),
        "ctx_rel_vol":      row.get("ctx_rel_vol"),
        "ctx_market_label": row.get("ctx_market_label"),
        "ctx_market_name":  row.get("ctx_market_name"),
        "ctx_market_roc":   row.get("ctx_market_roc"),
    }
    return result
