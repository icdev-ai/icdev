# WIRE — prove we built to the requirement, and wired it

> Programme document for the WIRE stream. Approved by the owner 2026-08-27.
> Evidence: `docs/audits/capability-liveness-2026-08-27.md`.

## Context

The board reads 189 cards at 100% and 3,571 tasks done, and the platform's own detector reports
512 of 622 declared capability units never consumed. The owner's question was not *how many* but
**"this is not the first time the build is 100% but not wired — add a process that verifies we
build to meet the requirement and wire it properly."**

The audit found the headline number points at the wrong half.

**The capability side is healthy.** 510 of the 512 are grandfathered with recorded budgets, and
those budgets ratchet *down* — `agent_approval_rule` 62→25, `extension_hook_point` 10→4,
`skill_optimizer` 1→0, with `prompt_template`/`audit_chain`/`reflex` drained out of the file
entirely. Exactly **one** raise has happened in the file's history (`mcp_dispatch_tool` 466→467)
and CLAUDE.md now forbids it. The two large balances are already analysed in
`docs/reference/mcp_tool_consumption.md` and `docs/security/approval-gate-reachability.md`.

**The requirement side was never measured, and it is where the defect lives.**
`acceptance_criteria` is populated on **7.5%** of tasks; **38%** of tasks completed via bypass;
`bypassed` verification rows (1,715) outnumber `passed` (900); **44%** of tasks have no
verification row at all. Three mechanisms make that silent, and all three are verified in source:

1. `tools/genesis/reflexes/kanban.py:9069 _check_acceptance_criteria` is **dead code** — the
   symbol appears exactly twice in `tools/`, once as its own `def` and once in a prose comment.
2. `tools/ci/pr_watcher.py:411` — `# int 0 or bool False — conformance failed (None = not
   judged, allowed)`. An empty criterion yields `review_passed=None`, which reads as a pass. At
   7.5% populated, **92.5% of tasks clear that rung vacuously**.
3. `tools/ci/pr_watcher.py:413` — `if result in ("pass", "passed", "bypassed")`. A skipped
   verification is accepted as a passed one.

## Decisions taken with the owner (2026-08-27)

| # | Question | Decision |
|---|---|---|
| 1 | What proves a capability is "wired"? | **Runtime — it must have RUN once.** Extend `capability_liveness`; a new unit stays red until it produces one real consumption event. The sanctioned fix is to dispatch it, never to raise a budget. |
| 2 | How hard to tighten the requirement side? | **Mandatory criteria at seed for build/fix, plus a bypass census** that may only shrink. `--force-done` is not blocked. |
| 3 | How far to extend consumption coverage? | **Make existing mechanisms binding first.** No new capability classes until these gates have caught something. |
| 4 | Arming posture | **Survey before arming, without exception.** This repo stands a check down at a 1.63% refusal rate on routine work; the bypass rate is 38%. |

## What exists today, and is reused rather than rebuilt

| Need | Existing machinery |
|---|---|
| Runtime consumption measurement | `tools/awareness/capability_consumption.py` — 11 classes, telemetry-only, `unmeasurable` never collapsed into zero |
| Per-task wiring gate | `coherence_checker::check_capability_liveness`, deliberately in the **fast** tier so a capability wired to nothing in a task's own diff is caught per-task |
| "Is X imported by anything" | `tools/awareness/edge_deriver.py::get_dependents(ref, mechanical_only=True)` — exists, gated nowhere |
| Ratcheting census, by name | `args/kanban_raw_insert_census.txt` + `tools/ci/census_growth.py::CENSUSES` (set monotonicity, not a ceiling) |
| Fire-rate survey before arming | `tools/ci/e2e_flake_survey.py`, `tools/kanban/landed_dispatch_survey.py` |
| Sanctioned "no consumer by design" | `args/external_only_surfaces.yaml` — **obligations, not a budget** |

That last one is the pattern this programme inherits, and its own header states why:

> *"This is NOT a suppression list and it has NO BUDGET. Every key below only ADDS an
> obligation… Declaring a module external-only is strictly more work than deleting it and
> strictly more work than wiring it up. That is deliberate — the easy path must never be
> 'declare it and move on'."*

## Approach

| Card | Slice |
|---|---|
| `wire-gate-00` | MANUAL-MODE GATE. Held: this programme rewrites the done gate itself, so a runner building it would be a session modifying the gate that judges it. |
| `wire-audit-01` | The audit write-up. **Done** — `docs/audits/capability-liveness-2026-08-27.md`. |
| `wire-req-01` | Delete the dead checker; refuse an empty criterion at seed (`task_factory`) and stop `NULL`-because-empty reading as a pass; bypass census. **Report-only until the survey says otherwise.** |
| `wire-run-01` | `capability_consumption --new-units --since <ref>`: name units whose *declaration* is in this diff and which have zero lifetime events. Consulted by the **done gate**, not CI — the author dispatches it once. |
| `wire-reg-01` | Close the registration checklist: 3 of 8 points have a real gate, 2 are partial and point the wrong way, 3 are prose. Add `new_module_registration`. |

## Verification

1. Every number in the audit is re-derived by the commands in its own Reproduction block.
2. `wire-req-01` ships nothing armed until `tools/kanban/bypass_survey.py` reports a
   would-have-refused rate, split right/wrong, **in the PR body** — the same discipline by which
   `landed_dispatch_survey` (kpr-fix-03) declined to arm at 2.67%.
3. `wire-run-01` asserts `--new-units` names a unit added in a synthetic diff and stays silent
   for one that already has events; `test_runs_in_the_fast_tier` still passes.
4. `wire-reg-01` fires on a synthetic new CLI tool with no manifest row and passes on one with
   it; a full-tree run does not fire on the existing tree.
5. Red-first proof for every behavioural change; `ruff check` on the changed set; mirror sync.

## What this programme will not do

- Raise or add a budget in `args/liveness_gate.yaml`. It ratchets down only.
- Lower `mcp_dispatch_tool` to 73 — `mcp_tool_consumption.md` §7 forbids it.
- Add capability classes for the 56 blueprints / 63 IQE adapters / 28 MCP servers / 23 skills
  that have none. They have structural checks, and inventing telemetry to satisfy a gate is the
  failure this codebase warns about.
- Block `--force-done`. The census makes it finite and visible instead.
