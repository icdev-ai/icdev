# CUI // SP-CTI
"""Regression tests for the recalibrated AI-ify scoring + verdict engine.

These lock in the fix for the "every opportunity scores ~40%" bug: before, the
scanner emitted no component fields so every composite pinned at ~0.44. Now
components are derived from observable signals and each opportunity gets a
3-tier AI-readiness verdict with disqualifier guards.
"""
from __future__ import annotations

from tools.aiify import opportunity_scorer as s


def _ctx():
    return {"il_level": "il4"}


def test_scores_vary_across_pattern_types():
    big = s.score_and_assess(
        {"pattern_type": "large_rule_table", "module_path": "services/pricing.py",
         "line_start": 10, "line_end": 90, "pattern_detail": {"key_count": 40, "func_loc": 80}},
        _ctx(),
    )
    small = s.score_and_assess(
        {"pattern_type": "hardcoded_threshold", "module_path": "util/helpers.py",
         "line_start": 1, "line_end": 12, "pattern_detail": {"func_loc": 11}},
        _ctx(),
    )
    assert big["composite_score"] != small["composite_score"]
    # The old bug pinned everything at ~0.44; assert we've moved off that constant.
    assert abs(big["composite_score"] - 0.44) > 0.05


def test_il_model_exists_defaults_true():
    r = s.derive_components(
        {"pattern_type": "nested_conditionals", "module_path": "a.py",
         "ai_paradigm": "ml_classifier", "pattern_detail": {"max_depth": 5}},
        _ctx(),
    )
    assert r["il_model_exists"] == 1.0


def test_airgap_without_local_model_zeroes_il_model():
    r = s.derive_components(
        {"pattern_type": "nested_conditionals", "module_path": "a.py",
         "ai_paradigm": "ml_classifier", "pattern_detail": {"max_depth": 5}},
        {"il_level": "il6", "airgap": True},
    )
    assert r["il_model_exists"] == 0.0


def test_trivial_function_disqualified():
    r = s.score_and_assess(
        {"pattern_type": "nested_conditionals", "module_path": "app/view.py",
         "line_start": 1, "line_end": 4, "pattern_detail": {"max_depth": 2, "func_loc": 3}},
        _ctx(),
    )
    assert r["ai_readiness"] == "not_suitable"
    assert "trivial_function" in r["disqualifiers"]


def test_security_path_disqualified():
    r = s.score_and_assess(
        {"pattern_type": "nested_conditionals", "module_path": "auth/login_handler.py",
         "line_start": 1, "line_end": 60, "pattern_detail": {"max_depth": 6, "func_loc": 55}},
        _ctx(),
    )
    assert r["ai_readiness"] == "not_suitable"
    assert "security_sensitive" in r["disqualifiers"]


def test_high_value_opportunity_is_ready_or_partial():
    r = s.score_and_assess(
        {"pattern_type": "keyword_list_search", "module_path": "api/search_service.py",
         "line_start": 1, "line_end": 50, "pattern_detail": {"count": 20, "func_loc": 49}},
        _ctx(),
    )
    assert r["ai_readiness"] in ("ai_ready", "partial")
    assert r["pros"] and r["cons"]


def test_category_mapping():
    assert s.categorize({"pattern_type": "scheduled_cron"}) == "automation"
    assert s.categorize({"pattern_type": "large_rule_table"}) == "innovation"
    assert s.categorize({"pattern_type": "regex_user_input"}) == "enhancement"


def test_roll_up_empty_is_not_suitable():
    out = s.roll_up_scan_verdict([])
    assert out["overall_ai_readiness"] == "not_suitable"


def test_roll_up_bands():
    ready = [{"ai_readiness": "ai_ready", "composite_score": 0.8, "module_path": "x"}]
    mixed = ready + [{"ai_readiness": "not_suitable", "composite_score": 0.2, "module_path": "y"}]
    assert s.roll_up_scan_verdict(ready)["overall_ai_readiness"] == "ai_ready"
    assert s.roll_up_scan_verdict(mixed)["overall_ai_readiness"] == "ai_ready"
    all_bad = [{"ai_readiness": "not_suitable", "composite_score": 0.1, "module_path": "z"}]
    assert s.roll_up_scan_verdict(all_bad)["overall_ai_readiness"] == "not_suitable"


def test_score_opportunity_unchanged_for_explicit_components():
    r = s.score_opportunity(
        {"usage_freq": 1.0, "task_complexity": 1.0, "automation_deficit": 1.0,
         "data_avail": 1.0, "il_model_exists": 1.0, "integration_complexity": 0.0,
         "reversibility": 1.0, "compliance_impact": 0.0, "dep_complexity": 0.0},
        _ctx(),
    )
    assert r["composite_score"] > 0.9
