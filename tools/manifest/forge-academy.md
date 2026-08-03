# Tool Manifest — FORGE Academy

**Module:** `apps/forge_academy/`
**Blueprint route prefix:** `/academy` (pages), `/api/academy` (API)
**Templates:** `tools/dashboard/templates/forge_academy/` — mirrored to
`icdev/tools/dashboard/templates/forge_academy/` (18 pages + 9 partials, parity enforced)
**Content:** `apps/forge_academy/content/tier{1,2,3}/<mission-slug>/steps/` — markdown lessons
plus `stepN_starter.py` / `stepN_test.py` assets
**Tables:** 25 `fa_*` tables in `data/icdev.db` — see
[docs/reference/databases.md](../../docs/reference/databases.md)

---

## Read this before touching grading

Everything that decides whether a learner passed lives in **`apps/forge_academy/grading.py`**.
Nothing else may decide it, and no route may take a verdict from the request body. Prior to
aca-int-01 the browser sent `passed` (defaulting to `True` when omitted), supplied the test it
was graded against, and declared its own mission completion. If you are adding a step type, a
route, or an XP award, add it here — not in `blueprint.py`.

The single-sentence rule: **the graded party never supplies the grader, the verdict, or the
amount.**

---

## Modules

### `apps/forge_academy/grading.py` — server-authoritative verdicts (aca-int-01/02/03/05)
The only source of a step's pass/fail. There is deliberately **no `test_code` parameter** for a
caller to pass.
- `grade_step(step_id, submission="", *, chosen_option=None)` → verdict dict
  (`passed`, `assessed`, `reason`, `xp_base`, `stdout`, `stderr`, `exit_code`)
- `run_step_code(step_id, code)` — run-without-submitting; loads the step's own `test_code_path`
- `mission_is_complete(user_id, mission_id)` — derived from recorded step progress, never from
  the client's "this was the last step"
- `step_xp_base(step)` / `mission_xp_reward(mission_id)` — amounts come from
  `fa_mission_steps.xp_partial` / `fa_missions.xp_reward`
- `client_safe_steps(steps)` — strips `test_code`, `test_code_path` and the reflect answer key
  (`correct`/`is_correct`/`answer`/`explanation`) from the page payload
- `ASSESSED_STEP_TYPES = {"coding", "reflect"}` — everything else reports `assessed=False`:
  completable (reading is real work) but never described as an assessment
- A `coding` step with **no stored test is ungraded, not passed** (`reason="ungraded_no_test"`)

### `apps/forge_academy/xapi.py` — xAPI 1.0.3 export for an external LMS (aca-trn-05)
Renders `fa_*` records as xAPI statements so Academy results can count as training of record.
Hard-depends on the INT epic: without server-authoritative grading and the provenance chain
there is nothing here worth exporting.
- `build_statements(*, user_id=None, since=None, include_unverified=False, tenant_id=None)`
  → `{statements, excluded, counts, generated_at, activity_base}`
- One statement per **verified** step (`passed`), mission (`completed`) and certificate
  (`earned`); each is matched to its provenance row — `fa_xp_ledger` for step/mission,
  `fa_certificate_evidence` for a certificate — **before** it is emitted
- A record with no provenance row, or one flagged `verified=0` by the 315 backfill, is
  withheld and counted in `excluded`; `include_unverified=True` emits it stamped
  `verified: false` in the statement's own provenance extension. There is no mode in which
  an unverifiable completion is presented as a verified one.
- Statement IDs are UUIDv5 over activity+actor+verb+timestamp, so re-POSTing an export to the
  same LRS is idempotent rather than duplicating the learner's history
- Actor is `mbox` when the learner has an email, otherwise an `account` scoped to
  `ICDEV_XAPI_ACTIVITY_BASE` — a local identity is never dressed up as an email. A learner with
  neither is excluded, not anonymised.
- **SCORM is deliberately not implemented.** SCORM's unit of record is one rolled-up
  completion per launch, which discards the per-step granularity that makes this export worth
  having. Wrap these statements when a named target LMS demands it.
- CLI: `python -m apps.forge_academy.xapi --statements-only --out feed.json`
- Env: `ICDEV_XAPI_ACTIVITY_BASE` (default `https://icdev.ai/xapi/forge-academy`) — two
  deployments feeding one LRS must not both claim the same activity IRIs

### `apps/forge_academy/db.py` — data access, PG-native (aca-hyg-05)
Runtime SQL is authored for PostgreSQL (`%s` placeholders). Key entry points:
- XP provenance — `record_xp(...)` (keyword-only `reason`, **no default**), `earned_xp(user_id)`
  = `SUM(xp_delta) WHERE is_attendance = 0`, `update_user_xp`
