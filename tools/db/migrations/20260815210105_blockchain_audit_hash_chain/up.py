#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 149: Blockchain Audit Hash Chain.

Adds cryptographic hash chaining columns to audit tables:
- hash         : SHA-256 of the row content
- previous_hash: SHA-256 of the previous row (tamper-evident chain)
- signature    : ECDSA or HMAC signature of the hash

Also creates:
- source_citation_registry : unified index of all citations across subsystems
- govchain_pending_operations: queue for air-gapped blockchain intent

PostgreSQL note: Uses lock_timeout to avoid hanging on busy tables.
If a table is locked, the migration reports blockers and continues.
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, is_pg

DB_PATH = BASE_DIR / "data" / "icdev.db"

HASH_CHAIN_TABLES = [
    "audit_trail", "audit", "compliance_evidence_chain", "agent_mailbox",
    "devsecops_pipeline_audit", "production_audits", "remediation_audit_log",
    "genesis_audit", "redaction_audit", "pg_proposal_genesis_audit",
    "egress_policy_audit", "evolution_audit", "review_board_audit",
    "scout_audit", "maintenance_audits",
]

NEW_COLUMNS = [("hash", "TEXT"), ("previous_hash", "TEXT"), ("signature", "TEXT")]

NEW_INDEXES = [
    ("audit_trail", "idx_audit_prev_hash", "previous_hash"),
    ("audit_trail", "idx_audit_sig", "signature"),
    ("source_citation_registry", "idx_scr_project", "project_id"),
    ("source_citation_registry", "idx_scr_type", "citation_type"),
    ("source_citation_registry", "idx_scr_hash", "source_hash"),
    ("source_citation_registry", "idx_scr_anchor", "anchor_hash"),
]


def _table_exists(conn, table: str) -> bool:
    """Check if a table exists in the database."""
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


def _table_has_column(conn, table: str, column: str) -> bool:
    """Check if a table already has a column."""
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
            (table, column),
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
            info = conn.execute(f"PRAGMA table_info({table})").fetchall()
            return any(col[1] == column for col in info)
    except Exception:
        pass
    return False


def _get_blockers(conn, table: str) -> list:
    """Return PIDs blocking this table. PostgreSQL only."""
    if not is_pg(conn):
        return []
    try:
        rows = conn.execute(
            """SELECT DISTINCT blocked_locks.pid AS blocked_pid,
                     blocking_locks.pid AS blocking_pid
               FROM pg_locks blocked_locks
               INNER JOIN pg_locks blocking_locks
                 ON blocking_locks.locktype = blocked_locks.locktype
                 AND blocking_locks.relation = blocked_locks.relation
               WHERE blocked_locks.granted = false
                 AND blocking_locks.granted = true
                 AND blocked_locks.relation = %s::regclass""",
            (table,),
        ).fetchall()
        return [r["blocking_pid"] for r in rows]
    except Exception:
        return []


def _add_column_safe(conn, table: str, column: str, dtype: str) -> dict:
    """Add a column with lock_timeout. Returns status dict."""
    if not _table_exists(conn, table):
        return {"status": "skip", "reason": "table does not exist"}

    if _table_has_column(conn, table, column):
        return {"status": "skip", "reason": "already exists"}

    # Set lock timeout to avoid hanging on production tables
    if is_pg(conn):
        try:
            conn.execute("SET LOCAL lock_timeout = '10s'")
        except Exception:
            pass

    # Use SAVEPOINT on PostgreSQL so one failed DDL doesn't abort the whole tx
    savepoint_name = f"sp_{table}_{column}"
    if is_pg(conn):
        try:
            conn.execute(f"SAVEPOINT {savepoint_name}")
        except Exception:
            pass

    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {dtype}")
        if is_pg(conn):
            try:
                conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
            except Exception:
                pass
        return {"status": "ok", "table": table, "column": column}
    except Exception as e:
        err = str(e).lower()
        if is_pg(conn):
            try:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
            except Exception:
                pass
        if "lock" in err or "timeout" in err:
            blockers = _get_blockers(conn, table)
            return {
                "status": "locked",
                "table": table,
                "column": column,
                "error": str(e),
                "blocking_pids": blockers,
                "recommendation": "Terminate idle-in-transaction backends or run during maintenance window",
            }
        return {"status": "error", "table": table, "column": column, "error": str(e)}


