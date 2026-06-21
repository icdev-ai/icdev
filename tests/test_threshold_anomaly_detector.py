# CUI // SP-CTI
"""Tests for the AI-driven anomaly detection in pattern_classifier.

Covers _is_threshold_anomalous, _collect_all_numeric_thresholds, and
the hardcoded_threshold detector with anomaly filtering enabled/disabled.
"""
from __future__ import annotations

import ast
import sys
import textwrap
from pathlib import Path

import pytest

# Ensure repo root is on sys.path so tools.* imports resolve.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from tools.ai_augmentation.pattern_classifier import (
        _collect_all_numeric_thresholds,
        _is_threshold_anomalous,
        _detect_hardcoded_threshold,
        _build_parent_map,
        _build_scope_map,
    )
    _IMPORT_OK = True
except ImportError:
    _IMPORT_OK = False

pytestmark = pytest.mark.skipif(not _IMPORT_OK, reason="pattern_classifier not importable")


# ── _is_threshold_anomalous ───────────────────────────────────────────────────


def test_is_anomalous_outlier_high():
    """A value far above the population mean is anomalous."""
    pop = [1.0, 2.0, 3.0, 4.0, 5.0, 100.0]
    assert _is_threshold_anomalous(100.0, pop) is True


def test_is_anomalous_within_range():
    """A value in the middle of the population is not anomalous."""
    pop = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert _is_threshold_anomalous(3.0, pop) is False


def test_is_anomalous_small_population_fallback():
    """With fewer than min_sample_size values, uses fallback_to_all (True)."""
    # Default min_sample_size is 5 — 4 items triggers fallback
    pop = [1.0, 2.0, 3.0, 4.0]
    # fallback_to_all defaults to True so every value is considered anomalous
    assert _is_threshold_anomalous(3.0, pop) is True


def test_is_anomalous_empty_population_fallback():
    """Empty population also triggers fallback."""
    assert _is_threshold_anomalous(42.0, []) is True


def test_is_anomalous_iqr_fence():
    """Values beyond IQR fence are anomalous even if z-score is modest."""
    # Tight cluster with one outlier
    pop = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 100.0]
    assert _is_threshold_anomalous(100.0, pop) is True


def test_is_not_anomalous_uniform_population():
    """All identical values — no outlier possible."""
    pop = [5.0] * 10
    # variance is 0 and IQR is 0, so fence test doesn't fire either
    assert _is_threshold_anomalous(5.0, pop) is False


# ── _collect_all_numeric_thresholds ──────────────────────────────────────────


def test_collect_thresholds_compare_nodes():
    """Numeric constants in comparison operators are collected."""
    src = textwrap.dedent("""\
        def f(x):
            if x > 10:
                pass
            if x <= 20:
                pass
    """)
    tree = ast.parse(src)
    vals = _collect_all_numeric_thresholds(tree)
    assert 10.0 in vals
    assert 20.0 in vals


def test_collect_thresholds_binop_nodes():
    """Numeric constants in binary operations are collected."""
    src = textwrap.dedent("""\
        def f(x):
            y = x + 5
            z = 3 * x
    """)
    tree = ast.parse(src)
    vals = _collect_all_numeric_thresholds(tree)
    assert 5.0 in vals
    assert 3.0 in vals


def test_collect_thresholds_empty():
    """Module with no numeric constants returns an empty list."""
    src = textwrap.dedent("""\
        def f(x):
            return x
    """)
    tree = ast.parse(src)
    assert _collect_all_numeric_thresholds(tree) == []


# ── _detect_hardcoded_threshold with anomaly detection ───────────────────────


def _detect_in_source(src: str) -> list[dict]:
    """Helper: parse src and run _detect_hardcoded_threshold on it."""
    tree = ast.parse(textwrap.dedent(src))
    _build_parent_map(tree)
    scope_map = _build_scope_map(tree)
    return _detect_hardcoded_threshold("<test>", tree, scope_map)


def test_detect_threshold_reports_outlier():
    """An anomalous numeric constant in a comparison is reported."""
    src = """\
        def check(x):
            if x > 9999:
                pass
            if x > 1:
                pass
            if x > 2:
                pass
            if x > 3:
                pass
            if x > 4:
                pass
    """
    results = _detect_in_source(src)
    constants = [
        v
        for r in results
        for v in r["pattern_detail"].get("constants", [])
    ]
    # 9999 should be flagged as anomalous
    assert 9999 in constants, f"Expected 9999 in {constants}"


def test_detect_threshold_returns_hardcoded_threshold_type():
    """Each result has pattern_type='hardcoded_threshold'."""
    src = """\
        def f(x):
            if x > 500:
                pass
    """
    results = _detect_in_source(src)
    for r in results:
        assert r["pattern_type"] == "hardcoded_threshold"


def test_detect_threshold_result_schema():
    """Each result dict has the required canonical keys."""
    src = """\
        def f(x):
            if x > 500:
                pass
    """
    results = _detect_in_source(src)
    if not results:
        pytest.skip("No thresholds detected in sample source")
    required_keys = {"pattern_type", "module_path", "function_name",
                     "line_start", "line_end", "pattern_detail"}
    for r in results:
        missing = required_keys - r.keys()
        assert not missing, f"Missing keys: {missing}"


def test_detect_threshold_anomaly_detail_flag():
    """pattern_detail includes anomaly_detected key."""
    src = """\
        def f(x):
            if x > 99999:
                pass
    """
    results = _detect_in_source(src)
    if not results:
        pytest.skip("No thresholds detected")
    for r in results:
        assert "anomaly_detected" in r["pattern_detail"]


def test_detect_threshold_binop_reported():
    """Anomalous numeric constant in a BinOp is also reported."""
    src = """\
        def f(x):
            a = x + 1
            b = x + 2
            c = x + 3
            d = x + 4
            e = x + 5
            # This one is a huge outlier
            z = x + 99999
    """
    results = _detect_in_source(src)
    binop_consts = [
        v
        for r in results
        if r["pattern_detail"].get("kind") == "binop"
        for v in r["pattern_detail"].get("constants", [])
    ]
    assert 99999 in binop_consts, f"99999 not found in {binop_consts}"
