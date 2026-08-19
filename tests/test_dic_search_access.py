"""Tests for clearance-aware search + grounded access explanation in DIC.

aiify-opp-6045: fulltext_search_engine -> llm_generation, modeled on the
access-control layer of a permission-aware fulltext search (paperless
``permissions.py``). The DIC search engine now:

* filters results above the caller's clearance out of ``search()`` (before the
  ``top_k`` cap) when a clearance is supplied — and leaves behavior unchanged
  when it is not;
* composes a short, grounded, NON-LEAKING ``access_explanation()`` of what was
  withheld and why, feeding the LLM only the classification counts/levels (never
  document content or the raw query), and falling back to a deterministic
  template when the LLM is unavailable so a message is always returned.

These tests pin those guarantees, with special attention to the non-leak
invariant: protected content must never reach the model or the message.
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
    """Stand-in LLMRouter that records the request and returns a canned reply."""

    last_request = None
    _content = "2 results were withheld above your CUI clearance (SECRET, TOP SECRET)."

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        _Router.last_request = request
        return _Resp(self._content)


@pytest.fixture(autouse=True)
def _reset_router():
    _Router.last_request = None
    _Router._content = "2 results were withheld above your CUI clearance (SECRET, TOP SECRET)."
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


def _result(i, classification="CUI", content="grounded source text"):
    return se.DICSearchResult(
        chunk_id=f"chunk-{i}",
        doc_id=f"doc-{i}",
        doc_title=f"Doc {i}",
        content=content,
        score=1.0 - i * 0.1,
        citation=se.Citation(
            doc_id=f"doc-{i}", doc_title=f"Doc {i}", chunk_id=f"chunk-{i}",
            classification=classification,
        ),
    )


def _patch_search(monkeypatch, results):
    monkeypatch.setattr(
        se.DICSearchEngine, "search", lambda self, *a, **k: list(results)
    )


# --------------------------------------------------------------------------- #
# Clearance ranking
# --------------------------------------------------------------------------- #

def test_clearance_rank_orders_levels():
    assert se._clearance_rank("UNCLASSIFIED") < se._clearance_rank("CUI")
    assert se._clearance_rank("CUI") < se._clearance_rank("SECRET")
    assert se._clearance_rank("SECRET") < se._clearance_rank("TOP SECRET")


def test_clearance_rank_top_secret_sci_is_highest_tier():
    assert se._clearance_rank("TOP SECRET//SCI") == se._clearance_rank("TOP SECRET")


def test_clearance_rank_unknown_defaults_to_cui_not_open():
    # An unrecognized marking must never be treated as UNCLASSIFIED (rank 0).
    assert se._clearance_rank("WEIRD-LABEL") == se._clearance_rank("CUI")
    assert se._clearance_rank(None) == se._clearance_rank("CUI")


# --------------------------------------------------------------------------- #
# access_explanation: nothing withheld
# --------------------------------------------------------------------------- #

def test_nothing_withheld_when_all_within_clearance(monkeypatch):
    _patch_search(monkeypatch, [_result(1, "CUI"), _result(2, "UNCLASSIFIED")])
    _patch_router(monkeypatch)
    expl = se.DICSearchEngine().access_explanation("q", clearance="CUI")
    assert expl.withheld_count == 0
    assert expl.visible_count == 2
    assert expl.withheld_by_level == {}
    # No need to call the LLM when nothing is restricted.
    assert _Router.last_request is None
    assert expl.llm_used is False


# --------------------------------------------------------------------------- #
# access_explanation: withholding
# --------------------------------------------------------------------------- #

def test_withholds_above_clearance_and_breaks_down_by_level(monkeypatch):
    _patch_search(
        monkeypatch,
        [_result(1, "CUI"), _result(2, "SECRET"), _result(3, "TOP SECRET"), _result(4, "SECRET")],
    )
    _patch_router(monkeypatch)
    expl = se.DICSearchEngine().access_explanation("q", clearance="CUI")
    assert expl.visible_count == 1
    assert expl.withheld_count == 3
    assert expl.withheld_by_level == {"SECRET": 2, "TOP SECRET": 1}
    assert expl.llm_used is True
    assert expl.origin == "ai_generated"


def test_llm_prompt_never_contains_content_or_query(monkeypatch):
    secret_text = "LAUNCH-CODES-XYZ"
    _patch_search(monkeypatch, [_result(1, "SECRET", content=secret_text)])
    _patch_router(monkeypatch)
    se.DICSearchEngine().access_explanation("what are the launch codes?", clearance="CUI")
    sent = _Router.last_request.messages[0]["content"] + _Router.last_request.system_prompt
    # Protected content and the raw query must never reach the model.
    assert secret_text not in sent
    assert "launch codes" not in sent.lower()
    # Only the structured breakdown (levels + counts) is shared.
    assert "SECRET" in sent


def test_fallback_message_used_when_llm_unavailable(monkeypatch):
    secret_text = "LAUNCH-CODES-XYZ"
    _patch_search(monkeypatch, [_result(1, "SECRET", content=secret_text), _result(2, "TOP SECRET")])

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
    expl = se.DICSearchEngine().access_explanation("q", clearance="CUI")
    assert expl.llm_used is False
    assert expl.withheld_count == 2
    # Deterministic fallback still names counts + levels, never the content.
    assert "SECRET" in expl.message
    assert secret_text not in expl.message
    assert "2" in expl.message


def test_blank_llm_output_falls_back(monkeypatch):
    _patch_search(monkeypatch, [_result(1, "SECRET")])
    _patch_router(monkeypatch, content="   ")
    expl = se.DICSearchEngine().access_explanation("q", clearance="CUI")
    assert expl.llm_used is False
    assert expl.withheld_count == 1
    assert expl.message  # never empty


def test_to_dict_shape(monkeypatch):
    _patch_search(monkeypatch, [_result(1, "SECRET")])
    _patch_router(monkeypatch)
    d = se.DICSearchEngine().access_explanation("q", clearance="CUI").to_dict()
    assert set(d) == {
        "clearance",
        "visible_count",
        "withheld_count",
        "withheld_by_level",
        "message",
        "llm_used",
        "origin",
    }
    assert d["withheld_by_level"] == {"SECRET": 1}


# --------------------------------------------------------------------------- #
# search() clearance filtering
# --------------------------------------------------------------------------- #

class _Raw:
    def __init__(self, i):
        self.chunk_id = f"chunk-{i}"
        self.source_id = f"doc-{i}"
        self.content = "text"
        self.final_score = 1.0 - i * 0.1
        self.score = self.final_score


def _patch_pipeline(monkeypatch, classifications):
    """Drive search() with controlled per-doc classifications."""
    storage = importlib.import_module("tools.db.storage")

    class _Conn:
        def execute(self, *a, **k):
            return self

        def fetchone(self):
            return None

        def close(self):
            pass

    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _Conn())
    # **kwargs so this stub survives a signature change on the private method it
    # stands in for — cef-di-04 added `clearance=` to `_rag_search`, and a stub
    # that enumerates the parameters breaks on a change that has nothing to do
    # with what this file tests (the clearance DROP, downstream of retrieval).
    monkeypatch.setattr(
        se.DICSearchEngine, "_rag_search",
        lambda self, query, top_k, mode, collection_id=None, **kwargs: [
            _Raw(i) for i in range(len(classifications))
        ],
    )
    # doc_id -> classification injected via _doc_meta; chunk meta resolves doc_id.
    monkeypatch.setattr(
        se, "_chunk_meta",
        lambda conn, chunk_id: {
            "page": 0, "section": "", "doc_id": chunk_id.replace("chunk-", "doc-"),
            "collection_id": "", "sha256": "", "attribution_pct": 0,
        },
    )
    by_doc = {f"doc-{i}": cls for i, cls in enumerate(classifications)}
    monkeypatch.setattr(
        se, "_doc_meta",
        lambda conn, doc_id: {"title": doc_id, "classification": by_doc.get(doc_id, "CUI")},
    )


def test_search_without_clearance_returns_all(monkeypatch):
    _patch_pipeline(monkeypatch, ["CUI", "SECRET", "TOP SECRET"])
    results = se.DICSearchEngine().search("q", top_k=10)
    assert len(results) == 3


def test_search_with_clearance_drops_above_level(monkeypatch):
    _patch_pipeline(monkeypatch, ["CUI", "SECRET", "TOP SECRET", "UNCLASSIFIED"])
    results = se.DICSearchEngine().search("q", top_k=10, clearance="CUI")
    classes = sorted(r.citation.classification for r in results)
    assert classes == ["CUI", "UNCLASSIFIED"]


def test_search_clearance_filters_before_top_k_cap(monkeypatch):
    # Two SECRET docs precede the accessible ones; with top_k=2 and CUI clearance
    # the cap must still fill with the two CUI results, not be starved.
    _patch_pipeline(monkeypatch, ["SECRET", "SECRET", "CUI", "CUI"])
    results = se.DICSearchEngine().search("q", top_k=2, clearance="CUI")
    assert len(results) == 2
    assert all(r.citation.classification == "CUI" for r in results)
