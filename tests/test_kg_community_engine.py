# CUI // SP-CTI
"""GraphRAG community engine: cluster the KG, summarise each theme, serve global Q&A.

Flat RAG can retrieve chunks; it cannot answer "what are the main themes across
the corpus" because that is a property of the whole graph. This engine clusters
the graph and summarises each community so a query can reason over the themes.
The clustering must be deterministic and ignore noise; the summariser must never
crash the pipeline; and the store must be idempotent so re-runs replace rather
than accumulate.
"""
from __future__ import annotations

import json

from tools.knowledge_graph import community_engine as ce


class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    def __init__(self, content):
        self._content = content
        self.calls = 0

    def invoke(self, function, req):
        self.calls += 1
        assert function == "kg_community_summary"
        return _Resp(self._content)


# Two triangles joined by nothing -> two communities; plus an isolate.
NODES = ["a", "b", "c", "x", "y", "z", "lonely"]
EDGES = [("a", "b"), ("b", "c"), ("a", "c"), ("x", "y"), ("y", "z"), ("x", "z")]


class TestDetectCommunities:
    def test_splits_disconnected_clusters(self):
        comms = ce.detect_communities(NODES, EDGES, min_size=3)
        assert len(comms) == 2
        sets = [set(c) for c in comms]
        assert {"a", "b", "c"} in sets and {"x", "y", "z"} in sets

    def test_isolated_node_is_not_a_community(self):
        comms = ce.detect_communities(NODES, EDGES, min_size=3)
        assert all("lonely" not in c for c in comms)

    def test_min_size_filters_small_clusters(self):
        # a-b is a size-2 component; with min_size 3 it is dropped.
        comms = ce.detect_communities(["a", "b", "x", "y", "z"], [("a", "b"), ("x", "y"), ("y", "z"), ("x", "z")], min_size=3)
        assert len(comms) == 1 and set(comms[0]) == {"x", "y", "z"}

    def test_no_edges_yields_no_communities(self):
        assert ce.detect_communities(["a", "b", "c"], [], min_size=3) == []

    def test_is_deterministic(self):
        assert ce.detect_communities(NODES, EDGES) == ce.detect_communities(NODES, EDGES)


class TestCommunityId:
    def test_stable_for_same_membership(self):
        assert ce._community_id("g1", ["a", "b", "c"]) == ce._community_id("g1", ["c", "b", "a"])

    def test_differs_by_graph(self):
        assert ce._community_id("g1", ["a", "b"]) != ce._community_id("g2", ["a", "b"])

    def test_encodes_graph_tag_for_prefix_delete(self):
        cid = ce._community_id("g1", ["a", "b", "c"])
        assert cid.startswith(f"comm-{ce._graph_tag('g1')}-")


class TestSummarise:
    MEMBERS = [
        {"id": "a", "label": "BGP", "entity_type": "technology"},
        {"id": "b", "label": "Edge Router", "entity_type": "component"},
    ]
    INTERNAL = [("b", "a", "implements")]

    def test_uses_llm_summary_when_available(self):
        text, cites = ce.summarise_community(self.MEMBERS, self.INTERNAL, router=_Router("Routing layer theme."))
        assert text == "Routing layer theme."
        assert cites == ["BGP", "Edge Router"]

    def test_falls_back_deterministically_without_llm(self):
        class Boom:
            def invoke(self, *_):
                raise RuntimeError("air-gapped")
        text, cites = ce.summarise_community(self.MEMBERS, self.INTERNAL, router=Boom())
        assert "BGP" in text and "Edge Router" in text
        assert cites == ["BGP", "Edge Router"]

    def test_empty_llm_reply_falls_back(self):
        text, _ = ce.summarise_community(self.MEMBERS, self.INTERNAL, router=_Router("   "))
        assert "BGP" in text  # fallback, not the blank reply


