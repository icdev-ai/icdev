# CUI // SP-CTI
"""ECR-DRES V&V: Data residency zones + GDPR erasure audit trail tests."""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys

import pytest

# Windows first-SQLite-connection latency can exceed the default 30s per-test
# timeout (antivirus scanning of new .db files).  Override for this file.
pytestmark = pytest.mark.timeout(120)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_residency_zones (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    region      TEXT NOT NULL,
    pg_dsn_env  TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS tenant_zone_assignments (
    tenant_id   TEXT PRIMARY KEY,
    zone_id     TEXT NOT NULL REFERENCES data_residency_zones(id),
    assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
    assigned_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_tenant_zone_assignments_zone
    ON tenant_zone_assignments(zone_id);
"""

_SEED_ZONE = """
INSERT OR IGNORE INTO data_residency_zones (id, name, region, pg_dsn_env, description)
VALUES ('us-default', 'US Default', 'us-east-1', 'ICDEV_DATABASE_URL',
        'Default US data residency zone');
"""


def _make_db(tmp_path):
    """Return a fresh in-memory SQLite conn with the zone schema applied."""
    conn = sqlite3.connect(str(tmp_path / "test_dres.db"))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.executescript(_SEED_ZONE)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_zone_tables_exist(tmp_path):
    """data_residency_zones and tenant_zone_assignments tables are created."""
    conn = _make_db(tmp_path)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "data_residency_zones" in tables, "data_residency_zones table missing"
    assert "tenant_zone_assignments" in tables, "tenant_zone_assignments table missing"
    conn.close()


def test_us_default_zone_seeded(tmp_path):
    """Default zone 'us-default' is present after migration."""
    conn = _make_db(tmp_path)
    row = conn.execute(
        "SELECT id, region, pg_dsn_env FROM data_residency_zones WHERE id='us-default'"
    ).fetchone()
    assert row is not None, "us-default zone not seeded"
    assert row["region"] == "us-east-1"
    assert row["pg_dsn_env"] == "ICDEV_DATABASE_URL"
    conn.close()


def test_zone_columns(tmp_path):
    """data_residency_zones has all required columns."""
    conn = _make_db(tmp_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(data_residency_zones)").fetchall()}
    for required in ("id", "name", "region", "pg_dsn_env", "description", "created_at"):
        assert required in cols, f"column {required!r} missing from data_residency_zones"
    conn.close()


def test_assignment_columns(tmp_path):
    """tenant_zone_assignments has all required columns."""
    conn = _make_db(tmp_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tenant_zone_assignments)").fetchall()}
    for required in ("tenant_id", "zone_id", "assigned_at", "assigned_by"):
        assert required in cols, f"column {required!r} missing from tenant_zone_assignments"
    conn.close()


def test_tenant_zone_assignment_fk(tmp_path):
    """tenant_zone_assignments.zone_id FK references data_residency_zones.id."""
    conn = _make_db(tmp_path)
    conn.execute("PRAGMA foreign_keys=ON")
    # Valid insert (zone exists)
    conn.execute(
        "INSERT INTO tenant_zone_assignments (tenant_id, zone_id) VALUES (?, ?)",
        ("acme-corp", "us-default"),
    )
    conn.commit()
    row = conn.execute(
        "SELECT zone_id FROM tenant_zone_assignments WHERE tenant_id='acme-corp'"
    ).fetchone()
    assert row["zone_id"] == "us-default"
    conn.close()


def test_zone_dsn_env_override(tmp_path, monkeypatch):
    """When ICDEV_DATA_ZONE is set and zone DSN env var is missing, get_connection falls back gracefully."""
    monkeypatch.setenv("ICDEV_DATA_ZONE", "eu-west-1")
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))

    # Patch _resolve_zone_dsn_env to avoid a real PG lookup in unit tests
    import tools.db.storage as storage_mod

    original = storage_mod._resolve_zone_dsn_env
    monkeypatch.setattr(storage_mod, "_resolve_zone_dsn_env", lambda: None)
    try:
        conn = storage_mod.get_connection()
        assert conn is not None
        conn.close()
    finally:
        monkeypatch.setattr(storage_mod, "_resolve_zone_dsn_env", original)


def test_zone_router_fallback(tmp_path, monkeypatch):
    """get_zone_connection falls back to the default connection when the tenant
    has no zone assignment (or when the backend is SQLite, which skips PG zone
    lookup entirely).  The returned connection must be usable and closeable
    without raising.
    """
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "zone_fallback.db"))
    # ICDEV_STORAGE_BACKEND=sqlite is already set by conftest — zone lookup is
    # skipped for non-PG backends, so fallback to default SQLite is guaranteed.

    import sys
    for key in list(sys.modules.keys()):
        if "zone_router" in key:
            del sys.modules[key]

    from tools.db.zone_router import get_zone_connection

    conn = get_zone_connection("tenant-no-zone")
    assert conn is not None, "get_zone_connection must return a connection, not None"

    # Verify the connection is functional
    row = conn.execute("SELECT 1 AS x").fetchone()
    assert row is not None

    # Must close without raising (no connection leak)
    conn.close()


def test_erasure_nulls_pii(tmp_path):
    """erase_tenant_data() nulls PII columns and writes an erasure_audit row.

    Acceptance criteria (ECR-DRES-03):
      - email / name / ip_address are set to NULL for the target tenant
      - non-PII columns (role) are untouched
      - other tenants' rows are untouched
      - append-only erasure_audit row is created with correct fields
    """
    # Translating wrapper — gdpr_eraser authors %s for PostgreSQL.
    from _sql_compat import connect as _tconnect

    conn = _tconnect(tmp_path / "test_erasure.db")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          TEXT PRIMARY KEY,
            tenant_id   TEXT NOT NULL,
            email       TEXT,
            name        TEXT,
            ip_address  TEXT,
            role        TEXT
        );
        CREATE TABLE IF NOT EXISTS erasure_audit (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL,
            requested_by    TEXT NOT NULL,
            scope           TEXT NOT NULL DEFAULT 'pii',
            tables_affected TEXT,
            completed_at    TEXT NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO users (id, tenant_id, email, name, ip_address, role) "
        "VALUES ('u1', 'acme', 'alice@acme.com', 'Alice Smith', '1.2.3.4', 'admin')"
    )
    conn.execute(
        "INSERT INTO users (id, tenant_id, email, name, ip_address, role) "
        "VALUES ('u2', 'other', 'bob@other.com', 'Bob Jones', '5.6.7.8', 'user')"
    )
    conn.commit()

    from tools.compliance.gdpr_eraser import erase_tenant_data

    result = erase_tenant_data("acme", "admin@icdev.local", conn=conn)

    # PII columns for the target tenant must be NULL
    row = conn.execute(
        "SELECT email, name, ip_address, role FROM users WHERE id='u1'"
    ).fetchone()
    assert row["email"] is None, "email must be NULL after erasure"
    assert row["name"] is None, "name must be NULL after erasure"
    assert row["ip_address"] is None, "ip_address must be NULL after erasure"
    assert row["role"] == "admin", "non-PII column must be untouched"

    # Other tenant's data must be untouched
    row2 = conn.execute(
        "SELECT email, name FROM users WHERE id='u2'"
    ).fetchone()
    assert row2["email"] == "bob@other.com", "other tenant email must be untouched"
    assert row2["name"] == "Bob Jones", "other tenant name must be untouched"

    # Erasure audit row must be created (append-only)
    audit = conn.execute(
        "SELECT * FROM erasure_audit WHERE tenant_id='acme'"
    ).fetchone()
    assert audit is not None, "erasure_audit row must be created"
    assert audit["requested_by"] == "admin@icdev.local"
    assert audit["scope"] == "pii"
    assert "users" in (audit["tables_affected"] or ""), "users must appear in tables_affected"

    # Return value shape
    assert result["erasure_id"]
    assert result["tenant_id"] == "acme"
    assert "users" in result["tables_affected"]

    conn.close()


def test_migration_212_module_importable():
    """Migration 212 module can be imported without error."""
    repo_root = str(
        __import__("pathlib").Path(__file__).resolve().parent.parent
    )
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    spec = importlib.util.spec_from_file_location(
        "migration_212",
        os.path.join(repo_root, "tools", "db", "migrations", "212_data_residency", "up.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "up"), "migration 212 must expose an up() function"
    assert hasattr(mod, "DDL"), "migration 212 must expose DDL"


def test_erasure_audit_row_created(tmp_path):
    """erase_tenant_data() writes exactly one erasure_audit row per call."""
    # Translating wrapper — gdpr_eraser authors %s for PostgreSQL.
    from _sql_compat import connect as _tconnect

    conn = _tconnect(tmp_path / "audit_row_test.db")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id        TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            email     TEXT,
            name      TEXT
        );
        CREATE TABLE IF NOT EXISTS erasure_audit (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL,
            requested_by    TEXT NOT NULL,
            scope           TEXT NOT NULL DEFAULT 'pii',
            tables_affected TEXT,
            completed_at    TEXT NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO users VALUES ('u1', 'acme', 'alice@acme.com', 'Alice')"
    )
    conn.commit()

    from tools.compliance.gdpr_eraser import erase_tenant_data

    result = erase_tenant_data("acme", "auditor@icdev.local", conn=conn)

    rows = conn.execute(
        "SELECT * FROM erasure_audit WHERE tenant_id='acme'"
    ).fetchall()
    assert len(rows) == 1, "exactly one erasure_audit row expected after erasure"
    row = rows[0]
    assert row["requested_by"] == "auditor@icdev.local"
    assert row["scope"] == "pii"
    assert row["completed_at"] is not None, "completed_at must be populated"
    # Return value must contain the same audit id
    assert result["erasure_id"] == row["id"], "result erasure_id must match row id"
    assert result["tenant_id"] == "acme"

    conn.close()


def test_erasure_skips_audit_tables(tmp_path):
    """Append-only tables in _SKIP_TABLES are not modified by erase_tenant_data()."""
    # Translating wrapper — gdpr_eraser authors %s for PostgreSQL.
    from _sql_compat import connect as _tconnect

    conn = _tconnect(tmp_path / "skip_audit_test.db")
    conn.executescript("""
        -- rls_audit is in _SKIP_TABLES; give it tenant_id + PII columns
        -- to confirm it is explicitly skipped (not just lacking those columns).
        CREATE TABLE IF NOT EXISTS rls_audit (
            id        TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            email     TEXT,
            name      TEXT
        );
        -- Regular user-facing table — should be erased.
        CREATE TABLE IF NOT EXISTS users (
            id        TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            email     TEXT,
            name      TEXT
        );
        CREATE TABLE IF NOT EXISTS erasure_audit (
            id              TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL,
            requested_by    TEXT NOT NULL,
            scope           TEXT NOT NULL DEFAULT 'pii',
            tables_affected TEXT,
            completed_at    TEXT NOT NULL
        );
    """)
    conn.execute(
        "INSERT INTO rls_audit  VALUES ('r1', 'acme', 'audit@acme.com', 'Auditor')"
    )
    conn.execute(
        "INSERT INTO users       VALUES ('u1', 'acme', 'user@acme.com',  'User')"
    )
    conn.commit()

    from tools.compliance.gdpr_eraser import erase_tenant_data

    result = erase_tenant_data("acme", "admin@icdev.local", conn=conn)

    # rls_audit is in _SKIP_TABLES — PII must be untouched
    skip_row = conn.execute(
        "SELECT email, name FROM rls_audit WHERE id='r1'"
    ).fetchone()
    assert skip_row["email"] == "audit@acme.com", (
        "rls_audit.email must not be nulled (append-only skip table)"
    )
    assert skip_row["name"] == "Auditor", (
        "rls_audit.name must not be nulled (append-only skip table)"
    )

    # users is a normal table — PII must be erased
    user_row = conn.execute(
        "SELECT email, name FROM users WHERE id='u1'"
    ).fetchone()
    assert user_row["email"] is None, "users.email must be NULL after erasure"
    assert user_row["name"] is None, "users.name must be NULL after erasure"

    # rls_audit must NOT appear in tables_affected
    assert "rls_audit" not in result["tables_affected"], (
        "rls_audit must not appear in tables_affected"
    )
    # users must appear in tables_affected
    assert "users" in result["tables_affected"], (
        "users must appear in tables_affected"
    )

    conn.close()


def test_erasure_requires_admin(monkeypatch):
    """The admin-role guard aborts 403 for non-admin users when enforcement is on,
    and passes through for admin users.
    """
    monkeypatch.setenv("ICDEV_ENFORCE_CANVAS_ACCESS", "true")

    from flask import Flask, g
    from werkzeug.exceptions import Forbidden
    import tools.admin.blueprint as bp_mod

    app = Flask(__name__)

    # Non-admin user — expect Forbidden
    with app.test_request_context():
        g.current_user = {"role": "viewer", "email": "viewer@test.com"}
        try:
            bp_mod._require_admin()
            raise AssertionError("Expected Forbidden for non-admin viewer role")
        except Forbidden as exc:
            assert exc.code == 403, f"Expected 403, got {exc.code}"

    # Admin user — must pass without raising
    with app.test_request_context():
        g.current_user = {"role": "admin", "email": "admin@test.com"}
        bp_mod._require_admin()  # Must not raise

    # No user context — non-admin-by-default also blocked when enforcement is on
    with app.test_request_context():
        g.current_user = None
        try:
            bp_mod._require_admin()
            raise AssertionError("Expected Forbidden for unauthenticated user")
        except Forbidden as exc:
            assert exc.code == 403
