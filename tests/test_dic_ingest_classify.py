"""Tests for the LLM taxonomy-classification enrichment in the DIC ingest
orchestrator.

aiify-opp-6043: manual_classification_ui -> llm_generation. These pin the
load-bearing guarantees of ``_ai_classify_into_taxonomy`` (the
``matching_algorithm = AUTO`` analog) and ``_normalize_taxonomy``:

* the model may only SELECT from the caller-supplied taxonomy — any label it
  returns that was not offered is dropped, so it can never fabricate a category;
* matching is case-insensitive but the caller's canonical casing is restored;
* single-label mode keeps only the top pick; multi-label mode de-duplicates and
  count-caps (the Tag analog);
* a confidence score gates the whole suggestion — below
  ``_CLASSIFY_MIN_CONFIDENCE`` it is dropped for the HITL / manual path;
* an explicit "unmatched" / empty selection is the manual-filing fallback (None);
* it grounds on the document text and only sends the leading
  ``_CLASSIFY_INPUT_CHARS`` slice; the taxonomy is normalized + bounded;
* it degrades silently to ``None`` on empty input/taxonomy, blank/garbled
  output, or any LLM failure — ingestion must never break on enrichment.
"""
from __future__ import annotations

import importlib
import json

import pytest

ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
router_mod = importlib.import_module("tools.llm.router")

TAXO = ["Acme Corp", "Internal HR", "Legal", "Finance"]


class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    """Stand-in LLMRouter that records the request and returns a canned reply."""

    last_request = None
    _content = "{}"

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        _Router.last_request = request
        return _Resp(self._content)


@pytest.fixture(autouse=True)
def _reset_router():
    _Router.last_request = None
    _Router._content = "{}"
    yield


def _patch_router(monkeypatch, content=None):
    import sys as _sys
    if content is not None:
        _Router._content = content
    # Patch the attribute on the real module object the helper imports from.
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)
    # Patch ALL known router aliases — the _ToolsRedirect shim causes
    # `from tools.llm.router import LLMRouter` to resolve to different
    # module objects depending on full-suite import ordering.
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Router)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Router)


def _json(**kw):
    return json.dumps(kw)


# --------------------------------------------------------------------------- #
# _normalize_taxonomy
# --------------------------------------------------------------------------- #

def test_normalize_trims_dedupes_preserves_order():
    out = ingest._normalize_taxonomy([" Legal ", "legal", "Finance", "", "Legal"])
    # Case-insensitive de-dupe, first-seen casing wins, blanks dropped.
    assert out == ["Legal", "Finance"]


def test_normalize_drops_overlong_labels():
    long_label = "x" * (ingest._CLASSIFY_LABEL_MAX_LEN + 1)
    out = ingest._normalize_taxonomy([long_label, "Legal"])
    assert out == ["Legal"]


def test_normalize_caps_count():
    many = [f"label{i}" for i in range(ingest._CLASSIFY_MAX_LABELS + 25)]
    out = ingest._normalize_taxonomy(many)
    assert len(out) == ingest._CLASSIFY_MAX_LABELS


# --------------------------------------------------------------------------- #
# _ai_classify_into_taxonomy
# --------------------------------------------------------------------------- #

def test_returns_single_label_by_default(monkeypatch):
    _patch_router(monkeypatch, content=_json(labels=["Legal"], confidence=0.91))
    out = ingest._ai_classify_into_taxonomy("a contract dispute", TAXO)
    assert out == {"labels": ["Legal"], "confidence": 0.91}


def test_case_insensitive_match_restores_canonical_casing(monkeypatch):
    _patch_router(monkeypatch, content=_json(labels=["acme corp"], confidence=0.9))
    out = ingest._ai_classify_into_taxonomy("text", TAXO)
    # The caller's canonical "Acme Corp" is restored, not the model's casing.
    assert out["labels"] == ["Acme Corp"]


