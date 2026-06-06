# CUI // SP-CTI
"""SIPA security regression — no ``shell=True`` and no execution of fetched code.

This test pins the **bypass-documented** sandbox decision for SIPA's ingest /
fetch path (``docs/security/sandbox-coverage.md`` Gap 16). SIPA stages an
*untrusted target* (local path / UNC share / ``file://`` URI / git clone) into
quarantine and then statically analyzes it. The locked invariant is that the
target is treated as **inert data** — it is copied, hashed, and scanned, but
never executed, imported, ``exec``-ed, or run through a shell.

The two guarantees enforced here, across **every** ``*.py`` under
``tools/integrity/``:

  1. **No ``shell=True``** on any call — subprocess invocations (the git clone
     and the scanner fan-out) must use a fixed argument list with
     ``shell=False``. A shell string is a command-injection vector.
  2. **No execution of fetched code** — no call to a dynamic-execution builtin
     (``exec`` / ``eval`` / ``compile`` / ``execfile`` / ``__import__``) and no
     call to an OS/runtime code-execution helper (``os.system`` / ``os.popen`` /
     ``os.exec*`` / ``os.spawn*`` / ``os.startfile`` / ``runpy.run_*`` /
     ``importlib.import_module`` / ``importlib.reload`` / ``pty.spawn`` /
     ``commands.get*``). If SIPA ever needs to *run* a target, that becomes a
     ``sandboxed`` decision (route through ``tools/security/sandbox_executor.py``)
     and this test must be revisited — not silently weakened.

Detection is **AST-based**, not text-based: the offence is a real *call* node.
This is essential because SIPA is itself a malware scanner — ``scanners.py`` and
``capability_extractor.py`` carry the literal strings ``exec`` / ``eval`` /
``__import__`` / ``pty.spawn`` / ``os.system`` as *detection signatures* for
malicious code in the assessed target. Those string literals and regex patterns
must NOT trip this regression; only genuine calls in SIPA's own code do.

``subprocess.run`` / ``subprocess.Popen`` are deliberately **allowed** — that is
how the git clone and the first-party scanners (bandit/semgrep/...) run, each
with a fixed-arg, ``shell=False`` invocation and the staged tree passed as a
read-only *scan-target path*, never as ``python <staged_file>``. The ``shell``
guarantee (1) is what keeps that surface safe.

Pure stdlib (``ast`` + ``pathlib``); does not import the integrity package, so it
runs without a DB/LLM/conftest dependency.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# tests/ -> repo root -> tools/integrity
_INTEGRITY_DIR = Path(__file__).resolve().parents[1] / "tools" / "integrity"

# Bare-name builtins that execute / import code at runtime.
_BANNED_BUILTINS = {"exec", "eval", "compile", "execfile", "__import__"}

# Dotted helper calls that execute code via the OS / runtime. Exact matches plus
# the ``os.exec*`` / ``os.spawn*`` families (checked by prefix below).
_BANNED_DOTTED = {
    "os.system",
    "os.popen",
    "os.popen2",
    "os.popen3",
    "os.popen4",
    "os.startfile",
    "runpy.run_path",
    "runpy.run_module",
    "importlib.import_module",
    "importlib.reload",
    "importlib.__import__",
    "pty.spawn",
    "commands.getoutput",
    "commands.getstatusoutput",
}
_BANNED_DOTTED_PREFIXES = ("os.exec", "os.spawn")


def _integrity_py_files() -> list[Path]:
    assert _INTEGRITY_DIR.is_dir(), f"tools/integrity not found at {_INTEGRITY_DIR}"
    files = sorted(p for p in _INTEGRITY_DIR.rglob("*.py") if "__pycache__" not in p.parts)
    assert files, "expected at least one .py file under tools/integrity/"
    return files


def _dotted_name(func: ast.AST) -> str | None:
    """Return the dotted call target (``os.system``) for a ``Name``/``Attribute``
    func, or ``None`` if the call target is computed (subscript, call result, ...).
    """
    parts: list[str] = []
    cur = func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        parts.reverse()
        return ".".join(parts)
    return None


def test_no_shell_true_anywhere_under_integrity():
    """No ``shell=True`` keyword on any call under tools/integrity/ (AST)."""
    offenders: list[str] = []
    for py in _integrity_py_files():
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (
                        kw.arg == "shell"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        offenders.append(f"{py.name}:{node.lineno}")
    assert offenders == [], (
        "shell=True is banned across tools/integrity/ (command-injection vector); "
        f"found at: {offenders}"
    )


def test_no_execution_of_fetched_code_under_integrity():
    """No dynamic-exec builtin or OS/runtime exec helper is *called* under
    tools/integrity/. SIPA must never execute the assessed target (AST)."""
    offenders: list[str] = []
    for py in _integrity_py_files():
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Bare-name executors: exec(...), eval(...), __import__(...), ...
            if isinstance(func, ast.Name) and func.id in _BANNED_BUILTINS:
                offenders.append(f"{py.name}:{node.lineno}: {func.id}()")
                continue
            # Dotted executors: os.system(...), runpy.run_path(...), pty.spawn(...)
            dotted = _dotted_name(func)
            if dotted is None:
                continue
            if dotted in _BANNED_DOTTED or dotted.startswith(_BANNED_DOTTED_PREFIXES):
                offenders.append(f"{py.name}:{node.lineno}: {dotted}()")
    assert offenders == [], (
        "tools/integrity/ must never execute the fetched/staged target "
        "(bypass-documented decision, sandbox-coverage.md Gap 16). "
        f"Offending call(s): {offenders}"
    )


def test_integrity_sources_are_parseable():
    """Sanity: every integrity module parses (the AST scans above are meaningful)."""
    for py in _integrity_py_files():
        ast.parse(py.read_text(encoding="utf-8"), filename=str(py))


if __name__ == "__main__":  # pragma: no cover - manual invocation
    raise SystemExit(pytest.main([__file__, "-v"]))
