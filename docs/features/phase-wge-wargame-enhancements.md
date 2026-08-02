# Phase WGE: Wargame Enhancements

> **Classification:** CUI // SP-CTI
> **Impact Level:** IL4 (CUI/GovCloud) | IL5 (CUI/Dedicated)
> **Distribution:** Authorized ICDEV™ personnel only — not for public release
> **Handling:** Per DoDI 5200.48 and 32 CFR Part 2002

---

## Security Markings

This document is **Controlled Unclassified Information (CUI)** under the CUI Program (32 CFR Part 2002, EO 13556). It contains Specified CUI — Specified Program/Contract Technical Information (SP-CTI) which is subject to dissemination controls beyond basic CUI handling requirements.

| Attribute | Value |
|-----------|-------|
| Classification | CUI // SP-CTI |
| Originating Agency | ICDEV™ / IC Dev Division |
| Impact Level | IL4 (GovCloud), IL5 (Dedicated) |
| Framework Authority | NIST SP 800-53 Rev 5, NIST SP 800-171 Rev 2 |
| Authorized Recipients | ICDEV™ operators, authorized government program officers, cleared contractors with Need-to-Know |
| Decontrol Condition | Per program release authority or 25-year auto-declassify |
| Handling Requirements | Encrypt in transit (TLS 1.3+), encrypt at rest (AES-256/FIPS 140-3), no transmission via unclassified networks without approved guard |

**Export Control Notice:** This document may contain technical data subject to EAR/ITAR. Disclosure to foreign nationals requires prior authorization from the cognizant security authority.

**AI-Generated Content Warning:** Portions of this document were produced with AI assistance. All AI-generated content has been reviewed and validated by authorized personnel per ICDEV™ AI governance policy (OMB M-25-21, NIST AI RMF).

---

## Overview

This document describes the six wargame enhancements delivered in the WGE phase. Each enhancement extends the AI GameDay and Strategos wargame platform with new capabilities for competitive tabletop exercises and multi-domain operations.

---

### 1. TTX Engine (Generic Tabletop Exercise Engine)

A reusable, scenario-agnostic engine (`tools/ttx/`) that drives any competitive tabletop exercise. It handles session lifecycle, inject dispatch, team scoring, and after-action report generation — decoupled from the AI GameDay scenario pack so new scenario packs can be dropped in without touching engine code.

### 2. AI GameDay Application

A full Flask child app (`apps/ai_gameday/`) providing the player, facilitator, leaderboard, and scenario-builder UIs at `/gameday`. Teams compete through a series of AI-focused injects (signal cluster analysis, COA recommendation, ransomware response, fine-tune sprint, war council brief) scored by the TTX engine.

### 3. Scenario Pack #1 — AI GameDay Injects

Five structured inject files (`scenarios/ai_gameday/injects/`) covering the core AI GameDay scenario arc, plus four persona cards and three scoring rubrics. Each inject is a YAML manifest with situation text, expected actions, and scoring weights consumed by the TTX AI scorer.

### 4. Persona Generator

A `tools/ttx/persona_generator.py` module that synthesizes role-specific personas for wargame participants from YAML persona cards. Personas define communication style, decision bias, and domain expertise so the AI scorer can evaluate responses in role-appropriate context.

### 5. AI Scorer and Rubric Engine

`tools/ttx/ai_scorer.py` evaluates team inject responses against YAML rubrics using an LLM-backed scoring pipeline. Scores are normalized 0–100, written to the leaderboard DB table, and surfaced in real time on the facilitator dashboard.

### 6. After-Action Report (AAR) Generator

`tools/ttx/aar_generator.py` produces a structured post-exercise AAR in Markdown, summarizing team performance, inject-by-inject breakdowns, identified strengths and gaps, and recommended follow-on training. Reports are exportable from the session results page.

---

## Architecture

### Modules

#### `tools/ttx/` — TTX Engine (scenario-agnostic)

