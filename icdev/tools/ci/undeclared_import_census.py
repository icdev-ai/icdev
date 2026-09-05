#!/usr/bin/env python3
# CUI // SP-CTI
"""Census of undeclared third-party imports that fail SILENTLY (tsg-iso-03).

WHY THIS EXISTS
---------------
``python-dateutil`` was imported by two runtime modules and declared in NEITHER
``requirements.txt`` nor ``pyproject.toml``. Both imports sat inside a bare
``except Exception`` that returned a benign-looking value, so on any install
without the package the feature degraded with nothing anywhere to say why:

  * ``tools/genesis/reflexes/kanban.py`` — the stale reaper skipped EVERY task,
    and had never once run on CI.
  * ``tools/notification_service/event_service.py::_duration_str`` — every
    notification duration rendered ``"unknown"``, which is indistinguishable
    from a genuinely unknown duration.

It passed on Windows, where dateutil arrives transitively as somebody else's
dependency, and failed on the CI runner and on any air-gapped install — the
deployment this project targets. That asymmetry is what kept it alive: the
machine where it was written could not reproduce it.

WHAT THE DEFECT ACTUALLY IS
---------------------------
Not "an undeclared import". A dependency that is genuinely optional and guarded
by a handler that SAYS SO is correct, and this repo already does it properly —
``tools/blockchain/transports/__init__.py`` raises a message naming
``fabric-sdk-py (hfc)`` as an undeclared optional dependency, so an operator
who hits it learns what to install. Banning that shape would be wrong.

The defect is the CONJUNCTION:

    an import of an UNDECLARED third-party package,
    inside a handler that SWALLOWS — returns, passes or continues without
    logging, raising, or otherwise recording that it fired.

That is the shape which cannot be distinguished from working, and it is the one
this census enumerates. A site stops being a finding by fixing EITHER half:
declare the package, or make the handler say something.

CENSUS DISCIPLINE (same as args/ci_test_backlog.txt and args/ci_skip_census.txt)
-------------------------------------------------------------------------------
The census ENUMERATES sites by name. It does not count them. A bare count can be
held constant while the set churns — delete one site, add another, count
unchanged, gate green, and the thing the gate exists to notice has happened
unobserved. That is how the ungated-test gap regrew behind a green gate, and
identity is the only thing that survives it.

``undeclared_import_census.undeclared_max`` in
``args/undeclared_import_gate.yaml`` is a ceiling on the registered count and
MAY ONLY GO DOWN. Never raise it to get a commit through.

PER SITE, NOT PER FILE
----------------------
The key is ``<file>::<qualname>::<module>``. A per-FILE census would grandfather
a module once and then let it grow a second and third silent import without a
word. Line numbers are deliberately absent from the key: they churn on every
edit above the site, which would make the census a merge-conflict generator and
every unrelated PR a census edit.

WHAT THIS DOES **NOT** DO
-------------------------
It converts nothing. Every site that exists today is grandfathered by name.
This task only closes the door — the two dateutil sites are fixed, and the
shape cannot re-enter unobserved.

USAGE
-----
    python tools/ci/undeclared_import_census.py --check        # the gate
    python tools/ci/undeclared_import_census.py --json
    python tools/ci/undeclared_import_census.py --changed tools/foo.py --check
    python tools/ci/undeclared_import_census.py --staged
    python tools/ci/undeclared_import_census.py --prune
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

def _find_repo_root(start: Path) -> Path:
    """Walk up to the checkout root, rather than counting parents.

    This module is mirrored to ``icdev/tools/ci/``, where a fixed
    ``parents[2]`` resolves to ``<repo>/icdev`` and every path below it is
    wrong. Resolved from ``__file__`` and never from ``os.getcwd()``, which is
    the worktree root under a git worktree (see CLAUDE.md).
    """
    for candidate in (start, *start.parents):
        if (candidate / "requirements.txt").exists() and (candidate / "args").is_dir():
            return candidate
    return start.parents[2]


REPO = _find_repo_root(Path(__file__).resolve().parent)
GATE_FILE = REPO / "args" / "undeclared_import_gate.yaml"

#: Import name -> distribution name, for the packages whose two names differ.
#: Curated, not derived: ``importlib.metadata.packages_distributions()`` only
#: knows what is INSTALLED, so on the very runner where the dependency is
#: missing it would report nothing and the gate would invent findings.
IMPORT_TO_DIST = {
    "attr": "attrs",
    "bs4": "beautifulsoup4",
    "cv2": "opencv_python",
    "dateutil": "python_dateutil",
    "docx": "python_docx",
    "dotenv": "python_dotenv",
    "fitz": "pymupdf",
    "git": "gitpython",
    "jose": "python_jose",
    "jwt": "pyjwt",
    "magic": "python_magic",
    "OpenSSL": "pyopenssl",
    "PIL": "pillow",
    "pptx": "python_pptx",
    "psycopg2": "psycopg2_binary",
    "serial": "pyserial",
    "sklearn": "scikit_learn",
    "slugify": "python_slugify",
    "socketio": "python_socketio",
    "yaml": "pyyaml",
}

#: Names that are this repository's own, imported via a sys.path insertion
#: rather than through the package. Not third party, so not this gate's problem.
FIRST_PARTY_ROOTS = {
    "tools", "icdev", "args", "goals", "tests", "frontend", "context",
    "hardprompts", "features", "memory", "data", "docs", "playwright",
}


# ── declarations ───────────────────────────────────────────────────────────
def declared_distributions(repo: Path = REPO) -> set[str]:
    """Every distribution named in requirements.txt or pyproject.toml.

    Normalised the way PEP 503 normalises: lowercase, ``-``/``.`` -> ``_``.
    """
    found: set[str] = set()
    for name in ("requirements.txt", "pyproject.toml"):
        path = repo / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.split("#")[0].strip().strip(",")
            if not line or line.startswith("-"):
                continue
            match = re.match(r'^"?\'?([A-Za-z0-9][A-Za-z0-9._-]*)', line)
            if match:
                found.add(_normalise(match.group(1)))
    return found


def _normalise(name: str) -> str:
    return re.sub(r"[-.]+", "_", name).lower()


@lru_cache(maxsize=None)
def _first_party_names(repo: Path) -> frozenset[str]:
    """Every bare module name this repository can satisfy from its own tree.

    Built ONCE by a single walk. The obvious spelling — an ``rglob`` per
    candidate import — is quadratic over a tree this size and took the gate
    from a second to four and a half minutes, which is how a merge gate earns
    itself a ``|| true``.

    It exists because several modules import a sibling by bare name after a
    ``sys.path`` insert (``cui_marker``, ``accountability_manager``,
    ``base_assessor``). Those are first-party and no requirements file will
    ever declare them.
    """
    names = set(FIRST_PARTY_ROOTS)
    for child in repo.iterdir():
        if child.is_dir() and not child.name.startswith("."):
            names.add(child.name)
        elif child.suffix == ".py":
            names.add(child.stem)
    for root in ("tools", "icdev/tools"):
        base = repo / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_dir():
                names.add(path.name)
            elif path.suffix == ".py":
                names.add(path.stem)
    return frozenset(names)


def _is_first_party(top: str, repo: Path) -> bool:
    """True for a module that lives in this repository under some other name."""
    return top in _first_party_names(repo)


# ── the swallow predicate ──────────────────────────────────────────────────
_SPEAKING_CALLS = {
    "warning", "warn", "error", "exception", "critical", "info", "debug",
    "print", "log", "write", "emit", "notify", "record", "append",
}


def handler_swallows(handler: ast.ExceptHandler) -> bool:
    """True if this handler fires without leaving any trace that it fired.

    Anything that RAISES (including a bare re-raise), LOGS, PRINTS or otherwise
    records is not swallowing — the operator has something to read. A handler
    that only returns, passes or continues is the shape that reads as success.
    """
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return False
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            if name in _SPEAKING_CALLS:
                return False
    return all(
        isinstance(stmt, (ast.Return, ast.Pass, ast.Continue, ast.Break, ast.Assign))
        for stmt in handler.body
    )


# ── scanning ───────────────────────────────────────────────────────────────
def _qualname_index(tree: ast.AST) -> dict[int, str]:
    """Map every node's lineno to its enclosing def/class qualname."""
    index: dict[int, str] = {}

    def walk(node, prefix: str):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qual = f"{prefix}.{child.name}" if prefix else child.name
                for line in range(child.lineno, (child.end_lineno or child.lineno) + 1):
                    index[line] = qual
                walk(child, qual)
            else:
                walk(child, prefix)

    walk(tree, "")
    return index


