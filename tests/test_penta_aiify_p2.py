# CUI // SP-CTI
"""penta-aiify-06 — P2 batch: placeholders, read-path writes, dedupe, migrations.

Verifies the structural cleanups:
  1. SQL placeholder consistency — engine.py has no ``_exec`` blind retry and no
     raw ``?`` placeholders; blueprint IN-clauses use ``%s`` (one style through
     the translating connection).
  2. index() performs NO write (the summary UPDATE backfill was removed from the
     GET read path; the summary is displayed but not persisted on read).
  3. Shared helpers dedupe — tools/aiify/prd_common.py is imported by the blueprint
     and produces the canonical phase priority + 4-step decomposition.
  4. Orderly migrations — tools/aiify/db/migrations/ exists with a versioned
     baseline; init_db tracks it in aiify_schema_migrations (no blind ALTER loop).
"""
from __future__ import annotations

import pathlib
import re

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Placeholder consistency (source inspection — cheap, no DB)
# ─────────────────────────────────────────────────────────────────────────────

def _sql_lines(src: str) -> list[str]:
    """Return string-literal lines that look like SQL (contain a SQL keyword)."""
    out = []
    for ln in src.split("\n"):
        s = ln.strip()
        if not (s.startswith('"') or s.startswith("'")):
            continue
        if re.search(r"\b(INSERT|UPDATE|DELETE|SELECT|VALUES|SET|WHERE)\b", s):
            out.append(s)
    return out


def test_engine_has_no_exec_retry_helper():
    src = (_REPO / "tools" / "aiify" / "engine.py").read_text(encoding="utf-8")
    assert "def _exec(" not in src, "blind _exec retry helper must be removed"
    # No lingering call sites either.
    assert "_exec(" not in src


def test_engine_sql_uses_percent_s_not_qmark():
    src = (_REPO / "tools" / "aiify" / "engine.py").read_text(encoding="utf-8")
    for line in _sql_lines(src):
        # No bare ? placeholder (VALUES (?, ...), = ?, IN (?)) — %s only.
        assert not re.search(r"\(\s*\?", line), f"qmark placeholder in: {line}"
        assert "= ?" not in line, f"qmark placeholder in: {line}"
        assert ", ?" not in line and "(?," not in line, f"qmark placeholder in: {line}"


def test_blueprint_in_clause_uses_percent_s():
    src = (_REPO / "tools" / "aiify" / "blueprint.py").read_text(encoding="utf-8")
    # The dynamic IN(...) builders must emit %s, not ?, so PG is not broken.
    assert '",".join("?"' not in src
    assert '["%s"]' in src


# ─────────────────────────────────────────────────────────────────────────────
# Flask harness (same seam as the route suites)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))
    monkeypatch.setenv("AIIFY_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("AIIFY_DB_PATH", str(tmp_path / "aiify_canvas.db"))

    from flask import Flask, g, request
    import tools.aiify.blueprint as bp
    import tools.aiify.db.init_db as init_db
    from tools.db.storage import get_canvas_connection

    _default_dbpath = init_db._ICDEV_ROOT / "data" / "aiify_canvas.db"
    _orig_init_done = bp._INIT_DONE
    init_db.DB_PATH = tmp_path / "aiify_canvas.db"
    bp._INIT_DONE = False
    monkeypatch.setattr(bp, "_conn", lambda: get_canvas_connection("AIIFY_DB_PATH"))
    monkeypatch.setattr(bp, "render_template", lambda *a, **k: "RENDERED")

    app = Flask(__name__)
    app.secret_key = "test-secret"

    @app.before_request
    def _fake_auth():
        role = request.headers.get("X-Test-Role")
        if role:
            g.current_user = {"id": "u-test", "role": role, "tenant_id": "t-test"}

    app.register_blueprint(bp.aiify_bp)
    try:
        yield app
    finally:
        init_db.DB_PATH = _default_dbpath
        bp._INIT_DONE = _orig_init_done


@pytest.fixture
def client(app):
    return app.test_client()


def _cx():
    from tools.db.storage import get_canvas_connection
    return get_canvas_connection("AIIFY_DB_PATH")


# ─────────────────────────────────────────────────────────────────────────────
# 2. index() performs NO write
# ─────────────────────────────────────────────────────────────────────────────

def test_index_does_not_persist_summary(client, app):
    # Seed a scan with a NULL project_summary and one opportunity so the display
    # summary would be computed — but the read path must NOT write it back.
    with app.app_context():
        import tools.aiify.db.init_db as init_db
        init_db.init_db()
        conn = _cx()
        try:
            conn.execute(
                "INSERT INTO aiify_scans (scan_id, input_type, input_ref, status) "
                "VALUES (%s, %s, %s, %s)", (1, "path", "tools/demo", "completed"),
            )
            conn.execute(
                "INSERT INTO aiify_opportunities (opportunity_id, scan_id, module_path, "
                "function_name, language, pattern_type, ai_paradigm) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (1, 1, "tools/demo.py", "run", "python", "hardcoded_threshold",
                 "anomaly_detection"),
            )
            conn.commit()
        finally:
            conn.close()

    resp = client.get("/ai-ify/", headers={"X-Test-Role": "admin"})
    assert resp.status_code == 200

    with app.app_context():
        conn = _cx()
        try:
            row = conn.execute(
                "SELECT project_summary FROM aiify_scans WHERE scan_id = %s", (1,)
            ).fetchone()
        finally:
            conn.close()
    # Read path is side-effect-free: summary was NOT persisted by the GET.
    assert row["project_summary"] is None


