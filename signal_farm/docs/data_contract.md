# Data Contract

## OHLCV DataFrame Conventions

### Column Names
All DataFrames use lowercase column names:
- `open`, `high`, `low`, `close`, `volume`

### Aligned DataFrame Prefixes
After `alignment.align_timeframes()` the wide DataFrame uses:
- `exec_open`, `exec_high`, `exec_low`, `exec_close`, `exec_volume` — executor timeframe
- `dir_open`, `dir_high`, `dir_low`, `dir_close`, `dir_volume` — director (daily) values
- `filt_open`, `filt_high`, `filt_low`, `filt_close`, `filt_volume` — filter TF (3-level profiles only)

Indicator columns appended on the aligned DataFrame follow the same prefix:
- `exec_sma_fast`, `exec_sma_slow`, `exec_sma_fast_slope`, `exec_sma_slow_slope`
- `exec_atr14`, `exec_keltner_mid`, `exec_keltner_upper`, `exec_keltner_lower`
- `dir_sma_fast`, `dir_sma_slow`, `dir_sma_fast_slope`, `dir_roc10`
- `filt_sma_fast`, `filt_sma_fast_slope` (3-level only)

### Signal Columns (added by signals/engine.py)
- `direction` — `"LONG"`, `"SHORT"`, `"NEUTRAL"`
- `depth_ok` — bool
- `signal` — bool (True = entry signal on this bar)
- `entry_price` — float (NaN when no signal)
- `stop` — float (NaN when no signal)
- `target` — float (NaN when no signal)
- `rr` — float, actual risk:reward ratio (NaN when no signal)

## Timezone Standard
- **All DatetimeIndex values are UTC** throughout the pipeline.
- `YFinanceProvider._normalize()` converts naive daily timestamps to UTC and converts tz-aware intraday timestamps from their local tz to UTC.
- The US stocks session filter converts to ET internally then converts back to UTC.

## NaN Policy
| Column | NaN handling |
|---|---|
| `close`, `open`, `high`, `low` | Rows with NaN in any OHLC are dropped in provider |
| `volume` | NaN filled with 0 (some indices have no volume) |
| Indicator warmup rows | Left as NaN; signal logic treats NaN indicator as no-signal |
| `dir_*` / `filt_*` on aligned df | NaN at the start (before first director bar) — signal engine skips these |

## dtypes
- OHLCV: `float64`
- `volume`: `float64` (converted from int by yfinance)
- `signal`, `depth_ok`: `bool`
- `direction`: `object` (string category)
- All DatetimeIndex: `DatetimeTZDtype(tz=UTC)`

## Cache Format
- Pickle files stored in `.cache/` at project root
- Cache key: `md5(f"{ticker}_{interval}_{period}_{as_of}")` where `as_of` is `YYYY-MM-DD` for daily TFs and `YYYY-MM-DD-HH` for intraday
- One file per `(ticker, interval, period, as_of)` tuple
- Invalidated automatically by the time-based `as_of` key
