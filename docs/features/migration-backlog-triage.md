# CUI // SP-CTI

# Migration backlog — triage (2026-07-26)

**Status:** finding recorded; one instance fixed (`dic_chat_memory`), the rest open
**Trigger:** investigating why `dic_chat_memory` held 0 rows on a live corpus of 54 documents

---

## Headline

`schema_migrations` holds **189 rows with a max version of 293**, against **273 distinct migration
versions on disk** — 182 of which have no applied row.

**That raw number overstates the problem.** PostgreSQL is bootstrapped from
`tools/db/schema/pg_consolidated.sql`, not by replaying migrations in order (see
`bootstrap_pg.py`), so most "unapplied" migrations are already reflected in the consolidated schema
and simply never got a bookkeeping row. `001_baseline` appears in the unapplied list, which is proof
enough that the list cannot be read literally.

The real question is which unapplied migrations describe schema that is **genuinely absent from the
live database**. Answering it by comparing each unapplied migration's `CREATE TABLE` / `ADD COLUMN`
targets against `information_schema`:

- **~40 tables genuinely missing**
- **23 columns genuinely missing**

They cluster from **migration 227 onward**, with a few older stragglers (57, 85). That is consistent
with the consolidation squash having captured state around m226 and post-squash migrations drifting
since.

## Why this matters beyond one table

Several of the missing objects are **TRUST / provenance infrastructure** that other work assumes exists:

| Migration | Missing object | Consequence |
|---|---|---|
| 250 | `rag_provenance_ledger` | `tools/dic/provenance_adapter.py` silently returns empty dicts. Confirms gap A-4 in the anti-hallucination plan. |
| 276 | `idr_publish_audit` | The `CHECK (gate IN ('citation_guard','placeholder_guard'))` audit table does not exist — the citation-gate audit trail has nowhere to land. |
| 283 | `dic_claims` | DIC claim tracking. |
| 252 | `rag_queries`, `rag_citations` | Retrieval/citation logging. |
| 265 | `cortex_service_keys` | Cortex service auth. |
| 227 | `agent_loop_checkpoints` | Agent-loop resumability. |
| 229 | `ace_colearning_suggestions` | ACE co-learning. |

Anything that reads these degrades through a broad `except` rather than failing loudly, which is why
none of it has surfaced as a visible bug.

## Root cause of a large share of it: duplicate version numbers

**Added 2026-07-26, after the initial triage.** The missing objects are not simply "migrations that
were never run". Many of them *cannot* run, ever.

`schema_migrations.version` is UNIQUE, and `MigrationRunner.get_pending_migrations` dedupes by
version within a run as well, keeping the first entry by sort order. Its own docstring says the
consequence out loud:

> in steady state get_pending naturally yields only the first because the version is already in
> applied_versions after the first run

So when two migration files claim the same version number, **only the first is ever applied. The
rest are skipped permanently and silently** — no error, no warning, no row, and the tables they
declare never exist.

On disk today: **275 versions, 54 of them duplicated, 71 migrations shadowed and unrunnable.** Some
versions are claimed by four or five files (139 has five).

Cross-referencing against the missing-objects list above, the overlap is not subtle:

| Version | Applied | Shadowed — never runs | Confirmed missing from live DB |
|---|---|---|---|
| 283 | `283_dic_claims.sql` | `283_soar_playbook_runs.sql` | `soar_playbook_runs`, `soar_playbook_audit` |
| 282 | `282_docmod_nist_pubs.sql` | `282_insider_risk_uba.sql` | `insider_risk_baselines`, `insider_risk_scores` |
| 289 | `289_agent_cron_jobs.sql` | `289_twin_compat_reports.sql` | `twin_compat_reports` |

`179`, `215`, `223`, `236`, `257` follow the same pattern.

**This was about to happen again.** Three branches independently claimed version `295`:
`295_dic_chat_memory_reconcile.sql` (merged and applied), `295_web_citation_fetch_provenance/`
(PR #824) and `295_dynamic_finding_reproductions.sql` (PR #819). Because 295 is already recorded as
applied, both of the others would have been skipped on merge and their tables would have joined the
missing list — with nothing anywhere reporting it.

### Guard

`tools/db/migration_versions.py` + `tests/test_migration_version_uniqueness.py`:

```bash
python tools/db/migration_versions.py --json        # report
python tools/db/migration_versions.py --gate        # exit 1 on NEW duplicates
python tools/db/migration_versions.py --shadowed    # what will never run
```

The 54 existing collisions are grandfathered in `args/migration_duplicate_versions.yaml` so the gate
is actionable rather than perpetually red. Entries must not be added to silence a new collision —
renumber instead. Adding a third file to an existing collision shadows one more migration and is
treated as a new violation.

**Picking a migration number is a repo-wide claim, not a local choice.** Check open PRs, not just
`main` — `main` had no 295 when all three of these branches chose it.

## The failure mode this produced — worth generalising

`dic_chat_memory` is the fully-diagnosed instance, and the shape of it is likely to repeat:

1. Migration **191** created the table with a message-log schema (`memory_id`/`role`/`content`) that
   no code ever consumed.
2. `chat_memory.record_turn()` writes a **turn-based** schema (`turn_id`/`query`/`answer`/…), so
   every INSERT failed.
3. Migration **264** was written to reconcile the two — and was never applied.
4. The consolidation squash captured the **pre-264** state, so `pg_consolidated.sql` shipped the
   broken shape as well. **A fresh bootstrap was equally broken**, which is why nobody caught it by
   reinstalling.
5. `_ensure_table()` used `CREATE TABLE IF NOT EXISTS`, which **cannot repair a table that exists
   with the wrong columns** — it silently no-ops.
6. The write failure was swallowed twice (`logger.warning` → `logger.debug`), so a broken table was
   indistinguishable from an idle one.

**Generalisable lessons:**

- A migration that is not folded into the consolidation squash is orphaned twice over — unapplied on
  existing databases *and* absent from fresh ones. Squashing must either fold in every prior
  migration or record which ones it superseded.
- `CREATE TABLE IF NOT EXISTS` is not a self-heal. Any code claiming to self-heal a schema must
  compare **columns**, not existence.
- Swallowing a write failure to `debug` converts "broken" into "idle". Schema faults belong at
  `error`, with a health probe callers can surface.

## Recommended next steps (not done here)

1. **Do not bulk-apply the backlog.** `migrate --target N` applies all pending migrations, and many
   are already-consolidated no-ops whose runners may not be idempotent. Applying 182 blind is the
   riskier move, not the safer one.
2. Work the ~40 missing tables / 23 missing columns individually, prioritising the provenance set
   above, each with a shape-aware guard like `295_dic_chat_memory_reconcile.sql`.
3. Re-cut the consolidation from the live schema **after** the genuinely-missing objects land, and
   record the covered version range in the file header so the next reader can tell consolidated from
   pending at a glance.
4. Add a coherence check that fails when a migration defines an object absent from both the live
   database and `pg_consolidated.sql`. This class of drift is mechanically detectable — the triage
   query used here is about fifteen lines.

## Reproducing the triage

Compare each unapplied migration's declared objects against `information_schema`:

```python
from tools.db.storage import get_connection
# applied = {version} from schema_migrations
# live    = {table: {columns}} from information_schema.columns
# for each migration file whose version is not applied:
#     flag CREATE TABLE targets absent from `live`
#     flag ALTER TABLE ... ADD COLUMN targets absent from live[table]
```

Counting `schema_migrations` rows alone is not a triage and will mislead.
