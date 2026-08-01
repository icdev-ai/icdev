# CUI // SP-CTI
"""Tests for the Migration Intelligence Engine (cnr-mi-01, cnr-mi-02).

cnr-mi-01 — DB layer repair: every write/param-read used ``%s`` while the
connection was a raw qmark sqlite3 handle, so goal/wishlist/scan writes raised
OperationalError. Routing through StorageConnection (get_connection) fixes the
placeholder mismatch on both SQLite and PostgreSQL. These tests exercise the
real DB writes that were previously broken.

cnr-mi-02 — auth: the blueprint had no auth on any route, including the pipeline
POST endpoints. A blueprint-wide before_request now returns 401 for unauth
API/mutating requests. These tests assert the gate.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# DB-write coverage (cnr-mi-01) — SQLite + PostgreSQL
# ---------------------------------------------------------------------------

def _exercise_db_writes(db_path: str | None) -> None:
    """Create a goal (+ update), a wishlist entry, and run a scan — all writes
    that raised OperationalError before the placeholder fix. Asserts they land."""
    from tools.migration_intelligence import goal_manager, opportunity_scanner
    from tools.migration_intelligence.db.init_db import get_connection, init_db

    init_db(db_path)

    # Goal create + list + update + get
    created = goal_manager.create_goal(
        title="Modernize primary datacenter",
        description="Refresh EOL compute and consolidate racks",
        category="infrastructure",
        priority="high",
        db_path=db_path,
    )
    goal_id = created["id"]
    assert goal_id.startswith("goal-")

    goals = goal_manager.list_goals(status="active", db_path=db_path)
    assert any(g["id"] == goal_id for g in goals)

    assert goal_manager.update_goal(goal_id, {"priority": "critical", "tags": ["dc", "eol"]}, db_path=db_path)
    fetched = goal_manager.get_goal(goal_id, db_path=db_path)
    assert fetched is not None and fetched["priority"] == "critical"

    # Wishlist insert (same INSERT the blueprint POST /wishlist route uses)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO mi_wishlist (id, title, item_type, status, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            ("wl-cnr-1", "Core switch refresh", "network_equipment", "wishlist", "2026-01-01", "2026-01-01"),
        )
        conn.commit()
        wl_count = conn.execute(
            "SELECT COUNT(*) AS c FROM mi_wishlist WHERE id=%s", ("wl-cnr-1",)
        ).fetchone()["c"]
        assert wl_count == 1
    finally:
        conn.close()

    # Full scan writes a mi_scans row and completes without raising.
    scan = opportunity_scanner.run_full_scan(db_path=db_path)
    assert scan["scan_id"].startswith("scan-")
    assert "total_found" in scan

    conn = get_connection(db_path)
    try:
        scans = conn.execute(
            "SELECT status FROM mi_scans WHERE id=%s", (scan["scan_id"],)
        ).fetchone()
        assert scans is not None and scans["status"] == "completed"
    finally:
        conn.close()


def test_mi_db_writes_sqlite(tmp_path, monkeypatch):
    """Goal + wishlist + scan writes on the SQLite backend (default suite backend)."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db_path = str(tmp_path / "migration_intel.db")
    monkeypatch.setenv("MI_DB_PATH", db_path)
    _exercise_db_writes(db_path)


def _pg_available() -> bool:
    try:
        from tools.db.storage import _get_pg_connection  # noqa: PLC0415
        conn = _get_pg_connection(None)
    except Exception:
        return False
    try:
        conn.close()
    except Exception:
        pass
    return True


@pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL not reachable (ICDEV_PG_*/ICDEV_DATABASE_URL) — PG-primary write path needs a live PG",
)
def test_mi_db_writes_postgresql(monkeypatch):
    """Same writes on the PostgreSQL backend (shared icdev db, mi_* namespaced)."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")
    monkeypatch.setenv("MI_STORAGE_BACKEND", "postgresql")
    _exercise_db_writes(None)


# ---------------------------------------------------------------------------
# Auth coverage (cnr-mi-02)
# ---------------------------------------------------------------------------

@pytest.fixture
def mi_client(tmp_path, monkeypatch):
    """Minimal Flask app with only the migration_intel blueprint registered.

    Isolates the blueprint's own before_request auth from the dashboard's global
    auth hook, so a 401 here proves the blueprint gate — the defect fixed by
    cnr-mi-02 — is present rather than the app-wide guard.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("ICDEV_AUTH_BYPASS", raising=False)

    import tools.migration_intelligence.blueprint as mi_bp
    monkeypatch.setattr(mi_bp, "_DB_PATH", str(tmp_path / "migration_intel.db"))

    from flask import Flask
    app = Flask(__name__)
    app.secret_key = "cnr-mi-test"
    app.register_blueprint(mi_bp.create_migration_intel_blueprint())
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


@pytest.mark.parametrize("path", [
    "/api/migration-intel/run",
    "/api/migration-intel/scan",
])
def test_unauth_pipeline_post_returns_401(mi_client, path):
    resp = mi_client.post(path)
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "Authentication required"


def test_unauth_api_get_returns_401(mi_client):
    resp = mi_client.get("/api/migration-intel/goals")
    assert resp.status_code == 401


def test_unauth_page_redirects_to_login(mi_client):
    resp = mi_client.get("/migration-intel/")
    assert resp.status_code in (301, 302)
    assert "/login" in resp.headers.get("Location", "")


def test_authed_session_passes_gate(mi_client):
    with mi_client.session_transaction() as sess:
        sess["user_id"] = "test-admin"
    resp = mi_client.get("/api/migration-intel/goals")
    assert resp.status_code == 200
    assert "goals" in resp.get_json()


def test_auth_bypass_env_allows_access(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_AUTH_BYPASS", "true")
    import tools.migration_intelligence.blueprint as mi_bp
    monkeypatch.setattr(mi_bp, "_DB_PATH", str(tmp_path / "migration_intel.db"))
    from flask import Flask
    app = Flask(__name__)
    app.secret_key = "cnr-mi-test"
    app.register_blueprint(mi_bp.create_migration_intel_blueprint())
    with app.test_client() as client:
        assert client.get("/api/migration-intel/goals").status_code == 200
