@echo off
REM ── eMAG Scraper - Rulare automata ──
REM Acest script ruleaza emag_scraper.py si salveaza log-ul

cd /d "%~dp0"
set LOGFILE=%~dp0logs\emag_scraper_%date:~-4%%date:~3,2%%date:~0,2%.log

if not exist logs mkdir logs

echo [%date% %time%] Start eMAG scraper >> "%LOGFILE%"
"C:\Users\madal\AppData\Local\Programs\Python\Python312\python.exe" emag_scraper.py >> "%LOGFILE%" 2>&1
echo [%date% %time%] Done >> "%LOGFILE%"

REM Sterge log-uri mai vechi de 7 zile
forfiles /p "%~dp0logs" /m "emag_scraper_*.log" /d -7 /c "cmd /c del @path" >nul 2>&1
