# Shadowed Migrations — Triage

CUI // SP-CTI · 2026-08-02

`schema_migrations.version` is UNIQUE and `MigrationRunner.get_pending_migrations`
dedupes by version, **keeping the first by sort order**. When two migrations share a
number the later one never runs — on any database, forever, silently.

**Measured: 53 duplicated version numbers shadowing 71 migrations.** This triages which
of those actually left a hole in the schema, and what it would take to close each.

Method: extract `CREATE TABLE` targets from every shadowed migration, diff against
`information_schema.tables` on the live PostgreSQL instance, then attempt each candidate
inside a transaction that is **rolled back** — so applicability is measured, not assumed.

---

## Result

| Outcome | Count |
|---|---|
| Shadowed migrations | 71 |
| Tables exist anyway (created by init/consolidated schema/another migration) | 38 |
| Create no tables (ALTER/data only — not assessed) | 9 |
| **Left a real gap** | **10** |

So most of the shadowing is harmless. Ten are not.

## The ten real gaps — all ten now un-shadowed

| Was | Now | Tables recovered |
|---|---|---|
| `193_gate_failures.sql` | 331 | `compliance_gates`, `gate_failures` |
| `289_twin_compat_reports.sql` | 332 | `twin_compat_reports` |
| `023_sharepoint` | 333 | `sharepoint_sites/lists/items/documents` |
| `027_compliance_twin_schema` | 334 | `compliance_twin_runs/snapshots/violations` |
| `027_pipeline_snapshots` | 335 | `pipeline_snapshots` |
| `028_odc_mitre_coverage` | 336 | `mitre_coverage` |
| `082_sg_orbat_strengths` | 337 | `sg_orbat_strengths` |
| `173_create_documents_table.sql` | 338 | `documents` |
| `211_soc2_evidence` | 339 | `evidence_items` |
| `283_soar_playbook_runs.sql` | 340 | `soar_playbook_runs`, `soar_playbook_audit` |

**All ten verified against the live PostgreSQL schema** — each executed inside a
transaction that was rolled back: 10 OK, 0 FAIL.

### One real bug found and fixed

`211_soc2_evidence` declared `def up() -> None` and opened its own connection, but the
runner calls `mod.up(conn)`. It would have raised
`TypeError: up() takes 0 positional arguments but 1 was given` on first contact with the
runner. Nobody found out because it shares version 211 with `211_idr_tables.sql`, which
sorts first — the shadowing hid a migration that could never have run even if dispatched.
Now takes an optional `conn` so the runner owns the transaction and the `__main__` path
still works.

### A correction worth recording

An earlier pass of this document reported that `173_create_documents_table.sql`
(`strftime()`) and `283_soar_playbook_runs.sql` (`AUTOINCREMENT`) **fail on PostgreSQL**.
That was wrong, twice over, and both errors came from testing outside the runner's own path:

* `173` already carries a `-- @pg-only` section. The first test stripped all `--` lines as
  comments, which removed the engine directives along with them, so the SQLite-only
  statements were executed against PostgreSQL.
* `283` is translated by `tools/db/storage.py::translate_sql`, which rewrites
  `INTEGER PRIMARY KEY AUTOINCREMENT` to `SERIAL PRIMARY KEY` — exactly as that migration's
  own header claims. Testing with raw `psycopg2` bypassed it.

A third artefact in the same vein: splitting the SQL on `;` broke a statement containing a
semicolon inside a comment, producing a spurious `syntax error at or near "enrichment"`.

**The lesson generalises: a migration must be exercised through
`_filter_sql` + `translate_sql`, never by hand-parsing the file.** Dialect that looks
SQLite-only is frequently handled one layer down.

## What was changed, and what was not

**Changed:** all ten migrations that left a real gap are renumbered to 331-340, and the
`up()` signature bug in `211_soc2_evidence` is fixed. Each was verified against the live
schema through the runner's own `_filter_sql` + `translate_sql` path before renumbering.

**Not changed:** the 38 benign shadowed migrations (their tables exist by other routes) and
the 9 that create no tables. Renumbering those would make them re-run for no benefit, and
an ALTER re-applied is not always idempotent. They stay shadowed and grandfathered.

Nothing here applies a migration to the live database — renumbering only makes them
*pending*. Running `migrate` is a separate, deliberate step.

The collision *gate* — `tests/test_migration_version_collisions.py`, shipped in #1197 —
stops new duplicates regardless, and grandfathers the remaining 49 so the backlog is frozen
rather than growing. Once this merges, versions 023, 027, 028, 082, 173, 193, 211, 283 and
289 are no longer duplicated and must leave that grandfather list; its stale-exemption test
enforces exactly that.
