# Are the grandfathered shadowed migrations actually benign?

**CUI // SP-CTI** · task `mvs-audit-03` · 2026-08-03

## The question

`args/migration_duplicate_versions.yaml` grandfathers 48 duplicated migration
versions covering **60 migrations that can never run**. `MigrationRunner` keeps
the FIRST entry per version, so every other entry sharing that version is
skipped permanently and silently — no error, no warning, no row.

Freezing them made the collision gate actionable. It never established that they
were harmless, and those are different claims. PR #1199 went through part of the
set and found ten real schema gaps, so the remainder could not be assumed safe.

This audit answers the question by **building**, not by reading: does the schema
each shadowed migration declares actually exist on a database created today?

## Method

`tools/db/shadowed_migration_audit.py` classifies every shadowed entry against
four oracles, because no single one answers the question:

| Oracle | Built by | Weight |
|---|---|---|
| Fresh **PostgreSQL** | `bootstrap_pg.py` + `migrate.py --up --converge` into an empty DB | Strongest — PG is the primary backend |
| Fresh **SQLite** | `init_icdev_db.py` + `migrate.py --up --converge` | Conclusive when present; weak when absent (⅓ of the chain is PG-only DDL) |
| **Declaring source** in the tree | grep for `CREATE TABLE`, minus files that never run | Catches tables created lazily by a canvas `init_db.py` |
| **Long-lived database** | ambient connection | Weakest — never clears an entry alone; reported as `live_only` |

Two methodological traps had to be handled explicitly:

1. **A migration that never runs cannot vouch for another's schema.** Two
   shadowed migrations at different versions routinely declare the same table
   (`188_genesis_phase_log.sql` / `189_genesis_phase_log`); attributing each to
   the other clears both while the table is created by neither. Every entry the
   runner will not execute is disqualified as a source.
2. **The PG build must go through `bootstrap_pg.py`.** `init_icdev_db.py`
   refuses to run on PostgreSQL, and the migration chain is *not* the schema
   source there — `pg_consolidated.sql` is, with migrations ≤ `through_version`
   marked applied without running. A migrate-only PG build is the wrong oracle
   and produced three wrong verdicts on the first pass.

Reproduce with:

```bash
python tools/db/shadowed_migration_audit.py \
    --fresh-db      <sqlite built by init_icdev_db.py + migrate.py --up --converge> \
    --fresh-pg-dsn  <postgres built by bootstrap_pg.py + migrate.py --up --converge>
```

## Result

| Verdict | Count | Meaning |
|---|---|---|
| `benign` | 42 | Every declared object present on a fresh build by another route |
| `not_shadowed` | 9 | The gate is wrong — the runner never discovers the *winner*, so this entry is the one that runs |
| `no_schema` | 3 | Data-only; no DDL to lose |
| **real gap** | **6** | Schema no supported backend produces on a fresh install |

So "grandfathered" was **not** a synonym for harmless: 6 of 60 were live defects,
and 9 more were misclassified by the gate itself.

### The six real gaps

| Shadowed entry | Lost | Affected | Impact |
|---|---|---|---|
| `247_dashboard_users_role_check` **+** `139_govlift_rbac_roles` | `dashboard_users.role` CHECK | PostgreSQL | `create_user()` failed for `migration_engineer`, `component_admin`, `auditor`, `ciso` — four roles the RBAC matrix hands out. **Two separate migrations were written to fix this and both were shadowed.** |
| `236_rfi_workbench.sql` | `rfi_workbench_sessions` / `_sections` / `_exports` | PG + SQLite | 9 runtime consumers. Also cascades: migrations 237, 239, 241, 249, 255 fail on a fresh SQLite chain with *"no such table: rfi_workbench_sections"* |
| `210_sso` | `sso_providers`, `sso_sessions` | PG + SQLite | Enterprise SSO never worked on a new deployment; 6 auth modules consume these |
| `113_kanban_vibe_tier1` | `kanban_tasks` ×5 cols + `kanban_task_comments` | SQLite | PG had them via the snapshot |
| `055_sg_conflict_events_cyber_op` | `sg_conflict_events` ×4 cols | SQLite | — |
| `184_memory_fts5` | `memory_fts` (FTS5) | SQLite | `session_indexer.py` only *probes* for it and never creates it, so full-text search was silently degraded to unavailable rather than failing loudly |

