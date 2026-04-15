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
    aggregate_ecosystem_state,
    EcosystemState,
    NEUTRAL_STATE,
    _ECOSYSTEM_ASSET_CLASSES,
    _VIX_CACHE,
    _SECTOR_CACHE,
)
import signals.ecosystem_monitor as eco_mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear_caches():
    """Clear in-process caches so tests don't bleed into each other."""
    _VIX_CACHE.clear()
    _SECTOR_CACHE.clear()


# ---------------------------------------------------------------------------
# _classify_ecosystem — pure logic, no I/O
# ---------------------------------------------------------------------------

class TestClassifyEcosystem:
    """Tests for the pure classification function."""

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
        # vix must be < 13
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
        # Both None should not reach _classify_ecosystem in normal flow
        # but it should handle gracefully
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


# ---------------------------------------------------------------------------
# aggregate_ecosystem_state — scope guard
# ---------------------------------------------------------------------------

class TestAggregateEcosystemScope:
    def setup_method(self):
        _clear_caches()

    def test_non_us_asset_class_returns_neutral(self):
        for ac in ["forex", "crypto", "precious_metals", "energies", "agricultural_commodities"]:
            state = aggregate_ecosystem_state(ac)
            assert state == NEUTRAL_STATE, f"Expected NEUTRAL for {ac}, got {state}"

    def test_us_stocks_in_scope(self):
        assert "us_stocks" in _ECOSYSTEM_ASSET_CLASSES

    def test_indices_futures_in_scope(self):
        assert "indices_futures" in _ECOSYSTEM_ASSET_CLASSES

    def test_neutral_state_multiplier_is_one(self):
        assert NEUTRAL_STATE.size_multiplier == 1.0
        assert NEUTRAL_STATE.label == "GRAY"
        assert NEUTRAL_STATE.confidence == "LOW"


# ---------------------------------------------------------------------------
# aggregate_ecosystem_state — with mocked data
# ---------------------------------------------------------------------------

class TestAggregateWithMocks:
    def setup_method(self):
        _clear_caches()

    def test_both_sources_available_gives_high_confidence(self):
        with (
            patch.object(eco_mod, "get_vix_level", return_value=14.0),
            patch.object(eco_mod, "compute_sector_momentum", return_value=3.0),
        ):
            state = aggregate_ecosystem_state("us_stocks")
        assert state.confidence == "HIGH"
        assert state.vix_level == 14.0
        assert state.sector_score == 3.0

    def test_only_vix_gives_medium_confidence(self):
        with (
            patch.object(eco_mod, "get_vix_level", return_value=14.0),
            patch.object(eco_mod, "compute_sector_momentum", return_value=None),
        ):
            state = aggregate_ecosystem_state("us_stocks")
        assert state.confidence == "MEDIUM"

    def test_only_sector_gives_medium_confidence(self):
        with (
            patch.object(eco_mod, "get_vix_level", return_value=None),
            patch.object(eco_mod, "compute_sector_momentum", return_value=4.0),
        ):
            state = aggregate_ecosystem_state("us_stocks")
        assert state.confidence == "MEDIUM"

    def test_both_unavailable_returns_neutral(self):
        with (
            patch.object(eco_mod, "get_vix_level", return_value=None),
            patch.object(eco_mod, "compute_sector_momentum", return_value=None),
        ):
            state = aggregate_ecosystem_state("us_stocks")
        assert state == NEUTRAL_STATE

    def test_green_state_from_good_conditions(self):
        with (
            patch.object(eco_mod, "get_vix_level", return_value=15.0),
            patch.object(eco_mod, "compute_sector_momentum", return_value=4.0),
        ):
            state = aggregate_ecosystem_state("indices_futures")
        assert state.label == "GREEN"
        assert state.size_multiplier == 1.5

    def test_bright_green_state(self):
        with (
            patch.object(eco_mod, "get_vix_level", return_value=11.0),
            patch.object(eco_mod, "compute_sector_momentum", return_value=6.0),
        ):
            state = aggregate_ecosystem_state("us_stocks")
        assert state.label == "BRIGHT_GREEN"
        assert state.size_multiplier == 2.0

    def test_dark_red_state(self):
        with (
            patch.object(eco_mod, "get_vix_level", return_value=27.0),
            patch.object(eco_mod, "compute_sector_momentum", return_value=-5.0),
        ):
            state = aggregate_ecosystem_state("us_stocks")
        assert state.label == "DARK_RED"
        assert state.size_multiplier == 0.5

    def test_red_state_from_vix(self):
        with (
            patch.object(eco_mod, "get_vix_level", return_value=21.0),
            patch.object(eco_mod, "compute_sector_momentum", return_value=2.0),
        ):
            state = aggregate_ecosystem_state("us_stocks")
        assert state.label == "RED"
        assert state.size_multiplier == 0.7

    def test_returns_ecosystem_state_namedtuple(self):
        with (
            patch.object(eco_mod, "get_vix_level", return_value=15.0),
            patch.object(eco_mod, "compute_sector_momentum", return_value=2.0),
        ):
            state = aggregate_ecosystem_state("us_stocks")
        assert isinstance(state, EcosystemState)
        assert hasattr(state, "label")
        assert hasattr(state, "size_multiplier")
        assert hasattr(state, "vix_level")
        assert hasattr(state, "sector_score")
        assert hasattr(state, "confidence")


# ---------------------------------------------------------------------------
# EcosystemState NamedTuple
# ---------------------------------------------------------------------------

class TestEcosystemState:
    def test_fields(self):
        s = EcosystemState(
            label="GREEN",
            size_multiplier=1.5,
            vix_level=14.5,
            sector_score=3.0,
            confidence="HIGH",
        )
        assert s.label == "GREEN"
        assert s.size_multiplier == 1.5
        assert s.vix_level == 14.5
        assert s.sector_score == 3.0
        assert s.confidence == "HIGH"

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
