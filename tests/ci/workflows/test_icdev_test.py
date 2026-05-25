# CUI // SP-CTI
"""Spec-conformance tests for tools/ci/workflows/icdev_test.py."""
from __future__ import annotations

import logging
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci.workflows import icdev_test  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Fakes
# ────────────────────────────────────────────────────────────────────────────


class _State:
    def __init__(self):
        self._d = {}

    def get(self, k, default=None):
        return self._d.get(k, default)

    def save(self, *_a, **_kw):
        pass


class _VCS:
    def __init__(self):
        self.comments = []
        self.is_gitlab = False

    def comment_on_issue(self, issue, body):
        self.comments.append((issue, body))

    def check_pr_exists(self, branch_name):
        return None

    def create_pr(self, **_kw):
        return None


def _logger():
    return logging.getLogger("t")


class _Orchestrator:
    """In-memory stand-in for tools.testing.test_orchestrator."""

    def __init__(self, *, unit, e2e, security, compliance):
        self._unit = unit
        self._e2e = e2e
        self._security = security
        self._compliance = compliance

    def run_tests_with_resolution(self, project_dir, max_attempts):
        return self._unit

    def run_e2e_tests_with_resolution(self, run_id, logger, max_attempts):
        return self._e2e

    def evaluate_security_gate(self, project_dir):
        return self._security

    def evaluate_compliance_gate(self, project_dir):
        return self._compliance


def _wire(monkeypatch, fake_state, fake_vcs, *, orchestrator,
          commit_ok=True, recovery=None):
    monkeypatch.setattr(icdev_test, "setup_logger",
                        lambda *a, **k: _logger())
    monkeypatch.setattr(
        icdev_test.ICDevState, "load",
        classmethod(lambda cls, run_id, logger=None: fake_state),
    )
    monkeypatch.setattr(icdev_test, "VCS", lambda: fake_vcs)
    monkeypatch.setattr(
        icdev_test, "_import_orchestrator", lambda: orchestrator,
    )
    monkeypatch.setattr(
        icdev_test, "commit_changes",
        lambda msg, paths=None: (commit_ok, None if commit_ok else "denied"),
    )
    monkeypatch.setattr(
        icdev_test, "finalize_git_operations", lambda *a, **k: None,
    )

    if recovery is not None:
        monkeypatch.setattr(
            icdev_test, "_try_recover", lambda *a, **k: recovery,
        )


# ────────────────────────────────────────────────────────────────────────────
# format_test_summary
# ────────────────────────────────────────────────────────────────────────────


def test_format_summary_all_passed():
    summary = icdev_test.format_test_summary({
        "unit_tests": [{"passed": True}, {"passed": True}],
        "e2e_tests": {"completed": True},
        "security_gate": {"passed": True},
        "compliance_gate": {"passed": True},
        "all_passed": True,
    })
    assert "## Test Results" in summary
    assert "2/2 passed" in summary
    assert "Security Gate:** PASS" in summary
    assert "Compliance Gate:** PASS" in summary
    assert "Overall:** PASS" in summary


def test_format_summary_unit_failures():
    summary = icdev_test.format_test_summary({
        "unit_tests": [{"passed": True}, {"passed": False}],
        "all_passed": False,
    })
    assert "1/2 passed" in summary
    assert "Overall:** FAIL" in summary


def test_format_summary_e2e_skipped():
    summary = icdev_test.format_test_summary({
        "e2e_tests": {"skipped": True, "reason": "--skip-e2e flag"},
        "all_passed": True,
    })
    assert "Skipped" in summary
    assert "skip-e2e" in summary


def test_format_summary_security_fail():
    summary = icdev_test.format_test_summary({
        "security_gate": {"passed": False},
        "compliance_gate": {"passed": True},
        "all_passed": False,
    })
    assert "Security Gate:** FAIL" in summary
    assert "Compliance Gate:** PASS" in summary


# ────────────────────────────────────────────────────────────────────────────
# run_test_suite
# ────────────────────────────────────────────────────────────────────────────


