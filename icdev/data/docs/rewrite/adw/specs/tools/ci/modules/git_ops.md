# Spec: `tools/ci/modules/git_ops.py`

_OPT-75 Phase 1 clean-room spec. Written from the file's external
contract only._

## Purpose

A thin wrapper around the local `git` CLI plus the ICDEV VCS abstraction
(`tools.ci.modules.vcs`) so the workflow scripts under
`tools/ci/workflows/icdev_*.py` can branch, commit, push, and create or
update a PR/MR through one consistent surface. The module knows nothing
about GitHub vs. GitLab — that distinction is the VCS abstraction's job.

## Public surface

All functions are module-level (no class).

### Git primitives

- `create_branch(branch_name: str) -> tuple[bool, str | None]`
  Try to create the branch with `git checkout -b`. If that fails because
  the branch already exists, fall back to `git checkout <branch>`.
  Returns `(True, None)` on success or `(False, "<reason>")` on failure.

- `commit_changes(message: str, paths: list | None = None) -> tuple[bool, str | None]`
  Stage changes and commit. Two staging modes:
    * If `paths` is provided, run `git add -- <paths>` (targeted).
    * Otherwise run `git add -u` (only tracked modified files — never
      `-A`, which would risk leaking `.env` and credentials).
  After staging, check `git status --porcelain`; if the working tree is
  clean, return `(True, None)` (no-op success). Otherwise commit with
  the supplied message.

- `push_branch(branch_name: str) -> tuple[bool, str | None]`
  Run `git push -u origin <branch_name>`.

- `get_current_branch() -> str | None`
  Run `git branch --show-current` and return the trimmed name, or `None`
  on error.

### Workflow finaliser

- `finalize_git_operations(state, logger, vcs=None) -> None`
  End-of-pipeline helper called by `icdev_*` workflow scripts:
    1. Read `branch_name` and `issue_number` from `state` (an
       `ICDevState`-shaped object — anything with `.get(key)`).
    2. If no branch name, log a warning and return.
    3. If `vcs` is None, lazy-import `tools.ci.modules.vcs.VCS()` and
       construct an instance. On failure, log an error and return.
    4. Push the branch via `push_branch()`. On failure, log the error
       and (if `issue_number` is set) comment on the issue with the
       failure reason. Return.
    5. Call `vcs.check_pr_exists(branch_name)`. If a URL comes back,
       log "already exists", optionally comment on the issue with the
       updated URL, and return.
    6. Otherwise build a title and body and call
       `vcs.create_pr(title, body, head=branch_name)`. If the call
       returns a URL, log success and comment on the issue. If it
       returns nothing, log a failure error.

  Title and body shape (must remain stable so existing PRs match):
    * Title: `ICDEV™-<run_id>: Issue #<issue_number>` when an issue is
      bound, otherwise `ICDEV™-<run_id>`.
    * Body: a markdown summary that includes "Automated by ICDEV™
      workflow run `<run_id>`", a `Closes #<issue_number>` line when
      bound, and a CUI marking line.

  The `vcs` object exposes:
    * `is_gitlab: bool`
    * `check_pr_exists(branch_name: str) -> str | None`
    * `create_pr(title, body, head) -> str | None`
    * `comment_on_issue(issue_number: int, body: str)`

## Internal helpers (rewrite is free to change names)

A small `_run_git(args, cwd=None)` helper that returns `(stdout, stderr,
returncode)` and defaults `cwd` to the repo root.

## Repo root resolution

`PROJECT_ROOT = Path(__file__).resolve().parents[3]` (go up 4 levels:
`git_ops.py → modules → ci → tools → repo`).

## Semantics

* All git commands run in `PROJECT_ROOT` unless an explicit `cwd` is
  passed.
* Stdout/stderr are captured and trimmed before return.
* No exception is raised by the public functions on git failure — they
  return `(False, reason_string)`.
* `commit_changes()` is idempotent: a clean working tree is a successful
  no-op, not an error.
* `finalize_git_operations()` is **best-effort**: every failure path
  logs and returns rather than raising.

## Integration points

* **Callers:** every script under `tools/ci/workflows/icdev_*.py` calls
  `finalize_git_operations(state, logger)` at the tail of its
  pipeline. Some also call `create_branch()` early.
* **Depends on:** `tools.ci.modules.vcs.VCS` (lazy-imported in
  `finalize_git_operations` so test code can inject a fake `vcs`).
* **No DB writes**, **no LLM calls**, **no network** beyond the
  underlying `git push` which is the system's own remote call.

## Forbidden

* Never use `git add -A` in `commit_changes()` — `.env` files would
  leak. `git add -u` only.
* Never use `git push --force` (or `-f`, `--force-with-lease`).
  ICDEV's `tools/airgap/hook_compat.py` already blocks these in the
  outer hook layer; this module must not try to work around it.
* No interactive git commands (`-i`, `--interactive`).

## Acceptance

When the rewrite lands:

1. All existing workflow scripts call the same functions with the same
   signatures and get the same return shapes.
2. `git add -u` is the default staging mode. The string `git add -A`
   does not appear anywhere in the file.
3. `commit_changes()` returns `(True, None)` when there's nothing to
   commit (idempotent).
4. `finalize_git_operations()` accepts a fake `vcs` object via the
   keyword arg so unit tests don't need a real GitHub/GitLab remote.
5. Pytest tests cover: branch create + already-exists, commit no-op,
   commit success, push failure path, PR-already-exists path, PR-create
   path, missing-branch-name early return.
