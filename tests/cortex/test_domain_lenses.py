# CUI // SP-CTI
"""Tests for the document / proposal / network domain lenses + persona wiring.

The lens machinery (DomainProfile / apply_persona) already existed and was
consumed only for search source-scoping; these tests pin the three new profiles
and the new `complete` persona-injection seam (_apply_domain_persona).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.cortex import api
from tools.cortex import governance as _gov
from tools.cortex.domains import apply_persona, load_domain_profile
from tools.cortex.schemas import CortexContext


@pytest.fixture(autouse=True)
def _stub_governance_sinks(monkeypatch):
    """Keep complete()/classify() fully in-memory — no DB audit/provenance write,
    no heavy gateway/anonymizer. We only assert on the request the facade builds."""
    monkeypatch.setattr(_gov, "_gate_record_audit", lambda payload: None)
    monkeypatch.setattr(_gov, "_gate_register_provenance",
                        lambda text, ctx, op, rid: "scr-test")
    monkeypatch.setattr(_gov, "_gate_check_text",
                        lambda text: {"allowed": True, "warnings": [],
                                      "blocked_reason": None})
    monkeypatch.setattr(_gov, "_gate_redact_input", lambda text, cls: (text, 0))
    monkeypatch.setattr(_gov, "_gate_redact_output", lambda text: (text, []))


@pytest.mark.parametrize("domain,needle,has_triage", [
    ("document", "records and compliance analyst", True),
    ("proposal", "capture manager", True),
    ("network", "network architect", False),  # no bespoke formatter yet
])
def test_new_profiles_load_with_persona(domain, needle, has_triage):
    profile = load_domain_profile(domain)
    assert profile is not None
    assert needle in profile.persona
    assert profile.backends  # non-empty backend scope
    assert profile.triage is has_triage


def test_apply_persona_prepends_and_preserves_base():
    ctx = CortexContext(domain="proposal")
    out = apply_persona(ctx, "Follow the house style guide.")
    assert out.startswith(load_domain_profile("proposal").persona.split()[0])
    assert "capture manager" in out
    assert "house style guide" in out  # caller's base prompt preserved


def test_apply_persona_noop_without_domain():
    assert apply_persona(CortexContext(), "base") == "base"
    assert apply_persona(CortexContext(domain=""), "base") == "base"


# --------------------------------------------------------------------------- #
# complete() persona-injection seam
# --------------------------------------------------------------------------- #
def _fake_response():
    return SimpleNamespace(
        content="ok", provider="ollama", model_id="m", cost_usd=0.0, duration_ms=1,
    )


def test_complete_injects_domain_persona(monkeypatch):
    captured = {}

    def _fake_invoke(function, request, context):
        captured["system_prompt"] = request.system_prompt
        return _fake_response()

    monkeypatch.setattr(api, "_invoke", _fake_invoke)
    api.complete("Draft the intro.", ctx=CortexContext(domain="network"),
                 system_prompt="Keep it under 200 words.")
    sp = captured["system_prompt"]
    assert "network architect" in sp
    assert "Keep it under 200 words." in sp  # composed, not replaced


def test_complete_no_domain_leaves_system_prompt_untouched(monkeypatch):
    captured = {}

    def _fake_invoke(function, request, context):
        captured["system_prompt"] = request.system_prompt
        return _fake_response()

    monkeypatch.setattr(api, "_invoke", _fake_invoke)
    api.complete("Draft the intro.", ctx=CortexContext(), system_prompt="base only")
    assert captured["system_prompt"] == "base only"


def test_classify_extract_do_not_get_persona(monkeypatch):
    # Structured facades must stay terse — persona is complete-only.
    captured = {}

    def _fake_invoke(function, request, context):
        captured["system_prompt"] = request.system_prompt
        # Return a response whose content is one of the caller's labels so
        # classify() resolves through the patched router path (where the
        # system prompt under test is built). A non-matching content ("ok")
        # would make classify() degrade to tools.rag.query_classifier, whose
        # own — unpatched — router.invoke makes a real LLM/DB call that hangs
        # offline, and which never sees the system prompt we assert on here.
        return SimpleNamespace(
            content="a", provider="ollama", model_id="m",
            cost_usd=0.0, duration_ms=1,
        )

    monkeypatch.setattr(api, "_invoke", _fake_invoke)
    api.classify("some text", ["a", "b"], ctx=CortexContext(domain="network"))
    assert "network architect" not in (captured.get("system_prompt") or "")
