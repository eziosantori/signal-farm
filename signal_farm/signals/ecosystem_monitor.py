"""
Ecosystem Monitor — macro context for US-correlated assets.

Computes a combined ecosystem state from up to three sources:

  1. VIX level          — fear gauge, proxy for market risk appetite
  2. Sector ETF momentum — 7 ETFs covering key US equity sectors
  3. NAS100 breadth      — % of top-100 NASDAQ stocks in buy regime (SMA slope > 0)

The EcosystemState.size_multiplier is intended to scale position size
via calc_position_size(). It does NOT affect signal_score, which remains
a pure technical measure of the individual setup quality.

Scope by asset class:
  us_stocks, indices_futures → VIX + sector ETF + NAS100 alignment (full 0.5×–2.0×)
  crypto                     → NAS100 alignment only (same 0.5×–2.0× scale)
  forex, precious_metals, …  → NEUTRAL_STATE (1.0×) — applying a NASDAQ-ecosystem
                               multiplier to EURUSD or XAUUSD would introduce noise.

Cache policy:
  VIX data            : 5-minute TTL (intraday changes matter)
  Sector ETFs         : 1-hour TTL  (slower-moving)
  NAS100 breadth      : 1-hour TTL  (batch fetch is expensive)

Multiplier scale (EcosystemState.label → size_multiplier):
  DARK_RED     0.5×
  RED          0.7×
  GRAY         1.0×   neutral baseline
  GREEN        1.5×
  BRIGHT_GREEN 2.0×
"""
from __future__ import annotations

import logging
import os
import time
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scope guards
# ---------------------------------------------------------------------------

# Full ecosystem (VIX + sector + NAS100)
_ECOSYSTEM_ASSET_CLASSES = {"us_stocks", "indices_futures"}

# NAS100 alignment only (no VIX / sector)
_NAS100_ONLY_ASSET_CLASSES = {"crypto"}

# All asset classes that receive any ecosystem signal
_ALL_ECOSYSTEM_CLASSES = _ECOSYSTEM_ASSET_CLASSES | _NAS100_ONLY_ASSET_CLASSES

# ---------------------------------------------------------------------------
# Sector ETF configuration
# ---------------------------------------------------------------------------

SECTOR_ETFS = ["XSD", "XLK", "IBB", "XRT", "SH", "TLT", "XLU"]
_RISK_OFF_ETFS = {"SH", "TLT", "XLU"}

# ---------------------------------------------------------------------------
# In-process caches (reset when process restarts)
# ---------------------------------------------------------------------------

_VIX_CACHE: dict    = {}   # {"value": float, "ts": float}
_SECTOR_CACHE: dict = {}   # {"score": float, "ts": float}
_NAS100_CACHE: dict = {}   # {"score": float, "ts": float}

_VIX_TTL    =  5 * 60   # seconds
_SECTOR_TTL = 60 * 60   # seconds
_NAS100_TTL = 60 * 60   # seconds


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

class EcosystemState(NamedTuple):
    """
    Snapshot of macro ecosystem state for one scan cycle.

    Attributes
    ----------
    label           : DARK_RED / RED / GRAY / GREEN / BRIGHT_GREEN
    size_multiplier : multiply against calc_position_size() output (0.5–2.0)
    vix_level       : most recent VIX daily close (None if unavailable / not applicable)
    sector_score    : net ETF bullish score [-7, +7] (None if unavailable / not applicable)
    confidence      : LOW (no data) / MEDIUM (one source) / HIGH (two+ sources)
    nas100_score    : fraction of top-100 NASDAQ stocks in buy regime [0.0, 1.0]
    nas100_alignment: GREEN / GRAY / RED based on nas100_score (None if unavailable)
    """
    label: str
    size_multiplier: float
    vix_level: Optional[float]
    sector_score: Optional[float]
    confidence: str
    nas100_score: Optional[float] = None
    nas100_alignment: Optional[str] = None


# Returned when out-of-scope or all data unavailable
NEUTRAL_STATE = EcosystemState(
    label="GRAY",
    size_multiplier=1.0,
    vix_level=None,
    sector_score=None,
    confidence="LOW",
)


# ---------------------------------------------------------------------------
# VIX fetch
# ---------------------------------------------------------------------------

