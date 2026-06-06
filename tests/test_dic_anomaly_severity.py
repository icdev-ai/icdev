"""Tests for the LLM anomaly-severity grading in the DIC analytics engine.

aiify-opp-6090: hardcoded_threshold -> anomaly_detection. The original
``detect_anomalies`` classified severity with inline magic-number thresholds.
These tests pin the load-bearing guarantees of the AI-ified replacement:

* the deterministic ``_heuristic_severity`` baseline is a pure function of the
  count summary and is ALWAYS available (the safety net);
* ``_ai_anomaly_severity`` grounds the model on the real counts + a bounded
  sample of concrete anomalies, and degrades silently to ``None`` on no-data,
  blank/malformed/out-of-range output, or any LLM failure;
* a ``None`` from the model means callers fall back to the heuristic — anomaly
  detection NEVER depends on the LLM being reachable.
"""
from __future__ import annotations

import importlib

import pytest

analytics = importlib.import_module("tools.document_intelligence.analytics_engine")
router_mod = importlib.import_module("tools.llm.router")


class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    """Stand-in LLMRouter that records the request and returns a canned reply."""

    last_request = None
    last_function = None

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        _Router.last_request = request
        _Router.last_function = function
        return _Resp(self._content)

    _content = '{"severity": "high", "rationale": "Many contradictions.", "top_concern": "contradictions"}'


@pytest.fixture(autouse=True)
def _reset_router():
    _Router.last_request = None
    _Router.last_function = None
    _Router._content = (
        '{"severity": "high", "rationale": "Many contradictions.", "top_concern": "contradictions"}'
    )
    yield


def _patch_router(monkeypatch, content=None):
    if content is not None:
        _Router._content = content
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)


# ── Heuristic baseline (deterministic, always available) ──────────────────────

def test_heuristic_low_when_clean():
    summary = {"orphan_count": 0, "single_source_count": 0, "hub_count": 0,
               "contradiction_count": 0, "stale_doc_count": 0}
    assert analytics._heuristic_severity(summary) == "low"


def test_heuristic_high_on_contradictions():
    summary = {"contradiction_count": 6, "stale_doc_count": 0,
               "orphan_count": 0, "single_source_count": 0}
    assert analytics._heuristic_severity(summary) == "high"


def test_heuristic_high_on_stale_docs():
    summary = {"contradiction_count": 0, "stale_doc_count": 3,
               "orphan_count": 0, "single_source_count": 0}
    assert analytics._heuristic_severity(summary) == "high"


def test_heuristic_medium_on_orphans():
    summary = {"contradiction_count": 0, "stale_doc_count": 0,
               "orphan_count": 21, "single_source_count": 0}
    assert analytics._heuristic_severity(summary) == "medium"


def test_heuristic_tolerates_missing_keys():
    # Pure + defensive: absent keys default to 0, never KeyError.
    assert analytics._heuristic_severity({}) == "low"


# ── AI grading ────────────────────────────────────────────────────────────────

_SUMMARY = {"orphan_count": 3, "single_source_count": 2, "hub_count": 1,
            "contradiction_count": 7, "stale_doc_count": 1}
_SAMPLES = {"contradictions": [{"source": "A", "target": "B"}], "orphans": [],
            "single_source": [], "stale_docs": []}


def test_ai_parses_severity_rationale_concern(monkeypatch):
    _patch_router(monkeypatch)
    out = analytics._ai_anomaly_severity(_SUMMARY, _SAMPLES)
    assert out == {
        "severity": "high",
        "rationale": "Many contradictions.",
        "top_concern": "contradictions",
    }
    # Routed through the dedicated anomaly function key.
    assert _Router.last_function == "dic_anomaly_severity"


def test_ai_no_anomalies_skips_llm(monkeypatch):
    _patch_router(monkeypatch)
    empty = {"orphan_count": 0, "single_source_count": 0, "hub_count": 0,
             "contradiction_count": 0, "stale_doc_count": 0}
    assert analytics._ai_anomaly_severity(empty, {}) is None
    # The model must never be invoked when there is nothing to grade.
    assert _Router.last_request is None


def test_ai_sample_is_bounded(monkeypatch):
    _patch_router(monkeypatch)
    many = {"orphans": [{"label": f"n{i}"} for i in range(50)]}
    summary = {"orphan_count": 50, "single_source_count": 0, "hub_count": 0,
               "contradiction_count": 0, "stale_doc_count": 0}
    analytics._ai_anomaly_severity(summary, many)
    sent = _Router.last_request.messages[0]["content"]
    # Only the leading _ANOMALY_SAMPLE examples are described to the model.
    assert sent.count('"label"') == analytics._ANOMALY_SAMPLE


def test_ai_baseline_is_grounded_in_prompt(monkeypatch):
    _patch_router(monkeypatch)
    analytics._ai_anomaly_severity(_SUMMARY, _SAMPLES)
    sent = _Router.last_request.messages[0]["content"]
    # The deterministic baseline (high, given 7 contradictions) is handed to the
    # model as the reference point it may agree with or adjust.
    assert "Deterministic baseline severity: high" in sent


def test_ai_tolerates_fenced_json(monkeypatch):
    _patch_router(
        monkeypatch,
        content='```json\n{"severity": "medium", "rationale": "r", "top_concern": "orphans"}\n```',
    )
    out = analytics._ai_anomaly_severity(_SUMMARY, _SAMPLES)
    assert out["severity"] == "medium"


def test_ai_out_of_range_severity_returns_none(monkeypatch):
    _patch_router(monkeypatch, content='{"severity": "catastrophic"}')
    assert analytics._ai_anomaly_severity(_SUMMARY, _SAMPLES) is None


def test_ai_malformed_output_returns_none(monkeypatch):
    _patch_router(monkeypatch, content="not json at all")
    assert analytics._ai_anomaly_severity(_SUMMARY, _SAMPLES) is None


def test_ai_blank_severity_returns_none(monkeypatch):
    _patch_router(monkeypatch, content='{"severity": "", "rationale": "x"}')
    assert analytics._ai_anomaly_severity(_SUMMARY, _SAMPLES) is None


def test_ai_llm_failure_returns_none(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    assert analytics._ai_anomaly_severity(_SUMMARY, _SAMPLES) is None


def test_ai_rationale_and_concern_are_bounded(monkeypatch):
    _patch_router(
        monkeypatch,
        content='{"severity": "low", "rationale": "' + "x" * 500 + '", "top_concern": "'
        + "y" * 500 + '"}',
    )
    out = analytics._ai_anomaly_severity(_SUMMARY, _SAMPLES)
    assert len(out["rationale"]) <= 200
    assert len(out["top_concern"]) <= 80
