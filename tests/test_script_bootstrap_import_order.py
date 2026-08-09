# CUI // SP-CTI
"""Tree-wide gate (kax-conflict-04): a module's ``sys.path`` bootstrap must run
BEFORE its first first-party (``tools.*`` / ``icdev.*``) import.

THE BUG. Running a Python file BY PATH puts the FILE's directory on
``sys.path[0]`` — never the repository root. Modules under ``tools/`` therefore
carry a bootstrap::

    BASE_DIR = Path(__file__).resolve().parents[2]
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))

A ``get_logger`` sweep later hoisted ``from tools.logging.icdev_logger import
get_logger`` to the TOP of these files — ABOVE the bootstrap that makes it
resolvable. Every such module died with ``ModuleNotFoundError: No module named
'tools'`` when started as ``python tools/<path>.py``, before ``main()`` was
ever reached. 557 modules across ``tools/`` and the ``icdev/tools/`` mirror
were in that state; 455 of them had a runnable ``__main__``.

WHY NOBODY NOTICED. On a developer box (and in CI) the repository root is
already on ``sys.path`` — via ``PYTHONPATH``, via ``pip install -e .``, or via a
user-site ``.pth``. That ambient state masks the bug completely: the broken
module imports a DIFFERENT checkout's ``tools`` package and appears to work.
The behavioural test below therefore SANITISES ``sys.path`` before launching,
so it reproduces a plain checkout rather than this machine.

APPROACH TAKEN — option (a), the mechanical move, applied by a scripted AST
transform with a re-parse of every file. Option (b) (delete the bootstraps and
convert every caller to ``python -m tools.x``) was rejected: 766 documented
command lines across CLAUDE.md, docs/ and .claude/ spell the path form, and
``python -m tools.x`` does not work inside the installed wheel. Option (c) (a
shared ``tools/_bootstrap.py``) was rejected because importing it is itself a
first-party import — the same chicken-and-egg the bootstrap exists to solve.

WHAT IS CHECKED
  * ``test_bootstrap_precedes_first_first_party_import`` — static, tree-wide.
    Anything not in ``DELIBERATE_EXCEPTIONS`` must order bootstrap before the
    first import-time first-party import.
  * ``test_tools_and_icdev_mirror_agree_on_bootstrap_order`` — the ``tools/``
    tree and its ``icdev/tools/`` mirror must not drift apart on this
    invariant (a fix applied to one but not the other is itself a defect).
  * ``test_representative_entrypoints_start_with_sanitised_syspath`` —
    behavioural. Launches real entrypoints with the repo root stripped from
    ``sys.path`` and asserts they do not die with a first-party
    ``ModuleNotFoundError``.

Only import-time code counts. An import inside a function body is deferred and
is NOT a violation; an import inside a module-level ``if``/``try``/``with`` IS.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNED_TREES = ("tools", "icdev/tools")
FIRST_PARTY_ROOTS = {"tools", "icdev"}

# ---------------------------------------------------------------------------
# Deliberate exceptions. Each entry MUST carry a reason. Do not add an entry to
# get a commit through — fix the ordering instead.
# ---------------------------------------------------------------------------
DELIBERATE_EXCEPTIONS: dict[str, str] = {}


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------
def _walk_import_time(node: ast.AST):
    """Yield nodes that execute when the module is imported.

    Descends into module-level ``if`` / ``try`` / ``with`` / ``for`` / class
    bodies (all of which run at import time) but NOT into function bodies,
    whose imports are deferred until the function is called.
    """
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield from _walk_import_time(child)


def _import_time_nodes(tree: ast.Module):
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield from _walk_import_time(stmt)


def _guarded_line_ranges(tree: ast.Module) -> list[tuple[int, int]]:
    """Line ranges of ``try`` bodies whose handler catches an import failure.

    ``try: from tools.x import y / except ImportError: <fallback>`` is the
    codebase's deliberate dual-root idiom — the module survives the failure and
    the bootstrap that follows repairs sys.path. It is NOT the defect this gate
    hunts, which is an UNGUARDED import that kills the process outright.
    """
    ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        catches_import = False
        for handler in node.handlers:
            exc = handler.type
            if exc is None:  # bare except
                catches_import = True
                break
            names = (
                [e.id for e in exc.elts if isinstance(e, ast.Name)]
                if isinstance(exc, ast.Tuple)
                else [exc.id] if isinstance(exc, ast.Name) else []
            )
            if {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"} & set(names):
                catches_import = True
                break
        if catches_import and node.body:
            ranges.append((node.body[0].lineno, node.body[-1].end_lineno or node.body[-1].lineno))
    return ranges


def _is_first_party_import(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(a.name.split(".")[0] in FIRST_PARTY_ROOTS for a in node.names)
    if isinstance(node, ast.ImportFrom):
        # level > 0 is a relative import — it resolves via the package, not sys.path.
        return node.level == 0 and (node.module or "").split(".")[0] in FIRST_PARTY_ROOTS
    return False


def _is_syspath_mutation(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr in ("insert", "append")):
        return False
    owner = func.value
    return (
        isinstance(owner, ast.Attribute)
        and owner.attr == "path"
        and isinstance(owner.value, ast.Name)
        and owner.value.id == "sys"
    )


def analyze_source(source: str) -> dict:
    """Return {'bootstrap': line|None, 'first_import': line|None, 'text': str|None}."""
    if source.startswith("﻿"):
        # ast.parse() does not strip the UTF-8 BOM that CPython's tokenizer does.
        source = source[1:]
    tree = ast.parse(source)
    guarded = _guarded_line_ranges(tree)

    bootstrap: int | None = None
    first_import: int | None = None

    for node in _import_time_nodes(tree):
        if _is_syspath_mutation(node):
            if bootstrap is None or node.lineno < bootstrap:
                bootstrap = node.lineno
        elif _is_first_party_import(node):
            if any(lo <= node.lineno <= hi for lo, hi in guarded):
                continue
            if first_import is None or node.lineno < first_import:
                first_import = node.lineno

    return {"bootstrap": bootstrap, "first_import": first_import}


def _iter_python_files():
    for tree_name in SCANNED_TREES:
        root = REPO_ROOT / tree_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            yield path.relative_to(REPO_ROOT).as_posix(), path


def collect_violations() -> list[tuple[str, dict]]:
    """Every module whose first import-time first-party import precedes its own
    sys.path bootstrap."""
    violations: list[tuple[str, dict]] = []
    for rel, path in _iter_python_files():
        if rel in DELIBERATE_EXCEPTIONS:
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        try:
            info = analyze_source(source)
        except SyntaxError:
            # Syntax is another gate's problem; do not double-report it here.
            continue
        boot, first = info["bootstrap"], info["first_import"]
        if boot is not None and first is not None and first < boot:
            violations.append((rel, info))
    return violations


# ---------------------------------------------------------------------------
# Static, tree-wide
# ---------------------------------------------------------------------------
def test_bootstrap_precedes_first_first_party_import() -> None:
    """No module may import ``tools.*`` / ``icdev.*`` at import time before its
    own ``sys.path`` bootstrap has run."""
    violations = collect_violations()
    if violations:
        detail = "\n".join(
            f"  {rel}: first-party import at line {info['first_import']} "
            f"precedes sys.path bootstrap at line {info['bootstrap']}"
            for rel, info in violations[:40]
        )
        more = f"\n  ... and {len(violations) - 40} more" if len(violations) > 40 else ""
        pytest.fail(
            f"{len(violations)} module(s) import first-party code above their own "
            f"sys.path bootstrap. Started via `python <path>.py` each one dies with "
            f"ModuleNotFoundError before main() is reached (kax-conflict-04).\n"
            f"Fix: move the import BELOW the bootstrap and append `# noqa: E402`.\n"
            f"{detail}{more}"
        )


def test_deliberate_exceptions_are_justified_and_live() -> None:
    """Every allowlist entry must name a real file and carry a reason."""
    for rel, reason in DELIBERATE_EXCEPTIONS.items():
        assert (REPO_ROOT / rel).is_file(), f"stale exception for missing file: {rel}"
        assert reason.strip(), f"exception for {rel} has no reason"


def test_tools_and_icdev_mirror_agree_on_bootstrap_order() -> None:
    """A fix landed in ``tools/`` but not in the ``icdev/tools/`` mirror (or vice
    versa) leaves the packaged copy broken. Compare the invariant across both."""
    drift: list[str] = []
    for rel, path in _iter_python_files():
        if not rel.startswith("tools/"):
            continue
        mirror = REPO_ROOT / "icdev" / rel
        if not mirror.is_file():
            continue
        try:
            a = analyze_source(path.read_text(encoding="utf-8"))
            b = analyze_source(mirror.read_text(encoding="utf-8"))
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue

        def ordered(info: dict) -> bool:
            boot, first = info["bootstrap"], info["first_import"]
            return not (boot is not None and first is not None and first < boot)

        if ordered(a) != ordered(b):
            drift.append(
                f"  {rel}: tools/ ordered={ordered(a)} but icdev/ ordered={ordered(b)}"
            )

    assert not drift, (
        "bootstrap-order fix drifted between tools/ and its icdev/ mirror "
        "(the packaged copy is what a pip install ships):\n" + "\n".join(drift[:40])
    )


# ---------------------------------------------------------------------------
# Behavioural
# ---------------------------------------------------------------------------
# Launched for real with a SANITISED sys.path. Each was in the broken set before
# kax-conflict-04 and covers a different shape the sweep had to handle.
#
# An argparse CLI is launched as ``__main__`` with ``--help``, which argparse
# short-circuits before any side effect. Anything else is launched under a
# NON-``__main__`` run name so the module body executes — which is where the
# ModuleNotFoundError lives — without entering its CLI. That distinction is not
# cosmetic: ``tools/mcp/standalone/core.py``'s main() calls ``server.run()`` on
# stdio and would block until the timeout.
BEHAVIOURAL_ENTRYPOINTS = [
    # The two that PR #1436 fixed because failing tests happened to prove them broken.
    "tools/kanban/des_audit_logger.py",
    "tools/audit/cross_agency_transfer_logger.py",
    # One per shape the sweep had to handle.
    "tools/agent/agent_memory.py",             # plain hoisted get_logger import
    "tools/saas/tenant_manager.py",            # logger = get_logger(...) also had to move
    "tools/mcp/standalone/core.py",            # bootstrap lived inside main()
    "tools/compliance/ai_transparency_audit.py",  # sys.path insert added a SIBLING dir
    "tools/threat_analysis/osint_normalizer.py",  # bootstrap had the wrong parents[N]
    "tools/skills/gepa_optimizer.py",
    "tools/redaction/pulse_sanitizer.py",
    "tools/migration/discovery_scanner.py",
]

# Executes <script> with the repo root REMOVED from sys.path, so the run
# reproduces a plain checkout instead of this machine's ambient PYTHONPATH /
# editable install. sys.path[0] is set to the script's own directory, exactly as
# CPython does for `python <script>`.
_SANITISED_LAUNCHER = textwrap.dedent(
    """
    import os, runpy, sys
    script = os.path.abspath(sys.argv[1])
    run_name = sys.argv[2]
    strip = {
        os.path.normcase(os.path.abspath(p))
        for p in os.environ["ICDEV_STRIP_ROOTS"].split(os.pathsep)
        if p
    }
    sys.path[:] = [
        p for p in sys.path
        if os.path.normcase(os.path.abspath(p or os.getcwd())) not in strip
    ]
    sys.path.insert(0, os.path.dirname(script))
    sys.argv = [script] + sys.argv[3:]
    runpy.run_path(script, run_name=run_name)
    """
)


@pytest.mark.parametrize("relpath", BEHAVIOURAL_ENTRYPOINTS)
def test_representative_entrypoints_start_with_sanitised_syspath(relpath: str) -> None:
    """A real launch, with the ambient repo-root-on-sys.path crutch removed, must
    not die with a first-party ``ModuleNotFoundError``."""
    script = REPO_ROOT / relpath
    assert script.is_file(), f"behavioural entrypoint missing: {relpath}"

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["ICDEV_STRIP_ROOTS"] = os.pathsep.join(
        {str(REPO_ROOT), str(Path(sys.argv[0]).resolve().parent), os.getcwd()}
    )
    env["ICDEV_STORAGE_BACKEND"] = "sqlite"

    source = script.read_text(encoding="utf-8", errors="replace")
    has_main = '__name__ == "__main__"' in source or "__name__ == '__main__'" in source
    if "argparse.ArgumentParser" in source and has_main:
        args = [str(script), "__main__", "--help"]
        how = "--help"
    else:
        # Module body runs; the CLI does not. This is where the import lives.
        args = [str(script), "__icdev_import_probe__"]
        how = "import probe"

    proc = subprocess.run(
        [sys.executable, "-c", _SANITISED_LAUNCHER, *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")

    for missing in ("No module named 'tools'", "No module named 'icdev'"):
        assert missing not in combined, (
            f"{relpath} cannot start by path — {missing}. Its first-party import "
            f"runs above its own sys.path bootstrap (kax-conflict-04):\n{combined}"
        )
    assert proc.returncode == 0, (
        f"{relpath} ({how}) exited {proc.returncode} with a sanitised sys.path:\n{combined}"
    )
