# Plan: Run ICDEV Engines on worldmonitor + TimesFM

## Value Statement

`worldmonitor` (AGPL-3.0) is a mature real-time situational-awareness dashboard with 60k+ stars; `google-research/timesfm` (Apache-2.0) is a 25k+ star self-hostable time-series foundation model. Running them through ICDEV's Innovation, Creative, and Research engines will produce deterministic scores and a build-vs-buy dossier so we can decide whether to **integrate, emulate, or skip** each repo — and document the license/compliance boundary up front.

## Assumptions

1. We will use the existing ICDEV **PostgreSQL** backend (`.env` already points to it); no SQLite override.
2. `worldmonitor`'s AGPL license is expected to **BLOCK** auto-solution generation per Innovation Engine triage rules (D202); we will still seed it to capture lessons/patterns.
3. `timesfm`'s Apache-2.0 license is permissive and can pass triage if it maps to a FORGE layer.
4. Web scanner may be rate-limited; we will rely on manually seeded repo metadata plus engine-native scoring rather than bulk web scraping.
5. All work is local/scratch; no git push or marketplace publish is in scope.

## Success Criteria

- Both repos are registered as `innovation_signals` and scored.
- At least 3 customer pain points derived from the repos are seeded into `creative_pain_points`, scored, and gaps identified.
- A Research Engine dossier exists for the pair with build-vs-buy recommendations.
- Final summary reports: (a) what to adapt, (b) what to avoid and why, (c) next concrete steps.

## Phase 1 — Bootstrap

Verify PostgreSQL is already bootstrapped. Do **not** bootstrap unless `--check` reports an empty/young database.

```powershell
$env:PYTHONPATH="C:\AI\ICDev"
python tools/db/bootstrap_pg.py --check --json
python tools/db/storage.py --health --json
python tools/testing/health_check.py --json
```

## Phase 2 — Seed Innovation Engine Signals

Create `.tmp/seed_two_repos.py` (scratch, no commit) that inserts two rows into `innovation_signals` with:
- `source_type='external_repo_scouting'`
- scoring hints matching the 6 dimensions in `signal_ranker.py`
- license field noted (`AGPL-3.0` for worldmonitor, `Apache-2.0` for timesfm)
- **Use psycopg2 `%s` placeholders** (PG-primary), not SQLite `?`

Then run scoring:

```powershell
python .tmp/seed_two_repos.py
python tools/innovation/signal_ranker.py --score-all --json
python tools/innovation/triage_engine.py --triage-all --json
python tools/innovation/solution_generator.py --generate-all --json
```

## Phase 3 — Seed Creative Engine Pain Points

Create `.tmp/seed_creative_two_repos.py` that inserts pain points (using `%s` PG placeholders) such as:
- "ICDEV lacks a unified real-time global situational-awareness dashboard" (worldmonitor)
- "ICDEV has no self-hostable time-series foundation model for telemetry forecasting" (timesfm)
- "ICDEV dashboards lack offline/air-gap-first multi-variant packaging" (worldmonitor/Tauri pattern)

Then run:

```powershell
python .tmp/seed_creative_two_repos.py
python tools/creative/gap_scorer.py --score-all --json
python tools/creative/gap_scorer.py --gaps --json
python tools/creative/spec_generator.py --generate-all --json
```

## Phase 4 — Research Engine Dossier

Use existing `cybersecurity` or `defense` vertical; if missing, load verticals first.

```powershell
python tools/research/vertical_loader.py --load --json
python tools/research/session_manager.py --create --name "worldmonitor + TimesFM scan" --vertical defense --json
# capture session_id
python tools/research/research_engine.py --run-stage LANDSCAPE --session-id <id> --json
python tools/research/research_engine.py --run-stage BUILD_BUY --session-id <id> --json
python tools/research/research_engine.py --run-stage SYNTHESIZE --session-id <id> --json
python tools/research/dossier_generator.py --generate --session-id <id> --json
```

If web adapters fail due to rate limits, use air-gap graceful degradation — the dossier will still contain the manually seeded build-vs-buy data.

## Phase 5 — Synthesize & Report

Read engine outputs from:
- `innovation_signals` (scores, triage status, generated solutions)
- `creative_pain_points` / `creative_feature_gaps` / `creative_specs`
- `research_dossiers` / `research_build_buy`

Produce final summary markdown under `.tmp/external_repo_adaptation_report.md` with sections:
1. Repo summaries
2. Innovation scores + triage verdict
3. Creative gaps + generated specs
4. Research build-vs-buy matrix
5. Concrete recommendations
6. Blockers (license, compliance, effort)

## Phase 6 — Optional Follow-through

If any signal/gap crosses the auto-queue threshold and is not blocked, preview with `python tools/innovation/kanban_promoter.py --dry-run --json` and then promote with `--promote --json` (cards land as `suggested`, gap-gated and rate-limited), or create a memory entry for the finding.

## Risks & Notes

- **AGPL blocker:** worldmonitor will likely be triaged as BLOCKED due to copyleft. The value is in pattern extraction, not code reuse.
- **DB import path:** All Python commands must be run with `$env:PYTHONPATH="C:\AI\ICDev"` (or Bash `export PYTHONPATH=C:/AI/ICDev`) because `tools` is a namespace package at the repo root.
- **PostgreSQL placeholders:** Scratch seed scripts must use `%s` for parameterized SQL; the existing `seed_external_repos.py` / `seed_competitor_repos.py` use `?` and will fail against PG.
- **No commits:** This is an eval/scout task; scratch scripts live in `.tmp/` and are not committed.
