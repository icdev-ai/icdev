# CUI // SP-CTI
"""Tests for constant extraction in the fulfill (R11) proposal_genesis reflex.

AI-ify opp 5404 (hardcoded_threshold → anomaly_detection): the inline magic
numbers in fulfill.py (lookahead window, per-run generation cap, stale-doc
age, GovEval gate threshold, generation timeout) were extracted into named,
config-aligned module constants.

AI-ify opp 5405 (same file/pattern/paradigm) completes the slice 5404 left
open: the *anomaly threshold itself* — the GovEval composite below which a
CDRL is flagged ``needs_review`` — is now config-overridable (config:
reflexes.fulfill.goveval_gate_threshold), not just a module default.
"""
import inspect
import sys
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.proposal_genesis.reflexes.fulfill import (
    _DEFAULT_DAYS_AHEAD,
    _DEFAULT_MAX_GENERATIONS,
    _DEFAULT_STALE_THRESHOLD_DAYS,
    _GOVEVAL_GATE_THRESHOLD,
    _CDRL_GEN_TIMEOUT_SECS,
    _generate_cdrl,
    _get_due_deliverables,
    _get_stale_documentation,
)


class TestFulfillConstants:
    def test_window_and_limits_positive(self):
        assert _DEFAULT_DAYS_AHEAD > 0
        assert _DEFAULT_MAX_GENERATIONS > 0
        assert _DEFAULT_STALE_THRESHOLD_DAYS > 0

    def test_goveval_gate_is_unit_fraction(self):
        # GovEval composite scores are normalised to 0..1; the gate must lie
        # strictly inside that range to flag below-threshold (anomalous) CDRLs.
        assert 0.0 < _GOVEVAL_GATE_THRESHOLD < 1.0

    def test_generation_timeout_positive(self):
        assert _CDRL_GEN_TIMEOUT_SECS > 0


class TestFulfillConstantWiring:
    """Verify the extracted constants are actually used at the call sites."""

    def test_due_deliverables_default_matches_constant(self):
        defaults = _get_due_deliverables.__defaults__
        assert defaults == (_DEFAULT_DAYS_AHEAD,)

    def test_stale_documentation_default_matches_constant(self):
        defaults = _get_stale_documentation.__defaults__
        assert defaults == (_DEFAULT_STALE_THRESHOLD_DAYS,)

    def test_config_defaults_align(self):
        # The run() config.get() fallbacks mirror proposal_genesis_config.yaml
        # reflexes.fulfill (days_ahead=14, max_generations_per_run=10,
        # stale_threshold_days=90).
        assert _DEFAULT_DAYS_AHEAD == 14
        assert _DEFAULT_MAX_GENERATIONS == 10
        assert _DEFAULT_STALE_THRESHOLD_DAYS == 90


class TestAnomalyThresholdOverridable:
    """opp 5405 — the GovEval anomaly gate is config-overridable, not hardcoded."""

    def test_generate_cdrl_exposes_threshold_param(self):
        # The anomaly gate must be injectable so run() can pass a config value.
        sig = inspect.signature(_generate_cdrl)
        assert "goveval_gate_threshold" in sig.parameters
        # Default falls back to the module constant (backward-compatible).
        assert sig.parameters["goveval_gate_threshold"].default == _GOVEVAL_GATE_THRESHOLD

    def test_config_exposes_goveval_gate_threshold(self):
        cfg = yaml.safe_load(
            (BASE_DIR / "args" / "proposal_genesis_config.yaml").read_text(encoding="utf-8")
        )
        fulfill_cfg = cfg["reflexes"]["fulfill"]
        assert "goveval_gate_threshold" in fulfill_cfg
        assert fulfill_cfg["goveval_gate_threshold"] == _GOVEVAL_GATE_THRESHOLD