class _FakeConn:
    """Minimal conn: canned kg_nodes/kg_edges, records community writes."""

    def __init__(self, nodes, edges):
        self._nodes = nodes  # [(id,label,type,graph_id,props)]
        self._edges = edges  # [(src,tgt,rel,graph_id)]
        self.summaries = {}   # summary_id -> row
        self.deletes = []
        self.committed = 0

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        parent = self

        class Cur:
            def __init__(self, rows):
                self._rows = rows

            def __iter__(self):
                return iter(self._rows)

            def fetchall(self):
                return self._rows

        if s.startswith("SELECT graph_id, properties FROM kg_nodes"):
            return Cur([{"graph_id": n[3], "properties": n[4]} for n in self._nodes])
        if s.startswith("SELECT id, label, entity_type FROM kg_nodes WHERE graph_id"):
            gid = params[0]
            return Cur([{"id": n[0], "label": n[1], "entity_type": n[2]} for n in self._nodes if n[3] == gid])
        if s.startswith("SELECT source_id, target_id, relationship FROM kg_edges WHERE graph_id"):
            gid = params[0]
            return Cur([{"source_id": e[0], "target_id": e[1], "relationship": e[2]} for e in self._edges if e[3] == gid])
        if s.startswith("DELETE FROM dic_community_summaries"):
            parent.deletes.append(params[0])
            return Cur([])
        if s.startswith("INSERT INTO dic_community_summaries"):
            sid = params[0]
            parent.summaries[sid] = {
                "summary_id": params[0], "community_id": params[1],
                "summary_text": params[2], "citations_list": params[3],
            }
            return Cur([])
        if s.startswith("SELECT summary_id, community_id, summary_text, citations_list FROM dic_community_summaries"):
            return Cur(list(parent.summaries.values()))
        return Cur([])

    def commit(self):
        self.committed += 1


def _dic_nodes():
    props = json.dumps({"source": "rag_kg_bridge"})
    return [(n, n.upper(), "concept", "g1", props) for n in ["a", "b", "c", "x", "y", "z"]]


def _dic_edges():
    return [(a, b, "relates_to", "g1") for (a, b) in EDGES]


class TestBuildCommunities:
    def test_builds_and_stores_two_communities(self):
        conn = _FakeConn(_dic_nodes(), _dic_edges())
        stats = ce.build_communities(conn, router=_Router("theme"))
        assert stats["graphs"] == 1
        assert stats["communities"] == 2
        assert len(conn.summaries) == 2

    def test_only_processes_dic_bridge_graphs(self):
        """Canvas architecture graphs (no rag_kg_bridge source) are skipped —
        kg_edges is shared and must not be clustered as documents."""
        canvas = [(n, n, "concept", "canvas1", json.dumps({"source": "canvas"})) for n in ["p", "q", "r"]]
        conn = _FakeConn(_dic_nodes() + canvas, _dic_edges() + [("p", "q", "flow", "canvas1")])
        ce.build_communities(conn, router=_Router("theme"))
        assert all(row["community_id"].startswith(f"comm-{ce._graph_tag('g1')}-") for row in conn.summaries.values())

    def test_clears_graph_before_rebuild(self):
        conn = _FakeConn(_dic_nodes(), _dic_edges())
        ce.build_communities(conn, router=_Router("theme"))
        assert conn.deletes and conn.deletes[0] == f"comm-{ce._graph_tag('g1')}-%"

    def test_rerun_is_idempotent(self):
        conn = _FakeConn(_dic_nodes(), _dic_edges())
        ce.build_communities(conn, router=_Router("theme"))
        first = dict(conn.summaries)
        ce.build_communities(conn, router=_Router("theme"))
        assert set(conn.summaries) == set(first)  # same community ids, not doubled


class TestSearchCommunities:
    def test_ranks_by_token_overlap(self):
        conn = _FakeConn([], [])
        conn.summaries = {
            "c1": {"summary_id": "c1", "community_id": "c1", "summary_text": "network routing and BGP", "citations_list": "[]"},
            "c2": {"summary_id": "c2", "community_id": "c2", "summary_text": "financial reporting", "citations_list": "[]"},
        }
        out = ce.search_communities(conn, "how does BGP routing work", limit=2)
        assert out[0]["community_id"] == "c1"

    def test_returns_top_summaries_even_without_overlap(self):
        conn = _FakeConn([], [])
        conn.summaries = {"c1": {"summary_id": "c1", "community_id": "c1", "summary_text": "x", "citations_list": "[]"}}
        assert len(ce.search_communities(conn, "totally unrelated query", limit=5)) == 1
