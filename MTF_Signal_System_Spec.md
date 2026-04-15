# MTF Convergence Signal System — Specification Document (v2)

## Overview

Multi-Timeframe (MTF) trend-following signal system that uses SMA convergence to identify
high-probability pullback entries in trending markets. The system adapts its timeframe
hierarchy to the market structure of each asset class. Three strategy variants share
the same directional framework but differ in entry trigger logic.

Target assets: US Stocks, Indices/Futures, Agricultural Commodities, Precious Metals, Crypto.

---

## Architecture

### Design Principle: Adapt to Market Structure

Different asset classes have different trading sessions. A fixed 3-TF hierarchy doesn't
work everywhere. The system uses **asset profiles** that define the right timeframe
combination for each market:

- **2-Level profiles** (Director + Executor): for markets with short sessions where
  intermediate TFs produce dirty data (e.g., US stocks with 6.5h sessions)
- **3-Level profiles** (Director + Filter + Executor): for markets with long/continuous
  sessions where all TFs produce clean data (e.g., crypto 24/7, futures ~23h)

### Asset Profiles

```python
ASSET_PROFILES = {

    "us_stocks": {
        # 6.5h session (9:30-16:00 ET) → 4H candles are broken/gapped
        # Use 30min executor with dual SMA instead of 4H filter
        "director_tf": "1d",
        "executor_tf": "30m",
        "filter_tf": None,          # no intermediate filter
        "sma_fast": 10,             # pullback target on executor TF
        "sma_slow": 50,             # pullback depth limit (replaces 4H filter)
        "candles_per_day": 13,      # 6.5h / 30min
        "data_provider": "yfinance",
    },

    "indices_futures": {
        # ~23h session on Globex (ES, NQ, YM) → all TFs clean
        "director_tf": "1d",
        "filter_tf": "4h",
        "executor_tf": "1h",
        "sma_fast": 10,
        "sma_slow": 40,             # proxy for SMA 10 on 4H
        "candles_per_day": 23,
        "data_provider": "yfinance",
    },

    "agricultural_commodities": {
        # ~17h electronic session on CME Globex (ZC, ZW, ZS)
        # 4H works but produces ~4 candles/day → usable as filter
        "director_tf": "1d",
        "filter_tf": "4h",
        "executor_tf": "1h",
        "sma_fast": 10,
        "sma_slow": 40,
        "candles_per_day": 17,
        "data_provider": "yfinance",
    },

    "precious_metals": {
        # ~23h session on COMEX Globex (GC, SI) → all TFs clean
        "director_tf": "1d",
        "filter_tf": "4h",
        "executor_tf": "1h",
        "sma_fast": 10,
        "sma_slow": 40,
        "candles_per_day": 23,
        "data_provider": "yfinance",
    },

    "crypto": {
        # 24/7 → all TFs perfectly clean, no gaps, no incomplete candles
        "director_tf": "1d",
        "filter_tf": "4h",
        "executor_tf": "1h",
        "sma_fast": 10,
        "sma_slow": 40,
        "candles_per_day": 24,
        "data_provider": "yfinance",   # or "ccxt" for more exchanges
    },
}
```

### Timeframe Roles

| Role | What it does | Check frequency |
|------|-------------|-----------------|
| **Director (Daily)** | Determines IF we trade and in which DIRECTION. If not trending, no trades. | Once per day |
| **Filter (4H)** | Confirms trend is alive, pullback hasn't become reversal. *Only used in 3-level profiles.* | Each 4H candle |
| **Executor (1H/30m)** | Waits for pullback and generates entry signal. Only TF where entries happen. | Each executor candle |

### How the 2-Level Profile Compensates for Missing Filter

In 2-level profiles (us_stocks), the intermediate filter role is replaced by a **slow SMA
on the executor TF**:

- SMA 50 on 30min ≈ ~2 trading days → acts as "pullback depth limit"
- If price breaks below SMA 50 (30min), the pullback is too deep → no entry
- This is functionally equivalent to the 4H filter but calculated on clean data

