@echo off
:: ============================================================
:: Signal Farm — Beta Scan
:: Scansiona la watchlist beta e invia alert Telegram
:: Uso: doppio click oppure Task Scheduler ogni 30 minuti
::
:: Frequenza consigliata: ogni 30 min
::   - US Stocks girano su barre 30min → serve scan ogni 30min per non perdere segnali
::   - Forex/Indici/Metalli/Crypto girano su barre 1h → la dedup 12h evita reinvii duplicati
:: ============================================================

:: Imposta la directory di lavoro alla root del progetto
cd /d "%~dp0.."

:: Log con timestamp
set LOG_DIR=%~dp0..\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set LOG_FILE=%LOG_DIR%\scan_%date:~-4,4%%date:~-7,2%%date:~0,2%.log

echo. >> "%LOG_FILE%"
echo [%date% %time%] === SCAN START === >> "%LOG_FILE%"

:: Attiva il virtual environment
call "%~dp0..\.venv\Scripts\activate.bat"

:: Esegui la scansione con notifica Telegram
python signal_farm\main.py scan --watchlist beta --notify >> "%LOG_FILE%" 2>&1

if %ERRORLEVEL% == 0 (
    echo [%date% %time%] Scan completato con successo. >> "%LOG_FILE%"
) else (
    echo [%date% %time%] ERRORE: exit code %ERRORLEVEL% >> "%LOG_FILE%"
)

echo [%date% %time%] === SCAN END === >> "%LOG_FILE%"
