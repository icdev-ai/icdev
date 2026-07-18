# CUI // SP-CTI
"""Tests for the ODC Observability Digital Twin (obx-fix-02, obx-fix-03).

Covers:
  * snapshot round-trip — take_snapshot persists, list_snapshots (the twin page
    read path) reads it back; service_count matches the seeded graph_json and
    coverage_score matches the seeded od_assessments row.
  * no-assessment marker — coverage_score 0.0 + basis "no_assessment".
  * persist failure surfaces an error (raise / non-201 route status), never a
    fake success.
  * simulate_delta / slo_projection are design-derived — materially different
    designs yield materially different numbers — and every projection carries
    estimate=True + a basis naming its inputs (TRUST: no fabricated constants
    presented as measurements).

Backend is SQLite-forced by tests/conftest.py. The twin's canvas connection
(tools.observability_canvas.db.init_db.get_connection) is monkeypatched — shim
aware, via importlib.import_module + setattr — to a translating StorageConnection
pointed at a temp icdev.db seeded from MINIMAL_ICDEV_SCHEMA.

NIST 800-53: AU-2, SA-11, SI-4
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.conftest import MINIMAL_ICDEV_SCHEMA  # noqa: E402


# ── Graph fixtures ──────────────────────────────────────────────────────────

def _graph(n_sources: int, extra_nodes: int = 0) -> dict:
    """Build a graph_json with `n_sources` recommended source nodes + extras."""
    src_types = [
        "src-app-log", "src-os-log", "src-network-log", "src-cloud-log",
        "src-container-log", "src-db-audit", "src-endpoint", "src-iam",
        "src-metric", "src-trace",
    ]
    nodes = []
    for i in range(n_sources):
        nodes.append({"id": f"s{i}", "type": src_types[i % len(src_types)], "label": f"S{i}"})
    for j in range(extra_nodes):
        nodes.append({"id": f"x{j}", "type": "col-otel", "label": f"X{j}"})
    return {"nodes": nodes, "edges": []}


@pytest.fixture
def twin_env(tmp_path, monkeypatch):
    """Temp icdev.db + translating connection wired into the twin canvas getter."""
    db_path = tmp_path / "icdev.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(MINIMAL_ICDEV_SCHEMA)
    raw.commit()
    raw.close()

    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    from tools.db.storage import get_connection as storage_get_connection

    def _fake_conn():
        return storage_get_connection(str(db_path))

    # Shim-aware: patch the exact tools.* module the twin imports from.
    init_db_mod = importlib.import_module("tools.observability_canvas.db.init_db")
    monkeypatch.setattr(init_db_mod, "get_connection", _fake_conn, raising=True)

    return db_path, _fake_conn


def _seed_design(conn, design_id, graph, name="Test Design"):
    conn.execute(
        "INSERT INTO observability_designs (id, name, description, graph_json) VALUES (%s,%s,%s,%s)",
        (design_id, name, "seed", json.dumps(graph)),
    )
    conn.commit()


def _seed_assessment(conn, design_id, score, created_at="2026-01-01T00:00:00+00:00"):
    conn.execute(
        "INSERT INTO od_assessments (id, design_id, assessment_type, score, grade, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (str(uuid.uuid4()), design_id, "coverage", score, "B", created_at),
    )
    conn.commit()


# ── Snapshot round-trip ─────────────────────────────────────────────────────

def test_snapshot_round_trip(twin_env):
    _db, fake_conn = twin_env
    twin = importlib.import_module("tools.observability_canvas.twin")

    design_id = "d-round-trip"
    graph = _graph(n_sources=4, extra_nodes=3)  # 7 nodes total
    conn = fake_conn()
    _seed_design(conn, design_id, graph)
    _seed_assessment(conn, design_id, 72.5)

    snap = twin.take_snapshot(design_id, label="baseline")
    assert snap["service_count"] == 7, "service_count must match seeded graph node count"
    assert snap["coverage_score"] == 72.5, "coverage_score must match latest od_assessments.score"
    assert snap["coverage_basis"] == "od_assessments.score"

    # Read back through the SAME path the twin page uses.
    listed = twin.list_snapshots(design_id)
    assert len(listed) == 1
    row = listed[0]
    assert row["id"] == snap["id"]
    assert row["service_count"] == 7
    assert row["coverage_score"] == 72.5
    assert row["label"] == "baseline"


def test_snapshot_uses_latest_assessment(twin_env):
    _db, fake_conn = twin_env
    twin = importlib.import_module("tools.observability_canvas.twin")

    design_id = "d-latest"
    conn = fake_conn()
    _seed_design(conn, design_id, _graph(2))
    _seed_assessment(conn, design_id, 40.0, created_at="2026-01-01T00:00:00+00:00")
    _seed_assessment(conn, design_id, 88.0, created_at="2026-02-01T00:00:00+00:00")  # newer

    snap = twin.take_snapshot(design_id)
    assert snap["coverage_score"] == 88.0


def test_snapshot_no_assessment_marker(twin_env):
    _db, fake_conn = twin_env
    twin = importlib.import_module("tools.observability_canvas.twin")

    design_id = "d-noassess"
    conn = fake_conn()
    _seed_design(conn, design_id, _graph(3))

    snap = twin.take_snapshot(design_id)
    assert snap["coverage_score"] == 0.0
    assert snap["coverage_basis"] == "no_assessment"
    assert snap["service_count"] == 3


# ── Persist failure surfaces an error, never a fake success ─────────────────

def test_snapshot_unknown_design_raises(twin_env):
    _db, _fake_conn = twin_env
    twin = importlib.import_module("tools.observability_canvas.twin")
    with pytest.raises(ValueError):
        twin.take_snapshot("does-not-exist")


def test_snapshot_persist_failure_raises(twin_env):
    _db, fake_conn = twin_env
    twin = importlib.import_module("tools.observability_canvas.twin")

    design_id = "d-persistfail"
    conn = fake_conn()
    _seed_design(conn, design_id, _graph(2))
    # Remove the destination table so the INSERT cannot succeed.
    conn.execute("DROP TABLE odc_twin_snapshots")
    conn.commit()

    with pytest.raises(Exception):
        twin.take_snapshot(design_id)


def test_route_persist_failure_returns_500(twin_env, monkeypatch):
    """The snapshot route must map a persist failure to 500, not a fake 201."""
    from flask import Flask
    from tools.observability_canvas.blueprint import create_observability_blueprint

    twin = importlib.import_module("tools.observability_canvas.twin")

    def _boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(twin, "take_snapshot", _boom, raising=True)

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "test"
    app.register_blueprint(create_observability_blueprint(), url_prefix="/observability")

    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = "test-admin"
        resp = c.post("/observability/api/twin/d1/snapshot", json={"label": "x"})
    assert resp.status_code == 500, f"expected 500 on persist failure, got {resp.status_code}"


def test_route_unknown_design_returns_404(twin_env, monkeypatch):
    from flask import Flask
    from tools.observability_canvas.blueprint import create_observability_blueprint

    twin = importlib.import_module("tools.observability_canvas.twin")

    def _missing(*a, **k):
        raise ValueError("observability design not found: d1")

    monkeypatch.setattr(twin, "take_snapshot", _missing, raising=True)

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "test"
    app.register_blueprint(create_observability_blueprint(), url_prefix="/observability")

    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = "test-admin"
        resp = c.post("/observability/api/twin/d1/snapshot", json={})
    assert resp.status_code == 404, f"expected 404 for unknown design, got {resp.status_code}"


# ── Projections are design-derived + labelled estimates ─────────────────────

def test_simulate_estimate_and_basis_present(twin_env):
    _db, fake_conn = twin_env
    twin = importlib.import_module("tools.observability_canvas.twin")

    design_id = "d-sim"
    _seed_design(fake_conn(), design_id, _graph(5))
    result = twin.simulate_delta(
        design_id,
        {"add_receivers": ["prometheus/new"], "remove_exporters": ["jaeger/old"],
         "services": {"svc-a": {"traces": True, "metrics": True, "logs": False}}},
    )
    assert result["estimate"] is True
    assert result.get("basis"), "simulate_delta must name its inputs in 'basis'"
    assert "compute_coverage_score" in result["basis"]
    # service_coverage derived from the actual design node count + delta.
    assert result["service_coverage"]["covered"] >= 5


def test_slo_projection_estimate_and_basis_present(twin_env):
    _db, fake_conn = twin_env
    twin = importlib.import_module("tools.observability_canvas.twin")

    design_id = "d-slo"
    _seed_design(fake_conn(), design_id, _graph(5))
    result = twin.slo_projection(design_id, {"remove_exporters": ["a"]})
    assert result["estimate"] is True
    assert result.get("basis")
    assert "current_burn_pct" in result and "projected_burn_pct" in result


def test_projections_differ_for_materially_different_designs(twin_env):
    _db, fake_conn = twin_env
    twin = importlib.import_module("tools.observability_canvas.twin")

    conn = fake_conn()
    rich_id, sparse_id = "d-rich", "d-sparse"
    _seed_design(conn, rich_id, _graph(n_sources=10))   # full recommended coverage
    _seed_design(conn, sparse_id, _graph(n_sources=1))  # minimal coverage

    delta = {"remove_exporters": ["exp1"], "add_receivers": ["rcv1"]}

    sim_rich = twin.simulate_delta(rich_id, delta)
    sim_sparse = twin.simulate_delta(sparse_id, delta)
    assert sim_rich["baseline_coverage_pct"] != sim_sparse["baseline_coverage_pct"], (
        "simulate_delta must be design-derived, not a function of the payload alone"
    )

    slo_rich = twin.slo_projection(rich_id, delta)
    slo_sparse = twin.slo_projection(sparse_id, delta)
    assert slo_rich["current_burn_pct"] != slo_sparse["current_burn_pct"], (
        "slo_projection burn must derive from the design's actual coverage"
    )
    # Richer coverage => lower assumed burn.
    assert slo_rich["current_burn_pct"] < slo_sparse["current_burn_pct"]
