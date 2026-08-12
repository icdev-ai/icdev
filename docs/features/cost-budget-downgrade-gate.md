# Cost Budget as a Downgrade Gate (exa-policy-04)

**Classification:** CUI // SP-CTI
**Status:** shipped
**Module:** `tools/llm/cost_budget.py` — consumed in `tools/llm/router.py::LLMRouter.invoke`
**Config:** `cost_budget:` in `args/llm_config.yaml`
**Tests:** `tests/test_cost_budget_downgrade.py` (31 cases, in the CI `test` allowlist)

## The problem

ICDEV had four budget layers before this one, and every single one of them
blocks outright:

| Layer | Scope | On breach |
|---|---|---|
| `tools/agent/token_tracker.py::check_budget` | per agent, monthly | `BudgetExceededError` (raised in `router.invoke`) |
| `tools/budget/module_budget_tracker.py` | per module | `ModuleBudgetExceededError` |
| `tools/llm/chain_orchestrator.py::_check_budget` | per chain run | refuses the run |
| `tools/llm/proxy_budgets.py` | per virtual key | `block` decision |

A hard stop is the right shape for a virtual key handed to a training cohort.
It is the wrong shape for a long autonomous run: reaching a ceiling unattended
at 02:00 ends the run, and the operator finds a dead pipeline rather than a
cheaper one. What they wanted was for the work to continue on a smaller model.

This is what omnigent's `cost_budget` does that ICDEV could not: **ask** at soft
thresholds, and at the hard limit act as a **downgrade** gate — block the
expensive model, but let the work continue.

## What ships

### Soft threshold → ASK, once per threshold

Crossing a declared fraction of `limit_usd` raises one ASK for that threshold
for that period. Not one per call — an ASK that fires on every invocation is
noise an operator learns to ignore, which is the same as having no ASK.

The dedupe key is `(tool_name, rule, period_start)` read back from
`agent_approval_log`, the append-only table `tools/agent_runtime/approval_gate.py`
already writes. A process-local cache short-circuits the steady state, but
correctness does not depend on it: a fresh process re-reads the log and stays
silent. Verified end-to-end against a real SQLite database.

The tier recorded is `irreversible`, and that is not a vocabulary bent to fit —
money spent cannot be un-spent, which is precisely what that table exists to
record.

Approvers are config-selected: `record` (log + append, keep working — the
autonomous default, because a long unattended run has no console), `console`
(prompt, deny on EOF), `deny` (fail closed). A **denied** soft ASK downgrades
early rather than failing, so an operator saying "stop spending on the expensive
model" gets exactly that and not an outage.

### Hard limit → DOWNGRADE, not failure

At `spend >= limit_usd` the function's declared `routing.<function>.chain` is
reordered so the affordable tier leads. Sort order:

1. models over `downgrade.max_blended_per_1k` last — this is the demotion
2. cheaper first
3. **local first among equals** when `prefer_local`
4. original chain position, so a tie keeps the operator's declared preference

**Every model survives the reorder.** Truncating the chain to the affordable
tier would convert a budget event into an outage the moment that tier is down.
The chain already means "fallback order", so demoting the expensive model to the
tail makes it unreachable while the cheap tier works and still available if that
tier fails — which is the correct degradation, not a second failure mode.

An **unpriced** model sorts with the over-ceiling group. Unknown price is not
free; the same fail-closed reflex as "unknown model is not local".

### The air-gap constraint

Rule 3 above is what satisfies "downgrade can reach the local tier". Local
Ollama models declare `pricing: {input_per_1k: 0.0, output_per_1k: 0.0}`, so
they sort under any ceiling — but so do several cloud entries (`claude-sonnet`,
`kimi-cloud` and the CLI-bridge models are all declared 0.0). Price alone would
land the downgrade on whichever zero-priced cloud model happened to be listed
first, which is not air-gap correct. `prefer_local` breaks that tie using
`cli_bridge.activate.is_local_only_model` — the **one** definition of local in
the platform, never a second inline `provider == "ollama"` test, because two
definitions of "local" is how CUI leaks.

Against the shipped `code_generation` chain:

```
declared   qwen3-local, kimi-cloud, claude-sonnet, gemini-2.5-pro, gpt-4o, codestral-local
downgraded qwen3-local, codestral-local, kimi-cloud, claude-sonnet, gemini-2.5-pro, gpt-4o
           ^^ both local models lead              priced cloud demoted to the tail ^^
```

### No model id in Python

The chain comes from `routing:` and the order from the `pricing:` block already
declared on each model. `tests/test_cost_budget_downgrade.py` parses the module's
AST and fails on any string bound to `model=`/`model_id=`, mirroring
`tests/test_no_hardcoded_model_ids.py`. A literal there would pin one vendor into
the downgrade path, and an air-gapped deployment would silently downgrade onto a
model it cannot reach.

## Router wiring — two things that are easy to get wrong

**1. The budget is re-applied AFTER RL re-ranking.** `invoke` calls
`self._get_rl_router().rank_models(function, chain)`, which reorders by learned
Q-value and has no notion of price. Evaluated once before that call and never
again, RL would promote the just-demoted model straight back to the head — the
same way it can undo `force_local`. The reorder is therefore re-applied after,
from the already-in-memory config, so it costs no file or DB read.

**2. The two-tier escalation is suppressed when downgraded.**
`_maybe_invoke_two_tier` bypasses the chain entirely to reach the expensive
tier-2 planner. A downgrade that only reordered the chain would leave the
costliest path in the router wide open.

## Measurement, and refusing to fabricate a zero

Spend is summed from `ai_telemetry` (`cost_usd`, `function`, `created_at`) —
existing telemetry, no new table and no migration. When the table is absent or
unreadable the verdict is `unmeasurable` and the action is `allow`: a fresh
worktree or an ephemeral CI database must not read as "you have spent nothing,
spend freely", and must not read as "you are over budget" either. Same principle
as `tools/awareness/capability_consumption.py`.

The period boundary is a **10-character date prefix** (`2026-08-01`), not a full
ISO timestamp, and that is load-bearing. `ai_telemetry.created_at` is TEXT
written by two writers: SQLite's `datetime('now')` emits
`2026-08-01 10:00:00` (space) while `isoformat()` emits
`2026-08-01T10:00:00+00:00` (`T`). Comparing lexicographically against a full ISO
boundary silently drops every space-separated row on day one of the period,
because `' '` sorts below `'T'`. A date prefix is correct under both spellings
and casts cleanly against a real PostgreSQL timestamp column.

## Failure posture

The gate never breaks routing. `apply_to_chain` returns the chain it was handed
on every failure path — unreadable config, unreadable telemetry, a raising
approver, an unexpected exception. A budget gate that can take routing down is
worse than the overspend it prevents.

`hard_action: block` and `ask.on_denied: block` exist for operators who
genuinely want the old hard stop (raising `CostBudgetExceededError`), but
`downgrade` is the shipped default — a fifth blocking layer would have been the
bug, not the feature.

## Configuration

```yaml
cost_budget:
  enabled: true
  scope: global               # global | function
  period: monthly             # monthly | daily
  limit_usd: 200.00
  soft_thresholds: [0.50, 0.80, 0.95]
  hard_action: downgrade      # downgrade | block
  downgrade:
    max_blended_per_1k: 0.0   # over this => demoted to the chain tail
    prefer_local: true        # tie-break local-first (the air-gap guarantee)
  ask:
    approver: record          # record | console | deny
    on_denied: downgrade      # downgrade | block
  per_function: {}            # keys merge over the block above
```

An **undeclared** `llm_function` silently falls back to `routing.default`, so a
budget decision "for code_generation" would in fact be reordering some other
function's chain. `is_declared_function` detects that and the verdict carries
`routing_declared: false` with a warning — surfaced, not hidden.

## CLI

```bash
python tools/llm/cost_budget.py --status --json
python tools/llm/cost_budget.py --function code_generation --json
python tools/llm/cost_budget.py --explain code_generation --json   # + per-model prices
python tools/llm/cost_budget.py --gate                             # exit 1 only under hard_action: block
```
