# CUI // SP-CTI
"""FORGE Academy sandboxed code runner — subprocess isolation for coding missions."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

TIMEOUT_SECONDS = 10

_ALLOWED_IMPORTS = {
    "json", "os", "sys", "re", "math", "random", "datetime", "time",
    "collections", "itertools", "functools", "pathlib", "io", "base64",
    "hashlib", "hmac", "uuid", "enum", "dataclasses", "typing", "abc",
    "contextlib", "copy", "pprint", "string", "textwrap", "traceback",
    "logging", "struct", "urllib.parse",
}

_BLOCKED_PATTERNS = [
    "subprocess", "__import__", "exec(", "eval(", "compile(",
    "os.system", "os.popen", "shutil.rmtree", "open('/'",
    "socket", "requests", "httpx", "aiohttp", "flask", "django",
]


def _check_code_safety(code: str) -> tuple[bool, str]:
    lower = code.lower()
    for pat in _BLOCKED_PATTERNS:
        if pat.lower() in lower:
            return False, f"Blocked pattern detected: `{pat}`"
    return True, ""


def run_code(code: str, test_code: str = "") -> dict:
    """Execute learner code in a subprocess sandbox. Returns dict with stdout, stderr, passed."""
    safe, reason = _check_code_safety(code)
    if not safe:
        return {"stdout": "", "stderr": reason, "passed": False,
                "error": "blocked", "exit_code": -1}

    combined = textwrap.dedent(code)
    if test_code:
        combined += "\n\n" + textwrap.dedent(test_code)

    with tempfile.TemporaryDirectory(prefix="fa_sandbox_") as tmpdir:
        script = Path(tmpdir) / "solution.py"
        script.write_text(combined, encoding="utf-8")
        try:
            result = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                cwd=tmpdir,
            )
            passed = result.returncode == 0
            return {
                "stdout": result.stdout[:4000],
                "stderr": result.stderr[:2000],
                "passed": passed,
                "exit_code": result.returncode,
            }
        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": f"Time limit exceeded ({TIMEOUT_SECONDS}s). Optimize your code.",
                "passed": False,
                "exit_code": -2,
            }
        except Exception as exc:
            return {
                "stdout": "",
                "stderr": str(exc),
                "passed": False,
                "exit_code": -3,
            }
