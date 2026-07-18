# CUI // SP-CTI
"""penta-aimc-02 — AIMC graph persistence + orphan reflex.

Proves that saving/creating an AIMC design via the engine materializes the
graph into the relational aiml_nodes / aiml_edges tables (previously only the
demo seed wrote those tables, so the IQE aimc.nodes adapter and the
aimc_orphan_refs reflex operated on stale demo data), and that the reflex's
model-node filter now matches the *real* model node types from constants.

Tests run against a temp SQLite copy of the AIMC canvas DB through the canvas
DB layer (tools/aiml_canvas/db/init_db.get_connection), never raw sqlite3
against the shared icdev.db.
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def aimc_db(tmp_path, monkeypatch):
    """Point the AIMC canvas DB at a fresh temp SQLite file."""
    import tools.aiml_canvas.db.init_db as aimc_init

    monkeypatch.setattr(aimc_init, "_AIMC_BACKEND", "sqlite")
    monkeypatch.setattr(aimc_init, "DB_PATH", tmp_path / "aiml_canvas.db")
    aimc_init.init_db(verbose=False)
    return aimc_init


def _sample_graph() -> dict:
    return {
        "nodes": [
            {"id": "n1", "type": "model-llm", "label": "Qwen3", "x": 10, "y": 20,
             "properties_json": {"model_id": "qwen3-local"}},
            {"id": "n2", "type": "safety-guardrail", "label": "Guard", "x": 30, "y": 40},
            {"id": "n3", "type": "safety-output-validator", "label": "Validator", "x": 50, "y": 60},
        ],
        "edges": [
            {"id": "e1", "source": "n2", "target": "n1", "type": "safety-check"},
            {"id": "e2", "source": "n1", "target": "n3", "type": "data-flow"},
        ],
        "boundaries": [],
    }


# ── Persistence ────────────────────────────────────────────────────────────────

def test_save_design_populates_nodes_and_edges(aimc_db):
    from tools.aiml_canvas import aiml_engine as eng

    design = eng.create_design(name="Persist Test", il_level="IL4", classification="CUI")
    eng.save_design(design["id"], graph=_sample_graph())

    conn = aimc_db.get_connection()
    try:
        nodes = conn.execute(
            "SELECT id, node_type, properties_json, classification FROM aiml_nodes "
            "WHERE design_id=? ORDER BY id", (design["id"],)
        ).fetchall()
        edges = conn.execute(
            "SELECT id, source_node_id, target_node_id, edge_type FROM aiml_edges "
            "WHERE design_id=? ORDER BY id", (design["id"],)
        ).fetchall()
    finally:
        conn.close()

    assert len(nodes) == 3
    assert len(edges) == 2
    types = {n["node_type"] for n in nodes}
    assert "model-llm" in types
    llm = next(n for n in nodes if n["node_type"] == "model-llm")
    assert "qwen3-local" in llm["properties_json"]
    # classification defaulted from the design when node had none
    assert all(n["classification"] for n in nodes)


def test_save_design_replaces_prior_graph(aimc_db):
    """A subsequent save must not accumulate stale node/edge rows."""
    from tools.aiml_canvas import aiml_engine as eng

    design = eng.create_design(name="Replace Test", il_level="IL4")
    eng.save_design(design["id"], graph=_sample_graph())
    eng.save_design(design["id"], graph={
        "nodes": [{"id": "x1", "type": "model-vlm", "label": "VLM"}],
        "edges": [],
    })

    conn = aimc_db.get_connection()
    try:
        nodes = conn.execute(
            "SELECT node_type FROM aiml_nodes WHERE design_id=?", (design["id"],)
        ).fetchall()
        edges = conn.execute(
            "SELECT id FROM aiml_edges WHERE design_id=?", (design["id"],)
        ).fetchall()
    finally:
        conn.close()

    assert len(nodes) == 1
    assert nodes[0]["node_type"] == "model-vlm"
    assert len(edges) == 0


def test_create_design_with_template_persists_nodes(aimc_db):
    """Creating from a seeded template materializes its graph nodes."""
    from tools.aiml_canvas import aiml_engine as eng

    design = eng.create_design(name="From Template", template_id="tpl-rag-govdoc")
    conn = aimc_db.get_connection()
    try:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM aiml_nodes WHERE design_id=?", (design["id"],)
        ).fetchone()["c"]
    finally:
        conn.close()
    assert count > 0


# ── IQE adapter ────────────────────────────────────────────────────────────────

def test_iqe_nodes_adapter_returns_saved_design_nodes(aimc_db):
    from tools.aiml_canvas import aiml_engine as eng
    from tools.iqe.adapters.aimc import nodes_adapter

    design = eng.create_design(name="IQE Test", il_level="IL4")
    eng.save_design(design["id"], graph=_sample_graph())

    rows = nodes_adapter(None)  # conn=None → adapter opens the canvas connection
    mine = [r for r in rows if r["design_id"] == design["id"]]
    assert len(mine) == 3
    assert any(r["node_type"] == "model-llm" for r in mine)


# ── Orphan reflex ──────────────────────────────────────────────────────────────

def test_reflex_matches_real_model_node_types(aimc_db):
    """The reflex model-node filter must include real palette types, not the
    old dead {foundation-model, model, llm-node, model-node} set."""
    from tools.genesis.reflexes import aimc_orphan_refs as reflex

    assert "model-llm" in reflex._MODEL_NODE_TYPES
    assert "model-vlm" in reflex._MODEL_NODE_TYPES
    # None of the dead placeholder types should remain.
    assert "foundation-model" not in reflex._MODEL_NODE_TYPES
    assert "llm-node" not in reflex._MODEL_NODE_TYPES


def test_reflex_scan_finds_dangling_model_reference(aimc_db):
    from tools.aiml_canvas import aiml_engine as eng
    from tools.genesis.reflexes import aimc_orphan_refs as reflex

    design = eng.create_design(name="Orphan Test", il_level="IL4")
    eng.save_design(design["id"], graph={
        "nodes": [
            {"id": "m1", "type": "model-llm", "label": "Bad",
             "properties_json": {"model_id": "totally-not-in-catalog-xyz"}},
            {"id": "m2", "type": "model-llm", "label": "Good",
             "properties_json": {"model_id": "qwen3-local"}},
        ],
        "edges": [],
    })

    orphans = reflex._scan_orphans()
    orphan_ids = {o["model_id"] for o in orphans}
    assert "totally-not-in-catalog-xyz" in orphan_ids
    # A valid catalog model_id must NOT be flagged.
    assert "qwen3-local" not in orphan_ids


def test_reflex_run_dry_run_reports_orphans(aimc_db, monkeypatch):
    from tools.aiml_canvas import aiml_engine as eng
    from tools.genesis.reflexes import aimc_orphan_refs as reflex

    # Force deterministic offline anomaly decision (no network LLM call).
    monkeypatch.setattr(reflex, "_llm_router", None)

    design = eng.create_design(name="Dry Run Test", il_level="IL4")
    eng.save_design(design["id"], graph={
        "nodes": [
            {"id": "m1", "type": "model-llm", "label": "Bad",
             "properties_json": {"model_id": "nonexistent-model-abc"}},
        ],
        "edges": [],
    })

    result = reflex.run({"dry_run": True}, None)
    assert result["status"] == "ok"
    assert result["dry_run"] is True
    assert result["orphans_found"] >= 1
    assert result["suggestion_id"] is None  # dry-run never writes a Kanban card
