"""
Unit tests for signal_farm/signals/ecosystem_monitor.py

Tests classification logic and aggregation without network calls
(yfinance is patched via monkeypatch / mock).
"""
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from signals.ecosystem_monitor import (
    _classify_ecosystem,
    _classify_nas100_only,
    _nas100_label,
    aggregate_ecosystem_state,
    EcosystemState,
    NEUTRAL_STATE,
    _ECOSYSTEM_ASSET_CLASSES,
    _NAS100_ONLY_ASSET_CLASSES,
    _VIX_CACHE,
    _SECTOR_CACHE,
    _NAS100_CACHE,
)
import signals.ecosystem_monitor as eco_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_caches():
    """Clear in-process caches so tests don't bleed into each other."""
    _VIX_CACHE.clear()
    _SECTOR_CACHE.clear()
    _NAS100_CACHE.clear()


def _patch_all(vix=None, sector=None, nas100=None):
    """Context manager that patches all three data sources."""
    return (
        patch.object(eco_mod, "get_vix_level", return_value=vix),
        patch.object(eco_mod, "compute_sector_momentum", return_value=sector),
        patch.object(eco_mod, "compute_nas100_alignment", return_value=nas100),
    )


# ---------------------------------------------------------------------------
# _classify_ecosystem — pure logic, no I/O
# ---------------------------------------------------------------------------

class TestClassifyEcosystem:
    """Tests for the VIX + sector + NAS100 classification function."""

    # --- DARK_RED ---
    def test_dark_red_vix_above_25(self):
        label, mult = _classify_ecosystem(vix=26.0, sector=0)
        assert label == "DARK_RED"
        assert mult == 0.5

    def test_dark_red_sector_below_minus4(self):
        label, mult = _classify_ecosystem(vix=15.0, sector=-5)
        assert label == "DARK_RED"
        assert mult == 0.5

    def test_dark_red_both_negative(self):
        label, mult = _classify_ecosystem(vix=30.0, sector=-6)
        assert label == "DARK_RED"
        assert mult == 0.5

    # --- RED ---
    def test_red_vix_above_20(self):
        label, mult = _classify_ecosystem(vix=22.0, sector=0)
        assert label == "RED"
        assert mult == 0.7

    def test_red_sector_below_minus2(self):
        label, mult = _classify_ecosystem(vix=12.0, sector=-3)
        assert label == "RED"
        assert mult == 0.7

    def test_red_vix_just_above_20(self):
        label, mult = _classify_ecosystem(vix=20.1, sector=1)
        assert label == "RED"
        assert mult == 0.7

    # --- BRIGHT_GREEN ---
    def test_bright_green_both_conditions(self):
        label, mult = _classify_ecosystem(vix=12.0, sector=5)
        assert label == "BRIGHT_GREEN"
        assert mult == 2.0

    def test_bright_green_vix_exactly_13_not_enough(self):
        label, mult = _classify_ecosystem(vix=13.0, sector=5)
        assert label != "BRIGHT_GREEN"

    # --- GREEN ---
    def test_green_both_conditions(self):
        label, mult = _classify_ecosystem(vix=15.0, sector=3)
        assert label == "GREEN"
        assert mult == 1.5

    def test_green_vix_only_below_13(self):
        label, mult = _classify_ecosystem(vix=12.5, sector=None)
        assert label == "GREEN"
        assert mult == 1.5

    def test_green_sector_only_above_4(self):
        label, mult = _classify_ecosystem(vix=None, sector=5)
        assert label == "GREEN"
        assert mult == 1.5

    # --- GRAY ---
    def test_gray_neutral_vix(self):
        label, mult = _classify_ecosystem(vix=18.0, sector=1)
        assert label == "GRAY"
        assert mult == 1.0

    def test_gray_both_none(self):
        label, mult = _classify_ecosystem(vix=None, sector=None)
        assert label == "GRAY"
        assert mult == 1.0

    def test_gray_vix_borderline(self):
        label, mult = _classify_ecosystem(vix=19.9, sector=2)
        assert label == "GRAY"
        assert mult == 1.0

    # --- Risk-off override ---
    def test_risk_off_overrides_green_sector(self):
        """VIX > 25 overrides a positive sector score."""
        label, mult = _classify_ecosystem(vix=28.0, sector=6)
        assert label == "DARK_RED"
        assert mult == 0.5

    def test_risk_off_overrides_green_vix(self):
        """Sector < -4 overrides a low VIX."""
        label, mult = _classify_ecosystem(vix=11.0, sector=-5)
        assert label == "DARK_RED"
        assert mult == 0.5

    # --- NAS100 tiebreaker in GRAY zone ---
    def test_nas100_green_breaks_gray_tie(self):
        """GRAY VIX+sector, strong NAS100 breadth → GREEN."""
        label, mult = _classify_ecosystem(vix=18.0, sector=1, nas100_score=0.80)
        assert label == "GREEN"
        assert mult == 1.5

    def test_nas100_red_breaks_gray_tie(self):
        """GRAY VIX+sector, weak NAS100 breadth → RED."""
        label, mult = _classify_ecosystem(vix=18.0, sector=1, nas100_score=0.30)
        assert label == "RED"
        assert mult == 0.7

    def test_nas100_does_not_override_dark_red(self):
        """Risk-off conditions cannot be overridden by NAS100 alignment."""
        label, mult = _classify_ecosystem(vix=28.0, sector=-6, nas100_score=0.90)
        assert label == "DARK_RED"
        assert mult == 0.5

    def test_nas100_does_not_override_red(self):
        """VIX > 20 RED stays RED even with strong NAS100 breadth."""
        label, mult = _classify_ecosystem(vix=21.0, sector=1, nas100_score=0.90)
        assert label == "RED"
        assert mult == 0.7

    def test_nas100_gray_leaves_gray(self):
        """NAS100 in neutral range does not change GRAY outcome."""
        label, mult = _classify_ecosystem(vix=18.0, sector=1, nas100_score=0.55)
        assert label == "GRAY"
        assert mult == 1.0

    def test_nas100_none_leaves_gray(self):
        """NAS100 unavailable → does not affect GRAY classification."""
        label, mult = _classify_ecosystem(vix=18.0, sector=1, nas100_score=None)
        assert label == "GRAY"
        assert mult == 1.0


