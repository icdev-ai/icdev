# CUI // SP-CTI
"""Tests for collection-relative freshness anomaly detection in the DIC engine.

aiify-opp-6042: hardcoded_threshold -> anomaly_detection (the document-freshness
sibling of opp-6090 in analytics_engine). The original ``_score_doc`` classified
state with inline magic-number cutoffs (0.35 / 0.7). These tests pin:

* the cutoffs now live in named constants and ``_classify_state`` reproduces the
  exact original banding;
* ``detect_freshness_anomalies`` is a pure statistical heuristic that flags docs
  whose freshness score is an outlier *for their collection* — never depending on
  the LLM, guarded for tiny collections, and never flagging a still-fresh doc;
* ``_heuristic_anomaly_severity`` is a pure baseline that is ALWAYS available;
* ``_ai_freshness_anomaly_severity`` grounds the model on the real distribution +
  a bounded sample of outliers, and degrades silently to ``None`` on no-data,
  blank/malformed/out-of-range output, or any LLM failure.
"""
from __future__ import annotations

import importlib

import pytest

fe = importlib.import_module("tools.document_intelligence.freshness_engine")
router_mod = importlib.import_module("tools.llm.router")


def _doc(doc_id: str, score: float, title: str = "") -> "fe.FreshnessResult":
    return fe.FreshnessResult(
        doc_id=doc_id,
        title=title or doc_id,
        collection_id="col1",
        state=fe._classify_state(score),
        score=score,
    )


# ── Named-constant banding (preserves the original 0.35 / 0.7 cutoffs) ─────────

def test_classify_state_fresh():
    assert fe._classify_state(0.0) == "fresh"
    assert fe._classify_state(0.349) == "fresh"


def test_classify_state_aging():
    assert fe._classify_state(0.35) == "aging"
    assert fe._classify_state(0.699) == "aging"


def test_classify_state_stale():
    assert fe._classify_state(0.70) == "stale"
    assert fe._classify_state(1.0) == "stale"


def test_constants_match_original_magic_numbers():
    # Guards against an accidental drift of the band boundaries.
    assert fe._FRESH_THRESHOLD == 0.35
    assert fe._STALE_THRESHOLD == 0.70


# ── Deterministic anomaly severity baseline (always available) ────────────────

def test_heuristic_severity_low_when_none():
    assert fe._heuristic_anomaly_severity(0, 100) == "low"


def test_heuristic_severity_low_empty_collection():
    assert fe._heuristic_anomaly_severity(0, 0) == "low"


def test_heuristic_severity_high_on_large_fraction():
    # 30% of the collection anomalous -> high.
    assert fe._heuristic_anomaly_severity(3, 10) == "high"


def test_heuristic_severity_medium_on_moderate_fraction():
    # 10% -> medium.
    assert fe._heuristic_anomaly_severity(1, 10) == "medium"


# ── detect_freshness_anomalies (pure statistical heuristic) ───────────────────

def test_detect_below_min_docs_returns_empty():
    results = [_doc("d1", 0.9), _doc("d2", 0.1)]
    out = fe._compute_freshness_anomalies(results)
    assert out["anomaly_count"] == 0
    assert out["anomalies"] == []
    assert out["severity"] == "low"


def test_detect_flags_stale_outlier_in_fresh_corpus():
    # Five fresh docs + one clearly stale outlier.
    results = [_doc(f"d{i}", 0.10) for i in range(5)] + [_doc("rot", 0.95)]
    out = fe._compute_freshness_anomalies(results)
    assert out["anomaly_count"] == 1
    assert out["anomalies"][0]["doc_id"] == "rot"
    assert out["anomalies"][0]["z_score"] > fe._ANOMALY_STDEV_K


def test_detect_never_flags_a_fresh_doc():
    # One doc slightly above the others but still well under the fresh floor.
    results = [_doc(f"d{i}", 0.05) for i in range(5)] + [_doc("hi", 0.20)]
    out = fe._compute_freshness_anomalies(results)
    # 0.20 < _ANOMALY_ABS_FLOOR (0.35) -> not an anomaly even if it is an outlier.
    assert out["anomaly_count"] == 0


def test_detect_uniform_collection_has_no_outliers():
    results = [_doc(f"d{i}", 0.5) for i in range(6)]
    out = fe._compute_freshness_anomalies(results)
    assert out["anomaly_count"] == 0
    assert out["stdev"] == 0.0


def test_detect_anomalies_sorted_by_score_desc():
    results = [_doc(f"d{i}", 0.10) for i in range(6)] + [_doc("a", 0.80), _doc("b", 0.95)]
    out = fe._compute_freshness_anomalies(results)
    scores = [a["score"] for a in out["anomalies"]]
    assert scores == sorted(scores, reverse=True)


# ── AI grading (best-effort enrichment, mirrors opp-6090 contract) ────────────