`050_theater_supply_chain` was a seventh gap on SQLite (`vendor_type` rejected
`defense_contractor`).

The role CHECK is the one worth dwelling on: it is **invisible to a table/column
diff**, because the migration changes neither. Any audit that only compares
tables and columns — as PR #1199's did — cannot see it.

### One gap deliberately left unfixed

`139_mfa_enforcement` declares four MFA columns on `dashboard_users` that exist
on no backend. It is **not** resurrected: `tools/saas/auth/mfa.py` reimplemented
MFA on its own `user_mfa` / `mfa_attempts` tables, so nothing reads those
columns. Running it would add four permanently-unused columns. The migration
should be deleted, not applied; that is recorded in the allowlist.

## What was fixed

* **`20260803204235_mvs_audit_03_shadowed_gaps`** — folds the six gaps into one
  idempotent migration. Folded rather than renumbered because a renumbered entry
  gets a timestamp id, which sorts *after* every legacy version — so
  `236_rfi_workbench` would run after the five migrations that ALTER its tables.
* **`init_icdev_db.py`** — `vendor_type` gains `defense_contractor`. Widening a
  CHECK on SQLite means rebuilding the table (`create _new`, copy, `DROP`,
  rename); that is a destructive operation to fix a non-destructive problem.
* **`bootstrap_pg.py`** — see below.
* **`migration_versions.py`** — the allowlist now carries a reason per entry and
  the gate fails on an entry that has none.

### A separate, larger bug found on the way

`bootstrap_pg.baseline_versions` compared versions as **strings**:

```python
"20260803204235" <= "301"   # True — '2' < '3'
```

Every 14-digit timestamp migration therefore sorted into the pre-snapshot
baseline and was **marked applied without ever running** on a fresh PostgreSQL
bootstrap. That silently cost the 8 timestamp migrations then on `main` their
DDL on every new PG install and in CI — precisely the "value too HIGH" failure
the module's own docstring says it exists to prevent, arriving through the
comparison instead of through the marker. Now ordered by `(len(digits), digits)`,
matching `MigrationRunner.discover_migrations`.

This is also what made the fix verifiable: the fold-in migration was itself
swallowed by the same bug on its first run.

## Verification

Both backends rebuilt from empty, after the fix:

```
fresh PostgreSQL          fresh SQLite
  sso_providers            PRESENT      PRESENT
  sso_sessions             PRESENT      PRESENT
  rfi_workbench_sessions   PRESENT      PRESENT
  rfi_workbench_sections   PRESENT      PRESENT
  rfi_workbench_exports    PRESENT      PRESENT
  kanban_task_comments     PRESENT      PRESENT
  memory_fts               n/a          PRESENT (MATCH query returns a hit)
  kanban_tasks +5 cols     PRESENT      PRESENT
  sg_conflict_events +4    PRESENT      PRESENT
  defense_contractor       PRESENT      PRESENT
  role CHECK rejects       NONE         NONE
```

Functional proof for the role CHECK — inserting each previously-rejected role:

```
BEFORE fix   accepted=None                       REJECTED=[migration_engineer, component_admin, auditor, ciso]
AFTER  fix   accepted=[all four]                 REJECTED=None
```

Re-running the audit against the rebuilt databases leaves **1 gap**
(`139_mfa_enforcement`, obsolete by design) and no entry with unexplained
missing objects.

## Known-remaining: the `icdev/` mirror

`tools/db/migrations` and `icdev/tools/db/migrations` are different sets — 27
entries missing from the mirror, 20 stale in it, and **17 colliding versions
that differ between the trees**. Because the `tools.* → icdev.tools.*` shim sends
every import-based caller to the mirror, which tree a caller migrates depends on
how the runner was loaded. This audit's fix is landed in both trees, and the
tests pin the canonical tree explicitly, but reconciling the two migration sets
is separate work and is not attempted here.

## Files

| Path | Role |
|---|---|
| `tools/db/shadowed_migration_audit.py` | The audit — re-runnable, four oracles |
| `args/migration_duplicate_versions.yaml` | 60 entries, each with its verified reason |
| `tools/db/migrations/20260803204235_mvs_audit_03_shadowed_gaps/` | The fold-in |
| `tests/db/test_shadowed_migration_audit.py` | Guards, incl. the reason requirement |
