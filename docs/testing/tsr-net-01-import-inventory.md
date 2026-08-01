# TSR NET — network-subsystem test-file inventory (tsr-net-01-d1)

Diagnostic only. Produced 2026-08-01 on branch `kanban/tsr-net-01-d1`, a worktree off `origin/main`
at `fab5009a9`. No source or test file was modified.

Answers: which `tests/` files exercise the NET epic — the network / delivery-plumbing `tools.*`
packages — selected by what each file **imports**, not by filename.

## Databases seeded

All three seed steps ran clean in this worktree before the inventory was taken (a fresh worktree
starts with no `data/*.db`, so this is a prerequisite, not a formality):

| # | command | result |
|---|---------|--------|
| 1 | `python tools/db/init_icdev_db.py` | OK — `data/icdev.db`, **525 tables**, 8 wf_templates + 3 wf_document_templates seeded |
| 2 | `python tools/studio/init_db.py` | OK — studio tables created (exit 0) |
| 3 | `python tools/db/migrations/311_studio_event_tables_rls_columns/up.py` | OK — `Migration 311 applied.` |

Both env pins are load-bearing and must be repeated by any follow-on task in this worktree:

```bash
export PYTHONPATH="C:\AI\ICDev\.tmp\worktrees\tsr-net-01-d1"   # else: ModuleNotFoundError: No module named 'tools'
export ICDEV_STORAGE_BACKEND=sqlite                            # else the seed half-lands against PG
```

The ambient environment has `ICDEV_STORAGE_BACKEND=postgresql`; without the sqlite pin step 1 does
not populate the local SQLite file the tests read.

## Scope

The task named 8 packages. **Six exist as named; two do not.**

| named by task | resolution |
|---------------|------------|
| `tools/net` | **does not exist** → resolved to `tools/network`. Zero `tests/` files reference `tools.net`, so nothing is lost. |
| `tools/deploy` | **does not exist**, and zero `tests/` files reference `tools.deploy`. Dropped rather than substituted — see note below. |
| `tools/pipeline`, `tools/ci`, `tools/infra`, `tools/cloud`, `tools/sre`, `tools/ops_hub` | exist as named, each a real package with `__init__.py` |

`tools/deploy` was **not** silently remapped. Plausible neighbours exist (`tools/devops`,
`tools/devsecops`, `tools/ops`, `tools/infra_canvas`), but picking one would invent scope the task
did not ask for. If the NET epic is meant to cover deployment tooling, that is a scope decision for
whoever owns the card, and the package needs naming explicitly.

### Selection regex

```
(?<![\w.])(?:icdev\.)?tools\.(?:network|pipeline|ops_hub|cloud|infra|sre|ci)(?![\w])
```

Applied to every `*.py` under `tests/` (not just `test_*.py`, matching the CANV slice convention, so
`conftest.py` and `e2e_*.py` helpers are caught). The leading `(?<![\w.])` stops
`tools.canvas_ci`-style false positives; both the canonical `icdev.tools.*` and the legacy `tools.*`
shim namespaces are matched.

## Results — 131 files

| # | package | files matching | of which a real `import` |
|---|---------|----------------|--------------------------|
| 1 | `tools/network` | 72 | 67 |
| 2 | `tools/pipeline` | 27 | 23 |
| 3 | `tools/ci` | 22 | 21 |
| 4 | `tools/infra` | 6 | 6 |
| 5 | `tools/cloud` | 5 | 5 |
| 6 | `tools/ops_hub` | 4 | 2 |
| 7 | `tools/sre` | **0** | **0** |

131 distinct files; 123 import at least one in-scope package directly, 8 reference one only through
a `mock.patch("tools.…")` target string. Per-package counts sum above 131 because a file may
exercise more than one package.

### `tools/sre` has zero test coverage

`tools/sre` is not an empty package — it ships four modules:

```
tools/sre/incident_commander.py
tools/sre/runbook_executor.py
tools/sre/seed_runbooks.py
tools/sre/slo_manager.py
```

No file under `tests/` imports any of them. This is a genuine coverage gap, not an artifact of the
regex. It is worth a card of its own; a test-suite-remediation epic cannot remediate tests that were
never written.

### Patch-string-only files (8)

These reference an in-scope package solely as a `mock.patch(...)` target, never via `import`. They
are included in the slice — a patch target is still a real coupling and still breaks when the target
module moves — but they will not surface an import-time error, so they are the wrong place to look
first when triaging collection failures:

| file | package(s) referenced |
|------|-----------------------|
| `tests/test_canvas_cards_pg.py` | pipeline |
| `tests/test_cnr_ops_cache_lookingglass.py` | ops_hub |
| `tests/test_component_registry.py` | network, ops_hub, pipeline |
| `tests/test_kanban_scheduled_and_pr_opened.py` | ci |
| `tests/test_system_graph_ndc.py` | network |
| `tests/test_twin_airgap_rules.py` | network |
| `tests/test_twin_core.py` | network, pipeline |
| `tests/test_twin_core_event_bridge.py` | network, pipeline |

## Overlap with the CANV slice — read before running

**78 of these 131 files are already in `docs/testing/tsr-canv-01-slice.txt`.** The CANV slice claims
`tools/network` as a canvas package (72 files there); the NET slice claims it as a network package.
The two epics genuinely share it.

Consequence: running the NET slice as-is re-runs ~60% of CANV's work, and two sessions fixing the
same file concurrently will collide. Two artifacts are therefore provided:

| file | contents | use |
|------|----------|-----|
| `docs/testing/tsr-net-01-slice.txt` | all 131 | the complete NET blast radius |
| `docs/testing/tsr-net-01-exclusive.txt` | **53** files not in the CANV slice | safe to run/fix in parallel with CANV |

68 of the 131 reference `tools.network` and nothing else in NET scope — i.e. the bulk of this slice
is network-canvas surface that CANV has an equal claim on. Whoever schedules `tsr-net-01-d2` should
decide which epic owns `tools/network` before dispatching, or work from the exclusive list.

## Reproducing

```bash
cd C:/AI/ICDev/.tmp/worktrees/tsr-net-01-d1
export PYTHONPATH="C:\AI\ICDev\.tmp\worktrees\tsr-net-01-d1"
export ICDEV_STORAGE_BACKEND=sqlite
pytest $(cat docs/testing/tsr-net-01-exclusive.txt | tr '\n' ' ') -rfE --timeout=120
```

Use `-rfE`, not `-rf`: `-rf` hides ERRORs, and collection errors are the expected failure mode for a
freshly seeded worktree. `--timeout` guards the known `test_production_audit.py` wedge.

No baseline pytest run was performed under this card — its acceptance criterion is the inventory and
the seeded DBs. Establishing the NET failure baseline is `tsr-net-01-d2`'s job.

## Artifacts

| file | contents |
|------|----------|
| `docs/testing/tsr-net-01-slice.txt` | 131 file paths, one per line |
| `docs/testing/tsr-net-01-exclusive.txt` | 53 paths not shared with the CANV slice |
| `docs/testing/tsr-net-01-inventory.json` | per-file package attribution, per-package counts, regex, scope decisions |
| `docs/testing/tsr-net-01-import-inventory.md` | this document |