The "pullback corridor" is the zone between SMA 10 (fast) and SMA 50 (slow) on the
executor TF. Healthy pullbacks stay inside this corridor.

---

## Indicators

### Per-Profile Indicator Stack

**All profiles — Director (Daily):**
- SMA 10 Daily
- ROC 10 Daily (Rate of Change for momentum)

**3-Level profiles — Filter (4H):**
- SMA 10 (4H), monitored via SMA `sma_slow` on executor TF as proxy

**All profiles — Executor (1H or 30m):**
- SMA `sma_fast` (10) — pullback target
- SMA `sma_slow` (40 or 50) — pullback depth limit
- ATR 14 — for stop loss and position sizing
- Keltner Channel: EMA 20, ATR 10, multiplier 1.5 (for Variants B and C)

---

## Signal Logic

### Shared Conditions (all three variants)

#### LONG Setup

**Daily Direction (check once per day):**
1. SMA 10 Daily slope > 0 (slope = SMA[current] - SMA[previous])
2. Close Daily > SMA 10 Daily
3. ROC 10 Daily > 0

**Depth Filter (check each executor candle):**
4. Price on executor TF > SMA slow (sma_slow param)
   - 3-level: this is the proxy for SMA 10 on 4H
   - 2-level: this is SMA 50 on 30min, the pullback depth limit
5. SMA slow slope >= 0

*Additionally for 3-level profiles:*
6. SMA 10 (4H) slope >= 0 (verified via proxy or true MTF data)

#### SHORT Setup
Mirror of LONG: all slopes negative, close below SMAs, ROC < 0.

---

### Variant A — "SMA Pullback"

**Entry Trigger (executor TF):**
- Price has touched or slightly penetrated SMA fast (10) during pullback
  - Defined as: Low of any candle <= SMA fast + tolerance within last N candles
  - N = PULLBACK_LOOKBACK (default 5 for 1H, 8 for 30min)
  - tolerance = 0.1% of price (avoids missing near-touches)
- Current candle closes ABOVE SMA fast
- SMA fast slope >= 0 (not turning negative)

**Character:** Early entry, captures more movement, more noise.
Best for strongly trending markets (indices in trend, large-cap stocks).

**Stop Loss:** Max of (swing low of pullback, entry - 1.5 × ATR(14))
**Take Profit:** 2:1 R:R minimum, or trailing stop at 2 × ATR below price

---

### Variant B — "Keltner Breakout"

**Entry Trigger (executor TF):**
- Price has pulled back into or below the Keltner Channel middle line (EMA 20)
  - Defined as: Low of any candle <= Keltner EMA 20 within last N candles
  - N = KELTNER_LOOKBACK (default 8 for 1H, 12 for 30min)
- Current candle closes ABOVE the Keltner upper band
- This signals volatility expansion in the trend direction after consolidation

**Keltner Parameters:**
- Center: EMA 20 (executor TF)
- ATR period: 10
- Multiplier: 1.5

**Character:** Later entry, higher conviction. Waits for volatility expansion.
Best for markets with structured pullbacks (commodities, precious metals, crypto).

**Stop Loss:** Keltner middle line (EMA 20) or swing low of consolidation, whichever is lower
**Take Profit:** 2:1 R:R minimum, or trailing stop at 2 × ATR

---

### Variant C — "Hybrid" (SMA Touch + Keltner Breakout)

**Entry Trigger (executor TF):**
- Price has touched SMA fast (10) during pullback (same as Variant A touch condition)
- AND THEN current candle closes ABOVE Keltner upper band (same as Variant B breakout)
- Double filter: confirms both mean-reversion touch AND momentum resumption

**Character:** Most selective, fewest signals, highest expected win rate.
Best as "quality over quantity" approach across all asset classes.

**Stop Loss:** Below the consolidation low (lowest low between SMA touch and Keltner breakout)
**Take Profit:** 2.5:1 R:R or trailing stop

---

## Risk Management