def test_run_test_suite_happy_path(monkeypatch):
    orch = _Orchestrator(
        unit=[{"passed": True}, {"passed": True}],
        e2e=([{"passed": True}], 1, 0),
        security={"passed": True},
        compliance={"passed": True},
    )
    monkeypatch.setattr(icdev_test, "_import_orchestrator", lambda: orch)
    results = icdev_test.run_test_suite("rid", _logger())
    assert results["all_passed"] is True
    assert results["unit_tests"]
    assert results["security_gate"] == {"passed": True}


def test_run_test_suite_skip_e2e(monkeypatch):
    orch = _Orchestrator(
        unit=[{"passed": True}],
        e2e=None,
        security={"passed": True},
        compliance={"passed": True},
    )
    monkeypatch.setattr(icdev_test, "_import_orchestrator", lambda: orch)
    results = icdev_test.run_test_suite("rid", _logger(), skip_e2e=True)
    assert results["e2e_tests"] == {"skipped": True, "reason": "--skip-e2e flag"}
    assert results["all_passed"] is True


def test_run_test_suite_swallows_security_gate_exception(monkeypatch):
    class _Boom(_Orchestrator):
        def evaluate_security_gate(self, project_dir):
            raise RuntimeError("bandit crashed")

    monkeypatch.setattr(
        icdev_test, "_import_orchestrator",
        lambda: _Boom(
            unit=[{"passed": True}],
            e2e=([], 0, 0),
            security={"passed": True},
            compliance={"passed": True},
        ),
    )
    results = icdev_test.run_test_suite("rid", _logger())
    # security_gate is None on exception; suite still completes
    assert results["security_gate"] is None


def test_run_test_suite_unit_driver_exception_marks_fail(monkeypatch):
    class _Boom(_Orchestrator):
        def run_tests_with_resolution(self, project_dir, max_attempts):
            raise RuntimeError("pytest crashed")

    monkeypatch.setattr(
        icdev_test, "_import_orchestrator",
        lambda: _Boom(
            unit=[{"passed": True}],
            e2e=([], 0, 0),
            security={"passed": True},
            compliance={"passed": True},
        ),
    )
    results = icdev_test.run_test_suite("rid", _logger())
    assert results["all_passed"] is False
    assert results["unit_tests"][0]["passed"] is False


# ────────────────────────────────────────────────────────────────────────────
# main
# ────────────────────────────────────────────────────────────────────────────


def test_main_missing_args_returns_one(capsys):
    rc = icdev_test.main(["icdev_test.py"])
    assert rc == 1
    assert "Usage" in capsys.readouterr().out


def test_main_happy_path_returns_zero(monkeypatch):
    state = _State()
    vcs = _VCS()
    orch = _Orchestrator(
        unit=[{"passed": True}],
        e2e=([], 0, 0),
        security={"passed": True},
        compliance={"passed": True},
    )
    _wire(monkeypatch, state, vcs, orchestrator=orch)
    rc = icdev_test.main(["icdev_test.py", "9", "rid"])
    assert rc == 0
    bodies = [b for _, b in vcs.comments]
    assert any("Starting test suite" in b for b in bodies)
    assert any("all passed" in b for b in bodies)


def test_main_failure_with_recovery_returns_zero(monkeypatch):
    state = _State()
    vcs = _VCS()
    orch = _Orchestrator(
        unit=[{"passed": False}],
        e2e=([], 0, 0),
        security={"passed": True},
        compliance={"passed": True},
    )
    _wire(monkeypatch, state, vcs, orchestrator=orch, recovery=True)
    rc = icdev_test.main(["icdev_test.py", "9", "rid"])
    assert rc == 0


def test_main_failure_no_recovery_returns_one(monkeypatch):
    state = _State()
    vcs = _VCS()
    orch = _Orchestrator(
        unit=[{"passed": False}],
        e2e=([], 0, 0),
        security={"passed": True},
        compliance={"passed": True},
    )
    _wire(monkeypatch, state, vcs, orchestrator=orch, recovery=False)
    rc = icdev_test.main(["icdev_test.py", "9", "rid"])
    assert rc == 1
    bodies = [b for _, b in vcs.comments]
    assert any("some failures" in b for b in bodies)
