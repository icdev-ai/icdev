"""Tests for the ingest-workload anomaly detection in the DIC ingest orchestrator.

aiify-opp-6097: hardcoded_threshold -> anomaly_detection. The paperless Celery
worker config (src/paperless/celery.py) fenced off runaway tasks with fixed
numeric thresholds (task time/size limits); the scan recommended an
anomaly-detection paradigm. The repo is ephemeral, so the augmentation lands in
the analogous ICDEV subsystem (DIC). The DIC analog of a worker guard is the
ingest pipeline's *workload profile*: every file becomes a background job whose
cost is driven by the file, so a pathological cost profile is detected up front
the way a task time/size limit fences off a runaway. These pin the load-bearing
guarantees:

* ``_detect_workload_anomaly`` flags three pathological profiles against the
  *named* thresholds the scan called out — sparse_extraction (a large file that
  yielded almost no text), sparse_pages (many pages, near-zero text each), and
  payload_explosion (text far larger than the file's bytes) — and is the
  ALWAYS-authoritative offline baseline;
* small files are excluded by ``_WORKLOAD_MIN_FILE_BYTES`` so a short legitimate
  note is never mistaken for a runaway job, and a zero byte size never divides;
* ``_heuristic_workload_severity`` escalates an explosion or an essentially-empty
  large file to ``high`` and is the always-available baseline;
* ``_ai_workload_severity`` only grades severity and degrades silently to
  ``None`` on empty input, blank/garbled output, or any LLM failure;
* ``assess_ingest_workload`` returns ``None`` on an unremarkable profile and
  never lets the LLM override the deterministic *detection*, only the severity.
"""
from __future__ import annotations

import importlib
import json

import pytest

ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
router_mod = importlib.import_module("tools.llm.router")


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


def _rules(out):
    return {f["rule"] for f in out["flags"]}


# ── _detect_workload_anomaly ──────────────────────────────────────────────────

def test_clean_profile_no_flags():
    # A normal 100 KiB doc with healthy text yield is unremarkable.
    out = ingest._detect_workload_anomaly(byte_size=100_000, text_len=80_000, page_count=10)
    assert out["anomaly_count"] == 0
    assert out["flags"] == []
    assert out["baseline_severity"] == "low"


def test_sparse_extraction_flagged_medium():
    # Large file, almost no text -> sparse_extraction. Below the SEVERE file-size
    # band so it stays medium, not high.
    out = ingest._detect_workload_anomaly(byte_size=100_000, text_len=50, page_count=0)
    assert _rules(out) == {"sparse_extraction"}
    assert out["baseline_severity"] == "medium"
    assert out["flags"][0]["metric"] < ingest._WORKLOAD_MIN_CHARS_PER_KB


def test_sparse_extraction_essentially_empty_large_is_high():
    # A big file (>= SEVERE bytes) yielding essentially zero text is the worst
    # case -> escalated to high.
    out = ingest._detect_workload_anomaly(byte_size=300_000, text_len=10, page_count=0)
    assert "sparse_extraction" in _rules(out)
    assert out["baseline_severity"] == "high"


def test_sparse_pages_flagged():
    # Many pages but near-zero text per page (scanned imagery). Healthy chars/KiB
    # keeps sparse_extraction from also firing, isolating the per-page rule.
    out = ingest._detect_workload_anomaly(byte_size=60_000, text_len=400, page_count=20)
    assert "sparse_pages" in _rules(out)
    assert "sparse_extraction" not in _rules(out)
    assert out["chars_per_page"] < ingest._WORKLOAD_MIN_CHARS_PER_PAGE
    assert out["baseline_severity"] == "medium"


def test_payload_explosion_flagged_high():
    # Text far larger than the file's own bytes -> decompression/expansion blow-up.
    out = ingest._detect_workload_anomaly(byte_size=2_000, text_len=20_000, page_count=0)
    assert _rules(out) == {"payload_explosion"}
    assert out["flags"][0]["metric"] > ingest._WORKLOAD_MAX_CHARS_PER_KB
    assert out["baseline_severity"] == "high"


def test_small_text_light_file_not_flagged():
    # A tiny note that is small AND text-light is NOT a runaway job: the size
    # floor excludes it even though its chars/KiB is below the sparse threshold.
    out = ingest._detect_workload_anomaly(byte_size=2_000, text_len=1, page_count=1)
    assert out["anomaly_count"] == 0
    assert out["baseline_severity"] == "low"


def test_zero_byte_size_never_divides():
    out = ingest._detect_workload_anomaly(byte_size=0, text_len=100, page_count=0)
    assert out["chars_per_kb"] == 0.0
    assert out["anomaly_count"] == 0


def test_negative_inputs_clamped():
    out = ingest._detect_workload_anomaly(byte_size=-5, text_len=-9, page_count=-3)
    assert out["byte_size"] == 0
    assert out["text_len"] == 0
    assert out["page_count"] == 0
    assert out["anomaly_count"] == 0


def test_detect_result_is_json_clean():
    out = ingest._detect_workload_anomaly(byte_size=300_000, text_len=10, page_count=0)
    json.dumps(out)  # the proposal must serialize for metadata


