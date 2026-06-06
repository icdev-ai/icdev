# CUI // SP-CTI
"""Tests for tools/codegen/reuse_scout.py — pre-generation reuse/scope brief."""
from __future__ import annotations

from tools.codegen import reuse_scout


def test_tokenize_splits_camel_and_drops_stopwords():
    toks = reuse_scout._tokenize("getCustomerConnection for the database")
    assert "customer" in toks
    assert "connection" in toks
    assert "database" in toks
    # stopwords / short tokens removed
    assert "the" not in toks
    assert "for" not in toks


def test_overlap_score_rewards_shared_tokens():
    q = {"database", "connection"}
    near = reuse_scout._overlap_score(q, {"database", "connection", "pool"})
    far = reuse_scout._overlap_score(q, {"render", "html"})
    assert near > far
    assert far == 0.0


def test_signature_renders_params_and_return():
    fn = {
        "name": "get_connection",
        "parameters": [{"name": "db_path"}, {"name": "timeout"}],
        "return_type": "Connection",
    }
    assert reuse_scout._signature(fn) == "get_connection(db_path, timeout) -> Connection"


def test_scout_classifies_reuse_vs_generate(monkeypatch):
    # Hermetic KG + extractor: one matching module exposing get_connection.
    monkeypatch.setattr(
        reuse_scout,
        "_load_kg_nodes",
        lambda: [
            {
                "label": "Storage",
                "entity_type": "tool",
                "file_path": "tools/db/storage.py",
                "description": "database connection and storage layer",
            },
            {
                "label": "Unrelated",
                "entity_type": "tool",
                "file_path": "tools/x/unrelated.py",
                "description": "renders html templates",
            },
        ],
    )
    monkeypatch.setattr(
        reuse_scout,
        "_module_symbols",
        lambda fp: (
            [{"name": "get_connection", "signature": "get_connection(db_path)", "doc": "open db", "kind": "function"}]
            if "storage.py" in fp
            else []
        ),
    )
    monkeypatch.setattr(reuse_scout, "_search_manifest", lambda q, limit: [])

    brief = reuse_scout.scout(
        "open a database connection",
        symbols=["get_connection", "make_widget"],
    )
    assert "get_connection" in brief["already_exists"]
    assert brief["generate_only"] == ["make_widget"]
    # The matching module ranks ahead of the unrelated one.
    assert brief["reuse"][0]["file_path"] == "tools/db/storage.py"


def test_scout_degrades_without_kg(monkeypatch):
    monkeypatch.setattr(reuse_scout, "_load_kg_nodes", lambda: [])
    monkeypatch.setattr(reuse_scout, "_search_manifest", lambda q, limit: [])
    brief = reuse_scout.scout("anything", symbols=["foo"])
    assert brief["reuse"] == []
    assert brief["generate_only"] == ["foo"]


def test_format_markdown_has_required_slots(monkeypatch):
    monkeypatch.setattr(reuse_scout, "_load_kg_nodes", lambda: [])
    monkeypatch.setattr(reuse_scout, "_search_manifest", lambda q, limit: [])
    md = reuse_scout.format_markdown(reuse_scout.scout("x", symbols=["foo"]))
    assert "REUSE THESE" in md
    assert "GENERATE ONLY" in md
