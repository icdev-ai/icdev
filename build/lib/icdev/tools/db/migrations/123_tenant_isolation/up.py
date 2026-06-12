#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 123 — tenant isolation.

Adds tenant_id TEXT column to every application table that currently has no
tenant scope, enabling per-tenant data isolation across FORGE Academy, AI
GameDay (TTX), Strategos, and dashboard_users.

All new columns are nullable. Existing rows default to NULL, which is treated
as the system/demo tenant — no data loss, no backfill required.

Tables modified:
  FORGE Academy:  fa_users, fa_missions, fa_guilds, fa_challenges,
                  fa_leaderboard_cache
  AI GameDay:     ttx_sessions, ttx_teams, ttx_leaderboard, ttx_scenarios
  Strategos:      strategos_signals, strategos_darkweb_signals
  Dashboard:      dashboard_users

Indexes added:
  idx_fa_users_tenant            ON fa_users(tenant_id)
  idx_ttx_sessions_tenant        ON ttx_sessions(tenant_id)
  idx_dashboard_users_tenant     ON dashboard_users(tenant_id)
  idx_strategos_signals_tenant   ON strategos_signals(tenant_id)
  idx_ttx_teams_code_tenant      UNIQUE ON ttx_teams(join_code, tenant_id)

Idempotent: uses IF NOT EXISTS guards throughout.
"""
from __future__ import annotations

MIGRATION_ID = "123"
MIGRATION_NAME = "tenant_isolation"
DESCRIPTION = (
    "Add tenant_id column to FORGE Academy, AI GameDay, Strategos, and "
    "dashboard_users for per-tenant data isolation."
)

_TABLES = [
    "fa_users",
    "fa_missions",
    "fa_guilds",
    "fa_challenges",
    "fa_leaderboard_cache",
    "ttx_sessions",
    "ttx_teams",
    "ttx_leaderboard",
    "ttx_scenarios",
    "strategos_signals",
    "strategos_darkweb_signals",
    "dashboard_users",
]

_INDEXES = [
    # (index_name, table, column_expr, unique)
    ("idx_fa_users_tenant",          "fa_users",          "tenant_id", False),
    ("idx_ttx_sessions_tenant",      "ttx_sessions",      "tenant_id", False),
    ("idx_dashboard_users_tenant",   "dashboard_users",   "tenant_id", False),
    ("idx_strategos_signals_tenant", "strategos_signals", "tenant_id", False),
]


def _column_exists(conn, table: str, column: str) -> bool:
    """Return True if the column already exists in the table."""
    try:
        # SQLite
        rows = list(conn.execute(f"PRAGMA table_info({table})"))
        if rows:
            return any(str(r[1]) == column for r in rows)
    except Exception:
        pass
    try:
        # PostgreSQL — query information_schema
        rows = list(conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            (table, column),
        ))
        return bool(rows)
    except Exception:
        pass
    return False


def _table_exists(conn, table: str) -> bool:
    """Return True if the table exists (backend-agnostic)."""
    try:
        rows = list(conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = %s",
            (table,),
        ))
        return bool(rows)
    except Exception:
        pass
    try:
        rows = list(conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table,),
        ))
        return bool(rows)
    except Exception:
        pass
    return False


def _add_column_safe(conn, table: str, column: str, col_type: str, backend: str) -> None:
    """Add column using a savepoint so a pre-existing column doesn't abort the txn."""
    if backend == "postgresql":
        sp = f"sp_{table}_{column}"
        try:
            conn.execute(f"SAVEPOINT {sp}")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            conn.execute(f"RELEASE SAVEPOINT {sp}")
        except Exception as exc:
            if "already exists" in str(exc).lower() or "duplicate column" in str(exc).lower():
                conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                conn.execute(f"RELEASE SAVEPOINT {sp}")
            else:
                conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                raise
    else:
        # SQLite — ALTER TABLE ADD COLUMN IF NOT EXISTS not yet in all 3.37 builds
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except Exception as exc:
            if "duplicate column" in str(exc).lower() or "already exists" in str(exc).lower():
                pass  # idempotent
            else:
                raise


def up(conn=None) -> None:
    from tools.db.storage import get_connection

    _conn = conn or get_connection()

    # Detect backend from the underlying connection type
    raw = getattr(_conn, "_conn", None) or getattr(_conn, "connection", None)
    backend = "postgresql" if "psycopg2" in type(raw).__module__ else "sqlite"
    if hasattr(_conn, "_backend"):
        backend = _conn._backend

    try:
        for table in _TABLES:
            if not _table_exists(_conn, table):
                print(f"  [skip] table {table} does not exist — skipping")
                continue
            _add_column_safe(_conn, table, "tenant_id", "TEXT", backend)

        # Regular partial indexes (skip if column doesn't exist after adds)
        for idx_name, table, col_expr, unique in _INDEXES:
            try:
                unique_kw = "UNIQUE " if unique else ""
                if backend == "postgresql":
                    # PostgreSQL partial index with IS NOT NULL
                    _conn.execute(
                        f"CREATE {unique_kw}INDEX IF NOT EXISTS {idx_name} "
                        f"ON {table}({col_expr}) WHERE {col_expr} IS NOT NULL"
                    )
                else:
                    _conn.execute(
                        f"CREATE {unique_kw}INDEX IF NOT EXISTS {idx_name} "
                        f"ON {table}({col_expr}) WHERE {col_expr} IS NOT NULL"
                    )
            except Exception as exc:
                if "already exists" in str(exc).lower():
                    pass
                else:
                    print(f"  [warn] index {idx_name}: {exc}")

        # Composite unique for GameDay join codes
        try:
            if backend == "postgresql":
                _conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_ttx_teams_code_tenant "
                    "ON ttx_teams(join_code, COALESCE(tenant_id, ''))"
                )
            else:
                _conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_ttx_teams_code_tenant "
                    "ON ttx_teams(join_code, COALESCE(tenant_id, ''))"
                )
        except Exception as exc:
            if "already exists" in str(exc).lower():
                pass
            else:
                print(f"  [warn] idx_ttx_teams_code_tenant: {exc}")

        _conn.commit()
        print(f"[migration-{MIGRATION_ID}] {MIGRATION_NAME} applied OK")
    finally:
        if conn is None:
            _conn.close()


def run(conn=None) -> None:
    up(conn)


if __name__ == "__main__":
    up()
