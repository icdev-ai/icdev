# CUI // SP-CTI
"""penta-aiify-01 — runaway kanban seeding is capped + routed through task_factory.

Root cause under test: tools/aiify/engine.py used to auto-create one kanban task
per opportunity across every roadmap phase on EVERY scan, with raw INSERTs and no
cap/quarantine — the origin of the 353 aiify-* branch explosion.

Guards:
  * A scan yielding 100 opportunities auto-promotes <= auto_promote_cap tasks.
  * Every auto-promoted task lands in the non-dispatchable 'suggested' status.
  * ALL task creation flows through tools.kanban.task_factory.create_tasks.
  * Re-promoting the same opportunities is idempotent (per-opportunity key).
  * No raw ``INSERT INTO kanban_tasks`` remains anywhere under tools/aiify/.
"""
from __future__ import annotations

import pathlib

import pytest


@pytest.fixture
def kanban_env(tmp_path, monkeypatch):
    """Point main storage + aiify audit DB at isolated, empty temp SQLite files.

    The main DB is left empty so tools.kanban.init_db.init_kanban_tables() builds
    the canonical kanban schema (all columns + indexes) — the exact schema
    create_tasks writes against in production.
    """
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.setenv("AIIFY_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("AIIFY_DB_PATH", str(tmp_path / "aiify_canvas.db"))
    return db_path


def _make_opps(n: int):
    """n synthetic opportunities + descending-composite scores (id order == rank)."""
    opp_rows = []
    score_rows = []
    for i in range(1, n + 1):
        opp_rows.append({
            "opportunity_id": i,
            "pattern_type": "hardcoded_threshold",
            "module_path": f"tools/mod_{i}.py",
            "function_name": f"fn_{i}",
            "ai_paradigm": "anomaly_detection",
            "il_recommended_model": "claude-haiku-4-5-20251001",
        })
        composite = max(0.05, 1.0 - (i - 1) * 0.008)
        score_rows.append({
            "opportunity_id": i,
            "composite_score": composite,
            "value_score": composite,
            "feasibility_score": composite,
            "risk_score": 1.0 - composite,
        })
    return opp_rows, score_rows


def _make_roadmap(opp_rows):
    return {
        "roadmap_id": "rm-testabc123",
        "phases": [
            {
                "phase_id": "P1",
                "label": "P1 — Quick Wins",
                "opportunities": [{"opportunity_id": o["opportunity_id"]} for o in opp_rows],
            }
        ],
    }


def _count_tasks(db_path):
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, status FROM kanban_tasks ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def test_scan_promotion_is_capped_and_suggested(kanban_env, monkeypatch):
    from tools.aiify import engine
    import tools.kanban.task_factory as tf

    cap = int(engine._load_promotion_config()["auto_promote_cap"])

    # Spy on the canonical seeder to prove it is the sole creation path.
    real_create = tf.create_tasks
    calls = {"n": 0, "specs": 0}

    def _spy(specs):
        calls["n"] += 1
        calls["specs"] += len(specs)
        return real_create(specs)

    monkeypatch.setattr(tf, "create_tasks", _spy)

    opp_rows, score_rows = _make_opps(100)
    roadmap = _make_roadmap(opp_rows)

    top = engine._promote_top_opportunities(opp_rows, score_rows, scan_id=1,
                                            roadmap_id=roadmap["roadmap_id"])
    phase = engine._promote_phase_opportunities(roadmap, opp_rows, score_rows, scan_id=1)

    tasks = _count_tasks(kanban_env)

    # Cap respected: total auto-promoted tasks per scan never exceeds the cap.
    assert len(tasks) <= cap, f"expected <= {cap} tasks, got {len(tasks)}"
    assert top + phase == len(tasks)
    # With 100 opportunities we expect the full cap to be used.
    assert len(tasks) == cap

    # Every auto-promoted task is quarantined ('suggested'), never dispatchable.
    assert all(t["status"] == "suggested" for t in tasks), \
        [t["status"] for t in tasks]
    assert all(t["id"].startswith("aiify-") for t in tasks)

    # All creation went through task_factory.create_tasks.
    assert calls["n"] >= 1


def test_promotion_is_idempotent(kanban_env):
    from tools.aiify import engine

    cap = int(engine._load_promotion_config()["auto_promote_cap"])
    opp_rows, score_rows = _make_opps(100)
    roadmap = _make_roadmap(opp_rows)

    engine._promote_top_opportunities(opp_rows, score_rows, 1, roadmap["roadmap_id"])
    engine._promote_phase_opportunities(roadmap, opp_rows, score_rows, 1)
    first = _count_tasks(kanban_env)

    # Re-run over the SAME opportunities: per-opportunity idempotency_key must
    # prevent any duplicate tasks.
    again_top = engine._promote_top_opportunities(opp_rows, score_rows, 1, roadmap["roadmap_id"])
    again_phase = engine._promote_phase_opportunities(roadmap, opp_rows, score_rows, 1)
    second = _count_tasks(kanban_env)

    assert again_top == 0 and again_phase == 0
    assert len(second) == len(first) == cap


def test_no_raw_kanban_inserts_in_aiify():
    """grep -rn 'INSERT INTO kanban_tasks' tools/aiify/ must return nothing."""
    root = pathlib.Path(__file__).resolve().parent.parent
    aiify_dir = root / "tools" / "aiify"
    offenders = []
    for py in aiify_dir.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        if "INSERT INTO kanban_tasks" in text:
            offenders.append(str(py))
    assert not offenders, f"raw kanban INSERT found in: {offenders}"
