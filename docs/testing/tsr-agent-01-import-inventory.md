# TSR AGENT — agent/ACE/MCP test-file inventory (tsr-agent-01-d1)

Diagnostic only. Produced 2026-08-01 on branch `kanban/tsr-agent-01-d1`, a worktree off `origin/main`
at `180348f91`. No source or test file was modified.

Answers: which `tests/` files exercise the AGENT epic — the ACE / agent-runtime / A2A / MCP `tools.*`
packages — selected by what each file **imports**, not by filename.

## Databases seeded

All three seed steps ran clean in this worktree before the inventory was taken (a fresh worktree
starts with no `data/*.db`, so this is a prerequisite, not a formality):

| # | command | result |
|---|---------|--------|
| 1 | `python tools/db/init_icdev_db.py` | OK — `data/icdev.db`, **525 tables**, 8 wf_templates + 3 wf_document_templates seeded |
| 2 | `python tools/studio/init_db.py --json` | OK — **16 `studio_*` tables created**, 0 pre-existing |
| 3 | `python tools/db/migrations/311_studio_event_tables_rls_columns/up.py` | OK — `Migration 311 applied.` |

Verified after the fact against the SQLite file the tests actually read — not just exit codes:

```
data/icdev.db   8,937,472 bytes   541 tables   16 studio_*
studio_event_sources       classification=True  tenant_id=True
studio_workflow_triggers   classification=True  tenant_id=True
studio_trigger_events      classification=True  tenant_id=True
```

Both env pins are load-bearing and must be repeated by any follow-on task in this worktree:

```bash
export PYTHONPATH="C:\AI\ICDev\.tmp\worktrees\tsr-agent-01-d1"   # else: ModuleNotFoundError: No module named 'tools'
export ICDEV_STORAGE_BACKEND=sqlite                              # else the seed half-lands against PG
```

The ambient environment has `ICDEV_STORAGE_BACKEND=postgresql` and `PYTHONPATH=C:\AI\ICDev` (the
shared checkout, not this worktree). Without the sqlite pin, steps 2 and 3 write their tables to
PostgreSQL while step 1 writes SQLite; all three still exit 0 and `data/icdev.db` still looks
populated, but `tests/conftest.py` forces sqlite and the suite then fails on `no such table:
studio_*`.

## Scope

The task named 5 packages. **All five exist as named**, each a real package with `__init__.py`:

| package | modules | notes |
|---------|---------|-------|
| `tools/ace` | 38 | ACE Co-Worker Engine — the bulk of the epic |
| `tools/mcp` | 40 | MCP servers + gateway |
| `tools/agent_runtime` | 19 | headless agent runtime |
| `tools/a2a` | 8 | A2A protocol client/server |
| `tools/agents` | 7 | CLI-adapter registry (claude/codex/copilot/local) |

### Selection regex

```
(?<![\w.])(?:icdev\.)?tools\.(?:ace|agent_runtime|a2a|agents|mcp)(?![\w])
```

Applied to every `*.py` under `tests/` (not just `test_*.py`, matching the CANV/NET slice convention,
so `conftest.py` and helper modules are caught). The leading `(?<![\w.])` stops
`tools.agentic_ai_canvas`-style false positives; the trailing `(?![\w])` keeps `tools.agents` from
swallowing `tools.agent_runtime`. Both the canonical `icdev.tools.*` and the legacy `tools.*` shim
namespaces are matched.

## Results — 123 files

| # | package | files matching | of which a real `import` |
|---|---------|----------------|--------------------------|
| 1 | `tools/ace` | 70 | 64 |
| 2 | `tools/mcp` | 33 | 32 |
| 3 | `tools/agent_runtime` | 18 | 17 |
| 4 | `tools/a2a` | 6 | 4 |
| 5 | `tools/agents` | **1** | **1** |

123 distinct files; 115 import at least one in-scope package directly (including via
`importlib.import_module`), 8 reference one only through a `mock.patch("tools.…")` target string.
Per-package counts sum above 123 because a file may exercise more than one package.

