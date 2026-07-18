# CUI // SP-CTI
"""nav-llm-01 — regression tests for the dead LLMRouter API fix.

Background: several shipped call sites invoked ``LLMRouter().complete(...)`` — a
method that does not exist on ``LLMRouter`` (which exposes only
``invoke(function_name, LLMRequest)``). Because each call sat inside a
``try/except`` that degraded to a deterministic fallback, the ``AttributeError``
was swallowed and the LLM path was permanently, silently dead.

These tests prove the LLM path now actually executes (the mocked ``invoke``
result is consumed, not the fallback) AND that the deterministic fallback still
fires when ``invoke`` raises. A guard test locks in that ``LLMRouter`` has no
``complete`` method and that the fixed modules no longer contain ``router.complete(``.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class _FakeResponse:
    """Minimal stand-in for LLMResponse — only ``.content`` is consumed."""

    def __init__(self, content: str = "") -> None:
        self.content = content


def _install_fake_invoke(monkeypatch, *, content: str = "", raises: bool = False) -> list:
    """Stub ``LLMRouter.invoke`` on the class the fixed modules import.

    The fixed modules do ``from tools.llm.router import LLMRouter; LLMRouter()``.
    Because the ``tools.*`` shim makes ``import tools.llm.router`` and
    ``from tools.llm.router import ...`` resolve *different* module objects,
    patching the module attribute is unreliable. Instead we patch ``invoke`` on
    the exact class object that the identical ``from``-import yields — the real
    ``__init__`` still runs, only the network/DB chain is bypassed.

    Returns a list that records every ``(function, request)`` invocation so a
    test can assert the LLM path was actually taken.
    """
    from tools.llm.router import LLMRouter

    calls: list = []

    def _fake_invoke(self, function, request, **kwargs):
        calls.append((function, request))
        if raises:
            raise RuntimeError("simulated LLM unavailable")
        return _FakeResponse(content)

    monkeypatch.setattr(LLMRouter, "invoke", _fake_invoke)
    return calls


# ---------------------------------------------------------------------------
# report_generator — LLM prose synthesis
# ---------------------------------------------------------------------------
def _report_fixtures():
    section = SimpleNamespace(
        name="Executive Summary",
        key="exec_summary",
        description="High-level overview.",
        max_words=200,
    )
    chunk = SimpleNamespace(
        content="Legacy system X handles payroll.",
        section_heading="Overview",
        filename="doc1.pdf",
        wf_document_template_id="tmpl-1",
    )
    citations = [{"source_doc": "doc1.pdf", "section": "Overview", "excerpt": "payroll"}]
    style_rules = {"tone": "formal", "max_section_words": 800, "citation_style": "numeric"}
    return section, [chunk], citations, style_rules


def test_report_generator_consumes_llm_path(monkeypatch):
    mod = importlib.import_module("tools.workflow_hitl.report_generator")
    calls = _install_fake_invoke(monkeypatch, content="  LLM-authored section body [1].  ")

    section, chunks, citations, style_rules = _report_fixtures()
    out = mod._llm_synthesize_section(section, chunks, citations, style_rules, "modernization")

    assert out == "LLM-authored section body [1]."
    assert calls, "router.invoke was never called — LLM path is dead"
    fn, req = calls[0]
    assert fn == "report_generation"
    # The prompt must have been carried as a user message, not lost.
    assert req.messages and req.messages[0]["role"] == "user"


def test_report_generator_falls_back_when_invoke_raises(monkeypatch):
    mod = importlib.import_module("tools.workflow_hitl.report_generator")
    _install_fake_invoke(monkeypatch, raises=True)

    section, chunks, citations, style_rules = _report_fixtures()
    out = mod._llm_synthesize_section(section, chunks, citations, style_rules, "modernization")

    # Deterministic assembly fallback is used — it embeds the section name.
    assert "Executive Summary" in out
    assert "payroll" in out.lower()


# ---------------------------------------------------------------------------
# mop_generator — MOP step generation
# ---------------------------------------------------------------------------
def test_mop_generator_consumes_llm_path(monkeypatch):
    mod = importlib.import_module("tools.noc_canvas.mop_generator")
    steps_json = (
        '[{"step": 1, "action": "Drain node", "rollback": "Undrain", '
        '"timeout_min": 5, "verification": "ping ok"}]'
    )
    calls = _install_fake_invoke(monkeypatch, content="Here are the steps:\n" + steps_json)

    result = mod.generate_mop({"title": "Upgrade core switch", "risk_level": "high"})

    assert result["generated_by"] == "ai"
    assert result["steps"][0]["action"] == "Drain node"
    assert calls and calls[0][0] == "narrative_generation"


def test_mop_generator_falls_back_when_invoke_raises(monkeypatch):
    mod = importlib.import_module("tools.noc_canvas.mop_generator")
    _install_fake_invoke(monkeypatch, raises=True)

    result = mod.generate_mop({"title": "Upgrade core switch"})

    assert result["generated_by"] == "ai_template"
    assert result["steps"], "template fallback must still yield steps"


# ---------------------------------------------------------------------------
# rag_server._grade_chunk — one RAG site
# ---------------------------------------------------------------------------
def test_rag_grade_chunk_consumes_llm_path(monkeypatch):
    mod = importlib.import_module("tools.mcp.rag_server")
    calls = _install_fake_invoke(monkeypatch, content='{"score": 0.9, "reason": "directly answers"}')

    result = mod._grade_chunk("what is X?", "X is a payroll system.")

    assert result["method"] == "llm"
    assert result["score"] == pytest.approx(0.9)
    assert calls and calls[0][0] == "rag_evaluate"


def test_rag_grade_chunk_falls_back_when_invoke_raises(monkeypatch):
    mod = importlib.import_module("tools.mcp.rag_server")
    _install_fake_invoke(monkeypatch, raises=True)

    result = mod._grade_chunk("payroll system", "X is a payroll system")

    # Heuristic keyword-overlap fallback still produces a score.
    assert result["method"] == "heuristic"
    assert 0.0 <= result["score"] <= 1.0


# ---------------------------------------------------------------------------
# Guard: the dead API must stay dead, and no fixed module may call it.
# ---------------------------------------------------------------------------
_FIXED_MODULES = [
    "tools.workflow_hitl.report_generator",
    "tools.strategos.predictive_intel_engine",
    "tools.migration_intelligence.goal_manager",
    "tools.noc_canvas.mop_generator",
    "tools.rag.entitlement_rag",
    "tools.mcp.rag_server",
    "tools.conflict_mesh.ml_pattern_engine",
    "tools.genesis.reflexes.strategos.red_cell",
    "tools.ai_augmentation.implementations.llm_http_auth",
]


def test_llmrouter_has_no_complete_method():
    from tools.llm.router import LLMRouter

    assert not hasattr(LLMRouter, "complete"), (
        "LLMRouter grew a .complete() method — update nav-llm-01 assumptions"
    )
    assert not hasattr(LLMRouter, "chat")


@pytest.mark.parametrize("module_path", _FIXED_MODULES)
def test_fixed_module_source_has_no_router_complete(module_path):
    mod = importlib.import_module(module_path)
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "router.complete(" not in src, f"{module_path} still calls router.complete("
    assert "LLMRouter().complete(" not in src, f"{module_path} still calls LLMRouter().complete("
    assert "_llm.complete(" not in src, f"{module_path} still calls self._llm.complete("
    assert ".complete(request)" not in src, f"{module_path} still calls .complete(request)"
