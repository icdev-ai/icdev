# CUI // SP-CTI
"""Tests for the AI-ified triage narrative in the alert service (aiify-opp-5525).

The db -> render -> notify chains in ``tools.notification_service.alert_service``
gained an opt-in LLM triage narrative. These tests pin the two load-bearing
guarantees:

1. The narrative is best-effort and degrades to ``None`` on ANY failure
   (no-LLM mode, network error, missing credentials) so a security alert
   never depends on LLM availability.
2. When the LLM is available, the helper returns its synthesized content
   and grounds the prompt in the supplied facts only.
"""

from __future__ import annotations

import types

from tools.notification_service import alert_service


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
    captured = _fake_router(monkeypatch, content="  Remediate the CAT-I finding now.  ")
    facts = {"finding_title": "Unpatched OpenSSL", "severity": "CAT I", "sla_hours": 24}

    out = alert_service._ai_alert_narrative("CAT-I security finding escalation", facts)

    assert out == "Remediate the CAT-I finding now."  # stripped
    assert captured["function"] == "narrative_generation"
    # Facts are grounded into the user prompt, sorted for cache stability.
    user_msg = captured["request"].messages[0]["content"]
    assert "Unpatched OpenSSL" in user_msg
    assert "sla_hours: 24" in user_msg
    # Trusted first-party facts skip the injection scan.
    assert captured["request"].skip_injection_scan is True


def test_narrative_none_on_llm_exception(monkeypatch):
    _fake_router(monkeypatch, raises=RuntimeError("no provider available"))

    out = alert_service._ai_alert_narrative("STIG check finding alert", {"check_id": "V-1"})

    assert out is None  # graceful degradation, never raises


def test_narrative_none_on_empty_content(monkeypatch):
    _fake_router(monkeypatch, content="")

    out = alert_service._ai_alert_narrative("POA&M deadline reminder", {"poam_id": "P-1"})

    assert out is None


def test_narrative_none_when_router_import_fails(monkeypatch):
    # Simulate an environment where the LLM stack is absent entirely.
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def _boom(name, *args, **kwargs):
        if name == "tools.llm.router":
            raise ImportError("llm stack unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", _boom)

    out = alert_service._ai_alert_narrative("CAT-I security finding escalation", {"x": 1})

    assert out is None


def test_facts_sorted_for_cache_stability(monkeypatch):
    """Fact lines must be sorted so identical inputs produce identical prompts."""
    captured = _fake_router(monkeypatch, content="Review the CAT-I finding immediately.")
    facts = {"z_sla_hours": 24, "a_finding_title": "Unpatched OpenSSL", "m_severity": "CAT I"}

    alert_service._ai_alert_narrative("CAT-I security finding escalation", facts)

    user_msg = captured["request"].messages[0]["content"]
    pos_a = user_msg.index("a_finding_title")
    pos_m = user_msg.index("m_severity")
    pos_z = user_msg.index("z_sla_hours")
    assert pos_a < pos_m < pos_z
