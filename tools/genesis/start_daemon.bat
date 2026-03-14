@echo off
REM ICDEV Services — Windows Task Scheduler launcher
REM Starts Dashboard + Genesis Daemon via Python launcher.
REM Scheduled via: schtasks /create /tn "ICDEV Genesis Daemon" ...

cd /d "C:\Users\schuo\Downloads\ICDev"

REM Create log directories
if not exist ".tmp\genesis" mkdir ".tmp\genesis"

REM Kill any stale dashboard on port 5050
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5050" ^| findstr LISTENING') do (
    taskkill /PID %%a /F >nul 2>&1
)

REM Launch both services via Python (handles subprocess management)
title ICDEV Services
python tools\genesis\launcher.py
