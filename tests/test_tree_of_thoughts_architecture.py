# CUI // SP-CTI
"""Tests for the Tree of Thoughts architecture (agx-search-02).

The two load-bearing safety properties are asserted directly:
  1. The budget is a CEILING — exceeding max_llm_calls returns degraded=True with
     stop_reason="budget_exceeded", never a fabricated-complete result.
  2. Branch evaluation is deterministic-picker — the enum verdict is composed in
     Python; unknown tokens fail closed to dead_end.
"""
from __future__ import annotations

import json

from tools.llm.architectures import registry
from tools.llm.architectures.envelope import ArchitectureBudget
from tools.llm.architectures.tree_of_thoughts import (
    EVAL_VOCAB,
    branch_score,
    classify_branch,
    tree_of_thoughts,
)


class _FakeResp:
    def __init__(self, content, input_tokens=10, output_tokens=10, cost_usd=0.001):
        self.content = content
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cost_usd = cost_usd
        self.model_id = "fake-model"


class _CountingRouter:
    """Counts calls; scripts expand/evaluate/synthesize replies by prompt shape."""

    def __init__(self, verdict="promising"):
        self.calls = 0
        self.verdict = verdict

    def invoke(self, function, request, **kwargs):
        self.calls += 1
        prompt = ""
        for m in request.messages or []:
            if m.get("role") == "user":
                prompt = m["content"]
        if '"candidates"' in prompt:
            return _FakeResp(json.dumps({"candidates": ["step A", "step B", "step C"]}))
        if '"verdict"' in prompt:
            return _FakeResp(json.dumps({"verdict": self.verdict}))
        return _FakeResp("FINAL ANSWER")


# ── deterministic-picker ────────────────────────────────────────────────────

def test_classify_branch_fails_closed():
    assert classify_branch("promising") == "promising"
    assert classify_branch("garbage") == "dead_end"
    assert classify_branch("") == "dead_end"


def test_branch_scores_are_ordered():
    assert branch_score("promising") > branch_score("maybe") > branch_score("dead_end")


def test_eval_vocab_small_and_bounded():
    assert set(EVAL_VOCAB.values()) == {0.0, 0.5, 1.0}
    assert len(EVAL_VOCAB) == 3


# ── budget is a hard ceiling ────────────────────────────────────────────────

def test_max_llm_calls_ceiling_degrades_honestly():
    router = _CountingRouter(verdict="promising")
    result = tree_of_thoughts(
        "Plan a migration.", router=router,
        beam_width=2, branching_factor=3, max_depth=5,
        max_llm_calls=4,  # tiny ceiling -> must trip
    )
    assert result.degraded is True
    assert result.stop_reason == "budget_exceeded"
    assert result.metadata["budget_exceeded"] is True
    # Never exceeds the ceiling by more than the in-flight call granularity.
    assert result.metadata["cost_report"]["llm_calls"] <= 6


def test_budget_cost_ceiling_trips():
    router = _CountingRouter(verdict="promising")
    result = tree_of_thoughts(
        "Plan a migration.", router=router,
        beam_width=2, branching_factor=3, max_depth=5, max_llm_calls=1000,
        budget=ArchitectureBudget(max_cost_usd=0.002),
    )
    assert result.degraded is True
    assert result.stop_reason == "budget_exceeded"


def test_cost_report_present_and_honest():
    router = _CountingRouter(verdict="promising")
    result = tree_of_thoughts("A branchy task.", router=router,
                              beam_width=1, branching_factor=2, max_depth=1, max_llm_calls=50)
    rep = result.metadata["cost_report"]
    assert rep["llm_calls"] == router.calls
    assert rep["input_tokens"] > 0


# ── happy path ──────────────────────────────────────────────────────────────

def test_completes_within_budget():
    router = _CountingRouter(verdict="promising")
    result = tree_of_thoughts("A branchy task.", router=router,
                              beam_width=2, branching_factor=2, max_depth=1, max_llm_calls=50)
    assert result.stop_reason == "completed"
    assert result.output == "FINAL ANSWER"
    assert result.metadata["best_path"]


def test_dead_end_verdicts_still_terminate():
    router = _CountingRouter(verdict="dead_end")
    result = tree_of_thoughts("A task.", router=router,
                              beam_width=2, branching_factor=2, max_depth=1, max_llm_calls=50)
    # All dead-ends: still returns honestly (completed synthesis or no_path),
    # never crashes.
    assert result.stop_reason in {"completed", "no_path"}


def test_registered_in_registry():
    assert registry.is_registered("tree_of_thoughts")
    assert registry.get("tree_of_thoughts") is tree_of_thoughts
