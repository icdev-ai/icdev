# CUI // SP-CTI
"""Migration 328 — give a mission step a competency class of its own (aca-trn-02).

``fa_mission_ontology``, ``fa_step_ontology`` and ``fa_user_competencies`` have
existed for some time and all three were EMPTY in production. The tables were
never the problem; nothing could reach them:

  1. ``seed_mission_ontology_mappings()`` ran from inside ``migrate()``, which
     executes BEFORE ``seed_mission_catalog()``. On a fresh install
     ``fa_missions`` was still empty, so every mapping lookup found nothing. It
     then wrote a count that its own "already seeded" guard read on the next
     boot as proof the job was done. 35 of 124 missions and 122 of 212 steps
     carried no ontology row.

     MEASURED 2026-08-02, AND SINCE PARTLY REMEDIATED IN PLACE. Do not re-run
     those counts against production and conclude this note is wrong: an earlier
     aca-trn-02 session ran the rewritten, DB-driven mapper against the live
     database, so all 124 missions and 212 steps now carry an ontology row
     there. The 35 figure is still checkable — it is exactly
     ``len(fa_missions) - len(BUILTIN_MISSIONS)`` (124 - 89), the missions
     seeded by the AADC/AIMC modules that the old ``BUILTIN_MISSIONS`` loop
     could never reach. What is NOT yet remediated in production is this column:
     it does not exist there, so every one of those 212 step rows still has no
     competency class. That is what this migration and the re-map on the next
     init are for.
  2. ``_create_kg_competency_edge`` inserted ``kg_edges.label``. The platform
     table names that column ``relationship``. On PostgreSQL that raises
     UndefinedColumn and ABORTS the transaction, so the statement after it
     failed too — inside a bare ``except Exception: pass``, which is why no one
     ever saw it.
  3. The platform KG DDL declares ``kg_nodes.graph_id`` and ``kg_edges.graph_id``
     as ``REFERENCES kg_graphs(id)``, and ``icdev-core-ontology`` is only created
     by the ontology federation pass. Where that key is materialised and
     federation never ran, the edge failed on a foreign key even with the column
     name corrected. Verified 2026-08-02: production PostgreSQL has the tables
     but never materialised this key, so there defect 2 alone was sufficient to
     keep the chain empty.

This migration covers the schema half. A step could only ever be typed
(``step_class``: what KIND of activity it is), never mapped to what passing it
DEMONSTRATES. Mission-level recording alone credits a learner for everything a
mission touches; the step class is what lets a certificate cite the specific
submissions behind a claim, and lets passive steps (``watch``) be excluded from
evidence entirely — being shown a topic is not evidence of it.

Nullable by design. NULL means "passing this step demonstrates nothing on its
own", which is the correct and common answer, so it is also the correct default
for a row written before this column existed. Backfill is not done here: the
class depends on the mission's topic class, which is derived in Python by
``apps/forge_academy/ontology.py``. ``seed_mission_ontology_mappings()`` re-maps
every step whose ``competency_class`` IS NULL on the next init, so existing rows
are filled by the app rather than by hand-maintained SQL that would drift from
the mapping rules it duplicates.

**Why this is Python and not a flat .sql file.** The column is added from two
directions: here, and by the Academy's own ``migrate()`` (apps/forge_academy/db.py),
whose ALTER list runs at app init. Whichever lands second sees a column that is
already there. ``migration_runner`` guards a failing script only when the error
text contains "already exists" — which is PostgreSQL's wording
(``column "competency_class" of relation "fa_step_ontology" already exists``) but
not SQLite's (``duplicate column name: competency_class``). As a flat .sql this
migration therefore raised on SQLite, and because ``executescript`` aborts the
whole script, the index after the ALTER was skipped as well. Checking first is
backend-independent and keeps the index creation reachable on both.
"""
from __future__ import annotations

from tools.db.storage import column_exists, get_connection, table_exists

_TABLE = "fa_step_ontology"
_COLUMN = "competency_class"
_TAG = "[328_fa_step_competency_class]"


def up(conn=None) -> None:
    own = conn is None
    conn = conn or get_connection()
    try:
        # fa_step_ontology is created by the Academy's own DDL, which may not
        # have run yet on a database migrated before the app is first served.
        # Nothing to alter in that case — the DDL already declares the column.
        if not table_exists(conn, _TABLE):
            print(f"{_TAG} {_TABLE} absent; the Academy DDL will declare the column")
            return

        if column_exists(conn, _TABLE, _COLUMN):
            print(f"{_TAG} {_TABLE}.{_COLUMN} already present")
        else:
            conn.execute(f"ALTER TABLE {_TABLE} ADD COLUMN {_COLUMN} TEXT")
            print(f"{_TAG} added {_TABLE}.{_COLUMN}")

        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_fa_step_ontology_competency "
            f"ON {_TABLE}({_COLUMN})"
        )
        conn.commit()
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    up()
