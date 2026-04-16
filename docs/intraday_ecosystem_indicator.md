# Intraday Ecosystem Indicator — NAS100 Regime Monitor (5min)

> **Provider attuale**: yfinance (gratuito, zero infrastruttura)
> **Provider futuro**: TwelveData paid (vedi sezione 10 — migrazione pianificata)

## Obiettivo

Indicatore composito intraday che, su timeframe 5 minuti, misura l'allineamento dell'ecosistema NASDAQ per modulare in tempo reale il risk sizing e la qualità dei segnali su NAS100 (e potenzialmente SPY). Non è un segnale di entry: è un **filtro di regime** che amplifica o riduce l'aggressività del sistema esistente.

**Input**: 15 mega-cap stocks + sector ETFs risk-on/off + VIX, tutto su TF 5min.  
**Output**: un singolo `EcosystemState` aggiornato ogni 5 minuti con score, colore, e moltiplicatore di sizing.

---

## 1. Data Provider

### Fase attuale: **yfinance** (gratuito)

yfinance fornisce dati real-time (non delayed) su US stocks, ETFs e VIX durante market hours tramite scraping di Yahoo Finance. L'implementazione è già in codebase (`yfinance_provider.py`).

**Meccanismo**:
```python
# 1 sola chiamata HTTP ogni 5 minuti
yf.download(
    ["AAPL","MSFT","NVDA",...,"^VIX","XSD","XLK","IBB","TLT","XLU","SH"],
    period="1d",
    interval="5m",
    group_by="ticker"
)
# → ricevi tutte le barre 5min della sessione corrente per tutti i 22 simboli
# → latenza: ~1-2 secondi per batch
# → costo: $0
```

| Criterio | yfinance | Note |
|---|---|---|
| Barre 5min native | ✅ polling ogni 5min | 1 batch call = 22 simboli |
| VIX nativo | ✅ `^VIX` | real-time durante market hours |
| Sector ETFs | ✅ XSD, XLK, IBB, TLT, XLU, SH | inclusi nella stessa chiamata |
| Costo | **$0** | nessun abbonamento |
| Infrastruttura | **nessuna** | cloud API, no daemon locale |
| Storico 5min | 60 giorni max | sufficiente per live use |
| Affidabilità | ⚠️ API non ufficiale | Yahoo cambia endpoint ~1-2x/anno, community patcha in giorni |

**Limitazione accettata**: solo polling, nessun push/WebSocket. Su TF 5min è irrilevante — pollare una volta ogni 5 minuti è funzionalmente equivalente a un feed push.

---

## 2. Watchlist: Top 15 NASDAQ per Market Cap

Ridotto da 100 a 15 titoli — sufficiente per catturare >65% della capitalizzazione NAS100, con latenza e API cost minimi.

```yaml
# config/ecosystem_watchlist.yaml
ecosystem:
  mega_caps:
    - AAPL
    - MSFT
    - NVDA
    - AMZN
    - META
    - GOOGL
    - GOOG     # Class C — pesa separatamente nel NAS100
    - AVGO
    - TSLA
    - COST
    - NFLX
    - ASML
    - AMD
    - LIN
    - ADP

  risk_on_etfs:
    - XSD      # Semiconductors
    - XLK      # Technology
    - IBB      # Biotech

  risk_off_etfs:
    - TLT      # 20+ Year Treasury
    - XLU      # Utilities
    - SH       # Short S&P500

  vix:
    symbol: "^VIX"      # yfinance ticker
    fallback: "^VIX"    # stesso — nessun fallback necessario con yfinance

  last_updated: "2026-04-16"
  refresh_interval_days: 30   # Revisiona composizione mensilmente
```

**Nota**: la lista va aggiornata mensilmente. Le top 15 cambiano raramente (1-2 rotazioni/anno), ma è buona pratica verificare.

---

## 3. Logica dell'Indicatore

### 3.1 Mega-Cap Alignment (0–100)

Per ogni barra 5min, calcola lo "stato" di ciascuna delle 15 mega-cap:

```
Per ogni stock:
  1. SMA(10) su 5min → slope = (SMA[0] - SMA[3]) / SMA[3]  (slope su ultime 3 barre = 15min)
  2. Se slope > +0.0005 → BULLISH
     Se slope < -0.0005 → BEARISH
     Altrimenti → NEUTRAL

Score = (n_bullish / n_total) × 100

Colore:
  score >= 70  → GREEN  (ecosistema aligned long)
  score <= 30  → RED    (ecosistema aligned short)
  else         → GRAY   (mixed/choppy — cautela)
```

**Perché SMA slope e non prezzo > SMA?** Lo slope cattura il *momentum* del micro-trend, non solo la posizione. Se 12/15 mega-cap hanno slope positivo simultaneamente, c'è un flusso istituzionale direzionale.

