# CUI // SP-CTI
"""Regression guard for connection hygiene (chyg-vv-01).

Asserts that the kanban reflex has no `conn = get_connection()` site that is
NEVER closed (the always-leak pattern that drove the kanban_tasks lock storm).
Uses the same AST classification as the chyg audit: a get_connection() assignment
with no matching `.close()` anywhere in the function is a leak.

See docs/features/chyg-audit.md ; memory kanban-tasks-lock-storm.
"""
import ast
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parent.parent
GUARDED = [
    BASE / "tools" / "genesis" / "reflexes" / "kanban.py",
]


def _no_close_sites(path: Path):
    """Return [(func, lineno)] for get_connection() assigns with no close in the function."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    leaks = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        assigns = {}
        closes = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
                f = n.value.func
                name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
                if name in ("get_connection", "get_canvas_connection"):
                    for t in n.targets:
                        if isinstance(t, ast.Name):
                            assigns[t.id] = n.lineno
            if (isinstance(n, ast.Attribute) and n.attr == "close"
                    and isinstance(n.value, ast.Name)):
                closes.add(n.value.id)
        for var, ln in assigns.items():
            # A `with get_connection() as var:` reports no Assign node, so any var
            # here came from a bare assignment; require a close (or it's a leak).
            if var not in closes:
                leaks.append((fn.name, ln, var))
    return leaks


@pytest.mark.parametrize("path", GUARDED, ids=lambda p: p.name)
def test_no_unclosed_get_connection(path):
    assert path.exists(), f"{path} missing"
    leaks = _no_close_sites(path)
    assert not leaks, (
        "Unclosed get_connection() (always-leak) found — use "
        "`with get_connection() as conn:` or a finally-close:\n  "
        + "\n  ".join(f"{fn}() L{ln} var={var}" for fn, ln, var in leaks)
    )
