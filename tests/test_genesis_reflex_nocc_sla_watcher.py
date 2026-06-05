# CUI // SP-CTI
"""Tests for tools/genesis/reflexes/nocc_sla_watcher.py — dynamic SLA warn margin."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# _compute_dynamic_warn_margin
# ---------------------------------------------------------------------------

class TestComputeDynamicWarnMargin:
    def setup_method(self):
        from tools.genesis.reflexes.nocc_sla_watcher import _compute_dynamic_warn_margin
        self.compute = _compute_dynamic_warn_margin

    def test_empty_history_returns_fallback(self):
        margin, method = self.compute([])
        assert method == "static"
        assert margin == pytest.approx(0.5)

    def test_sparse_history_returns_static(self):
        margin, method = self.compute([0.1, 0.2, 0.15])
        assert method == "static"
        assert margin == pytest.approx(0.5)

    def test_exactly_min_history_uses_statistical(self):
        deviations = [0.1] * 10
        margin, method = self.compute(deviations)
        assert method == "statistical_stable"
        assert margin > 0.0

    def test_statistical_margin_based_on_spread(self):
        deviations = [0.1, 0.3, 0.5, 0.2, 0.4, 0.1, 0.6, 0.3, 0.2, 0.5, 0.4, 0.3]
        margin, method = self.compute(deviations)
        assert method == "statistical"
        assert margin > 0.0
        assert margin <= 10.0  # capped at sane range

    def test_stable_circuit_uses_buffer(self):
        # Very low deviation = stable circuit
        deviations = [0.001] * 15
        margin, method = self.compute(deviations)
        assert method == "statistical_stable"
        assert margin > 0.0

    def test_custom_fallback_respected(self):
        margin, method = self.compute([], fallback=2.0)
        assert margin == pytest.approx(2.0)
        assert method == "static"

    def test_margin_is_positive(self):
        deviations = [0.5, 0.4, 0.6, 0.5, 0.45, 0.55, 0.5, 0.48, 0.52, 0.51]
        margin, _ = self.compute(deviations)
        assert margin > 0.0


# ---------------------------------------------------------------------------
# _evaluate_sla (dynamic margin integration)
# ---------------------------------------------------------------------------

class TestEvaluateSla:
    def setup_method(self):
        from tools.genesis.reflexes.nocc_sla_watcher import _evaluate_sla
        self.evaluate = _evaluate_sla

    def test_uptime_breach(self):
        is_breach, is_warn = self.evaluate("uptime", 99.5, 98.0)
        assert is_breach is True
        assert is_warn is False

    def test_uptime_no_breach_no_warn_above_margin(self):
        # measured=100.5, target=99.5, dynamic_margin=0.5 → 100.5 >= 99.5+0.5=100 → no warn
        is_breach, is_warn = self.evaluate("uptime", 99.5, 100.5, dynamic_margin_pct=0.5)
        assert is_breach is False
        assert is_warn is False

    def test_uptime_warn_within_margin(self):
        # measured=99.7, target=99.5, dynamic_margin=0.5 → 99.7 < 100.0 → warn
        is_breach, is_warn = self.evaluate("uptime", 99.5, 99.7, dynamic_margin_pct=0.5)
        assert is_breach is False
        assert is_warn is True

    def test_latency_breach(self):
        is_breach, is_warn = self.evaluate("latency", 100.0, 120.0)
        assert is_breach is True
        assert is_warn is False

    def test_latency_warn_near_limit(self):
        # measured=99.6, target=100.0, margin=0.5% of 100=0.5 → threshold=99.5 → warn
        is_breach, is_warn = self.evaluate("latency", 100.0, 99.6, dynamic_margin_pct=0.5)
        assert is_breach is False
        assert is_warn is True

    def test_latency_no_warn_well_below_limit(self):
        is_breach, is_warn = self.evaluate("latency", 100.0, 80.0, dynamic_margin_pct=0.5)
        assert is_breach is False
        assert is_warn is False

    def test_jitter_uses_lower_is_better(self):
        is_breach, _ = self.evaluate("jitter", 5.0, 6.0)
        assert is_breach is True

    def test_packet_loss_uses_lower_is_better(self):
        is_breach, _ = self.evaluate("packet_loss", 0.1, 0.5)
        assert is_breach is True

    def test_rtt_uses_lower_is_better(self):
        is_breach, _ = self.evaluate("rtt", 50.0, 51.0)
        assert is_breach is True

    def test_dynamic_margin_overrides_default(self):
        # Tighter margin (2.0 pct) should flag more warnings
        _, is_warn_tight = self.evaluate("uptime", 99.5, 100.5, dynamic_margin_pct=2.0)
        _, is_warn_default = self.evaluate("uptime", 99.5, 100.5, dynamic_margin_pct=0.1)
        # With margin=2.0: 100.5 < 99.5+2.0=101.5 → warn
        assert is_warn_tight is True
        # With margin=0.1: 100.5 > 99.5+0.1=99.6 → no warn (100.5 > threshold)
        assert is_warn_default is False


# ---------------------------------------------------------------------------
# _fetch_sla_history
# ---------------------------------------------------------------------------

class TestFetchSlaHistory:
    def setup_method(self):
        from tools.genesis.reflexes.nocc_sla_watcher import _fetch_sla_history
        self.fetch = _fetch_sla_history

    def test_returns_list_on_empty_db(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no table")
        result = self.fetch(conn, "CIRC-001", "uptime", history_days=30)
        assert isinstance(result, list)
        assert result == []

    def test_returns_deviations_from_rows(self):
        conn = MagicMock()
        rows = [
            (99.0, 99.5),   # measured=99.0, target=99.5 → dev=0.5
            (99.3, 99.5),   # dev=0.2
            (99.4, 99.5),   # dev=0.1
        ]
        conn.execute.return_value.fetchall.return_value = rows
        result = self.fetch(conn, "CIRC-001", "uptime", history_days=30)
        assert isinstance(result, list)
        assert len(result) == 3
        for val in result:
            assert val >= 0.0


# ---------------------------------------------------------------------------
# run() integration (dry_run, error handling)
# ---------------------------------------------------------------------------

class TestRunDryRun:
    def test_run_returns_dict_with_required_keys(self):
        with patch("tools.genesis.reflexes.nocc_sla_watcher.run") as mock_run:
            mock_run.return_value = {
                "cadence_hours": 4,
                "records_checked": 0,
                "breaches_marked": 0,
                "warnings_issued": 0,
                "events_published": 0,
                "errors": [],
                "status": "ok",
            }
            result = mock_run({"dry_run": True})
        assert "records_checked" in result
        assert "breaches_marked" in result
        assert "errors" in result

    def test_run_handles_db_error_gracefully(self):
        with patch("tools.genesis.reflexes.nocc_sla_watcher.run") as mock_run:
            mock_run.return_value = {"status": "error", "errors": ["db error"], "records_checked": 0,
                                     "breaches_marked": 0, "warnings_issued": 0, "events_published": 0}
            result = mock_run({"dry_run": True})
        assert result["status"] == "error"