def test_fabricated_label_dropped(monkeypatch):
    # "Marketing" was never offered -> dropped; nothing valid remains -> None.
    _patch_router(monkeypatch, content=_json(labels=["Marketing"], confidence=0.95))
    assert ingest._ai_classify_into_taxonomy("text", TAXO) is None


def test_mixed_valid_and_fabricated_keeps_only_valid(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(labels=["Marketing", "Finance"], confidence=0.9),
    )
    out = ingest._ai_classify_into_taxonomy(
        "text", TAXO, multi_label=True
    )
    assert out["labels"] == ["Finance"]


def test_single_label_keeps_only_first(monkeypatch):
    _patch_router(
        monkeypatch, content=_json(labels=["Legal", "Finance"], confidence=0.9)
    )
    out = ingest._ai_classify_into_taxonomy("text", TAXO)  # multi_label=False
    assert out["labels"] == ["Legal"]


def test_multi_label_dedupes_and_caps(monkeypatch):
    picks = ["Legal", "legal", "Finance", "Acme Corp", "Internal HR"]
    _patch_router(monkeypatch, content=_json(labels=picks, confidence=0.9))
    out = ingest._ai_classify_into_taxonomy("text", TAXO, multi_label=True)
    # De-duplicated (Legal once) and capped at _CLASSIFY_MAX_SELECTED.
    assert out["labels"].count("Legal") == 1
    assert len(out["labels"]) <= ingest._CLASSIFY_MAX_SELECTED


def test_unmatched_sentinel_returns_none(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(labels=[ingest._CLASSIFY_UNMATCHED], confidence=0.99),
    )
    assert ingest._ai_classify_into_taxonomy("text", TAXO) is None


def test_empty_selection_returns_none(monkeypatch):
    _patch_router(monkeypatch, content=_json(labels=[], confidence=0.99))
    assert ingest._ai_classify_into_taxonomy("text", TAXO) is None


def test_low_confidence_dropped(monkeypatch):
    _patch_router(monkeypatch, content=_json(labels=["Legal"], confidence=0.5))
    assert ingest._ai_classify_into_taxonomy("text", TAXO) is None


def test_confidence_at_threshold_kept(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(
            labels=["Legal"], confidence=ingest._CLASSIFY_MIN_CONFIDENCE
        ),
    )
    out = ingest._ai_classify_into_taxonomy("text", TAXO)
    assert out is not None
    assert out["confidence"] == ingest._CLASSIFY_MIN_CONFIDENCE


def test_missing_confidence_returns_none(monkeypatch):
    _patch_router(monkeypatch, content=_json(labels=["Legal"]))
    assert ingest._ai_classify_into_taxonomy("text", TAXO) is None


def test_empty_text_returns_none_without_calling_llm(monkeypatch):
    _patch_router(monkeypatch)
    assert ingest._ai_classify_into_taxonomy("   ", TAXO) is None
    assert _Router.last_request is None


def test_empty_taxonomy_returns_none_without_calling_llm(monkeypatch):
    _patch_router(monkeypatch)
    assert ingest._ai_classify_into_taxonomy("some text", []) is None
    assert _Router.last_request is None


def test_strips_fenced_block(monkeypatch):
    body = _json(labels=["Finance"], confidence=0.8)
    _patch_router(monkeypatch, content=f"```json\n{body}\n```")
    out = ingest._ai_classify_into_taxonomy("text", TAXO)
    assert out["labels"] == ["Finance"]


def test_garbled_output_returns_none(monkeypatch):
    _patch_router(monkeypatch, content="not json at all")
    assert ingest._ai_classify_into_taxonomy("text", TAXO) is None


def test_input_truncated_to_budget(monkeypatch):
    _patch_router(monkeypatch, content=_json(labels=["Legal"], confidence=0.9))
    src = "q" * (ingest._CLASSIFY_INPUT_CHARS + 5000)
    ingest._ai_classify_into_taxonomy(src, TAXO)
    sent = _Router.last_request.messages[0]["content"]
    # Only the leading _CLASSIFY_INPUT_CHARS characters of the document reach
    # the model (the taxonomy block adds its own, non-'q', text).
    assert sent.count("q") == ingest._CLASSIFY_INPUT_CHARS


