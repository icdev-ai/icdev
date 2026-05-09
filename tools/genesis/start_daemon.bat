@echo off
REM CUI // SP-CTI
REM ICDEV™ Services — Windows launcher wrapper (cross-platform safe)
REM Derives project root from this script's own location — no hardcoded paths.
REM For full startup use: python tools\genesis\launch.py
REM To install as a scheduled task:  .\tools\genesis\install_scheduled_task.ps1

cd /d "%~dp0..\.."

REM Set environment
set ICDEV_GENESIS_ENABLED=true
set PYTHONIOENCODING=utf-8
set PYTHONDONTWRITEBYTECODE=1

REM Delegate to the cross-platform Python launcher
title ICDEV™ Services
python tools\genesis\launch.py %*
