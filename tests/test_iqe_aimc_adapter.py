# CUI // SP-CTI
"""Regression tests for tools/iqe/adapters/aimc.py + tools/aiml_canvas/db/init_db.py
— PG-primary hardening for the AIMC canvas (pgp-vfy-09-d5).

Background
----------
aiml_* tables store nested JSON in TEXT columns (graph_json / properties_json /
findings_json / content_json).  They also carry `classification` but NO
`tenant_id`, so the global RLS predicate injected by get_connection() would
raise UndefinedColumn on every query.  The d2 fix switched the canvas
init_db's PG branch to get_canvas_connection() (security_context=None, RLS
disabled).

The aimc IQE adapter re-uses the canvas init_db get_connection(); these tests
lock in that:
  * nested JSON columns are parsed to Python objects (not left as raw TEXT);
  * the conn=None path goes through the RLS-free canvas connection;
  * the parse+execute IQE pipeline still returns the expected rows.
"""
from __future__ import annotations

import importlib
import json
import sqlite3

from tools.iqe.executor import Executor
from tools.iqe.parser import parse

# Minimal schema matching the columns the aimc IQE adapters touch.
# graph_json / properties_json / findings_json / content_json are TEXT-stored
# JSON arrays/objects (the nested-JSON columns the runtime must NOT process via
# SQLite json_each / json_extract — that path is PG-incompatible).
_SCHEMA = """
CREATE TABLE IF NOT EXISTS aiml_designs (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    description         TEXT DEFAULT '',
    graph_json          TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    template_id         TEXT,
    classification      TEXT DEFAULT 'CUI',
    il_level            TEXT DEFAULT 'IL4',
    primary_use_case    TEXT DEFAULT '',
    adaptation_strategy TEXT DEFAULT 'prompt',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS aiml_nodes (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL REFERENCES aiml_designs(id) ON DELETE CASCADE,
    node_type       TEXT NOT NULL,
    label           TEXT DEFAULT '',
    x               REAL DEFAULT 0,
    y               REAL DEFAULT 0,
    width           REAL DEFAULT 160,
    height          REAL DEFAULT 60,
    classification  TEXT DEFAULT 'CUI',
    properties_json TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS aiml_assessments (
    id             TEXT PRIMARY KEY,
    design_id      TEXT REFERENCES aiml_designs(id) ON DELETE CASCADE,
    framework_id   TEXT NOT NULL,
    framework_name TEXT NOT NULL,
    findings_json  TEXT DEFAULT '[]',
    score          REAL DEFAULT 0.0,
    passed         INTEGER DEFAULT 0,
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS aiml_artifacts (
    id            TEXT PRIMARY KEY,
    design_id     TEXT REFERENCES aiml_designs(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    title         TEXT NOT NULL,
    content_json  TEXT DEFAULT '{}',
    format        TEXT DEFAULT 'json',
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def _make_conn() -> sqlite3.Connection:
    """Seed an in-memory SQLite DB with nested-JSON design + 2 nodes + 1 assessment + 1 artifact."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)

    # Nested graph_json with nodes/edges/boundaries arrays of dictionaries
    nested_graph = {
        "nodes": [
            {"id": "n1", "type": "model-llm", "label": "Qwen3 (Local)", "x": 200, "y": 100},
            {"id": "n2", "type": "safety-guardrail", "label": "Input Guardrail", "x": 400, "y": 100},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2", "type": "safety-check", "label": "validated"},
        ],
        "boundaries": [
            {"id": "b1", "type": "bnd-il-zone", "label": "IL4 — CUI Zone",
             "x": 60, "y": 60, "width": 500, "height": 200, "il_level": "IL4"},
        ],
    }

    conn.execute(
        "INSERT INTO aiml_designs (id, name, description, graph_json, classification, il_level) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "design-rag-il4-001",
            "RAG — Government Document Q&A",
            "Regression fixture: nested graph_json",
            json.dumps(nested_graph),
            "CUI",
            "IL4",
        ),
    )

    # Nested properties_json per node
    conn.execute(
        "INSERT INTO aiml_nodes (id, design_id, node_type, label, x, y, classification, properties_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "n1", "design-rag-il4-001", "model-llm", "Qwen3 (Local)", 200.0, 100.0, "CUI",
            json.dumps({"provider": "ollama", "quantization": "Q4_K_M", "vram_gb": 8,
                        "context_window": 32768, "params": {"temperature": 0.2, "top_p": 0.95}}),
        ),
    )
    conn.execute(
        "INSERT INTO aiml_nodes (id, design_id, node_type, label, x, y, classification, properties_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "n2", "design-rag-il4-001", "safety-guardrail", "Input Guardrail", 400.0, 100.0, "CUI",
            json.dumps({"policy": "cui-il4-only", "blocked_terms": ["ECI", "NOFORN"], "strict": True}),
        ),
    )

    # Nested findings_json (list of dicts) — exercises the list-of-dicts shape
    conn.execute(
        "INSERT INTO aiml_assessments (id, design_id, framework_id, framework_name, "
        "findings_json, score, passed, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "asmt-001", "design-rag-il4-001", "fw-nist-ai-rmf", "NIST AI RMF",
            json.dumps([
                {"severity": "high", "control": "GOVERN-1.2", "msg": "No model card linked."},
                {"severity": "low",  "control": "MANAGE-4.1", "msg": "Telemetry disabled."},
            ]),
            0.82, 1, "2026-06-08T00:00:00",
        ),
    )

    # Nested content_json — exercises an object-of-objects shape
    conn.execute(
        "INSERT INTO aiml_artifacts (id, design_id, artifact_type, title, content_json, format, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "art-001", "design-rag-il4-001", "model-card", "Model Card — Qwen3 RAG",
            json.dumps({
                "summary": "CUI document Q&A with guardrails",
                "intended_use": {"il_level": "IL4", "users": ["analyst", "researcher"]},
                "training_data": {"corpus": "CUI gov docs", "examples": 0, "license": "n/a"},
                "evaluation": {"benchmarks": [{"name": "dodqa-1", "score": 0.71}]},
            }),
            "json", "2026-06-08T00:00:00",
        ),
    )

    conn.commit()
    return conn


