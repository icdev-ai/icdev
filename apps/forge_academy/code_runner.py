# CUI // SP-CTI
"""FORGE Academy sandboxed code runner — subprocess isolation for coding missions.

Hardened per penta-aca-02. Learner submissions are executed in a subprocess with:

  * an **AST allowlist gate** (`_check_code_safety`) that rejects any import whose
    top-level module is not in `_ALLOWED_IMPORTS`, blocks dynamic-import escapes
    (`__import__` / `importlib`), blocks dangerous builtins (`eval`/`exec`/`compile`),
    blocks dangerous `os.*` process calls, and rejects filesystem opens of absolute
    or traversal paths;
  * a **scrubbed environment** — only a safe minimum of non-secret system variables
    is passed through, so `os.environ` can no longer leak `ICDEV_PG_PASSWORD`,
    API keys, or any other secret;
  * an **isolated working directory** (a fresh TemporaryDirectory);
  * interpreter **isolated mode** (`-I`) so no ambient `PYTHONPATH` / user site /
    cwd module can be imported, plus UTF-8 I/O (`-X utf8`);
  * a **wall-clock timeout**; and
  * **POSIX resource caps** (CPU / address-space / file-size) where the platform
    supports them (best-effort; Windows relies on the timeout + AST gate).

The previous denylist substring check was bypassable (e.g. `import os; print(os.environ)`,
`import urllib.request`, `open('/etc/passwd')`); those escapes are now blocked/neutralised.

Sandbox decision recorded in docs/security/sandbox-coverage.md (OPT-58).
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

TIMEOUT_SECONDS = 10

# Real allowlist (enforced by AST, not substring). Top-level module names that a
# learner submission may import. `urllib.parse` is allowed as a specific submodule;
# the rest of `urllib` (notably `urllib.request`) is NOT allowed.
_ALLOWED_IMPORTS = {
    "__future__",
    "json", "os", "sys", "re", "math", "random", "datetime", "time",
    "collections", "itertools", "functools", "pathlib", "io", "base64",
    "hashlib", "hmac", "uuid", "enum", "dataclasses", "typing", "abc",
    "contextlib", "copy", "pprint", "string", "textwrap", "traceback",
    "logging", "struct", "urllib.parse",
}

# Builtins that provide a code-execution / import escape hatch.
_BLOCKED_BUILTINS = {"__import__", "eval", "exec", "compile", "execfile"}

# `os.<attr>` process / shell escape surfaces blocked at the AST layer as defence
# in depth (import of `os` itself stays allowed; `os.environ` is neutralised by the
# scrubbed subprocess env rather than blocked here).
_BLOCKED_OS_ATTRS = {
    "system", "popen", "spawn", "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe", "execl", "execle", "execlp",
    "execlpe", "execv", "execve", "execvp", "execvpe", "startfile", "fork",
    "forkpty", "putenv",
}

# File-open builtins/functions whose first literal argument must not point outside
# the sandbox working directory.
_OPEN_CALLEES = {"open"}  # bare Name calls
_OPEN_ATTR_CALLEES = {("os", "open"), ("io", "open"), ("os", "fdopen")}

# Environment variables that are safe (non-secret) and useful for the child Python
# process to start. Everything else — including all ICDEV_*, *_API_KEY, *_PASSWORD,
# *_TOKEN secrets — is stripped from the child environment.
_SAFE_ENV_PASSTHROUGH = (
    "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT",
    "HOME", "USERPROFILE",
    "LANG", "LC_ALL", "LC_CTYPE",
)


def _literal_path_is_outside_sandbox(node: ast.AST) -> bool:
    """True if `node` is a string/bytes literal that denotes an absolute path,
    a Windows drive/UNC path, or a parent-traversal path."""
    if not isinstance(node, ast.Constant):
        return False
    value = node.value
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", "ignore")
        except Exception:
            return False
    if not isinstance(value, str) or not value:
        return False
    v = value.strip().replace("\\", "/")
    if v.startswith("/"):          # POSIX absolute or UNC (//host/share)
        return True
    if len(v) >= 2 and v[1] == ":":  # Windows drive path e.g. C:/...
        return True
    if v == ".." or v.startswith("../") or "/../" in v or v.endswith("/.."):
        return True
    return False


def _import_module_allowed(dotted: str) -> bool:
    """Allowlist check for a dotted module name. A module is allowed if it (or its
    top-level package) is explicitly listed. `urllib` is only allowed via the
    specific `urllib.parse` entry — bare `urllib` / `urllib.request` are rejected."""
    if dotted in _ALLOWED_IMPORTS:
        return True
    top = dotted.split(".")[0]
    return top in _ALLOWED_IMPORTS


def _check_code_safety(code: str) -> tuple[bool, str]:
    """AST-based allowlist gate. Returns (is_safe, reason)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # Code that does not parse cannot execute — Python compiles the whole
        # module before running any statement, so a SyntaxError means nothing
        # runs. Let it through so the learner sees a real traceback.
        return True, ""

    for node in ast.walk(tree):
        # ---- imports -----------------------------------------------------
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _import_module_allowed(alias.name):
                    return False, f"Import not allowed: `{alias.name}`"
        elif isinstance(node, ast.ImportFrom):
            # Relative imports (level > 0) have no top module we can resolve — reject.
            if node.level and node.level > 0:
                return False, "Relative imports are not allowed"
            module = node.module or ""
            if _import_module_allowed(module):
                continue
            # e.g. `from urllib import parse` — check module.name per imported name.
            all_ok = bool(module) and all(
                _import_module_allowed(f"{module}.{a.name}") for a in node.names
            )
            if not all_ok:
                return False, f"Import not allowed: `from {module or '.'} import ...`"

        # ---- dynamic-import / eval escapes -------------------------------
        elif isinstance(node, ast.Name):
            if node.id in _BLOCKED_BUILTINS:
                return False, f"Blocked builtin: `{node.id}`"

        # ---- attribute-based escapes (os.system, importlib.*, etc.) ------
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name):
                base = node.value.id
                if base == "os" and node.attr in _BLOCKED_OS_ATTRS:
                    return False, f"Blocked call: `os.{node.attr}`"
                if base == "importlib":
                    return False, "Blocked module usage: `importlib`"
                if base == "subprocess":
                    return False, "Blocked module usage: `subprocess`"

        # ---- filesystem escapes: open() on absolute/traversal paths ------
        elif isinstance(node, ast.Call):
            func = node.func
            check_callee = False
            if isinstance(func, ast.Name) and func.id in _OPEN_CALLEES:
                check_callee = True
            elif (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and (func.value.id, func.attr) in _OPEN_ATTR_CALLEES
            ):
                check_callee = True
            if check_callee and node.args and _literal_path_is_outside_sandbox(node.args[0]):
                return False, "File access outside the sandbox is not allowed"

    return True, ""


