# CUI // SP-CTI
"""Tests for tools/boundary_canvas/cato_readiness.py — task bdr-feat-2.

cATO readiness scorer (0-100) + route + twin-page tile.

Coverage:
  * three-band synthetic fixtures (green / amber / red)
  * missing-data (no rows)          -> score None, band "unknown"
  * missing-table (no schema)       -> graceful degrade, no exception
  * weights loaded from config file (default + override)
  * config re-normalisation over available components
  * Flask route returns score + band + breakdown, and stays 200 on error

Main-DB reads use PG-native `%s`; the storage layer translates for the SQLite
init-fallback, so we drive the scorer with a translating StorageConnection
(tools.db.storage.get_connection) pointed at a temp SQLite file. The canvas
ISA read takes no bound params, so an in-memory sqlite3 conn suffices there.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tools.boundary_canvas import cato_readiness as cr

# ---------------------------------------------------------------------------
# Schema fixtures
# ---------------------------------------------------------------------------

_MAIN_DDL = """
CREATE TABLE IF NOT EXISTS compliance_twin_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    framework TEXT NOT NULL,
    control_id TEXT NOT NULL,
    implementation_status TEXT NOT NULL,
    evidence_ref TEXT,
    score REAL,
    assessor TEXT,
    notes TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS compliance_twin_violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    framework TEXT NOT NULL,
    control_id TEXT NOT NULL,
    violation_type TEXT NOT NULL,
    severity TEXT DEFAULT 'moderate',
    details TEXT,
    poam_id INTEGER,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS poam_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    weakness_id TEXT,
    weakness_description TEXT,
    severity TEXT,
    source TEXT,
    control_id TEXT,
    status TEXT DEFAULT 'open',
    corrective_action TEXT,
    milestone_date TEXT,
    completion_date TEXT,
    responsible_party TEXT,
    resources_required TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

