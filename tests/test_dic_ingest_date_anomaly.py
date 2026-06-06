"""Tests for the date-parsing anomaly detection in the DIC ingest orchestrator.

aiify-opp-6048: hardcoded_threshold -> anomaly_detection. The paperless
date-parsing consumer plugin parsed dates out of OCR text with fixed rules; the
scan recommended an anomaly-detection paradigm. The repo is ephemeral, so the
augmentation lands in the analogous ICDEV subsystem (DIC). These pin the
load-bearing guarantees:

* ``_parse_candidate_dates`` deterministically extracts ISO, US-slash, and
  long-form (month-name) dates, validates them as real calendar dates, and
  de-duplicates by ISO value — no network, no third-party dateutil;
* ``_detect_date_anomalies`` flags future-dated / implausibly-old / statistical
  cluster-outlier dates against the *named* thresholds (the magic numbers the
  scan called out), and is the ALWAYS-authoritative offline baseline;
* outlier detection only fires with at least ``_DATE_ANOMALY_MIN_SAMPLE`` dates
  and a non-degenerate spread;
* ``_ai_date_anomaly_assessment`` only grades severity and degrades silently to
  ``None`` on empty input, blank/garbled output, or any LLM failure;
* ``assess_document_dates`` returns ``None`` when nothing is anomalous and never
  lets the LLM override the deterministic *detection*, only the severity label.
"""
from __future__ import annotations

import importlib
import json

import pytest

ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
router_mod = importlib.import_module("tools.llm.router")

# A fixed "now" so future/anomaly tests are deterministic regardless of clock.
NOW = "2026-06-06T00:00:00+00:00"


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
    if content is not None:
        _Router._content = content
    monkeypatch.setattr(router_mod, "LLMRouter", _Router)


def _json(**kw):
    return json.dumps(kw)


# ── _parse_candidate_dates ────────────────────────────────────────────────────

def test_parses_multiple_formats():
    text = (
        "Effective 2024-03-09, superseding the contract of 03/15/2023. "
        "Signed on March 9, 2024 and acknowledged 9 April 2025."
    )
    isos = {d["iso"] for d in ingest._parse_candidate_dates(text)}
    assert "2024-03-09" in isos
    assert "2023-03-15" in isos
    assert "2025-04-09" in isos


def test_dedup_by_iso_keeps_first_occurrence():
    text = "Dated 2024-03-09. Re-confirmed March 9, 2024 later in the file."
    parsed = ingest._parse_candidate_dates(text)
    nine_march = [d for d in parsed if d["iso"] == "2024-03-09"]
    assert len(nine_march) == 1
    assert nine_march[0]["raw"] == "2024-03-09"  # first textual hit wins


def test_invalid_calendar_dates_dropped():
    # Month 13 and Feb 30 are not real dates and must not parse.
    parsed = ingest._parse_candidate_dates("bogus 2024-13-01 and 2024-02-30 here")
    assert parsed == []


def test_empty_text_returns_empty_list():
    assert ingest._parse_candidate_dates("") == []
    assert ingest._parse_candidate_dates("no dates at all in here") == []


def test_only_leading_window_scanned():
    filler = "x" * (ingest._DATE_INPUT_CHARS + 100)
    text = filler + " 2024-03-09"
    # The date sits past the input budget, so it is never seen.
    assert ingest._parse_candidate_dates(text) == []


# ── _detect_date_anomalies ────────────────────────────────────────────────────

def test_future_dated_flagged():
    parsed = ingest._parse_candidate_dates("approved 2030-01-01")
    out = ingest._detect_date_anomalies(parsed, now_iso=NOW)
    assert out["anomaly_count"] == 1
    assert out["anomalies"][0]["reason"] == "future_dated"


def test_implausibly_old_flagged():
    parsed = ingest._parse_candidate_dates("origin 1066-10-14 per the record")
    out = ingest._detect_date_anomalies(parsed, now_iso=NOW)
    assert out["anomaly_count"] == 1
    assert out["anomalies"][0]["reason"] == "implausibly_old"


def test_cluster_outlier_flagged():
    # A tight 2024 cluster plus one 2093 OCR-typo outlier (still not "future"
    # relative to nothing — it IS future vs NOW, so assert outlier via a past
    # cluster with one far-past member instead).
    text = (
        "2024-01-01 2024-01-05 2024-01-09 2024-01-12 2024-01-15 1995-01-01"
    )
    parsed = ingest._parse_candidate_dates(text)
    out = ingest._detect_date_anomalies(parsed, now_iso=NOW)
    reasons = {a["iso"]: a["reason"] for a in out["anomalies"]}
    assert reasons.get("1995-01-01") == "cluster_outlier"


def test_no_outlier_below_min_sample():
    # Two dates is below _DATE_ANOMALY_MIN_SAMPLE; spread alone is not an anomaly.
    text = "2024-01-01 and 2010-01-01"
    parsed = ingest._parse_candidate_dates(text)
    out = ingest._detect_date_anomalies(parsed, now_iso=NOW)
    assert all(a["reason"] != "cluster_outlier" for a in out["anomalies"])


def test_uniform_dates_no_anomaly():
    text = "2024-01-01 2024-01-02 2024-01-03 2024-01-04 2024-01-05"
    parsed = ingest._parse_candidate_dates(text)
    out = ingest._detect_date_anomalies(parsed, now_iso=NOW)
    assert out["anomaly_count"] == 0
    assert out["baseline_severity"] == "low"


def test_empty_parsed_returns_clean_baseline():
    out = ingest._detect_date_anomalies([], now_iso=NOW)
    assert out["total"] == 0
    assert out["anomaly_count"] == 0
    assert out["baseline_severity"] == "low"