class _Resp:
    def __init__(self, content):
        self.content = content


class _Router:
    last_request = None
    last_function = None
    _content = '{"severity": "high", "rationale": "Dominant outlier.", "top_concern": "rot"}'

    def __init__(self, *a, **k):
        pass

    def invoke(self, function, request):
        _Router.last_request = request
        _Router.last_function = function
        return _Resp(self._content)


@pytest.fixture(autouse=True)
def _reset_router():
    _Router.last_request = None
    _Router.last_function = None
    _Router._content = '{"severity": "high", "rationale": "Dominant outlier.", "top_concern": "rot"}'
    yield


def _patch_router(monkeypatch, content=None):
    if content is not None:
        _Router._content = content
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)


_SUMMARY = {"anomaly_count": 2, "total": 10, "mean": 0.3, "stdev": 0.2,
            "baseline_severity": "medium"}
_ANOMS = [{"doc_id": "rot", "title": "Old Policy", "score": 0.95, "z_score": 3.2,
           "state": "stale"}]


def test_ai_parses_and_routes_to_dedicated_key(monkeypatch):
    _patch_router(monkeypatch)
    out = fe._ai_freshness_anomaly_severity(_SUMMARY, _ANOMS)
    assert out == {"severity": "high", "rationale": "Dominant outlier.", "top_concern": "rot"}
    assert _Router.last_function == "dic_freshness_anomaly_severity"


def test_ai_no_anomalies_skips_llm(monkeypatch):
    _patch_router(monkeypatch)
    assert fe._ai_freshness_anomaly_severity({"anomaly_count": 0}, []) is None
    assert _Router.last_request is None


def test_ai_sample_is_bounded(monkeypatch):
    _patch_router(monkeypatch)
    many = [{"doc_id": f"d{i}", "title": f"t{i}", "score": 0.9, "z_score": 3.0,
             "state": "stale"} for i in range(50)]
    summary = {"anomaly_count": 50, "total": 60, "mean": 0.2, "stdev": 0.2,
               "baseline_severity": "high"}
    fe._ai_freshness_anomaly_severity(summary, many)
    sent = _Router.last_request.messages[0]["content"]
    assert sent.count('"doc_id"') == fe._ANOMALY_SAMPLE


def test_ai_baseline_is_grounded_in_prompt(monkeypatch):
    _patch_router(monkeypatch)
    fe._ai_freshness_anomaly_severity(_SUMMARY, _ANOMS)
    sent = _Router.last_request.messages[0]["content"]
    assert "Deterministic baseline severity: medium" in sent


def test_ai_tolerates_fenced_json(monkeypatch):
    _patch_router(
        monkeypatch,
        content='```json\n{"severity": "medium", "rationale": "r", "top_concern": "rot"}\n```',
    )
    out = fe._ai_freshness_anomaly_severity(_SUMMARY, _ANOMS)
    assert out["severity"] == "medium"


def test_ai_out_of_range_severity_returns_none(monkeypatch):
    _patch_router(monkeypatch, content='{"severity": "catastrophic"}')
    assert fe._ai_freshness_anomaly_severity(_SUMMARY, _ANOMS) is None


def test_ai_malformed_output_returns_none(monkeypatch):
    _patch_router(monkeypatch, content="not json at all")
    assert fe._ai_freshness_anomaly_severity(_SUMMARY, _ANOMS) is None


def test_ai_blank_severity_returns_none(monkeypatch):
    _patch_router(monkeypatch, content='{"severity": "", "rationale": "x"}')
    assert fe._ai_freshness_anomaly_severity(_SUMMARY, _ANOMS) is None


def test_ai_llm_failure_returns_none(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    assert fe._ai_freshness_anomaly_severity(_SUMMARY, _ANOMS) is None


def test_ai_rationale_and_concern_are_bounded(monkeypatch):
    _patch_router(
        monkeypatch,
        content='{"severity": "low", "rationale": "' + "x" * 500 + '", "top_concern": "'
        + "y" * 500 + '"}',
    )
    out = fe._ai_freshness_anomaly_severity(_SUMMARY, _ANOMS)
    assert len(out["rationale"]) <= 200
    assert len(out["top_concern"]) <= 80


# ── Integration: detect wires the heuristic + AI together ─────────────────────

def test_detect_invokes_ai_grade_when_anomalies_present(monkeypatch):
    _patch_router(monkeypatch)
    results = [_doc(f"d{i}", 0.10) for i in range(5)] + [_doc("rot", 0.95)]
    out = fe.detect_freshness_anomalies(results)
    assert out["anomaly_count"] == 1
    assert out["ai_grade"] == {
        "severity": "high", "rationale": "Dominant outlier.", "top_concern": "rot",
    }
    # Deterministic severity remains authoritative and present regardless.
    assert out["severity"] in {"low", "medium", "high"}
