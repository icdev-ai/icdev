# CUI // SP-CTI

# mvs-audit-03-d3 — the first 20 shadowed migrations, replayed

[mvs-audit-03-d1](mvs-audit-03-d1-shadowed-migrations.md) enumerated the 60
migrations that never run. mvs-audit-03-d2 built
`tools/db/shadowed_migration_replay.py`, which answers "is this entry's schema
actually missing?" by **executing** it against a database built from every
migration the runner really applies. This audit runs that tool against the first
20 rows of the d1 list and classifies each one.

| File | Contents |
|------|----------|
| [mvs-audit-03-d3-first-20-replay.json](mvs-audit-03-d3-first-20-replay.json) | per-migration verdict, declared objects, all four oracles, evidence |

## Answer

**None of the first 20 is a gap on PostgreSQL.** Three are benign outright —
the migration chain already declares everything they declare. The other 17 are
gaps in the **SQLite** chain only: every object they declare is present both in
`tools/db/schema/pg_consolidated.sql` (what `bootstrap_pg.py` loads on a fresh
PostgreSQL database) and in the live PostgreSQL database, so no PostgreSQL
deployment is missing any of it.

| Classification | Count |
|----------------|-------|
| `benign` — the chain already declares it | 3 |
| `gap_sqlite_chain_only` — absent from the SQLite chain, present in both PG oracles | 17 |
| gap on PostgreSQL | **0** |

The distinction matters because PostgreSQL is the primary backend and it does
not replay the chain at all — `bootstrap_pg.py` loads the consolidated snapshot
and marks every version ≤ 301 applied. Shadowing cannot cost a PG deployment
schema that the snapshot already contains, and for these 20 entries it does not.

## The four oracles

1. **SQLite replay** — `tools/db/shadowed_migration_replay.py`. Baseline: 256
   migrations applied, 57 unapplicable on SQLite, 2539 objects. Each shadowed
   entry then runs against a throwaway copy and the normalised `sqlite_master`
   DDL is diffed.
2. **Live PostgreSQL** — read-only `information_schema.tables` /
   `information_schema.columns` / `pg_indexes` on the local `icdev` database
   (1764 public tables).
3. **`tools/db/schema/pg_consolidated.sql`** — the snapshot a *fresh* PG
   database is built from (1329 tables, 1918 indexes, `through_version: 301`).
   Every one of these 20 entries is version ≤ 57, well inside the snapshot.
4. **Declaring-source scan** — `CREATE TABLE` / `CREATE INDEX` across `tools/`
   outside `tools/db/migrations`, to find canvases and reflexes that create
   their tables at app startup and therefore cover a chain gap on SQLite too.

Oracle 3 is the one that decides. Oracle 2 alone would be weak: the live
database has had migrations hand-run against it, so its having a table proves
only that *this* database has it, not that a new deployment would.

## A defect in the d2 tool, found by this audit and fixed

`build_baseline()` did not pin `ICDEV_DB_PATH` while the chain ran.

Several migrations open `up(conn=None)` with `conn = get_connection()`,
discarding the connection `MigrationRunner` hands them and resolving the ambient
environment instead. `_apply_py` already pinned the variable for the *replay*
half — the d2 commit message describes exactly this hazard — but the *baseline*
half was unguarded, and the baseline is the oracle every verdict is measured
against. Unpinned, 61 tables were written to the repo's `data/icdev.db` instead
of the baseline. The baseline was therefore short of schema the chain really
does create, and a shadowed entry that declared the same table scored a gap it
is not.

`055_sg_conflict_events_cyber_op` is the case in these 20:

| | unpinned baseline | pinned baseline |
|---|---|---|
| baseline objects | 2371 | 2539 |
| migrations applied | 254 | 256 |
| 055's delta | new table `sg_conflict_events` + 5 indexes | 4 columns + 1 index |

`sg_conflict_events` is created by `052_sg_conflict_events` and
`118_strategos_core_tables`, both of which run. Both call `get_connection()`
inside `up()`, so both wrote elsewhere and the baseline looked as though nothing
created the table. Verdict *totals* happened not to move (17/3 either way), but
one verdict rested on wrong evidence, and on the remaining 40 entries (d4) the
same defect could flip a total.

Fixed in `build_baseline` by running the chain inside a new `_pinned_db_path`
context manager, mirrored to `icdev/`. Regression test:
`tests/db/test_shadowed_migration_replay.py::test_baseline_captures_a_migration_that_opens_its_own_connection`
— it builds a baseline containing a connection-discarding migration and asserts
both that the table landed in the baseline and that the ambient database was
never created. **d4 must use the fixed tool.**

## Per-migration results

`chain` = present in the SQLite migration chain. `snapshot` =
`pg_consolidated.sql`. `live PG` = the local PostgreSQL database.

