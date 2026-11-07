# CUI // SP-CTI
"""owui-api-07: tools.saas.tenant_manager on a LIVE PostgreSQL platform database.

The PostgreSQL half of tests/test_owui_tenant_manager_backends.py. Measured 2026-11-07
against a live PostgreSQL 16 at the merge base: create_tenant raised on its first query,
``'psycopg2.extensions.connection' object has no attribute 'execute'`` -- and behind that,
"tenant-xxxx" ids that platform_db's UUID columns would refuse. No SQLite run can reach
either: sqlite3 connections do have ``execute`` and TEXT ids take any string.

Isolation: the module creates its OWN throwaway database (``owui_api07_<hex>``), points
PLATFORM_DB_URL and ICDEV_PG_DATABASE at it (the gateway's API-key lookup reads the
platform tables through tools.db.storage), and drops it afterwards.

Excluded from CI gating and run by Test (PostgreSQL) via tests/pg_tier_allowlist.txt, for
the reason recorded against it in args/test_gating_gate.yaml.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ICDEV_PYTEST_PG", "").lower() not in ("1", "true", "yes"),
    reason="PG tier only — set ICDEV_PYTEST_PG=1 with a live PostgreSQL service",
)


def _admin_kwargs() -> dict:
    import tools.db.storage  # noqa: F401 — loads .env for a workstation run

    return dict(
        host=os.environ.get("ICDEV_PG_HOST", "localhost"),
        port=int(os.environ.get("ICDEV_PG_PORT", "5432")),
        user=os.environ.get("ICDEV_PG_USER", "icdev"),
        password=os.environ.get("ICDEV_PG_PASSWORD", ""),
        dbname=os.environ.get("ICDEV_PG_DATABASE", "icdev"),
        connect_timeout=10,
    )


@pytest.fixture()
def platform(tmp_path, monkeypatch):
    import psycopg2

    from tools.db import storage

    kw = _admin_kwargs()
    original_pool = storage._pg_pool
    name ="owui_api07_" + uuid.uuid4().hex[:10]
    admin = psycopg2.connect(**kw)
    admin.autocommit = True
    admin.cursor().execute(f'CREATE DATABASE "{name}"')
    try:
        url = "postgresql://{user}:{password}@{host}:{port}/".format(**kw) + name
        monkeypatch.setenv("PLATFORM_DB_URL", url)
        monkeypatch.setenv("ICDEV_PG_DATABASE", name)
        monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")
        # storage keeps ONE process-wide pool bound to the first database it reached;
        # give this test its own, and hand the original back untouched afterwards.
        monkeypatch.setattr(storage, "_pg_pool", None)

        from tools.saas import platform_db, tenant_manager
        from tools.security import canvas_access

        monkeypatch.setattr(tenant_manager, "TENANTS_DIR", tmp_path / "tenants")
        # Provisioning's per-tenant schema and canvas seeding are not under test here.
        monkeypatch.setattr(tenant_manager.init_icdev_db, "init_db", lambda path: None)
        monkeypatch.setattr(canvas_access, "seed_tenant_defaults", lambda *a, **k: {"granted": 1})
        platform_db.init_platform_db()
        yield tenant_manager
    finally:
        if storage._pg_pool is not None and storage._pg_pool is not original_pool:
            storage._pg_pool.closeall()
        cur = admin.cursor()
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
        cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


def _is_uuid(value):
    return str(uuid.UUID(str(value))) == str(value)


def test_create_tenant_add_user_and_validate_key_on_postgres(platform):
    from tools.saas.auth import api_key_auth

    tm = platform
    out = tm.create_tenant("PG Co", "IL4", "starter", "admin@pg.local")
    assert out["tenant"]["status"] == "active"
    assert all(_is_uuid(out[k]["id"]) for k in ("tenant", "user", "api_key"))

    added = tm.add_user(out["tenant"]["id"], "viewer@pg.local", "Val Viewer", "viewer")
    assert _is_uuid(added["id"])
    roster = {u["email"]: u["role"] for u in tm.list_users(out["tenant"]["id"])}
    assert roster == {"admin@pg.local": "tenant_admin", "viewer@pg.local": "viewer"}

    info = api_key_auth.validate_api_key(out["api_key"]["key"])
    assert info is not None and info["role"] == "tenant_admin"
    assert str(info["tenant_id"]) == out["tenant"]["id"]
