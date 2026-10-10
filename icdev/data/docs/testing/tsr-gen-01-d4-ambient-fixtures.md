# tsr-gen-01-d4 — Ambient-host-state fixture dependencies

Three test files passed in the shared checkout and failed (or hung) in a clean
worktree. In every case the difference was **not** unseeded tables or missing RLS
columns — it was *ambient host state* that the code under test reads directly:
an environment variable exported by the kanban scheduler, a pid lockfile in the
main worktree, and a dashboard listening on port 5050.

That is why these read as unreproducible: the fixture builds a complete `tmp_path`
DB, the assertions are correct, and the test still fails — because the branch under
test returned before it ever touched that DB.

Baseline and verification were run in two worktrees created from the same
`origin/main` commit (`2c2797d04`), under identical ambient conditions
(scheduler pid file present, `KANBAN_RUBRIC_LOOP=true` exported, dashboard up).

| File | Ambient dependency | Before | After |
|---|---|---|---|
| `tests/genesis/test_kanban_message_injection.py` | `KANBAN_RUBRIC_LOOP` env var | 2 failed | pass |
| `tests/test_kanban_manual_gate_exemption.py` | scheduler pid lockfile + Manual Build flag file | 2 failed | pass |
| `tests/test_qa_agent_reflex.py` | live HTTP dashboard on `localhost:5050` | **timeout** | pass (0.98s) |

Combined: **32 passed** in a clean worktree.

---

## 1. `tests/genesis/test_kanban_message_injection.py` — an env var, not a fixture

`_dispatch_via_llm_router`'s *first* statement is a Phase 3b opt-in: when
`KANBAN_RUBRIC_LOOP` is truthy it hands off to `_dispatch_via_rubric_loop` and
returns. The injected `LLMRouter` is then never invoked and the drain loop under
test never runs:

```
E   AssertionError: expected 2 invocations, got 0
    Captured stdout: Kanban: dispatched task-loop via rubric-gated agent loop (Phase 3b)
```

The toggle is read from `os.environ` **at call time**, and the kanban scheduler
exports it. So the file passes in a plain shell and fails in any process that
inherits the scheduler's environment — with no fixture difference to explain it.

**Fix:** `monkeypatch.delenv("KANBAN_RUBRIC_LOOP", raising=False)` via a shared
`_pin_text_only_dispatch` helper. Deleting the variable (rather than stubbing
`_rubric_loop_enabled`) keeps the real branch exercised.

## 2. `tests/test_kanban_manual_gate_exemption.py` — a pid file in another worktree

Both `_reap_stale_in_progress` and `_startup_recover_stale_in_progress` return
**before touching the DB** when another live kanban scheduler owns the runner.
`_foreign_scheduler_pid()` resolves that from `_main_worktree_root()/.tmp/kanban_scheduler.pid`
— never from the `tmp_path` DB the fixture builds. With a scheduler running
anywhere on the machine the sweep no-ops and the *control* assertion fails:

```
AssertionError: ordinary stale task must still be reaped
assert 'in_progress' == 'backlog'
```

One layer down, Manual Build is a flag file in the main worktree; while it is on,
the reaper skips every task whose `in_progress` transition was not recorded by the
scheduler — which is all of them here, since `_insert_task` writes `kanban_tasks`
directly with no transition row.

Both guards are real behaviour with their own coverage. In *this* file they are
ambient state, so they are pinned to "this process owns the runner":

```python
monkeypatch.setattr(km, "_foreign_scheduler_pid", lambda: 0)
monkeypatch.setattr(km, "_manual_build", lambda: False)
```

This file also carried a hand-rolled `_TranslatingConn` shim (kanban SQL is
authored for PostgreSQL, `%s` placeholders). It is replaced by the shared
`tests/_sql_compat.connect`, which applies the same `translate_sql` the runtime's
`StorageConnection` does **and** implements `__enter__`/`__exit__` the way
`StorageConnection` does — so `with get_connection() as conn:` call sites actually
commit. Both call sites were converted: `_patch_km` and
`TestStateMachineAutoCloseExemption._conn`.

## 3. `tests/test_qa_agent_reflex.py` — a hang, not a failure

Every test in `TestRunReflex` calls `run()`, and `run()` calls `_run_route_smoke()`,
which sweeps **79 `NAV_ROUTES` over real HTTP** against a dashboard that must
already be listening:

```python
def _run_route_smoke(base: str = "http://localhost:5050") -> list:
```

There is no injection seam — the base URL is a hardcoded default with no env
override. In the clean worktree the file does not fail, it is **killed by the
timeout while blocked in `urlopen`**:

```
smoke_failed = _run_route_smoke()
  result = _smoke_route(base, route, timeout=timeout)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
++++++++++++++ Timeout ++++++++++++++
```

Note this reproduced *with the dashboard up* — `localhost` resolving to `::1`
while Flask binds IPv4 is enough to make every one of the 79 routes block.

None of these tests are about route smoke. `_run_route_smoke` is stubbed to `[]`
in `setUp` (with `addCleanup`), so the class exercises the reflex's own DB/gap
logic only. Runtime went from a hard timeout to **0.98s**.

---

## Reproducing

```bash
# clean worktree at the same commit, no ambient DB
git worktree add --detach <path> origin/main
cd <path>
PYTHONPATH=<path> ICDEV_STORAGE_BACKEND=sqlite python -m pytest \
  tests/test_kanban_manual_gate_exemption.py \
  tests/genesis/test_kanban_message_injection.py \
  tests/test_qa_agent_reflex.py -p no:randomly -q
```

## Rule of thumb

Before adding fixture *data*, check whether the code under test reads host state
that short-circuits it: `os.environ` at call time, a lockfile or flag file
resolved from `_main_worktree_root()` (not from the test's `tmp_path`), or a
network call to a hardcoded localhost port. A test that fails with a correct
fixture, or that hangs instead of failing, is usually one of these three.
