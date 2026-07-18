# CUI // SP-CTI
"""penta-aimc-05 — AIMC blueprint route smoke + engine units + catalog enhancement.

* Route smoke: every AIMC blueprint route responds without a 500 (page routes are
  exercised with render_template patched, mirroring tests/test_dcpr_blueprint_resilience.py,
  because the standalone app has no nav_tree context processor for base.html).
* Units for governance_assessor / adaptation_engine / deployment_planner on
  crafted designs.
* The /model-catalog enhancement threads real scanned aimc_models inventory into
  the page and exposes it via /api/scanned-inventory, degrading to no-data.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture
def app(icdev_db, tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))

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


# ── Route smoke: no 500 anywhere ─────────────────────────────────────────────

_DUMMY = {
    "design_id": "d1",
    "template_id": "tpl-rag-govdoc",
    "snippet_id": "snp-rag-core",
    "version_id": "v1",
    "model_id": "qwen3-local",
    "assessment_id": "a1",
}


def _concrete_path(rule) -> str:
    path = rule.rule
    for arg in rule.arguments:
        path = path.replace(f"<{arg}>", _DUMMY.get(arg, "x"))
    return path


def test_no_blueprint_route_returns_500(app, client):
    headers = {"X-Test-Role": "admin"}
    problems: list[str] = []

    with patch(
        "tools.aiml_canvas.blueprint.render_template",
        side_effect=lambda tmpl, **ctx: f"RENDERED::{tmpl}",
    ):
        for rule in app.url_map.iter_rules():
            if rule.endpoint == "static":
                continue
            methods = (rule.methods or set()) - {"HEAD", "OPTIONS"}
            path = _concrete_path(rule)
            for method in sorted(methods):
                fn = getattr(client, method.lower())
                if method in ("POST", "PUT"):
                    resp = fn(path, headers=headers, json={})
                else:
                    resp = fn(path, headers=headers)
                assert resp.status_code < 600
                if resp.status_code == 500:
                    problems.append(
                        f"{method} {path} -> 500 :: "
                        f"{resp.get_data(as_text=True)[:200]}"
                    )

    assert not problems, "routes returned 500:\n" + "\n".join(problems)


def test_page_routes_render_2xx_with_patched_template(app, client):
    """Read-only page routes render (200) when base.html is stubbed out."""
    page_paths = ["/ai-ml/", "/ai-ml/canvas/new", "/ai-ml/templates",
                  "/ai-ml/snippets", "/ai-ml/model-catalog", "/ai-ml/modernize"]
    with patch(
        "tools.aiml_canvas.blueprint.render_template",
        side_effect=lambda tmpl, **ctx: f"RENDERED::{tmpl}",
    ):
        for path in page_paths:
            resp = client.get(path, headers={"X-Test-Role": "developer"})
            assert resp.status_code == 200, f"{path} -> {resp.status_code}"


# ── /model-catalog enhancement + /api/scanned-inventory ──────────────────────

def test_scanned_inventory_api_no_data(client):
    resp = client.get("/ai-ml/api/scanned-inventory?project_id=empty-proj")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "no-data"
    assert body["project_id"] == "empty-proj"


def test_model_catalog_threads_catalog_and_scanned_context(app, client):
    captured: dict = {}

    def _fake_render(tmpl, **ctx):
        captured.update(ctx)
        return "RENDERED"

    with patch("tools.aiml_canvas.blueprint.render_template", side_effect=_fake_render):
        resp = client.get("/ai-ml/model-catalog", headers={"X-Test-Role": "developer"})
    assert resp.status_code == 200
    # catalog source always present
    assert captured["models"], "static FOUNDATION_MODELS catalog missing"
    # scanned source threaded through (None here — no inventory recorded)
    assert "scanned" in captured
    assert captured["scanned"] is None
    assert captured["scanned_project_id"] == "default"


def test_model_catalog_surfaces_scanned_when_inventory_present(app, client, monkeypatch):
    import tools.aiml_canvas.blueprint as bp_mod

    fake = {
        "status": "success",
        "inventory": {"model_count": 3},
        "frameworks": ["pytorch"],
        "deployment_targets": ["ollama"],
        "missing_controls": [{"key": "ab_testing_enabled", "label": "A/B Testing"}],
        "governance_score": 71.4,
        "project_id": "proj-x",
    }
    monkeypatch.setattr(bp_mod, "_load_scanned_inventory", lambda pid: fake)

    captured: dict = {}

    def _fake_render(tmpl, **ctx):
        captured.update(ctx)
        return "RENDERED"

    with patch("tools.aiml_canvas.blueprint.render_template", side_effect=_fake_render):
        resp = client.get("/ai-ml/model-catalog?project_id=proj-x",
                          headers={"X-Test-Role": "developer"})
    assert resp.status_code == 200
    assert captured["scanned"] == fake
    assert captured["models"]  # catalog still present alongside scanned


@pytest.mark.parametrize("prefix", ["tools", "icdev/tools"])
def test_model_catalog_template_has_both_sources(prefix):
    """Source guard (tools/ + icdev/ mirror): template renders catalog + scanned."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    tpl = root / prefix / "dashboard" / "templates" / "aiml_canvas" / "model_catalog.html"
    assert tpl.exists(), tpl
    src = tpl.read_text(encoding="utf-8")
    assert "src-catalog" in src
    assert "src-scanned" in src
    assert "Scanned Model Inventory" in src
    assert "scanned.status == 'success'" in src


