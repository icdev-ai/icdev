# TTX Engine — Tabletop Exercise (AI GameDay)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## TTX Engine — `tools/ttx/`

Generic tabletop exercise engine backing the AI GameDay platform at `/gameday`. Supports live (timed) and async (sequential) session modes with AI-based scoring, real-time leaderboard, and end-of-session AAR generation.

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| TTX Engine | tools/ttx/engine.py | Main orchestrator facade: `TTXEngine` class wraps all subsystems (session lifecycle, team mgmt, inject dispatch, AI scoring, leaderboard, AAR). Entry point for all exercise operations. | `TTXEngine()` instance methods | dict / list[dict] |
| TTX Leaderboard | tools/ttx/leaderboard.py | Real-time leaderboard and end-of-session category ribbons. `compute_leaderboard(session_id)` recomputes team rankings and persists to `ttx_leaderboard`. `get_leaderboard(session_id)` returns cached or recomputed rankings. `award_ribbons(session_id)` determines Speed King / AI Innovator / Doctrine Scholar / Strategist ribbon winners. | session_id (int) | list[dict] ranks or dict[slug, winner] |
| TTX Session Manager | tools/ttx/session_manager.py | Session lifecycle CRUD. Creates/gets/lists/updates `ttx_sessions` rows; generates random `join_code`; enforces `SESSION_STATES` enum on transitions. Public API: `create_session`, `get_session`, `get_session_by_code`, `list_sessions`, `update_session_state`. | session kwargs | dict \| list[dict] |
| TTX Team Manager | tools/ttx/team_manager.py | Team formation and roster management. Creates teams within a session, adds/removes members, queries rosters from `ttx_teams`. | session_id, team kwargs | dict \| list[dict] |
| TTX Inject Dispatcher | tools/ttx/inject_dispatcher.py | Inject dispatch for live (timed) and async (sequential) modes. Seeds injects from scenario, dispatches/closes individual injects, unlocks next async inject. | session_id, inject_id | dict |
| TTX AI Scorer | tools/ttx/ai_scorer.py | AI scoring: receipt validation + LLM judge + time bonus. Validates ICDEV tool receipts, runs LLM quality evaluation (0–100), applies time-bonus brackets, writes to `ttx_scores`. | response dict, inject dict | scored dict |
| TTX Scenario Loader | tools/ttx/scenario_loader.py | Scenario YAML loader and validator. Loads scenario definitions from `tools/ttx/scenarios/`, validates required keys, returns structured scenario dict. | scenario_slug (str) | dict |
| TTX Persona Generator | tools/ttx/persona_generator.py | LLM-based persona generation for exercise participants. Generates role-specific personas (job title, background, objectives, decision style) for team members. | role kwargs | dict |
| TTX AAR Generator | tools/ttx/aar_generator.py | After-Action Report (AAR) generator. Aggregates session scores, inject responses, leaderboard, and ribbons into a structured markdown/JSON AAR for facilitator review. | session_id (int) | dict (AAR) |
| TTX Constants | tools/ttx/constants.py | Shared constants: `SESSION_STATES`, `INJECT_STATES`, `SESSION_MODES`, `SCORE_CATEGORIES`, `TIME_BONUS_BRACKETS`, `RIBBON_DEFS`, `SCOREABLE_TOOLS`. | (import) | constants |
| TTX Team Spend | tools/ttx/team_spend.py | Per-team spend attribution from `ttx_api_log` (lpx-teams-03). Sums `token_count`/`cost_usd` per team for an exercise (`team_spend_report`) + session roll-up (`session_spend_total`); zero-call teams included. Option (a): cost columns added to `ttx_api_log` (single-store, no cross-store `call_id` join); `log_api_receipt` records them at the existing hook. `ttx_api_log` is append-only. Attribution is per-TEAM (join-by-code, no per-member). | session_id [--total] [--json] | per-team spend / roll-up |
