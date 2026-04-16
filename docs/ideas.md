# US100 Big Plays — Risk-Adjusted Signal Routing

## Executive Summary

Implementare un sistema intra-session che modula il rischio su NAS100/SPY selon l'allineamento di trend macro con le top 100 stock NASDAQ, le sector ETF (risk-on vs risk-off) e il sentiment VIX. Questo consente entry più aggressive quando l'ecosistema è aligned, e mode difensivi quando divergence emerge.

**Status Codebase**:

- ✅ Multi-timeframe alignment (daily director + 4H filter + executor TF)
- ✅ 3 signal variants con scorer 0-100
- ✅ Risk sizing con correlation filter (max 5 posizioni totali)
- ⚠️ Manca: ETF sector tracking, top 100 NASDAQ correlation, VIX integration

---

## 1. Requirement Analysis

### 1.1 Alignment Layers

#### 1.1.1 Top 100 NASDAQ Market Cap Alignment

**What**: Verificare che il trend intraday di NAS100 sia in linea con la majority delle top 100 stocks.

**How to Implement**:

- Fetch top 100 by mcap settimanale (snapshot in config)
- Compute daily director trend score: % di stock in buy regime (SMA slope > 0 su daily)
- Se **score > 75%** → NAS100 aligned to upside (**GREEN**)
- Se **score < 40%** → NAS100 aligned to downside (**RED**)
- Else → **GRAY** (mixed, cautious)

**Data Source**: Alpaca (US stocks) + Yfinance fallback  
**Frequency**: Refresh 1x/ora intra-session (costo: ~30 API calls/ora)

#### 1.1.2 Multi-Timeframe Director (Already Implemented)

**Current**: Director usa daily per trend macro, filterer (opzionale) 4H per confirmation.  
**Enhancement**: Extend scorer con NAS100 alignment signal (+15 punti se green, -15 se red).

#### 1.1.3 Sector ETF Risk-On/Risk-Off

**Risk-On Sectors**:

- XSD (Semiconductors)
- XLK (Technology)
- IBB (Biotech)
- XRT (Retail — proxy inflation expectations)

**Risk-Off Sectors**:

- SH (Short S&P500)
- TLT (Long-term Bonds)
- XLU (Utilities)

**Strategy**:

- Score = (sum of risk-on sector daily slope) - (sum of risk-off sector daily slope)
- Normalize to -1..+1
- Modulate max position size: `base_size × (1 + 0.3 × risk_score)`
  - E.g., if risk_score = +1 (max bullish), size → base × 1.3 (30% increase)
  - If risk_score = -1 (max bearish), size → base × 0.7 (30% decrease)

#### 1.1.4 VIX Integration

**What**: If VIX > 20 (elevated tail risk), reduce position size by further 15%.

**Data Source**: Yfinance (^VIX)  
**Adjustment Logic**:

```
if vix > 20:
    size_adjustment = 0.85
elif vix > 15:
    size_adjustment = 0.92
else:
    size_adjustment = 1.0

final_size = base_size × risk_score_multiplier × size_adjustment
```

---

## 2. Architecture Proposal

### 2.1 New Module: `ecosystem_monitor.py`

Located in `signal_farm/signals/ecosystem_monitor.py`

**Inputs**:

- Watchlist config (top 100 NASDAQ)
- Risk-on/off ETF list
- Reference frame (NAS100 daily director)

**Outputs**:

```python
class EcosystemState(NamedTuple):
    nas100_alignment: str          # "GREEN" | "RED" | "GRAY"
    nas100_score: float            # 0..100
    risk_sector_score: float       # -1..+1
    vix_level: float
    size_multiplier: float         # 0.7..1.3
    confidence: int                # 0..100
```

**Main Methods**:

- `compute_nas100_alignment()` — Fetch top 100, compute buy-regime %, return color
- `compute_sector_momentum()` — Fetch risk-on/off ETFs, compute net score
- `compute_vix_adjustment()` — Fetch VIX, return size adjustment
- `aggregate_ecosystem_state()` → `EcosystemState`

### 2.2 Integration Points

1. **Scorer Enhancement** — [scorer.py](signal_farm/signals/scorer.py):
   - Add "Ecosystem Alignment" dimension (+20 points if GREEN, -20 if RED, 0 if GRAY)
   - New max score: 120

2. **Sizing Enhancement** — [sizing.py](signal_farm/risk_manager/sizing.py):
   - Accept `ecosystem_state` param in `calc_position_size()`
   - Apply `size_multiplier` after base calculation

3. **Engine Flow** — [engine.py](signal_farm/signals/engine.py):
   - Call `ecosystem_monitor.aggregate_ecosystem_state()` once per bar
   - Pass to scorer + sizer

---

## 3. Optimization: API Call Reduction

**Problem**: Fetching 100+ stocks every bar → prohibitive.

**Solution: Tiered Caching**

### 3.1 Caching Strategy