def test_named_thresholds_present():
    # The "hardcoded_threshold" the scan flagged is now tunable in one place.
    assert ingest._WORKLOAD_MIN_FILE_BYTES > 0
    assert ingest._WORKLOAD_MIN_CHARS_PER_KB > 0
    assert ingest._WORKLOAD_MIN_CHARS_PER_PAGE > 0
    assert ingest._WORKLOAD_MAX_CHARS_PER_KB > ingest._WORKLOAD_MIN_CHARS_PER_KB
    assert ingest._WORKLOAD_EXPLOSION_MIN_BYTES > 0
    assert ingest._WORKLOAD_SEVERE_CHARS_PER_KB < ingest._WORKLOAD_MIN_CHARS_PER_KB
    assert ingest._WORKLOAD_SEVERE_FILE_BYTES > ingest._WORKLOAD_MIN_FILE_BYTES


# ── _heuristic_workload_severity ──────────────────────────────────────────────

def test_heuristic_severity_bands():
    assert ingest._heuristic_workload_severity([]) == "low"
    assert ingest._heuristic_workload_severity([{"rule": "payload_explosion"}]) == "high"
    assert ingest._heuristic_workload_severity(
        [{"rule": "sparse_extraction", "byte_size": 300_000, "metric": 0.1}]
    ) == "high"
    assert ingest._heuristic_workload_severity(
        [{"rule": "sparse_extraction", "byte_size": 100_000, "metric": 1.0}]
    ) == "medium"
    assert ingest._heuristic_workload_severity([{"rule": "sparse_pages"}]) == "medium"


# ── _ai_workload_severity ─────────────────────────────────────────────────────

def test_ai_returns_normalized_grade(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(severity="high", rationale="empty 1 MB scan", top_concern="sparse_extraction"),
    )
    summary = {
        "anomaly_count": 1, "byte_size": 300_000, "text_len": 10,
        "baseline_severity": "high",
        "flags": [{"rule": "sparse_extraction", "metric": 0.03, "byte_size": 300_000}],
    }
    out = ingest._ai_workload_severity(summary)
    assert out == {
        "severity": "high",
        "rationale": "empty 1 MB scan",
        "top_concern": "sparse_extraction",
    }


def test_ai_no_flags_skips_llm(monkeypatch):
    _patch_router(monkeypatch)
    assert ingest._ai_workload_severity({"anomaly_count": 0, "flags": []}) is None
    assert _Router.last_request is None


def test_ai_invalid_severity_returns_none(monkeypatch):
    _patch_router(monkeypatch, content=_json(severity="apocalyptic", rationale="x", top_concern="y"))
    out = ingest._ai_workload_severity(
        {"anomaly_count": 1, "flags": [{"rule": "payload_explosion"}]}
    )
    assert out is None


def test_ai_strips_fenced_block(monkeypatch):
    body = _json(severity="medium", rationale="scanned pages", top_concern="sparse_pages")
    _patch_router(monkeypatch, content=f"```json\n{body}\n```")
    out = ingest._ai_workload_severity(
        {"anomaly_count": 1, "flags": [{"rule": "sparse_pages"}]}
    )
    assert out["severity"] == "medium"


def test_ai_garbled_returns_none(monkeypatch):
    _patch_router(monkeypatch, content="not json")
    out = ingest._ai_workload_severity(
        {"anomaly_count": 1, "flags": [{"rule": "payload_explosion"}]}
    )
    assert out is None


def test_ai_llm_failure_returns_none(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    out = ingest._ai_workload_severity(
        {"anomaly_count": 1, "flags": [{"rule": "payload_explosion"}]}
    )
    assert out is None


# ── assess_ingest_workload (orchestrator) ─────────────────────────────────────

def test_assess_returns_none_when_clean(monkeypatch):
    _patch_router(monkeypatch)
    assert ingest.assess_ingest_workload(byte_size=100_000, text_len=80_000, page_count=10) is None
    assert _Router.last_request is None  # nothing anomalous -> LLM never called


def test_assess_uses_baseline_when_llm_unavailable(monkeypatch):
    _patch_router(monkeypatch, content="garbage")  # LLM grade rejected
    out = ingest.assess_ingest_workload(byte_size=2_000, text_len=20_000, page_count=0)
    assert out is not None
    assert _rules(out) == {"payload_explosion"}
    assert out["severity"] == "high"  # deterministic baseline survives
    assert out["rationale"] == ""


def test_assess_llm_overrides_severity_only(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(severity="low", rationale="known archive export", top_concern="payload_explosion"),
    )
    out = ingest.assess_ingest_workload(byte_size=2_000, text_len=20_000, page_count=0)
    assert out["severity"] == "low"
    assert out["rationale"] == "known archive export"
    # The detection itself (which rule fired, the metric) stays deterministic.
    assert _rules(out) == {"payload_explosion"}
    assert out["flags"][0]["metric"] > ingest._WORKLOAD_MAX_CHARS_PER_KB


def test_assess_result_is_json_clean(monkeypatch):
    _patch_router(monkeypatch, content="garbage")
    out = ingest.assess_ingest_workload(byte_size=300_000, text_len=10, page_count=0)
    json.dumps(out)  # must serialize for the metadata proposal
