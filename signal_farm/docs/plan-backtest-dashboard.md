# Backtest Dashboard — Piano di Implementazione

> Piano eseguibile da un LLM. Ogni fase ha input/output chiari, file da creare/modificare, e criteri di completamento.

## Obiettivo

Dashboard web locale per visualizzare risultati di backtesting:
1. **Grafici TradingView** (candlestick + indicatori + entry/exit) via Lightweight Charts
2. **Equity curve e balance** con drawdown overlay
3. **Statistiche complete** in pannello laterale
4. **Correlazione cross-asset** tra equity curve di diversi backtest
5. **Multi-backtest comparison** per confrontare ticker/varianti side-by-side

## Architettura

```
signal_farm/
├── dashboard/
│   ├── server.py              # FastAPI server locale (porta 8501)
│   ├── exporter.py            # Pipeline Python: backtest → JSON
│   ├── static/
│   │   ├── index.html         # SPA principale
│   │   ├── css/
│   │   │   └── dashboard.css
│   │   └── js/
│   │       ├── app.js           # Router / state management
│   │       ├── chart-price.js   # TradingView Lightweight Charts wrapper
│   │       ├── chart-equity.js  # Equity / drawdown curve (Chart.js)
│   │       ├── chart-stats.js   # Stats panel renderer
│   │       ├── chart-correlation.js  # Heatmap correlazione (Plotly.js)
│   │       └── api.js           # Fetch wrapper per /api/* endpoints
│   └── templates/
│       └── (vuoto — SPA pura, nessun template server-side)
├── output/
│   └── dashboard_data/        # JSON pre-generati dall'exporter
│       ├── MSFT_A_2y.json
│       ├── ...
│       └── correlation_matrix.json
```

### Librerie JS (tutte via CDN, zero npm/build step)

