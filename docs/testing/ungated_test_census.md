# Ungated test census — which backlog modules are green TODAY?

`args/ci_test_backlog.txt` enumerates **1792** test modules that CI has never run, so none of them has ever gated a merge. They cannot be bulk-added to `args/ci_test_files/core.txt` — an unknown fraction are red, adding a red file turns `main` red, and a red `main` gets the gate disabled, which is strictly worse than the debt. This is the measurement that has to come first.

**1792 measured, 0 not reached. 1792 + 0 = 1792.**

Machine-readable companion: `docs/testing/ungated_test_census.json` — that is the artifact the promotion batches consume; this page is the summary.

## Outcome by status

| Status | Files | Share of measured | What it means |
|---|---:|---:|---|
| `passed` | 1691 | 94.4% | Green alone. **Candidate** for promotion — still needs the in-suite half. |
| `failed` | 93 | 5.2% | Ran, asserted, and at least one test failed. |
| `collection-error` | 0 | 0.0% | Did not import/collect at all. Import or dependency fix, batchable. |
| `no-tests` | 2 | 0.1% | Collected nothing. **Not a pass** — promoting it widens the allowlist without widening coverage. |
| `timeout` | 6 | 0.3% | No verdict inside the 240s per-file ceiling. |
| `missing` | 0 | 0.0% | The backlog names a path that is not in the checkout — a defect in the census file itself. |
| `not-reached` | 0 | — | Never launched (deadline or sample bound). No verdict is claimed. |

**1691 of 1792 measured modules (94.4%) pass when run alone.**

## What this does NOT say

- A `passed` row means **green ALONE**. It does not mean safe to append to `core.txt`: the in-suite half — running after the ~260 modules already on the list, in one process — catches a different defect class and is not measured here. A promotion batch runs it itself.
- `no-tests` is counted separately from `passed` on purpose. A module that collects nothing exits 0 and would look green to any check that only reads the exit code; promoting it is the same error as counting a skip as coverage.
- 0 non-passing rows carry a concurrency-shaped error (`database is locked`, `PermissionError`, …). This sweep ran 10 pytest processes at a time, each with its own `ICDEV_DB_PATH`, but tests that write fixed paths under `data/` or the OS temp dir can still collide. Those rows carry `concurrency_shaped: true` in the JSON and are worth re-running singly before being treated as real.

## Measurement conditions

| Condition | Value |
|---|---|
| `runner` | tools/ci/isolation_run.py::run_one (one pytest process per file) |
| `workers` | 10 |
| `per_file_timeout_s` | 240 |
| `deadline_s` | 5100 |
| `db_isolation` | per-file ICDEV_DB_PATH + PYTEST_DEBUG_TEMPROOT under a scratch root |
| `elapsed_s` | 1926.4 |
| `pytest_args` | `-q --tb=short -p no:cacheprovider -rfE` |

## Where the green modules are

Grouped by directory, because promotion happens in batches and a batch that shares a directory shares its fixtures. Promoting all 1691 of them would add roughly **251 minutes** of standalone pytest time — less in-suite, where the interpreter and imports are paid once, but not nothing, and that is the number a batch plan has to argue with.

| Green modules | Directory |
|---:|---|
| 1354 | `tests/` |
| 32 | `tests/cortex/` |
| 32 | `tests/genesis/` |
| 25 | `tests/kanban/` |
| 23 | `tests/docmod/` |
| 20 | `tests/agent_runtime/` |
| 19 | `tests/llm/` |
| 16 | `tests/ci/` |
| 16 | `tests/dashboard/` |
| 15 | `tests/bom/` |
| 10 | `tests/security/` |
| 8 | `tests/databridge/` |
| 7 | `tests/ci/workflows/` |
| 7 | `tests/rag/` |
| 7 | `tests/testing/` |
| 7 | `tests/viz/` |
| 6 | `tests/e2e/` |
| 5 | `tests/bi_dashboard/` |
| 5 | `tests/ci/modules/` |
| 5 | `tests/foundry/` |
| 4 | `tests/db/` |
| 4 | `tests/http/` |
| 4 | `tests/provenance/` |
| 4 | `tests/slides/` |
| 4 | `tests/workflow/` |
| … | 36 further directories — see the JSON |

## Most common failure signatures

Grouped on a normalised first-failure line, so a promotion batch can see which reds are one shared cause rather than N separate jobs.

| Files | Signature |
|---:|---|
| 9 | `E   assert N == N` |
| 5 | `E   assert False is True` |
| 4 | `E   sqlite3.OperationalError: near "%": syntax error` |
| 2 | `Ass...` |
| 2 | `...` |
| 2 | `E   AssertionError: icdev twin drifted: dashboard/templates/base.html` |
| 1 | `E   assert True is False` |
| 1 | `E   AssertionError: default tool list not found � update this test` |
| 1 | `E   AssertionError: assert True is False` |
| 1 | `E   AssertionError: Preview failed N: {"code":"CSRF_FAILED","error":"CSRF token missing or invalid","message":"This request requires a valid CSRF token. Reload ` |
| 1 | `Asser...` |
| 1 | `E   AssertionError: merge=union applied to 'args/ci_skip_census.txt', which is not in the union-safe allowlist. Union is only safe for flat, line-oriented files` |
| 1 | `E   AssertionError: skeleton fails its own validator: [LintFinding(line=N, kind='file_name', match='Users\\schuo\\AppData\\Local\\Temp\\icdev-census-otab__nr\\t` |
| 1 | `E   assert '"fa_certificate_evidence"' in 'APPEND_ONLY_TABLES list in is_append_only_table_modification()\n    - Direct sqlite3.connect() that bypasses the stor` |
| 1 | `E   AssertionError: blueprint.py builds SQL fragments with `?`: [(N, 'Export Academy completions as xAPI N.N.N statements (aca-trn-N).\n\n   ')]` |
| 1 | `E   assert '"fa_xp_ledger"' in 'APPEND_ONLY_TABLES list in is_append_only_table_modification()\n    - Direct sqlite3.connect() that bypasses the stor...        ` |
| 1 | `E   AssertionError: expected a warning to be emitted for an unknown predicate` |
| 1 | `E   AssertionError: controls-query failure must roll back � otherwise the PG transaction stays aborted and every later query in the cycle cascade-fails` |
| 1 | `E   AssertionError: assert 'error' == 'ok'` |
| 1 | `E   AssertionError: {"control_count":N,"error":"insert: table compliance_snapshots has no column named status","evidence_count":N,"framework_id":"FedRAMP Modera` |

## Reproducing this

```bash
python tools/ci/ungated_test_census.py --run \
  --workers 10 --timeout 240 \
  --out docs/testing/ungated_test_census.json \
  --md docs/testing/ungated_test_census.md
python tools/ci/ungated_test_census.py --verify docs/testing/ungated_test_census.json
```

This task MEASURES only. `args/ci_test_files/core.txt` and `args/ci_test_backlog.txt` are untouched by it, and the tool exits 0 whatever it finds — a census that becomes a gate gets switched off before anyone reads it.
