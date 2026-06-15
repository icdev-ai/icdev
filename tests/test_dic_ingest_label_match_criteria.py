"""Tests for the NLP-based label match-criteria suggester in the DIC ingest orchestrator.

aiify-rm-a3344-phase-75: regex_user_input -> nlp_extractor.

Pins the load-bearing guarantees of ``_ai_suggest_label_match_criteria``:

* blank label name returns None immediately (no LLM call);
* proposed keywords must be grounded in the label name / description — terms the
  model invents that do not appear in the input are stripped;
* below _LABEL_MATCH_MIN_CONFIDENCE the whole suggestion is dropped (HITL path);
* the match_mode value is constrained to "any"|"all"|"contextual";
* output lists are de-duplicated and count-capped at _LABEL_MATCH_MAX_KEYWORDS;
* any LLM failure degrades silently to None (air-gap safe);
* at least one term must survive grounding for the result to be non-None.
"""
from __future__ import annotations

import importlib
import json
import sys

import pytest

ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
router_mod = importlib.import_module("tools.llm.router")

_suggest = ingest._ai_suggest_label_match_criteria


# ---------------------------------------------------------------------------
# Stand-in router (same pattern as test_dic_ingest_classify.py)
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    """Minimal LLMRouter stub that returns a configurable JSON blob."""

    _content = "{}"

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        return _Resp(self._content)


@pytest.fixture(autouse=True)
def _reset_router():
    _Router._content = "{}"
    yield


def _patch_router(monkeypatch, content: str):
    _Router._content = content
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _Router)
    for _key, _mod in list(sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _Router)