# 1 — designs adapter returns the design row -------------------------------

def test_designs_adapter_returns_design_row() -> None:
    from tools.iqe.adapters.aimc import designs_adapter

    conn = _make_conn()
    rows = designs_adapter(conn)
    conn.close()

    assert len(rows) == 1
    d = rows[0]
    assert d["id"] == "design-rag-il4-001"
    assert d["il_level"] == "IL4"
    assert d["classification"] == "CUI"


# 2 — nodes adapter returns the 2 seeded nodes --------------------------------

def test_nodes_adapter_returns_seeded_nodes() -> None:
    from tools.iqe.adapters.aimc import nodes_adapter

    conn = _make_conn()
    rows = nodes_adapter(conn)
    conn.close()

    assert len(rows) == 2
    types = {r["node_type"] for r in rows}
    assert types == {"model-llm", "safety-guardrail"}


# 3 — assessments adapter returns the joined row + nested findings_json -------
#     is decoded by the blueprint on demand (assessment_detail page).
#     The "nested JSON" surface area: list-of-dicts in a TEXT column.

def test_assessments_adapter_returns_joined_row() -> None:
    """The IQE adapter SELECTs a curated set of columns (no nested JSON).
    The blueprint /assessment_detail page decodes findings_json via
    json.loads() — that path is the previously-broken nested-JSON read."""
    from tools.iqe.adapters.aimc import assessments_adapter

    conn = _make_conn()
    rows = assessments_adapter(conn)
    conn.close()

    assert len(rows) == 1
    a = rows[0]
    assert a["framework_id"] == "fw-nist-ai-rmf"
    assert a["score"] == 0.82
    assert a["design_name"] == "RAG — Government Document Q&A"


# 4 — REGRESSION: the nested findings_json round-trips through the canvas
#     init_db connection (the exact decoding pattern the blueprint uses at
#     /assessments/<id>).  This is the previously-failed nested-JSON path:
#     a plain get_connection() raises UndefinedColumn on aiml_assessments in
#     PG (no tenant_id), so the d2 fix swapped it for get_canvas_connection().

def test_nested_findings_json_decodes_via_canvas_connection(monkeypatch) -> None:
    """The exact json.loads() decoding pattern used by
    blueprint.assessment_detail(): fetch a row, decode findings_json.
    Locks in that the read uses the canvas RLS-free connection."""
    seeded = _make_conn()

    # Force the canvas get_connection() to take the PG branch (which the
    # d2 fix rewrote to use get_canvas_connection()).  Reload the module
    # so the env var takes effect at import time.
    monkeypatch.setenv("AIMC_STORAGE_BACKEND", "postgresql")

    storage = importlib.import_module("tools.db.storage")
    canvas_db = importlib.import_module("tools.aiml_canvas.db.init_db")

    def _fake_canvas(*_a, **_k):
        return seeded

    def _forbidden_get_connection(*_a, **_k):
        raise AssertionError(
            "nested-JSON read took the RLS get_connection() path; "
            "must use get_canvas_connection() for aiml_* tables."
        )

    monkeypatch.setattr(storage, "get_canvas_connection", _fake_canvas)
    monkeypatch.setattr(storage, "get_connection", _forbidden_get_connection)

    # Re-import under the new env so the module-level _AIMC_BACKEND
    # re-evaluates to "postgresql".
    importlib.reload(canvas_db)
    canvas_get_conn = canvas_db.get_connection

    conn = canvas_get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM aiml_assessments WHERE id=?", ("asmt-001",)
        ).fetchone()
        assert row is not None
        assessment = dict(row)
        # Same decoding the blueprint uses
        findings = json.loads(assessment.get("findings_json") or "[]")
        assert isinstance(findings, list)
        assert len(findings) == 2
        assert findings[0]["severity"] == "high"
        assert findings[0]["control"] == "GOVERN-1.2"
        assert findings[1]["severity"] == "low"
    finally:
        conn.close()


