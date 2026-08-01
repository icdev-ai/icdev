# LPX teams-03 — Per-team spend attribution on the existing ttx_api_log hook

CUI // SP-CTI

## Goal

Let a facilitator answer "what did each team spend this exercise?" from the
gameday UI or a CLI (`--json`).

## The hook

`ttx_api_log(log_id, session_id, team_id, tool_slug, endpoint, call_id,
result_hash, called_at)` already logs every AI tool call per team and is indexed
on `(team_id, session_id)` (`idx_ttx_api_log_team`). It is the natural attribution
surface — but it was a **call log**, not a cost ledger: no token or cost columns.

## Decision — option (a): add columns to ttx_api_log

Two options were possible:

- **(a)** add `token_count` / `cost_usd` columns to `ttx_api_log`, or
- **(b)** join `ttx_api_log` to `token_tracker` / `llm_gateway_audit` by `call_id`.

**We took (a).** `token_tracker` (`agent_token_usage`) and `llm_gateway_audit` are
keyed by agent/project, not by the gameday `call_id`, so option (b) would be a
fragile cross-store join that is frequently empty for gameday tool calls. Option
(a) keeps attribution a **single-store query** against the table that already
carries the right key + index, populated at the existing `log_api_receipt` insert
hook. We do **not** write both — no duplicate cost ledger (the same discipline
teams-02 applied by reusing the keys-02 ledger rather than inventing a table).

The columns are added via the gameday `db.py` migrate path: included in the
`CREATE TABLE` (fresh installs) and in the guarded `_ADD_COLUMNS` ALTER
(pre-existing tables), mirroring how `ontology_tags_json` was added. Mirrored to
`icdev/apps/ai_gameday/db.py` and added to `tests/conftest.py`.

## Append-only in fact → declared append-only

Only `engine.log_api_receipt` inserts into `ttx_api_log`; every other reference is
a `SELECT`. It is therefore append-only in fact, and is now declared in
`APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py` (small focused diff — a
known merge-conflict hot file). Rows are never UPDATE/DELETE; a spend record is a
new insert.

## API — `tools/ttx/team_spend.py`

- `team_spend_report(session_id)` — per-team `call_count`, `total_tokens`,
  `total_cost_usd`, LEFT JOINed from `ttx_teams` so **zero-call teams appear too**.
- `session_spend_total(session_id)` — exercise roll-up + `per_team` breakdown.
- CLI: `python tools/ttx/team_spend.py <session_id> [--total] [--json]`.

`engine.log_api_receipt` gained optional `token_count` / `cost_usd` params
(default 0, backward compatible) so callers record spend at the same hook that
already logs the call.

## Attribution granularity (accepted limit)

Attribution is per-TEAM, never per-member: a team's members share one team
identity (join-by-code, no `dashboard_users` row). See lpx-teams-02.

## Tests

`tests/test_lpx_team_spend.py` (5): columns exist after migrate, receipt records
token+cost and sums per team, zero-call teams included, backward-compatible
default zero, session roll-up. Gameday schema regression (`test_penta_gd_schema`)
stays green.