# ---------------------------------------------------------------------------
# _classify_nas100_only — crypto path
# ---------------------------------------------------------------------------

class TestClassifyNas100Only:
    def test_bright_green_above_085(self):
        label, mult = _classify_nas100_only(0.90)
        assert label == "BRIGHT_GREEN"
        assert mult == 2.0

    def test_green_above_075(self):
        label, mult = _classify_nas100_only(0.78)
        assert label == "GREEN"
        assert mult == 1.5

    def test_dark_red_below_025(self):
        label, mult = _classify_nas100_only(0.20)
        assert label == "DARK_RED"
        assert mult == 0.5

    def test_red_below_035(self):
        label, mult = _classify_nas100_only(0.30)
        assert label == "RED"
        assert mult == 0.7

    def test_gray_neutral(self):
        label, mult = _classify_nas100_only(0.55)
        assert label == "GRAY"
        assert mult == 1.0

    def test_boundary_075_is_green(self):
        # > 0.75 → GREEN; == 0.75 is not > 0.75 → GRAY
        label, _ = _classify_nas100_only(0.751)
        assert label == "GREEN"

    def test_boundary_075_exact_is_gray(self):
        label, _ = _classify_nas100_only(0.75)
        assert label == "GRAY"


# ---------------------------------------------------------------------------
# _nas100_label helper
# ---------------------------------------------------------------------------

class TestNas100Label:
    def test_green_above_075(self):
        assert _nas100_label(0.80) == "GREEN"

    def test_red_below_035(self):
        assert _nas100_label(0.30) == "RED"

    def test_gray_middle(self):
        assert _nas100_label(0.55) == "GRAY"


# ---------------------------------------------------------------------------
# aggregate_ecosystem_state — scope guard
# ---------------------------------------------------------------------------

class TestAggregateEcosystemScope:
    def setup_method(self):
        _clear_caches()

    def test_forex_returns_neutral(self):
        state = aggregate_ecosystem_state("forex")
        assert state == NEUTRAL_STATE

    def test_precious_metals_returns_neutral(self):
        state = aggregate_ecosystem_state("precious_metals")
        assert state == NEUTRAL_STATE

    def test_energies_returns_neutral(self):
        state = aggregate_ecosystem_state("energies")
        assert state == NEUTRAL_STATE

    def test_agricultural_commodities_returns_neutral(self):
        state = aggregate_ecosystem_state("agricultural_commodities")
        assert state == NEUTRAL_STATE

    def test_us_stocks_in_full_scope(self):
        assert "us_stocks" in _ECOSYSTEM_ASSET_CLASSES

    def test_indices_futures_in_full_scope(self):
        assert "indices_futures" in _ECOSYSTEM_ASSET_CLASSES

    def test_crypto_in_nas100_only_scope(self):
        assert "crypto" in _NAS100_ONLY_ASSET_CLASSES

    def test_neutral_state_constants(self):
        assert NEUTRAL_STATE.size_multiplier == 1.0
        assert NEUTRAL_STATE.label == "GRAY"
        assert NEUTRAL_STATE.confidence == "LOW"
        assert NEUTRAL_STATE.nas100_score is None
        assert NEUTRAL_STATE.nas100_alignment is None


