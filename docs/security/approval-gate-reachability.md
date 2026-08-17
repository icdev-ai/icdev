# Security-control finding — the agent approval gate never evaluates a declared rule

CUI // SP-CTI

**Task:** rem-cap-03 · **Control:** AC-3 / AC-6 / CM-7 / AU-2 · **ADR:** D394, D395
**Verdict:** the gate is **not reachable** from the dispatch path, and it is
**disarmed by default** on every in-process path but one.
**Measured:** 2026-08-16, live board (`ICDEV_STORAGE_BACKEND` default), worktree
`kanban/rem-cap-03`.

> **STATUS — partially remediated, 2026-08-16 (rem-cap-04).** The in-process
> half is fixed: the resolver is config-aware, the shipped config arms the gate
> in `dry_run`, and `agent_approval_log` now holds gate-written rows. Everything
> below is the finding **as measured before that change** and is left intact as
> the record; **§8.1 states what landed and what is still open.** The two rows
> in §0 marked † no longer hold. Findings 1 and 3 are unchanged: the `claude_cli`
> dispatch path is still a separate process no Python gate reaches, and
> `enforce` is still unarmed pending tool enumeration.

`args/agent_approval_policy.yaml` enumerates **62** tools by reversibility tier.
`tools/awareness/capability_consumption.py` reports **0 consumed**. This document
answers the question the count could not: is that 62 a backlog to work off, or a
control that does not exist?

**It is a control that does not exist.** Three independent reasons, each
sufficient on its own, and a fourth that means the count could not have reached
zero even if the first three were fixed.

---

## 0. Summary of measurements

| Measurement | Value | Where |
|---|---:|---|
| Tools declared by tier in `args/agent_approval_policy.yaml` | 62 | `reversible` 24, `recoverable` 13, `irreversible` 25, `unknown` 0 |
| Rows in `agent_approval_log` | 4 | none written by the approval gate — see §4 |
| Rows the **approval gate** has ever written | **0** | §4 |
| Kanban tasks dispatched `executor_type = claude_cli` | **3,214** | `kanban_tasks`, vs 3 `ollama_local` |
| Agent-loop invocation sites in `tools/` | 12 | §2 |
| …that pass `approval_gate=` explicitly | **1** | `tools/studio/executors/agent_executor.py:556` |
| † `args/agent_runtime.yaml` → `command_approval_mode` | `enforce` | and it does not arm the gate — §2. Now `dry_run`, and it does — §8.1 |
| † `_resolve_approval_gate(None)` on this checkout | **`None` (no gate)** | `icdev/tools/llm/agent_loop.py:804`. Now returns a hook — §8.1 |
| Studio workflow runs ever recorded | 26 (25 failed, 1 success) | `studio_workflow_runs` |
| Studio `agent`-node steps ever recorded | **0** | `studio_mcp_dispatch_audit` holds 19 rows, all `mcp-*`/probe steps |
| Declared tools that can *never* appear in `agent_approval_log` | **37 of 62** | §5 |
| SAG toolset tools that classify `unknown` (would halt) | **47 of 58 — 81%** | §6 |

Reproduce the first block with:

```bash
python tools/agent_runtime/approval_gate.py --list-policy --json
python tools/awareness/capability_consumption.py --class agent_approval_rule --json
```

---

## 1. Finding 1 — the dispatch path structurally cannot reach the gate

**CONFIRMED.** This was the card's hypothesis and it is correct.

`tools/agents/adapters/claude_cli.py::spawn` / `::invoke` start a **separate
`claude` process** via `subprocess.Popen` / `subprocess.run`
(`tools/agents/adapters/claude_cli.py:265`, `:304`). That process imports no
ICDEV module, so no ICDEV Python gate is in its tool-call path — the gate is not
merely disabled there, it is in a different address space.

The adapter package does not import either gate:

```bash
$ grep -rn "approval_gate\|agent_tool_gate" tools/agents/
tools/agents/adapters/claude_cli.py:198:        compensating controls — ``agent_runtime/approval_gate.py`` and
tools/agents/adapters/claude_cli.py:199:        ``studio/executors/agent_tool_gate.py`` — are **not in this adapter's
```

Two docstring lines saying the gate is absent. No import.

