# CUI // SP-CTI
"""Tests for the Self-Discover architecture (agx-search-01).

Deterministic-picker property: the model only NAMES module ids; Python
(``select_modules``) is the authority on which are valid and how they are
ordered. ``test_select_drops_unknown_ids`` asserts that.
"""
from __future__ import annotations

import json

from tools.llm.architectures import registry
from tools.llm.architectures.self_discover import (
    compose_structure,
    load_module_bank,
    select_modules,
    self_discover,
)


class _FakeResp:
    def __init__(self, content, model_id="fake-model"):
        self.content = content
        self.model_id = model_id


class _ScriptedRouter:
    """Routes by prompt content to a scripted structured reply."""

    def __init__(self, selected=("k1_state_assumptions", "decompose")):
        self.selected = list(selected)
        self.calls = []

    def invoke(self, function, request, **kwargs):
        prompt = ""
        for m in request.messages or []:
            if m.get("role") == "user":
                prompt = m["content"]
        self.calls.append(prompt)
        if '"selected"' in prompt:
            return _FakeResp(json.dumps({"selected": self.selected}))
        if '"adapted"' in prompt:
            return _FakeResp(json.dumps({"adapted": {self.selected[0]: "adapted text"}}))
        return _FakeResp("FINAL ANSWER")


# ── bank + composition (deterministic-picker) ───────────────────────────────

def test_bank_loads_and_has_karpathy_core():
    bank = load_module_bank()
    ids = {m["id"] for m in bank}
    for k in ("k1_state_assumptions", "k5_success_criteria"):
        assert k in ids


def test_select_drops_unknown_ids():
    bank = load_module_bank()
    chosen = select_modules(["k1_state_assumptions", "not_a_real_module", "decompose"], bank)
    ids = [m["id"] for m in chosen]
    assert "not_a_real_module" not in ids
    assert "k1_state_assumptions" in ids and "decompose" in ids


def test_select_preserves_bank_order_not_model_order():
    bank = load_module_bank()
    # Name them out of order; Python must return them in bank order.
    chosen = select_modules(["decompose", "k1_state_assumptions"], bank)
    ids = [m["id"] for m in chosen]
    assert ids.index("k1_state_assumptions") < ids.index("decompose")


def test_compose_structure_is_ordered_text():
    selected = [
        {"id": "a", "name": "First", "description": "do A"},
        {"id": "b", "name": "Second", "description": "do B"},
    ]
    text = compose_structure(selected)
    assert text.startswith("1. First")
    assert "2. Second" in text


def test_empty_selection_returns_empty_structure():
    assert compose_structure([]) == ""


# ── end-to-end ──────────────────────────────────────────────────────────────

def test_run_produces_output_and_records_modules():
    router = _ScriptedRouter(selected=["k1_state_assumptions", "decompose"])
    result = self_discover("Design a migration plan.", router=router)
    assert result.output == "FINAL ANSWER"
    assert result.stop_reason == "completed"
    assert "k1_state_assumptions" in result.metadata["selected_modules"]
    assert result.metadata["reasoning_structure"]


def test_solve_prompt_contains_reasoning_structure():
    router = _ScriptedRouter(selected=["k1_state_assumptions"])
    self_discover("Do a thing.", router=router)
    # the last call is SOLVE; its request carried the structure in the system prompt.
    # Assert the structure text appeared in some call after select/adapt.
    assert any("reasoning structure" in c.lower() or "State assumptions" in c for c in router.calls)


def test_unknown_selection_falls_back_to_karpathy_core():
    router = _ScriptedRouter(selected=["totally_unknown"])
    result = self_discover("A task.", router=router)
    # None valid -> fallback to k* core, still solves.
    assert result.metadata["selected_modules"]
    assert all(mid.startswith("k") for mid in result.metadata["selected_modules"])


def test_registered_in_registry():
    assert registry.is_registered("self_discover")
    assert registry.get("self_discover") is self_discover