# 5 — REGRESSION: artifacts.content_json (object-of-objects) round-trips ----

def test_nested_content_json_decodes_via_canvas_connection(monkeypatch) -> None:
    """The content_json column on aiml_artifacts stores a deeply-nested object
    (intended_use {il_level, users} + evaluation {benchmarks [{name, score}]}).
    Same RLS-free read path as test 4 — locks the fix in for artifacts too."""
    seeded = _make_conn()

    monkeypatch.setenv("AIMC_STORAGE_BACKEND", "postgresql")

    storage = importlib.import_module("tools.db.storage")
    canvas_db = importlib.import_module("tools.aiml_canvas.db.init_db")

    def _fake_canvas(*_a, **_k):
        return seeded

    def _forbidden_get_connection(*_a, **_k):
        raise AssertionError(
            "nested-JSON read took the RLS get_connection() path; "
            "must use get_canvas_connection() for aiml_* tables."
        )

    monkeypatch.setattr(storage, "get_canvas_connection", _fake_canvas)
    monkeypatch.setattr(storage, "get_connection", _forbidden_get_connection)

    importlib.reload(canvas_db)
    canvas_get_conn = canvas_db.get_connection

    conn = canvas_get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM aiml_artifacts WHERE id=?", ("art-001",)
        ).fetchone()
        assert row is not None
        content = json.loads(row["content_json"] or "{}")
        assert content["intended_use"]["il_level"] == "IL4"
        assert "analyst" in content["intended_use"]["users"]
        assert content["evaluation"]["benchmarks"][0]["score"] == 0.71
    finally:
        conn.close()


# 6 — REGRESSION: conn=None fetch uses RLS-free canvas connection (the fix) --

def test_nodes_adapter_conn_none_uses_canvas_connection(monkeypatch) -> None:
    """The fix: with no caller conn, the adapter must build its connection
    via the canvas init_db get_connection() (which in PG mode routes to
    get_canvas_connection(), RLS disabled).  We assert the global RLS
    get_connection() from tools.db.storage is NOT used by the adapter's
    default-code path."""
    seeded = _make_conn()

    # Patch the exact module the adapter's local import resolves from.
    storage = importlib.import_module("tools.db.storage")
    canvas_db = importlib.import_module("tools.aiml_canvas.db.init_db")

    def _fake_canvas(*_a, **_k):
        return seeded

    def _forbidden_get_connection(*_a, **_k):
        raise AssertionError(
            "adapter used tools.db.storage.get_connection (RLS path) directly; "
            "must route through tools.aiml_canvas.db.init_db.get_connection "
            "(which uses get_canvas_connection in PG mode)."
        )

    monkeypatch.setattr(storage, "get_canvas_connection", _fake_canvas)
    monkeypatch.setattr(storage, "get_connection", _forbidden_get_connection)
    # Patch the canvas get_connection to return the seeded (RLS-free) conn
    # directly — the test asserts the conn=None path skips the RLS branch.
    monkeypatch.setattr(canvas_db, "get_connection", _fake_canvas)

    from tools.iqe.adapters.aimc import nodes_adapter

    rows = nodes_adapter(None)
    seeded.close()

    assert len(rows) == 2
    types = {r["node_type"] for r in rows}
    assert types == {"model-llm", "safety-guardrail"}


# 7 — smoke: parse + execute foreach over aimc.designs ------------------------

def test_smoke_parse_and_execute_aimc_designs() -> None:
    from tools.iqe.adapters.aimc import designs_adapter

    conn = _make_conn()
    ast = parse("foreach d in aimc.designs select id, name, il_level")
    ex = Executor()
    ex.register_collection("aimc.designs", designs_adapter)
    result = ex.run(ast, conn=conn)
    conn.close()

    assert len(result) == 1
    assert result[0]["id"] == "design-rag-il4-001"
    assert result[0]["il_level"] == "IL4"
    assert result[0]["name"] == "RAG — Government Document Q&A"


# 8 — smoke: parse + execute foreach over aimc.nodes -------------------------

def test_smoke_parse_and_execute_aimc_nodes() -> None:
    from tools.iqe.adapters.aimc import nodes_adapter

    conn = _make_conn()
    ast = parse("foreach n in aimc.nodes select id, node_type, label")
    ex = Executor()
    ex.register_collection("aimc.nodes", nodes_adapter)
    result = ex.run(ast, conn=conn)
    conn.close()

    assert len(result) == 2
    labels = {r["label"] for r in result}
    assert "Qwen3 (Local)" in labels
    assert "Input Guardrail" in labels