- **Position Sizing:** Fixed fractional — risk 1-2% of equity per trade
- **Stop Loss:** As defined per variant (ATR-based or structural)
- **Max concurrent positions:** Configurable (suggest 3-5)
- **No re-entry:** If stopped out, wait for a fresh pullback cycle (new touch + new trigger)
- **Exit on structure break:** If Daily SMA 10 slope turns negative (for longs), close all longs
- **Correlation filter:** Avoid overexposure — max 2 positions in same sector/asset class

---

## Watchlists

```python
WATCHLISTS = {

    "us_stocks": {
        "profile": "us_stocks",
        "tickers": [
            # Large-cap tech
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
            # Financials
            "JPM", "BAC", "GS",
            # Healthcare
            "JNJ", "UNH", "PFE",
            # Consumer
            "WMT", "KO", "MCD",
            # Industrial
            "CAT", "DE", "BA",
        ],
    },

    "indices_futures": {
        "profile": "indices_futures",
        "tickers": [
            # US Index ETFs (use these if no futures data feed)
            "SPY", "QQQ", "DIA", "IWM",
            # US Futures (yfinance symbols)
            "ES=F", "NQ=F", "YM=F", "RTY=F",
            # European
            "EWI",      # Italy (FTSE MIB proxy)
            "EWG",      # Germany (DAX proxy)
            "EWU",      # UK (FTSE proxy)
        ],
    },

    "agricultural_commodities": {
        "profile": "agricultural_commodities",
        "tickers": [
            # Futures (yfinance symbols)
            "ZC=F",     # Corn
            "ZW=F",     # Wheat
            "ZS=F",     # Soybeans
            "KC=F",     # Coffee
            "CT=F",     # Cotton
            "SB=F",     # Sugar
            "CC=F",     # Cocoa
            # ETFs (alternative if futures data is unreliable)
            "DBA",      # Agriculture broad ETF
            "CORN",     # Corn ETF
            "WEAT",     # Wheat ETF
            "SOYB",     # Soybeans ETF
            "JO",       # Coffee ETN
        ],
    },

    "precious_metals": {
        "profile": "precious_metals",
        "tickers": [
            # Futures
            "GC=F",     # Gold
            "SI=F",     # Silver
            "PL=F",     # Platinum
            "PA=F",     # Palladium
            # ETFs
            "GLD",      # Gold ETF
            "SLV",      # Silver ETF
            "PPLT",     # Platinum ETF
        ],
    },

    "crypto": {
        "profile": "crypto",
        "tickers": [
            # yfinance symbols
            "BTC-USD",  # Bitcoin
            "ETH-USD",  # Ethereum
            "SOL-USD",  # Solana
            "BNB-USD",  # Binance Coin
            "ADA-USD",  # Cardano
            "AVAX-USD", # Avalanche
            "LINK-USD", # Chainlink
            "DOT-USD",  # Polkadot
        ],
    },
}
```

---

## Implementation Plan — Python

### Tech Stack

```
- Python 3.10+
- pandas / numpy — data manipulation
- yfinance — primary data feed (stocks, indices, commodities, metals, crypto)
- ccxt — alternative/supplementary data feed for crypto (more exchanges, cleaner data)
- pandas_ta — technical indicators (lighter than ta-lib, no C dependency)
- matplotlib / plotly — charting and visualization
- schedule — for periodic scanning (cron alternative)
- Optional: vectorbt for backtesting framework
```

### Project Structure

```
mtf_signal_system/
├── config.py              # Asset profiles, parameters, watchlists
├── data_feed.py           # Data fetching, MTF resampling, provider abstraction
├── indicators.py          # SMA, ROC, ATR, Keltner calculations
├── signals.py             # Signal logic for all 3 variants (profile-aware)
├── risk_manager.py        # Position sizing, stop/TP calculation
├── scanner.py             # Scan watchlists, output signals table
├── backtest.py            # Simple vectorized backtest engine
├── visualizer.py          # Chart signals on price with indicators
├── main.py                # CLI entry point
├── requirements.txt
└── README.md
```

### Module Details

#### config.py
Contains ASSET_PROFILES and WATCHLISTS as defined above, plus:

