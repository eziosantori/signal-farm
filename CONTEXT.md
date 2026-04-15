# Signal Farm — Project Context for Claude Code

> **Scopo di questo file**: permettere a Claude Code di riprendere il lavoro immediatamente,
> senza dover rileggere tutta la storia della conversazione.
> Aggiornalo ogni volta che completi una fase significativa.

---

## 1. Cos'è questo progetto

**Signal Farm** è un sistema di trading algoritmico multi-timeframe (MTF) basato su:
- Trend-following SMA convergence + pullback
- 3 varianti di segnale (A = SMA pullback, B = Keltner breakout, C = ibrida)
- Asset class coperte: US Stocks, Indices, Forex, Precious Metals, Energies, Soft Commodities, Crypto
- Output: backtest 2y con metriche (Sharpe, PF, win rate), signal scanner live, alert Telegram (da implementare)

La spec originale è in `MTF_Signal_System_Spec.md`.

---

## 2. Struttura del progetto

```
signal_farm/
├── config/
│   ├── instruments.yaml     # Catalogo strumenti: feed IDs, yfinance ticker, ccxt symbol, best_variant, best_params
│   ├── profiles.yaml        # Parametri per asset class: TF director/filter/executor, SMA, RSI, Keltner, stop, score
│   └── defaults.yaml        # Soglie indicatori globali
├── data_feed/
│   ├── provider.py          # Abstract base: get_ohlcv(ticker, interval, period) → DataFrame
│   ├── provider_factory.py  # Factory con priority routing (vedi sezione 4)
│   ├── alignment.py         # merge_asof + shift(1) lookahead-safe
│   ├── alpaca_provider.py   # US stocks, 2y storico, Parquet cache (.alpaca_data/)
│   ├── ccxt_provider.py     # Crypto via Binance pubblico, 2y storico, Parquet cache (.ccxt_data/)
│   ├── oanda_provider.py    # CFD via Oanda v20 REST, Parquet cache (.oanda_data/) — NON ATTIVO (vedi nota)
│   ├── dukascopy_provider.py# Fallback backtest CFD, scarica file storici
│   └── yfinance_provider.py # Fallback live scanner e dati daily
├── indicators/
│   └── core.py              # calc_sma, calc_sma_slope, calc_roc, calc_atr, calc_keltner
├── signals/
│   ├── director.py          # LONG/SHORT/NEUTRAL da daily TF
│   ├── depth_filter.py      # 2-level e 3-level filter
│   ├── variant_a.py         # SMA pullback entry
│   ├── variant_b.py         # Keltner breakout entry
│   ├── variant_c.py         # Touch + breakout combinati
│   ├── scorer.py            # Score composito: Trend (40) + Momentum (30) + Entry (30) = 100
│   ├── context.py           # Market context: regime, ROC, RSI, volatilità, market ref ticker
│   └── engine.py            # Orchestratore: fetch → align → indicators → signals → score → context
├── risk_manager/
│   └── sizing.py            # position size, stop/TP, correlation filter
├── backtest/
│   ├── engine.py            # Backtester vettorizzato: entry next open, stop/target/forced exit
│   └── metrics.py           # Sharpe, PF, win rate, max DD, total return
├── visualizer/
│   └── charts.py            # Plotly HTML: candlestick + segnali + equity curve
├── optimize.py              # Grid optimizer: loop su parametri, tabella risultati
├── main.py                  # CLI entry point
└── tests/
    ├── test_indicators.py
    ├── test_signals.py
    ├── test_alignment.py
    └── test_backtest.py
```

---

## 3. CLI — Comandi principali

```bash
# Tutti i comandi si lanciano da dentro signal_farm/
cd signal_farm

# Backtest singolo
python main.py backtest --asset us_stocks --variant A --ticker AAPL
python main.py backtest --asset crypto --variant B --ticker BTC-USD --period 2y
python main.py backtest --asset indices_futures --variant B --ticker NAS100 --period 2y

# Confronto 3 varianti
python main.py compare --asset us_stocks --ticker MSFT

# Output JSON (per automazione)
python main.py backtest --asset us_stocks --variant A --ticker AAPL --output json

# Chart HTML
python main.py chart --asset us_stocks --variant A --ticker AAPL

# Grid optimization
python optimize.py --asset us_stocks --ticker MSFT --stock-grid
python optimize.py --asset crypto --ticker BTCUSD
python optimize.py --asset indices_futures --ticker NAS100

# .env viene caricato automaticamente da main.py all'avvio
```

---

## 4. Data Provider — Priority Routing

```
get_provider(ticker) ordine di priorità:

1. AlpacaProvider    → asset_class == "us_stocks" AND ALPACA_API_KEY set
2. CcxtProvider      → ha campo "ccxt" in instruments.yaml (crypto, Binance pubblico)
3. OandaProvider     → in _SYMBOL_MAP Oanda AND OANDA_API_KEY set  ← NON ATTIVO (vedi nota)
4. DukascopyProvider → ha campo "feed" valido in instruments.yaml
5. YFinanceProvider  → fallback
```

