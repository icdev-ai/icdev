# CUI // SP-CTI
"""Tests for the governed cortex.reason facade (CoT / debate / council).

The router's multi-step orchestration methods already existed
(invoke_chain_of_thought / _debate / _council); reason() exposes them through
the same TRUST chain as complete(). Governance sinks are stubbed so the facade
runs fully in-memory.
"""
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


def _fake_router(record):
    def _mk(name):
        def _m(function, request):
            record["method"] = name
            record["function"] = function
            record["prompt"] = request.messages[-1]["content"]
            return SimpleNamespace(content=f"{name} result", provider="p",
                                   model_id="m", cost_usd=0.0, duration_ms=1,
                                   input_tokens=3, output_tokens=2)
        return _m
    return SimpleNamespace(
        invoke_chain_of_thought=_mk("invoke_chain_of_thought"),
        invoke_chain_of_debate=_mk("invoke_chain_of_debate"),
        invoke_council=_mk("invoke_council"),
    )


@pytest.mark.parametrize("mode,method", [
    ("cot", "invoke_chain_of_thought"),
    ("debate", "invoke_chain_of_debate"),
    ("council", "invoke_council"),
])
def test_reason_dispatches_by_mode(monkeypatch, mode, method):
    rec = {}
    monkeypatch.setattr(api, "_get_router", lambda: _fake_router(rec))
    result = api.reason("Design a cache.", mode=mode, ctx=CortexContext(tenant_id="t"))
    assert rec["method"] == method
    assert result.text == f"{method} result"
    assert result.metadata["reason_mode"] == mode
    assert result.input_tokens == 3 and result.output_tokens == 2


def test_reason_unknown_mode_raises():
    with pytest.raises(ValueError, match="unknown reason mode"):
        api.reason("x", mode="telepathy", ctx=CortexContext())


def test_reason_is_governed_and_registered():
    assert getattr(api.reason, "__cortex_governed__", False) is True
    assert getattr(api.reason, "__cortex_operation__", "") == "cortex.reason"
    assert "reason" in api.CORTEX_FACADES
    # The package re-exports a governed reason facade (identity is not asserted —
    # a sibling test's importlib.reload can mint a distinct object; the governance
    # stamp is the real invariant).
    from tools.cortex import reason as pkg_reason
    assert getattr(pkg_reason, "__cortex_governed__", False) is True


def test_reason_applies_domain_persona(monkeypatch):
    rec = {}
    monkeypatch.setattr(api, "_get_router", lambda: _fake_router(rec))
    captured = {}
    orig = api._build_request

    def _spy(content, ctx, **kw):
        captured["system_prompt"] = kw.get("system_prompt", "")
        return orig(content, ctx, **kw)

    monkeypatch.setattr(api, "_build_request", _spy)
    api.reason("Draft.", mode="cot", ctx=CortexContext(domain="network"))
    assert "network architect" in captured["system_prompt"]