### 3.2 Sector Risk Score (-1.0 … +1.0)

```
risk_on_momentum  = media(slope_5min di XSD, XLK, IBB)
risk_off_momentum = media(slope_5min di TLT, XLU, SH)

raw_score = risk_on_momentum - risk_off_momentum
sector_score = clip(raw_score / normalize_factor, -1.0, +1.0)
```

`normalize_factor` = mediana storica di |raw_score| su 20 sessioni (auto-calibrante).

### 3.3 VIX Adjustment

```python
def vix_adjustment(vix_level: float) -> float:
    if vix_level > 25:
        return 0.75    # -25% sizing (tail risk elevato)
    elif vix_level > 20:
        return 0.85    # -15% sizing
    elif vix_level > 15:
        return 0.92    # -8% sizing
    else:
        return 1.0     # nessun aggiustamento
```

**Nota**: VIX su 5min ha spikes rumorosi. Usare **SMA(12) del VIX 5min** (= media ultima ora) per smoothing.

### 3.4 Aggregazione Finale

```python
@dataclass
class EcosystemState:
    timestamp: datetime
    megacap_score: float          # 0–100
    megacap_color: str            # "GREEN" | "RED" | "GRAY"
    sector_score: float           # -1.0 … +1.0
    vix_level: float              # VIX spot
    vix_smoothed: float           # SMA(12) del VIX 5min
    size_multiplier: float        # 0.60 … 1.30
    confidence: int               # 0–100

# Calcolo size_multiplier
sector_mult = 1.0 + (0.20 * sector_score)    # range: 0.80 … 1.20
vix_mult    = vix_adjustment(vix_smoothed)

if megacap_color == "GREEN":
    base_mult = 1.10     # +10% base per ecosistema aligned
elif megacap_color == "RED":
    base_mult = 0.80     # -20% base per ecosistema disallineato
else:
    base_mult = 0.95     # -5% in incertezza

size_multiplier = clip(base_mult * sector_mult * vix_mult, 0.60, 1.30)

# Confidence = combinazione della "forza" dei segnali
confidence = int(
    0.50 * abs(megacap_score - 50) * 2    # 0-100: quanto è forte il consensus
  + 0.30 * abs(sector_score) * 100         # 0-100: quanto è chiara la rotazione
  + 0.20 * (100 - min(vix_smoothed, 40) * 2.5)  # 0-100: VIX basso = high confidence
)
```

---

## 4. Coesistenza con i Loop di Segnale Esistenti

Questo è il punto chiave: l'ecosystem monitor gira su 5min, i signal scanner esistenti girano su 30min (us_stocks) o 1h (indices, crypto, etc.). **Non si sovrappongono — girano in loop indipendenti e condividono solo un oggetto di stato.**

### 4.1 Architettura a Due Loop

```
┌─────────────────────────────────────────────────────────┐
│  LOOP A — EcosystemMonitor (5min)                       │
│  09:30 → ogni 5min → 16:00 ET                           │
│                                                         │
│  yf.download(22 simboli, interval="5m")                 │
│  → compute megacap_alignment + sector_score + vix       │
│  → scrive EcosystemState  ←──────────────────────┐      │
└────────────────────────────────────────────────  │  ────┘
                                                   │ (shared state, read-only per Loop B)
┌────────────────────────────────────────────────  │  ────┐
│  LOOP B — Signal Scanner (30min / 1h)            │      │
│  ogni chiusura barra executor                    │      │
│                                                   │      │
│  engine: director → filter → variant → scorer    │      │
│  AT SIZING: legge EcosystemState ─────────────────┘      │
│  → applica size_multiplier al position size              │
│  → emette segnale                                        │
└─────────────────────────────────────────────────────────┘
```

### 4.2 Perché non ci sono conflitti

1. **Clock separati**: Loop A si sveglia ogni 5min, Loop B ogni 30min/1h. Non si bloccano a vicenda.
2. **Accesso unidirezionale**: Loop A scrive su `EcosystemState`, Loop B lo legge. Nessuna scrittura concorrente.
3. **Fallback pulito**: se `EcosystemState` non è ancora disponibile all'avvio (primissimi 5min), il multiplier defaulta a `1.0` — il signal scanner funziona come oggi, senza crash.
4. **Nessun impatto sulla logica di segnale**: il Loop B continua a fare esattamente quello che fa ora (entry logic, score, etc.). L'ecosystem tocca *solo* il position size finale.

### 4.3 Timing in Pratica

```
09:30  Apertura sessione. Entrambi i loop si avviano.
09:35  Prima barra 5min chiude → Loop A: primo EcosystemState disponibile.
09:35  Nessun segnale 30min ancora — Loop B non ha ancora girato.
10:00  Prima barra 30min chiude → Loop B gira per la prima volta.
       EcosystemState ha già 5 aggiornamenti. Multiplier applicato correttamente.
10:30  Seconda barra 30min. EcosystemState ha 11 aggiornamenti.
...
```

