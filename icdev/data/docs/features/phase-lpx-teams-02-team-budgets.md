# LPX teams-02 — Per-team budget across the three non-uniform grouping units

CUI // SP-CTI

## What shipped — `tools/llm/proxy_team_budgets.py`

The gameday-facing budget wrapper. **A team's budget is its virtual key's
budget.** It binds a `ttx_teams` team (scoped to its `ttx_sessions` session) to an
exercise-windowed virtual key and gives facilitators a clear allow/warn/block
decision instead of a generic 500.

- `provision_team_budget(session_id, team_id, budget_usd, …)` — ensures the team
  has an active exercise-scoped key with that budget (issues one, or updates the
  existing key's budget in place — no duplicate keys).
- `check_team_budget(session_id, team_id, projected_cost_usd=…)` — resolves the
  team's key and returns a structured decision with a `facilitator_message`.
- `record_team_spend(...)` and `team_budget_status(session_id)`.

## No new table — and why that is correct

The three non-uniform grouping units already live on the SAME two tables from
lpx-keys-01/02:

- `llm_proxy_keys` — the key carries `scope_type` ∈ {`team`, `guild`, `user`} +
  `scope_ref` + `session_id`.
- `llm_proxy_spend` — the per-key, per-window ledger.

A dedicated team-budget table would be the "parallel notion" the cards warn
against and would duplicate the cost ledger (the same anti-pattern lpx-teams-03
avoids by not writing two ledgers). So this card adds behaviour, not schema.

| Unit | app | `scope_type` | window |
|------|-----|--------------|--------|
| gameday team | `/gameday` | `team` | `exercise` |
| academy guild | `/academy` | `guild` | `month` (decided separately) |
| local user | local canvas copy | `user` | `month`/`none` |

## Budget shape — per EXERCISE, not per month

`ttx_sessions.duration_minutes` defaults to 120, so a gameday budget is scoped to
one bounded session (`budget_window='exercise'` →
`window_key='exercise:<session_id>'`) and **resets for the next exercise**. The
strategy doc's "$X per student per month" shape is wrong for gameday and is not
used. Same team number in a different session is a separate budget (tested).

## Academy decided separately — per-guild, month-shaped

Academy is a different unit: budgets are **per-guild** (`scope_type='guild'`) and
may be **month-shaped** (`budget_window='month'`). Academy issues keys with those
values via lpx-keys-01/02; this gameday wrapper does not force academy into the
exercise window.

## Attribution limit (accepted, documented — not a gap)

A team's 3-5 members share ONE team key, so spend is attributable to the **team**,
never to the member: you will know Team Blue spent $40, not that one player spent
$18 of it. Per-member attribution would require giving gameday players real
`dashboard_users` accounts and an FK from `ttx_team_members` to them — a far
larger change, explicitly out of scope. This is a property of the join-by-code
model, not a bug. (Per-team spend attribution on `ttx_api_log` is lpx-teams-03.)

## Tests

`tests/test_lpx_team_budgets.py` (8): provision creates/updates an
exercise-scoped key, within-budget allow, **exhaustion blocks with a clear
facilitator message**, block scoped to one team, per-exercise reset across
sessions, no-budget fail-open, record-without-provision raises, status spend vs
budget.
