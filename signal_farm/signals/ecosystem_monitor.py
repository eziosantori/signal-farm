"""
Ecosystem Monitor — US equity macro context for NAS100 / US stocks.

Computes a combined ecosystem state from:
  - VIX level (fear gauge, proxy for market risk appetite)
  - Sector ETF momentum (7 ETFs covering key US equity sectors)

The EcosystemState.size_multiplier is intended to scale position size
via calc_position_size() — it does NOT affect signal_score, which
remains a pure technical measure of the individual setup quality.

Scope: us_stocks and indices_futures only. All other asset classes receive
       NEUTRAL_STATE (1.0×) — applying a NASDAQ-ecosystem multiplier to
       XAUUSD or EURUSD would introduce noise.

Cache policy:
  VIX data    : 5-minute TTL (intraday changes matter)
  Sector ETFs : 1-hour TTL  (slower-moving, expensive to fetch repeatedly)

Multiplier scale (EcosystemState.label → size_multiplier):
  DARK_RED     0.5×   VIX > 25  OR  sector_score < -4
  RED          0.7×   VIX > 20  OR  sector_score < -2
  GRAY         1.0×   neutral baseline
  GREEN        1.5×   VIX < 18  AND sector_score > 2
  BRIGHT_GREEN 2.0×   VIX < 13  AND sector_score > 4
"""
from __future__ import annotations

import logging
import time
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scope guard
# ---------------------------------------------------------------------------

_ECOSYSTEM_ASSET_CLASSES = {"us_stocks", "indices_futures"}

# ---------------------------------------------------------------------------
# Sector ETF configuration
# 7 proxies for US equity ecosystem health.
# SH (inverse S&P500), TLT (long-duration bonds), XLU (utilities) are
# risk-OFF instruments — their rising prices signal risk aversion.
# ---------------------------------------------------------------------------

SECTOR_ETFS = ["XSD", "XLK", "IBB", "XRT", "SH", "TLT", "XLU"]
_RISK_OFF_ETFS = {"SH", "TLT", "XLU"}

# ---------------------------------------------------------------------------
# In-process caches (reset when process restarts)
# ---------------------------------------------------------------------------

_VIX_CACHE: dict = {}       # {"value": float, "ts": float}
_SECTOR_CACHE: dict = {}    # {"score": float, "ts": float}

_VIX_TTL    = 5 * 60    # seconds
_SECTOR_TTL = 60 * 60   # seconds


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

class EcosystemState(NamedTuple):
    """
    Snapshot of US equity macro ecosystem for one scan cycle.

    Attributes
    ----------
    label           : DARK_RED / RED / GRAY / GREEN / BRIGHT_GREEN
    size_multiplier : multiply against calc_position_size() output (0.5–2.0)
    vix_level       : most recent VIX daily close (None if unavailable)
    sector_score    : net ETF bullish score, range [-7, +7] (None if unavailable)
    confidence      : LOW (no data) / MEDIUM (one source) / HIGH (both sources)
    """
    label: str
    size_multiplier: float
    vix_level: Optional[float]
    sector_score: Optional[float]
    confidence: str


# Returned when out-of-scope or data unavailable — never amplifies or reduces size
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
    """
    Fetch the latest VIX daily close from yfinance with 5-min cache.
    Returns None on fetch failure or if yfinance is unavailable.
    """
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

    Algorithm per ETF:
      1. Fetch last 30 days of daily closes
      2. Compute SMA10
      3. +1 if close > SMA10 (bullish), -1 otherwise (bearish)
      4. Risk-off ETFs (SH, TLT, XLU) are inverted: rising = bearish for risk

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
                is_bullish = not is_bullish  # invert: rising risk-off = bearish ecosystem
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
# Classification
# ---------------------------------------------------------------------------

def _classify_ecosystem(
    vix: Optional[float],
    sector: Optional[float],
) -> tuple[str, float]:
    """
    Map (VIX level, sector_score) → (label, size_multiplier).

    Conservative priority: risk-off conditions override risk-on signals.
    Either condition alone is sufficient to trigger RED/DARK_RED.
    Both conditions must be positive for GREEN/BRIGHT_GREEN when both available.
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
        # VIX only — require clear low-volatility signal
        if vix < 13:
            return "GREEN", 1.5
    elif sector is not None:
        # Sector only — require strong bullish consensus
        if sector > 4:
            return "GREEN", 1.5

    return "GRAY", 1.0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def aggregate_ecosystem_state(asset_class: str) -> EcosystemState:
    """
    Compute combined EcosystemState for the given asset class.

    Returns NEUTRAL_STATE (1.0×, confidence=LOW) when:
      - asset_class is not us_stocks or indices_futures
      - both VIX and sector data are unavailable

    Degrades gracefully to MEDIUM confidence when only one source is available.
    """
    if asset_class not in _ECOSYSTEM_ASSET_CLASSES:
        return NEUTRAL_STATE

    vix    = get_vix_level()
    sector = compute_sector_momentum()

    if vix is None and sector is None:
        logger.warning("ecosystem_monitor: all data sources unavailable, returning NEUTRAL")
        return NEUTRAL_STATE

    confidence = "HIGH" if (vix is not None and sector is not None) else "MEDIUM"
    label, multiplier = _classify_ecosystem(vix, sector)

    return EcosystemState(
        label=label,
        size_multiplier=multiplier,
        vix_level=vix,
        sector_score=sector,
        confidence=confidence,
    )
