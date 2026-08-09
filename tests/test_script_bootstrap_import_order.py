#!/usr/bin/env python3
"""A module that is documented as `python <path>.py` must be able to start.

Running a file BY PATH puts the FILE's directory on ``sys.path[0]`` — never the
repository root. A module under ``tools/`` that imports ``tools.*`` at import
time therefore needs a bootstrap::

    _REPO_ROOT = Path(__file__).resolve().parents[N]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))

There are two ways to get this wrong, and this file gates both:

  1. **Wrong order** (PR #1436) — the bootstrap exists but a first-party import
     sits ABOVE it. ``test_no_first_party_import_above_the_bootstrap``.
  2. **No bootstrap at all** (kax-conflict-05) — the sibling defect. There is no
     ordering to check, so (1) cannot see it. 705 modules under ``tools/`` were
     in this state; 280 of them are spelled in PATH form in the docs, i.e. they
     are documented CLIs that could not start.
     ``test_documented_path_form_clis_have_a_bootstrap``.

WHY IT STAYS INVISIBLE. On a developer box and in CI the repo root is already on
``sys.path`` — via ``PYTHONPATH``, ``pip install -e .``, or a user-site ``.pth``.
That ambient state masks the defect completely: the broken module imports a
DIFFERENT checkout's ``tools`` package and appears to work. The behavioural test
below therefore SANITISES ``sys.path`` before launching, so it reproduces a plain
checkout rather than this machine.

THE MIRROR RULE. In ``icdev/tools/`` the import root is ``<repo>/icdev``, NOT the
repo root, because ``<repo>/icdev`` is what carries ``tools/`` inside an
installed wheel. The two trees sit at different depths — never copy a
``parents[N]`` across.

Only import-time code counts. An import inside a function body is deferred and is
NOT a violation; an import inside a module-level ``if``/``try``/``with``, or
inside ``if __name__ == "__main__":``, IS — the latter is exactly the code that
runs when someone follows the documented command.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
REPO_ROOT = REPO  # alias: the behavioural test reads better with the longer name

SCANNED_TREES = ("tools", "icdev/tools")
FIRST_PARTY_ROOTS = {"tools", "icdev"}

#: Modules proven broken by a failing CLI test. Additions welcome; removals are
#: the thing to be suspicious of.
GUARDED = [
    "tools/audit/cross_agency_transfer_logger.py",
    "tools/kanban/des_audit_logger.py",
]

_FIRST_PARTY = re.compile(r"^\s*(?:from|import)\s+(?:tools|icdev)\b")


def _first_party_imports_above_bootstrap(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8", newline="").split("\n")
    boot = next((i for i, ln in enumerate(lines) if "sys.path.insert" in ln), None)
    if boot is None:
        return []
    return [ln.strip() for ln in lines[:boot] if _FIRST_PARTY.match(ln)]


@pytest.mark.parametrize("rel", GUARDED)
@pytest.mark.parametrize("base", ["", "icdev/"])
def test_no_first_party_import_above_the_bootstrap(base, rel):
    path = REPO / (base + rel)
    if not path.exists():
        pytest.skip(f"no mirror at {base + rel}")
    offenders = _first_party_imports_above_bootstrap(path)
    assert not offenders, (
        f"{base + rel} imports first-party modules above its own sys.path "
        f"bootstrap, so `python {rel}` dies on ModuleNotFoundError: {offenders}"
    )


@pytest.mark.parametrize("rel", GUARDED)
def test_the_guarded_modules_still_have_a_bootstrap_to_be_above(rel):
    """If the bootstrap is deleted, the check above silently passes forever."""
    src = (REPO / rel).read_text(encoding="utf-8", newline="")
    assert "sys.path.insert" in src, (
        f"{rel} lost its sys.path bootstrap — the import-order test above becomes "
        "vacuous, and the CLI breaks again for a different reason"
    )


# ===========================================================================
# kax-conflict-05 — documented path-form CLI with NO bootstrap at all
# ===========================================================================

# --------------------------------------------------------------------------
# Deliberate exceptions. Each entry MUST carry a reason. Do not add an entry to
# get a commit through — add the bootstrap instead.
# --------------------------------------------------------------------------
DELIBERATE_EXCEPTIONS: dict[str, str] = {}


def _walk_import_time(node: ast.AST):
    """Yield nodes that execute when the module is run.

    Descends into module-level ``if`` / ``try`` / ``with`` / ``for`` / class
    bodies (all of which run) but NOT into function bodies, whose imports are
    deferred until the function is called.
    """
    yield node
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield from _walk_import_time(child)


def _deferred_ranges(tree: ast.Module) -> list[tuple[int, int]]:
    """Line ranges whose body does NOT run, or whose failure is survivable.

    * ``try: from tools.x import y / except ImportError: <fallback>`` — the
      codebase's deliberate dual-root idiom. The module survives the failure, so
      it is not the defect this gate hunts (an UNGUARDED import kills the
      process outright).
    * ``if TYPE_CHECKING:`` / ``if False:`` — never executed at runtime.
    """
    ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            catches = False
            for handler in node.handlers:
                exc = handler.type
                if exc is None:  # bare except
                    catches = True
                    break
                names = (
                    [e.id for e in exc.elts if isinstance(e, ast.Name)]
                    if isinstance(exc, ast.Tuple)
                    else [exc.id] if isinstance(exc, ast.Name) else []
                )
                if {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"} & set(names):
                    catches = True
                    break
            if catches and node.body:
                ranges.append((node.body[0].lineno, node.body[-1].end_lineno or node.body[-1].lineno))
        elif isinstance(node, ast.If):
            test = node.test
            name = (
                test.id if isinstance(test, ast.Name)
                else test.attr if isinstance(test, ast.Attribute)
                else None
            )
            if name == "TYPE_CHECKING" or (isinstance(test, ast.Constant) and test.value is False):
                if node.body:
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
    if not (isinstance(func, ast.Attribute) and func.attr in ("insert", "append", "extend")):
        return False
    owner = func.value
    return (
        isinstance(owner, ast.Attribute)
        and owner.attr == "path"
        and isinstance(owner.value, ast.Name)
        and owner.value.id == "sys"
    )


def analyze_source(source: str) -> dict:
    """{'bootstrap': line|None, 'first_import': line|None, 'has_main': bool}."""
    if source.startswith("﻿"):
        # ast.parse() does not strip the UTF-8 BOM that CPython's tokenizer does.
        source = source[1:]
    tree = ast.parse(source)
    deferred = _deferred_ranges(tree)

    bootstrap: int | None = None
    first_import: int | None = None
    has_main = False

    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if isinstance(stmt, ast.If):
            dumped = ast.dump(stmt.test)
            if "__name__" in dumped and "__main__" in dumped:
                has_main = True
        for node in _walk_import_time(stmt):
            if _is_syspath_mutation(node):
                if bootstrap is None or node.lineno < bootstrap:
                    bootstrap = node.lineno
            elif _is_first_party_import(node):
                if any(lo <= node.lineno <= hi for lo, hi in deferred):
                    continue
                if first_import is None or node.lineno < first_import:
                    first_import = node.lineno

    return {"bootstrap": bootstrap, "first_import": first_import, "has_main": has_main}


# --------------------------------------------------------------------------
# The documented-command inventory is PARSED, never hand-maintained. A list
# typed out by hand goes stale the first time somebody documents a new CLI, and
# a stale list is a gate that quietly stops gating.
# --------------------------------------------------------------------------
DOC_ROOTS = ("CLAUDE.md", "AGENTS.md", "README.md", "docs", ".claude", "goals")
DOC_SUFFIXES = (".md", ".txt", ".yaml", ".yml", ".json", ".sh", ".ps1")

#: `python tools/x.py`, `python3 -u tools/x.py`, `py icdev/tools/x.py`. The
#: `python -m tools.x` form is deliberately NOT matched: it puts the repo root on
#: sys.path correctly and is not broken.
_PATH_FORM = re.compile(
    r"\b(?:python3?|py)\s+(?:-[A-Za-z]+\s+)*((?:icdev/)?tools/[A-Za-z0-9_./-]+\.py)\b"
)

#: Below this, assume the scan itself broke rather than that the docs emptied out.
_MIN_DOCUMENTED_COMMANDS = 400


def documented_path_form_commands() -> dict[str, list[str]]:
    """Repo-relative ``.py`` path -> docs that invoke it as ``python <path>``."""
    hits: dict[str, set[str]] = {}
    files: list[Path] = []
    for root in DOC_ROOTS:
        p = REPO / root
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files += [q for q in p.rglob("*") if q.is_file() and q.suffix.lower() in DOC_SUFFIXES]
    for doc in files:
        try:
            text = doc.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _PATH_FORM.finditer(text):
            hits.setdefault(m.group(1), set()).add(doc.relative_to(REPO).as_posix())
    return {k: sorted(v) for k, v in hits.items()}


def _iter_python_files():
    for tree_name in SCANNED_TREES:
        root = REPO / tree_name
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            yield path.relative_to(REPO).as_posix(), path


def collect_bootstrapless_documented_clis() -> list[str]:
    """Documented path-form CLIs that import first-party code with no bootstrap."""
    docs = documented_path_form_commands()
    violations: list[str] = []
    for rel, path in _iter_python_files():
        if rel in DELIBERATE_EXCEPTIONS:
            continue
        # A mirror under icdev/ is reached by the same documented command; the
        # docs spell the tools/ path and the packaged copy has to work too.
        plain = rel[len("icdev/"):] if rel.startswith("icdev/") else rel
        if rel not in docs and plain not in docs:
            continue
        try:
            info = analyze_source(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError, UnicodeDecodeError):
            # Syntax is another gate's problem; do not double-report it here.
            continue
        if info["has_main"] and info["first_import"] is not None and info["bootstrap"] is None:
            violations.append(rel)
    return violations


def test_the_documented_command_scan_is_not_vacuous() -> None:
    """If the parse silently stops matching, the gate below passes forever."""
    docs = documented_path_form_commands()
    assert len(docs) >= _MIN_DOCUMENTED_COMMANDS, (
        f"only {len(docs)} path-form commands parsed out of the docs (expected "
        f">= {_MIN_DOCUMENTED_COMMANDS}). The regex or DOC_ROOTS regressed, and "
        f"every gate built on this inventory just went vacuous."
    )


def test_documented_path_form_clis_have_a_bootstrap() -> None:
    """A documented ``python <path>.py`` that imports first-party code at import
    time MUST bootstrap sys.path itself — there is nothing else to do it."""
    violations = collect_bootstrapless_documented_clis()
    if violations:
        detail = "\n".join(f"  {rel}" for rel in violations[:40])
        more = f"\n  ... and {len(violations) - 40} more" if len(violations) > 40 else ""
        pytest.fail(
            f"{len(violations)} documented path-form CLI(s) import first-party code "
            f"at import time with NO sys.path bootstrap. Started as `python <path>.py` "
            f"each dies with ModuleNotFoundError before main() is reached "
            f"(kax-conflict-05).\n"
            f"Fix: insert, immediately above the first first-party import —\n"
            f"    _REPO_ROOT = Path(__file__).resolve().parents[N]\n"
            f"    if str(_REPO_ROOT) not in sys.path:\n"
            f"        sys.path.insert(0, str(_REPO_ROOT))\n"
            f"N is the file's depth. Under icdev/ the import root is <repo>/icdev, "
            f"NOT the repo root.\n{detail}{more}"
        )


def test_deliberate_exceptions_are_justified_and_live() -> None:
    """Every allowlist entry must name a real file and carry a reason."""
    for rel, reason in DELIBERATE_EXCEPTIONS.items():
        assert (REPO / rel).is_file(), f"stale exception for missing file: {rel}"
        assert reason.strip(), f"exception for {rel} has no reason"


#: Documented CLIs that now bootstrap correctly but STILL cannot reach main(),
#: because they import a name that was never written. A separate defect with a
#: separate fix; recorded here so the next person does not "solve" the non-zero
#: exit by deleting the bootstrap, which would restore the original bug on top
#: of this one. Value = the import that does not resolve.
BOOTSTRAPPED_BUT_BROKEN_ELSEWHERE = {
    "tools/builder/code_generator.py": "tools.builder.code_gen_core (module never committed)",
    "tools/supply_chain/isa_manager.py": "tools.common.helpers.row_to_dict_json (never written)",
    "tools/iqe/cli.py": "tools.iqe.parser.IQEParser (parser.py exports parse(), not a class)",
}


@pytest.mark.parametrize("rel", sorted(BOOTSTRAPPED_BUT_BROKEN_ELSEWHERE))
@pytest.mark.parametrize("base", ["", "icdev/"])
def test_the_separately_broken_clis_keep_their_bootstrap(base: str, rel: str) -> None:
    path = REPO / (base + rel)
    if not path.is_file():
        pytest.skip(f"no mirror at {base + rel}")
    info = analyze_source(path.read_text(encoding="utf-8"))
    assert info["bootstrap"] is not None, (
        f"{base + rel} lost its sys.path bootstrap. It still exits non-zero because "
        f"of {BOOTSTRAPPED_BUT_BROKEN_ELSEWHERE[rel]} — a different defect. Removing "
        f"the bootstrap does not fix that and re-breaks `python {rel}` a second way."
    )


def test_tools_and_icdev_mirror_agree_on_having_a_bootstrap() -> None:
    """A fix landed in ``tools/`` but not in the ``icdev/tools/`` mirror (or vice
    versa) leaves the packaged copy broken. Compare the invariant across both."""
    drift: list[str] = []
    for rel, path in _iter_python_files():
        if not rel.startswith("tools/"):
            continue
        mirror = REPO / "icdev" / rel
        if not mirror.is_file():
            continue
        try:
            a = analyze_source(path.read_text(encoding="utf-8"))
            b = analyze_source(mirror.read_text(encoding="utf-8"))
        except (SyntaxError, OSError, UnicodeDecodeError):
            continue

        def needs_and_lacks(info: dict) -> bool:
            return bool(info["has_main"]) and info["first_import"] is not None and info["bootstrap"] is None

        if needs_and_lacks(a) != needs_and_lacks(b):
            drift.append(
                f"  {rel}: tools/ bootstrapless={needs_and_lacks(a)} but "
                f"icdev/ bootstrapless={needs_and_lacks(b)}"
            )

    assert not drift, (
        "bootstrap fix drifted between tools/ and its icdev/ mirror "
        "(the packaged copy is what a pip install ships):\n" + "\n".join(drift[:40])
    )


# ===========================================================================
# Behavioural — a real launch with the ambient sys.path crutch removed
# ===========================================================================
# One per shape the kax-conflict-05 sweep had to handle. Each was proven broken
# before the sweep, and each takes a `--help` that argparse short-circuits before
# any side effect.
BEHAVIOURAL_ENTRYPOINTS = [
    "tools/memory/memory_read.py",              # bootstrap slots into the head import block
    "tools/ci/modules/worktree.py",             # deeper file — parents[3], not parents[2]
    "tools/agent_runtime/project_context.py",   # module already binds _REPO_ROOT to something else
    "tools/network/patch_planner.py",           # a transitive dep needed its own bootstrap too
    "icdev/tools/memory/memory_read.py",        # mirror: the import root is <repo>/icdev
    "icdev/tools/agent_runtime/project_context.py",
    "icdev/tools/network/patch_planner.py",     # mirror of the transitive-dep case
]

# Executes <script> with the repo root REMOVED from sys.path, so the run
# reproduces a plain checkout instead of this machine's ambient PYTHONPATH /
# editable install. sys.path[0] is set to the script's own directory, exactly as
# CPython does for `python <script>`.
_SANITISED_LAUNCHER = textwrap.dedent(
    """
    import os, runpy, sys
    script = os.path.abspath(sys.argv[1])
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
    sys.argv = sys.argv[1:]
    runpy.run_path(script, run_name="__main__")
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
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.run(
        [sys.executable, "-c", _SANITISED_LAUNCHER, str(script), "--help"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")

    for missing in ("No module named 'tools'", "No module named 'icdev'"):
        assert missing not in combined, (
            f"{relpath} cannot start by path — {missing}. It has no sys.path "
            f"bootstrap above its first first-party import (kax-conflict-05):\n{combined}"
        )
    assert proc.returncode == 0, (
        f"{relpath} --help exited {proc.returncode} with a sanitised sys.path:\n{combined}"
    )