### Nota su OandaProvider
L'account Oanda practice **non è accessibile dall'EU** per API. `oanda_provider.py` è implementato
ma non integrato attivamente. Per ora:
- **Backtest 2y CFD** → DukascopyProvider (feed storici gratuiti)
- **Live scanner CFD** → YFinanceProvider con ticker futures diretti (GC=F, NQ=F, BZ=F, ecc.)

### Prossimo step provider: TwelveData
Quando si vuole un provider live senza limitazioni EU, implementare `TwelveDataProvider`:
- Piano free: 800 req/giorno, 8/min — sufficiente per scanning orario su 30 strumenti
- Copre: forex, indices, metalli, energies
- Chiave gratuita da twelvedata.com

---

## 5. Configurazione .env

```env
# US Stocks — alpaca.markets → API Keys → paper trading ok per dati storici
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...

# Oanda — NON ATTIVO (limitazioni EU su account practice)
# Quando si attiva: https://www.oanda.com/account/tpa/personal_token
OANDA_API_KEY=
OANDA_ENV=practice
```

---

## 6. Strumenti e ticker yfinance corretti

**Tutti i CFD usano ticker futures diretti** (non più ETF proxy come SPY/GLD/QQQ):

| Canonical | yfinance | Note |
|-----------|----------|------|
| XAUUSD | `GC=F` | Gold futures ~23h |
| XAGUSD | `SI=F` | Silver futures |
| US500 | `ES=F` | S&P 500 futures ~23h |
| NAS100 | `NQ=F` | NASDAQ 100 futures ~23h |
| US30 | `YM=F` | Dow futures ~23h |
| GER40 | `^GDAXI` | DAX — solo sessione tedesca |
| UK100 | `^FTSE` | FTSE — solo sessione UK |
| JPN225 | `^N225` | Nikkei — solo sessione Tokyo |
| AUS200 | `^AXJO` | ASX — solo sessione Sydney |
| BRENT | `BZ=F` | Brent futures ~23h |
| WTI | `CL=F` | WTI futures ~23h |
| NATGAS | `NG=F` | Nat Gas futures |
| COPPER | `HG=F` | Copper futures COMEX |
| COCOA | `CC=F` | Cocoa futures ICE |
| COFFEE | `KC=F` | Coffee futures ICE |
| SOYBEAN | `ZS=F` | Soybean futures CBOT |
| SUGAR | `SB=F` | Sugar No.11 futures ICE |
| COTTON | `CT=F` | Cotton No.2 futures ICE |
| OJUICE | `OJ=F` | OJ futures ICE |
| EURUSD | `EURUSD=X` | già corretto |
| (altri forex) | `GBPUSD=X`, ecc. | già corretti |

---

## 7. Risultati backtest 2y (stato attuale)

### US Stocks (Alpaca 2y, 30m, session 09:30-16:00 ET)
| Ticker | Variant | Sharpe | PF | Note |
|--------|---------|--------|----|------|
| MSFT | A | 0.91 | 1.61 | ✅ strong edge |
| META | A | 0.74 | 1.82 | ✅ good edge |
| AMZN | A | 0.68 | 1.47 | ✅ good edge |
| TSLA | A | 0.98 | 2.00 | ✅ strong, stop=2.5x, rsi_tight=6 |
| NVDA | A | 0.31 | 1.18 | ⚠️ marginal, stop=2.5x, min_score=75 |
| AAPL | A | -0.22 | — | ❌ no edge, evitare |
| GOOGL | A | 0.03 | 1.01 | ❌ no edge |

Parametri ottimali per stocks applicati in `profiles.yaml`:
- `atr_stop_mult: 2.0` (era 1.5)
- `rsi_tightening: 3` pre-applicato nei range RSI ([44,56] invece di [41,59])
- `min_score_threshold: 68`
- `max_concurrent_positions: 3`

### Crypto (Binance/ccxt, 2y, 1h)
| Ticker | Variant | Sharpe | PF | Note |
|--------|---------|--------|----|------|
| BTC-USD | B | 1.07 | 1.51 | ✅ strong, keltner_lookback=10 |
| ETH-USD | B | 0.24 | 1.11 | ⚠️ choppy, monitor only |

### CFD (Dukascopy 2y — backtest disponibili ma non ancora eseguiti sistematicamente)
Vedi `instruments.yaml` per `best_variant` e note per ogni strumento.

---

## 8. Architettura scorer e market context

### Scorer (signals/scorer.py)
Score 0-100 composto da:
- **Trend** (40pt): allineamento SMA, slope, ROC direzionale
- **Momentum** (30pt): RSI in zona, distanza da SMA
- **Entry** (30pt): qualità del pullback, volume, ATR

### Market Context (signals/context.py)
Ogni segnale include colonne ctx_*:
- `ctx_trend_label`, `ctx_regime` → Bull/Bear/Neutral, Above/Below SMA
- `ctx_roc_pct`, `ctx_rsi`, `ctx_rel_vol`, `ctx_atr_pct`
- `ctx_market_name`, `ctx_market_label`, `ctx_market_roc` → ref ticker (NAS100 per stocks, XAUUSD per gold, ecc.)

