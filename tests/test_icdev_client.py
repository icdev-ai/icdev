#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/sdk/icdev_client.py."""

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.sdk.icdev_client import ICDEVClient, ICDEVError


# ── Test construction ───────────────────────────────────────────────────

class TestClientInit:
    def test_default_init(self):
        client = ICDEVClient(project_id="proj-123")
        assert client.project_id == "proj-123"
        assert client.timeout == 120
        assert "icdev.db" in client.db_path

    def test_custom_init(self):
        client = ICDEVClient(
            project_id="proj-456",
            project_dir="/tmp/project",
            db_path="/tmp/custom.db",
            timeout=60,
        )
        assert client.project_dir == "/tmp/project"
        assert client.db_path == "/tmp/custom.db"
        assert client.timeout == 60


# ── Test _run ───────────────────────────────────────────────────────────

class TestRun:
    def test_successful_json(self):
        client = ICDEVClient(project_id="proj-123")
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"status": "ok"})
        mock_result.stderr = ""

        with mock.patch("subprocess.run", return_value=mock_result) as mock_run:
            result = client._run("tools/project/project_status.py", ["--project", "proj-123", "--format", "json"])

        assert result == {"status": "ok"}
        # Verify subprocess was called with correct args
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "project_status.py" in cmd[1]
        assert "--json" in cmd

    def test_nonzero_exit_raises(self):
        client = ICDEVClient(project_id="proj-123")
        mock_result = mock.Mock()
        mock_result.returncode = 1
        mock_result.stderr = "Error: project not found"

        with mock.patch("subprocess.run", return_value=mock_result):
            with pytest.raises(ICDEVError) as exc_info:
                client._run("tools/project/project_status.py", ["--project", "proj-123"])

        assert exc_info.value.returncode == 1
        assert "project not found" in str(exc_info.value)

    def test_invalid_json_returns_raw(self):
        client = ICDEVClient(project_id="proj-123")
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Not JSON output"
        mock_result.stderr = ""

        with mock.patch("subprocess.run", return_value=mock_result):
            result = client._run("tools/some/tool.py")

        assert result["raw_output"] == "Not JSON output"


# ── Test method args ────────────────────────────────────────────────────

class TestMethodArgs:
    def _capture_run(self, client, method_name, *args, **kwargs):
        """Call a method and capture the _run args."""
        mock_result = mock.Mock()
        mock_result.returncode = 0
        mock_result.stdout = '{"ok": true}'
        mock_result.stderr = ""

        with mock.patch("subprocess.run", return_value=mock_result) as mock_run:
            method = getattr(client, method_name)
            method(*args, **kwargs)

        call_args = mock_run.call_args[0][0]
        return call_args

    def test_project_status_args(self):
        client = ICDEVClient(project_id="proj-test")
        cmd = self._capture_run(client, "project_status")
        assert "--project" in cmd
        assert "proj-test" in cmd

    def test_generate_ssp_args(self):
        client = ICDEVClient(project_id="proj-test")
        cmd = self._capture_run(client, "generate_ssp")
        assert "--project-id" in cmd
        assert "proj-test" in cmd
        assert "ssp_generator.py" in cmd[1]

    def test_run_sast_with_project_dir(self):
        client = ICDEVClient(project_id="proj-test", project_dir="/my/project")
        cmd = self._capture_run(client, "run_sast")
        assert "--project-dir" in cmd
        assert "/my/project" in cmd

    def test_build_context_with_db(self):
        client = ICDEVClient(project_id="proj-test", db_path="/tmp/test.db")
        cmd = self._capture_run(client, "build_context", directory="/tmp/dir")
        assert "--dir" in cmd
        assert "/tmp/dir" in cmd
        assert "--db" in cmd
        assert "/tmp/test.db" in cmd

    def test_load_manifest_args(self):
        client = ICDEVClient(project_dir="/my/project")
        cmd = self._capture_run(client, "load_manifest")
        assert "--dir" in cmd
        assert "/my/project" in cmd

    def test_generate_pipeline_args(self):
        client = ICDEVClient(project_id="proj-test")
        cmd = self._capture_run(client, "generate_pipeline", platform="github")
        assert "--platform" in cmd
        assert "github" in cmd
        assert "--dry-run" in cmd


# ── Test error class ────────────────────────────────────────────────────

class TestICDEVError:
    def test_error_attributes(self):
        err = ICDEVError("tools/foo.py", 2, "something broke")
        assert err.tool == "tools/foo.py"
        assert err.returncode == 2
        assert err.stderr == "something broke"
        assert "tools/foo.py" in str(err)
        assert "something broke" in str(err)
