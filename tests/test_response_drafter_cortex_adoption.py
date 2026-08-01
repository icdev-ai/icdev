# CUI // SP-CTI
"""Proposal drafting routes through the governed Cortex facade (adoption).

Mirrors the RFI-workbench adoption: response_drafter._try_llm_draft now calls
cortex_api.complete instead of a raw router.invoke, closing the RFI/proposal
inconsistency. The draft LLM is bridged to a fake so no real model runs.
"""
from __future__ import annotations


def test_try_llm_draft_routes_through_governed_cortex(monkeypatch):
    from tools.cortex import api as cortex_api
    from tools.cortex.schemas import CortexResult

    captured = {}

    def fake_complete(prompt, function=None, ctx=None, **kw):
        captured["prompt"] = prompt
        captured["function"] = function
        captured["domain"] = getattr(ctx, "domain", None)
        captured["classification"] = getattr(ctx, "classification", None)
        captured["agent_id"] = getattr(ctx, "agent_id", None)
        return CortexResult(text="DRAFTED VIA GOVERNED CORTEX")

    monkeypatch.setattr(cortex_api, "complete", fake_complete)

    from tools.govcon import response_drafter

    text, method = response_drafter._try_llm_draft(
        "The Contractor shall provide continuous monitoring.", [], [], "devsecops",
    )

    assert text == "DRAFTED VIA GOVERNED CORTEX"
    assert method == "two_tier_llm"  # label preserved for downstream
    # Routed through the proposal lens with the preserved routing function.
    assert captured["function"] == "proposal_drafting"
    assert captured["domain"] == "proposal"
    assert captured["classification"] == "CUI"
    assert captured["agent_id"] == "proposal-drafter"
    assert "shall provide continuous monitoring" in captured["prompt"]


def test_cortex_failure_falls_back_to_template(monkeypatch):
    from tools.cortex import api as cortex_api

    def boom(*a, **k):
        raise RuntimeError("cortex down")

    monkeypatch.setattr(cortex_api, "complete", boom)

    from tools.govcon import response_drafter

    # _try_llm_draft swallows the error and returns (None, None) so the caller
    # falls back to the deterministic template draft.
    text, method = response_drafter._try_llm_draft("shall X", [], [], "devsecops")
    assert text is None and method is None
