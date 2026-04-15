# MTF Signal Farm

Multi-Timeframe trend-following signal system with three entry variants, focused on backtesting.

## Installation

```bash
pip install -r requirements.txt
```

## Quickstart

```bash
# Navigate into the package directory
cd signal_farm

# Backtest Variant A on AAPL (US stocks, 30min executor, daily director)
python main.py backtest --asset us_stocks --variant A --ticker AAPL

# Backtest Variant B on BTC (crypto, 1H executor, 4H filter, daily director)
python main.py backtest --asset crypto --variant B --ticker BTC-USD

# Compare all 3 variants on a ticker
python main.py compare --asset us_stocks --ticker AAPL

# Machine-readable JSON output (for LLM/automation consumption)
python main.py backtest --asset us_stocks --variant A --ticker AAPL --output json

# Generate interactive HTML chart
python main.py chart --asset us_stocks --variant A --ticker AAPL

# Custom starting equity and risk per trade
python main.py backtest --asset precious_metals --variant C --ticker GLD --equity 50000 --risk-pct 0.02
```

## CLI Reference

```
main.py backtest --asset ASSET --variant {A,B,C} --ticker TICKER
                 [--equity FLOAT]     Starting equity (default: 100000)
                 [--risk-pct FLOAT]   Risk per trade as fraction (default: 0.01)
                 [--output {table,json,csv}]
                 [--verbose]

main.py compare  --asset ASSET --ticker TICKER
                 [--equity FLOAT] [--risk-pct FLOAT]

main.py chart    --asset ASSET --variant {A,B,C} --ticker TICKER
```

### Asset Classes

| `--asset`                | Director | Filter | Executor |
|--------------------------|----------|--------|----------|
| `us_stocks`              | 1D       | —      | 30min    |
| `indices_futures`        | 1D       | 4H     | 1H       |
| `agricultural_commodities` | 1D     | 4H     | 1H       |
| `precious_metals`        | 1D       | 4H     | 1H       |
| `crypto`                 | 1D       | 4H     | 1H       |

## Signal Variants

| Variant | Logic | Entry Trigger |
|---------|-------|--------------|
| **A** | SMA Pullback | Price touches SMA fast, then recovers above it |
| **B** | Keltner Breakout | Price touches Keltner mid, then breaks above Keltner upper |
| **C** | Hybrid | Both A touch AND B breakout on same bar (most selective) |

## Architecture

```
signal_farm/
├── config/          YAML configs (profiles, watchlists, defaults)
├── data_feed/       yfinance provider + MTF alignment (lookahead-safe)
├── indicators/      Pure functions: SMA, ROC, ATR, Keltner
├── signals/         Director, depth filter, Variants A/B/C, orchestrator engine
├── risk_manager/    Position sizing, stop/target, correlation filter
├── backtest/        Chronological backtester, performance metrics
├── visualizer/      Plotly HTML charts (falls back to matplotlib)
├── docs/            Data contract, signal spec notes, backtest methodology
└── main.py          CLI entry point
```

## Live Scanner & Telegram Alerts

### Setup

1. Copy `.env.example` to `.env` and fill in your credentials.

2. **Telegram bot** (one-time setup):
   - Open `@BotFather` on Telegram → `/newbot` → follow prompts → copy the token
   - Search for your bot by name in Telegram → press **Start**
   - Get your chat ID — visit in a browser (replace `<TOKEN>` with your token):
     ```
     https://api.telegram.org/bot<TOKEN>/getUpdates
     ```
     Find `"chat":{"id": 123456789}` in the JSON response.
     If the result is empty, send any message to the bot first, then reload.
   - Add both values to `.env`:
     ```env
     TELEGRAM_BOT_TOKEN=<token>
     TELEGRAM_CHAT_ID=<chat_id>
     ```

3. **Preview a notification** (no message sent):
   ```bash
   cd signal_farm
   python main.py scan --no-skip-closed --dry-run
   ```

### Scan Commands

```bash
cd signal_farm

# Scan all open markets (respects trading hours per asset class)
python main.py scan

# Scan and send Telegram alerts for found signals
python main.py scan --notify

# Scan a specific asset class only
python main.py scan --asset us_stocks --notify

# Scan multiple asset classes
python main.py scan --asset us_stocks,crypto --notify

# Force scan even if market is closed
python main.py scan --no-skip-closed --notify

# Preview Telegram message format without sending
python main.py scan --no-skip-closed --dry-run
```

### Windows Task Scheduler

Create `run_scan.bat` in the project root and schedule it to run every hour:

```bat
@echo off
cd /d D:\Repos\signal-farm\signal_farm
D:\Repos\signal-farm\.venv\Scripts\python.exe main.py scan --notify
```

Recommended schedule: every 30 min for `us_stocks` during market hours, every hour for everything else.

### Scan Hours (auto-skip closed markets)

| Asset class | Scan window |
|---|---|
| `us_stocks` | Mon–Fri 09:30–16:00 ET |
| `indices_futures` | Mon–Fri 00:00–22:00 UTC |
| `precious_metals` | Mon–Fri 00:00–22:00 UTC |
| `energies` | Mon–Fri 00:00–22:00 UTC |
| `agricultural_commodities` | Mon–Fri 01:00–20:30 UTC |
| `forex` | Mon–Fri 00:00–23:59 UTC |
| `crypto` | 24/7 |

## Running Tests

```bash
cd signal_farm
python -m pytest tests/ -v
```

## Config Reference

Edit `config/profiles.yaml` to adjust per-asset-class parameters:

```yaml
us_stocks:
  levels: 2                    # 2=director+executor, 3=director+filter+executor
  executor:
    sma_fast: 10               # Fast SMA period on executor TF
    sma_slow: 50               # Slow SMA period on executor TF
  pullback_lookback: 10        # Bars to look back for SMA touch (Variant A)
  keltner_lookback: 15         # Bars to look back for Keltner touch (Variant B)
  max_concurrent_positions: 5  # Max open trades simultaneously
  rr_ratio: 2.0                # Minimum risk:reward ratio
  atr_stop_mult: 1.5           # ATR multiplier for stop loss
```

## Output

- **Table** (default): printed to stdout with a trade log
- **JSON**: `{"metrics": {...}, "trades": [...]}` — suitable for LLM/pipeline consumption
- **CSV**: trade log as CSV
- **Charts**: saved to `./output/{ticker}_{variant}_{date}.html`

## Known Limitations

- **60-day intraday limit**: yfinance free tier limits intraday history to ~60 days. Default `--period` in profiles is set to `60d`.
- **No slippage or commission model** in v1. Results are optimistic — expect 5-15% degradation in live trading.
- **Stop fills**: stops are filled at the stop price, ignoring gaps. Gap-open scenarios would result in worse fills in practice.
- **yfinance reliability**: occasional missing bars, stale data, or download failures. The provider retries with exponential backoff and caches results locally in `.cache/`.

## Replacing yfinance

Implement the `DataProvider` abstract base class in `data_feed/provider.py` and swap the provider in `main.py`. The rest of the pipeline is data-source agnostic.
