@echo off
REM ICDEV Services — Windows Task Scheduler launcher
REM Starts Dashboard + Genesis Daemon via Python launcher.
REM Survives reboot via: install_scheduled_task.ps1

cd /d "C:\Users\schuo\Downloads\ICDev"

REM Set environment
set ICDEV_GENESIS_ENABLED=true
set PYTHONPATH=C:\Users\schuo\Downloads\ICDev
set PYTHONIOENCODING=utf-8

REM Create log directories
if not exist ".tmp\genesis" mkdir ".tmp\genesis"

REM Kill any stale dashboard on port 5050
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5050" ^| findstr LISTENING') do (
    taskkill /PID %%a /F >nul 2>&1
)

REM Start Ollama if not running (it's needed for qwen3.5 scanner-tier LLM)
tasklist /fi "imagename eq ollama.exe" 2>nul | findstr /i "ollama.exe" >nul
if errorlevel 1 (
    echo Starting Ollama...
    start "" "C:\Users\schuo\AppData\Local\Programs\Ollama\ollama app.exe"
    timeout /t 10 /nobreak >nul
)

REM Launch both services via Python (handles subprocess management + auto-restart)
title ICDEV Services
python tools\genesis\launcher.py
