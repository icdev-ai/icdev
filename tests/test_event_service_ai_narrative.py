# CUI // SP-CTI
"""Tests for the AI-ified event narrative in the event service (aiify-opp-5901).

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
    captured = _fake_router(monkeypatch, content="  Task TASK-42 completed; verify sprint closure gates pass before archiving.  ")
    facts = {
        "task_id": "TASK-42",
        "title": "Add ZIG maturity report",
        "actor": "sovanna",
        "event_type": "task_completed",
    }

    out = event_service._ai_event_narrative("kanban task event notification", facts)

    assert out == "Task TASK-42 completed; verify sprint closure gates pass before archiving."
    assert captured["function"] == "narrative_generation"
    user_msg = captured["request"].messages[0]["content"]
    assert "kanban task event notification" in user_msg
    assert "task_id" in user_msg
    assert "actor" in user_msg
    assert captured["request"].skip_injection_scan is True


def test_narrative_none_on_llm_exception(monkeypatch):
    _fake_router(monkeypatch, raises=RuntimeError("no provider available"))

    out = event_service._ai_event_narrative(
        "genesis milestone notification", {"design_id": "d-1", "phase": "validate"}
    )

    assert out is None


def test_narrative_none_on_empty_content(monkeypatch):
    _fake_router(monkeypatch, content="")

    out = event_service._ai_event_narrative(
        "oracle prediction alert notification", {"lens_id": "L-1", "count": 3}
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
        "kanban task event notification", {"task_id": "TASK-7", "event_type": "task_blocked"}
    )

    assert out is None


def test_facts_sorted_for_cache_stability(monkeypatch):
    """Fact lines must be sorted so identical inputs produce identical prompts."""
    captured = _fake_router(monkeypatch, content="Narrative text.")
    facts = {"z_last": "last", "a_first": "first", "m_middle": "middle"}

    event_service._ai_event_narrative("genesis milestone notification", facts)

    user_msg = captured["request"].messages[0]["content"]
    pos_a = user_msg.index("a_first")
    pos_m = user_msg.index("m_middle")
    pos_z = user_msg.index("z_last")
    assert pos_a < pos_m < pos_z


def test_event_kind_appears_in_prompt(monkeypatch):
    """The event_kind label must appear in the user message for framing."""
    captured = _fake_router(monkeypatch, content="Some narrative.")

    event_service._ai_event_narrative(
        "oracle prediction alert notification",
        {"lens_id": "L-99", "count": 5, "alert_type": "cat1_escalate"},
    )

    user_msg = captured["request"].messages[0]["content"]
    assert "oracle prediction alert notification" in user_msg
    assert "lens_id" in user_msg


def test_classification_is_cui(monkeypatch):
    """LLM requests for event narratives must carry CUI classification."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    event_service._ai_event_narrative("kanban task event notification", {"task_id": "TASK-1"})

    assert captured["request"].classification == "CUI"


def test_max_tokens_and_temperature(monkeypatch):
    """Narrative requests must use max_tokens=512 and temperature=0.3."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    event_service._ai_event_narrative("genesis milestone notification", {"design_id": "d-1", "phase": "build"})

    assert captured["request"].max_tokens == 512
    assert captured["request"].temperature == 0.3
