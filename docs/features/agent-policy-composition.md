# CUI // SP-CTI

# Three-level policy composition — session, agent, server — and session state

**Task:** `exa-policy-02` (EXA — External Adoption, POLICY epic)
**Module:** `tools/agent_runtime/policy_composition.py`
**Config:** `args/agent_policy_chain.yaml` (server level + `agent:` block),
`<profile_dir>/policy_chain.yaml`, `$ICDEV_AGENT_POLICY_CHAIN_SESSION` / `_AGENT`
**Tests:** `tests/test_agent_policy_composition.py`
**State table:** `agent_session_policy_state` (migration `20260812054330`, MUTABLE)
**Audit table:** `agent_approval_log` (migration `20260803002224`, append-only)
**Sandbox coverage:** Gap 63 in [docs/security/sandbox-coverage.md](../security/sandbox-coverage.md)

## What already existed, and is unchanged

`exa-policy-01` ([agent-policy-chain.md](agent-policy-chain.md)) made a policy a
function `PolicyEvent -> PolicyDecision` returning ALLOW / ASK / DENY plus a
reason, wrapped `approval_gate.classify()` as the `reversibility` policy, and gave
the chain a DENY that short-circuits and is never offered to the approver.
`approval_gate.py` and `args/agent_approval_policy.yaml` remain untouched, and so
does everything in `policy_engine.py` except one additive field
(`PolicyDecision.state_updates`, defaulted, so every existing caller is unaffected).

That chain is **process-wide**. Its own config said so, in prose, and said the
composition layer was deliberately absent rather than declared-and-unread. This is
that layer.

## The problem

Three different people are entitled to an opinion about one tool call, and they
are not equally trusted:

| Level | Set by | Config |
|-------|--------|--------|
| `session` | the **end user**, for this session only | passed in by the runtime, or `$ICDEV_AGENT_POLICY_CHAIN_SESSION` |
| `agent` | the **agent author** | `<profile_dir>/policy_chain.yaml`, or `$ICDEV_AGENT_POLICY_CHAIN_AGENT`, or the `agent:` block in `args/agent_policy_chain.yaml` |
| `server` | the **admin**, as the org baseline | the top-level `chain` in `args/agent_policy_chain.yaml` |

Evaluated **session first, then agent, then server**. A DENY at any level
short-circuits the whole composition — a session DENY means the server level is
never consulted, because there is no answer the baseline could give that would
make a refusal not a refusal.

## Why session-first is the safe order, not the dangerous one

The reflex is that letting the end user's rules run first is how you get talked
out of a control. It is the opposite, and the reason is structural rather than a
matter of care:

**Levels are additive. They do not override.** Each level contributes its own
chain, and the answer is the strictest opinion any level returned. So the only
thing a session-level policy can contribute is a *stricter* answer. There is no
syntax at the session level for removing a policy from the agent or server chain,
for lowering a floor, or for making a DENY into an ALLOW — not because those are
rejected by a check that could be forgotten, but because the composition never
reads a lower level as an override of anything. **A session ALLOW is
indistinguishable from a session abstention**, and there is a test asserting
exactly that: composing with a permissive session policy produces the same effect
and the same winning level as composing without one.

That is what makes running it first *safe*, and running it first is what makes it
*useful*: a user who wants to forbid something for the next hour gets the cheapest
possible evaluation and an immediate DENY, without the org baseline having to be
consulted to confirm a refusal.

Three narrower things are checked rather than structural, and each is **reported**
as a `Relaxation` and logged at WARNING rather than silently dropped — a key
ignored in silence is a key somebody keeps writing:

- `on_policy_error: allow`, refused at **every** level including server (a broken
  policy is an unanswered question, not an answer);
- an `audit:` block below the server level (a user does not get to stop their own
  denials being logged);
- a floor lower than a stricter level's floor (already ignored by `strictest`).

Two further properties are kept out of reach rather than validated:

- **The level order is a module constant (`LEVELS`), not a config key.** A config
  that could reorder the levels could put the session level last.
- **A config cannot introduce code.** A level names policies the in-process
  registry already holds, populated only by first-party `register_policy()` calls.
  A name the registry does not know resolves to a DENY naming itself, per level.

## Session state

A stateful policy needs somewhere to count. omnigent's mechanism is the
reference: a rule returns `state_updates`, e.g.
`{"key": "call_count", "action": "increment", "value": 1}`.

