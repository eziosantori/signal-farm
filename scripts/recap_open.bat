@echo off
:: ============================================================
:: Signal Farm — Pre-Session Brief
:: Invia il recap dei segnali attivi su Telegram
:: Schedulare: lunedi-venerdi alle 09:15 ET (15:15 UTC)
:: ============================================================

cd /d "%~dp0.."

call "%~dp0..\.venv\Scripts\activate.bat"

set LOG_DIR=%~dp0..\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [%date% %time%] Recap open inviato >> "%LOG_DIR%\recap.log" 2>&1
python signal_farm\main.py recap --type open >> "%LOG_DIR%\recap.log" 2>&1
