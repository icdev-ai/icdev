# CUI // SP-CTI
"""pytest gate (_run_pytest) + the record-only / enforce gating in validate_working_tree."""
import importlib
from pathlib import Path

vc = importlib.import_module("tools.workflow.validated_commit")


# ---- _run_pytest unit -------------------------------------------------------
def _write(p: Path, body: str):
    p.write_text(body, encoding="utf-8")


def test_run_pytest_passing(tmp_path):
    _write(tmp_path / "test_ok.py", "def test_ok():\n    assert 1 == 1\n")
    passed, failed = vc._run_pytest(str(tmp_path), ["test_ok.py"], 60)
    assert passed is True
    assert failed == []


def test_run_pytest_failing(tmp_path):
    _write(tmp_path / "test_bad.py", "def test_bad():\n    assert 1 == 2\n")
    passed, failed = vc._run_pytest(str(tmp_path), ["test_bad.py"], 60)
    assert passed is False


def test_run_pytest_no_test_files_is_not_run(tmp_path):
    passed, failed = vc._run_pytest(str(tmp_path), ["tools/foo.py", "README.md"], 60)
    assert passed is None
    assert failed == []


# ---- record-only vs enforce gating -----------------------------------------
def _patch_gates(monkeypatch, pytest_result):
    """Make all other gates pass; pytest returns the given (passed, failed)."""
    monkeypatch.setattr(vc, "_run_codelens", lambda *a, **k: (True, "", {"ruff_issues": 0, "bandit_issues": 0}))
    monkeypatch.setattr(vc, "_run_coherence", lambda *a, **k: (True, ""))
    monkeypatch.setattr(vc, "_run_e2e", lambda *a, **k: (True, "", {"e2e_ran": False, "e2e_passed": None}))
    monkeypatch.setattr(vc, "_run_companion_sync", lambda *a, **k: (True, ""))
    monkeypatch.setattr(vc, "_run_pytest", lambda *a, **k: pytest_result)


def test_pytest_fail_is_record_only_when_enforce_off(monkeypatch, tmp_path):
    monkeypatch.delenv("KANBAN_PIPELINE_ENFORCE", raising=False)
    _patch_gates(monkeypatch, (False, ["tests/x.py::test_y"]))
    ok, reason, metrics = vc.validate_working_tree(
        cwd=str(tmp_path), modified_files=["tests/x.py"],
        compare_to_main=False, run_e2e=True, run_companion=False,
    )
    # record-only: overall still passes, but the failure is recorded
    assert ok is True
    assert metrics["pytest_passed"] is False
    assert metrics["pytest_ran"] is True


def test_pytest_fail_blocks_when_enforce_on(monkeypatch, tmp_path):
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", "1")
    _patch_gates(monkeypatch, (False, ["tests/x.py::test_y"]))
    ok, reason, metrics = vc.validate_working_tree(
        cwd=str(tmp_path), modified_files=["tests/x.py"],
        compare_to_main=False, run_e2e=True, run_companion=False,
    )
    assert ok is False
    assert "UNIT TESTS FAILED" in reason


def test_pytest_notrun_never_blocks(monkeypatch, tmp_path):
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", "1")
    _patch_gates(monkeypatch, (None, []))
    ok, reason, metrics = vc.validate_working_tree(
        cwd=str(tmp_path), modified_files=["tools/foo.py"],
        compare_to_main=False, run_e2e=True, run_companion=False,
    )
    assert ok is True
    assert metrics["pytest_ran"] is False


def test_pipeline_enforce_flag(monkeypatch):
    monkeypatch.delenv("KANBAN_PIPELINE_ENFORCE", raising=False)
    assert vc._pipeline_enforce() is False
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", "1")
    assert vc._pipeline_enforce() is True
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", "true")
    assert vc._pipeline_enforce() is True
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", "0")
    assert vc._pipeline_enforce() is False
