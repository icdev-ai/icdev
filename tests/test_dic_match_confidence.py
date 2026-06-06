"""Tests for the DIC chat match-confidence gate.

aiify-opp-6034: hardcoded_threshold -> anomaly_detection. The DIC chat path
originally decided whether the top retrieval was a confident direct-lookup match
with a single hardcoded cutoff (``top_score >= 0.4``) — the same fixed-threshold
matching pattern flagged in the paperless ``src/documents/matching.py`` source.

``_is_confident_match`` replaces that magic number with the floor as the
authoritative baseline plus a deterministic high-side outlier (anomaly) test.
These tests pin the load-bearing guarantees:

* the configured floor still decides confidence exactly as the original cutoff
  did — clearing the floor is always confident, and the gate is pure stdlib
  (no LLM, air-gap safe);
* a top score below the floor but standing decisively apart from a weak field
  is promoted to confident via the z-score outlier test;
* a top score barely ahead of a tight cluster, or below the absolute hard
  floor, or with too small a field to judge, is NOT confident.
"""
from __future__ import annotations

import importlib

bp = importlib.import_module("tools.document_intelligence.blueprint")


def test_empty_scores_not_confident():
    assert bp._is_confident_match([]) is False


def test_top_clears_floor_is_confident():
    # At/above the floor → confident, regardless of the trailing field.
    assert bp._is_confident_match([0.4, 0.39, 0.38]) is True
    assert bp._is_confident_match([0.85, 0.1, 0.05]) is True


def test_single_result_below_floor_not_confident():
    # No trailing field to judge separation → not confident below the floor.
    assert bp._is_confident_match([0.39]) is False


def test_below_abs_floor_never_confident():
    # Even a huge separation cannot rescue a top score under the hard floor.
    assert bp._is_confident_match([0.2, 0.001, 0.001, 0.001]) is False


def test_below_floor_but_high_side_outlier_is_confident():
    # 0.38 < floor (0.4) but towers over a field of ~0.10 → clear winner.
    assert bp._is_confident_match([0.38, 0.10, 0.09, 0.11, 0.10]) is True


def test_below_floor_tight_cluster_not_confident():
    # 0.39 barely ahead of a tight cluster near 0.38 → weak match, not confident.
    assert bp._is_confident_match([0.39, 0.38, 0.38, 0.37, 0.38]) is False


def test_below_floor_too_few_trailing_not_confident():
    # Only one trailing score → cannot judge separation → not confident.
    assert bp._is_confident_match([0.38, 0.10]) is False


def test_degenerate_identical_trailing_requires_absolute_gap():
    min_gap = bp._MATCH_OUTLIER_MIN_GAP
    # Identical trailing scores (std == 0): the absolute-margin guard is the only
    # test. mean chosen so the top stays within [hard_floor, floor) in both cases.
    mean = 0.16
    big_gap_top = mean + min_gap  # exactly meets the required absolute margin
    assert bp._is_confident_match([big_gap_top, mean, mean, mean]) is True
    small_gap_top = mean + min_gap - 0.01  # just under the required margin
    assert bp._is_confident_match([small_gap_top, mean, mean, mean]) is False


def test_floor_is_env_configurable(monkeypatch):
    monkeypatch.setenv("DIC_MATCH_SCORE_FLOOR", "0.6")
    reloaded = importlib.reload(bp)
    try:
        assert reloaded._MATCH_SCORE_FLOOR == 0.6
        # 0.5 now sits below the raised floor.
        assert reloaded._is_confident_match([0.5, 0.49, 0.48]) is False
    finally:
        monkeypatch.delenv("DIC_MATCH_SCORE_FLOOR", raising=False)
        importlib.reload(bp)