def test_detect_result_is_json_clean():
    parsed = ingest._parse_candidate_dates("2030-01-01 2024-01-01")
    out = ingest._detect_date_anomalies(parsed, now_iso=NOW)
    # Scratch fields (_ts/_year) must be stripped so the proposal serializes.
    json.dumps(out)
    for d in parsed:
        assert "_ts" not in d and "_year" not in d


def test_named_thresholds_present():
    # The "hardcoded_threshold" the scan flagged is now tunable in one place.
    assert ingest._DATE_FUTURE_TOLERANCE_DAYS >= 0
    assert ingest._DATE_MIN_PLAUSIBLE_YEAR == 1900
    assert ingest._DATE_ANOMALY_STDEV_K > 0
    assert ingest._DATE_ANOMALY_MIN_SAMPLE >= 2


# ── _heuristic_date_anomaly_severity ──────────────────────────────────────────

def test_heuristic_severity_bands():
    assert ingest._heuristic_date_anomaly_severity(0, 0) == "low"
    assert ingest._heuristic_date_anomaly_severity(0, 10) == "low"
    assert ingest._heuristic_date_anomaly_severity(1, 10) == "medium"
    assert ingest._heuristic_date_anomaly_severity(5, 10) == "high"


# ── _ai_date_anomaly_assessment ───────────────────────────────────────────────

def test_ai_returns_normalized_grade(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(severity="high", rationale="future date dominates", top_concern="2030-01-01"),
    )
    summary = {"anomaly_count": 1, "total": 3, "baseline_severity": "medium"}
    out = ingest._ai_date_anomaly_assessment(summary, [{"iso": "2030-01-01", "reason": "future_dated"}])
    assert out == {
        "severity": "high",
        "rationale": "future date dominates",
        "top_concern": "2030-01-01",
    }


def test_ai_no_anomalies_skips_llm(monkeypatch):
    _patch_router(monkeypatch)
    assert ingest._ai_date_anomaly_assessment({"anomaly_count": 0}, []) is None
    assert _Router.last_request is None


def test_ai_invalid_severity_returns_none(monkeypatch):
    _patch_router(monkeypatch, content=_json(severity="catastrophic", rationale="x", top_concern="y"))
    out = ingest._ai_date_anomaly_assessment(
        {"anomaly_count": 1, "total": 1}, [{"iso": "2030-01-01", "reason": "future_dated"}]
    )
    assert out is None


def test_ai_strips_fenced_block(monkeypatch):
    body = _json(severity="low", rationale="minor", top_concern="2024-01-01")
    _patch_router(monkeypatch, content=f"```json\n{body}\n```")
    out = ingest._ai_date_anomaly_assessment(
        {"anomaly_count": 1, "total": 5}, [{"iso": "2024-01-01", "reason": "cluster_outlier"}]
    )
    assert out["severity"] == "low"


def test_ai_garbled_returns_none(monkeypatch):
    _patch_router(monkeypatch, content="not json")
    out = ingest._ai_date_anomaly_assessment(
        {"anomaly_count": 1, "total": 1}, [{"iso": "2030-01-01", "reason": "future_dated"}]
    )
    assert out is None


def test_ai_llm_failure_returns_none(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    out = ingest._ai_date_anomaly_assessment(
        {"anomaly_count": 1, "total": 1}, [{"iso": "2030-01-01", "reason": "future_dated"}]
    )
    assert out is None


def test_ai_sample_bounded(monkeypatch):
    _patch_router(monkeypatch, content=_json(severity="high", rationale="r", top_concern="t"))
    anomalies = [{"iso": f"203{i}-01-01", "reason": "future_dated"} for i in range(9)]
    ingest._ai_date_anomaly_assessment({"anomaly_count": 9, "total": 9}, anomalies)
    sent = _Router.last_request.messages[0]["content"]
    # Only the leading _DATE_ANOMALY_LLM_SAMPLE flagged dates reach the model.
    assert sent.count('"reason"') == ingest._DATE_ANOMALY_LLM_SAMPLE


# ── assess_document_dates (orchestrator) ──────────────────────────────────────

def test_assess_returns_none_when_clean(monkeypatch):
    _patch_router(monkeypatch)
    text = "2024-01-01 2024-01-02 2024-01-03 2024-01-04"
    assert ingest.assess_document_dates(text, now_iso=NOW) is None
    assert _Router.last_request is None  # no anomalies -> LLM never called


def test_assess_uses_baseline_when_llm_unavailable(monkeypatch):
    _patch_router(monkeypatch, content="garbage")  # LLM grade rejected
    out = ingest.assess_document_dates("approved 2030-01-01", now_iso=NOW)
    assert out is not None
    assert out["anomaly_count"] == 1
    # Falls back to deterministic baseline severity, never crashes.
    assert out["severity"] in {"low", "medium", "high"}
    assert out["rationale"] == ""


def test_assess_llm_overrides_severity_only(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(severity="high", rationale="future", top_concern="2030-01-01"),
    )
    out = ingest.assess_document_dates("approved 2030-01-01", now_iso=NOW)
    assert out["severity"] == "high"
    assert out["rationale"] == "future"
    # The detection itself (which date is anomalous) stays deterministic.
    assert out["anomalies"][0]["iso"] == "2030-01-01"
    assert out["anomalies"][0]["reason"] == "future_dated"


def test_assess_result_is_json_clean(monkeypatch):
    _patch_router(monkeypatch, content="garbage")
    out = ingest.assess_document_dates("origin 1066-10-14 today 2024-01-01", now_iso=NOW)
    json.dumps(out)  # must serialize for the metadata proposal
