# CUI // SP-CTI

# INSERT / schema parity — swp-scan-01

**Task:** `swp-scan-01` · **Date:** 2026-08-02 · **Scanner:** `tools/lint/insert_column_linter.py`
**Migration:** `tools/db/migrations/329_insert_column_schema_parity/` · **Test:** `tests/test_insert_column_schema_parity.py`

---

## What was scanned, and why it matters

Every *static* `INSERT INTO <table> (<columns>)` under `tools/` was parsed and its column
list checked against the **live** PostgreSQL schema (`information_schema.columns`).

The live schema, not the DDL in the source, is the only thing that can answer this
question. `CREATE TABLE IF NOT EXISTS` never alters an existing table, so a column added
to a canvas `db/init_db.py` long after that table was first created is present in the
source and absent from every database that already had the table. The resulting
`UndefinedColumn` is then almost always swallowed by a bare `except Exception: pass`
around the write — which is why these had been failing silently for as long as the
columns had been missing, with no error surfacing anywhere.

Extraction is AST-based rather than line-based, so implicitly-concatenated string
literals are recovered intact. Any statement whose *column list* contains an f-string
interpolation is skipped as non-static. `migrations/`, `tests/` and `__pycache__/` are
excluded.

**Confirmation.** Classification was not left to the scanner. Every flagged statement was
fed to PostgreSQL `PREPARE`, which parses and plans but never executes. All raised
`UndefinedColumn`. There were **no parse-level false positives** — classification turns
entirely on *which database a site actually writes to*.

---

## Counts

The task cited 95 statements. The AST-based scanner, which recovers concatenated literals
the original pass missed, found **126**. The larger number is the one triaged here; the 95
are a subset of it.

| Category | Count | Meaning |
|---|---:|---|
| **(i) real defect** | **85 tables** | The statement runs against the icdev PG database and PG rejects it. |
| **(ii) table created on demand by a canvas `init_db`** | **0** | No finding fell into this class. Every table that exists on demand showed up as `table_absent`, which is out of scope (see below). |
| **(iii) scanner false positive** | **12 sites** | The site writes to a different database — or the "SQL" is not SQL at all. |

Category (i) breaks down by remedy as: **R1** rename to an existing live column (47),
**R2** drop a redundant column (1), **R3** the column genuinely does not exist, so a
migration adds it (50), **R4** table-name collision — two subsystems own one name in one
database (11), **R5** the wrong table is targeted entirely (1). A table can carry more
than one remedy, so these sum above 85.

Fixes landed in two places: **44 tables / 108 columns** via migration 329, and **41
tables** in the calling module. The split is deliberate — see *Where each fix landed*.

### Out of scope

