# CUI // SP-CTI
"""Tests: TRUST coverage coherence check (xcut-01)."""

import importlib

cc = importlib.import_module("tools.workflow.coherence_checker")


def test_registered_in_checks():
    assert "trust_coverage" in cc.CHECK_REGISTRY
    assert cc.CHECK_REGISTRY["trust_coverage"] is cc.check_trust_coverage


def test_trust_invariants_present():
    r = cc.check_trust_coverage()
    assert r.status == "pass", f"missing TRUST invariants: {r.missing}"
    assert r.missing == []
    assert r.check_id == "trust_coverage"


def test_expected_covers_grounding_and_toggles():
    r = cc.check_trust_coverage()
    joined = " ".join(r.expected)
    assert "content_grounding.py" in joined
    assert "citation_grounding.py" in joined
    assert "fail_closed" in joined
    assert "mask_at_ingestion" in joined
    # both trees represented
    assert any(e.startswith("icdev/tools/quality/") for e in r.expected)
