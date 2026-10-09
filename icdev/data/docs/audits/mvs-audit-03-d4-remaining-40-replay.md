# CUI // SP-CTI

# mvs-audit-03-d4 — the remaining 40 shadowed migrations, replayed

[mvs-audit-03-d1](mvs-audit-03-d1-shadowed-migrations.md) enumerated the 60
migrations that never run. mvs-audit-03-d2 built
`tools/db/shadowed_migration_replay.py`.
[mvs-audit-03-d3](mvs-audit-03-d3-first-20-replay.md) ran it against the first
20 and found **0 gaps on PostgreSQL**. This audit covers rows 21–60 — the
remaining 40 — with the same tool and the same four oracles.

| File | Contents |
|------|----------|
| [mvs-audit-03-d4-remaining-40-replay.json](mvs-audit-03-d4-remaining-40-replay.json) | per-migration verdict, declared objects, all four oracles, evidence |

## Answer

**The remaining 40 do not hold.** Eleven are gaps on PostgreSQL, and seven of
those eleven have live consumers in the tree.

| Classification | Count |
|----------------|-------|
| `benign` — the chain already declares it, or it declares nothing | 7 |
| `gap_sqlite_chain_only` — absent from the SQLite chain, present in both PostgreSQL oracles | 22 |
| **`gap_on_postgresql`** — absent from what a fresh PostgreSQL database is built from | **11** |

and, within those eleven, by what it actually costs:

| PostgreSQL impact | Count | Entries |
|-------------------|-------|---------|
| **live consumers** — code in `tools/` reads or writes the missing object | **7** | `139_govlift_rbac_roles`, `247_dashboard_users_role_check`, `210_sso`, `236_rfi_workbench.sql`, `207_tenant_component_overrides`, `184_creative_gap_innovation_signal.sql`, `257_idr_dic_doc_link.sql` |
| index only — table/column present, index absent | 3 | `057_sg_raw_signals_processed`, `136_qdc_metrics`, `189_genesis_phase_log` |
| inert — schema absent, nothing reads it | 1 | `139_mfa_enforcement` |

Running totals for mvs-audit-03 across all 60 entries: 10 benign, 39
SQLite-chain-only, **11 gaps on PostgreSQL**.

## Why the answer differs from d3

The d1 list is ordered by version, and d3 took the first 20 — versions 10–56.
That range is almost entirely the Strategos/`sg_*` cluster, whose tables the
consolidated snapshot already contains. The tail is not: it reaches version
282, and it contains the entries that were *written to repair* an earlier
PostgreSQL defect and were then shadowed themselves.

## The oracle that decides, and why

`tools/db/bootstrap_pg.py` is how a fresh PostgreSQL database is built. It
loads `tools/db/schema/pg_consolidated.sql`, then reads
`pg_consolidated.meta.json` — `through_version: 301` — and marks **every
migration at or below 301 applied without running it**, executing only what is
above. All 40 entries here are ≤ 282.

So for a new PostgreSQL deployment there are exactly three ways an object can
exist: the snapshot contains it, a migration above 301 creates it, or runtime
code creates it at app startup. There is no fourth. In particular
`tools/db/init_icdev_db.py` — which does declare several of the objects below —
refuses to run on PostgreSQL:

```python
# tools/db/init_icdev_db.py:11383-11385
if backend == "postgresql":
    ... "ICDEV_STORAGE_BACKEND=postgresql detected. This monolithic ..."
```

An object absent from the snapshot with no runtime creator is therefore absent
from a fresh PostgreSQL database permanently. That is what `gap_on_postgresql`
means here.

Note the corollary: the **live** PostgreSQL database has most of these objects.
It has had migrations hand-run against it over time. Its having a table proves
only that *that* database has it — which is exactly why oracle 2 alone is weak
and oracle 3 decides.

## The four oracles

1. **SQLite replay** — `tools/db/shadowed_migration_replay.py`, with the d3
   baseline-pinning fix. Baseline: 256 migrations applied, 57 unapplicable on
   SQLite, 2539 objects — identical to d3's, which cross-checks both runs.
