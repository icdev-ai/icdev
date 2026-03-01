# Phase 12 — Integration Testing

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 12 |
| Title | Integration Testing |
| Status | Implemented |
| Priority | P0 |
| Dependencies | Phase 5 (TDD Workflow), Phase 7 (Security Scanning), Phase 6 (Compliance Workflow) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Individual test layers (unit tests, BDD scenarios, security scans) each provide valuable signal, but none alone can validate that an ICDEV-built application is production-ready for Gov/DoD deployment. A project can pass all unit tests while having critical STIG findings. It can pass security scans while missing CUI markings. It can pass compliance checks while having no E2E browser validation that the user interface actually works.

Gov/DoD ATO (Authority to Operate) requires evidence that systems have been tested at multiple levels: unit, integration, system, and acceptance. NIST 800-53 SA-11 (Developer Testing and Evaluation) and SA-15 (Development Process, Standards, and Tools) mandate comprehensive test coverage with documented results. The test pipeline must produce auditable evidence for each layer, not just a pass/fail summary.

The integration testing framework orchestrates a 9-step pipeline that runs every test layer in sequence, evaluates security and compliance gates, retries failures with resolution attempts, and produces a CUI-marked summary report suitable for ATO evidence packages. It adopts key patterns from the ADW (Agent Development Workflow): Pydantic data types for structured results, `parse_json()` for markdown-wrapped JSON, retry-with-resolution logic, fail-fast E2E execution, and `stdin=DEVNULL` for subprocess safety.

---

## 2. Goals

1. Orchestrate a 9-step testing pipeline: py_compile, Ruff linting, pytest unit tests, behave BDD tests, Bandit SAST, Playwright E2E tests, vision validation, acceptance validation, and security+compliance gates
2. Provide structured test results using Pydantic data types (TestResult, E2ETestResult, CheckResult) with consistent JSON serialization
3. Implement retry-with-resolution logic (max 4 unit test retries, max 2 E2E retries) that stops early when no progress is detected between retries
4. Execute E2E tests via native Playwright CLI (preferred) with MCP fallback, capturing screenshots and video recordings for audit evidence
5. Evaluate security gates (0 HIGH SAST findings, 0 secrets detected) and compliance gates (CUI markings present, 0 CAT1 STIG findings, SBOM current)
6. Generate CUI-marked test summary reports saved to `.tmp/test_runs/{run_id}/summary.md` with per-layer results
7. Support optional vision validation using LLM-based screenshot analysis (CUI banner verification, error detection, content verification)
8. Record all test events in the append-only audit trail satisfying NIST SA-11, SA-15, and CM-3

---

## 3. Architecture

### 9-Step Testing Pipeline

```
Step 1: py_compile          (Python syntax validation)
    |
Step 2: Ruff                (Ultra-fast linting — replaces flake8+isort+black)
    |
Step 3: pytest              (Unit/integration tests with coverage)
    |
Step 4: behave/Gherkin      (BDD scenario tests for business requirements)
    |
Step 5: Bandit              (SAST security scan)
    |
Step 6: Playwright E2E      (Browser automation — native CLI or MCP fallback)
    |
Step 7: Vision Validation   (Optional LLM-based screenshot analysis)
    |
Step 8: Acceptance          (Deterministic acceptance criteria verification)
    |
Step 9: Security +          (CUI markings, STIG, secrets, SBOM gates)
        Compliance Gates
    |
    v
  Summary Report (CUI-marked, saved to .tmp/test_runs/{run_id}/)
```

### Key ADW Patterns

| Pattern | Purpose |
|---------|---------|
| Pydantic data types | Structured, validated TestResult/E2ETestResult/CheckResult models |
| parse_json() | Handles markdown-wrapped JSON output from Claude Code |
| Retry with resolution | Max 4 unit / max 2 E2E retries; stop if no progress |
| Fail-fast E2E | Stop on first E2E failure (sequential execution) |
| Health check gating | Validate environment before test execution |
| Dual logging | File (DEBUG) + console (INFO) for audit traceability |
| Safe subprocess env | Filter environment variables for subprocess security |
| stdin=DEVNULL | Prevent Claude Code hanging in subprocess |

---

## 4. Requirements

### 4.1 Pipeline Orchestration

#### REQ-12-001: 9-Step Sequential Pipeline
The system SHALL execute a 9-step testing pipeline in sequence: py_compile, Ruff, pytest, behave, Bandit, Playwright E2E, vision validation, acceptance validation, security+compliance gates.

#### REQ-12-002: Health Check Pre-Validation
Before executing the test pipeline, the system SHALL validate environment variables, database connectivity, Python dependencies, tool availability, and MCP server configurations.

#### REQ-12-003: Run ID Tracking
Each test execution SHALL be assigned a unique run ID, with all artifacts (state, logs, screenshots, reports) stored under `.tmp/test_runs/{run_id}/`.

### 4.2 Unit and BDD Testing

#### REQ-12-004: pytest Execution
The system SHALL run pytest with verbose output and coverage reporting, parsing results into structured `TestResult` objects.

#### REQ-12-005: behave BDD Execution
The system SHALL run behave with JSON output, parsing Gherkin scenario results into `TestResult` objects with `test_type="bdd"`.

