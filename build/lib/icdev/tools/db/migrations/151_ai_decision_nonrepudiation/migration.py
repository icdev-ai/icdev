#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 151 — AI Decision Non-Repudiation.

Adds cryptographic hash chaining and ECDSA signatures to canvas_ai_decisions
so every AI decision is tamper-evident and independently auditable.

Satisfies:
  NIST 800-53 AU-9  — cryptographic protection of audit info
  NIST 800-53 AU-10 — non-repudiation
  DoD RAI "Traceable" + "Governable" principles
"""
from __future__ import annotations


MIGRATION_ID = "151"
MIGRATION_NAME = "ai_decision_nonrepudiation"
DESCRIPTION = (
    "Add decision_hash, previous_decision_hash, signature to canvas_ai_decisions "
    "for tamper-evident AI decision chaining."
)


def _is_postgresql(conn):
    try:
        cur = conn.execute("SELECT version()")
        return "postgresql" in cur.fetchone()[0].lower()
    except Exception:
        return False


def _table_exists(conn, table_name):
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row[0] > 0
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name=%s",
                (table_name,),
            ).fetchone()
            return row[0] > 0
        except Exception:
            return False


def _column_exists(conn, table_name, column_name):
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return any(r[1] == column_name for r in rows)
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
                (table_name, column_name),
            ).fetchone()
            return row[0] > 0
        except Exception:
            return False


def _add_column_safe(conn, table, column, ddl):
    if _column_exists(conn, table, column):
        print(f"[migration-{MIGRATION_ID}] {table}.{column} already exists — skip")
        return True
    try:
        if _is_postgresql(conn):
            conn.execute("SAVEPOINT add_col")
        conn.execute(ddl)
        if _is_postgresql(conn):
            conn.execute("RELEASE SAVEPOINT add_col")
        print(f"[migration-{MIGRATION_ID}] {table}: added {column}")
        return True
    except Exception as exc:
        if _is_postgresql(conn):
            try:
                conn.execute("ROLLBACK TO SAVEPOINT add_col")
            except Exception:
                pass
        print(f"[migration-{MIGRATION_ID}] WARN: {table}: failed to add {column}: {exc}")
        return False


def up(conn=None) -> None:
    from tools.db.storage import get_connection

    c = conn or get_connection()
    is_pg = _is_postgresql(c)
    if is_pg:
        c.execute("SET LOCAL lock_timeout = '10s'")

    stats = {"added": 0, "skipped": 0, "errors": 0}

    if _table_exists(c, "canvas_ai_decisions"):
        for col, ddl in [
            ("decision_hash", "ALTER TABLE canvas_ai_decisions ADD COLUMN decision_hash TEXT"),
            ("previous_decision_hash", "ALTER TABLE canvas_ai_decisions ADD COLUMN previous_decision_hash TEXT"),
            ("signature", "ALTER TABLE canvas_ai_decisions ADD COLUMN signature TEXT"),
        ]:
            if _column_exists(c, "canvas_ai_decisions", col):
                stats["skipped"] += 1
                continue
            ok = _add_column_safe(c, "canvas_ai_decisions", col, ddl)
            if ok:
                stats["added"] += 1
            else:
                stats["errors"] += 1

        # Add index on decision_hash for quick lookup
        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_cad_hash ON canvas_ai_decisions(decision_hash)")
            print(f"[migration-{MIGRATION_ID}] idx_cad_hash created")
        except Exception as exc:
            print(f"[migration-{MIGRATION_ID}] WARN: idx_cad_hash failed: {exc}")

    else:
        print(f"[migration-{MIGRATION_ID}] canvas_ai_decisions does not exist — skip")

    if conn is None:
        c.commit()
        c.close()

    print(
        f"[migration-{MIGRATION_ID}] done: added={stats['added']}, skipped={stats['skipped']}, errors={stats['errors']}"
    )


def run(conn=None) -> None:
    up(conn)


if __name__ == "__main__":
    up()
