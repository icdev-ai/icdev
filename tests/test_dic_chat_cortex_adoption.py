# CUI // SP-CTI
"""DIC chat synthesis adoption test (Cortex adoption wave 2).

_llm_synthesize must route through the GOVERNED cortex.complete (function=
question_answering, domain=document, agent_id=dic-chat), not a raw router.invoke.
"""
from __future__ import annotations

import types

import tools.cortex.api as cortex_api
import tools.document_intelligence.blueprint as bp
from tools.cortex.schemas import CortexResult


def test_llm_synthesize_routes_through_governed_cortex(monkeypatch):
    calls = {}

    def _complete(prompt, function="", ctx=None, max_tokens=None, **kw):
        calls["function"] = function
        calls["agent_id"] = getattr(ctx, "agent_id", None)
        calls["domain"] = getattr(ctx, "domain", None)
        calls["prompt_has_question"] = "backups" in prompt
        return CortexResult(text="Backups run nightly [1].")

    monkeypatch.setattr(cortex_api, "complete", _complete)
    # Keep evidence assembly trivial so the test targets the routing, not helpers.
    monkeypatch.setattr(bp, "_build_sources", lambda results: [])
    monkeypatch.setattr(bp, "_extract_passage", lambda content, q, max_chars=1000: content)

    results = [types.SimpleNamespace(content="Backups are performed nightly.", doc_id="d1", page=1, doc_title="Ops SOP")]
    out = bp._llm_synthesize("How often are backups run?", results)

    assert out == "Backups run nightly [1]."
    assert calls["function"] == "question_answering"
    assert calls["agent_id"] == "dic-chat"
    assert calls["domain"] == "document"
    assert calls["prompt_has_question"] is True


def test_llm_synthesize_returns_none_on_cortex_failure(monkeypatch):
    def _boom(*a, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(cortex_api, "complete", _boom)
    monkeypatch.setattr(bp, "_build_sources", lambda results: [])
    monkeypatch.setattr(bp, "_extract_passage", lambda content, q, max_chars=1000: content)

    results = [types.SimpleNamespace(content="x", doc_id="d1", page=1, doc_title="D")]
    # Degrades to None so the chat route falls back to grounded RAG+KG evidence.
    assert bp._llm_synthesize("q", results) is None


def test_llm_synthesize_returns_none_on_empty_completion(monkeypatch):
    monkeypatch.setattr(cortex_api, "complete", lambda *a, **kw: CortexResult(text="   "))
    monkeypatch.setattr(bp, "_build_sources", lambda results: [])
    monkeypatch.setattr(bp, "_extract_passage", lambda content, q, max_chars=1000: content)
    results = [types.SimpleNamespace(content="x", doc_id="d1", page=1, doc_title="D")]
    assert bp._llm_synthesize("q", results) is None