# ---------------------------------------------------------------------------
# aggregate_ecosystem_state — full scope (us_stocks / indices_futures)
# ---------------------------------------------------------------------------

class TestAggregateFullScope:
    def setup_method(self):
        _clear_caches()

    def test_both_sources_available_gives_high_confidence(self):
        p1, p2, p3 = _patch_all(vix=14.0, sector=3.0, nas100=0.60)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("us_stocks")
        assert state.confidence == "HIGH"
        assert state.vix_level == 14.0
        assert state.sector_score == 3.0

    def test_only_vix_gives_medium_confidence(self):
        p1, p2, p3 = _patch_all(vix=14.0, sector=None, nas100=None)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("us_stocks")
        assert state.confidence == "MEDIUM"

    def test_only_sector_gives_medium_confidence(self):
        p1, p2, p3 = _patch_all(vix=None, sector=4.0, nas100=None)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("us_stocks")
        assert state.confidence == "MEDIUM"

    def test_all_unavailable_returns_neutral(self):
        p1, p2, p3 = _patch_all(vix=None, sector=None, nas100=None)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("us_stocks")
        assert state == NEUTRAL_STATE

    def test_green_state_from_good_conditions(self):
        p1, p2, p3 = _patch_all(vix=15.0, sector=4.0, nas100=0.70)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("indices_futures")
        assert state.label == "GREEN"
        assert state.size_multiplier == 1.5

    def test_bright_green_state(self):
        p1, p2, p3 = _patch_all(vix=11.0, sector=6.0, nas100=0.80)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("us_stocks")
        assert state.label == "BRIGHT_GREEN"
        assert state.size_multiplier == 2.0

    def test_dark_red_state(self):
        p1, p2, p3 = _patch_all(vix=27.0, sector=-5.0, nas100=0.30)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("us_stocks")
        assert state.label == "DARK_RED"
        assert state.size_multiplier == 0.5

    def test_red_state_from_vix(self):
        p1, p2, p3 = _patch_all(vix=21.0, sector=2.0, nas100=0.60)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("us_stocks")
        assert state.label == "RED"
        assert state.size_multiplier == 0.7

    def test_nas100_breaks_gray_to_green(self):
        """NAS100 breadth upgrades GRAY (neutral VIX+sector) to GREEN."""
        p1, p2, p3 = _patch_all(vix=18.0, sector=1.0, nas100=0.82)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("us_stocks")
        assert state.label == "GREEN"
        assert state.nas100_score == 0.82
        assert state.nas100_alignment == "GREEN"

    def test_nas100_breaks_gray_to_red(self):
        """Weak NAS100 breadth downgrades GRAY to RED."""
        p1, p2, p3 = _patch_all(vix=18.0, sector=1.0, nas100=0.28)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("us_stocks")
        assert state.label == "RED"

    def test_nas100_fields_attached_to_state(self):
        p1, p2, p3 = _patch_all(vix=15.0, sector=2.0, nas100=0.65)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("us_stocks")
        assert state.nas100_score == 0.65
        assert state.nas100_alignment == "GRAY"

    def test_returns_ecosystem_state_namedtuple(self):
        p1, p2, p3 = _patch_all(vix=15.0, sector=2.0, nas100=None)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("us_stocks")
        assert isinstance(state, EcosystemState)
        assert hasattr(state, "label")
        assert hasattr(state, "size_multiplier")
        assert hasattr(state, "nas100_score")
        assert hasattr(state, "nas100_alignment")

    def test_high_confidence_with_two_sources(self):
        """VIX + NAS100 (no sector) → still HIGH confidence (2 sources)."""
        p1, p2, p3 = _patch_all(vix=15.0, sector=None, nas100=0.70)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("us_stocks")
        assert state.confidence == "HIGH"


# ---------------------------------------------------------------------------
# aggregate_ecosystem_state — crypto path (NAS100 only)
# ---------------------------------------------------------------------------

