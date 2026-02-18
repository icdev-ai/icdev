# Goal: Integration Testing (Phase 13)

## Purpose
Validate the complete ICDEV system through multi-layer testing: unit tests (pytest), BDD tests (behave/Gherkin), E2E browser tests (Playwright MCP), security gate evaluation, and compliance gate evaluation — with automatic retry and failure resolution.

## Trigger
- `/icdev-test` skill invoked
- Post-build validation in ATLAS workflow
- Pre-merge gate check
- Pre-deployment validation

## Inputs
- Project directory path
- Project UUID (optional, for compliance gates)
- Test orchestrator config (`args/project_defaults.yaml`)
- E2E test specs (`.claude/commands/e2e/*.md`)
- Playwright MCP config (`playwright-mcp-config.json`)

## Testing Architecture (Adapted from ADW)

### Test Layers
| Layer | Framework | Config | Purpose |
|-------|-----------|--------|---------|
| Unit | pytest | tests/ | Function-level correctness |
| BDD | behave (Gherkin) | features/ | Business requirement validation |
| E2E (native) | Playwright CLI | tests/e2e/*.spec.ts | Browser-based UI validation (preferred) |
| E2E (MCP) | Playwright MCP | .claude/commands/e2e/*.md | Browser-based UI validation (fallback) |
| Security | ICDEV security tools | security_gates.yaml | Vulnerability assessment |
| Compliance | ICDEV compliance tools | project_defaults.yaml | NIST 800-53 gate evaluation |

### Key ADW Patterns Adopted
1. **Pydantic data types** — TestResult, E2ETestResult, CheckResult (structured, validated)
2. **parse_json()** — Handles markdown-wrapped JSON output from Claude Code
3. **Retry with resolution** — Max 4 unit retries, max 2 E2E retries, stop if no progress
4. **Fail-fast E2E** — Stop on first E2E failure (sequential execution)
5. **Health check gating** — Validate environment before test execution
6. **Dual logging** — File (DEBUG) + console (INFO)
7. **Safe subprocess env** — Filter environment variables for subprocess security
8. **Playwright MCP** — Chromium headless, 1920x1080, video recording, screenshot capture
9. **stdin=DEVNULL** — Prevent Claude Code hanging in subprocess (E2B sandbox lesson)

## Process

### Step 1: Health Check
**Tool:** `tools/testing/health_check.py`
- Validate environment variables
- Check database connectivity (28 tables)
- Verify Python dependencies
- Check tool availability
- Validate MCP server configs
- Test Claude Code CLI

### Step 2: Unit Tests (pytest)
**Tool:** `tools/testing/test_orchestrator.py` → `run_pytest()`
- Run pytest with verbose output and coverage
- Parse results into `TestResult` objects
- Record results in audit trail
- **Gate:** All tests must pass

### Step 3: BDD Tests (behave)
**Tool:** `tools/testing/test_orchestrator.py` → `run_behave()`
- Run behave with JSON output
- Parse Gherkin scenario results
- Map to `TestResult` objects with `test_type="bdd"`
- **Gate:** All scenarios must pass

### Step 4: Retry Logic
**Tool:** `tools/testing/test_orchestrator.py` → `run_tests_with_resolution()`
- If failures detected, log failure details
- Retry up to `MAX_TEST_RETRY_ATTEMPTS` (4)
- Stop early if no progress between retries
- Each retry re-runs full suite

### Step 5: E2E Tests (Playwright Native + MCP Fallback)
**Tool:** `tools/testing/e2e_runner.py`
- **Native mode (preferred):** Run `tests/e2e/*.spec.ts` via `npx playwright test`
  - Auto-detected when Playwright CLI installed and `.spec.ts` files exist
  - JSON reporter output parsed into E2ETestResult objects
  - Supports Chromium, Firefox, WebKit (configurable via `--project`)
  - Config: `playwright.config.ts`
- **MCP mode (fallback):** Discover `.claude/commands/e2e/*.md` specs
  - Executed via Claude Code CLI + Playwright MCP
  - Used when native Playwright not installed
- Capture screenshots to `.tmp/test_runs/{run_id}/screenshots/`
- Record video per `playwright.config.ts` (native) or `playwright-mcp-config.json` (MCP)
- **Fail-fast:** Stop on first E2E failure
- Retry up to `MAX_E2E_TEST_RETRY_ATTEMPTS` (2)
- **CUI verification:** Check CUI banners on every page

### Step 6: Security Gate
**Tool:** `tools/testing/test_orchestrator.py` → `evaluate_security_gate()`
- Run SAST (bandit)
- Run secret detection
- Evaluate against security_gates.yaml thresholds
- **Gate:** 0 HIGH SAST findings, 0 secrets detected

### Step 7: Compliance Gate
**Tool:** `tools/testing/test_orchestrator.py` → `evaluate_compliance_gate()`
- Check CUI markings on all source files
- Verify STIG findings (0 CAT1)
- Verify SBOM currency
- **Gate:** All blocking gates must pass

### Step 8: Summary Report
Generate CUI-marked test report:
- Unit test results (pass/fail per test)
- BDD test results (pass/fail per scenario)
- E2E test results (pass/fail per spec, with screenshots)
- Security gate result
- Compliance gate result
- Overall pass/fail
- Save to `.tmp/test_runs/{run_id}/summary.md`

### Step 9: Audit Trail
**Tool:** `tools/audit/audit_logger.py`
- Record: event_type=test.complete
- Include: all counts, gate results, run_id
- **NIST Controls:** SA-11, SA-15, CM-3

## Testing Tools

| Tool | File | Purpose |
|------|------|---------|
| Data Types | tools/testing/data_types.py | Pydantic models for test results |
| Utilities | tools/testing/utils.py | JSON parsing, logging, safe env |
| Health Check | tools/testing/health_check.py | System validation (7 checks) |
| Test Orchestrator | tools/testing/test_orchestrator.py | Full test pipeline with retry |
| E2E Runner | tools/testing/e2e_runner.py | Native Playwright + MCP test execution |
| Playwright Config | playwright.config.ts | Playwright test runner configuration |
| E2E Specs | tests/e2e/*.spec.ts | Native Playwright test specifications |

## Configuration

### Playwright Native (playwright.config.ts)
```typescript
// Sequential execution for audit traceability, JSON + HTML reporters
// Supports chromium, firefox, webkit projects
// Screenshots on, video on, 1920x1080 viewport
// Output: .tmp/test_runs/playwright-results.json, .tmp/test_runs/playwright-report/
```

### Playwright MCP (playwright-mcp-config.json — fallback)
```json
{
  "browser": {"browserName": "chromium", "launchOptions": {"headless": true}},
  "contextOptions": {
    "recordVideo": {"dir": "./videos", "size": {"width": 1920, "height": 1080}},
    "viewport": {"width": 1920, "height": 1080}
  }
}
```

### MCP Server (.mcp.json — for MCP fallback mode)
```json
{
  "playwright": {
    "command": "npx",
    "args": ["@playwright/mcp@latest", "--isolated", "--config", "./playwright-mcp-config.json"]
  }
}
```

## Outputs
- Test run state: `.tmp/test_runs/{run_id}/state.json`
- Summary report: `.tmp/test_runs/{run_id}/summary.md`
- Execution log: `.tmp/test_runs/{run_id}/test_orchestrator/execution.log`
- Screenshots: `.tmp/test_runs/{run_id}/screenshots/`
- Videos: `.tmp/test_runs/playwright-artifacts/` (native) or `./videos/` (MCP)
- Playwright JSON: `.tmp/test_runs/{run_id}/playwright-results.json`
- Playwright HTML Report: `.tmp/test_runs/playwright-report/`
- pytest XML: `{project_dir}/test-results.xml`
- behave JSON: `{project_dir}/behave-results.json`
- Audit trail entry

## Edge Cases
- No tests found: report "no tests" and PASS (testing framework works, project needs tests)
- pytest not installed: skip unit tests with warning, continue to BDD/E2E
- behave not installed: skip BDD tests with warning, continue to E2E
- Playwright not installed: skip E2E with warning, continue to gates
- Native tests exist but Playwright CLI missing: fall back to MCP mode
- Health check fails: warn but continue (non-blocking)
- All retries exhausted: report final state, exit with failure code
- Claude Code not available: E2E runs in validation-only mode

## Related Goals
- `tdd_workflow.md` — TDD test generation (RED phase creates tests this goal runs)
- `code_review.md` — Uses gate results for merge decisions
- `security_scan.md` — Security tools invoked during security gate
- `compliance_workflow.md` — Compliance tools invoked during compliance gate
