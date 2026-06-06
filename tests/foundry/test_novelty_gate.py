# CUI // SP-CTI
"""Tests for tools/foundry/novelty_gate.py — the ACF dedup / anti-incremental gate.

Two layers:
  * Injected-catalog tests pin exact threshold behaviour (duplicate / low_novelty / survive).
  * Real-catalog tests prove the gate rejects a concept identical to an existing canvas
    and lets a genuinely-novel concept through.
"""

from __future__ import annotations

import pytest

from tools.foundry import novelty_gate as ng
from tools.foundry.novelty_gate import CatalogEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _catalog(*texts: str) -> list[CatalogEntry]:
    return [CatalogEntry(f"x:{i}", "tool", f"e{i}", t) for i, t in enumerate(texts)]


DEFAULT_CFG = dict(ng.DEFAULTS)  # min_novelty=0.35, duplicate_similarity=0.85


# ---------------------------------------------------------------------------
# Deterministic similarity primitives
# ---------------------------------------------------------------------------
def test_tokenizer_drops_stopwords_and_singletons():
    toks = ng._tokenize("The Network Design Canvas a of AI ndc")
    assert "network" in toks and "ndc" in toks and "ai" in toks
    # stopwords + structural noise removed
    assert "the" not in toks and "design" not in toks and "canvas" not in toks and "of" not in toks


def test_similarity_identical_is_one_disjoint_is_zero():
    a = ng._tokenize("alpha beta gamma")
    assert ng._similarity(a, a) == pytest.approx(1.0)
    assert ng._similarity(a, ng._tokenize("zeta theta omega")) == 0.0


# ---------------------------------------------------------------------------
# score_novelty — injected catalog (exact threshold control)
# ---------------------------------------------------------------------------
def test_exact_duplicate_scores_zero_and_is_rejected():
    cat = _catalog("alpha beta gamma delta epsilon")
    concept = {"name": "alpha beta", "proposed_capability": "gamma delta epsilon"}
    res = ng.score_novelty(concept, config=DEFAULT_CFG, catalog=cat)
    assert res["novelty_score"] == pytest.approx(0.0, abs=1e-9)
    assert res["is_duplicate"] is True
    assert res["rejected"] is True
    assert res["reject_reason"] == "duplicate"
    assert res["nearest"]["similarity"] == pytest.approx(1.0)


def test_partial_overlap_below_min_novelty_is_low_novelty():
    cat = _catalog("alpha beta gamma delta epsilon")
    concept = {"proposed_capability": "alpha beta gamma"}
    res = ng.score_novelty(concept, config=DEFAULT_CFG, catalog=cat)
    # blended sim ~0.70 -> novelty ~0.30 < 0.35 min, but < 0.85 dup threshold
    assert res["is_duplicate"] is False
    assert res["rejected"] is True
    assert res["reject_reason"] == "low_novelty"
    assert 0.0 < res["novelty_score"] < DEFAULT_CFG["min_novelty"]


def test_genuinely_novel_concept_survives_the_gate():
    cat = _catalog("alpha beta gamma delta epsilon")
    concept = {"proposed_capability": "alpha zeta theta omega"}
    res = ng.score_novelty(concept, config=DEFAULT_CFG, catalog=cat)
    assert res["is_duplicate"] is False
    assert res["rejected"] is False
    assert res["reject_reason"] is None
    assert res["novelty_score"] > DEFAULT_CFG["min_novelty"]


def test_empty_concept_is_maximally_novel_not_rejected():
    res = ng.score_novelty({}, config=DEFAULT_CFG, catalog=_catalog("alpha beta"))
    assert res["novelty_score"] == 1.0
    assert res["rejected"] is False
    assert res["nearest"] is None


def test_empty_catalog_yields_full_novelty():
    res = ng.score_novelty({"name": "anything"}, config=DEFAULT_CFG, catalog=[])
    assert res["novelty_score"] == 1.0
    assert res["catalog_size"] == 0


def test_config_thresholds_are_honored():
    cat = _catalog("alpha beta gamma delta epsilon")
    concept = {"proposed_capability": "alpha beta gamma"}
    # Loosen min_novelty so the same partial-overlap concept now survives.
    cfg = {**DEFAULT_CFG, "min_novelty": 0.1}
    res = ng.score_novelty(concept, config=cfg, catalog=cat)
    assert res["rejected"] is False
    # Tighten the duplicate threshold so the same concept is now a duplicate.
    cfg2 = {**DEFAULT_CFG, "duplicate_similarity": 0.5}
    res2 = ng.score_novelty(concept, config=cfg2, catalog=cat)
    assert res2["is_duplicate"] is True
    assert res2["reject_reason"] == "duplicate"


# ---------------------------------------------------------------------------
# apply_novelty_gate — concept annotation
# ---------------------------------------------------------------------------
def test_apply_novelty_gate_marks_rejected_concept():
    cat = _catalog("alpha beta gamma delta epsilon")
    concept = {"name": "alpha beta", "proposed_capability": "gamma delta epsilon", "status": "proposed"}
    out = ng.apply_novelty_gate(concept, config=DEFAULT_CFG, catalog=cat)
    assert out is concept  # annotates in place
    assert concept["status"] == "rejected"
    assert concept["reject_reason"] == "duplicate"
    assert concept["novelty_score"] == pytest.approx(0.0, abs=1e-9)


def test_apply_novelty_gate_leaves_novel_concept_proposed():
    cat = _catalog("alpha beta gamma delta epsilon")
    concept = {"proposed_capability": "underwater basket weaving penguins", "status": "proposed"}
    ng.apply_novelty_gate(concept, config=DEFAULT_CFG, catalog=cat)
    assert concept["status"] == "proposed"
    assert "reject_reason" not in concept
    assert concept["novelty_score"] > DEFAULT_CFG["min_novelty"]


# ---------------------------------------------------------------------------
# Real capability catalog (built from existing ICDEV assets)
# ---------------------------------------------------------------------------
def test_build_catalog_includes_canvas_entries():
    cat = ng.build_catalog(force_rebuild=True)
    assert len(cat) > 0
    kinds = {e.kind for e in cat}
    assert "canvas" in kinds  # canvas_registry always available
    # The Network Design Canvas should be present by name.
    assert any("Network Design Canvas" in e.name for e in cat)


def test_concept_identical_to_existing_canvas_is_rejected():
    cat = ng.build_catalog(force_rebuild=True)
    # Mirror an existing canvas exactly (name + flow noun + key).
    concept = {
        "name": "Network Design Canvas",
        "proposed_capability": "traffic flow ndc",
    }
    res = ng.score_novelty(concept, catalog=cat)
    assert res["novelty_score"] < 0.05
    assert res["is_duplicate"] is True
    assert res["rejected"] is True
    assert res["reject_reason"] == "duplicate"


def test_genuinely_new_capability_scores_high_against_real_catalog():
    cat = ng.build_catalog(force_rebuild=True)
    concept = {
        "name": "Apiary Thermoregulation Forecaster",
        "proposed_capability": "underwater basket weaving choreography for migratory penguins",
        "problem_statement": "penguins lack synchronized basket choreography during migration",
    }
    res = ng.score_novelty(concept, catalog=cat)
    assert res["is_duplicate"] is False
    assert res["rejected"] is False
    assert res["novelty_score"] > 0.5
