# Spec: `tools/testing/e2e_runner.py`

_OPT-75 Phase 1 clean-room spec._

## Purpose

CLI driver for ICDEV's end-to-end browser tests. Supports two execution
modes:

* **native** — runs `.spec.ts` files under `tests/e2e/` directly via
  `npx playwright test --reporter json`.
* **mcp** — runs markdown specs under `.claude/commands/e2e/*.md` via
  Claude Code with the Playwright MCP server.

Optionally feeds captured screenshots through a vision model for
assertion validation (Phase 23).

## CLI

```
python tools/testing/e2e_runner.py [--mode native|mcp|auto] \
    [--test-file PATH] [--run-all] [--discover] [--run-id ID] \
    [--project chromium] [--json] \
    [--validate-screenshots [--vision-assertions ASSERT ...] [--vision-strict]]
```

* `--mode auto` (default) prefers native if Playwright is installed
  and `tests/e2e/*.spec.ts` exists; otherwise falls back to mcp.
* `--discover` lists every test file the resolved mode can find.
* `--run-all` runs every test in the resolved mode.
* `--test-file` runs a single test (`.spec.ts` always uses native;
  `.md` always uses mcp).
* `--project` selects the Playwright browser project name.

## Public functions

### Discovery

* `discover_e2e_tests(mode="auto") -> list[str]`
  - native → sorted glob of `tests/e2e/*.spec.ts`
  - mcp → sorted glob of `.claude/commands/e2e/*.md`
  - auto → native if any, else mcp.
* `discover_native_tests() -> list[str]`
* `discover_mcp_tests() -> list[str]`

### Spec parsing

* `parse_test_spec(test_file: str) -> dict`
  Reads the file, builds a dict with `name`, `file`, `description`,
  `steps`, `assertions`, `raw_content`. The first heading or comment
  line of length > 5 chars becomes the description. Lines containing
  any verb in a small allowlist (navigate/click/fill/...) are added
  to `steps`; lines containing assertion verbs (assert/verify/expect/
  toContain/toBeVisible/toHaveTitle) are added to `assertions`.

### Playwright availability

* `check_playwright_installed() -> bool`
  Runs `<npx> playwright --version` (15s timeout, swallows
  FileNotFoundError + TimeoutExpired).

### Native execution

* `run_playwright_native(run_id, logger, test_file=None, project="chromium") -> list[E2ETestResult]`
  Builds `npx playwright test --project <project> --reporter json
  [<test_file>]`, sets `PLAYWRIGHT_JSON_OUTPUT_NAME` to a per-run
  results file, runs with a 300-second timeout. Parses results from
  the JSON file (or stdout) into `E2ETestResult` objects. Falls back
  to a single PASS/FAIL based on the exit code if no JSON is parseable.
  Handles `TimeoutExpired` and `FileNotFoundError` gracefully.

### MCP execution (legacy)

* `execute_e2e_test(test_file, run_id, logger) -> E2ETestResult`
  Parses the spec, decides whether Claude Code is available
  (CLAUDE_CODE_PATH binary exists AND `ANTHROPIC_API_KEY` is set):
  - Yes → `_execute_via_claude(...)`
  - No → `_validate_spec(...)` (returns passed iff steps and
    assertions are non-empty)

* `_execute_via_claude(test_file, test_name, run_id, screenshot_dir, logger) -> E2ETestResult`
  Uses `tools.testing.utils.get_safe_subprocess_env()`. Constructs a
  prompt that asks Claude Code (sonnet) to drive the Playwright MCP
  through the spec, save screenshots to the per-test directory, and
  return a JSON object with the documented keys. Wraps the call in a
  120-second timeout. On JSON parse failure, returns a failure result
  with the exit code + stderr snippet.

* `_validate_spec(spec, test_name, test_file, logger) -> E2ETestResult`
  Returns failure if `steps` or `assertions` are empty; passed
  otherwise.

### Vision validation (Phase 23)

* `_run_vision_validation(results, logger, assertions=None, strict=False) -> list[E2ETestResult]`
  Lazy-imports `tools.testing.screenshot_validator`. Skips with a
  warning if the module or vision model is unavailable. For every
  screenshot in every result, runs each assertion through the
  validator and stores the dicts in `result.vision_analysis`. When
  `strict=True`, vision failures flip `result.status` to "failed".

### CLI

* `main(argv=None) -> int`
  Standard argparse, resolves `mode`, dispatches to discover / run-all
  / single-file / help. Returns 0 on success, 1 on any failure.

## Bugs to fix during rewrite

1. **`args.project_id` typo.** The original references
   `args.project_id` in `--run-all` and single-file branches but only
   declares `--project`. Both branches crash on actual use. The rewrite
   must read from `args.project`.
2. **`screenshot_validator` import naming.** The vision validator
   import is brittle to circular imports — guard against the entire
   path being unavailable, not just the inner attribute.

## Forbidden

* No DB writes.
* No network calls outside the underlying Playwright/Claude
  subprocess.

## Acceptance

Tests cover:

* `discover_e2e_tests` returns the correct file lists per mode using
  a fake project root.
* `parse_test_spec` extracts the right description and counts steps
  vs. assertions.
* `check_playwright_installed` returns False on `FileNotFoundError`.
* `_parse_playwright_report` builds `E2ETestResult` objects from a
  fixture.
* `_validate_spec` returns success for non-empty steps + assertions.
* `_validate_spec` returns failure when steps or assertions are
  empty.
* `_run_vision_validation` no-ops when the validator module is missing.
* `_run_vision_validation` flips status to "failed" in strict mode.
* `main(["--discover", "--mode", "native"])` lists native tests.
* `main(["--run-all"])` exits 0 on a stubbed all-pass run, 1 on a
  failure.
* The `args.project` typo regression test (single-file invocation
  must not crash with `AttributeError`).
