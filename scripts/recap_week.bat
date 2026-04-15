@echo off
:: ============================================================
:: Signal Farm — Week Recap
:: Invia l'analisi settimanale su Telegram
:: Schedulare: ogni lunedi alle 08:00 ET (14:00 UTC)
:: ============================================================

cd /d "%~dp0.."

set LOG_DIR=%~dp0..\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo [%date% %time%] Week recap inviato >> "%LOG_DIR%\recap.log" 2>&1
python signal_farm\main.py recap --type week >> "%LOG_DIR%\recap.log" 2>&1