| # | Shadowed migration | Shadowed by | Declares | chain | snapshot | live PG | Class |
|---|--------------------|-------------|----------|-------|----------|---------|-------|
| 1 | `010_network_intelligence_schema` | `010_kanban_executor_schema` | 4 tables | ✗ | ✓ | ✓ | sqlite-only |
| 2 | `018_reflex_observations.py` | `018_memory_db_consolidation` | 1 table, 2 idx | ✓ | ✓ | ✓ | **benign** |
| 3 | `019_kanban_verifications` | `019_backlog_task_reassign.py` | 1 table, 3 idx, 1 CHECK | ✓ | ✓ | ✓ | **benign** |
| 4 | `020_nc_topologies_schema.py` | `020_kanban_failure_count` | 1 table, 1 idx | ✗ | ✓ | ✓ | sqlite-only |
| 5 | `020_options_coach_events` | `020_kanban_failure_count` | 1 table, 3 idx | ✗ | ✓ | ✓ | sqlite-only |
| 6 | `021_dispatch_source` | `021_ad_macro_regimes` | 2 columns, 2 idx | ✗ | ✓ | ✓ | sqlite-only |
| 7 | `021_sg_sigint_events.py` | `021_ad_macro_regimes` | 1 table, 2 idx | ✗ | ✓ | ✓ | sqlite-only |
| 8 | `022_sg_eo_signals.py` | `022_ad_event_stack_tables` | 4 idx | ✗ | ✓ | ✓ | sqlite-only |
| 9 | `023_sg_socmint_signals.py` | `023_ad_news_patterns` | 1 table, 4 idx | ✗ | ✓ | ✓ | sqlite-only |
| 10 | `024_telegram_inbox.py` | `024_ad_news_catalysts` | 1 table, 2 idx | ✗ | ✓ | ✓ | sqlite-only |
| 11 | `027_compliance_snapshots` | `027_ad_coach_alerts` | 1 table, 3 idx | ✗ | ✓ | ✓ | sqlite-only |
| 12 | `028_idc_infra_tables` | `028_attack_graph` | 2 tables, 3 idx | ✗ | ✓ | ✓ | sqlite-only |
| 13 | `031_network_twin_snapshots` | `031_ddc_twin_tables` | 1 table, 1 idx | ✗ | ✓ | ✓ | sqlite-only |
| 14 | `043_memory_fingerprint` | `043_memory_entity_relationships` | 1 column, 1 idx | ✗ | ✓ | ✓ | sqlite-only |
| 15 | `050_theater_supply_chain` | `050_sg_sio_assessments` | 1 CHECK widening | ✗ | ✓ | ✓ | sqlite-only |
| 16 | `052_sg_raw_signals` | `052_sg_conflict_events` | 2 tables, 2 idx | ✗ | ✓ | ✓ | sqlite-only |
| 17 | `055_sg_conflict_events_cyber_op` | `055_cta_scores_cache.sql` | 4 columns, 1 idx | ✗ | ✓ | ✓ | sqlite-only |
| 18 | `055_sg_information_signals` | `055_cta_scores_cache.sql` | 2 tables, 5 idx | ✗ | ✓ | ✓ | sqlite-only |
| 19 | `056_sg_prioritized_signals` | `056_historical_cases` | 1 table, 3 idx | ✗ | ✓ | ✓ | sqlite-only |
| 20 | `056_win_loss_analysis_tables.sql` | `056_historical_cases` | 2 tables, 1 idx | ✓ | ✓ | ✓ | **benign** |

### SQL-level evidence for the three benign verdicts

All three use `CREATE TABLE IF NOT EXISTS`, so "0 already-exists errors" is not
by itself evidence — an entry that no-ops looks identical. The verdict rests on
the empty delta, confirmed directly against the baseline:

```sql
-- against the chain-built baseline
SELECT type, name FROM sqlite_master WHERE name IN (
  'reflex_observations','idx_reflex_obs_name','idx_reflex_obs_started',   -- 018
  'kanban_verifications',                                                  -- 019
  'win_loss_analysis_runs','win_loss_feature_impacts',
  'idx_wl_feature_impacts_run');                                           -- 056
-- all 7 PRESENT
```

`019_kanban_verifications` needs a second check the SQLite oracle cannot make:
its second half widens the `kanban_tasks.status` CHECK constraint and is guarded
by `if _is_pg(conn)`, so the replay skips it entirely. Checked directly instead:

```sql
SELECT pg_get_constraintdef(oid) FROM pg_constraint
 WHERE conname = 'kanban_tasks_status_check';
-- CHECK ((status = ANY (ARRAY['backlog','scheduled','in_progress','done',
--   'token_exhausted','suggested','decomposed','validating',
--   'needs_decomposition','pr_opened','ci_failed','merge_conflict',
--   'changes_requested','failed'])))
```

