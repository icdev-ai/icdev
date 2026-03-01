# CUI // SP-CTI
# /production_check — Full Production Readiness Validation

Run all 8 production readiness test layers in dependency order. Each layer gates the next — if a blocking failure occurs, report it but continue to the next layer so the user gets a complete picture.

## Variables

PROJECT_DIR: . (auto-detected from cwd)
DASHBOARD_PORT: 5000

## Workflow

Use the TodoWrite tool to track progress across all 8 layers. Mark each layer as in_progress before starting and completed when done.

### Layer 1: Health Check

Run the system health check to verify the environment is properly configured.

```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/testing/health_check.py --json
```

Parse the JSON output. Report pass/fail counts. If critical failures (missing Python, missing DB), warn but continue.

### Layer 2: Claude Directory Governance

Validate .claude/ configuration alignment — append-only table protection, hook syntax, route documentation, E2E coverage.

```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/testing/claude_dir_validator.py --json
```

Parse JSON. Report each of the 9 checks with pass/fail. List any governance violations.

### Layer 3: Production Audit (42 Checks)

Run the full 42-check production audit across 7 categories: platform, security, compliance, integration, performance, documentation, code quality.

```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/testing/production_audit.py --json
```

Parse JSON. Report:
- Overall status (READY or BLOCKED)
- Pass/fail/warn/skip counts per category
- List all blockers (severity=blocking + status=fail)
- List all warnings

### Layer 4: Code Quality Analysis

Run the code quality analyzer on the tools/ directory to measure cyclomatic complexity, smell density, and maintainability.

```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/analysis/code_analyzer.py --project-dir tools/ --json
```

Parse JSON. Report:
- Total files analyzed
- Average cyclomatic complexity (blocking if > 25)
- Smell density per KLOC
- Maintainability score (0-1 scale)
- Top 5 most complex functions

### Layer 5: Unit Tests (pytest)

Run the full pytest suite. This is the largest test layer (~3,659 tests across 149 files).

```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -v --tb=short -q 2>&1
```

**IMPORTANT**: This will produce extensive output. Focus on:
- Total passed / failed / errors / skipped
- List any FAILED tests with their error messages
- Do NOT attempt to fix failing tests — just report them

If pytest is not installed, report skip and continue.

### Layer 6: BDD Tests (behave)

Run the BDD scenario tests against the feature files.

```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m behave features/ --no-capture 2>&1
```

Report:
- Total scenarios passed / failed / skipped
- List any failed scenarios with step details

If behave is not installed or no features/ directory, report skip and continue.

### Layer 7: Security Scanning

Run three security scans in parallel:

**7a. SAST (Bandit)**
```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m bandit -r tools/ -f json --severity-level medium -q 2>&1
```

**7b. Secret Detection**
```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/security/secret_detector.py --project-dir . --json 2>&1
```

**7c. Code Pattern Analysis**
```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/security/prompt_injection_detector.py --project-dir tools/ --gate --json 2>&1
```

Report findings by severity (critical/high/medium/low). List any critical or high findings.

### Layer 8: E2E Tests (Playwright)

**Prerequisite**: Dashboard must be running on port DASHBOARD_PORT. Check first:
```bash
python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health', timeout=2); print('RUNNING')" 2>/dev/null || echo "NOT_RUNNING"
```

If NOT_RUNNING, start it:
```bash
ICDEV_DASHBOARD_SECRET=test-secret-key nohup python tools/dashboard/app.py > .tmp/dashboard.log 2>&1 &
sleep 3
```

Then run E2E discovery and execution:
```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python tools/testing/e2e_runner.py --discover --json 2>&1
```

If E2E specs are found, use Playwright MCP to verify:
- Dashboard loads at http://localhost:5000
- CUI banner is visible
- Navigation works (click through 5+ pages)
- No 500 errors or tracebacks visible
- Take a screenshot of the home page

## Final Report

After all 8 layers complete, compile a consolidated report:

```
=== ICDEV PRODUCTION READINESS REPORT ===

Layer 1: Health Check          [PASS/FAIL] (X/Y checks passed)
Layer 2: Governance            [PASS/FAIL] (X/Y checks passed)
Layer 3: Production Audit      [PASS/FAIL] (X/Y checks passed, Z blockers, W warnings)
Layer 4: Code Quality          [PASS/FAIL] (complexity: X, maintainability: Y)
Layer 5: Unit Tests            [PASS/FAIL] (X passed, Y failed, Z skipped)
Layer 6: BDD Tests             [PASS/FAIL] (X scenarios passed, Y failed)
Layer 7: Security Scanning     [PASS/FAIL] (X critical, Y high, Z medium findings)
Layer 8: E2E Tests             [PASS/FAIL] (dashboard verified, X pages checked)

Overall: PRODUCTION READY / NOT READY
Blockers: [list if any]
Warnings: [list if any]
```

## Notes

- Each layer runs independently — failures in one layer do not skip subsequent layers
- pytest may take 5-10 minutes for the full suite; use --timeout flag if needed
- E2E tests require Playwright MCP and a running dashboard
- All results are advisory — the user decides whether blockers are acceptable
- Security scan findings should be triaged, not all are actionable
