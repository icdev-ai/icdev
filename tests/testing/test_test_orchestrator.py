# CUI // SP-CTI
"""Spec-conformance tests for tools/testing/test_orchestrator.py."""
from __future__ import annotations

import logging
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.testing import test_orchestrator as orch  # noqa: E402
from tools.testing.data_types import (  # noqa: E402
    GateEvaluation,
    GateResult,
    TestResult,
)


def _logger():
    return logging.getLogger("t")


# ────────────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────────────


def test_constants_exposed():
    assert orch.MAX_TEST_RETRY_ATTEMPTS == 4
    assert orch.MAX_E2E_TEST_RETRY_ATTEMPTS == 2


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


def test_find_source_dir_returns_first_with_py_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "x.py").write_text("pass")
    out = orch._find_source_dir(str(tmp_path))
    assert out is not None
    assert "src" in out


def test_find_source_dir_returns_none_when_no_python(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "data.txt").write_text("not py")
    assert orch._find_source_dir(str(tmp_path)) is None


def test_all_python_files_walks_recursive(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.py").write_text("pass")
    (tmp_path / "a" / "y.txt").write_text("not py")
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "b" / "z.py").write_text("pass")
    out = orch._all_python_files(str(tmp_path / "a"))
    assert len(out) == 2


# ────────────────────────────────────────────────────────────────────────────
# run_py_compile
# ────────────────────────────────────────────────────────────────────────────


def test_run_py_compile_no_source(tmp_path):
    out = orch.run_py_compile(str(tmp_path), _logger())
    assert out.passed is True
    assert "no source files" in out.execution_command.lower()


# ────────────────────────────────────────────────────────────────────────────
# run_pytest / run_behave
# ────────────────────────────────────────────────────────────────────────────


def test_run_pytest_handles_file_not_found(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise FileNotFoundError("no pytest")

    monkeypatch.setattr(orch.subprocess, "run", boom)
    results, p, f = orch.run_pytest(str(tmp_path), _logger())
    assert results == []
    assert p == 0 and f == 0


def test_run_behave_skips_when_no_features_dir(tmp_path):
    results, p, f = orch.run_behave(str(tmp_path), _logger())
    assert results == []
    assert p == 0 and f == 0


# ────────────────────────────────────────────────────────────────────────────
# run_tests_with_resolution
# ────────────────────────────────────────────────────────────────────────────


def test_run_tests_with_resolution_happy_path(monkeypatch, tmp_path):
    monkeypatch.setattr(
        orch, "run_py_compile",
        lambda *a, **k: TestResult(
            test_name="syn", passed=True,
            execution_command="x", test_purpose="x",
        ),
    )
    monkeypatch.setattr(
        orch, "run_ruff",
        lambda *a, **k: TestResult(
            test_name="ruff", passed=True,
            execution_command="x", test_purpose="x",
        ),
    )
    monkeypatch.setattr(
        orch, "run_sandbox_isolation",
        lambda *a, **k: TestResult(
            test_name="sb", passed=True,
            execution_command="x", test_purpose="x",
        ),
    )
    monkeypatch.setattr(
        orch, "run_pytest", lambda *a, **k: ([], 0, 0),
    )
    monkeypatch.setattr(
        orch, "run_behave", lambda *a, **k: ([], 0, 0),
    )
    monkeypatch.setattr(
        orch, "run_bandit",
        lambda *a, **k: TestResult(
            test_name="bandit", passed=True,
            execution_command="x", test_purpose="x",
        ),
    )

    results, p, f = orch.run_tests_with_resolution(
        str(tmp_path), run_id="rid", logger=_logger(),
    )
    assert f == 0
    assert all(r.passed for r in results)


def test_run_tests_with_resolution_retries_on_failure(monkeypatch, tmp_path):
    attempts = {"count": 0}

    def fake_pytest(*a, **k):
        attempts["count"] += 1
        # Fail every attempt
        return [TestResult(
            test_name="t1", passed=False,
            execution_command="x", test_purpose="x",
        )], 0, 1

    monkeypatch.setattr(
        orch, "run_py_compile",
        lambda *a, **k: TestResult(
            test_name="syn", passed=True,
            execution_command="x", test_purpose="x",
        ),
    )
    monkeypatch.setattr(
        orch, "run_ruff",
        lambda *a, **k: TestResult(
            test_name="ruff", passed=True,
            execution_command="x", test_purpose="x",
        ),
    )
    monkeypatch.setattr(
        orch, "run_sandbox_isolation",
        lambda *a, **k: TestResult(
            test_name="sb", passed=True,
            execution_command="x", test_purpose="x",
        ),
    )
    monkeypatch.setattr(orch, "run_pytest", fake_pytest)
    monkeypatch.setattr(orch, "run_behave", lambda *a, **k: ([], 0, 0))
    monkeypatch.setattr(
        orch, "run_bandit",
        lambda *a, **k: TestResult(
            test_name="bandit", passed=True,
            execution_command="x", test_purpose="x",
        ),
    )

    results, _p, f = orch.run_tests_with_resolution(
        str(tmp_path), run_id="rid", logger=_logger(), max_attempts=3,
    )
    assert attempts["count"] == 3  # Tried max_attempts times
    assert f >= 1


# ────────────────────────────────────────────────────────────────────────────
# E2E
# ────────────────────────────────────────────────────────────────────────────


def test_detect_e2e_mode_falls_back_to_mcp(monkeypatch):
    real_import = __import__

    def fake(name, *args, **kwargs):
        if name.startswith("tools.testing.e2e_runner"):
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake)
    assert orch._detect_e2e_mode() == "mcp"


