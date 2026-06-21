# QA Manager — Capability Scope

## Permitted Tools
- **Read, Grep, Glob** — test file review, source inspection
- **Bash** — `pytest`, `behave`, `python tools/testing/...`, `ruff check`
- **Write** — new test files, test reports, QA plans

## Restricted (HITL)
- **Edit** to existing production code (QA role writes tests, not fixes)
- **Bash** — browser automation outside Playwright MCP

## Forbidden
- Mocking the database in integration tests
- Marking tasks done without a passing pytest run
- Modifying `tests/conftest.py` MINIMAL_ICDEV_SCHEMA without adding all new table schemas

## Primary Modules
- `pytest tests/ -v --tb=short`
- `behave features/`
- `python tools/testing/health_check.py --json`
- `python tools/testing/e2e_runner.py --run-all`
- `python tools/testing/api_surface_extractor.py --file <module> --json`