# ─────────────────────────────────────────────────────────────────────────────
# 3. Shared helpers (dedupe verified by import)
# ─────────────────────────────────────────────────────────────────────────────

def test_prd_common_phase_priority_and_key():
    from tools.aiify.prd_common import PHASE_PRIORITY, phase_key
    assert PHASE_PRIORITY == {"P1": "high", "P2": "medium", "P3": "low"}
    assert phase_key({"phase_id": "P2", "label": "P2 — Strategic"}) == "P2"
    assert phase_key({"label": "P1 — Quick Wins"}) == "P1"
    assert phase_key({}) == "P3"


def test_build_task_steps_dependency_chain():
    from tools.aiify.prd_common import build_task_steps
    steps = build_task_steps(
        "aiify-abc123-p1-7", pattern="hardcoded_threshold",
        paradigm="anomaly_detection", module="tools/x.py", fn="run",
        model="claude-haiku-4-5", criterion="Replace threshold with model",
    )
    assert [s["suffix"] for s in steps] == ["d1", "d2", "d3", "d4"]
    assert [s["step"] for s in steps] == ["Design", "Implement", "Test", "Review"]
    # Sequential dependency chain d2->d1, d3->d2, d4->d3; d1 has no dep.
    assert steps[0]["dep"] is None
    assert steps[1]["dep"] == "aiify-abc123-p1-7-d1"
    assert steps[2]["dep"] == "aiify-abc123-p1-7-d2"
    assert steps[3]["dep"] == "aiify-abc123-p1-7-d3"
    # Titles carry the module:fn target and paradigm.
    assert "tools/x.py:run" in steps[0]["title"]
    assert "anomaly_detection" in steps[1]["title"]


def test_blueprint_imports_shared_helpers():
    src = (_REPO / "tools" / "aiify" / "blueprint.py").read_text(encoding="utf-8")
    assert "from tools.aiify.prd_common import" in src
    # The old per-route duplicated literals are gone.
    assert '_PHASE_PRIORITY = {"P1"' not in src
    assert src.count("build_task_steps(") >= 2  # used by send-to-kanban AND preview


def test_load_engine_enrichment_graceful_empty(tmp_path, monkeypatch):
    # Points at an empty icdev DB (no engine tables) — must degrade to empty lists.
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))
    from tools.aiify.prd_common import load_engine_enrichment
    out = load_engine_enrichment(hitl=None, limit=3)
    assert out["innovation"] == [] and out["research"] == [] and out["creative"] == []
    assert out["rejected_innovation"] == []


# ─────────────────────────────────────────────────────────────────────────────
# 4. Orderly migrations
# ─────────────────────────────────────────────────────────────────────────────

def test_migrations_dir_and_baseline_exist():
    mig = _REPO / "tools" / "aiify" / "db" / "migrations"
    assert mig.is_dir(), "registry-declared migrations dir must exist"
    assert (mig / "0001_baseline.sql").is_file()


def test_init_db_tracks_baseline_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("AIIFY_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("AIIFY_DB_PATH", str(tmp_path / "aiify.db"))
    import tools.aiify.db.init_db as init_db
    _orig = init_db.DB_PATH
    init_db.DB_PATH = tmp_path / "aiify.db"
    try:
        init_db.init_db()
        init_db.init_db()  # idempotent — second run must not error or double-apply
        conn = init_db.get_connection()
        try:
            versions = [
                (r["version"] if hasattr(r, "keys") else r[0])
                for r in conn.execute(
                    "SELECT version FROM aiify_schema_migrations"
                ).fetchall()
            ]
        finally:
            conn.close()
    finally:
        init_db.DB_PATH = _orig
    assert versions.count("0001_baseline") == 1


def test_init_db_no_blind_alter_loop():
    src = (_REPO / "tools" / "aiify" / "db" / "init_db.py").read_text(encoding="utf-8")
    # The former blind "try: ALTER ... except: rollback" backfill is replaced by
    # an introspection-based _ensure_columns reconcile.
    assert "def _ensure_columns(" in src
    assert "def _run_file_migrations(" in src
