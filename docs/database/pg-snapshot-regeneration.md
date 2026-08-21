# Regenerating the PostgreSQL schema snapshot

`tools/db/schema/pg_consolidated.sql` is what `tools/db/bootstrap_pg.py` loads
into a **fresh** PostgreSQL database (CI E2E, a new deployment, a second ICDEV
parent such as ICDEV[FT]). Bootstrap then marks every migration with a version
`<= through_version` (from `pg_consolidated.meta.json`) as applied **without
running it**, and runs everything newer.

That second sentence is the whole hazard: a column added by a marked migration
exists on a fresh database only if the snapshot carries it.

## What went wrong (2026-08-21)

The snapshot was a `pg_dump` taken on 2026-07-26 (`through_version` 301) and was
hand-extended for four weeks while the canonical database kept moving. Measured
against the canonical database on 2026-08-21, a database bootstrapped from it
was short **173 columns across 102 tables**:

| where the column is declared | columns |
|---|---|
| a legacy migration's `ALTER TABLE` (e.g. `dic_chunk_links.chunk_hash`, migration 267 — marked applied, never present) | 26 |
| a runtime `_ensure_schema` in a tool module | 19 |
| `init_icdev_db.py`'s `CREATE TABLE` (which never alters an existing table) | 2 |
| **nowhere in the tree** — RLS `tenant_id`/`classification` columns added by a generic loop, `kanban_tasks` runtime columns (`last_heartbeat_at`, `max_runtime_seconds`, `idempotency_key`, …), `data_nodes.x/y` | 128 |

Nothing in CI could see it. The CI database is built by `init_icdev_db.py`
first, so bootstrap takes its `already_bootstrapped` branch and only *marks*;
the snapshot's own contents had been exercised by nothing since July. The first
genuinely fresh database in weeks — ICDEV[FT]'s `icdev_ft` — failed on the first
INSERT the document-intelligence ingest made.

A second, older defect surfaced while fixing the first. The **canonical database
itself** carries 14 migrations in 302–341 as `squashed-*` stamps whose DDL never
ran there: `302, 303, 305–309, 322–328` (322 alone is the 26 `bom_*` tables).
So a dump of the canonical database can never honestly claim `through_version:
341` — on the old snapshot those migrations *ran* on a fresh database (they were
above 301), and bumping the marker over a plain re-dump would have silently
removed them from every future install. The canonical database's own debt is
reported here, not repaired: it is a production database and the repair is a
deliberate `migrate` operation, not a side effect of regenerating a file.

## The procedure

Everything is `tools/db/regen_pg_snapshot.py`; `compose` and `diff` touch only
what they are handed, `diff` is read-only.

```bash
# 1. a fresh schema-only dump of the canonical database
ICDEV_DATABASE_URL=<canonical dsn> python tools/db/regen_pg_snapshot.py dump --out .tmp/canonical.sql

# 2. make every stamp TRUE before you trust it: bootstrap a SCRATCH database from
#    the fresh dump with through_version left at the OLD value, so the stamped-
#    but-unrun legacy migrations execute there (it is replayable: 0 failures on
#    2026-08-21), then dump the scratch database. THAT dump is the one you compose.
python tools/db/regen_pg_snapshot.py dump --dsn <scratch dsn> --out .tmp/scratch.sql

# 3. what did the PREVIOUS snapshot build that the fresh dump lacks? (a database
#    bootstrapped from the previous snapshot is the reference)
python tools/db/regen_pg_snapshot.py diff --reference <old-path dsn> --candidate <scratch dsn> --emit-alters .tmp/carry.sql

# 4. compose: fresh dump + carried-forward tables + carried columns + the
#    hand-maintained tail, verbatim
python tools/db/regen_pg_snapshot.py compose --dump .tmp/scratch.sql \
    --previous tools/db/schema/pg_consolidated.sql --carry-columns .tmp/carry.sql \
    --out tools/db/schema/pg_consolidated.sql --generated 2026-08-21

# 5. bump through_version in pg_consolidated.meta.json to the highest LEGACY
#    version on disk; bootstrap ANOTHER scratch database from the result

# 6. prove it -- all three must print "superset"
python tools/db/regen_pg_snapshot.py diff --reference <canonical dsn>  --candidate <scratch3 dsn>
python tools/db/regen_pg_snapshot.py diff --reference <old-path dsn>   --candidate <scratch3 dsn>
python tools/db/regen_pg_snapshot.py diff --reference <scratch dsn>    --candidate <scratch3 dsn>
pytest tests/db/test_pg_bootstrap_baseline.py tests/db/test_regen_pg_snapshot.py -q

# 7. mirror: icdev/tools/db/schema/ carries a copy of both files
```

## What `compose` preserves, and why each one was lost before

* **Carried-forward tables.** Declared by init_db / a migration, absent from the
  canonical database, so `pg_dump` never emits them. The first regeneration
  dropped twelve with no error anywhere. `compose` re-emits, from the previous
  dump region, every statement that builds a table the fresh dump lacks — in
  the previous order, made idempotent.
* **The hand-maintained tail.** The `ICDEV ADDITIVE SECTION` blocks (Security
  Design Canvas, Pipeline Design Canvas, FedRAMP, …). Kept verbatim after the
  carried section; they are all `IF NOT EXISTS`, so redundancy costs nothing.
* **Columns the tail used to add.** A tail block that declares a table with a
  NEWER shape than the canonical database has becomes a no-op the moment the
  dump region creates the table — so its extra columns silently stop arriving.
  28 columns on 10 tables on 2026-08-21 (`dm_ports`, `domain_coverage`,
  `fedramp_controls.tenant_id`, `genesis_phase_log`, …). `diff --emit-alters`
  types them from a database the previous snapshot built; `compose` writes them
  as `ADD COLUMN IF NOT EXISTS` before the tail.

## Guards

`tests/db/test_pg_bootstrap_baseline.py` pins the carried-forward table list,
the FK ordering rule (an **inline** `REFERENCES` needs its parent first; a
`pg_dump` FK arrives by a trailing `ALTER`, so alphabetical order there is
legitimate), and — since this incident — two columns that were measured absent:
`dic_chunk_links.chunk_hash` and the `kanban_tasks` runtime columns. A
regenerated snapshot that drops them, or a `through_version` bumped past a
migration whose DDL is not actually inside, goes red there instead of on the
next fresh deployment.

What is deliberately **not** guarded: the full 173-column diff. It is a
measurement against a live database and belongs in the procedure above, not in
a unit test that would need one.
