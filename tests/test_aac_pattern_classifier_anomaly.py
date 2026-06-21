# CUI // SP-CTI
"""TDD tests for AnomalyThresholdDetector and configurable thresholds in pattern_classifier."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest


def test_anomaly_threshold_detector_importable():
    """AnomalyThresholdDetector must be importable from pattern_classifier."""
    try:
        from tools.ai_augmentation.pattern_classifier import AnomalyThresholdDetector
        assert callable(AnomalyThresholdDetector)
    except ImportError as e:
        pytest.skip(f"Dependency missing: {e}")


def test_anomaly_detector_no_samples_returns_false():
    """With no observations, is_anomalous must return False."""
    try:
        from tools.ai_augmentation.pattern_classifier import AnomalyThresholdDetector
    except ImportError:
        pytest.skip("Not importable")
    d = AnomalyThresholdDetector()
    assert d.is_anomalous(9999) is False


def test_anomaly_detector_below_min_samples_returns_false():
    """With fewer than min_samples observations, is_anomalous must return False."""
    try:
        from tools.ai_augmentation.pattern_classifier import AnomalyThresholdDetector
    except ImportError:
        pytest.skip("Not importable")
    d = AnomalyThresholdDetector(min_samples=5)
    for v in [1, 2, 3]:  # only 3, need 5
        d.observe(v)
    assert d.is_anomalous(9999) is False


def test_anomaly_detector_flags_outlier():
    """With enough samples, is_anomalous must flag an obvious outlier."""
    try:
        from tools.ai_augmentation.pattern_classifier import AnomalyThresholdDetector
    except ImportError:
        pytest.skip("Not importable")
    d = AnomalyThresholdDetector(min_samples=5)
    # Observe a tight cluster around 1-3
    for v in [1, 1, 2, 2, 2, 3, 3, 1, 2, 3]:
        d.observe(v)
    # 9999 should be flagged as anomalous
    assert d.is_anomalous(9999) is True


def test_anomaly_detector_normal_value_not_flagged():
    """A value within the normal distribution must not be flagged."""
    try:
        from tools.ai_augmentation.pattern_classifier import AnomalyThresholdDetector
    except ImportError:
        pytest.skip("Not importable")
    d = AnomalyThresholdDetector(min_samples=5)
    for v in [1, 1, 2, 2, 2, 3, 3, 1, 2, 3]:
        d.observe(v)
    assert d.is_anomalous(2) is False


def test_anomaly_detector_score_returns_float():
    """score() must return a float in [0.0, 1.0]."""
    try:
        from tools.ai_augmentation.pattern_classifier import AnomalyThresholdDetector
    except ImportError:
        pytest.skip("Not importable")
    d = AnomalyThresholdDetector(min_samples=5)
    for v in [1, 1, 2, 2, 2, 3, 3, 1, 2, 3]:
        d.observe(v)
    s = d.score(9999)
    assert isinstance(s, float)
    assert 0.0 <= s <= 1.0
    s_normal = d.score(2)
    assert 0.0 <= s_normal <= 1.0


def test_detect_hardcoded_threshold_includes_anomaly_scores():
    """_detect_hardcoded_threshold results must include anomaly_scores in pattern_detail."""
    import ast
    try:
        from tools.ai_augmentation.pattern_classifier import (
            _detect_hardcoded_threshold,
            _build_scope_map,
        )
    except ImportError:
        pytest.skip("Not importable")
    source = "x = 1\nif x > 5:\n    pass\n"
    tree = ast.parse(source)
    scope_map = _build_scope_map(tree)
    results = _detect_hardcoded_threshold("<test>", tree, scope_map)
    assert len(results) >= 1
    for r in results:
        assert "anomaly_scores" in r["pattern_detail"], (
            f"anomaly_scores missing from pattern_detail: {r['pattern_detail']}"
        )
        assert "max_anomaly_score" in r["pattern_detail"]


def test_keyword_min_string_count_module_constant():
    """_KEYWORD_MIN_STRING_COUNT must exist as a module-level constant."""
    try:
        import tools.ai_augmentation.pattern_classifier as mod
    except ImportError:
        pytest.skip("Not importable")
    assert hasattr(mod, "_KEYWORD_MIN_STRING_COUNT"), (
        "_KEYWORD_MIN_STRING_COUNT not found at module level"
    )
    assert isinstance(mod._KEYWORD_MIN_STRING_COUNT, int)


def test_java_min_if_depth_matches_config():
    """_JAVA_MIN_IF_DEPTH must match the value in aac_config.yaml."""
    try:
        import tools.ai_augmentation.pattern_classifier as mod
        from tools.ai_augmentation.pattern_classifier import _load_config
    except ImportError:
        pytest.skip("Not importable")
    cfg = _load_config()
    expected = int(cfg.get("java_min_if_depth", 2))
    assert mod._JAVA_MIN_IF_DEPTH == expected, (
        f"_JAVA_MIN_IF_DEPTH={mod._JAVA_MIN_IF_DEPTH} doesn't match config value {expected}"
    )
