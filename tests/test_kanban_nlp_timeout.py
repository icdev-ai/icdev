# CUI // SP-CTI
"""Tests for _nlp_extract_timeout_hint — NLP-backed timeout extraction.

Modernizes the regex_user_input pattern in _get_task_timeout so natural
language descriptions like "allow 25 minutes for this heavy task" are
understood in addition to the structured timeout_hint:NNN directive.
"""
from __future__ import annotations

import types
import sys

import pytest


# ---------------------------------------------------------------------------
# Helpers: patch LLMRouter so tests never hit a real LLM
# ---------------------------------------------------------------------------

class _FakeLLMResponse:
    def __init__(self, content: str):
        self.content = content


class _FakeRouter:
    def __init__(self, response_content: str):
        self._content = response_content

    def invoke(self, *args, **kwargs):
        return _FakeLLMResponse(self._content)


def _patch_router(monkeypatch, response_content: str):
    """Inject a fake LLMRouter into tools.llm.router."""
    fake_mod = types.ModuleType("tools.llm.router")
    fake_mod.LLMRouter = lambda: _FakeRouter(response_content)
    monkeypatch.setitem(sys.modules, "tools.llm.router", fake_mod)


def _patch_router_unavailable(monkeypatch):
    """Make LLMRouter import raise ImportError."""
    monkeypatch.setitem(sys.modules, "tools.llm.router", None)  # None means import error


# ---------------------------------------------------------------------------
# Import target (after sys.modules manipulation)
# ---------------------------------------------------------------------------

def _import_fn():
    # Re-import fresh each test to get un-cached state.
    import importlib
    import tools.genesis.reflexes.kanban as _m
    importlib.reload(_m)
    return _m._nlp_extract_timeout_hint


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_extracts_minutes_from_natural_language(monkeypatch):
    _patch_router(monkeypatch, '{"timeout_seconds": 1500}')
    fn = _import_fn()
    result = fn("This is a heavy computation task; allow 25 minutes for it to complete.")
    assert result == 1500


def test_returns_none_when_llm_finds_no_timeout(monkeypatch):
    _patch_router(monkeypatch, '{"timeout_seconds": null}')
    fn = _import_fn()
    result = fn("Add a unit test for the login form validation.")
    assert result is None


def test_caps_at_3600_seconds(monkeypatch):
    _patch_router(monkeypatch, '{"timeout_seconds": 7200}')
    fn = _import_fn()
    result = fn("This super-heavy migration will need 2 hours minimum.")
    assert result == 3600


def test_enforces_minimum_60_seconds(monkeypatch):
    _patch_router(monkeypatch, '{"timeout_seconds": 10}')
    fn = _import_fn()
    result = fn("Quick task, maybe 10 seconds should be fine.")
    assert result == 60


def test_short_description_skipped_without_llm_call(monkeypatch):
    """Descriptions shorter than 20 chars bypass the LLM entirely."""
    called = []

    class _SpyRouter:
        def invoke(self, *a, **kw):
            called.append(True)
            return _FakeLLMResponse('{"timeout_seconds": 999}')

    fake_mod = types.ModuleType("tools.llm.router")
    fake_mod.LLMRouter = lambda: _SpyRouter()
    monkeypatch.setitem(sys.modules, "tools.llm.router", fake_mod)

    fn = _import_fn()
    result = fn("Short desc")
    assert result is None
    assert not called, "LLM should not be called for short descriptions"


def test_llm_unavailable_returns_none(monkeypatch):
    """If LLMRouter import fails, function must return None (not raise)."""
    # Remove the module from sys.modules so the import inside the fn fails
    monkeypatch.delitem(sys.modules, "tools.llm.router", raising=False)
    # Also block the tools.llm package to force ImportError
    import importlib
    import tools.genesis.reflexes.kanban as _m
    importlib.reload(_m)
    fn = _m._nlp_extract_timeout_hint

    # Patch to raise on import
    original = sys.modules.get("tools.llm.router")
    sys.modules["tools.llm.router"] = None  # causes ImportError on 'from tools.llm.router import …'
    try:
        result = fn("This computation will take approximately 30 minutes to finish.")
        assert result is None
    finally:
        if original is not None:
            sys.modules["tools.llm.router"] = original
        else:
            sys.modules.pop("tools.llm.router", None)


def test_malformed_json_returns_none(monkeypatch):
    _patch_router(monkeypatch, "not valid json at all")
    fn = _import_fn()
    result = fn("This heavy analysis task should be given about 20 minutes.")
    assert result is None


def test_empty_description_returns_none(monkeypatch):
    _patch_router(monkeypatch, '{"timeout_seconds": 600}')
    fn = _import_fn()
    assert fn("") is None
    assert fn(None) is None
