# CUI // SP-CTI
"""Tests for tools/agent_toolkit/_shell.py execute_shell primitive."""
from __future__ import annotations

import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agent_toolkit import _shell


def test_execute_shell_success():
    r = _shell.execute_shell(
        [sys.executable, "-c", "print('hello')"],
        audit=False,
    )
    assert r["returncode"] == 0
    assert "hello" in r["stdout"]
    assert r["timed_out"] is False
    assert r["sandboxed"] is False
    assert r["duration_ms"] >= 0


def test_execute_shell_failure():
    r = _shell.execute_shell(
        [sys.executable, "-c", "import sys; sys.exit(3)"],
        audit=False,
    )
    assert r["returncode"] == 3
    assert r["timed_out"] is False


def test_execute_shell_command_not_found():
    r = _shell.execute_shell(
        ["nonexistent_binary_xyzzy_42"],
        audit=False,
    )
    assert r["returncode"] == 127
    assert "not found" in r["stderr"].lower() or "command" in r["stderr"].lower()


def test_execute_shell_stderr_captured():
    r = _shell.execute_shell(
        [sys.executable, "-c", "import sys; sys.stderr.write('err\\n'); sys.exit(0)"],
        audit=False,
    )
    assert "err" in r["stderr"]
    assert r["returncode"] == 0


def test_execute_shell_timeout():
    # Sleep for 5s, timeout at 1s
    r = _shell.execute_shell(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        timeout=1,
        audit=False,
    )
    assert r["timed_out"] is True
    assert r["returncode"] == -9


def test_execute_shell_string_cmd_shlex_parse(tmp_path):
    # Write a trivial script and run it — avoids platform-specific
    # shlex quoting issues with `sys.executable` that contains backslashes
    # on Windows.
    script = tmp_path / "s.py"
    script.write_text("print(1+1)")
    cmd = f'"{sys.executable}" "{script}"'
    r = _shell.execute_shell(cmd, audit=False)
    assert r["returncode"] == 0
    assert "2" in r["stdout"]


def test_execute_shell_cwd(tmp_path):
    (tmp_path / "marker.txt").write_text("here")
    r = _shell.execute_shell(
        [sys.executable, "-c", "import os; print(os.path.exists('marker.txt'))"],
        cwd=str(tmp_path),
        audit=False,
    )
    assert r["returncode"] == 0
    assert "True" in r["stdout"]


def test_execute_shell_result_shape():
    r = _shell.execute_shell(
        [sys.executable, "-c", "pass"],
        audit=False,
    )
    expected_keys = {
        "stdout",
        "stderr",
        "returncode",
        "duration_ms",
        "timed_out",
        "sandboxed",
        "cmd",
        "cwd",
    }
    assert set(r.keys()) >= expected_keys