| Layer                          | Refresh          | Cost/Hour | Notes                                 |
| ------------------------------ | ---------------- | --------- | ------------------------------------- |
| **Top 100 NASDAQ (metadata)**  | 1x/week          | 1-2 calls | Cached in config/top_100_nasdaq.yaml  |
| **Top 100 daily candle + SMA** | 1x/hour (or EOD) | ~30 calls | Batch fetch via Alpaca, cache locally |
| **Risk-on/off ETF daily**      | 1x/hour          | ~6 calls  | Yfinance batch (small watchlist)      |
| **VIX**                        | 1x/bar (~1min)   | ~60/hour  | Cold: 1 call. Warm: cache 1min        |

### 3.2 Implementation (Pseudo-Code)

```python
class EcosystemCache:
    def __init__(self, cache_ttl_sec=3600):
        self.cache_ttl = cache_ttl_sec
        self.top_100_candles = None
        self.last_fetch_time = 0
        self.top_100_list = load_config("config/top_100_nasdaq.yaml")

    def get_nas100_alignment(self, current_time):
        """Fetch only if cache expired, else return cached."""
        if current_time - self.last_fetch_time > self.cache_ttl:
            candles = fetch_batch_alpaca(self.top_100_list)
            self.top_100_candles = compute_sma_slopes(candles)
            self.last_fetch_time = current_time

        return score_alignment(self.top_100_candles)
```

### 3.3 Expected Performance

- **Baseline** (no cache): ~100 API calls, ~5-8 sec latency per bar
- **With cache** (1h TTL): ~1-2 API calls/bar (only VIX), ~200ms latency
- **Improvement**: 50x+ throughput increase, minimal error rate increase (stale top 100 < 1% impact)

---

## 4. Configuration Schema

### 4.1 New File: `config/top_100_nasdaq.yaml`

```yaml
top_100_nasdaq:
  symbols:
    - AAPL
    - MSFT
    - NVDA
    # ... 97 more
  last_updated: "2024-01-15"
  data_source: "nasdaq_official"
  refresh_interval_days: 7
```

### 4.2 Enhancement: `config/profiles.yaml`

```yaml
ecosystem_config:
  enabled: true
  cache_ttl_sec: 3600

  top_100:
    fetch_method: "alpaca" # or "yfinance"
    buy_regime_threshold: 0.75 # % of stocks in buy (SMA slope > 0)

  risk_sectors:
    on: ["XSD", "XLK", "IBB", "XRT"]
    off: ["SH", "TLT", "XLU"]

  vix_thresholds:
    high: 20 # Reduce size 15%
    medium: 15 # Reduce size 8%
    low: 0 # No adjustment

  size_multiplier:
    min: 0.7
    max: 1.3
```

---

## 5. Execution Phases

### Phase 1 (v1 — Foundation)

- [ ] Implement `ecosystem_monitor.py` + VIX only
- [ ] Integrate into scorer (Ecosystem Alignment dim)
- [ ] Config schema + top 100 NASDAQ snapshot
- **Timeline**: ~3-4 hours
- **Test**: Backtest 6 months data, measure Sharpe improvement

### Phase 2 (v1.1 — Sector Integration)

- [ ] Add risk-sector tracking (6 ETFs)
- [ ] Modulate sizing per risk_sector_score
- [ ] Implement caching layer
- **Timeline**: ~4-5 hours

### Phase 3 (v2 — Top 100 Alignment)

- [ ] Batch fetch + cache top 100 daily candles
- [ ] Implement buy-regime scoring
- [ ] A/B test: with/without alignment (6-12 months data)
- **Timeline**: ~6-8 hours (+ backtest validation)

---

## 6. Success Metrics

| Metric            | Target             | Notes                         |
| ----------------- | ------------------ | ----------------------------- |
| **Sharpe Ratio**  | +0.3 (vs baseline) | +0.30 improvement = good      |
| **Win Rate**      | +2-3%              | During bullish ecosystem only |
| **Drawdown Max**  | -5% under risk-off | VIX > 20 protection           |
| **API Latency**   | < 250ms/bar        | With caching                  |
| **API Cost/Hour** | < 50 calls         | vs 100+ baseline              |

---

## 7. Known Gaps & Future Work

- ❌ **Intra-minute ecosystem changes**: Top 100 snapshot refreshed hourly — misses micro divergences
- ❌ **ETF liquidity weighting**: All sectors weighted equally (should weight by sector GDP %)
- ❌ **Correlation vs causation**: Assumes top 100 → NAS100; may be lagged
- 📊 **Optional**: Add regime filter based on US Treasury curve (10Y-2Y spread)

---

## Questions for Refinement

1. Should top 100 be **market-cap weighted** or binary (in regime vs not)?
2. **Minimum ecosystem confidence** to take trades (e.g., only trade if `confidence > 60`)?
3. **Real-time vs batch**: Prefer live VIX + hourly batch top 100, or daily EOD everything?
