# CUI // SP-CTI
"""Within-graph entity resolution: collapse the N copies of each entity.

The per-chunk LLM extractor mints a fresh node for "Peering" in every chunk, so a
collection's graph carries dozens of identical nodes with edges scattered across
them. This merges each (normalized label, type) group down to one canonical node
so the graph — and the communities built on it — are sharp. It must be
conservative: same normalized label AND type only, never a guess across surface
forms, and a single failed merge must not abort the sweep.
"""
from __future__ import annotations

from tools.knowledge_graph import entity_resolution as er


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, nodes):
        # nodes: [(id, label, entity_type)]
        self._nodes = nodes

    def execute(self, sql, params=None):
        return _Cur([{"id": n[0], "label": n[1], "entity_type": n[2]} for n in self._nodes])

    def close(self):
        pass


def _recording_merge():
    calls = []

    def _merge(source, target):
        calls.append((source, target))
        return {"status": "ok"}

    _merge.calls = calls
    return _merge


class TestResolveGraphDuplicates:
    def test_collapses_exact_repeats_to_one_canonical(self):
        # Three "Peering" (concept) nodes -> two merges into the first (n1).
        conn = _Conn([("n1", "Peering", "concept"), ("n2", "Peering", "concept"), ("n3", "Peering", "concept")])
        merge = _recording_merge()
        stats = er.resolve_graph_duplicates("g1", conn=conn, merge_fn=merge)
        assert stats["nodes_merged"] == 2
        assert stats["duplicate_groups"] == 1
        assert set(merge.calls) == {("n2", "n1"), ("n3", "n1")}

    def test_case_and_whitespace_variants_merge(self):
        conn = _Conn([("n1", "Exchange Point", "concept"), ("n2", "exchange point", "concept"), ("n3", "EXCHANGE  POINT", "concept")])
        merge = _recording_merge()
        stats = er.resolve_graph_duplicates("g1", conn=conn, merge_fn=merge)
        assert stats["nodes_merged"] == 2  # all normalize to the same key

    def test_different_types_are_not_merged(self):
        """Same label, different type is a genuine ambiguity — leave for review."""
        conn = _Conn([("n1", "ISP", "organization"), ("n2", "ISP", "concept")])
        merge = _recording_merge()
        stats = er.resolve_graph_duplicates("g1", conn=conn, merge_fn=merge)
        assert stats["nodes_merged"] == 0 and merge.calls == []

    def test_different_surface_forms_are_not_merged(self):
        """Conservative: 'ISP' and 'Internet Service Provider' are NOT collapsed
        without an abbreviation/embedding signal."""
        conn = _Conn([("n1", "ISP", "organization"), ("n2", "Internet Service Provider", "organization")])
        merge = _recording_merge()
        stats = er.resolve_graph_duplicates("g1", conn=conn, merge_fn=merge)
        assert stats["nodes_merged"] == 0

    def test_singletons_untouched(self):
        conn = _Conn([("n1", "A entity", "concept"), ("n2", "B entity", "concept")])
        merge = _recording_merge()
        assert er.resolve_graph_duplicates("g1", conn=conn, merge_fn=merge)["nodes_merged"] == 0

    def test_dry_run_writes_nothing(self):
        conn = _Conn([("n1", "Peering", "concept"), ("n2", "Peering", "concept")])
        merge = _recording_merge()
        stats = er.resolve_graph_duplicates("g1", conn=conn, dry_run=True, merge_fn=merge)
        assert stats["nodes_merged"] == 1 and merge.calls == []

    def test_a_failed_merge_does_not_abort_the_sweep(self):
        conn = _Conn([("n1", "Peering", "concept"), ("n2", "Peering", "concept"), ("n3", "Peering", "concept")])

        def _merge(source, target):
            if source == "n2":
                raise RuntimeError("locked")
            return {"status": "ok"}

        stats = er.resolve_graph_duplicates("g1", conn=conn, merge_fn=_merge)
        assert stats["nodes_merged"] == 1  # n3 still merged despite n2 failing

    def test_error_status_from_merge_is_counted_as_skip(self):
        conn = _Conn([("n1", "Peering", "concept"), ("n2", "Peering", "concept")])

        def _merge(source, target):
            return {"status": "error", "error": "not found"}

        assert er.resolve_graph_duplicates("g1", conn=conn, merge_fn=_merge)["nodes_merged"] == 0
