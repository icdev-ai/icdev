# TSR AI — cause-(B) ambient DB state remediation (tsr-ai-01-d4)

Produced 2026-08-02 on branch `kanban/tsr-ai-01-d4`, off `origin/main` at `c9c4643d5`.

d4's brief is cause **(B)** only — *"depends on ambient DB state in the shared checkout"*.
Cause (A) (`%s` vs bare `sqlite3.connect`) was tsr-ai-01-d3. Causes (C) (missing config key,
silent default) and (D) (real code/test disagreement) are **tsr-ai-01-d5**, and the list this
document hands over in §4 is d5's input.

## 1. Why this task had to redo d2's triage

d2's acceptance was *"a table showing file path, pass/fail in worktree, pass/fail in shared
checkout, and root cause category for each failing file."* That table was never produced. What
d2 actually landed is four short-summary transcripts under
`docs/testing/tsr-ai-01-d2-evidence/` — **the clean arm only**, with no populated arm and no
per-file categorisation. It also ran only part of the slice:

| slice segment | files | run by d2? |
|---|---|---|
| `tests/cortex/` | 50 | yes — 613 passed, 2 skipped, 16 errors |
| `tests/llm/` | 15 | yes — 269 passed |
| `tests/rag/` | 9 | yes — 125 passed |
| other subdirectories (`genesis`, `workflow`, `agent_runtime`, …) | 39 | yes — 577 passed, 12 failed |
| **top-level `tests/test_*.py`** | **140** | **no — never executed** |
| | 253 line-entries / 233 unique paths | |

So 140 of the 233 slice files had no result at all, and no file anywhere in the slice carried a
(B)-vs-(D) verdict. d4 therefore ran the missing 140 and classified from scratch.

## 2. Arms

Both arms are the same commit. The distinguishing variable is `data/icdev.db` content.

| arm | `data/icdev.db` |
|---|---|
| clean worktree (this branch) | schema seed only — `tools/db/init_icdev_db.py` (525 tables), then `tools/studio/init_db.py`, then `tools/db/migrations/311_studio_event_tables_rls_columns/up.py` |
| populated | the shared checkout's accumulated dev database (months of dashboard + kanban traffic) |

Note the migration path is `tools/db/migrations/311_…`, **not** the repo-root `migrations/`
directory that the project brief's wording implies — root `migrations/` holds three files and no
311. Running the brief's command verbatim fails with `No such file or directory`.

Command (identical per shard, six shards in parallel):

```bash
export PYTHONPATH=<worktree root>      # NOT the parent worktree — see §5
export ICDEV_STORAGE_BACKEND=sqlite    # without the pin the seed half-lands
unset ICDEV_DATABASE_URL; export ICDEV_PG_NO_FALLBACK=0
python -m pytest $(cat shardNN) -q -rfE --timeout=90 --timeout-method=thread \
    -p no:cacheprovider --tb=no --continue-on-collection-errors
```

`-rfE`, not `-rf`: `-rf` omits the short-summary section for **errors**.

## 3. The cause-(B) finding, and the fix

Of the 21 failures the 140 unrun files produced, **five are cause (B)**, all in
`tests/test_time_decay.py`, all one root cause:

```
FAILED TestScoreEntry::test_scores_existing_entry      ValueError: Memory entry 1 not found
FAILED TestScoreEntry::test_returns_expected_keys      ValueError: Memory entry 1 not found
FAILED TestRankWithDecay::test_recent_ranks_above_old  assert 0 == 2
FAILED TestRankWithDecay::test_high_importance_competitive  assert 0 == 2
FAILED TestRankWithDecay::test_returns_expected_keys   assert 0 == 1
```

`tools/memory/time_decay.py` exposed a `db_path` seam and then ignored it:

```python
def _get_connection(db_path: Optional[Path] = None):
    """Get a DB connection."""
    return get_connection()          # db_path accepted, never used
```

Every test in the file seeds a `tmp_path` SQLite database and passes it as `db_path=`. Because
the parameter was discarded, `score_entry` and `rank_with_decay` read the **live**
`data/icdev.db` instead. On a populated checkout `memory_entries` happens to hold a row with
`id = 1` and rows matching the queries `"python coding"`, `"security"` and `"test"`, so the
assertions pass on content the test never wrote. On a clean checkout the table is empty and the
same tests fail. That is the textbook (B) signature: **green for a reason unrelated to the code
under test.**

The fix honours the seam, in the shape tsr-ai-01-d3 already established for `memory_db`,
`memory_read`, `memory_write` and `hybrid_search`:

```python
if db_path is not None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row            # get_connection() sets this (storage.py:1554);
                                              # rank_with_decay subscripts rows by name
    return StorageConnection(conn, "sqlite")  # score_entry's SELECT uses %s
return get_connection()
```

Both wrappers are load-bearing and were verified by removing each in turn: without
`row_factory`, `rank_with_decay` dies on `r["id"]`; without `StorageConnection`, `score_entry`
dies on `near "%": syntax error`.

Mirrored to `icdev/tools/memory/time_decay.py` (the two files are byte-identical).

### Two tests were passing vacuously

`TestRankWithDecay::test_top_k_limits` (`assert len(results) <= 5`) and
`test_empty_db` (`assert results == []`) were both satisfied by the empty result set the bug
produced. They pass meaningfully now. They are not counted as fixed failures because they were
never red.

### The seam is also a documented CLI flag

`time_decay.py --db-path` is advertised as `"Override database path"`. It was a silent no-op —
it read the platform database whatever you passed. It now works:

