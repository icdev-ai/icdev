# LPX keys-02 — Per-key budgets across three non-uniform grouping units

CUI // SP-CTI

## Summary

`tools/llm/proxy_budgets.py` adds a **per-key spend budget** layer on top of the
virtual keys from `lpx-keys-01`. Budgets wire onto ICDEV's **existing** grouping
units via the scope already carried on each key — there is no new notion of
"student".

## The three grouping units (deliberately not one abstraction)

The units are non-uniform and are distinguished only by `scope_type` on the key
(and mirrored onto each spend row), never forced into a single dedicated table:

| Consumer | Grouping unit | `scope_type` | `scope_ref` | Notes |
|----------|---------------|--------------|-------------|-------|
| `/gameday` | `ttx_teams(team_id, session_id)` | `team` | `team_id` | Players join by `join_code` and have **no** `dashboard_users` row, so per-user budgets are impossible — per-team is the only granularity. Per-exercise wiring is `lpx-teams-02`. |
| `/academy` | `fa_guilds` / `fa_guild_members` over `fa_users` | `guild` or `user` | guild or user id | Per-guild vs per-user is decided **at issuance**, not assumed to mirror gameday. |
| Local canvas copies | individual dashboard users | `user` | user id | See `lpx-keys-04`. |

## Budget windows

`budget_window` on the key drives the accounting window (`window_key_for`):

- `exercise` → `exercise:<session_id>` — one bounded gameday session; resets per
  exercise (the shape `lpx-teams-02` uses; **not** the "$/month" shape from the
  strategy doc).
- `day` / `month` → calendar windows (academy/tenant may be month-shaped).
- `none` → a single lifetime bucket.

Spend in one window never counts against another (test:
`test_windows_isolate_spend`).

## Enforcement contract

`check_budget(key_id, projected_cost_usd=…)` returns `allow` / `warn` / `block`
with a shape mirroring `tools/agent/token_tracker.check_budget`, so both budget
layers can be consumed uniformly. Key properties, all asserted in
`tests/test_lpx_proxy_budgets.py`:

- Unlimited key (`max_budget_usd` NULL) always allows.
- `warn` at ≥80% utilisation; `block` when `spent + projected ≥ budget`.
- **Deny is scoped to a single key/window** — one team exhausting its budget does
  **not** block another team (`test_deny_is_scoped_only_to_that_key`).
- A revoked/expired key blocks.

## Defense in depth — this is an additional layer, not a replacement

| Layer | Where | Dimension | Kept? |
|-------|-------|-----------|-------|
| Per-agent cost cap | `tools/llm/gateway.py` → `tools/agent/token_tracker.check_budget` | per-agent, process-global, monthly | **Untouched.** Still runs. |
| Per-key budget (this card) | `tools/llm/proxy_budgets.py` | per team / guild / user, per window | New. |

The gateway cap throttles a runaway *agent* process-wide; the per-key budget
degrades only the *cohort unit* that overran. Both apply; neither is removed.

## Interaction with `tools/llm/rate_gate.py` — no double-throttle

`rate_gate` is a process/host/cluster **concurrency** gate plus an inter-call
pause, measured in *requests in flight*. It protects the shared provider account
from bursts and has **no scope dimension**. Budgets here are measured in
**dollars per scope**. The two gate on orthogonal axes and compose cleanly:

- A call may pass the concurrency gate and still be blocked for budget.
- A call may be within budget and still wait behind the concurrency gate.
- Neither reads the other's state, so there is no double-counting or
  double-throttling.

Per-team **RPM/TPM rate** ceilings (a third, scope-aware axis for competition
fairness) are built in `lpx-teams-01` and are likewise orthogonal to both.

## Attribution limit (stated, not a gap)

A gameday team's 3–5 members share one team key, so spend is attributable to the
**team**, never to the member. Per-member attribution would require giving
players real accounts (an FK from `ttx_team_members` to `dashboard_users`) — a
far larger change, explicitly out of scope. See `lpx-teams-02` / `lpx-teams-03`.

## Storage

`llm_proxy_spend` — append-style spend ledger keyed by `(key_id, window_key)`,
carrying `tenant_id` + `classification` for the global RLS predicate. Aggregation
is plain `SUM` (PG-portable; no SQLite-dialect JSON SQL). Added to
`tests/conftest.py` `MINIMAL_ICDEV_SCHEMA`.
