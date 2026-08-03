# CUI // SP-CTI

# Agent-Loop Approval Gate (ars-appr-01)

## The gap

Every tool call the model emitted from `run_agent_loop` executed. The only thing
between an agent loop and an irreversible act was whether the caller happened to
pass an `on_pre_tool_use` hook — and none of the call sites did.

ICDEV has real irreversible surfaces: `git push` and force-push, PR merge, branch
and worktree deletion, writes against append-only tables, marking a kanban task
`done` on a shared board, registry publication, and external posts. CLAUDE.md
already names one absolute prohibition — never delete YouTube videos — precisely
because irreversibility is the risk.

## What shipped

`tools/agent_runtime/approval_gate.py` classifies every tool call by
**reversibility** and halts anything that is not provably reversible until a human
decides. `run_agent_loop` composes it with any caller-supplied
`on_pre_tool_use` (`_resolve_approval_gate` + `_compose_pre_tool_hooks`), so the
caller's hook still runs and can still block — the gate's block wins.

Policy: `args/agent_approval_policy.yaml`. Audit table: `agent_approval_log`
(migration `20260803002224_agent_approval_log`).

### Rule order is the security property

The asymmetry between escalation and downgrade is the whole point. First match
wins:

| # | Rule | Verdict |
|---|------|---------|
| 0 | the policy enumerates the tool `reversible` **and** it is not in `command_tools` | `reversible`, no escalation |
| 1 | an `irreversible` content pattern matches `"<tool_name> <flattened input>"` | `irreversible` |
| 2 | the tool is enumerated in the policy's tier lists | as enumerated |
| 3 | a non-irreversible pattern matches **and** the tool is in `command_tools` | as matched |
| 4 | nothing matched | `default_tier` = `unknown` → **requires approval** |

Four of those positions are load-bearing:

- **Rule 0 stops a read from prompting.** A tool that does not execute what it is
  handed cannot be made irreversible by it — its arguments are data being read,
  not a command being run. Without this, `read_file("how do I git push safely")`
  halted for human approval, and a gate that prompts on a read teaches operators
  to approve reflexively, which costs more safety than the escalation buys.
  The exemption is granted by the **operator's** policy file, not by a tool
  schema's self-declared `is_read_only` — see Provenance. A generic executor
  never receives it, whatever the policy says, because for a shell the input *is*
  the command.
- **Rule 1 applies to every other tool, unconditionally.** `run_command` is not a
  tier; the string it is handed is. A shell tool a caller marked read-only can
  still carry `git push`.
- **Rule 3 only fires for declared generic executors.** A content pattern may
  always make a call *worse*, but may only make it *better* for a tool that is
  nothing but a shell. Otherwise incidental text decides: `git_push` with a
  `{"note": "mkdir logs"}` argument matched the `mkdir` recoverable pattern and
  was auto-allowed — and so was any unenumerated tool carrying the same word. A
  downgrade rule that fires on a tool it was never written for is an allowlist
  that fails open by accident.
- **Rule 4 is default deny.** An allowlist that fails open is decoration; the
  whole point is the call nobody enumerated. `unknown` gates exactly like
  `irreversible`, and is recorded distinctly only so the config gap shows up in
  the trail.

### Modes

Resolved from the `mode` argument → `ICDEV_AGENT_APPROVAL_MODE` → the policy file.

| Mode | Behaviour |
|------|-----------|
| `enforce` (default) | Ask the injected `Approver`. `console_approver` prompts on a TTY and **denies on EOF**, so cron, CI and the kanban runner can never self-approve. |
| `dry_run` | Record what *would* have halted, then allow. For measuring blast radius before turning enforcement on. |
| `off` | Allow — still audited. The explicit escape hatch. |

`dry_run` and `off` still write the audit row: the point of the trail is that it
does not have gaps where someone turned the gate down.

### Failure behaviour

Two independent fail-closed positions:

- **Missing or malformed policy** — `load_policy` falls back to a policy that
  enumerates zero tools, so every call lands on rule 4 and needs a human. A config
  failure can never be the reason an irreversible action ran unattended.
- **Gate requested but unbuildable** — `_resolve_approval_gate` returns a hook
  that denies *every* tool with the underlying error in its message. An operator
  who set `ICDEV_AGENT_APPROVAL_MODE` did not ask for "run unsupervised if the gate
  is broken".

A broken *approver* also denies, and `console_approver` denies on EOF.

### Enablement

The gate is **env-resolved, not always-on**: with `approval_gate=None` (the
default) and `ICDEV_AGENT_APPROVAL_MODE` unset or `off`, no gate is installed.
Pass `approval_gate=True`, a callable, or set the env var to enable it;
`approval_gate=False` disables it outright.

That default is deliberate — it keeps the gate out of the path of every existing
call site until an operator opts in — but it does mean **an unconfigured
deployment is ungated**. If you want coverage by default, set
`ICDEV_AGENT_APPROVAL_MODE=enforce` in `.env`.

### What is recorded

`record_decision` writes to the append-only `agent_approval_log`: tool name, tier,
the rule that fired, actor, reason, decision, mode, session and trace ids, and a
SHA-256 digest of the flattened input. **Argument values are never persisted** —
only argument *keys* and the digest — so the trail cannot become a copy of the
secrets the tools were handed. If the table is absent the decision falls back to
`hook_events` rather than being dropped.

Auto-allowed reversible calls produce no row: a trail that logs everything is one
nobody reads.

## Verification

```bash
pytest tests/test_agent_approval_gate.py -v
```

Covers classification order, the downgrade asymmetry, default-deny on
unenumerated tools, fail-closed on a missing policy, approver contracts
(bool-returning, broken, non-interactive), mode resolution, the recorded-schema
match against the migration, that argument values never reach the row, that the
table is registered append-only, and that the gate composes correctly with a
caller-supplied hook in both directions.

## Provenance

Two sessions implemented `ars-appr-01` independently and in different places. The
implementation in `tools/agent_runtime/` (PR #1229) is canonical. A parallel
branch, `feat/ars-appr-01-approval-gate`, built `tools/llm/approval_gate.py` with
its own config, migration and 566-line test file; it was never opened as a PR and
is superseded. Its distinctive ideas were reviewed against this one before it was
retired:

- Its agent-loop wiring is **already present here**, and in a safer form — that
  branch logged an error and continued *unguarded* when the gate could not be
  built, where this one denies every tool.
- Its schema-driven rules were the one genuine design disagreement, and it is now
  **resolved** (rule 0 above). That branch let a tool schema declare
  `reversibility` and treated `is_read_only: true` as proof that short-circuits
  the content patterns. This implementation originally refused any such
  short-circuit — "an assertion by the caller rather than a fact" — and escalated
  on content first for every tool, unconditionally.

  Both positions were half right, and the disagreement was really about *whose*
  claim to trust rather than whether to trust one at all:

  - The branch was right that scanning a read's arguments for `git push` is a
    category error. Measured on the shipped gate before the fix,
    `read_file("how do I git push safely")` classified `irreversible` and halted.
  - This implementation was right that the *tool's own* `is_read_only` flag is
    not evidence — it ships with the tool and asserts its own safety.

  The resolution takes the exemption from neither: it comes from the tool being
  enumerated `reversible` in `args/agent_approval_policy.yaml`, which is the
  operator's file and already the source of truth for every other tier. A generic
  executor is excluded unconditionally, so the smuggling hole rule 1 exists to
  close stays closed even if someone lists a shell as reversible. Listing a tool
  under `reversible` is now a stronger claim than the other tiers — it asserts the
  tool cannot act — and the policy file says so.
- This document is adapted from that branch's, corrected to the API that actually
  shipped.
