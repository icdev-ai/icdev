# CUI // SP-CTI
"""penta-aimc-04 — P2 batch fixes for the AI/ML Canvas (AIMC).

Covers:
  * the assess-gov AI-trace decision records the real ``overall_score`` rather
    than the ``'?'`` placeholder (governance_assessor.run_all returns
    ``overall_score``, not ``score``);
  * the /api/ai-trace route uses the sql_placeholder() pattern (works against
    SQLite) and logs query failures instead of swallowing them;
  * governance_assessor.run_all surfaces unavailable external assessors with an
    explicit ``status: unavailable`` entry instead of dropping them; and
  * the canvas page emits design data through ``| tojson`` so a script-breakout
    payload in a design name cannot escape the inline <script> block.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def app(icdev_db, tmp_path, monkeypatch):
    # RLS-aware icdev.db (canvas_ai_decisions lives here) — pre-seeded schema.
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))

    # Canvas DB (aiml_*) points at a fresh temp SQLite file.
    import tools.aiml_canvas.db.init_db as aimc_init
    monkeypatch.setattr(aimc_init, "_AIMC_BACKEND", "sqlite")
    monkeypatch.setattr(aimc_init, "DB_PATH", tmp_path / "aiml_canvas.db")

    from flask import Flask, g, request
    from tools.aiml_canvas.blueprint import create_aiml_blueprint

    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"

    @flask_app.before_request
    def _fake_auth():
        role = request.headers.get("X-Test-Role")
        if role:
            g.current_user = {"id": "u-test", "role": role, "tenant_id": "t-test"}

    bp = create_aiml_blueprint()
    assert bp is not None
    flask_app.register_blueprint(bp, url_prefix="/ai-ml")
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _create_design(client, name="Test Design"):
    resp = client.post(
        "/ai-ml/api/designs", headers={"X-Test-Role": "admin"}, json={"name": name}
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["id"]


# ── (3) score key fix ────────────────────────────────────────────────────────

def test_assess_gov_records_overall_score_not_placeholder(client, monkeypatch):
    """The AI-trace decision string must carry the numeric overall_score."""
    import tools.aiml_canvas.blueprint as bp_mod

    captured: dict = {}

    def _capture(**kw):
        captured.update(kw)
        return "decision-id"

    monkeypatch.setattr(bp_mod, "_record_decision", _capture)

    did = _create_design(client)
    resp = client.post(
        f"/ai-ml/api/designs/{did}/assess-gov",
        headers={"X-Test-Role": "admin"},
        json={},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert "overall_score" in body

    assert captured, "record_canvas_decision was never invoked"
    decision = captured.get("decision", "")
    # Regression guard: pre-fix this was result.get('score','?') -> always '?'.
    assert "score=?" not in decision
    assert str(body["overall_score"]) in decision


# ── (6) unavailable external-framework surfacing ─────────────────────────────

def test_run_all_surfaces_unavailable_external_frameworks(monkeypatch):
    import importlib

    from tools.aiml_canvas import governance_assessor as gov

    real_import = importlib.import_module

    def _fake_import(name, *args, **kwargs):
        if name.startswith("tools.compliance."):
            raise ImportError(f"forced unavailable: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", _fake_import)

    design = {"id": "d-x", "il_level": "IL4"}
    graph = {"nodes": [], "edges": []}
    result = gov.run_all(graph, design)

    ext = result["external_assessments"]
    by_id = {e["framework_id"]: e for e in ext}
    for fw in ("nist-ai-rmf", "owasp-llm", "mitre-atlas"):
        assert fw in by_id, f"{fw} was dropped from external_assessments"
        assert by_id[fw]["status"] == "unavailable"
        assert by_id[fw].get("reason"), f"{fw} missing failure reason"

    # Internal frameworks still scored normally.
    assert "overall_score" in result
    assert "dod_rai" in result and "il_suitability" in result and "omm_m25_21" in result


def test_run_all_external_entries_always_have_status(monkeypatch):
    """Whether an assessor is present or not, every entry is tagged with status."""
    from tools.aiml_canvas import governance_assessor as gov

    result = gov.run_all({"nodes": [], "edges": []}, {"id": "d-y", "il_level": "IL4"})
    for entry in result["external_assessments"]:
        assert "framework_id" in entry
        assert entry.get("status") in ("assessed", "unavailable")


# ── (2) ai-trace: sql_placeholder + failure logging ──────────────────────────

def test_ai_trace_logs_on_query_failure(client, monkeypatch):
    import tools.aiml_canvas.blueprint as bp_mod

    warnings: list = []

    class _CapLogger:
        def warning(self, *a, **k):
            warnings.append(a)

        def info(self, *a, **k):
            pass

        def debug(self, *a, **k):
            pass

    monkeypatch.setattr(bp_mod, "log", _CapLogger())

    # Shim-aware: `tools.db.storage` resolves to icdev.tools.db.storage; patch the
    # resolved module object so the route's `from tools.db.storage import
    # get_connection` picks up the boom.
    import importlib

    storage = importlib.import_module("tools.db.storage")

    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(storage, "get_connection", _boom)

    resp = client.get("/ai-ml/api/ai-trace")
    assert resp.status_code == 500
    body = resp.get_json()
    assert body["ok"] is False
    assert warnings, "log.warning was not called on ai-trace query failure"
    assert "ai-trace query failed" in warnings[0][0]


def test_ai_trace_returns_recorded_decisions_over_sqlite(client):
    """Happy path proves the sql_placeholder() rewrite runs on SQLite.

    A hardcoded ``%s`` placeholder would raise against SQLite and 500; the
    placeholder-derived query returns the aimc decision the assessment wrote.
    """
    did = _create_design(client)
    r1 = client.post(
        f"/ai-ml/api/designs/{did}/assess-gov",
        headers={"X-Test-Role": "admin"},
        json={},
    )
    assert r1.status_code == 200, r1.get_data(as_text=True)

    resp = client.get("/ai-ml/api/ai-trace")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["ok"] is True
    assert body["canvas"] == "aimc"
    assert isinstance(body["decisions"], list)
    assert any(d.get("record_id") == did for d in body["decisions"]), body

    # record_id filter path also exercises the placeholder rewrite.
    resp2 = client.get(f"/ai-ml/api/ai-trace?record_id={did}")
    assert resp2.status_code == 200, resp2.get_data(as_text=True)
    assert resp2.get_json()["ok"] is True


# ── (5) tojson script-injection hardening ────────────────────────────────────

@pytest.mark.parametrize("prefix", ["tools", "icdev/tools"])
def test_canvas_template_uses_tojson_not_safe(prefix):
    """Source guard (tools/ + icdev/ mirror): the inline-<script> design
    injections use | tojson, and the old | safe injections are gone."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    tpl = root / prefix / "dashboard" / "templates" / "aiml_canvas" / "canvas.html"
    assert tpl.exists(), tpl
    src = tpl.read_text(encoding="utf-8")
    assert "design_id | tojson" in src
    assert "design | tojson" in src
    assert "design_id | safe" not in src
    assert "design_json | safe" not in src


def test_tojson_escapes_script_breakout(app):
    """Behavioural proof that | tojson (Flask's htmlsafe encoder) neutralises a
    script-breakout payload in a design name — the mechanism the template now
    relies on instead of | safe."""
    from flask import render_template_string

    payload = "</script><script>alert(1)</script>"
    with app.app_context():
        out = render_template_string(
            "var DESIGN_ID = {{ design_id | tojson }};\n"
            "var _d = {{ design | tojson }};",
            design_id=payload,
            design={"id": "d1", "name": payload},
        )

    # Raw breakout does not survive.
    assert payload not in out
    # '<' is escaped to < so the inline <script> block cannot be closed.
    assert "\\u003c/script\\u003e" in out
    # DESIGN_ID is emitted as a quoted JSON literal, not a bare single-quoted string.
    assert 'var DESIGN_ID = "' in out
