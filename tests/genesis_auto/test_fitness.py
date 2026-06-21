#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for NOVA SELA — tools/evolution/fitness.py

Validates: FitnessScore composite formula, score_fast heuristics,
length_penalty, score_examples mode gating. LLM is mocked — no real calls.
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


# ---------------------------------------------------------------------------
# Import checks
# ---------------------------------------------------------------------------

def test_fitness_imports():
    """Module imports without error."""
    import tools.evolution.fitness  # noqa: F401


def test_fitness_classes_and_functions_exist():
    """FitnessScore, score_fast, score_full, score_examples are exposed."""
    from tools.evolution.fitness import FitnessScore, score_fast, score_full, score_examples
    for obj in (FitnessScore, score_fast, score_full, score_examples):
        assert obj is not None


# ---------------------------------------------------------------------------
# FitnessScore.composite formula
# ---------------------------------------------------------------------------

def test_fitness_score_composite_formula():
    """composite = 0.5*correctness + 0.3*procedure + 0.2*conciseness - length_penalty"""
    from tools.evolution.fitness import FitnessScore

    fs = FitnessScore(
        correctness=0.8,
        procedure_following=0.6,
        conciseness=0.5,
        length_penalty=0.0,
    )
    expected = round(0.5 * 0.8 + 0.3 * 0.6 + 0.2 * 0.5, 4)
    assert abs(fs.composite - expected) < 1e-3


def test_fitness_score_composite_subtracts_penalty():
    """length_penalty is subtracted from composite."""
    from tools.evolution.fitness import FitnessScore

    fs_no_penalty = FitnessScore(correctness=0.8, procedure_following=0.6, conciseness=0.5, length_penalty=0.0)
    fs_with_penalty = FitnessScore(correctness=0.8, procedure_following=0.6, conciseness=0.5, length_penalty=0.1)
    assert fs_with_penalty.composite < fs_no_penalty.composite


def test_fitness_score_composite_clamped_to_zero():
    """composite never goes below 0.0 even with high penalties."""
    from tools.evolution.fitness import FitnessScore

    fs = FitnessScore(correctness=0.0, procedure_following=0.0, conciseness=0.0, length_penalty=1.0)
    assert fs.composite == 0.0


def test_fitness_score_composite_clamped_to_one():
    """composite never exceeds 1.0 even with perfect scores."""
    from tools.evolution.fitness import FitnessScore

    fs = FitnessScore(correctness=1.0, procedure_following=1.0, conciseness=1.0, length_penalty=0.0)
    assert fs.composite <= 1.0


# ---------------------------------------------------------------------------
# score_fast — returns FitnessScore with composite in [0, 1]
# ---------------------------------------------------------------------------

def test_score_fast_returns_fitness_score():
    """score_fast returns a FitnessScore instance."""
    from tools.evolution.fitness import FitnessScore, score_fast

    result = score_fast(
        task_input="build a feature",
        expected_behavior="all tests pass and feature is live",
        actual_output="success: feature created and tests passed",
        skill_text="# Build Skill\nRun tests. Deploy feature.",
    )
    assert isinstance(result, FitnessScore)


def test_score_fast_composite_in_range():
    """score_fast composite is always in [0.0, 1.0]."""
    from tools.evolution.fitness import score_fast

    result = score_fast(
        task_input="run tests",
        expected_behavior="all tests pass",
        actual_output="success: 42 tests passed",
        skill_text="# Test Skill\nRun pytest.",
    )
    assert 0.0 <= result.composite <= 1.0


def test_score_fast_empty_output_returns_zero():
    """score_fast with empty actual_output returns a low/zero composite."""
    from tools.evolution.fitness import score_fast

    result = score_fast(
        task_input="do something",
        expected_behavior="task completed successfully",
        actual_output="",
        skill_text="# Skill\nDo thing.",
    )
    assert result.composite == 0.0


def test_score_fast_success_marker_boosts_correctness():
    """Output with success markers scores higher correctness than one without."""
    from tools.evolution.fitness import score_fast

    base_kwargs = dict(
        task_input="deploy service",
        expected_behavior="service deployed and running",
        skill_text="# Deploy\nRun deploy script.",
    )
    good = score_fast(actual_output="success: service deployed and running", **base_kwargs)
    bad = score_fast(actual_output="the operation was attempted", **base_kwargs)
    assert good.composite >= bad.composite


def test_score_fast_failure_marker_hurts_correctness():
    """Output with error/traceback markers scores lower than clean output."""
    from tools.evolution.fitness import score_fast

    base_kwargs = dict(
        task_input="run tests",
        expected_behavior="all tests pass and complete",
        skill_text="# Test Skill\nRun pytest and verify pass.",
    )
    clean = score_fast(actual_output="all tests pass and complete", **base_kwargs)
    broken = score_fast(actual_output="Traceback (most recent call last): error: failed", **base_kwargs)
    assert clean.composite >= broken.composite


