# `test_workflow_hitl_engine` cross-file pollution — root cause and resolution (`tsr-agent-01-d4`)

Closes the FLOW work item opened by `docs/testing/tsr-flow-01-baseline.md`
("Isolating the polluter is the first FLOW work item; it is worth 11 of the epic's 36 failures").

**Status: already fixed on `main` before this task ran.** The leak was root-caused and repaired by
commit `0393c7808` (`fix(tests): bind workflow_hitl fixtures to their tmp DB via _sql_compat`,
`tsr-flow-01-d2`, 2026-08-01) — one day *after* the 2026-07-31 baseline (`1453f378b`) that this
card was seeded from. This document records the independent re-measurement, corrects two wrong
premises in the card, and lands one piece of residual hardening.

## Two corrections to the task card

**1. The named polluter is wrong.** The card says the failures appear "when `test_workflow_hitl_engine`
runs with `tests/studio` + `test_pdc_studio_steps`". Those files are unrelated — they never import
`tools.workflow_hitl`. The real polluter is **any file that imports `tools.workflow_hitl` before the
engine tests run**, and in the FLOW slice that was `tests/test_workflow_hitl_api.py`, a sibling in the
same epic. The baseline doc had this right; the card restated it inaccurately.

**2. There is no studio fixture to clean up.** The card offers "studio fixture cleanup or hitl_engine
isolation" as the two candidate fix sites. Only the second was ever in play.

## Root cause (re-measured, not assumed)

The `tmp_db` fixture patched a single target:

```python
_targets = ["tools.db.storage.get_connection"]
```

`mock.patch` on that string rebinds the `get_connection` **attribute on the `tools.db.storage`
module**. But every module in `tools/workflow_hitl/` binds the function into its own namespace at
import time:

```python
from tools.db.storage import get_connection   # tools/workflow_hitl/team_manager.py:8
```

A module that already ran that from-import holds the **original function object**. Rebinding the
attribute on `tools.db.storage` afterwards does not reach it — this is the `monkeypatch`-a-function-object
failure mode, not a schema or teardown-ordering problem.

That is why the failure looked order-dependent:

- **Run alone** — no `tools.workflow_hitl` module is in `sys.modules` yet. Each one is first imported
  lazily *inside* an already-patched test, so its from-import reads the patched attribute. Green.
- **Run after any importer** — the bindings already exist and are original. Those modules read and
  write the ambient `data/icdev.db` instead of the fixture's tmp DB. 11 tests fail.

This also explains the unstable signature the baseline flagged (`no such table: wf_templates` in one
batch, `FOREIGN KEY constraint failed` in the next): the failure mode is "wrong database", so the
error depends entirely on what state the ambient DB happens to be in.

### Measurement

Reverting the fixture to its pre-fix single-target form reproduces the baseline **exactly**:

```
# pre-fix fixture simulated, engine solo
21 passed, 2 skipped

# pre-fix fixture simulated, api then engine
11 failed, 30 passed     <- sqlite3.IntegrityError: FOREIGN KEY constraint failed
```

Both numbers match `tsr-flow-01-baseline.md` line for line (11 failed / 12 passed in-file; 21 passed,
2 skipped solo). The fixture's module-sweep is therefore load-bearing, and the stated root cause is
confirmed by measurement rather than inferred from the commit message.

## The fix on `main`

The fixture now also patches every already-imported package module that carries its own binding:

```python
_targets = ["tools.db.storage.get_connection"]
_targets += [
    f"{name}.get_connection"
    for name, mod in list(sys.modules.items())
    if (name.startswith(_PKG_SUFFIX) or name.startswith(f"icdev.{_PKG_SUFFIX}"))
    and mod is not None and hasattr(mod, "get_connection")
]
```

Only modules **already** in `sys.modules` are swept, so this forces no imports and adds no ordering
dependency: anything imported later re-reads the (patched) attribute from `tools.db.storage`. No skip
decorators and no ordering markers were added, per the card's constraint.

## Residual hardening landed by this task

The upstream sweep matched `name.startswith("tools.workflow_hitl")` only. `tools/` is a shim over
`icdev.tools`, but `_ToolsRedirect` keeps its own `__path__`, so the redirect fires on *attribute*
access and not on submodule import:

```
import tools.workflow_hitl.team_manager        -> loads tools/workflow_hitl/team_manager.py
import icdev.tools.workflow_hitl.team_manager  -> loads icdev/tools/workflow_hitl/team_manager.py
distinct module objects: True
```

Two copies of the same source, each with an independent `get_connection` binding. Importing the
package under both namespaces yields **28** modules carrying their own binding; the pre-fix filter
swept **14** of them and missed the other **14**.

This is **latent, not live**, and the measurement says so rather than the reasoning. Pre-importing the
whole package under the canonical namespace and re-running the engine tests gives **23 passed both
with the two-namespace filter and with the pre-fix single-namespace filter** — identical, because
every `workflow_hitl` import in this test file goes through the shim (`from tools.workflow_hitl import
…`, 20 call sites; zero `icdev.tools.workflow_hitl` imports). The canonical copies are loaded but
never exercised, so leaving them unpatched costs nothing *today*.

CLAUDE.md requires new code to use `icdev.tools.*`, so the first module or test that follows that rule
would silently reopen the leak — and it would reopen it in the same shape as the original bug, where
the symptom is "wrong database" rather than an import error. The filter now matches both namespaces.
This is defensive hardening with a known trigger condition, not a fix for a live failure.

### An adjacent hazard, noted and not fixed

`hasattr(sys.modules["tools"], "<anything>")` raises `ModuleNotFoundError`, not `AttributeError`,
because `tools/__init__.py::__getattr__` lets the failed `import_module` escape. Any code that probes
modules with `hasattr` across all of `sys.modules` will crash on the shim. The fixture is safe because
it filters on `name.startswith(...)` **before** calling `hasattr`, so the shim root is never probed —
but that ordering is load-bearing and easy to lose in a refactor. Out of scope for this card;
recorded here so the next reader does not rediscover it the hard way.

## Acceptance criterion — verified

`ICDEV_STORAGE_BACKEND=sqlite`, `ICDEV_PG_NO_FALLBACK` unset, DB seeded per the
`tsr-flow-01-baseline.md` recipe (546 tables, 16 `studio_*`, migration 311 applied).

| Arrangement | Result |
|---|---|
| `test_workflow_hitl_engine.py` alone | **23 passed** |
| the card's exact arrangement — `tests/studio` + `test_pdc_studio_steps` + engine | **141 passed** |
| after the *full* studio group (adds `test_studio_rls_column_coverage`, `test_studio_run_tables_rls`, `test_studio_trigger_events_schema_parity`) | **173 passed** |
| studio group + hitl trio, engine last | **176 passed** |
| hitl trio | **58 passed** |
| **the real polluter** — `test_workflow_hitl_api` then engine | **41 passed** |
| all 9 `workflow_hitl`-touching files, engine last | **155 passed** |
| the same 9 in reverse file order | **155 passed** |
| canonical namespace pre-imported, engine solo | **23 passed** |

Every row above was re-measured from scratch under `tsr-agent-01-d4` against commit `1fb18639b`, not
carried over from the `tsr-flow-01` run.

The card's "21/21" target reflects the pre-fix count where 2 tests silently skipped; commit
`0393c7808` repaired both, so the correct green number is **23/23**.

Note: `pytest-randomly` is not installed in this environment, so ordering is deterministic and the
permutations above were run explicitly rather than seeded.