def _build_scrubbed_env(tmpdir: str) -> dict[str, str]:
    """Minimal, secret-free environment for the child process."""
    env: dict[str, str] = {}
    for key in _SAFE_ENV_PASSTHROUGH:
        val = os.environ.get(key)
        if val:
            env[key] = val
    # Point temp at the isolated sandbox dir so tempfile-based learner code stays in.
    env["TMPDIR"] = tmpdir
    env["TEMP"] = tmpdir
    env["TMP"] = tmpdir
    return env


def _posix_resource_limits():  # pragma: no cover - platform dependent
    """preexec_fn that applies CPU / memory / file-size caps on POSIX. Returns
    None on platforms without the `resource` module (e.g. Windows)."""
    try:
        import resource  # noqa: PLC0415 - POSIX-only, imported lazily
    except Exception:
        return None

    def _apply():
        try:
            resource.setrlimit(resource.RLIMIT_CPU,
                               (TIMEOUT_SECONDS + 2, TIMEOUT_SECONDS + 2))
        except Exception:
            pass
        try:  # Address space: 512 MB.
            cap = 512 * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        except Exception:
            pass
        try:  # Max written file size: 10 MB.
            cap = 10 * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))
        except Exception:
            pass

    return _apply


def run_code(code: str, test_code: str = "") -> dict:
    """Execute learner code in a hardened subprocess sandbox.

    Returns dict with stdout, stderr, passed, exit_code (and `error` when blocked).
    """
    combined = textwrap.dedent(code or "")
    if test_code:
        combined += "\n\n" + textwrap.dedent(test_code)

    # The AST gate inspects the *combined* script — both learner code and any
    # supplied test harness must satisfy the allowlist.
    safe, reason = _check_code_safety(combined)
    if not safe:
        return {"stdout": "", "stderr": reason, "passed": False,
                "error": "blocked", "exit_code": -1}

    with tempfile.TemporaryDirectory(prefix="fa_sandbox_") as tmpdir:
        script = Path(tmpdir) / "solution.py"
        script.write_text(combined, encoding="utf-8")
        env = _build_scrubbed_env(tmpdir)
        preexec = _posix_resource_limits() if os.name != "nt" else None
        popen_kwargs: dict = {
            "capture_output": True,
            "text": True,
            "timeout": TIMEOUT_SECONDS,
            "cwd": tmpdir,
            "env": env,
        }
        if preexec is not None:
            popen_kwargs["preexec_fn"] = preexec
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-X", "utf8", str(script)],
                **popen_kwargs,
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
