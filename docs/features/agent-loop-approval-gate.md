# CUI // SP-CTI

# Agent-Loop Approval Gate (ars-appr-01)

## The gap

`grep -c approval icdev/tools/llm/agent_loop.py` returned **0**. Every tool call the
model emitted executed. The only thing between an agent loop and an irreversible
act was whether the caller happened to pass an `on_pre_tool_use` hook — and of the
nine call sites in the repo, none did.

ICDEV has real irreversible surfaces: `git push` and force-push, PR merge, branch
and worktree deletion, writes against append-only tables, marking a kanban task
`done` on a shared board, registry publication, and external posts. CLAUDE.md
already names one absolute prohibition — never delete YouTube videos — precisely
because irreversibility is the risk.

## What shipped

`icdev/tools/llm/approval_gate.py` classifies every tool call by **reversibility**
and halts anything that is not provably reversible for a human decision.
`run_agent_loop` wires it **by default**, so a call site written before this
existed is covered anyway.

### Rule order is the security property

First match wins:

| # | Rule | Verdict |
|---|------|---------|
| 1 | schema declares `reversibility: reversible\|irreversible` | as declared |
| 2 | tool name matches `irreversible.tools` (exact or glob) | `irreversible` |
| 3 | schema declares `is_read_only: true` | `reversible` |
| 4 | `irreversible.patterns` regex matches the flattened tool input | `irreversible` |
| 5 | tool name matches `reversible.tools` | `reversible` |
| 6 | nothing matched | `unknown` → **requires approval** |

Three of those positions are load-bearing:

- **Rule 6 is default deny.** An allowlist that fails open is decoration — the
  whole point is the call nobody enumerated. `unknown` gates exactly like
  `irreversible`; it is recorded distinctly only so the config gap shows up in the
  trail.
- **Rule 4 beats rule 5.** `run_command` is on the reversible allowlist and still
  halts when its command is `git push --force`. If that order flipped, every
  irreversible act in the platform could be smuggled through one allowlisted tool.
- **Rule 3 beats rule 4.** `is_read_only` lives in the tool *schema*, which is
  first-party code the model cannot write, and a read-only tool cannot act
  irreversibly. Without this, `grep_files("git push")` would prompt.

### Modes

Resolved from the `approval_mode` argument → `ICDEV_AGENT_APPROVAL_MODE` →
`args/approval_gate.yaml`.

| Mode | Behaviour |
|------|-----------|
| `manual` (default) | Ask the injected `Approver`. The default approver prompts on a TTY and **denies fail-closed** with no interactive console, so cron, CI and the kanban runner can never self-approve. |
| `deny` | Never approve. For unattended batch runs that must not stall. |
| `off` | Auto-approve — still recorded with actor and reason, and every use logs a WARNING. The audited escape hatch. |

An action the platform forbids outright — via
`tools.airgap.hook_compat.run_pre_tool_check`, the headless replica of
`.claude/hooks/pre_tool_use.py` — is **denied, not offered for approval**. A
yes-to-everything approver cannot override it.

### The audit trail

Every decision, approved and denied alike, INSERTs one row into the append-only
`agent_approval_log` (migration `20260802200931`, registered in
`APPEND_ONLY_TABLES`): who decided, why, what was classified how, and by which
rule. A blank actor is written as `system:unattributed` rather than NULL —
anonymous is worse than attributed-to-the-system. A failed INSERT is logged at
WARNING, never swallowed: a silent write failure is how a feature reports success
while persisting nothing.

A **reversible** call produces no row. The trail records *decisions*, and a
reversible call has none to make; ordinary execution is already in the loop's
`tool_call_log`.

### A denied call is not a crash

The model receives a confirm-card tool result — what would have run, its
classification, who denied it and why, and an instruction not to retry — and the
loop continues. This mirrors `tools/cortex/blueprint.py::_agent_proposal`, the
platform's existing confirm-then-launch affordance: describe what *would* happen
and ask, rather than acting.

## Precedent reused

- `tools/agent_runtime/safety.py` — the SAG command-approval layer. Its injectable
  `Approver`, its mode switch, its "audit both outcomes" discipline and its
  `run_pre_tool_check` composition are all kept. What this adds is the
  reversibility taxonomy, coverage of *every* agent loop rather than SAG's
  dispatch, and a dedicated non-repudiation table.
- `tools/cortex/blueprint.py` — confirm-then-launch for agent teams.

## Usage

```python
from icdev.tools.llm.approval_gate import build_approval_hook, classify

# Classify one call.
verdict = classify("run_command", {"command": "git push origin main"})
# -> Classification(reversibility='irreversible',
#                   rule_id='irreversible_pattern:git-push', ...)

# Build the hook yourself (run_agent_loop does this for you by default).
hook = build_approval_hook(tools=tools, session_id=sid, actor="alice")
```

```python
# Attach an approver — e.g. a chat adapter that asks the operator.
run_agent_loop(
    router, ...,
    approver=lambda req: ApprovalDecision(
        approved=ask_operator(req.summary()), actor="alice", reason="reviewed"),
)
```

```bash
# Unattended run that must not stall on a prompt.
export ICDEV_AGENT_APPROVAL_MODE=deny

# Audited escape hatch.
export ICDEV_AGENT_APPROVAL_MODE=off ICDEV_APPROVAL_ACTOR=operator-on-call
```

## Configuration

`args/approval_gate.yaml` (mirrored to `icdev/args/`) — `enabled`, `mode`,
`irreversible.tools`, `irreversible.patterns`, `reversible.tools`. Add a *pattern*
rather than widening the allowlist when a new irreversible command shows up. A
missing or malformed config narrows the allowlist; it does not open the gate.

## Blast radius

Mutating tools that are neither allowlisted nor schema-declared read-only now halt
for approval. In practice most are covered already: SAG's tool discovery
(`tools/agent_runtime/discovery.py`) emits `is_read_only` on every schema
including MCP-derived tools, and the ACE/browser tool names are allowlisted. A
mutating MCP tool that is neither — `kanban_move_task`, say — now requires a
decision, which is the intent.

Two test files opt out via `ICDEV_AGENT_APPROVAL_GATE=0`
(`tests/test_agent_loop.py`, `tests/test_agent_error_circuit.py`): they exercise
loop mechanics with synthetic tools and would otherwise assert on the gate instead
of on the behaviour they are about. The gate's default-on behaviour is proven
end-to-end in `tests/test_approval_gate.py`.

## Tests

`tests/test_approval_gate.py` — 44 tests, one section per acceptance property:
an irreversible call halts for confirmation; an unknown tool requires approval by
default; every decision is recorded with an actor and a reason. Plus the ordering
properties, mode resolution, approver-failure handling, hook composition, and four
end-to-end `run_agent_loop` wiring tests that pass **no** approval arguments at all.
