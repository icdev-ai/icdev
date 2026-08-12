# CUI // SP-CTI

# Fail-Closed Enforcement Gates

An enforcement point that grants permission when it malfunctions is not an
enforcement point. This document records the ICDEV™ gates that are explicitly
deny-by-default on error, the escape hatch each one ships with, and what to
watch after enabling them.

## Why

The failure mode is quiet by construction. Each of the gates below wrapped its
decision in a broad `except Exception` and returned the *permissive* value, so:

* the error path and the "nothing to enforce" path produced identical output;
* the surrounding code had no way to tell them apart;
* the only symptom was an approval or a spend cap that silently stopped
  applying — usually during exactly the incident that broke the backend.

Fixed under `exa-policy-06`.

## Gates

### 1. HITL approval gate — `tools/workflow_hitl/gate.py`

Answers "is a human approval outstanding for this task?" A `False` here is what
lets the Kanban state machine advance a task to `done`.

| Situation | Result |
|-----------|--------|
| No approval pending | `get_pending() -> None`, transition proceeds |
| Approval pending | `get_pending() -> dict`, transition refused (`HITL_PENDING`) |
| **State unreadable** (DB down, table missing, RLS error) | **raises `HITLGateUnavailable`, transition refused (`REFUSED_done_hitl_unavailable`)** |

`should_gate()` returns `True` for the unreadable case, so any caller using the
boolean form also blocks. Both refusals are recorded through
`_record_status_transition`, so a fail-closed block is visible on the board's
transition history rather than silent.

Only active when `ICDEV_HITL_KANBAN_GATE=true`.

### 2. LLM cost cap — `tools/llm/gateway.py::_check_cost_cap`

| Situation | Result |
|-----------|--------|
| Within budget | `allowed: True` |
| Budget exhausted (`hard_stop`) | `allowed: False` |
| Cap disabled in `args/llm_gateway_config.yaml` | `allowed: True` |
| `token_tracker` not installed at all | `allowed: True` — documented as "ignored gracefully"; a deployment that never ships the budget module, not one whose module is broken |
| **Budget backend raises** (DB locked, corrupt config) | **`allowed: False`** |

Supporting change in `tools/agent/token_tracker.py`: `_load_budget_config()` no
longer swallows a read failure into `{}`. A missing config file or a missing
PyYAML still yields `{}` (genuinely unconfigured), but a file that is *present
and unparseable* now raises `BudgetConfigError` — previously that was read as
`enabled: False`, which `check_budget` reports as `allow`, silently removing
every cap. The failure is not cached, so repairing the file recovers without a
restart.

## Escape hatches

Both gates ship an opt-in env var that restores the old permissive behaviour for
a staged rollout. Neither is on by default, and each logs at **ERROR** on every
single use so it cannot be left on quietly.

| Variable | Effect |
|----------|--------|
| `ICDEV_HITL_GATE_FAIL_OPEN=1` | `get_pending` returns `None` on error instead of raising |
| `ICDEV_COST_CAP_FAIL_OPEN=1` | `_check_cost_cap` returns `allowed: True` on backend error |

These exist for the case where closing a gate surfaces latent breakage that was
previously slipping through — e.g. a deployment whose `wf_approvals` table was
never migrated. The correct use is to buy time while fixing the underlying
backend, not to keep the board moving. Grep for the ERROR lines to find every
request that took the override.

## What to watch after enabling

* **Approval inbox** (`tools/agent_runtime/approval_inbox.py`) and the board's
  transition history — a burst of `REFUSED_done_hitl_unavailable` means the HITL
  backend is broken, not that the tasks are unapproved.
* **`llm_gateway_audit`** — a rise in `blocked_reason` containing
  `unenforceable` means the budget backend is failing, not that agents are
  overspending.

In both cases the fix is the backend. A gate that started refusing is reporting
a problem that already existed.

## Shipped `token_budgets` defaults

`check_budget` returns `allow` unconditionally when `token_budgets.enabled` is
falsy or when the resolved cap is `<= 0` ("unlimited"). Those YAML values in
`args/llm_config.yaml` therefore *are* the cost cap — a well-meaning edit to `0`
disables enforcement with no other signal. The shipped defaults are intentional
and pinned by `tests/test_exa_policy_06_fail_closed.py::TestShippedTokenBudgetDefaults`:

```yaml
token_budgets:
  enabled: true
  default_monthly_usd: 50.00
  warning_threshold: 0.8
  hard_stop: true
```

## Known remaining fail-open on the same backend

`tools/llm/router.py` (~line 2380) runs its own `check_budget` call and still
catches every non-`BudgetExceededError` exception into a `logger.debug` and
proceeds. It was left unchanged deliberately: closing it makes *every* LLM
invocation in the platform fail closed on a budget-backend blip, which is a far
larger blast radius than the gateway pre-check and deserves its own staged
change. The gateway path fixed here is the one that fronts guarded calls.
