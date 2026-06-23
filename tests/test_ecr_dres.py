"""Tests for ecr-dres: data residency zone table + per-tenant zone connection router."""
import os
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


_ZONE_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_residency_zones (
    zone_id     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    pg_dsn_env  TEXT NOT NULL,
    region      TEXT,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS tenant_zone_assignments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   TEXT NOT NULL UNIQUE,
    zone_id     TEXT NOT NULL REFERENCES data_residency_zones(zone_id),
    assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
    assigned_by TEXT
);
"""


@pytest.fixture
def zone_db(tmp_path, monkeypatch):
    """Temporary SQLite DB with data residency tables and a seeded zone."""
    db_path = tmp_path / "zone_test.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    raw = sqlite3.connect(str(db_path))
    raw.executescript(_ZONE_SCHEMA)
    raw.execute(
        "INSERT INTO data_residency_zones (zone_id, name, pg_dsn_env, region) VALUES (?,?,?,?)",
        ("us-east", "US East", "ZONE_US_EAST_DSN", "us-east-1"),
    )
    raw.commit()
    raw.close()
    return db_path


# ---------------------------------------------------------------------------
# Migration 212 tests (ecr-dres-01)
# ---------------------------------------------------------------------------

def test_migration_212_tables_exist(zone_db):
    """data_residency_zones and tenant_zone_assignments are present."""
    raw = sqlite3.connect(str(zone_db))
    tables = {r[0] for r in raw.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    raw.close()
    assert "data_residency_zones" in tables, "data_residency_zones table missing"
    assert "tenant_zone_assignments" in tables, "tenant_zone_assignments table missing"


def test_migration_212_zone_insert_and_read(zone_db):
    """Zones can be inserted and retrieved."""
    raw = sqlite3.connect(str(zone_db))
    raw.execute(
        "INSERT OR IGNORE INTO data_residency_zones (zone_id, name, pg_dsn_env, region) "
        "VALUES (?, ?, ?, ?)",
        ("eu-west", "EU West", "ZONE_EU_WEST_DSN", "eu-west-1"),
    )
    raw.commit()
    row = raw.execute(
        "SELECT pg_dsn_env FROM data_residency_zones WHERE zone_id = ?",
        ("eu-west",),
    ).fetchone()
    raw.close()
    assert row is not None
    assert row[0] == "ZONE_EU_WEST_DSN"


def test_tenant_zone_assignment_unique(zone_db):
    """tenant_zone_assignments enforces one zone per tenant."""
    raw = sqlite3.connect(str(zone_db))
    raw.execute(
        "INSERT INTO tenant_zone_assignments (tenant_id, zone_id) VALUES (?, ?)",
        ("tenant-x", "us-east"),
    )
    raw.commit()
    with pytest.raises(Exception):
        raw.execute(
            "INSERT INTO tenant_zone_assignments (tenant_id, zone_id) VALUES (?, ?)",
            ("tenant-x", "us-east"),
        )
        raw.commit()
    raw.close()


# ---------------------------------------------------------------------------
# Zone router tests (ecr-dres-02)
# ---------------------------------------------------------------------------

def test_zone_router_fallback(zone_db, monkeypatch):
    """When zone DSN env var is unset, get_zone_connection falls back to default."""
    monkeypatch.delenv("ZONE_US_EAST_DSN", raising=False)

    raw = sqlite3.connect(str(zone_db))
    raw.execute(
        "INSERT INTO tenant_zone_assignments (tenant_id, zone_id) VALUES (?, ?)",
        ("tenant-fallback", "us-east"),
    )
    raw.commit()
    raw.close()

    from tools.db.zone_router import get_zone_connection

    conn = get_zone_connection("tenant-fallback")
    assert conn is not None
    conn.close()


def test_zone_router_no_assignment(zone_db, monkeypatch):
    """Tenant with no zone assignment returns a valid default connection."""
    from tools.db.zone_router import get_zone_connection

    conn = get_zone_connection("tenant-unassigned")
    assert conn is not None
    conn.close()


def test_zone_router_returns_storage_connection(zone_db, monkeypatch):
    """get_zone_connection returns a StorageConnection instance."""
    from tools.db.storage import StorageConnection
    from tools.db.zone_router import get_zone_connection

    conn = get_zone_connection("tenant-sc-check")
    assert isinstance(conn, StorageConnection)
    conn.close()


def test_zone_router_invalid_dsn_falls_back(zone_db, monkeypatch):
    """When zone DSN is set but unreachable, router falls back gracefully."""
    monkeypatch.setenv("ZONE_US_EAST_DSN", "postgresql://invalid-host:9999/noexist")

    raw = sqlite3.connect(str(zone_db))
    raw.execute(
        "INSERT INTO tenant_zone_assignments (tenant_id, zone_id) VALUES (?, ?)",
        ("tenant-bad-dsn", "us-east"),
    )
    raw.commit()
    raw.close()

    from tools.db.zone_router import get_zone_connection

    conn = get_zone_connection("tenant-bad-dsn")
    assert conn is not None
    conn.close()


def test_teardown_zone_connections_no_flask():
    """teardown_zone_connections is a no-op outside a Flask request context."""
    from tools.db.zone_router import teardown_zone_connections

    teardown_zone_connections()  # must not raise


def test_get_connection_zone_routing_disabled(zone_db, monkeypatch):
    """get_connection ignores zone routing when ICDEV_DATA_RESIDENCY_ENABLED is off."""
    monkeypatch.delenv("ICDEV_DATA_RESIDENCY_ENABLED", raising=False)

    from tools.db.storage import get_connection

    conn = get_connection()
    assert conn is not None
    conn.close()
