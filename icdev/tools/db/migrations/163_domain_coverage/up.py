# CUI // SP-CTI
"""Migration 163 — Create domain_coverage table.

Persists computed domain-level capability coverage metrics (L/M/N grade
counts per requirement domain) so they can be queried without re-joining
rfp_shall_statements + icdev_capability_map on every request.

Populated by the GovCon capabilities route; refreshed on each capability
gap analysis pass.
"""

from tools.db.storage import get_connection, is_pg

MIGRATION_ID = "163"
MIGRATION_NAME = "domain_coverage"
DESCRIPTION = "Create domain_coverage cache table for GovCon capability gap analysis"

_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS domain_coverage (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    domain        TEXT NOT NULL,
    total         INTEGER NOT NULL DEFAULT 0,
    l_count       INTEGER NOT NULL DEFAULT 0,
    m_count       INTEGER NOT NULL DEFAULT 0,
    n_count       INTEGER NOT NULL DEFAULT 0,
    computed_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    classification TEXT NOT NULL DEFAULT 'CUI'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_domain_coverage_domain
    ON domain_coverage (domain);
"""

_DDL_PG = """
CREATE TABLE IF NOT EXISTS domain_coverage (
    id            SERIAL PRIMARY KEY,
    domain        TEXT NOT NULL,
    total         INTEGER NOT NULL DEFAULT 0,
    l_count       INTEGER NOT NULL DEFAULT 0,
    m_count       INTEGER NOT NULL DEFAULT 0,
    n_count       INTEGER NOT NULL DEFAULT 0,
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    classification TEXT NOT NULL DEFAULT 'CUI'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_domain_coverage_domain
    ON domain_coverage (domain);
"""


def up(conn=None) -> None:
    conn = get_connection()
    try:
        ddl = _DDL_PG if is_pg() else _DDL_SQLITE
        for stmt in ddl.strip().split(";"):
            s = stmt.strip()
            if s:
                try:
                    conn.execute(s)
                except Exception:
                    pass
        conn.commit()
        print("Migration 163 up: domain_coverage created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
