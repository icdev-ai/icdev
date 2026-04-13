# CUI // SP-CTI
"""Spec-conformance tests for tools/ci/workflows/icdev_sdlc.py."""
from __future__ import annotations

import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci.workflows import icdev_sdlc  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# _attempt_phase_recovery (phase mapping table)
# ────────────────────────────────────────────────────────────────────────────


def test_phase_to_parser_mapping():
    assert icdev_sdlc._PHASE_TO_PARSER["Test"] == "test"
    assert icdev_sdlc._PHASE_TO_PARSER["Build"] == "compile"


def test_recoverable_phases_set():
    assert "Test" in icdev_sdlc.RECOVERABLE_PHASES
    assert "Build" in icdev_sdlc.RECOVERABLE_PHASES
    # Other phases must NOT be recoverable
    assert "Plan" not in icdev_sdlc.RECOVERABLE_PHASES
    assert "Comply" not in icdev_sdlc.RECOVERABLE_PHASES


# ────────────────────────────────────────────────────────────────────────────
# run_phase
# ────────────────────────────────────────────────────────────────────────────


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_phase_success(monkeypatch):
    monkeypatch.setattr(
        icdev_sdlc.subprocess, "run",
        lambda *a, **k: _Proc(returncode=0, stdout="ok"),
    )
    assert icdev_sdlc.run_phase("Plan", "icdev_plan", "1", "rid") is True


def test_run_phase_failure_non_recoverable_returns_false(monkeypatch):
    monkeypatch.setattr(
        icdev_sdlc.subprocess, "run",
        lambda *a, **k: _Proc(returncode=2, stderr="boom"),
    )
    assert icdev_sdlc.run_phase("Plan", "icdev_plan", "1", "rid") is False


def test_run_phase_failure_recoverable_calls_recovery(monkeypatch):
    monkeypatch.setattr(
        icdev_sdlc.subprocess, "run",
        lambda *a, **k: _Proc(returncode=1, stderr="bad"),
    )
    seen = []
    monkeypatch.setattr(
        icdev_sdlc, "_attempt_phase_recovery",
        lambda phase, output, run_id, issue: seen.append(phase) or True,
    )
    assert icdev_sdlc.run_phase("Test", "icdev_test", "5", "rid") is True
    assert seen == ["Test"]


def test_run_phase_recoverable_recovery_failure_returns_false(monkeypatch):
    monkeypatch.setattr(
        icdev_sdlc.subprocess, "run",
        lambda *a, **k: _Proc(returncode=1, stderr="bad"),
    )
    monkeypatch.setattr(
        icdev_sdlc, "_attempt_phase_recovery",
        lambda *a, **k: False,
    )
    assert icdev_sdlc.run_phase("Build", "icdev_build", "5", "rid") is False


# ────────────────────────────────────────────────────────────────────────────
# _attempt_phase_recovery
# ────────────────────────────────────────────────────────────────────────────


def test_attempt_recovery_returns_false_when_engine_missing(monkeypatch):
    """ImportError on RecoveryEngine must degrade to False."""
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("tools.ci.core.recovery_engine"):
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    assert icdev_sdlc._attempt_phase_recovery("Test", "log", "rid", "1") is False


# ────────────────────────────────────────────────────────────────────────────
# run_orchestrated
# ────────────────────────────────────────────────────────────────────────────


def test_run_orchestrated_returns_false_on_import_error(monkeypatch):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("tools.agent.team_orchestrator"):
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    assert icdev_sdlc.run_orchestrated("1", "rid") is False


# ────────────────────────────────────────────────────────────────────────────
# main()
# ────────────────────────────────────────────────────────────────────────────


def test_missing_args_returns_one(capsys):
    rc = icdev_sdlc.main(["icdev_sdlc.py"])
    assert rc == 1
    assert "Usage" in capsys.readouterr().out


def test_main_happy_path_runs_all_phases(monkeypatch):
    seen = []

    def fake_phase(name, script, issue, rid, extra_args=None):
        seen.append(name)
        return True

    monkeypatch.setattr(icdev_sdlc, "run_phase", fake_phase)
    monkeypatch.setattr(icdev_sdlc, "_run_coherence_phase", lambda: None)
    monkeypatch.setattr(
        icdev_sdlc, "ensure_run_id", lambda issue, rid: rid or "rid",
    )
    rc = icdev_sdlc.main(["icdev_sdlc.py", "9", "rid"])
    assert rc == 0
    # Plan, Build, Test, E2E, Review, Comply
    assert seen == ["Plan", "Build", "Test", "E2E", "Review", "Comply"]


def test_main_aborts_on_plan_failure(monkeypatch):
    monkeypatch.setattr(
        icdev_sdlc, "run_phase",
        lambda name, *a, **k: False if name == "Plan" else True,
    )
    monkeypatch.setattr(icdev_sdlc, "_run_coherence_phase", lambda: None)
    monkeypatch.setattr(
        icdev_sdlc, "ensure_run_id", lambda issue, rid: rid or "rid",
    )
    rc = icdev_sdlc.main(["icdev_sdlc.py", "9", "rid"])
    assert rc == 1


def test_main_e2e_failure_is_non_blocking(monkeypatch, capsys):
    def fake_phase(name, *a, **k):
        return name != "E2E"

    monkeypatch.setattr(icdev_sdlc, "run_phase", fake_phase)
    monkeypatch.setattr(icdev_sdlc, "_run_coherence_phase", lambda: None)
    monkeypatch.setattr(
        icdev_sdlc, "ensure_run_id", lambda issue, rid: rid or "rid",
    )
    rc = icdev_sdlc.main(["icdev_sdlc.py", "9", "rid"])
    assert rc == 0
    assert "E2E phase had failures" in capsys.readouterr().out


def test_main_orchestrated_success_short_circuits(monkeypatch):
    monkeypatch.setattr(
        icdev_sdlc, "ensure_run_id", lambda issue, rid: rid or "rid",
    )
    monkeypatch.setattr(icdev_sdlc, "run_orchestrated",
                        lambda issue, rid: True)
    seen = []
    monkeypatch.setattr(icdev_sdlc, "run_phase",
                        lambda *a, **k: seen.append(True) or True)
    rc = icdev_sdlc.main(["icdev_sdlc.py", "9", "--orchestrated"])
    assert rc == 0
    # Sequential phases should NOT have been called
    assert seen == []


def test_main_orchestrated_failure_falls_back(monkeypatch):
    monkeypatch.setattr(
        icdev_sdlc, "ensure_run_id", lambda issue, rid: rid or "rid",
    )
    monkeypatch.setattr(icdev_sdlc, "run_orchestrated",
                        lambda issue, rid: False)
    monkeypatch.setattr(icdev_sdlc, "_run_coherence_phase", lambda: None)
    seen = []
    monkeypatch.setattr(
        icdev_sdlc, "run_phase",
        lambda name, *a, **k: seen.append(name) or True,
    )
    rc = icdev_sdlc.main(["icdev_sdlc.py", "9", "rid", "--orchestrated"])
    assert rc == 0
    assert "Plan" in seen and "Comply" in seen
