# Testing Framework (Adapted from ADW)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Testing Framework (Adapted from ADW)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Test Data Types | tools/testing/data_types.py | Pydantic models: TestResult, E2ETestResult, GateResult, etc. | — | — |
| Test Utilities | tools/testing/utils.py | JSON parsing, dual logging, safe subprocess env, run ID gen | — | — |
| Health Check | tools/testing/health_check.py | System validation (env, DB, deps, tools, MCP, git, Claude, Playwright) | --json, --project-id | Health report |
| Test Orchestrator [DEPRECATED] | tools/testing/test_orchestrator.py | Full test pipeline: unit + BDD + E2E + gates with retry | --project-dir, --skip-e2e | Summary + state |
| E2E Runner | tools/testing/e2e_runner.py | E2E tests via native Playwright CLI or MCP fallback; `--driver selenium --run-all --include-scripts` also runs allowlisted standalone `tests/e2e_*.py` scripts (opt-in; default unchanged) | --test-file, --discover, --run-all, --mode, --validate-screenshots, --include-scripts | E2E results |
| E2E Script Allowlist | args/e2e_script_allowlist.yaml | Allowlist (importable) + excluded (broken-import, with reasons) of standalone `tests/e2e_*.py` selenium scripts; read by `e2e_runner --include-scripts`. Inventory + remediation feed: docs/testing/e2e-script-inventory.md | — | — |
| Full Dashboard E2E | tools/testing/e2e_full_dashboard.py | Selenium headless Chrome lifecycle test: every dashboard page, all canvases, nav links, API endpoints, chart rendering, kanban board, CUI banners, and JS error detection — sign-off gate after merges | python tools/testing/e2e_full_dashboard.py | Pass/fail counts + screenshots to playwright/screenshots/e2e-full/ |
| Screenshot Validator | tools/testing/screenshot_validator.py | Vision-based screenshot validation using LLM (Ollama LLaVA / Claude / GPT-4o) | --image, --assert, --batch-dir, --check | Pass/fail + explanation |
| Integration Smoke Test | tools/testing/smoke_test.py | Verify all CLI tools are importable and --help works after refactors | --json, --quick, --verbose | N tools tested, N passed |
| CLI Fuzz Test | tools/testing/fuzz_cli.py | Fuzz CLI tools with malformed inputs to catch crashes | --json, --tools, --discover | N tools fuzzed, 0 crashes |
| Acceptance Validator | tools/testing/acceptance_validator.py | V&V gate: validate plan acceptance criteria against test evidence + DOM content checks | --plan, --test-results, --base-url, --pages, --json | AcceptanceReport JSON |
| UI Analyzer | tools/modernization/ui_analyzer.py | Legacy UI screenshot analysis for 7R migration scoring | --image, --image-dir, --app-id, --store, --score-only | UI complexity score + analysis |
| Diagram Extractor | tools/mbse/diagram_extractor.py | Vision-based SysML diagram extraction from screenshots | --image, --diagram-type, --project-id, --store, --validate | Elements + relationships |
| Diagram Validator | tools/compliance/diagram_validator.py | Compliance diagram validation (SSP, network zone, ATO boundary) | --image, --type, --expected-components, --expected-zones | Pass/fail per check |
| Production Audit | tools/testing/production_audit.py | 30-check pre-production readiness audit across 6 categories (platform, security, compliance, integration, performance, documentation) | --json, --human, --stream, --gate, --category | AuditReport JSON + exit code |
| Production Remediate | tools/testing/production_remediate.py | Auto-fix audit blockers using 3-tier confidence model (auto-fix >= 0.7, suggest 0.3-0.7, escalate < 0.3) | --auto, --dry-run, --check-id, --category, --skip-audit, --json, --human, --stream | RemediationReport JSON + exit code |
| Stub Detector | tools/testing/stub_detector.py | 4-level verification & stub detection (GSD-adapted): EXISTS→SUBSTANTIVE→WIRED→FUNCTIONAL cascade, per-language stub patterns (6 languages), Python AST analysis, orphan detection, security gate (D-GSD-1 through D-GSD-3) | --file, --project-dir, --max-level, --project-id, --store, --gate, --json, --human | Verification results + gate |
| API Surface Extractor | tools/testing/api_surface_extractor.py | AST-based extraction of public API surface (functions, classes, dataclass fields, dict constants, imports, mock targets) — run BEFORE writing tests to prevent field name, return type, and mock path errors (D-API-1) | --file, --dir, --json, --human, --mock-targets, --include-private | API surface JSON or markdown |
| ACE Session Smoke Test | tools/testing/ace_session_smoke.py | Smoke test for ACE Session Replay UI pages: verifies `/coworker/sessions` page and `/api/ace/sessions` API return valid HTML/JSON, plus optional detail page checks. | `--url`, `--fast`, `--json` | Pass/fail JSON or human-readable report |
| Playwright Config | playwright.config.ts | Playwright test runner config (Chromium/Firefox/WebKit, video, screenshots) | — | — |
| E2E Test: Dashboard | tests/e2e/dashboard_health.spec.ts | Native Playwright test: dashboard CUI banners + navigation | npx playwright test | Pass/fail + screenshots |
| E2E Test: Compliance | tests/e2e/compliance_artifacts.spec.ts | Native Playwright test: compliance artifact display | npx playwright test | Pass/fail + screenshots |
| E2E Test: Security | tests/e2e/security_scan_results.spec.ts | Native Playwright test: security scan + audit trail display | npx playwright test | Pass/fail + screenshots |
| E2E Runner | tools/testing/e2e_runner.py | E2E tests via native Playwright CLI or MCP fallback | --test-file, --discover, --run-all, --mode, --validate-screenshots | E2E results |

