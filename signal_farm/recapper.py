"""
Signal Farm — Recap module.

Responsibilities:
  - append_to_history(sig)          : write a signal dict to the JSONL history file
  - load_history(hours=24)          : read signals sent in the last N hours
  - format_open_brief(signals)      : pre-session Telegram message ("cosa ho sul tavolo?")
  - format_close_brief(signals)     : post-session Telegram message ("cosa è successo oggi?")
  - format_week_brief(signals)      : weekly recap Telegram message (run on Mondays)
  - generate_reading(signals)       : deterministic interpretive text for daily briefs
  - generate_week_reading(signals)  : deterministic interpretive text for weekly recap
  - format_history_list(signals)    : compact list of recent signals (for --last Nh)

History file: .signal_farm_history.jsonl  (append-only, one JSON object per line)
Each line is the full signal dict enriched with `sent_at` (ISO-8601 UTC).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)

_HISTORY_FILE_ENV = "SIGNAL_FARM_HISTORY_FILE"
_DEFAULT_HISTORY_FILENAME = ".signal_farm_history.jsonl"

_DIRECTION_ARROW = {"LONG": "\u2197", "SHORT": "\u2198"}   # ↗ ↘
_DIRECTION_ICON  = {"LONG": "\U0001f4c8", "SHORT": "\U0001f4c9"}   # 📈 📉


# ---------------------------------------------------------------------------
# History path
# ---------------------------------------------------------------------------

def _history_file_path() -> str:
    return os.environ.get(
        _HISTORY_FILE_ENV,
        os.path.join(os.path.dirname(__file__), "..", _DEFAULT_HISTORY_FILENAME),
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def append_to_history(sig: dict[str, Any]) -> None:
    """Append a signal dict (enriched with sent_at) to the JSONL history file."""
    record = _serialize_sig(sig)
    record["sent_at"] = datetime.now(tz=timezone.utc).isoformat()

    path = _history_file_path()
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("recapper: could not write history: %s", exc)


def load_history(hours: float = 24) -> list[dict[str, Any]]:
    """Return signals sent within the last `hours` hours, newest-first."""
    path = _history_file_path()
    if not os.path.exists(path):
        return []

    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    results = []

    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    sent_at_s = record.get("sent_at")
                    if not sent_at_s:
                        continue
                    sent_at = datetime.fromisoformat(sent_at_s)
                    if sent_at.tzinfo is None:
                        sent_at = sent_at.replace(tzinfo=timezone.utc)
                    if sent_at >= cutoff:
                        record["_sent_at_dt"] = sent_at   # parsed, for sorting
                        results.append(record)
                except Exception:
                    continue
    except Exception as exc:
        logger.warning("recapper: could not read history: %s", exc)
        return []

    results.sort(key=lambda r: r["_sent_at_dt"], reverse=True)
    # Remove the temporary parsed key before returning
    for r in results:
        r.pop("_sent_at_dt", None)
    return results


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _p(v, fmt=".4f", fallback="N/A") -> str:
    if v is None or (isinstance(v, float) and v != v):
        return fallback
    try:
        return format(float(v), fmt)
    except Exception:
        return str(v)


def _hours_ago_label(sent_at_s: str) -> str:
    try:
        sent_at = datetime.fromisoformat(sent_at_s)
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        delta = datetime.now(tz=timezone.utc) - sent_at
        h = int(delta.total_seconds() // 3600)
        m = int((delta.total_seconds() % 3600) // 60)
        if h == 0:
            return f"{m}m fa"
        return f"{h}h fa"
    except Exception:
        return "?"


def _score_bar(score, width=8) -> str:
    if score is None or (isinstance(score, float) and score != score):
        return "░" * width
    filled = round((float(score) / 100) * width)
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


# ---------------------------------------------------------------------------
# Public formatters
# ---------------------------------------------------------------------------

def format_history_list(signals: list[dict[str, Any]]) -> str:
    """Compact list of signals for --last Nh output."""
    if not signals:
        return "Nessun segnale trovato nel periodo richiesto."

    lines = [
        f"\U0001f4cb <b>SIGNAL HISTORY — ultimi {len(signals)} segnali</b>",
        "\u2501" * 24,
    ]

    for sig in signals:
        direction = sig.get("direction", "?")
        icon      = _DIRECTION_ICON.get(direction, "\U0001f7e1")
        arrow     = _DIRECTION_ARROW.get(direction, "\u2192")
        canonical = sig.get("canonical", "?")
        variant   = sig.get("variant_used") or sig.get("variant", "?")
        score     = sig.get("signal_score")
        score_s   = f"{score:.0f}" if score is not None and score == score else "N/A"
        entry     = sig.get("entry_price")
        sent_at   = sig.get("sent_at", "")
        ago       = _hours_ago_label(sent_at)
        asset_cls = sig.get("asset_class", "")

        lines.append(
            f"{icon} <b>{canonical}</b>  {direction} {arrow}  "
            f"V:{variant}  Score:{score_s}  Entry:<code>{_p(entry)}</code>  "
            f"[{ago}]"
        )
        if asset_cls:
            lines.append(f"   <i>{asset_cls}</i>")

    return "\n".join(lines)


def format_open_brief(signals: list[dict[str, Any]], ecosystem_state=None) -> str:
    """
    Pre-session brief: 'cosa ho sul tavolo oggi?'

    Parameters
    ----------
    signals         : list of signal dicts from load_history()
    ecosystem_state : optional EcosystemState from ecosystem_monitor.aggregate_ecosystem_state()
                      — shown at top of brief when non-neutral
    """
    now = datetime.now(tz=timezone.utc)
    date_s = now.strftime("%a %d %b, %H:%M UTC")

    if not signals:
        no_sig_lines = [
            f"\U0001f4cb <b>SESSION OPEN BRIEF — {date_s}</b>",
            "\u2501" * 24,
        ]
        if ecosystem_state is not None and ecosystem_state.label != "GRAY":
            no_sig_lines += ["", _format_ecosystem_line(ecosystem_state)]
        no_sig_lines += ["", "Nessun segnale attivo nelle ultime 24h.", "\u2501" * 24]
        return "\n".join(no_sig_lines)

    long_sigs  = [s for s in signals if s.get("direction") == "LONG"]
    short_sigs = [s for s in signals if s.get("direction") == "SHORT"]

    # Macro context from most recent signal
    macro_lines = _macro_summary(signals)
    reading     = generate_reading(signals)

    lines = [
        f"\U0001f4cb <b>SESSION OPEN BRIEF — {date_s}</b>",
        "\u2501" * 24,
    ]

    # Ecosystem state — shown before signals when non-neutral (actionable info first)
    if ecosystem_state is not None and ecosystem_state.label != "GRAY":
        lines += ["", _format_ecosystem_line(ecosystem_state)]

    lines += [
        "",
        f"\U0001f514 <b>SEGNALI ATTIVI</b> (ultime 24h) — {len(signals)} totali",
    ]

    for sig in signals:
        direction = sig.get("direction", "?")
        icon      = _DIRECTION_ICON.get(direction, "\U0001f7e1")
        arrow     = _DIRECTION_ARROW.get(direction, "\u2192")
        canonical = sig.get("canonical", "?")
        variant   = sig.get("variant_used") or sig.get("variant", "?")
        score     = sig.get("signal_score")
        score_s   = f"{score:.0f}" if score is not None and score == score else "N/A"
        entry     = sig.get("entry_price")
        sent_at   = sig.get("sent_at", "")
        ago       = _hours_ago_label(sent_at)

        lines.append(
            f"{icon} <b>{canonical}</b>  {direction} {arrow}  "
            f"V:{variant}  Score:{score_s}  "
            f"Entry:<code>{_p(entry)}</code>  [{ago}]"
        )

    if macro_lines:
        lines += ["", "\U0001f4ca <b>MACRO DI SESSIONE</b>"] + macro_lines

    if reading:
        lines += ["", f"\U0001f4a1 <b>LETTURA</b>: {reading}"]

    lines.append("\u2501" * 24)
    return "\n".join(lines)


def format_close_brief(signals: list[dict[str, Any]]) -> str:
    """Post-session brief: 'cosa è successo oggi?'"""
    now = datetime.now(tz=timezone.utc)
    date_s = now.strftime("%a %d %b, %H:%M UTC")

    lines = [
        f"\U0001f4ca <b>SESSION CLOSE — {date_s}</b>",
        "\u2501" * 24,
        "",
        "<b>OGGI</b>",
    ]

    if not signals:
        lines.append("  Nessun segnale inviato oggi.")
        lines.append("\u2501" * 24)
        return "\n".join(lines)

    long_sigs  = [s for s in signals if s.get("direction") == "LONG"]
    short_sigs = [s for s in signals if s.get("direction") == "SHORT"]

    asset_counts: dict[str, int] = {}
    for sig in signals:
        ac = sig.get("asset_class", "unknown")
        asset_counts[ac] = asset_counts.get(ac, 0) + 1

    asset_str = "  ".join(f"{ac} ×{n}" for ac, n in sorted(asset_counts.items()))

    scores = [
        s["signal_score"] for s in signals
        if s.get("signal_score") is not None and s["signal_score"] == s["signal_score"]
    ]
    avg_score = sum(scores) / len(scores) if scores else None

    lines += [
        f"  Segnali inviati: {len(signals)}",
        f"  LONG: {len(long_sigs)}  |  SHORT: {len(short_sigs)}",
        f"  Asset class: {asset_str}",
    ]
    if avg_score is not None:
        lines.append(f"  Score medio: {avg_score:.1f}/100")

    # Sub-score averages
    trend_scores    = [s.get("score_trend")    for s in signals if s.get("score_trend")    is not None]
    entry_scores    = [s.get("score_entry")    for s in signals if s.get("score_entry")    is not None]
    strong_ctx      = sum(1 for s in signals if (s.get("signal_score") or 0) >= 70)

    if trend_scores or entry_scores:
        lines += ["", "<b>QUALITÀ SESSIONE</b>"]
        if trend_scores:
            lines.append(f"  Trend score medio: {sum(trend_scores)/len(trend_scores):.0f}/45")
        if entry_scores:
            lines.append(f"  Entry score medio: {sum(entry_scores)/len(entry_scores):.0f}/25")
        lines.append(f"  Contesti forti (≥70): {strong_ctx}/{len(signals)}")

    # Still-open signals (rough check: no outcome tracked, just report as "still active")
    lines += [
        "",
        "<b>DOMANI</b>",
        f"  \u2192 {len(signals)} segnale/i registrato/i questa sessione.",
        "  Verifica entry/stop sul tuo broker prima di aprire nuove posizioni.",
    ]

    lines.append("\u2501" * 24)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Interpretive text (deterministic rule-based)
# ---------------------------------------------------------------------------

def generate_reading(signals: list[dict[str, Any]]) -> str:
    """Generate a short interpretive sentence based on signals and macro context."""
    if not signals:
        return ""

    long_sigs  = [s for s in signals if s.get("direction") == "LONG"]
    short_sigs = [s for s in signals if s.get("direction") == "SHORT"]

    scores = [
        s["signal_score"] for s in signals
        if s.get("signal_score") is not None and s["signal_score"] == s["signal_score"]
    ]
    avg_score = sum(scores) / len(scores) if scores else None

    asset_classes = {s.get("asset_class", "") for s in signals}

    # Determine dominant macro label (most common mkt_label)
    mkt_labels = [s.get("ctx_market_label", "") for s in signals if s.get("ctx_market_label")]
    dominant_mkt = max(set(mkt_labels), key=mkt_labels.count) if mkt_labels else ""

    regimes = [s.get("ctx_regime", "") for s in signals if s.get("ctx_regime")]
    dominant_regime = max(set(regimes), key=regimes.count) if regimes else ""

    parts = []

    # Direction alignment
    if len(long_sigs) >= 2 and not short_sigs and "BULL" in dominant_mkt.upper():
        parts.append("Setup allineati con trend macro. Alta probabilità di follow-through.")
    elif len(short_sigs) >= 2 and not long_sigs and "BEAR" in dominant_mkt.upper():
        parts.append("Setup allineati con trend macro ribassista. Alta probabilità di follow-through.")
    elif long_sigs and short_sigs:
        parts.append("Segnali misti — mercato in transizione. Preferire variante C (più selettiva).")

    # Score quality
    if avg_score is not None:
        if avg_score < 65:
            parts.append("Qualità bassa. Aspettare conferma prima di entrare.")
        elif avg_score >= 78:
            parts.append("Setup di alta qualità. Risk management standard applicabile.")

    # Asset concentration
    if len(asset_classes) == 1 and asset_classes != {""}:
        cls = next(iter(asset_classes))
        parts.append(f"Concentrazione su {cls}. Attenzione alla correlazione.")

    # Regime
    if "VOLATILE" in dominant_regime.upper() or "CHOP" in dominant_regime.upper():
        parts.append("Alta volatilità. Considerare stop più ampi o size ridotta.")

    return " ".join(parts) if parts else "Analizza i livelli di entry/stop prima di agire."


# ---------------------------------------------------------------------------
# Ecosystem summary helper
# ---------------------------------------------------------------------------

def _format_ecosystem_line(ecosystem_state) -> str:
    """Format one-line ecosystem status for session briefs."""
    _ECO_ICONS = {
        "BRIGHT_GREEN": "\U0001f7e2\U0001f7e2",
        "GREEN":        "\U0001f7e2",
        "RED":          "\U0001f7e1",
        "DARK_RED":     "\U0001f534",
    }
    icon  = _ECO_ICONS.get(ecosystem_state.label, "\u26aa")
    label = ecosystem_state.label

    parts = []
    if ecosystem_state.vix_level is not None:
        parts.append(f"VIX {ecosystem_state.vix_level:.1f}")
    if ecosystem_state.sector_score is not None:
        parts.append(f"Settori {ecosystem_state.sector_score:+.0f}/7")

    mult_s = f"{ecosystem_state.size_multiplier:.1f}\u00d7"   # e.g. "1.5×"
    info   = f"({' | '.join(parts)})" if parts else ""

    line = f"\U0001f321\ufe0f <b>Ecosistema NAS100</b>: {icon} {label}  {info}"
    if ecosystem_state.size_multiplier != 1.0:
        line += f"\n   \u2192 Moltiplicatore attivo: <b>{mult_s}</b>"
    return line


# ---------------------------------------------------------------------------
# Macro summary helper
# ---------------------------------------------------------------------------

def _macro_summary(signals: list[dict[str, Any]]) -> list[str]:
    """Return 1-2 lines of macro context from the most recent signals."""
    lines = []
    seen_markets: set[str] = set()
    for sig in signals:
        mkt_name  = sig.get("ctx_market_name") or ""
        mkt_label = sig.get("ctx_market_label") or ""
        mkt_roc   = sig.get("ctx_market_roc")
        regime    = sig.get("ctx_regime") or ""

        if mkt_name and mkt_label and mkt_name not in seen_markets:
            roc_s = ""
            if mkt_roc is not None and mkt_roc == mkt_roc:
                roc_s = f" ({mkt_roc * 100:+.1f}%)"
            lines.append(f"  {mkt_name}: {mkt_label}{roc_s}")
            seen_markets.add(mkt_name)

        if regime and len(lines) < 4:
            lines.append(f"  Regime: {regime}")
            regime = ""  # only add once per signal

    return lines[:4]


# ---------------------------------------------------------------------------
# Weekly recap
# ---------------------------------------------------------------------------

_WEEK_HOURS = 14 * 24   # 2 weeks


def format_week_brief(signals: list[dict[str, Any]]) -> str:
    """
    Weekly recap — designed to be sent on Mondays.
    Covers the last 2 weeks of signals (descriptive stats only,
    no outcome tracking — outcomes are a future feature).
    """
    now    = datetime.now(tz=timezone.utc)
    date_s = now.strftime("%a %d %b %Y")

    header = [
        f"\U0001f4c6 <b>WEEK RECAP — {date_s}</b>",
        "\u2501" * 24,
        "<i>Analisi ultimi 14 giorni · outcome non tracciato</i>",
    ]

    if not signals:
        return "\n".join(header + ["", "Nessun segnale nelle ultime 2 settimane.", "\u2501" * 24])

    long_sigs  = [s for s in signals if s.get("direction") == "LONG"]
    short_sigs = [s for s in signals if s.get("direction") == "SHORT"]
    long_pct   = len(long_sigs) / len(signals) * 100

    # --- Asset class breakdown ---
    asset_counts: dict[str, int] = {}
    for sig in signals:
        ac = sig.get("asset_class", "unknown")
        asset_counts[ac] = asset_counts.get(ac, 0) + 1

    # --- Scores ---
    scores = [
        s["signal_score"] for s in signals
        if s.get("signal_score") is not None and s["signal_score"] == s["signal_score"]
    ]
    avg_score = sum(scores) / len(scores) if scores else None
    min_score = min(scores) if scores else None
    max_score = max(scores) if scores else None
    strong_count = sum(1 for sc in scores if sc >= 70)

    # --- Week-over-week split ---
    week1_cutoff = now - timedelta(hours=_WEEK_HOURS)
    week2_cutoff = now - timedelta(hours=_WEEK_HOURS // 2)   # 7 days ago

    week1 = [s for s in signals if _sent_at_dt(s) < week2_cutoff]
    week2 = [s for s in signals if _sent_at_dt(s) >= week2_cutoff]

    week1_scores = [s["signal_score"] for s in week1 if s.get("signal_score") is not None and s["signal_score"] == s["signal_score"]]
    week2_scores = [s["signal_score"] for s in week2 if s.get("signal_score") is not None and s["signal_score"] == s["signal_score"]]
    week1_avg = sum(week1_scores) / len(week1_scores) if week1_scores else None
    week2_avg = sum(week2_scores) / len(week2_scores) if week2_scores else None

    # --- Daily distribution ---
    day_counts: dict[str, int] = {}
    for sig in signals:
        dt = _sent_at_dt(sig)
        day_key = dt.strftime("%a %d/%m")
        day_counts[day_key] = day_counts.get(day_key, 0) + 1
    busiest_day, busiest_n = max(day_counts.items(), key=lambda x: x[1])

    # --- Dominant macro ---
    mkt_labels = [s.get("ctx_market_label", "") for s in signals if s.get("ctx_market_label")]
    dominant_mkt = max(set(mkt_labels), key=mkt_labels.count) if mkt_labels else "—"
    regimes = [s.get("ctx_regime", "") for s in signals if s.get("ctx_regime")]
    dominant_regime = max(set(regimes), key=regimes.count) if regimes else "—"

    # --- Build message ---
    lines = header + [""]

    # Volume
    lines += [
        "<b>VOLUME</b>",
        f"  Segnali totali:  {len(signals)}",
        f"  LONG: {len(long_sigs)} ({long_pct:.0f}%)  |  SHORT: {len(short_sigs)} ({100-long_pct:.0f}%)",
        f"  Giorno più attivo: {busiest_day} ({busiest_n} segnali)",
    ]

    # Asset breakdown
    asset_parts = "  ".join(f"{ac} ×{n}" for ac, n in sorted(asset_counts.items()))
    lines += ["", "<b>ASSET CLASS</b>", f"  {asset_parts}"]

    # Score quality
    if avg_score is not None:
        lines += ["", "<b>QUALITÀ SEGNALI</b>"]
        lines.append(f"  Score medio:  {avg_score:.1f}/100  (min {min_score:.0f} · max {max_score:.0f})")
        lines.append(f"  Score ≥70:    {strong_count}/{len(scores)}")

        if week1_avg is not None and week2_avg is not None:
            trend_arrow = "\u2191" if week2_avg > week1_avg else "\u2193" if week2_avg < week1_avg else "\u2192"
            lines.append(
                f"  Trend score:  sett.1 {week1_avg:.1f} → sett.2 {week2_avg:.1f}  {trend_arrow}"
            )

    # Macro
    lines += [
        "",
        "<b>MACRO DOMINANTE</b>",
        f"  Mercato: {dominant_mkt}",
        f"  Regime:  {dominant_regime}",
    ]

    # Interpretive reading
    reading = generate_week_reading(signals, week1, week2, dominant_mkt, dominant_regime)
    if reading:
        lines += ["", "\U0001f4a1 <b>LETTURA</b>", f"  {reading}"]

    # Disclaimer
    lines += [
        "",
        "<i>\u26a0\ufe0f Outcome non tracciato. Statistiche basate sui segnali</i>",
        "<i>   generati, non sui risultati reali delle trade.</i>",
        "\u2501" * 24,
    ]

    return "\n".join(lines)


def generate_week_reading(
    signals: list[dict[str, Any]],
    week1: list[dict[str, Any]],
    week2: list[dict[str, Any]],
    dominant_mkt: str,
    dominant_regime: str,
) -> str:
    """Deterministic interpretive text for the weekly recap."""
    if not signals:
        return ""

    parts = []

    long_sigs  = [s for s in signals if s.get("direction") == "LONG"]
    short_sigs = [s for s in signals if s.get("direction") == "SHORT"]
    long_pct   = len(long_sigs) / len(signals)

    scores = [
        s["signal_score"] for s in signals
        if s.get("signal_score") is not None and s["signal_score"] == s["signal_score"]
    ]
    avg_score = sum(scores) / len(scores) if scores else None

    asset_classes = {s.get("asset_class", "") for s in signals if s.get("asset_class")}

    # Week-over-week momentum
    w1_scores = [s["signal_score"] for s in week1 if s.get("signal_score") is not None and s["signal_score"] == s["signal_score"]]
    w2_scores = [s["signal_score"] for s in week2 if s.get("signal_score") is not None and s["signal_score"] == s["signal_score"]]
    w1_avg = sum(w1_scores) / len(w1_scores) if w1_scores else None
    w2_avg = sum(w2_scores) / len(w2_scores) if w2_scores else None

    # Directional bias
    if long_pct >= 0.80 and "BULL" in dominant_mkt.upper():
        parts.append("Forte bias LONG, allineato con macro bullish. Il sistema è in fase di momentum.")
    elif long_pct <= 0.20 and "BEAR" in dominant_mkt.upper():
        parts.append("Forte bias SHORT, allineato con macro bearish. Il sistema è in fase difensiva.")
    elif 0.35 <= long_pct <= 0.65:
        parts.append("Bias direzionale equilibrato. Il mercato non mostra una tendenza dominante — selettività alta consigliata.")
    elif long_pct > 0.65 and "BEAR" in dominant_mkt.upper():
        parts.append("Bias LONG ma macro bearish: i segnali contrastano il trend macro. Attenzione al rischio di false breakout.")
    elif long_pct < 0.35 and "BULL" in dominant_mkt.upper():
        parts.append("Bias SHORT in macro bullish: rotazione o correzione in corso. Verifica i timeframe superiori.")

    # Score trend week-over-week
    if w1_avg is not None and w2_avg is not None:
        diff = w2_avg - w1_avg
        if diff >= 5:
            parts.append(f"Qualità segnali in miglioramento (+{diff:.1f} punti in settimana 2). Setup più puliti.")
        elif diff <= -5:
            parts.append(f"Qualità segnali in calo ({diff:.1f} punti in settimana 2). Mercato più rumoroso.")

    # Absolute score quality
    if avg_score is not None:
        if avg_score < 62:
            parts.append("Score medio basso: la settimana ha prodotto pochi setup di qualità. Aspettare.")
        elif avg_score >= 75:
            parts.append("Score medio elevato: batch di buona qualità complessiva.")

    # Asset concentration
    if len(asset_classes) == 1:
        cls = next(iter(asset_classes))
        parts.append(f"Tutti i segnali su {cls}: rischio di correlazione elevato.")
    elif len(asset_classes) >= 3:
        parts.append("Buona diversificazione tra asset class.")

    # Regime
    if "VOLATILE" in dominant_regime.upper() or "CHOP" in dominant_regime.upper():
        parts.append("Regime volatile dominante: dimensioni di posizione ridotte e stop più ampi raccomandati.")
    elif "TRENDING" in dominant_regime.upper():
        parts.append("Regime trending: favorisce le strategie momentum del sistema.")

    # Low volume
    if len(signals) <= 2:
        parts.append("Settimana a basso volume di segnali: dati insufficienti per conclusioni robuste.")

    return " ".join(parts) if parts else "Settimana nella norma. Nessuna anomalia rilevata."


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _sent_at_dt(sig: dict[str, Any]) -> datetime:
    """Parse sent_at from a history record, returning UTC datetime."""
    sent_at_s = sig.get("sent_at", "")
    try:
        dt = datetime.fromisoformat(sent_at_s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Serialization helpers (make signal dicts JSON-safe)
# ---------------------------------------------------------------------------

def _serialize_sig(sig: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-serialisable copy of a signal dict."""
    out = {}
    for k, v in sig.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif hasattr(v, "item"):          # numpy scalar
            out[k] = v.item()
        elif hasattr(v, "isoformat"):     # pandas Timestamp
            out[k] = v.isoformat()
        elif isinstance(v, float) and v != v:   # NaN → None
            out[k] = None
        else:
            out[k] = v
    return out
