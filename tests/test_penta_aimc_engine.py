# CUI // SP-CTI
"""penta-aimc-05 — aiml_engine unit coverage (CRUD / assessment / artifact / versions).

Every case exercises the engine through the canvas DB connection layer
(``tools/aiml_canvas/db/init_db.py``) against a temp SQLite file — never raw
sqlite3 against a production path.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def engine(tmp_path, monkeypatch):
    """aiml_engine wired to a fresh, seeded temp AIMC canvas DB (SQLite)."""
    import tools.aiml_canvas.db.init_db as aimc_init

    monkeypatch.setattr(aimc_init, "_AIMC_BACKEND", "sqlite")
    monkeypatch.setattr(aimc_init, "DB_PATH", tmp_path / "aiml_canvas.db")
    aimc_init.init_db(verbose=False)

    import tools.aiml_canvas.aiml_engine as eng

    return eng


def _rag_graph():
    return {
        "nodes": [
            {"id": "n1", "type": "data-corpus", "label": "Corpus"},
            {"id": "n2", "type": "model-llm", "label": "Qwen3",
             "properties_json": {"model_id": "qwen3-local"}},
            {"id": "n3", "type": "safety-guardrail", "label": "Guard"},
            {"id": "n4", "type": "safety-output-validator", "label": "Validator"},
            {"id": "n5", "type": "gov-model-card", "label": "Card"},
            {"id": "n6", "type": "deploy-ollama", "label": "Ollama"},
        ],
        "edges": [
            {"id": "e1", "source": "n3", "target": "n2", "type": "safety-check"},
            {"id": "e2", "source": "n2", "target": "n4", "type": "data-flow"},
            {"id": "e3", "source": "n5", "target": "n2", "type": "governance"},
            {"id": "e4", "source": "n2", "target": "n6", "type": "deployment"},
        ],
        "boundaries": [],
    }


# ── CRUD ─────────────────────────────────────────────────────────────────────

def test_create_and_get_design(engine):
    d = engine.create_design(name="D1", il_level="IL4", primary_use_case="qa")
    assert d["id"]
    got = engine.get_design(d["id"])
    assert got is not None
    assert got["name"] == "D1"
    assert got["il_level"] == "IL4"
    assert isinstance(got["graph"], dict)


def test_get_missing_design_returns_none(engine):
    assert engine.get_design("does-not-exist") is None


def test_list_designs(engine):
    engine.create_design(name="A")
    engine.create_design(name="B")
    names = {d["name"] for d in engine.list_designs()}
    assert {"A", "B"} <= names


def test_save_design_persists_graph(engine):
    d = engine.create_design(name="Save")
    saved = engine.save_design(d["id"], graph=_rag_graph(), name="Renamed")
    assert saved["name"] == "Renamed"
    assert len(saved["graph"]["nodes"]) == 6


def test_save_design_unknown_raises(engine):
    with pytest.raises(ValueError):
        engine.save_design("nope", graph={"nodes": [], "edges": []})


def test_delete_design(engine):
    d = engine.create_design(name="Del")
    assert engine.delete_design(d["id"]) is True
    assert engine.get_design(d["id"]) is None
    # deleting again is a no-op / False, never an error
    assert engine.delete_design(d["id"]) is False


# ── Versions ─────────────────────────────────────────────────────────────────

def test_save_creates_version_snapshots(engine):
    d = engine.create_design(name="Ver")
    engine.save_design(d["id"], graph=_rag_graph())
    engine.save_design(d["id"], graph={"nodes": [], "edges": [], "boundaries": []})
    versions = engine.list_versions(d["id"])
    assert len(versions) == 2
    # newest first (descending version_number)
    assert versions[0]["version_number"] == 2
    assert versions[1]["version_number"] == 1


def test_get_version_roundtrip(engine):
    d = engine.create_design(name="Ver2")
    engine.save_design(d["id"], graph=_rag_graph())
    vid = engine.list_versions(d["id"])[0]["id"]
    v = engine.get_version(vid)
    assert v is not None
    assert v["design_id"] == d["id"]
    assert isinstance(v["graph"], dict)
    assert engine.get_version("ghost-version") is None


# ── Assessment ───────────────────────────────────────────────────────────────

def test_run_assessment_persists_and_scores(engine):
    d = engine.create_design(name="Assess", il_level="IL4")
    engine.save_design(d["id"], graph=_rag_graph())
    result = engine.run_assessment(d["id"])
    assert result["design_id"] == d["id"]
    assert 0 <= result["score"] <= 100
    assert result["findings"]
    assert result["summary"]["total"] == len(result["findings"])
    # persisted — stats should now count it as assessed
    stats = engine.get_stats()
    assert stats["assessed_designs"] >= 1


def test_run_assessment_unknown_raises(engine):
    with pytest.raises(ValueError):
        engine.run_assessment("ghost")


def test_assess_graph_pure_function(engine):
    findings = engine.assess_graph(_rag_graph(), {"il_level": "IL4", "name": "x"})
    assert findings and all("passed" in f and "severity" in f for f in findings)


# ── Artifacts ────────────────────────────────────────────────────────────────

def test_generate_model_card_artifact(engine):
    d = engine.create_design(name="Card", il_level="IL4")
    engine.save_design(d["id"], graph=_rag_graph())
    art = engine.generate_model_card(d["id"])
    assert art["type"] == "model-card"
    assert art["artifact_id"]
    assert art["content"]["model_details"]["model_id"] == "qwen3-local"


def test_generate_model_card_unknown_raises(engine):
    with pytest.raises(ValueError):
        engine.generate_model_card("ghost")


def test_generate_deployment_manifest_artifact(engine):
    d = engine.create_design(name="Manifest", il_level="IL4")
    engine.save_design(d["id"], graph=_rag_graph())
    art = engine.generate_deployment_manifest(d["id"])
    assert art["type"] == "deploy-manifest"
    assert art["artifact_id"]
    # the deploy-ollama node yields an ollama service in the compose manifest
    assert art["content"]["services"], "expected at least one deploy service"


# ── Templates / snippets / stats ────────────────────────────────────────────

def test_seed_templates_and_snippets_loaded(engine):
    templates = engine.list_templates()
    snippets = engine.list_snippets()
    assert len(templates) >= 4
    assert len(snippets) >= 4
    # get_template resolves the graph_json into a dict
    tpl = engine.get_template(templates[0]["id"])
    assert tpl is not None and isinstance(tpl["graph"], dict)
    assert engine.get_template("no-such-template") is None


def test_stats_shape(engine):
    engine.create_design(name="S1", il_level="IL4", adaptation_strategy="rag")
    stats = engine.get_stats()
    assert stats["total_designs"] >= 1
    assert "by_strategy" in stats and "by_il" in stats
    assert "avg_compliance_score" in stats
