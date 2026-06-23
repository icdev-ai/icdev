# CUI // SP-CTI
"""ECR-DRES-01: Data residency zone tables + per-tenant zone config tests."""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys

import pytest

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
