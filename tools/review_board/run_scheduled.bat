@echo off
REM CUI // SP-CTI
REM Engineering Review Board — Windows Scheduled Task Runner
REM Runs all due reflexes in a single pass, then exits.
REM Internal scheduling (is_due) handles per-reflex intervals.

cd /d "C:\Users\schuo\Downloads\ICDev"

REM Quiet hours: skip if before 8 AM or after 11 PM
for /f "tokens=1 delims=: " %%h in ("%time%") do set HOUR=%%h
set HOUR=%HOUR: =%
if %HOUR% LSS 8 exit /b 0
if %HOUR% GEQ 23 exit /b 0

REM Enable the daemon for this run
set ICDEV_REVIEW_BOARD_ENABLED=true
set PYTHONIOENCODING=utf-8

REM Run single pass — daemon handles WAL mode and busy timeout internally
"C:\Python314\python.exe" tools\review_board\daemon.py --once --json >> ".tmp\review_board\scheduled_runs.log" 2>&1

REM Run auto-remediation on fixable findings
"C:\Python314\python.exe" tools\review_board\remediation_engine.py --run --json >> ".tmp\review_board\scheduled_runs.log" 2>&1

REM Compute and store health score
"C:\Python314\python.exe" tools\review_board\health_scorer.py --compute --json >> ".tmp\review_board\scheduled_runs.log" 2>&1

REM Sync to audit trail, cATO evidence, and NIST control mapping
"C:\Python314\python.exe" tools\review_board\compliance_bridge.py --sync --json >> ".tmp\review_board\scheduled_runs.log" 2>&1

REM Escalate critical/high unresolved findings to GitHub issues
"C:\Python314\python.exe" tools\review_board\escalation.py --run --json >> ".tmp\review_board\scheduled_runs.log" 2>&1

REM Autonomy Engine: learn from behavior, check federation, route signals
"C:\Python314\python.exe" tools\autonomy\behavior_learner.py --scan --json >> ".tmp\review_board\scheduled_runs.log" 2>&1
"C:\Python314\python.exe" tools\autonomy\federation.py --route --json >> ".tmp\review_board\scheduled_runs.log" 2>&1
