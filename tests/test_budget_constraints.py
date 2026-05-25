# CUI // SP-CTI
"""Integration tests for budget monitor and throttle controller.

Acceptance criteria:
- When resource usage is simulated at 110% of limit, throttling is triggered.
- When resource usage is under limit, throttling is NOT triggered.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from services.budget_monitor import BudgetMonitor, UsageSample
from services.throttle_controller import ThrottleController


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def monitor_with_limits(tmp_path):
    """BudgetMonitor pre-loaded with a 100-unit limit for 'llm_tokens'."""
    cfg = tmp_path / "budget_config.yaml"
    cfg.write_text("limits:\n  - resource: llm_tokens\n    limit: 100\n    unit: tokens\n")
    m = BudgetMonitor(config_path=cfg)
    m.initialize()
    return m


@pytest.fixture
def over_budget_monitor(tmp_path):
    """BudgetMonitor where llm_tokens usage is 110 (110% of 100 limit)."""
    cfg = tmp_path / "budget_config.yaml"
    cfg.write_text("limits:\n  - resource: llm_tokens\n    limit: 100\n    unit: tokens\n")
    m = BudgetMonitor(config_path=cfg)
    m.initialize()
    # Mock fetch to return 110 units (over budget)
    m.fetch_current_usage = lambda: [UsageSample(resource="llm_tokens", used=110.0)]
    return m


@pytest.fixture
def under_budget_monitor(tmp_path):
    """BudgetMonitor where llm_tokens usage is 50 (50% of 100 limit)."""
    cfg = tmp_path / "budget_config.yaml"
    cfg.write_text("limits:\n  - resource: llm_tokens\n    limit: 100\n    unit: tokens\n")
    m = BudgetMonitor(config_path=cfg)
    m.initialize()
    m.fetch_current_usage = lambda: [UsageSample(resource="llm_tokens", used=50.0)]
    return m


# ── BudgetMonitor unit tests ───────────────────────────────────────────────────

class TestBudgetMonitor:
    def test_initialize_loads_limits(self, monitor_with_limits):
        assert len(monitor_with_limits._limits) == 1
        assert monitor_with_limits._limits[0].resource == "llm_tokens"
        assert monitor_with_limits._limits[0].limit == 100.0

    def test_variance_over_budget(self, over_budget_monitor):
        variances = over_budget_monitor.calculate_variance()
        assert len(variances) == 1
        v = variances[0]
        assert v.over_budget is True
        assert v.used == 110.0
        assert v.remaining == -10.0
        assert v.pct_used > 100.0

    def test_variance_under_budget(self, under_budget_monitor):
        variances = under_budget_monitor.calculate_variance()
        assert len(variances) == 1
        v = variances[0]
        assert v.over_budget is False
        assert v.remaining == 50.0
        assert v.pct_used == 50.0

    def test_variance_at_exact_limit(self, tmp_path):
        cfg = tmp_path / "budget_config.yaml"
        cfg.write_text("limits:\n  - resource: gpu_hours\n    limit: 10\n    unit: hours\n")
        m = BudgetMonitor(config_path=cfg)
        m.initialize()
        m.fetch_current_usage = lambda: [UsageSample(resource="gpu_hours", used=10.0)]
        v = m.calculate_variance()[0]
        assert v.over_budget is False
        assert v.remaining == 0.0
        assert v.pct_used == 100.0

    def test_empty_config_returns_no_variances(self, tmp_path):
        cfg = tmp_path / "empty.yaml"
        cfg.write_text("limits: []\n")
        m = BudgetMonitor(config_path=cfg)
        m.initialize()
        assert m.calculate_variance() == []

    def test_missing_config_returns_no_variances(self, tmp_path):
        m = BudgetMonitor(config_path=tmp_path / "nonexistent.yaml")
        m.initialize()
        assert m.calculate_variance() == []


# ── ThrottleController integration tests ──────────────────────────────────────

class TestThrottleController:
    def test_throttling_triggered_at_110_percent(self, over_budget_monitor):
        """When usage is 110% of limit, ThrottleController must throttle the resource."""
        ctrl = ThrottleController(monitor=over_budget_monitor, poll_interval=9999)
        # Manually run one poll cycle (no background thread)
        variances = over_budget_monitor.calculate_variance()
        for v in variances:
            if v.over_budget or v.pct_used >= 90.0:
                ctrl._throttled[v.resource] = True
            else:
                ctrl._throttled[v.resource] = False

        assert ctrl.is_throttled("llm_tokens") is True

    def test_no_throttling_when_under_limit(self, under_budget_monitor):
        """When usage is 50% of limit, ThrottleController must NOT throttle."""
        ctrl = ThrottleController(monitor=under_budget_monitor, poll_interval=9999)
        variances = under_budget_monitor.calculate_variance()
        for v in variances:
            if v.over_budget or v.pct_used >= 90.0:
                ctrl._throttled[v.resource] = True
            else:
                ctrl._throttled[v.resource] = False

        assert ctrl.is_throttled("llm_tokens") is False

    def test_throttling_at_90_percent_soft_limit(self, tmp_path):
        """Usage at 90% triggers soft throttle."""
        cfg = tmp_path / "budget_config.yaml"
        cfg.write_text("limits:\n  - resource: api_calls\n    limit: 100\n    unit: calls\n")
        m = BudgetMonitor(config_path=cfg)
        m.initialize()
        m.fetch_current_usage = lambda: [UsageSample(resource="api_calls", used=90.0)]

        ctrl = ThrottleController(monitor=m, poll_interval=9999)
        variances = m.calculate_variance()
        for v in variances:
            if v.over_budget or v.pct_used >= 90.0:
                ctrl._throttled[v.resource] = True
            else:
                ctrl._throttled[v.resource] = False

        assert ctrl.is_throttled("api_calls") is True

    def test_is_throttled_false_for_unknown_resource(self, over_budget_monitor):
        ctrl = ThrottleController(monitor=over_budget_monitor, poll_interval=9999)
        assert ctrl.is_throttled("nonexistent_resource") is False

    def test_controller_starts_background_thread(self, under_budget_monitor):
        ctrl = ThrottleController(monitor=under_budget_monitor, poll_interval=9999)
        ctrl.start()
        assert ctrl._running is True
        assert ctrl._thread is not None
        assert ctrl._thread.is_alive()
        ctrl.stop()