**Fix critico implementato**: quando `same_as_signal: true` (es. XAUUSD che usa se stesso come ref),
il context resampla i dati intraday a daily OHLCV prima di calcolare il regime, altrimenti mostrava sempre NEUTRAL.

**Fix critico**: `context.py` usa `get_provider(ref_ticker)` per il reference ticker,
non il provider dello strumento principale. NAS100 deve usare Dukascopy, non Alpaca.

---

## 9. Bug fixes importanti già applicati

1. **Session filter US stocks**: Alpaca restituisce 32 bar/giorno (extended hours). Senza filtro i segnali
   erano su orari non tradabili. Fix: `_apply_session_filter()` in `signals/engine.py` con pytz ET.

2. **Cache coverage miss**: primo fetch breve (7d test) poi fetch lungo (2y) trovava la cache del primo.
   Fix: `_cache_valid()` in tutti i provider controlla che i dati coprano almeno il 90% del periodo richiesto.

3. **ccxt Timestamp error**: `pd.Timestamp(since, tz="UTC")` falliva se `since` era già tz-aware.
   Fix: `pd.Timestamp(since).tz_convert("UTC") if since.tzinfo else pd.Timestamp(since, tz="UTC")`

4. **Sub-scores non passavano al backtest**: `backtest/engine.py` usava solo `signal_score`.
   Fix: aggiunto `_CTX_COLS` list, le colonne score_*/ctx_* passano per pending_entry → open_positions → trade_results.

5. **GOLD market context sempre NEUTRAL**: path `same_as_signal: true` usava colonne dir_* già forward-filled.
   Fix: resample a `"1D"` con `.agg({open/high/low/close})` prima di `_compute_regime()`.

---

## 10. Da fare (prossimi step in ordine)

### IMMEDIATO
- [ ] **`scan` command** in `main.py`: scansiona tutti i ticker attivi, restituisce segnali correnti
  - Loop su instruments.yaml per asset class attiva
  - Chiama `generate_signals()` per ogni ticker
  - Filtra segnali all'ultima barra (current bar)
  - Output: tabella o JSON

- [ ] **Telegram notifier** (`notifier.py`):
  - Legge output JSON dello scan
  - Manda alert via `python-telegram-bot` o `httpx` (solo POST a Telegram API)
  - Card con: strumento, variante, direzione, entry/stop/target, score, ctx_market
  - Config: `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` in `.env`

- [ ] **Windows Task Scheduler**: script `.bat` che lancia scan + notifier ogni ora

### MEDIO TERMINE
- [ ] **TwelveDataProvider**: provider live per CFD senza limitazioni EU
  - Priorità 3 nel factory (prima di Dukascopy, dopo ccxt)
  - Copre: forex, indices (come indici, non futures), metalli, energies
  - Chiave gratuita: twelvedata.com

- [ ] **Backtests 2y CFD**: eseguire grid optimization su forex majors, indices, gold via Dukascopy
  - Strumenti prioritari: EURUSD, XAUUSD, NAS100, GER40, BRENT, NATGAS, COCOA

- [ ] **cBot cTrader**: C# bot che legge `signals_latest.json` e apre ordini
  - OneFunder usa cTrader → piena compatibilità
  - Semi-auto (alert) o full-auto (execution diretta)

### FUTURO
- [ ] **Delivery strategia**: Telegram (alert) + JSON file (log) + cBot (esecuzione)
- [ ] **Live performance tracking**: confronto segnali emessi vs outcome reale

---

## 11. Ambiente e dipendenze

```bash
# Dipendenze principali (requirements.txt)
yfinance>=0.2.36
pandas>=2.0
numpy>=1.26
plotly>=5.20
PyYAML>=6.0
requests>=2.31
alpaca-py>=0.20
ccxt>=4.0
pytz
pyarrow          # per Parquet caching

# Opzionale (per ccxt)
pip install ccxt

# Per il futuro notifier
pip install python-telegram-bot
```

```bash
# Setup Windows (PowerShell)
cd path\to\signal-farm\signal_farm
python -m venv .venv
.venv\Scripts\activate
pip install -r ..\requirements.txt

# Verifica che .env sia in signal-farm/ (un livello sopra signal_farm/)
# main.py lo carica automaticamente con _load_dotenv()
```

---

## 12. Note architetturali critiche

- **Lookahead prevention**: colonne director/filter hanno `.shift(1)` PRIMA del `merge_asof` in `alignment.py`
- **Entry fill**: next candle open dopo la barra del segnale (unico modello lookahead-safe)
- **Forced exit**: quando `dir_sma10_slope < 0` il backtester chiude la posizione indipendentemente da stop/target
- **Cache TTL**: 12h per tutti i provider. Parquet in `.{provider}_data/<SYMBOL>/<TF>.parquet`
- **DataUnavailableError**: propagata fino a `main.py` che esce con code 1 e messaggio chiaro
- **Working directory**: tutti i comandi si lanciano da dentro `signal_farm/`, non dalla root del repo
