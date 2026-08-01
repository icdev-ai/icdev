"""Module-surface tests for tools/document_intelligence/tech_writing_assist.py.

Covers the public API (validate_standards_references, research_and_draft,
generate_diagram_syntax) and result dataclasses. Standards-validation edge
cases and CoD gating live in tests/test_tw_standards_validation.py.
"""
import importlib

import pytest

twa = importlib.import_module("tools.document_intelligence.tech_writing_assist")


@pytest.fixture(autouse=True)
def _fresh_whitelist_cache():
    twa._whitelist_cache = None
    yield
    twa._whitelist_cache = None


class _Resp:
    def __init__(self, content):
        self.content = content


class _Req:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _offline(monkeypatch, router):
    monkeypatch.setattr(twa, "LLMRouter", router)
    monkeypatch.setattr(twa, "LLMRequest", _Req)
    monkeypatch.setattr(twa, "RAGRetriever", None)
    monkeypatch.setattr(twa, "kg_retrieve", None)
    monkeypatch.setattr(twa, "is_airgap", lambda **kw: True)
    # The single-shot draft now routes through the GOVERNED cortex.complete facade
    # (adoption pilot). Bridge it to the fake router so the existing _Router.invoke()
    # responses still drive draft_content — proving the call path without a real LLM.
    import tools.cortex.api as cortex_api
    from tools.cortex.schemas import CortexResult

    def _fake_complete(prompt, function="", ctx=None, system_prompt="", **kw):
        resp = router().invoke(function, _Req(content=prompt))  # type: ignore[misc]
        return CortexResult(text=(resp.content or ""))

    monkeypatch.setattr(cortex_api, "complete", _fake_complete)


# ── Result dataclasses ────────────────────────────────────────────────────────

def test_research_result_defaults():
    r = twa.ResearchResult()
    assert r.draft_content == ""
    assert r.rag_chunks == [] and r.kg_entities == [] and r.web_sources == []
    assert r.warnings == [] and r.error == ""


def test_diagram_result_defaults():
    d = twa.DiagramResult()
    assert d.diagram_type == "mermaid"
    assert d.syntax == "" and d.error == ""


# ── validate_standards_references ─────────────────────────────────────────────

def test_validate_returns_list_of_strings():
    warnings = twa.validate_standards_references("## References\n- NIST SP 800-999\n")
    assert isinstance(warnings, list)
    assert all(isinstance(w, str) for w in warnings)


def test_validate_never_raises_on_odd_input():
    assert twa.validate_standards_references("") == []
    assert isinstance(twa.validate_standards_references("no citations here"), list)


# ── research_and_draft ────────────────────────────────────────────────────────

def test_research_and_draft_no_query_no_context_sets_error(monkeypatch):
    class _Router:
        def invoke(self, fn, req):
            raise AssertionError("LLM must not be invoked with no query/context")

    _offline(monkeypatch, _Router)
    result = twa.research_and_draft("", "Design", template_type="SOP")
    assert result.error == "No query and no context retrieved."
    assert result.draft_content == ""


def test_research_and_draft_llm_unavailable_sets_error(monkeypatch):
    _offline(monkeypatch, None)
    monkeypatch.setattr(twa, "LLMRequest", None)
    result = twa.research_and_draft("backup cadence", "Steps", template_type="SOP")
    assert result.error == "LLM not available."


def test_research_and_draft_single_shot_happy_path(monkeypatch):
    class _Router:
        def invoke(self, fn, req):
            assert fn == "tech_writing_draft"
            return _Resp("  drafted content  ")

    _offline(monkeypatch, _Router)
    result = twa.research_and_draft("backup cadence", "Steps", template_type="SOP")
    assert result.draft_content == "drafted content"
    assert result.error == ""


def test_research_and_draft_runs_standards_check_on_draft(monkeypatch):
    class _Router:
        def invoke(self, fn, req):
            return _Resp("## References\n- NIST SP 800-999\n")

    _offline(monkeypatch, _Router)
    result = twa.research_and_draft("controls", "References", template_type="STANDARD_GUIDE")
    assert any("SP 800-999" in w for w in result.warnings)


def test_research_and_draft_llm_exception_surfaces_in_error(monkeypatch):
    class _Router:
        def invoke(self, fn, req):
            raise RuntimeError("provider down")

    _offline(monkeypatch, _Router)
    result = twa.research_and_draft("controls", "Design", template_type="SOP")
    assert "provider down" in result.error


def test_draft_routes_through_governed_cortex_complete(monkeypatch):
    # Adoption pilot proof: the draft goes through cortex.complete (governed),
    # is attributed to the dic-tech-writer agent, uses the tech_writing_draft
    # routing function, and surfaces any governance redaction count to WriteGuard.
    monkeypatch.setattr(twa, "LLMRouter", object)  # non-None -> passes LLM guard
    monkeypatch.setattr(twa, "LLMRequest", _Req)
    monkeypatch.setattr(twa, "RAGRetriever", None)
    monkeypatch.setattr(twa, "kg_retrieve", None)
    monkeypatch.setattr(twa, "is_airgap", lambda **kw: True)

    import tools.cortex.api as cortex_api
    from tools.cortex.schemas import CortexResult, GovernanceReport

    calls = {}

    def _complete(prompt, function="", ctx=None, system_prompt="", **kw):
        calls["function"] = function
        calls["agent_id"] = getattr(ctx, "agent_id", None)
        calls["domain"] = getattr(ctx, "domain", None)
        gr = GovernanceReport()
        gr.redactions_applied = 2
        return CortexResult(text="governed draft", governance=gr)

    monkeypatch.setattr(cortex_api, "complete", _complete)

    result = twa.research_and_draft("backup cadence", "Steps", template_type="SOP")
    assert result.draft_content == "governed draft"
    assert calls["function"] == "tech_writing_draft"
    assert calls["agent_id"] == "dic-tech-writer"
    assert calls["domain"] == "document"
    assert any("masked 2 sensitive" in w for w in result.warnings)


# ── generate_diagram_syntax ───────────────────────────────────────────────────

def test_generate_diagram_strips_markdown_fences(monkeypatch):
    class _Router:
        def invoke(self, fn, req):
            assert fn == "diagram_generation"
            return _Resp("```mermaid\nflowchart TD\n  A --> B\n```")

    monkeypatch.setattr(twa, "LLMRouter", _Router)
    monkeypatch.setattr(twa, "LLMRequest", _Req)
    result = twa.generate_diagram_syntax("auth flow", template_type="ARCH_NETWORK")
    assert result.syntax == "flowchart TD\n  A --> B"
    assert result.error == ""


def test_generate_diagram_llm_unavailable_sets_error(monkeypatch):
    monkeypatch.setattr(twa, "LLMRouter", None)
    monkeypatch.setattr(twa, "LLMRequest", None)
    result = twa.generate_diagram_syntax("auth flow")
    assert result.error == "LLM not available."
    assert result.syntax == ""


def test_generate_diagram_llm_exception_never_raises(monkeypatch):
    class _Router:
        def invoke(self, fn, req):
            raise RuntimeError("timeout")

    monkeypatch.setattr(twa, "LLMRouter", _Router)
    monkeypatch.setattr(twa, "LLMRequest", _Req)
    result = twa.generate_diagram_syntax("auth flow")
    assert "timeout" in result.error
