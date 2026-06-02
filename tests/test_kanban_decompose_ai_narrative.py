# CUI // SP-CTI
"""Tests for the AI-ified decomposition narrative in kanban reflex (aiify-opp-5304).

The _decompose_one_task function gained an opt-in LLM decomposition narrative
(metadata_extraction → llm_generation). These tests pin two load-bearing guarantees:

1. The narrative is best-effort and degrades to ``None`` on ANY failure so the
   deterministic decomposition always completes.
2. When the LLM is available, the helper returns its synthesized content
   grounded in the supplied facts only.
"""

from __future__ import annotations

import types

from tools.genesis.reflexes import kanban


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
        content="  Task add-metrics-endpoint was too large and was split into 3 subtasks ordered by dependency.  ",
    )
    facts = {
        "task_id": "feat-metrics-01",
        "task_title": "Add metrics endpoint",
        "subtask_count": 3,
        "subtask_ids": "feat-metrics-01-d1, feat-metrics-01-d2, feat-metrics-01-d3",
        "subtask_titles": "Add route; Add handler; Add tests",
        "failure_reason": "none (upfront decomposition)",
    }

    out = kanban._ai_decompose_narrative("feat-metrics-01", facts)

    assert out == "Task add-metrics-endpoint was too large and was split into 3 subtasks ordered by dependency."
    assert captured["function"] == "narrative_generation"
    user_msg = captured["request"].messages[0]["content"]
    assert "feat-metrics-01" in user_msg
    assert "task_title" in user_msg
    assert "subtask_count" in user_msg
    assert captured["request"].skip_injection_scan is True


def test_narrative_none_on_llm_exception(monkeypatch):
    _fake_router(monkeypatch, raises=RuntimeError("no provider available"))

    out = kanban._ai_decompose_narrative("task-xyz", {"task_title": "Some task", "subtask_count": 2})

    assert out is None


def test_narrative_none_on_empty_content(monkeypatch):
    _fake_router(monkeypatch, content="")

    out = kanban._ai_decompose_narrative("task-abc", {"task_title": "Another task", "subtask_count": 1})

    assert out is None


def test_narrative_none_when_router_import_fails(monkeypatch):
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _boom(name, *args, **kwargs):
        if name == "tools.llm.router":
            raise ImportError("llm stack unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _boom)

    out = kanban._ai_decompose_narrative("task-no-llm", {"task_title": "Test task", "subtask_count": 4})

    assert out is None


def test_facts_sorted_for_cache_stability(monkeypatch):
    """Fact lines must be sorted so identical inputs produce identical prompts."""
    captured = _fake_router(monkeypatch, content="Narrative text.")
    facts = {"z_last": "last value", "a_first": "first value", "m_middle": "middle value"}

    kanban._ai_decompose_narrative("task-sort-test", facts)

    user_msg = captured["request"].messages[0]["content"]
    pos_a = user_msg.index("a_first")
    pos_m = user_msg.index("m_middle")
    pos_z = user_msg.index("z_last")
    assert pos_a < pos_m < pos_z


def test_classification_is_cui(monkeypatch):
    """LLM requests for decomposition narratives must carry CUI classification."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    kanban._ai_decompose_narrative("task-cui-check", {"task_title": "Build ZIG module", "subtask_count": 3})

    assert captured["request"].classification == "CUI"


def test_max_tokens_and_temperature(monkeypatch):
    """Narrative requests must use max_tokens=512 and temperature=0.3."""
    captured = _fake_router(monkeypatch, content="Narrative.")

    kanban._ai_decompose_narrative("task-params-check", {"task_title": "Fix RLS bug", "subtask_count": 2})

    assert captured["request"].max_tokens == 512
    assert captured["request"].temperature == 0.3


def test_digest_kind_appears_in_prompt(monkeypatch):
    """The task_id label must appear in the user message for prompt framing."""
    captured = _fake_router(monkeypatch, content="Some narrative.")

    kanban._ai_decompose_narrative(
        "feat-zig-pillar-01",
        {"task_title": "Implement ZIG identity pillar", "subtask_count": 5},
    )

    user_msg = captured["request"].messages[0]["content"]
    assert "feat-zig-pillar-01" in user_msg
    assert "task_title" in user_msg
