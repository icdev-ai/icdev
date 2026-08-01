#!/usr/bin/env python3
"""Context budgeting — fill the window, never truncate blind. CUI // SP-CTI.

`_llm_synthesize` sent `results[:5]` at `max_chars=1000` — roughly 1.2k tokens
— **regardless of the model**. Claude Sonnet 4.5 (~200k) saw exactly what an 8k
local model saw, after the retriever had already fetched 50 candidates and
thrown 45 away.

That is the "corpus larger than the context window" problem, and it is not a
retrieval-quality problem: the answer was frequently already retrieved and
discarded before the model was asked.

Two properties matter and are pinned here:

  * the pack SCALES with the real window, and
  * nothing is dropped SILENTLY — a partial view must never read as exhaustive.
"""
from __future__ import annotations

import pytest

from tools.document_intelligence.blueprint import (
    _MAX_EVIDENCE,
    _MIN_EVIDENCE,
    _budgeted_evidence,
)
from tools.llm import context_budget as cb


class _R:
    def __init__(self, i: int, text: str | None = None):
        self.content = text if text is not None else (
            "The contractor shall retain all records for seven years. " * 12
        )
        self.doc_id = f"doc-{i}"
        self.page = 1
        self.doc_title = f"Doc {i}"


# --------------------------------------------------------------------------- #
# Token estimation
# --------------------------------------------------------------------------- #


def test_estimate_over_counts_rather_than_under():
    """Under-estimating overflows a real request; over-estimating packs one fewer.

    The chunker's 4 chars/token is an English-prose average. Code, JSON and CJK
    run far denser, so the estimate here is deliberately tighter.
    """
    text = "a" * 1000
    assert cb.estimate_tokens(text) > 1000 / 4


def test_word_count_is_a_hard_floor():
    """A token is never longer than a word."""
    text = " ".join(["x"] * 500)
    assert cb.estimate_tokens(text) >= 500


@pytest.mark.parametrize("text", ["", None])
def test_estimate_handles_empty(text):
    assert cb.estimate_tokens(text or "") == 0


# --------------------------------------------------------------------------- #
# Window resolution
# --------------------------------------------------------------------------- #


def test_every_routed_model_declares_a_window():
    """The config gap this module exists to close.

    Scoped to ROUTED models: `models:` declares spares no chain references, and
    failing on those would push people to invent numbers they have not
    verified — worse than an honest default.
    """
    gaps = cb.config_gaps()
    assert not gaps, f"routed models with no context_window: {gaps}"


def test_floor_takes_the_smallest_model_in_the_chain(monkeypatch):
    """two_tier, RL re-ranking and the CLI bridge all reorder the chain.

    Budgeting for the largest member and being served by the smallest is an
    overflow, so the minimum is the only safe assumption.
    """
    monkeypatch.setattr(cb, "chain_for_function", lambda fn: ["big", "small"])
    monkeypatch.setattr(cb, "context_window_for",
                        lambda m: 200000 if m == "big" else 32768)
    assert cb.floor_window_for_function("anything") == 32768


def test_available_leaves_room_for_output_and_prompt():
    avail = cb.available_input_tokens(
        "question_answering", system_prompt="x" * 4000, question="y" * 400,
        reserved_output=2048,
    )
    assert 0 < avail < cb.floor_window_for_function("question_answering")


def test_available_never_goes_negative():
    assert cb.available_input_tokens(
        "question_answering", system_prompt="x" * 10_000_000, reserved_output=999999
    ) == 0


# --------------------------------------------------------------------------- #
# The packing itself
# --------------------------------------------------------------------------- #


def test_pack_scales_past_the_old_fixed_five():
    """The headline. 50 retrieved candidates, and the old code used 5."""
    kept, dropped = _budgeted_evidence([_R(i) for i in range(50)], "retention period?")
    assert len(kept) > 5, "budgeting did not widen the funnel at all"
    assert len(kept) + dropped == 50


def test_ceiling_is_respected():
    kept, _ = _budgeted_evidence([_R(i) for i in range(200)], "retention?")
    assert len(kept) <= _MAX_EVIDENCE


def test_small_window_degrades_instead_of_overflowing(monkeypatch):
    """A 4k local model must not be handed a 200k-model's pack."""
    monkeypatch.setattr(cb, "floor_window_for_function", lambda fn: 4096)
    kept, dropped = _budgeted_evidence([_R(i) for i in range(50)], "retention?")
    assert len(kept) >= _MIN_EVIDENCE, "must still cite something"
    assert len(kept) < _MAX_EVIDENCE, "small window should pack fewer"
    assert dropped > 0


def test_minimum_evidence_survives_an_impossible_budget(monkeypatch):
    """Returning nothing would silently produce an UNGROUNDED answer.

    A tight pack is bad; no evidence at all is worse, because the model then
    answers from memory with no citation and nothing signals it.
    """
    monkeypatch.setattr(cb, "available_input_tokens", lambda *a, **k: 1)
    kept, _ = _budgeted_evidence([_R(i) for i in range(10)], "q")
    assert len(kept) >= _MIN_EVIDENCE


def test_dropped_count_is_reported():
    """Never drop silently — the caller must be able to say what is missing."""
    kept, dropped = _budgeted_evidence([_R(i) for i in range(50)], "q")
    assert dropped == 50 - len(kept)


def test_falls_back_to_the_old_slice_on_failure(monkeypatch):
    """A budgeting error must never cost the user their answer."""
    def boom(*a, **k):
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(cb, "available_input_tokens", boom)
    kept, dropped = _budgeted_evidence([_R(i) for i in range(20)], "q")
    assert len(kept) == 5
    assert dropped == 15


@pytest.mark.parametrize("results", [[], None])
def test_empty_results_are_safe(results):
    kept, dropped = _budgeted_evidence(results, "q")
    assert kept == [] and dropped == 0


# --------------------------------------------------------------------------- #
# The pack_evidence primitive
# --------------------------------------------------------------------------- #


def test_pack_reports_everything_it_excluded():
    items = ["word " * 500 for _ in range(20)]
    pack = cb.pack_evidence(items, budget=200)
    assert len(pack.included) + len(pack.dropped) == 20
    assert not pack.complete


def test_pack_is_complete_when_everything_fits():
    pack = cb.pack_evidence(["short"] * 3, budget=100000)
    assert pack.complete and pack.dropped == []