`decomposed` and `validating` — the two values 019 exists to add — are present,
and the same constraint text is in `pg_consolidated.sql:18375`. Benign.

### SQL-level evidence for the two constraint-widening entries

Neither adds a table or a column, so a name-comparison audit scores both benign
without looking. The replay rebuilds the table and diffs the DDL text.

**`050_theater_supply_chain`** widens `supply_chain_vendors.vendor_type`:

```
baseline:  CHECK(vendor_type IN ('cots','gots','oss','saas','paas','iaas',
                                 'contractor','subcontractor'))
replayed:  CHECK(vendor_type IN ('cots','gots','oss','saas','paas','iaas',
                                 'contractor','subcontractor','defense_contractor'))
```

Against PostgreSQL, `defense_contractor` is already accepted — twice over:

```sql
SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
 WHERE conrelid = 'supply_chain_vendors'::regclass AND contype = 'c';
-- supply_chain_vendors_vendor_type_check ... 'defense_contractor'
-- sc_vendors_vendor_type_049_check       ... 'defense_contractor',
--                                            'raw_material_supplier',
--                                            'transshipment_hub',
--                                            'state_owned_enterprise'
```

and the first of those is in `pg_consolidated.sql:30465`. Benign on PostgreSQL;
a chain-built SQLite database would reject a `defense_contractor` vendor.

**`055_sg_conflict_events_cyber_op`** adds `technique_ids`, `threat_actor`,
`malware_family`, `confidence` to `sg_conflict_events` plus
`idx_sg_ce_technique_ids`. All four columns are present in live PG and in the
snapshot.

### What a chain-built SQLite database would actually lack

The 17 sqlite-only gaps are not equally consequential. Four of them declare
tables that something else creates at app startup, so a running SQLite
deployment has them anyway:

| Migration | Tables | Created instead by |
|-----------|--------|--------------------|
| `010_network_intelligence_schema` | `ni_analyses`, `ni_devices`, `ni_state_snapshots`, `topologies` | `tools/network/db/init_db.py` |
| `028_idc_infra_tables` | `idc_infra_resources`, `idc_infra_snapshots` | `tools/infra_canvas/db/init_db.py` |
| `031_network_twin_snapshots` | `network_twin_snapshots` | `tools/network/twin.py` |
| `052_sg_raw_signals` | `sg_raw_signals`, `sg_raw_signals_audit` | `tools/genesis/reflexes/strategos/osint_harvester.py` |
| `056_sg_prioritized_signals` | `sg_prioritized_signals` | `tools/genesis/reflexes/strategos/signal_scout.py` |

Eight tables have **no creator anywhere outside their shadowed migration** —
not the chain, not `init_icdev_db.py` (527 tables, none of these), not a canvas
or reflex:

`ad_options_coach_events`, `compliance_snapshots`, `nc_topologies`,
`sg_information_scores`, `sg_information_signals`, `sg_sigint_events`,
`sg_socmint_signals`, `telegram_inbox`

and 37 indexes are likewise created nowhere else. One of those is not a
performance index: `idx_ad_coach_position_trap_dedup` is the dedup index for
`ad_options_coach_events`, so on SQLite that table would be missing entirely
rather than merely unindexed.

This is the residue for d5 to rule on: it is bounded to SQLite, and its cost
depends on whether any supported SQLite deployment is built by replaying the
chain rather than by `init_icdev_db.py`.

## Reproducing

```bash
python tools/db/shadowed_migration_replay.py --sample 20 \
  --baseline-db /path/outside/the/repo/baseline.db --json
```

Use a baseline path outside the repository. Do not pass `--sample 20` while
`ICDEV_DB_PATH` points at a database you care about: the tool pins the variable
for itself now, but the pin is what protects that database.

## Limits

- `remaining_failures = 57` — migrations SQLite cannot apply, mostly
  PostgreSQL-only DDL. A gap verdict resting on an object one of *those* would
  have created is weak. None of these 20 does: every declared table was traced
  back to the migrations that declare it, and in each case only the shadowed
  entry (plus, for `sg_conflict_events` and `sg_eo_signals`, an applied one that
  already supplies the table).
- Audited `tools/db/migrations`. `icdev/tools/db/migrations` is a separate tree
  with 78 shadowed entries; all 20 audited entries exist in both.
- Oracle 3 parses `pg_consolidated.sql` with a regex. Spot-checked against the
  file (`ni_analyses:20765`, `telegram_inbox:30772`,
  `idx_sg_info_scores_created:58411`) and cross-agrees with oracle 2 on all 20.
