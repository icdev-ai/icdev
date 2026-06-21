# CUI // SP-CTI
"""Tests for the AI-ified event narrative in the notification event service (aiify-opp-5716).

The db -> render -> notify chains in ``tools.notification_service.event_service``
gained an opt-in LLM event narrative. These tests pin the two load-bearing
guarantees:

1. The narrative is best-effort and degrades to ``None`` on ANY failure
   (no-LLM mode, network error, missing credentials) so an event notification
   never depends on LLM availability.
2. When the LLM is available, the helper returns its synthesized content
   and grounds the prompt in the supplied facts only.
"""

from __future__ import annotations

import types

from tools.notification_service import event_service


def _fake_router(monkeypatch, *, content=None, raises=None):
    """Install a fake LLMRouter whose ``invoke`` returns/raises as directed."""
    captured = {}

    class _Resp:
        def __init__(self, text):
            self.content = text

    class _FakeRouter:
        def invoke(self, function, request):
            captured["function"] = function
            captured["request"] = request
            if raises is not None:
                raise raises
            return _Resp(content)

    fake_module = types.SimpleNamespace(LLMRouter=_FakeRouter)
    monkeypatch.setitem(__import__("sys").modules, "tools.llm.router", fake_module)
    return captured


def test_narrative_returns_content_when_llm_available(monkeypatch):
    captured = _fake_router(
        monkeypatch,
        content="  Task dt-zig-01 completed; verify all downstream dependents before closing the epic.  ",
    )
    facts = {
        "task_id": "dt-zig-01",
        "title": "Implement ZIG Identity Pillar",
        "actor": "sovanna",
        "duration": "2h 15m",
        "attempts": "1",
    }

    out = event_service._ai_event_narrative("kanban task_completed notification", facts)

    assert out == "Task dt-zig-01 completed; verify all downstream dependents before closing the epic."
    assert captured["function"] == "narrative_generation"
    user_msg = captured["request"].messages[0]["content"]
    assert "kanban task_completed notification" in user_msg
    assert "task_id" in user_msg
    assert "duration" in user_msg
    assert captured["request"].skip_injection_scan is True


def test_narrative_none_on_llm_exception(monkeypatch):
    _fake_router(monkeypatch, raises=RuntimeError("no provider available"))

    out = event_service._ai_event_narrative(
        "genesis phase_complete milestone notification",
        {"design_id": "d-001", "phase": "Architect", "next_phase": "Navigate"},
    )

    assert out is None


def test_narrative_none_on_empty_content(monkeypatch):
    _fake_router(monkeypatch, content="")

    out = event_service._ai_event_narrative(
        "oracle cat1_new alert notification",
        {"lens_id": "risk-lens-1", "title": "Critical finding", "confidence": "0.92"},
    )

    assert out is None


def test_narrative_none_when_router_import_fails(monkeypatch):
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _boom(name, *args, **kwargs):
        if name == "tools.llm.router":
            raise ImportError("llm stack unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _boom)

    out = event_service._ai_event_narrative(
        "kanban task_blocked notification",
        {"task_id": "dt-dic-03", "actor": "system", "reason": "dependency missing"},
    )

    assert out is None


def test_facts_sorted_for_cache_stability(monkeypatch):
    """Fact lines must be sorted so identical inputs produce identical prompts."""
    captured = _fake_router(monkeypatch, content="Narrative text.")
    facts = {"z_tokens": "4096", "a_actor": "sovanna", "m_task_id": "dt-zig-02"}

    event_service._ai_event_narrative("kanban token_limit notification", facts)

    user_msg = captured["request"].messages[0]["content"]
    pos_a = user_msg.index("a_actor")
    pos_m = user_msg.index("m_task_id")
    pos_z = user_msg.index("z_tokens")
    assert pos_a < pos_m < pos_z


def test_event_kind_appears_in_prompt(monkeypatch):
    """The event_kind label must appear in the user message for framing."""
    captured = _fake_router(monkeypatch, content="Some narrative.")

    event_service._ai_event_narrative(
        "genesis drift_detected milestone notification",
        {"component": "coherence_checker", "delta": "0.15", "action": "auto-fix"},
    )

    user_msg = captured["request"].messages[0]["content"]
    assert "genesis drift_detected milestone notification" in user_msg
    assert "component" in user_msg


def test_classification_is_cui(monkeypatch):
    """LLM requests for event narratives must carry CUI classification."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    event_service._ai_event_narrative(
        "oracle convergence alert notification",
        {"lens_id": "risk-lens-2", "count": "3", "finding": "auth bypass risk"},
    )

    assert captured["request"].classification == "CUI"


def test_max_tokens_and_temperature(monkeypatch):
    """Narrative requests must use max_tokens=512 and temperature=0.3."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    event_service._ai_event_narrative(
        "genesis reflex_fired milestone notification",
        {"reflex_name": "drift-reflex", "confidence": "0.85", "summary": "Drift corrected"},
    )

    assert captured["request"].max_tokens == 512
    assert captured["request"].temperature == 0.3


def test_narrative_stripped_of_whitespace(monkeypatch):
    """Returned narrative must be stripped — no leading/trailing whitespace."""
    _fake_router(monkeypatch, content="\n\n  Padded narrative.  \n")

    out = event_service._ai_event_narrative(
        "kanban sprint_closed notification",
        {"sprint": "Sprint-7", "done_count": "12", "total_count": "14"},
    )

    assert out == "Padded narrative."