**Count cross-check.** The bare `Select-String -Path "tests\*.py"` the task specifies returns **83**
files; that pattern only scans the top level of `tests/`. Run recursively it returns **123**, which
matches the classifier exactly, and the per-package attribution sums back to 123 once the 3
multi-package files are de-duplicated (`tests/browser/test_four_seams.py`,
`tests/test_ace_persona_query.py`, `tests/mcp/test_registry_handler_coverage.py`). The 40-file
difference is all under `tests/<subdir>/` — `agent_runtime/`, `genesis_auto/`, `studio/`, `mcp/`,
`cortex/`, `docmod/`, `e2e/`, `rag/`, `browser/`, `ace/`. **Do not use the top-level-only form; it
silently drops a third of the epic**, including all 13 `tests/agent_runtime/` files.

### `tools/agents` is exercised by exactly one test file

`tests/test_a2a_registry.py` is the only file in the repository that imports `tools.agents`, and it
reaches only `a2a_registry`. Six of the package's seven modules are named by no test at all:

```
tools/agents/registry.py
tools/agents/adapter_base.py
tools/agents/adapters/claude_cli.py
tools/agents/adapters/codex_cli.py
tools/agents/adapters/copilot_cli.py
tools/agents/adapters/local_llm_router.py
```

This is a genuine coverage gap, not an artifact of the regex — the same shape as the `tools/sre`
finding in the NET slice. A test-suite-remediation epic cannot remediate tests that were never
written; this needs a card of its own.

### Other never-tested modules in scope

Modules that no `tests/` file names, by either `tools.pkg.mod` or `from tools.pkg import mod`:

| package | modules / total | never named |
|---------|-----------------|-------------|
| `tools/mcp` | 24 / 40 | `a2a_bridge_server`, `builder_server`, `devsecops_server`, `generate_registry`, `infra_server`, `integration_server`, `llm_proxy_server`, `lsp_server`, `maintenance_server`, `marketplace_server`, `mbse_server`, `mcp_debug_wrapper`, `mcp_scanner`, `modernization_server`, `ontology_server`, `requirements_server`, `research_server`, `simulation_server`, `supply_chain_server`, `standalone/{builder,compliance,core,knowledge,maintenance}` |
| `tools/a2a` | 5 / 8 | `agent_client`, `agent_entrypoint`, `agent_server`, `task`, `task_lease` |
| `tools/ace` | 3 / 38 | `profile_generator`, `simulate_roles`, `skill_adapter` |
| `tools/agent_runtime` | 1 / 19 | `__main__` |

The `tools/mcp` figure is dominated by per-domain MCP servers, which are thin dispatch shims over
tools tested elsewhere — lower risk than the `tools/a2a` gap, where the protocol server, client and
task-lease logic are all untested.

### Patch-string-only files (8)

These reference an in-scope package solely as a `mock.patch(...)` target, never via `import`. They
are included in the slice — a patch target is still a real coupling and still breaks when the target
module moves — but they will not surface an import-time error, so they are the wrong place to look
first when triaging collection failures:

| file | package(s) referenced |
|------|-----------------------|
| `tests/test_ace_mirror_parity.py` | ace |
| `tests/test_agent_loop_wiring.py` | ace |
| `tests/test_auto_resolver_heartbeat.py` | a2a |
| `tests/test_co_learning_store.py` | ace |
| `tests/test_component_registry.py` | ace |
| `tests/test_docgen.py` | ace |
| `tests/test_heartbeat_a2a_health.py` | a2a |
| `tests/test_pkg_subprocess_namespace.py` | agent_runtime |

## Scope gap — `tools/agent` (singular) is not in the task's regex

`tools/agent` is a real 19-module package (`agent_executor`, `mailbox`, `team_orchestrator`,
`a2a_discovery_server`, `a2a_agent_card_generator`, `token_tracker`, `authority`, `topology`, …) and
is distinct from both `tools/agents` and `tools/agent_runtime`. **21 test files import it; 18 of
those are not in this slice.**

