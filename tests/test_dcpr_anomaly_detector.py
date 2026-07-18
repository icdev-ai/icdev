# CUI // SP-CTI
"""Behavior tests for tools/data_canvas/anomaly_detector.py (dcpr-qa-01).

Covers the detection *logic* (not the DB connection). ``detect_anomalies``
prefers an LLM but falls back to deterministic rule-based heuristics; these
tests force the rule-based path (by neutering the LLM provider lookup) so the
computed findings are deterministic, and also exercise the pure helpers
``_parse_llm_response`` and ``_compute_overall_risk``.
"""

import importlib

import pytest

from tools.data_canvas import anomaly_detector as ad


@pytest.fixture(autouse=True)
def _force_rule_based(monkeypatch):
    """Neuter the LLM path so detect_anomalies uses rule-based heuristics.

    detect_anomalies does ``from tools.llm.router import LLMRouter`` at call
    time; the ``tools.*`` and ``icdev.tools.*`` namespaces are distinct module
    objects (backward-compat shim), so patch the exact module the function
    imports from via importlib+setattr (repo shim-aware monkeypatch pattern).

    - Provider lookup returns no provider ⇒ the LLM branch is skipped.
    - _record_decision is stubbed so no DB write is attempted (the shared
      icdev.db may be held open by a live dashboard).
    """
    class _NoProviderRouter:
        def __init__(self, *a, **k):
            pass

        def get_provider_for_function(self, _fn):
            return (None, None, None)

    router_mod = importlib.import_module("tools.llm.router")
    monkeypatch.setattr(router_mod, "LLMRouter", _NoProviderRouter)
    monkeypatch.setattr(ad, "_record_decision", lambda **_k: None)


# ── detect_anomalies (rule-based) ─────────────────────────────────────────────

def test_high_null_rate_flagged_high():
    profile = {"email": {"null_pct": 55.0, "distinct_count": 10}}
    result = ad.detect_anomalies(profile)
    types = {f["finding_type"] for f in result["findings"]}
    assert "high_null_rate" in types
    null_finding = next(f for f in result["findings"] if f["finding_type"] == "high_null_rate")
    assert null_finding["severity"] == "high"
    assert result["overall_risk"] == "high"
    assert result["classification"] == "CUI"
    assert result["analyzed_at"]


def test_moderate_null_rate_flagged_medium():
    profile = {"note": {"null_pct": 25.0, "distinct_count": 5}}
    result = ad.detect_anomalies(profile)
    null_finding = next(
        (f for f in result["findings"]
         if f["finding_type"] == "high_null_rate" and f["column"] == "note"),
        None,
    )
    assert null_finding is not None
    assert null_finding["severity"] == "medium"


def test_zero_cardinality_flagged():
    profile = {"status": {"null_pct": 0.0, "distinct_count": 0}}
    result = ad.detect_anomalies(profile)
    types = {f["finding_type"] for f in result["findings"]}
    assert "zero_cardinality" in types


def test_pii_column_name_flagged():
    profile = {"ssn": {"null_pct": 0.0, "distinct_count": 100}}
    result = ad.detect_anomalies(profile)
    pii = [f for f in result["findings"] if f["finding_type"] == "potential_pii"]
    assert pii
    assert pii[0]["severity"] == "medium"


def test_clean_profile_has_no_findings():
    profile = {"amount": {"null_pct": 1.0, "distinct_count": 500, "min": 0, "max": 100}}
    result = ad.detect_anomalies(profile)
    assert result["findings"] == []
    assert result["overall_risk"] == "none"


def test_outlier_range_flagged():
    # min/max span far beyond the common (top) values.
    profile = {
        "score": {
            "null_pct": 0.0,
            "distinct_count": 50,
            "min": 0,
            "max": 1000,
            "top_values": [10, 12, 11],
        }
    }
    result = ad.detect_anomalies(profile)
    types = {f["finding_type"] for f in result["findings"]}
    assert "outlier_range" in types


# ── pure helpers ──────────────────────────────────────────────────────────────

def test_compute_overall_risk_takes_max_severity():
    findings = [
        {"severity": "low"},
        {"severity": "high"},
        {"severity": "medium"},
    ]
    assert ad._compute_overall_risk(findings) == "high"
    assert ad._compute_overall_risk([]) == "none"


def test_parse_llm_response_strips_code_fence():
    raw = '```json\n{"findings": [{"column": "x", "severity": "high", ' \
          '"finding_type": "high_null_rate", "description": "d"}]}\n```'
    parsed = ad._parse_llm_response(raw)
    assert len(parsed) == 1
    assert parsed[0]["column"] == "x"
    assert parsed[0]["severity"] == "high"


def test_parse_llm_response_rejects_garbage():
    assert ad._parse_llm_response("not json at all") == []
    assert ad._parse_llm_response("") == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