2. **Live PostgreSQL** — read-only session over `information_schema.tables` /
   `.columns`, `pg_indexes` and `pg_constraint` on the local `icdev` database
   (1764 public tables).
3. **`tools/db/schema/pg_consolidated.sql`** — what a fresh PostgreSQL database
   is built from. The deciding oracle, for the reason above.
4. **Declaring-source scan** — `CREATE TABLE` / `CREATE INDEX` across `tools/`
   and `apps/` outside the migration trees, to find canvases and reflexes that
   create their tables at app startup.

## The seven gaps with live consumers

### 1–2. `dashboard_users.role` rejects four RBAC roles — `139_govlift_rbac_roles` + `247_dashboard_users_role_check`

Two migrations were written to add `migration_engineer`, `component_admin`,
`auditor` and `ciso` to the CHECK constraint. Both are shadowed. 247 exists
*because* 139 was PG-incompatible; 247 is then lost the same way, by version
collision with `247_cpmp_int_coverage_tenant_id.sql`.

```sql
SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
 WHERE conrelid = 'dashboard_users'::regclass AND contype = 'c';
-- dashboard_users_role_check CHECK ((role = ANY (ARRAY[
--   'admin','pm','developer','isso','co','cor',
--   'bd','capture_mgr','contract_mgr','reviewer'])))
```

Ten values. The four GovLift roles are not among them, in the live database and
identically at `pg_consolidated.sql:10397`. Meanwhile:

| Source | Roles |
|--------|-------|
| `tools/db/init_icdev_db.py:3344` (SQLite install) | all 14 |
| `tools/dashboard/auth.py:357` `VALID_DASHBOARD_ROLES` | all 14 |
| `tools/govlift/rbac.py:28` `GOVLIFT_ROLES` | the 4 GovLift roles |
| `tools/dashboard/api/sre.py:37` `_SRE_MUTATION_ROLES` | includes `component_admin` |
| live PostgreSQL + snapshot | **10** |

`create_user()` (`tools/dashboard/auth.py:119`) inserts `role` straight into the
table with no pre-validation, so on PostgreSQL:

```sql
SELECT 'ciso' = ANY (ARRAY['admin','pm','developer','isso','co','cor',
                           'bd','capture_mgr','contract_mgr','reviewer']);
-- f
```

— a CHECK violation. A SQLite install built by `init_icdev_db.py` accepts all
14. This is a backend-divergent RBAC failure, and it is 247's own docstring's
description of the bug it was written to fix.

This entry is also the tool's documented blind spot in the flesh: 247 is PG-only
by design ("SQLite doesn't support ALTER TABLE … DROP/ADD CONSTRAINT"), so the
SQLite replay executed it, changed nothing, and scored it
`schema_already_exists`. A no-op and an already-satisfied migration are
indistinguishable on that oracle. Only `pg_constraint` separates them.

### 3. SSO is absent from a fresh PostgreSQL database — `210_sso`

`sso_providers`, `sso_sessions` and three indexes. `grep -c sso_providers
pg_consolidated.sql` → **0**. The only other declaration anywhere in the tree is
a test fixture (`tests/conftest.py:2387`). Consumers:

- `tools/admin/blueprint.py:234–344` — the SSO provider admin API, four routes
  (list / insert / update / disable)
- `tools/auth/blueprint.py:116` — `INSERT INTO sso_sessions` on login
- `tools/auth/oidc.py:26` — reads provider config for the OIDC flow

### 4. The RFI Workbench canvas is absent — `236_rfi_workbench.sql`

`rfi_workbench_sessions`, `rfi_workbench_sections`, `rfi_workbench_exports` and
two indexes; **0** occurrences in the snapshot; no runtime creator.

What makes this one self-sealing: migrations 237, 238, 239 and 255 all
`ALTER TABLE rfi_workbench_*`, and every one of them is below `through_version`,
so bootstrap marks them applied without running them. Nothing ever notices the
tables are missing and nothing repairs them. Consumers:
`tools/govcon/rfi_canvas_blueprint.py`, `rfi_engine_runner.py`,
`rfi_style_engine.py`, `rfi_workbench.py`, `capture_strategy.py`.