Scale: **3,214 of 3,217** kanban tasks on the live board carry
`executor_type = claude_cli`; the remaining 3 are `ollama_local`. So effectively
every autonomous ICDEV build in the board's history ran on the one path where no
declared approval rule can be evaluated.

This part is already a stated decision — D394 / D395, written up in
[`agent-vendor-permission-bypass.md`](agent-vendor-permission-bypass.md), which
says in §2 exactly this ("the two gates usually named as the compensating
controls for this flag are not in this adapter's path"). rem-cap-03 adds the
dispatch counts and the log-provenance proof in §4; it does **not** revisit the
flag, which is out of scope by the card's own instruction.

The control that *is* in the spawned CLI's path is `.claude/hooks/pre_tool_use.py`
(armed since exa-bench-05). That is a real control — but it reads
`args/file_access_tiers.yaml` and its own check list, **not**
`args/agent_approval_policy.yaml`. It cannot consume a declared approval rule, so
it does not make any of the 62 reachable.

## 2. Finding 2 — the in-process gate is disarmed by default, and the config that says `enforce` does not arm it

**NEW.** This is the more serious half, because it affects paths that D394 never
claimed to give up.

`icdev/tools/llm/agent_loop.py::_resolve_approval_gate` (line 791) turns the
`approval_gate=` argument into a hook. With the default `None`:

```python
if approval_gate is None:
    mode = (os.environ.get("ICDEV_AGENT_APPROVAL_MODE") or "").strip().lower()
    if not mode or mode == "off":
        return None
```

It reads the **raw environment variable** and returns `None` — no gate — when it
is unset. It never calls `approval_gate.resolve_mode()`, which is the function
that layers `args/agent_runtime.yaml → subsystems.approval.command_mode` under
the env var. Measured on this checkout:

| Question | Answer |
|---|---|
| `ICDEV_AGENT_APPROVAL_MODE` set in the environment? | **no** |
| `load_config().command_approval_mode` | `enforce` |
| `approval_gate.resolve_mode()` | `enforce` |
| `_resolve_approval_gate(None)` | **`None` — NO GATE** |

So the operator-facing configuration reports the gate as enforcing, the gate's
own mode resolver agrees, and the loop runs ungated. `args/agent_runtime.yaml`
even documents the mapping (`command_mode: enforce   # env:
ICDEV_AGENT_APPROVAL_MODE`), which is true of `resolve_mode()` and false of the
only caller that decides whether the gate exists at all.

Eleven of the twelve agent-loop invocation sites take that default:

| site | what it is | passes `approval_gate=`? |
|---|---|---|
| `tools/studio/executors/agent_executor.py:546` | Studio `node_type: agent` step | **yes** (`:556`) |
| `tools/genesis/reflexes/kanban.py:5254` | kanban rubric build loop (`KANBAN_RUBRIC_LOOP`), toolset includes `write_file` / `patch_file` / `run_command` | no |
| `tools/agents/adapters/local_agent.py:223` | the `local_agent` AgentAdapter — the in-process dispatch alternative | no |
| `tools/ace/coworker_thread.py:884`, `:900` | ACE co-worker roles | no |
| `tools/ace/agent_tools.py:1018` | ACE child-agent delegation | no |
| `tools/ace/eval_runner.py:119` | ACE eval harness | no |
| `tools/cortex/api.py:380`, `:392` | Cortex agent launch | no |
| `tools/gameday/team_runner.py:145` | GameDay team runner | no |
| `tools/agent_runtime/runtime.py:489` | SAG interactive/unattended runtime | no |
| `tools/llm/architectures/adapters.py:228` | ReAct architecture adapter | no |

None of those nine modules mentions `approval_gate` anywhere
(`grep -c approval_gate` returns 0 for each).

`agent_executor.py`'s own docstring names the defect precisely, having
anticipated it:

> Both are passed to `run_agent_loop` explicitly rather than left to
> `ICDEV_AGENT_APPROVAL_MODE`, so an agent node is gated in a deployment that
> never set that variable.

This is that deployment. One surface took the precaution; eleven did not, and the
config knob that was supposed to cover them does not reach the code that reads it.

## 3. Finding 3 — the one gated surface has never run an agent step

`agent_executor.py` is genuinely gated (AGENT-WF-001 authorization chained to the
ars-appr-01 reversibility gate, `_build_approval_hook`, line 674). It has never
carried an agent-node step on this board:

* `studio_workflow_runs` — 26 rows, **25 `failed`**, 1 `success`.
* `studio_mcp_dispatch_audit` — 19 rows, every one an MCP-node or probe step
  (`step_id` = `mcp-studio_run_status`, `gateprobe`, `probe`), all from a single
  verification session on 2026-08-09. No `agent` step among them.

So the only wired path is also the only unexercised one. The gate is not "wired
but idle in the recent window" (`idle_this_window`, which
`check_capability_liveness` deliberately does not fail on) — it has zero lifetime
consumption.

