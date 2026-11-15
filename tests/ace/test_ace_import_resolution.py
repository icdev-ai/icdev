# CUI // SP-CTI
"""Every name an ACE module imports from another ACE module exists (icx-ace-01).

Commit 36d611a31 rewrote ``event_bus.py`` and dropped six functions that four
callers still imported. Each import sat inside a function, under ``try/except``,
so nothing failed at start-up and nothing went red: ``GET /api/ace/events``
answered 500 for months and completion events were silently lost.

This parses every module under ``icdev/tools/ace/`` and its ``tools/ace/``
mirror, including imports inside functions and ``try`` blocks, and for each
``from icdev.tools.ace.<mod> import <names>`` (or ``tools.ace.<mod>``) imports the
module and asserts every name resolves, as an attribute or a submodule.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TREES = (ROOT / "icdev" / "tools" / "ace", ROOT / "tools" / "ace")
PREFIXES = ("icdev.tools.ace", "tools.ace")


def _ace_imports() -> list[tuple[str, int, str, str]]:
    """(file, line, module, name) for every ACE-to-ACE ``from ... import``."""
    found = []
    for tree in TREES:
        for path in sorted(tree.rglob("*.py")):
            module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(module):
                if not isinstance(node, ast.ImportFrom) or node.level or not node.module:
                    continue
                if not (node.module in PREFIXES
                        or node.module.startswith(tuple(p + "." for p in PREFIXES))):
                    continue
                for alias in node.names:
                    if alias.name != "*":
                        found.append((path.relative_to(ROOT).as_posix(), node.lineno,
                                      node.module, alias.name))
    return found


IMPORTS = _ace_imports()


def test_the_walk_finds_the_imports_that_broke():
    seen = {(mod, name) for _, _, mod, name in IMPORTS}
    # The callers 36d611a31 left dangling, now pointed at the event log.
    assert ("icdev.tools.ace.event_log", "emit") in seen
    assert ("icdev.tools.ace.event_log", "get_pending") not in seen  # imported as a module
    assert ("icdev.tools.ace", "event_log") in seen
    assert len(IMPORTS) > 50


@pytest.mark.parametrize("path, line, module, name", IMPORTS,
                         ids=[f"{p}:{n}:{m}.{a}" for p, n, m, a in IMPORTS])
def test_every_imported_ace_name_resolves(path, line, module, name):
    mod = importlib.import_module(module)
    if hasattr(mod, name):
        return
    try:
        importlib.import_module(f"{module}.{name}")
    except ModuleNotFoundError:
        pytest.fail(f"{path}:{line} imports {name!r} from {module}, which does not define it")
