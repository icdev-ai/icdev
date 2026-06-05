# CUI // SP-CTI
"""Tests for meta_harness config-driven adaptive thresholds (AI-ify 5238 — hardcoded_threshold).

Verifies that _get_adaptive_thresholds() and helpers read thresholds from
genesis_config.yaml instead of divergent hardcoded Python constants.
"""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# _load_meta_config_gates
# ---------------------------------------------------------------------------

class TestLoadMetaConfigGates:
    def test_reads_precision_min_and_false_heal_max_from_yaml(self, tmp_path):
        """Happy path: _load_meta_config_gates() parses both keys from harness.gates."""
        config_yaml = (
            "harness:\n"
            "  gates:\n"
            "    precision_min: 0.85\n"
            "    false_heal_max: 0.18\n"
        )
        config_file = tmp_path / "genesis_config.yaml"
        config_file.write_text(config_yaml, encoding="utf-8")

        from tools.genesis.harness.meta_harness import _load_meta_config_gates
        with patch("tools.genesis.harness.meta_harness._find_meta_config_path", return_value=config_file):
            gates = _load_meta_config_gates()

        assert gates["precision_min"] == pytest.approx(0.85)
        assert gates["false_heal_max"] == pytest.approx(0.18)

    def test_falls_back_to_module_constants_when_config_path_is_none(self):
        """_load_meta_config_gates() returns module-level constants when no config file is found."""
        from tools.genesis.harness.meta_harness import (
            _load_meta_config_gates,
            PRECISION_HARD_FLOOR,
            FALSE_HEAL_CEILING,
        )
        with patch("tools.genesis.harness.meta_harness._find_meta_config_path", return_value=None):
            gates = _load_meta_config_gates()

        assert gates["precision_min"] == pytest.approx(PRECISION_HARD_FLOOR)
        assert gates["false_heal_max"] == pytest.approx(FALSE_HEAL_CEILING)

    def test_falls_back_gracefully_on_corrupt_yaml(self, tmp_path):
        """_load_meta_config_gates() does not raise when YAML is malformed."""
        bad_config = tmp_path / "genesis_config.yaml"
        bad_config.write_text(": invalid: {yaml: [}", encoding="utf-8")

        from tools.genesis.harness.meta_harness import _load_meta_config_gates, PRECISION_HARD_FLOOR
        with patch("tools.genesis.harness.meta_harness._find_meta_config_path", return_value=bad_config):
            gates = _load_meta_config_gates()

        assert "precision_min" in gates
        assert "false_heal_max" in gates
        assert gates["precision_min"] == pytest.approx(PRECISION_HARD_FLOOR)

    def test_uses_partial_config_keys_with_constant_fallback(self, tmp_path):
        """When only one key is present in harness.gates, the other uses module constant."""
        config_yaml = (
            "harness:\n"
            "  gates:\n"
            "    precision_min: 0.78\n"
        )
        config_file = tmp_path / "genesis_config.yaml"
        config_file.write_text(config_yaml, encoding="utf-8")

        from tools.genesis.harness.meta_harness import _load_meta_config_gates, FALSE_HEAL_CEILING
        with patch("tools.genesis.harness.meta_harness._find_meta_config_path", return_value=config_file):
            gates = _load_meta_config_gates()

        assert gates["precision_min"] == pytest.approx(0.78)
        assert gates["false_heal_max"] == pytest.approx(FALSE_HEAL_CEILING)


# ---------------------------------------------------------------------------
# Module-constant / config alignment
# ---------------------------------------------------------------------------

class TestModuleConstantsAlignedWithConfig:
    def test_precision_hard_floor_matches_genesis_config(self):
        """PRECISION_HARD_FLOOR must equal genesis_config.yaml harness.gates.precision_min."""
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")

        from tools.genesis.harness.meta_harness import PRECISION_HARD_FLOOR

        search = Path(__file__).resolve()
        config_path = None
        for parent in search.parents:
            candidate = parent / "args" / "genesis_config.yaml"
            if candidate.exists():
                config_path = candidate
                break

        if config_path is None:
            pytest.skip("genesis_config.yaml not found in tree")

        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        config_val = cfg.get("harness", {}).get("gates", {}).get("precision_min")
        if config_val is None:
            pytest.skip("harness.gates.precision_min not in config")

        assert PRECISION_HARD_FLOOR == pytest.approx(float(config_val)), (
            f"PRECISION_HARD_FLOOR ({PRECISION_HARD_FLOOR}) diverges from "
            f"genesis_config.yaml harness.gates.precision_min ({config_val})"
        )

    def test_false_heal_ceiling_matches_genesis_config(self):
        """FALSE_HEAL_CEILING must equal genesis_config.yaml harness.gates.false_heal_max."""
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")

        from tools.genesis.harness.meta_harness import FALSE_HEAL_CEILING

        search = Path(__file__).resolve()
        config_path = None
        for parent in search.parents:
            candidate = parent / "args" / "genesis_config.yaml"
            if candidate.exists():
                config_path = candidate
                break

        if config_path is None:
            pytest.skip("genesis_config.yaml not found in tree")

        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        config_val = cfg.get("harness", {}).get("gates", {}).get("false_heal_max")
        if config_val is None:
            pytest.skip("harness.gates.false_heal_max not in config")

        assert FALSE_HEAL_CEILING == pytest.approx(float(config_val)), (
            f"FALSE_HEAL_CEILING ({FALSE_HEAL_CEILING}) diverges from "
            f"genesis_config.yaml harness.gates.false_heal_max ({config_val})"
        )


