# CUI // SP-CTI
"""Themes grouped by collection for the Explorer browse view.

community_id carries a graph tag, not a collection name, so the view resolves
each collection to its graph tag forward and buckets summaries under it. A theme
whose graph does not resolve to any collection must surface under '(unmatched)',
never vanish.
"""
from __future__ import annotations

import json

from tools.knowledge_graph import community_engine as ce

TAG_A = ce._graph_tag("graph-A")
TAG_B = ce._graph_tag("graph-B")
TAG_ORPHAN = ce._graph_tag("graph-Z")


def _summary(tag, text, entities):
    return {
        "community_id": f"comm-{tag}-x{text[:2]}",
        "summary_text": text,
        "citations_list": json.dumps(entities),
    }


class _Conn:
    def __init__(self, summaries, collections, coll_graphs):
        self._summaries = summaries
        self._collections = collections           # [(collection_id, name)]
        self._coll_graphs = coll_graphs           # {project_id: [graph_id]}

    def execute(self, sql, params=None):
        s = " ".join(sql.split())

        class Cur:
            def __init__(self, rows):
                self._rows = rows

            def __iter__(self):
                return iter(self._rows)

            def fetchall(self):
                return self._rows

        if s.startswith("SELECT community_id, summary_text, citations_list"):
            return Cur(self._summaries)
        if s.startswith("SELECT collection_id, name FROM dic_collections"):
            return Cur([{"collection_id": c[0], "name": c[1]} for c in self._collections])
        if "kg_nodes n JOIN rag_chunks" in s:
            return Cur([{"graph_id": g} for g in self._coll_graphs.get(params[0], [])])
        return Cur([])


SUMMARIES = [
    _summary(TAG_A, "peering economics", ["ISP", "Peering"]),
    _summary(TAG_A, "network performance", ["latency"]),
    _summary(TAG_B, "constitutional law", ["First Amendment"]),
    _summary(TAG_ORPHAN, "an orphaned theme", ["ghost"]),
]


class TestThemesByCollection:
    def test_groups_themes_under_their_collection(self):
        conn = _Conn(
            SUMMARIES,
            [("cid-net", "Networking"), ("cid-law", "Law")],
            {"cid-net": ["graph-A"], "cid-law": ["graph-B"]},
        )
        out = ce.themes_by_collection(conn)
        groups = {g["collection"]: g for g in out}
        assert len(groups["Networking"]["themes"]) == 2
        assert len(groups["Law"]["themes"]) == 1
        assert groups["Networking"]["themes"][0]["entities"] == ["ISP", "Peering"]

    def test_unmatched_themes_surface_not_dropped(self):
        conn = _Conn(SUMMARIES, [("cid-net", "Networking")], {"cid-net": ["graph-A"]})
        out = ce.themes_by_collection(conn)
        groups = {g["collection"]: g for g in out}
        # graph-B and the orphan tag resolve to no collection -> '(unmatched)'
        assert "(unmatched)" in groups
        texts = {t["summary"] for t in groups["(unmatched)"]["themes"]}
        assert "constitutional law" in texts and "an orphaned theme" in texts

    def test_project_id_by_name_also_resolves(self):
        """Live project_id is sometimes the collection NAME, not its id — both are
        tried, so a name-keyed collection still gets its themes."""
        conn = _Conn(SUMMARIES, [("cid-net", "isp-demo")], {"isp-demo": ["graph-A"]})
        out = ce.themes_by_collection(conn)
        assert {g["collection"] for g in out} >= {"isp-demo"}
        assert len([g for g in out if g["collection"] == "isp-demo"][0]["themes"]) == 2

    def test_unmatched_sorts_last(self):
        conn = _Conn(SUMMARIES, [("cid-net", "Networking")], {"cid-net": ["graph-A"]})
        out = ce.themes_by_collection(conn)
        assert out[-1]["collection"] == "(unmatched)"

    def test_empty_when_no_summaries(self):
        conn = _Conn([], [("cid", "C")], {})
        assert ce.themes_by_collection(conn) == []
