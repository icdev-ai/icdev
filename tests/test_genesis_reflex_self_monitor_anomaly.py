# CUI // SP-CTI
"""Tests for configurable anomaly-detection thresholds in self_monitor.py.

Covers the hardcoded_threshold → config migration:
  - _build_anomaly_system_prompt uses DEFAULT_CATEGORY_THRESHOLDS by default
  - custom category_thresholds from config override/extend defaults
  - _AnomalyDetector builds a per-instance prompt from supplied thresholds
  - LLM path uses the instance prompt, not the global constant
  - run() passes category_thresholds from ad_cfg to the detector
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def rx():
    import tools.genesis.reflexes.self_monitor as m
    return m


class TestBuildAnomalySystemPrompt:
    def test_default_prompt_contains_all_default_categories(self, rx):
        prompt = rx._build_anomaly_system_prompt()
        for cat in rx.DEFAULT_CATEGORY_THRESHOLDS:
            assert cat in prompt, f"default category '{cat}' missing from prompt"

    def test_default_prompt_contains_module_import_guidance(self, rx):
        prompt = rx._build_anomaly_system_prompt()
        assert "any failure" in prompt
        assert "module_import" in prompt

    def test_custom_guidance_overrides_default(self, rx):
        custom = {"module_import": {"guidance": "CUSTOM_GUIDANCE_TEXT"}}
        prompt = rx._build_anomaly_system_prompt(custom)
        assert "CUSTOM_GUIDANCE_TEXT" in prompt
        # original default guidance should be replaced
        assert "any failure (>=1) is critical" not in prompt

    def test_new_category_appended(self, rx):
        custom = {"my_new_probe": {"guidance": "NEW_PROBE_GUIDANCE"}}
        prompt = rx._build_anomaly_system_prompt(custom)
        assert "my_new_probe" in prompt
        assert "NEW_PROBE_GUIDANCE" in prompt

    def test_none_thresholds_returns_default(self, rx):
        default_prompt = rx._build_anomaly_system_prompt(None)
        assert default_prompt == rx._build_anomaly_system_prompt()

    def test_empty_dict_returns_default(self, rx):
        assert rx._build_anomaly_system_prompt({}) == rx._build_anomaly_system_prompt()

    def test_prompt_contains_json_format_instruction(self, rx):
        prompt = rx._build_anomaly_system_prompt()
        assert "is_anomaly" in prompt
        assert "Return ONLY that JSON" in prompt


class TestAnomalyDetectorPrompt:
    def test_default_detector_uses_default_prompt(self, rx):
        detector = rx._AnomalyDetector()
        assert detector._system_prompt == rx._build_anomaly_system_prompt()

    def test_custom_thresholds_change_instance_prompt(self, rx):
        custom = {"http_head": {"guidance": "ZERO_TOLERANCE"}}
        detector = rx._AnomalyDetector(category_thresholds=custom)
        assert "ZERO_TOLERANCE" in detector._system_prompt
        # default http_head guidance should be replaced
        assert "1-3 route errors" not in detector._system_prompt

    def test_two_detectors_can_have_different_prompts(self, rx):
        d1 = rx._AnomalyDetector()
        d2 = rx._AnomalyDetector({"coherence_status": {"guidance": "STRICT_COHERENCE"}})
        assert d1._system_prompt != d2._system_prompt
        assert "STRICT_COHERENCE" in d2._system_prompt

    def test_llm_classify_uses_instance_prompt_not_global(self, rx):
        """_llm_classify must use self._system_prompt, not the module-level constant."""
        custom = {"module_import": {"guidance": "INSTANCE_SPECIFIC_GUIDANCE"}}
        detector = rx._AnomalyDetector(category_thresholds=custom)

        captured_requests = []

        class FakeRequest:
            def __init__(self, **kwargs):
                captured_requests.append(kwargs)
                self.content = '{"categories": [{"name": "module_import", "is_anomaly": true, "confidence": 0.9}]}'

        class FakeRouter:
            def invoke(self, fn, req):
                return req

        detector._router = FakeRouter()
        detector._LLMRequest = FakeRequest

        _result = detector._llm_classify(
            {"module_import": 2}, {"module_import": 2}, None
        )
        assert len(captured_requests) == 1
        assert captured_requests[0]["system_prompt"] == detector._system_prompt
        assert "INSTANCE_SPECIFIC_GUIDANCE" in captured_requests[0]["system_prompt"]


class TestRunPassesCategoryThresholds:
    def test_run_passes_category_thresholds_to_detector(self, rx, monkeypatch):
        """run() must forward ad_cfg['category_thresholds'] to _AnomalyDetector."""
        created_detectors = []

        class CapturingDetector(rx._AnomalyDetector):
            def __init__(self, category_thresholds=None):
                created_detectors.append(category_thresholds)
                super().__init__(category_thresholds=category_thresholds)

        monkeypatch.setattr(rx, "_AnomalyDetector", CapturingDetector)
        monkeypatch.setattr(rx, "_refresh_probes", lambda _: {})
        monkeypatch.setattr(rx, "_latest_failures", lambda *a, **kw: {})
        monkeypatch.setattr(rx, "_get_failure_baseline", lambda *a: {})
        monkeypatch.setattr(rx, "_adaptive_min_fail", lambda *a, **kw: 1)
        monkeypatch.setattr(rx, "_record_failures", lambda *a, **kw: 0)
        monkeypatch.setattr(rx, "_sync_alerts", lambda *a, **kw: {"opened": 0, "updated": 0, "resolved": 0, "firing": 0})

        mock_conn = MagicMock()
        mock_conn.set_security_context = MagicMock()
        mock_conn.close = MagicMock()
        monkeypatch.setattr(rx, "get_connection", lambda: mock_conn)

        custom_thresholds = {"http_head": {"guidance": "STRICT"}}
        cfg = {
            "anomaly_detection": {
                "enabled": True,
                "llm_enabled": True,
                "baseline_hours": 24,
                "category_thresholds": custom_thresholds,
            }
        }
        rx.run(cfg, None)

        assert len(created_detectors) == 1
        assert created_detectors[0] == custom_thresholds

    def test_run_uses_none_when_category_thresholds_absent(self, rx, monkeypatch):
        """When category_thresholds is absent, detector receives None (uses defaults)."""
        created_detectors = []

        class CapturingDetector(rx._AnomalyDetector):
            def __init__(self, category_thresholds=None):
                created_detectors.append(category_thresholds)
                super().__init__(category_thresholds=category_thresholds)

        monkeypatch.setattr(rx, "_AnomalyDetector", CapturingDetector)
        monkeypatch.setattr(rx, "_refresh_probes", lambda _: {})
        monkeypatch.setattr(rx, "_latest_failures", lambda *a, **kw: {})
        monkeypatch.setattr(rx, "_get_failure_baseline", lambda *a: {})
        monkeypatch.setattr(rx, "_adaptive_min_fail", lambda *a, **kw: 1)
        monkeypatch.setattr(rx, "_record_failures", lambda *a, **kw: 0)
        monkeypatch.setattr(rx, "_sync_alerts", lambda *a, **kw: {"opened": 0, "updated": 0, "resolved": 0, "firing": 0})

        mock_conn = MagicMock()
        mock_conn.set_security_context = MagicMock()
        mock_conn.close = MagicMock()
        monkeypatch.setattr(rx, "get_connection", lambda: mock_conn)

        cfg = {
            "anomaly_detection": {
                "enabled": True,
                "llm_enabled": True,
                "baseline_hours": 24,
                # no category_thresholds key
            }
        }
        rx.run(cfg, None)

        assert len(created_detectors) == 1
        # empty dict → None (defaults used)
        assert created_detectors[0] is None


class TestDefaultCategoryThresholds:
    def test_all_expected_categories_present(self, rx):
        expected = {"module_import", "http_head", "coherence_status", "twin_probe", "gap::tool_not_in_manifest"}
        assert expected <= set(rx.DEFAULT_CATEGORY_THRESHOLDS.keys())

    def test_each_entry_has_required_keys(self, rx):
        for cat, cfg in rx.DEFAULT_CATEGORY_THRESHOLDS.items():
            assert "guidance" in cfg, f"{cat} missing 'guidance'"
            assert "noise_max" in cfg, f"{cat} missing 'noise_max'"
            assert "anomalous_min" in cfg, f"{cat} missing 'anomalous_min'"

    def test_noise_max_lt_anomalous_min(self, rx):
        for cat, cfg in rx.DEFAULT_CATEGORY_THRESHOLDS.items():
            assert cfg["noise_max"] < cfg["anomalous_min"], (
                f"{cat}: noise_max ({cfg['noise_max']}) must be < anomalous_min ({cfg['anomalous_min']})"
            )
