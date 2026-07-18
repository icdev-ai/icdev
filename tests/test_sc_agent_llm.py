# [TEMPLATE: CUI // SP-CTI]
"""Unit tests for llm_identify_threats in tools/security_canvas/agent.py (shx-llm-02).

agent.py was migrated off a hardcoded Ollama/qwen3 urllib call onto the governed
Security Canvas LLM adapter (``tools.security_canvas.llm_adapter.generate``).

Per the shim-aware-monkeypatch lesson, ``tools.*`` and ``icdev.tools.*`` are
distinct module objects. agent.py does ``from tools.security_canvas import
llm_adapter`` inside the function, so it resolves ``generate`` off the
``tools.security_canvas.llm_adapter`` module object at call time. We patch
``generate`` on that EXACT module via importlib + monkeypatch.setattr.
"""

from __future__ import annotations

import importlib
from pathlib import Path

# Canonical shim namespace, matching agent.py's own imports.
agent = importlib.import_module("tools.security_canvas.agent")
adapter = importlib.import_module("tools.security_canvas.llm_adapter")


_GRAPH = {
    "nodes": [
        {"id": "web", "label": "Web App", "type": "web_app"},
        {"id": "db", "label": "Database", "type": "database"},
    ],
    "edges": [
        {
            "source": "web",
            "target": "db",
            "protocol": "tcp",
            "encrypted": False,
            "authenticated": False,
        }
    ],
    "boundaries": [
        {"id": "b1", "label": "DMZ", "boundary_type": "network", "il_level": "IL4"}
    ],
}


def test_llm_path_used_and_parsed(monkeypatch):
    """(a) When the adapter returns valid text, the LLM path is taken and parsed."""
    captured = {}

    def _fake_generate(prompt, purpose="security_canvas", system=None, **opts):
        captured["prompt"] = prompt
        captured["purpose"] = purpose
        captured["opts"] = opts
        return (
            "<think>reasoning that must be stripped</think>\n"
            'Here you go:\n```json\n{"threats": [{"category": "S", '
            '"title": "Spoofing", "description": "Unauthenticated flow.", '
            '"affected": "Web App", "nist_control": "IA-2"}]}\n```'
        )

    monkeypatch.setattr(adapter, "generate", _fake_generate)

    result = agent.llm_identify_threats(_GRAPH)

    assert result["source"] == "llm"
    assert result["error"] is None
    assert result["total_threats"] == 1
    assert result["threats"][0]["category"] == "S"
    assert result["threats"][0]["title"] == "Spoofing"
    # The governed adapter was invoked with the STRIDE prompt + routing purpose.
    assert "STRIDE" in captured["prompt"]
    assert captured["purpose"] == "security_canvas"
    # Generation options forwarded (config-driven; no hardcoded model here).
    assert captured["opts"].get("max_tokens") == 1024
    assert captured["opts"].get("temperature") == 0.3


def test_none_triggers_deterministic_fallback(monkeypatch):
    """(b) When the adapter returns None, the deterministic STRIDE fallback runs."""

    def _fake_generate(prompt, purpose="security_canvas", system=None, **opts):
        return None

    monkeypatch.setattr(adapter, "generate", _fake_generate)

    result = agent.llm_identify_threats(_GRAPH)

    # Same result structure as the pre-migration deterministic fallback.
    assert result["source"] == "deterministic"
    assert result["model"] is None
    assert "deterministic" in (result["error"] or "").lower()
    assert isinstance(result["threats"], list)
    assert result["total_threats"] == len(result["threats"])


def test_no_hardcoded_ollama_literals():
    """(c) Static: no qwen3/11434 (or urllib Ollama plumbing) remains in agent.py."""
    src = Path(agent.__file__).read_text(encoding="utf-8")
    assert "qwen3" not in src
    assert "11434" not in src
    assert "urllib" not in src
