# CUI // SP-CTI
"""RFI drafting adoption test (Cortex adoption wave 2).

rfi_workbench._call_llm must route RFI drafting through the GOVERNED cortex.complete
(CUI compliance artifact), preserving the role circuit-breaker + template fallback.
"""
from __future__ import annotations

import tools.cortex.api as cortex_api
import tools.govcon.rfi_workbench as wb
from tools.cortex.schemas import CortexResult


def test_call_llm_routes_through_governed_cortex(monkeypatch):
    calls = {}

    def _complete(prompt, function="", ctx=None, max_tokens=None, **kw):
        calls["function"] = function
        calls["agent_id"] = getattr(ctx, "agent_id", None)
        calls["domain"] = getattr(ctx, "domain", None)
        calls["classification"] = getattr(ctx, "classification", None)
        return CortexResult(text="## Approach\nGoverned RFI draft.")

    monkeypatch.setattr(cortex_api, "complete", _complete)
    monkeypatch.setattr(wb, "_role_blocked", set())

    out = wb._call_llm(
        "write the approach", "Technical Approach", 3,
        role_key="rfi_writer", llm_function="rfi_writer_drafting",
    )
    assert "Governed RFI draft" in out
    assert calls["function"] == "rfi_writer_drafting"
    assert calls["agent_id"] == "rfi:rfi_writer"
    assert calls["domain"] == "proposal"
    assert calls["classification"] == "CUI"


def test_call_llm_falls_back_on_cortex_failure(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("governance blocked")

    monkeypatch.setattr(cortex_api, "complete", _boom)
    monkeypatch.setattr(wb, "_role_blocked", set())
    out = wb._call_llm("p", "Sec", 1)
    assert "AI generation unavailable" in out  # _template_fallback


def test_call_llm_role_blocked_skips_cortex(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(cortex_api, "complete",
                        lambda *a, **kw: called.__setitem__("n", called["n"] + 1))
    monkeypatch.setattr(wb, "_role_blocked", {"rfi_writer"})
    out = wb._call_llm("p", "Sec", 1, role_key="rfi_writer")
    assert "AI generation unavailable" in out
    assert called["n"] == 0  # blocked role never reaches the LLM
