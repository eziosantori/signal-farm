# Signal Farm — Guida Beta

## Cosa fa il sistema

Signal Farm scansiona una watchlist di strumenti finanziari, identifica segnali MTF (Multi-TimeFrame) e invia alert Telegram con entry, stop, target e score di qualità.

---

## Setup iniziale

### 1. Prerequisiti

- Python 3.10+
- Node.js 18+ (richiesto solo per backtest con `--provider dukascopy`)
- Dipendenze Python:

```
pip install -r requirements.txt
```

### 2. Configurare il file `.env`

Copia `.env.example` in `.env` e compila:

```
TELEGRAM_BOT_TOKEN=il_tuo_token
TELEGRAM_CHAT_ID=il_tuo_chat_id
ALPACA_API_KEY=la_tua_chiave      # per US stocks (paper key va bene)
ALPACA_SECRET_KEY=il_tuo_secret
```

**Come ottenere le credenziali Telegram:**
1. Crea un bot con [@BotFather](https://t.me/BotFather) → copia il token
2. Avvia una chat con il bot (o aggiungilo a un canale)
3. Visita `https://api.telegram.org/bot<TOKEN>/getUpdates` per trovare il chat ID

**Come ottenere le credenziali Alpaca:**
1. Registrati su [alpaca.markets](https://alpaca.markets)
2. Paper Trading account → API Keys → genera le chiavi
3. Le paper keys funzionano per i dati storici

---

## Watchlist Beta

La beta include **11 strumenti** selezionati su edge backtest 2y:

| Strumento | Tipo | Variant | Edge |
|-----------|------|---------|------|
| MSFT | US Stock | A | ✅ Sharpe 0.91 |
| TSLA | US Stock | A | ✅ Sharpe 0.98 |
| META | US Stock | A | ✅ Sharpe 0.74 |
| AMZN | US Stock | A | ✅ Sharpe 0.68 |
| NVDA | US Stock | A | ⚠️ Sharpe 0.31 |
| NAS100 | Indice | B | ✅ Sharpe 0.86 |
| GER40 | Indice | A | ✅ Sharpe 1.40 |
| XAUUSD | Oro | B | ✅ Sharpe 1.82 |
| EURUSD | Forex | B | ✅ Sharpe 0.57 |
| GBPUSD | Forex | B | ✅ Sharpe 0.76 |
| BTCUSD | Crypto | B | ⚠️ incluso per beta |

> AAPL e GOOGL sono esclusi: edge negativo/nullo nei backtest 2y.

---

## Uso quotidiano

### Scan manuale (test)

```
scripts\scan_beta_dryrun.bat
```

Mostra i segnali trovati e l'anteprima dei messaggi Telegram **senza inviare nulla**.

### Scan con notifiche

```
scripts\scan_beta.bat
```

Oppure da terminale:

```
python signal_farm/main.py scan --watchlist beta --notify
```

### Recap manuale

```
# Pre-sessione (cosa ho sul tavolo?)
python signal_farm/main.py recap --type open --dry-run

# Post-sessione (com'è andata?)
python signal_farm/main.py recap --type close --dry-run

# Week recap (analisi 2 settimane)
python signal_farm/main.py recap --type week --dry-run

# Storico segnali ultimi N ore
python signal_farm/main.py recap --last 48h --dry-run
```

Rimuovi `--dry-run` per inviare su Telegram.

---

## Automazione con Windows Task Scheduler

Apri **Task Scheduler** → *Create Task* e configura questi job:

### Scan ogni 30 minuti (core del sistema)

| Campo | Valore |
|-------|--------|
| Nome | Signal Farm — Beta Scan |
| Trigger | Daily, 00:00 → ripeti ogni **30 minuti** per 24h |
| Azione | `D:\Repos\signal-farm\scripts\scan_beta.bat` |
| Condizione | Deseleziona "Start only if on AC power" |

> **Perché 30 minuti e non 1 ora?**
> Gli US Stocks usano barre 30min — un segnale può formarsi e invecchiare in 30 minuti.
> Forex, Indici, Metalli e Crypto usano barre 1h: la deduplicazione (TTL 12h) impedisce
> automaticamente reinvii duplicati, quindi il secondo scan dell'ora è innocuo.
>
> Il sistema gestisce autonomamente i mercati chiusi: salta gli asset class non aperti.

### Pre-session brief (opzionale)

| Campo | Valore |
|-------|--------|
| Nome | Signal Farm — Open Brief |
| Trigger | Weekly, Lun-Ven, 15:15 UTC (09:15 ET) |
| Azione | `D:\Repos\signal-farm\scripts\recap_open.bat` |

### Post-session brief (opzionale)

| Campo | Valore |
|-------|--------|
| Nome | Signal Farm — Close Brief |
| Trigger | Weekly, Lun-Ven, 22:15 UTC (16:15 ET) |
| Azione | `D:\Repos\signal-farm\scripts\recap_close.bat` |

### Week recap (opzionale)

| Campo | Valore |
|-------|--------|
| Nome | Signal Farm — Week Recap |
| Trigger | Weekly, Lunedì, 14:00 UTC (09:00 ET) |
| Azione | `D:\Repos\signal-farm\scripts\recap_week.bat` |

---

## Log

Tutti gli script scrivono in `logs/` nella root del progetto:
- `logs/scan_YYYYMMDD.log` — un file per giorno con output di ogni scan
- `logs/recap.log` — log cumulativo dei recap inviati

---

## Troubleshooting

**"TELEGRAM_BOT_TOKEN not set"**
→ Verifica che il file `.env` esista nella root del progetto e contenga le variabili corrette.

**"No tickers to scan"**
→ Tutti i mercati sono chiusi. Usa `--no-skip-closed` per forzare la scansione.

**Scan lento su forex/indici/metalli**
→ Normale al primo run su yfinance: scarica e mette in cache i dati recenti. Le run successive sono più veloci.

**Dukascopy fallisce con "npx not found"**
→ Dukascopy è usato solo per i backtest (`--provider dukascopy`), non per lo scan live.
  Se serve, installa Node.js da [nodejs.org](https://nodejs.org) e riavvia il terminale.

**Score sempre bassi o nessun segnale**
→ Normale in mercati laterali o choppy. Il sistema è selettivo per design — meglio nessun segnale che segnali di bassa qualità.

---

## Struttura dei file

```
signal_farm/
  main.py                  ← entry point CLI
  config/
    profiles.yaml          ← parametri per asset class
    instruments.yaml       ← catalog strumenti (feed, yfinance ticker, best_variant, edge)
    watchlists.yaml        ← watchlist completa + named_watchlists (beta, ecc.)
  signals/
    engine.py              ← logica segnali MTF
    scanner.py             ← scan watchlist
  notifier.py              ← invio Telegram + deduplicazione
  recapper.py              ← history log + brief/recap

scripts/
  scan_beta.bat            ← scan con notifiche (uso produzione)
  scan_beta_dryrun.bat     ← scan preview (test)
  recap_open.bat           ← pre-session brief
  recap_close.bat          ← post-session brief
  recap_week.bat           ← week recap

logs/                      ← creata automaticamente dagli script
.signal_farm_state.json    ← stato deduplicazione segnali (12h TTL)
.signal_farm_history.jsonl ← storico completo segnali inviati
```
