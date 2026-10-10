# CUI // SP-CTI
"""BEFORE — every query raises UndefinedColumn on PostgreSQL."""
from tools.db.storage import get_connection  # wrong helper for a canvas


def init_db() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS aac_findings (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def list_findings():
    conn = get_connection()
    try:
        # The RLS predicate injected here references classification/tenant_id,
        # which aac_findings does not have. UndefinedColumn, every time.
        return conn.execute("SELECT id, title FROM aac_findings").fetchall()
    finally:
        conn.close()