# ────────────────────────────────────────────────────────────────────────────
# Gates
# ────────────────────────────────────────────────────────────────────────────


def test_evaluate_security_gate_swallows_import_error(monkeypatch):
    real_import = __import__

    def fake(name, *args, **kwargs):
        if name.startswith("tools.security."):
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake)
    monkeypatch.delenv("ICDEV_OPENCLAW_ENABLED", raising=False)
    out = orch.evaluate_security_gate("/tmp", _logger())
    assert isinstance(out, GateEvaluation)
    assert len(out.gates) >= 2  # SAST + Secret detection probes


def test_evaluate_compliance_gate_swallows_import_error(monkeypatch):
    real_import = __import__

    def fake(name, *args, **kwargs):
        if name.startswith("tools.compliance."):
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake)
    out = orch.evaluate_compliance_gate("proj-1", "/tmp", _logger())
    assert isinstance(out, GateEvaluation)
    assert any(g.gate_name == "CUI Markings" for g in out.gates)


# ────────────────────────────────────────────────────────────────────────────
# generate_summary
# ────────────────────────────────────────────────────────────────────────────


def test_generate_summary_contains_expected_sections():
    unit = [TestResult(
        test_name="t1", passed=True,
        execution_command="x", test_purpose="x",
    )]
    bdd: list = []
    e2e: list = []
    security = GateEvaluation(
        gate_type="code_review",
        overall_pass=True,
        gates=[GateResult(gate_name="SAST", passed=True)],
    )
    out = orch.generate_summary(unit, bdd, e2e, security, None, _logger())
    assert "## ICDEV™ Test Run Summary" in out
    assert "### Unit Tests" in out
    assert "### Security Gate: PASS" in out
    assert "Overall: PASSED" in out


def test_generate_summary_overall_failed_when_any_failure():
    unit = [TestResult(
        test_name="t1", passed=False,
        execution_command="x", test_purpose="x",
    )]
    out = orch.generate_summary(unit, [], [], None, None, _logger())
    assert "Overall: FAILED" in out


# ────────────────────────────────────────────────────────────────────────────
# main()
# ────────────────────────────────────────────────────────────────────────────


def test_main_requires_project_dir(capsys):
    import pytest

    with pytest.raises(SystemExit) as exc_info:
        orch.main([])
    assert exc_info.value.code != 0


def test_main_happy_path_with_stubs(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        orch, "run_tests_with_resolution",
        lambda *a, **k: ([], 0, 0),
    )
    monkeypatch.setattr(
        orch, "run_e2e_tests_with_resolution",
        lambda *a, **k: ([], 0, 0),
    )
    monkeypatch.setattr(
        orch, "evaluate_security_gate",
        lambda *a, **k: GateEvaluation(
            gate_type="code_review", overall_pass=True,
        ),
    )
    monkeypatch.setattr(orch, "_run_coherence_check", lambda *a, **k: None)
    monkeypatch.setattr(orch, "_run_agentic_tests", lambda *a, **k: [])
    monkeypatch.setattr(
        orch, "_run_acceptance_validation",
        lambda *a, **k: None,
    )

    rc = orch.main([
        "--project-dir", str(tmp_path),
        "--skip-e2e",
        "--skip-compliance",
    ])
    assert rc == 0


def test_main_returns_one_on_failures(monkeypatch, tmp_path):
    failing = [TestResult(
        test_name="t1", passed=False,
        execution_command="x", test_purpose="x",
        test_type="unit",
    )]
    monkeypatch.setattr(
        orch, "run_tests_with_resolution",
        lambda *a, **k: (failing, 0, 1),
    )
    monkeypatch.setattr(orch, "_run_coherence_check", lambda *a, **k: None)
    monkeypatch.setattr(orch, "_run_agentic_tests", lambda *a, **k: [])
    monkeypatch.setattr(
        orch, "_run_acceptance_validation",
        lambda *a, **k: None,
    )

    rc = orch.main([
        "--project-dir", str(tmp_path),
        "--skip-e2e",
        "--skip-security",
        "--skip-compliance",
    ])
    assert rc == 1
