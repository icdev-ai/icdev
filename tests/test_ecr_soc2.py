# CUI // SP-CTI
"""Tests for ECR-SOC2: evidence_items table, collector, exporter, admin evidence tab."""
from __future__ import annotations

import contextlib
import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Fixture helpers (reuse SSO pattern — sqlite factory)
# ---------------------------------------------------------------------------

def _sqlite_conn_factory(db_path):
    def _factory():
        # Must translate: soc2_collector and the admin evidence route author
        # PostgreSQL-style %s placeholders, which a bare sqlite3 connection
        # rejects with `near "%": syntax error`. See tests/_sql_compat.
        from _sql_compat import connect as _tconnect

        conn = _tconnect(db_path, check_same_thread=False)
        return contextlib.closing(conn)
    return _factory


@pytest.fixture
def _soc2_db(icdev_db, monkeypatch):
    from tools.db import storage
    monkeypatch.setattr(storage, "get_connection", _sqlite_conn_factory(icdev_db))
    return icdev_db


# ---------------------------------------------------------------------------
# ecr-soc2-01: evidence table exists and is append-only
# ---------------------------------------------------------------------------

def test_evidence_table_exists(_soc2_db):
    """evidence_items table is created by conftest schema."""
    from tools.db import storage

    with storage.get_connection() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='evidence_items'"
        ).fetchone()
    assert row is not None, "evidence_items table not found in schema"


def test_evidence_table_schema(_soc2_db):
    """evidence_items has required columns including control_id and tenant_id."""
    from tools.db import storage

    with storage.get_connection() as conn:
        cols = conn.execute("PRAGMA table_info(evidence_items)").fetchall()
    col_names = {c[1] for c in cols}
    required = {"id", "control_id", "framework", "tenant_id", "evidence_type",
                "source_table", "source_row_id", "summary", "collected_at", "collector"}
    missing = required - col_names
    assert not missing, f"evidence_items missing columns: {missing}"


# ---------------------------------------------------------------------------
# ecr-soc2-02: collector populates evidence rows
# ---------------------------------------------------------------------------

def test_collector_populates_evidence(_soc2_db, monkeypatch):
    """collect_evidence inserts rows when audit_trail has matching events."""
    from tools.db import storage
    from tools.compliance.soc2_collector import collect_evidence

    tid = f"tenant-{uuid.uuid4().hex[:8]}"

    # Seed audit_trail with a login_success event (maps to CC6.1)
    with storage.get_connection() as conn:
        conn.execute(
            "INSERT INTO audit_trail (id, tenant_id, action, resource, recorded_at) "
            "VALUES (?,?,?,?,datetime('now'))",
            (str(uuid.uuid4()), tid, "login_success", "auth", ),
        )
        conn.commit()

    result = collect_evidence(tid)
    assert result["collected"] >= 1
    assert "CC6.1" in result["controls_covered"]

    # Verify row persisted
    with storage.get_connection() as conn:
        row = conn.execute(
            "SELECT control_id FROM evidence_items WHERE tenant_id = ?", (tid,)
        ).fetchone()
    assert row is not None
    assert row[0] == "CC6.1"


def test_collector_idempotent(_soc2_db, monkeypatch):
    """Running collect_evidence twice skips duplicate source rows."""
    from tools.db import storage
    from tools.compliance.soc2_collector import collect_evidence

    tid = f"idem-{uuid.uuid4().hex[:8]}"
    audit_id = str(uuid.uuid4())

    with storage.get_connection() as conn:
        conn.execute(
            "INSERT INTO audit_trail (id, tenant_id, action, resource, recorded_at) "
            "VALUES (?,?,?,?,datetime('now'))",
            (audit_id, tid, "login_success", "auth"),
        )
        conn.commit()

    r1 = collect_evidence(tid)
    r2 = collect_evidence(tid)

    # Second run should skip all previously inserted rows
    assert r2["collected"] == 0 or r2["skipped"] >= r1["collected"]


# ---------------------------------------------------------------------------
# ecr-soc2-03/04: export produces valid HTML/JSON
# ---------------------------------------------------------------------------

def test_export_html_valid(_soc2_db, tmp_path, monkeypatch):
    """export_report generates a non-empty HTML file with SOC 2 content."""
    from tools.compliance.soc2_exporter import export_report

    tid = f"export-{uuid.uuid4().hex[:8]}"
    out = tmp_path / "report.html"
    data = export_report(tid, out, fmt="html")

    assert out.exists(), "HTML report file was not created"
    html = out.read_text(encoding="utf-8")
    assert "<html" in html.lower(), "Output is not HTML"
    assert "SOC 2" in html or "soc2" in html.lower()
    assert data["summary"]["total_controls"] > 0


def test_export_json_valid(_soc2_db, tmp_path, monkeypatch):
    """export_report --format json produces parseable JSON with controls list."""
    from tools.compliance.soc2_exporter import export_report

    tid = f"json-{uuid.uuid4().hex[:8]}"
    out = tmp_path / "report.json"
    export_report(tid, out, fmt="json")

    assert out.exists()
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert "summary" in parsed
    assert "controls" in parsed
    assert isinstance(parsed["controls"], list)
    assert parsed["summary"]["total_controls"] == len(parsed["controls"])


# ---------------------------------------------------------------------------
# ecr-soc2-04: evidence tab API route returns 200
# ---------------------------------------------------------------------------

def test_evidence_tab_route(_soc2_db, monkeypatch):
    """GET /api/admin/tenants/<tid>/evidence returns 200 with items list."""
    import importlib
    from flask import Flask, g

    monkeypatch.setenv("ICDEV_ADMIN_CONSOLE_ENABLED", "true")
    monkeypatch.setenv("ICDEV_ENFORCE_CANVAS_ACCESS", "false")

    import tools.admin.blueprint as admin_mod
    importlib.reload(admin_mod)

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-evidence-tab"

    bp = admin_mod.create_admin_blueprint()
    assert bp is not None
    app.register_blueprint(bp)

    @app.before_request
    def _set_user():
        g.current_user = {"id": "admin", "role": "admin"}

    client = app.test_client()
    tid = f"ev-{uuid.uuid4().hex[:8]}"

    resp = client.get(f"/api/admin/tenants/{tid}/evidence")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert "items" in body
    assert isinstance(body["items"], list)
