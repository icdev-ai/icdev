# CUI // SP-CTI
"""penta-aiify-05 — run_scan integration + posture/roadmap/IQE-adapter smoke.

Drives the full engine.run_scan pipeline against a temp canvas DB (pattern
detection mocked for determinism) and asserts:
  scan row completed  ->  opportunities + scores persisted  ->  roadmap built
  ->  capped promotion to kanban_tasks via task_factory  ->  posture reflects it.

Plus happy-path smoke units for posture.compute_posture / snapshot_posture,
roadmap_generator.generate_roadmap, and the IQE adapter collections.

Determinism: engine imports ``detect_patterns`` into its own namespace, so the
mock is installed as ``tools.aiify.engine.detect_patterns``. The real scorer,
roadmap generator, promotion (task_factory) and posture all run unmocked.
"""
from __future__ import annotations

import json

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: temp canvas + kanban DBs, init_db.DB_PATH restored on teardown
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def aiify_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))
    monkeypatch.setenv("AIIFY_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("AIIFY_DB_PATH", str(tmp_path / "aiify_canvas.db"))

    import tools.aiify.db.init_db as init_db
    _default_dbpath = init_db._ICDEV_ROOT / "data" / "aiify_canvas.db"
    init_db.DB_PATH = tmp_path / "aiify_canvas.db"
    try:
        yield tmp_path
    finally:
        init_db.DB_PATH = _default_dbpath


_MOCK_PATTERNS = [
    {
        "module_path": "tools/demo/alpha.py",
        "function_name": "score_it",
        "line_start": 10, "line_end": 12,
        "language": "python",
        "pattern_type": "hardcoded_threshold",
        "pattern_detail": {"value": 0.7},
    },
    {
        "module_path": "tools/demo/beta.py",
        "function_name": "route_it",
        "line_start": 20, "line_end": 40,
        "language": "python",
        "pattern_type": "nested_conditionals",
        "pattern_detail": {"depth": 4},
    },
]


def _canvas_conn():
    from tools.db.storage import get_canvas_connection
    return get_canvas_connection("AIIFY_DB_PATH")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Full run_scan integration
# ─────────────────────────────────────────────────────────────────────────────

def test_run_scan_end_to_end(aiify_env, tmp_path, monkeypatch):
    import tools.aiify.engine as engine

    # Deterministic pattern detection; a real (empty-ish) source dir for _count_source.
    src = tmp_path / "src"
    src.mkdir()
    (src / "alpha.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(engine, "detect_patterns", lambda path: [dict(p) for p in _MOCK_PATTERNS])

    result = engine.run_scan("local_path", str(src), {"il_level": "il4"})

    assert result["status"] == "completed"
    scan_id = result["scan_id"]
    assert result["opportunities_count"] == 2

    conn = _canvas_conn()
    try:
        scan = conn.execute(
            "SELECT status, project_summary FROM aiify_scans WHERE scan_id = %s", (scan_id,)
        ).fetchone()
        assert scan["status"] == "completed"
        assert scan["project_summary"]  # deterministic summary backfilled at completion

        opps = conn.execute(
            "SELECT COUNT(*) FROM aiify_opportunities WHERE scan_id = %s", (scan_id,)
        ).fetchone()[0]
        assert opps == 2

        scores = conn.execute(
            "SELECT COUNT(*) FROM aiify_scores s JOIN aiify_opportunities o "
            "ON o.opportunity_id = s.opportunity_id WHERE o.scan_id = %s", (scan_id,)
        ).fetchone()[0]
        assert scores == 2

        rm = conn.execute(
            "SELECT roadmap_id, phases FROM aiify_roadmaps WHERE scan_id = %s", (scan_id,)
        ).fetchone()
        assert rm is not None
        phases = json.loads(rm["phases"]) if isinstance(rm["phases"], str) else rm["phases"]
        assert isinstance(phases, list) and phases
    finally:
        conn.close()


def test_run_scan_promotes_capped_to_kanban(aiify_env, tmp_path, monkeypatch):
    import tools.aiify.engine as engine
    src = tmp_path / "src"
    src.mkdir()
    (src / "alpha.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(engine, "detect_patterns", lambda path: [dict(p) for p in _MOCK_PATTERNS])

    result = engine.run_scan("local_path", str(src), {"il_level": "il4"})
    assert result["status"] == "completed"

    # Promotion routed through task_factory into the kanban DB (main icdev DB).
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT id, status, dispatch_source FROM kanban_tasks WHERE id LIKE 'aiify-%'"
        ).fetchall()]
    finally:
        conn.close()
    assert rows, "expected auto-promoted aiify tasks"
    # Auto-promoted opportunities land in the non-dispatchable quarantine status.
    opp_tasks = [r for r in rows if r["id"].startswith("aiify-opp-")]
    assert opp_tasks
    assert all(r["status"] == "suggested" for r in opp_tasks)
    # Cap honored: never more opp tasks than distinct opportunities scanned.
    assert len(opp_tasks) <= 2


def test_run_scan_posture_reflects_scan(aiify_env, tmp_path, monkeypatch):
    import tools.aiify.engine as engine
    from tools.aiify.posture import compute_posture
    src = tmp_path / "src"
    src.mkdir()
    (src / "alpha.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(engine, "detect_patterns", lambda path: [dict(p) for p in _MOCK_PATTERNS])

    engine.run_scan("local_path", str(src), {"il_level": "il4"})

    conn = _canvas_conn()
    try:
        posture = compute_posture(conn)
    finally:
        conn.close()
    assert posture["counts"]["total_scans"] >= 1
    assert posture["counts"]["total_opportunities"] >= 2
    assert 0.0 <= posture["overall_score"] <= 100.0
    assert posture["grade"] in {"A", "B", "C", "D", "F"}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Posture smoke (empty DB + snapshot round-trip)
# ─────────────────────────────────────────────────────────────────────────────

def test_posture_compute_empty(aiify_env):
    import tools.aiify.db.init_db as init_db
    from tools.aiify.posture import compute_posture
    init_db.init_db()
    conn = _canvas_conn()
    try:
        posture = compute_posture(conn)
    finally:
        conn.close()
    assert posture["counts"]["total_scans"] == 0
    assert posture["grade"] == "F"
    assert posture["dimensions"]


def test_posture_snapshot_roundtrip(aiify_env):
    import tools.aiify.db.init_db as init_db
    from tools.aiify.posture import snapshot_posture, posture_trend
    init_db.init_db()
    conn = _canvas_conn()
    try:
        p = snapshot_posture(conn, actor="tester")
        trend = posture_trend(conn, limit=5)
    finally:
        conn.close()
    assert "overall_score" in p
    assert len(trend) == 1


# ─────────────────────────────────────────────────────────────────────────────
# 3. roadmap_generator smoke
# ─────────────────────────────────────────────────────────────────────────────

def test_generate_roadmap_smoke(aiify_env):
    import tools.aiify.db.init_db as init_db
    from tools.aiify.roadmap_generator import generate_roadmap
    init_db.init_db()

    # Seed a scan so the FK on aiify_roadmaps.scan_id is satisfiable.
    conn = _canvas_conn()
    try:
        conn.execute(
            "INSERT INTO aiify_scans (scan_id, input_type, input_ref, status) "
            "VALUES (%s, %s, %s, %s)", (1, "path", "tools/demo", "completed"),
        )
        conn.commit()
    finally:
        conn.close()

    opps = [
        {"opportunity_id": 1, "module_path": "a.py", "function_name": "f",
         "pattern_type": "hardcoded_threshold", "ai_paradigm": "anomaly_detection",
         "il_recommended_model": "claude-sonnet-4-6"},
        {"opportunity_id": 2, "module_path": "b.py", "function_name": "g",
         "pattern_type": "nested_conditionals", "ai_paradigm": "ml_classifier",
         "il_recommended_model": "claude-sonnet-4-6"},
    ]
    scores = [
        {"opportunity_id": 1, "composite_score": 0.8},
        {"opportunity_id": 2, "composite_score": 0.5},
    ]
    roadmap = generate_roadmap(1, opps, scores)
    assert "phases" in roadmap
    assert isinstance(roadmap["phases"], list) and roadmap["phases"]
    assert roadmap.get("total_effort_days", 0) >= 0
    assert "roadmap_id" in roadmap


# ─────────────────────────────────────────────────────────────────────────────
# 4. IQE adapter smoke — collections return rows from the canvas DB
# ─────────────────────────────────────────────────────────────────────────────

def test_iqe_adapters_happy_path(aiify_env, tmp_path, monkeypatch):
    import importlib
    import tools.aiify.engine as engine
    src = tmp_path / "src"
    src.mkdir()
    (src / "alpha.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(engine, "detect_patterns", lambda path: [dict(p) for p in _MOCK_PATTERNS])
    engine.run_scan("local_path", str(src), {"il_level": "il4"})

    adapters = importlib.import_module("tools.iqe.adapters.aiify")
    conn = _canvas_conn()
    try:
        scans = adapters.scans_adapter(conn)
        opps = adapters.opportunities_adapter(conn)
        roadmaps = adapters.roadmaps_adapter(conn)
        posture = adapters.posture_adapter(conn)
    finally:
        conn.close()
    assert isinstance(scans, list) and scans
    assert isinstance(opps, list) and len(opps) == 2
    assert isinstance(roadmaps, list) and roadmaps
    assert isinstance(posture, list)  # posture is a single-row projection
