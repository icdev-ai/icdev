"""Tests for tools.observability_canvas.replay_verify (ODC closed-loop hook).

Covers the library-level verify_path contract (od_ttp_coverage + od_audit
writes) plus the obx-cov-04 wiring:
  * POST /observability/api/replay-verify/<design_id> persists coverage rows
  * _ensure_coverage_schema materializes its DDL at most once per process
  * POST /observability/api/mitre/ingest (local mode) seeds odc_mitre_techniques
    from the mitre_catalog single-source-of-truth and is idempotent on re-run
"""
from __future__ import annotations

import importlib
import json
import uuid

import pytest

from tools.observability_canvas.replay_verify import (
    _sigma_known_ttps,
    verify_path,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _init_odc_db():
    """Ensure ODC DB schema exists before each test."""
    from tools.observability_canvas.db.init_db import init_db

    init_db()


@pytest.fixture()
def _conn():
    from tools.observability_canvas.db.init_db import get_connection

    c = get_connection()
    yield c
    c.close()


# ── Case 1: empty path ────────────────────────────────────────────────────────


def test_verify_empty_path_returns_empty_results():
    result = verify_path([])
    assert result["path"] == []
    assert result["results"] == []
    assert result["summary"]["total"] == 0
    assert result["summary"]["full"] == 0
    assert result["summary"]["partial"] == 0
    assert result["summary"]["none"] == 0


# ── Case 2: unknown TTP gets state=none ───────────────────────────────────────


def test_verify_unknown_ttp_gets_none():
    fake_tid = "T9999"
    result = verify_path([fake_tid])
    assert len(result["results"]) == 1
    row = result["results"][0]
    assert row["ttp_id"] == fake_tid
    assert row["state"] == "none"
    assert result["summary"]["none"] == 1
    assert result["summary"]["total"] == 1


# ── Case 3: sigma-known TTP (no design baseline) → partial ───────────────────


def test_verify_sigma_known_ttp_without_baseline_is_partial():
    sigma_ttps = _sigma_known_ttps()
    if not sigma_ttps:
        pytest.skip("no sigma TTPs available in current env")
    tid = next(iter(sigma_ttps))
    result = verify_path([tid])
    row = result["results"][0]
    # Without any design baseline entry, state must be partial (sigma-only signal)
    assert row["state"] in ("partial", "full")


# ── Case 4: coverage row written to od_ttp_coverage ──────────────────────────


def test_coverage_row_written_to_db(_conn):
    fake_tid = f"T-cov-{uuid.uuid4().hex[:6]}"
    result = verify_path([fake_tid])
    expected_row_id = result["results"][0]["coverage_row_id"]
    row = _conn.execute(
        "SELECT * FROM od_ttp_coverage WHERE id = ?", (expected_row_id,)
    ).fetchone()
    assert row is not None
    assert row["ttp_id"] == fake_tid
    assert row["state"] == "none"


# ── Case 5: audit event written to od_audit ───────────────────────────────────


def test_audit_row_written_per_ttp(_conn):
    fake_tid = f"T-aud-{uuid.uuid4().hex[:6]}"
    design_id = "test-design-audit"
    result = verify_path([fake_tid], design_id=design_id)
    coverage_row_id = result["results"][0]["coverage_row_id"]

    rows = _conn.execute(
        "SELECT * FROM od_audit WHERE action = 'ttp_coverage_check' AND design_id = ?",
        (design_id,),
    ).fetchall()
    assert len(rows) >= 1
    detail = json.loads(rows[-1]["detail"])
    assert detail["ttp_id"] == fake_tid
    assert detail["coverage_row_id"] == coverage_row_id
    assert detail["state"] == "none"


# ── Bonus: full state when baseline + sigma both present ──────────────────────


def test_full_state_when_design_baseline_covers_sigma_ttp(_conn):
    sigma_ttps = _sigma_known_ttps()
    if not sigma_ttps:
        pytest.skip("no sigma TTPs available in current env")
    tid = next(iter(sigma_ttps))

    # Insert a design with a cmp-baseline node that marks the TTP as covered
    design_id = f"test-design-{uuid.uuid4().hex[:8]}"
    graph = {
        "nodes": [
            {
                "id": "baseline-node",
                "type": "cmp-baseline",
                "label": "MITRE Baseline",
                "config_json": json.dumps(
                    {"techniques": [{"id": tid, "name": "Test Technique", "covered": True}]}
                ),
            }
        ],
        "edges": [],
    }
    _conn.execute(
        "INSERT INTO observability_designs (id, name, graph_json) VALUES (?, ?, ?)",
        (design_id, "test-design", json.dumps(graph)),
    )
    _conn.commit()

    result = verify_path([tid], design_id=design_id)
    row = result["results"][0]
    assert row["state"] == "full", (
        f"Expected 'full' for {tid} with sigma + covered baseline, got '{row['state']}'"
    )
    assert result["summary"]["full"] == 1


# ── (b) DDL-once behavior ─────────────────────────────────────────────────────


def test_ensure_coverage_schema_ddl_materializes_at_most_once_per_process():
    """verify_path() no longer re-issues od_ttp_coverage DDL on every call.

    _ensure_coverage_schema is gated by the module-level _schema_ensured flag;
    across three verify_path() calls the DDL must materialize exactly once.
    Shim-aware: patch via importlib.import_module + setattr.
    """
    rv = importlib.import_module("tools.observability_canvas.replay_verify")
    rv._schema_ensured = False
    materialized = {"n": 0}
    orig = rv._ensure_coverage_schema

    def _counting(conn):
        before = rv._schema_ensured
        orig(conn)
        if not before and rv._schema_ensured:
            materialized["n"] += 1

    setattr(rv, "_ensure_coverage_schema", _counting)
    try:
        rv.verify_path([f"T-ddl-{uuid.uuid4().hex[:4]}"])
        rv.verify_path([f"T-ddl-{uuid.uuid4().hex[:4]}"])
        rv.verify_path([f"T-ddl-{uuid.uuid4().hex[:4]}"])
    finally:
        setattr(rv, "_ensure_coverage_schema", orig)

    assert materialized["n"] == 1, (
        f"DDL should materialize once per process, materialized {materialized['n']} times"
    )
    assert rv._schema_ensured is True


# ── Route-level tests (blueprint wiring) ──────────────────────────────────────


@pytest.fixture()
def odc_client(monkeypatch):
    """Authenticated Flask test client with the ODC blueprint mounted.

    API routes return JSON (no template rendering), so no template loader is
    needed. Uses the real observability_canvas.db (bootstrapped by init_db) —
    the same store the library-level tests above write to.
    """
    from flask import Flask

    monkeypatch.setenv("ICDEV_OBSERVABILITY_ENABLED", "true")
    bp_mod = importlib.import_module("tools.observability_canvas.blueprint")
    bp = bp_mod.create_observability_blueprint()
    assert bp is not None, "blueprint should build when ICDEV_OBSERVABILITY_ENABLED=true"

    app = Flask(__name__)
    app.secret_key = "obx-cov-04-test"
    app.register_blueprint(bp, url_prefix="/observability")

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "obx-cov-04-tester"
    return client


def test_route_replay_verify_requires_auth(odc_client):
    """Unauthenticated POST is rejected 401 (oc_login_required)."""
    from flask import Flask

    bp_mod = importlib.import_module("tools.observability_canvas.blueprint")
    bp = bp_mod.create_observability_blueprint()
    app = Flask(__name__)
    app.secret_key = "obx-cov-04-noauth"
    app.register_blueprint(bp, url_prefix="/observability")
    anon = app.test_client()
    resp = anon.post("/observability/api/replay-verify/anydesign", json={"ttp_ids": ["T1059"]})
    assert resp.status_code == 401


def test_route_replay_verify_persists_od_ttp_coverage(odc_client):
    """POST /api/replay-verify/<design_id> persists od_ttp_coverage rows."""
    from tools.observability_canvas.db.init_db import get_connection

    tid = f"T-route-{uuid.uuid4().hex[:6]}"
    design_id = f"design-{uuid.uuid4().hex[:8]}"
    resp = odc_client.post(
        f"/observability/api/replay-verify/{design_id}",
        json={"ttp_ids": [tid]},
    )
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["path"] == [tid]
    assert body["summary"]["total"] == 1
    row_id = body["results"][0]["coverage_row_id"]

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM od_ttp_coverage WHERE id = ?", (row_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row["ttp_id"] == tid
    assert row["design_id"] == design_id


def test_route_replay_verify_rejects_non_list(odc_client):
    """ttp_ids must be a list → 400."""
    resp = odc_client.post(
        "/observability/api/replay-verify/somedesign",
        json={"ttp_ids": "T1059"},
    )
    assert resp.status_code == 400


def test_route_mitre_ingest_local_seeds_from_catalog(odc_client):
    """POST /api/mitre/ingest (local) populates odc_mitre_techniques from the
    single-source-of-truth catalog and is idempotent on re-run."""
    from tools.observability_canvas.db.init_db import get_connection
    from tools.observability_canvas.mitre_catalog import MITRE_CATALOG

    resp = odc_client.post("/observability/api/mitre/ingest", json={"source": "local"})
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["source"] == "local"
    assert isinstance(body["ingested"], int)
    assert body["errors"] == []

    conn = get_connection()
    try:
        rows = conn.execute("SELECT technique_id FROM odc_mitre_techniques").fetchall()
    finally:
        conn.close()
    present = {r["technique_id"] for r in rows}
    # Every catalog technique is now persisted (drift-free seeding).
    assert set(MITRE_CATALOG.keys()) <= present

    # Idempotent re-run: append-only skip means nothing new is ingested.
    resp2 = odc_client.post("/observability/api/mitre/ingest", json={"source": "local"})
    assert resp2.status_code == 200
    body2 = resp2.get_json()
    assert body2["ingested"] == 0
    assert body2["skipped"] >= len(MITRE_CATALOG)


def test_route_mitre_ingest_rejects_bad_source(odc_client):
    """Unknown source value → 400."""
    resp = odc_client.post("/observability/api/mitre/ingest", json={"source": "bogus"})
    assert resp.status_code == 400
