# CUI // SP-CTI

# FGA-00 — Disposition of the five FORGE Academy / AI GameDay audit documents

**Date:** 2026-07-27
**Sources evaluated** (all in `C:\AI\searches\FORGE`):

| Doc | Date | Provenance |
|---|---|---|
| `academy_mission_audit.md` | 2026-07-26 | "Student simulation bot" — mission content sweep |
| `academy_test_report.md` | 2026-07-26 | Automated student bot — browser walkthrough + HTTP audit |
| `academy_student_experience_report.md` | 2026-07-27 | Hands-on student simulation |
| `academy_thorough_audit_all_missions.md` | 2026-07-27 | Follow-up; all 39 catalogued missions via HTTP |
| `gameday_student_experience_report.md` | 2026-07-27 | GameDay: API probing + browser verification |

The first two were explicitly held out of scope by `docs/spikes/ahx-00-agent-audit-docs-disposition.md`
("the work they describe is unfinished"). All five are dispositioned here.

**Method:** every material claim was checked against the tree at `C:\AI\ICDev` on 2026-07-27,
file:line where cited, against the **root `apps/` + `tools/` copies** — never the `icdev/`
packaged mirror. Both existing suites were executed.

**Prior art this document defers to (do not re-litigate):** the **`penta`** card
(`args/projects.yaml:5475`, epics `gd` + `aca`). PENTA ran five independent reviews of this exact
surface, shipped 31 merged PRs, and closed **2026-07-18** — eight days before the earliest of
these five reports. It already delivered the code-runner hardening (`penta-aca-02`), the AI League
engine repoint, the `ontology_tags_json` migration (`penta-gd-03`), the stored-XSS escaping
(`penta-gd-04`), the `/forge-academy` → `/academy` nav fix (`penta-aca-01`), the Tier-3
canvas-taxonomy lesson rewrite (`penta-aca-03`), 10 new missions (`penta-aca-04/05`), 3 TTX
scenarios (`penta-gd-06`), and the `penta-fix-01/02/03` follow-ups.

---

## 1. Headline verdict

**About one quarter of the recommended surface is real. The rest is refuted by working, tested
code, or was already fixed before the audit ran.**

| Doc | Verdict |
|---|---|
| `academy_mission_audit.md` | **Reject** — internally inconsistent; lists the same missions as both "no steps" and "404 not found", including missions its own companion report calls rich |
| `academy_test_report.md` | **Partly adopt** — the empty-mission census is right; every backend claim is wrong |
| `academy_student_experience_report.md` | **Partly adopt** — symptoms observed accurately, causes diagnosed wrongly |
| `academy_thorough_audit_all_missions.md` | **Partly adopt** — best of the five; the content census is the durable contribution |
| `gameday_student_experience_report.md` | **Mostly reject** — 6 of 10 claims refuted, 2 stale, 2 partial, 0 confirmed as written |

**The single most important correction: the reports' largest recommendation is inverted.**
They prescribe *"author step content for M01–M10 — High effort"* and grade content coverage
`D`/`C+`. Tier 1 is not unwritten. It is **unwired**: 199 markdown step files across 43 mission
directories sit on disk and are never ingested. The fix is a registration change, not an
authoring project.

This is the second consecutive external analysis of this repository to produce confident gap
claims that did not survive contact with the code — see `ahx-00` §1 for the first. Three recurring
failure modes are visible again here and are worth naming so the next reader recognises them:

1. **Wrong URL, confident conclusion.** The reports probed `/academy/api/progress`,
   `/academy/api/achievements`, `/academy/api/missions`, got 404s, and concluded "zero API
   endpoints exist for gamification data … the XP shown in the UI is hardcoded." The real prefix
   is `/api/academy/*` — the segments are transposed. There are 25 registered routes and 23
   `fa_*` tables. Likewise `/gameday/scenario/build` (a URL that never existed) versus the real
   `/gameday/scenarios/builder`.
2. **Unprivileged tester reads RBAC as breakage.** Oracle is gated to `admin/pm/isso`
   (`auth.py:26`); the GameDay responses API is `@require_facilitator` (`blueprint.py:503`). A
   401/redirect was recorded as "produces zero predictions" and "returns an empty array".
