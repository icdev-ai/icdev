# CUI // SP-CTI
"""Unit tests for the shared RAG retrieval helpers (hcx-ctx-03).

Covers ``clamp_unit`` (unit-interval score normalization) and
``run_rag_search`` (tenant-scoped construction + ``.search()`` invocation with
verbatim kwarg forwarding). The retriever class is injected as a fake so the
helper is exercised without a live vector store.
"""
from __future__ import annotations

import pytest

from tools.rag.retriever_common import clamp_unit, run_rag_search


# ---------------------------------------------------------------------------
# clamp_unit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.5, 0.5),
        (0.0, 0.0),
        (1.0, 1.0),
        (1.7, 1.0),          # above range -> clamped to 1.0
        (-0.3, 0.0),         # below range -> clamped to 0.0
        ("0.42", 0.42),      # numeric string coerced
        (None, 0.0),         # unparseable -> 0.0
        ("nope", 0.0),       # non-numeric string -> 0.0
        (2, 1.0),            # int above range
    ],
)
def test_clamp_unit(value, expected):
    assert clamp_unit(value) == expected


# ---------------------------------------------------------------------------
# run_rag_search
# ---------------------------------------------------------------------------


class _FakeRetriever:
    """Records constructor + search args; mirrors RAGRetriever's shape."""

    last_instance = None

    def __init__(self, tenant_id=""):
        self.tenant_id = tenant_id
        self.search_calls = []
        _FakeRetriever.last_instance = self

    def search(self, query, **kwargs):
        self.search_calls.append((query, kwargs))
        return kwargs.get("_return", [{"chunk": query}])


def test_run_rag_search_scopes_tenant_and_forwards_kwargs():
    out = run_rag_search(
        _FakeRetriever, "fedramp ac-2", tenant_id="tenant-a", top_k=3
    )
    inst = _FakeRetriever.last_instance
    assert inst.tenant_id == "tenant-a"
    assert inst.search_calls == [("fedramp ac-2", {"top_k": 3})]
    assert out == [{"chunk": "fedramp ac-2"}]


def test_run_rag_search_defaults_tenant_to_empty():
    run_rag_search(_FakeRetriever, "q", top_k=5)
    assert _FakeRetriever.last_instance.tenant_id == ""


def test_run_rag_search_falsy_result_becomes_empty_list():
    # search() returning None/empty -> helper normalizes to []
    out = run_rag_search(_FakeRetriever, "q", top_k=1, _return=None)
    assert out == []


def test_run_rag_search_propagates_search_exceptions():
    class _Boom:
        def __init__(self, tenant_id=""):
            pass

        def search(self, query, **kwargs):
            # Reproduces the rag_search MCP handler's pre-existing divergence:
            # RAGRetriever.search() rejects filters=/agent_id=, raising here so
            # the caller's own try/except can preserve its error behavior.
            raise TypeError("search() got an unexpected keyword argument 'filters'")

    with pytest.raises(TypeError):
        run_rag_search(_Boom, "q", tenant_id="t", top_k=5, filters={"x": 1})
