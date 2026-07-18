# CUI // SP-CTI
"""Regression tests for dcpr-fix-01: ai_mapper LLM import + LLMRequest API.

Before this fix, ``generate_transforms`` imported ``LLMRouter``/``LLMRequest``
from the wrong module (``icdev.tools.llm.router``) and constructed
``LLMRequest(prompt=..., temperature=...)`` — a field that does not exist on
the real dataclass. Both defects made the LLM path dead-on-arrival: every call
silently fell back to the template.

These tests pin the *real* contract:
  * ``generate_transforms`` calls ``router.invoke("code_generation", request)``
    with a well-formed ``LLMRequest`` — ``messages`` populated, NO ``prompt``
    attribute.
  * The template fallback still fires (and returns something) when the router
    raises.

The repo has a ``tools.*`` vs ``icdev.tools.*`` shim; ai_mapper (the ``tools/``
copy) imports ``LLMRouter`` from ``tools.llm.router`` at call time, so we patch
that module object's attribute via importlib + monkeypatch.
"""
from __future__ import annotations

import importlib

from tools.data_canvas import ai_mapper
from tools.llm.provider import LLMRequest


_PAIRS = [
    {"source_field": "cust_id", "target_field": "customer_id",
     "source_type": "integer", "target_type": "integer"},
    {"source_field": "email_addr", "target_field": "email",
     "source_type": "string", "target_type": "string"},
]


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeProvider:
    """Stand-in truthy provider object."""


def _make_fake_router(captured: list, *, invoke_impl=None):
    """Build a FakeRouter class whose invoke records the request it receives."""

    class _FakeRouter:
        def __init__(self, *a, **kw) -> None:
            pass

        def get_provider_for_function(self, function):
            return _FakeProvider(), "fake-model-id", {}

        def invoke(self, function, request, **kwargs):
            captured.append((function, request, kwargs))
            if invoke_impl is not None:
                return invoke_impl(function, request)
            return _FakeResponse("def transform(source):\n    return {}\n")

    return _FakeRouter


def _patch_router(monkeypatch, fake_router):
    # Patch the name as imported by ai_mapper: `from tools.llm.router import LLMRouter`
    router_mod = importlib.import_module("tools.llm.router")
    monkeypatch.setattr(router_mod, "LLMRouter", fake_router, raising=True)


def test_generate_transforms_invokes_router_with_wellformed_request(monkeypatch):
    captured: list = []
    _patch_router(monkeypatch, _make_fake_router(captured))

    text, model_used = ai_mapper.generate_transforms(
        "sess-1", _PAIRS, "python", classification="CUI"
    )

    # LLM output is used on success.
    assert "def transform" in text
    assert model_used == "fake-model-id"

    # router.invoke was called exactly once with the code_generation function.
    assert len(captured) == 1
    function, request, kwargs = captured[0]
    assert function == "code_generation"

    # The request is a real LLMRequest built with the correct API.
    assert isinstance(request, LLMRequest)
    # messages present and non-empty, carrying the prompt content.
    assert request.messages, "LLMRequest.messages must be populated"
    assert request.messages[0]["role"] == "user"
    assert "source_field" in request.messages[0]["content"]
    # The dead API used prompt= / temperature-only; real dataclass has no `prompt`.
    assert not hasattr(request, "prompt"), "LLMRequest must not carry a `prompt` field"
    # system_prompt is set (real field), not smuggled as a positional prompt.
    assert isinstance(request.system_prompt, str) and request.system_prompt


def test_generate_transforms_falls_back_to_template_when_router_raises(monkeypatch):
    captured: list = []

    def _boom(function, request):
        raise RuntimeError("provider exploded")

    _patch_router(monkeypatch, _make_fake_router(captured, invoke_impl=_boom))

    text, model_used = ai_mapper.generate_transforms(
        "sess-2", _PAIRS, "python", classification="CUI"
    )

    # invoke was attempted (proves the path is reachable, not dead-on-import)...
    assert len(captured) == 1
    # ...and the template safety net still returns a usable artifact.
    assert model_used == "template"
    assert "def transform" in text


def test_generate_transforms_sql_is_deterministic_no_llm(monkeypatch):
    # SQL never touches the LLM — guard that the router is not even constructed.
    captured: list = []
    _patch_router(monkeypatch, _make_fake_router(captured))

    text, model_used = ai_mapper.generate_transforms("sess-3", _PAIRS, "sql")

    assert model_used == "template"
    assert "SELECT" in text
    assert captured == []
