# [TEMPLATE: CUI // SP-CTI]
"""Unit tests for tools/security_canvas/llm_adapter.py (shx-llm-01).

The adapter is a thin, fail-soft seam onto LLMRouter. These tests mock the
router so no real provider/network is touched. Per the shim-aware-monkeypatch
lesson, ``tools.*`` and ``icdev.tools.*`` are distinct module objects, so we
patch ``LLMRouter`` on the EXACT module the adapter imports it from
(``tools.llm.router``) via importlib + setattr.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# The adapter under test (canonical shim namespace, matching its own imports).
adapter = importlib.import_module("tools.security_canvas.llm_adapter")
router_mod = importlib.import_module("tools.llm.router")


class _FakeResponse:
    def __init__(self, content):
        self.content = content


class _RecordingRouter:
    """Stand-in LLMRouter that records invoke() calls and returns canned text."""

    last_function = None
    last_request = None

    def __init__(self, *args, **kwargs):
        pass

    def invoke(self, function, request, **kwargs):
        type(self).last_function = function
        type(self).last_request = request
        return _FakeResponse("GENERATED TEXT")


class _RaisingRouter:
    def __init__(self, *args, **kwargs):
        pass

    def invoke(self, function, request, **kwargs):
        raise RuntimeError("provider exploded")


@pytest.fixture(autouse=True)
def _reset_router_cache():
    """Clear the adapter's process-wide cached router before and after each test."""
    adapter._router = None
    yield
    adapter._router = None


def _install_router(monkeypatch, cls):
    # Patch on the exact module object the adapter imports LLMRouter from.
    monkeypatch.setattr(router_mod, "LLMRouter", cls, raising=True)
    _RecordingRouter.last_function = None
    _RecordingRouter.last_request = None


def test_generate_returns_text_on_success(monkeypatch):
    _install_router(monkeypatch, _RecordingRouter)
    out = adapter.generate("what is my zero-trust posture?")
    assert out == "GENERATED TEXT"


def test_generate_returns_none_on_router_exception(monkeypatch):
    _install_router(monkeypatch, _RaisingRouter)
    out = adapter.generate("trigger a failure")
    assert out is None


def test_generate_passes_expected_function_name(monkeypatch):
    _install_router(monkeypatch, _RecordingRouter)
    adapter.generate("hello", system="be terse")
    assert _RecordingRouter.last_function == "security_canvas"
    # System prompt and user prompt threaded into the LLMRequest.
    req = _RecordingRouter.last_request
    assert req.system_prompt == "be terse"
    assert req.messages == [{"role": "user", "content": "hello"}]


def test_generate_returns_none_when_router_unavailable(monkeypatch):
    # Router construction itself fails -> adapter must degrade to None, not raise.
    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("cannot construct")

    _install_router(monkeypatch, _Boom)
    assert adapter.generate("x") is None


def test_no_hardcoded_model_ids_in_adapter_source():
    """Static guard: the adapter must contain no hardcoded model identifiers."""
    src = Path(adapter.__file__).read_text(encoding="utf-8").lower()
    forbidden = [
        "qwen3:",
        "qwen2",
        "llama3",
        "llama-3",
        "gpt-4",
        "gpt-3",
        "claude-3",
        "claude-opus",
        "claude-sonnet",
        "claude-haiku",
        "gemini-",
        "mistral",
        "codestral",
        "kimi",
        "deepseek",
        "o3-",
        ":latest",
    ]
    hits = [tok for tok in forbidden if tok in src]
    assert not hits, f"adapter source contains hardcoded model IDs: {hits}"