Per il 1h executor (indices, crypto): EcosystemState ha 12 aggiornamenti prima che Loop B giri per la prima volta. Perfetto.

### 4.4 Punto di Integrazione nel Codice

Il solo file da modificare nel sistema esistente è `risk_manager/sizing.py`:

```python
# OGGI (invariato):
def calc_position_size(capital, price, atr, profile) -> float:
    base_size = capital * profile["risk_pct"] / (atr * profile["atr_stop_mult"])
    return base_size

# CON ECOSYSTEM (aggiunta minimale):
def calc_position_size(capital, price, atr, profile,
                       ecosystem_state=None) -> float:
    base_size = capital * profile["risk_pct"] / (atr * profile["atr_stop_mult"])

    if ecosystem_state is None or ecosystem_state.confidence < 40:
        return base_size  # comportamento invariato

    return base_size * ecosystem_state.size_multiplier
```

`engine.py` passa `ecosystem_state` solo se `ecosystem_monitor.enabled = True` nel profilo — altrimenti tutto gira esattamente come oggi.

---

## 5. Architettura

### 5.1 Nuovo Modulo: `signal_farm/signals/ecosystem_monitor.py`

```
EcosystemMonitor
├── __init__(config)
├── refresh()                    # Chiama yfinance, aggiorna stato — invocato ogni 5min
├── compute_megacap_alignment() → (score, color)
├── compute_sector_score() → float
├── compute_vix_adjustment() → float
├── get_state() → EcosystemState  # Stato corrente
└── is_stale() → bool            # True se ultimo refresh > 10min fa
```

### 5.2 Data Feed: usa `YFinanceProvider` esistente

**Nessun nuovo provider da implementare.** `yfinance_provider.py` è già in codebase. L'ecosystem monitor lo istanzia direttamente con `interval="5m"` per i simboli del watchlist.

### 5.3 Integrazione con Sistema Esistente

```
engine.py (run_scan)
│
├── [invariato] Per ogni ticker del watchlist:
│   ├── fetch data
│   ├── compute signals + score
│   └── [NUOVO] sizing.calc_position_size(..., ecosystem_state=monitor.get_state())
│
└── [NUOVO] Loop A separato (ogni 5min):
    └── ecosystem_monitor.refresh()
```

---

## 6. Caching e Performance

### Budget API per sessione (6.5h, 09:30–16:00)

| Componente | Simboli | Metodo | Costo/sessione |
|---|---|---|---|
| Mega-caps 5min | 15 | yfinance batch | 78 calls (1 ogni 5min) |
| Sector ETFs 5min | 6 | stessa chiamata batch | incluso |
| VIX 5min | 1 `^VIX` | stessa chiamata batch | incluso |
| **Totale** | **22** | **1 batch call ogni 5min** | **78 chiamate HTTP, $0** |

1 chiamata yfinance con 22 simboli = ~1-2 secondi. Tra una chiamata e la successiva ci sono ~298 secondi di idle. Nessun problema di rate limit.

### Latenza

- Aggiornamento EcosystemState: 1-2 secondi (tempo di risposta yfinance)
- Nessun bottleneck: SMA(10) su 22 serie è computazione trascurabile

### Cache Interna

```python
class EcosystemCache:
    bars: Dict[str, pd.DataFrame]    # ultimi 20 bar per simbolo (100min di storia)
    sma_slopes: Dict[str, float]     # slope corrente per simbolo
    last_update: datetime

    def update(self, symbol: str, bar: BarData):
        self.bars[symbol].append(bar)
        self.bars[symbol] = self.bars[symbol].tail(20)  # rolling window
        self.sma_slopes[symbol] = self._calc_slope(symbol)
```

---

## 7. Configurazione

### Enhancement a `config/profiles.yaml`

```yaml
us_stocks:
  ecosystem_monitor:
    enabled: true
    provider: "yfinance"       # fase attuale — migrazione a "twelvedata" in futuro
    timeframe: "5min"
    watchlist: "config/ecosystem_watchlist.yaml"

    megacap:
      sma_period: 10
      slope_lookback: 3        # barre per slope (3 × 5min = 15min)
      slope_threshold: 0.0005  # soglia per BULLISH/BEARISH
      green_threshold: 70      # % bullish per GREEN
      red_threshold: 30        # % bullish per RED

    sectors:
      normalize_window: 20     # sessioni per auto-calibrazione

    vix:
      smoothing_period: 12     # SMA(12) su 5min = 1 ora
      thresholds:
        extreme: 25
        high: 20
        moderate: 15

    sizing:
      min_multiplier: 0.60
      max_multiplier: 1.30
      green_base: 1.10
      red_base: 0.80
      gray_base: 0.95
      sector_weight: 0.20     # peso sector score nel multiplier

    min_confidence: 40         # sotto questa soglia, ecosystem ignorato
```

