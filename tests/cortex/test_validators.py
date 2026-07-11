# CUI // SP-CTI
"""Unit tests for tools/cortex/validators.py (ctx-expose-02).

The validators are the single request-validation source shared by the REST v1
blueprint and the MCP tool family. These tests pin the coercion/bounds/enum
rules and the rule that identity fields are never read (only ``domain`` is
surfaced from the payload).
"""
from __future__ import annotations

import pytest

from tools.cortex import validators as v


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------
def test_validate_search_defaults():
    out = v.validate_search({"query": "hello"})
    assert out == {"query": "hello", "top_k": 5, "strategy": "auto"}


def test_validate_search_requires_query():
    with pytest.raises(v.CortexValidationError):
        v.validate_search({"top_k": 3})


def test_validate_search_rejects_unknown_strategy():
    with pytest.raises(v.CortexValidationError):
        v.validate_search({"query": "x", "strategy": "telepathy"})


@pytest.mark.parametrize("bad", [0, 51, -1, "5", True])
def test_validate_search_top_k_bounds(bad):
    with pytest.raises(v.CortexValidationError):
        v.validate_search({"query": "x", "top_k": bad})


def test_validate_search_strategy_case_insensitive():
    assert v.validate_search({"query": "x", "strategy": "RAG"})["strategy"] == "rag"


# ---------------------------------------------------------------------------
# ask
# ---------------------------------------------------------------------------
def test_validate_ask_defaults():
    out = v.validate_ask({"question": "how many?"})
    assert out["mode"] == "auto"
    assert out["canvas"] is None
    assert out["collections"] is None
    assert out["summarize"] is False


def test_validate_ask_bad_mode():
    with pytest.raises(v.CortexValidationError):
        v.validate_ask({"question": "x", "mode": "psychic"})


def test_validate_ask_collections_must_be_strings():
    with pytest.raises(v.CortexValidationError):
        v.validate_ask({"question": "x", "collections": [1, 2]})


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------
def test_validate_complete_minimal():
    out = v.validate_complete({"prompt": "draft"})
    assert out == {"prompt": "draft", "system_prompt": ""}


def test_validate_complete_optional_numerics():
    out = v.validate_complete({"prompt": "p", "max_tokens": 100, "temperature": 0.5})
    assert out["max_tokens"] == 100
    assert out["temperature"] == 0.5


def test_validate_complete_temperature_out_of_range():
    with pytest.raises(v.CortexValidationError):
        v.validate_complete({"prompt": "p", "temperature": 5})


def test_validate_complete_temperature_bool_rejected():
    with pytest.raises(v.CortexValidationError):
        v.validate_complete({"prompt": "p", "temperature": True})


# ---------------------------------------------------------------------------
# classify / extract
# ---------------------------------------------------------------------------
def test_validate_classify_ok():
    out = v.validate_classify({"text": "a crash", "labels": ["bug", "feat"]})
    assert out == {"text": "a crash", "labels": ["bug", "feat"]}


def test_validate_classify_requires_nonempty_labels():
    with pytest.raises(v.CortexValidationError):
        v.validate_classify({"text": "x", "labels": []})


def test_validate_extract_requires_schema_object():
    with pytest.raises(v.CortexValidationError):
        v.validate_extract({"text": "x", "schema": []})
    out = v.validate_extract({"text": "x", "schema": {"type": "object"}})
    assert out["schema"] == {"type": "object"}


# ---------------------------------------------------------------------------
# govern
# ---------------------------------------------------------------------------
def test_validate_govern_defaults():
    out = v.validate_govern({"text": "candidate"})
    assert out["retrieval"] is True
    assert out["operation"] == "cortex.govern"
    assert out["context_sources"] is None


def test_validate_govern_context_sources_type():
    with pytest.raises(v.CortexValidationError):
        v.validate_govern({"text": "x", "context_sources": "not-a-list"})
    assert v.validate_govern({"text": "x", "context_sources": 3})["context_sources"] == 3


# ---------------------------------------------------------------------------
# domain — the one honored client-supplied context field
# ---------------------------------------------------------------------------
def test_domain_of_reads_only_domain():
    assert v.domain_of({"domain": "network", "tenant_id": "evil"}) == "network"
    assert v.domain_of({}) == ""


def test_non_dict_body_rejected():
    for fn in (v.validate_search, v.validate_ask, v.validate_complete,
               v.validate_classify, v.validate_extract, v.validate_govern):
        with pytest.raises(v.CortexValidationError):
            fn(None)
        with pytest.raises(v.CortexValidationError):
            fn("a string")
