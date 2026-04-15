# Backtest Methodology

## Fill Assumptions

**Entry fill:** Next candle open after the signal bar. The signal is computed on bar close (all indicator values are final at that point). The position is entered at the `open` price of bar `t+1`. This is the only lookahead-safe fill model without a full order book simulation.

**Exit fill:**
- **Stop hit:** Exit at exactly the stop price (assumes the stop was a limit order that filled at the stop level). In practice, gaps can cause worse fills — this is a known optimistic assumption in v1.
- **Target hit:** Exit at exactly the target price.
- **Forced exit:** Exit at the next candle open after the director slope flips (slope < 0 for LONG, slope > 0 for SHORT).
- **End of data:** Any position still open at the last bar of the dataset closes at the last close price.

## No Slippage, No Commission (v1)

Version 1 does not model slippage or trading commissions. This means results will be optimistic compared to live trading. When replacing yfinance with a more reliable data source, consider adding a slippage model (e.g., 0.05% of entry price) and per-trade commission.

## Concurrent Position Tracking

Positions are processed chronologically. When a new signal appears:
1. All previously opened positions are checked for exit conditions up to the signal bar
2. The remaining open position count is checked against `max_concurrent_positions`
3. The same `asset_class` count is checked against `max_per_sector`
4. If both limits allow, the new position is opened

Note: in the current implementation, position exits are simulated in a second pass over the signal DataFrame (for simplicity). The concurrent position check at entry time uses the positions opened so far without accounting for positions that might have exited before the signal. This is a conservative approach that may reject some valid entries.

## Forced Exit Mechanics

A forced exit is triggered when the director's SMA fast slope crosses below zero (LONG) or above zero (SHORT) while a trade is open. The slope is read from the `dir_sma_fast_slope` column of the aligned DataFrame — which already has the `shift(1)` applied (i.e., no lookahead). The trade closes at the **next** candle open after the triggering bar.

## No Re-Entry Rule

After a stop-out, the system requires a new pullback cycle before re-entering the same ticker. In practice this is enforced naturally by the rolling window touch detection: a fresh touch is required within the last N bars. After a stop-out the price typically moves away from the SMA, requiring time before a new valid touch is detected.

## Known Edge Cases

- **Gap openings:** If price gaps through the stop level at open, the stop fill is at the stop price (not the gap open). This is optimistic. A production system should use the bar open as the fill price when `open < stop` (LONG).
- **Halt days / missing bars:** yfinance occasionally returns missing bars for suspended tickers. The `dropna()` in the provider removes these, but gaps in the index can affect rolling indicator calculations near the gap.
- **60-day yfinance intraday limit:** The default `--period 60d` is the safe maximum for intraday data. Older intraday data is not available from yfinance free tier. Daily-TF director data uses `--period 200d` which is within the free tier limit.
- **Thin markets:** Very illiquid tickers may have wide bid-ask spreads. The backtest does not model this.

## Equity Curve Construction

The equity curve is built by updating current equity at each trade exit. Between exits, equity is held constant (no mark-to-market). The curve is reindexed to the full executor bar index and forward-filled. This means the curve shows flat segments between exits, which is a simplification.