# ── Engine units: governance_assessor ────────────────────────────────────────

def _gov_graph():
    return {
        "nodes": [
            {"id": "n1", "type": "model-llm", "label": "LLM",
             "properties_json": {"model_id": "qwen3-local"}},
            {"id": "n2", "type": "safety-guardrail", "label": "Guard"},
            {"id": "n3", "type": "safety-output-validator", "label": "Validator"},
            {"id": "n4", "type": "gov-model-card", "label": "Card"},
            {"id": "n5", "type": "gov-nist-ai-rmf", "label": "RMF"},
            {"id": "n6", "type": "gov-dod-rai", "label": "RAI"},
            {"id": "n7", "type": "eval-benchmark", "label": "Bench"},
            {"id": "n8", "type": "deploy-ollama", "label": "Ollama"},
        ],
        "edges": [],
    }


def test_governance_run_all_structure():
    from tools.aiml_canvas import governance_assessor as gov

    result = gov.run_all(_gov_graph(), {"id": "d1", "il_level": "IL4"})
    assert 0 <= result["overall_score"] <= 100
    for key in ("dod_rai", "il_suitability", "omm_m25_21", "external_assessments"):
        assert key in result
    assert isinstance(result["external_assessments"], list)


def test_governance_dod_rai_scores_each_principle():
    from tools.aiml_canvas import governance_assessor as gov

    rai = gov.assess_dod_rai(_gov_graph(), {"il_level": "IL4"})
    assert rai["framework_id"] == "dod-rai"
    assert len(rai["findings"]) == 5
    assert all(0 <= f["score"] <= 100 for f in rai["findings"])


# ── Engine units: adaptation_engine ──────────────────────────────────────────

def test_adaptation_recommends_rag_for_corpus():
    from tools.aiml_canvas import adaptation_engine as adapt

    rec = adapt.recommend(has_corpus=True, requires_source_citation=True,
                          knowledge_changes_frequently=True, il_level="IL4")
    assert rec["recommended"] == "rag"
    assert rec["ranked"][0]["strategy"] == "rag"
    assert rec["rationale"]


def test_adaptation_recommends_finetune_for_training_data():
    from tools.aiml_canvas import adaptation_engine as adapt

    rec = adapt.recommend(has_training_data=True, training_examples=2000,
                          has_gpu=True, vram_gb=24, domain_specific_reasoning=True,
                          requires_consistent_format=True, accuracy_target_pct=95,
                          il_level="IL5")
    assert rec["recommended"] == "finetune"


def test_rank_models_for_il_returns_list():
    from tools.aiml_canvas import adaptation_engine as adapt

    ranked = adapt.rank_models_for_il("IL6")
    assert isinstance(ranked, list) and ranked
    # IL6 requires air-gap — top model should be air-gap ready
    assert ranked[0].get("air_gap_ready") in (True, 1)


# ── Engine units: deployment_planner ─────────────────────────────────────────

def test_deployment_plan_known_model():
    from tools.aiml_canvas import deployment_planner as dep

    plan = dep.plan(model_id="qwen3-local", il_level="IL5", vram_gb=16)
    assert plan["model"]["id"] == "qwen3-local"
    assert "quantization" in plan
    assert "inference_server" in plan
    assert "warnings" in plan
    assert plan["il_assessment"]["il_level"] == "IL5"


def test_deployment_plan_unknown_model_returns_error():
    from tools.aiml_canvas import deployment_planner as dep

    plan = dep.plan(model_id="nonexistent-model")
    assert "error" in plan
