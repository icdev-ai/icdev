<!-- CUI // SP-CTI -->
# Audit: 512 of 622 declared capability units have never been consumed

- **Task ID:** wire-audit-01
- **Type:** research / audit
- **Date:** 2026-08-27
- **Scope:** the 11 capability classes in `args/capability_consumption.yaml`, their budgets in
  `args/liveness_gate.yaml`, and the requirement layer of the kanban done-gate
- **Method:** the platform's own detectors, re-run against the live PostgreSQL board — see
  *Reproduction* below. No new telemetry was added for this audit.

## Why this exists

The board reads 189 project cards at 100% and 3,571 tasks done. `capability_consumption` says
**512 of 622 declared capability units have never been consumed**. The question this audit was
asked is not "how many" but *"the build is 100% and not wired — again."*

The honest answer is that **the capability side is largely healthy and the requirement side is
not**, and the headline number points at the wrong half.

## Reproduction (deterministic)

```bash
# From repo root, against the live board (PostgreSQL).
python tools/awareness/capability_consumption.py                          # the 11-class table
python tools/awareness/capability_consumption.py --class agent_approval_rule --json
python tools/awareness/capability_consumption.py --class extension_hook_point --json
python tools/awareness/capability_consumption.py --class mcp_dispatch_tool --json
python tools/workflow/coherence_checker.py --check capability_liveness --json

# Budget history — has a grandfathered allowance ever been RAISED?
git log -p --follow -- args/liveness_gate.yaml | grep -E "^[+-]  [a-z_]+: [0-9]+"
```

The requirement-side numbers, as SQL against `kanban_tasks` / `kanban_verifications`:

```sql
SELECT COUNT(*) FROM kanban_tasks;                                    -- 3571
SELECT COUNT(*) FROM kanban_tasks
  WHERE acceptance_criteria IS NOT NULL AND TRIM(acceptance_criteria) <> '';   -- 267
SELECT COUNT(*) FROM kanban_tasks WHERE completed_via_bypass = 1;     -- 1362
SELECT result, COUNT(*) FROM kanban_verifications GROUP BY result;
SELECT COUNT(DISTINCT task_id) FROM kanban_verifications;             -- 1993
```

## Verdict legend

| Verdict | Meaning |
|---|---|
| `healthy` | Measured, budgeted, and the budget has ratcheted **down** over time. |
| `explained` | A large inert balance whose cause is already analysed in a cited document. Not a new finding. |
| `breach` | Over its recorded budget today. |
| `unmeasured` | Nothing was counting this at all before this audit. |

---

## Finding 1 — the inert count is 510 grandfathered, 2 breaching · `healthy`

Measured 2026-08-27, 30-day window, `backend=postgresql`:

| class | declared | consumed | inert | budget | verdict |
|---|---:|---:|---:|---:|---|
| `mcp_dispatch_tool` | 472 | 4 | 468 | 467 | **breach (+1)** |
| `agent_approval_rule` | 25 | 0 | 25 | 25 | at budget |
| `cortex_backend` | 7 | 1 | 6 | 6 | at budget |
| `cortex_facade` | 9 | 5 | 4 | 4 | at budget |
| `mcp_tool_authorization` | 5 | 1 | 4 | 4 | at budget |
| `extension_hook_point` | 4 | 0 | 4 | 4 | at budget |
| `verified_claim` | 7 | 6 | 1 | 0 | **breach (+1)** |
| `reflex` | 77 | 77 | 0 | 0 | clean |
| `prompt_template` | 14 | 14 | 0 | 0 | clean |
| `audit_chain` | 1 | 1 | 0 | 0 | clean |
| `skill_optimizer` | 1 | 1 | 0 | 0 | clean |
| **total** | **622** | **110** | **512** | | |

**510 of the 512 are explicitly grandfathered with recorded budgets.** Only two units exceed
their allowance, and each by exactly one.

## Finding 2 — the budgets ratchet down, and have been raised once ever · `healthy`

`args/liveness_gate.yaml` has 11 commits. Every movement except one is a *drain*:

| class | movement | driver |
|---|---|---|
| `agent_approval_rule` | 62 → **25** | `rem-cap-05` — 37 of the 62 could never be recorded; the denominator was wrong |
| `extension_hook_point` | 10 → **4** | `hcx-live-gate-01` — *"delete the four points nothing dispatches"* |
| `skill_optimizer` | 1 → **0** | GEPA records a decision, not only an apply |
| `prompt_template`, `audit_chain`, `reflex` | dropped from the file | drained to zero |
| `mcp_dispatch_tool` | 466 → **467** | the only raise ever, +1 |

That single raise is why the class breaches today: `cef-rsv-01` registered `cortex_resolve`,
making 472. CLAUDE.md now forbids raising it. **The mechanism is working** — this is a debt
being paid down, not one accumulating.

## Finding 3 — the two large balances are already explained · `explained`

Neither is a new discovery and neither should be re-derived here.

**`mcp_dispatch_tool` 468/472 — see `docs/reference/mcp_tool_consumption.md`.** That document
classifies the registry as **73 reachable / 21 external-only / 377 genuinely unused**, and
records that **439 of 472 can only ever earn a `refused` row** because gate `MCP-WF-001`
default-denies and `args/security_gates.yaml::mcp_workflow_tools` names only 32 tools. Its §7
explicitly says **do not lower the 467 budget** on its strength.