# ---------------------------------------------------------------------------
# _get_adaptive_thresholds — fallback path uses config, not hardcoded values
# ---------------------------------------------------------------------------

class TestGetAdaptiveThresholdsConfigFallback:
    def test_returns_config_gates_when_detector_import_fails(self, monkeypatch):
        """When _AnomalyDetector cannot be imported, thresholds come from _load_meta_config_gates()."""
        import sys
        from tools.genesis.harness import meta_harness

        config_gates = {"precision_min": 0.77, "false_heal_max": 0.13}
        monkeypatch.setattr(meta_harness, "_load_meta_config_gates", lambda: config_gates)

        # Force eval_harness import inside the try block to raise ImportError
        evh_key = "tools.genesis.harness.eval_harness"
        saved = sys.modules.get(evh_key, "NOT_SET")
        sys.modules[evh_key] = None  # None causes ImportError on 'from … import …'
        try:
            precision, ceiling = meta_harness._get_adaptive_thresholds()
        finally:
            if saved == "NOT_SET":
                sys.modules.pop(evh_key, None)
            else:
                sys.modules[evh_key] = saved

        assert precision == pytest.approx(0.77)
        assert ceiling == pytest.approx(0.13)

    def test_uses_anomaly_detector_over_config_when_detector_available(self, monkeypatch):
        """When _AnomalyDetector works, its adaptive values take precedence over config gates."""
        import sys
        from tools.genesis.harness import meta_harness

        # Config returns different values — these should NOT be used when detector works
        monkeypatch.setattr(
            meta_harness, "_load_meta_config_gates",
            lambda: {"precision_min": 0.50, "false_heal_max": 0.50},
        )

        # Build a minimal fake eval_harness module with a working _AnomalyDetector
        import types
        fake_evh = types.ModuleType("tools.genesis.harness.eval_harness")

        class FakeDetector:
            def get_thresholds(self, reflex):
                if reflex == "oracle_triage":
                    return {"precision_min": 0.91}
                return {"false_heal_max": 0.09}

        fake_evh._AnomalyDetector = FakeDetector

        evh_key = "tools.genesis.harness.eval_harness"
        saved = sys.modules.get(evh_key, "NOT_SET")
        sys.modules[evh_key] = fake_evh
        try:
            precision, ceiling = meta_harness._get_adaptive_thresholds()
        finally:
            if saved == "NOT_SET":
                sys.modules.pop(evh_key, None)
            else:
                sys.modules[evh_key] = saved

        assert precision == pytest.approx(0.91)
        assert ceiling == pytest.approx(0.09)


# ---------------------------------------------------------------------------
# Proposal helpers respect caller-supplied thresholds (not internal hardcoded values)
# ---------------------------------------------------------------------------

class TestProposalHelpersDelegateThreshold:
    def test_propose_heuristic_retirements_fires_with_adaptive_floor(self):
        """_propose_heuristic_retirements() uses the caller-supplied floor, not a hardcoded constant.

        precision=0.78 is between old hardcoded (0.70) and the config-aligned floor (0.80).
        With floor=0.80 (adaptive), a retirement should be proposed.
        With floor=0.70 (old hardcoded), no retirement should be proposed.
        """
        from tools.genesis.harness.meta_harness import _propose_heuristic_retirements

        hits = {"bad_heuristic": 3}

        proposals_adaptive = _propose_heuristic_retirements(0.78, hits, precision_floor=0.80)
        assert len(proposals_adaptive) == 1, "Should propose retirement: precision 0.78 < adaptive floor 0.80"

        proposals_old = _propose_heuristic_retirements(0.78, hits, precision_floor=0.70)
        assert len(proposals_old) == 0, "Should NOT propose: precision 0.78 >= old hardcoded floor 0.70"

    def test_propose_constitution_tightening_fires_with_adaptive_ceiling(self):
        """_propose_constitution_tightening() uses the caller-supplied ceiling, not a hardcoded constant.

        false_heal_rate=0.22 is between config-aligned ceiling (0.20) and old hardcoded (0.25).
        With ceiling=0.20 (adaptive), a tightening should be proposed.
        With ceiling=0.25 (old hardcoded), no tightening should be proposed.
        """
        from tools.genesis.harness.meta_harness import _propose_constitution_tightening

        constitution = {"rules": []}
        error_types = {"restart_service": 3}

        proposals_adaptive = _propose_constitution_tightening(
            0.22, constitution, error_types, false_heal_ceiling=0.20
        )
        assert len(proposals_adaptive) == 1, "Should tighten: false_heal 0.22 > adaptive ceiling 0.20"

        proposals_old = _propose_constitution_tightening(
            0.22, constitution, error_types, false_heal_ceiling=0.25
        )
        assert len(proposals_old) == 0, "Should NOT tighten: false_heal 0.22 <= old hardcoded ceiling 0.25"
