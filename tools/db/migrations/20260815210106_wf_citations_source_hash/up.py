#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 150: Add source_hash to wf_citations.

Links HITL citations to cryptographic hashes for provenance tracking.
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, is_pg

DB_PATH = BASE_DIR / "data" / "icdev.db"


def _table_exists(conn, table: str) -> bool:
    """Check if a table exists."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s AND table_name=%s",
            ("public", table),
        ).fetchone()
        if row and row[0] > 0:
            return True
    except Exception:
        pass
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if row and row[0] > 0:
            return True
    except Exception:
        pass
    return False


def _column_exists(conn, table: str, column: str) -> bool:
    """Check if a column exists."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=%s AND table_name=%s AND column_name=%s",
            ("public", table, column),
        ).fetchone()
        if row and row[0] > 0:
            return True
    except Exception:
        pass
    try:
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(col[1] == column for col in info)
    except Exception:
        pass
    return False


def migrate():
    conn = get_connection(db_path=str(DB_PATH))
    try:
        print("Migration 150: Add source_hash to wf_citations")
        print(f"Backend: {'PostgreSQL' if is_pg(conn) else 'SQLite'}")

        if not _table_exists(conn, "wf_citations"):
            print("  SKIP: wf_citations table does not exist")
            return True

        if _column_exists(conn, "wf_citations", "source_hash"):
            print("  SKIP: source_hash already exists on wf_citations")
            return True

        if is_pg(conn):
            try:
                conn.execute("SET LOCAL lock_timeout = '10s'")
            except Exception:
                pass
            try:
                conn.execute("SAVEPOINT sp_m150")
            except Exception:
                pass

        conn.execute("ALTER TABLE wf_citations ADD COLUMN source_hash TEXT")

        if is_pg(conn):
            try:
                conn.execute("RELEASE SAVEPOINT sp_m150")
            except Exception:
                pass
            try:
                conn.execute("SAVEPOINT sp_m150_idx")
            except Exception:
                pass

        conn.execute("CREATE INDEX IF NOT EXISTS idx_wf_citations_hash ON wf_citations(source_hash)")

        if is_pg(conn):
            try:
                conn.execute("RELEASE SAVEPOINT sp_m150_idx")
            except Exception:
                pass

        conn.commit()
        print("  OK:   Added source_hash + index to wf_citations")
        return True
    except Exception as e:
        print(f"  ERR:  {e}")
        if is_pg(conn):
            try:
                conn.execute("ROLLBACK TO SAVEPOINT sp_m150")
            except Exception:
                pass
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        conn.close()



def up(conn=None):  # noqa: ARG001 — migrate() manages its own connection
    """Runner entry point. See the note in the sibling 149 migration."""
    return migrate()

if __name__ == "__main__":
    ok = migrate()
    sys.exit(0 if ok else 1)