3. **Stale checkout.** Two GameDay findings were fixed on `main` on 2026-07-18, eight days before
   the report was written.

### Evidence base

| Check | Result |
|---|---|
| `tests/test_penta_aca_{sandbox,content_seed,routes,oracle}.py` | **112 passed** |
| `tests/test_penta_gd_{routes,league,scoring,schema}.py` | **75 passed** |
| `BUILTIN_MISSIONS` / `BUILTIN_STEPS` | 89 missions / 36 keyed / 90 step records |
| Missions with **zero** seeded steps | **53** |
| — of those, with step content **on disk** (orphaned) | **43** |
| — of those, with nothing on disk (genuinely unwritten) | 10 |
| `watch` steps lacking `demo_output` **and** `demo_url` | **27 of 27** |
| `AI_TOOLS_CATALOG` entries that are POST-only `/api/` paths | **12 of 34** |
| `hub.html` references to `/gameday/scenarios` | **0** |

---

## 2. Verified TRUE — carded as `fga-`

| # | Finding | Evidence |
|---|---|---|
| 1 | **43 mission directories are orphaned**; every Tier-1 mission renders "No steps found for this mission." | Steps are DB-backed and seeded **only** from the hand-maintained `BUILTIN_STEPS` dict. `apps/forge_academy/content_loader.py:1618` — `if existing == 0 and m["slug"] in BUILTIN_STEPS: _seed_steps(...)`. A mission absent from that dict gets zero step rows, permanently. The `content/` tree is never scanned. `content/tier1/m01-llm-fundamentals/steps/` holds `step1_what_is_an_llm.md`, `step2_token_economics.md`, `step3_temperature.md`, `step4_context_window.md`, `step5_llm_router.md` plus starter/test `.py` — and `m01-llm-fundamentals` has no `BUILTIN_STEPS` key. Same for `m02`–`m10`, all `m-ace-*`, `m-gov-*`, `m-sre-*`, `m-netops-*`, `m-readiness-*`, `m-docgen-*`, `m-isso-*`, `m-issm-*`. |
| 2 | **No reverse-direction test**, so the 43 orphans pass CI silently | `tests/test_penta_aca_content_seed.py:89` (`test_every_builtin_step_content_file_exists`) asserts only *declared step → file exists*. Nothing asserts *file on disk → declared in `BUILTIN_STEPS`*. |
| 3 | **Guild creation 500s on every call** | `apps/forge_academy/blueprint.py:586` calls `create_guild(name=…, description=…, invite_code=…, created_by=…)`; `apps/forge_academy/db.py:788` is `create_guild(name, description, created_by)`. `inspect.signature(db.create_guild).bind(...)` → `TypeError: got an unexpected keyword argument 'invite_code'`. `db.create_guild` generates its own code internally (`db.py:790`), so the `invite_code` returned to the client at `blueprint.py:588` is a discarded local value that would not match the stored one even once the signature is fixed. Zero test coverage — `grep create_guild tests/` returns nothing. |
| 4 | **27 of 27 `watch` steps render an identical fake "▶ Demo Output" block** | Hardcoded `{% else %}` fallback at `tools/dashboard/templates/forge_academy/partials/_step_watch.html:20-34`, rendered whenever a watch step's `config_schema` supplies neither `demo_output` nor `demo_url`. **No seeded watch step supplies either.** This is the "template injection" the reports saw on 8 missions; the true blast radius is 27. It is also **wrong code**: `LLMRouter.get_provider_for_function('chat')` returns a **tuple**, so the displayed `provider.chat(messages=[...])` would raise `AttributeError`. A training platform is teaching a call that does not work against its own API, under a heading claiming it is real output. |
| 5 | **Profile save drops `display_name` and writes a `tenant_id IS NULL` orphan row** | `blueprint.py:373` calls `get_or_create_user(email, display_name=…)` **without** `tenant_id`, while every page reads via `_fa_user()` (`blueprint.py:131`) **with** `tenant_id=_fa_tenant_id()`. In multi-tenant mode the role is written where no page reads it. `get_or_create_user` also returns an existing row without updating `display_name`, so the POSTed name is silently discarded. `wizard_answers` are computed at `:378` and never stored. |
| 6 | **Arena is permanently empty** | `blueprint.py:335-337` selects `fa_challenges WHERE ends_at > now()`. Repo-wide, `fa_challenges` is only ever CREATEd and SELECTed — **zero INSERT statements**, no seeder, no admin-create route. The entry API at `blueprint.py:616` writes `fa_challenge_entries` but is unreachable. |
| 7 | **Workflow Builder palette empty via a swallowed import failure** | `apps/forge_academy/integrations.py:53-59` catches any exception from the pattern-registry import, logs a `warning`, and returns `[]`. `blueprint.py:353` feeds that to the template, which renders "No patterns available." (`workflow_builder.html:89`). The backend is real (`blueprint.py:637-658` → `integrations.create_workflow` → `tools.aisg.visual_agent_builder`); the failure is invisible. |
| 8 | **GameDay scenario manager and builder are navigationally unreachable** | Routes exist and are tested — `apps/ai_gameday/blueprint.py:178` `/gameday/scenarios`, `:211` `/gameday/scenarios/builder`, asserted in `tests/test_penta_gd_routes.py:356-357`. But `tools/dashboard/templates/ai_gameday/hub.html` contains **zero** references to either. The builder is linked only from `scenario_manager.html`, which is itself orphaned from the hub. |
| 9 | **12 of 34 GameDay AI-tool links are POST-only APIs rendered as GET anchors** | `apps/ai_gameday/constants.py:61-103` `AI_TOOLS_CATALOG`; `player.html:98` renders each as `<a href="{{ tool.endpoint }}" target="_blank">`. Twelve entries are `/api/…` POST endpoints (`/api/strategos/oracle`, `/api/finetune/jobs`, `/api/readiness/check`, …) → 405/404 in a new tab. Two of those additionally contain unresolved `{id}` placeholders (`/api/strategos/wargame/{id}/ooda`, `/api/ace/coworker/{id}/result`). The other 22 point at real pages and work. |

