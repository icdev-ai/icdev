"""Tests for LLM-assisted query expansion in the DIC search engine.

aiify-opp-6039: fulltext_search_engine -> llm_generation, modeled on the
document-matching module of a fulltext search engine. ``DICSearchEngine.
expand_query`` is an OPT-IN layer that asks the LLM for additional search
keywords / synonyms so documents phrased differently than the query still
match (better recall). These tests pin its load-bearing guarantees:

* it returns extra keywords, de-duplicated against the query's own words and
  capped at the term limit, and builds an additive ``expanded_query``;
* it never degrades below the un-expanded baseline — empty query, LLM failure,
  blank/None output, the NONE sentinel, and no-usable-terms all fall back to the
  original query with a ``refusal_reason``;
* the query is user-provided, so injection scanning stays ON;
* it rejects overlong "terms" (a returned sentence instead of keywords).
"""
from __future__ import annotations

import importlib

import pytest

se = importlib.import_module("tools.document_intelligence.search_engine")
router_mod = importlib.import_module("tools.llm.router")


class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    """Stand-in LLMRouter that records the request and returns canned keywords."""

    last_request = None
    _content = "budget, funding, expenditure"

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        _Router.last_request = request
        return _Resp(self._content)


@pytest.fixture(autouse=True)
def _reset_router():
    _Router.last_request = None
    _Router._content = "budget, funding, expenditure"
    yield


def _patch_router(monkeypatch, content=None):
    import sys as _sys
    if content is not None:
        _Router._content = content
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)
    # Patch ALL known router aliases — the _ToolsRedirect shim causes
    # `from tools.llm.router import LLMRouter` to resolve to different
    # module objects depending on full-suite import ordering.
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Router)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Router)


# --------------------------------------------------------------------------- #
# Empty query — refuse without calling the LLM
# --------------------------------------------------------------------------- #

def test_empty_query_refuses_without_llm(monkeypatch):
    _patch_router(monkeypatch)
    ex = se.DICSearchEngine().expand_query("   ")
    assert ex.llm_used is False
    assert ex.refusal_reason == "empty_query"
    assert ex.terms == []
    assert ex.expanded_query == ""
    assert _Router.last_request is None


# --------------------------------------------------------------------------- #
# Happy path: additive expansion
# --------------------------------------------------------------------------- #

def test_expansion_returns_additive_terms(monkeypatch):
    _patch_router(monkeypatch)
    ex = se.DICSearchEngine().expand_query("budget report")
    assert ex.llm_used is True
    assert ex.refusal_reason == ""
    # "budget" is already in the query and must be de-duplicated out.
    assert "budget" not in ex.terms
    assert "funding" in ex.terms and "expenditure" in ex.terms
    # expanded_query is the original query with the new terms appended.
    assert ex.expanded_query.startswith("budget report")
    assert "funding" in ex.expanded_query and "expenditure" in ex.expanded_query
    assert ex.origin == "ai_generated"


def test_to_dict_shape(monkeypatch):
    _patch_router(monkeypatch)
    d = se.DICSearchEngine().expand_query("budget").to_dict()
    assert set(d) == {
        "original_query",
        "terms",
        "expanded_query",
        "llm_used",
        "refusal_reason",
        "origin",
    }
    assert isinstance(d["terms"], list)


def test_terms_capped_at_max(monkeypatch):
    many = ", ".join(f"term{i}" for i in range(50))
    _patch_router(monkeypatch, content=many)
    ex = se.DICSearchEngine().expand_query("q")
    assert len(ex.terms) == se._EXPANSION_MAX_TERMS


def test_overlong_term_rejected(monkeypatch):
    sentence = "X" * (se._EXPANSION_MAX_TERM_LEN + 5)
    _patch_router(monkeypatch, content=f"{sentence}, funding")
    ex = se.DICSearchEngine().expand_query("budget")
    # The runaway sentence is dropped; the real keyword survives.
    assert all(len(t) <= se._EXPANSION_MAX_TERM_LEN for t in ex.terms)
    assert "funding" in ex.terms


def test_newline_separated_terms_parsed(monkeypatch):
    _patch_router(monkeypatch, content="funding\nexpenditure\nspending")
    ex = se.DICSearchEngine().expand_query("budget")
    assert {"funding", "expenditure", "spending"} <= set(ex.terms)


# --------------------------------------------------------------------------- #
# Injection scanning stays on (user-provided query)
# --------------------------------------------------------------------------- #

def test_query_is_not_injection_skipped(monkeypatch):
    _patch_router(monkeypatch)
    se.DICSearchEngine().expand_query("budget")
    req = _Router.last_request
    assert getattr(req, "skip_injection_scan", False) is False
    # The query is forwarded to the model.
    assert "budget" in req.messages[0]["content"]


# --------------------------------------------------------------------------- #
# Degradation never falls below the baseline
# --------------------------------------------------------------------------- #

def test_none_sentinel_falls_back_to_original(monkeypatch):
    _patch_router(monkeypatch, content="NONE")
    ex = se.DICSearchEngine().expand_query("budget report")
    assert ex.llm_used is True
    assert ex.refusal_reason == "no_terms"
    assert ex.terms == []
    assert ex.expanded_query == "budget report"


def test_no_usable_terms_falls_back(monkeypatch):
    # Model echoes only words already in the query — nothing additive survives.
    _patch_router(monkeypatch, content="budget, report")
    ex = se.DICSearchEngine().expand_query("budget report")
    assert ex.terms == []
    assert ex.refusal_reason == "no_terms"
    assert ex.expanded_query == "budget report"


def test_llm_exception_degrades_to_original(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    import sys as _sys
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Boom)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Boom)
    ex = se.DICSearchEngine().expand_query("budget report")
    assert ex.llm_used is False
    assert ex.refusal_reason == "llm_unavailable"
    assert ex.expanded_query == "budget report"


def test_blank_output_degrades(monkeypatch):
    _patch_router(monkeypatch, content="   ")
    ex = se.DICSearchEngine().expand_query("budget report")
    assert ex.llm_used is False
    assert ex.refusal_reason == "llm_unavailable"
    assert ex.expanded_query == "budget report"


def test_none_response_degrades(monkeypatch):
    class _NoneRouter:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            return None

    monkeypatch.setattr(router_mod, "LLMRouter", _NoneRouter)
    import sys as _sys
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _NoneRouter)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _NoneRouter)
    ex = se.DICSearchEngine().expand_query("budget report")
    assert ex.llm_used is False
    assert ex.refusal_reason == "llm_unavailable"
    assert ex.expanded_query == "budget report"
