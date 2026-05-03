# TTX — Tabletop Exercise (GameDay) Engine

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## TTX Tabletop Exercise Engine
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Engine | tools/ttx/engine.py | Facade orchestrating all TTX subsystems: session lifecycle, inject dispatch, scoring, AAR | TTXEngine() instance methods | Session/inject/score dicts |
| Session Manager | tools/ttx/session_manager.py | Session CRUD — create, get by ID/join-code, list, state transitions (pending→active→paused→ended) | scenario_slug, session_mode, facilitator_name, duration_minutes, max_teams | Session dict with join_code |
| Team Manager | tools/ttx/team_manager.py | Team and member management within a session | session_id, team_name, member details | Team/member dicts |
| Inject Dispatcher | tools/ttx/inject_dispatcher.py | Seed, dispatch, and close scenario injects; unlock next async inject | session_id, inject dicts | Inject status dicts |
| AI Scorer | tools/ttx/ai_scorer.py | LLM-based scoring of team responses against inject rubrics | response text, inject context | Score dict (receipt_pts, judge_pts, time_bonus_pts, total_pts) |
| Persona Generator | tools/ttx/persona_generator.py | Generate AI-driven adversary/red-team personas for scenarios | scenario_slug, role | Persona description dict |
| Scenario Loader | tools/ttx/scenario_loader.py | Load scenario definitions from the scenario pack | scenario_slug | Scenario config dict |
| Leaderboard | tools/ttx/leaderboard.py | Compute live leaderboard, retrieve standings, award end-of-session ribbons | session_id | Ranked team list + ribbon awards |
| AAR Generator | tools/ttx/aar_generator.py | Generate After Action Review document summarizing session outcomes | session_id | AAR markdown/dict |
| Constants | tools/ttx/constants.py | Shared constants: SESSION_STATES, INJECT_STATES, SESSION_MODES, SCORE_CATEGORIES, TIME_BONUS_BRACKETS, RIBBON_DEFS, SCOREABLE_TOOLS | — | Constants |
