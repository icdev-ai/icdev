# CUI // SP-CTI

# Headless Guardrail Parity (hgx-guard-01, hgx-guard-02)

Card: **HGX — Harness Agent Parity and Graph Runtime**.

## Problem

Two orchestration paths reach the same tools, and they enforced different rules.

`.claude/hooks/pre_tool_use.py` ran eight checks. `tools/airgap/hook_compat.py::run_pre_tool_check`
— the function the Studio agent loop, the kanban runner, the MCP gateway and every
other non-Claude-Code orchestrator actually call — ran two, and had one the Claude
Code hook did not (the destructive-git blocklist). Neither was a superset of the
other, so **an agent running outside Claude Code was materially less guarded than
one inside it.** For an IL5/IL6 platform that is backwards.

Two specific holes made it worse:

1. **Name-based short-circuit.** `run_pre_tool_check` returned *allowed* for any
   tool whose name was not one of `("Bash","bash","shell","sql","Write","Edit")`.
   An unrecognised mutating tool — an MCP shell, a SQL runner, a `write_file` — was
   waved through completely unscanned.
2. **A drifted table list.** The headless copy of `APPEND_ONLY_TABLES` held 22
   entries against the hook's 361. Roughly 340 append-only tables (NIST 800-53 AU)
   had no protection at all on the headless path.

Only `PreToolUse` had a headless analogue. `PostToolUse` and `Stop` had none, even
though `run_agent_loop` already exposed matching slots.

## What changed

### One registry, two callers

`tools/hooks/shared_checks.py` is now the single source of truth. Both hook paths
iterate the same `CHECKS` tuple:

| Check | Blocks | Paths |
|-------|--------|-------|
| `git_danger` | force-push, hard reset, `branch -D`, `clean -f`, `checkout .`, `rebase -i` | headless (Claude Code opts in with `ICDEV_GIT_DANGER_GUARD=1`) |
| `env_file_access` | reads/writes of secret-bearing `.env` files | both |
| `dangerous_rm` | recursive-force `rm`, recursive `rm` at a dangerous path | both |
| `append_only_tables` | UPDATE/DELETE/DROP/TRUNCATE on any of 361 tables (NIST AU, D6) | both |
| `direct_sqlite` | `sqlite3.connect()` past the storage layer | both |
| `file_access_tiers` | D-ORCH-8 `zero_access` / `read_only` / `no_delete` | both |
| `branch_deletion` | deleting a remote branch holding unmerged commits | both |
| `worktree_path` | `git worktree add` outside the sanctioned roots | both |
| `review_loop_precommit` | *warn* — unfixable staged lint (`ICDEV_REVIEW_LOOP_BLOCK=1` to block) | both |

A check declares which paths run it, so the one remaining asymmetry is **declared
and tested** rather than accidental. `git_danger` is headless-only on purpose: it
refuses a history rewrite outright, which is right for an unattended agent and
wrong for an interactive session where a human can authorise one.

`tests/test_hgx_guard_parity.py::test_headless_runs_every_claude_code_check` reads
the registry itself, so a new check that forgets `PATH_HEADLESS` fails the suite
instead of quietly reopening the gap.

### Tool names are no longer a security boundary

Calls are classified by the **shape of their input**, not by a name allowlist:

* text under `command` / `cmd` / `script` / `sql` / `query` → executed text, scanned.
* a path plus a body → a write, scanned.
* a path alone → a read.

Known tools classify exactly as the Claude Code hook always treated them, so its
behaviour is unchanged. Unknown tools are scanned rather than trusted. Read-only
traffic stays cheap because nothing is extracted from it, so every check exits on
its first guard.

File **bodies** are deliberately excluded from the append-only SQL scan. Writing a
file containing `DELETE FROM audit_trail` does not delete anything; executing it
does. Scanning bodies flags nine legitimate files in this repo — chief among them
the audit tests that assert the DELETE is refused. Writes stay covered by the
file-tier and direct-sqlite checks.

### PostToolUse and Stop

`hook_compat` gained `run_post_tool_check()` and `run_stop_check()`, mirroring
`.claude/hooks/post_tool_use.py` and `stop.py` (audit to the append-only
`hook_events` trail, `TOOL_EXECUTE_AFTER` dispatch, `ICDEV_AUTO_COMMIT`).
`agent_loop_hooks()` returns all three shaped for the loop's `PreToolUseHook` /
`PostToolUseHook` / `StopHook` aliases:

```python
from tools.airgap.hook_compat import agent_loop_hooks
result = run_agent_loop(..., **agent_loop_hooks())
```

ACE's co-worker loop now binds `on_post_tool_use`; it already bound the other two.

Transcript capture is **not** mirrored — it reads a Claude Code `.jsonl` transcript
with no headless counterpart. The agent loop persists its own history through
`session_store` checkpoints instead.

## Where the table list lives now

`APPEND_ONLY_TABLES` moved from `.claude/hooks/pre_tool_use.py` to
`tools/hooks/shared_checks.py`. The extraction is verified table-for-table
identical to `origin/main` (361 unique entries, none lost, none gained).

Callers updated to follow it: `claude_dir_validator.append_only_registry_path()`,
`coherence_checker._append_only_registry_path()` (used by `check_append_only`, the
HITL workflow check, and `_autofix_append_only`), and the thirteen test modules
that assert "table X is registered append-only".

The hook still re-exports the predicates (`is_append_only_table_modification`,
`check_worktree_path`, …) so callers that import them by name keep working — they
delegate to the registry, so there is one implementation of each rule.

## Design constraints

**Stdlib-only at import time.** The Claude Code hook is a fresh process on the
critical path of every tool call, and `import tools` eagerly constructs an
`LLMRouter` (~80 ms). The hook loads `shared_checks` straight from disk with
`importlib` instead. `yaml`, `tools.git.worktree_paths` and
`tools.quality.review_loop` are imported lazily inside the one check that needs
each.

**No `os.getcwd()`.** These checks run from worktrees, where cwd is the worktree
root. `repo_root()` walks up from `__file__` for a `.git` marker. `.claude/` alone
is *not* a marker: companion sync writes a `tools/.claude` directory, which made an
earlier draft resolve the repo root to `<repo>/tools`.

**Fail open, visibly.** A check that raises is treated as a pass — a broken guard
must never be the reason a session cannot work — but the failure is recorded as a
warning on the outcome rather than swallowed.

## Known gaps (not addressed here)

* Three tables (`integrity_assessments`, `voc_job_statements`,
  `workflow_replay_sessions`) carry append-only comments in `init_icdev_db.py` but
  are not registered. **Pre-existing on `origin/main`** and left alone: registering
  a table is a judgement about its write sites, not a mechanical edit — the list
  documents five `gd_ai_*` tables that are mutable *by design* and must stay out.
* `_autofix_append_only` in the coherence checker has never fired (its regex
  matches a `{...}` set literal; the list has always been `[...]`). Retargeted at
  the new file but deliberately left inert, for the same reason.
* **ACE's `_combined_pre_hook` does not run the guardrails at all** — it composes
  only the trust scorer and the HITL gate. Routing ACE's pre-tool hook through
  `run_pre_tool_check` is a blocking-behaviour change to a live subsystem and
  belongs with hgx-exec-03 (executor routing), not here.

## Verification

```bash
pytest tests/test_hgx_guard_parity.py -q          # 49 passed
pytest tests/airgap/ tests/test_agent_loop.py tests/agent_runtime/ -q
python tools/workflow/coherence_checker.py --tier fast --gate --changed-files "<changed>"
```
