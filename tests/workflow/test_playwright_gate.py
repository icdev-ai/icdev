# CUI // SP-CTI
"""Phase 2b — per-task Playwright E2E gate.

Record-only + enforce-gated, degrading gracefully to the existing Selenium path.
"""
import os

import pytest

from tools.workflow import validated_commit as vc


# --- spec mapping heuristic -------------------------------------------------
def test_specs_backend_only_diff_returns_empty():
    assert vc._playwright_specs_for_changed_files(
        ["tools/kanban/task_factory.py", "tools/db/storage.py"]
    ) == []


def test_specs_app_py_too_broad_returns_empty():
    assert vc._playwright_specs_for_changed_files(
        ["tools/dashboard/app.py", "tools/dashboard/templates/chat/index.html"]
    ) == []


def test_specs_base_layout_too_broad_returns_empty():
    assert vc._playwright_specs_for_changed_files(
        ["tools/dashboard/templates/base.html"]
    ) == []


def test_specs_maps_changed_ui_to_existing_spec():
    # chat.spec.ts exists in tests/e2e; a chat template change maps to it.
    specs = vc._playwright_specs_for_changed_files(
        ["tools/dashboard/templates/chat/index.html"]
    )
    assert any(os.path.basename(s).startswith("chat") for s in specs)
    assert len(specs) <= 2


# --- _run_playwright graceful degradation ----------------------------------
def test_run_playwright_no_specs_is_not_run():
    passed, _reason, _m = vc._run_playwright(".", [], 120.0)
    assert passed is None


def test_run_playwright_exception_is_not_run(monkeypatch):
    er = pytest.importorskip("tools.testing.e2e_runner")

    def _boom(*a, **k):
        raise RuntimeError("npx missing")

    monkeypatch.setattr(er, "run_playwright_native", _boom)
    passed, reason, _m = vc._run_playwright(".", ["tests/e2e/chat.spec.ts"], 120.0)
    assert passed is None
    assert "unavailable" in reason or "skipped" in reason


def test_run_playwright_pass(monkeypatch):
    er = pytest.importorskip("tools.testing.e2e_runner")

    class _R:
        passed = True

    monkeypatch.setattr(er, "run_playwright_native", lambda *a, **k: [_R(), _R()])
    passed, _reason, _m = vc._run_playwright(".", ["tests/e2e/chat.spec.ts"], 120.0)
    assert passed is True


def test_run_playwright_fail(monkeypatch):
    er = pytest.importorskip("tools.testing.e2e_runner")

    class _R:
        def __init__(self, p):
            self.passed = p

    monkeypatch.setattr(er, "run_playwright_native", lambda *a, **k: [_R(True), _R(False)])
    passed, _reason, _m = vc._run_playwright(".", ["tests/e2e/chat.spec.ts"], 120.0)
    assert passed is False


def test_run_playwright_empty_results_is_not_run(monkeypatch):
    er = pytest.importorskip("tools.testing.e2e_runner")
    monkeypatch.setattr(er, "run_playwright_native", lambda *a, **k: [])
    passed, _reason, _m = vc._run_playwright(".", ["tests/e2e/chat.spec.ts"], 120.0)
    assert passed is None


# --- _run_e2e branch + Selenium fallback -----------------------------------
def _pass_preconditions(monkeypatch):
    """Get past _run_e2e's dashboard-up + route-smoke + kanban-API guards."""
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: None)
    monkeypatch.setattr(
        vc, "_run_route_smoke",
        lambda mf: (True, "smoke ok", {"smoke_ran": True, "smoke_passed": True, "smoke_failures": []}),
    )


_UI = ["tools/dashboard/templates/chat/index.html"]


def test_e2e_playwright_pass_records_playwright(monkeypatch):
    _pass_preconditions(monkeypatch)
    monkeypatch.setattr(vc, "_playwright_specs_for_changed_files", lambda mf: ["tests/e2e/chat.spec.ts"])
    monkeypatch.setattr(vc, "_run_playwright", lambda *a, **k: (True, "chat.spec.ts:pass", {}))
    ok, _reason, metrics = vc._run_e2e(".", True, modified_files=_UI)
    assert ok is True
    assert metrics["e2e_passed"] is True
    assert metrics["e2e_engine"] == "playwright"


def test_e2e_playwright_fail_record_only_when_enforce_off(monkeypatch):
    _pass_preconditions(monkeypatch)
    monkeypatch.setattr(vc, "_playwright_specs_for_changed_files", lambda mf: ["tests/e2e/chat.spec.ts"])
    monkeypatch.setattr(vc, "_run_playwright", lambda *a, **k: (False, "chat.spec.ts:fail", {}))
    monkeypatch.setattr(vc, "_pipeline_enforce", lambda: False)
    ok, _reason, metrics = vc._run_e2e(".", True, modified_files=_UI)
    assert ok is True  # record-only — does NOT block
    assert metrics["e2e_passed"] is False
    assert metrics["e2e_engine"] == "playwright"


def test_e2e_playwright_fail_blocks_when_enforce_on(monkeypatch):
    _pass_preconditions(monkeypatch)
    monkeypatch.setattr(vc, "_playwright_specs_for_changed_files", lambda mf: ["tests/e2e/chat.spec.ts"])
    monkeypatch.setattr(vc, "_run_playwright", lambda *a, **k: (False, "chat.spec.ts:fail", {}))
    monkeypatch.setattr(vc, "_pipeline_enforce", lambda: True)
    ok, reason, _m = vc._run_e2e(".", True, modified_files=_UI)
    assert ok is False  # blocks under enforcement
    assert "Playwright FAILED" in reason


def test_e2e_no_spec_falls_back_to_selenium(monkeypatch):
    _pass_preconditions(monkeypatch)
    monkeypatch.setattr(vc, "_playwright_specs_for_changed_files", lambda mf: [])
    calls = {}

    class _R:
        returncode = 0
        stdout = ""

    def _fake_run(cmd, *a, **k):
        calls["cmd"] = cmd
        return _R()

    monkeypatch.setattr(vc.subprocess, "run", _fake_run)
    _ok, _reason, metrics = vc._run_e2e(".", True, modified_files=_UI)
    assert metrics["e2e_engine"] == "selenium"
    assert calls.get("cmd") == ["python", "tests/e2e_kanban_depends_on.py"]


def test_e2e_playwright_notrun_falls_back_to_selenium(monkeypatch):
    _pass_preconditions(monkeypatch)
    monkeypatch.setattr(vc, "_playwright_specs_for_changed_files", lambda mf: ["tests/e2e/chat.spec.ts"])
    monkeypatch.setattr(vc, "_run_playwright", lambda *a, **k: (None, "npx missing", {}))
    calls = {}

    class _R:
        returncode = 0
        stdout = ""

    def _fake_run(cmd, *a, **k):
        calls["cmd"] = cmd
        return _R()

    monkeypatch.setattr(vc.subprocess, "run", _fake_run)
    _ok, _reason, metrics = vc._run_e2e(".", True, modified_files=_UI)
    assert metrics["e2e_engine"] == "selenium"
    assert calls.get("cmd") == ["python", "tests/e2e_kanban_depends_on.py"]
