# CUI // SP-CTI
"""Characterization test for GET /security/api/posture-summary — shx-hyg-02.

This test pins the EXACT JSON contract of the posture-summary aggregation route
(``sc_api_posture_summary`` in ``tools/security_canvas/blueprint.py``, delegating
to ``tools/security_canvas/posture.py``). It seeds a scratch security-canvas DB
with designs and assessments spanning every aggregation block the route performs
and snapshots the full response body (deep structure + values, not just keys).

Aggregation blocks exercised:
  * per-design "latest assessment" (ORDER BY ran_at DESC LIMIT 1)
  * assessed / unassessed counts, average risk score + derived average grade
  * grade distribution across A/B/C/D/F
  * overall_posture bucketing
  * pipeline-level assessments (design_id IS NULL, trigger_source='pdc_save')
    with dedup by source_entity_id and CAT1 roll-up into total_cat1
  * NDC-triggered design assessments (trigger_source='ndc_save') with dedup by
    design_id

CAT1 accounting (shx-hyg-09): the per-design "latest assessment" query selects
``findings_json`` and counts real CAT1-severity findings via ``_count_cat1``, so
each design reports its true ``cat1_count`` and those roll into
``total_cat1_findings`` alongside pipeline CAT1s. NDC CAT1s are NOT added to the
total a second time: NDC topologies are imported as ``security_designs`` rows and
their ``ndc_save`` assessment is that design's latest, so their CAT1 findings are
already counted by the per-design roll-up. The seeded NDC designs here use ids
(``D-ndc-1``/``D-ndc-2``) that are NOT in ``security_designs``, so they exercise
only the display-only ``ndc_assessments`` block and never touch the total.

The fixture isolates a throwaway SQLite DB by patching ``init_db.get_connection``
before the blueprint closure captures it — mirroring the sibling error tests.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

import pytest


# ── App builder with an isolated scratch DB ──────────────────────────────────

def _conn_factory(db_path: Path):
    """Return a get_connection() replacement bound to an isolated SQLite file.

    Wraps each fresh sqlite3 connection in the same StorageConnection used in
    production so ``%s`` placeholder translation and context-manager semantics
    are identical to the real code path.
    """
    from tools.db.storage import StorageConnection

    def factory(*_a, **_k):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return StorageConnection(conn, "sqlite")

    return factory


def _build_isolated_app(monkeypatch, db_path: Path):
    """Build a Flask app hosting only the security_canvas blueprint, wired to an
    isolated scratch DB. Patches get_connection BEFORE the blueprint is created
    so its closure captures the isolated factory."""
    from flask import Flask
    from tools.security_canvas.db import init_db as init_db_mod
    from tools.security_canvas.blueprint import create_security_blueprint

    monkeypatch.setenv("ICDEV_SECURITY_ENABLED", "true")
    monkeypatch.setenv("ICDEV_AUTH_BYPASS", "true")
    monkeypatch.delenv("ICDEV_DASHBOARD_API_KEY", raising=False)

    factory = _conn_factory(db_path)
    monkeypatch.setattr(init_db_mod, "get_connection", factory)

    flask_app = Flask(__name__, template_folder="../tools/dashboard/templates")
    flask_app.config.update(TESTING=True, SECRET_KEY="test-secret", WTF_CSRF_ENABLED=False)

    bp = create_security_blueprint()  # runs init_db() against the isolated DB
    assert bp is not None, "create_security_blueprint() returned None"
    flask_app.register_blueprint(bp, url_prefix="/security")
    return flask_app, factory


def _seed(db_path: Path):
    """Seed designs + assessments spanning every aggregation block."""
    conn = sqlite3.connect(str(db_path))
    try:
        # Designs (ordered by name in the route: Alpha, Bravo, Charlie, Delta)
        designs = [
            ("d-alpha", "Alpha"),
            ("d-bravo", "Bravo"),
            ("d-charlie", "Charlie"),  # no assessments -> unassessed
            ("d-delta", "Delta"),
        ]
        conn.executemany(
            "INSERT INTO security_designs (id, name) VALUES (?, ?)", designs
        )

        def _a(design_id, atype, trigger, source_entity, risk, grade, findings, ran_at):
            conn.execute(
                "INSERT INTO sc_assessments "
                "(id, design_id, assessment_type, trigger_source, source_entity_id, "
                " risk_score, posture_grade, findings_json, ran_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    design_id,
                    atype,
                    trigger,
                    source_entity,
                    risk,
                    grade,
                    json.dumps(findings),
                    ran_at,
                ),
            )

        cat1 = {"severity": "CAT1"}
        # Alpha: two assessments; latest (Feb) wins -> risk 92, grade A
        _a("d-alpha", "manual", "manual", None, 95.0, "A", [cat1], "2024-01-01T00:00:00")
        _a("d-alpha", "manual", "manual", None, 92.0, "A", [cat1, cat1], "2024-02-01T00:00:00")
        # Bravo: single -> risk 75, grade C
        _a("d-bravo", "manual", "manual", None, 75.0, "C", [], "2024-01-15T00:00:00")
        # Delta: single -> risk 55, grade F
        _a("d-delta", "manual", "manual", None, 55.0, "F", [cat1], "2024-03-01T00:00:00")

        # Pipeline-level (design_id IS NULL, pdc_save); dedup by source_entity_id
        _a(None, "pipeline", "pdc_save", "pipe-1", 0.0, "F", [cat1, cat1], "2024-05-02T00:00:00")
        _a(None, "pipeline", "pdc_save", "pipe-1", 0.0, "F", [cat1], "2024-05-01T00:00:00")  # dup -> skipped
        _a(None, "pipeline", "pdc_save", "pipe-2", 0.0, "F", [cat1], "2024-05-03T00:00:00")

        # NDC-triggered (ndc_save, design_id NOT NULL); dedup by design_id
        _a("D-ndc-1", "ndc", "ndc_save", "topo-1", 80.0, "B", [cat1], "2024-06-02T00:00:00")
        _a("D-ndc-1", "ndc", "ndc_save", "topo-1b", 70.0, "C", [cat1], "2024-06-01T00:00:00")  # dup -> skipped
        _a("D-ndc-2", "ndc", "ndc_save", "topo-2", 40.0, "F", [], "2024-06-03T00:00:00")

        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def tmp_db(tmp_path):
    return tmp_path / "sc_posture.db"


# ── Characterization: seeded DB, full-body snapshot ──────────────────────────

def test_posture_summary_full_snapshot(monkeypatch, tmp_db):
    app, _factory = _build_isolated_app(monkeypatch, tmp_db)
    _seed(tmp_db)

    with app.test_client() as client:
        resp = client.get("/security/api/posture-summary")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()

    expected = {
        "total_designs": 4,
        "assessed_designs": 3,
        "unassessed_designs": 1,
        "average_risk_score": 74.0,  # (92 + 75 + 55) / 3
        "average_grade": "C",        # 74 -> [70, 80)
        "grade_distribution": {"A": 1, "B": 0, "C": 1, "D": 0, "F": 1},
        "designs": [
            {
                "id": "d-alpha",
                "name": "Alpha",
                "risk_score": 92.0,
                "grade": "A",
                "last_assessed": "2024-02-01T00:00:00",
                "cat1_count": 2,  # latest (Feb) assessment has [cat1, cat1]
            },
            {
                "id": "d-bravo",
                "name": "Bravo",
                "risk_score": 75.0,
                "grade": "C",
                "last_assessed": "2024-01-15T00:00:00",
                "cat1_count": 0,  # empty findings
            },
            {
                "id": "d-charlie",
                "name": "Charlie",
                "risk_score": None,
                "grade": None,
                "last_assessed": None,
                "cat1_count": 0,  # unassessed
            },
            {
                "id": "d-delta",
                "name": "Delta",
                "risk_score": 55.0,
                "grade": "F",
                "last_assessed": "2024-03-01T00:00:00",
                "cat1_count": 1,  # single assessment has [cat1]
            },
        ],
        "overall_posture": "moderate",  # 74 -> [60, 80)
        # per-design: Alpha 2 + Delta 1 = 3; pipeline: pipe-2 (1) + pipe-1 (2) = 3
        "total_cat1_findings": 6,
        "pipeline_assessments": [
            {"pipeline_id": "pipe-2", "cat1_count": 1, "last_assessed": "2024-05-03T00:00:00"},
            {"pipeline_id": "pipe-1", "cat1_count": 2, "last_assessed": "2024-05-02T00:00:00"},
        ],
        "ndc_assessments": [
            {
                "topology_id": "topo-2",
                "design_id": "D-ndc-2",
                "risk_score": 40.0,
                "posture_grade": "F",
                "cat1_count": 0,
                "last_assessed": "2024-06-03T00:00:00",
            },
            {
                "topology_id": "topo-1",
                "design_id": "D-ndc-1",
                "risk_score": 80.0,
                "posture_grade": "B",
                "cat1_count": 1,
                "last_assessed": "2024-06-02T00:00:00",
            },
        ],
    }

    assert body == expected


# ── Characterization: empty DB ───────────────────────────────────────────────

def test_posture_summary_empty_db(monkeypatch, tmp_db):
    app, _factory = _build_isolated_app(monkeypatch, tmp_db)
    # No seeding — schema created by init_db() but no designs/assessments.

    with app.test_client() as client:
        resp = client.get("/security/api/posture-summary")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()

    assert body == {
        "total_designs": 0,
        "assessed_designs": 0,
        "unassessed_designs": 0,
        "average_risk_score": 0.0,
        "average_grade": "F",
        "grade_distribution": {"A": 0, "B": 0, "C": 0, "D": 0, "F": 0},
        "designs": [],
        "overall_posture": "critical",
        "total_cat1_findings": 0,
        "pipeline_assessments": [],
        "ndc_assessments": [],
    }