def scan_file(path: Path, declared: set[str], repo: Path = REPO) -> list[dict]:
    """Every undeclared-third-party-import-inside-a-swallowing-handler site."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []

    quals = _qualname_index(tree)
    rel = path.relative_to(repo).as_posix()
    sites: list[dict] = []

    for try_node in ast.walk(tree):
        if not isinstance(try_node, ast.Try):
            continue
        if not any(handler_swallows(h) for h in try_node.handlers):
            continue

        imported: list[tuple[str, int]] = []
        for body_stmt in try_node.body:
            for node in ast.walk(body_stmt):
                if isinstance(node, ast.Import):
                    imported += [(a.name, node.lineno) for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    imported.append((node.module, node.lineno))

        for module, lineno in imported:
            top = module.split(".")[0]
            if top in sys.stdlib_module_names:
                continue
            if _is_first_party(top, repo):
                continue
            dist = IMPORT_TO_DIST.get(top, top)
            if _normalise(dist) in declared or _normalise(top) in declared:
                continue
            sites.append({
                "key": f"{rel}::{quals.get(lineno, '<module>')}::{top}",
                "file": rel,
                "line": lineno,
                "module": module,
                "package": top,
                "distribution": dist,
            })

    # stable, de-duplicated: the same package imported twice in one function is
    # one site, because it is one decision.
    seen, unique = set(), []
    for site in sorted(sites, key=lambda s: (s["file"], s["line"])):
        if site["key"] in seen:
            continue
        seen.add(site["key"])
        unique.append(site)
    return unique


# ── config ─────────────────────────────────────────────────────────────────
def load_gate(path: Path = GATE_FILE) -> dict:
    try:
        import yaml  # noqa: PLC0415 — pyyaml IS declared; this is not a census site
    except ImportError:  # pragma: no cover
        raise SystemExit("undeclared_import_census: pyyaml is required and declared")
    if not path.exists():
        raise SystemExit(f"undeclared_import_census: missing gate config {path}")
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(
        "undeclared_import_census", {})


def load_census(repo: Path, cfg: dict) -> set[str]:
    census_path = repo / cfg.get("census_file", "args/undeclared_import_census.txt")
    if not census_path.exists():
        return set()
    entries = set()
    for line in census_path.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            entries.add(line)
    return entries


def _excluded(rel: str, cfg: dict) -> bool:
    from fnmatch import fnmatch
    for entry in cfg.get("exclude", []) or []:
        if fnmatch(rel, entry.get("path", "")):
            return True
    return False


def collect(repo: Path, cfg: dict, only: list[str] | None = None) -> list[dict]:
    declared = declared_distributions(repo)
    targets: list[Path] = []
    if only is not None:
        targets = [repo / f for f in only if f.endswith(".py")]
    else:
        for root in cfg.get("scan_roots", ["tools", "icdev/tools"]):
            base = repo / root
            if base.is_dir():
                targets += sorted(base.rglob("*.py"))

    sites: list[dict] = []
    for path in targets:
        if not path.exists():
            continue
        try:
            rel = path.relative_to(repo).as_posix()
        except ValueError:
            continue
        if _excluded(rel, cfg):
            continue
        if not any(rel.startswith(r + "/") for r in cfg.get("scan_roots", ["tools", "icdev/tools"])):
            continue
        sites += scan_file(path, declared, repo)
    return sites


def _staged_files(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=repo, capture_output=True, text=True, check=False,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip().endswith(".py")]


# ── report ─────────────────────────────────────────────────────────────────
def build_report(repo: Path = REPO, only: list[str] | None = None) -> dict:
    cfg = load_gate()
    census = load_census(repo, cfg)
    sites = collect(repo, cfg, only)
    keys = [s["key"] for s in sites]

    unregistered = [s for s in sites if s["key"] not in census]
    ceiling = int(cfg.get("undeclared_max", 0))

    report = {
        "scope": "changed" if only is not None else "tree",
        "sites_seen": len(sites),
        "registered": len([k for k in keys if k in census]),
        "unregistered": unregistered,
        "census_size": len(census),
        "ceiling": ceiling,
        "over_ceiling": len(census) > ceiling,
        "ok": not unregistered and len(census) <= ceiling,
    }
    if only is None:
        report["stale_entries"] = sorted(census - set(keys))
    return report


def prune(repo: Path = REPO) -> int:
    """Drop census entries whose site no longer exists. Only ever SHRINKS."""
    cfg = load_gate()
    census_path = repo / cfg.get("census_file", "args/undeclared_import_census.txt")
    live = {s["key"] for s in collect(repo, cfg)}
    kept, dropped = [], 0
    for line in census_path.read_text(encoding="utf-8").splitlines():
        bare = line.split("#")[0].strip()
        if bare and bare not in live:
            dropped += 1
            continue
        kept.append(line)
    census_path.write_text("\n".join(kept).rstrip("\n") + "\n", encoding="utf-8", newline="\n")
    return dropped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 on a NEW site")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--changed", nargs="*", help="limit the scan to these files")
    parser.add_argument("--staged", action="store_true", help="scan only staged files")
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--root", default=None,
                        help="checkout to scan (default: the one this tool lives in); "
                             "the gate config is always read from the tool's own checkout")
    args = parser.parse_args(argv)
    repo = Path(args.root).resolve() if args.root else REPO

    if args.prune:
        dropped = prune()
        print(f"Undeclared-import census: pruned {dropped} stale entr(ies).")
        return 0

    only = None
    if args.staged:
        only = _staged_files(repo)
    elif args.changed is not None:
        only = list(args.changed)

    report = build_report(repo, only)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"Undeclared-import census ({report['scope']}): {report['sites_seen']} site(s) seen, "
            f"{report['registered']} registered, {len(report['unregistered'])} unregistered "
            f"| census {report['census_size']} (ceiling {report['ceiling']})"
        )
        for site in report["unregistered"][:40]:
            print(f"  NEW  {site['file']}:{site['line']}  imports {site['module']!r} "
                  f"(distribution {site['distribution']!r}) inside a swallowing handler")
        if report.get("over_ceiling"):
            print(f"  CEILING BREACHED: census {report['census_size']} > {report['ceiling']}. "
                  f"undeclared_max may only go DOWN.")

    if args.check and not report["ok"]:
        print(
            "\nAn undeclared third-party import inside a swallowing handler cannot be "
            "distinguished from working code. Fix EITHER half:\n"
            "  * declare the distribution in requirements.txt, or\n"
            "  * make the handler say it fired (log it, or raise a message naming "
            "the package), or\n"
            "  * use the stdlib — tools.common.helpers.parse_utc_timestamp is what "
            "replaced dateutil.\n"
            "Registering it in args/undeclared_import_census.txt is a debt you have "
            "written down, and it breaches the ceiling.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    os.chdir(REPO)
    raise SystemExit(main())
