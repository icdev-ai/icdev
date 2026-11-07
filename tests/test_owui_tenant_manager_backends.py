"""owui-api-07: tools.saas.tenant_manager creates a tenant, a user and a working API key.

Measured 2026-11-07 against throwaway platform databases: create_tenant failed on its
FIRST query on both backends -- sqlite3 rejects the module's ``%s`` placeholders
(``near "%": syntax error``) and a psycopg2 connection has no ``execute``. So the owui E2E
and demo gateways were seeded by hand-written SQL. tenant_manager now goes through
tools.db.storage.StorageConnection and mints UUID ids (platform_db declares every id UUID on
PostgreSQL). This file is the SQLite half; tests/pg_tier/test_tenant_manager_pg.py runs the
same flow against a live PostgreSQL.
"""
import uuid

import pytest

from icdev.tools.saas import platform_db, tenant_manager
from icdev.tools.saas.auth import api_key_auth


@pytest.fixture()
def platform(tmp_path, monkeypatch):
    db = tmp_path / "platform.db"
    monkeypatch.delenv("PLATFORM_DB_URL", raising=False)
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db))
    monkeypatch.setattr(platform_db, "SQLITE_PATH", db)
    monkeypatch.setattr(tenant_manager, "TENANTS_DIR", tmp_path / "tenants")
    # Provisioning's per-tenant schema and canvas seeding are not under test here.
    monkeypatch.setattr(tenant_manager.init_icdev_db, "init_db", lambda path: None)
    from icdev.tools.security import canvas_access
    monkeypatch.setattr(canvas_access, "seed_tenant_defaults", lambda *a, **k: {"granted": 1})
    platform_db.init_platform_db()
    return db


def _is_uuid(value):
    return str(uuid.UUID(str(value))) == str(value)


def test_create_tenant_mints_uuid_ids_and_a_key_that_validates(platform):
    out = tenant_manager.create_tenant("Backends Co", "IL4", "starter", "admin@backends.local")
    assert out["tenant"]["status"] == "active"
    assert all(_is_uuid(out[k]["id"]) for k in ("tenant", "user", "api_key"))

    info = api_key_auth.validate_api_key(out["api_key"]["key"])
    assert info is not None
    assert str(info["tenant_id"]) == out["tenant"]["id"]
    assert str(info["user_id"]) == out["user"]["id"]
    assert info["role"] == "tenant_admin"


def test_add_user_then_list_users(platform):
    tid = tenant_manager.create_tenant("Roster Co", "IL4", "professional", "lead@roster.local")["tenant"]["id"]
    added = tenant_manager.add_user(tid, "viewer@roster.local", "Val Viewer", "viewer")
    assert _is_uuid(added["id"])
    roster = {u["email"]: u["role"] for u in tenant_manager.list_users(tid)}
    assert roster == {"lead@roster.local": "tenant_admin", "viewer@roster.local": "viewer"}


def test_a_revoked_key_no_longer_validates(platform):
    out = tenant_manager.create_tenant("Rotate Co", "IL4", "starter", "admin@rotate.local")
    new = tenant_manager.rotate_api_key(out["api_key"]["id"], out["user"]["id"])
    assert api_key_auth.validate_api_key(out["api_key"]["key"]) is None
    assert api_key_auth.validate_api_key(new["key"])["tenant_id"] == out["tenant"]["id"]