#### REQ-12-006: Retry with Resolution
The system SHALL retry failed tests up to 4 times (unit) or 2 times (E2E), stopping early if no progress is detected between retries.

### 4.3 E2E Testing

#### REQ-12-007: Dual-Mode E2E Execution
The system SHALL prefer native Playwright CLI (`npx playwright test`) when available and fall back to Playwright MCP via Claude Code CLI when native execution is not possible.

#### REQ-12-008: Screenshot and Video Capture
E2E tests SHALL capture screenshots to `.tmp/test_runs/{run_id}/screenshots/` and record video per configured viewport (1920x1080).

#### REQ-12-009: CUI Banner Verification
E2E tests SHALL verify CUI banners ("CUI // SP-CTI") are present on every tested page.

### 4.4 Vision Validation

#### REQ-12-010: Optional Vision Validation
The system SHALL optionally validate screenshots using a vision LLM (Ollama LLaVA, Claude, GPT-4o) to verify CUI banners, detect error states, and confirm content rendering.

### 4.5 Security and Compliance Gates

#### REQ-12-011: Security Gate Evaluation
The system SHALL evaluate security gates: 0 HIGH SAST findings (Bandit), 0 secrets detected, with thresholds configurable via `args/security_gates.yaml`.

#### REQ-12-012: Compliance Gate Evaluation
The system SHALL evaluate compliance gates: CUI markings present on all source files, 0 CAT1 STIG findings, SBOM current.

#### REQ-12-013: Acceptance Validation
The system SHALL verify acceptance criteria by mapping plan criteria to test evidence, checking rendered pages for error patterns (500, tracebacks, JS errors).

### 4.6 Reporting

#### REQ-12-014: CUI-Marked Summary Report
The system SHALL generate a CUI-marked test summary report with per-layer results (pass/fail counts), gate evaluations, screenshots, and overall pass/fail determination.

#### REQ-12-015: Audit Trail Recording
All test events SHALL be recorded in the append-only audit trail with event_type=test.complete, satisfying NIST SA-11, SA-15, and CM-3.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `audit_trail` | Append-only test completion events with run_id, counts, gate results |
| `sast_findings` | Bandit SAST findings per project per scan |
| `stig_findings` | STIG checklist findings (CAT1/CAT2/CAT3) per project |
| `sbom_records` | SBOM generation records with currency tracking |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/testing/test_orchestrator.py` | Full 9-step pipeline orchestration with retry and gate evaluation |
| `tools/testing/e2e_runner.py` | Native Playwright CLI + MCP fallback E2E test execution |
| `tools/testing/health_check.py` | Pre-pipeline environment validation (7 checks) |
| `tools/testing/data_types.py` | Pydantic models for TestResult, E2ETestResult, CheckResult |
| `tools/testing/utils.py` | JSON parsing (parse_json), logging setup, safe subprocess environment |
| `tools/testing/screenshot_validator.py` | Vision LLM screenshot analysis (CUI banners, errors, content) |
| `tools/testing/platform_check.py` | Platform compatibility validation (OS, dependencies) |
| `tools/security/sast_runner.py` | Bandit SAST execution for security gate |
| `tools/security/secret_detector.py` | Secret detection for security gate |
| `tools/compliance/stig_checker.py` | STIG findings evaluation for compliance gate |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D82 | Ollama LLaVA for air-gapped vision | Air-gap safe; vision is a message format concern, not a provider architecture concern |
| D145 | Platform compatibility module centralizes OS detection | Single source of truth for platform-specific behavior in test execution |
| D155 | Project-root conftest.py with shared fixtures | Centralizes test DB setup; prioritizes security-critical test paths |

---

## 8. Security Gate

**Integration Testing Gate:**
- All unit tests must pass (0 failures)
- All BDD scenarios must pass (0 failures)
- 0 HIGH or CRITICAL SAST findings (Bandit)
- 0 secrets detected in source code
- CUI markings present on all source files
- 0 CAT1 STIG findings
- SBOM current (generated within last build cycle)
- Acceptance criteria: 0 failed criteria, 0 pages with error patterns (500, tracebacks, JS errors)

---

## 9. Commands

```bash
# Full test pipeline
python tools/testing/test_orchestrator.py --project-dir /path/to/project
python tools/testing/test_orchestrator.py --project-dir /path --skip-e2e --project-id "proj-123"

# ICDEV platform tests
pytest tests/ -v --tb=short

# E2E tests
python tools/testing/e2e_runner.py --discover
python tools/testing/e2e_runner.py --run-all
python tools/testing/e2e_runner.py --run-all --validate-screenshots
python tools/testing/e2e_runner.py --run-all --validate-screenshots --vision-strict

# Screenshot validation
python tools/testing/screenshot_validator.py --check --json
python tools/testing/screenshot_validator.py --image screenshot.png --assert "CUI banner is visible" --json
python tools/testing/screenshot_validator.py --batch-dir .tmp/test_runs/screenshots/ --json

# Health check
python tools/testing/health_check.py
python tools/testing/health_check.py --json

# Platform check
python tools/testing/platform_check.py
python tools/testing/platform_check.py --json
```

**CUI // SP-CTI**