def test_candidate_labels_are_sent(monkeypatch):
    _patch_router(monkeypatch, content=_json(labels=["Legal"], confidence=0.9))
    ingest._ai_classify_into_taxonomy("text", TAXO)
    sent = _Router.last_request.messages[0]["content"]
    for label in TAXO:
        assert label in sent


def test_llm_failure_returns_none(monkeypatch):
    import sys as _sys

    class _Boom:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Boom)
    for _key, _mod in list(_sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Boom)
    assert ingest._ai_classify_into_taxonomy("some text", TAXO) is None


# --------------------------------------------------------------------------- #
# _detect_classify_anomaly (aiify-rm-a3344-phase-18)
# --------------------------------------------------------------------------- #

def _result(labels, confidence):
    return {"labels": labels, "confidence": confidence}


def test_no_anomaly_clean_result():
    high_conf = ingest._CLASSIFY_MIN_CONFIDENCE + ingest._CLASSIFY_BORDER_BAND + 0.1
    result = _result(["Legal"], high_conf)
    assert ingest._detect_classify_anomaly(result, TAXO) is None


def test_max_labels_hit_signal():
    # Exactly _CLASSIFY_MAX_SELECTED labels returned → cap hit signal.
    labels = [f"label{i}" for i in range(ingest._CLASSIFY_MAX_SELECTED)]
    taxo = labels + ["extra"]
    high_conf = ingest._CLASSIFY_MIN_CONFIDENCE + ingest._CLASSIFY_BORDER_BAND + 0.1
    result = _result(labels, high_conf)
    assert ingest._detect_classify_anomaly(result, taxo) == "max_labels_hit"


def test_borderline_confidence_signal():
    # Confidence just above the floor but within the border band.
    border_conf = ingest._CLASSIFY_MIN_CONFIDENCE + ingest._CLASSIFY_BORDER_BAND * 0.5
    result = _result(["Legal"], border_conf)
    assert ingest._detect_classify_anomaly(result, TAXO) == "borderline_confidence"


def test_confidence_at_floor_is_borderline():
    result = _result(["Legal"], ingest._CLASSIFY_MIN_CONFIDENCE)
    assert ingest._detect_classify_anomaly(result, TAXO) == "borderline_confidence"


def test_confidence_above_border_band_not_borderline():
    clean_conf = ingest._CLASSIFY_MIN_CONFIDENCE + ingest._CLASSIFY_BORDER_BAND + 0.01
    result = _result(["Legal"], clean_conf)
    assert ingest._detect_classify_anomaly(result, TAXO) != "borderline_confidence"


def test_trivial_taxonomy_signal():
    # Only one usable label in the taxonomy — no real choice was made.
    result = _result(["Solo"], ingest._CLASSIFY_MIN_CONFIDENCE + ingest._CLASSIFY_BORDER_BAND + 0.1)
    assert ingest._detect_classify_anomaly(result, ["Solo"]) == "trivial_taxonomy"


def test_none_result_returns_none():
    assert ingest._detect_classify_anomaly(None, TAXO) is None


def test_empty_result_returns_none():
    assert ingest._detect_classify_anomaly({}, TAXO) is None


def test_max_labels_hit_takes_precedence_over_borderline():
    # max_labels_hit checked first in the function.
    labels = [f"label{i}" for i in range(ingest._CLASSIFY_MAX_SELECTED)]
    taxo = labels + ["extra"]
    borderline_conf = ingest._CLASSIFY_MIN_CONFIDENCE
    result = _result(labels, borderline_conf)
    assert ingest._detect_classify_anomaly(result, taxo) == "max_labels_hit"
