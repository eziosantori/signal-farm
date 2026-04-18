"""
Telegram notifier for Signal Farm.

Sends a formatted alert card for each new signal via the Telegram Bot API.
Deduplicates: the same (ticker, direction, signal_time) is only sent once per
DEDUP_TTL_HOURS window, using a local state file.

Configuration (from .env or environment):
    TELEGRAM_BOT_TOKEN  — bot token from @BotFather
    TELEGRAM_CHAT_ID    — chat / channel ID to send messages to
    TELEGRAM_STATE_FILE — path to dedup state file (default: .signal_farm_state.json)

Usage
-----
    from notifier import send_signals
    send_signals(list_of_signal_dicts)

    # Or from CLI:
    python main.py scan --notify
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any
from urllib import request, error as urllib_error

logger = logging.getLogger(__name__)

DEDUP_TTL_HOURS = 12

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

_DIRECTION_ICON = {"LONG": "\U0001f7e2", "SHORT": "\U0001f534"}   # 🟢 🔴
_DIRECTION_ARROW = {"LONG": "\u2197", "SHORT": "\u2198"}           # ↗ ↘

_VARIANT_LABELS = {"A": "Pullback", "B": "Breakout", "C": "Hybrid"}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def send_signals(signals: list[dict[str, Any]], dry_run: bool = False) -> int:
    """
    Send Telegram alerts for each signal in `signals`.

    Skips signals already sent within DEDUP_TTL_HOURS.
    Returns number of messages actually sent.
    """
    if not signals:
        return 0

    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

    if not dry_run and (not token or not chat_id):
        logger.warning("notifier: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping")
        return 0

    state = _load_state()
    sent  = 0

    for sig in signals:
        key = _dedup_key(sig)
        if _is_duplicate(key, state):
            logger.debug("notifier: skipping duplicate signal %s", key)
            continue

        message = format_signal_message(sig)

        if dry_run:
            print("── DRY RUN ──────────────────────────────")
            print(message)
            print("─────────────────────────────────────────")
            sent += 1
            # dry-run does NOT update state — allows real --notify to still send
            continue
        else:
            success = _send_telegram(token, chat_id, message)
            if not success:
                continue  # don't mark as sent if delivery failed

        state[key] = datetime.now(tz=timezone.utc).isoformat()
        sent += 1

        # Persist full signal payload to history (skip in dry-run — already handled above)
        try:
            from recapper import append_to_history
            append_to_history(sig)
        except Exception as exc:
            logger.warning("notifier: could not append to history: %s", exc)

    _save_state(state)
    return sent


# ---------------------------------------------------------------------------
# Message formatting  (pure — easy to test)
# ---------------------------------------------------------------------------

def format_signal_message(sig: dict[str, Any]) -> str:
    """
    Render a signal dict as an HTML-formatted Telegram message.
    Uses HTML parse_mode (safer than MarkdownV2 with special chars in prices).
    """
    canonical   = sig.get("canonical", "?")
    description = sig.get("description", canonical)
    asset_class = sig.get("asset_class", "")
    direction   = sig.get("direction", "?")
    variant     = sig.get("variant_used") or sig.get("variant", "?")
    score       = sig.get("signal_score")
    bars_ago    = sig.get("bars_ago", 0)
    sig_time    = sig.get("signal_time")

    entry  = sig.get("entry_price")
    stop   = sig.get("stop")
    target = sig.get("target")
    rr     = sig.get("rr")

    score_trend    = sig.get("score_trend")
    score_momentum = sig.get("score_momentum")
    score_entry    = sig.get("score_entry")
    ctx_rel_vol    = sig.get("ctx_rel_vol")
    ctx_atr_pct    = sig.get("ctx_atr_pct")

    ctx_trend  = sig.get("ctx_trend_label") or "—"
    ctx_regime = sig.get("ctx_regime") or ""
    ctx_rsi    = sig.get("ctx_rsi")
    ctx_roc    = sig.get("ctx_roc_pct")
    mkt_name   = sig.get("ctx_market_name") or ""
    mkt_label  = sig.get("ctx_market_label") or ""
    mkt_roc    = sig.get("ctx_market_roc")

    icon  = _DIRECTION_ICON.get(direction, "\U0001f7e1")   # 🟡 fallback
    arrow = _DIRECTION_ARROW.get(direction, "\u2192")

    def _p(v, fmt=".4f", fallback="N/A"):
        if v is None or (isinstance(v, float) and v != v):
            return fallback
        try:
            return format(v, fmt)
        except Exception:
            return str(v)

    score_str = f"{score:.0f}/100" if score is not None and score == score else "N/A"

    def _bar(val, max_val, width=8):
        if val is None or (isinstance(val, float) and val != val) or max_val == 0:
            return "░" * width
        filled = round((float(val) / max_val) * width)
        filled = max(0, min(width, filled))
        return "█" * filled + "░" * (width - filled)

    # Stop / target distance as %
    if entry and stop and entry != 0:
        stop_pct  = f"({(stop - entry) / entry * 100:+.1f}%)"
        tgt_pct   = f"({(target - entry) / entry * 100:+.1f}%)" if target else ""
    else:
        stop_pct = tgt_pct = ""

    time_str = (
        sig_time.strftime("%Y-%m-%d %H:%M UTC")
        if isinstance(sig_time, datetime)
        else (str(sig_time)[:16] if sig_time else "—")
    )
    bars_str = f"({bars_ago} bar{'s' if bars_ago != 1 else ''} ago)" if bars_ago else ""

    lines = [
        f"\U0001f514 <b>SIGNAL FARM</b>",
        "\u2501" * 22,
        f"{icon} <b>{direction} {arrow} {description}</b>",
        f"<i>{asset_class} | {_VARIANT_LABELS.get(variant, variant)} | Score {score_str}</i>",
        "",
        f"\U0001f4b0 Entry:   <code>{_p(entry)}</code>",
        f"\U0001f6d1 Stop:    <code>{_p(stop)}</code>  {stop_pct}",
        f"\U0001f3af Target:  <code>{_p(target)}</code>  {tgt_pct}",
        f"\U0001f4ca R:R {_p(rr, '.1f')}",
        "",
    ]

    # Score breakdown block
    has_subscores = any(v is not None for v in [score_trend, score_momentum, score_entry])
    if has_subscores:
        lines.append("\U0001f4ca <b>Score</b>  " + score_str)
        if score_trend is not None and score_trend == score_trend:
            roc_s = f"  ROC {ctx_roc:+.1f}%" if ctx_roc is not None and ctx_roc == ctx_roc else ""
            lines.append(f"  Trend      <code>{_bar(score_trend, 45)}</code>  {_p(score_trend, '.0f'):>2}/45{roc_s}")
        if score_momentum is not None and score_momentum == score_momentum:
            rsi_s = f"  RSI {ctx_rsi:.1f}" if ctx_rsi is not None and ctx_rsi == ctx_rsi else ""
            lines.append(f"  Momentum   <code>{_bar(score_momentum, 30)}</code>  {_p(score_momentum, '.0f'):>2}/30{rsi_s}")
        if score_entry is not None and score_entry == score_entry:
            vol_s = f"  Vol {ctx_rel_vol:.1f}x" if ctx_rel_vol is not None and ctx_rel_vol == ctx_rel_vol else ""
            atr_s = f"  ATR {ctx_atr_pct:.2f}%" if ctx_atr_pct is not None and ctx_atr_pct == ctx_atr_pct else ""
            lines.append(f"  Entry      <code>{_bar(score_entry, 25)}</code>  {_p(score_entry, '.0f'):>2}/25{vol_s}{atr_s}")
        lines.append("")

    # Context block — skip trend/RSI if already shown in score breakdown
    ctx_parts = []
    if ctx_trend and ctx_trend != "—" and not has_subscores:
        roc_s = f" ROC {ctx_roc:+.1f}%" if ctx_roc is not None and ctx_roc == ctx_roc else ""
        ctx_parts.append(f"Trend: {ctx_trend}{roc_s}")
    if ctx_regime:
        ctx_parts.append(f"Regime: {ctx_regime}")
    if ctx_rsi is not None and ctx_rsi == ctx_rsi and not has_subscores:
        ctx_parts.append(f"RSI {ctx_rsi:.1f}")
    if mkt_name and mkt_label:
        mkt_roc_s = f" ({mkt_roc * 100:+.1f}%)" if mkt_roc is not None and mkt_roc == mkt_roc else ""
        ctx_parts.append(f"{mkt_name}: {mkt_label}{mkt_roc_s}")

    if ctx_parts:
        lines.append("\U0001f4c8 <b>Context</b>")
        lines.extend(f"  {p}" for p in ctx_parts)
        lines.append("")

    # Ecosystem block — shown for US/indices/crypto when non-neutral
    eco_label  = sig.get("ecosystem_label")
    eco_mult   = sig.get("ecosystem_multiplier")
    eco_vix    = sig.get("ecosystem_vix")
    eco_sect   = sig.get("ecosystem_sector_score")
    eco_nas100 = sig.get("ecosystem_nas100_score")
    eco_nas_al = sig.get("ecosystem_nas100_alignment")

    if eco_label and eco_label != "GRAY":
        _ECO_ICON = {
            "BRIGHT_GREEN": "\U0001f7e2\U0001f7e2",   # 🟢🟢
            "GREEN":        "\U0001f7e2",               # 🟢
            "RED":          "\U0001f7e1",               # 🟡
            "DARK_RED":     "\U0001f534",               # 🔴
        }
        eco_icon  = _ECO_ICON.get(eco_label, "\u26aa")  # ⚪ fallback
        vix_s     = f"VIX {eco_vix:.1f}" if eco_vix is not None else ""
        sect_s    = f"Settori {eco_sect:+.0f}/7" if eco_sect is not None else ""
        nas_s     = f"NAS100 {eco_nas_al} {eco_nas100*100:.0f}%" if eco_nas100 is not None and eco_nas_al else ""
        mult_s    = f"Size {eco_mult:.1f}×" if eco_mult is not None else ""
        eco_parts = [p for p in [vix_s, sect_s, nas_s, mult_s] if p]
        lines.append(f"\U0001f321\ufe0f <b>Ecosistema</b>: {eco_icon} {eco_label}  ({' | '.join(eco_parts)})")
        lines.append("")

    lines.append(f"\u23f0 {time_str}  {bars_str}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _dedup_key(sig: dict) -> str:
    canonical  = sig.get("canonical", "")
    direction  = sig.get("direction", "")
    variant    = sig.get("variant_used") or sig.get("variant", "")
    sig_time   = sig.get("signal_time")
    time_part  = sig_time.isoformat() if isinstance(sig_time, datetime) else str(sig_time)
    return f"{canonical}_{direction}_{variant}_{time_part}"


def _is_duplicate(key: str, state: dict) -> bool:
    sent_at_s = state.get(key)
    if not sent_at_s:
        return False
    try:
        sent_at = datetime.fromisoformat(sent_at_s)
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        age = datetime.now(tz=timezone.utc) - sent_at
        return age < timedelta(hours=DEDUP_TTL_HOURS)
    except Exception:
        return False


def _state_file_path() -> str:
    return os.environ.get(
        "TELEGRAM_STATE_FILE",
        os.path.join(os.path.dirname(__file__), "..", ".signal_farm_state.json"),
    )


def _load_state() -> dict:
    path = _state_file_path()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_state(state: dict) -> None:
    path = _state_file_path()
    # Prune entries older than 2× TTL to keep the file small
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=DEDUP_TTL_HOURS * 2)
    pruned = {}
    for k, v in state.items():
        try:
            ts = datetime.fromisoformat(v)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts > cutoff:
                pruned[k] = v
        except Exception:
            pass
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pruned, f, indent=2)
    except Exception as exc:
        logger.warning("notifier: could not save state file: %s", exc)


# ---------------------------------------------------------------------------
# HTTP delivery
# ---------------------------------------------------------------------------

def _send_telegram(token: str, chat_id: str, text: str) -> bool:
    """POST a message to Telegram. Returns True on success."""
    url = _TELEGRAM_API.format(token=token)
    payload = json.dumps({
        "chat_id":    chat_id,
        "text":       text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")

    req = request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
            if not body.get("ok"):
                logger.warning("notifier: Telegram API error: %s", body)
                return False
            return True
    except urllib_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        logger.warning("notifier: HTTP %s — %s", exc.code, body)
        return False
    except Exception as exc:
        logger.warning("notifier: delivery failed: %s", exc)
        return False