A tenth item is carded as **verify-before-fix**, not as a defect: the reports claim the scenario
picker is cosmetic and all 9 scenarios serve the same 6 injects. Both disk and code contradict
this (§3), but the observation was specific and repeated across 9 sessions, so `fga-gd-03`
reproduces it against a live session before any fix is written.

---

## 3. Verified FALSE — not carded

| Claim | Why it is wrong |
|---|---|
| "The '✓ Understood → Continue' button is a no-op on **every** mission — a global template bug" (P0, all five reports) | `nextStep()` is defined at `mission.html:313` and correctly bound. The button at `_step_watch.html:37` calls `watchComplete()` (`:47-50`), which awaits `submitStep()` then reveals a **second** button, `→ Next Step` (`:42`), which calls `nextStep()`. The flow is deliberately two-click: acknowledge, then advance. The tester clicked the first button, saw no navigation, and inferred a broken handler. Real (minor) defect: the first button's label promises navigation it does not perform, and the revealed button may sit below the fold. |
| "Code execution engine is completely non-functional — implement a backend sandbox or Pyodide" (P0) | `apps/forge_academy/code_runner.py` is a hardened subprocess sandbox delivered by `penta-aca-02`: AST import allowlist (`:112-173`), scrubbed environment (`:176-187`), POSIX rlimits (`:190-215`), 10s timeout (`:37`), `subprocess.run([sys.executable, "-I", "-X", "utf8", …])` (`:249-252`). Route `/api/academy/code/run` at `blueprint.py:393`. Wired from `_step_coding.html:12` → `mission.html:209-238`. 14 passing tests in `tests/test_penta_aca_sandbox.py`. |
| "Zero API endpoints exist — `/academy/api/{progress,achievements,missions}` all 404" (P0/P1) | Wrong prefix. The real routes are `/api/academy/*`: `progress` (`blueprint.py:383`), `step/submit` (`:403`), `code/run` (`:393`), `user/setup` (`:366`), `guild/create` (`:575`), `oracle/run` (`:703`), `leaderboard` (`:608`), `certificate/<key>/issue` (`:831`) and 17 more — 25 in total. Persistence: `fa_mission_progress` (`db.py:76`), `fa_step_progress` (`db.py:89`), 23 `fa_*` tables. |
| "Reflection questions reference text fields that don't exist" (P0) | `_step_reflect.html` is multiple-choice by design (radio labels `:14-21`, scored by `checkAnswer()` `:41-79`). Free-text `<textarea>` exists where the design calls for it — `_step_coding.html:8`, `_step_configure.html:43`. |
| "Skill Tree is an empty SVG with no nodes" (P2) | `skill_tree.html:32-89` renders real SVG from `skill_nodes` (`:50`, `:61`), sourced from `constants.SKILL_NODES` via `blueprint.py:254`, with per-user unlock state from `get_user_skills` (`:257`). |
| "Oracle Intel 'Run Oracle' produces zero predictions" (P1) | Seven real lenses (`oracle/runner.py:22-30`), persisted via `insert_prediction`/`insert_convergence`; routes at `blueprint.py:682/695/703`. Gated by `@require_org_intel` (`auth.py:26` — `admin/pm/isso`). An unprivileged tester gets a redirect, not an empty result. 19 tests in `tests/test_penta_aca_oracle.py`. |
| GameDay: "Response submission silently fails; nothing persists" (P0) | Wired at `player.html:228-256` → `blueprint.py:432-463` → `tools/ttx/engine.py:139-151` `INSERT INTO ttx_responses`. Preconditions the tester did not meet: a team must be joined (`player.html:229` early-returns), an inject must be **dispatched** (`player.html:149` fetches `?state=dispatched`), and `GET /responses` is `@require_facilitator` (`blueprint.py:503`) — so a player session receives 401, not `[]`. Round-trip covered by `tests/test_penta_gd_routes.py:160-260`. |
| GameDay: "Scenario picker is cosmetic — all 9 scenarios serve the same 6 `ai_gameday` injects" (P0) | Nine distinct packs exist under `scenarios/` with **differing** inject counts — ai_gameday 7, forge_ascent 6, grounding-red-team 6, hunt_the_fleet 6, meridian 6, document-integrity 5, red_team_the_ai 5, slo-meltdown 5, interagency inline. Picker is wired: `hub.html:70-71` → `:155,159,161` → `blueprint.py:318` `data.get("scenario_slug")` → `engine.py:42` `load_scenario()` → `:56 seed_injects()`. **No fallback-to-default path exists** — an unknown slug raises `FileNotFoundError` (`scenario_loader.py:31`) → 400. Retained as `fga-gd-03` for live reproduction only. |
| GameDay: "AI League rounds never execute" (P1) | `blueprint.py:874-902` instantiates `GameMaster` and starts a daemon thread running `gm.run_tournament()`. The runner is `tools/gameday/round_manager.py` (not the legacy, never-built `tools/ai_game_engine/`); `game_master.py:66-70` loops `round_count` calling `manager.run_round()`, `:72` marks completed, `:76` refreshes the leaderboard, `:86-100` persists `status='aborted'` + error on failure. Covered by `tests/test_penta_gd_league.py:123,171`. |
| GameDay: "Simulation mode never loads" (P1) | `simulate.html:202,381` fetches `/api/gameday/session/<id>/simulate-state`, which exists at `blueprint.py:777-806` and returns `{ok, session, injects, leaderboard, teams}`. Asserted in `tests/test_penta_gd_routes.py:367`. The placeholder persists only for a session with zero seeded injects. |
| GameDay: "Team detail links go to `/activity`" (P1) | `templates/gameday/ai_league.html:56` is `href="/gameday/ai-league/team/{{ t.team_key }}"`. Zero occurrences of `/activity` in either GameDay template directory. Route at `blueprint.py:845-857`. |
| GameDay: "No player state persistence — refresh returns to the Join form" (P1) | `player.html:130` writes `sessionStorage`; `:116-117` restores on load and hides the join panel. Minor real caveat: `sessionStorage` survives refresh but not tab close, and in-memory receipt chips/timers reset. |
| "Persona `?role=` filter is cosmetic" (P1) | **Partial, not false.** `missions_browser()` (`blueprint.py:191`), `leaderboard_page()` (`:287`) and `api_leaderboard()` (`:611`) all read and apply `request.args.get("role")`. Only `hub()` (`:163-181`) ignores it, hardcoding `fa_user.get("role")` (`:171`). Carded as `fga-fix-06` for parity. |

