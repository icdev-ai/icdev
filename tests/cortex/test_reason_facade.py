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
from tools.mcp import gap_handlers


@pytest.fixture(autouse=True)
def _stub_governance(monkeypatch):
    monkeypatch.setattr(gov, "_gate_record_audit", lambda p: None)
    monkeypatch.setattr(gov, "_gate_register_provenance", lambda t, c, o, r: "scr")
    monkeypatch.setattr(gov, "_gate_check_text",
                        lambda t: {"allowed": True, "warnings": [], "blocked_reason": None})
    monkeypatch.setattr(gov, "_gate_redact_input", lambda t, c: (t, 0))
    monkeypatch.setattr(gov, "_gate_redact_output", lambda t: (t, []))


def _fake_router(record, **extra):
    """Router double. ``extra`` adds attributes to the returned LLMResponse
    stand-in (e.g. chain_rounds / stop_reason, which the real orchestration
    methods hang on the response)."""
    def _mk(name):
        def _m(function, request):
            record["method"] = name
            record["function"] = function
            record["prompt"] = request.messages[-1]["content"]
            return SimpleNamespace(content=f"{name} result", provider="p",
                                   model_id="m", cost_usd=0.0, duration_ms=1,
                                   input_tokens=3, output_tokens=2, **extra)
        return _m
    return SimpleNamespace(
        invoke_chain_of_thought=_mk("invoke_chain_of_thought"),
        invoke_chain_of_debate=_mk("invoke_chain_of_debate"),
        invoke_council=_mk("invoke_council"),
    )


# Council rounds as the orchestrator emits them: telemetry per step, no model
# prose. Only the ``advisor:`` steps are the council_query tool's advisor_rounds.
_COUNCIL_ROUNDS = [
    {"step": "advisor:Contrarian", "model_id": "m1", "input_tokens": 10,
     "output_tokens": 20, "cost_usd": 0.001, "duration_ms": 500},
    {"step": "advisor:Executor", "model_id": "m2", "input_tokens": 11,
     "output_tokens": 21, "cost_usd": 0.002, "duration_ms": 600},
    {"step": "peer_review", "model_id": "m1", "input_tokens": 30,
     "output_tokens": 5, "cost_usd": 0.003, "duration_ms": 700},
    {"step": "chairman", "model_id": "m3", "input_tokens": 40,
     "output_tokens": 60, "cost_usd": 0.004, "duration_ms": 800},
]


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


# ---------------------------------------------------------------------------
# Chain telemetry carried onto CortexResult.data (cxo-adopt-03)
# ---------------------------------------------------------------------------
def test_reason_carries_chain_rounds_and_stop_reason(monkeypatch):
    """_result_from_response drops the chain fields; reason() must re-attach them.

    Without this, every caller that needs the per-step view (council_query's
    advisor_rounds, the cortex_reason MCP tool) has to bypass the governed
    facade and call the router directly to see them.
    """
    rec = {}
    monkeypatch.setattr(api, "_get_router", lambda: _fake_router(
        rec, chain_rounds=_COUNCIL_ROUNDS, stop_reason="completed"))
    result = api.reason("Should we ship?", mode="council", ctx=CortexContext(tenant_id="t"))
    assert result.data["chain_rounds"] == _COUNCIL_ROUNDS
    assert result.data["stop_reason"] == "completed"
    # A copy, not the response's own list — a caller mutating data must not
    # reach back into the router's result object.
    assert result.data["chain_rounds"] is not _COUNCIL_ROUNDS


def test_reason_chain_fields_default_when_response_lacks_them(monkeypatch):
    """A response without the chain attributes yields empty, typed defaults."""
    rec = {}
    monkeypatch.setattr(api, "_get_router", lambda: _fake_router(rec))
    result = api.reason("Think.", mode="cot", ctx=CortexContext())
    assert result.data["chain_rounds"] == []
    assert result.data["stop_reason"] == ""


# ---------------------------------------------------------------------------
# council_query retargeted onto the governed facade (cxo-adopt-03)
# ---------------------------------------------------------------------------
def test_council_query_preserves_response_shape(monkeypatch):
    """The live cross-repo callers' contract: {verdict, advisor_rounds, stop_reason}."""
    rec = {}
    monkeypatch.setattr(api, "_get_router", lambda: _fake_router(
        rec, chain_rounds=_COUNCIL_ROUNDS, stop_reason="completed"))

    out = gap_handlers.handle_council_query(
        {"question": "Should we ship?", "context": "scores: 8/10"})

    assert set(out) == {"verdict", "advisor_rounds", "stop_reason"}
    assert out["verdict"] == "invoke_council result"
    assert out["stop_reason"] == "completed"
    # advisor_rounds is the advisor: subset only — peer_review/chairman filtered out.
    assert [r["step"] for r in out["advisor_rounds"]] == [
        "advisor:Contrarian", "advisor:Executor"]
    # Same orchestration + same routing function as the direct router call it replaced.
    assert rec["method"] == "invoke_council"
    assert rec["function"] == "idealab_council_query"
    assert "scores: 8/10" in rec["prompt"] and "Should we ship?" in rec["prompt"]


def test_council_query_is_governed_audited_and_redacted(monkeypatch):
    """The point of the retarget: no ungoverned LLM egress.

    Asserts the call now produces a cortex_audit row for cortex.reason and that
    output redaction reaches the verdict the tool returns.
    """
    rec = {}
    monkeypatch.setattr(api, "_get_router", lambda: _fake_router(
        rec, chain_rounds=_COUNCIL_ROUNDS, stop_reason="completed"))

    audited: list = []
    monkeypatch.setattr(gov, "_gate_record_audit", lambda p: audited.append(p))
    screened: list = []
    monkeypatch.setattr(gov, "_gate_check_text", lambda t: screened.append(t) or {
        "allowed": True, "warnings": [], "blocked_reason": None})
    monkeypatch.setattr(gov, "_gate_redact_output",
                        lambda t: (t.replace("result", "[REDACTED]"), ["fake-pii"]))

    # A question distinct from the sibling tests': the response cache is opt-in
    # and off by default, but if it were on, an identical (operation, text, ctx)
    # would serve a cached result and never reach the router or the audit gate.
    out = gap_handlers.handle_council_query({"question": "Is the council governed?"})

    assert out["verdict"] == "invoke_council [REDACTED]"
    assert len(audited) == 1
    payload = audited[0]
    assert payload["operation"] == "cortex.reason"
    assert payload["redactions_applied"] == 1
    assert payload["classification"] == "CUI"
    # The question was screened on the way in, not only on the way out.
    assert any("Is the council governed?" in t for t in screened)


def test_council_query_requires_a_question():
    assert gap_handlers.handle_council_query({"question": "  "}) == {
        "error": "question is required"}


def test_council_query_reports_router_failure_as_error(monkeypatch):
    """Governed or not, a router blow-up still returns the handler's error shape."""

    def _boom():
        raise RuntimeError("chain orchestrator unavailable")

    monkeypatch.setattr(api, "_get_router", _boom)
    out = gap_handlers.handle_council_query({"question": "Should we ship?"})
    assert "chain orchestrator unavailable" in out["error"]
