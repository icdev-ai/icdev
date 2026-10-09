# HGX V&V — end-to-end verification and portability proof (hgx-vv-01)

**Card:** HGX — Harness Agent Parity and Graph Runtime
**Base:** `origin/main` @ `8dea45b5b`
**Date:** 2026-08-11
**Scope:** verify the WHOLE card, not one slice. All 13 prior HGX tasks
(`hgx-gate-00/01/02`, `agent-01`, `ctxw-01`, `cx-02`, `eval-01`, `exec-01`,
`exec-02`, `guard-01`, `guard-02`, `port-01`, `port-02`) were `done` before this
ran.

Every result below is reproducible from a clean checkout. Where something is
red, it is either FIXED here or attributed to pre-existing state with the
evidence that establishes it.

---

## 1. Backward compatibility — templates are not reordered

The claim `hgx-par-01` shipped on was a COMMENT in the runner: "at
`max_parallel == 1` this walks the graph in exactly `_resolve_dag` order". This
task turns it into a re-runnable check, `tools/studio/dispatch_parity.py`, which
recomputes the PRE-parallel `static_order()` and replays the CURRENT
`get_ready()`/`done()` loop at one slot for every template on disk, then diffs
the two sequences.

```
python -m icdev.tools.studio.dispatch_parity --gate
templates found:    62
templates compared: 62
declaring max_parallel: 1
  context/workflow_templates/multi_angle_review.yaml
PASS — every compared template dispatches in baseline order
```

**Result: PASS.** 62 templates across both roots (`args/workflow_templates` 42 +
`context/workflow_templates` 20), zero divergence. Exactly ONE declares
`max_parallel` — `multi_angle_review.yaml`, which `hgx-tmpl-01` added *after*
the card was written. So all 61 pre-existing templates leave `max_parallel`
unset and their executed order is byte-for-byte what it was before par/cond.

The card says "61 templates"; there are 62 now because of that one addition.

Pinned by `tests/test_dwo_dispatch_parity.py` (8 tests) and registered in BOTH
CI allowlists, so a future dispatch change cannot quietly reorder shipped
workflows.

> A path bug in this checker would surface as ZERO templates compared, which is
> a silently-passing check rather than a failure — hence the explicit
> `templates_compared > 0` assertion, and why the module resolves its root by
> MARKER rather than a fixed `parents[N]` (it ships as two copies at different
> depths; a fixed index is off by one for the `tools/` copy).

## 2. Regression net

```
pytest tests/test_dwo_*.py tests/studio tests/test_agent_loop*.py
```

| Run | Result |
|-----|--------|
| Before this task | **906 passed, 1 failed** |
| After the fix in §2.1 (+ new tests, + `tests/ci/test_gated_test_list.py`) | **936 passed, 0 failed** (3m37s) |

The card's KNOWN pollution note (`tests/test_workflow_hitl_engine.py` failing
~20 tests alongside `tests/studio`) did NOT appear — that file is not in this
selection, so it was neither triggered nor masked here.

### 2.1 The one failure — FIXED (pre-existing, not this card)

`tests/test_dwo_mcp_allowlist.py::test_package_mirror_matches_the_root_copy`

`args/security_gates.yaml` and its packaged mirror
`icdev/data/args/security_gates.yaml` had drifted by 28 lines — the whole
`agent_detection_rules` gate block (`agov-det-07`) was present in the root copy
and ABSENT from the mirror.

**Attribution — pre-existing on clean `origin/main`:**

- `git diff origin/main -- args/security_gates.yaml icdev/data/args/security_gates.yaml`
  was EMPTY, i.e. this worktree was byte-identical to main for both files.
- Diffing the two files *as they exist on `origin/main`* reproduces the same
  28-line gap.
