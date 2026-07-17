# CUI // SP-CTI
"""Community search scopes to the active collection.

A user asking "the main themes" while inside a collection means THAT collection,
not every document in the tenant. search_communities resolves the collection to
its graph (via the node->chunk join) and keeps only that graph's communities —
but degrades to tenant-wide rather than returning nothing when the collection has
no communities yet.
"""
from __future__ import annotations

from tools.knowledge_graph import community_engine as ce


def _row(cid, text):
    return {"summary_id": cid, "community_id": cid, "summary_text": text, "citations_list": "[]"}


class _Conn:
    def __init__(self, summaries, coll_graphs):
        self._summaries = summaries          # list of community rows
        self._coll_graphs = coll_graphs      # {project_id: [graph_id,...]}

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

        if s.startswith("SELECT summary_id, community_id, summary_text, citations_list"):
            return Cur(parent._summaries)
        if "kg_nodes n JOIN rag_chunks" in s:
            gids = parent._coll_graphs.get(params[0], [])
            return Cur([{"graph_id": g} for g in gids])
        return Cur([])


GA = ce._graph_tag("graph-A")
GB = ce._graph_tag("graph-B")
SUMMARIES = [
    _row(f"comm-{GA}-aaa", "peering and routing"),
    _row(f"comm-{GA}-bbb", "network performance"),
    _row(f"comm-{GB}-ccc", "financial reporting"),
]


class TestCollectionScoping:
    def test_scopes_to_the_collections_graph(self):
        conn = _Conn(SUMMARIES, {"collA": ["graph-A"]})
        out = ce.search_communities(conn, "themes", collection_id="collA", limit=10)
        cids = {r["community_id"] for r in out}
        assert cids == {f"comm-{GA}-aaa", f"comm-{GA}-bbb"}  # graph-B excluded

    def test_no_collection_returns_tenant_wide(self):
        conn = _Conn(SUMMARIES, {})
        out = ce.search_communities(conn, "themes", limit=10)
        assert len(out) == 3

    def test_unknown_collection_degrades_to_tenant_wide(self):
        """A collection with nothing ingested yet must not return an empty set —
        fall back to tenant-wide rather than silence."""
        conn = _Conn(SUMMARIES, {"collA": ["graph-A"]})
        out = ce.search_communities(conn, "themes", collection_id="ghost", limit=10)
        assert len(out) == 3

    def test_graph_tags_for_collection_resolves(self):
        conn = _Conn(SUMMARIES, {"collA": ["graph-A", "graph-B"]})
        assert ce._graph_tags_for_collection(conn, "collA") == {GA, GB}

    def test_graph_tags_empty_collection_id(self):
        conn = _Conn(SUMMARIES, {})
        assert ce._graph_tags_for_collection(conn, "") == set()

    def test_join_failure_is_graceful(self):
        class Boom:
            def execute(self, *_a, **_k):
                raise RuntimeError("no rag_chunks")
        assert ce._graph_tags_for_collection(Boom(), "collA") == set()