A further **200 statements** name a table that is absent from the live schema entirely
(`table_absent`). The task explicitly deferred these ("130 further tables … were not
evaluated"), and they are a different problem: a missing *table* usually means an
`init_db.py` that has never been run on this database, not a code/schema disagreement.
They remain visible in the scanner output and are **not** gated.

---

## Where each fix landed, and why

The dividing line is **which side is wrong**, not which side is easier to change.

* **The database was behind → migration 329.** The column is declared in the owning
  `init_db.py` or an earlier migration, carries data with no live equivalent under
  another name, and simply never landed because `CREATE TABLE IF NOT EXISTS` does not
  alter a table that already exists.
* **The code was wrong → the calling module.** The column already exists under a
  different name (`rack_location` vs `rack`), or the statement targets the wrong table
  entirely (`tasks` vs `kanban_tasks`). Renaming a *live column* to match a caller would
  break every reader of that column; renaming the caller costs nothing.

Migration 329 is idempotent on both backends: every add is guarded by a live column read
rather than `ADD COLUMN IF NOT EXISTS`, which SQLite does not support. Re-running is a
no-op — verified against the live instance (`0 column(s) added, 108 already present`).

### Two findings that needed more than a column

**`simulation_results` — adding columns was not enough.** One table name, two owners: the
Network Canvas (`topology_id` / `sim_type` / `result_json`) and the Digital Program Twin
(`scenario_id` / `dimension` / `metric_name`). Both `CREATE`s are `IF NOT EXISTS`, so the
loser silently inherited the winner's table and every one of its INSERTs raised
`UndefinedColumn`. Observed live during this task, the shape **flipped between two scans
minutes apart** — which subsystem is broken was not even stable.

The Network Canvas prefixes its other 134 tables `nc_`; this one was the anomaly, so it
moves to `nc_simulation_results` (existing rows carried across) and the twin keeps the
unprefixed name that `init_icdev_db.py` declares. Critically, the migration must **also
drop `sim_type`'s NOT NULL**: with the twin's columns added but that constraint intact,
the INSERT stops raising `UndefinedColumn` and starts raising `NotNullViolation` — still
rejected, just further along. This was caught by the round-trip test, not by inspection.

**`fairness_assessments` — a shape mismatch, not a missing column.** The live table held
one row per project (`overall_score` / `assessment_data`) while the assessor writes one
row per dimension. The per-dimension columns are added rather than the assessor rewritten,
because `assessment_data` / `overall_score` have no per-dimension meaning and the
dashboard reads both. `assessment_data`'s NOT NULL is relaxed and a unique index on
`(project_id, dimension)` added, since the assessor's `ON CONFLICT` had no matching index.

### TRUST invariants restored

Three of the missing columns were provenance fields, so the citation gate had nothing to
read back at rest: `sg_intsums.grounding_status` / `grounding_json`,
`sg_intsum_paragraphs.grounded` / `require_citations` / `citations`, and
`dic_documents.origin` / `status` (the AI-provenance and review-gate fields — without them
no DIC document could record whether a human or a model wrote it).

---

## Category (iii) — scanner false positives

Each was verified by opening the other database and confirming the column exists there.

| Site | Table | Why it is not a defect |
|---|---|---|
| `tools/saas/tenant_manager.py` | `tenants`, `users` | `get_platform_connection()` resolves to `data/platform.db` (`PLATFORM_DB_URL` unset). Verified: both tables have `compartments` there. |
| `tools/sg/supply_chain_bridge.py` | `sg_entities`, `sg_kg_nodes` | `_geo_upsert_*()` is handed the `apps/geosigint` SQLite connection, and `_ensure_geo_schema()` says so. Verified: that DB carries the `entity_name`/`theater`/`lat`/`lon` shape. |
| `tools/ais/ais_importer.py` | `sg_tracks` | `get_connection` is imported from `apps.geosigint.models`, **not** `tools.db.storage`. Verified: `geosigint.db` has the AIS shape. |
| `tools/rag/sqlite_vector_store.py` | `rag_chunks` | `_get_conn()` is deliberately pinned to its own `.db` file and documents at length why it must never resolve to PostgreSQL. `sign_bits` is a SQLite-only quantisation column. |
| `tools/appforge/reflexes/build.py` | `kg_edges` | The INSERT lives inside an f-string that is **generated child-app source**. It is never executed by this module; it targets the generated app's own database. |
| `tools/ndc/seed_dewie_demo.py` | `ni_devices` | Opens `sqlite3.connect("data/network_canvas.db")` at module level — a dedicated file, never the icdev instance. **No longer reported:** migration 329 added `downstream_count` / `properties_json` / `annual_maintenance_cost` to the *icdev* copy of `ni_devices`, which already carried `rack_location` and `criticality_score`, so that table became a superset of what the seed writes and the scanner fell silent. The classification stands; it is simply not in the regression allowlist, because an allowlist entry asserts that a finding still fires. |
| `tools/playground/seed_data.py` | `projects`, `poam_items` | `seed_playground_db()` takes an explicit `db_path` and `CREATE`s both tables — with `compliance_score`, `finding`, `milestone`, `due_date` — in the same `executescript`. |
| `tools/finetune/doc_extractor.py` | `rag_ingestion_log` | `_get_db()` takes a caller-supplied `db_path` and returns a `sqlite3.Connection` (`PRAGMA journal_mode=WAL`, `INSERT OR IGNORE`). |
| `tools/kanban/seed_dsyn_kanban.py` | `canvas_events` | **Not SQL at all.** The text sits inside a kanban task *description* telling a future session what to write. The AST walk cannot distinguish prose-in-a-string from a query. |

### One category (i) resolved as a documented skip

`tools/trading/db.py` → `ad_macro_indicators` is a genuine R4 collision: the module
`CREATE`s a run-scoped shape, while the live table is the global FathomDesk reference
table (`as_of_date` / `flags_json`). `save_macro_indicators()` now documents the mismatch
and skips explicitly rather than failing silently. Nothing is lost — the per-run values
are already persisted in `ad_macro_context.context_json` by `save_macro_context()`.

---

## Regression guard

`tests/test_insert_column_schema_parity.py` holds the line in two ways:

1. **`test_no_new_insert_column_drift`** re-runs the scanner against the live schema and
   asserts the surviving `column_absent` findings are *exactly* eleven of the twelve
   category-(iii) sites plus the one documented skip — twelve entries in all. The twelfth
   category-(iii) site, `seed_dewie_demo.py::ni_devices`, is deliberately absent from the
   allowlist because migration 329 made the icdev table a superset of what it writes, so
   it no longer reports. Adding a column to an INSERT without adding it to the schema fails
   here — and so does reverting any fix. The allowlist is asserted in
   both directions, so an entry that stops firing also fails rather than quietly rotting.
   That is not hypothetical: this exact assertion is what caught the `ni_devices` entry
   going stale once the migration was applied to the live instance.
   PostgreSQL-only: under the SQLite test backend the fixture DB is minimal, so every
   table would report absent and the comparison would be meaningless.
2. **The migration round-trip** builds each affected table in its *pre*-329 shape on a
   throwaway SQLite database, asserts the INSERT genuinely fails first, applies 329,
   writes a row naming the added columns, and reads it back. Idempotency and the
   `nc_simulation_results` carry-across are asserted alongside.

All 8 pass against live PostgreSQL; 6 pass / 2 skip under the default SQLite backend.

To run the PG-only pair you need `ICDEV_PYTEST_PG=1` as well as a PostgreSQL backend —
`conftest.py` forces `ICDEV_STORAGE_BACKEND=sqlite` otherwise, and the two tests then skip
rather than fail, which is easy to mistake for a pass. `test_no_new_insert_column_drift`
carries an explicit `@pytest.mark.timeout(300)`: the scan AST-parses every file under
`tools/` and takes ~25s warm but exceeds the project-wide 30s budget on a cold filesystem
cache, so without the marker it is intermittently red rather than merely slow.

**What actually runs in CI, and what does not.** The six round-trip / idempotency tests run
in the normal suite on every PR — they are what guard the fixes. The two PG-only tests,
including `test_no_new_insert_column_drift`, run **only** against a fully-migrated
PostgreSQL (locally, or under `ICDEV_PYTEST_PG=1`). This file is deliberately **not** in
`tests/pg_tier_allowlist.txt`: that tier runs against a *fresh* CI PostgreSQL built by
`init_db`, where many of the flagged tables do not exist yet. Their findings would come
back as `table_absent` rather than `column_absent`, so the allowlist entries would stop
firing and the both-directions assertion would fail. The allowlist file documents this
same caution for its own held-back entries. Adding it is tracked below as a follow-up
rather than done blind — a guard that reddens CI for a reason unrelated to the drift it
watches is worse than one that is honestly scoped.

---

## Follow-ups (not addressed here)

* **200 `table_absent` statements** — deferred by the task. A missing table is a
  never-run `init_db.py`, not a code/schema disagreement.
* **~106 now-stale entries in `args/insert_schema_gate.yaml`.** With these fixes in place
  the coherence gate reports that many grandfathered mismatches no longer fire
  (`insert_schema_parity`: *"No NEW INSERT/schema mismatches — 7 grandfathered mismatch(es)
  remain; 106 stale allowlist entry(ies) can be removed"*). They are **not** pruned here.
  That staleness was measured against a *locally migrated* PostgreSQL; on a fresh database
  that has not had migration 329 applied, most of them would still fire. Pruning on that
  basis would encode a fact about one machine into a repo-wide gate. The gate passes either
  way — it fails only on *new* findings, and treats stale entries as a warning — so the
  correct time to prune is a follow-up run against a freshly migrated instance.
* **Twelve other recent migrations are missing from the `icdev/` mirror.** Migration 329
  is mirrored to `icdev/tools/db/migrations/` because the two runners do not read the same
  directory: `MIGRATIONS_DIR` is `BASE_DIR / "tools" / "db" / "migrations"` in both copies,
  but `BASE_DIR` is `Path(__file__).resolve().parent.parent.parent` — the repo root for
  `tools/`, and `icdev/` for `icdev/tools/`. So `icdev.tools.db.migration_runner` — the
  *canonical* namespace per CLAUDE.md — only ever sees the mirror, and an unmirrored
  migration is silently invisible to it rather than failing. 374 of 377 migrations are
  mirrored, but 310, 315–326 and 329 were not; 327 and 328 were. Only 329 is fixed here.
  The other twelve are a pre-existing drift that predates this task and are left for a
  sweep that can verify each one applies cleanly, rather than copied in blind.
* `tools/ndc/seed_dewie_demo.py` hardcodes the relative path `data/network_canvas.db`,
  which resolves against the current working directory.
* `tools/finetune/doc_extractor.py::_get_db()` calls `get_connection(db_path=str(db_path))`
  with `db_path` possibly `None`, yielding a file literally named `"None"`. It also uses
  SQLite-only `INSERT OR IGNORE`.

---

## Appendix — every category (i) finding

Remedy classes: **R1** rename to an existing live column · **R2** drop a redundant
column · **R3** the column does not exist, a migration adds it · **R4** table-name
collision · **R5** the wrong table is targeted.

| Table | Verdict | Remedy | Fixed in | Note |
|---|---|---|---|---|
| `ace_artifacts` | (i) | R1 | calling module | content -> content_md (live splits content_md / content_json). |
| `ace_sessions` | (i) | R2 | calling module | The INSERT writes BOTH conversation_history and history_json; history_json is redundant. |
| `ad_macro_indicators` | (i) | R4 | calling module | tools/trading/db.py CREATEs a run-scoped shape; the live table is the as_of_date/flags_json FathomDesk shape. One name, two schemas, one database. |
| `ad_quality_scores` | (i) | R3 | migration 329 | model_version, updated_at have no live equivalent. |
| `ad_ticker_performance` | (i) | R3 | migration 329 | is_anomaly has no live equivalent. |
| `audit_trail` | (i) | R1/R3 | calling module | recorded_at -> created_at; user_id -> actor; resource/resource_type/resource_id and tenant_id have no live equivalent. |
| `canvas_events` | (i) | R1/R3 | calling module | payload -> payload_json; tenant_id absent. |
| `child_capabilities` | (i) | R3 | migration 329 | metadata, updated_at absent. |
| `cloud_provider_status` | (i) | R1 | calling module | error_message -> details. |
| `compliance_snapshots` | (i) | R1 | calling module | implementation_status -> status. |
| `control_crosswalk` | (i) | R3 | migration 329 | created_at absent. |
| `creative_competitors` | (i) | R1/R3 | calling module | description/website/created_at/updated_at vs live metadata/source_url/discovered_at. |
| `creative_signals` | (i) | R1/R3 | calling module | raw_content -> body; created_at -> discovered_at; source_signal_id absent. |
| `creative_specs` | (i) | R3 | calling module | executive_summary, pain_score absent; live carries spec_content/composite_score. |
| `dashboard_users` | (i) | R1 | calling module | name -> display_name. The audit user was never created, so every authenticated page check in production_audit.py silently degraded to an unauthenticated one. |
| `data_classifications` | (i) | R3 | migration 329 | subcategory, confidence absent. |
| `dd_anomaly_runs` | (i) | R1 | calling module | run_id -> id; run_at -> created_at. |
| `dic_doc_freshness` | (i) | R3 | migration 329 | score absent. |
| `dic_documents` | (i) | R3 | migration 329 | origin, status absent — the DIC AI-provenance and review-gate fields are never persisted. |
| `dm_product_subscriptions` | (i) | R1 | calling module | subscriber_team -> subscriber; approved -> status. |
| `fairness_assessments` | (i) | R4 | migration 329 | The live table is one row per project (overall_score/assessment_data); the code writes one row per dimension. |
| `foundry_signals` | (i) | R3 | migration 329 | content_hash absent — the dedup key itself. |
| `framework_applicability` | (i) | R1 | calling module | detection_confidence -> confidence; detection_reason -> detection_rule. |
| `ft_dataset_examples` | (i) | R1 | calling module | status -> approved (live column is boolean). |
| `gd_ai_training_pairs` | (i) | R4 | migration 329 | Live is game_key/round_id/member_role; the code writes tournament_id/artifact_type. |
| `govcon_awards` | (i) | R1/R3 | calling module | period_of_performance -> _start/_end; created_at -> discovered_at; awardee_cage, contract_type, description absent. |
| `govlift_runbook_executions` | (i) | R1 | calling module | created_at -> started_at. |
| `govlift_runbook_templates` | (i) | R3 | migration 329 | estimated_min absent. |
| `govlift_runbooks` | (i) | R1 | calling module | step_count -> steps_count. |
| `heartbeat_checks` | (i) | R1 | calling module | details -> result_summary. |
| `hook_events` | (i) | R3 | migration 329 | severity, message absent. |
| `icdev_capability_map` | (i) | R1/R3 | migration 329 | created_at -> mapped_at; grade, metadata absent. |
| `innovation_signals` | (i) | R1/R3 | migration 329 | source_url -> url; raw_score, keywords absent. |
| `kanban_tasks` | (i) | R1 | calling module | source -> dispatch_source. |
| `kg_edges` | (i) | R1/R3 | calling module | relation -> relationship; source_type, target_type absent. |
| `kg_nodes` | (i) | R1 | calling module | node_type -> entity_type. |
| `marketplace_assets` | (i) | R1 | calling module | tenant_id -> publisher_tenant_id. |
| `mc_srv_inventory` | (i) | R4 | migration 329 | Live is a hardware-profile table (vcpus/ram_gb/os_family); the code writes an asset-inventory shape (hostname/ip_address/os). |
| `memory_entries` | (i) | R1/R3 | migration 329 | tags -> topics; metadata absent. |
| `nc_change_request_items` | (i) | R1 | calling module | change_request_id -> cr_id; object_id -> entity_id; item_type -> action_type; description -> justification. |
| `nc_change_requests` | (i) | R1/R3 | calling module | requested_by -> submitter_name; requested_at -> submitted_at; change_type, risk_level absent. |
| `nc_intent_policies` | (i) | R1/R3 | migration 329 | enabled -> is_active; rule_json, severity absent. |
| `nc_intent_validations` | (i) | R1 | calling module | result -> passed; detail -> violations_json; validated_at -> ran_at. |
| `nc_nqe_audit_log` | (i) | R1/R3 | migration 329 | nql_query -> nql_generated; user_confirmed, row_count absent. |
| `nc_patch_plans` | (i) | R1/R3 | migration 329 | maintenance_window_id -> window_id; action, blast_radius_json absent. |
| `nc_traffic_flows` | (i) | R1 | calling module | src_zone -> source_zone; dst_zone -> destination_zone; app_type -> application_type. |
| `ni_devices` | (i) | R1/R3 | migration 329 | rack_location -> rack; criticality_score -> criticality; downstream_count, properties_json absent. |
| `notification_log` | (i) | R1/R3 | migration 329 | type -> event_type; body, tenant_id absent. |
| `od_audit` | (i) | R1 | calling module | user -> actor. `user` is also a reserved word in PostgreSQL, so the statement never parsed there and od_audit has stayed empty. |
| `odc_twin_snapshots` | (i) | R3 | migration 329 | coverage_basis, payload_json absent. |
| `oracle_predictions` | (i) | R1/R3 | migration 329 | target -> subject_id; rationale -> prediction_text; status, suggested_action, expires_at absent. |
| `peering_peers` | (i) | R1 | calling module | peeringdb_sync -> peeringdb_synced_at (boolean -> timestamp; the written value must change too). |
| `pg_ai_clause_compliance` | (i) | R4 | migration 329 | Live is a per-clause checklist; the code writes an artifact row. |
| `pg_cmmc_supply_chain` | (i) | R1/R3 | migration 329 | cage_code -> team_member_cage; cert_expiry -> certification_expiry; created_at, updated_at absent. |
| `pg_theme_tracking` | (i) | R1 | calling module | status -> implementation_status; notes -> reviewer_notes; created_at -> checked_at. |
| `pg_win_loss_records` | (i) | R1/R3 | migration 329 | recorded_at -> created_at; bid_decision_id, debrief_notes absent. |
| `pg_win_themes` | (i) | R3 | migration 329 | keywords absent. |
| `pma_credential_alerts` | (i) | R1/R3 | migration 329 | days_remaining -> days_warning; severity absent. |
| `pma_personnel` | (i) | R3 | migration 329 | role, secret_expiry, ts_expiry absent (live carries lcat and poly_expiry only). |
| `poam_items` | (i) | R1/R3 | calling module | finding_ref -> weakness_id; title -> weakness_description; due_date -> milestone_date; workload_id absent. |
| `project_framework_status` | (i) | R1 | calling module | implemented_controls -> implemented_count. |
| `projects` | (i) | R3 | calling module | compliance_score absent. |
| `pulse_demand_signals` | (i) | R3 | migration 329 | sam_opportunity_ids, status absent. |
| `rag_chunks` | (i) | R3 | calling module | sign_bits absent on PG (SQLite-only quantisation column). |
| `rag_ingestion_log` | (i) | R1/R3 | migration 329 | chunk_count -> chunks_created; status, started_at, completed_at absent. |
| `redaction_audit` | (i) | R1/R3 | migration 329 | function -> module; entity_types_json -> entity_types; created_at -> timestamp; action absent. |
| `research_dossiers` | (i) | R1/R3 | calling module | created_at -> generated_at; vertical_name absent (live keys by vertical_id). |
| `sdc_attack_snapshots` | (i) | R4 | migration 329 | Live keys by component_id with nodes_json/edges_json; the code writes design_id/label/counts. |
| `sg_coa_options` | (i) | R1/R3 | migration 329 | title -> coa_name; description -> course_description; resource_allocation absent. |
| `sg_entities` | (i) | R4 | calling module | The icdev PG table is the strategos supply-chain shape (name/theater_id/location_wkt). |
| `sg_interdiction_results` | (i) | R1 | calling module | criticality_score -> criticality; substitutability_inverse -> substitutability; composite_score -> priority_score; computed_at -> created_at; affected_units_json -> metadata_json. |
| `sg_intsum_paragraphs` | (i) | R3 | migration 329 | grounded, require_citations, citations absent — a TRUST provenance invariant that cannot be enforced at rest. |
| `sg_intsums` | (i) | R3 | migration 329 | grounding_status, grounding_json absent — the same TRUST invariant. |
| `sg_kg_edges` | (i) | R3 | migration 329 | created_at absent. |
| `sg_kg_nodes` | (i) | R4 | migration 329 | icdev PG holds the 5-column strategos shape; the code writes the 10-column geosigint shape. |
| `sg_tracks` | (i) | R4 | calling module | icdev PG holds a PostGIS track shape (location_wkt/track_ts). |
| `simulation_results` | (i) | R4 | migration 329 | Network Canvas owns the live table (sim_type/topology_id/result_json); the Digital Program Twin writes scenario_id/metric_name/delta. One name, two owners. |
| `tasks` | (i) | R5 | calling module | These sites believe `tasks` is the Kanban board. The board is `kanban_tasks`; live `tasks` is an 8-column legacy table with no description column. |
| `tenants` | (i) | R3 | calling module | compartments absent on the icdev copy (see the platform.db override — only non-platform callers land here). |
| `ttx_api_log` | (i) | R3 | migration 329 | token_count, cost_usd absent — TTX cost telemetry is never persisted. |
| `usage_events` | (i) | R4 | migration 329 | Live is a web-request log (route/method/status_code); billing/metering writes tenant metering rows. |
| `users` | (i) | R3 | calling module | compartments absent on the icdev copy (see the platform.db override). |
| `wfc_chain_phases` | (i) | R1/R3 | migration 329 | phase_name -> name; phase_status -> status; workflow_snapshot_yaml absent. |
| `workflow_acceptance_criteria` | (i) | R3 | migration 329 | cot_config absent. |
| `zig_maturity_scores` | (i) | R3 | migration 329 | target_id absent — external-target scores collapse onto the same pillar row. |