### 5. Per-tenant component overrides are absent — `207_tenant_component_overrides`

`tenant_component_overrides` and two indexes; **0** occurrences in the snapshot.
This is the table CLAUDE.md names as the tenant-level enablement override for
the enterprise-configurable platform, read and written in five places in
`tools/config/component_registry.py` (516, 534, 575, 622, 653). Its only other
declaration is `init_icdev_db.py:11077` — the SQLite-only path.

The SQLite replay scored it `schema_already_exists`, correctly: a chain-built
SQLite database does have it. The divergence is PostgreSQL-only.

### 6. `creative_gap` / `innovation_signal` are absent — `184_creative_gap_innovation_signal.sql`

Two tables and eight indexes, **0** occurrences in the snapshot. The only other
declaration is `init_icdev_db.py` (5117, 3577). Beware the near-miss:
`innovation_signal**s**` (plural) is a *different* table and is present — a
name-comparison audit that does not distinguish them scores this benign.

### 7. The docgen → Tech Writer bridge is half-wired — `257_idr_dic_doc_link.sql`

`dic_documents.source_wg_result_id` and `.source_idr_session_id`: **0**
occurrences in the snapshot. `tools/document_intelligence/blueprint.py` writes
both (lines 921 and 964). The three `idr_sessions` columns the same migration
adds *are* present, so the failure is partial — the write to `dic_documents`
fails while the session side succeeds.

## The other four PostgreSQL gaps

`139_mfa_enforcement` adds `mfa_enabled`, `totp_secret`, `mfa_backup_codes` and
`mfa_verified_at` to `dashboard_users`. All four are absent from live
PostgreSQL *and* the snapshot — and nothing in the tree reads any of them.
SaaS MFA uses a separate `user_mfa` table (`tools/saas/auth/mfa.py`). Dead
schema; no failure.

Three are index-only, with the table or column present:

| Entry | Missing | Where |
|-------|---------|-------|
| `057_sg_raw_signals_processed` | `idx_sg_raw_signals_processed` | live + snapshot (`sg_raw_signals.processed` is present in both) |
| `136_qdc_metrics` | `idx_qdc_metrics_key` | live + snapshot (`qdc_metrics` is present) |
| `189_genesis_phase_log` | `idx_gpl_started_at` | snapshot (present live) |

`057_sg_raw_signals_processed` is worth the extra line: the replay scored it
`schema_already_exists`, which is wrong. Its `ALTER TABLE sg_raw_signals ADD
COLUMN processed` is wrapped in the migration's own `except Exception: pass`,
and `sg_raw_signals` is absent from the chain-built baseline — its creator
`052_sg_raw_signals` is itself shadowed (d3 entry 16). The statement raised
"no such table" and was swallowed, so the delta is empty for a reason that has
nothing to do with the column already existing. Read the source, not the delta,
whenever `bypasses_passed_connection` or a bare `except` is in play.

The mirror case, recorded but *not* a deployable gap: `188_genesis_phase_log.sql`
declares `idx_gpl_tenant`, which **is** in the snapshot but is missing from the
live database. A fresh PostgreSQL deployment gets it; the local database has
drifted. Drift in one deployment, not a gap.

## Three entries the replay could not judge, resolved by reading the source

Nine entries came back `inconclusive`. Six were PostgreSQL-only syntax
(`ADD COLUMN IF NOT EXISTS`) or a missing parent table, and the PostgreSQL
oracles settled them. Three needed the source:

**`269_kg_empty_graph_counts.sql` declares no schema at all.** It is a single
`UPDATE kg_graphs SET entity_count = 0, edge_count = 0 WHERE name LIKE
'rag-chunk-%' …`. There is nothing to compare and nothing missing. What is lost
is the *data correction* — on the live corpus the migration's own comment
records 39 emptied graphs claiming 59 phantom entities, and the dashboard reads
`SUM(entity_count)` straight from those columns. Classified `benign` for schema
with a `no_ddl` flag; whether the count fix still matters is a data question, not
a schema one.

