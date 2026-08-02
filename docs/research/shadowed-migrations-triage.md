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

## The ten real gaps

| Migration | Missing tables | Verdict |
|---|---|---|
| `193_gate_failures.sql` | `compliance_gates`, `gate_failures` | **applies cleanly — renumbered 331** |
| `289_twin_compat_reports.sql` | `twin_compat_reports` | **applies cleanly — renumbered 332** |
| `336`→`173_create_documents_table.sql` | `documents` | **fails on PG**: `strftime()` is SQLite-only |
| `339`→`283_soar_playbook_runs.sql` | `soar_playbook_audit`, `soar_playbook_runs` | **fails on PG**: `AUTOINCREMENT` |
| `027_compliance_twin_schema` | `compliance_twin_runs`, `compliance_twin_snapshots`, `compliance_twin_violations` | python migration, carries SQLite dialect |
| `211_soc2_evidence` | `evidence_items` | python migration, carries SQLite dialect |
| `023_sharepoint` | `sharepoint_sites`, `sharepoint_lists`, `sharepoint_items`, `sharepoint_documents` | python migration, no dialect markers — unverified |
| `027_pipeline_snapshots` | `pipeline_snapshots` | python migration, no dialect markers — unverified |
| `028_odc_mitre_coverage` | `mitre_coverage` | python migration, no dialect markers — unverified |
| `082_sg_orbat_strengths` | `sg_orbat_strengths` | python migration, no dialect markers — unverified |

## Two findings worth separating out

**Being shadowed hid dialect bugs.** `173_create_documents_table.sql` uses `strftime()` and
`283_soar_playbook_runs.sql` uses `AUTOINCREMENT` — both SQLite-only. They fail against
PostgreSQL, which is the primary backend. Because they never ran, nobody found out.
Renumbering them without fixing the dialect would **break the migration chain** rather than
close a gap. The runner supports `-- @pg-only` / `-- @sqlite-only` directives, so the repair
is mechanical but needs per-migration care.

**A shadowed migration explains an inert subsystem.**
`tools/boundary_canvas/cato_readiness.py::compute_readiness` was found inert earlier in this
review because `compliance_twin_snapshots` and `bd_designs` "do not exist". They do not exist
*because* `027_compliance_twin_schema` was shadowed by `027_ad_coach_alerts` and has never
run. That is the causal chain: duplicate version → migration never applies → table missing →
scorer silently returns nothing → subsystem reads as unimplemented.

Worth checking whether other "inert" subsystems in
[canvas-engine-sweep.md](canvas-engine-sweep.md) have the same root cause rather than being
unbuilt.

## What was changed here, and what was not

**Changed:** only the two migrations *proven* to apply — verified by executing them against
the live schema in a rolled-back transaction. Renumbered to 331 and 332.

**Not changed:** the other eight. Four are Python migrations with no dialect markers, but
absence of a marker is not proof of execution, and renumbering makes a migration RUN on the
next `migrate`. A migration that fails mid-chain is worse than one that never ran. Closing
those needs per-migration execution against a scratch database.

The collision *gate* — `tests/test_migration_version_collisions.py` — stops new duplicates
from being created regardless, and grandfathers the existing 53 so the backlog is frozen
rather than growing.
