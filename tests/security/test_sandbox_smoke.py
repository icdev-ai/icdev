"""OPT-57 — tests for tools/maintenance/sandbox_smoke.py + api/sandbox.py helpers.

Covers pure helpers and the exit-code contract without requiring Docker.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.maintenance import sandbox_smoke as ss  # noqa: E402
from tools.dashboard.api import sandbox as api_mod  # noqa: E402


# ---------------------------------------------------------------------------
# run_probe exit-code contract
# ---------------------------------------------------------------------------
def test_run_probe_degraded_when_docker_down():
    """Docker unreachable -> exit_code=1, status=degraded."""
    fake_exec = MagicMock()
    fake_exec.health_check.return_value = {
        "available": False, "docker_running": False,
        "llm_sandbox_installed": True, "runtime": "docker",
    }
    with patch("tools.security.sandbox_executor.SandboxExecutor",
               return_value=fake_exec):
        result = ss.run_probe(write_audit=False)
    assert result["exit_code"] == 1
    assert result["status"] == "degraded"
    assert result["healthy"] is False
    assert result["smoke_passed"] is False


def test_run_probe_healthy_smoke_passes():
    fake_exec = MagicMock()
    fake_exec.health_check.return_value = {
        "available": True, "docker_running": True,
        "llm_sandbox_installed": True, "runtime": "docker",
    }
    fake_result = MagicMock(status="completed", exit_code=0,
                            runtime_ms=50, stdout="ok\n", stderr="", error=None)
    fake_exec.execute.return_value = fake_result
    with patch("tools.security.sandbox_executor.SandboxExecutor",
               return_value=fake_exec):
        result = ss.run_probe(write_audit=False)
    assert result["exit_code"] == 0
    assert result["status"] == "healthy"
    assert result["smoke_passed"] is True


def test_run_probe_smoke_fails_when_exit_nonzero():
    fake_exec = MagicMock()
    fake_exec.health_check.return_value = {
        "available": True, "docker_running": True,
        "llm_sandbox_installed": True, "runtime": "docker",
    }
    fake_result = MagicMock(status="failed", exit_code=1,
                            runtime_ms=10, stdout="", stderr="boom", error="rc=1")
    fake_exec.execute.return_value = fake_result
    with patch("tools.security.sandbox_executor.SandboxExecutor",
               return_value=fake_exec):
        result = ss.run_probe(write_audit=False)
    assert result["exit_code"] == 2
    assert result["status"] == "smoke_failed"
    assert result["smoke_passed"] is False


def test_run_probe_smoke_requires_ok_in_stdout():
    """A successful run with wrong stdout should still fail."""
    fake_exec = MagicMock()
    fake_exec.health_check.return_value = {
        "available": True, "docker_running": True,
        "llm_sandbox_installed": True, "runtime": "docker",
    }
    fake_result = MagicMock(status="completed", exit_code=0,
                            runtime_ms=5, stdout="unexpected\n", stderr="", error=None)
    fake_exec.execute.return_value = fake_result
    with patch("tools.security.sandbox_executor.SandboxExecutor",
               return_value=fake_exec):
        result = ss.run_probe(write_audit=False)
    assert result["exit_code"] == 2
    assert result["smoke_passed"] is False


def test_run_probe_writes_result_shape():
    fake_exec = MagicMock()
    fake_exec.health_check.return_value = {
        "available": True, "docker_running": True,
        "llm_sandbox_installed": True, "runtime": "docker",
    }
    fake_exec.execute.return_value = MagicMock(
        status="completed", exit_code=0, runtime_ms=30,
        stdout="ok\n", stderr="", error=None,
    )
    with patch("tools.security.sandbox_executor.SandboxExecutor",
               return_value=fake_exec):
        result = ss.run_probe(write_audit=False)
    for key in ("probe_at", "health", "smoke", "healthy", "smoke_passed",
                "exit_code", "status", "duration_ms"):
        assert key in result, f"missing {key}"


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------
def test_parse_details_double_encoded():
    """audit_trail details is TEXT, often double-JSON-encoded."""
    inner = json.dumps({"exit_code": 0, "health": {"runtime": "docker"}})
    raw = json.dumps(inner)
    d = api_mod._parse_details(raw)
    assert d["exit_code"] == 0
    assert d["health"]["runtime"] == "docker"


def test_parse_details_dict_passthrough():
    assert api_mod._parse_details({"k": "v"}) == {"k": "v"}


def test_parse_details_none_and_invalid():
    assert api_mod._parse_details(None) == {}
    assert api_mod._parse_details("not json {") == {}


def test_iso_roundtrip():
    from datetime import datetime, timezone
    dt = datetime(2026, 4, 12, tzinfo=timezone.utc)
    assert api_mod._iso(dt) == "2026-04-12T00:00:00+00:00"
    assert api_mod._iso(None) is None
