#!/usr/bin/env python3
"""ICDEV SaaS Platform -- Platform Database Schema & Connection.

CUI // SP-CTI

Platform-level database for tenant metadata, users, API keys, subscriptions,
and usage tracking. This is NOT the per-tenant project database -- those are
provisioned separately per tenant.

Dual backend support:
  - PostgreSQL (production): Set PLATFORM_DB_URL env var
  - SQLite (local dev/testing): Falls back to data/platform.db

Usage:
    python tools/saas/platform_db.py --init
    python tools/saas/platform_db.py --init --force
    python tools/saas/platform_db.py --verify
    python tools/saas/platform_db.py --info
"""

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("platform_db")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SQLITE_PATH = DATA_DIR / "platform.db"

# ---------------------------------------------------------------------------
# PostgreSQL Schema
# ---------------------------------------------------------------------------
PG_SCHEMA_SQL = """
-- ICDEV SaaS Platform -- PostgreSQL Schema (CUI // SP-CTI)

CREATE TABLE IF NOT EXISTS tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(128) NOT NULL, slug VARCHAR(128) NOT NULL UNIQUE,
    impact_level VARCHAR(4) NOT NULL DEFAULT 'IL4' CHECK (impact_level IN ('IL2','IL4','IL5','IL6')),
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','provisioning','active','suspended','deactivated','deleted')),
    tier VARCHAR(20) NOT NULL DEFAULT 'starter' CHECK (tier IN ('starter','professional','enterprise')),
    db_host VARCHAR(256), db_name VARCHAR(128), db_port INTEGER DEFAULT 5432,
    k8s_namespace VARCHAR(128), aws_account_id VARCHAR(32),
    artifact_config JSONB DEFAULT '{}', bedrock_config JSONB DEFAULT '{}',
    idp_config JSONB DEFAULT '{}', settings JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_by VARCHAR(128), approved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_tenants_slug ON tenants(slug);
CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status);

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email VARCHAR(256) NOT NULL, display_name VARCHAR(256),
    role VARCHAR(24) NOT NULL DEFAULT 'developer' CHECK (role IN ('tenant_admin','developer','compliance_officer','auditor','viewer')),
    auth_method VARCHAR(16) NOT NULL DEFAULT 'api_key' CHECK (auth_method IN ('api_key','oauth','cac_piv')),
    status VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','deactivated')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), last_login TIMESTAMPTZ,
    UNIQUE (tenant_id, email)
);
CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_hash VARCHAR(128) NOT NULL UNIQUE, key_prefix VARCHAR(16) NOT NULL,
    name VARCHAR(64) NOT NULL, scopes JSONB DEFAULT '[]',
    status VARCHAR(12) NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked','expired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), expires_at TIMESTAMPTZ, last_used_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys(tenant_id);

CREATE TABLE IF NOT EXISTS subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tier VARCHAR(20) NOT NULL DEFAULT 'starter' CHECK (tier IN ('starter','professional','enterprise')),
    max_projects INTEGER NOT NULL DEFAULT 5, max_users INTEGER NOT NULL DEFAULT 3,
    allowed_il_levels JSONB DEFAULT '["IL2","IL4"]', allowed_frameworks JSONB DEFAULT '["nist_800_53"]',
    bedrock_pool_enabled BOOLEAN DEFAULT FALSE,
    starts_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), ends_at TIMESTAMPTZ,
    status VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active','expired','cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_tenant ON subscriptions(tenant_id);

CREATE TABLE IF NOT EXISTS usage_records (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    endpoint VARCHAR(256) NOT NULL, method VARCHAR(8) NOT NULL,
    tokens_used INTEGER DEFAULT 0, status_code INTEGER, duration_ms INTEGER,
    metadata JSONB DEFAULT '{}', recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_usage_tenant_time ON usage_records(tenant_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_usage_recorded ON usage_records(recorded_at);

CREATE TABLE IF NOT EXISTS audit_platform (
    id BIGSERIAL PRIMARY KEY, tenant_id UUID, user_id UUID,
    event_type VARCHAR(64) NOT NULL, action TEXT NOT NULL,
    details JSONB DEFAULT '{}', ip_address VARCHAR(45), user_agent VARCHAR(512),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_platform(tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_platform(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_platform(recorded_at);

CREATE OR REPLACE FUNCTION prevent_audit_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_platform is append-only';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_audit_no_update ON audit_platform;
CREATE TRIGGER trg_audit_no_update BEFORE UPDATE OR DELETE ON audit_platform
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();

CREATE TABLE IF NOT EXISTS rate_limits (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    window_start TIMESTAMPTZ NOT NULL,
    window_type VARCHAR(12) NOT NULL CHECK (window_type IN ('minute','hour','day')),
    request_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (tenant_id, window_start, window_type)
);
CREATE INDEX IF NOT EXISTS idx_rate_limits_tenant ON rate_limits(tenant_id, window_start);
"""

