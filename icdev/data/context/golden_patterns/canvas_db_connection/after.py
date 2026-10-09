# CUI // SP-CTI
"""AFTER — canvas-local tables, no RLS predicate attached."""
from tools.db.storage import get_canvas_connection


def init_db() -> None:
    conn = get_canvas_connection()
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
    conn = get_canvas_connection()
    try:
        return conn.execute("SELECT id, title FROM aac_findings").fetchall()
    finally:
        conn.close()
