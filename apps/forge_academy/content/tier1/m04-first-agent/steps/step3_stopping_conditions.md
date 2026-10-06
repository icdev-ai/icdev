---
ontology_id: icdev:mission:m04-first-agent:step:3
step_class: icdev:Lesson
---

# Stopping Conditions

*Current as of October 2026.*

A loop with no reliable exit is not an agent; it is a bill. Every agent needs to
know when it is finished, and — separately — when it must give up.

## The normal exit: the model stops calling tools

In step 1's loop, the agent ends when `decide_tool()` returns `None`: the model
answers instead of requesting another tool. That is the ONLY exit that means "task
complete". Every other exit below is a safety exit, and a safety exit means the task
was **not** completed.

## Safety exits

- **Step (iteration) budget.** `max_steps` caps how many turns the loop may take, so
  a confused model cannot loop forever. Hitting it is a failure to report, not an
  answer to return.
- **Token or cost budget.** A cap on cumulative tokens or spend, because a few long
  tool results can exhaust the budget long before the step count does.
- **Wall-clock budget.** A cap on total elapsed time, distinct from a per-tool
  timeout: many calls that are each individually fast can still add up.
- **Repetition detection.** The same tool called with the same arguments twice in a
  row is getting the same answer twice. Stop, or tell the model it is repeating
  itself, rather than spending the rest of the budget on it.
- **Escalation to a human.** Some steps should not be taken autonomously — an action
  that is irreversible, or outside the agent's authority. The correct stop there is
  to pause and ask for approval.

## Report HOW the loop ended

Step 1's minimal agent returns the string `"Max steps reached"` — and a caller that
just prints the return value cannot tell that from a real answer. Return the
outcome as data instead: success, or which budget was exhausted. Then the caller can
retry, escalate, or report the failure honestly.

ICDEV's `icdev/tools/llm/agent_loop.py` does exactly this. Its result carries a
`result_subtype`: `success` when the model ended the turn, and a distinct value for
each budget that can stop it — `error_max_turns`, `error_max_budget_tokens`,
`error_max_budget_cost` and `error_max_wall_clock` — so "ran out of steps" is never
mistaken for "finished".
