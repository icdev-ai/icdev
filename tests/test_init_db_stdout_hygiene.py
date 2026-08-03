# CUI // SP-CTI
"""Import-time banner prints must go to stderr, never stdout.

Regression this locks down (gpx-hyg-01):
  The canvas ``db/init_db.py`` modules emitted ~120 ``print("[init_db] ...")``
  banners at IMPORT time. Anything importing ``tools.dashboard.app`` therefore
  got ~47 lines of banner on stdout ahead of its own output, which broke every
  downstream ``json.loads(stdout)`` — most visibly ``coherence_checker.py
  --tier full``, whose JSON report became unparseable.

  The banners are progress messages, not data, so they belong on stderr.

The rule enforced here: in these modules, *library* scope may not write to
stdout. Their CLI entry points (``main()`` and the ``if __name__ ==
"__main__":`` block) still may — that output is the caller's requested data,
e.g. ``network/db/init_db.py --json`` printing a machine-readable status blob.

Asserting on the AST keeps this fast and database-free; importing the modules
for real would need a live backend.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Both the canonical package and the backward-compat shim tree are enforced;
# check_icdev_mirror_parity requires the twins to stay identical.
TREES = ["tools", "icdev/tools"]

# Extra non-canvas modules that emit banners at import time.
EXTRA_MODULES = ["a2a/agent_registry.py"]


def _banner_modules(tree: str) -> list[str]:
    """Every ``init_db.py`` under a top-level module of *tree*, not hardcoded.

    A hardcoded list rots: gpx-hyg-01 originally fixed 15 modules by name, and
    four later canvases (aiml, infra, noc, pmc) reintroduced the exact same
    stdout banners without tripping this guard. Globbing means a new canvas is
    covered the moment it lands.

    Both layouts are globbed. Canvases put the module at ``<name>/db/
    init_db.py``, but ``govcon``, ``kanban`` and ``studio`` put it directly at
    ``<name>/init_db.py`` — a ``*/db/*`` glob cannot see those, which is how
    ``tools/studio/init_db.py`` kept printing to stdout from library scope
    while this guard stayed green.

    Each tree is globbed separately on purpose. Whether a module is *present*
    in both trees is ``check_icdev_mirror_parity``'s job; duplicating it here
    would make this guard fail for unrelated mirror gaps instead of for the one
    rule it owns.
    """
    root = REPO_ROOT / tree
    found = sorted(
        {
            p.relative_to(root).as_posix()
            for pattern in ("*/db/init_db.py", "*/init_db.py")
            for p in root.glob(pattern)
        }
    )
    assert found, f"no {tree}/**/init_db.py found — REPO_ROOT is wrong"
    return found + [m for m in EXTRA_MODULES if (root / m).exists()]


CASES = [(tree, rel) for tree in TREES for rel in _banner_modules(tree)]

CLI_FUNCS = {"main", "cli"}


def _print_calls(node: ast.AST):
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "print"
        ):
            yield child


def _cli_scope_lines(module: ast.Module) -> set[int]:
    """Line numbers of print() calls belonging to a CLI entry point."""
    lines: set[int] = set()
    for top in module.body:
        is_cli_func = isinstance(top, ast.FunctionDef) and top.name in CLI_FUNCS
        is_main_guard = isinstance(top, ast.If) and "__main__" in ast.dump(top.test)
        if is_cli_func or is_main_guard:
            lines.update(call.lineno for call in _print_calls(top))
    return lines


def _library_stdout_prints(path: Path) -> list[int]:
    """Line numbers of library-scope print() calls that write to stdout."""
    module = ast.parse(path.read_text(encoding="utf-8"))
    cli_lines = _cli_scope_lines(module)
    return [
        call.lineno
        for call in _print_calls(module)
        if call.lineno not in cli_lines
        and not any(kw.arg == "file" for kw in call.keywords)
    ]


@pytest.mark.parametrize("tree,rel", CASES, ids=[f"{t}/{r}" for t, r in CASES])
def test_library_scope_never_prints_to_stdout(tree: str, rel: str) -> None:
    path = REPO_ROOT / tree / rel
    assert path.exists(), f"{path} is missing — update BANNER_MODULES"

    offenders = _library_stdout_prints(path)
    assert not offenders, (
        f"{tree}/{rel} writes to stdout from library scope on line(s) "
        f"{offenders}. Import-time banners must pass file=sys.stderr so tools "
        f"that json.loads(stdout) are not corrupted."
    )


def test_the_rule_still_permits_cli_stdout() -> None:
    """Guard the guard: a CLI entry point printing to stdout must stay legal.

    ``network/db/init_db.py`` prints a JSON status blob under ``--json`` from
    its ``__main__`` block. If a future refactor made ``_cli_scope_lines``
    stop recognising that scope, the check above would silently widen into
    something that forbids legitimate machine-readable output.
    """
    path = REPO_ROOT / "tools" / "network" / "db" / "init_db.py"
    module = ast.parse(path.read_text(encoding="utf-8"))

    cli_lines = _cli_scope_lines(module)
    assert cli_lines, "no CLI-scope print() found — scope detection is broken"
    assert not _library_stdout_prints(path)
