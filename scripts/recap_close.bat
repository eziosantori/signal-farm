@echo off
:: ============================================================
:: Signal Farm — Post-Session Brief
:: Invia il recap di fine giornata su Telegram
:: Schedulare: lunedi-venerdi alle 16:15 ET (22:15 UTC)
:: ============================================================

cd /d "%~dp0.."

call "%~dp0..\.venv\Scripts\activate.bat"

set LOG_DIR=%~dp0..\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [%date% %time%] Recap close inviato >> "%LOG_DIR%\recap.log" 2>&1
python signal_farm\main.py recap --type close >> "%LOG_DIR%\recap.log" 2>&1
