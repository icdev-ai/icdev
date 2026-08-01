# CUI // SP-CTI
"""cortex.extract JSON-Schema conformance tests (Cortex analysis item 4).

extract previously returned whatever JSON the model emitted with no conformance
check. It now validates against the caller's schema and DEGRADES (schema_valid=
False + grounded=False + schema_error) rather than refusing.
"""
from __future__ import annotations

import importlib

import pytest

from tools.cortex import api as cortex_api
from tools.cortex.schemas import CortexContext
from tools.llm.provider import LLMResponse

_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "year": {"type": "integer"}},
    "required": ["name", "year"],
}


class _FakeRouter:
    def __init__(self, response):
        self.response = response

    def invoke(self, function, request, **kw):
        return self.response


@pytest.fixture
def install_router(monkeypatch):
    def _install(resp):
        llm = importlib.import_module("tools.llm")
        monkeypatch.setattr(llm, "get_router", lambda config_path=None: _FakeRouter(resp))

    return _install


def _resp(**ov):
    d = dict(content="", provider="fake", model_id="m", cost_usd=0.0,
             duration_ms=1, input_tokens=1, output_tokens=1)
    d.update(ov)
    return LLMResponse(**d)


def test_conforming_payload_is_valid(install_router):
    install_router(_resp(structured_output={"name": "ICDEV", "year": 2026}))
    r = cortex_api.extract("txt", _SCHEMA, ctx=CortexContext(tenant_id="t1"))
    assert r.metadata.get("schema_valid") is True
    assert "schema_error" not in r.metadata


def test_missing_required_field_degrades(install_router):
    install_router(_resp(structured_output={"name": "ICDEV"}))  # no 'year'
    r = cortex_api.extract("txt", _SCHEMA, ctx=CortexContext(tenant_id="t1"))
    assert r.metadata.get("schema_valid") is False
    assert r.grounded is False
    assert r.metadata.get("schema_error")


def test_wrong_type_degrades(install_router):
    install_router(_resp(structured_output={"name": "ICDEV", "year": "MMXXVI"}))
    r = cortex_api.extract("txt", _SCHEMA, ctx=CortexContext(tenant_id="t1"))
    assert r.metadata.get("schema_valid") is False
    assert r.grounded is False


def test_unparseable_payload_degrades(install_router):
    install_router(_resp(structured_output=None, content="not json at all"))
    r = cortex_api.extract("txt", _SCHEMA, ctx=CortexContext(tenant_id="t1"))
    assert r.metadata.get("schema_valid") is False
    assert r.grounded is False


def test_no_schema_is_permissive(install_router):
    install_router(_resp(structured_output={"anything": True}))
    r = cortex_api.extract("txt", {}, ctx=CortexContext(tenant_id="t1"))
    assert r.metadata.get("schema_valid") is True
