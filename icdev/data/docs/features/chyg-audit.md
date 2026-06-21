<!-- CUI // SP-CTI -->
# Connection-Hygiene Audit — `tools/genesis/reflexes/kanban.py` (+ siblings)

**Task:** chyg-audit-01 · **Date:** 2026-05-30 · **Method:** AST analysis
(`.tmp/chyg_audit.py`) classifying every `get_connection()` callsite by close
guarantee + leak severity.

## Why
Pooled PostgreSQL connections are `autocommit=False` (`tools/db/storage.py`). A
`conn = get_connection()` whose `close()` is skipped (exception path, or never
called) sits **`idle in transaction`** holding ACCESS SHARE locks → the recurring
`kanban_tasks` lock storm that hangs the dashboard + scheduler. See memory
`kanban-tasks-lock-storm`.

## Canonical fix pattern
`with get_connection() as conn:` — `StorageConnection.__exit__`
(`storage.py:921-927`) **commits on success, rolls back on exception, and always
closes**. Drop the now-redundant manual `conn.commit()` / `conn.close()`. Scope the
`with` to just the DB work; keep post-processing of fetched rows outside the block.
Use `try/finally: conn.close()` only where the connection must outlive a single
block (reused/returned). **Never hold a connection across a `requests`/network call
or `sleep`.**

Already-correct reference sites: the 22 `try/finally: conn.close()` functions in
this file, `tools/genesis/harness/eval_harness.py::_safe_close`, and the
GA/CI poll functions fixed 2026-05-30.

## Summary (kanban.py — 6607 lines, 55 `get_connection()` calls)
| Class | Count | Meaning |
|-------|------:|---------|
| `SAFE_FINALLY` | 22 | close in `finally` — leak-proof, no change |
| `LINEAR_CLOSE` | 31 | `close()` present but **not** in `finally` — leaks on the exception path |
| `NO_CLOSE` | 2 | **never closed — always leaks** |

> Severity note: the analyzer marks a site `high` when its enclosing function
> contains *any* loop/network call. That is conservative — for many `LINEAR_CLOSE`
> sites the connection is closed immediately after the query and the loop is
> unrelated, so the real exposure is only the exception path (which the Phase-1
> `idle_in_transaction_session_timeout` safety-net also covers). The genuinely
> urgent sites are the **2 `NO_CLOSE`** and the **network-held** one.

### Top priority (always-leak / network-held)
| Line | Function | Class | Note |
|-----:|----------|-------|------|
| 3042 | `_dispatch_github_actions()` | NO_CLOSE | commit then no close; in a try/except — **always leaks a pooled conn** |
| 2961 | `_dispatch_gitlab()` | NO_CLOSE | same pattern as above |
| 2347 | `_queue_alert_locally()` | LINEAR_CLOSE | network in function — avoid holding conn across it |

## Fix batches (matches chyg-sweep-01/02/03)

### Batch 1 — lines ~50–1810 (3 sites)
| Line | Function | Class | Sev |
|-----:|----------|-------|-----|
| 139 | `_get_task_timeout()` | LINEAR_CLOSE | (query then close; wrap query in `with`) |
| 1071 | `_cleanup_worktree()` | LINEAR_CLOSE | low |
| 1760 | `_parent_is_done()` | LINEAR_CLOSE | low |

### Batch 2 — lines ~1810–3700 (7 sites to fix; 2 to leave)
| Line | Function | Class | Action |
|-----:|----------|-------|--------|
| 1810 | `_close_orphaned_rca_children()` | LINEAR_CLOSE | fix |
| 2347 | `_queue_alert_locally()` | LINEAR_CLOSE | fix (network) |
| 2961 | `_dispatch_gitlab()` | NO_CLOSE | **fix (priority)** |
| 3042 | `_dispatch_github_actions()` | NO_CLOSE | **fix (priority)** |
| 3089 | `_poll_github_actions_completions()` | LINEAR_CLOSE | **LEAVE — already fixed 2026-05-30** |
| 3202 | `_detect_and_queue_ci_failures()` | LINEAR_CLOSE | **LEAVE — already fixed 2026-05-30** |
| 3439 | `_set_executor_type()` | LINEAR_CLOSE | fix |
| 3453 | `_get_executor_type()` | LINEAR_CLOSE | fix |
| 3593 | `_dispatch_to_claude()` | LINEAR_CLOSE | fix |

### Batch 3 — lines ~3700–end (21 sites)
`_is_dangerous_task` (3700), `_run_verify_checks` (3875/3922/4047/4074/4271),
`_write_verification_log` (4358), `_verify_task_specific` (4443/4719),
`_update_verification_metrics` (4817), `_promote_stale_suggested` (5016),
`_reap_stale_in_progress` (5068), `_startup_recover_stale_in_progress` (5196),
`_check_completed` (5265/5311/5348/5364/5422), `run` (6297/6359/6379).

## Sibling reflexes / dispatcher (chyg-sweep-03 scope)
Other `tools/genesis/` files with `get_connection()` callsites (re-run the
analyzer per file before fixing): `promoter.py` (13), `goal_learner.py` (12),
`reflexes/evolve.py` (8), `reflexes/heal.py` (5), `stagnation_detector.py` (3),
`reflexes/publish.py` (3), `reflexes/dat_refresh.py` (3), `convergence.py` (3),
`daemon.py` (3), plus ~20 single-callsite modules. Audit each with
`.tmp/chyg_audit.py <file>` and fix any non-`SAFE_FINALLY` sites with the same pattern.

## Verification (chyg-vv-01)
After sweeps: restart kanban scheduler + genesis daemon; after ~2 idle cycles
assert `idle in transaction` count in the icdev DB stays `< 3` (query in
`.tmp/_verify_kanban_leak.py`); add `tests/test_conn_hygiene.py` regression guard;
run coherence gate + companion sync.

## COMPLETED — 2026-05-30
All **29 leak sites** in `tools/genesis/reflexes/kanban.py` converted to
`with get_connection() as conn:` (commits + closes via `__exit__`) or, for large
multi-close loops (`_reap_stale_in_progress`, `_startup_recover_stale_in_progress`),
the `conn=None … finally: conn.close()` pattern. The 2 GA/CI poll functions were
left as-is per scope.

Final analyzer state (`.tmp/chyg_audit.py`):
`with_safe: 29 · SAFE_FINALLY: 24 · LINEAR_CLOSE: 2 (GA/CI polls) · **NO_CLOSE: 0**`.
`py_compile` + `ruff` clean. Regression guard `tests/test_conn_hygiene.py` passes
(asserts 0 always-leak sites). **Systemic backstop already live**: `storage.py`
sets `idle_in_transaction_session_timeout=30s` + `lock_timeout=10s` on every
pooled PG connection. The running scheduler/daemon load the fixed code on their
next restart. Done manually under a coordination lease on the file.