**`173_white_team_review_type.py` and `179_kanban_task_revivals.py` are not
shadowed — they are never discovered.** Both are bare `NNN_name.py` files rather
than directories, and `MigrationRunner.discover_migrations` handles flat `.sql`
files but skips non-directories otherwise (`migration_runner.py:271`,
`if not entry.is_dir(): continue`). d1 lists them as shadowed because
`migration_versions.py::shadowed_migrations()` scans the filesystem, not the
runner's own discovery. The condition is strictly worse than shadowing — the
version collision is not even the reason they do not run — but the schema
outcome is benign for both: 179's `kanban_task_revivals` is created at runtime by
`tools/genesis/reflexes/kanban.py:7922`, and 173's `white_team` value was
superseded by `20260803201015_proposal_reviews_white_team_review_type`, which is
*above* `through_version` and therefore actually runs on a fresh PostgreSQL
database. (That is why live PostgreSQL accepts `white_team` while the snapshot,
dumped 2026-07-26, does not.)

**Two more entries cannot run even unshadowed.** `210_sso` and
`215_user_preferences` define `def up()` with no parameter, while
`MigrationRunner.apply_migration` calls `mod.up(conn)` (`migration_runner.py:452`).
Both would raise `TypeError` if they were ever reached. Flagged
`signature_incompatible_with_runner`; for 210 it compounds a gap that is real
anyway.

## Per-migration results

`chain` = present in the SQLite migration chain. `snapshot` =
`pg_consolidated.sql`. `live PG` = the local PostgreSQL database. `—` = not
replayable on SQLite.

