# CUI // SP-CTI

# Can a worker worktree skip the `icdev/` mirror? NO

**Date:** 2026-09-12
**Card:** `mfx-own-07`
**Tool:** `python tools/ci/mirror_disk_readers.py [--json|--imports|--kind <k>]`
**Verdict:** **`do_not_skip`** — measured no. No change ships to the mirror, the
wheel, or `mirror_parity` from this card.

---

## The question

`icdev/tools/` is **5,409 tracked files and 88.8 MB** — 26.7% of the files and
30.3% of the bytes of every `git worktree add`, which runs under a 30 s budget
that `kph-repark-kph-repark-mfx-ci-04` forbids raising and forbids retrying.
5,316 of those 5,409 paths are the same relative path as under `tools/`: a 93%
mirror.

`mfx-own-06` (#2236) removed the parks caused by two dispatchers **colliding**.
The host-load rung was then surveyed and **refused** (#2238,
`docs/audits/mfx-own-06-host-load-gate-survey.md`): over 86 adds, a slow add
predicts the next with 0% precision, so timing the add better cannot work. That
leaves making the add **cheaper**, and CLAUDE.md (`xit-decl-02`) appears to
licence exactly that — in a source checkout the two spellings are ONE module
object, "the physical file is the one under `tools/`", so a worker session
imports from `tools/` and the mirrored bytes are never the ones running.

Three measurements were taken. Each one alone would be enough to refuse.

---

## Re-derive every number here

```bash
git ls-files -z | <sum sizes by top-two path segments>   # the size table
python tools/ci/mirror_disk_readers.py --json            # section (b)
python tools/ci/mirror_disk_readers.py --imports         # the blockers
python -m tools.kanban.host_io --survey --days 7         # why timing cannot fix it
```

The timing harness, the load generator and the gate runner are disposable
scratch scripts, reproduced in full in the appendix rather than cited by a
`.tmp/` path that will not exist tomorrow.

---

## The tree, re-derived 2026-09-12

| top-two segments | files | MB | % files | % bytes |
|---|---:|---:|---:|---:|
| **icdev/tools** | **5,409** | **88.8** | **26.7%** | **30.3%** |
| tools/dashboard | 856 | 25.3 | 4.2% | 8.6% |
| icdev/data | 2,094 | 23.6 | 10.3% | 8.0% |
| context/oscal | 3 | 10.4 | 0.0% | 3.6% |
| **tracked total** | **20,266** | **293.2** | | |

The card's figures reproduce exactly.

---

## (a) Does skipping it make the add cheaper? A little, and not where it matters

### Method

Four arms, each timed as the **whole** cost of getting a usable worktree — the
sparse arms need three git commands and a survey that timed only the first
would be measuring a `--no-checkout` add.

| arm | commands | files written | MB |
|---|---|---:|---:|
| `full` | `git -c checkout.workers=0 worktree add --detach` | 20,267 | 293.3 |
| `sparse_no_icdev_tools` | add `--no-checkout`; `sparse-checkout set --no-cone '/*' '!/icdev/tools/'`; `checkout` | 14,858 | 204.5 |
| `sparse_no_icdev` | same, excluding **all** of `/icdev/` | 12,683 | 179.7 |
| `no_checkout` | add `--no-checkout` only | 1 | ~0 |

**The arm order is ROTATED every trial**, and that correction is the first
finding. A one-shot run with `full` first read **24.95 s** against **4.88 s**
for an arm writing 73% of the files — a 5× gap for a 27% reduction, which is
not a property of the arm at all: it is git's cold pack/object cache on the
first add of a process. Rotating puts every arm in the first slot equally
often, so an order effect surfaces as within-arm variance instead of hiding
inside a between-arm difference. **Every number below is from rotated trials;
the 24.95 s is reported only as the artifact it is.**

### Quiet host — 10 rotated trials per arm

| arm | n | median | mean | min | max | over 30 s |
|---|---:|---:|---:|---:|---:|---:|
| full | 10 | 7.75 | 9.01 | 4.55 | 15.46 | 0 |
| sparse_no_icdev_tools | 10 | **6.63** | 7.34 | 3.35 | 17.43 | 0 |
| sparse_no_icdev | 10 | 7.40 | 7.43 | 3.26 | 10.63 | 0 |
| no_checkout | 10 | 0.08 | 0.10 | 0.06 | 0.17 | 0 |

Median saving **1.11 s (14.3%)** for a 26.7% file reduction — and **the ranges
overlap completely**: `full`'s fastest add (4.55 s) is faster than the sparse
arm's slowest (17.43 s). On a quiet host the effect is inside the noise, and
neither arm goes anywhere near the budget.

### Loaded host — 5 rotated trials per arm, competing full add in a loop

Load is a second `git worktree add` looping against the same disk — the shape
`mfx-own-06` measured (34.2–44.5 s adds), reproduced rather than approximated
with a synthetic filler, and deliberately **not** taking the mfx-own-06 add
lock, because the point is the load a lock cannot remove.

| arm | n | median | mean | min | max | **over 30 s** |
|---|---:|---:|---:|---:|---:|---:|
| full | 5 | 18.30 | 33.21 | 9.88 | **70.35** | **2 / 5** |
| sparse_no_icdev_tools | 5 | 15.90 | 22.98 | 9.22 | **61.19** | **1 / 5** |
| sparse_no_icdev | 5 | 9.52 | 11.72 | 4.37 | 27.85 | 0 / 5 |
| no_checkout | 5 | 0.10 | 0.13 | 0.07 | 0.26 | 0 / 5 |

Raw seconds, sorted:

```
full                  9.9  13.9  18.3  53.6  70.3
sparse_no_icdev_tools 9.2  12.1  15.9  16.5  61.2
sparse_no_icdev       4.4   6.1   9.5  10.7  27.9
```

**This is the measurement that refuses the lever on its own terms.** Dropping
the mirror still produced a **61.2 s** add — twice the budget, and a task
parked. Dropping the *entire* `icdev/` tree (37% fewer files, and far more than
this card proposes) still produced a **27.9 s** add, inside 8% of the budget.
The kill is a **load** event: under contention the distribution's tail is set
by what else is writing the disk, not by how many files this add writes. A 27%
size reduction does not move a 44 s add under 30 s, and the arm that reduced
size hardest still came within seconds of being killed.

`n = 5` per arm under load is small and the tail is what matters, so no
precise ratio is claimed from it. The claim that survives is the one that does
not need a ratio: **the sparse arm was killed too.**

### And the sparse route is three commands, not one

`_create_worktree` runs **one** `git worktree add` under `communicate(timeout=…)`,
and on expiry kills the whole process tree and removes the partial worktree so
the park describes what is on disk. The sparse route splits that into add
(0.07 s) → `sparse-checkout set` (0.03 s) → `checkout` (the entire cost), so
adopting it means re-plumbing the budget around a three-command sequence and
teaching `_remove_partial_worktree` about a worktree that is registered,
branched and half-checked-out. That is a real cost against a 1.11 s median
saving, and it is charged before any of section (b) is considered.

---

## (b) Who reads the mirror from disk — enumerated by a scanner

`tools/ci/mirror_disk_readers.py`, run over the whole tracked tree. The tool
turns on one discrimination, and reports every side of it so that no bucket can
be produced by simply not looking:

* **`disk_read`** — a reference carrying a **path separator** (`icdev/tools`,
  `icdev\tools`, `Path(...) / "icdev" / "tools"`, adjacent `"icdev", "tools"`
  call arguments).
* **`import_reference`** — a **dotted** `icdev.tools.x`: an import statement, an
  `import_module`, a `patch` target. Not a disk read.
* **`prose_mention`** — a docstring, or a path buried in a sentence. Split out
  because the naive net reported **1,077** findings against **717** real ones,
  and this module's own docstring names the path five times.
* **`import_blocker`** — the case the separator rule structurally *cannot* see.

| kind | findings |
|---|---:|
| `import_reference` | 1,751 |
| **`disk_read`** | **717** (216 files) |
| `prose_mention` | 360 |
| `import_blocker:mirror_only` | 68 |
| `import_blocker:shim` | 5 |

### The 717 disk reads, by consumer

| bucket | findings | files |
|---|---:|---:|
| `tests/` | 375 | **168** |
| `args/` (gate + census config) | 212 | 12 |
| `tools/` (runtime) | 52 | 17 |
| packaging / wheel build | 46 | 4 |
| other (`apps/`, `features/`, `data/templates/`) | 11 | 8 |
| `docs/` | 10 | 3 |
| `scripts/` | 4 | 2 |
| `context/` | 5 | 1 |
| `.github/` workflows | 2 | 1 |

The 17 runtime modules, in full:

```
tools/ace/persona_generator.py          tools/kanban/seed_nmce_kanban.py
tools/ci/perfect_score_census.py        tools/kanban/union_resolver.py
tools/ci/schema_drift_census.py         tools/presentations/generate_exec_doc.py
tools/ci/undeclared_import_census.py    tools/quality/completion_auditor.py
tools/db/shadowed_migration_audit.py    tools/refactor/swallowed_persistence.py
tools/dx/mirror_parity.py               tools/security_canvas/zt_verdict_survey.py
tools/kanban/raw_insert_census.py       tools/testing/pre_commit_check.py
tools/kanban/seed_ahx_arr_clx.py        tools/workflow/coherence_checker.py
                                        tools/workflow/validated_commit.py
```

Three censuses **declare `icdev/tools` a scan root** in their gate config —
`args/undeclared_import_gate.yaml`, `args/board_writer_gate.yaml`,
`args/perfect_score_gate.yaml`. (`args/self_root_gate.yaml` deliberately scans
`tools` only, and its own comment says why: the mirror "would double-count".)
And **190 of the 212 `args/` findings are census ENTRIES that name a mirror path
by name** — 102 in `args/kanban_raw_insert_census.txt`, 88 in
`args/undeclared_import_census.txt`. Those censuses do not merely scan the
mirror; half their contents *are* the mirror.

### The import blockers — what the separator rule cannot see

Five `tools/` files are **re-export shims** whose implementation lives only in
the mirror, derived by importing `icdev._shim.is_backcompat_shim` (the one
statement of what a shim is, never a second copy):

```
tools/billing/tier.py     tools/testing/qa_agent_runner.py
tools/llm/agent_loop.py   tools/testing/selector_healer.py
tools/showcase/synthetic_data_engine.py
```

And **68 Python modules exist ONLY in the mirror** — the finder has no
`tools/<rest>` twin to alias onto, so removing the mirror removes the module.
34 are under `tools/db/migrations/`; the other 34 include two **genesis
reflexes** (`capability_sheet_reflex.py`, `zta_monitor.py`),
`llm/agent_loop_session.py` (**referenced by 15 files** outside the mirror), and
the whole `strategos/` and `ai_augmentation/` trees. Three of them are package
`__init__.py` files — `tools/ace/`, `tools/safety/` and `tools/forecast/` have
no `__init__.py` under `tools/` at all.

The card named five shims. The real blocking set is **73 modules**.

---

## (c) Does a sparse worktree pass the gates? It passes the wrong ones and fails the suite

A real sparse worktree was built at **`935d60e85`** (main's HEAD at the time),
excluding `!/icdev/tools/`. It builds in **9.24 s** and `git status` is clean.
Both arms of section (c) — sparse and control — are that same commit and that
same directory, which is what makes the pair comparable; the `706` gated
targets there against `703` on main today is main moving between the two
readings, not a list this card touched.

| check | rc | what it actually reported |
|---|---|---|
| `git status --porcelain` | 0 | clean |
| `mirror_parity.py --files tools/db/storage.py --json` | 0 | **`not_mirrored: ["db/storage.py"]`, `clean: true`** |
| `coherence_checker --check bootstrap_parity` | 0 | pass — `icdev/data/` is kept by this exclusion |
| `undeclared_import_census.py --check` | 0 | **91 sites seen** (full tree: **179**) |
| `raw_insert_census.py --check` | 0 | **101 registered** (full tree: **203**) + a `::warning::` |
| `perfect_score_census.py --check` | 0 | 0/0 — already drained, unaffected |
| `gated_test_list.py --check --list core` | 0 | 706 targets present |
| `red_first_gate.py` | 0 | **`subjects: 0`** — a detached HEAD changed no test file. Not evidence. |
| **the gated suite (706 targets)** | **2** | **22 collection errors, ZERO tests run** (control with the mirror restored: **rc 0**, all 706 collect) |

### The gates do not refuse. They go quiet — which is worse

Not one gate blocked. Each one passed while measuring **half the tree**:

* **`mirror_parity --files` passed by reporting the file as `not_mirrored`.**
  `mfx-ci-01` made that a NOTE and never a block, on purpose (the
  missing-from-mirror backlog is ~300 files and blocking on it refuses routine
  work). In a sparse worktree *every* file is missing from the mirror, so the
  pre-commit content-drift check silently stops checking anything at all. That
  is the `|| true` shape this file already has a standing rule against, reached
  without anyone writing `|| true`.
* **`undeclared_import_census` saw 91 of 179 sites** and reported
  `0 unregistered`. A new undeclared import in the mirrored copy of a file the
  branch edits could never be caught locally; CI, with the full tree, would
  then fail — a **false pass at the hook, paid for in a red PR**.
* **`raw_insert_census` printed the trap in words:**

  ```
  ::warning::102 census entr(ies) name a site that no longer exists —
  run `python tools/kanban/raw_insert_census.py --prune` and
  LOWER raw_insert_census.raw_insert_max
  ```

  An agent or a human who follows that instruction inside a sparse worktree
  deletes **102 real grandfathered entries** and ratchets a ceiling that
  **"may only go DOWN"** from 203 to 101. The census's own policy then forbids
  putting it back. One `--prune` in the wrong checkout is a permanent,
  policy-irreversible loss of the record for half the board-writer debt.

`bootstrap_parity` passing is correct for *this* exclusion and would flip for
the `!/icdev/` arm, which drops `icdev/data/claude_bootstrap`.

### The suite does not run at all

706 gated targets, **pytest exit 2, 22 collection errors, interrupted before a
single test executed**:

```
tests/test_init_icdev_db.py              tests/test_databridge_broker.py
tests/test_agent_approval_gate.py        tests/test_eval_suggestions.py
tests/genesis/test_rubric_build_tools.py tests/test_databridge_first_grant.py
tests/test_agov_case_bundle.py           tests/test_acoic_cortex_evidence.py
tests/test_agov_case_bundle_verifier.py  tests/databridge/test_author_evidence_grant.py
tests/test_agov_case_cli.py              tests/databridge/test_floci_grant.py
tests/test_audit_row_hash.py             tests/kanban/test_protected_merge_override.py
tests/test_audit_chain_writer.py         tests/test_qa_agent_runner.py
tests/test_refinement_cycle.py           tests/cost/test_session_cost.py
tests/test_project_prefix_scope.py       tests/cost/test_task_cost_attribution.py
tests/test_approval_gate_arming.py       tests/testing/test_claude_dir_shield_checks.py
```

Every one is an `icdev.tools.*` resolution failure.

**The paired control settles the attribution.** The *same* worktree, same
commit, same path, same host, minutes later, with `git sparse-checkout disable`
restoring the mirror and **nothing else changed**:

| arm | mirror present | targets | collect rc | result |
|---|---|---:|---:|---|
| sparse | no | 706 | **2** | 22 collection errors, 0 tests run |
| control | yes | 706 | **0** | all 706 collect clean |

22 errors in one arm mean nothing until the same 706 targets are shown to
collect with the mirror present. They do. `tests/testing/test_claude_dir_shield_checks.py`,
one of the 22, collects 30 tests in the control. The errors are caused by the
absence and by nothing else.

---

## The finding that decides it: absence is not fail-closed

Three probes, one absent tree, **three different failure modes** — and only one
of them is legible:

```
from tools.billing.tier import TIER_ORDER
  -> ModuleNotFoundError: No module named 'icdev.tools.billing'          CLEAN BREAK

from tools.llm.agent_loop import run_agent_loop
  -> ImportError: cannot import name 'AgentLoopCompactionError' from
     'icdev.tools.llm.agent_loop' (C:\AI\new\FathomDesk\icdev\tools\llm\agent_loop.py)
                                                                          WRONG REPO, loud
import icdev.tools.llm.agent_loop_session
  -> ok.  __file__ = C:\AI\new\FathomDesk\icdev\tools\llm\agent_loop_session.py
                                                                          WRONG REPO, SILENT
```

`C:\AI\new\FathomDesk` is **a different repository on this machine.** The
mechanism is measured, not inferred — two editable installs both map the
top-level name `icdev`:

```
__editable___icdev_1_2_42_finder      MAPPING 'icdev' -> C:\AI\ICDev\icdev
__editable___fathomdesk_0_1_0_finder  MAPPING 'icdev' -> C:\AI\new\FathomDesk\icdev
```

`icdev` is a shared namespace package (`icdev/__init__.py` calls
`pkgutil.extend_path`), so when the worktree's own `icdev/tools` is absent the
search does not stop — it continues into whichever portion the host has, and
binds it.

**This is why the answer is not "skip it and fix the 73 blockers".** The
failure mode is not a missing module you would notice; it is a *silent bind to
another tree*. And note the second mapping: on a host with only the ICDEV
editable install, a sparse worktree's `import icdev.tools.X` would bind
`C:\AI\ICDev\icdev` — **the main checkout, at whatever commit main happens to
be on**, not the branch under test. A worker session would run its own branch's
`tools/` code beside main's `icdev/tools/` code, and every gate above would
still report green.

The mirror is not merely *read by* N consumers. Its presence is what keeps
`icdev.tools` resolving to **this** checkout.

---

## Verdict

**`do_not_skip`.** Four independent reasons, any one sufficient:

1. **The saving does not buy the outcome.** 1.11 s median (14.3%) on a quiet
   host, inside the noise; and under load the sparse arm was killed anyway at
   61.2 s, while even the far more aggressive whole-`icdev/` arm reached 27.9 s
   against a 30 s budget. The parks are a load phenomenon. `mfx-own-06` showed
   timing cannot fix them; this shows size cannot either.
2. **73 modules break outright** — 5 shims plus 68 mirror-only modules,
   including two genesis reflexes, three package `__init__.py` files and a
   module 15 files import.
3. **717 disk reads across 216 files**, of which 168 are test files, 190 are
   census entries naming mirror paths, and three censuses declare the mirror a
   scan root. The gated suite does not collect: 22 errors, 0 tests.
4. **The gates go quiet rather than refusing**, and one of them actively
   instructs a reader to `--prune` 102 census entries and ratchet a one-way
   ceiling from 203 to 101.

And the reason not to revisit it with "just fix the blockers": **absence is not
fail-closed.** With the mirror gone, `icdev.tools` binds another `icdev`
distribution on the host — measured here as a foreign repository, and on a
plainer host it would be the main checkout at a different commit. A worker
worktree that silently imports another tree's code while every gate reads green
is a strictly worse failure than a slow `git worktree add`.

## Not measured, and named rather than implied

* **A cone-mode or `--filter=blob:none` worktree.** Both change *how* the tree
  is fetched or matched, not *which paths exist* in it, so every finding in (b)
  and (c) applies unchanged. Not run.
* **A second host.** All timing is from this one Windows host. The quiet-host
  effect is small enough that a different disk could plausibly change its sign;
  the loaded-host conclusion rests on a kill that happened, which another host
  cannot un-happen.
* **`n = 5` under load.** The over-budget counts (2/5, 1/5, 0/5) are reported as
  what they are and no rate is derived from them.
* **The 34 mirror-only migrations.** `discover_migrations` walks the physical
  `tools/db/migrations/`, so those 34 appear never to run from a source checkout
  at all. That is the pre-existing shadowing CLAUDE.md already records, it is
  not caused by anything here, and it is not this card's.
* **The real lever, if one is wanted.** The measured cost is dominated by total
  bytes written under contention, and `kph-repark-kph-repark-mfx-ci-04` already
  found and untracked the largest offender (`playwright-report/`, 209.9 MB).
  Nothing here suggests a *second* such win exists in the mirror, because the
  mirror is load-bearing and `playwright-report/` was not.

## Appendix — the harness

Rotated timing harness (`timing.py`), competing-add load generator
(`load.py`), sparse gate runner (`sparse_gates.py`) and the paired control
(`control_suite.py`) were run from a scratch directory outside the repo. Their
substance is stated above in full: four arms, order rotated per trial, each arm
timed end to end, cleanup between every trial, file and byte counts taken from
a walk of the produced worktree with `.git` excluded. The one non-obvious
detail worth carrying forward is the rotation itself — without it this survey
would have reported a 5× speedup that does not exist.