# ---------------------------------------------------------------------------
# SQLite Schema (translated from PG)
# ---------------------------------------------------------------------------
SQLITE_SCHEMA_SQL = """
-- ICDEV SaaS Platform -- SQLite Schema (CUI // SP-CTI)

CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE,
    impact_level TEXT NOT NULL DEFAULT 'IL4' CHECK (impact_level IN ('IL2','IL4','IL5','IL6')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','provisioning','active','suspended','deactivated','deleted')),
    tier TEXT NOT NULL DEFAULT 'starter' CHECK (tier IN ('starter','professional','enterprise')),
    db_host TEXT, db_name TEXT, db_port INTEGER DEFAULT 5432,
    k8s_namespace TEXT, aws_account_id TEXT,
    artifact_config TEXT DEFAULT '{}', bedrock_config TEXT DEFAULT '{}',
    idp_config TEXT DEFAULT '{}', settings TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    approved_by TEXT, approved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_tenants_slug ON tenants(slug);
CREATE INDEX IF NOT EXISTS idx_tenants_status ON tenants(status);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    email TEXT NOT NULL, display_name TEXT,
    role TEXT NOT NULL DEFAULT 'developer' CHECK (role IN ('tenant_admin','developer','compliance_officer','auditor','viewer')),
    auth_method TEXT NOT NULL DEFAULT 'api_key' CHECK (auth_method IN ('api_key','oauth','cac_piv')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','deactivated')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_login TEXT,
    UNIQUE (tenant_id, email)
);
CREATE INDEX IF NOT EXISTS idx_users_tenant ON users(tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_hash TEXT NOT NULL UNIQUE, key_prefix TEXT NOT NULL,
    name TEXT NOT NULL, scopes TEXT DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked','expired')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    expires_at TEXT, last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys(tenant_id);

CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tier TEXT NOT NULL DEFAULT 'starter' CHECK (tier IN ('starter','professional','enterprise')),
    max_projects INTEGER NOT NULL DEFAULT 5, max_users INTEGER NOT NULL DEFAULT 3,
    allowed_il_levels TEXT DEFAULT '["IL2","IL4"]', allowed_frameworks TEXT DEFAULT '["nist_800_53"]',
    bedrock_pool_enabled INTEGER DEFAULT 0,
    starts_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    ends_at TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','expired','cancelled')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_tenant ON subscriptions(tenant_id);

CREATE TABLE IF NOT EXISTS usage_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    endpoint TEXT NOT NULL, method TEXT NOT NULL,
    tokens_used INTEGER DEFAULT 0, status_code INTEGER, duration_ms INTEGER,
    metadata TEXT DEFAULT '{}',
    recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_usage_tenant_time ON usage_records(tenant_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_usage_recorded ON usage_records(recorded_at);

CREATE TABLE IF NOT EXISTS audit_platform (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT, user_id TEXT,
    event_type TEXT NOT NULL, action TEXT NOT NULL,
    details TEXT DEFAULT '{}', ip_address TEXT, user_agent TEXT,
    recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_platform(tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_platform(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_platform(recorded_at);

CREATE TRIGGER IF NOT EXISTS trg_audit_no_update
    BEFORE UPDATE ON audit_platform
BEGIN
    SELECT RAISE(ABORT, 'audit_platform is append-only: UPDATE is prohibited');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete
    BEFORE DELETE ON audit_platform
BEGIN
    SELECT RAISE(ABORT, 'audit_platform is append-only: DELETE is prohibited');
END;

CREATE TABLE IF NOT EXISTS rate_limits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    window_start TEXT NOT NULL,
    window_type TEXT NOT NULL CHECK (window_type IN ('minute','hour','day')),
    request_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (tenant_id, window_start, window_type)
);
CREATE INDEX IF NOT EXISTS idx_rate_limits_tenant ON rate_limits(tenant_id, window_start);
"""

# ---------------------------------------------------------------------------
# Expected tables for verification
# ---------------------------------------------------------------------------
EXPECTED_TABLES = [
    "tenants", "users", "api_keys", "subscriptions",
    "usage_records", "audit_platform", "rate_limits",
]


# ---------------------------------------------------------------------------
# Connection Management
# ---------------------------------------------------------------------------
def _get_db_url():
    """Return PLATFORM_DB_URL from environment or None for SQLite fallback."""
    return os.environ.get("PLATFORM_DB_URL")


