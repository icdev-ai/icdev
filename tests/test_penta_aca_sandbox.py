# CUI // SP-CTI
"""penta-aca-02 — FORGE Academy code_runner sandbox-escape regression tests.

Covers the three named escapes from the task and confirms legitimate lesson
starter code still runs through the runner:

  1. `import os; print(os.environ)` — must NOT leak secrets (scrubbed env).
  2. `urllib.request` egress — must be blocked at the import allowlist.
  3. `open('/etc/passwd')` — absolute-path file access must be blocked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.forge_academy.code_runner import (
    _check_code_safety,
    run_code,
)

_TIER1 = Path(__file__).resolve().parent.parent / "apps" / "forge_academy" / "content" / "tier1"


# ---------------------------------------------------------------------------
# Escape 1 — secret exfiltration via os.environ
# ---------------------------------------------------------------------------

def test_os_environ_does_not_leak_secrets(monkeypatch):
    """`import os; print(os.environ)` runs (os is allowed) but the scrubbed
    subprocess env must not carry any secret from the parent process."""
    secret = "TOPSECRET_PENTA_ACA_02_PW"
    monkeypatch.setenv("ICDEV_PG_PASSWORD", secret)
    monkeypatch.setenv("OPENAI_API_KEY", secret + "_APIKEY")

    result = run_code("import os\nprint(dict(os.environ))")
    blob = result["stdout"] + result["stderr"]
    assert secret not in blob, "secret leaked through scrubbed environment"
    assert "ICDEV_PG_PASSWORD" not in blob
    assert "OPENAI_API_KEY" not in blob


def test_os_environ_get_password_is_empty(monkeypatch):
    monkeypatch.setenv("ICDEV_PG_PASSWORD", "SHOULD_NOT_APPEAR_XYZ")
    result = run_code(
        "import os\nprint('PW=' + repr(os.environ.get('ICDEV_PG_PASSWORD')))"
    )
    assert "SHOULD_NOT_APPEAR_XYZ" not in result["stdout"]
    assert "PW=None" in result["stdout"]


# ---------------------------------------------------------------------------
# Escape 2 — network egress via urllib.request (and other network modules)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("snippet", [
    "import urllib.request",
    "import urllib.request as u",
    "from urllib.request import urlopen",
    "from urllib import request",
    "import socket",
    "import requests",
    "import httpx",
    "import aiohttp",
])
def test_network_egress_blocked(snippet):
    result = run_code(snippet)
    assert result["passed"] is False
    assert result.get("error") == "blocked"
    assert result["exit_code"] == -1
    assert result["stdout"] == ""


def test_urllib_parse_still_allowed():
    """urllib.parse is the one explicitly-allowed urllib submodule."""
    result = run_code(
        "from urllib.parse import quote\nprint(quote('a b'))"
    )
    assert result["passed"] is True
    assert "a%20b" in result["stdout"]


# ---------------------------------------------------------------------------
# Escape 3 — arbitrary file read via open() on an absolute path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("snippet", [
    "open('/etc/passwd')",
    "open('/etc/passwd').read()",
    'open("C:/Windows/System32/drivers/etc/hosts")',
    r"open('C:\\Windows\\win.ini')",
    "open('../../../etc/passwd')",
    "import os\nos.open('/etc/passwd', os.O_RDONLY)",
])
def test_absolute_and_traversal_file_open_blocked(snippet):
    result = run_code(snippet)
    assert result["passed"] is False
    assert result.get("error") == "blocked"
    assert result["exit_code"] == -1


def test_relative_open_in_sandbox_allowed():
    """A relative open inside the isolated cwd is fine — learner file I/O works."""
    result = run_code(
        "with open('scratch.txt', 'w') as fh:\n"
        "    fh.write('hi')\n"
        "with open('scratch.txt') as fh:\n"
        "    print(fh.read())"
    )
    assert result["passed"] is True
    assert "hi" in result["stdout"]


# ---------------------------------------------------------------------------
# Dynamic-import / eval escapes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("snippet", [
    "__import__('os').environ",
    "import importlib",
    "importlib.import_module('os')",
    "eval('1+1')",
    "exec('x = 1')",
    "compile('1', '<s>', 'eval')",
    "import subprocess",
    "import os\nos.system('echo hi')",
    "import os\nos.popen('echo hi')",
])
def test_dynamic_and_process_escapes_blocked(snippet):
    result = run_code(snippet)
    assert result["passed"] is False
    assert result.get("error") == "blocked"
    assert result["exit_code"] == -1


# ---------------------------------------------------------------------------
# Allowlist gate unit-level checks
# ---------------------------------------------------------------------------

def test_future_import_is_allowed():
    ok, reason = _check_code_safety(
        "from __future__ import annotations\nimport json\n"
    )
    assert ok is True, reason


def test_syntax_error_passes_gate_but_fails_at_runtime():
    """Unparseable code cannot execute, so the gate lets it through and the
    subprocess reports the SyntaxError — no code runs."""
    ok, _ = _check_code_safety("def broken(:\n")
    assert ok is True
    result = run_code("def broken(:\n")
    assert result["passed"] is False
    assert "SyntaxError" in result["stderr"] or "invalid syntax" in result["stderr"]


def test_test_code_is_also_gated():
    """A malicious payload smuggled via test_code must be blocked too."""
    result = run_code("x = 1", test_code="import socket")
    assert result["passed"] is False
    assert result.get("error") == "blocked"


# ---------------------------------------------------------------------------
# Legitimate lesson code still runs / passes
# ---------------------------------------------------------------------------

def test_legit_starter_imports_not_blocked():
    """Every tier1 starter's imports must survive the allowlist gate."""
    starters = list(_TIER1.glob("*/steps/*_starter.py"))
    assert starters, "no tier1 starter files found"
    for starter in starters:
        code = starter.read_text(encoding="utf-8")
        ok, reason = _check_code_safety(code)
        assert ok is True, f"{starter.name} wrongly blocked: {reason}"


