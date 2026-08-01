# CUI // SP-CTI
"""Tests for DIC cross-canvas RAG+KG context (tools/document_intelligence/cross_canvas_context.py).

Covers the routing (which canvases inform which document) and the deterministic
KG-lexical gather against a throwaway in-memory graph — no Flask, no real DB, no
embedding service.
"""
import sqlite3

import pytest

from tools.document_intelligence.cross_canvas_context import (
    gather,
    resolve_context_canvases,
)


# ---- routing -------------------------------------------------------------- #
def test_network_collection_routes_to_ndc_and_migration():
    canvases = resolve_context_canvases("net_knowledge", "BGP segmentation runbook")
    assert "ndc" in canvases
    assert "mdc" in canvases  # network-migration


def test_security_query_routes_to_sdc_and_compliance():
    canvases = resolve_context_canvases("policies", "zero trust hardening and threat model")
    assert canvases[:2] == ["sdc", "compliance"]


def test_compliance_keywords_route_to_compliance():
    canvases = resolve_context_canvases("collection", "NIST 800-53 control SSP mapping")
    assert "compliance" in canvases


def test_unrelated_document_pulls_no_cross_canvas_context():
    assert resolve_context_canvases("recipes", "how to bake bread") == []


# ---- gather (deterministic KG path) --------------------------------------- #
class _TranslatingConn:
    """In-memory sqlite that rewrites %s -> ? like the storage layer.

    gather()/_neighbors run canonical PostgreSQL (%s) and in production get a
    StorageConnection that translates for sqlite (tools.db.storage.translate_sql).
    A bare sqlite3 connection does not, so %s raised and the relationship context
    was silently dropped. Wrapping the fixture makes it behave like production.
    """

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=None):
        from tools.db.storage import translate_sql
        return self._raw.execute(translate_sql(sql, "sqlite"), params or [])

    def __getattr__(self, name):
        return getattr(self._raw, name)


def _kg_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE kg_graphs (id TEXT PRIMARY KEY, project_id TEXT, name TEXT)")
    conn.execute(
        "CREATE TABLE kg_nodes (id TEXT PRIMARY KEY, graph_id TEXT, label TEXT, "
        "entity_type TEXT, properties TEXT, centrality REAL)"
    )
    conn.execute(
        "CREATE TABLE kg_edges (id TEXT PRIMARY KEY, graph_id TEXT, source_id TEXT, "
        "target_id TEXT, relationship TEXT)"
    )
    conn.execute("INSERT INTO kg_graphs VALUES ('g_ndc','ndc','network_dc')")
    conn.execute(
        "INSERT INTO kg_nodes VALUES ('n1','g_ndc','Core BGP Router', 'device', "
        "'{\"vendor\":\"Cisco\",\"role\":\"edge\"}', 0.9)"
    )
    conn.execute(
        "INSERT INTO kg_nodes VALUES ('n2','g_ndc','Production VLAN Segmentation', 'policy', "
        "'{\"scope\":\"prod\"}', 0.7)"
    )
    conn.execute("INSERT INTO kg_edges VALUES ('e1','g_ndc','n1','n2','enforces')")
    conn.commit()
    return _TranslatingConn(conn)


def test_gather_returns_ndc_kg_evidence():
    conn = _kg_db()
    ev = gather("BGP routing and VLAN segmentation", ["ndc"], conn=conn, use_rag=False)
    assert ev.found >= 1
    assert ev.canvases == ["ndc"]
    blob = " ".join(ev.texts)
    assert "BGP Router" in blob or "Segmentation" in blob
    # citations carry the source canvas + a canvas:// uri
    assert any(c.get("canvas") == "ndc" for c in ev.citations)
    assert any(str(c.get("source_uri", "")).startswith("canvas://ndc") for c in ev.citations)
    # the formatted block is non-empty and labels the source canvas
    assert "Network Design Canvas" in ev.block


def test_gather_includes_relationship_context():
    conn = _kg_db()
    ev = gather("BGP router", ["ndc"], conn=conn, use_rag=False)
    blob = " ".join(ev.texts)
    # the n1->n2 'enforces' edge should surface as related context on the BGP router
    assert "related" in blob.lower()


def test_gather_empty_when_no_canvases():
    conn = _kg_db()
    ev = gather("anything", [], conn=conn, use_rag=False)
    assert ev.found == 0
    assert ev.block == ""


def test_gather_empty_when_no_keyword_match_graph_absent():
    conn = _kg_db()
    # 'sdc' has no graph in this stub DB -> no rows, graceful empty.
    ev = gather("threat model", ["sdc"], conn=conn, use_rag=False)
    assert ev.found == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