```python
# Indicator defaults (can be overridden per profile)
DEFAULTS = {
    "sma_fast": 10,
    "sma_slow": 40,
    "roc_period": 10,
    "atr_period": 14,
    "keltner_ema": 20,
    "keltner_atr": 10,
    "keltner_mult": 1.5,
}

# Signal params (adjusted per executor TF)
SIGNAL_PARAMS = {
    "30m": {
        "pullback_lookback": 8,     # ~4h of candles
        "keltner_lookback": 12,     # ~6h of candles
    },
    "1h": {
        "pullback_lookback": 5,     # ~5h of candles
        "keltner_lookback": 8,      # ~8h of candles
    },
}

# Risk params
RISK_PER_TRADE = 0.01       # 1% of equity
ATR_STOP_MULT = 1.5
MIN_RR_RATIO = 2.0
MAX_CONCURRENT_POSITIONS = 5
MAX_PER_ASSET_CLASS = 2     # correlation filter
```

#### data_feed.py
- **Provider abstraction:** `DataProvider` base class with `YFinanceProvider` and
  `CCXTProvider` implementations. Profile specifies which provider to use.
- Fetch OHLCV data at executor TF resolution
- Resample to higher TFs (or fetch directly if provider supports it)
- Handle market hours, gaps, incomplete candles per profile
- **Critical:** for 30min on US stocks, filter out pre/post market data
- Cache data to reduce API calls during development/backtesting

```python
class DataFeed:
    def get_data(self, ticker: str, profile: dict) -> dict:
        """Returns {"director": df_daily, "filter": df_4h_or_None, "executor": df_exec}"""
        ...
```

#### indicators.py
- `calc_sma(series, period)` → SMA values
- `calc_sma_slope(sma_series)` → slope as SMA[i] - SMA[i-1]
- `calc_roc(series, period)` → Rate of Change
- `calc_atr(high, low, close, period)` → Average True Range
- `calc_keltner(high, low, close, ema_period, atr_period, multiplier)` → upper, middle, lower
- All functions are pure: take Series/DataFrame in, return Series out

#### signals.py
Profile-aware signal generation:

```python
def generate_signals(executor_df, director_df, filter_df, profile, variant):
    """
    Main entry point. Routes to variant-specific logic.
    filter_df can be None for 2-level profiles.
    Returns executor_df with added columns: signal, entry_price, stop, target
    """
    # 1. Check director conditions
    direction = check_director(director_df)
    if direction == "NEUTRAL":
        return executor_df  # no signals

    # 2. Check depth filter (works for both 2-level and 3-level)
    depth_ok = check_depth_filter(executor_df, profile)

    # 3. If 3-level, also check intermediate filter
    if filter_df is not None:
        filter_ok = check_intermediate_filter(filter_df, direction)
        depth_ok = depth_ok & filter_ok

    # 4. Generate variant-specific entry triggers
    if variant == "A":
        return variant_a_signals(executor_df, direction, depth_ok, profile)
    elif variant == "B":
        return variant_b_signals(executor_df, direction, depth_ok, profile)
    elif variant == "C":
        return variant_c_signals(executor_df, direction, depth_ok, profile)
```

#### scanner.py
- Iterate all configured watchlists
- For each asset: resolve profile → fetch data → compute signals for all 3 variants
- Output: consolidated table sorted by signal strength/recency
- Format: `{asset, asset_class, variant, direction, entry_price, stop, target, rr_ratio, timestamp}`
- Support filtering: `--class us_stocks`, `--variant B`, `--direction LONG`

#### backtest.py
- Vectorized backtest per asset per variant
- For each signal: simulate entry at next candle open, check stop/target hit
- Track: win rate, avg R:R, profit factor, max drawdown, total return, Sharpe
- Compare all 3 variants side by side per asset class
- **Note:** yfinance intraday limited to 60 days. For longer backtests, use Daily-only
  validation of director conditions, or switch to a provider with deeper history.

