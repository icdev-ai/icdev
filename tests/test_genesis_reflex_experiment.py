# CUI // SP-CTI
"""Tests for tools/genesis/reflexes/experiment.py — adaptive threshold + run()."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def reflex():
    import tools.genesis.reflexes.experiment as rx
    return rx


@pytest.fixture
def mock_db(monkeypatch):
    """Monkeypatch _get_db so no real DB connection is needed."""
    conn = MagicMock()
    import tools.genesis.reflexes.experiment as rx
    monkeypatch.setattr(rx, "_get_db", lambda: conn)
    return conn


class TestComputeAdaptiveThreshold:
    def test_sufficient_data_returns_mean_plus_z(self, reflex, mock_db):
        """With >= min_samples rows, returns mean + z_factor * std (clamped to [0,1])."""
        # mean=0.60, variance=0.01 → std=0.10 → threshold=0.60+0.5*0.10=0.65
        row = (0.60, 0.01, 20)
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.execute.return_value.fetchone.return_value = row

        result = reflex._compute_adaptive_threshold(fallback=0.70, z_factor=0.5, min_samples=10)

        assert abs(result - 0.65) < 1e-9

    def test_insufficient_samples_returns_fallback(self, reflex, mock_db):
        """Fewer than min_samples rows → fallback value returned."""
        row = (0.55, 0.02, 5)  # count=5 < min_samples=10
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.execute.return_value.fetchone.return_value = row

        result = reflex._compute_adaptive_threshold(fallback=0.70, z_factor=0.5, min_samples=10)

        assert result == 0.70

    def test_db_error_returns_fallback(self, reflex, mock_db):
        """DB exception → fallback value returned without propagating."""
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.execute.side_effect = RuntimeError("db error")

        result = reflex._compute_adaptive_threshold(fallback=0.70)

        assert result == 0.70

    def test_result_clamped_to_one(self, reflex, mock_db):
        """Very high mean + std never exceeds 1.0."""
        row = (0.95, 0.04, 15)  # mean=0.95, variance=0.04 → std=0.20, raw=1.05
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.execute.return_value.fetchone.return_value = row

        result = reflex._compute_adaptive_threshold(fallback=0.70, z_factor=0.5, min_samples=10)

        assert result == 1.0

    def test_null_count_returns_fallback(self, reflex, mock_db):
        """None row from DB → fallback returned."""
        mock_db.__enter__ = lambda s: mock_db
        mock_db.__exit__ = MagicMock(return_value=False)
        mock_db.execute.return_value.fetchone.return_value = None

        result = reflex._compute_adaptive_threshold(fallback=0.70)

        assert result == 0.70


class TestRunAdaptiveThreshold:
    def _make_run_loop(self, monkeypatch, reflex):
        """Stub experiment_engine.run_loop to avoid real execution."""
        fake_result = {
            "success": True,
            "kept": 1,
            "experiments_run": 2,
            "acceptance_rate": 0.5,
            "total_improvement": 0.1,
        }

        class FakeEngine:
            @staticmethod
            def run_loop(**kwargs):
                return fake_result

        monkeypatch.setattr(
            "tools.autoresearch.experiment_engine",
            FakeEngine,
            raising=False,
        )
        return fake_result

    def test_run_uses_adaptive_threshold_when_enabled(self, reflex, monkeypatch):
        """run() calls _compute_adaptive_threshold when adaptive_threshold.enabled=True."""
        calls = []

        def fake_adaptive(fallback=0.70, z_factor=0.5, min_samples=10):
            calls.append({"fallback": fallback, "z_factor": z_factor})
            return 0.65

        monkeypatch.setattr(reflex, "_compute_adaptive_threshold", fake_adaptive)
        monkeypatch.setattr(reflex, "_fetch_high_score_signals", lambda t: [])
        monkeypatch.setattr(
            "tools.autoresearch.experiment_engine",
            type("E", (), {"run_loop": staticmethod(lambda **kw: {"success": False})}),
            raising=False,
        )

        config = {
            "adaptive_threshold": {"enabled": True, "z_factor": 0.5, "min_samples": 10},
            "signal_score_threshold": 0.70,
            "max_experiments_per_run": 1,
            "domains": ["compliance"],
        }
        result = reflex.run(config, None)

        assert result["success"] is True
        assert len(calls) == 1
        assert calls[0]["fallback"] == 0.70

    def test_run_uses_static_threshold_when_adaptive_disabled(self, reflex, monkeypatch):
        """run() does NOT call _compute_adaptive_threshold when adaptive disabled."""
        calls = []

        def fake_adaptive(**kwargs):
            calls.append(True)
            return 0.65

        monkeypatch.setattr(reflex, "_compute_adaptive_threshold", fake_adaptive)
        monkeypatch.setattr(reflex, "_fetch_high_score_signals", lambda t: [])
        monkeypatch.setattr(
            "tools.autoresearch.experiment_engine",
            type("E", (), {"run_loop": staticmethod(lambda **kw: {"success": False})}),
            raising=False,
        )

        config = {
            "adaptive_threshold": {"enabled": False},
            "signal_score_threshold": 0.80,
            "max_experiments_per_run": 1,
            "domains": ["compliance"],
        }
        reflex.run(config, None)

        assert calls == []

    def test_run_returns_success_shape(self, reflex, monkeypatch):
        """run() always returns the expected top-level shape."""
        monkeypatch.setattr(reflex, "_compute_adaptive_threshold", lambda **kw: 0.65)
        monkeypatch.setattr(reflex, "_fetch_high_score_signals", lambda t: [])
        monkeypatch.setattr(
            "tools.autoresearch.experiment_engine",
            type("E", (), {"run_loop": staticmethod(lambda **kw: {"success": False})}),
            raising=False,
        )

        config = {
            "adaptive_threshold": {"enabled": True},
            "max_experiments_per_run": 1,
            "domains": ["security"],
        }
        result = reflex.run(config, None)

        assert "success" in result
        assert "metric_value" in result
        assert "details" in result
