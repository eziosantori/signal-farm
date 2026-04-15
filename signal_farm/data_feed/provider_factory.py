"""
Provider factory: selects the right DataProvider for a given ticker.

Priority order:
  1. AlpacaProvider    — if ALPACA_API_KEY is set AND ticker is a US stock
  2. CcxtProvider      — if ticker has a ccxt symbol (crypto, no API key needed)
  3. OandaProvider     — if OANDA_API_KEY is set AND ticker is in the Oanda symbol map
  4. DukascopyProvider — if the ticker has a valid Dukascopy feed ID
  5. YFinanceProvider  — fallback
"""
from __future__ import annotations

import logging
import os

import yaml

from data_feed.provider import DataProvider
from data_feed.yfinance_provider import YFinanceProvider

logger = logging.getLogger(__name__)

_INSTRUMENTS_YAML = os.path.join(
    os.path.dirname(__file__), "..", "config", "instruments.yaml"
)

# Oanda supports these asset classes (all CFD instruments)
_OANDA_ASSET_CLASSES = {
    "forex", "indices_futures", "precious_metals", "energies", "agricultural_commodities",
}


def _load_instrument_map(yaml_path: str) -> tuple[dict, set, set, set]:
    """
    Return:
      feed_map    : {symbol: dukascopy_feed_id | None}
      us_stocks   : set of symbols with asset_class='us_stocks'
      crypto_syms : set of symbols that have a ccxt field defined
      oanda_syms  : set of symbols supported by OandaProvider (CFD instruments)
    """
    from data_feed.oanda_provider import _SYMBOL_MAP as _oanda_map

    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data.pop("timeframes", None)

    feed_map: dict[str, str | None] = {}
    us_stocks: set[str] = set()
    crypto_syms: set[str] = set()
    oanda_syms: set[str] = set()

    for section in data.values():
        for symbol, meta in section.items():
            feed = meta.get("feed")
            feed_map[symbol] = feed if (feed and feed != "~") else None
            if meta.get("asset_class") == "us_stocks":
                us_stocks.add(symbol)
            if meta.get("ccxt"):
                crypto_syms.add(symbol)
            if symbol in _oanda_map:
                oanda_syms.add(symbol)

    return feed_map, us_stocks, crypto_syms, oanda_syms


_feed_map: dict[str, str | None] | None = None
_us_stocks: set[str] | None = None
_crypto_syms: set[str] | None = None
_oanda_syms: set[str] | None = None


def _get_maps() -> tuple[dict, set, set, set]:
    global _feed_map, _us_stocks, _crypto_syms, _oanda_syms
    if _feed_map is None:
        _feed_map, _us_stocks, _crypto_syms, _oanda_syms = _load_instrument_map(_INSTRUMENTS_YAML)
    return _feed_map, _us_stocks, _crypto_syms, _oanda_syms


def _normalize_symbol(ticker: str) -> str:
    """Normalise yfinance-style tickers to canonical form."""
    return (
        ticker.upper()
        .replace("-USD", "USD")
        .replace("-EUR", "EUR")
        .replace("=X", "")
    )


def _has_alpaca_creds() -> bool:
    return bool(
        os.environ.get("ALPACA_API_KEY") and
        os.environ.get("ALPACA_SECRET_KEY")
    )


def _has_oanda_creds() -> bool:
    return bool(os.environ.get("OANDA_API_KEY"))


def get_provider(ticker: str) -> DataProvider:
    """
    Return the best DataProvider for `ticker`.

    1. AlpacaProvider   → US stocks when ALPACA_API_KEY/SECRET are set
    2. CcxtProvider     → crypto instruments with a ccxt symbol (no auth needed)
    3. OandaProvider    → CFD instruments (forex/indices/metals/energies) when OANDA_API_KEY set
    4. DukascopyProvider → instruments with a valid Dukascopy feed
    5. YFinanceProvider  → fallback
    """
    symbol = _normalize_symbol(ticker)
    feed_map, us_stocks, crypto_syms, oanda_syms = _get_maps()

    # 1. Alpaca for US stocks
    if symbol in us_stocks and _has_alpaca_creds():
        from data_feed.alpaca_provider import AlpacaProvider
        logger.debug("%s → AlpacaProvider", ticker)
        return AlpacaProvider()

    # 2. ccxt for crypto (public endpoints, no key required)
    if symbol in crypto_syms:
        from data_feed.ccxt_provider import CcxtProvider
        logger.debug("%s → CcxtProvider (Binance)", ticker)
        return CcxtProvider()

    # 3. Oanda for CFD instruments (forex, indices, metals, energies)
    if symbol in oanda_syms and _has_oanda_creds():
        from data_feed.oanda_provider import OandaProvider
        logger.debug("%s → OandaProvider", ticker)
        return OandaProvider()

    # 4. Dukascopy for instruments with a valid feed
    if feed_map.get(symbol) is not None:
        from data_feed.dukascopy_provider import DukascopyProvider
        logger.debug("%s → DukascopyProvider (feed: %s)", ticker, feed_map[symbol])
        return DukascopyProvider()

    # 5. Fallback
    logger.debug("%s → YFinanceProvider", ticker)
    return YFinanceProvider()
