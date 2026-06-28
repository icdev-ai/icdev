"""Tests: ACE Markov step sequencer — TransitionMatrix and MarkovSequencer logic."""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from icdev.tools.ace.markov_sequencer import (
    MarkovSequencer,
    TransitionMatrix,
    patch_role_template,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_matrix(
    role_id: str = "test_role",
    counts: dict[str, dict[str, int]] | None = None,
) -> TransitionMatrix:
    return TransitionMatrix(
        role_id=role_id,
        counts=counts or {},
        built_at="2026-01-01T00:00:00+00:00",
    )


def _make_mock_conn(rows: list | None = None) -> MagicMock:
    """Return a mock DB connection that returns rows from fetchall()."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = rows or []
    conn.execute.return_value = cursor
    return conn


@dataclass
class _FakeStep:
    name: str
    tool: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    condition: str | None = None


@dataclass
class _FakeTemplate:
    role_id: str
    steps: list[_FakeStep]
    display_name: str = ""
    description: str = ""
    version: str = "1.0"
    trust_tier: str = "yellow"
    default_count: int = 1
    max_instances: int = 1
    communication: dict[str, Any] = field(default_factory=dict)
    llm_function: str = ""
    tool_permissions: list[str] = field(default_factory=list)
    genesis_reflex: str = ""


# ---------------------------------------------------------------------------
# TransitionMatrix.get_probs
# ---------------------------------------------------------------------------


class TestTransitionMatrixGetProbs:
    def test_basic_normalisation(self):
        matrix = _make_matrix(counts={"a": {"b": 3, "c": 1}})
        probs = matrix.get_probs("a")
        assert abs(probs["b"] - 0.75) < 1e-9
        assert abs(probs["c"] - 0.25) < 1e-9

    def test_unknown_step_returns_empty(self):
        matrix = _make_matrix(counts={"a": {"b": 3}})
        assert matrix.get_probs("unknown") == {}

    def test_empty_counts_returns_empty(self):
        matrix = _make_matrix()
        assert matrix.get_probs("anything") == {}

    def test_single_successor_prob_is_one(self):
        matrix = _make_matrix(counts={"x": {"y": 5}})
        probs = matrix.get_probs("x")
        assert abs(probs["y"] - 1.0) < 1e-9

    def test_probs_sum_to_one(self):
        matrix = _make_matrix(counts={"s": {"a": 2, "b": 3, "c": 5}})
        probs = matrix.get_probs("s")
        assert abs(sum(probs.values()) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# TransitionMatrix.recommend_next
# ---------------------------------------------------------------------------


class TestTransitionMatrixRecommendNext:
    def test_reorders_by_probability_descending(self):
        matrix = _make_matrix(counts={"a": {"b": 3, "c": 1}})
        result = matrix.recommend_next("a", ["c", "b"])
        assert result == ["b", "c"]

    def test_unknown_candidates_go_last(self):
        matrix = _make_matrix(counts={"a": {"b": 5}})
        result = matrix.recommend_next("a", ["unknown", "b"])
        assert result == ["b", "unknown"]

    def test_unknown_from_step_returns_candidates_unchanged(self):
        matrix = _make_matrix(counts={"a": {"b": 5}})
        result = matrix.recommend_next("z", ["x", "y"])
        assert result == ["x", "y"]

    def test_all_unknown_candidates_preserved_order(self):
        matrix = _make_matrix(counts={"a": {"b": 5}})
        result = matrix.recommend_next("a", ["x", "y", "z"])
        assert result == ["x", "y", "z"]

    def test_three_candidates_sorted_correctly(self):
        matrix = _make_matrix(counts={"s": {"a": 10, "b": 3, "c": 7}})
        result = matrix.recommend_next("s", ["b", "c", "a"])
        assert result == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# MarkovSequencer.recommend_next — no history fallback
# ---------------------------------------------------------------------------


class TestMarkovSequencerRecommendNextNoHistory:
    def _seq_no_data(self) -> MarkovSequencer:
        conn = _make_mock_conn(rows=[])  # empty DB
        return MarkovSequencer(role_id="test_role", conn=conn, min_samples=5)

    def test_returns_candidates_unchanged_when_no_history(self):
        seq = self._seq_no_data()
        candidates = ["run_tests", "implement_code", "refactor"]
        result = seq.recommend_next("write_tests", candidates)
        assert result == candidates

    def test_preserves_original_list_object_not_mutated(self):
        seq = self._seq_no_data()
        original = ["a", "b", "c"]
        result = seq.recommend_next("x", original)
        assert result is not original  # returns a new list
        assert result == original


# ---------------------------------------------------------------------------
# MarkovSequencer.entropy — max entropy when no data
# ---------------------------------------------------------------------------


class TestMarkovSequencerEntropy:
    def _seq_no_data(self) -> MarkovSequencer:
        conn = _make_mock_conn(rows=[])
        return MarkovSequencer(role_id="role", conn=conn, min_samples=5)

    def test_entropy_max_when_no_data_two_candidates(self):
        seq = self._seq_no_data()
        result = seq.entropy("any_step", candidates=["a", "b"])
        assert abs(result - math.log2(2)) < 1e-9

    def test_entropy_max_when_no_data_four_candidates(self):
        seq = self._seq_no_data()
        result = seq.entropy("any_step", candidates=["a", "b", "c", "d"])
        assert abs(result - math.log2(4)) < 1e-9

    def test_entropy_default_two_when_no_candidates(self):
        seq = self._seq_no_data()
        result = seq.entropy("any_step")
        assert abs(result - math.log2(2)) < 1e-9

    def test_entropy_low_for_deterministic_distribution(self):
        # 9 successes of a→b, 1 of a→c — low entropy expected
        rows = [("a", "b", 9), ("a", "c", 1)]
        conn = _make_mock_conn(rows=rows)
        seq = MarkovSequencer(role_id="role", conn=conn, min_samples=5)
        ent = seq.entropy("a")
        # Max would be log2(2)=1.0; deterministic skew should be well below that
        assert ent < 0.6

    def test_entropy_max_for_uniform_distribution(self):
        rows = [("a", "b", 5), ("a", "c", 5)]
        conn = _make_mock_conn(rows=rows)
        seq = MarkovSequencer(role_id="role", conn=conn, min_samples=5)
        ent = seq.entropy("a")
        assert abs(ent - math.log2(2)) < 1e-6


# ---------------------------------------------------------------------------
# MarkovSequencer.get_transition_probs — min_samples gate
# ---------------------------------------------------------------------------


class TestMarkovSequencerMinSamples:
    def test_returns_empty_below_min_samples(self):
        # 3 total transitions, min_samples=5 → empty
        rows = [("a", "b", 3)]
        conn = _make_mock_conn(rows=rows)
        seq = MarkovSequencer(role_id="role", conn=conn, min_samples=5)
        assert seq.get_transition_probs("a") == {}

    def test_returns_probs_at_or_above_min_samples(self):
        rows = [("a", "b", 3), ("a", "c", 2)]  # total=5
        conn = _make_mock_conn(rows=rows)
        seq = MarkovSequencer(role_id="role", conn=conn, min_samples=5)
        probs = seq.get_transition_probs("a")
        assert abs(probs.get("b", 0) - 0.6) < 1e-9
        assert abs(probs.get("c", 0) - 0.4) < 1e-9


# ---------------------------------------------------------------------------
# MarkovSequencer.record_transition
# ---------------------------------------------------------------------------


class TestMarkovSequencerRecordTransition:
    def test_record_calls_execute_and_commit(self):
        conn = _make_mock_conn()
        seq = MarkovSequencer(role_id="role", conn=conn, min_samples=1)
        seq.record_transition("step_a", "step_b", success=True)
        assert conn.execute.called
        assert conn.commit.called

    def test_record_passes_correct_params(self):
        conn = _make_mock_conn()
        seq = MarkovSequencer(role_id="my_role", conn=conn, min_samples=1)
        seq.record_transition("from_x", "to_y", success=False, session_id="s1")
        call_args = conn.execute.call_args
        params = call_args[0][1]  # positional args[1] = params tuple
        assert params[0] == "my_role"
        assert params[1] == "from_x"
        assert params[2] == "to_y"
        assert params[3] is False

    def test_record_invalidates_matrix_cache(self):
        conn = _make_mock_conn(rows=[])
        seq = MarkovSequencer(role_id="role", conn=conn, min_samples=1)
        # Build matrix to populate cache
        seq._get_matrix()
        assert seq._matrix_cache is not None
        # Record transition invalidates cache
        seq.record_transition("a", "b")
        assert seq._matrix_cache is None


# ---------------------------------------------------------------------------
# patch_role_template
# ---------------------------------------------------------------------------


class TestPatchRoleTemplate:
    def _no_data_seq(self, role_id: str = "role") -> MarkovSequencer:
        conn = _make_mock_conn(rows=[])
        return MarkovSequencer(role_id=role_id, conn=conn, min_samples=5)

    def test_returns_original_when_no_history(self):
        steps = [_FakeStep("a"), _FakeStep("b"), _FakeStep("c")]
        tmpl = _FakeTemplate(role_id="role", steps=steps)
        seq = self._no_data_seq()
        result = patch_role_template(tmpl, seq)
        assert result is tmpl  # unchanged object returned

    def test_returns_original_for_single_step(self):
        tmpl = _FakeTemplate(role_id="role", steps=[_FakeStep("a")])
        seq = self._no_data_seq()
        result = patch_role_template(tmpl, seq)
        assert result is tmpl

    def test_returns_original_for_empty_steps(self):
        tmpl = _FakeTemplate(role_id="role", steps=[])
        seq = self._no_data_seq()
        result = patch_role_template(tmpl, seq)
        assert result is tmpl

    def test_reorders_steps_with_sufficient_history(self):
        # Sequence observed: a→c more often than a→b
        rows = [("a", "c", 8), ("a", "b", 2), ("c", "b", 5)]
        conn = _make_mock_conn(rows=rows)
        seq = MarkovSequencer(role_id="role", conn=conn, min_samples=5)

        steps = [_FakeStep("a"), _FakeStep("b"), _FakeStep("c")]
        tmpl = _FakeTemplate(role_id="role", steps=steps)
        result = patch_role_template(tmpl, seq)

        assert result is not tmpl  # new object returned
        assert result.steps[0].name == "a"  # first step unchanged
        assert result.steps[1].name == "c"  # c preferred over b from a
        assert result.steps[2].name == "b"

    def test_first_step_always_preserved(self):
        rows = [("a", "c", 10), ("a", "b", 1)]
        conn = _make_mock_conn(rows=rows)
        seq = MarkovSequencer(role_id="role", conn=conn, min_samples=5)

        steps = [_FakeStep("a"), _FakeStep("b"), _FakeStep("c")]
        tmpl = _FakeTemplate(role_id="role", steps=steps)
        result = patch_role_template(tmpl, seq)

        assert result.steps[0].name == "a"


# ---------------------------------------------------------------------------
# MarkovSequencer.top_sequences
# ---------------------------------------------------------------------------


class TestMarkovSequencerTopSequences:
    def test_returns_empty_when_no_history(self):
        conn = _make_mock_conn(rows=[])
        seq = MarkovSequencer(role_id="role", conn=conn, min_samples=1)
        assert seq.top_sequences() == []

    def test_single_greedy_sequence(self):
        # a→b→c chain
        rows = [("a", "b", 5), ("b", "c", 5)]
        conn = _make_mock_conn(rows=rows)
        seq = MarkovSequencer(role_id="role", conn=conn, min_samples=1)
        seqs = seq.top_sequences(n=1)
        assert len(seqs) == 1
        assert seqs[0][0] == "a"
        assert seqs[0][1] == "b"

    def test_limits_to_n(self):
        rows = [("a", "b", 3), ("c", "d", 2), ("e", "f", 1)]
        conn = _make_mock_conn(rows=rows)
        seq = MarkovSequencer(role_id="role", conn=conn, min_samples=1)
        seqs = seq.top_sequences(n=2)
        assert len(seqs) <= 2


# ---------------------------------------------------------------------------
# MarkovSequencer.build_matrix — row parsing
# ---------------------------------------------------------------------------


class TestMarkovSequencerBuildMatrix:
    def test_parses_tuple_rows(self):
        rows = [("a", "b", 3), ("a", "c", 1)]
        conn = _make_mock_conn(rows=rows)
        seq = MarkovSequencer(role_id="role", conn=conn, min_samples=1)
        matrix = seq.build_matrix()
        assert matrix.counts["a"]["b"] == 3
        assert matrix.counts["a"]["c"] == 1

    def test_parses_dict_rows(self):
        rows = [{"from_step": "a", "to_step": "b", "cnt": 7}]
        conn = _make_mock_conn(rows=rows)
        seq = MarkovSequencer(role_id="role", conn=conn, min_samples=1)
        matrix = seq.build_matrix()
        assert matrix.counts["a"]["b"] == 7

    def test_empty_db_returns_empty_counts(self):
        conn = _make_mock_conn(rows=[])
        seq = MarkovSequencer(role_id="role", conn=conn, min_samples=1)
        matrix = seq.build_matrix()
        assert matrix.counts == {}
        assert matrix.role_id == "role"