- Progress — `record_step_attempt` (aliased `complete_step`; a failure is
  `STEP_STATUS_ATTEMPTED`, never `completed`), `record_mission_attempt`, `complete_mission`,
  `mission_step_progress`, `resume_target`
- Tier gating — `tier_progress(user_id, tier)`, `is_tier_unlocked(user_id, tier)` — computed
  over **completable** missions, never the raw count
- Certificates — `check_cert_eligibility`, `collect_cert_evidence`, `get_cert_evidence`,
  `issue_certificate`, `verify_certificate_token`
- Ontology / competency — `upsert_mission_ontology`, `record_user_competency`,
  `get_user_competencies`
- `role_matches(user_role, mission_roles)` — whole-token match, **not** `LIKE '%role%'`
  (aca-hyg-02)

### `apps/forge_academy/gamification.py` — XP arithmetic and awards
- `projected_step_xp(base_xp, hints_used)` — what the hint panel quotes; the same multiplier the
  submit applies, so the quoted price is the charged price (aca-int-06 / aca-ux-02)
- `award_step_xp` / `award_mission_xp` — take the step or mission id and pass it to `record_xp`
- `award_daily_login` — writes `is_attendance=1` ledger rows; attendance never buys a rank
- `get_gameday_seed_bonus` — repaired dead code (aca-hyg-01)

### `apps/forge_academy/code_runner.py` — learner code sandbox (penta-aca-02)
`run_code(code, test_code=...)` — AST import allowlist, scrubbed env, `python -I -X utf8`,
isolated `TemporaryDirectory` cwd, 10s timeout, POSIX rlimits. The gate inspects the
**combined** learner + test script, so a stored test must satisfy the allowlist too. Sandbox
decision recorded as Gap 31 in [docs/security/sandbox-coverage.md](../../docs/security/sandbox-coverage.md).

### `apps/forge_academy/content_loader.py` — catalogue discovery and seeding
- `discover_steps` / `discover_missions` / `seed_mission_catalog` — frontmatter-keyed by
  `ontology_id`; attaches `stepN_starter.py` / `stepN_test.py` siblings. **A sibling test
  promotes a step to `coding`; a starter alone does not** (aca-hon-05)
- `reconcile_mission_types` / `mission_type_from_steps` — the badge is derived from actual step
  composition (aca-hon-04)
- `retire_superseded_missions`, `reconcile_all_step_assets`
- `load_step_content` / `load_starter_code` / `load_test_code`
- `extract_learning_objective` / `objective_for_mission` — reads the objective an author
  already wrote: explicit `learning_objective:` frontmatter first, else the lead paragraph of
  an objective-bearing section in the mission's **first** step. Returns `None` rather than
  guessing — a question prompt, a section opening on a list or code fence, and a fragment
  under 40 chars all yield nothing, because an absent objective is a visible content gap and
  an invented one is a false record on an audited field (aca-trn-03). Backfilled into
  `fa_missions.learning_objective` by migration `20260803005919`; 53 of 124 missions state one

### `apps/forge_academy/configurator.py` — guided configure steps
`dispatch_configure(data)` — 7 handlers. Handlers that cannot reach live ICDEV data return an
explicit demo-mode note rather than invented figures presented as output (aca-hon-01).

### `apps/forge_academy/verifier.py` — configure-step DB verification
`verify_step(user_id, step_type, verification_data)` → `{passed, evidence}`. Confirms a
configure step's effect actually landed in the database.

### `apps/forge_academy/constants.py`
`TIER_UNLOCK_PCT = {2: 80, 3: 25}` (Tier 1 absent — the entry point never gates),
`STEP_STATUSES`, `MISSION_STATUSES`, XP multipliers, 20 achievements, competency levels.
SQL `CHECK` constraints are derived from these constants, never hardcoded.

### `apps/forge_academy/ai_coach.py` — hints
CoT explanations + CoD debate mode. Hints are counted and charged **server-side**
(`db.record_hint`); the navigation reset that laundered the penalty is gone (aca-int-06).

### `apps/forge_academy/oracle/` — 7 predictive lenses
`runner.py` + `lens_{skill_gap,learner_risk,content_quality,staleness_detector,agent_readiness,aadc_readiness,ace_skill_gap}.py`,
persisted to `fa_oracle_predictions` / `fa_oracle_convergence_events`. Driven by
`tools/genesis/reflexes/academy_oracle_reflex.py`.

### Other
`auth.py` (learner identity), `blueprint.py` (routes), `integrations.py`, `ontology.py` +
`ontology.yaml`, `patterns.py`, `workflow_builder.py`, `seed_aadc_missions.py`,
`seed_aimc_missions.py`.

---

## Page routes (17)

