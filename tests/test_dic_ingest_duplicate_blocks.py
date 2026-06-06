"""Tests for the duplicate-content anomaly detection in the DIC ingest orchestrator.

aiify-opp-5984: hardcoded_threshold -> anomaly_detection. The paperless page
deduper (docker/rootfs/usr/local/bin/deduplicate.py) stripped duplicate content
out of a scanned document with fixed rules; the scan recommended an
anomaly-detection paradigm. The repo is ephemeral, so the augmentation lands in
the analogous ICDEV subsystem (DIC). DIC already dedups whole files across a
collection by exact content hash; this complementary layer flags the
*intra*-document case (the same paragraph/page repeated within one file). These
pin the load-bearing guarantees:

* ``_segment_blocks`` deterministically splits text on page breaks / blank lines,
  normalizes each block, and drops blocks shorter than ``_DUP_BLOCK_MIN_CHARS``
  so running headers/footers and page numbers are not mistaken for duplicates;
* ``_detect_duplicate_blocks`` flags blocks recurring at least
  ``_DUP_BLOCK_MIN_REPEATS`` times against the *named* thresholds (the magic
  numbers the scan called out), escalates statistical over-repetition outliers,
  and is the ALWAYS-authoritative offline baseline;
* outlier escalation only fires with at least ``_DUP_ANOMALY_MIN_SAMPLE``
  distinct blocks and a non-degenerate spread;
* ``_ai_dup_block_assessment`` only grades severity and degrades silently to
  ``None`` on empty input, blank/garbled output, or any LLM failure;
* ``assess_duplicate_blocks`` returns ``None`` when nothing recurs and never lets
  the LLM override the deterministic *detection*, only the severity label.
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


# A substantial (>= _DUP_BLOCK_MIN_CHARS) paragraph used as a repeatable block.
PARA_A = (
    "This invoice covers professional services rendered under contract "
    "FA8750-24-C-0001 for the third quarter of the fiscal year."
)
PARA_B = (
    "Payment is due net thirty days from the invoice date via electronic "
    "funds transfer to the account on file with the contracting office."
)
PARA_C = (
    "All work was performed in accordance with the approved statement of work "
    "and the applicable quality assurance surveillance plan provisions."
)


def _doc(*paras):
    return "\n\n".join(paras)


# ── _segment_blocks ───────────────────────────────────────────────────────────

def test_segments_on_blank_lines_and_form_feed():
    text = f"{PARA_A}\n\n{PARA_B}\f{PARA_C}"
    blocks = ingest._segment_blocks(text)
    norms = [b["norm"] for b in blocks]
    assert len(blocks) == 3
    assert " ".join(PARA_A.split()).lower() in norms


def test_short_blocks_dropped():
    # Page numbers / banners are below the length floor and must be ignored.
    text = f"Page 1\n\n{PARA_A}\n\nCONFIDENTIAL\n\nPage 2"
    blocks = ingest._segment_blocks(text)
    assert len(blocks) == 1
    assert all(len(b["norm"]) >= ingest._DUP_BLOCK_MIN_CHARS for b in blocks)


def test_normalization_collapses_whitespace():
    # Cosmetic re-flow (extra spaces / newlines inside a block) must not hide a
    # duplicate — both copies normalize identically.
    reflowed = PARA_A.replace(" ", "   ").replace("for", "for\n")
    blocks = ingest._segment_blocks(f"{PARA_A}\n\n{reflowed}")
    assert blocks[0]["norm"] == blocks[1]["norm"]


def test_empty_text_returns_empty_list():
    assert ingest._segment_blocks("") == []
    assert ingest._segment_blocks("short\n\nalso short") == []


def test_only_leading_window_scanned():
    filler = "x" * (ingest._DUP_INPUT_CHARS + 100)
    text = filler + "\n\n" + PARA_A
    # The paragraph sits past the input budget, so it is never seen as a block.
    norms = {b["norm"] for b in ingest._segment_blocks(text)}
    assert " ".join(PARA_A.split()).lower() not in norms


# ── _detect_duplicate_blocks ──────────────────────────────────────────────────

def test_repeated_block_flagged():
    blocks = ingest._segment_blocks(_doc(PARA_A, PARA_B, PARA_A))
    out = ingest._detect_duplicate_blocks(blocks)
    assert out["duplicate_clusters"] == 1
    assert out["clusters"][0]["repeats"] == 2
    assert out["clusters"][0]["reason"] == "duplicate_block"
    assert out["duplicate_fraction"] > 0


def test_no_repeats_clean_baseline():
    blocks = ingest._segment_blocks(_doc(PARA_A, PARA_B, PARA_C))
    out = ingest._detect_duplicate_blocks(blocks)
    assert out["duplicate_clusters"] == 0
    assert out["duplicate_fraction"] == 0.0
    assert out["baseline_severity"] == "low"


def test_below_min_repeats_not_flagged():
    # A block appearing once is not a duplicate.
    blocks = ingest._segment_blocks(_doc(PARA_A, PARA_B))
    out = ingest._detect_duplicate_blocks(blocks)
    assert out["duplicate_clusters"] == 0


def test_statistical_over_repetition_escalated():
    # One block hammered many times against a backdrop of many distinct blocks is
    # a statistical outlier -> escalated reason. Enough distinct singletons keep
    # the per-block mean/stdev low so the spike clears mean + k*stdev (a lone
    # outlier self-inflates stdev, so the backdrop has to be wide).
    distinct = [
        (f"Distinct paragraph number {i} of the report, written out at a length "
         f"comfortably above the minimum block character floor for detection.")
        for i in range(6)
    ]
    spam_para = ("This duplicated page block was emitted repeatedly by a scanner "
                 "double-feed and recurs far more than any other content here.")
    blocks = ingest._segment_blocks(_doc(*distinct, *([spam_para] * 10)))
    out = ingest._detect_duplicate_blocks(blocks)
    top = out["clusters"][0]
    assert top["repeats"] == 10
    assert top["reason"] == "anomalous_repeat"
    assert out["anomaly_count"] >= 1


def test_no_outlier_below_min_sample():
    # Two distinct blocks (one repeated) is below _DUP_ANOMALY_MIN_SAMPLE; the
    # repeat is still a duplicate, just never escalated to a statistical outlier.
    blocks = ingest._segment_blocks(_doc(PARA_A, PARA_A, PARA_B))
    out = ingest._detect_duplicate_blocks(blocks)
    assert out["duplicate_clusters"] == 1
    assert all(c["reason"] == "duplicate_block" for c in out["clusters"])


def test_empty_blocks_returns_clean_baseline():
    out = ingest._detect_duplicate_blocks([])
    assert out["total_blocks"] == 0
    assert out["duplicate_clusters"] == 0
    assert out["baseline_severity"] == "low"


def test_detect_result_is_json_clean():
    blocks = ingest._segment_blocks(_doc(PARA_A, PARA_A))
    out = ingest._detect_duplicate_blocks(blocks)
    json.dumps(out)  # the proposal must serialize for metadata


def test_clusters_sorted_by_repeats_desc():
    text = _doc(PARA_A, PARA_A, PARA_A, PARA_B, PARA_B, PARA_C)
    out = ingest._detect_duplicate_blocks(ingest._segment_blocks(text))
    reps = [c["repeats"] for c in out["clusters"]]
    assert reps == sorted(reps, reverse=True)
    assert reps[0] == 3  # PARA_A


def test_named_thresholds_present():
    # The "hardcoded_threshold" the scan flagged is now tunable in one place.
    assert ingest._DUP_BLOCK_MIN_CHARS > 0
    assert ingest._DUP_BLOCK_MIN_REPEATS >= 2
    assert ingest._DUP_ANOMALY_STDEV_K > 0
    assert ingest._DUP_ANOMALY_MIN_SAMPLE >= 2
    assert 0 < ingest._DUP_SEV_MEDIUM_FRACTION < ingest._DUP_SEV_HIGH_FRACTION <= 1


# ── _heuristic_dup_severity ───────────────────────────────────────────────────

def test_heuristic_severity_bands():
    assert ingest._heuristic_dup_severity(0.0, 0) == "low"
    assert ingest._heuristic_dup_severity(0.9, 0) == "low"  # no clusters -> low
    assert ingest._heuristic_dup_severity(0.05, 1) == "low"
    assert ingest._heuristic_dup_severity(0.20, 1) == "medium"
    assert ingest._heuristic_dup_severity(0.50, 1) == "high"


# ── _ai_dup_block_assessment ──────────────────────────────────────────────────

def test_ai_returns_normalized_grade(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(severity="high", rationale="whole page duplicated", top_concern="invoice page"),
    )
    summary = {"duplicate_clusters": 1, "total_blocks": 3, "baseline_severity": "medium"}
    out = ingest._ai_dup_block_assessment(summary, [{"snippet": "x", "repeats": 2, "reason": "duplicate_block"}])
    assert out == {
        "severity": "high",
        "rationale": "whole page duplicated",
        "top_concern": "invoice page",
    }


def test_ai_no_clusters_skips_llm(monkeypatch):
    _patch_router(monkeypatch)
    assert ingest._ai_dup_block_assessment({"duplicate_clusters": 0}, []) is None
    assert _Router.last_request is None


def test_ai_invalid_severity_returns_none(monkeypatch):
    _patch_router(monkeypatch, content=_json(severity="apocalyptic", rationale="x", top_concern="y"))
    out = ingest._ai_dup_block_assessment(
        {"duplicate_clusters": 1, "total_blocks": 2}, [{"snippet": "x", "repeats": 2, "reason": "duplicate_block"}]
    )
    assert out is None


def test_ai_strips_fenced_block(monkeypatch):
    body = _json(severity="low", rationale="quoted twice", top_concern="clause")
    _patch_router(monkeypatch, content=f"```json\n{body}\n```")
    out = ingest._ai_dup_block_assessment(
        {"duplicate_clusters": 1, "total_blocks": 5}, [{"snippet": "x", "repeats": 2, "reason": "duplicate_block"}]
    )
    assert out["severity"] == "low"


def test_ai_garbled_returns_none(monkeypatch):
    _patch_router(monkeypatch, content="not json")
    out = ingest._ai_dup_block_assessment(
        {"duplicate_clusters": 1, "total_blocks": 2}, [{"snippet": "x", "repeats": 2, "reason": "duplicate_block"}]
    )
    assert out is None


def test_ai_llm_failure_returns_none(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k):
            pass

        def invoke(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr(router_mod, "LLMRouter", _Boom)
    out = ingest._ai_dup_block_assessment(
        {"duplicate_clusters": 1, "total_blocks": 2}, [{"snippet": "x", "repeats": 2, "reason": "duplicate_block"}]
    )
    assert out is None


def test_ai_sample_bounded(monkeypatch):
    _patch_router(monkeypatch, content=_json(severity="high", rationale="r", top_concern="t"))
    clusters = [{"snippet": f"block {i}", "repeats": 2, "reason": "duplicate_block"} for i in range(9)]
    ingest._ai_dup_block_assessment({"duplicate_clusters": 9, "total_blocks": 20}, clusters)
    sent = _Router.last_request.messages[0]["content"]
    # Only the leading _DUP_LLM_SAMPLE clusters reach the model.
    assert sent.count('"reason"') == ingest._DUP_LLM_SAMPLE


# ── assess_duplicate_blocks (orchestrator) ────────────────────────────────────

def test_assess_returns_none_when_clean(monkeypatch):
    _patch_router(monkeypatch)
    assert ingest.assess_duplicate_blocks(_doc(PARA_A, PARA_B, PARA_C)) is None
    assert _Router.last_request is None  # no duplicates -> LLM never called


def test_assess_uses_baseline_when_llm_unavailable(monkeypatch):
    _patch_router(monkeypatch, content="garbage")  # LLM grade rejected
    out = ingest.assess_duplicate_blocks(_doc(PARA_A, PARA_B, PARA_A))
    assert out is not None
    assert out["duplicate_clusters"] == 1
    assert out["severity"] in {"low", "medium", "high"}
    assert out["rationale"] == ""


def test_assess_llm_overrides_severity_only(monkeypatch):
    _patch_router(
        monkeypatch,
        content=_json(severity="high", rationale="page duplicated", top_concern="invoice"),
    )
    out = ingest.assess_duplicate_blocks(_doc(PARA_A, PARA_B, PARA_A))
    assert out["severity"] == "high"
    assert out["rationale"] == "page duplicated"
    # The detection itself (which block recurs, how often) stays deterministic.
    assert out["clusters"][0]["repeats"] == 2
    assert out["clusters"][0]["reason"] == "duplicate_block"


def test_assess_result_is_json_clean(monkeypatch):
    _patch_router(monkeypatch, content="garbage")
    out = ingest.assess_duplicate_blocks(_doc(PARA_A, PARA_A, PARA_B))
    json.dumps(out)  # must serialize for the metadata proposal