def _make_json(
    keywords=None,
    required_phrases=None,
    contextual_signals=None,
    match_mode="any",
    confidence=0.80,
) -> str:
    return json.dumps({
        "keywords": keywords if keywords is not None else ["invoice", "acme"],
        "required_phrases": required_phrases if required_phrases is not None else [],
        "contextual_signals": contextual_signals if contextual_signals is not None else [],
        "match_mode": match_mode,
        "confidence": confidence,
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_blank_label_returns_none():
    """Blank / whitespace-only label_name must return None without any LLM call."""
    assert _suggest("") is None
    assert _suggest("   ") is None


def test_successful_suggestion(monkeypatch):
    """Happy-path: valid JSON from LLM with grounded keywords returns a dict."""
    _patch_router(monkeypatch, _make_json(keywords=["invoice", "acme"], confidence=0.85))
    result = _suggest("Acme Corp Invoice", description="quarterly invoice from Acme")
    assert result is not None
    assert result["match_mode"] == "any"
    assert result["confidence"] == pytest.approx(0.85, abs=1e-4)
    # Both keywords are grounded in "Acme Corp Invoice quarterly invoice from Acme".
    assert "invoice" in result["keywords"]
    assert "acme" in result["keywords"]
    assert result["origin"] == "ai_generated"


def test_low_confidence_returns_none(monkeypatch):
    """Confidence below _LABEL_MATCH_MIN_CONFIDENCE must return None."""
    _patch_router(monkeypatch, _make_json(keywords=["invoice", "acme"], confidence=0.30))
    result = _suggest("Acme Corp Invoice", description="quarterly invoice from Acme")
    assert result is None


def test_confidence_at_threshold_passes(monkeypatch):
    """Confidence exactly at _LABEL_MATCH_MIN_CONFIDENCE must pass the gate."""
    threshold = ingest._LABEL_MATCH_MIN_CONFIDENCE
    _patch_router(monkeypatch, _make_json(keywords=["invoice", "acme"], confidence=threshold))
    result = _suggest("Acme Corp Invoice", description="quarterly invoice from acme corp")
    assert result is not None


def test_ungrounded_keywords_stripped(monkeypatch):
    """Keywords not derivable from the label name / description must be removed."""
    payload = json.dumps({
        "keywords": ["invoice", "xyzzyphan"],  # "xyzzyphan" not in input
        "required_phrases": [],
        "contextual_signals": [],
        "match_mode": "any",
        "confidence": 0.80,
    })
    _patch_router(monkeypatch, payload)
    result = _suggest("Acme Invoice", description="invoice from acme")
    assert result is not None
    assert "xyzzyphan" not in result["keywords"]
    assert "invoice" in result["keywords"]


def test_all_keywords_ungrounded_returns_none(monkeypatch):
    """When every proposed keyword fails grounding the whole result is None."""
    payload = json.dumps({
        "keywords": ["xyzzyphan", "foobarbaz"],
        "required_phrases": [],
        "contextual_signals": [],
        "match_mode": "any",
        "confidence": 0.90,
    })
    _patch_router(monkeypatch, payload)
    result = _suggest("Acme Invoice", description="invoice from acme")
    assert result is None


def test_match_mode_constrained(monkeypatch):
    """Unknown match_mode values must be normalised to 'any'."""
    payload = json.dumps({
        "keywords": ["invoice", "acme"],
        "required_phrases": [],
        "contextual_signals": [],
        "match_mode": "fuzzy",  # not in the allowed set
        "confidence": 0.80,
    })
    _patch_router(monkeypatch, payload)
    result = _suggest("Acme Invoice", description="invoice acme")
    assert result is not None
    assert result["match_mode"] == "any"


def test_keywords_count_capped(monkeypatch):
    """Output keywords must not exceed _LABEL_MATCH_MAX_KEYWORDS."""
    cap = ingest._LABEL_MATCH_MAX_KEYWORDS
    label = " ".join(f"word{i}" for i in range(cap + 5))
    keywords = [f"word{i}" for i in range(cap + 5)]
    payload = json.dumps({
        "keywords": keywords,
        "required_phrases": [],
        "contextual_signals": [],
        "match_mode": "any",
        "confidence": 0.80,
    })
    _patch_router(monkeypatch, payload)
    result = _suggest(label)
    assert result is not None
    assert len(result["keywords"]) <= cap


def test_llm_failure_returns_none(monkeypatch):
    """Any LLM exception must degrade to None without raising."""
    class _BrokenRouter:
        def __init__(self, *a, **k):
            pass
        def invoke(self, *a, **k):
            raise RuntimeError("network failure")

    monkeypatch.setattr(router_mod, "LLMRouter", _BrokenRouter)
    import icdev.tools.llm.router as _icdev_router_mod
    monkeypatch.setattr(_icdev_router_mod, "LLMRouter", _BrokenRouter)
    for _key, _mod in list(sys.modules.items()):
        if "llm.router" in _key and hasattr(_mod, "LLMRouter"):
            monkeypatch.setattr(_mod, "LLMRouter", _BrokenRouter)

    result = _suggest("Acme Invoice", description="invoice acme")
    assert result is None


def test_malformed_json_returns_none(monkeypatch):
    """Non-JSON response must degrade to None."""
    _patch_router(monkeypatch, "not valid json at all")
    result = _suggest("Acme Invoice", description="invoice acme")
    assert result is None


def test_required_phrases_included(monkeypatch):
    """required_phrases grounded in label/description must appear in output."""
    payload = json.dumps({
        "keywords": ["invoice"],
        "required_phrases": ["acme corp"],
        "contextual_signals": [],
        "match_mode": "all",
        "confidence": 0.75,
    })
    _patch_router(monkeypatch, payload)
    result = _suggest("Acme Corp Invoice", description="invoice from acme corp")
    assert result is not None
    assert "acme corp" in result["required_phrases"]
    assert result["match_mode"] == "all"


def test_fenced_json_accepted(monkeypatch):
    """JSON wrapped in markdown fences must still be parsed correctly."""
    inner = _make_json(keywords=["invoice", "acme"], confidence=0.80)
    _patch_router(monkeypatch, f"```json\n{inner}\n```")
    result = _suggest("Acme Invoice", description="invoice from acme")
    assert result is not None
    assert "invoice" in result["keywords"]


def test_description_extends_haystack(monkeypatch):
    """Terms from description (not in label name) must survive grounding."""
    payload = json.dumps({
        "keywords": ["quarterly", "invoice"],
        "required_phrases": [],
        "contextual_signals": [],
        "match_mode": "any",
        "confidence": 0.78,
    })
    _patch_router(monkeypatch, payload)
    # "quarterly" only appears in description, not in "Acme Invoice"
    result = _suggest("Acme Invoice", description="quarterly reports and invoices")
    assert result is not None
    assert "quarterly" in result["keywords"]