```
$ python tools/memory/time_decay.py --score --entry-id 1 --db-path <tmp>/cli_smoke.db --json
{"entry_id": 1, "content": "cli smoke content", "type": "fact", "importance": 9, ...}
```

When the flag is absent `db_path` is `None` and behaviour is unchanged, so no production caller
moves.

## 4. Not cause (B) — handover to tsr-ai-01-d5

The other 16 failures cannot be flipped by database contents; each fails on an import, a
monkeypatch target, a config key or a tuple arity. They are (C)/(D) and are **d5's** scope —
with one exception, closed here because it sits inside a file d4 already had to fix (see below).

| file | n | mechanism | cause |
|---|---|---|---|
| `tests/test_benchmark_runner.py` | 5 | `ImportError: cannot import name '_compute_auroc' from 'icdev.tools.genesis.harness.eval_harness'` — mirror drift | (D) |
| ~~`tests/test_time_decay.py::TestHybridSearchIntegration`~~ | ~~2~~ | test passes 6-tuples; `hybrid_search.hybrid_rank:210` unpacks 8 (`classification`, `compartment` added later). Production `get_all_entries` returns 8-tuples, so **the production path is correct and only the test is stale** — **fixed in d4**, not handed over: the acceptance criterion is that each fixed file passes, and this pair lives in the file d4 fixed. The two literals now carry `"CUI", ""` in the `classification`/`compartment` positions. No production code moved. | (D) |
| `tests/test_chat_manager.py::TestPendingPlaceholder` | 2 | `_process_message` returns `'[Agent sonnet] Acknowledged: hi'` — an agent-mode branch the `_patch_router` helper does not intercept | (D) |
| `tests/test_prompt_context_caching.py::TestCacheSavingsDashboard` | 3 | `get_savings_stats()` returns `enabled/backend/window_hours/summary/by_function`; the test expects `hit_count/miss_count/tokens_saved/cost_usd_saved/hit_rate_pct` | (D) |
| `tests/test_reranker_provider.py::TestLoadAnomalyConfig::test_falls_back_to_yaml` | 1 | `_load_anomaly_cfg()` returns `{}` — section absent from `args/rag_config.yaml` | (C) |
| `tests/test_sqlite_vector_store_anomaly.py::TestLoadAnomalyConfig::test_falls_back_to_yaml` | 1 | same as above | (C) |
| `tests/test_failure_triage.py::TestDiagnoseFallback` | 1 | patches `router_mod.LLMRouter.invoke`; `diagnose_task` never reaches it, so the `self_debug.diagnose` fallback is not exercised | (D) |
| `tests/test_finetune_router_integration.py::TestTwoTierFineTunedIntegration` | 1 | `_check_finetuned_override` called though `two_tier` is disabled | (C)/(D) |

Also inherited from d2's clean arm and **already resolved** — verified re-run, no code change
needed in d4:

| file | d2 clean arm | now |
|---|---|---|
| `tests/cortex/test_blueprint_routes.py` | 8 ERROR `SqliteServerRefused` | 26 passed (with `test_chat_routing.py`) |
| `tests/cortex/test_chat_routing.py` | 8 ERROR `SqliteServerRefused` | passes, incl. under the inherited `ICDEV_STORAGE_BACKEND=postgresql` env |
| `tests/genesis/test_kanban_message_injection.py` | 2 FAILED | passes |

The `SqliteServerRefused` errors were an artefact of d2's incompletely seeded database, not a
fixture defect.

`tests/workflow/test_pipeline_grader.py` (8 failures, also from d2's clean arm) is likewise
**not** (B) and belongs to d5: `make_pipeline_grader` calls
`validate_working_tree(..., budget_sec=budget_sec)`, but the `patch_collab` fixture's `_vwt` stub
has no `budget_sec` parameter, so every call raises `TypeError`, is caught by the grader's
`except Exception`, and returns `grader_error`.

## 5. Environment trap worth recording

The kanban dispatch environment exports `PYTHONPATH=C:\AI\.wt-tsh-d4-audit5` — the **parent**
worktree, not the task worktree. Left alone, `import tools.…` resolves against the parent
checkout, so a test run silently measures code this branch does not contain. Every command in
this task therefore sets `PYTHONPATH` to the task worktree root explicitly. The same environment
also exports `ICDEV_STORAGE_BACKEND=postgresql`, `ICDEV_PG_NO_FALLBACK=true` and
`ICDEV_DATABASE_URL`; `tests/conftest.py` pins SQLite for tests, but the seeding scripts run
outside pytest and need the pin set by hand.

## 6. Result

| | before | after |
|---|---|---|
| 140 previously unrun slice files | 21 failed, 2790 passed | **14 failed, 2797 passed** |
| cause-(B) failures | 5 | **0** |

Full-file check: `tests/test_time_decay.py` **30 passed, 0 failed** in the clean worktree
against a freshly seeded `data/icdev.db`. Regression across every module importing `time_decay`
plus the memory suite — `tests/cortex/test_search_adapters.py`, `test_api_surface_extractor`,
`test_memory_enhancements`, `test_rag_raptor`, `test_rag_retriever`, `test_memory_wiring`,
`test_memory_classification`, `test_memory_read_db`, `test_fts5_memory`,
`test_memory_consolidation` — 222 passed, 0 new failures.

`ruff check` clean on `tools/memory/time_decay.py`, `icdev/tools/memory/time_decay.py`,
`tests/test_time_decay.py`.