> **The number measures "not routed through one audited door", not "never used".** Consumption
> for this class is `studio_mcp_dispatch_audit`, written only by the Studio executor. The stdio
> MCP server, the SaaS HTTP/SSE surfaces and the agent runtime all dispatch tools and write
> `runtime_invocations` instead. Reading 468 as "468 dead tools" overstates the problem; the
> repair named in CLAUDE.md is routing the other entry points through the same audit.

All 19 recorded events are acceptance traffic (`nist_lookup` 8, `health_check` 7,
`studio_run_start` 3, `studio_run_status` 1), with `principal_id=''` and
`caller_source='default (no caller declared)'`.

**`agent_approval_rule` 25/25 — see `docs/security/approval-gate-reachability.md`.** The 25 are
the `irreversible`-tier tools: `git_force_push`, `terraform_destroy`, `k8s_deploy`,
`merge_pull_request`, `delete_video`, `kanban_set_done` and 19 more. Zero have ever been
evaluated.

> **The gate is wired and reachable; it lacks traffic.** `tools/agents/adapters/claude_cli.py`
> spawns a *separate* `claude` process that imports no ICDEV module, and **3,214 of 3,217 tasks
> carry `executor_type=claude_cli`**. `runtime_invocations` holds 1,489 `surface='agent'` rows
> and **none of them is a tool call** — every row is a whole task run. The cited document states
> the consequence plainly: the count cannot move by improving the gate.

This is worth stating in its own right: **the approval control for 25 irreversible operations
has never evaluated one.** That is a real exposure, and it is a traffic-routing problem, not a
liveness-budget problem.

## Finding 4 — `verified_claim` breaches by one, and the cause is Finding 3 · `breach`

`approval_park_is_whole` has run **22 times and returned `unmeasurable` all 22**, never once a
measured verdict. It compares approval-gate parking state, and the approval subsystem it reads
has no traffic (Finding 3) — so both sides of the comparison are always empty. Two empty sides
are correctly reported `unmeasurable` rather than `agrees`.

For contrast, the mechanism demonstrably works: `held_task_lease_has_a_live_holder` has caught
**2 real disagreements** in the same period.

## Finding 5 — the requirement layer was never measured · `unmeasured`

This is the finding that answers the question the audit was asked. Nothing in the platform was
counting these:

| | |
|---|---|
| tasks with `acceptance_criteria` populated | **267 / 3,571 — 7.5%** |
| tasks completed via bypass | **1,362 / 3,571 — 38%** |
| `kanban_verifications` rows that are `bypassed` | **1,715** — against `passed` 900, `failed` 561, `phantom` 10 |
| tasks with no verification row at all | **1,578 / 3,571 — 44%** |

`bypassed` is defined in `tools/idp/delivery_events.py` as *"verification was skipped, e.g. a
force-done with an audited reason — that is an **unverified** change, not a failed one."* The
distinction is the codebase's own and this audit keeps it: **38% of tasks landed unverified**,
which is not the same claim as 38% failed.

Three mechanisms make that silent:

1. **`_check_acceptance_criteria` is dead code.** `tools/genesis/reflexes/kanban.py:9069` is
   defined and called nowhere. It is the declared-but-unconsumed defect living inside the
   acceptance checker. It would also be a rung that cannot fail: it returns `True` on
   no-criteria, on DB error, on judge-unavailable and on judge-exception.

2. **An empty criterion reads as a pass.** `conformance_reviewer.review_conformance` returns
   `status="not_run"`, `review_passed=None` when the column is empty, and
   `pr_watcher._enforced_done_ok` treats `None` as allowed — `# None = not judged, allowed`. At
   7.5% populated, **92.5% of tasks clear the conformance rung vacuously.**

3. **Verification short-circuits on "bytes moved".** `_run_verify_checks` Check 0 is a git-first
   fast path: if the branch has commits and the task is not `_is_dangerous_task`, it returns
   `True` immediately and checks 1–5 never run.

And there is nowhere to declare the answer even if someone wanted to: `args/projects.yaml` epics
carry only `{key, title, priority}` — **no deliverable, no acceptance test, no consumer field** —
and 56 of 189 `briefs` are empty.

## What this audit does NOT conclude

- **Not** that 512 capabilities are dead. 510 are budgeted and the budgets are falling; the two
  largest balances are explained by cited analysis; `mcp_dispatch_tool`'s 468 is a
  measurement-coverage figure, not a usage figure.
- **Not** that the budget for `mcp_dispatch_tool` should be lowered to 73.
  `mcp_tool_consumption.md` §7 forbids exactly that.
- **Not** that `--force-done` should be blocked. It is the sanctioned path when there is
  genuinely nothing to land, and it already records a reason.

## Recommended action

Carried by the WIRE programme — `docs/programmes/wired-not-just-built.md`:

| card | action |
|---|---|
| `wire-req-01` | delete the dead checker; refuse an empty criterion at seed and at the done gate; **survey before arming** — the bypass rate is 38% and this repo stands a check down at 1.63% |
| `wire-run-01` | a newly declared unit must produce one real consumption event before its card can close |
| `wire-reg-01` | close the registration checklist — 3 of its 8 points have a real gate |

Nothing here proposes raising or adding a budget.