def get_vix_level() -> Optional[float]:
    """Fetch latest VIX daily close from yfinance with 5-min cache."""
    now = time.time()
    if _VIX_CACHE.get("ts") and (now - _VIX_CACHE["ts"]) < _VIX_TTL:
        return _VIX_CACHE.get("value")

    try:
        import yfinance as yf
        hist = yf.Ticker("^VIX").history(period="5d", interval="1d")
        if hist.empty:
            logger.warning("ecosystem_monitor: VIX history returned empty")
            return None
        value = float(hist["Close"].iloc[-1])
        _VIX_CACHE["value"] = value
        _VIX_CACHE["ts"]    = now
        logger.debug("ecosystem_monitor: VIX fetched = %.2f", value)
        return value
    except Exception as exc:
        logger.warning("ecosystem_monitor: VIX fetch failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Sector ETF momentum
# ---------------------------------------------------------------------------

def compute_sector_momentum() -> Optional[float]:
    """
    Compute net bullish/bearish score across 7 sector ETFs (cached 1h).

    +1 per ETF with close > SMA10; risk-off ETFs (SH/TLT/XLU) are inverted.
    Returns net score in [-7, +7]. None if all ETF fetches fail.
    """
    now = time.time()
    if _SECTOR_CACHE.get("ts") and (now - _SECTOR_CACHE["ts"]) < _SECTOR_TTL:
        return _SECTOR_CACHE.get("score")

    try:
        import yfinance as yf
    except ImportError:
        logger.warning("ecosystem_monitor: yfinance not installed, sector ETF monitoring disabled")
        return None

    net_score   = 0
    valid_count = 0

    for etf in SECTOR_ETFS:
        try:
            hist = yf.Ticker(etf).history(period="30d", interval="1d")
            if hist.empty:
                continue
            close = hist["Close"].dropna()
            if len(close) < 12:
                continue
            last_close = float(close.iloc[-1])
            sma10      = float(close.rolling(10).mean().iloc[-1])
            if sma10 != sma10:   # NaN guard
                continue
            is_bullish = last_close > sma10
            if etf in _RISK_OFF_ETFS:
                is_bullish = not is_bullish
            net_score   += 1 if is_bullish else -1
            valid_count += 1
        except Exception as exc:
            logger.debug("ecosystem_monitor: ETF %s fetch error: %s", etf, exc)
            continue

    if valid_count == 0:
        logger.warning("ecosystem_monitor: no sector ETF data available")
        return None

    score = float(net_score)
    _SECTOR_CACHE["score"] = score
    _SECTOR_CACHE["ts"]    = now
    logger.debug("ecosystem_monitor: sector_score = %+.0f (%d/%d ETFs)", score, valid_count, len(SECTOR_ETFS))
    return score


# ---------------------------------------------------------------------------
# NAS100 breadth (top-100 stock alignment)
# ---------------------------------------------------------------------------

def _load_nas100_tickers() -> list[str]:
    """Load NASDAQ-100 tickers from config/top_100_nasdaq.yaml."""
    try:
        import yaml
        config_path = os.path.join(
            os.path.dirname(__file__), "..", "config", "top_100_nasdaq.yaml"
        )
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("symbols", [])
    except Exception as exc:
        logger.warning("ecosystem_monitor: could not load top_100_nasdaq.yaml: %s", exc)
        return []


def compute_nas100_alignment(tickers: Optional[list[str]] = None) -> Optional[float]:
    """
    Compute fraction of top-100 NASDAQ stocks in buy regime (cached 1h).

    Algorithm per stock:
      1. Fetch 30d of daily closes via yfinance batch download
      2. Compute SMA10 slope: (SMA10[-1] - SMA10[-5]) / SMA10[-5]
      3. Bullish if slope > 0

    Returns score in [0.0, 1.0] (1.0 = all stocks bullish). None if unavailable.
    """
    now = time.time()
    if _NAS100_CACHE.get("ts") and (now - _NAS100_CACHE["ts"]) < _NAS100_TTL:
        return _NAS100_CACHE.get("score")

    if tickers is None:
        tickers = _load_nas100_tickers()
    if not tickers:
        logger.warning("ecosystem_monitor: NAS100 tickers list empty")
        return None

    try:
        import yfinance as yf
        import pandas as pd
        hist = yf.download(
            tickers,
            period="30d",
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
    except Exception as exc:
        logger.warning("ecosystem_monitor: NAS100 batch download failed: %s", exc)
        return None

    if hist is None or hist.empty:
        return None

    bullish     = 0
    valid_count = 0

    for ticker in tickers:
        try:
            # Multi-ticker download → MultiIndex columns (field, ticker)
            if isinstance(hist.columns, pd.MultiIndex):
                close = hist["Close"][ticker].dropna()
            else:
                # Single ticker (shouldn't happen, but handle gracefully)
                close = hist["Close"].dropna()

            if len(close) < 12:
                continue

            sma10 = close.rolling(10).mean().dropna()
            if len(sma10) < 6:
                continue

            slope = (float(sma10.iloc[-1]) - float(sma10.iloc[-5])) / float(sma10.iloc[-5])
            valid_count += 1
            if slope > 0:
                bullish += 1
        except Exception:
            continue

    if valid_count == 0:
        logger.warning("ecosystem_monitor: NAS100 — no valid stocks computed")
        return None

    score = bullish / valid_count
    _NAS100_CACHE["score"] = score
    _NAS100_CACHE["ts"]    = now
    logger.info(
        "ecosystem_monitor: NAS100 breadth = %.1f%% (%d/%d stocks bullish)",
        score * 100, bullish, valid_count,
    )
    return score


def _nas100_label(score: float) -> str:
    """Map NAS100 breadth score [0,1] to alignment label."""
    if score > 0.75:
        return "GREEN"
    if score < 0.35:
        return "RED"
    return "GRAY"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify_ecosystem(
    vix: Optional[float],
    sector: Optional[float],
    nas100_score: Optional[float] = None,
) -> tuple[str, float]:
    """
    Map (VIX, sector_score, nas100_score) → (label, size_multiplier).

    Priority:
      1. Risk-off conditions override everything (DARK_RED / RED)
      2. VIX + sector combined → GREEN / BRIGHT_GREEN if both confirm
      3. NAS100 breadth as tiebreaker in the GRAY zone
    """
    # --- Risk-off first (override any bullish signals) ---
    if (vix is not None and vix > 25) or (sector is not None and sector < -4):
        return "DARK_RED", 0.5

    if (vix is not None and vix > 20) or (sector is not None and sector < -2):
        return "RED", 0.7

    # --- Risk-on (require both positive when both available) ---
    if vix is not None and sector is not None:
        if vix < 13 and sector > 4:
            return "BRIGHT_GREEN", 2.0
        if vix < 18 and sector > 2:
            return "GREEN", 1.5
    elif vix is not None:
        if vix < 13:
            return "GREEN", 1.5
    elif sector is not None:
        if sector > 4:
            return "GREEN", 1.5

    # --- GRAY zone: use NAS100 breadth as tiebreaker ---
    if nas100_score is not None:
        if nas100_score > 0.75:
            return "GREEN", 1.5
        if nas100_score < 0.35:
            return "RED", 0.7

    return "GRAY", 1.0


def _classify_nas100_only(nas100_score: float) -> tuple[str, float]:
    """
    Classification when only NAS100 breadth is available (crypto).

    Uses the same multiplier scale as the full ecosystem.
    Slightly higher threshold for BRIGHT_GREEN (0.85 vs 0.75) because
    crypto volatility is already elevated — conservative amplification.
    """
    if nas100_score > 0.85:
        return "BRIGHT_GREEN", 2.0
    if nas100_score > 0.75:
        return "GREEN", 1.5
    if nas100_score < 0.25:
        return "DARK_RED", 0.5
    if nas100_score < 0.35:
        return "RED", 0.7
    return "GRAY", 1.0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def aggregate_ecosystem_state(asset_class: str) -> EcosystemState:
    """
    Compute EcosystemState for the given asset class.

    Returns NEUTRAL_STATE (1.0×, confidence=LOW) when:
      - asset_class is out of scope (forex, precious_metals, …)
      - all data sources are unavailable

    Data sources used per asset class:
      us_stocks / indices_futures → VIX + sector ETF + NAS100 breadth
      crypto                      → NAS100 breadth only
    """
    if asset_class not in _ALL_ECOSYSTEM_CLASSES:
        return NEUTRAL_STATE

    # --- NAS100 breadth (all correlated asset classes) ---
    nas100_score     = compute_nas100_alignment()
    nas100_alignment = _nas100_label(nas100_score) if nas100_score is not None else None

    # --- Crypto: NAS100 only ---
    if asset_class in _NAS100_ONLY_ASSET_CLASSES:
        if nas100_score is None:
            return NEUTRAL_STATE
        label, multiplier = _classify_nas100_only(nas100_score)
        return EcosystemState(
            label=label,
            size_multiplier=multiplier,
            vix_level=None,
            sector_score=None,
            confidence="MEDIUM",
            nas100_score=nas100_score,
            nas100_alignment=nas100_alignment,
        )

    # --- Full ecosystem: VIX + sector + NAS100 ---
    vix    = get_vix_level()
    sector = compute_sector_momentum()

    if vix is None and sector is None and nas100_score is None:
        logger.warning("ecosystem_monitor: all data sources unavailable, returning NEUTRAL")
        return NEUTRAL_STATE

    sources    = sum(x is not None for x in [vix, sector, nas100_score])
    confidence = "HIGH" if sources >= 2 else "MEDIUM"

    label, multiplier = _classify_ecosystem(vix, sector, nas100_score)

    return EcosystemState(
        label=label,
        size_multiplier=multiplier,
        vix_level=vix,
        sector_score=sector,
        confidence=confidence,
        nas100_score=nas100_score,
        nas100_alignment=nas100_alignment,
    )
