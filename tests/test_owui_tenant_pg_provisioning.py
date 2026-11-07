"""owui-api-08: on a PostgreSQL platform a tenant gets its OWN PostgreSQL database, or stays provisioning.

Measured 2026-11-07 (owui-api-07, #2550): tools.saas.tenant_manager.provision_tenant always ran
init_icdev_db.init_db(<slug>.db) -- which REFUSES on PostgreSQL ("This monolithic init script is
SQLite-only") -- recorded db_host 'localhost-sqlite', and marked the tenant ACTIVE anyway, so
tools.saas.tenant_db_adapter routed it to a SQLite file that was never created. The adapter's
PostgreSQL branch also connected with no credentials. This file pins the decision logic with
the database creation faked; tests/pg_tier/test_tenant_manager_pg.py runs it for real.
"""
import pytest

from icdev.tools.saas import platform_db, tenant_db_adapter, tenant_manager

FAKE_PG = {"db_host": "pg.internal", "db_port": 6543, "db_name": "icdev_tenant_fake"}


@pytest.fixture()
def platform(tmp_path, monkeypatch):
    db = tmp_path / "platform.db"
    monkeypatch.delenv("PLATFORM_DB_URL", raising=False)
    monkeypatch.setenv("PLATFORM_DB_PATH", str(db))
    monkeypatch.setattr(platform_db, "SQLITE_PATH", db)
    monkeypatch.setattr(tenant_manager, "TENANTS_DIR", tmp_path / "tenants")
    from icdev.tools.security import canvas_access
    monkeypatch.setattr(canvas_access, "seed_tenant_defaults", lambda *a, **k: {"granted": 1})
    platform_db.init_platform_db()
    # The tenants live on PostgreSQL; the SQLite initialiser must never be reached.
    monkeypatch.setattr(tenant_manager, "_tenant_db_on_postgres", lambda: True)
    monkeypatch.setattr(tenant_manager.init_icdev_db, "init_db",
                        lambda path: pytest.fail("init_icdev_db.init_db called for a PostgreSQL tenant"))
    return db


def _status(tenant_id):
    conn = tenant_manager._platform_conn()
    try:
        return dict(conn.execute("SELECT status, db_host, db_name, db_port FROM tenants WHERE id = %s",
                                 (tenant_id,)).fetchone())
    finally:
        conn.close()


def test_a_pg_tenant_is_active_only_with_its_own_database_recorded(platform, monkeypatch):
    made = []
    monkeypatch.setattr(tenant_manager, "_create_pg_tenant_db", lambda slug: made.append(slug) or dict(FAKE_PG))
    out = tenant_manager.create_tenant("Prov Co", "IL4", "starter", "admin@prov.local")
    assert made == ["prov-co"]
    assert out["tenant"]["status"] == "active"
    assert _status(out["tenant"]["id"]) == {"status": "active", "db_host": "pg.internal",
                                           "db_name": "icdev_tenant_fake", "db_port": 6543}


def test_a_failed_pg_database_leaves_the_tenant_provisioning_and_says_why(platform, monkeypatch):
    def boom(slug):
        raise RuntimeError("tenant database icdev_tenant_fail_co: CREATE DATABASE failed: permission denied")

    monkeypatch.setattr(tenant_manager, "_create_pg_tenant_db", boom)
    with pytest.raises(RuntimeError, match="CREATE DATABASE failed: permission denied"):
        tenant_manager.create_tenant("Fail Co", "IL4", "starter", "admin@fail.local")
    rows = [t for t in tenant_manager.list_tenants() if t["slug"] == "fail-co"]
    assert len(rows) == 1 and rows[0]["status"] == "provisioning"


def test_tenant_db_names_are_postgres_identifiers():
    name = tenant_manager._pg_tenant_db_name("Acme-Defense.Co " + "x" * 80)
    assert name.startswith("icdev_tenant_acme_defense_co_") and len(name) <= 63
    assert set(name) <= set("abcdefghijklmnopqrstuvwxyz0123456789_")


def test_the_adapter_reaches_a_pg_tenant_with_the_platform_credentials(monkeypatch):
    import psycopg2

    seen = {}

    class _Conn:
        cursor_factory = None

    monkeypatch.setenv("PLATFORM_DB_URL", "postgresql://plat:s3cret@pg.internal:5432/platform")
    monkeypatch.setattr(tenant_db_adapter, "_get_tenant_config",
                        lambda tid: {"slug": "prov-co", **FAKE_PG, "status": "active"})
    monkeypatch.setattr(psycopg2, "connect", lambda *a, **k: seen.update(k) or _Conn())
    tenant_db_adapter.get_tenant_db_connection("t-1")
    assert {k: seen.get(k) for k in ("host", "port", "dbname", "user", "password")} == {
        "host": "pg.internal", "port": 6543, "dbname": "icdev_tenant_fake", "user": "plat", "password": "s3cret"}
    assert tenant_db_adapter._is_sqlite_host(FAKE_PG["db_host"]) is False
