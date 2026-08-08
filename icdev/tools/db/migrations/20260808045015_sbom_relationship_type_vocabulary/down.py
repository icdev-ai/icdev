#!/usr/bin/env python3
# CUI // SP-CTI
"""Rollback 20260808045015: drop the relationship_type CHECK vocabulary.

Returns sbom_dependencies to the shape 20260808030213 created — same columns,
same indexes, no CHECK on relationship_type. Rows are preserved: every value
that satisfies the constraint also satisfies its absence.
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sbom_dep_record ON sbom_dependencies(sbom_record_id)",
    "CREATE INDEX IF NOT EXISTS idx_sbom_dep_parent ON sbom_dependencies(parent_component_id)",
    "CREATE INDEX IF NOT EXISTS idx_sbom_dep_child  ON sbom_dependencies(child_component_id)",
    "CREATE INDEX IF NOT EXISTS idx_sbom_dep_tenant ON sbom_dependencies(tenant_id)",
]

CONSTRAINT_NAME = "sbom_dependencies_relationship_type_check"


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _table_exists(conn) -> bool:
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            ("sbom_dependencies",),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=%s",
            ("sbom_dependencies",),
        ).fetchone()
    return row is not None


def down(conn) -> dict:
    if not _table_exists(conn):
        return {"status": "skipped", "reason": "sbom_dependencies does not exist"}

    if _is_pg(conn):
        conn.execute(f"ALTER TABLE sbom_dependencies DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}")
        conn.commit()
        return {"status": "rolled_back", "actions": ["pg_constraint_dropped"]}

    conn.execute("DROP TABLE IF EXISTS sbom_dependencies__old")
    conn.execute(
        """
        CREATE TABLE sbom_dependencies__old (
            id                  TEXT    PRIMARY KEY,
            sbom_record_id      INTEGER NOT NULL REFERENCES sbom_records(id),
            parent_component_id TEXT    NOT NULL REFERENCES sbom_components(id),
            child_component_id  TEXT    NOT NULL REFERENCES sbom_components(id),
            relationship_type   TEXT    NOT NULL DEFAULT 'depends_on',
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
        INSERT INTO sbom_dependencies__old
            (id, sbom_record_id, parent_component_id, child_component_id,
             relationship_type, scope, classification, tenant_id, created_at)
        SELECT id, sbom_record_id, parent_component_id, child_component_id,
               relationship_type, scope, classification, tenant_id, created_at
        FROM sbom_dependencies
        """
    )
    conn.execute("DROP TABLE sbom_dependencies")
    conn.execute("ALTER TABLE sbom_dependencies__old RENAME TO sbom_dependencies")
    for statement in _INDEXES:
        conn.execute(statement)
    conn.commit()
    return {"status": "rolled_back", "actions": ["sqlite_table_rebuilt_without_check"]}