def test_m01_step1_solution_plus_grader_passes():
    """A COMPLETED M01/step1 solution plus its grader runs clean through the sandbox.

    This asserted that the raw starter passed, and it did — because the grader was
    self-contained, as the original docstring noted: it defined its own
    simulate_llm_call, re-ran its own solution and asserted on its own output, so it
    passed whatever the learner submitted (aca-vv-01). The grader now checks the
    learner's work, so the starter alone correctly FAILS: its TODOs are the exercise.

    The sandbox intent of this test is unchanged — legitimate lesson code must not be
    blocked by the hardened runner and must execute to completion.
    """
    base = _TIER1 / "m01-llm-fundamentals" / "steps"
    starter = (base / "step1_starter.py").read_text(encoding="utf-8")
    test_code = (base / "step1_test.py").read_text(encoding="utf-8")
    solution = starter + (
        "\nresponse = simulate_llm_call(system_prompt, user_message)\n"
        "print(response['content'])\n"
        "print(response['usage']['input_tokens'], response['usage']['output_tokens'])\n"
    )
    result = run_code(solution, test_code=test_code)
    assert result.get("error") != "blocked"
    assert result["passed"] is True, result["stderr"]
    assert "PASS" in result["stdout"]


def test_m01_step1_unfinished_starter_does_not_pass():
    """The TODOs are the exercise; submitting the starter untouched must not pass."""
    base = _TIER1 / "m01-llm-fundamentals" / "steps"
    starter = (base / "step1_starter.py").read_text(encoding="utf-8")
    test_code = (base / "step1_test.py").read_text(encoding="utf-8")
    result = run_code(starter, test_code=test_code)
    assert result.get("error") != "blocked", "must fail on its merits, not the gate"
    assert result["passed"] is False


def test_legit_stdlib_code_runs():
    result = run_code(
        "import json, re, random\n"
        "from typing import Any\n"
        "from collections import Counter\n"
        "print(json.dumps({'ok': True}))\n"
        "print(Counter('aab').most_common(1))"
    )
    assert result["passed"] is True
    assert '"ok": true' in result["stdout"]


# ---------------------------------------------------------------------------
# Timeout still enforced
# ---------------------------------------------------------------------------

def test_timeout_still_enforced(monkeypatch):
    import apps.forge_academy.code_runner as cr
    monkeypatch.setattr(cr, "TIMEOUT_SECONDS", 2)
    result = cr.run_code("while True:\n    pass")
    assert result["passed"] is False
    assert result["exit_code"] == -2
    assert "Time limit" in result["stderr"]
