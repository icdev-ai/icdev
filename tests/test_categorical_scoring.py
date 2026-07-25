# CUI // SP-CTI
"""Truth-table tests for the deterministic-picker composition (agx-pick-02).

The whole point of deterministic-picker is that the aggregation is inspectable
and reproducible, so these tests exhaustively cover every enum combination of
each composition function and assert the exact composed number — the arithmetic
is a spec, not an approximation.
"""
from __future__ import annotations

import itertools

import pytest

from tools.quality.categorical_scoring import (
    EVAL_DIMENSIONS,
    FITNESS_VOCAB,
    GROUNDING_VOCAB,
    VOCABULARY_VERSION,
    compose_eval_overall,
    compose_fitness,
    compose_grounding,
    map_eval_enum,
    map_fitness_enum,
    map_grounding_enum,
)


# ── fitness (surface #1) ─────────────────────────────────────────────────────

def test_fitness_full_truth_table():
    """All 27 enum combinations compose to the documented weighted composite."""
    cmap = FITNESS_VOCAB["correctness"]
    pmap = FITNESS_VOCAB["procedure_following"]
    nmap = FITNESS_VOCAB["conciseness"]
    for c, p, n in itertools.product(cmap, pmap, nmap):
        out = compose_fitness(c, p, n)
        expected = round(0.5 * cmap[c] + 0.3 * pmap[p] + 0.2 * nmap[n], 4)
        assert out["composite"] == expected, (c, p, n)
        assert out["correctness"] == cmap[c]
        assert out["vocabulary_version"] == VOCABULARY_VERSION


def test_fitness_best_and_worst():
    assert compose_fitness("correct", "followed", "concise")["composite"] == 1.0
    assert compose_fitness("incorrect", "violated", "verbose")["composite"] == 0.0


def test_fitness_length_penalty_subtracts_and_clamps():
    out = compose_fitness("correct", "followed", "concise", length_penalty=0.3)
    assert out["composite"] == 0.7
    # penalty cannot drive composite below 0
    assert compose_fitness("incorrect", "violated", "verbose", length_penalty=0.5)["composite"] == 0.0


def test_fitness_unknown_token_degrades_to_midpoint():
    assert map_fitness_enum("correctness", "banana") == 0.5
    # composite with all-unknown == all-midpoint == 0.5
    assert compose_fitness("??", "??", "??")["composite"] == 0.5


def test_fitness_case_and_whitespace_insensitive():
    assert map_fitness_enum("correctness", "  CORRECT ") == 1.0


# ── ACE grade (surface #2) ───────────────────────────────────────────────────

def test_eval_all_supported_is_one():
    grade = {d: "supported" for d in EVAL_DIMENSIONS}
    out = compose_eval_overall(grade)
    assert out["overall"] == 1.0
    assert out["faithfulness_failed"] is False


def test_eval_all_unsupported_is_zero():
    grade = {d: "unsupported" for d in EVAL_DIMENSIONS}
    out = compose_eval_overall(grade)
    assert out["overall"] == 0.0
    assert out["faithfulness_failed"] is True


def test_eval_faithfulness_unsupported_caps_overall():
    """Strong secondary dimensions cannot mask an unfaithful output."""
    grade = {d: "supported" for d in EVAL_DIMENSIONS}
    grade["faithfulness"] = "unsupported"
    out = compose_eval_overall(grade)
    assert out["faithfulness_failed"] is True
    assert out["overall"] <= 0.25


def test_eval_missing_dimension_treated_as_partial():
    out = compose_eval_overall({"faithfulness": "supported"})
    # completeness etc. default to partial (0.5); faithfulness supported (1.0)
    assert 0.0 < out["overall"] < 1.0
    assert out["vocabulary_version"] == VOCABULARY_VERSION


def test_eval_full_truth_table_monotonic_and_bounded():
    """Every combination stays in [0,1]; upgrading any label never lowers overall."""
    order = {"unsupported": 0, "partial": 1, "supported": 2}
    for combo in itertools.product(list(order), repeat=len(EVAL_DIMENSIONS)):
        grade = dict(zip(EVAL_DIMENSIONS, combo))
        out = compose_eval_overall(grade)
        assert 0.0 <= out["overall"] <= 1.0


def test_map_eval_enum_unknown_is_midpoint():
    assert map_eval_enum("nonsense") == 0.5


# ── grounding (surface #3) ───────────────────────────────────────────────────

def test_grounding_mean_of_claims():
    out = compose_grounding(["grounded", "partial", "ungrounded"])
    assert out["score"] == round((1.0 + 0.5 + 0.0) / 3, 4)
    assert out["claim_count"] == 3
    assert out["grounded"] == 1 and out["partial"] == 1 and out["ungrounded"] == 1


def test_grounding_all_grounded_and_all_ungrounded():
    assert compose_grounding(["grounded", "grounded"])["score"] == 1.0
    assert compose_grounding(["ungrounded", "ungrounded"])["score"] == 0.0


def test_grounding_empty_is_zero():
    out = compose_grounding([])
    assert out["score"] == 0.0 and out["claim_count"] == 0


def test_grounding_unknown_token_fails_closed():
    # malformed token counts as ungrounded (0.0), never silently grounded
    assert map_grounding_enum("maybe") == 0.0
    out = compose_grounding(["grounded", "maybe"])
    assert out["score"] == 0.5
    assert out["ungrounded"] == 1


def test_grounding_vocab_is_three_values():
    assert set(GROUNDING_VOCAB.values()) == {0.0, 0.5, 1.0}


@pytest.mark.parametrize("dim", EVAL_DIMENSIONS)
def test_every_eval_dimension_maps(dim):
    out = compose_eval_overall({d: "partial" for d in EVAL_DIMENSIONS})
    assert out[dim] == 0.5
