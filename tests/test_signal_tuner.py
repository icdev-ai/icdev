"""Smoke tests for signal_tuner: dry_run shape, signal_thresholds.yaml load, backtester.score."""
from __future__ import annotations

import yaml
import pytest

from tools.fathomdesk import backtester
from tools.fathomdesk import signal_generator
from tools.fathomdesk import signal_tuner


def test_dry_run_returns_expected_shape():
    result = signal_tuner.dry_run()
    assert result["dry_run"] is True
    assert result["tuner"] == "signal_tuner"
    assert isinstance(result["steps_count"], int) and result["steps_count"] > 0
    assert isinstance(result["thresholds"], dict)
    assert isinstance(result["steps"], list)
    assert len(result["steps"]) == result["steps_count"]
    assert "input_signals" in result
    for step in result["steps"]:
        assert "step_id" in step
        assert "action" in step


def test_signal_thresholds_yaml_loaded_by_signal_generator(tmp_path):
    config_file = tmp_path / "signal_thresholds.yaml"
    config_file.write_text(
        yaml.dump(
            {
                "_schema_version": 1,
                "thresholds": {
                    "min_confidence": 0.75,
                    "min_score": 0.65,
                    "max_signals": 10,
                },
            }
        ),
        encoding="utf-8",
    )

    thresholds = signal_generator.load_thresholds(path=config_file)

    assert thresholds["min_confidence"] == pytest.approx(0.75)
    assert thresholds["min_score"] == pytest.approx(0.65)
    assert thresholds["max_signals"] == 10


def test_backtester_score_with_synthetic_signals():
    signals = [
        {"direction": "long", "p1y": 0.12, "confidence": 0.80},
        {"direction": "long", "p1y": -0.05, "confidence": 0.70},
        {"direction": "short", "p1y": 0.08, "confidence": 0.65},
        {"direction": "short", "p1y": -0.10, "confidence": 0.75},
    ]
    result = backtester.score(signals)

    assert isinstance(result, dict)
    assert set(result.keys()) == {"precision", "recall", "f1"}
    assert all(isinstance(v, float) for v in result.values())
    assert all(0.0 <= v <= 1.0 for v in result.values())
