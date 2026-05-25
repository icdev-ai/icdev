"""OPT-58 Gap 2 regression: kanban `_dispatch_via_llm_router` must stay LLM-only.

If a future change wires exec/eval/subprocess into this executor, it would
execute LLM-generated code without sandbox isolation. This test fires the
instant such a change lands, forcing a re-decision in
docs/security/sandbox-coverage.md.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

KANBAN_FILE = BASE_DIR / "tools" / "genesis" / "reflexes" / "kanban.py"


def _extract_dispatch_source() -> str:
    """Return source of _dispatch_via_llm_router + its closures (via AST)."""
    tree = ast.parse(KANBAN_FILE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_dispatch_via_llm_router":
            return ast.unparse(node)
    raise AssertionError("_dispatch_via_llm_router not found in " + str(KANBAN_FILE))


def test_dispatch_function_exists():
    src = _extract_dispatch_source()
    assert "LLMRouter" in src, "dispatch function should call LLMRouter.invoke()"


def test_dispatch_no_exec_builtin():
    src = _extract_dispatch_source()
    # Look for bare `exec(` or `eval(` calls (not `subprocess.exec`, not `.exec(...)` attr)
    bad = re.findall(r"(?<![\w.])(?:exec|eval)\s*\(", src)
    assert not bad, (
        f"OPT-58 Gap 2 breach: {len(bad)} exec/eval calls in "
        f"_dispatch_via_llm_router. Update docs/security/sandbox-coverage.md "
        f"to re-decide sandbox vs. trusted, then update this regression."
    )


def test_dispatch_no_subprocess():
    src = _extract_dispatch_source()
    assert "subprocess." not in src and "from subprocess" not in src, (
        "OPT-58 Gap 2 breach: subprocess usage in _dispatch_via_llm_router. "
        "Re-decide sandboxing in docs/security/sandbox-coverage.md."
    )


def test_dispatch_no_os_system():
    src = _extract_dispatch_source()
    assert "os.system(" not in src and "os.popen(" not in src, (
        "OPT-58 Gap 2 breach: os.system/os.popen in _dispatch_via_llm_router."
    )


def test_sandbox_coverage_doc_exists():
    doc = BASE_DIR / "docs" / "security" / "sandbox-coverage.md"
    assert doc.exists(), "docs/security/sandbox-coverage.md missing"
    body = doc.read_text(encoding="utf-8")
    for gap in ("auto_remediator.py", "_dispatch_via_llm_router",
                "pdf_provider.py", ".tmp/"):
        assert gap in body, f"sandbox-coverage.md missing gap reference: {gap}"