## 4. Finding 4 — the 4 rows in `agent_approval_log` are not gate decisions

The card reads the 4 rows as "the machinery has fired — but not through these
rules". Measured, it is stronger than that: **the approval gate has never written
a row at all.**

| decided_at | tool_name | tier | rule | actor |
|---|---|---|---|---|
| 2026-08-15T06:51:55Z | `trust_delta_review` | `review` | `claim_guard` | `smoke-tester` |
| 2026-08-15T07:03:40Z | `trust_delta_review` | `review` | `claim_guard` | `admin@icdev.local` |
| 2026-08-15T12:28:46Z | `trust_delta:promote` | `trust_delta` | `hitl_delta` | `admin@icdev.local` |
| 2026-08-15T12:30:07Z | `trust_delta:promote` | `trust_delta` | `hitl_delta` | `admin@icdev.local` |

Neither `review` nor `trust_delta` is a member of `approval_gate.TIERS`
(`reversible` / `recoverable` / `irreversible` / `unknown`), and neither
`claim_guard` nor `hitl_delta` is a rule `classify()` can emit. These rows come
from `tools/quality/hitl_delta.py` / the delta-review surface reusing
`agent_approval_log` as a shared append-only audit sink — which is legitimate
reuse of `record_decision()`, and is documented as such in
`docs/features/cost-budget-downgrade-gate.md`.

The consequence is a measurement hazard, and it is live today. The substrate
probe reports the table as healthy:

```
$ python tools/awareness/capability_consumption.py --probe-substrate agent_approval_log
substrate                                    status                    rows
agent_approval_log                           populated                    4
Findings: 0
```

`populated`, zero findings — yet not one of those 4 rows was written by the
capability the table exists to record. A reader who checks `COUNT(*)` concludes
the gate is partially alive. The query that actually answers the question is:

```sql
SELECT COUNT(*) FROM agent_approval_log
 WHERE tier IN ('reversible','recoverable','irreversible','unknown');   -- 0
```

`probe_agent_approval_rule` gets this right by accident rather than by design: it
counts per `tool_name` scoped to the declared list, and since no declared tool
appears it reports `events: 0`. Had `hitl_delta` happened to reuse a tool name
that the approval policy enumerates, the class would have reported a false
consumption. Tier is the discriminator; the probe does not use it.

## 5. Finding 5 — 37 of the 62 declared rules can never be recorded, so the count cannot reach zero

**NEW, and it blocks remediation of the other four.**

The policy sets `require_approval_tiers: [irreversible, unknown]`. The hook built
by `build_approval_hook` returns early for anything else
(`tools/agent_runtime/approval_gate.py:822`):

```python
# 2. Reversible / recoverable: allowed without ceremony and without a
#    row — the audit trail is for decisions, not for every read.
if not cls.requires_approval:
    _emit(GateEvent(..., recorded=False, ...))
    return None
```

That is the right design for an audit trail. But `probe_agent_approval_rule`
counts **all four tiers** as declared:

```python
declared = [str(name).lower() for tier in TIERS
            for name in (tools_by_tier.get(tier) or [])]
```

62 = 24 `reversible` + 13 `recoverable` + 25 `irreversible` + 0 `unknown`. The 24
+ 13 = **37** in the non-approval tiers are unmeasurable **by construction**:
`read_file`, `list_files`, `grep`, `git_diff`, `done`, `search_knowledge`,
`write_file`, `patch_file` and the rest could be evaluated ten thousand times a
day and `agent_approval_log` would still show zero for each.

So even a fully wired, heavily exercised gate would report **≥37 of 62 inert
forever**, and `args/liveness_gate.yaml`'s `agent_approval_rule: 62` budget could
never be lowered past 37. This is the shape ICDEV has already named once: a
capability that records only one of its outcomes is unmeasurable — "ran and
correctly auto-allowed" is indistinguishable from "never ran".

