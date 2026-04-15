"""
Unit tests for signal_farm/recapper.py

Covers:
  - append_to_history / load_history round-trip
  - format_history_list
  - format_open_brief
  - format_close_brief
  - generate_reading (rule-based interpretive text)
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

import pytest

# Make sure signal_farm/ is importable when running from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import recapper


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_sig(
    canonical="AAPL",
    direction="LONG",
    asset_class="us_stocks",
    variant="A",
    score=75.0,
    entry=200.0,
    stop=195.0,
    target=210.0,
    rr=2.0,
    ctx_market_name="NASDAQ",
    ctx_market_label="BULL",
    ctx_market_roc=0.07,
    ctx_regime="TRENDING",
    score_trend=30,
    score_entry=20,
) -> dict:
    return {
        "canonical": canonical,
        "direction": direction,
        "asset_class": asset_class,
        "variant_used": variant,
        "signal_score": score,
        "score_trend": score_trend,
        "score_momentum": 25,
        "score_entry": score_entry,
        "entry_price": entry,
        "stop": stop,
        "target": target,
        "rr": rr,
        "ctx_market_name": ctx_market_name,
        "ctx_market_label": ctx_market_label,
        "ctx_market_roc": ctx_market_roc,
        "ctx_regime": ctx_regime,
        "ctx_trend_label": "BULL",
        "ctx_rsi": 55.0,
        "signal_time": datetime(2026, 4, 15, 9, 30, tzinfo=timezone.utc),
        "bars_ago": 0,
    }


@pytest.fixture
def tmp_history(tmp_path, monkeypatch):
    """Redirect history file to a temp location for each test."""
    path = str(tmp_path / "test_history.jsonl")
    monkeypatch.setenv("SIGNAL_FARM_HISTORY_FILE", path)
    return path


# ---------------------------------------------------------------------------
# History persistence
# ---------------------------------------------------------------------------

class TestHistory:
    def test_append_creates_file(self, tmp_history):
        sig = _make_sig()
        recapper.append_to_history(sig)
        assert os.path.exists(tmp_history)

    def test_append_round_trip(self, tmp_history):
        sig = _make_sig(canonical="TSLA", direction="SHORT")
        recapper.append_to_history(sig)

        signals = recapper.load_history(hours=1)
        assert len(signals) == 1
        assert signals[0]["canonical"] == "TSLA"
        assert signals[0]["direction"] == "SHORT"

    def test_load_returns_recent_only(self, tmp_history):
        # Write two records: one recent, one old
        recent_sig = _make_sig(canonical="AAPL")
        old_sig    = _make_sig(canonical="MSFT")

        recapper.append_to_history(recent_sig)

        # Manually write an old record
        old_record = recapper._serialize_sig(old_sig)
        old_record["sent_at"] = (
            datetime.now(tz=timezone.utc) - timedelta(hours=30)
        ).isoformat()
        with open(tmp_history, "a", encoding="utf-8") as f:
            f.write(json.dumps(old_record) + "\n")

        signals = recapper.load_history(hours=24)
        assert len(signals) == 1
        assert signals[0]["canonical"] == "AAPL"

    def test_load_empty_file(self, tmp_history):
        open(tmp_history, "w").close()  # create empty
        assert recapper.load_history(hours=24) == []

    def test_load_nonexistent_file(self, tmp_history):
        assert recapper.load_history(hours=24) == []

    def test_append_serializes_datetime(self, tmp_history):
        sig = _make_sig()
        recapper.append_to_history(sig)

        with open(tmp_history, encoding="utf-8") as f:
            line = f.readline()
        record = json.loads(line)
        # signal_time should be an ISO string, not a datetime object
        assert isinstance(record.get("signal_time"), str)
        assert "sent_at" in record

    def test_multiple_appends_sorted_newest_first(self, tmp_history):
        for name in ["AAA", "BBB", "CCC"]:
            recapper.append_to_history(_make_sig(canonical=name))

        signals = recapper.load_history(hours=1)
        assert len(signals) == 3
        # newest-first: CCC was appended last
        assert signals[0]["canonical"] == "CCC"
        assert signals[-1]["canonical"] == "AAA"


# ---------------------------------------------------------------------------
# format_history_list
# ---------------------------------------------------------------------------

class TestFormatHistoryList:
    def test_empty(self):
        result = recapper.format_history_list([])
        assert "Nessun segnale" in result

    def test_contains_canonical(self):
        sig = _make_sig(canonical="NVDA")
        sig["sent_at"] = datetime.now(tz=timezone.utc).isoformat()
        result = recapper.format_history_list([sig])
        assert "NVDA" in result

    def test_contains_direction(self):
        sig = _make_sig(direction="SHORT")
        sig["sent_at"] = datetime.now(tz=timezone.utc).isoformat()
        result = recapper.format_history_list([sig])
        assert "SHORT" in result

    def test_contains_score(self):
        sig = _make_sig(score=82.0)
        sig["sent_at"] = datetime.now(tz=timezone.utc).isoformat()
        result = recapper.format_history_list([sig])
        assert "82" in result


# ---------------------------------------------------------------------------
# format_open_brief
# ---------------------------------------------------------------------------

class TestFormatOpenBrief:
    def test_empty_signals(self):
        result = recapper.format_open_brief([])
        assert "Nessun segnale" in result
        assert "SESSION OPEN BRIEF" in result

    def test_contains_canonical(self):
        sig = _make_sig(canonical="MSFT")
        sig["sent_at"] = datetime.now(tz=timezone.utc).isoformat()
        result = recapper.format_open_brief([sig])
        assert "MSFT" in result

    def test_contains_macro_market(self):
        sig = _make_sig(ctx_market_name="NASDAQ", ctx_market_label="BULL")
        sig["sent_at"] = datetime.now(tz=timezone.utc).isoformat()
        result = recapper.format_open_brief([sig])
        assert "NASDAQ" in result

    def test_contains_reading(self):
        sig1 = _make_sig(canonical="AAPL", direction="LONG")
        sig2 = _make_sig(canonical="MSFT", direction="LONG")
        for s in [sig1, sig2]:
            s["sent_at"] = datetime.now(tz=timezone.utc).isoformat()
        result = recapper.format_open_brief([sig1, sig2])
        assert "LETTURA" in result

    def test_multiple_signals_count(self):
        sigs = [_make_sig(canonical=f"T{i}") for i in range(3)]
        for s in sigs:
            s["sent_at"] = datetime.now(tz=timezone.utc).isoformat()
        result = recapper.format_open_brief(sigs)
        assert "3" in result


# ---------------------------------------------------------------------------
# format_close_brief
# ---------------------------------------------------------------------------

class TestFormatCloseBrief:
    def test_empty_signals(self):
        result = recapper.format_close_brief([])
        assert "SESSION CLOSE" in result
        assert "Nessun segnale" in result

    def test_contains_sent_count(self):
        sigs = [_make_sig(canonical=f"X{i}") for i in range(2)]
        for s in sigs:
            s["sent_at"] = datetime.now(tz=timezone.utc).isoformat()
        result = recapper.format_close_brief(sigs)
        assert "2" in result

    def test_contains_direction_breakdown(self):
        sig1 = _make_sig(direction="LONG")
        sig2 = _make_sig(direction="SHORT")
        for s in [sig1, sig2]:
            s["sent_at"] = datetime.now(tz=timezone.utc).isoformat()
        result = recapper.format_close_brief([sig1, sig2])
        assert "LONG" in result
        assert "SHORT" in result

    def test_contains_avg_score(self):
        sig = _make_sig(score=74.0)
        sig["sent_at"] = datetime.now(tz=timezone.utc).isoformat()
        result = recapper.format_close_brief([sig])
        assert "74" in result

    def test_contains_asset_class(self):
        sig = _make_sig(asset_class="crypto")
        sig["sent_at"] = datetime.now(tz=timezone.utc).isoformat()
        result = recapper.format_close_brief([sig])
        assert "crypto" in result


# ---------------------------------------------------------------------------
# generate_reading (rule-based)
# ---------------------------------------------------------------------------

class TestGenerateReading:
    def test_empty(self):
        assert recapper.generate_reading([]) == ""

    def test_two_longs_bull_macro(self):
        sigs = [
            _make_sig(direction="LONG", ctx_market_label="BULL"),
            _make_sig(direction="LONG", ctx_market_label="BULL"),
        ]
        result = recapper.generate_reading(sigs)
        assert "follow-through" in result.lower() or "allineati" in result.lower()

    def test_two_shorts_bear_macro(self):
        sigs = [
            _make_sig(direction="SHORT", ctx_market_label="BEAR"),
            _make_sig(direction="SHORT", ctx_market_label="BEAR"),
        ]
        result = recapper.generate_reading(sigs)
        assert "ribassista" in result.lower() or "follow-through" in result.lower()

    def test_mixed_signals(self):
        sigs = [
            _make_sig(direction="LONG"),
            _make_sig(direction="SHORT"),
        ]
        result = recapper.generate_reading(sigs)
        assert "misti" in result.lower() or "transizione" in result.lower()

    def test_low_score(self):
        sigs = [_make_sig(score=55.0), _make_sig(score=60.0)]
        result = recapper.generate_reading(sigs)
        assert "qualità bassa" in result.lower() or "conferma" in result.lower()

    def test_high_score(self):
        sigs = [_make_sig(score=80.0), _make_sig(score=82.0)]
        result = recapper.generate_reading(sigs)
        assert "alta qualità" in result.lower() or "standard" in result.lower()

    def test_concentration_warning(self):
        sigs = [
            _make_sig(canonical="A", asset_class="us_stocks"),
            _make_sig(canonical="B", asset_class="us_stocks"),
        ]
        result = recapper.generate_reading(sigs)
        assert "us_stocks" in result or "concentrazione" in result.lower()

    def test_volatile_regime(self):
        sigs = [_make_sig(ctx_regime="VOLATILE")]
        result = recapper.generate_reading(sigs)
        assert "volatilità" in result.lower() or "stop" in result.lower()

    def test_returns_string(self):
        sig = _make_sig()
        result = recapper.generate_reading([sig])
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# format_week_brief
# ---------------------------------------------------------------------------

def _make_sig_days_ago(days: float, **kwargs) -> dict:
    sig = _make_sig(**kwargs)
    sig["sent_at"] = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()
    return sig


class TestFormatWeekBrief:
    def test_empty(self):
        result = recapper.format_week_brief([])
        assert "WEEK RECAP" in result
        assert "Nessun segnale" in result

    def test_contains_total_count(self):
        sigs = [_make_sig_days_ago(i) for i in range(1, 5)]
        result = recapper.format_week_brief(sigs)
        assert "4" in result

    def test_contains_direction_breakdown(self):
        sigs = [
            _make_sig_days_ago(1, direction="LONG"),
            _make_sig_days_ago(2, direction="SHORT"),
        ]
        result = recapper.format_week_brief(sigs)
        assert "LONG" in result
        assert "SHORT" in result

    def test_contains_asset_class(self):
        sigs = [_make_sig_days_ago(1, asset_class="crypto")]
        result = recapper.format_week_brief(sigs)
        assert "crypto" in result

    def test_contains_score_stats(self):
        sigs = [_make_sig_days_ago(1, score=72.0), _make_sig_days_ago(2, score=80.0)]
        result = recapper.format_week_brief(sigs)
        assert "76" in result or "72" in result or "80" in result

    def test_contains_disclaimer(self):
        sigs = [_make_sig_days_ago(1)]
        result = recapper.format_week_brief(sigs)
        assert "outcome" in result.lower()

    def test_week_over_week_trend_shown(self):
        # week1: 8-14 days ago, week2: 1-7 days ago
        sigs = (
            [_make_sig_days_ago(10, score=60.0) for _ in range(3)] +
            [_make_sig_days_ago(2,  score=80.0) for _ in range(3)]
        )
        result = recapper.format_week_brief(sigs)
        # score trend section should appear
        assert "sett." in result

    def test_contains_lettura(self):
        sigs = [_make_sig_days_ago(i, direction="LONG", ctx_market_label="BULL") for i in range(1, 6)]
        result = recapper.format_week_brief(sigs)
        assert "LETTURA" in result


# ---------------------------------------------------------------------------
# generate_week_reading (rule-based)
# ---------------------------------------------------------------------------

class TestGenerateWeekReading:
    def _call(self, signals, dominant_mkt="BULL", dominant_regime="TRENDING"):
        week1 = signals[:len(signals)//2]
        week2 = signals[len(signals)//2:]
        return recapper.generate_week_reading(signals, week1, week2, dominant_mkt, dominant_regime)

    def test_empty(self):
        result = recapper.generate_week_reading([], [], [], "", "")
        assert result == ""

    def test_strong_long_bull(self):
        sigs = [_make_sig(direction="LONG") for _ in range(8)] + [_make_sig(direction="SHORT")]
        result = self._call(sigs, dominant_mkt="BULL")
        assert "long" in result.lower() or "momentum" in result.lower()

    def test_strong_short_bear(self):
        sigs = [_make_sig(direction="SHORT") for _ in range(8)] + [_make_sig(direction="LONG")]
        result = self._call(sigs, dominant_mkt="BEAR")
        assert "short" in result.lower() or "difensiva" in result.lower()

    def test_balanced_signals(self):
        sigs = [_make_sig(direction="LONG") for _ in range(5)] + [_make_sig(direction="SHORT") for _ in range(5)]
        result = self._call(sigs)
        assert "equilibrat" in result.lower() or "tendenza" in result.lower() or "selettività" in result.lower()

    def test_score_improving(self):
        week1 = [_make_sig(score=60.0)] * 3
        week2 = [_make_sig(score=80.0)] * 3
        result = recapper.generate_week_reading(week1 + week2, week1, week2, "BULL", "TRENDING")
        assert "miglioramento" in result.lower() or "puliti" in result.lower()

    def test_score_declining(self):
        week1 = [_make_sig(score=80.0)] * 3
        week2 = [_make_sig(score=58.0)] * 3
        result = recapper.generate_week_reading(week1 + week2, week1, week2, "BULL", "TRENDING")
        assert "calo" in result.lower() or "rumoroso" in result.lower()

    def test_volatile_regime(self):
        sigs = [_make_sig()] * 4
        result = self._call(sigs, dominant_regime="VOLATILE")
        assert "volatile" in result.lower() or "stop" in result.lower()

    def test_trending_regime(self):
        sigs = [_make_sig()] * 4
        result = self._call(sigs, dominant_regime="TRENDING")
        assert "trending" in result.lower() or "momentum" in result.lower()

    def test_single_asset_class_concentration(self):
        sigs = [_make_sig(asset_class="crypto")] * 5
        result = self._call(sigs)
        assert "crypto" in result or "correlazione" in result.lower()

    def test_diverse_asset_classes(self):
        sigs = [
            _make_sig(asset_class="us_stocks"),
            _make_sig(asset_class="crypto"),
            _make_sig(asset_class="forex"),
        ]
        result = self._call(sigs)
        assert "diversificazione" in result.lower() or "asset" in result.lower()

    def test_low_volume_warning(self):
        sigs = [_make_sig()]
        result = self._call(sigs)
        assert "basso volume" in result.lower() or "insufficienti" in result.lower()

    def test_returns_string(self):
        sigs = [_make_sig()] * 3
        result = self._call(sigs)
        assert isinstance(result, str)
        assert len(result) > 0