It was **not** silently merged into the slice — the task named five packages and adding a sixth would
invent scope the card did not ask for (same call the NET slice made on `tools/deploy`). The candidate
list is provided separately as `docs/testing/tsr-agent-01-agent-pkg-candidates.txt` so whoever
schedules `tsr-agent-01-d2` can make the decision explicitly. Given the epic is named "agent" and
`tools/agent` holds the A2A discovery server and the agent executor, including it is the likely
right answer — but that is a card-owner decision, not a scanner's.

## Filename-keyed files deliberately excluded

Selection is by import, so 50 files whose *names* suggest the epic are not in the slice. Their actual
imports place them elsewhere, and the largest cluster is unambiguous: **16 `test_agent_readiness_*`
files import `tools.ai_augmentation`, not any agent package** — they belong to the AI epic. Others
map to `tools.studio` (4 of the 5 `test_dwo_*mcp*` files → FLOW; `test_dwo_mcp_allowlist.py` imports
no `tools.*` at all), `tools.saas` (`test_mcp_http_e2e`,
`test_mcp_oauth`, `test_rest_api_agents`), `tools.security` (`test_mcp_tool_authorizer`,
`test_agent_output_validator`), `tools.browser`, `tools.llm`, `tools.genesis`, `tools.testing`.

This cuts the other way from the NAV lesson (where a filename-keyed suite existed with no matching
package). Here every named package is real, so import-based selection is sound — but the 50-file
exclusion is worth recording so a later session does not "discover" them and re-scope the epic.

## Overlap with prior slices — 22 of 123

Unlike NET/CANV (which shared 78 files), the AGENT epic is largely self-contained. 22 files are
already claimed by a sibling slice:

| epic | shared files |
|------|--------------|
| `flow` | 8 |
| `comp` | 7 |
| `doc` | 7 |
| `canv` | 4 |
| `intel` | 3 |
| `core` | 2 |
| `dash` | 2 |
| `net` | 2 |

(Counts sum above 22 because 7 files are claimed by more than one sibling epic —
`tests/test_component_registry.py` by five, `tests/test_nav_llm_01_router_invoke.py` by four,
`tests/test_network_doc_lifecycle.py` by three.)

| file | contents | use |
|------|----------|-----|
| `docs/testing/tsr-agent-01-slice.txt` | all 123 | the complete AGENT blast radius |
| `docs/testing/tsr-agent-01-exclusive.txt` | **101** files claimed by no other slice | safe to run/fix in parallel with the other epics |

## Collection verified

The seeded worktree collects the full slice cleanly — this is the evidence that the DB seed took, not
just that the scripts exited 0:

```
pytest <123 files> --collect-only -q --timeout=120
2377 tests collected in 33.10s          # zero collection errors
```

## Reproducing

```bash
cd C:/AI/ICDev/.tmp/worktrees/tsr-agent-01-d1
export PYTHONPATH="C:\AI\ICDev\.tmp\worktrees\tsr-agent-01-d1"
export ICDEV_STORAGE_BACKEND=sqlite
pytest $(cat docs/testing/tsr-agent-01-exclusive.txt | tr '\n' ' ') -rfE --timeout=120
```

Use `-rfE`, not `-rf`: `-rf` hides ERRORs, and collection errors are the expected failure mode for a
freshly seeded worktree. `--timeout` guards the known `test_production_audit.py` wedge.

No baseline pytest **run** was performed under this card — its acceptance criterion is the inventory
and the seeded DBs, and collection alone is enough to prove the seed. Establishing the AGENT failure
baseline is `tsr-agent-01-d2`'s job. At 2,377 tests the slice is large; sharding it the way
`tsr-dash-01-d2` did is advisable.

## Artifacts

| file | contents |
|------|----------|
| `docs/testing/tsr-agent-01-slice.txt` | 123 file paths, one per line |
| `docs/testing/tsr-agent-01-exclusive.txt` | 101 paths not shared with any prior slice |
| `docs/testing/tsr-agent-01-agent-pkg-candidates.txt` | 18 `tools.agent` (singular) importers — proposed scope extension |
| `docs/testing/tsr-agent-01-inventory.json` | per-file package attribution, per-package counts, regex, overlap lists, never-tested modules |
| `docs/testing/tsr-agent-01-import-inventory.md` | this document |