The honest fix is to scope `declared` to `require_approval_tiers` and report the
other 37 as *not measurable by design* rather than as inert. It is a probe fix,
not a gate fix, and it must land **with or before** any wiring work — otherwise
whoever wires the gate will watch the number refuse to move and reasonably
conclude the wiring failed.

> **RESOLVED — rem-cap-05, 2026-08-16.** `probe_agent_approval_rule` now reads
> `require_approval_tiers` from the policy (never a hardcoded tier list, so an
> operator who adds `recoverable` moves those 13 tools into the measurable set on
> the next run) and enumerates the excluded tools in
> `extra.not_measurable_by_design` — count, tiers and tool names, so a tier change
> is visible rather than a quietly shrunken denominator. `declared` is now **25**,
> and `args/liveness_gate.yaml`'s budget was lowered 62 → 25. A policy with an
> empty `require_approval_tiers`, and an `agent_approval_log` without the `tier`
> column, both report **UNMEASURABLE** rather than a clean zero. The gate itself
> is unchanged: an auto-allowed call still writes no row, which is the correct
> design for an audit trail of decisions. Findings 1–4 remain open.
>
> The same commit fixed §4's near-miss: the probe filters
> `agent_approval_log` on `tier IN (reversible, recoverable, irreversible,
> unknown)`, so `hitl_delta`'s `review` / `trust_delta` rows can no longer be
> counted as gate consumption if one of them ever reuses a tool name the policy
> enumerates. Pinned by
> `tests/test_capability_consumption_approval_tiers.py`.

## 6. Why "just arm it" is the wrong remediation

`default_tier: unknown`, `require_approval_tiers: [irreversible, unknown]`, and
the default approver is `console_approver`, which **denies on EOF** — correct
fail-closed behaviour, and fatal in a non-interactive run. Classifying the tool
names the SAG toolsets actually register against the live policy:

| tier | tools | share |
|---|---:|---:|
| `reversible` | 7 | 12% |
| `recoverable` | 4 | 6% |
| **`unknown`** | **47** | **81%** |
| | **58 distinct** | |

Per bundle, `compliance` (13 tools), `security` (9), `canvas` (7), `govcon` (5)
and `kanban` (5) are **entirely** unenumerated. Flipping
`ICDEV_AGENT_APPROVAL_MODE=enforce` today would refuse roughly four in five tool
calls on every SAG surface, unattended, with no human on the other end.

The kanban rubric-loop toolset is the exception and the reason a staged fix is
cheap: 9 tools, 8 enumerated (6 `reversible`, 2 `recoverable`), only
`run_command` falling to `unknown` — and `run_command` is a declared
`command_tool`, so with a real command string the content patterns classify it
rather than the default tier.

CLAUDE.md already states the rule this obeys: *never enable a security check
without a fire-rate survey first* — the lesson of the `|| true` PreToolUse
wrapper, where eight of twelve checks were refusing 4.86% of real work the moment
the neutraliser came off. Arming this gate blind repeats that mistake with an
81% refusal rate instead of 4.86%.

## 7. Why this is a security-control finding and not backlog

The vendor permission system is off by decision (D394). The documents that
justify that decision name `approval_gate.py` among the compensating controls —
`docs/security/mcp-tool-authorization.md` lists it as the "Reversibility
classifier", `docs/security/evidence-integrity-file-existence.md` lists it as
"Classifies each call by reversibility; halts irreversible ones for approval",
and `docs/security/sandbox-coverage.md` Gap 46 records it as covered because the
module and its tests exist.

The module does exist. Its tests pass. Its policy is well-designed and
fail-closed. And it has evaluated **zero** tool calls in production, on a board
that has dispatched 3,214 autonomous builds. That is precisely the
file-existence-as-compliance-evidence defect CLAUDE.md describes: an inert module
counted as a satisfied control. A compensating control that never evaluates is
not a weaker control — it is the absence of one, wearing the paperwork of one.

Note also that the composable-policy layer built on top of this gate
(`tools/agent_runtime/policy_engine.py`, `policy_composition.py`,
`policy_builtins.py`, which wrap `approval_gate.classify()` as a `reversibility`
policy) has **zero non-test consumers** — a second declared-but-unconsumed layer
stacked on the first.

