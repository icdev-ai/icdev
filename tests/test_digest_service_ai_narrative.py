# CUI // SP-CTI
"""Tests for the AI-ified digest narrative in the digest service (aiify-opp-5550).

The db -> render -> notify chains in ``tools.notification_service.digest_service``
gained an opt-in LLM digest narrative. These tests pin the two load-bearing
guarantees:

1. The narrative is best-effort and degrades to ``None`` on ANY failure
   (no-LLM mode, network error, missing credentials) so a scheduled digest
   never depends on LLM availability.
2. When the LLM is available, the helper returns its synthesized content
   and grounds the prompt in the supplied facts only.
"""

from __future__ import annotations

import types

from tools.notification_service import digest_service


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
    captured = _fake_router(monkeypatch, content="  Three tasks completed today; prioritize the two overdue items.  ")
    facts = {
        "date": "2026-06-01",
        "status_breakdown": "done: 3; in_progress: 2; backlog: 10",
        "status_group_count": 3,
        "recently_completed": "Add ZIG maturity report; Fix RLS subquery bug",
    }

    out = digest_service._ai_digest_narrative("daily kanban digest", facts)

    assert out == "Three tasks completed today; prioritize the two overdue items."  # stripped
    assert captured["function"] == "narrative_generation"
    # Facts are grounded into the user prompt, sorted for cache stability.
    user_msg = captured["request"].messages[0]["content"]
    assert "daily kanban digest" in user_msg
    assert "status_breakdown" in user_msg
    assert "recently_completed" in user_msg
    # Trusted first-party facts skip the injection scan.
    assert captured["request"].skip_injection_scan is True


def test_narrative_none_on_llm_exception(monkeypatch):
    _fake_router(monkeypatch, raises=RuntimeError("no provider available"))

    out = digest_service._ai_digest_narrative("oracle weekly prediction digest", {"prediction_groups": 4})

    assert out is None  # graceful degradation, never raises


def test_narrative_none_on_empty_content(monkeypatch):
    _fake_router(monkeypatch, content="")

    out = digest_service._ai_digest_narrative("compliance posture report", {"overall_score": 85.0})

    assert out is None


def test_narrative_none_when_router_import_fails(monkeypatch):
    # Simulate an environment where the LLM stack is absent entirely.
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _boom(name, *args, **kwargs):
        if name == "tools.llm.router":
            raise ImportError("llm stack unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _boom)

    out = digest_service._ai_digest_narrative("ZIG zero-trust maturity report", {"overall_pct": 72.5})

    assert out is None


def test_facts_sorted_for_cache_stability(monkeypatch):
    """Fact lines must be sorted so identical inputs produce identical prompts."""
    captured = _fake_router(monkeypatch, content="Narrative text.")
    facts = {"z_last": "last", "a_first": "first", "m_middle": "middle"}

    digest_service._ai_digest_narrative("agent health alert", facts)

    user_msg = captured["request"].messages[0]["content"]
    pos_a = user_msg.index("a_first")
    pos_m = user_msg.index("m_middle")
    pos_z = user_msg.index("z_last")
    assert pos_a < pos_m < pos_z