# ---------------------------------------------------------------------------
# score_fast — length_penalty applied to very long outputs
# ---------------------------------------------------------------------------

def test_score_fast_length_penalty_for_long_output():
    """Very long output receives a positive length_penalty."""
    from tools.evolution.fitness import score_fast

    short_output = "success: done"
    long_output = "x" * 5000  # >> max_length default (4000)

    short_result = score_fast(
        task_input="task", expected_behavior="done", actual_output=short_output,
        skill_text="# Skill\nDo thing.", max_length=4000,
    )
    long_result = score_fast(
        task_input="task", expected_behavior="done", actual_output=long_output,
        skill_text="# Skill\nDo thing.", max_length=4000,
    )
    assert long_result.length_penalty > 0.0
    assert short_result.length_penalty == 0.0


def test_score_fast_same_quality_longer_output_lower_composite():
    """Given otherwise equal outputs, a longer one scores lower due to length_penalty."""
    from tools.evolution.fitness import score_fast

    base_kwargs = dict(
        task_input="deploy",
        expected_behavior="deployed successfully done",
        skill_text="# Deploy\nDeploy service.",
        max_length=4000,
    )
    short = score_fast(actual_output="deployed successfully done", **base_kwargs)
    long = score_fast(actual_output="deployed successfully done " + ("x " * 2000), **base_kwargs)
    assert long.length_penalty >= short.length_penalty


# ---------------------------------------------------------------------------
# score_examples — mode gating (fast does NOT call LLMRouter)
# ---------------------------------------------------------------------------

def test_score_examples_fast_mode_does_not_call_llm():
    """score_examples with mode='fast' never calls LLMRouter."""
    from tools.evolution.fitness import score_examples
    from tools.evolution.eval_builder import EvalExample

    examples = [
        EvalExample("e1", "run tests", "all tests pass", "easy", "testing", "golden"),
        EvalExample("e2", "build feature", "feature deployed", "medium", "build", "golden"),
    ]

    with patch("tools.evolution.fitness.score_full") as mock_full:
        mean, scores = score_examples(examples, candidate_skill_text="# Skill\nRun tests.", mode="fast")

    mock_full.assert_not_called()
    assert 0.0 <= mean <= 1.0
    assert len(scores) == 2


def test_score_examples_returns_mean_and_list():
    """score_examples returns (float, list[FitnessScore])."""
    from tools.evolution.fitness import FitnessScore, score_examples
    from tools.evolution.eval_builder import EvalExample

    examples = [
        EvalExample("e1", "task A", "expected A", "easy", "test", "golden"),
        EvalExample("e2", "task B", "expected B", "hard", "test", "golden"),
        EvalExample("e3", "task C", "expected C", "medium", "test", "golden"),
    ]

    mean, scores = score_examples(examples, candidate_skill_text="# Skill\nDo tasks.", mode="fast")
    assert isinstance(mean, float)
    assert isinstance(scores, list)
    assert len(scores) == 3
    for s in scores:
        assert isinstance(s, FitnessScore)


def test_score_examples_empty_list_returns_zero():
    """score_examples with empty list returns (0.0, [])."""
    from tools.evolution.fitness import score_examples

    mean, scores = score_examples([], candidate_skill_text="# Skill", mode="fast")
    assert mean == 0.0
    assert scores == []


def test_score_examples_full_mode_calls_score_full():
    """score_examples with mode='full' calls score_full (mocked) for each example."""
    from tools.evolution.fitness import FitnessScore, score_examples
    from tools.evolution.eval_builder import EvalExample

    examples = [EvalExample("e1", "task", "expected", "easy", "test", "golden")]
    fake_score = FitnessScore(correctness=0.9, procedure_following=0.8, conciseness=0.7)

    with patch("tools.evolution.fitness.score_full", return_value=fake_score) as mock_full:
        mean, scores = score_examples(examples, candidate_skill_text="# Skill", mode="full")

    mock_full.assert_called_once()
    assert scores[0] is fake_score


# ---------------------------------------------------------------------------
# score_full — falls back to heuristic on LLM error
# ---------------------------------------------------------------------------

def test_score_full_falls_back_to_heuristic_on_error():
    """score_full falls back to score_fast when LLM raises an exception."""
    from tools.evolution.fitness import FitnessScore, score_full

    # LLMRouter is imported lazily inside score_full; patch it in its home module
    with patch("tools.llm.router.LLMRouter", side_effect=ImportError("no LLM")):
        result = score_full(
            task_input="run tests",
            expected_behavior="all tests pass",
            actual_output="success: tests passed",
            skill_text="# Skill\nRun tests.",
        )

    assert isinstance(result, FitnessScore)
    assert 0.0 <= result.composite <= 1.0


def test_score_full_empty_output():
    """score_full with empty actual_output returns composite=0.0."""
    from tools.evolution.fitness import score_full

    result = score_full(
        task_input="run",
        expected_behavior="done",
        actual_output="",
        skill_text="# Skill",
    )
    assert result.composite == 0.0