def _is_postgres():
    """Check if we should use PostgreSQL."""
    url = _get_db_url()
    return url is not None and url.strip() != ""


def get_platform_connection():
    """Get a database connection to the platform database.

    Returns either a psycopg2 connection (PostgreSQL) or sqlite3 connection,
    both configured for dict-like row access.
    """
    if _is_postgres():
        return _get_pg_connection()
    else:
        return _get_sqlite_connection()


def _get_pg_connection():
    """Get PostgreSQL connection with RealDictCursor."""
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        raise ImportError(
            "psycopg2 is required for PostgreSQL. "
            "Install with: pip install psycopg2-binary"
        )
    url = _get_db_url()
    try:
        conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
        conn.autocommit = False
        return conn
    except Exception as exc:
        raise ConnectionError(f"Failed to connect to PostgreSQL: {exc}") from exc


def _get_sqlite_connection():
    """Get SQLite connection with Row factory for dict-like access."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SQLITE_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Schema Initialization
# ---------------------------------------------------------------------------
def init_platform_db(force=False):
    """Initialize the platform database schema.

    Args:
        force: If True, drop existing tables before creating (DESTRUCTIVE).

    Returns:
        dict with status, backend, tables_created, and message.
    """
    backend = "postgresql" if _is_postgres() else "sqlite"
    logger.info("Initializing platform database (backend=%s, force=%s)", backend, force)

    conn = get_platform_connection()
    cursor = conn.cursor()

    try:
        if force:
            _drop_all_tables(cursor, backend)
            conn.commit()
            logger.info("Dropped existing tables (force=True)")

        if backend == "postgresql":
            _init_pg_schema(cursor)
        else:
            _init_sqlite_schema(cursor)

        conn.commit()

        tables = _list_tables(cursor, backend)
        missing = [t for t in EXPECTED_TABLES if t not in tables]

        if missing:
            result = {
                "status": "error", "backend": backend,
                "tables_created": tables, "missing_tables": missing,
                "message": f"Schema incomplete -- missing tables: {missing}",
            }
            logger.error(result["message"])
        else:
            result = {
                "status": "ok", "backend": backend,
                "tables_created": tables, "missing_tables": [],
                "message": f"Platform DB initialized: {len(tables)} tables ({backend})",
            }
            logger.info(result["message"])

        return result
    except Exception as exc:
        conn.rollback()
        logger.error("Schema initialization failed: %s", exc)
        raise
    finally:
        cursor.close()
        conn.close()


def _init_pg_schema(cursor):
    """Execute PostgreSQL schema SQL."""
    cursor.execute(PG_SCHEMA_SQL)


def _init_sqlite_schema(cursor):
    """Execute SQLite schema SQL statement by statement."""
    statements = _split_sql_statements(SQLITE_SCHEMA_SQL)
    for stmt in statements:
        stmt = stmt.strip()
        if stmt:
            cursor.execute(stmt)


def _split_sql_statements(sql):
    """Split SQL text into individual statements, respecting trigger blocks."""
    statements = []
    current = []
    in_trigger = False

    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") and not current:
            continue
        if not stripped and not current:
            continue
        if "CREATE TRIGGER" in stripped.upper():
            in_trigger = True
        current.append(line)
        if in_trigger and stripped.upper() == "END;":
            statements.append("\n".join(current))
            current = []
            in_trigger = False
            continue
        if not in_trigger and stripped.endswith(";"):
            statements.append("\n".join(current))
            current = []

    if current:
        remaining = "\n".join(current).strip()
        if remaining:
            statements.append(remaining)
    return statements


def _drop_all_tables(cursor, backend):
    """Drop all platform tables (for --force reinit)."""
    drop_order = [
        "rate_limits", "audit_platform", "usage_records",
        "subscriptions", "api_keys", "users", "tenants",
    ]
    if backend == "postgresql":
        cursor.execute("DROP TRIGGER IF EXISTS trg_audit_no_update ON audit_platform")
        cursor.execute("DROP FUNCTION IF EXISTS prevent_audit_mutation()")
        for table in drop_order:
            cursor.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    else:
        cursor.execute("DROP TRIGGER IF EXISTS trg_audit_no_update")
        cursor.execute("DROP TRIGGER IF EXISTS trg_audit_no_delete")
        for table in drop_order:
            cursor.execute(f"DROP TABLE IF EXISTS {table}")


def _list_tables(cursor, backend):
    """List all tables in the platform database."""
    if backend == "postgresql":
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        return [row["tablename"] for row in cursor.fetchall()]
    else:
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [row["name"] for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Schema Verification
# ---------------------------------------------------------------------------
def verify_platform_db():
    """Verify that the platform database schema is correct."""
    backend = "postgresql" if _is_postgres() else "sqlite"
    try:
        conn = get_platform_connection()
        cursor = conn.cursor()
        tables = _list_tables(cursor, backend)
        missing = [t for t in EXPECTED_TABLES if t not in tables]
        counts = {}
        for table in tables:
            if table in EXPECTED_TABLES:
                cursor.execute(f"SELECT COUNT(*) as cnt FROM {table}")
                row = cursor.fetchone()
                counts[table] = row["cnt"] if row else 0
        cursor.close()
        conn.close()
        if missing:
            return {
                "status": "incomplete", "backend": backend,
                "tables": tables, "missing": missing,
                "row_counts": counts,
                "message": f"Missing tables: {missing}",
            }
        return {
            "status": "ok", "backend": backend,
            "tables": tables, "missing": [],
            "row_counts": counts,
            "message": f"Schema verified: {len(tables)} tables ({backend})",
        }
    except Exception as exc:
        return {
            "status": "error", "backend": backend,
            "tables": [], "missing": EXPECTED_TABLES,
            "row_counts": {},
            "message": f"Verification failed: {exc}",
        }


# ---------------------------------------------------------------------------
# Connection Info
# ---------------------------------------------------------------------------
def get_connection_info():
    """Return info about which backend is configured."""
    if _is_postgres():
        url = _get_db_url()
        masked = url
        if "@" in url:
            pre_at = url.split("@")[0]
            post_at = url.split("@", 1)[1]
            if ":" in pre_at:
                parts = pre_at.rsplit(":", 1)
                masked = f"{parts[0]}:****@{post_at}"
        return {"backend": "postgresql", "url": masked, "sqlite_path": None}
    else:
        return {"backend": "sqlite", "url": None, "sqlite_path": str(SQLITE_PATH)}


# ---------------------------------------------------------------------------
# Audit Helper (append-only)
# ---------------------------------------------------------------------------
def log_platform_audit(
    event_type,
    action,
    tenant_id=None,
    user_id=None,
    details=None,
    ip_address=None,
    user_agent=None,
):
    """Insert an append-only audit record into audit_platform."""
    conn = get_platform_connection()
    cursor = conn.cursor()
    try:
        details_json = json.dumps(details) if details else "{}"
        if _is_postgres():
            cursor.execute(
                "INSERT INTO audit_platform (tenant_id, user_id, event_type, "
                "action, details, ip_address, user_agent) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (tenant_id, user_id, event_type, action, details_json,
                 ip_address, user_agent),
            )
        else:
            cursor.execute(
                "INSERT INTO audit_platform (tenant_id, user_id, event_type, "
                "action, details, ip_address, user_agent) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tenant_id, user_id, event_type, action, details_json,
                 ip_address, user_agent),
            )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.error("Failed to write audit record: %s", exc)
        raise
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    """CLI entry point for platform database management."""
    parser = argparse.ArgumentParser(
        description="ICDEV SaaS Platform Database Manager (CUI // SP-CTI)",
    )
    parser.add_argument("--init", action="store_true",
                        help="Initialize the platform database schema")
    parser.add_argument("--force", action="store_true",
                        help="Drop existing tables before creating (DESTRUCTIVE)")
    parser.add_argument("--verify", action="store_true",
                        help="Verify schema integrity")
    parser.add_argument("--info", action="store_true",
                        help="Show connection info")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    args = parser.parse_args()

    if not any([args.init, args.verify, args.info]):
        parser.print_help()
        sys.exit(1)

    if args.info:
        info = get_connection_info()
        if args.json:
            print(json.dumps(info, indent=2))
        else:
            print(f"Backend:     {info['backend']}")
            if info["backend"] == "postgresql":
                print(f"URL:         {info['url']}")
            else:
                print(f"SQLite path: {info['sqlite_path']}")
        if not args.init and not args.verify:
            return

    if args.init:
        result = init_platform_db(force=args.force)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"[{result['status'].upper()}] {result['message']}")
            if result.get("tables_created"):
                for t in sorted(result["tables_created"]):
                    print(f"  - {t}")
            if result.get("missing_tables"):
                print("Missing:")
                for t in result["missing_tables"]:
                    print(f"  ! {t}")
        if result["status"] == "error":
            sys.exit(1)

    if args.verify:
        result = verify_platform_db()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"[{result['status'].upper()}] {result['message']}")
            if result.get("row_counts"):
                for t, c in sorted(result["row_counts"].items()):
                    print(f"  - {t}: {c} rows")
            if result.get("missing"):
                print("Missing:")
                for t in result["missing"]:
                    print(f"  ! {t}")
        if result["status"] == "error":
            sys.exit(1)


if __name__ == "__main__":
    main()