| Libreria | Versione | Uso |
|----------|---------|-----|
| [TradingView Lightweight Charts](https://github.com/nicehash/lightweight-charts) | 4.x | Candlestick + indicatori + marker entry/exit |
| [Chart.js](https://www.chartjs.org/) | 4.x | Equity curve, drawdown, bar chart distribuzioni |
| [Plotly.js](https://plotly.com/javascript/) | 2.x | Heatmap correlazione (solo pagina correlation) |

### Dipendenze Python (aggiungere a requirements.txt)

```
fastapi>=0.100
uvicorn>=0.22
```

`pandas`, `numpy`, `pyyaml` sono già presenti nel progetto.

---

## Formato dati JSON (contratto exporter → frontend)

### Single Backtest (`output/dashboard_data/{TICKER}_{VARIANT}_{PERIOD}.json`)

```json
{
  "meta": {
    "ticker": "MSFT",
    "asset_class": "us_stocks",
    "variant": "A",
    "variant_label": "Pullback",
    "period": "2y",
    "period_start": "2024-04-18",
    "period_end": "2026-04-18",
    "bars_count": 6800,
    "generated_at": "2026-04-18T14:30:00Z"
  },

  "metrics": {
    "total_trades": 56,
    "win_rate": 48.2,
    "avg_rr": 0.45,
    "profit_factor": 1.61,
    "max_drawdown_pct": -8.7,
    "total_return_pct": 19.6,
    "sharpe_ratio": 0.91,
    "wins": 27,
    "losses": 29,
    "avg_win_r": 1.82,
    "avg_loss_r": -0.85,
    "max_consecutive_wins": 5,
    "max_consecutive_losses": 4,
    "avg_trade_duration_bars": 12,
    "expectancy_r": 0.18
  },

  "ohlc": [
    {"t": 1713427200, "o": 414.10, "h": 415.80, "l": 413.20, "c": 415.50, "v": 12340},
    ...
  ],

  "indicators": {
    "sma_fast":       [{"t": 1713427200, "v": 413.5}, ...],
    "sma_slow":       [{"t": 1713427200, "v": 410.2}, ...],
    "keltner_upper":  [{"t": 1713427200, "v": 418.1}, ...],
    "keltner_mid":    [{"t": 1713427200, "v": 414.0}, ...],
    "keltner_lower":  [{"t": 1713427200, "v": 409.9}, ...],
    "rsi":            [{"t": 1713427200, "v": 55.3}, ...],
    "atr":            [{"t": 1713427200, "v": 2.15}, ...],
    "dir_slope":      [{"t": 1713427200, "v": 0.32}, ...],
    "dir_roc":        [{"t": 1713427200, "v": 1.8}, ...]
  },

  "trades": [
    {
      "entry_time": 1713513600,
      "exit_time": 1713772800,
      "direction": "LONG",
      "entry_price": 414.15,
      "exit_price": 420.30,
      "stop": 410.80,
      "target": 420.85,
      "pnl_r": 1.84,
      "pnl_pct": 1.48,
      "exit_reason": "target",
      "signal_score": 78,
      "score_trend": 35,
      "score_momentum": 22,
      "score_entry": 21
    },
    ...
  ],

  "equity": [
    {"t": 1713427200, "v": 100000},
    {"t": 1713430800, "v": 100000},
    ...
  ],

  "drawdown": [
    {"t": 1713427200, "v": 0.0},
    {"t": 1713430800, "v": -0.5},
    ...
  ]
}
```

**Note sul formato:**
- Tutti i timestamp `t` sono Unix epoch seconds (UTC) — Lightweight Charts li usa nativamente
- `ohlc` contiene solo dati executor (30m per stocks, 1h per forex/indices)
- `indicators` contiene solo le serie non-null (se Variant A, niente Keltner)
- `equity` e `drawdown` sono campionati a ogni barra (stessa frequenza di `ohlc`)

### Correlation Matrix (`output/dashboard_data/correlation_matrix.json`)

```json
{
  "tickers": ["MSFT_A", "META_A", "NFLX_A", "AMD_B", ...],
  "matrix": [
    [1.00, 0.72, 0.45, 0.38, ...],
    [0.72, 1.00, 0.51, 0.42, ...],
    ...
  ],
  "rolling_30d": {
    "dates": ["2024-06-01", "2024-06-02", ...],
    "pairs": {
      "MSFT_A|META_A": [0.65, 0.68, 0.71, ...],
      "MSFT_A|NFLX_A": [0.40, 0.38, 0.42, ...],
      ...
    }
  }
}
```

---

## Fasi di implementazione

### Fase 1 — Data Exporter (Python)

**File da creare:** `signal_farm/dashboard/exporter.py`

**Cosa fa:**
- Prende in input ticker, asset_class, variant, period
- Esegue `generate_signals()` + `run_backtest()` (stessa pipeline di `main.py backtest`)
- Calcola metriche estese (oltre a quelle base di `calc_metrics`)
- Serializza tutto nel formato JSON definito sopra
- Salva in `output/dashboard_data/`

**Metriche estese da calcolare** (oltre alle 7 già in `backtest/metrics.py`):
```python
# Queste vanno aggiunte in una funzione calc_extended_metrics()
# dentro dashboard/exporter.py, NON modificare backtest/metrics.py

wins = trade_log[trade_log["pnl_r"] > 0]
losses = trade_log[trade_log["pnl_r"] <= 0]

extended = {
    "wins": len(wins),
    "losses": len(losses),
    "avg_win_r": wins["pnl_r"].mean() if not wins.empty else 0,
    "avg_loss_r": losses["pnl_r"].mean() if not losses.empty else 0,
    "max_consecutive_wins": _max_consecutive(trade_log["pnl_r"] > 0),
    "max_consecutive_losses": _max_consecutive(trade_log["pnl_r"] <= 0),
    "avg_trade_duration_bars": (
        (trade_log["exit_time"] - trade_log["entry_time"])
        .dt.total_seconds().mean() / 1800  # 30m bars
    ),
    "expectancy_r": trade_log["pnl_r"].mean(),
}
```

**Funzione di export batch:**
```python
def export_batch(
    tickers: list[dict],  # [{ticker, asset_class, variant, period}, ...]
    output_dir: str = "output/dashboard_data",
):
    """Esporta tutti i backtest in JSON per la dashboard."""
```

**Funzione correlation matrix:**
```python
def export_correlation_matrix(
    json_dir: str = "output/dashboard_data",
    output_path: str = "output/dashboard_data/correlation_matrix.json",
):
    """
    Legge tutti i file *_equity.json dalla directory,
    allinea le equity curve per data, calcola la matrice
    di correlazione di Pearson sui rendimenti giornalieri.
    """
```

**Colonne da leggere da `signal_df` per gli indicatori:**
- `exec_open`, `exec_high`, `exec_low`, `exec_close` → OHLC
- `exec_sma_fast`, `exec_sma_slow` → SMA
- `exec_keltner_upper`, `exec_keltner_mid`, `exec_keltner_lower` → Keltner (solo B/C)
- `exec_rsi14` → RSI
- `exec_atr14` → ATR
- `dir_sma_fast_slope` → Director slope
- `dir_roc10` → Director ROC
- `signal`, `direction`, `entry_price`, `stop`, `target` → Signal markers

**CLI integration:**
Aggiungere a `main.py` un subcommand `dashboard`:
```
python main.py dashboard export --ticker MSFT --asset us_stocks --variant A --period 2y
python main.py dashboard export-beta --period 2y    # esporta tutti i ticker della beta watchlist
python main.py dashboard correlation               # calcola matrice correlazione
python main.py dashboard serve                     # avvia server locale sulla porta 8501
```

**Criterio di completamento:** i file JSON vengono generati in `output/dashboard_data/` e sono validi (parsabili, contengono tutti i campi).

**Dipendenze da codice esistente:**
- `signals.engine.generate_signals()` — genera signal_df
- `backtest.engine.run_backtest()` — genera trade_log + equity_curve
- `backtest.metrics.calc_metrics()` — metriche base
- `data_feed.provider_factory.get_provider()` — provider dati
- `signals.scanner.build_ticker_list()` — per export-beta

---

### Fase 2 — Server locale FastAPI

**File da creare:** `signal_farm/dashboard/server.py`

```python
"""
Dashboard server — serve la SPA statica + API JSON.

Avvio: python -m dashboard.server
       oppure: python main.py dashboard serve
"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import json, os, glob

app = FastAPI(title="Signal Farm Dashboard")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "output", "dashboard_data")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# API endpoints
@app.get("/api/list")
def list_backtests():
    """Restituisce la lista di tutti i backtest disponibili."""
    files = glob.glob(os.path.join(DATA_DIR, "*.json"))
    # Filtra correlation_matrix.json
    return [os.path.basename(f).replace(".json", "") for f in files
            if "correlation" not in f]

@app.get("/api/backtest/{name}")
def get_backtest(name: str):
    """Restituisce i dati di un singolo backtest."""
    path = os.path.join(DATA_DIR, f"{name}.json")
    with open(path) as f:
        return json.load(f)

@app.get("/api/correlation")
def get_correlation():
    """Restituisce la matrice di correlazione."""
    path = os.path.join(DATA_DIR, "correlation_matrix.json")
    with open(path) as f:
        return json.load(f)

# SPA fallback
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/{path:path}")
def spa_fallback(path: str):
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
```

**Criterio di completamento:** `python main.py dashboard serve` avvia il server su `http://localhost:8501`, la pagina index.html si carica, gli endpoint `/api/list` e `/api/backtest/{name}` rispondono con JSON valido.

---

### Fase 3 — Frontend: Pagina Single Backtest

**File da creare:**
- `signal_farm/dashboard/static/index.html`
- `signal_farm/dashboard/static/css/dashboard.css`
- `signal_farm/dashboard/static/js/app.js`
- `signal_farm/dashboard/static/js/api.js`
- `signal_farm/dashboard/static/js/chart-price.js`
- `signal_farm/dashboard/static/js/chart-equity.js`
- `signal_farm/dashboard/static/js/chart-stats.js`

#### `index.html` — Shell della SPA

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Signal Farm Dashboard</title>
  <link rel="stylesheet" href="/static/css/dashboard.css">
  <!-- CDN libs -->
  <script src="https://unpkg.com/lightweight-charts@4/dist/lightweight-charts.standalone.production.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
  <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
</head>
<body>
  <nav id="sidebar">
    <h2>Signal Farm</h2>
    <div id="backtest-list"></div>
    <hr>
    <a href="#" id="nav-correlation">Correlation Matrix</a>
  </nav>
  <main id="content">
    <section id="view-backtest" class="view">
      <div id="stats-panel"></div>
      <div id="price-chart"></div>
      <div id="indicator-panels"></div>
      <div id="equity-chart"></div>
      <div id="trade-table"></div>
    </section>
    <section id="view-correlation" class="view hidden">
      <div id="correlation-heatmap"></div>
      <div id="rolling-correlation"></div>
    </section>
  </main>
  <script type="module" src="/static/js/api.js"></script>
  <script type="module" src="/static/js/chart-price.js"></script>
  <script type="module" src="/static/js/chart-equity.js"></script>
  <script type="module" src="/static/js/chart-stats.js"></script>
  <script type="module" src="/static/js/chart-correlation.js"></script>
  <script type="module" src="/static/js/app.js"></script>
</body>
</html>
```

#### `chart-price.js` — TradingView Lightweight Charts

**Responsabilita:**
- Renderizza il grafico candlestick dall'array `ohlc`
- Aggiunge line series per ogni indicatore in `indicators` (SMA, Keltner, RSI)
- Aggiunge markers per entry/exit da `trades[]`
  - Entry LONG: triangolo verde in basso
  - Entry SHORT: triangolo rosso in alto
  - Exit target: cerchio verde
  - Exit stop: croce rossa
  - Exit forced: quadrato giallo
- Disegna linee orizzontali per stop/target durante la durata del trade
- RSI va in un pane separato sotto il candlestick (Lightweight Charts supporta pane multipli)

**API Lightweight Charts da usare:**
```javascript
const chart = LightweightCharts.createChart(container, options);
const candleSeries = chart.addCandlestickSeries();
candleSeries.setData(ohlcData);  // [{time, open, high, low, close}]
candleSeries.setMarkers(markers);  // [{time, position, color, shape, text}]

// Indicatori come line series overlay
const smaLine = chart.addLineSeries({color: 'royalblue', lineWidth: 1});
smaLine.setData(smaData);  // [{time, value}]

// RSI in pane separato
const rsiPane = chart.addLineSeries({
  color: 'purple', lineWidth: 1,
  pane: 1,  // pane separato
});
```

**Interattivita:**
- Crosshair sincronizzato tra price chart e sottopannelli
- Click su un marker → evidenzia la riga corrispondente nella trade table
- Zoom/pan con mouse (nativo di Lightweight Charts)

#### `chart-equity.js` — Equity Curve + Drawdown

**Responsabilita:**
- Grafico a 2 assi Y con Chart.js:
  - Asse sinistro: equity curve (area chart, blu)
  - Asse destro: drawdown % (area chart invertita, rosso, trasparenza 0.3)
- Marker sui punti di trade exit (verde = win, rosso = loss)
- Linea orizzontale a equity iniziale ($100k)
- Tooltip con: data, equity value, drawdown %, trade info se presente

#### `chart-stats.js` — Pannello Statistiche

Pannello a griglia nel lato destro, mostra:

| Sezione | Contenuto |
|---------|-----------|
| Performance | Sharpe, PF, Total Return, Max DD |
| Trades | Total, Wins, Losses, Win Rate |
| Risk | Avg Win R, Avg Loss R, Expectancy |
| Duration | Avg trade bars, Max consec wins/losses |
| Score | Avg score winners vs losers |

Sotto le metriche, un mini-bar chart (Chart.js) della distribuzione dei trade per:
- PnL in R-multiple (istogramma)
- Signal score distribution (istogramma)
- Exit reason breakdown (pie chart: stop/target/forced/end_of_data)

**Criterio di completamento:** selezionando un backtest dalla sidebar, il grafico candlestick mostra OHLC con indicatori e marker, l'equity curve si visualizza sotto, le statistiche appaiono nel pannello laterale. Tutti i dati vengono da `/api/backtest/{name}`.

---

### Fase 4 — Frontend: Pagina Correlation

**File da creare:** `signal_farm/dashboard/static/js/chart-correlation.js`

**Heatmap (Plotly.js):**
```javascript
Plotly.newPlot('correlation-heatmap', [{
  z: matrix,
  x: tickers,
  y: tickers,
  type: 'heatmap',
  colorscale: 'RdBu',
  zmin: -1, zmax: 1,
  text: matrix.map(row => row.map(v => v.toFixed(2))),
  texttemplate: '%{text}',
}], {
  title: 'Equity Curve Correlation (daily returns)',
  width: 800, height: 800,
});
```

**Rolling correlation (Chart.js):**
- Line chart con una linea per ogni coppia di ticker selezionata
- Finestra rolling 30 giorni
- Checkbox per selezionare/deselezionare coppie
- Default: mostra le 5 coppie con correlazione piu alta

**Criterio di completamento:** la pagina Correlation mostra la heatmap e il grafico rolling. Click su una cella della heatmap seleziona la coppia nel grafico rolling.

---

### Fase 5 — Multi-Backtest Comparison

**Aggiunta a `index.html`:** sezione `#view-compare` con:
- Dropdown multi-select per scegliere 2-6 backtest da confrontare
- Equity curves sovrapposte (Chart.js, una linea per backtest, colori distinti)
- Tabella comparativa delle metriche (le stesse di `chart-stats.js` ma affiancate)
- Normalizzazione opzionale: equity in % (base 100) per rendere confrontabili equity con scale diverse

**File da modificare:** `app.js` (aggiungere route `#compare`)

**Criterio di completamento:** si possono selezionare 2+ backtest e le equity curve si sovrappongono nello stesso grafico. La tabella metriche mostra i valori side-by-side.

---

### Fase 6 — Integrazione CLI

**File da modificare:** `signal_farm/main.py`

Aggiungere il subcommand `dashboard` con sotto-azioni:

```python
# ── dashboard ──
ds = sub.add_parser("dashboard", help="Backtest visualization dashboard")
ds_sub = ds.add_subparsers(dest="dashboard_action", required=True)

# export singolo
ds_export = ds_sub.add_parser("export", help="Export single backtest to JSON")
ds_export.add_argument("--ticker", required=True)
ds_export.add_argument("--asset", required=True)
ds_export.add_argument("--variant", required=True, choices=["A", "B", "C"])
ds_export.add_argument("--period", default="2y")
ds_export.add_argument("--direction", default=None)
ds_export.add_argument("--provider", choices=["auto", "yfinance", "dukascopy"], default="auto")

# export batch (tutta la beta watchlist)
ds_batch = ds_sub.add_parser("export-beta", help="Export all beta watchlist backtests")
ds_batch.add_argument("--period", default="2y")
ds_batch.add_argument("--direction", default=None)

# correlation
ds_corr = ds_sub.add_parser("correlation", help="Compute correlation matrix from exported data")

# serve
ds_serve = ds_sub.add_parser("serve", help="Start local dashboard server")
ds_serve.add_argument("--port", type=int, default=8501)
ds_serve.add_argument("--open", action="store_true", help="Open browser automatically")
```

**Workflow tipico per l'utente:**
```bash
# 1. Esporta tutta la beta watchlist
python main.py dashboard export-beta --period 2y --direction LONG

# 2. Calcola la matrice di correlazione
python main.py dashboard correlation

# 3. Avvia il server e apri il browser
python main.py dashboard serve --open
```

---

## Stile CSS

Tema scuro (coerente con Plotly dark template gia usato in `visualizer/charts.py`):

```css
:root {
  --bg-primary: #1a1a2e;
  --bg-secondary: #16213e;
  --bg-card: #0f3460;
  --text-primary: #e0e0e0;
  --text-secondary: #a0a0a0;
  --accent-green: #26a69a;
  --accent-red: #ef5350;
  --accent-blue: #4fc3f7;
  --border: #2a2a4a;
}
```

Layout: sidebar fissa a sinistra (250px), contenuto principale a destra con scroll. Grafici responsive (resize con finestra).

---

## Note per l'implementazione

### Cose da NON fare
- Non installare npm/webpack/vite — tutto via CDN + vanilla JS modules
- Non modificare `backtest/engine.py` o `backtest/metrics.py` — l'exporter fa le sue estensioni
- Non aggiungere ORM/database — JSON files sono sufficienti per la scala del progetto
- Non usare framework CSS (Bootstrap, Tailwind) — CSS custom minimale

### Cose da fare
- Usare ES modules (`import/export`) nei file JS
- Gestire correttamente i timestamp UTC → local nella visualizzazione
- Sanitizzare i nomi file (no caratteri speciali nei ticker)
- Gestire il caso in cui il JSON non esiste ancora (mostrare messaggio "Run export first")
- Il server deve funzionare su Windows (path con backslash → normalizzare)

### Relazione con `visualizer/charts.py` esistente
Il modulo `visualizer/charts.py` genera file HTML standalone con Plotly. La dashboard lo **sostituisce** per la visualizzazione interattiva, ma `charts.py` rimane funzionante per export rapido da CLI (`main.py chart`). Non serve toccarlo.

### Workspace / Worktree
L'implementazione puo avvenire su un branch dedicato (`feature/dashboard`) usando un git worktree per isolare le modifiche. I file da creare sono tutti nuovi (`dashboard/*`), l'unico file esistente da modificare e `main.py` (aggiunta subcommand). Nessun rischio di conflitto con il codice di segnali/backtest.

```bash
git worktree add ../signal-farm-dashboard feature/dashboard
```

---

## Ordine di esecuzione consigliato

| Step | Fase | Tempo stimato | Prerequisiti |
|------|------|--------------|-------------|
| 1 | Fase 1 — Exporter | 2-3h | Nessuno |
| 2 | Fase 2 — Server FastAPI | 30min | Fase 1 |
| 3 | Fase 6 — CLI integration | 1h | Fase 1+2 |
| 4 | Fase 3 — Single Backtest view | 4-5h | Fase 2 |
| 5 | Fase 4 — Correlation | 2-3h | Fase 1+3 |
| 6 | Fase 5 — Multi-comparison | 2-3h | Fase 3 |

**Totale stimato: 12-15 ore di lavoro LLM**

## Validazione finale

- [ ] `python main.py dashboard export --ticker MSFT --asset us_stocks --variant A --period 2y` genera JSON valido
- [ ] `python main.py dashboard export-beta --period 2y` esporta tutti i ticker della beta
- [ ] `python main.py dashboard correlation` genera `correlation_matrix.json`
- [ ] `python main.py dashboard serve --open` avvia il server e apre il browser
- [ ] Il grafico candlestick mostra OHLC + SMA + Keltner + RSI + entry/exit markers
- [ ] L'equity curve mostra la curva + drawdown overlay + marker trade
- [ ] Le statistiche sono complete e corrette (confrontare con output di `main.py backtest`)
- [ ] La correlation heatmap mostra la matrice colorata con valori
- [ ] La vista compare sovrappone 2+ equity curve normalizzate
- [ ] Tutto funziona su Windows 11 con Chrome/Edge
