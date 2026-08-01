#!/usr/bin/env python3
# [TEMPLATE: CUI // SP-CTI]
"""Shared detector for swallowed persistence writes (``except Exception: pass`` over an INSERT).

Both the fixer (:mod:`tools.refactor.fix_swallowed_persistence`) and the gate
(``coherence_checker.check_swallowed_persistence``) import from here so they can
never disagree about what counts as a violation.

What is flagged: a ``try`` whose body contains an ``INSERT`` (in a string
literal) and which has a broad handler — ``except Exception``, ``except
BaseException``, or a bare ``except`` — whose body is exactly ``pass``.

What is *not* flagged: narrow handlers (``except sqlite3.OperationalError``),
handlers that re-raise or return, and handlers that already log. Best-effort
behaviour is fine; silent best-effort behaviour is not.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

#: Import line the fixer adds when a module has no logger yet.
LOGGER_IMPORT = "from tools.logging.icdev_logger import get_logger"

#: SQL write verbs that make a silent failure a data-loss bug rather than noise.
_INSERT_RE = re.compile(r"\binsert\s+(?:or\s+\w+\s+)?into\s+([A-Za-z_][\w.]*)", re.IGNORECASE)

#: Path fragments never scanned.
#:   migrations/ — a failed migration is caught by the migration runner, and the
#:                 card that commissioned this check scoped them out explicitly.
#:   tests/      — a test that swallows is a test problem, not a persistence bug.
_EXCLUDED_PARTS = ("__pycache__", "migrations", "tests", ".tmp", "node_modules")

#: Modules that describe the pattern rather than commit it. Excluding them keeps
#: the detector from flagging its own documentation and fixture strings.
_SELF_REFERENTIAL = (
    "tools/refactor/swallowed_persistence.py",
    "tools/refactor/fix_swallowed_persistence.py",
    "tools/workflow/coherence_checker.py",
    "icdev/tools/refactor/swallowed_persistence.py",
    "icdev/tools/refactor/fix_swallowed_persistence.py",
    "icdev/tools/workflow/coherence_checker.py",
)


@dataclass
class SwallowSite:
    """One ``except Exception: pass`` guarding a write."""

    path: Path
    rel: str
    try_lineno: int
    handler_lineno: int
    pass_lineno: int
    handler_name: Optional[str]
    func_name: Optional[str]
    table: Optional[str]
    func_node: Optional[ast.AST] = field(default=None, repr=False)


def read_source(path: Path) -> Tuple[str, bool, str]:
    """Read a file, returning (text-with-LF, had_bom, original_newline).

    A UTF-8 BOM makes :func:`ast.parse` raise, which is exactly how a handful of
    files in this tree escaped every AST-based gate. Read through it rather than
    skipping the file.
    """
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    if "\r\n" in text:
        newline = "\r\n"
        text = text.replace("\r\n", "\n")
    else:
        newline = "\n"
    return text, had_bom, newline


def get_logger_import_line(tree: ast.Module) -> int:
    """1-based end line of an existing top-level ``get_logger`` import, else 0."""
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("logging.icdev_logger"):
            if any(a.name == "get_logger" for a in node.names):
                return node.end_lineno or node.lineno
    return 0


def header_import_line(tree: ast.Module) -> int:
    """1-based end line of the module's *header* import block.

    The last top-level import that precedes the first top-level function or
    class. Anything later is a mid-file import — appending there would put the
    module logger below the code that uses it. Imports after a
    ``sys.path.insert(...)`` are still inside the header block, so the project
    namespace stays importable for standalone CLI execution.
    """
    first_def = None
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            first_def = node.lineno
            break
    last = 0
    for node in tree.body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if first_def is not None and node.lineno > first_def:
            break
        last = max(last, node.end_lineno or node.lineno)
    return last


def _is_excluded(rel: str) -> bool:
    if rel in _SELF_REFERENTIAL:
        return True
    return any(part in _EXCLUDED_PARTS for part in rel.split("/"))


def _string_constants(node: ast.AST) -> Iterable[str]:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub.value


def _insert_table(node: ast.Try) -> Optional[str]:
    """Return the table of the first INSERT found in the try body, if any."""
    for stmt in node.body:
        for text in _string_constants(stmt):
            m = _INSERT_RE.search(text)
            if m:
                return m.group(1)
    return None


def _try_writes(node: ast.Try) -> bool:
    return _insert_table(node) is not None


def _is_broad(handler: ast.ExceptHandler) -> bool:
    exc_type = handler.type
    if exc_type is None:
        return True  # bare `except:`
    names: List[str] = []
    if isinstance(exc_type, ast.Tuple):
        for elt in exc_type.elts:
            if isinstance(elt, ast.Name):
                names.append(elt.id)
            elif isinstance(elt, ast.Attribute):
                names.append(elt.attr)
    elif isinstance(exc_type, ast.Name):
        names.append(exc_type.id)
    elif isinstance(exc_type, ast.Attribute):
        names.append(exc_type.attr)
    return any(n in ("Exception", "BaseException") for n in names)


def _is_bare_pass(handler: ast.ExceptHandler) -> bool:
    return len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass)


def _annotate_scopes(tree: ast.Module) -> dict:
    """Map each Try node to its innermost enclosing function (name, node)."""
    scopes: dict = {}

    def walk(node: ast.AST, fname: Optional[str], fnode: Optional[ast.AST]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, child.name, child)
            else:
                if isinstance(child, ast.Try):
                    scopes[id(child)] = (fname, fnode)
                walk(child, fname, fnode)

    walk(tree, None, None)
    return scopes


def iter_python_files(paths: Iterable[Path], project_root: Path) -> Iterable[Tuple[Path, str]]:
    for base in paths:
        if base.is_file() and base.suffix == ".py":
            candidates = [base]
        else:
            candidates = sorted(base.rglob("*.py"))
        for path in candidates:
            try:
                rel = path.relative_to(project_root).as_posix()
            except ValueError:
                rel = path.as_posix()
            if _is_excluded(rel):
                continue
            yield path, rel


def find_sites(paths: Iterable[Path], project_root: Optional[Path] = None) -> List[SwallowSite]:
    """Scan ``paths`` and return every swallowed-persistence site."""
    root = project_root or Path(__file__).resolve().parents[2]
    sites: List[SwallowSite] = []
    for path, rel in iter_python_files(paths, root):
        try:
            src, _bom, _nl = read_source(path)
            tree = ast.parse(src)
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        scopes = _annotate_scopes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            table = _insert_table(node)
            if table is None:
                continue
            fname, fnode = scopes.get(id(node), (None, None))
            for handler in node.handlers:
                if _is_broad(handler) and _is_bare_pass(handler):
                    sites.append(
                        SwallowSite(
                            path=path,
                            rel=rel,
                            try_lineno=node.lineno,
                            handler_lineno=handler.lineno,
                            pass_lineno=handler.body[0].lineno,
                            handler_name=handler.name,
                            func_name=fname,
                            table=table,
                            func_node=fnode,
                        )
                    )
    return sites