| Module | Purpose |
|--------|---------|
| `engine.py` | Facade orchestrating all subsystems: session lifecycle, team/member management, inject seeding/dispatch, response submission, scoring, leaderboard computation, AAR, and ribbon awards. |
| `session_manager.py` | Session CRUD: create/retrieve sessions, manage state transitions (`pending → active → paused → ended`), generate join codes. |
| `team_manager.py` | Team formation and roster management: create teams, add/retrieve members, manage scores and rankings. |
| `inject_dispatcher.py` | Inject lifecycle for live (timed) and async (sequential) modes. Seeds injects from scenario, dispatches on schedule, closes injects, applies consequence system (world-state mutations triggered by scoring thresholds). |
| `ai_scorer.py` | LLM-judge scoring pipeline: validates AI tool receipts against `ttx_api_log`, applies receipt and fine-tune gates, computes judge points with Academy role bonuses, calculates time-bonus brackets. |
| `leaderboard.py` | Real-time leaderboard computation. Aggregates team scores by category (`receipt_pts`, `judge_pts`, `time_bonus_pts`, `total_pts`), assigns rankings, awards achievement ribbons. |
| `aar_generator.py` | Produces Markdown AAR: scenario summary, team performance, per-team AI usage statistics, final leaderboard, and ribbon awards. |
| `persona_generator.py` | LLM-backed persona synthesis for exercise participants; falls back to deterministic pools when LLM unavailable. Supports `military_intel`, `civilian_analyst`, `contractor_swe`, `ciso_lead` roles. |
| `scenario_loader.py` | YAML scenario loader and validator. Resolves packs from `scenarios/`, merges inject/persona/rubric sub-files inline, handles `body_variants` and consequence configuration. |
| `constants.py` | Shared constants: session/inject/mode states, score categories, time-bonus brackets, ribbon definitions, scoreable tool catalog, receipt/fine-tune gate multipliers, Academy role→mission→rubric bonus mappings. |

#### `apps/ai_gameday/` — Flask Child App

