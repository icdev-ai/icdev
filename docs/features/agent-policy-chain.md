# CUI // SP-CTI

# Agent policy chain — ALLOW / DENY / ASK over the reversibility gate

**Task:** `exa-policy-01` (EXA — External Adoption, POLICY epic)
**Module:** `tools/agent_runtime/policy_engine.py`
**Config:** `args/agent_policy_chain.yaml`
**Tests:** `tests/test_agent_policy_engine.py`
**Audit table:** `agent_approval_log` (migration `20260803002224`, append-only)

## What already existed, and is unchanged

`tools/agent_runtime/approval_gate.py` classifies a tool call by
**reversibility** and halts the ones it cannot vouch for. Three properties make
it stronger than the equivalent in the project this work was adapted from
(omnigent), and none of them were touched:

1. `default_tier: unknown` with `unknown` in `require_approval_tiers` — a tool
   has to be **named** in `args/agent_approval_policy.yaml` to be automatic. An
   allowlist that fails open is decoration.
2. Irreversible **content patterns escalate before** the per-tool tier. A shell
   tool is only as reversible as the string it is handed.
3. **Downgrade patterns apply only to `command_tools`.** Incidental argument
   text cannot make `git_push` look recoverable.

`approval_gate.py` and `args/agent_approval_policy.yaml` are byte-identical
after this change. `classify()` is imported and called, not reimplemented.

## What it could not express

The gate answers exactly one question with exactly two outcomes: *auto-allow*
or *ask a human*. It is single-level, pattern-only and stateless, so it has
nowhere to put:

- a verdict that depends on **who** is asking, on **usage**, or on **session
  state**;
- an outright **refusal**. The gate's strongest verdict is "ask", which means
  every rule it can express is answerable by a tired operator at 3am. Some
  rules should not be;
- more than one opinion at a time.

## The layer

A policy is a plain function:

```python
def protected_branch(event: PolicyEvent) -> PolicyDecision:
    if event.arguments.get("branch") == "main":
        return PolicyDecision(DENY, "main is protected", policy="protected_branch")
    return PolicyDecision(ALLOW, "not a protected branch")
```

`PolicyEvent` carries `event_type`, `target`, `arguments`, `actor`, `usage`,
`session_state`, `session_id` and `metadata`. `PolicyDecision` carries an
`effect` — `ALLOW`, `ASK` or `DENY` — and a **reason**, plus the reversibility
`tier`/`rule`/`detail` when a policy establishes one.

### Chain semantics

`evaluate(event)` runs the configured chain in order:

| Effect | Behaviour |
|--------|-----------|
| `ALLOW` | proceed to the next policy |
| `ASK` | recorded, **does not** short-circuit — a later `DENY` must still win |
| `DENY` | **short-circuits.** The rest of the chain is never consulted. |

The answer is the strictest effect any policy returned, then raised to the
per-event-type `floors` value if that floor is stricter. A floor can only raise
an answer, never lower one — `floors: {tool_call: ask}` pins "every tool call
goes to a human" for a change-freeze window without editing a single policy.

`DENY` short-circuits because there is no answer a later policy could give that
would make a refusal not a refusal, and evaluating on would only invite a
"but this one said allow" reading of the log.

### The reversibility gate as one policy

`reversibility_policy()` is a pure translation of the gate's verdict:

```
classification.requires_approval  True  -> ASK
classification.requires_approval  False -> ALLOW
```

and it carries `tier`, `rule` and `detail` through unchanged. It **never
returns DENY** — the gate's contract is that a human may still authorise what
it dislikes, and this layer does not tighten it by stealth. A rule that must be
refusable without a human goes in its own policy.

## Fail-closed inventory

Every failure mode resolves toward *more* human involvement, never less:

| Failure | Resolution |
|---------|-----------|
| A policy raises | `on_policy_error` — `deny` by default. `allow` is **not an accepted value**; a config typo resolves to `deny`. |
| A policy returns a nonsense value or an unrecognised effect | `DENY` |
| The chain is empty | `ASK` — nobody vouched for the call |
| A name in `chain:` is not registered | A `DENY` naming itself. **Not** silently skipped — a chain that quietly drops a policy stops enforcing what its own config says it enforces, which is the exact failure this card exists to stop. |
| The config file is missing or unreadable | Falls back to the reversibility-only chain, which is itself fail-closed |
| A `floors:` value is unparseable | Treated as no floor (the chain's own answer stands), never as a lowered one |
| The approver raises | Denies |
| `.claude/hooks/pre_tool_use.py` hard-blocks the call | Blocked before any policy runs, and never escalated to a human — a hard block is not a question |

`dry_run` and `off` apply to `ASK` only. They are an escape hatch for an
escalation, not for a refusal: a `DENY` blocks in every mode.

## Audit

Every decision is appended to the existing append-only `agent_approval_log`
**through `approval_gate.record_decision()`**. Delegating rather than issuing a
second INSERT is deliberate: that function is the single place that knows the
no-argument-values rule — argument **key names** plus a SHA-256 of the flattened
input, never a value — and a second writer here would be a second chance to
break it. Tool arguments can carry CUI and this repo is public.

Two smaller consequences of the same rule:

- `PolicyEvent.__repr__` elides argument values, so a traceback or a debug log
  cannot become the leak the audit row was designed to avoid.
- The `--json` CLI emits policy names, effects, reasons and rules — never
  arguments.

Column mapping:

| Column | Value |
|--------|-------|
| `decision` | `approved` / `denied` — the existing vocabulary, unchanged |
| `rule` | `policy_chain:<policy>:<effect>` — distinguishes a machine evaluation from a human approval in the same table |
| `tier` | the reversibility tier when a policy established one, else `unknown`. Effects never enter this column; the tier vocabulary stays the tier vocabulary. |
| `detail` | the `[EFFECT] policy: reason` summary of every decision in the chain |

`audit.log_allow` defaults to `true`, which is what makes "every decision is
recorded" literally true. `deny` and `ask` outcomes are always recorded and have
no switch.

## Using it

```python
from tools.agent_runtime.policy_engine import build_policy_hook

run_agent_loop(..., approval_gate=build_policy_hook())
```

`build_policy_hook()` has the same `PreToolUseHook` contract as
`build_approval_hook()` — return `None` to allow, a string to halt — so it is a
drop-in. It is opt-in; the default `approval_gate=True` path still builds the
plain reversibility gate.

```bash
python tools/agent_runtime/policy_engine.py --list-policies --json
python tools/agent_runtime/policy_engine.py --evaluate run_command \
    --input '{"command": "git push --force"}' --json
```

## Deliberately not in this task

Named here so the next task does not have to guess, and so nothing ships as a
declared-but-unconsumed key in the YAML:

- **Three-level composition** (session → agent → server, stricter first) and
  `state_updates` — `exa-policy-02`. There is no `scope:` key in
  `agent_policy_chain.yaml` yet, on purpose.
- **Builtin policies** (per-session tool-call cap, repo/branch allowlist, risk
  accrual) — `exa-policy-03`. The registry and the `usage`/`session_state`
  fields they need are here; the policies are not.
- **`cost_budget` as a downgrade gate** — `exa-policy-04`.
