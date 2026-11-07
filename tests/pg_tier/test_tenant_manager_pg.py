# CUI // SP-CTI
"""owui-api-07: tools.saas.tenant_manager on a LIVE PostgreSQL platform database.

The PostgreSQL half of tests/test_owui_tenant_manager_backends.py. Measured 2026-11-07
against a live PostgreSQL 16 at the merge base: create_tenant raised on its first query,
``'psycopg2.extensions.connection' object has no attribute 'execute'`` -- and behind that,
"tenant-xxxx" ids that platform_db's UUID columns would refuse. No SQLite run can reach
either: sqlite3 connections do have ``execute`` and TEXT ids take any string.

owui-api-08: provisioning on this platform now creates the tenant's OWN PostgreSQL database
(``icdev_tenant_<slug>``) and bootstraps the ICDEV schema into it; the last test runs that for
real, with nothing stubbed. At the merge base it ran the SQLite-only init_db, which refuses on
PostgreSQL, and marked the tenant active against a file that was never created.

Isolation: the module creates its OWN throwaway platform database (``owui_api07_<hex>``),
points PLATFORM_DB_URL and ICDEV_PG_DATABASE at it (the gateway's API-key lookup reads the
platform tables through tools.db.storage), and drops it afterwards -- together with exactly the
tenant databases provisioning was asked to create, which the fixture records by name.

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
    tenant_dbs: list[str] = []
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
        monkeypatch.setattr(tenant_manager.init_icdev_db, "init_db",
                            lambda path: pytest.fail("SQLite init_db reached on a PostgreSQL platform"))
        monkeypatch.setattr(canvas_access, "seed_tenant_defaults", lambda *a, **k: {"granted": 1})
        real_create = getattr(tenant_manager, "_create_pg_tenant_db", None)
        if real_create is not None:  # absent at the merge base, whose provisioning never made one
            def recording_create(slug):
                tenant_dbs.append(tenant_manager._pg_tenant_db_name(slug))
                return real_create(slug)

            monkeypatch.setattr(tenant_manager, "_create_pg_tenant_db", recording_create)
        platform_db.init_platform_db()
        yield tenant_manager
    finally:
        if storage._pg_pool is not None and storage._pg_pool is not original_pool:
            storage._pg_pool.closeall()
        cur = admin.cursor()
        for db in [name, *tenant_dbs]:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()", (db,))
            cur.execute(f'DROP DATABASE IF EXISTS "{db}"')
        admin.close()


def _is_uuid(value):
    return str(uuid.UUID(str(value))) == str(value)


def test_create_tenant_add_user_and_validate_key_on_postgres(platform, monkeypatch):
    from tools.saas.auth import api_key_auth

    tm = platform
    # The tenant's own database is the next test's subject; keep this one about the platform rows.
    monkeypatch.setattr(tm, "_create_pg_tenant_db",
                        lambda slug: {"db_host": "pg.internal", "db_port": 5432, "db_name": "icdev_tenant_stub"},
                        raising=False)
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


def test_provisioning_gives_a_pg_tenant_its_own_migrated_database(platform):
    from tools.saas import tenant_db_adapter

    tm = platform
    name = "Own DB " + uuid.uuid4().hex[:6]
    out = tm.create_tenant(name, "IL4", "starter", "admin@owndb.local")
    tid = out["tenant"]["id"]
    expected = tm._pg_tenant_db_name(tm._slugify(name))
    assert out["tenant"]["status"] == "active"
    assert out["tenant"]["db_name"] == expected

    tenant_db_adapter._cache_clear()
    assert tenant_db_adapter.get_tenant_db_path(tid) is None          # not a SQLite tenant
    conn = tenant_db_adapter.get_tenant_db_connection(tid)
    try:
        cur = conn.cursor()
        cur.execute("SELECT current_database() AS db, to_regclass('public.projects') AS projects, "
                    "to_regclass('public.schema_migrations') AS migrations")
        row = cur.fetchone()
        assert row["db"] == expected
        assert row["projects"] and row["migrations"], "the ICDEV schema was not loaded"
    finally:
        conn.close()