| Module | Purpose |
|--------|---------|
| `blueprint.py` | Flask blueprint registering all `/gameday` and `/api/gameday` routes (35 total). Implements session/team management, inject dispatch, response submission, scoring, leaderboard, scenario builder, and AAR export. The registration/team-formation workflow is **designed but not wired** — see [Registration & team formation: designed, not built](#registration--team-formation-designed-not-built). |
| `db.py` | Database schema and idempotent migration. Defines all `ttx_*` tables via DDL; handles alter-table migrations for columns added post-launch. |
| `constants.py` | App-level constants: `INJECT_TYPES`, `ROLE_TECH_WEIGHTS`, `SCENARIO_TECH_PROFILES` (tech_ideal/min/max for scenario recommendation), and XP tier definitions. |

#### `scenarios/ai_gameday/` — Scenario Pack #1

| Path | Contents |
|------|----------|
| `scenario.yaml` | Master definition — Operation CIPHER FORGE, Zero Hour (120-min live, 8-team max). Defines roles, injects, scoring rules, and Academy bonuses. |
| `injects/inject-01-signal-cluster.yaml` | Anomalous Signal Cluster, Sector 7 — SIGINT threat assessment (15 min). |
| `injects/inject-02-coa-force-posture.yaml` | COA Required: Recommend Force Posture — military strategy (40 min). |
| `injects/inject-03-ransomware-hit.yaml` | Ransomware Cascade Incident — incident response (sequential). |
| `injects/inject-04-fine-tune-sprint.yaml` | ML Model Fine-Tune Sprint — AI engineering (sequential). |
| `injects/inject-05-war-council-brief.yaml` | War Council Strategic Brief — executive summary (final inject). |
| `personas/military_intel.yaml` | Intelligence Officer persona card. |
| `personas/civilian_analyst.yaml` | Government Analyst (GS-12 to SES) persona card. |
| `personas/contractor_swe.yaml` | Defense Contractor Engineer persona card. |
| `personas/ciso_lead.yaml` | CISO / Security Executive persona card. |
| `rubrics/intel_assessment.yaml` | Scoring rubric for SIGINT analysis injects. |
| `rubrics/coa_recommendation.yaml` | Scoring rubric for military COA evaluation. |
| `rubrics/ai_build_sprint.yaml` | Scoring rubric for ML/code quality assessment. |

---

### API Routes

All routes are registered under the `ai_gameday` Flask blueprint.

#### Page Routes

| Route | Method | Description |
|-------|--------|-------------|
| `/gameday` | GET | Hub: list active, pending, and past sessions with scenario list. |
| `/gameday/session/<session_id>/play` | GET | Player console: session info, teams, roles, AI tools catalog. |
| `/gameday/session/<session_id>/facilitate` | GET | Facilitator console: session state, teams, inject controls, leaderboard. |
| `/gameday/leaderboard/<session_id>` | GET | Live leaderboard with ribbon awards. |
| `/gameday/session/<session_id>/results` | GET | Post-session results: AAR, per-team AI stats, final leaderboard. |
| `/gameday/scenarios` | GET | Scenario manager: YAML file scenarios and DB-authored scenarios. |
| `/gameday/scenarios/builder` | GET | Scenario builder UI: inject templates, AI tools, rubric editor. |
| `/gameday/session/<session_id>/register` | GET | **NOT BUILT** — designed player registration form for skill→role matching. |
| `/gameday/session/<session_id>/registrations` | GET | **NOT BUILT** — designed facilitator view: all registrations and team formation plan. |

#### API Routes — Session & Team Management

| Route | Method | Description |
|-------|--------|-------------|
| `/api/gameday/session` | POST | Create new session; returns `session_id` and `join_code`. |
| `/api/gameday/session/<session_id>/state` | PATCH | Transition session state (`pending → active → paused → ended`). |
| `/api/gameday/session/join` | POST | Join session via join code; create team. |
| `/api/gameday/team/join` | POST | Join team via join code; add member with role and Academy profile link. |
| `/api/gameday/team/<team_id>/name` | PATCH | Update team display name. |
| `/api/gameday/team/<team_id>/academy` | POST | Link Academy username to team member(s). |
| `/api/gameday/session/<session_id>/injects` | GET | List injects, optionally filtered by state. |

#### API Routes — Injects

| Route | Method | Description |
|-------|--------|-------------|
| `/api/gameday/inject/<inject_id>/dispatch` | POST | Dispatch inject to team (live mode). |
| `/api/gameday/inject/<inject_id>/close` | POST | Close inject; stop accepting responses. |

#### API Routes — Response & Scoring

| Route | Method | Description |
|-------|--------|-------------|
| `/api/gameday/response` | POST | Submit response to inject; triggers scoring and async unlock check. |
| `/api/gameday/invoke` | POST | Server-side tool invocation; validates session/team, calls tool, generates verified receipt. |
| `/api/gameday/api-log` | POST | Legacy self-report receipt endpoint (unverified, backward-compat). |

#### API Routes — Leaderboard

| Route | Method | Description |
|-------|--------|-------------|
| `/api/gameday/session/<session_id>/leaderboard` | GET | Compute and return leaderboard (rank, scores by category). |
| `/api/gameday/session/<session_id>/ribbons` | GET | Return end-of-session category ribbon awards. |

#### API Routes — Scenario Management

| Route | Method | Description |
|-------|--------|-------------|
| `/api/gameday/scenarios` | GET | List all scenarios (DB + file-based). |
| `/api/gameday/scenarios` | POST | Save/upsert scenario to DB. |
| `/api/gameday/inject-templates` | GET | List inject template library. |
| `/api/gameday/inject-templates` | POST | Save inject template to DB. |

#### API Routes — Registration & Team Formation

> **NOT BUILT.** None of the seven routes below is registered in `apps/ai_gameday/blueprint.py`.
> They are the design for an unwired feature — see
> [Registration & team formation: designed, not built](#registration--team-formation-designed-not-built).

| Route | Method | Description |
|-------|--------|-------------|
| `/api/gameday/session/<session_id>/register` | POST | Submit player registration (skill→role matching). |
| `/api/gameday/session/<session_id>/registrations` | GET | Retrieve all registrations for session. |
| `/api/gameday/session/<session_id>/match-skill` | POST | Live skill→role matcher for typeahead. |
| `/api/gameday/session/<session_id>/form-teams` | POST | Run snake-draft team formation; returns plan. |
| `/api/gameday/session/<session_id>/confirm-teams` | POST | Materialize formation plan into `ttx_teams` and `ttx_team_members`. |
| `/api/gameday/registration/<registration_id>` | DELETE | Delete registration (pre-confirm only). |
| `/api/gameday/session/<session_id>/formation-plan/move` | POST | Move player to different team slot in draft plan. |

#### API Routes — Scenario Selection & AAR

| Route | Method | Description |
|-------|--------|-------------|
| `/api/gameday/session/<session_id>/scenario-recommendation` | GET | Recommend scenario based on role composition (`tech_ratio` scoring). |
| `/api/gameday/session/<session_id>/scenario` | PATCH | Admin override: swap scenario (pending sessions only). |
| `/api/gameday/session/<session_id>/aar` | GET | Export After-Action Report as Markdown. |

---

### Database Schema

All tables are prefixed `ttx_` and reside in the main `data/icdev.db` (or PostgreSQL equivalent). Schema is created idempotently by `apps/ai_gameday/db.py` at app startup.

| Table | Primary Key | Description |
|-------|-------------|-------------|
| `ttx_sessions` | `session_id` TEXT | Master session record: `scenario_slug`, `state`, `facilitator`, `join_code`, `mode` (live/async), `duration_min`, `config_json`, `world_state_json`. |
| `ttx_teams` | `team_id` TEXT | Teams within a session: `session_id` FK, `team_name`, `join_code`, `total_score`, `rank_pos`. |
| `ttx_team_members` | `member_id` TEXT | Individual players: `team_id` FK, `player_name`, `role_id`, `persona_json`, `academy_username`, `academy_profile_json`. |
| `ttx_injects` | `inject_id` TEXT | Scenario injects: `session_id` FK, `slug`, `title`, `body_md`, `state` (pending/dispatched/closed), `config_json` (scoring + consequence), dispatch/close timestamps. |
| `ttx_responses` | `response_id` TEXT | Team responses to injects: `team_id` FK, `inject_id` FK, `response_text`, `ai_receipts_json`, `time_taken_s`, `target_grid_json`. |
| `ttx_scores` | `score_id` TEXT | Scoring breakdown per response: `response_id` FK, `team_id` FK, `inject_id` FK, `receipt_pts`, `judge_pts`, `time_bonus_pts`, `total_pts`, `judge_rationale_json`. |
| `ttx_api_log` | `log_id` INTEGER | AI tool call receipts: `session_id` FK, `team_id` FK, `tool_slug`, `endpoint`, `call_id` UNIQUE, `result_hash`, `called_at`. |
| `ttx_leaderboard` | `lb_id` INTEGER | Cached leaderboard snapshot: `session_id` FK, `team_id` FK, `rank_pos`, score breakdown columns, `computed_at` (UNIQUE on session+team). |
| `ttx_scenarios` | `scenario_id` INTEGER | DB-authored scenarios: `slug` UNIQUE, `name`, `yaml_content`, `created_by`, `is_active`, `created_at`. |
| `ttx_inject_templates` | `template_id` INTEGER | Reusable inject templates: `name`, `inject_type`, `body_md`, `rubric_json`, `ai_tools_json`, `created_at`. |
| `ttx_registrations` ⚠️ | `registration_id` INTEGER | **UNWIRED** — no module reads or writes it. Pre-session player registrations: `session_id` FK, `player_name`, `email`, `stated_skill`, `matched_role_id`, `match_confidence`, `match_method`, `match_reasoning`, `academy_username`. |
| `ttx_formation_plan` ⚠️ | `plan_id` INTEGER | **UNWIRED** — no module reads or writes it. Snake-draft team formation plan (pre-confirm): `session_id` FK, `registration_id` FK, `team_slot`, `team_name`, `confirmed`, `created_at`. |

⚠️ The last two tables exist **only** in `tools/db/schema/pg_consolidated.sql`. No migration creates
them and no runtime DDL (`apps/ai_gameday/db.py`) declares them, so they are absent from any freshly
initialised database. See
[Registration & team formation: designed, not built](#registration--team-formation-designed-not-built).

**Indexes:** `idx_ttx_teams_session`, `idx_ttx_injects_session`, `idx_ttx_injects_slug`, `idx_ttx_responses_team`, `idx_ttx_responses_inject`, `idx_ttx_scores_team`, `idx_ttx_api_log_team`, `idx_ttx_leaderboard_session`.

**Post-launch alter-table migrations (idempotent):** `ttx_sessions.world_state_json`, `ttx_team_members.academy_username`, `ttx_team_members.academy_profile_json`, `ttx_responses.target_grid_json`.

---

## Registration & team formation: designed, not built

**Status: DESIGNED, NOT WIRED. Do not read this document's registration sections as shipped
behaviour.** Recorded by `gdx-dead-02` (2026-08-01); the wiring decision is carded as `gdx-reg-01`.

What actually exists on disk:

| Artifact | State |
|---|---|
| `ttx_registrations`, `ttx_formation_plan` DDL | Present in `tools/db/schema/pg_consolidated.sql` only — **no migration creates them** |
| `tools/dashboard/templates/ai_gameday/{register,registrations}.html` | Present in both trees (promoted by `gdx-mir-02`), rendered by **no route** |
| 2 UI routes + 7 API routes documented above | **None registered** — `blueprint.py` has 35 routes, zero of them registration |
| `apps/ai_gameday/registration.py` (skill matching + snake draft) | **Deleted** by `penta-gd-03` (2026-07-18) as unreachable dead code |

The history is deliberately two-sided and both halves are correct:

- **`penta-gd-03` deleted the implementation.** The 697-line module was reachable from no route, its
  only caller was a demo script, and it queried two tables absent from the runtime DDL — so it was
  broken on every fresh database. Deleting unreachable code was right.
- **`gdx-mir-02` kept the templates and the DDL.** The design is complete and the UI is written;
  dropping tables that no migration creates would mean a destructive migration against the live
  PostgreSQL schema to remove something inert. Keeping them is reversible and zero-risk.

Net: the *design* survives (doc + DDL + templates), the *unreachable code* does not. Anyone picking
up `gdx-reg-01` rebuilds the routes and the matching logic against the DDL and templates already
here, and adds the two tables to `apps/ai_gameday/db.py` `_DDL` plus a migration so they exist
outside the consolidated snapshot.

---

## NIST 800-53 Compliance

The WGE enhancements satisfy three NIST SP 800-53 Rev 5 controls relevant to the wargame platform's participant management, audit trail, and input handling posture.

---

### AC-2 — Account Management

**Control summary:** Organizations must manage information system accounts including creation, activation, modification, review, disabling, and removal.

**How WGE satisfies AC-2:**

> **Partial coverage.** The registration/team-formation subsystem is **not built** (see
> [above](#registration--team-formation-designed-not-built)), so it contributes **nothing** to this
> control today. The rows below are limited to what is actually wired. Account *creation* and
> *removal* are the coverage gap; do not cite them as satisfied.

Participant lifecycle controls that are implemented:

| Feature | AC-2 Mapping |
|---------|-------------|
| Join codes on `ttx_sessions` and `ttx_teams` — scoped access tokens that expire with the session | Access activation: participants must present a valid code; no open enrollment |
| `ttx_team_members.role_id` — each member is bound to a specific role (`military_intel`, `civilian_analyst`, `contractor_swe`, `ciso_lead`) | Role assignment: role-based access aligns with least-privilege and need-to-know principles |
| `ttx_team_members.academy_username` / `academy_profile_json` — links exercise identity to an authoritative Academy profile | Account linkage: participants are traceable to an authoritative identity store rather than anonymous |
| Session state machine (`pending → active → paused → ended`) terminates all access at `ended` state | Account disabling: once a session ends, participation is closed; no further responses or tool invocations are accepted |

**Not satisfied — pending `gdx-reg-01`:**

| Gap | Why it is open |
|---|---|
| Account creation via a registration record | `ttx_registrations` is unwired DDL; participants are created ad hoc as `ttx_team_members` with no captured registration identity |
| Account removal / revocation before confirmation | The `/api/gameday/registration/<registration_id>` DELETE endpoint does not exist |

---

### AU-2 — Event Logging

**Control summary:** Organizations must identify events that the information system is capable of logging in support of the audit function, and coordinate the event-logging function with other organizations.

**How WGE satisfies AU-2:**

The platform maintains a multi-layer, append-only audit trail across all exercise activities:

| Feature | AU-2 Mapping |
|---------|-------------|
| `ttx_api_log` table — records every AI tool call with `session_id`, `team_id`, `tool_slug`, `endpoint`, `call_id` (UNIQUE), `result_hash`, and `called_at` timestamp | Tool invocation events: every AI tool use during exercise is logged with a tamper-evident hash and unique call identifier |
| `/api/gameday/invoke` endpoint — server-side tool invocation generates a verified receipt written to `ttx_api_log` before returning to the client | Authoritative event source: tool receipts are generated server-side, not self-reported, eliminating client-side forgery |
| `ttx_responses` table — captures full response text, AI receipts JSON, and time-taken per inject per team | Response submission events: all participant inputs are recorded with temporal metadata |
| `ttx_scores` table — stores scoring breakdown (`receipt_pts`, `judge_pts`, `time_bonus_pts`, `total_pts`) with `judge_rationale_json` | Scoring events: automated scoring decisions are recorded with rationale for post-exercise review |
| `ttx_sessions` state transitions via `/api/gameday/session/<session_id>/state` PATCH — session lifecycle events | Session lifecycle events: session creation, activation, pause, and termination are recorded |
| After-Action Report (`aar_generator.py`) — exports a structured summary of all team activities, AI usage statistics, and final leaderboard | Audit reporting: the AAR constitutes the formal audit summary for each exercise, satisfying the coordination and reporting aspect of AU-2 |

All `ttx_*` tables use append-only semantics consistent with ICDEV's NIST AU immutability requirement; scoring and log rows are never updated or deleted after creation.

---

### SI-10: Information Input Validation

**Control summary:** The information system checks the validity of information inputs and rejects or quarantines inputs that do not meet defined criteria.

**How WGE satisfies SI-10:**

Input validation is enforced at three layers — schema, state machine, and receipt verification — across the inject response and tool invocation pipelines.

The scenario loader (`scenario_loader.py`) validates every YAML scenario pack against a strict schema before any content reaches the engine. Required top-level keys (`slug`, `injects`, `personas`, `rubrics`, `scoring_rules`) are checked at load time, and packs that are missing fields, contain malformed inject definitions, or reference undefined rubric slugs are rejected with a descriptive error before any DB write occurs. This prevents malformed exercise content — whether from authoring errors or intentional injection — from corrupting session state.

The session state machine in `session_manager.py` enforces a strict set of valid state transitions (`pending → active → paused → ended`). Any API request that attempts an out-of-sequence or invalid transition is rejected before touching the database. Similarly, the inject response submission endpoint validates that the submitted `session_id`, `team_id`, and `inject_id` form a valid FK chain; orphaned or spoofed identifiers are refused at the API boundary. The AI receipt verification path in `ai_scorer.py` cross-references every submitted `call_id` value against the authoritative `ttx_api_log` table — receipts that cannot be corroborated trigger a zero-multiplier receipt gate (`0.0` vs `1.0` for server-verified calls), effectively quarantining unverified inputs from influencing scored outcomes. (A further quarantine layer over skill-to-role matching confidence was designed around `ttx_registrations.match_confidence`, but that subsystem is **not built** — see [above](#registration--team-formation-designed-not-built) — so it contributes nothing to this control today.)

| Feature | SI-10 Mapping |
|---------|-------------|
| `scenario_loader.py` schema validation — rejects malformed scenario packs at load time | Structural input validation: scenario content is schema-checked before reaching the engine |
| Session state machine in `session_manager.py` — invalid transitions rejected before any DB write | State input validation: API callers cannot submit out-of-sequence state changes |
| AI receipt verification in `ai_scorer.py` — `call_id` cross-referenced against `ttx_api_log`; unverified receipts apply 0.0 gate multiplier | Receipt input validation: self-reported tool receipts are flagged and quarantined from scoring |
| `/api/gameday/api-log` legacy endpoint — explicitly marked unverified; scoring distinguishes it from `/invoke` | Input provenance tracking: trusted vs. untrusted inputs are differentiated with appropriate scoring penalties |
| ~~`ttx_registrations.match_confidence` threshold~~ — **NOT BUILT**, contributes nothing to SI-10 today | *(designed only — confidence-gated input acceptance; pending `gdx-reg-01`)* |
| Inject response submission validates `session_id`, `team_id`, and `inject_id` FK relationships | Referential input validation: orphaned or spoofed identifiers are rejected at the API boundary before DB writes occur |
