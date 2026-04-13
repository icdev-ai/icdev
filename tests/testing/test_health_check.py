# CUI // SP-CTI
"""Spec-conformance tests for tools/testing/health_check.py."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.testing import health_check as hc  # noqa: E402
from tools.testing.data_types import CheckResult  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# check_env_vars
# ────────────────────────────────────────────────────────────────────────────


def test_env_vars_passes_when_default_db_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(hc, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("ICDEV_DB_PATH", raising=False)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "icdev.db").write_bytes(b"")
    result = hc.check_env_vars()
    assert result.success is True


def test_env_vars_fails_when_no_db_and_no_env(monkeypatch, tmp_path):
    monkeypatch.setattr(hc, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("ICDEV_DB_PATH", raising=False)
    result = hc.check_env_vars()
    assert result.success is False


def test_env_vars_optional_misses_in_warnings(monkeypatch, tmp_path):
    monkeypatch.setattr(hc, "PROJECT_ROOT", tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "icdev.db").write_bytes(b"")
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    result = hc.check_env_vars()
    assert any("GITLAB_TOKEN" in m for m in result.details["missing_optional"])


# ────────────────────────────────────────────────────────────────────────────
# check_database
# ────────────────────────────────────────────────────────────────────────────


def test_database_failure_when_path_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(hc, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv(
        "ICDEV_DB_PATH", str(tmp_path / "no" / "such.db"),
    )
    result = hc.check_database()
    assert result.success is False
    assert "Database not found" in result.error


def test_database_aggregates_missing_tables(monkeypatch):
    """Stub the connection so list_tables returns a tiny set."""
    class _Cur:
        def __init__(self, rows):
            self._rows = rows

        def execute(self, *_a, **_kw):
            return self

        def fetchall(self):
            return self._rows

    class _Conn:
        def __init__(self, rows):
            self._rows = rows

        def cursor(self):
            return _Cur(self._rows)

        def close(self):
            pass

    rows = [("agents",), ("audit_trail",)]
    monkeypatch.setattr(
        "tools.db.storage.get_connection", lambda: _Conn(rows),
        raising=False,
    )
    monkeypatch.setattr(hc, "_list_tables",
                        lambda conn: ["agents", "audit_trail"])
    monkeypatch.delenv("ICDEV_DB_PATH", raising=False)
    monkeypatch.setattr(hc, "PROJECT_ROOT", pathlib.Path("/"))

    # Bypass the file-existence guard by pointing at a non-.db path
    monkeypatch.setenv("ICDEV_DB_PATH", "postgresql://stub")
    result = hc.check_database()
    assert result.success is False
    assert result.details["tables_found"] == 2


# ────────────────────────────────────────────────────────────────────────────
# check_python_deps
# ────────────────────────────────────────────────────────────────────────────


def test_python_deps_happy_path():
    result = hc.check_python_deps()
    # stdlib should always import on the test host
    assert result.success is True


# ────────────────────────────────────────────────────────────────────────────
# check_tools
# ────────────────────────────────────────────────────────────────────────────


def test_check_tools_warn_when_some_missing(monkeypatch):
    def _imp(name):
        if name == "tools.db.init_icdev_db":
            return object()
        raise ImportError(name)

    monkeypatch.setattr(hc.importlib, "import_module", _imp)
    result = hc.check_tools()
    assert result.success is True  # at least one available
    assert "unavailable" in result.details
    assert result.details["available"] == 1


def test_check_tools_fail_when_all_missing(monkeypatch):
    monkeypatch.setattr(
        hc.importlib, "import_module",
        lambda name: (_ for _ in ()).throw(ImportError(name)),
    )
    result = hc.check_tools()
    assert result.success is False


# ────────────────────────────────────────────────────────────────────────────
# check_mcp_servers
# ────────────────────────────────────────────────────────────────────────────


def test_mcp_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(hc, "PROJECT_ROOT", tmp_path)
    result = hc.check_mcp_servers()
    assert result.success is False


def test_mcp_parse_error(monkeypatch, tmp_path):
    monkeypatch.setattr(hc, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".mcp.json").write_text("{not json", encoding="utf-8")
    result = hc.check_mcp_servers()
    assert result.success is False
    assert "parse error" in result.error.lower()


def test_mcp_python_server_script_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(hc, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".mcp.json").write_text(json.dumps({
        "mcpServers": {
            "alpha": {"command": "python", "args": ["scripts/missing.py"]},
        }
    }), encoding="utf-8")
    result = hc.check_mcp_servers()
    assert result.success is False
    assert any("missing.py" in s for s in result.details["invalid_servers"])


def test_mcp_python_server_script_present(monkeypatch, tmp_path):
    monkeypatch.setattr(hc, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".mcp.json").write_text(json.dumps({
        "mcpServers": {
            "alpha": {"command": "python", "args": ["scripts/server.py"]},
        }
    }), encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "server.py").write_text("# server")
    result = hc.check_mcp_servers()
    assert result.success is True
    assert "alpha" in result.details["valid_servers"]


def test_mcp_non_python_command_passes(monkeypatch, tmp_path):
    monkeypatch.setattr(hc, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".mcp.json").write_text(json.dumps({
        "mcpServers": {
            "alpha": {"command": "node", "args": ["server.js"]},
        }
    }), encoding="utf-8")
    result = hc.check_mcp_servers()
    assert result.success is True


# ────────────────────────────────────────────────────────────────────────────
# check_git_repo
# ────────────────────────────────────────────────────────────────────────────


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_git_repo_success_with_remote(monkeypatch):
    monkeypatch.setattr(
        hc.subprocess, "run",
        lambda *a, **k: _Proc(stdout="https://github.com/o/r.git\n"),
    )
    result = hc.check_git_repo()
    assert result.success is True
    assert result.details["has_remote"] is True


def test_git_repo_warning_when_no_remote(monkeypatch):
    monkeypatch.setattr(
        hc.subprocess, "run",
        lambda *a, **k: _Proc(returncode=1, stderr="no upstream"),
    )
    result = hc.check_git_repo()
    assert result.success is True
    assert "No git remote" in result.warning


def test_git_repo_failure_when_git_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(hc.subprocess, "run", boom)
    result = hc.check_git_repo()
    assert result.success is False
    assert "Git" in result.error


# ────────────────────────────────────────────────────────────────────────────
# check_claude_code
# ────────────────────────────────────────────────────────────────────────────


def test_claude_code_skipped_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = hc.check_claude_code()
    assert result.success is True
    assert result.details["skipped"] is True


def test_claude_code_handles_file_not_found(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(
        hc.subprocess, "run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()),
    )
    result = hc.check_claude_code()
    assert result.success is False
    assert "not found" in result.error.lower()


def test_claude_code_handles_timeout(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=10)

    monkeypatch.setattr(hc.subprocess, "run", boom)
    result = hc.check_claude_code()
    assert result.success is False
    assert "timed out" in result.error.lower()


# ────────────────────────────────────────────────────────────────────────────
# check_playwright
# ────────────────────────────────────────────────────────────────────────────


def test_playwright_handles_missing_npx(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("npx not found")

    monkeypatch.setattr(hc.subprocess, "run", boom)
    monkeypatch.setattr(
        "tools.compat.platform_utils.get_npx_cmd", lambda: "npx",
        raising=False,
    )
    result = hc.check_playwright()
    assert result.success is True
    assert "npx" in (result.warning or "")


def test_playwright_returns_native_mode_when_specs_present(monkeypatch, tmp_path):
    monkeypatch.setattr(hc, "PROJECT_ROOT", tmp_path)
    e2e = tmp_path / "tests" / "e2e"
    e2e.mkdir(parents=True)
    (e2e / "smoke.spec.ts").write_text("// spec")

    monkeypatch.setattr(
        "tools.compat.platform_utils.get_npx_cmd", lambda: "npx",
        raising=False,
    )
    monkeypatch.setattr(
        hc.subprocess, "run",
        lambda *a, **k: _Proc(stdout="Version 1.50.0"),
    )
    result = hc.check_playwright()
    assert result.success is True
    assert result.details["mode"] == "native"
    assert result.details["native_test_count"] == 1


# ────────────────────────────────────────────────────────────────────────────
# run_health_check + main
# ────────────────────────────────────────────────────────────────────────────


def test_run_health_check_swallows_crashing_check(monkeypatch):
    def boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(hc, "_HEALTH_CHECKS", {"explosion": boom})
    result = hc.run_health_check()
    assert result.success is False
    assert "explosion" in result.checks
    assert "Check crashed" in result.checks["explosion"].error


def test_main_returns_zero_on_healthy(monkeypatch, capsys):
    monkeypatch.setattr(
        hc, "run_health_check",
        lambda: hc.HealthCheckResult(
            success=True,
            timestamp="t",
            checks={"x": CheckResult(success=True)},
        ),
    )
    rc = hc.main(["--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"success": true' in out


def test_main_returns_one_on_unhealthy(monkeypatch, capsys):
    monkeypatch.setattr(
        hc, "run_health_check",
        lambda: hc.HealthCheckResult(
            success=False,
            timestamp="t",
            checks={"x": CheckResult(success=False, error="boom")},
        ),
    )
    rc = hc.main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert "UNHEALTHY" in out
