# HTF Swing Trading Variant — Brainstorm Ideas

## 1. Contesto e obiettivo

Le varianti attuali (A/B/C) sono **intraday**:
- **Variant A** — SMA pullback (30m stocks, 1h altri)
- **Variant B** — Keltner breakout (idem)
- **Variant C** — Ibrida A+B (idem)

Manca un segmento **HTF swing trading** con holding periods da giorni a settimane, su grafici daily (± weekly). L'obiettivo è esplorare 8 idee concrete di varianti long-biased per:

- **US Stocks** (beta watchlist via Alpaca)
- **Indici CFD** (NAS100, SPX500, GER40 via Dukascopy)

Ogni idea specifica rationale, indicatori, filtri, stop/target, position size, exit, effort di implementazione e lacune del sistema da colmare. Alla fine una shortlist raccomandata e una roadmap di integrazione.

---

## 2. Inventario capacità attuali

### Già disponibile — riuso diretto

| Componente | Dove | Note |
|---|---|---|
| Indicatori: SMA, ROC, RSI, ATR, Keltner, RelVolume | `signal_farm/indicators/core.py` | 7 funzioni, tutte pure |
| Director daily + Depth filter | `signals/director.py`, `signals/depth_filter.py` | 2-level e 3-level |
| Scoring 100pt (trend 45 + momentum 30 + entry 25) | `signals/scorer.py` | Modulare per-variant |
| Ecosystem monitor: VIX + sector ETF (7) + NAS100 breadth | `signals/ecosystem_monitor.py` | Size multiplier 0.5×–2.0× |
| Backtest engine: hard stop + target + forced exit su director flip | `backtest/engine.py` | Event-driven, next-bar-open entry |
| Risk sizing ATR-based + correlation filter | `risk_manager/sizing.py` | `risk_pct=0.01`, RR-based targets |
| Alpaca provider con cache Parquet incrementale | `data_feed/alpaca_provider.py` | Supporta `1d` nativamente |
| Dukascopy provider per CFD storici | `data_feed/dukascopy_provider.py` | Indici/Forex/Metals |

### Lacune — da aggiungere se servono

**Indicatori mancanti**: EMA, MACD, Donchian, Bollinger Bands, ADX/DI, Supertrend, rolling N-day high/low, relative strength vs benchmark.

**Dati**: `1wk` non mappato in `alpaca_provider.py::_INTERVAL_MAP` (oppure ottenibile via resample di `1d`).

**Engine**:
- Trailing stop (ATR chandelier)
- Partial exits / scale-out (es. 50% a 1R + move to BE, 50% runner)
- Time-based exit (max holding bars)

**Registrazione nuova variante** (per ogni nuovo `variant_d`):
- Aggiungere `"D"` al set `VARIANTS` in `signals/engine.py`
- Import + elif branch in `apply_variant_signals()`
- Chiave `rsi_filter.variant_d` nei profili
- Branch per D in `scorer.py::_rsi_quality()` e `_entry_precision()`

---

## 3. Nota su timeframe 4h per US stocks

La sessione RTH US è 9:30–16:00 ET = 6.5 ore. Bucketing 4h produce candele **irregolari**: 9:30–13:30 (4h piena) + 13:30–16:00 (2.5h troncata), oppure — se il provider bucketizza su boundary UTC — candele ancora più sballate che spezzano la sessione in modo non comparabile. I volumi tra le due candele non sono omogenei, rendendo qualsiasi filtro rel_volume o momentum TF-dependent inaffidabile.

**Raccomandazione**:
- **US Stocks** → usare solo daily e (opzionalmente) weekly. Niente 4h.
- **Indici Dukascopy** → CFD tradano ~23h/giorno, il 4h è pulito e utilizzabile senza distorsioni.

---

## 4. Ideas

### Idea 1 — Daily SMA Pullback (Variant A on Daily)