---

## 8. Fasi di Implementazione

### Fase 1 — EcosystemMonitor + VIX only (~2h)

- [ ] Creare `signals/ecosystem_monitor.py` con sola logica VIX adjustment
- [ ] `yf.download(["^VIX"], interval="5m", period="1d")` ogni 5min
- [ ] `EcosystemState` con solo `vix_level`, `vix_smoothed`, `size_multiplier` basato su VIX
- [ ] Aggiungere `ecosystem_state` param a `sizing.calc_position_size()` (opzionale, default None)
- **Test**: eseguire durante market hours, verificare che VIX si aggiorna ogni 5min

### Fase 2 — Mega-Cap Alignment (~3h)

- [ ] Aggiungere batch fetch per le 15 mega-cap nella stessa chiamata yfinance
- [ ] Implementare `compute_megacap_alignment()` (SMA slope per simbolo)
- [ ] Integrare `size_multiplier` completo (VIX + megacap color)
- **Test**: loggare GREEN/RED/GRAY durante una sessione — verificare che varia con il mercato

### Fase 3 — Sector ETFs + Integrazione Completa (~3h)

- [ ] Aggiungere 6 ETF al batch (XSD, XLK, IBB, TLT, XLU, SH)
- [ ] Implementare `compute_sector_score()` con auto-normalizzazione su 20 sessioni
- [ ] Integrare `ecosystem_state` nel flow `engine.py → sizing.py`
- [ ] Alert Telegram opzionale su regime shift (RED→GREEN, GREEN→RED)
- **Test**: A/B log segnali con/senza multiplier per 1-2 settimane

### Fase 4 — Backtest Validation (~4h)

- [ ] Fetch storico 5min con yfinance (60 giorni max)
- [ ] Simulare EcosystemState storicamente su quei 60 giorni
- [ ] A/B test: performance segnali esistenti con/senza ecosystem multiplier
- **Target**: Sharpe improvement +0.1-0.2, drawdown reduction -3-5%

---

## 9. Rischi e Mitigazioni

| Rischio | Mitigazione |
|---|---|
| yfinance API break (Yahoo cambia endpoint) | Il fallback è immediato: se `refresh()` fallisce, `EcosystemState` resta l'ultimo valido. Se stale > 10min → disabilita ecosystem, sizing torna a `multiplier=1.0` |
| VIX spike flash (rumore 5min) | SMA(12) smoothing — non reagire a spike singoli |
| Mega-cap stock halt | Escludere dal conteggio (`n_total -= 1`), ricalcolare score |
| Loop A che rallenta Loop B | I due loop girano in thread separati — Loop A non può bloccare il signal scanner |
| Cambio composizione top 15 | Revisione mensile del file `ecosystem_watchlist.yaml` |

---

## 9. Domande Aperte

1. **Market-cap weighted vs equal weight**: Pesare AAPL/MSFT/NVDA di più nel score, o trattare tutte e 15 uguale? (Suggerimento: iniziare equal-weight, iterare se serve)
2. **Pre-market**: Calcolare ecosystem prima dell'apertura (09:00-09:30) con dati pre-market? O partire da zero alle 09:30?
3. **Alerting Telegram**: `EcosystemState` genera alert standalone su regime shift (es. "RED→GREEN"), o solo modula il sizing silenziosamente?
4. **Overnight carry**: Se il sistema genera un segnale alle 15:55 con GREEN ecosystem, il multiplier resta attivo overnight o si resetta?

---

## 10. Migrazione a TwelveData (pianificata)

Quando si sottoscrive TwelveData paid (~$29/mo), la migrazione è motivata non solo dall'ecosystem indicator ma dall'unificazione di **tutti i provider del sistema**:

| Provider attuale | Copre | Sostituito da TwelveData |
|---|---|---|
| yfinance | US stocks, ETFs, ^VIX, indici | ✅ |
| Alpaca | US stocks storico | ✅ |
| Dukascopy | CFD backtest (forex, metalli, energies) | ✅ |
| ccxt/Binance | Crypto | ❌ (TwelveData non copre crypto depth) |

**Calcolo crediti TwelveData paid per ecosystem (batch multi-symbol)**:
- Sessione 5min: 78 poll × 1 batch call = 78 credits/sessione (irrilevante su 5.000/g)

**Cosa cambia nel codice**: solo il provider passato a `EcosystemMonitor.__init__()` — da `YFinanceProvider` a `TwelveDataProvider`. La logica di calcolo rimane identica.

**Prerequisito**: implementare prima `TwelveDataProvider` per la parte principale del sistema (CFD/forex live scanner), come già pianificato in `CONTEXT.md`. L'ecosystem indicator eredita il provider già pronto.
