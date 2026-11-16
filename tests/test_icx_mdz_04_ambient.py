# CUI // SP-CTI
"""A planned wave's estimate and the bundle preview's new fields on whatever the platform database is (icx-mdz-04).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: the exporter reads a plan's
``migration_tasks`` (each task's component and ``estimated_hours``, a ``real``) and the application's
``legacy_components`` to sum each wave's estimate, and the Modernisation page's bundle preview reads the latest
``migration_assessments`` row for the 7R's confidence. In the normal suite it is the conftest SQLite database
(``wf_db``), with the tables created from their definitions of record in ``tools/db/init_icdev_db.py``.

No fixture redirects the backend on PG or opens a raw connection. Every row this file writes is keyed by an id it mints,
and removed afterwards (none is an append-only table).
"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TABLES = ("projects", "legacy_applications", "legacy_components", "legacy_dependencies", "legacy_apis", "legacy_db_schemas",
          "migration_plans", "migration_tasks", "migration_assessments")


def _pg() -> bool:
    return os.environ.get("ICDEV_PYTEST_PG", "").lower() in ("1", "true", "yes")


@pytest.fixture(autouse=True)
def platform_db(request, tmp_path, monkeypatch):
    """The conftest SQLite platform DB with the tables of record, unless this is the PG tier (the ambient PG)."""
    if _pg():
        return None
    db = request.getfixturevalue("wf_db")
    monkeypatch.setenv("MC_STORAGE_BACKEND", "sqlite")          # CAM's canvas database: this test's own
    monkeypatch.setenv("MC_DB_PATH", str(tmp_path / "mc.db"))
    monkeypatch.setenv("CAM_ARTIFACTS_DIR", str(tmp_path / "cam"))
    from tools.db.storage import get_connection
    from tools.migration_canvas.db.init_db import init_db

    init_db()

    text = (ROOT / "tools" / "db" / "init_icdev_db.py").read_text(encoding="utf-8")
    conn = get_connection()
    try:
        for t in TABLES:
            conn.execute(re.search(rf"CREATE TABLE IF NOT EXISTS {t} \(.*?\n\);", text, re.S).group(0))
        conn.commit()
    finally:
        conn.close()
    return db


@pytest.fixture()
def app():
    from tools.db.storage import get_connection

    a = f"mdz04-app-{uuid.uuid4().hex[:8]}"
    rows = [
        ("INSERT INTO legacy_applications (id, project_id, name, source_path, primary_language) VALUES (%s, %s, %s, %s, %s)",
         (a, f"{a}-proj", f"{a}-shop", "/nowhere", "java")),
        ("INSERT INTO legacy_components (id, legacy_app_id, name, qualified_name, component_type, file_path) "
         "VALUES (%s, %s, %s, %s, %s, %s)", (f"{a}-c1", a, "Cart", f"{a}.Cart", "class", "Cart.java")),
        ("INSERT INTO legacy_components (id, legacy_app_id, name, qualified_name, component_type, file_path) "
         "VALUES (%s, %s, %s, %s, %s, %s)", (f"{a}-c2", a, "Order", f"{a}.Order", "class", "Order.java")),
        ("INSERT INTO migration_plans (id, legacy_app_id, plan_name, strategy, status, created_at) "
         "VALUES (%s, %s, %s, %s, %s, %s)", (f"{a}-plan", a, "rehost", "rehost", "draft", "2026-11-16T00:00:00Z")),
        ("INSERT INTO migration_tasks (id, plan_id, legacy_component_id, task_type, title, estimated_hours) "
         "VALUES (%s, %s, %s, %s, %s, %s)", (f"{a}-t1", f"{a}-plan", f"{a}-c1", "deploy", "deploy cart", 6.5)),
        ("INSERT INTO migration_tasks (id, plan_id, legacy_component_id, task_type, title, estimated_hours) "
         "VALUES (%s, %s, %s, %s, %s, %s)", (f"{a}-t2", f"{a}-plan", f"{a}-c1", "validate", "validate cart", 2.0)),
        ("INSERT INTO migration_assessments (id, legacy_app_id, assessment_scope, recommended_strategy, evidence, "
         "assessed_at) VALUES (%s, %s, %s, %s, %s, %s)",
         (f"{a}-7r", a, "application", "rehost", json.dumps({
             "ranking": [{"strategy_id": "rehost", "score": 0.8}],
             "strategies": {"rehost": {"score": 0.8, "confidence": 0.7, "assessable": True, "unmeasured": ["x"]}},
             "recommendation": {"status": "recommended", "strategy": "rehost", "confidence": 0.7}}),
          "2026-11-16T00:00:00Z")),
    ]
    if not _pg():   # the SQLite definitions of record reference projects(id); the schema of record (PG) does not
        rows.insert(0, ("INSERT INTO projects (id, name, type, directory_path) VALUES (%s, %s, %s, %s)",
                        (f"{a}-proj", "mdz04", "webapp", "/nowhere")))
    conn = get_connection()
    try:
        for sql, params in rows:
            conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()
    yield a
    conn = get_connection()
    try:
        for t, col in (("migration_assessments", "legacy_app_id"), ("migration_tasks", "plan_id"),
                       ("migration_plans", "legacy_app_id"), ("legacy_components", "legacy_app_id"),
                       ("legacy_applications", "id")):
            conn.execute(f"DELETE FROM {t} WHERE {col} = %s", (f"{a}-plan" if col == "plan_id" else a,))  # nosec B608
        conn.commit()
    finally:
        conn.close()


def test_a_planned_waves_estimate_is_read_from_its_tasks(app):
    from tools.db.storage import get_connection
    from tools.modernization import apptwin_export as ax

    waves = {"waves": [{"id": "w1", "name": "Wave 1", "wave_number": 1, "status": "planned", "components": [f"{app}.Cart"]},
                       {"id": "w2", "name": "Wave 2", "wave_number": 2, "status": "planned", "components": [f"{app}.Order"]}]}
    conn = get_connection()
    try:
        b = ax.build(app, conn, at="2026-11-16T00:00:00Z", waves=waves)
    finally:
        conn.close()
    got = {w["id"]: w.get("estimated_hours") for w in b["migration"]["waves"]}
    assert got == {"w1": 8.5, "w2": None}, "the sum of its tasks' estimates; none where no task is on it"
    assert ax.validate(b) == []


def test_the_bundle_preview_carries_the_7r_confidence_and_the_unreported_domain(app):
    from tools.migration_canvas.modernisation import bundle_preview

    d = bundle_preview(app)
    assert d["sections"] is not None, d
    assert d["seven_r"]["recommended"] == "rehost" and d["seven_r"]["confidence"] == 0.7
    assert d["seven_r"]["ranked"][0]["unmeasured"] == ["x"]
    assert d["security_domain"] is None and d["environment_report"]["declared"] is False