- Introduced by `8dea45b5b` (#1521, the most recent commit on main), which added
  the gate to the root copy without refreshing the package mirror.

**Fix:** synced that ONE file. The full `sync_package_tree.py` would have
touched 7,144 files — far outside a V&V task's scope. Consequence of the bug: an
installed wheel shipped a `security_gates.yaml` with no agent-detection gate.

## 3. Portability

- **`Test (Windows)` is GREEN on `8dea45b5b`** — the exact base of this branch.
  Verified at JOB level, not just run conclusion:
  `gh run view 31538253075 --json jobs` → `success | Test (Windows)`.
- **Windows suite re-run locally** on Windows 11 (all 13 allowlisted targets):
  **655 passed** in 2m37s.
- **LF/CRLF round-trip** — `tests/genesis/test_rubric_build_tools.py` (the
  `hgx-exec-01` regression pin, asserts on RAW BYTES) is listed in BOTH
  `args/ci_test_files/core.txt` (ubuntu `test` job) and
  `args/ci_test_files/windows.txt`. Both jobs are green on the base commit, so
  "passes on both OSes" is structurally enforced rather than asserted once.
- `tests/test_toolset_portability.py` is likewise in both lists.

## 4. Security

| Check | Result |
|-------|--------|
| `bandit -r tools/ --severity-level medium` | **0 HIGH**, 1422 MEDIUM (B608 SQL 1294, B310 114, B314 13, B704 1) — repo-wide pre-existing; `tools/` is byte-identical to `origin/main` here, so this IS the main baseline |
| bandit on the new module | **0 findings at any severity** |
| Agent node cannot reach a tool outside its allowlist; `requires_approval` still blocks until its gate is approved | **81 passed** — `test_dwo_agent_allowlist.py`, `studio/test_agent_tool_gate.py`, `studio/test_mcp_executor_approval.py`, `test_dwo_mcp_allowlist.py`, `test_dwo_gate_durability.py` |
| `tools/integrity/engine.py --gate` | see below |

**On the integrity engine.** The acceptance criterion's command as written
(`python tools/integrity/engine.py --gate`) does not do anything on its own — the
CLI needs a source. Pointed at the repo itself (`--source .`) it returns
`verdict=quarantine, risk_score=100.0, gate.blocked=true` in
`mode=provenance_blind` with `trust_score: 0.0, authorized_edges: 0`. That is the
engine doing its job on an UNREGISTERED third-party ingest, which is what it is
for; it is not an HGX defect and not a repo-health signal. Confirmed by
attribution: of the 237 findings recorded under assessment 15, **0 are in either
file this task changed**.

*Addendum (salvage, PR #1523).* Documenting that command surfaced a second,
separate defect in it: `engine.py` imported first-party code at import time with
no `sys.path` bootstrap, so started as `python tools/integrity/engine.py` it died
with `ModuleNotFoundError` before argparse was ever reached — in both the `tools/`
and `icdev/tools/` copies. The original run never saw that, because a shell whose
`PYTHONPATH` already holds the repo root masks it; it saw only the missing-source
message. Fixed in this PR (kax-conflict-05 gate), so the command above now runs
by path with an empty `PYTHONPATH` and reports its usage.

## 5. Gates

| Gate | Result |
|------|--------|
| `ruff check tools/ tests/ --select E,F,W --ignore …` (the command CI actually runs) | **All checks passed** |
| `ruff check .` (bare, default ruleset) | 605 errors — repo-wide, outside the enforced gate, pre-existing and unchanged by this task |
| `coherence_checker.py --all --fix --gate` | **EXIT=1**, one check: `insert_schema_parity` — FALSE POSITIVE, see below |
| `companion.py --sync --write --json` | ran clean: 10 instruction files, 9 MCP configs, 63 skills translated |

### 5.1 `insert_schema_parity` — false positive, attributed

Reported: `tools/workflow_canvas/blueprint.py:974: INSERT INTO studio_workflows
names missing column 'source_doc_text'`.

The column EXISTS. Evidence:

- `tools/db/migrations/223_wfc_doc_regen.sql` adds it.
- The live dashboard returns it: `GET /api/studio/workflows` includes
  `"source_doc_text": null` on every row.
- This worktree's `data/icdev.db` has **41 tables** (against 391+ in a real
  install) and its `studio_workflows` has 12 columns, missing `source_doc_text`,
  `style_fingerprint` and `regen_artifact_path`.

The checker's own output says it: *"37 validated against 40 live sqlite
table(s)"*. It validated against a fresh-worktree SQLite database stuck at an
early migration state, not against the schema the code runs on. The `Doc
Coherence Gate` job is green on the base commit.

### 5.2 What `--fix` and `--sync` proposed, and was NOT committed

Both gates mutate the tree. Two edits were reverted as out of scope for a V&V
task, and are recorded here rather than silently dropped:

- `coherence_checker --fix` appended an `## Auto-Registered (Coherence Fix)`
  block to `tools/manifest.md` naming two UNRELATED tools
  (`genesis/code_reload.py`, `kanban/conflict_resolvers.py`) with backslash
  paths, into the thin index rather than a topic shard.
- `coherence_checker --fix` added an autouse fixture to
  `tests/test_dwo_bus_subscriber.py`.
- `companion.py --sync` rewrote 9 MCP dotfiles; its ONLY substantive diff on this
  box was reverting a deliberate local debug config
  (`mcp_debug_wrapper.py` → `unified_server.py`, playwright `cmd` → `node`).
  Three other agent sessions were active; clobbering their debug wiring is not
  this task's business.

## 6. E2E — BLOCKED BY ENVIRONMENT

Criterion 6 (chat → intent router → confirm → graph run → per-node SSE →
approval gate → result delivery, driven through `/studio/workflows` with
Playwright screenshots) could **not** be executed on this box. This is a tooling
blocker, not a product failure, and it is pre-existing:

- Playwright MCP: `Browser "chrome-for-testing" is not installed`, and this
  environment has no npm to install it.
- Selenium path: the only vendored driver is
  `vendor/drivers/chromedriver/147/chromedriver.exe` against installed **Chrome
  151.0.7922.77** → `session not created: This version of ChromeDriver only
  supports Chrome version 147`. No `msedgedriver` is vendored (Edge is
  151.0.4129.78). `driver_manager.py --probe` falls through to
  `selenium_manager`, which needs network to fetch a matching driver.

What WAS established instead, without a browser:

- `/studio/workflows` serves **HTTP 200**; `GET /api/studio/workflows` returns
  saved workflows.
- The behaviours the E2E spec exists to prove are covered by the passing suite in
  §2/§4 — gate durability, approval blocking, per-node allowlisting, run resume.

The full `e2e:dwo_workflow` spec additionally REQUIRES restarting the dashboard,
and its own text forbids running it against a dashboard another session owns.
Three other sessions were active and the dashboard on :5050 is not this
session's to restart, so that scenario was not attempted.

**This criterion is not satisfied and is not claimed to be.** Unblocking it needs
a driver matching Chrome 151 vendored (or network access for
`selenium_manager`), which is an environment change outside this task.

## 7. Mirror parity

`tools/studio/` and `icdev/tools/studio/` are FULL COPIES, not shims
(`workflow_runner.py` is byte-identical between them), so the new module ships to
both and is verified byte-identical by
`test_parity_module_is_mirrored_into_the_package`.

Files changed by this task:

| File | Mirrored |
|------|----------|
| `tools/studio/dispatch_parity.py` | ✅ `icdev/tools/studio/dispatch_parity.py`, byte-identical, asserted by a test |
| `icdev/data/args/security_gates.yaml` | the mirror itself — this is the §2.1 fix |
| `tests/test_dwo_dispatch_parity.py` | tests are not mirrored |
| `args/ci_test_files/{core,windows}.txt` | mirror refreshed at release by `sync_package_tree.py`; the files' own header says do not hand-edit the `icdev/data/args/` copy, and `tests/ci/test_gated_test_list.py` asserts existence + floor, not equality |
| `tools/manifest/icdev-studio-low-code-no-code-platform.md` | shard row appended (`merge=union`) |

---

## Incidental finding — two Studio MCP tools are dead

Not caused by this card, and NOT fixed here (out of scope for V&V):

```
studio_list_workflows → list_workflows() takes 0 positional arguments but 1 was given
studio_list_templates → list_builtin_templates() takes 0 positional arguments but 1 was given
```

The gateway dispatches every tool as `handler(args: dict)`, but these two point
straight at `tools.studio.workflow_editor` functions whose signatures are
keyword-only (`def list_workflows(*, shared_only: bool = False)`), so they raise
on every call. These are Phase-72 registry entries.

This card's own tools are wired correctly — `handle_studio_run_start` /
`_status` / `_resume` in `tools/mcp/gap_handlers.py` all take `args: dict`, which
is why `hgx-cx-03`'s headless surface works where the older reads do not.

Worth a follow-up card.
