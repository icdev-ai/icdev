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


# ────────────────────────────────────────────────────────────────────────────
# Standalone script allowlist (oxf-e2e-01)
# ────────────────────────────────────────────────────────────────────────────


def _seed_allowlist(tmp_path, allowlist, excluded=None, scripts=None):
    """Point PROJECT_ROOT at tmp_path; write an allowlist yaml + fake scripts."""
    (tmp_path / "args").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    lines = ["allowlist:"]
    for n in allowlist:
        lines.append(f"  - {n}")
    lines.append("excluded:")
    for n, reason in (excluded or {}).items():
        lines.append(f'  {n}: "{reason}"')
    (tmp_path / "args" / "e2e_script_allowlist.yaml").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    for n in (scripts or []):
        (tmp_path / "tests" / f"{n}.py").write_text("# fake\n", encoding="utf-8")


def test_allowlist_respected_only_allowlisted_scripts(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "PROJECT_ROOT", tmp_path)
    _seed_allowlist(
        tmp_path,
        allowlist=["e2e_good_one", "e2e_good_two"],
        excluded={"e2e_bad": "sqlite3.OperationalError: no such table"},
        scripts=["e2e_good_one", "e2e_good_two", "e2e_bad"],
    )
    out = er.discover_allowlisted_scripts(_logger())
    names = {er.os.path.basename(s)[:-3] for s in out}
    assert names == {"e2e_good_one", "e2e_good_two"}
    assert not any("e2e_bad" in s for s in out)


def test_broken_import_script_excluded_with_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "PROJECT_ROOT", tmp_path)
    _seed_allowlist(
        tmp_path,
        allowlist=["e2e_good"],
        excluded={"e2e_bad": "ModuleNotFoundError: No module named 'x'"},
        scripts=["e2e_good", "e2e_bad"],
    )
    allow = er.load_script_allowlist(_logger())
    excl = er.load_script_exclusions(_logger())
    assert "e2e_bad" not in allow
    assert "e2e_bad" in excl
    assert "ModuleNotFoundError" in excl["e2e_bad"]


def test_missing_allowlist_degrades_with_warning(monkeypatch, tmp_path, caplog):
    # tmp_path has no args/e2e_script_allowlist.yaml
    monkeypatch.setattr(er, "PROJECT_ROOT", tmp_path)
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests" / "e2e_orphan.py").write_text("# x\n", encoding="utf-8")
    with caplog.at_level("WARNING"):
        out = er.discover_allowlisted_scripts(_logger())
    assert out == []
    assert any("allowlist" in r.message.lower() for r in caplog.records)


def test_run_all_selenium_unchanged_without_include_flag(monkeypatch, tmp_path):
    """Default --driver selenium --run-all must NOT touch standalone scripts."""
    monkeypatch.setattr(er, "check_selenium_driver", lambda: True)
    monkeypatch.setattr(
        er, "run_selenium",
        lambda *a, **k: [
            E2ETestResult(test_name="suite", status="passed", test_path="p"),
        ],
    )

    def _boom(*a, **k):  # must not be reached without the flag
        raise AssertionError("discover_allowlisted_scripts called without flag")

    monkeypatch.setattr(er, "discover_allowlisted_scripts", _boom)
    called = {"script": False}

    def _script(*a, **k):
        called["script"] = True
        return []

    monkeypatch.setattr(er, "run_selenium_script", _script)
    rc = er.main(["--driver", "selenium", "--run-all"])
    assert rc == 0
    assert called["script"] is False


def test_run_all_selenium_include_scripts_runs_allowlisted(monkeypatch):
    """--include-scripts appends allowlisted script results to the suite run."""
    monkeypatch.setattr(er, "check_selenium_driver", lambda: True)
    monkeypatch.setattr(
        er, "run_selenium",
        lambda *a, **k: [
            E2ETestResult(test_name="suite", status="passed", test_path="p"),
        ],
    )
    monkeypatch.setattr(
        er, "discover_allowlisted_scripts",
        lambda logger=None: ["tests/e2e_good.py"],
    )
    ran = []

    def _script(run_id, logger, script):
        ran.append(script)
        return [E2ETestResult(test_name="e2e_good", status="passed", test_path=script)]

    monkeypatch.setattr(er, "run_selenium_script", _script)
    rc = er.main(["--driver", "selenium", "--run-all", "--include-scripts"])
    assert rc == 0
    assert ran == ["tests/e2e_good.py"]