**Rationale**: il classico swing pullback su daily chart. Trend confermato dalla slope della SMA50, entry al pullback verso SMA20 e bounce. È la traslazione "1:1" di Variant A su timeframe daily.

**Timeframes**: 2-level `1d/1d` (director daily SMA50 slope + executor daily SMA20 touch).

**Indicatori**: SMA10/20/50, RSI14, ATR14 — tutti già disponibili.

**Entry (LONG)**:
- `exec_low <= exec_sma20 * (1 + tol)` nei precedenti N bar (lookback 5–10 giorni)
- `exec_close > exec_sma20` (recupero sopra la media)
- `exec_sma20_slope > 0`
- `RSI14 ∈ [40, 58]` (zona pullback più larga della controparte intraday)

**Filtri**:
- Director: SMA50 slope > 0 e `close > SMA50`
- Depth: `exec_close > exec_sma50`
- RelVolume sul bar di bounce > 1.0 (opzionale, boost allo score)

**Stop**: `max(swing_low_5d, entry - 2.0 × ATR14)`
**Target**: `entry + 2.5 × |entry - stop|` (RR 2.5)

**Position size**: fixed fractional 1% equity, `size = (equity × 0.01) / (entry - stop)`. Size multiplier ecosystem se US stock/index.

**Exit**: stop hit / target hit / forced exit se SMA50 daily slope flip a negativo per 3 bar consecutivi (director flip più lento della versione intraday).

**Strengths**:
- Zero nuovo codice: solo profilo nuovo
- Setup well-documented, ampia letteratura
- Holding period 5–15 giorni, ottimo per un cBot che esegue alla chiusura daily

**Weaknesses**:
- Bassa frequenza (10–30 segnali/anno/ticker)
- Director forced-exit originale troppo aggressivo per daily → va ammorbidito
- Richiede ≥ 200 giorni di storia per SMA50 stabile

**Effort**: ★☆☆☆☆
**Lacune da colmare**: nessuna. Solo nuovo profilo `us_stocks_swing` con `executor.interval=1d`.

---

### Idea 2 — 50/200 SMA "Stage 2" Pullback (Minervini-style)

**Rationale**: lo stock è in Stage 2 uptrend (close > SMA50 > SMA200) e ha appena fatto un reset tecnico. Entriamo al pullback verso SMA50 quando RSI torna sotto 45 e compare un bar di reversal. Setup battle-tested su growth stocks.

**Timeframes**: 2-level `1d/1d`.