#### visualizer.py
- Plot price chart with:
  - SMA fast + SMA slow on executor TF
  - Keltner Channel bands (if Variant B or C)
  - Entry/exit markers with arrows
  - Pullback zones highlighted (shaded area between SMA fast touch and entry)
  - Daily SMA 10 overlaid as reference line
- Support for multi-panel: price + ROC + ATR subplots

---

## Monitoring & Scheduling

### Scan Frequency per Profile

| Profile | Executor TF | Scan every | Daily scans |
|---------|-------------|------------|-------------|
| us_stocks | 30min | 30 min (market hours only) | ~13 |
| indices_futures | 1h | 1 hour | ~23 |
| agricultural_commodities | 1h | 1 hour | ~17 |
| precious_metals | 1h | 1 hour | ~23 |
| crypto | 1h | 1 hour | 24 |

### Practical Setup

```bash
# Option 1: crontab (separate schedules per profile)
# US stocks: every 30min during market hours (15:30-22:00 CET for Italian timezone)
*/30 15-22 * * 1-5  python main.py scan --class us_stocks

# Everything else: every hour
0 * * * *  python main.py scan --class indices_futures,precious_metals,crypto
0 * * * 1-5  python main.py scan --class agricultural_commodities

# Option 2: built-in scheduler (single process)
python main.py monitor --all
```

### yfinance Constraints
- Intraday data: last 60 days only (sufficient for live monitoring)
- Rate limit: ~360 requests/hour (our max scan cycle: ~80 tickers × 1 call ≈ 80 req)
- Data delay: ~15 minutes for US equities (acceptable for candle-close strategies)
- Reliability: occasional outages — implement retry logic with exponential backoff
- For crypto, ccxt is more reliable and has no intraday history limit

---

## Usage Examples

```bash
# Scan all watchlists for current signals
python main.py scan

# Scan only US stocks
python main.py scan --class us_stocks

# Scan specific ticker
python main.py scan --ticker AAPL

# Scan only Variant C signals (most selective)
python main.py scan --variant C

# Backtest variant A on SPY
python main.py backtest --ticker SPY --variant A --period 60d

# Compare all variants on gold
python main.py compare --ticker GC=F --period 60d

# Generate chart with signals
python main.py chart --ticker AAPL --variant C --period 30d

# Start continuous monitor (scans on schedule, prints/notifies on new signals)
python main.py monitor --all

# Monitor only crypto
python main.py monitor --class crypto
```

---

## Notes for Implementation

1. **MTF data alignment is critical** — when checking Daily conditions on executor TF,
   use the LAST COMPLETED Daily candle, never the forming one. Same for 4H filter.

2. **SMA proxy vs true MTF** — SMA slow on executor TF is a proxy for the intermediate
   filter. For 3-level profiles, implement proxy first (simpler), add true MTF merge as
   enhancement. For 2-level profiles, the proxy IS the design.

3. **Slope threshold** — pure slope > 0 generates noise on flat SMAs. Use minimum:
   `slope > 0.0001 × price`. Make configurable in DEFAULTS.

4. **Pullback detection tolerance** — define "touch" as `low <= SMA + (0.001 × price)`.
   Avoids missing entries where price comes very close but doesn't mathematically touch.

5. **No lookahead bias** — all signals must use only data available at signal time.
   Use `.shift(1)` on higher TF signals when merging down.

6. **Incomplete candle handling** — for us_stocks 30min, the last candle before close
   is complete. No 4H = no broken candles. For futures/crypto, check that the current
   candle is complete before generating signals.

7. **Start simple** — Phase 1: scanner with signal table output. Phase 2: backtest.
   Phase 3: visualization. Phase 4: monitoring with notifications.

8. **Data caching** — cache fetched data locally (pickle or SQLite) to avoid redundant
   API calls during development. Invalidate cache based on candle completion time.

9. **Timezone handling** — store all data in UTC internally. Convert for display only.
   US market hours: 13:30-20:00 UTC. Use profile metadata for market hour filtering.

10. **Provider fallback** — if yfinance fails for a ticker, log the error and continue
    with remaining tickers. Never let one failure block the entire scan cycle.