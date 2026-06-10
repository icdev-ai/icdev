# CUI // SP-CTI
"""Tests for tools/genesis/reflexes/failure_triage.py — the Genesis
reflex wrapper around tools.workflow.failure_triage.triage_once."""
from __future__ import annotations

import pytest


@pytest.fixture
def reflex(monkeypatch):
    from tools.genesis.reflexes import failure_triage as rx
    return rx


@pytest.fixture
def mock_triage(monkeypatch):
    """Replace triage_once with a fixture that returns a scripted summary."""
    summaries = {}

    def _set(summary):
        summaries["value"] = summary

    def _fake(window_hours: int = 1, *, apply: bool = False):
        return summaries["value"]

    import tools.workflow.failure_triage as _ft
    monkeypatch.setattr(_ft, "triage_once", _fake)
    # arc-cal-02: also stub resolve_outcomes and rolling_precision so the
    # reflex tests don't need a real DB.
    monkeypatch.setattr(_ft, "resolve_outcomes", lambda **k: {"resolved": 0})
    monkeypatch.setattr(_ft, "rolling_precision", lambda **k: {"cohorts": []})
    return _set


class TestReflexRun:
    def test_empty_scan_returns_zeros(self, reflex, mock_triage):
        mock_triage({
            "failures_scanned": 0, "results": [],
            "autofix_enabled": True, "apply_mode": True,
        })
        r = reflex.run({}, None)
        assert r["scanned"] == 0
        assert r["applied"] == 0
        assert r["suggested"] == 0
        assert r["skipped"] == 0
        assert r["autofix_enabled"] is True

    def test_counts_bucket_outcomes_correctly(self, reflex, mock_triage):
        mock_triage({
            "failures_scanned": 5,
            "autofix_enabled": True,
            "apply_mode": True,
            "results": [
                {"outcome": "applied_verified_committed"},
                {"outcome": "applied_verified_committed_merged"},
                {"outcome": "suggested_card_created"},
                {"outcome": "skipped_already_triaged"},
                {"outcome": "apply_failed_fell_through_to_card"},
            ],
        })
        r = reflex.run({}, None)
        assert r["scanned"] == 5
        assert r["applied"] == 2       # both applied_* variants
        assert r["suggested"] == 2     # suggested_card_created + apply_failed_fell_through
        assert r["skipped"] == 1

    def test_config_window_hours_forwarded(self, reflex, monkeypatch):
        seen = {}

        def _fake(window_hours: int = 1, *, apply: bool = False):
            seen["window"] = window_hours
            seen["apply"] = apply
            return {"failures_scanned": 0, "results": [],
                    "autofix_enabled": False, "apply_mode": apply}

        import tools.workflow.failure_triage as _ft
        monkeypatch.setattr(_ft, "triage_once", _fake)
        reflex.run({"window_hours": 6, "apply": False}, None)
        assert seen == {"window": 6, "apply": False}

    def test_default_apply_is_true(self, reflex, monkeypatch):
        """The reflex defaults to apply=True so the daemon actually
        exercises the auto-apply path (env flag still gates the real work)."""
        seen = {}

        def _fake(window_hours: int = 1, *, apply: bool = False):
            seen["apply"] = apply
            return {"failures_scanned": 0, "results": [],
                    "autofix_enabled": False, "apply_mode": apply}

        import tools.workflow.failure_triage as _ft
        monkeypatch.setattr(_ft, "triage_once", _fake)
        reflex.run({}, None)
        assert seen["apply"] is True

    def test_triage_import_failure_returns_error_shape(self, reflex, monkeypatch):
        """If the triage module can't be imported, the reflex must still
        return a well-formed summary so the daemon doesn't crash."""
        import builtins
        real_import = builtins.__import__

        def _boom(name, *a, **k):
            if name == "tools.workflow.failure_triage":
                raise ImportError("simulated")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _boom)
        r = reflex.run({}, None)
        assert r["scanned"] == 0
        assert r["applied"] == 0
        assert "error" in r

    def test_resolve_and_precision_results_included(self, reflex, monkeypatch):
        """arc-cal-02: reflex return dict includes resolved count and
        precision cohort count."""
        import tools.workflow.failure_triage as _ft

        monkeypatch.setattr(_ft, "triage_once", lambda **k: {
            "failures_scanned": 0, "results": [],
            "autofix_enabled": False, "apply_mode": True,
        })
        monkeypatch.setattr(_ft, "resolve_outcomes", lambda **k: {
            "resolved": 3, "error": None, "details": [{"id": "o1", "held": "held"}],
        })
        monkeypatch.setattr(_ft, "rolling_precision", lambda **k: {
            "window_days": 7, "cohorts": [
                {"task_type": "fix", "signature_class": "null_deref",
                 "applied_count": 2, "held_count": 1, "precision": 0.5},
            ],
        })

        r = reflex.run({}, None)
        assert r["resolved"] == 3
        assert r["resolution_error"] is None
        assert r["precision_cohorts"] == 1
        assert r["precision_error"] is None

    def test_resolve_precision_error_surfaces_gracefully(self, reflex, monkeypatch):
        """arc-cal-02: if resolve_outcomes or rolling_precision fail, the
        reflex still returns successfully and surfaces the error field."""
        import tools.workflow.failure_triage as _ft

        monkeypatch.setattr(_ft, "triage_once", lambda **k: {
            "failures_scanned": 0, "results": [],
            "autofix_enabled": False, "apply_mode": True,
        })
        monkeypatch.setattr(_ft, "resolve_outcomes", lambda **k: {
            "resolved": 0, "error": "db_locked", "details": [],
        })
        monkeypatch.setattr(_ft, "rolling_precision", lambda **k: {
            "window_days": 30, "cohorts": [], "error": "db_locked",
        })

        r = reflex.run({}, None)
        assert r["resolved"] == 0
        assert r["resolution_error"] == "db_locked"
        assert r["precision_cohorts"] == 0
        assert r["precision_error"] == "db_locked"