**Indicatori**: SMA20/50/**200**, RSI14, ATR14, RelVolume. Tutti già disponibili.

**Entry (LONG, solo long)**:
- `close > sma50 > sma200` per ≥ 20 bar consecutivi (Stage 2 confermato)
- RSI14 è sceso sotto 45 nei precedenti 10 bar (pullback vero)
- Bar corrente: `close > prev_high` e `close > open` (reversal bar)
- `rel_volume > 1.2` sul bar di reversal (partecipazione)

**Filtri**:
- Director: `sma50_slope > 0` e `sma200_slope >= 0`
- Ecosystem: size multiplier applicato
- ATR non esplosivo: `atr_pct < 1.5× percentile_50(atr_pct_60d)` (pullback "quiet", non panico)

**Stop**: `min(swing_low_pullback, entry - 2.0 × ATR14)` — ancora lo swing low del pullback
**Target**: `entry + 2.5 × |entry - stop|`

**Position size**: 1% equity × ecosystem_multiplier.

**Exit**: stop / target / forced exit se `close < sma50` per 2 bar consecutivi (uscita da Stage 2).

**Strengths**:
- Zero nuovo codice indicatori (SMA200 è solo un parametro in più)
- Setup con 40+ anni di validazione (O'Neil, Minervini)
- Perfetto fit per la beta watchlist (stocks ad alto momentum)
- Exit rule su Stage 2 break è naturale e interpretabile

**Weaknesses**:
- Pochi segnali durante bear market (per design)
- Richiede ≥ 250 giorni di storia (SMA200 stabile)
- RelVolume daily su Alpaca free IEX può essere meno affidabile di un feed SIP

**Effort**: ★☆☆☆☆
**Lacune**: aggiungere `sma_very_slow=200` nel profilo `us_stocks_swing` e nel signal; zero nuovi indicatori.

---

### Idea 3 — Donchian Channel Pullback

**Rationale**: dentro un canale Donchian 20-day forte, comprare i pullback al midpoint. Differisce da Variant A/B perché usa livelli estremi orizzontali (non medie mobili) → segnali complementari, minore overlap con le varianti esistenti.

**Timeframes**: 2-level `1d/1d` (o 3-level `1d/4h/4h` per indici Dukascopy).

**Indicatori**: **NUOVO Donchian** (rolling max/min su N bar — ~10 righe in `core.py`), SMA50, RSI14, ATR14.

**Entry (LONG)**:
- `close_prev_bar ∈ top_30%_of_donchian_range` (siamo nel terzo superiore del canale 20d)
- `low` del bar corrente tocca `donchian_mid = (donchian_high + donchian_low) / 2` con tolleranza 0.5%
- `close > donchian_mid` (bounce)
- RSI14 ∈ [42, 62]

**Filtri**:
- Director daily: SMA50 slope > 0
- `donchian_width_pct = (dch_high - dch_low)/close > 3%` (canale abbastanza largo da far muovere il prezzo)

**Stop**: `min(donchian_low - 0.5 × ATR14, swing_low_5d)` — sotto il bordo inferiore del canale
**Target**: `donchian_high` (mira al top del canale) OR `entry + 2.0 × |entry - stop|` (whichever prima)

**Position size**: 1% equity × ecosystem_multiplier.

**Exit**: stop / target / forced exit se `close < donchian_mid` per 2 bar (rottura della struttura).

**Strengths**:
- Livelli orizzontali interpretabili (non dipendono dalla slope)
- Segnali distinti rispetto ad A/B (meno correlazione)
- Aggiunta indicatore minima

**Weaknesses**:
- Setup richiede formazione del canale (prime settimane dopo breakout spesso senza segnali)
- In strong trend senza pullback significativo, pochissime entry
- Target "donchian_high" può fare take-profit troppo presto se breakout continua

**Effort**: ★★☆☆☆
**Lacune**: aggiungere `calc_donchian(high, low, period)` in `indicators/core.py` (ritorna `upper/lower/mid`).

---

### Idea 4 — N-day High Breakout (Turtle-light)

**Rationale**: breakout momentum classico. Entry quando il close sfonda il massimo degli ultimi N giorni (50 è il Turtle "slow system"). Cavalcare il trend con trailing stop.

**Timeframes**: 2-level `1d/1d`.

**Indicatori**: **NUOVO rolling N-day high** (trivial), SMA50, ATR14, opzionale ADX o slope filter.

**Entry (LONG)**:
- `close > max(close_prev_50)` (breakout)
- Non già in trade sullo stesso ticker

**Filtri**:
- Director: SMA50 slope > 0 (trend up conferma)
- ATR moderato: `atr_pct < percentile_75(atr_pct_60d)` (no whipsaw zone)
- Optional: `breadth_ecosystem >= GREEN` per indici

**Stop**:
- Inizio: `entry - 2.5 × ATR14`
- **Trailing**: Chandelier Exit = `max_since_entry - 3 × ATR14`, aggiornato ogni bar

**Target**:
- Caso ideale: nessun target fisso, esce solo su trailing → cavalca il trend
- Fallback (se trailing non disponibile): `entry + 3 × |entry - stop|`

**Position size**: 1% equity. Volatility-adjusted: size inversamente proporzionale ad ATR per normalizzare il rischio dollaro tra ticker.

**Exit**: trailing chandelier / stop iniziale / forced exit se breadth ecosystem → DARK_RED.

**Strengths**:
- Accademicamente solido (Turtle, Covel, Clenow)
- Ottimo su indici trending (NAS100, SPX500)
- Pochi segnali ma ogni trade può avere RR 5:1 o 10:1

**Weaknesses**:
- Senza trailing stop, l'idea perde gran parte del suo edge (fixed 2R è subottimale)
- Alto drawdown psicologico in periodi di whipsaw
- Richiede disciplina: win rate basso (~35%) compensato da grandi winner

**Effort**: ★★★☆☆ (★★ senza trailing)
**Lacune**:
- Rolling N-day high (trivial, 1 riga con `.rolling().max()`)
- **Trailing stop ATR/chandelier** in `backtest/engine.py::run_backtest` (necessario per valutare l'idea in modo realistico)

---

### Idea 5 — Weekly Director + Daily Momentum Trigger (3-level)

**Rationale**: architettura MTF istituzionale. Il trend settimanale determina il bias, il daily filter conferma la struttura, l'entry avviene su trigger di momentum daily (RSI cross up 50 o SMA20 cross up).

**Timeframes**: 3-level `1wk / 1d / 1d` (director weekly, filter daily, executor daily).

**Indicatori**: SMA10/20/50 weekly + daily, RSI14 daily, ATR14 daily.

**Entry (LONG)**:
- **Weekly**: SMA20_weekly > SMA50_weekly, SMA20_weekly slope > 0
- **Daily filter**: `close_daily > sma50_daily`, `sma50_daily_slope >= 0`
- **Daily trigger**: RSI14 cross up da < 45 a > 50 **oppure** `close > sma20 && prev_close <= prev_sma20` (SMA cross-up)

**Filtri**:
- Ecosystem: BULL/GREEN per US stocks/indici
- Correlation filter (già presente): max 2 per settore

**Stop**: `min(swing_low_10d, entry - 2.5 × ATR14_daily)`
**Target**: RR 3.0 (swing più ampio → target più lontano) **oppure** exit su `close < sma20_daily` per 3 bar.

**Position size**: 1% equity × ecosystem_multiplier.

**Exit**: stop / target / forced exit su weekly SMA20 slope flip a negativo.

**Strengths**:
- Massima qualità del segnale per via della tripla conferma TF
- Timing di entry nitido (momentum trigger)
- Holding medio 2–4 settimane → ottimo per cBot a bassa frequenza

**Weaknesses**:
- Richiede weekly data support — gap infrastrutturale
- Frequenza ulteriormente ridotta (~5–15 segnali/anno/ticker)
- Out-of-sample validation più difficile (meno eventi storici)

**Effort**: ★★★☆☆
**Lacune**:
- **Weekly support**: scelta tra (a) aggiungere `"1wk": (1, "Week")` a `alpaca_provider.py::_INTERVAL_MAP` + test feed Alpaca weekly, oppure (b) resample lato `alignment.py` da `1d` (pandas `.resample("W-FRI").agg(OHLCV)`). Opzione (b) è più sicura/portabile.
- Estensione wiring 3-level con `executor.interval=1d` (oggi mai usato su 3-level).

---

### Idea 6 — Ecosystem Regime Long-Only Index

**Rationale**: sfrutta al massimo l'ecosystem_monitor già esistente. Gli indici US tendono a performare in modo stabile quando il contesto macro (VIX basso, sector ETF allineati, NAS100 breadth alta) è favorevole. Entry = regime BULL + breakout semplice. Zero nuovi indicatori.

**Timeframes**: 2-level `1d/1d` per NAS100/SPX500 o 3-level `1d/4h/4h` via Dukascopy.

**Indicatori**: SMA20/50, ATR14, RSI14 — tutti esistenti. Ecosystem = segnale primario.

**Entry (LONG)**:
- `ecosystem_state ∈ {GREEN, BRIGHT_GREEN}` (breadth > 0.6, VIX < soglia, sector score > 0.5)
- Trigger tecnico (uno dei due):
  - `close > max(close_prev_20)` (20d breakout)
  - `close > sma20 && prev_close <= prev_sma20` (SMA20 cross-up)

**Filtri**:
- VIX NON in DARK_RED (oggi solo size multiplier → diventa entry gate)
- RSI14 < 75 (non comprare estremamente overbought)

**Stop**: `entry - 2.5 × ATR14`
**Target**: RR 2.5 **oppure** exit su degradazione ecosystem a GRAY.

**Position size**: 1% × ecosystem_multiplier (invariato).

**Exit**: stop / target / forced exit se ecosystem scende a RED/DARK_RED.

**Strengths**:
- Zero nuovo codice: ecosystem_monitor è già pronto e testato
- Naturale fit per gli indici US (ecosystem già applicato a `indices_futures`)
- Risk management già integrato (size multiplier)
- Frequenza moderata (20–40 segnali/anno/indice)

**Weaknesses**:
- Non applicabile fuori da US indici + US stocks
- Ecosystem_monitor su GER40 oggi ritorna NEUTRAL (servirebbe estensione con VSTOXX/DAX breadth)
- Dipende da reference data esterni (VIX fetch + sector ETF + NASDAQ snapshot)

**Effort**: ★☆☆☆☆ (NAS100/SPX500), ★★★☆☆ (per estendere a GER40)
**Lacune**: solo registrazione `variant_d` + profilo. Per GER40: snapshot DAX-40 + sector ETF EU (XLE.DE, ecc.) — deferibile.

---

### Idea 7 — MACD + EMA Ribbon Momentum

**Rationale**: classico momentum setup. Trend confermato dalla EMA50 (più reattiva della SMA50), entry su MACD histogram cross positivo allineato con close > EMA20. Copre un filone tecnico canonico non ancora presente nel sistema.

**Timeframes**: 2-level `1d/1d`.

**Indicatori**: **NUOVI EMA** (trivial, `ewm()` pandas) e **MACD** (diff di due EMA + signal EMA), SMA50, ATR14.

**Entry (LONG)**:
- `close > ema50` (trend up confermato)
- `macd_histogram > 0` e `macd_histogram_prev ≤ 0` (cross-up fresh)
- `close > ema20` (momentum short-term up)

**Filtri**:
- Director: EMA50 slope > 0 su daily
- Ecosystem: GREEN o superiore per stocks/indici US

**Stop**: `entry - 2.0 × ATR14` o `min(swing_low_5d)` (whichever più vicino)
**Target**: RR 2.5

**Position size**: 1% × ecosystem_multiplier.

**Exit**: stop / target / forced exit su MACD histogram cross sotto zero per 3 bar consecutivi.

**Strengths**:
- Copertura di una famiglia tecnica (MACD/EMA) oggi non rappresentata
- Entry timing nitido (istante del cross)
- Letteratura amplissima, facile da comunicare

**Weaknesses**:
- MACD è lagging; in trend molto rapidi l'entry è "late"
- Ricco di falsi cross in mercati laterali → il filtro EMA50 è essenziale
- Due indicatori nuovi da aggiungere e testare

**Effort**: ★★★☆☆
**Lacune**:
- `calc_ema(series, period)` in `core.py` (5 righe)
- `calc_macd(series, fast=12, slow=26, signal=9)` in `core.py` (10 righe, ritorna `macd/signal/histogram`)

---

### Idea 8 — Keltner Squeeze Breakout (TTM-style)

**Rationale**: momento di compressione della volatilità (Bollinger Bands **inside** Keltner Channel) seguito da breakout direzionale. Classico TTM Squeeze di John Carter. Eccellente per catturare transizioni range→trend su indici.

**Timeframes**: 2-level `1d/1d` (stocks) o 3-level `1d/4h/4h` (indici Dukascopy).

**Indicatori**: Keltner (esistente), **NUOVO Bollinger Bands** (`ma ± 2σ`), squeeze_flag (derivato).

**Entry (LONG)**:
- `squeeze_active_bars >= 6` negli ultimi 20 bar (BB dentro Keltner per almeno 6 bar consecutivi)
- Bar corrente: BB esce da Keltner (fine squeeze)
- Direzione: `close > keltner_upper` (break up) OR `close > ema20 && close > prev_high` (momentum up)

**Filtri**:
- Director: SMA50 slope > 0
- RSI14 ∈ [50, 72] (momentum up, non esausto)

**Stop**: `keltner_mid - 0.5 × ATR14` (sotto il midpoint del canale)
**Target**:
- 50% del size a `entry + 1.5 × |entry - stop|` (scale-out, move to BE)
- 50% runner con trailing chandelier `max_since_entry - 3 × ATR14`

**Position size**: 1% × ecosystem_multiplier.

**Exit**: stop / partial target + trailing.

**Strengths**:
- Cattura i setup ad alto RR (squeeze → trend explosion)
- Logica interpretabile visivamente (molto utile per debug)
- Ottimo su indici che alternano ranging e trending

**Weaknesses**:
- Molta complessità nuova (BB + squeeze detection + partial exits + trailing)
- False squeeze break in low-liquidity periods (es. agosto, dicembre)
- Difficile ottimizzare i parametri senza molti dati

**Effort**: ★★★★☆
**Lacune**:
- `calc_bollinger(series, period=20, std_mult=2.0)` in `core.py`
- `keltner_width_percentile` helper per detectare la compressione
- **Partial exits (scale-out)** nell'engine
- **Trailing stop chandelier** nell'engine

---

## 5. Tabella riepilogativa

| # | Idea | Asset | TF | Rationale short | Effort | Nuove dipendenze | Fit long |
|---|---|---|---|---|---|---|---|
| 1 | Daily SMA Pullback | Stocks + Indici | 1d/1d | Variant A traslata su daily | ★☆☆☆☆ | — | ✓✓ |
| 2 | 50/200 SMA Stage 2 | Stocks | 1d/1d | Minervini-style pullback in uptrend | ★☆☆☆☆ | `sma200` param | ✓✓✓ |
| 3 | Donchian Pullback | Stocks + Indici | 1d/1d (4h indici) | Pullback in canale 20d | ★★☆☆☆ | `calc_donchian` | ✓✓ |
| 4 | N-day High Breakout | Stocks + Indici | 1d/1d | Turtle-light momentum | ★★★☆☆ | Rolling high + trailing stop | ✓✓ |
| 5 | Weekly+Daily MTF | Stocks + Indici | 1w/1d/1d | Tripla conferma istituzionale | ★★★☆☆ | Weekly data support | ✓✓✓ |
| 6 | Ecosystem Regime Index | Indici US | 1d/1d (4h Duka) | Breadth+VIX gated breakout | ★☆☆☆☆ | — | ✓✓ |
| 7 | MACD + EMA Ribbon | Stocks + Indici | 1d/1d | Momentum cross classico | ★★★☆☆ | `calc_ema`, `calc_macd` | ✓✓ |
| 8 | Keltner Squeeze TTM | Indici + Stocks | 1d/1d (4h Duka) | Range→trend transition | ★★★★☆ | BB + squeeze + scale-out + trailing | ✓✓ |

---

## 6. Shortlist consigliata

### Tier 1 — implementabili subito (zero/minimo engine work)

1. **Idea 2 — 50/200 SMA Stage 2 Pullback**
   - Zero nuovo codice indicatori
   - Setup battle-tested su growth stocks → ottimo fit con la beta watchlist
   - Delivery stimata: 1 giornata (profilo + variant_d + scorer branch + backtest)

2. **Idea 6 — Ecosystem Regime Index**
   - Zero nuovi indicatori, zero nuove dipendenze
   - Valorizza l'ecosystem_monitor già costruito
   - Delivery stimata: 1 giornata per NAS100/SPX500

3. **Idea 3 — Donchian Pullback**
   - Un solo indicatore nuovo (~10 righe)
   - Signal distinto da A/B/C → buona diversificazione del portafoglio segnali
   - Delivery stimata: 1.5 giornate

### Tier 2 — dopo estensione engine

4. **Idea 4 — N-day High Breakout** — valorizzato pienamente solo con trailing stop
5. **Idea 8 — Keltner Squeeze** — richiede trailing + scale-out per essere realistico

### Tier 3 — richiedono lavoro infrastrutturale

6. **Idea 5 — Weekly MTF** — bloccata da weekly data support
7. **Idea 7 — MACD + EMA** — due indicatori nuovi da testare, signal classico ma non differenziante

---

## 7. Roadmap di integrazione (lacune di sistema)

Ordine per priorità, basato su quante idee ciascuno sblocca:

1. **Partial exits / scale-out** nel backtest engine
   Impatta: Idea 8 (direttamente), qualunque futura variante swing realistica.
   Dove: `backtest/engine.py::run_backtest`, campo `partial_exits: [(rr, fraction), ...]` nel profilo.

2. **Trailing stop ATR chandelier**
   Impatta: Idee 4, 8.
   Dove: `backtest/engine.py`, aggiornamento stop ogni bar = `max_since_entry - N × ATR14`.

3. **Indicatori mancanti in `core.py`**: EMA, MACD, Donchian, Bollinger Bands.
   Impatta: Idee 3, 7, 8. ~40 righe totali.

4. **Registrazione `variant_d`** (ripetibile per ogni nuova variante):
   - `VARIANTS = {"A", "B", "C", "D"}` in engine.py
   - Import + elif in `apply_variant_signals()`
   - Nuovo `signals/variant_d.py`
   - Chiave `rsi_filter.variant_d` nei profili
   - Branch D in `scorer.py::_rsi_quality()` e `_entry_precision()`

5. **Weekly data support**
   Impatta: Idea 5.
   Opzione A: aggiungere `"1wk": (1, "Week")` in `alpaca_provider.py::_INTERVAL_MAP`.
   Opzione B (consigliata): helper `resample_to_weekly(df)` in `data_feed/alignment.py` via `df.resample("W-FRI").agg(OHLCV)` — provider-agnostico.

6. **Time-based exit** (max holding bars) — opzionale, utile per evitare trade "morti" che restano aperti a lungo.

---

## 8. Domande aperte per l'utente

Prima di implementare, chiarire:

- **RR ratio per swing**: mantenere 2.0 (coerente con A/B/C) o alzare a 2.5–3.0 per compensare la minore frequenza?
- **Schema partial exits di riferimento**: preferenza per 50% a 1R + move to BE + 50% runner, oppure altro (es. 33/33/33 a 1R/2R/trailing)?
- **Max holding period**: implementare un cap in bar (es. 40 bar = 8 settimane di trading) o lasciarlo aperto?
- **Watchlist dedicata swing**: subset della beta (solo i ticker più trendy/growth) o riuso completo della beta esistente?
- **Priorità Tier 1**: partire da Idea 2 (stocks), Idea 6 (indici) o entrambe in parallelo?
- **Se si procede con Idea 5 (weekly)**: resample pandas lato `alignment.py` (consigliato) o estensione del provider Alpaca?

---

## 9. Prossimi passi

1. Utente sceglie 1–2 idee dalla shortlist (idealmente una per stocks + una per indici).
2. Risposta alle domande aperte → finalizzazione parametri.
3. Implementazione:
   - Eventuali estensioni engine (se richieste dall'idea scelta)
   - Nuovo `signals/variant_d.py`
   - Profilo(i) in `profiles.yaml`
   - Branch scoring in `scorer.py`
4. Backtest 2y + 3y su watchlist rilevante, export al dashboard, comparison con A/B/C.
5. Se i risultati sono solidi: wiring in `scan` per produzione Telegram.