---

## 4. Stale — fixed on `main` before the audit was written

| Claim | Fixed by |
|---|---|
| `POST /api/gameday/session` → `400: column "ontology_tags_json" does not exist` | `dbd1d17d5` (`penta-gd-03`, 2026-07-18). Column in `apps/ai_gameday/db.py` DDL for `ttx_sessions` + `ttx_injects`, guarded ALTER via `_ADD_COLUMNS`/`column_exists()`, PG migration `tools/db/migrations/274_ttx_ontology_tags.sql`, regression test `tests/test_penta_gd_schema.py`. |
| "Leaderboard always 0 — `ai_scorer` never runs" | `23b2d83f5` (`penta-gd-04`) removed the silent `_fallback_score` of 50 and made LLM outage return an explicit `unscored` flag; `3ed20f02d` (`penta-fix-01`) fixed the `AttributeError` on legacy dict-of-dicts rubrics that 500'd `POST /api/gameday/response`. `ai_scorer.score_response` is invoked on every submission (`engine.py:19,166-180`), followed by `compute_leaderboard` (`:182`). Residual truth: with no LLM router configured, judge points are legitimately 0 — receipt and time points still accrue. |

---

## 5. Rejected on product grounds — not defects

- **"Author content for the 10 genuinely-empty missions."** Real gap, but authoring is a product
  decision, not a defect fix, and it is dwarfed by the 43 already-authored missions that merely
  need wiring. `fga-wire-06` marks these "Coming Soon" instead of shipping dead cards.
