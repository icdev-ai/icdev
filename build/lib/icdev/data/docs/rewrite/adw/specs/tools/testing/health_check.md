# Spec: `tools/testing/health_check.py`

_OPT-75 Phase 1 clean-room spec._

## Purpose

A diagnostic CLI that runs eight checks against the ICDEV system and
reports a single aggregate health verdict. Used by operators to confirm
the install is wired up correctly.

## CLI

```
python tools/testing/health_check.py [--json] [--project-id <id>]
```

Exit codes: `0 = healthy`, `1 = unhealthy`.

## Public functions

Each `check_*` function returns `CheckResult` (from
`tools.testing.data_types`). The aggregate `run_health_check()` returns
`HealthCheckResult`.

### `check_env_vars() -> CheckResult`
Inspect required + optional env vars. The single required var is
`ICDEV_DB_PATH`; if it's unset, the check still passes when
`<repo>/data/icdev.db` exists. Optional vars are: `ANTHROPIC_API_KEY`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`,
`GITLAB_TOKEN`, `CLAUDE_CODE_PATH`. Missing optionals do not affect
the success bit.

### `check_database() -> CheckResult`
Resolve the DB path (`ICDEV_DB_PATH` or default), verify the file
exists, open a connection via `tools.db.storage.get_connection`, and
list every table. Compare against an `expected_tables` list and report
any missing. Return failure if the DB file is missing or any expected
table is absent.

### `check_python_deps() -> CheckResult`
Try to `importlib.import_module(...)` each of `sqlite3`, `pathlib`,
`json`, `argparse` (required) and `yaml`, `jinja2`, `flask`, `pytest`,
`behave`, `pydantic` (optional). Missing required → fail. Missing
optional → warning only.

### `check_tools() -> CheckResult`
Try to import a small probe set of ICDEV tool modules
(`tools.db.init_icdev_db`, `tools.audit.audit_logger`,
`tools.compliance.nist_lookup`, `tools.security.sast_runner`,
`tools.builder.scaffolder`). Pass if at least one module is
available; warn on the rest.

### `check_mcp_servers() -> CheckResult`
Open `<repo>/.mcp.json`, parse the `mcpServers` section. For each
server, treat it as valid if it has `command` AND `args`; if `command
== "python"`, additionally require that the script path
(`args[0]`) exists on disk. Return success when at least one server
parses cleanly. JSON parse errors → failure.

### `check_git_repo() -> CheckResult`
Run `git remote get-url origin`. Non-zero rc → success-with-warning
("no remote"). `FileNotFoundError` (git not on PATH) → failure.

### `check_claude_code() -> CheckResult`
If `ANTHROPIC_API_KEY` is unset, skip with a warning. Otherwise call
`<claude_path> --version` (10s timeout). Failure → return CheckResult
with the appropriate error.

### `check_playwright() -> CheckResult`
Use `tools.compat.platform_utils.get_npx_cmd()` to locate npx, then
run `<npx> playwright --version` (15s timeout). Missing or non-zero →
return success-with-warning. On success, also count
`tests/e2e/*.spec.ts` files and report `mode` as `"native"` when any
exist, otherwise `"mcp"`.

### `run_health_check() -> HealthCheckResult`
Run every check by name. Capture exceptions inside the runner so a
single crashing check doesn't kill the whole report. Aggregate
`success`, `errors`, `warnings`, and `checks` into the
`HealthCheckResult`.

### `main() -> int`
Argparse `--json` and `--project-id`. Print either a JSON document or
a pretty human-readable report. Return 0 when healthy, 1 otherwise.
The `--project-id` flag is reserved for future scoped checks; the
current behaviour is to ignore it.

## Forbidden

* No DB writes.
* No network calls outside the underlying CLI invocations.
* No `print()` other than the CLI's own report (the helpers use the
  caller's logger or just return CheckResult).

## Acceptance

Tests cover (with mocked subprocess + filesystem):

* `check_env_vars` happy path (icdev.db present), failure path (missing
  DB and no env var), warning path (optionals missing).
* `check_database` failure when the DB file is absent.
* `check_python_deps` happy path on the standard set.
* `check_tools` returns success-with-warning when most tool modules
  are missing but at least one is importable.
* `check_mcp_servers` parses a small in-memory `.mcp.json` correctly,
  flags missing scripts, and reports JSON parse errors.
* `check_git_repo` returns warning when no remote, failure when git is
  missing.
* `check_claude_code` skips cleanly without `ANTHROPIC_API_KEY`.
* `check_playwright` returns warning when npx is missing.
* `run_health_check` swallows a crashing check and still returns a
  `HealthCheckResult`.
* `main` exits 0/1 according to the aggregate verdict.
