#!/usr/bin/env python3
"""KG-to-text constrained validation — the invariants. CUI // SP-CTI

Deterministic by construction: every test drives a fake connection that returns
exactly the rows it declares. No live database, no environment sensitivity.

That is not incidental. The previous TRUST PR shipped a test that asserted a
check "degrades honestly when the board is unreachable" and passed locally for
the wrong reason — it monkeypatched one of two distinct module aliases, so the
check queried the real board, and only the worktree's missing table produced the
expected answer. CI caught it. A test for honest degradation must not itself
depend on the environment.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.quality.kg_grounding import (  # noqa: E402
    KG_CONTRADICTED,
    KG_NO_ENTITIES,
    KG_SUPPORTED,
    KG_UNATTESTED,
    SCHEMA_DECLARED,
    SCHEMA_OBSERVED,
    SCHEMA_UNAVAILABLE,
    STATUS_OK,
    STATUS_UNMEASURABLE,
    GraphSchema,
    Lexicon,
    NodeRef,
    Triple,
    kg_gate,
    kg_ground_claims,
    load_schema,
    _is_unambiguous_label,
    validate_triple,
)


class FakeConn:
    """Returns rows by matching a fragment of the SQL. Explicit, not clever."""

    def __init__(self, **tables):
        self.nodes = tables.get("nodes", [])
        self.edges = tables.get("edges", [])
        self.ontology = tables.get("ontology", [])
        self.observed = tables.get("observed", [])

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if "COUNT(*) AS n FROM kg_nodes" in s:
            rows = [{"n": len(self.nodes)}]
        elif "FROM kg_ontology" in s:
            rows = list(self.ontology)
        elif "FROM kg_edges e" in s:
            rows = list(self.observed)
        elif "FROM kg_edges WHERE source_id" in s:
            rows = [
                {"id": "e1"} for e in self.edges
                if e["source_id"] == params[0] and e["target_id"] == params[1]
                and e["relationship"] == params[2]
            ][:1]
        elif "FROM kg_nodes" in s:
            rows = list(self.nodes)
        else:
            rows = []

        class _Cur:
            def fetchall(self_inner):
                return rows
        return _Cur()

    def close(self):
        pass


_NODES = [
    {"id": "n1", "label": "Shih Huang Ti", "entity_type": "person", "properties": "{}"},
    {"id": "n2", "label": "Ch'in State", "entity_type": "organization", "properties": "{}"},
    {"id": "n3", "label": "New York", "entity_type": "location", "properties": "{}"},
]
_EDGES = [{"source_id": "n1", "target_id": "n2", "relationship": "governs"}]
_OBSERVED = [{"st": "person", "p": "governs", "ot": "organization"}]


def _conn(**over):
    return FakeConn(nodes=_NODES, edges=_EDGES, observed=_OBSERVED, **over)


# --------------------------------------------------------------------------- #
# The core discrimination
# --------------------------------------------------------------------------- #

def test_a_claim_backed_by_a_real_edge_is_supported():
    r = kg_ground_claims("Shih Huang Ti governs Ch'in State [source: kb1].", conn=_conn())
    assert r["status"] == STATUS_OK
    assert r["claims"][0]["verdict"] == KG_SUPPORTED
    assert r["claims"][0]["triples"][0]["reason"] == "edge_present"


def test_a_fabricated_relation_is_unattested_not_supported():
    """No such edge exists between these two nodes."""
    r = kg_ground_claims("Shih Huang Ti governs New York [source: kb1].", conn=_conn())
    assert r["claims"][0]["verdict"] == KG_UNATTESTED


def test_an_entity_the_graph_does_not_know_is_reported():
    r = kg_ground_claims("Zorblax Prime governs Ch'in State [source: kb1].", conn=_conn())
    assert "Zorblax Prime" in r["unknown_entities"]


# --------------------------------------------------------------------------- #
# THE invariant — absence of evidence is not evidence of absence
# --------------------------------------------------------------------------- #

def test_an_observed_schema_can_never_contradict():
    """The single most important line in this module.

    An observed schema is derived from the edges that happen to exist, so every
    signature it lacks is one the graph has merely not indexed. Treating that as
    a contradiction would fabricate a finding out of incomplete coverage — and
    a guard that cries wolf gets switched off, taking the real findings with it.
    """
    schema = load_schema(_conn())
    assert schema.source == SCHEMA_OBSERVED
    assert schema.can_block is False

    triple = Triple(
        subject=NodeRef("n1", "Shih Huang Ti", "person"),
        predicate="governs",
        obj=NodeRef("n3", "New York", "location"),
    )
    result = validate_triple(triple, conn=_conn(), schema=schema)
    assert result["verdict"] == KG_UNATTESTED
    assert result["verdict"] != KG_CONTRADICTED


def test_a_declared_schema_may_contradict_an_illegal_signature():
    """With kg_ontology populated, an unlisted signature IS provably illegal."""
    ontology = [{"subject_type": "person", "predicate": "governs", "object_type": "organization"}]
    conn = _conn(ontology=ontology)
    schema = load_schema(conn)
    assert schema.source == SCHEMA_DECLARED
    assert schema.can_block is True

    illegal = Triple(
        subject=NodeRef("n1", "Shih Huang Ti", "person"),
        predicate="governs",
        obj=NodeRef("n3", "New York", "location"),   # person governs LOCATION: not declared
    )
    assert validate_triple(illegal, conn=conn, schema=schema)["verdict"] == KG_CONTRADICTED


def test_an_attested_edge_wins_over_the_schema():
    """Edge existence settles the question regardless of what the schema lists."""
    conn = _conn(ontology=[{"subject_type": "zzz", "predicate": "nope", "object_type": "zzz"}])
    schema = load_schema(conn)
    assert schema.can_block is True
    triple = Triple(
        subject=NodeRef("n1", "Shih Huang Ti", "person"),
        predicate="governs",
        obj=NodeRef("n2", "Ch'in State", "organization"),
    )
    assert validate_triple(triple, conn=conn, schema=schema)["verdict"] == KG_SUPPORTED


def test_an_empty_graph_is_unmeasurable_not_a_wall_of_failures():
    """A fresh worktree or an ephemeral CI database must not manufacture findings."""
    r = kg_ground_claims("Anything at all [source: kb1].", conn=FakeConn(nodes=[]))
    assert r["status"] == STATUS_UNMEASURABLE
    assert r["claims"] == []
    assert kg_gate(r) == []


def test_a_graph_with_no_usable_predicates_reports_unavailable():
    conn = FakeConn(nodes=_NODES, edges=[], observed=[])
    assert load_schema(conn).source == SCHEMA_UNAVAILABLE


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #

def test_only_a_contradiction_blocks_by_default():
    """unattested and unknown_entity must not block, or the guard is unusable."""
    r = kg_ground_claims("Shih Huang Ti governs New York [source: kb1].", conn=_conn())
    assert r["claims"][0]["verdict"] == KG_UNATTESTED
    assert kg_gate(r) == []


def test_unknown_entities_are_opt_in():
    r = kg_ground_claims("Zorblax Prime governs Ch'in State [source: kb1].", conn=_conn())
    assert kg_gate(r) == []
    findings = kg_gate(r, flag_unknown_entities=True)
    assert findings and findings[0]["issue"] == "unknown_entity"


def test_gate_findings_match_the_shared_shape():
    """Same {item_number, issue, detail} as citation_gate / claim_gate."""
    ontology = [{"subject_type": "person", "predicate": "governs", "object_type": "organization"}]
    r = kg_ground_claims(
        "Shih Huang Ti governs New York [source: kb1].", conn=_conn(ontology=ontology)
    )
    findings = kg_gate(r)
    assert findings
    assert set(findings[0]) == {"item_number", "issue", "detail"}
    assert findings[0]["issue"] == "kg_contradicted_claim"


# --------------------------------------------------------------------------- #
# Extraction discipline
# --------------------------------------------------------------------------- #

def test_prose_edge_labels_are_kept_out_of_the_predicate_vocabulary():
    """kg_edges.relationship also holds free text: "CUI data", "LLM inference".

    Admitting those makes the extractor match ordinary prose and invent triples.
    Measured on the live board: 32 identifier-shaped relationships cover 16,423
    of 16,493 edges; the 68 prose-shaped ones cover 70 edges between them.
    """
    conn = FakeConn(
        nodes=_NODES, edges=[],
        observed=[
            {"st": "person", "p": "governs", "ot": "organization"},
            {"st": "person", "p": "CUI data", "ot": "organization"},
            {"st": "person", "p": "LLM inference", "ot": "organization"},
        ],
    )
    assert load_schema(conn).predicates == ("governs",)


@pytest.mark.parametrize("label,ok", [
    ("Shih Huang Ti", True),    # multi-token
    ("Storage", True),          # capitalised
    ("audit_trail", True),      # identifier separator
    ("AC-3", True),             # control id
    ("research", False),        # bare lowercase dictionary word — matches prose
    ("chunks", False),
    ("ab", False),              # too short
])
def test_only_distinctive_labels_enter_the_lexicon(label, ok):
    """Bias toward false negatives: a missed entity weakens one check, an
    invented one fabricates a finding."""
    assert _is_unambiguous_label(label) is ok


def test_lexicon_matches_on_word_boundaries_only():
    lex = Lexicon(by_label={"storage": NodeRef("n9", "Storage", "tool")})
    assert lex.find("The Storage tool")
    assert not lex.find("StorageAccount handles this")


def test_lexicon_prefers_the_longest_label():
    lex = Lexicon(by_label={
        "new york": NodeRef("n3", "New York", "location"),
        "new york city": NodeRef("n4", "New York City", "location"),
    })
    hits = lex.find("Located in New York City today")
    assert [h[2].label for h in hits] == ["New York City"]


def test_a_claim_with_one_entity_yields_no_triple():
    """Two entities and a recognised predicate between them, or nothing."""
    r = kg_ground_claims("Shih Huang Ti was influential [source: kb1].", conn=_conn())
    assert r["claims"][0]["verdict"] == KG_NO_ENTITIES


def test_no_predicate_between_two_entities_yields_no_guess():
    r = kg_ground_claims("Shih Huang Ti and Ch'in State [source: kb1].", conn=_conn())
    assert r["claims"][0]["verdict"] == KG_NO_ENTITIES


def test_schema_source_is_always_reported():
    """A caller must never have to guess which instrument produced the verdict."""
    assert kg_ground_claims("x [source: k].", conn=_conn())["schema_source"] == SCHEMA_OBSERVED
    assert GraphSchema(source=SCHEMA_DECLARED).can_block is True
    assert GraphSchema(source=SCHEMA_OBSERVED).can_block is False
    assert GraphSchema(source=SCHEMA_UNAVAILABLE).can_block is False