## 8. The smallest change that would make the gate reachable

Not the adapter flag. D394 is a separately-argued decision and the card
explicitly forbids touching it; the spawned CLI is a different process and no
Python gate can be inserted into it without abandoning subprocess dispatch
entirely.

The smallest change is three lines in one function, staged so that arming is
measured before it is enforced:

1. **Make `_resolve_approval_gate` config-aware.** Replace the direct
   `os.environ.get("ICDEV_AGENT_APPROVAL_MODE")` read in
   `icdev/tools/llm/agent_loop.py:804` with a call to
   `approval_gate.resolve_mode()`, which already layers env over
   `args/agent_runtime.yaml`. One function, no signature change, no call-site
   change. This alone makes all eleven default call sites gated in any deployment
   whose config says so — which is the shipped default.
2. **Ship `subsystems.approval.command_mode: dry_run`, not `enforce`.**
   `dry_run` calls `_finish()`, so it **writes the audit row** and then allows.
   That is the fire-rate survey: it makes the gate consumed and measurable
   without a liveness risk, and `agent_approval_log` becomes the corpus for
   deciding what to enumerate. Promote to `enforce` in a later task, per bundle,
   once the 47 unknowns are enumerated or explicitly accepted.
3. **Fix `probe_agent_approval_rule` first or alongside** (§5), so the number can
   actually move when the wiring lands.

Seeded as **`rem-cap-04`** (steps 1–2) and **`rem-cap-05`** (step 3).

What deliberately stays out of scope: the `--dangerously-skip-permissions` flag,
`args/agent_approval_policy.yaml`'s tiers, and the `agent_executor.py` wiring,
which is already correct.

**A second candidate wiring point, noted and not chosen.** hcx-live-01
(`dd9f6c6`, merged 2026-08-16) added a `TOOL_EXECUTE_BEFORE` extension-point
dispatch to `tools/agent_runtime/dispatch.py`, so every SAG tool call now passes
a behavioral-tier hook that may refuse. That is a legitimate place to hang the
reversibility gate, and it would cover the SAG surface without touching
`agent_loop.py` at all. It is not the smallest change, for two reasons: it
reaches only the SAG dispatch path (not ACE, Cortex, GameDay or the kanban
rubric loop, which call `run_agent_loop` directly), and it would make the gate's
arming depend on extension registration rather than on the mode the operator set
in `args/agent_runtime.yaml` — a second declared-vs-consumed hop of exactly the
kind this finding is about. Fix the resolver first; that covers all eleven sites
with one function.

---

## 8.1 What landed — rem-cap-04, 2026-08-16