def migrate():
    conn = get_connection(db_path=str(DB_PATH))
    results = {"ok": [], "locked": [], "errors": [], "skipped": []}
    try:
        print("Migration 149: Blockchain Audit Hash Chain")
        print(f"Backend: {'PostgreSQL' if is_pg(conn) else 'SQLite'}")
        print("=" * 50)

        # 1. Add hash chain columns
        print("\n[1/4] Adding hash chain columns to audit tables...")
        for table in HASH_CHAIN_TABLES:
            for col, dtype in NEW_COLUMNS:
                r = _add_column_safe(conn, table, col, dtype)
                if r["status"] == "ok":
                    results["ok"].append(f"{table}.{col}")
                    print(f"  OK:   {table}.{col}")
                elif r["status"] == "skip":
                    results["skipped"].append(f"{table}.{col}")
                    print(f"  SKIP: {table}.{col} (exists)")
                elif r["status"] == "locked":
                    results["locked"].append(r)
                    print(f"  LOCK: {table}.{col} — blocked by PIDs {r.get('blocking_pids', [])}")
                else:
                    results["errors"].append(r)
                    print(f"  ERR:  {table}.{col} — {r.get('error', '')}")

        conn.commit()

        # 2. Create source_citation_registry
        print("\n[2/4] Creating source_citation_registry...")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_citation_registry (
                id TEXT PRIMARY KEY,
                citation_type TEXT NOT NULL CHECK(citation_type IN (
                    'hitl','rag','prov_entity','prov_activity','canvas_ai',
                    'slsa','sbom','compliance_evidence','agent_decision','manual'
                )),
                source_table TEXT NOT NULL,
                source_record_id TEXT NOT NULL,
                source_doc TEXT,
                source_hash TEXT NOT NULL,
                anchor_hash TEXT,
                merkle_root TEXT,
                blockchain_tx_id TEXT,
                classification TEXT DEFAULT 'CUI',
                project_id TEXT,
                trust_score REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        print("  OK:   source_citation_registry")

        # 3. Create govchain_pending_operations
        print("\n[3/4] Creating govchain_pending_operations...")
        if is_pg(conn):
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS govchain_pending_operations (
                    id SERIAL PRIMARY KEY,
                    operation_type TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    submitted_at TIMESTAMP,
                    error_message TEXT
                )
                """
            )
        else:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS govchain_pending_operations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation_type TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    submitted_at TIMESTAMP,
                    error_message TEXT
                )
                """
            )
        print("  OK:   govchain_pending_operations")

        # 4. Create indexes
        print("\n[4/4] Creating indexes...")
        for table, idx_name, columns in NEW_INDEXES:
            if not _table_exists(conn, table):
                print(f"  SKIP: {idx_name} (table {table} does not exist)")
                continue
            savepoint_name = f"sp_idx_{idx_name}"
            if is_pg(conn):
                try:
                    conn.execute(f"SAVEPOINT {savepoint_name}")
                except Exception:
                    pass
            try:
                conn.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({columns})")
                if is_pg(conn):
                    try:
                        conn.execute(f"RELEASE SAVEPOINT {savepoint_name}")
                    except Exception:
                        pass
                print(f"  OK:   {idx_name}")
            except Exception as e:
                if is_pg(conn):
                    try:
                        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
                    except Exception:
                        pass
                print(f"  ERR:  {idx_name}: {e}")

        conn.commit()

        # Summary
        print("\n" + "=" * 50)
        print(f"Migration 149 complete: {len(results['ok'])} added, {len(results['skipped'])} skipped, {len(results['locked'])} locked, {len(results['errors'])} errors")
        if results["locked"]:
            print("\nLOCKED tables (run during maintenance window):")
            for r in results["locked"]:
                print(f"  {r['table']}.{r['column']} — blocked by PIDs {r.get('blocking_pids', [])}")
        return len(results["errors"]) == 0
    except Exception as e:
        print(f"\nMigration 149 FAILED: {e}")
        import traceback
        traceback.print_exc()
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        conn.close()



def up(conn=None):  # noqa: ARG001 — migrate() manages its own connection
    """Runner entry point.

    The work lives in ``migrate``, which opens and closes its own connection
    against DB_PATH. The runner's ``conn`` is accepted and deliberately not
    forwarded: rewiring that function's connection handling is a behaviour
    change, and this promotion is about making it RUN, not about changing what
    it does.
    """
    return migrate()

if __name__ == "__main__":
    ok = migrate()
    sys.exit(0 if ok else 1)
