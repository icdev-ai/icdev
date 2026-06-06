# CUI // SP-CTI
"""Tests for the graphify-style call-flow + PR-impact tools."""
from __future__ import annotations

from tools.awareness import callflow, change_impact


# ── callflow ────────────────────────────────────────────────────────────────

def test_symbol_index_drops_ambiguous_and_private():
    idx = callflow.build_symbol_index(scope="tools/awareness")
    # private/dunder and the common 'main'/'run' names are excluded
    assert "_reachable" not in idx
    assert "main" not in idx


def test_build_call_graph_resolves_known_call():
    # Hermetic-ish: supply a tiny index so only the call pass runs over awareness/.
    idx = {"build_call_graph": "tools/awareness/callflow.py"}
    graph = callflow.build_call_graph(scope="tools/awareness", index=idx)
    edge = ["tools/awareness/change_impact.py", "tools/awareness/callflow.py"]
    assert edge in graph["module_edges"], graph["module_edges"]


def test_export_html_writes_file(tmp_path):
    graph = {"functions": {"m::f": {}}, "edges": [], "module_edges": [["a.py", "b.py"]]}
    out = callflow.export_html(graph, tmp_path / "cf.html")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "a.py" in text and "b.py" in text


# ── change_impact ───────────────────────────────────────────────────────────

def test_to_module_rel_filters_non_tools():
    assert change_impact._to_module_rel("tools/db/storage.py") == "tools/db/storage.py"
    assert change_impact._to_module_rel("docs/readme.md") is None


def test_is_route_module():
    assert change_impact._is_route_module("tools/x/blueprint.py")
    assert change_impact._is_route_module("tools/dashboard/app.py")
    assert change_impact._is_route_module("tools/dashboard/api/foo.py")
    assert not change_impact._is_route_module("tools/db/storage.py")


def test_communities_groups_connected():
    nodes = {"a", "b", "c", "d"}
    edges = [["a", "b"], ["c", "d"]]
    comms = change_impact._communities(nodes, edges)
    assert sorted(len(c) for c in comms) == [2, 2]


def test_compute_impact_blast_radius(monkeypatch):
    # caller.py calls storage.py → changing storage impacts caller.
    monkeypatch.setattr(
        change_impact,
        "build_call_graph",
        lambda scope=None: {
            "functions": {},
            "edges": [],
            "module_edges": [
                ["tools/x/caller.py", "tools/db/storage.py"],
                ["tools/y/leaf.py", "tools/x/caller.py"],
            ],
        },
    )
    impact = change_impact.compute_impact(["tools/db/storage.py"])
    assert impact["changed"] == ["tools/db/storage.py"]
    # transitive upstream: caller (direct) + leaf (calls caller)
    assert "tools/x/caller.py" in impact["blast_radius"]
    assert "tools/y/leaf.py" in impact["blast_radius"]


def test_compute_impact_no_resolved_modules():
    impact = change_impact.compute_impact(["docs/readme.md"])
    assert impact["changed"] == []
    assert impact["blast_radius"] == []