class TestAggregateCryptoScope:
    def setup_method(self):
        _clear_caches()

    def test_crypto_nas100_unavailable_returns_neutral(self):
        p1, p2, p3 = _patch_all(nas100=None)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("crypto")
        assert state == NEUTRAL_STATE

    def test_crypto_strong_nas100_gives_green(self):
        p1, p2, p3 = _patch_all(nas100=0.80)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("crypto")
        assert state.label == "GREEN"
        assert state.size_multiplier == 1.5
        assert state.confidence == "MEDIUM"

    def test_crypto_very_strong_nas100_gives_bright_green(self):
        p1, p2, p3 = _patch_all(nas100=0.88)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("crypto")
        assert state.label == "BRIGHT_GREEN"
        assert state.size_multiplier == 2.0

    def test_crypto_weak_nas100_gives_red(self):
        p1, p2, p3 = _patch_all(nas100=0.32)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("crypto")
        assert state.label == "RED"
        assert state.size_multiplier == 0.7

    def test_crypto_very_weak_nas100_gives_dark_red(self):
        p1, p2, p3 = _patch_all(nas100=0.18)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("crypto")
        assert state.label == "DARK_RED"
        assert state.size_multiplier == 0.5

    def test_crypto_no_vix_no_sector(self):
        """Crypto never uses VIX or sector — fields should be None."""
        p1, p2, p3 = _patch_all(vix=10.0, sector=7.0, nas100=0.80)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("crypto")
        assert state.vix_level is None
        assert state.sector_score is None
        assert state.nas100_score == 0.80

    def test_crypto_neutral_nas100_gives_gray(self):
        p1, p2, p3 = _patch_all(nas100=0.55)
        with p1, p2, p3:
            state = aggregate_ecosystem_state("crypto")
        assert state.label == "GRAY"
        assert state.size_multiplier == 1.0


# ---------------------------------------------------------------------------
# EcosystemState NamedTuple
# ---------------------------------------------------------------------------

class TestEcosystemState:
    def test_fields_without_nas100(self):
        """Backward compat: EcosystemState without nas100 fields."""
        s = EcosystemState(
            label="GREEN",
            size_multiplier=1.5,
            vix_level=14.5,
            sector_score=3.0,
            confidence="HIGH",
        )
        assert s.label == "GREEN"
        assert s.size_multiplier == 1.5
        assert s.nas100_score is None
        assert s.nas100_alignment is None

    def test_fields_with_nas100(self):
        s = EcosystemState(
            label="GREEN",
            size_multiplier=1.5,
            vix_level=14.5,
            sector_score=3.0,
            confidence="HIGH",
            nas100_score=0.78,
            nas100_alignment="GREEN",
        )
        assert s.nas100_score == 0.78
        assert s.nas100_alignment == "GREEN"

    def test_immutable(self):
        s = EcosystemState("GRAY", 1.0, None, None, "LOW")
        with pytest.raises(AttributeError):
            s.label = "GREEN"

    def test_equality(self):
        s1 = EcosystemState("GREEN", 1.5, 14.0, 3.0, "HIGH")
        s2 = EcosystemState("GREEN", 1.5, 14.0, 3.0, "HIGH")
        assert s1 == s2

    def test_neutral_state_constant(self):
        assert NEUTRAL_STATE.label == "GRAY"
        assert NEUTRAL_STATE.size_multiplier == 1.0
        assert NEUTRAL_STATE.confidence == "LOW"
        assert NEUTRAL_STATE.nas100_score is None
        assert NEUTRAL_STATE.nas100_alignment is None


# ---------------------------------------------------------------------------
# VIX cache behavior
# ---------------------------------------------------------------------------

class TestVixCache:
    def setup_method(self):
        _clear_caches()

    def test_vix_cached_on_second_call(self):
        """Second call should return cached value without fetching."""
        call_count = 0

        def mock_history(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            import pandas as pd
            return pd.DataFrame({"Close": [20.0]}, index=pd.date_range("2026-01-01", periods=1))

        mock_ticker = MagicMock()
        mock_ticker.history = mock_history

        with patch("yfinance.Ticker", return_value=mock_ticker):
            from signals.ecosystem_monitor import get_vix_level
            v1 = get_vix_level()
            v2 = get_vix_level()

        assert v1 == v2
        assert call_count == 1  # only one actual fetch

    def test_vix_returns_none_on_empty_history(self):
        import pandas as pd
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        with patch("yfinance.Ticker", return_value=mock_ticker):
            from signals.ecosystem_monitor import get_vix_level
            result = get_vix_level()
        assert result is None

    def test_vix_returns_none_on_exception(self):
        with patch("yfinance.Ticker", side_effect=Exception("network error")):
            from signals.ecosystem_monitor import get_vix_level
            result = get_vix_level()
        assert result is None