_CANVAS_DDL = """
CREATE TABLE IF NOT EXISTS bd_isa_tracker (
    id TEXT PRIMARY KEY,
    design_id TEXT,
    interconnection_id TEXT NOT NULL,
    isa_doc_id TEXT,
    status TEXT DEFAULT 'draft',
    expiry_date TEXT,
    isa_expiry_date TEXT,
    review_date TEXT,
    owner TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_from_now(n: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=n)).date().isoformat()


@pytest.fixture
def main_conn(tmp_path):
    """Translating StorageConnection over a temp SQLite DB (%s placeholders work)."""
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(tmp_path / "main.db"))
    conn.executescript(_MAIN_DDL)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def canvas_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_CANVAS_DDL)
    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _seed_snapshot(conn, project_id, snap_id, controls, created_at=None):
    created_at = created_at or _now_iso()
    for cid, status, score in controls:
        conn.execute(
            "INSERT INTO compliance_twin_snapshots "
            "(snapshot_id, project_id, framework, control_id, implementation_status, score, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (snap_id, project_id, "FedRAMP Moderate", cid, status, score, created_at),
        )
    conn.commit()


def _seed_violation_with_poam(conn, project_id, control_id, poam_status):
    cur = conn.execute(
        "INSERT INTO poam_items (project_id, weakness_id, control_id, status, source) "
        "VALUES (%s, %s, %s, %s, %s)",
        (project_id, f"TWIN-{control_id}", control_id, poam_status, "test"),
    )
    poam_id = cur.lastrowid
    conn.execute(
        "INSERT INTO compliance_twin_violations "
        "(snapshot_id, project_id, framework, control_id, violation_type, poam_id) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        ("snap-x", project_id, "FedRAMP Moderate", control_id, "not_satisfied", poam_id),
    )
    conn.commit()


def _seed_isa(conn, design_id, isa_id, expiry_days, status="active"):
    conn.execute(
        "INSERT INTO bd_isa_tracker (id, design_id, interconnection_id, status, expiry_date) "
        "VALUES (?, ?, ?, ?, ?)",
        (isa_id, design_id, f"ic-{isa_id}", status, _days_from_now(expiry_days)),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Band tests
# ---------------------------------------------------------------------------

def test_green_band(main_conn, canvas_conn):
    did = "design-green"
    # All controls satisfied -> coverage 100; fresh snapshot -> freshness ~100.
    _seed_snapshot(main_conn, did, "snap-g",
                   [("AC-2", "satisfied", 1.0), ("AU-6", "satisfied", 1.0)])
    # A violation exists but its POA&M is closed -> 0 open -> poam 100.
    _seed_violation_with_poam(main_conn, did, "AC-2", "closed")
    # ISA valid for a year -> isa 100.
    _seed_isa(canvas_conn, did, "isa-1", 365)

    result = cr.compute_readiness(did, conn=main_conn, canvas_conn=canvas_conn)
    assert result["band"] == "green"
    assert result["score"] >= 80
    assert result["readiness_score"] == result["score"]  # MCP-style alias
    assert result["components"]["control_coverage"]["score"] == 100.0
    assert result["components"]["poam"]["open_poam_count"] == 0


def test_amber_band(main_conn, canvas_conn):
    did = "design-amber"
    # Half satisfied -> coverage 50.
    _seed_snapshot(main_conn, did, "snap-a",
                   [("AC-2", "satisfied", 1.0), ("AU-6", "not_satisfied", 0.0)])
    # 10 open linked POA&Ms / full_penalty 20 -> poam 50.
    for i in range(10):
        _seed_violation_with_poam(main_conn, did, f"CM-{i}", "open")
    # One ISA expiring soon -> isa 50.
    _seed_isa(canvas_conn, did, "isa-2", 30)

    result = cr.compute_readiness(did, conn=main_conn, canvas_conn=canvas_conn)
    assert result["band"] == "amber"
    assert 50 <= result["score"] < 80


def test_red_band(main_conn, canvas_conn):
    did = "design-red"
    # All not_satisfied -> coverage 0. Old snapshot -> low freshness.
    old = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    _seed_snapshot(main_conn, did, "snap-r",
                   [("AC-2", "not_satisfied", 0.0), ("AU-6", "not_satisfied", 0.0)],
                   created_at=old)
    # 20 open POA&Ms -> poam 0.
    for i in range(20):
        _seed_violation_with_poam(main_conn, did, f"SC-{i}", "open")
    # Expired ISA -> isa 0.
    _seed_isa(canvas_conn, did, "isa-3", -5)

    result = cr.compute_readiness(did, conn=main_conn, canvas_conn=canvas_conn)
    assert result["band"] == "red"
    assert result["score"] < 50


# ---------------------------------------------------------------------------
# Missing-data / graceful-degradation tests
# ---------------------------------------------------------------------------

def test_missing_data_yields_unknown(main_conn, canvas_conn):
    # Tables exist but no rows for this design -> every component None.
    result = cr.compute_readiness("design-empty", conn=main_conn, canvas_conn=canvas_conn)
    assert result["score"] is None
    assert result["band"] == "unknown"
    for comp in result["components"].values():
        assert comp["score"] is None


def test_missing_tables_do_not_raise():
    # A main conn with NO schema and no canvas conn -> reads raise internally
    # but compute_readiness swallows them and returns unknown (never a 500).
    empty = sqlite3.connect(":memory:")
    empty.row_factory = sqlite3.Row
    result = cr.compute_readiness("design-x", conn=empty, canvas_conn=None)
    assert result["score"] is None
    assert result["band"] == "unknown"
    empty.close()


def test_partial_data_renormalises(main_conn, canvas_conn):
    # Only ISA data present (isa_expiry=100); other components None.
    # Composite must equal the sole available component, not be diluted to 20.
    did = "design-isa-only"
    _seed_isa(canvas_conn, did, "isa-only", 365)
    result = cr.compute_readiness(did, conn=main_conn, canvas_conn=canvas_conn)
    assert result["components"]["isa_expiry"]["score"] == 100.0
    assert result["components"]["control_coverage"]["score"] is None
    assert result["score"] == 100.0
    assert result["band"] == "green"


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

def test_config_loads_from_repo_file():
    cfg = cr.load_config()
    assert set(cfg["weights"]) == {"control_coverage", "poam", "isa_expiry", "freshness"}
    assert cfg["bands"]["green"] == 80
    assert cfg["bands"]["amber"] == 50
    # weights are not hardcoded in logic — the loaded set drives compute
    assert abs(sum(cfg["weights"].values()) - 1.0) < 1e-6


def test_config_missing_file_falls_back(tmp_path):
    cfg = cr.load_config(tmp_path / "does_not_exist.yaml")
    assert cfg["weights"] == cr._DEFAULT_CONFIG["weights"]
    assert cfg["bands"]["green"] == 80


def test_config_override_changes_bands(tmp_path):
    override = tmp_path / "cfg.yaml"
    override.write_text(
        "weights:\n  control_coverage: 1.0\n  poam: 0\n  isa_expiry: 0\n  freshness: 0\n"
        "bands:\n  green: 95\n  amber: 40\n",
        encoding="utf-8",
    )
    cfg = cr.load_config(override)
    assert cfg["weights"]["control_coverage"] == 1.0
    assert cfg["bands"]["green"] == 95
    assert cfg["bands"]["amber"] == 40


def test_config_drives_scoring(main_conn, canvas_conn, tmp_path):
    # A partly-satisfied design scores ~73; with green threshold pushed to 99
    # it must land in amber (proving thresholds come from config, not logic).
    did = "design-cfg"
    _seed_snapshot(main_conn, did, "snap-c",
                   [("AC-2", "satisfied", 1.0), ("AU-6", "not_satisfied", 0.0)])
    _seed_isa(canvas_conn, did, "isa-c", 365)
    override = tmp_path / "cfg.yaml"
    override.write_text("bands:\n  green: 99\n  amber: 40\n", encoding="utf-8")
    cfg = cr.load_config(override)
    result = cr.compute_readiness(did, conn=main_conn, canvas_conn=canvas_conn, config=cfg)
    assert result["score"] < 99
    assert result["band"] == "amber"


# ---------------------------------------------------------------------------
# Route tests (Flask test client)
# ---------------------------------------------------------------------------

@pytest.fixture
def bdc_client(monkeypatch):
    monkeypatch.setenv("ICDEV_BOUNDARY_ENABLED", "true")
    from flask import Flask
    from tools.boundary_canvas.blueprint import create_boundary_blueprint

    bp = create_boundary_blueprint()
    assert bp is not None
    app = Flask(__name__)
    app.secret_key = "test-secret"
    app.register_blueprint(bp, url_prefix="/boundary")
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "test-admin"
    return client


def test_route_returns_score_band_breakdown(bdc_client, monkeypatch):
    canned = {
        "design_id": "d1", "project_id": "d1",
        "score": 72.5, "readiness_score": 72.5, "band": "amber",
        "components": {"control_coverage": {"score": 80.0}},
        "weights": {},
    }
    monkeypatch.setattr(
        "tools.boundary_canvas.cato_readiness.compute_readiness",
        lambda *a, **k: canned,
    )
    resp = bdc_client.get("/boundary/api/cato/readiness/d1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["score"] == 72.5
    assert data["band"] == "amber"
    assert "control_coverage" in data["components"]


def test_route_requires_auth():
    from flask import Flask
    from tools.boundary_canvas.blueprint import create_boundary_blueprint

    app = Flask(__name__)
    app.secret_key = "s"
    app.register_blueprint(create_boundary_blueprint(), url_prefix="/boundary")
    resp = app.test_client().get("/boundary/api/cato/readiness/d1")
    assert resp.status_code == 401


def test_route_stays_200_on_error(bdc_client, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(
        "tools.boundary_canvas.cato_readiness.compute_readiness", _boom
    )
    resp = bdc_client.get("/boundary/api/cato/readiness/d1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["band"] == "unknown"
    assert data["score"] is None
