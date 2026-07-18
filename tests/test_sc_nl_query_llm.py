# [TEMPLATE: CUI // SP-CTI]
"""Unit tests for the LLM fallback path in tools/security_canvas/nl_query.py (shx-llm-03).

nl_query.py used to POST to Ollama directly with a hardcoded model ID. It now
routes its open-ended fallback through ``tools.security_canvas.llm_adapter.generate``
(governed LLMRouter, function ``security_canvas``). These tests mock the adapter
so no real provider/network is touched.

Per the shim-aware-monkeypatch lesson, ``tools.*`` and ``icdev.tools.*`` are
distinct module objects. nl_query does ``from tools.security_canvas import
llm_adapter`` then calls ``llm_adapter.generate(...)``, so we patch ``generate``
on the EXACT ``tools.security_canvas.llm_adapter`` module object via
importlib + setattr.
"""

from __future__ import annotations

import importlib
from pathlib import Path

# Canonical shim namespace, matching nl_query's own imports.
nl_query = importlib.import_module("tools.security_canvas.nl_query")
llm_adapter = importlib.import_module("tools.security_canvas.llm_adapter")


class _FakeGraph:
    """Minimal stand-in for SDCComplianceGraph — only compact_context is used."""

    def compact_context(self, max_nodes: int = 80) -> str:
        return "## STRIDE nodes (1):\n  - STRIDE:Spoofing"


def test_llm_query_returns_text_on_adapter_success(monkeypatch):
    calls = {}

    def _fake_generate(prompt, purpose="security_canvas", system=None, **opts):
        calls["prompt"] = prompt
        calls["purpose"] = purpose
        calls["system"] = system
        calls["opts"] = opts
        return "  AC-2 addresses spoofing.  "

    monkeypatch.setattr(llm_adapter, "generate", _fake_generate, raising=True)

    result = nl_query._llm_query("How is spoofing handled?", _FakeGraph())

    assert result["intent"] == "general"
    assert result["llm_used"] is True
    # Answer is stripped and passed through verbatim.
    assert result["answer"] == "AC-2 addresses spoofing."
    # Governed adapter was called with the graph context in the prompt.
    assert "GRAPH CONTEXT" in calls["prompt"]
    assert "STRIDE:Spoofing" in calls["prompt"]
    assert calls["purpose"] == "nl_query"


def test_llm_query_falls_back_when_adapter_returns_none(monkeypatch):
    monkeypatch.setattr(
        llm_adapter,
        "generate",
        lambda *a, **k: None,
        raising=True,
    )

    result = nl_query._llm_query("An unclassifiable open question", _FakeGraph())

    assert result["intent"] == "general"
    assert result["llm_used"] is False
    assert "LLM unavailable" in result["answer"]
    # Deterministic guidance is preserved so the user still gets actionable help.
    assert "STRIDE codes" in result["answer"]
    # No model ID leaks into the fallback message.
    assert "model" not in result


def test_no_direct_ollama_literals_in_nl_query_source():
    """Static guard: nl_query must contain no direct-Ollama or hardcoded model literals."""
    src = Path(nl_query.__file__).read_text(encoding="utf-8").lower()
    forbidden = [
        "11434",
        "ollama",
        "/api/chat",
        "qwen",
        "llama3",
        "llama-3",
        "gpt-4",
        "gpt-3",
        "claude-3",
        "claude-opus",
        "claude-sonnet",
        "gemini-",
        "mistral",
        "codestral",
        "kimi",
        "deepseek",
        ":latest",
    ]
    hits = [tok for tok in forbidden if tok in src]
    assert not hits, f"nl_query source contains direct-Ollama / hardcoded model literals: {hits}"
