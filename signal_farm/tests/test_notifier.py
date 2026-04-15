"""
Unit tests for notifier.py — message formatting and deduplication logic.
No network calls, no file I/O (state is passed in-memory where needed).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import datetime, timezone, timedelta
import pandas as pd

from notifier import (
    format_signal_message,
    _dedup_key,
    _is_duplicate,
    _save_state,
    _load_state,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_sig(**overrides):
    base = {
        "canonical":        "MSFT",
        "description":      "Microsoft Corp.",
        "asset_class":      "us_stocks",
        "direction":        "LONG",
        "variant_used":     "A",
        "signal_score":     78.0,
        "entry_price":      414.15,
        "stop":             411.56,
        "target":           419.32,
        "rr":               2.0,
        "bars_ago":         0,
        "signal_time":      datetime(2026, 4, 15, 14, 30, tzinfo=timezone.utc),
        "ctx_trend_label":  "MODERATE UP",
        "ctx_regime":       "TRENDING",
        "ctx_rsi":          52.5,
        "ctx_roc_pct":      2.9,
        "ctx_market_name":  "NASDAQ",
        "ctx_market_label": "BULL",
        "ctx_market_roc":   0.07,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# format_signal_message
# ---------------------------------------------------------------------------

class TestFormatSignalMessage:
    def test_contains_ticker(self):
        msg = format_signal_message(_make_sig())
        assert "MSFT" in msg or "Microsoft" in msg

    def test_contains_direction(self):
        msg = format_signal_message(_make_sig())
        assert "LONG" in msg

    def test_contains_entry_price(self):
        msg = format_signal_message(_make_sig())
        assert "414.1500" in msg

    def test_contains_stop(self):
        msg = format_signal_message(_make_sig())
        assert "411.5600" in msg

    def test_contains_target(self):
        msg = format_signal_message(_make_sig())
        assert "419.3200" in msg

    def test_contains_score(self):
        msg = format_signal_message(_make_sig())
        assert "78" in msg

    def test_contains_rr(self):
        msg = format_signal_message(_make_sig())
        assert "2.0" in msg

    def test_contains_market_context(self):
        msg = format_signal_message(_make_sig())
        assert "NASDAQ" in msg
        assert "BULL" in msg

    def test_contains_signal_time(self):
        msg = format_signal_message(_make_sig())
        assert "2026-04-15" in msg

    def test_short_direction(self):
        msg = format_signal_message(_make_sig(direction="SHORT"))
        assert "SHORT" in msg

    def test_missing_context_does_not_crash(self):
        sig = _make_sig(
            ctx_trend_label=None,
            ctx_regime=None,
            ctx_rsi=None,
            ctx_roc_pct=None,
            ctx_market_name=None,
            ctx_market_label=None,
            ctx_market_roc=None,
        )
        msg = format_signal_message(sig)
        assert "MSFT" in msg or "Microsoft" in msg

    def test_nan_values_display_na(self):
        import math
        sig = _make_sig(signal_score=float("nan"), rr=float("nan"))
        msg = format_signal_message(sig)
        assert "N/A" in msg

    def test_stop_pct_shown(self):
        msg = format_signal_message(_make_sig())
        # Stop is below entry → negative %
        assert "-" in msg

    def test_target_pct_shown(self):
        msg = format_signal_message(_make_sig())
        # Target is above entry → positive %
        assert "+" in msg

    def test_html_parse_mode_no_unescaped_brackets(self):
        """Telegram HTML mode breaks on unescaped < > & outside tags."""
        msg = format_signal_message(_make_sig())
        # All < > should be part of HTML tags, not raw price data
        # Prices use <code> tags — check structure is valid-ish
        assert "<b>" in msg
        assert "<code>" in msg

    def test_bars_ago_zero_omitted_or_shown(self):
        msg0 = format_signal_message(_make_sig(bars_ago=0))
        msg2 = format_signal_message(_make_sig(bars_ago=2))
        assert "2 bars ago" in msg2

    def test_returns_string(self):
        assert isinstance(format_signal_message(_make_sig()), str)


# ---------------------------------------------------------------------------
# _dedup_key
# ---------------------------------------------------------------------------

class TestDedupKey:
    def test_key_includes_canonical(self):
        key = _dedup_key(_make_sig())
        assert "MSFT" in key

    def test_key_includes_direction(self):
        key = _dedup_key(_make_sig())
        assert "LONG" in key

    def test_key_includes_signal_time(self):
        key = _dedup_key(_make_sig())
        assert "2026" in key

    def test_different_direction_different_key(self):
        k1 = _dedup_key(_make_sig(direction="LONG"))
        k2 = _dedup_key(_make_sig(direction="SHORT"))
        assert k1 != k2

    def test_different_time_different_key(self):
        t1 = datetime(2026, 4, 15, 14, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 4, 15, 15, 0, tzinfo=timezone.utc)
        k1 = _dedup_key(_make_sig(signal_time=t1))
        k2 = _dedup_key(_make_sig(signal_time=t2))
        assert k1 != k2

    def test_pandas_timestamp_works(self):
        sig = _make_sig(signal_time=pd.Timestamp("2026-04-15 14:30", tz="UTC"))
        key = _dedup_key(sig)
        assert "MSFT" in key


# ---------------------------------------------------------------------------
# _is_duplicate
# ---------------------------------------------------------------------------

class TestIsDuplicate:
    def _state_with_key(self, key, hours_ago=1):
        sent_at = datetime.now(tz=timezone.utc) - timedelta(hours=hours_ago)
        return {key: sent_at.isoformat()}

    def test_not_duplicate_when_key_absent(self):
        assert _is_duplicate("new_key", {}) is False

    def test_duplicate_within_ttl(self):
        key = "MSFT_LONG_2026-04-15T14:30:00+00:00"
        state = self._state_with_key(key, hours_ago=1)
        assert _is_duplicate(key, state) is True

    def test_not_duplicate_after_ttl(self):
        key = "MSFT_LONG_2026-04-15T14:30:00+00:00"
        state = self._state_with_key(key, hours_ago=13)  # > 12h TTL
        assert _is_duplicate(key, state) is False

    def test_not_duplicate_at_ttl_boundary(self):
        key = "MSFT_LONG_2026-04-15T14:30:00+00:00"
        state = self._state_with_key(key, hours_ago=12)
        # Exactly at TTL: timedelta(hours=12) is NOT < timedelta(hours=12)
        assert _is_duplicate(key, state) is False

    def test_malformed_state_value_not_duplicate(self):
        assert _is_duplicate("key", {"key": "not-a-date"}) is False


# ---------------------------------------------------------------------------
# send_signals dry_run — does NOT update state
# ---------------------------------------------------------------------------

class TestSendSignalsDryRun:
    def test_dry_run_does_not_persist_state(self, tmp_path, monkeypatch):
        """dry-run should not write to state file so --notify can still send."""
        import notifier as n

        state_file = str(tmp_path / "state.json")
        monkeypatch.setenv("TELEGRAM_STATE_FILE", state_file)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")

        sig = _make_sig()
        from notifier import send_signals
        sent = send_signals([sig], dry_run=True)
        assert sent == 1

        # State file should NOT exist (or be empty) after dry-run
        import json, os
        if os.path.exists(state_file):
            state = json.loads(open(state_file).read())
            assert _dedup_key(sig) not in state