| # | Shadowed migration | Shadowed by | Declares | chain | snapshot | live PG | Class |
|---|--------------------|-------------|----------|-------|----------|---------|-------|
| 21 | `057_sg_raw_signals_processed` | `057_ad_backtest_runs.sql` | 1 col, 1 idx | ✓* | ✗ idx | ✗ idx | **PG gap** (index) |
| 22 | `057_sg_sc_graph` | `057_ad_backtest_runs.sql` | 2 tables, 4 idx | ✗ | ✓ | ✓ | sqlite-only |
| 23 | `057_sg_signals_actions` | `057_ad_backtest_runs.sql` | 3 cols, 2 idx | — | ✓ | ✓ | sqlite-only |
| 24 | `064_sg_pattern_learner_log` | `064_sg_hitl_items_type` | 1 table, 2 idx | ✗ | ✓ | ✓ | sqlite-only |
| 25 | `078_workflow_hitl` | `078_ad_decision_audit` | 1 col | ✗ | ✓ | ✓ | sqlite-only |
| 26 | `083_sg_multidomain_tracks` | `083_cyber_ext_columns` | 4 tables, 7 idx | ✗ | ✓ | ✓ | sqlite-only |
| 27 | `084_wne_sessions` | `084_aisg_wizard.sql` | 2 tables, 2 idx | ✗ | ✓ | ✓ | sqlite-only |
| 28 | `085_aisg_learning_tracks` | `085_aadc_versions.sql` | 2 tables, 1 idx | ✗ | ✓ | ✓ | sqlite-only |
| 29 | `085_sg_ccir_trigger_events` | `085_aadc_versions.sql` | 1 table, 2 idx | ✗ | ✓ | ✓ | sqlite-only |
| 30 | `086_sg_intsums` | `086_aadc_events.sql` | 2 tables, 2 idx | ✗ | ✓ | ✓ | sqlite-only |
| 31 | `107_sg_theaters` | `107_aadc_phase5.sql` | 1 table, 2 idx | ✗ | ✓ | ✓ | sqlite-only |
| 32 | `108_sg_war_council_briefs` | `108_aadc_phase6.sql` | 1 table, 2 idx | ✗ | ✓ | ✓ | sqlite-only |
| 33 | `113_kanban_vibe_tier1` | `113_aadc_compliance.sql` | 1 table, 5 cols, 2 idx | ✗ | ✓ | ✓ | sqlite-only |
| 34 | `120_ops_hub` | `120_kanban_alert_queue` | nothing in the main DB | ✓ | n/a | n/a | **benign** |
| 35 | `135_sdc_designs` | `135_ohc_runbooks` | 1 table, 1 idx | ✗ | ✓ | ✓ | sqlite-only |
| 36 | `136_qdc_metrics` | `136_gameday_ai_league` | 1 table, 2 idx | ✗ | ✗ idx | ✗ idx | **PG gap** (index) |
| 37 | `139_govlift_map_assessment.sql` | `139_fisma_ir` | 1 table, 2 idx | ✗ | ✓ | ✓ | sqlite-only |
| 38 | `139_govlift_rbac_roles` | `139_fisma_ir` | 4 CHECK values, 2 idx | ✗ | ✗ | ✗ | **PG GAP — consumers** |
| 39 | `139_mfa_enforcement` | `139_fisma_ir` | 4 cols | ✗ | ✗ | ✗ | **PG gap** (inert) |
| 40 | `139_qdc_metrics.sql` | `139_fisma_ir` | 1 table, 2 idx | ✗ | ✓ | ✓ | sqlite-only |
| 41 | `158_sg_leadership_briefs` | `158_conflict_predictions` | 1 table, 3 idx | ✗ | ✓ | ✓ | sqlite-only |
| 42 | `161_sdc_rag_stigs` | `161_sdc_compliance_timeline` | 2 tables, 4 idx | ✗ | ✓ | ✓ | sqlite-only |
| 43 | `163_groups_canvas_access.sql` | `163_domain_coverage` | 4 cols | ✓ | ✓ | ✓ | **benign** |
| 44 | `173_white_team_review_type.py` | `173_cpmp_obligation_periods.py` | 1 CHECK value | — | ✗† | ✓ | **benign** (superseded) |
| 45 | `179_kanban_task_revivals.py` | `179_integrity_tables.py` | 1 table | — | ✓ | ✓ | **benign** |
| 46 | `184_creative_gap_innovation_signal.sql` | `184_coworkers_canvas_tables.sql` | 2 tables, 8 idx | ✓ | ✗ | ✓ | **PG GAP — consumers** |
| 47 | `184_memory_fts5` | `184_coworkers_canvas_tables.sql` | 6 tables (FTS5) | ✗ | n/a | n/a | sqlite-only |
| 48 | `188_genesis_phase_log.sql` | `188_genesis_outputs` | 1 table, 3 idx | ✗ | ✓ | ✗ idx | sqlite-only |
| 49 | `189_genesis_phase_log` | `189_dd_pii_scans.sql` | 1 table, 3 idx | ✗ | ✗ idx | ✓ | **PG gap** (index) |
| 50 | `207_tenant_component_overrides` | `207_mcip_dat_tables.sql` | 1 table, 2 idx | ✓ | ✗ | ✓ | **PG GAP — consumers** |
| 51 | `210_sso` | `210_showcase_apps.sql` | 2 tables, 3 idx | — | ✗ | ✓ | **PG GAP — consumers** |
| 52 | `212_idr_suggested_classification.sql` | `212_data_residency` | 6 cols | — | ✓ | ✓ | **benign** |
| 53 | `215_user_preferences` | `215_api_keys` | 1 table | — | ✓ | ✓ | **benign** |
| 54 | `223_user_identity.sql` | `223_agent_evals.sql` | 6 tables, 4 idx | ✗ | ✓ | ✓ | sqlite-only |
| 55 | `223_wfc_doc_regen.sql` | `223_agent_evals.sql` | 2 tables | ✗ | ✗ | ✓ | sqlite-only‡ |
| 56 | `236_rfi_workbench.sql` | `236_personal_rag.sql` | 3 tables, 2 idx | ✗ | ✗ | ✓ | **PG GAP — consumers** |
| 57 | `247_dashboard_users_role_check` | `247_cpmp_int_coverage_tenant_id.sql` | 8 CHECK values | ✓* | ✗ | ✗ | **PG GAP — consumers** |
| 58 | `257_idr_dic_doc_link.sql` | `257_doc_modernization.sql` | 5 cols | — | ✗ | ✓ | **PG GAP — consumers** |
| 59 | `269_kg_empty_graph_counts.sql` | `269_kg_embedding_vec_backfill` | no DDL | — | n/a | n/a | **benign** |
| 60 | `282_insider_risk_uba.sql` | `282_docmod_nist_pubs.sql` | 2 tables, 1 idx | ✗ | ✗ | ✓ | sqlite-only‡ |

