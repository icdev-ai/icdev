# Spec: `tools/ci/modules/state.py`

_OPT-75 Phase 1 clean-room spec. Written from the file's external
contract only — integration points, inputs, outputs, side effects._

**Status:** spec complete, awaiting rewrite.

## Purpose

Provide a tiny persistent-state helper for ICDEV's CI/CD workflow scripts
so a multi-step pipeline (`plan → build → test → review → merge`) can
pass a small, well-typed state object between scripts either via a
shared JSON file on disk OR via stdin/stdout pipes.

This module is used only by the CI workflow scripts under
`tools/ci/workflows/icdev_*.py`. It is NOT a general-purpose state
store; it has no locking, no schema migration, and no concurrency
story.

## Public surface

### Module constants

- `CORE_FIELDS: frozenset[str]` — the whitelisted set of keys that are
  persisted to disk and re-emitted to stdout. Unknown keys are
  silently dropped. At least the following must be included:
  `run_id`, `issue_number`, `branch_name`, `plan_file`, `issue_class`,
  `platform`, `project_id`.

### Class: `ICDevState`

- `__init__(self, run_id: str, logger=None)` — bind to a run id.
  The logger is optional and used only for best-effort debug output.
- `get(self, key: str, default=None) -> Any` — read a single value
  out of the state dict.
- `update(self, **kwargs) -> None` — merge the given keyword args into
  the state dict, dropping any key that is not in `CORE_FIELDS` and
  any value that is `None`.
- `save(self, workflow_step: str = "") -> None` — write the current
  state dict to `PROJECT_ROOT / "agents" / <run_id> / icdev_state.json`.
  Creates the directory tree if absent. `workflow_step` is informational
  only — used for the optional debug log line.
- `to_stdout(self) -> None` — emit the `CORE_FIELDS`-filtered state as
  a single-line JSON object on stdout, for piping to the next step.
- `to_dict(self) -> dict` — return a shallow copy of the full state
  dict (no filtering).

#### Class methods

- `ICDevState.load(run_id: str, logger=None) -> ICDevState` — read the
  JSON file for `run_id` if it exists, parse it into a fresh
  `ICDevState`, and return it. If the file is missing or invalid,
  return an empty state (not an error).
- `ICDevState.from_stdin(logger=None) -> Optional[ICDevState]` — if
  `sys.stdin.isatty()` is False, read one line of JSON from stdin,
  parse it, require a `run_id` key, and return an `ICDevState` bound
  to that run id with all of the parsed fields loaded. Return None
  in any other case (tty, empty, invalid JSON, missing `run_id`).

### Properties

- `state_dir -> Path` — the directory that would hold the state file
  for this run id: `PROJECT_ROOT / "agents" / run_id`.
- `state_file -> Path` — `state_dir / "icdev_state.json"`.

## Semantics

### Where state lives

State is serialised to `PROJECT_ROOT/agents/<run_id>/icdev_state.json`.
`PROJECT_ROOT` is the repo root, computed as
`Path(__file__).resolve().parents[3]`. The file is a plain JSON
object; no nested structure is required; `indent=2` is expected for
human readability.

### Piping semantics

The typical workflow chain is:

```bash
python tools/ci/workflows/icdev_plan.py --run-id R1 \
    | python tools/ci/workflows/icdev_build.py \
    | python tools/ci/workflows/icdev_test.py
```

Each script calls `ICDevState.from_stdin()` first; if that returns
None (tty or empty input) it falls back to `ICDevState.load(run_id)`.
On exit, the script calls `to_stdout()` so the next script in the
pipe can pick up the state.

### Error tolerance

- JSON decode errors during `load()` must log a warning via the
  optional logger and return an empty state, not raise.
- JSON decode errors during `from_stdin()` must return None, not raise.
- `save()` may raise IOError if the filesystem is read-only; callers
  don't have to catch it.

## Integration points

- **Callers:** every script under `tools/ci/workflows/icdev_*.py`, plus
  the testing harness in `tools/testing/`.
- **Filesystem:** reads and writes files under
  `PROJECT_ROOT/agents/<run_id>/`. The directory is git-ignored by
  convention.
- **No DB writes.** The module must NOT talk to `tools/db/storage.py`
  or any database — it is a pure file helper.
- **No LLM calls.** The module must NOT import `tools.llm.*`.
- **No network.** The module must NOT make network calls.

## Fields the rewrite MUST preserve

- The state file path `agents/<run_id>/icdev_state.json`.
- The `CORE_FIELDS` whitelist (any caller that sets one of these keys
  today must still be able to after rewrite).
- The `from_stdin` / `to_stdout` pipe protocol — it's a cross-process
  contract, so format changes break the chain.
- The behavior where unknown keys in `update(**kwargs)` are silently
  dropped, not stored.
- The behavior where a missing or malformed state file produces an
  empty state instead of raising.

## Fields the rewrite may change

- Internal field storage (`_data` dict is an implementation detail).
- Logging calls (the logger is best-effort; rewrite can use ICDEV's
  standard logger instead of whatever the original uses).
- `__repr__` format.

## Non-goals

- No concurrency / locking.
- No schema migration if `CORE_FIELDS` grows in a future version.
- No back-compat with pre-spec state files that used a different
  layout.
- No sensitive-data handling — state files must never contain
  credentials. (Callers are responsible.)

## Acceptance

When the rewrite lands:

1. Every caller script continues to work unchanged.
2. A file produced by the old implementation can be `load()`ed by the
   new implementation — the JSON format is stable.
3. The chain `plan | build | test` preserves `run_id` and
   `issue_number` through all three stages.
4. No `import from tools.db.storage`, `import tools.llm`, or network
   activity.
5. Module-level pytest tests exist: roundtrip save/load, stdin/stdout
   piping, unknown-key drop, missing-file tolerance, core-fields
   filtering in `to_stdout`.

## Out-of-scope notes for future rewrites

The spec writer looked at the current file only to catalogue
behaviour. No attempt was made to describe the current code structure,
variable names, or method ordering. The rewrite engineer should NOT
read the current `tools/ci/modules/state.py` — they should satisfy
only the public surface documented above, choose any internal layout
they prefer, and cite only this spec in the rewrite commit.
