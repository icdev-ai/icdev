# CUI // SP-CTI
"""Migration 141 rollback — drop servicenow_change_tickets table."""

SQL_DOWN = """
DROP INDEX IF EXISTS idx_sn_change_connection;
DROP INDEX IF EXISTS idx_sn_change_state;
DROP INDEX IF EXISTS idx_sn_change_project;
DROP INDEX IF EXISTS idx_sn_change_plan;
DROP TABLE IF EXISTS servicenow_change_tickets;
"""


def down(conn):
    for stmt in SQL_DOWN.strip().split(";"):
        s = stmt.strip()
        if s and not s.startswith("--"):
            conn.execute(s)
    conn.commit()
