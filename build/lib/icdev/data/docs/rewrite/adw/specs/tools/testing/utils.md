# Spec: `tools/testing/utils.py`

_OPT-75 Phase 1 clean-room spec. Written from external contract only._

## Purpose

A grab-bag of small utilities used by the ICDEV testing framework
(`tools/testing/*`). Six independent helpers — none of them depend on
each other and they share nothing but `PROJECT_ROOT`.

## Public surface (module-level functions)

### `make_run_id() -> str`
Return an 8-character lowercase hexadecimal identifier suitable for
labelling a test run. Implementation must use a UUID4 source so the
output is collision-resistant for the lifetime of a developer machine.

### `setup_logger(run_id: str, phase: str = "test_run") -> logging.Logger`
Build (or rebuild) a `logging.Logger` whose name follows the convention
`icdev_<run_id>_<phase>`. Behaviour:

* The logger writes to **two** sinks: a per-run file at
  `<repo>/.tmp/test_runs/<run_id>/<phase>/execution.log` (DEBUG level)
  and `sys.stdout` (INFO level).
* The log directory tree is created on first call.
* Calling `setup_logger` for the same `(run_id, phase)` twice does NOT
  duplicate handlers — any pre-existing handlers on that logger are
  cleared first.
* The file handler uses the format
  `%(asctime)s - %(levelname)s - %(message)s`. The console handler uses
  `%(message)s` only.
* The first messages emitted to the new logger record (a) the run id
  and phase at INFO and (b) the resolved log file path at DEBUG.

### `get_logger(run_id: str, phase: str = "test_run") -> logging.Logger`
Return the existing logger (`logging.getLogger(...)`) without
re-configuring handlers. Used by long-running test scripts that
already called `setup_logger`.

### `parse_json(text: str, target_type=None) -> Any`
Tolerant JSON extractor. Behaviour:

1. If `text` contains a fenced code block (` ```json ... ``` ` or
   bare ` ``` ... ``` `), extract the inner block first.
2. Otherwise strip whitespace and try to parse the whole string.
3. If the candidate isn't an object/array (`{...}` or `[...]`), find
   the *first* `{` or `[` and the *last* matching `}` or `]` and use
   that slice.
4. Call `json.loads` on the candidate.
5. If `target_type` is supplied:
   * If it's a parameterised generic with `__origin__ is list`, treat
     `target_type.__args__[0]` as the item model and validate every
     element. Item model can quack as either Pydantic v2
     (`model_validate`) or v1 (`parse_obj`).
   * If it's a single Pydantic-shaped class, validate the whole result.
6. Raise `ValueError` (NOT `JSONDecodeError`) on any parse failure,
   embedding a short snippet of the candidate text in the message.

### `get_safe_subprocess_env() -> dict[str, str]`
Return an environment dict for subprocess invocation that strips
unrelated variables and forwards only the ones the testing framework
needs. The dict must include (when defined in the host env):

* `ICDEV_DB_PATH` (default `<repo>/data/icdev.db`)
* `ICDEV_PROJECT_ROOT` (default `<repo>`)
* `ANTHROPIC_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
  `AWS_DEFAULT_REGION` (default `us-gov-west-1`)
* `CLAUDE_CODE_PATH` (default `claude`),
  `CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR` (default `"true"`)
* `GITLAB_TOKEN`, `GITLAB_URL`
* `HOME`/`USERPROFILE`, `USER`/`USERNAME`, `PATH`, `SHELL`, `TERM`,
  `LANG`
* Windows-specific: `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`,
  `SYSTEMROOT`, `TEMP`, `TMP`
* `GH_TOKEN` (fallback `GITHUB_PAT`)
* `PYTHONPATH`, `PYTHONUNBUFFERED` (always `"1"`)
* `PWD` (always the current working directory at call time)

After collection, drop any key whose value is `None` so callers can
use the dict directly with `subprocess.run(env=...)`.

### `timestamp_iso() -> str`
Return the current UTC timestamp formatted as an ISO 8601 string with
a literal trailing `Z`.

### `ensure_run_dir(run_id: str) -> Path`
Create (if necessary) and return `<repo>/.tmp/test_runs/<run_id>`.

## Constants

`PROJECT_ROOT = Path(__file__).resolve().parents[2]`
(`utils.py → testing → tools → repo`)

## Forbidden

* No DB writes (`tools.db.*` is off-limits).
* No LLM calls.
* No network calls.
* No `print()` for production output — everything goes through the
  configured logger.

## Acceptance

Tests must verify:

1. `make_run_id()` returns 8 lowercase hex chars and two consecutive
   calls disagree.
2. `setup_logger` writes to the file path under
   `.tmp/test_runs/<run_id>/<phase>/execution.log` and a re-call doesn't
   double the handler count.
3. `parse_json` round-trips raw JSON, fenced code-blocked JSON, and
   text-with-extra-prose containing one JSON object. It raises
   `ValueError` (not `JSONDecodeError`) on bad input.
4. `parse_json` validates a `target_type` Pydantic-shaped class via
   either `model_validate` or `parse_obj`.
5. `get_safe_subprocess_env` includes `PWD`, omits `None` values, and
   forwards `PATH` from the calling env when set.
6. `timestamp_iso()` is parseable by `datetime.fromisoformat` after
   stripping the trailing `Z`.
7. `ensure_run_dir(run_id)` creates the directory and returns the same
   path.
