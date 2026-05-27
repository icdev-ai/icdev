@echo off
REM CUI // SP-CTI
REM Engineering Review Board — Windows Scheduled Task Runner
REM Runs all due reflexes in a single pass, then exits.
REM Internal scheduling (is_due) handles per-reflex intervals.
REM Derives project root from script location — no hardcoded paths.

cd /d "%~dp0..\.."

REM Quiet hours: skip if before 8 AM or after 11 PM
for /f "tokens=1 delims=: " %%h in ("%time%") do set HOUR=%%h
set HOUR=%HOUR: =%
if %HOUR% LSS 8 exit /b 0
if %HOUR% GEQ 23 exit /b 0

REM Enable the daemon for this run
set ICDEV_REVIEW_BOARD_ENABLED=true
set PYTHONIOENCODING=utf-8

REM Resolve Python executable (prefer pythonw for windowless, fall back to python)
for /f %%i in ('where pythonw 2^>nul') do set PYEXE=%%i
if not defined PYEXE (
    for /f %%i in ('where python 2^>nul') do set PYEXE=%%i
)
if not defined PYEXE set PYEXE=python

REM Ensure log directory exists
if not exist ".tmp\review_board" mkdir ".tmp\review_board"

REM Run single pass — daemon handles WAL mode and busy timeout internally
"%PYEXE%" tools\review_board\daemon.py --once --json >> ".tmp\review_board\scheduled_runs.log" 2>&1

REM Run auto-remediation on fixable findings
"%PYEXE%" tools\review_board\remediation_engine.py --run --json >> ".tmp\review_board\scheduled_runs.log" 2>&1

REM Compute and store health score
"%PYEXE%" tools\review_board\health_scorer.py --compute --json >> ".tmp\review_board\scheduled_runs.log" 2>&1

REM Sync to audit trail, cATO evidence, and NIST control mapping
"%PYEXE%" tools\review_board\compliance_bridge.py --sync --json >> ".tmp\review_board\scheduled_runs.log" 2>&1

REM Escalate critical/high unresolved findings to GitHub issues
"%PYEXE%" tools\review_board\escalation.py --run --json >> ".tmp\review_board\scheduled_runs.log" 2>&1

REM Autonomy Engine: learn from behavior, check federation, route signals
"%PYEXE%" tools\autonomy\behavior_learner.py --scan --json >> ".tmp\review_board\scheduled_runs.log" 2>&1
"%PYEXE%" tools\autonomy\federation.py --route --json >> ".tmp\review_board\scheduled_runs.log" 2>&1
