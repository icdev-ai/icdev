# CUI // SP-CTI
"""Migration 20260902233931 — the two clocks rmf_workflow_stages could not tell apart.

rmf-cyc-01. The table records *where* an RMF package is (``stage``, ``status``,
``completed_at``) and nothing about *how long anything took*. A single
``completed_at`` cannot answer either of the two questions the pursuit actually
makes a claim about:

    automation_time    OURS. How long the platform took to produce the package.
                       This is the "72 hours" claim.
    decision_latency   THE AO's. How long the authorization package sat in the
                       Authorizing Official's queue awaiting a decision.

They are different quantities owned by different parties, and a single elapsed
figure that spans both is unfalsifiable: a deployment can halve its automation
time and watch the headline number get WORSE because an AO went on leave, or
leave the automation untouched and watch it improve. Merging them is how
"months -> 72 hours" becomes a sentence nobody can check. So the schema is
widened to record the boundary BETWEEN them rather than a single span across
them.

WHAT EACH COLUMN IS FOR
-----------------------
``started_at``    when work on this stage FIRST produced something. Stamped once,
                  by the first artifact write, and never moved — an artifact
                  regenerated on day 3 must not reset the clock to day 3.
``actor``         WHO produced the most recent write: a generator name
                  (``ssp_generator``) or ``human:<who>``. This is what separates
                  the automated population from the manual one, and therefore
                  what makes a ``measured_here`` baseline possible at all.
                  Without it every row looks automated and the improvement claim
                  has no control group.
``evidence_ref``  a pointer to the artifact that CAUSED the write
                  (``ssp_documents:12``, ``file:/path/ssp.md``). A stage row
                  asserting "assess is in progress" with nothing to point at is
                  the same defect this repo keeps finding: a surface asserting
                  something whose supporting evidence nothing can re-derive.
``submitted_at``  when the package for this stage was HANDED to the decider.
                  On the ``authorize`` row this is the AO submission, and it is
                  the boundary: everything before it is ours, everything after
                  it is theirs. It is a separate column from ``completed_at``
                  precisely because a package that has been submitted and not
                  yet decided is a real, common, reportable state — and with one
                  column it is indistinguishable from a package never submitted.

WHY A PYTHON MIGRATION AND NOT up.sql
-------------------------------------
This table is in an unusual position: it exists on PostgreSQL (it is in
``tools/db/schema/pg_consolidated.sql``) and is created by NOTHING on SQLite —
``init_icdev_db.py`` has never carried it and no earlier migration creates it.
So the three populations need three different things:

  * live PG              ALTER TABLE, add four columns
  * fresh SQLite         CREATE TABLE, whole shape
  * a SQLite database that somehow already has the old shape
                         ALTER TABLE, one column at a time

SQLite has no ``ADD COLUMN IF NOT EXISTS``, so a directive-split up.sql would
have to guess which of those three it is facing. Probing the live catalogue and
adding exactly the columns that are missing does not guess. ``init_icdev_db.py``
carries the full shape too, for databases created after this lands; this
migration is what reaches the ones already running.
"""
from __future__ import annotations

# The four columns this migration adds, in declaration order. Every one is
# NULLABLE with no default: NULL means NOT RECORDED, and a default would make a
# row that no producer has ever touched indistinguishable from one it has.
NEW_COLUMNS = (
    ("started_at", "TEXT"),
    ("actor", "TEXT"),
    ("evidence_ref", "TEXT"),
    ("submitted_at", "TEXT"),
)

# The full shape, for a database that does not have the table at all. Mirrors
# the PostgreSQL definition in pg_consolidated.sql (stage/status CHECKs, the
# UNIQUE(project_id, stage) the recorder upserts on, classification defaulting
# to the LABEL 'CUI' the existing rows carry) plus the four new columns.
CREATE_SQLITE = """
CREATE TABLE IF NOT EXISTS rmf_workflow_stages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL,
    stage           TEXT NOT NULL
        CHECK (stage IN ('categorize', 'select', 'implement',
                         'assess', 'authorize', 'monitor')),
    status          TEXT NOT NULL DEFAULT 'not_started'
        CHECK (status IN ('not_started', 'in_progress', 'complete', 'blocked')),
    assigned_to     TEXT,
    completed_at    TEXT,
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    classification  VARCHAR(50) DEFAULT 'CUI',
    started_at      TEXT,
    actor           TEXT,
    evidence_ref    TEXT,
    submitted_at    TEXT,
    UNIQUE (project_id, stage)
)
"""


def _backend(conn) -> str:
    return getattr(conn, "_backend", "sqlite")


def _table_present(conn, backend: str) -> bool:
    if backend == "postgresql":
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'rmf_workflow_stages'"
        ).fetchone()
        return row is not None
    # pg-portability: sqlite-only path — sqlite_master is the SQLite catalogue;
    # the PostgreSQL branch above reads information_schema instead.
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rmf_workflow_stages'"
    ).fetchone()
    return row is not None


def _existing_columns(conn, backend: str) -> set:
    if backend == "postgresql":
        rows = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'rmf_workflow_stages'"
        ).fetchall()
        return {dict(r)["column_name"] for r in rows}
    # pg-portability: sqlite-only path — PRAGMA has no information_schema
    # equivalent and vice versa.
    rows = conn.execute("PRAGMA table_info(rmf_workflow_stages)").fetchall()
    out = set()
    for r in rows:
        d = dict(r) if hasattr(r, "keys") else None
        out.add(d["name"] if d and "name" in d else r[1])
    return out


def up(conn):
    backend = _backend(conn)

    if not _table_present(conn, backend):
        if backend == "postgresql":
            # Not the expected PG population, but a PG database restored without
            # the consolidated snapshot is a real case and must not silently skip
            # the rest of this migration.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rmf_workflow_stages (
                    id              SERIAL PRIMARY KEY,
                    project_id      TEXT NOT NULL,
                    stage           TEXT NOT NULL
                        CHECK (stage IN ('categorize', 'select', 'implement',
                                         'assess', 'authorize', 'monitor')),
                    status          TEXT NOT NULL DEFAULT 'not_started'
                        CHECK (status IN ('not_started', 'in_progress',
                                          'complete', 'blocked')),
                    assigned_to     TEXT,
                    completed_at    TEXT,
                    notes           TEXT,
                    created_at      TEXT DEFAULT (now())::text,
                    updated_at      TEXT DEFAULT (now())::text,
                    classification  VARCHAR(50) DEFAULT 'CUI',
                    started_at      TEXT,
                    actor           TEXT,
                    evidence_ref    TEXT,
                    submitted_at    TEXT,
                    UNIQUE (project_id, stage)
                )
                """
            )
        else:
            conn.execute(CREATE_SQLITE)
        conn.commit()
        return

    present = _existing_columns(conn, backend)
    for name, sql_type in NEW_COLUMNS:
        if name in present:
            continue
        conn.execute(f"ALTER TABLE rmf_workflow_stages ADD COLUMN {name} {sql_type}")
    conn.commit()