- **New missions** (Evals & Testing, AI Security & Red Teaming, Production Deployment, Structured
  Output, Cost Governance) and **new GameDay scenarios** (DEEP FAKE, SUPPLY CHAIN, BIAS AUDIT,
  MODEL THEFT, PROMPT BOMB). Reasonable backlog; PENTA already added 10 missions and 3 TTX
  scenarios against the same request.
- **"Write unique inject content for the 8 non-default scenarios."** Moot — already distinct (§3).
- **"Seed the leaderboard with 10–15 fake but plausible profiles."** Rejected. Fabricated data
  presented as real is the precise failure PENTA was chartered to eliminate on this surface
  ("Kill fabricated-data paths; fail loud, never serve demo defaults as real"). The same objection
  applies to the fake "▶ Demo Output" block, which is why finding #4 is a removal, not a rewrite.
- **"Break the 9 single-step missions into 2–3 steps each."** Framed as pacing work; in reality
  those missions have exactly one authored step file each. Any additional steps are new content.

---

## 6. Card

Registered as **`fga`** / `task_prefix: fga-` in `args/projects.yaml`, **MANUAL-ONLY**: all tasks
depend on `fga-gate-00`, held `in_progress`, so the kanban runner never dispatches them. Release
with `python tools/kanban/cli.py --set-status fga-gate-00 done` (`.env` loaded).

| Epic | Tasks | Scope |
|---|---|---|
| `gate` | 1 | Held sentinel |
| `wire` | 6 | Ingest the 43 orphaned mission directories; add the reverse-direction test; mark the 10 empty missions "Coming Soon" |
| `fix` | 7 | Findings 3–7, plus `?role=` parity and unenrolled-user silent-failure feedback |
| `gd` | 3 | Findings 8–9, plus the scenario-picker live reproduction |

Both apps ship `default_enabled: false` (`args/component_registry.yaml:1168` `ICDEV_FORGE_ACADEMY_ENABLED`;
`:1194` `ICDEV_GAMEDAY_ENABLED`) and are opt-in child apps, which is why this is carded at medium
priority behind the manual gate rather than dispatched.
