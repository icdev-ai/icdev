# CUI // SP-CTI
"""Multi-turn (history) + model-pin support on cortex.complete (chat enabler)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.cortex import api
from tools.cortex import governance as gov
from tools.cortex.schemas import CortexContext


@pytest.fixture(autouse=True)
def _stub_governance(monkeypatch):
    monkeypatch.setattr(gov, "_gate_record_audit", lambda p: None)
    monkeypatch.setattr(gov, "_gate_register_provenance", lambda t, c, o, r: "scr")
    monkeypatch.setattr(gov, "_gate_check_text",
                        lambda t: {"allowed": True, "warnings": [], "blocked_reason": None})
    monkeypatch.setattr(gov, "_gate_redact_input", lambda t, c: (t, 0))
    monkeypatch.setattr(gov, "_gate_redact_output", lambda t: (t, []))


def _capture_invoke(monkeypatch):
    cap = {}

    def _fake(function, request, context):
        cap["messages"] = list(request.messages)
        cap["model"] = getattr(request, "model", None)
        cap["system_prompt"] = request.system_prompt
        return SimpleNamespace(content="ok", provider="p", model_id="m",
                               cost_usd=0.0, duration_ms=1, input_tokens=1, output_tokens=1)

    monkeypatch.setattr(api, "_invoke", _fake)
    return cap


def test_history_prepended_current_turn_last(monkeypatch):
    cap = _capture_invoke(monkeypatch)
    api.complete(
        "current turn",
        ctx=CortexContext(tenant_id="t"),
        history=[{"role": "user", "content": "prev q"},
                 {"role": "assistant", "content": "prev a"}],
        model="pinned-model",
        system_prompt="be brief",
    )
    assert [m["content"] for m in cap["messages"]] == ["prev q", "prev a", "current turn"]
    assert cap["messages"][-1]["role"] == "user"
    assert cap["model"] == "pinned-model"
    assert cap["system_prompt"] == "be brief"


def test_no_history_is_single_turn(monkeypatch):
    cap = _capture_invoke(monkeypatch)
    api.complete("just this", ctx=CortexContext())
    assert cap["messages"] == [{"role": "user", "content": "just this"}]
    assert not cap["model"]  # unset -> LLMRequest default (no pin)


def test_malformed_history_is_dropped(monkeypatch):
    cap = _capture_invoke(monkeypatch)
    api.complete(
        "q",
        ctx=CortexContext(),
        history=[
            "not a dict",
            {"role": "bogus", "content": "x"},   # unknown role -> dropped
            {"role": "user", "content": "kept"},
            {"role": "assistant"},                # missing content -> ""
        ],
    )
    kept = cap["messages"]
    assert {"role": "user", "content": "kept"} in kept
    assert {"role": "assistant", "content": ""} in kept
    assert all(isinstance(m, dict) for m in kept)
    assert kept[-1] == {"role": "user", "content": "q"}


def test_history_current_turn_is_the_governed_prompt(monkeypatch):
    # Input redaction rewrites the CURRENT turn; history passes through as-is.
    monkeypatch.setattr(gov, "_gate_redact_input", lambda t, c: ("[REDACTED]", 1))
    cap = _capture_invoke(monkeypatch)
    api.complete("my ssn is 000-00-0000", ctx=CortexContext(),
                 history=[{"role": "user", "content": "earlier"}])
    assert cap["messages"][0] == {"role": "user", "content": "earlier"}  # history untouched
    assert cap["messages"][-1]["content"] == "[REDACTED]"  # current turn redacted