| Route | Template |
|-------|----------|
| `GET /academy` | `page.html` |
| `GET /academy/missions` | `missions.html` |
| `GET /academy/mission/<slug>` | `mission.html` |
| `GET /academy/skill-tree` | `skill_tree.html` |
| `GET /academy/guild` | `guild.html` |
| `GET /academy/leaderboard` | `leaderboard.html` |
| `GET /academy/achievements` | `achievements.html` |
| `GET /academy/profile` | `profile.html` |
| `GET /academy/arena` | `arena.html` |
| `GET /academy/workflow-builder` | `workflow_builder.html` |
| `GET /academy/oracle` | `oracle.html` |
| `GET /academy/patterns` | `pattern_library.html` |
| `GET /academy/patterns/<pattern_id>` | `pattern_detail.html` |
| `GET /academy/org-readiness` | `org_readiness.html` |
| `GET /academy/certificate/<cert_key>` | `certificate.html` |
| `GET /academy/verify/<token>` | `cert_verify.html` |
| `GET /academy/my-certificates` | `my_certificates.html` |

`/forge-academy` and `/forge-academy/<path>` redirect to the `/academy` equivalents (legacy prefix).

## API routes (22)

| Route | Method | Notes |
|-------|--------|-------|
| `/api/academy/user/setup` | POST | |
| `/api/academy/progress` | GET | |
| `/api/academy/code/run` | POST | takes `step_id` + `code`; **never** a `test_code` body |
| `/api/academy/step/submit` | POST | verdict from `grading.grade_step`, tier gate enforced here |
| `/api/academy/step/design-assess` | POST | server-side `verify_step` |
| `/api/academy/step/configure` | POST | `configurator.dispatch_configure` |
| `/api/academy/coach/hint` | POST | records the hint server-side and quotes the real price |
| `/api/academy/guild/create` | POST | |
| `/api/academy/guild/join` | POST | |
| `/api/academy/guild/<guild_id>` | GET | 404s on a nonexistent guild (aca-hyg-03) |
| `/api/academy/leaderboard` | GET | |
| `/api/academy/challenge/enter` | POST | |
| `/api/academy/workflow/submit` | POST | |
| `/api/academy/oracle/predictions` | GET | |
| `/api/academy/oracle/summary` | GET | |
| `/api/academy/oracle/run` | POST | |
| `/api/academy/oracle/prediction/<pred_id>/outcome` | POST | |
| `/api/academy/org-readiness` | GET | |
| `/api/academy/health` | GET | |
| `/api/academy/certificate/<cert_key>/issue` | POST | snapshots evidence into `fa_certificate_evidence` |
| `/api/academy/learning-path` | GET | projected view; does not leak internal columns (aca-hyg-03) |
| `/api/academy/export/xapi` | GET | xAPI 1.0.3 statements; `@require_org_intel`; withholds unverified records (aca-trn-05) |

---

## Migrations

| Migration | Purpose |
|-----------|---------|
| `313_fa_mission_progress_reconcile.sql` | undo the page-view attempts recorded by the GET write |
| `314_fa_retire_duplicate_roi_mission.sql` | retire the superseded duplicate mission |
| `315_fa_xp_ledger.sql` | append-only `fa_xp_ledger` + honest backfill |
| `316_fa_rank_from_earned_xp/` | recompute stored rank from earned XP |
| `317_fa_certificate_evidence.sql` | append-only `fa_certificate_evidence` |

`fa_xp_ledger` and `fa_certificate_evidence` are registered in `APPEND_ONLY_TABLES`
(`.claude/hooks/pre_tool_use.py`) and in `MINIMAL_ICDEV_SCHEMA` (`tests/conftest.py`).

---

## Tests

`tests/test_aca_*.py` (26 files) plus `tests/_academy_conn.py` (shared connection helper).
The reverse-direction suite is **`tests/test_aca_vv_integrity_refusal.py`** — it posts what an
attacker would post over the real HTTP routes and asserts on the response *and* the database.
It also aggregates over every graded coding step in the catalogue rather than a sample, which is
how a vacuous grader was found. `_GRADERS_BLOCKED_BY_SANDBOX` is a pinned set that fails the
build both when a new blocked grader appears **and** when one is fixed without updating the list.

E2E: `.claude/commands/e2e/forge_academy.md` (picked up automatically by `e2e_runner`'s mcp glob).

Feature docs: [docs/features/forge-academy-assessment-integrity.md](../../docs/features/forge-academy-assessment-integrity.md),
[forge-academy-aca-ux-07-rank-xp-split.md](../../docs/features/forge-academy-aca-ux-07-rank-xp-split.md),
[forge-academy-phase5-credential-multimodal.md](../../docs/features/forge-academy-phase5-credential-multimodal.md),
[forge-academy-phase6-auto-currency.md](../../docs/features/forge-academy-phase6-auto-currency.md),
[forge-academy-aca-trn-05-xapi-export.md](../../docs/features/forge-academy-aca-trn-05-xapi-export.md).
