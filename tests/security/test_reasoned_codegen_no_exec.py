"""Gap 15 regression: reasoned_codegen wrapper + advisor must stay execution-free.

The wrapper receives LLM-generated code but must NEVER execute it — any
execution is the downstream pipeline's responsibility (covered by its own
sandbox-coverage decision). If a future change wires exec/eval/subprocess into
either module, this test fires, forcing a re-decision in
docs/security/sandbox-coverage.md (Gap 15: bypass-documented -> sandboxed).

AST-based so that string-literal keywords (the advisor scans specs for the
substrings "exec(" / "eval(") are NOT mistaken for real calls.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

_FILES = [
    BASE_DIR / "tools" / "llm" / "reasoned_codegen.py",
    BASE_DIR / "tools" / "llm" / "reasoned_codegen_advisor.py",
]

_BANNED_CALLS = {"exec", "eval", "system", "popen"}
_BANNED_MODULES = {"subprocess", "os.system", "os.popen"}


def _violations(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            # bare exec(...) / eval(...)
            if isinstance(fn, ast.Name) and fn.id in {"exec", "eval"}:
                found.append(fn.id)
            # os.system(...) / os.popen(...) / subprocess.run(...) etc.
            if isinstance(fn, ast.Attribute):
                if fn.attr in {"system", "popen"}:
                    found.append(fn.attr)
                if isinstance(fn.value, ast.Name) and fn.value.id == "subprocess":
                    found.append(f"subprocess.{fn.attr}")
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    found.append("import subprocess")
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            found.append("from subprocess")
    return found


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_no_code_execution(path):
    bad = _violations(path)
    assert not bad, (
        f"{path.name} introduced code execution {bad} — "
        "re-decide docs/security/sandbox-coverage.md Gap 15 (bypass -> sandboxed)"
    )
