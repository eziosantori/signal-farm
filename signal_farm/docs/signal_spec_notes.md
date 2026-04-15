# Signal Spec Implementation Notes

## Ambiguities Resolved

### 1. Slope Threshold Formula
The spec mentions a slope threshold but doesn't give a concrete formula.
**Decision:** `threshold = 0.0001 * dir_close` (0.01% of price per bar).
This is price-proportional so it works across asset classes with very different price levels (e.g., BTC vs. a $10 stock).
Configurable via `defaults.yaml → director.slope_threshold_factor`.

### 2. "Touch" Tolerance Definition
**Decision:** A touch is defined as `low <= sma_fast * (1 + tolerance)` for LONG, `high >= sma_fast * (1 - tolerance)` for SHORT.
Tolerance = `0.001` (0.1%) by default. This handles the common case where the wick pierces the SMA by a sliver but doesn't close through it.

### 3. Rolling Window Implementation of N-Candle Lookback
The spec says "price touched SMA within the last N candles." This is implemented as:
```python
touched = (low <= threshold).rolling(N).apply(lambda x: int(x.any()), raw=True)
```
The rolling window looks back exactly N bars (inclusive of current bar). The `raw=True` flag passes a numpy array for speed.

### 4. Swing Low / High Definition
- **LONG:** `swing_low = min(low)` over the entire pullback window (rolling N bars).
- **SHORT:** `swing_high = max(high)` over the same window.
Both are computed on the aligned DataFrame's executor `low`/`high` columns at signal bar time. No separate event detection system.

### 5. 2-Level vs 3-Level Profile Routing
The `levels` field in `profiles.yaml` determines routing:
- `levels: 2` → director + executor only; `depth_filter.py` uses only executor conditions.
- `levels: 3` → director + filter + executor; filter data is fetched and aligned; depth filter additionally checks `filt_sma_fast_slope`.

### 6. First Incomplete Candle of Session
No special treatment. The session filter in `YFinanceProvider._filter_session()` already removes bars outside 09:30–16:00 ET for the `30m` interval. The SMA warmup naturally handles the first few bars having NaN indicators.

### 7. Target Price Calculation
`target = entry_price ± rr_ratio * |entry_price - stop|`
Where `rr_ratio = 2.0` by default. This gives a minimum 2:1 R:R.

### 8. Variant C Stop Definition
Variant C stop is the swing low (LONG) or swing high (SHORT) over `max(pullback_lookback, keltner_lookback)` bars. This is more conservative than Variant A's ATR-based stop, reflecting the more selective entry.

### 9. Director Columns on Aligned DataFrame
After alignment, director indicators are computed on the `dir_*` columns of the already-aligned DataFrame (not on the raw director DataFrame). This is safe because `dir_close` in the aligned DataFrame already has the shift(1) applied (lookahead prevention is handled by `alignment.py`).

### 10. Short-Side Signal Support
All three variants support both LONG and SHORT directions symmetrically. The director must explicitly emit "SHORT" for short signals to be generated; "NEUTRAL" suppresses all signals.
