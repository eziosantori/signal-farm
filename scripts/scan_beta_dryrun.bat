@echo off
:: ============================================================
:: Signal Farm — Beta Scan DRY RUN
:: Preview dei segnali e messaggi Telegram senza invio
:: Uso: test manuale prima di attivare le notifiche reali
:: ============================================================

cd /d "%~dp0.."

echo.
echo ============================================================
echo  Signal Farm Beta — DRY RUN
echo  (nessun messaggio Telegram verra inviato)
echo ============================================================
echo.

python signal_farm\main.py scan --watchlist beta --no-skip-closed --dry-run

echo.
pause
