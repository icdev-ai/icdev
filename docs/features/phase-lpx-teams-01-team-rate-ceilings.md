# LPX teams-01 — Per-team RPM/TPM ceilings (competition fairness)

CUI // SP-CTI

## The bug this fixes (fairness, not cost)

A `/gameday` exercise runs 4–5 teams competing on ONE shared server against one
org-level provider rate limit. Teams are adversaries, so a team that exhausts the
shared rate limit degrades every opponent's exercise — **even while staying under
budget**. That is a competition-integrity bug.

Why the existing guards don't cover it:

- `tools/llm/gateway.py` cost cap is process-global — it throttles *everyone*
  when one team overruns (the opposite of fair).
- `tools/llm/rate_gate.py` is a process/cluster concurrency gate with no team
  dimension.
- `tools/llm/proxy_budgets.py` (lpx-keys-02) caps **dollars** per scope, not the
  request/token **rate** that starves opponents.

## What shipped — `tools/llm/proxy_team_limits.py`

A per-team RPM/TPM ceiling keyed on `ttx_teams.team_id`, **scoped to the active
`ttx_sessions.session_id`** (a team only means something inside its session).

- `configure_session_ceilings(session_id, …)` computes and persists each team's
  ceiling.
- `check_team_rate(session_id, team_id, tokens=…)` returns allow/deny by
  consulting **only that team's** current-minute counters — so exceeding a
  ceiling degrades only that team.
- `record_team_call(...)` increments the team's minute-bucket window.
- `team_rate_status(session_id)` — facilitator view with an `at_ceiling` flag so
  a throttled team is observable, not a silent stall.

### Ceiling sizing (configurable per session, never hardcoded)

- Base share = `org_limit / N`, where **N is the session's ACTUAL team count**
  (`COUNT(ttx_teams)`), falling back to `ttx_sessions.max_teams` (DEFAULT 8) only
  when no teams exist yet. Sizing off the default would hand a 5-team exercise 8
  teams' worth of headroom; sizing off a hardcoded 5 would break a 3- or 7-team
  run.
- A `burst_factor` (≥ 1.0, env `ICDEV_LLM_TEAM_BURST_FACTOR`, default 1.5) lets a
  team briefly exceed its 1/N share so idle capacity is not stranded when only
  two teams are mid-response. Ceilings may therefore sum to more than the org
  limit — the intended burst allowance, safe because teams rarely peak together.
- Org limits: `ICDEV_LLM_ORG_RPM` (default 60), `ICDEV_LLM_ORG_TPM`
  (default 100000).

### Members share the team ceiling

A team's 3–5 members share one ceiling. That is correct and forced — gameday
members join by `join_code` and have no `dashboard_users` row, so per-member
ceilings are impossible (see lpx-teams-02).

## Relationship to the other rate/cost axes (no double-throttle)

| Axis | Module | Dimension |
|------|--------|-----------|
| Concurrency | `rate_gate.py` | requests in flight, process/host/cluster |
| Dollars | `proxy_budgets.py` (keys-02) | $ per scope/window |
| **Rate fairness** | **this card** | **RPM/TPM per team per session** |

All three are orthogonal and compose; none reads another's state.

## Storage

`llm_proxy_team_limits` (per-session per-team ceilings) and
`llm_proxy_team_usage` (minute-bucket rolling counters). Like their sibling
`ttx_api_log`, they carry no `tenant_id`/`classification` — the gameday
enforcement path uses `get_connection()` without a security context, so unset
tenant columns would only risk an RLS predicate mismatch. Windows are integer
`epoch // 60` buckets (PG-portable; no JSON SQL). Added to `tests/conftest.py`.

## Tests

`tests/test_lpx_team_limits.py` (8): sizing off actual team count, burst factor,
the RPM deny case, **one team looping does not degrade opponents** (fairness),
window reset next minute, TPM deny, unconfigured-session fail-open, and the
facilitator status flag. The deny case is asserted directly — it is the point.
