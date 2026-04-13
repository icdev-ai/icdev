# CUI // SP-CTI
"""Spec-conformance tests for tools/testing/e2e_runner.py."""
from __future__ import annotations

import logging
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.testing import e2e_runner as er  # noqa: E402
from tools.testing.data_types import E2ETestResult  # noqa: E402


def _logger():
    return logging.getLogger("t")


# ────────────────────────────────────────────────────────────────────────────
# Discovery
# ────────────────────────────────────────────────────────────────────────────


def test_discover_native_tests(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "PROJECT_ROOT", tmp_path)
    e2e = tmp_path / "tests" / "e2e"
    e2e.mkdir(parents=True)
    (e2e / "alpha.spec.ts").write_text("// a")
    (e2e / "beta.spec.ts").write_text("// b")
    out = er.discover_native_tests()
    assert len(out) == 2


def test_discover_mcp_tests(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "PROJECT_ROOT", tmp_path)
    cmds = tmp_path / ".claude" / "commands" / "e2e"
    cmds.mkdir(parents=True)
    (cmds / "smoke.md").write_text("# smoke")
    out = er.discover_mcp_tests()
    assert len(out) == 1


def test_discover_e2e_auto_prefers_native(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "PROJECT_ROOT", tmp_path)
    e2e = tmp_path / "tests" / "e2e"
    e2e.mkdir(parents=True)
    (e2e / "x.spec.ts").write_text("// x")
    cmds = tmp_path / ".claude" / "commands" / "e2e"
    cmds.mkdir(parents=True)
    (cmds / "y.md").write_text("# y")
    assert er.discover_e2e_tests("auto")[0].endswith("x.spec.ts")


def test_discover_e2e_auto_falls_back_to_mcp(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "PROJECT_ROOT", tmp_path)
    cmds = tmp_path / ".claude" / "commands" / "e2e"
    cmds.mkdir(parents=True)
    (cmds / "y.md").write_text("# y")
    out = er.discover_e2e_tests("auto")
    assert out[0].endswith("y.md")


# ────────────────────────────────────────────────────────────────────────────
# parse_test_spec
# ────────────────────────────────────────────────────────────────────────────


def test_parse_test_spec_extracts_description_and_steps(tmp_path):
    p = tmp_path / "spec.md"
    p.write_text(
        "# Smoke test for the dashboard\n"
        "\n"
        "1. Navigate to /\n"
        "2. Click the login button\n"
        "3. Verify the title contains 'ICDEV'\n",
        encoding="utf-8",
    )
    spec = er.parse_test_spec(str(p))
    assert "Smoke test" in spec["description"]
    assert any("navigate" in s.lower() for s in spec["steps"])
    assert any("verify" in a.lower() for a in spec["assertions"])


# ────────────────────────────────────────────────────────────────────────────
# Playwright availability
# ────────────────────────────────────────────────────────────────────────────


def test_check_playwright_returns_false_on_missing_npx(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("npx")

    monkeypatch.setattr(er.subprocess, "run", boom)
    monkeypatch.setattr(er, "_npx_cmd", lambda: "npx")
    assert er.check_playwright_installed() is False


# ────────────────────────────────────────────────────────────────────────────
# _parse_playwright_report
# ────────────────────────────────────────────────────────────────────────────


def test_parse_playwright_report_extracts_results():
    report = {
        "suites": [{
            "title": "dashboard",
            "specs": [{
                "title": "loads",
                "file": "tests/e2e/dashboard.spec.ts",
                "tests": [{
                    "results": [{
                        "status": "passed",
                        "attachments": [
                            {"contentType": "image/png",
                             "path": "/screens/loads.png"},
                        ],
                    }]
                }]
            }]
        }]
    }
    out = er._parse_playwright_report(report, _logger())
    assert len(out) == 1
    assert out[0].passed is True
    assert "/screens/loads.png" in out[0].screenshots


def test_parse_playwright_report_failed_includes_error():
    report = {
        "suites": [{
            "title": "dashboard",
            "specs": [{
                "title": "fails",
                "file": "tests/e2e/x.spec.ts",
                "tests": [{
                    "results": [{
                        "status": "failed",
                        "error": {"message": "boom"},
                        "attachments": [],
                    }]
                }]
            }]
        }]
    }
    out = er._parse_playwright_report(report, _logger())
    assert out[0].passed is False
    assert "boom" in (out[0].error or "")


# ────────────────────────────────────────────────────────────────────────────
# _validate_spec
# ────────────────────────────────────────────────────────────────────────────


def test_validate_spec_passes_with_steps_and_assertions():
    spec = {"steps": ["navigate"], "assertions": ["expect title"]}
    out = er._validate_spec(spec, "x", "/tmp/x.md", _logger())
    assert out.passed is True


def test_validate_spec_fails_with_no_steps():
    spec = {"steps": [], "assertions": ["expect title"]}
    out = er._validate_spec(spec, "x", "/tmp/x.md", _logger())
    assert out.passed is False
    assert "No test steps" in (out.error or "")


# ────────────────────────────────────────────────────────────────────────────
# _run_vision_validation
# ────────────────────────────────────────────────────────────────────────────


def test_vision_validation_skips_when_module_missing(monkeypatch):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("tools.testing.screenshot_validator"):
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    results = [E2ETestResult(test_name="x", status="passed", test_path="x.md")]
    out = er._run_vision_validation(results, _logger())
    assert out is results  # untouched


# ────────────────────────────────────────────────────────────────────────────
# main()
# ────────────────────────────────────────────────────────────────────────────


def test_main_returns_one_on_no_args(capsys):
    rc = er.main([])
    assert rc == 1


def test_main_discover_native_lists_tests(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(er, "PROJECT_ROOT", tmp_path)
    e2e = tmp_path / "tests" / "e2e"
    e2e.mkdir(parents=True)
    (e2e / "smoke.spec.ts").write_text("// smoke\n// click button")
    rc = er.main(["--discover", "--mode", "native"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "smoke" in out


def test_main_run_all_native_does_not_crash_on_args_project(monkeypatch):
    """Regression: the original code referenced args.project_id which
    doesn't exist. Ensure --run-all goes through without AttributeError."""
    monkeypatch.setattr(er, "_resolve_mode", lambda mode: "native")

    def fake_native(run_id, logger, project="chromium", test_file=None):
        # Verify the rewrite passes args.project (not project_id)
        assert project == "chromium"
        return [E2ETestResult(test_name="ok", status="passed", test_path="x.spec.ts")]

    monkeypatch.setattr(er, "run_playwright_native", fake_native)
    rc = er.main(["--run-all"])
    assert rc == 0


def test_main_run_all_native_returns_one_on_failure(monkeypatch):
    monkeypatch.setattr(er, "_resolve_mode", lambda mode: "native")
    monkeypatch.setattr(
        er, "run_playwright_native",
        lambda *a, **k: [
            E2ETestResult(test_name="x", status="failed", test_path="x.spec.ts"),
        ],
    )
    rc = er.main(["--run-all"])
    assert rc == 1


def test_main_test_file_native_path(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "_resolve_mode", lambda mode: "native")
    monkeypatch.setattr(
        er, "run_playwright_native",
        lambda *a, **k: [
            E2ETestResult(
                test_name="single", status="passed", test_path="t.spec.ts",
            )
        ],
    )
    rc = er.main(["--test-file", "tests/e2e/t.spec.ts"])
    assert rc == 0
