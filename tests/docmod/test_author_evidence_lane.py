# CUI // SP-CTI
"""The docmod evidence lane hands the pack the STORE's winner (dwr-ev-01).

``_currency_lane`` used to re-sort Cortex's structured claims by
authority-then-confidence — a second copy of the precedence rule, and one that
disagreed with the store the moment a source declared a precedence: the store
ranked the author's assertion first and this lane handed the pack the curated
catalog's. Now the store's rank rides on every claim and the lane preserves it.
No LLM anywhere on this path, and the losing sources travel with the answer.
"""
from __future__ import annotations

from tools.doc_modernization import evidence as seam

_MODEL = "Zeta ZX-4400 Chassis"


class _Resolution:
    def __init__(self, claims):
        self.citations = []
        self.backends_consulted = ["currency"]
        self.backend_errors = []
        self.metadata = {"entity_resolution": {"claims": list(claims)}}


def _claim(**over):
    claim = {
        "entity_label": _MODEL, "entity_type": "hardware_model", "backend": "currency",
        "extraction": "structured", "status": "deprecated", "raw_status": "end_of_life",
        "source": "docmod_catalog_entries", "authoritative": True, "confidence": 0.95,
        "as_of": "2026-08-30", "rank": 0, "eol_date": "", "eos_date": "", "superseded_by": "",
    }
    claim.update(over)
    return claim


def _author_first():
    """What Cortex hands back when the store ranked the author top: the author
    claim is rank 0 and NOT authoritative; the catalog is rank 1 AND
    authoritative, with a higher declared confidence."""
    return _Resolution(claims=[
        _claim(source="dic_author_assertions", authoritative=False, confidence=0.9,
               status="current", raw_status="current", as_of="2026-08-01", rank=0),
        _claim(rank=1),
    ])


def test_lane_preserves_the_stores_rank_over_its_own_idea_of_authority():
    lane = seam._currency_lane(_author_first())
    assert [c["source"] for c in lane] == ["dic_author_assertions", "docmod_catalog_entries"]


def test_currency_assertion_hands_the_pack_the_author_with_the_catalog_beside_it():
    bundle = seam.CortexEvidence(entity=_MODEL, currency=seam._currency_lane(_author_first()))
    hit = seam.currency_assertion(bundle)
    assert hit["source"] == "dic_author_assertions"
    assert hit["verdict"] == "current"
    assert hit["as_of"] == "2026-08-01"
    assert hit["authoritative"] is False
    assert hit["conflict"] is True
    (other,) = hit["others"]
    assert other == {
        "source": "docmod_catalog_entries", "verdict": "end_of_life", "confidence": 0.95,
        "as_of": "2026-08-30", "authoritative": True, "rank": 1,
    }


def test_claims_without_a_rank_keep_the_old_order():
    """An older carrier stamps no rank: every claim reads as a winner and the
    previous key (authority, confidence, source) still decides."""
    resolution = _Resolution(claims=[
        {k: v for k, v in _claim(source="docmod_eol_products", authoritative=False,
                                 confidence=0.8).items() if k != "rank"},
        {k: v for k, v in _claim().items() if k != "rank"},
    ])
    lane = seam._currency_lane(resolution)
    assert [c["source"] for c in lane] == ["docmod_catalog_entries", "docmod_eol_products"]


def test_entity_resolution_stamps_the_stores_rank_on_every_structured_claim():
    from tools.cortex import entity_resolution as er
    from tools.cortex.schemas import Citation, CortexSearchResult

    hit = CortexSearchResult(
        content="...", score=0.9, backend="currency", strategy="assertion",
        citation=Citation(source_id="ec-1", source_type="currency_assertion",
                          source_table="dic_author_assertions", title=_MODEL),
        metadata={
            "entity_label": _MODEL, "entity_type": "hardware_model", "verdict": "current",
            "source": "dic_author_assertions", "authoritative": False, "confidence": 0.9,
            "as_of": "2026-08-01", "precedence": 0,
            "others": [
                {"source": "docmod_catalog_entries", "verdict": "end_of_life",
                 "authoritative": True, "confidence": 0.95, "as_of": "2026-08-30", "rank": 1},
                {"source": "mc_net_eol_data", "verdict": "end_of_support",
                 "authoritative": False, "confidence": 0.75, "as_of": "2026-07-01"},
            ],
        },
    )
    claims = er._structured_claims(hit)
    assert [(c.source, c.rank) for c in claims] == [
        ("dic_author_assertions", 0), ("docmod_catalog_entries", 1), ("mc_net_eol_data", 2),
    ]
    assert all(c.extraction == "structured" for c in claims)
    # Round trip: rank survives to_dict/from_dict, so the seam reads it.
    assert er.EntityClaim.from_dict(claims[1].to_dict()).rank == 1