Steps 1 and 2 above. Step 3 landed separately as rem-cap-05 (#1758). **The gate
now exists as a control.** `enforce` is deliberately NOT armed and is still
pending the enumeration task — see "What is still open" below.

**1. `_resolve_approval_gate` is config-aware.** The direct
`os.environ.get("ICDEV_AGENT_APPROVAL_MODE")` read in
`icdev/tools/llm/agent_loop.py` is now
`tools.agent_runtime.approval_gate.resolve_mode()`, which layers env *over*
`args/agent_runtime.yaml` over the selected posture. No signature change, no call
site changed. `tools/llm/agent_loop.py` was verified to be a pure re-export shim
(it re-exports `_resolve_approval_gate` by name for object identity), so there is
one copy to change, not two.

One asymmetry was added deliberately: when `approval_gate=None` and the gate
module cannot even be imported to *ask* the mode, the resolver returns `None`
and logs a warning rather than denying. `None` means "use the operator's
default", so there is no request to fail closed on; refusing every tool call on
all eleven default sites because a config layer broke is an outage, not a
safety property. An explicit `True` — which *is* a request — keeps its
deny-everything behaviour unchanged.

**2. `subsystems.approval.command_mode: dry_run` ships**, in `args/` **and** in
the packaged `icdev/data/args/` mirror. `_find_config_path` probes both layouts,
so shipping the pin in only one would leave a wheel install falling through to
the posture's `enforce` — the 81% refusal, in the deployment nobody tests
locally. The two files are asserted byte-identical.

**A consequence the card did not name: this pins a POSTURE-GOVERNED knob.**
hcx-post-01 ships all four such keys commented out so that selecting a posture
actually moves them, and `tests/agent_runtime/test_permission_postures.py`
asserted the shipped config pins nothing. The pin is still the right call, and
the inertness it costs was measured rather than assumed: `off`
(danger-full-access) and `dry_run` **both** write the audit row and **both**
allow, so for that posture the pin changes the recorded reason and not the
behaviour. The invariant was kept rather than deleted — pins are now enumerated
by name with a written reason in `SANCTIONED_PINS`, and the exact *value* is
asserted, so an unreviewed edit from `dry_run` to `enforce` fails the test
instead of slipping past an "is it pinned?" check. The four `test_posture_selection.py`
cases that broke were testing the posture MECHANISM, which was working correctly
and reporting the pin loudly exactly as designed; they now point the loader at
their own config, per the convention `tests/agent_runtime/test_config.py`
already documents.

**The hard-block leg was surveyed before arming, because `dry_run` does not
disarm it.** `build_approval_hook`'s step 1 consults
`.claude/hooks/pre_tool_use.py` via `tools.airgap.hook_compat.run_pre_tool_check`
and **denies regardless of mode** — so arming eleven previously-ungated call
sites is a real enforcement change, not a purely observational one, and
CLAUDE.md requires a fire-rate survey first. Measured across every tool the 13
SAG bundles register:

| probe | fires |
|---|---:|
| 63 distinct tools x {empty input, realistic input} = 126 probes | **0** |
| 3 genuinely dangerous inputs (`rm -rf /`, `git push --force`, a write to `/etc/cron.d/`) | **3** |

Zero false refusals, and the leg is not inert. (The tier census reproduced 47
`unknown` exactly; the distinct-tool count is 63 here against §6's 58, a
counting difference in bundle dedup that does not move the `unknown` figure.)

**Proof the control now exists.** Live board, PostgreSQL, immediately after the
change — the first row the approval gate has ever written, from a default call
site that passed no `approval_gate=` argument:

```
id: 5   decided_at: 2026-08-16T21:03:06Z   session_id: local-2ad8368cdc06
tool_name: generate_ssp        tier: unknown       rule: default_tier
decision: approved             mode: dry_run       arg_keys: system
reason: dry_run: would have required approval
detail: 'generate_ssp' is not enumerated in agent_approval_policy.yaml...
```

`SELECT tier, COUNT(*) FROM agent_approval_log WHERE tier IN (...) GROUP BY tier`
returned `[]` before and `{unknown: 1}` after. Note `arg_keys` records key names
and never operands, consistent with the platform's telemetry stance.

Note the second row that did **not** appear, because it is the more instructive
result: a `write_file` call in the same exercise was classified `recoverable`
and auto-allowed **without** a row, by design — `require_approval_tiers` is
`[irreversible, unknown]`, so `dry_run` surveys the tools that would have
halted, not every tool call. The `agent_approval_log` corpus this produces is
therefore exactly the enumeration backlog and nothing else.

**What is still open.** `enforce` remains unarmed and must stay that way until
the 47 `unknown` tools are enumerated in `args/agent_approval_policy.yaml` or
explicitly accepted, per bundle — `compliance` (13), `security` (9), `canvas`
(7), `govcon` (5) and `kanban` (5) are entirely unenumerated. Promote per
bundle, from the `agent_approval_log` corpus this change starts collecting; do
not flip the single config line to do it globally. Also still true and unchanged
by this task: findings 1 and 3 stand — the `claude_cli` dispatch path (3,214 of
3,217 board tasks) is a separate process that no Python gate reaches, and Studio
has still never run an `agent`-node step.

---

## Provenance

Every number above was produced by query or by running the module, not by
reading. Reproduce with:

```bash
python tools/agent_runtime/approval_gate.py --list-policy --json
python tools/awareness/capability_consumption.py --class agent_approval_rule --json
python tools/awareness/capability_consumption.py --probe-substrate agent_approval_log
python tools/agents/capability_matrix.py --json
grep -rn "approval_gate\|agent_tool_gate" tools/agents/
grep -rn "approval_gate=" --include=*.py tools/
```

`_resolve_approval_gate(None) is None`, the `declared`-vs-recordable split, the
per-bundle tier tallies, and the `agent_approval_log` provenance were measured by
importing `tools.agent_runtime.approval_gate`, `icdev.tools.llm.agent_loop` and
`tools.agent_runtime.toolsets` directly and by `SELECT` against the live board.
