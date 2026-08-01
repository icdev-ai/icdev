# CUI // SP-CTI
"""The LLM relationship extractor must add real edges without polluting the graph.

The heuristic extractor leaves the KG node-rich and edge-poor (2,749 nodes / ~140
edges live) because it only fires on "EntityA <verb> EntityB" in one sentence.
This infers edges semantically — but it may NOT invent nodes or relationship
labels, and it must fall back cleanly when the LLM is unavailable, or it would
trade an empty graph for a hallucinated one.
"""
from __future__ import annotations

import pytest

from tools.knowledge_graph import llm_relationship_extractor as ex


class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    """Stub router returning a canned reply; records the call."""

    def __init__(self, content):
        self._content = content
        self.calls = []

    def invoke(self, function, req):
        self.calls.append((function, req))
        return _Resp(self._content)


ENTS = [("AC-2", "control"), ("Access Control Policy", "document"), ("SIEM", "system")]


class TestIsValidRelationship:
    @pytest.mark.parametrize("rel", ["implements", "co_occurs_with", "IMPLEMENTS", "DEPENDS_ON"])
    def test_accepts_real_types(self, rel):
        assert ex.is_valid_relationship(rel)

    @pytest.mark.parametrize("rel", [
        "KG context",      # space
        "batch 15min",     # space + digit
        "train/infer",     # slash
        "clean records",   # space
        "",                # empty
        "x" * 41,          # too long
        None, 123,
    ])
    def test_rejects_garbage(self, rel):
        """Every polluted value observed live must be rejected."""
        assert not ex.is_valid_relationship(rel)


class TestExtractionConstrainsToInputs:
    def test_maps_indices_to_entity_labels_with_evidence(self):
        router = _Router('[{"source":0,"target":1,"relationship":"references","evidence":"AC-2 cites the policy"}]')
        out = ex.extract_relationships_llm("AC-2 references the Access Control Policy.", ENTS, router=router)
        assert out == [("AC-2", "Access Control Policy", "references", "AC-2 cites the policy")]
        assert router.calls[0][0] == "kg_relationship_extraction"

    def test_out_of_range_index_is_dropped(self):
        """The model may not invent a node by pointing past the entity list."""
        router = _Router('[{"source":0,"target":9,"relationship":"references","evidence":"x"}]')
        assert ex.extract_relationships_llm("text", ENTS, router=router) == []

    def test_self_edge_is_dropped(self):
        router = _Router('[{"source":1,"target":1,"relationship":"references","evidence":"x"}]')
        assert ex.extract_relationships_llm("text", ENTS, router=router) == []

    def test_label_outside_vocabulary_is_dropped(self):
        """An off-vocab (even if clean) label is not persisted."""
        router = _Router('[{"source":0,"target":1,"relationship":"pwns","evidence":"x"}]')
        assert ex.extract_relationships_llm("text", ENTS, router=router) == []

    def test_garbage_label_is_dropped(self):
        router = _Router('[{"source":0,"target":1,"relationship":"batch 15min","evidence":"x"}]')
        assert ex.extract_relationships_llm("text", ENTS, router=router) == []

    def test_duplicate_edges_collapse(self):
        router = _Router(
            '[{"source":0,"target":1,"relationship":"references","evidence":"a"},'
            ' {"source":0,"target":1,"relationship":"references","evidence":"b"}]'
        )
        assert len(ex.extract_relationships_llm("text", ENTS, router=router)) == 1


class TestExtractGraphLLM:
    """Full entity+relationship extraction — the fix for the 87% zero-entity corpus."""

    def test_extracts_entities_and_edges(self):
        router = _Router(
            '{"entities":[{"name":"BGP","type":"technology"},'
            ' {"name":"Edge Router","type":"component"}],'
            ' "relationships":[{"source":"Edge Router","target":"BGP",'
            ' "relationship":"implements","evidence":"the edge router runs BGP"}]}'
        )
        ents, rels = ex.extract_graph_llm("The edge router runs BGP.", router=router)
        assert ("BGP", "technology") in ents and ("Edge Router", "component") in ents
        assert rels == [("Edge Router", "BGP", "implements", "the edge router runs BGP")]

    def test_relationship_to_unknown_entity_is_dropped(self):
        """Edges may only connect entities the model actually extracted."""
        router = _Router(
            '{"entities":[{"name":"Alpha","type":"concept"}],'
            ' "relationships":[{"source":"Alpha","target":"Ghost","relationship":"relates_to","evidence":"x"}]}'
        )
        ents, rels = ex.extract_graph_llm("t", router=router)
        assert ents == [("Alpha", "concept")] and rels == []

    def test_unknown_entity_type_normalises_to_concept(self):
        router = _Router('{"entities":[{"name":"Zeta","type":"alien"}],"relationships":[]}')
        ents, _ = ex.extract_graph_llm("t", router=router)
        assert ents == [("Zeta", "concept")]

    def test_off_vocab_relationship_dropped(self):
        router = _Router(
            '{"entities":[{"name":"Alpha","type":"concept"},{"name":"Bravo","type":"concept"}],'
            ' "relationships":[{"source":"Alpha","target":"Bravo","relationship":"pwns","evidence":"x"}]}'
        )
        ents, rels = ex.extract_graph_llm("t", router=router)
        assert len(ents) == 2 and rels == []

    def test_empty_or_unparseable_yields_empty(self):
        assert ex.extract_graph_llm("t", router=_Router("no json here")) == ([], [])
        assert ex.extract_graph_llm("", router=_Router("{}")) == ([], [])

    def test_router_exception_is_graceful(self):
        class Boom:
            def invoke(self, *_):
                raise RuntimeError("air-gapped")
        assert ex.extract_graph_llm("t", router=Boom()) == ([], [])


class TestRobustness:
    def test_fenced_json_is_parsed(self):
        router = _Router('```json\n[{"source":0,"target":2,"relationship":"governs","evidence":"e"}]\n```')
        out = ex.extract_relationships_llm("t", ENTS, router=router)
        assert out == [("AC-2", "SIEM", "governs", "e")]

    def test_unparseable_reply_yields_empty(self):
        assert ex.extract_relationships_llm("t", ENTS, router=_Router("I could not find any.")) == []

    def test_router_exception_yields_empty_not_raise(self):
        class Boom:
            def invoke(self, *_):
                raise RuntimeError("air-gapped")
        assert ex.extract_relationships_llm("t", ENTS, router=Boom()) == []

    def test_fewer_than_two_entities_short_circuits(self):
        """No LLM call when there is nothing to connect."""
        router = _Router("[]")
        assert ex.extract_relationships_llm("t", [("solo", "x")], router=router) == []
        assert router.calls == []

    def test_evidence_is_bounded(self):
        long = "z" * 500
        router = _Router(f'[{{"source":0,"target":1,"relationship":"references","evidence":"{long}"}}]')
        out = ex.extract_relationships_llm("t", ENTS, router=router)
        assert len(out[0][3]) <= 200