\* `schema_already_exists` on the SQLite oracle is an artifact here, not
evidence — see `057_sg_raw_signals_processed` and `247` above.
† superseded by a post-`through_version` migration that does run.
‡ absent from the snapshot but created at app startup by runtime code
(`tools/workflow_canvas/blueprint.py`, `tools/security/insider_risk.py`), so no
deployment is missing them.

## What a chain-built SQLite database would lack

39 tables and 49 indexes declared by these 40 entries have no creator anywhere
outside their shadowed migration — not the chain, not `init_icdev_db.py`, not a
canvas or reflex. Full lists are in the JSON under
`summary.tables_a_chain_built_sqlite_db_would_lack_with_no_other_creator` and
`summary.indexes_nothing_in_the_tree_creates`. The largest clusters are
Strategos (`sg_*`, 12 tables), Second Brain (`user_*`, 6 tables), the RFI
Workbench (3), and the SQLite FTS5 memory index (`memory_fts` + 5 shadow
tables). This joins d3's residue for d5 to rule on, and, as in d3, its cost
depends on whether any supported SQLite deployment is built by replaying the
chain rather than by `init_icdev_db.py`.

## Reproducing

```bash
# 1. replay (baseline ~13s, then 40 entries)
python tools/db/shadowed_migration_replay.py --json \
  --baseline-db <path OUTSIDE the repo>/baseline.db \
  --migration 057_sg_raw_signals_processed --migration 057_sg_sc_graph  # … all 40
```

Use a baseline path outside the repository, and do not run this while
`ICDEV_DB_PATH` points at a database you care about — the tool pins the variable
for itself, but the pin is what protects that database.

Oracles 2–4 are read-only queries against the local PostgreSQL database, a regex
scan of `pg_consolidated.sql`, and a `CREATE TABLE`/`CREATE INDEX` grep over
`tools/` and `apps/`. The specific queries behind every verdict above are quoted
inline; the per-entry results are in the JSON.

## Limits

- `remaining_failures = 57` — migrations SQLite cannot apply. A gap verdict
  resting on an object one of *those* would have created is weak. No verdict
  here rests on the SQLite oracle alone: every `gap_on_postgresql` is decided by
  the snapshot plus the declaring-source scan, and the SQLite replay is used
  only to bound what the entry declares.
- Oracle 3 parses `pg_consolidated.sql` with a regex. Every one of the eleven
  `gap_on_postgresql` verdicts was re-checked with a plain `grep -c` for the
  object name against the file, so no verdict rests on the parse. The
  `creative_gap` / `innovation_signal` pair is the case where that mattered —
  `innovation_signal` matches inside `innovation_signals`, a different and
  present table.
- The claim "a fresh PostgreSQL database lacks this" is deductive, not measured:
  snapshot absent + version ≤ `through_version` (so bootstrap marks it applied
  without running) + no runtime creator + `init_icdev_db.py` refuses PostgreSQL.
  Each link is verified above. Building a fresh database and diffing it would
  measure it directly and is the obvious follow-up.
- Audited `tools/db/migrations`. `icdev/tools/db/migrations` is a separate tree;
  all 40 entries exist in both.
- `120_ops_hub` writes to `ohc_canvas.db`, a different database. Its verdict
  covers the main ICDEV database only.
