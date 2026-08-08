#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 20260808045015: CHECK vocabulary for sbom_dependencies.relationship_type.

sbx-cov-02 — the Component Dependency Relationship element.

sbx-fnd-02 created ``sbom_dependencies`` with ``relationship_type TEXT NOT NULL
DEFAULT 'depends_on'`` and NO CHECK, and said why: the house rule is that a
CHECK vocabulary is derived from a Python constant rather than hand-written in
DDL, and the vocabulary itself was this task's to define — it has to express
both CycloneDX ``dependsOn`` and SPDX ``RELATIONSHIP`` kinds. Inventing one
there would have pinned the wrong list in a constraint a later migration then
had to rewrite.

The vocabulary now exists as ``dependency_graph.RELATIONSHIP_TYPES`` and this
migration installs it. It is imported, not restated, so the constraint and the
graph builder cannot disagree — the failure mode where a component's edge type
is legal in Python and rejected by the database.

Why this is a .py migration rather than the .sql the scaffold produced: the
value list has to come from the Python constant, and SQLite cannot add a CHECK
to an existing table at all. The SQLite branch therefore rebuilds the table
(create-copy-drop-rename), which is the documented 12-step ALTER and is correct
whether or not rows exist. PostgreSQL takes a plain ADD CONSTRAINT, guarded by
an information_schema lookup because PG has no ADD CONSTRAINT IF NOT EXISTS.
"""

from tools.compliance.dependency_graph import RELATIONSHIP_TYPES, relationship_check_sql

MIGRATION_ID = "20260808045015"
MIGRATION_NAME = "sbom_relationship_type_vocabulary"
DESCRIPTION = "CHECK vocabulary for sbom_dependencies.relationship_type (sbx-cov-02)"

CONSTRAINT_NAME = "sbom_dependencies_relationship_type_check"

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sbom_dep_record ON sbom_dependencies(sbom_record_id)",
    "CREATE INDEX IF NOT EXISTS idx_sbom_dep_parent ON sbom_dependencies(parent_component_id)",
    "CREATE INDEX IF NOT EXISTS idx_sbom_dep_child  ON sbom_dependencies(child_component_id)",
    "CREATE INDEX IF NOT EXISTS idx_sbom_dep_tenant ON sbom_dependencies(tenant_id)",
]


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _table_exists(conn, table: str) -> bool:
    if _is_pg(conn):
        # current_schema(), not a hardcoded 'public': the migration must see the
        # table the rest of this connection sees, whatever search_path says.
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (table,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=%s",
            (table,),
        ).fetchone()
    return row is not None


def _sqlite_table_sql(conn) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=%s",
        ("sbom_dependencies",),
    ).fetchone()
    return (dict(row).get("sql") or "") if row else ""


def _rebuild_sqlite(conn, actions):
    """Recreate sbom_dependencies with the CHECK, preserving any rows.

    The column list is restated rather than read back from the live schema:
    this migration ships one revision after the CREATE TABLE and the two are
    reviewed together, so a copy that silently dropped a column added in
    between would be caught here rather than in production.
    """
    check = relationship_check_sql()
    conn.execute("DROP TABLE IF EXISTS sbom_dependencies__new")
    conn.execute(
        f"""
        CREATE TABLE sbom_dependencies__new (
            id                  TEXT    PRIMARY KEY,
            sbom_record_id      INTEGER NOT NULL REFERENCES sbom_records(id),
            parent_component_id TEXT    NOT NULL REFERENCES sbom_components(id),
            child_component_id  TEXT    NOT NULL REFERENCES sbom_components(id),
            relationship_type   TEXT    NOT NULL DEFAULT 'depends_on' {check},
            scope               TEXT,
            classification      TEXT    NOT NULL DEFAULT 'CUI',
            tenant_id           TEXT,
            created_at          TEXT    DEFAULT (datetime('now')),
            UNIQUE (sbom_record_id, parent_component_id, child_component_id, relationship_type)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO sbom_dependencies__new
            (id, sbom_record_id, parent_component_id, child_component_id,
             relationship_type, scope, classification, tenant_id, created_at)
        SELECT id, sbom_record_id, parent_component_id, child_component_id,
               relationship_type, scope, classification, tenant_id, created_at
        FROM sbom_dependencies
        """
    )
    conn.execute("DROP TABLE sbom_dependencies")
    conn.execute("ALTER TABLE sbom_dependencies__new RENAME TO sbom_dependencies")
    for statement in _INDEXES:
        conn.execute(statement)
    actions.append("sqlite_table_rebuilt_with_check")


def _constraint_exists(conn) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.table_constraints "
        "WHERE table_schema = current_schema() AND table_name = %s AND constraint_name = %s",
        ("sbom_dependencies", CONSTRAINT_NAME),
    ).fetchone()
    return row is not None


def up(conn) -> dict:
    """Install the relationship_type CHECK derived from RELATIONSHIP_TYPES."""
    actions = []

    if not _table_exists(conn, "sbom_dependencies"):
        # 20260808030213 creates it. If that has not run, there is nothing to
        # constrain and re-running this migration later is not possible, so say
        # so loudly rather than recording a no-op as applied.
        raise RuntimeError(
            "sbom_dependencies does not exist — apply 20260808030213_sbom_2026_minimum_elements "
            "first"
        )

    if _is_pg(conn):
        if _constraint_exists(conn):
            actions.append("pg_constraint_already_present")
        else:
            conn.execute(
                f"ALTER TABLE sbom_dependencies ADD CONSTRAINT {CONSTRAINT_NAME} "
                f"{relationship_check_sql()}"
            )
            actions.append("pg_constraint_added")
    else:
        if "CHECK" in _sqlite_table_sql(conn).upper():
            actions.append("sqlite_check_already_present")
        else:
            _rebuild_sqlite(conn, actions)

    conn.commit()
    return {"status": "applied", "actions": actions, "vocabulary": list(RELATIONSHIP_TYPES)}