```python
PolicyDecision(
    ALLOW, "under the limit", policy="max_calls",
    state_updates=({"key": "call_count", "action": "increment", "value": 1},),
)
```

Actions: `increment`, `decrement` (value defaults to 1), `set`, `append`,
`delete`. Four properties matter:

1. **Updates apply as each policy returns**, before the next policy runs, so a
   later policy reads what an earlier one wrote — "the server level checks the
   total" is the main reason to have a total.
2. **A policy cannot write state by mutating the event.**
   `PolicyEvent.session_state` is a snapshot, re-taken per policy;
   `apply_updates` is the only writer.
3. **A policy that never ran never wrote.** Short-circuited policies do not
   increment anything.
4. **A malformed update raises**, which the chain resolves to `on_policy_error`
   (DENY by default). A counter that silently fails to increment is a limit that
   silently never fires — the declared-but-unconsumed failure this card exists to
   close, in miniature.

State is keyed by `session_id` through a per-process registry, so a hook rebuilt
for the next turn counts against the same numbers rather than starting over, and
persisted to `agent_session_policy_state`.

### Why the state is a table and not a dict

ICDEV resumes agent sessions across process restarts on purpose —
`AgentLoopResult.session_id` handed back as `resume_session_id` restores the full
tool-use history. An in-memory-only counter therefore means
`max_tool_calls_per_session: 50` is bypassed by restarting the runtime and
resuming, which is not a limit, it is a speed bump. The session id is the
identity that outlives the process, so the state keyed by it must too.

The table is **MUTABLE and deliberately not append-only**: a counter IS its
current value, written with an UPSERT on `(session_id, state_key)`, and there is
nothing for an auditor to reconstruct from its history. The evidence record is
elsewhere and is append-only — the *decision* each value produced lands in
`agent_approval_log`, with the composed effect, the reason, and a `rule` that
names the deciding **level** (`policy_composition:session:max_calls:deny`), which
is the first question anyone asks of a composed decision. A missing table degrades
to in-process state with a WARNING naming the migration, never to an absent limit
reported as a satisfied one.

## Usage

```python
from tools.agent_runtime.policy_composition import build_composed_policy_hook

run_agent_loop(..., approval_gate=build_composed_policy_hook(
    session_id=session_id,
    session_policy={"chain": [{"name": "max_tool_calls"}]},   # user tightening
))
```

Same `PreToolUseHook` contract as `approval_gate.build_approval_hook` and
`policy_engine.build_policy_hook` — return `None` to let the call run, a string to
halt it — so it is a drop-in.

```bash
python tools/agent_runtime/policy_composition.py --levels --json
python tools/agent_runtime/policy_composition.py --evaluate git_push --json
python tools/agent_runtime/policy_composition.py --evaluate git_push \
    --session-policy '{"chain": [{"name": "reversibility"}]}' --json
python tools/agent_runtime/policy_composition.py --state <session-id> --json
```

## What this reuses rather than reimplements

`compose()` reduces to `policy_engine.evaluate()` over the levels' chains
concatenated in `LEVELS` order, with a composed floor and a composed
`on_policy_error`. That reuse is the point rather than a shortcut: DENY
short-circuit, strictest-wins, abstention-is-not-authorisation and fail-closed
normalisation are already tested in `test_agent_policy_engine.py`, and a second
implementation of them here would be a second one to keep correct. Composition
adds exactly three things — level **order**, per-level **provenance** on every
decision, and session-state updates applied as the chain runs.

The audit row is written by `approval_gate.record_decision()` for the same reason
`policy_engine` does it: that function is the one place that knows the
no-argument-VALUES rule (key names plus a SHA-256), and a second writer would be a
second chance to break it.

## Deliberately not in this task

- **Builtin policies** — per-session tool-call cap, repo/branch allowlists, risk
  accrual — `exa-policy-03`. The composition, the registry, the `usage` and
  `session_state` fields and the `state_updates` mechanism they need are all here;
  the policies themselves are not. The card's ordering is explicit: the policy
  layer lands before the builtins.
- **`cost_budget` as a downgrade gate** — `exa-policy-04`. `strictest()` has three
  effects and a downgrade is a fourth kind of answer ("allowed, but not at this
  tier"), so it is a change to the effect vocabulary rather than a new policy.
- **`factory_params`** for repo and branch allowlists — belongs with the builtins
  that consume them (`exa-policy-03`); a params key nothing reads is the failure
  mode this card exists to stop.
