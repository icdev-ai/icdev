# CUI // SP-CTI
"""Self-root census (xit-decl-03) -- a module that computes the REPO ROOT from its own location.

THE DEFECT
----------
2,054 modules under tools/ derive a data, args, context or database path from
``Path(__file__).resolve().parent.parent.parent`` (or ``parents[2]``, or a
nest of ``os.path.dirname``). Each one is a private, hard-coded claim about
where the file sits relative to the repository root. The claim is true today
and false the moment the file moves: a kernel package physically relocated to
another repository (the ICDEV[domain] split, docs/programmes/
icdev-domain-split.md) keeps computing ``parents[2]`` and lands its ``args/``
reads in the WRONG checkout -- silently, because the directory it lands on
usually exists.

``icdev/core/paths.py::repo_root()`` (xit-decl-01) is the one resolver that
answers the question correctly wherever the code lives. This census stops the
set of private answers GROWING while they are migrated onto it.

WHAT IS AND IS NOT A FINDING
----------------------------
A finding is a ``__file__``-rooted expression that CLIMBS TO THE REPOSITORY
ROOT: the number of directory hops equals the file's depth under the root.

    tools/db/storage.py:   Path(__file__).resolve().parents[2]         -> site
                           Path(__file__).resolve().parent.parent.parent -> site
                           os.path.dirname(os.path.dirname(os.path.dirname(__file__))) -> site

NOT findings, because they are correct and must stay:

* a MODULE-LOCAL path -- ``Path(__file__).parent / "templates"`` climbs to the
  module's own directory, and those assets move WITH the module;
* the ``sys.path`` BOOTSTRAP idiom -- ``_REPO_ROOT = Path(__file__).resolve()
  .parents[2]; sys.path.insert(0, str(_REPO_ROOT))`` resolves the IMPORT root,
  which is the same directory before and after a move, and stays correct. A
  name whose every use is a ``sys.path`` insertion or membership test is this
  idiom and is skipped. The same name ALSO used to build a data path is a site;
* a marker WALK -- ``for p in Path(__file__).parents: if (p / "pyproject.toml")
  .exists()`` -- is the GOOD pattern and is never matched, because it climbs
  by evidence, not by a hard-coded count.

Climbing PAST the root (``parents[3]`` from tools/db/) is reported separately
as ``overwalk``: that is not a self-root, it is a bug, and it usually means
the file was written for the icdev/tools/ mirror's depth.

THE RATCHET
-----------
1. A NEW site fails ``python tools/ci/self_root_census.py --check`` by name.
2. The sites that existed at adoption are grandfathered BY NAME in
   args/self_root_census.txt -- enumerated, never counted, so the set cannot
   churn behind a constant total.
3. ``self_root_max`` in args/self_root_gate.yaml may ONLY go DOWN.
4. ``--fix`` rewrites the SIMPLE module-level form
   ``NAME = Path(__file__).resolve().parent.parent.parent`` (any depth,
   ``parents[n]`` too) to ``NAME = repo_root(__file__)`` and adds the import.
   It refuses anything else, and it refuses the bootstrap idiom.

Key: ``<file>::<qualname>::<name | name-N | inline-N>``. No line numbers.

    python tools/ci/self_root_census.py --check
    python tools/ci/self_root_census.py --changed tools/foo.py --check
    python tools/ci/self_root_census.py --staged
    python tools/ci/self_root_census.py --json
    python tools/ci/self_root_census.py --prune
    python tools/ci/self_root_census.py --fix tools/foo.py
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from pathlib import Path


def _find_repo_root(start: Path) -> Path:
    # The census tool itself walks by MARKER -- the good pattern.
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").exists() or (candidate / ".git").exists():
            return candidate
    return start


REPO = _find_repo_root(Path(__file__).resolve().parent)
GATE_FILE = REPO / "args" / "self_root_gate.yaml"
GATE_KEY = "self_root_census"

_ROOT_CALL_ATTRS = frozenset({"resolve", "absolute"})
_PATH_NAMES = frozenset({"Path", "PurePath", "PosixPath", "WindowsPath"})


# ── the predicate ────────────────────────────────────────────────────────────
def climb_hops(node: ast.AST) -> int | None:
    """How many directories ABOVE the file's own directory does ``node`` name?

    ``Path(__file__).resolve().parent`` is the file's directory -> 0;
    ``.parent.parent`` -> 1; ``.parents[k]`` -> k;
    ``os.path.dirname(os.path.abspath(__file__))`` -> 0, each nested
    ``dirname`` -> +1. Returns None when ``node`` is not a ``__file__`` climb.
    """
    hops = 0
    n = node
    while True:
        if isinstance(n, ast.Attribute) and n.attr == "parent":
            hops += 1
            n = n.value
            continue
        if (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Attribute)
                and n.value.attr == "parents"):
            idx = n.slice
            if isinstance(idx, ast.Constant) and isinstance(idx.value, int) and idx.value >= 0:
                inner = climb_hops(n.value.value)
                # inner is the Path(__file__)[.resolve()] object: -1 means "the file"
                return hops + idx.value if inner == -1 else None
            return None
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr in _ROOT_CALL_ATTRS:
                n = n.func.value
                continue
            if n.func.attr == "dirname" and n.args:
                hops += 1
                n = n.args[0]
                continue
            if n.func.attr in ("abspath", "realpath") and n.args:
                n = n.args[0]
                continue
            return None
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id in _PATH_NAMES and n.args
                and isinstance(n.args[0], ast.Name) and n.args[0].id == "__file__"):
            return hops - 1  # Path(__file__) itself is the FILE, one below its dir
        if isinstance(n, ast.Name) and n.id == "__file__":
            return hops - 1
        return None


def _file_depth(rel: Path) -> int:
    """tools/db/storage.py -> 2: two hops above the file's directory is the root."""
    return len(rel.parts) - 1


class _Parents(ast.NodeVisitor):
    def __init__(self) -> None:
        self.parent: dict[ast.AST, ast.AST] = {}

    def generic_visit(self, node):
        for child in ast.iter_child_nodes(node):
            self.parent[child] = node
        super().generic_visit(node)


def _in_sys_path_expr(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    q = node
    while q in parents:
        q = parents[q]
        if isinstance(q, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return False
        if isinstance(q, (ast.Call, ast.Compare, ast.If, ast.Expr, ast.Assign)):
            if "sys.path" in ast.unparse(q):
                return True
    return False


def _bootstrap_names(tree: ast.AST, parents: dict[ast.AST, ast.AST]) -> set[str]:
    """Names assigned from a __file__ climb whose EVERY use is a sys.path expression."""
    candidates: dict[str, list[bool]] = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name) and climb_hops(node.value) is not None):
            candidates.setdefault(node.targets[0].id, [])
    if not candidates:
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in candidates:
            candidates[node.id].append(_in_sys_path_expr(node, parents))
    return {name for name, uses in candidates.items() if uses and all(uses)}


def _qualname_index(tree: ast.AST) -> dict[int, str]:
    out: dict[int, str] = {}

    def visit(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = f"{prefix}.{child.name}" if prefix else child.name
                for sub in ast.walk(child):
                    if hasattr(sub, "lineno"):
                        out.setdefault(sub.lineno, q)
                visit(child, q)
            else:
                visit(child, prefix)

    visit(tree, "")
    return out


def scan_file(path: Path, repo: Path = REPO) -> list[dict]:
    """Every self-root site in ``path``. Each dict carries a stable key."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text)
    except (OSError, SyntaxError):
        return []
    rel = path.relative_to(repo)
    depth = _file_depth(rel)
    pv = _Parents()
    pv.visit(tree)
    parents = pv.parent
    bootstrap = _bootstrap_names(tree, parents)
    qual = _qualname_index(tree)

    sites: list[dict] = []
    seen_lines: set[int] = set()
    inline_counter: dict[str, int] = {}
    for node in ast.walk(tree):
        hops = climb_hops(node)
        if hops is None or hops < depth:
            continue
        # only the OUTERMOST climb on a line counts once
        if node.lineno in seen_lines:
            continue
        seen_lines.add(node.lineno)
        kind = "overwalk" if hops > depth else "root"
        assign = parents.get(node)
        name = None
        if (isinstance(assign, ast.Assign) and len(assign.targets) == 1
                and isinstance(assign.targets[0], ast.Name) and assign.value is node):
            name = assign.targets[0].id
            if name in bootstrap:
                continue  # the sys.path idiom: the import root, correct after a move
        q = qual.get(node.lineno, "<module>")
        # Keys must survive the census reader, which strips `#` comments, and
        # must be unique when the same name is assigned twice in one scope.
        if name is None:
            inline_counter[q] = inline_counter.get(q, 0) + 1
            ident = f"inline-{inline_counter[q]}"
        else:
            seen_key = f"{q}::{name}"
            inline_counter[seen_key] = inline_counter.get(seen_key, 0) + 1
            ident = name if inline_counter[seen_key] == 1 else f"{name}-{inline_counter[seen_key]}"
        sites.append({
            "key": f"{rel.as_posix()}::{q}::{ident}",
            "file": rel.as_posix(),
            "line": node.lineno,
            "qualname": q,
            "name": name,
            "hops": hops,
            "depth": depth,
            "kind": kind,
            "expr": ast.unparse(node),
            "fixable": name is not None and isinstance(assign, ast.Assign)
            and q == "<module>" and kind == "root",
        })
    return sites


# ── config ───────────────────────────────────────────────────────────────────
def gate_file(repo: Path = REPO) -> Path:
    return repo / "args" / "self_root_gate.yaml"


def load_gate(path: Path | None = None) -> dict:
    # Resolved at CALL time, so a report against another repository reads THAT
    # repository's gate rather than the one this module was imported beside.
    path = Path(path) if path is not None else gate_file(REPO)
    try:
        import yaml  # noqa: PLC0415 — pyyaml IS declared
    except ImportError:  # pragma: no cover
        raise SystemExit("self_root_census: pyyaml is required and declared")
    if not path.exists():
        raise SystemExit(f"self_root_census: missing gate config {path}")
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get(GATE_KEY, {})


def load_census(repo: Path, cfg: dict) -> set[str]:
    census_path = repo / cfg.get("census_file", "args/self_root_census.txt")
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
    return any(fnmatch(rel, e.get("path", "")) for e in cfg.get("exclude", []) or [])


def filter_scope(files: list[str], cfg: dict | None = None) -> list[str]:
    cfg = cfg or load_gate()
    roots = cfg.get("scan_roots", ["tools"])
    out = []
    for f in files:
        f = f.replace("\\", "/")
        if f.endswith(".py") and any(f.startswith(r + "/") for r in roots) and not _excluded(f, cfg):
            out.append(f)
    return out


def collect(repo: Path, cfg: dict, only: list[str] | None = None) -> list[dict]:
    roots = cfg.get("scan_roots", ["tools"])
    targets: list[Path] = []
    if only is not None:
        targets = [repo / f for f in filter_scope(only, cfg)]
    else:
        for root in roots:
            base = repo / root
            if base.is_dir():
                targets += sorted(base.rglob("*.py"))
    sites: list[dict] = []
    for path in targets:
        if not path.exists():
            continue
        rel = path.relative_to(repo).as_posix()
        if _excluded(rel, cfg):
            continue
        sites += scan_file(path, repo)
    return sites


def _staged_files(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=repo, capture_output=True, text=True, check=False,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip().endswith(".py")]


# ── report ───────────────────────────────────────────────────────────────────
def build_report(repo: Path = REPO, only: list[str] | None = None) -> dict:
    cfg = load_gate(gate_file(repo))
    census = load_census(repo, cfg)
    sites = collect(repo, cfg, only)
    keys = [s["key"] for s in sites]
    unregistered = [s for s in sites if s["key"] not in census]
    ceiling = int(cfg.get("self_root_max", 0))
    report = {
        "scope": "changed" if only is not None else "tree",
        "sites_seen": len(sites),
        "root_sites": len([s for s in sites if s["kind"] == "root"]),
        "overwalk_sites": len([s for s in sites if s["kind"] == "overwalk"]),
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
    cfg = load_gate(gate_file(repo))
    census_path = repo / cfg.get("census_file", "args/self_root_census.txt")
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


# ── --fix ────────────────────────────────────────────────────────────────────
_IMPORT_LINE = "from icdev.core.paths import repo_root"


def fix_file(path: Path, repo: Path = REPO) -> list[str]:
    """Rewrite the simple module-level ``NAME = <climb to root>`` sites.

    Returns the names rewritten. Touches nothing else: an inline join, a site
    inside a function, an over-walk or the bootstrap idiom is left for a human.
    """
    sites = [s for s in scan_file(path, repo) if s["fixable"]]
    if not sites:
        return []
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    tree = ast.parse("".join(lines))
    rewritten: list[str] = []
    # apply bottom-up so line numbers stay valid
    for site in sorted(sites, key=lambda s: -s["line"]):
        target = None
        for node in tree.body:
            if (isinstance(node, ast.Assign) and node.lineno == site["line"]
                    and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
                target = node
                break
        if target is None:
            continue
        start, end = target.lineno - 1, target.end_lineno
        indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
        lines[start:end] = [f"{indent}{site['name']} = repo_root(__file__)\n"]
        rewritten.append(site["name"])
    if rewritten and not any(l.strip() == _IMPORT_LINE for l in lines):
        # after the last top-level import that precedes the first rewritten site
        insert_at = 0
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)) and node.lineno < min(s["line"] for s in sites):
                insert_at = node.end_lineno
        lines.insert(insert_at, _IMPORT_LINE + "\n")
    path.write_text("".join(lines), encoding="utf-8", newline="\n")
    return rewritten


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true", help="exit 1 on a NEW site or a breached ceiling")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--changed", nargs="*", help="limit the scan to these files")
    parser.add_argument("--staged", action="store_true", help="scan only staged files")
    parser.add_argument("--prune", action="store_true")
    parser.add_argument("--fix", nargs="+", metavar="FILE", help="rewrite simple module-level sites in FILE(s)")
    args = parser.parse_args(argv)

    if args.prune:
        dropped = prune()
        print(f"Self-root census: pruned {dropped} stale entr(ies).")
        return 0
    if args.fix:
        total = 0
        for f in args.fix:
            names = fix_file(REPO / f)
            total += len(names)
            print(f"  {f}: {', '.join(names) if names else 'nothing fixable'}")
        print(f"Self-root census: rewrote {total} site(s) onto repo_root(__file__). "
              "Re-run the module's tests; then drop the entries from args/self_root_census.txt "
              "and LOWER self_root_max.")
        return 0

    only = None
    if args.staged:
        only = _staged_files(REPO)
    elif args.changed is not None:
        only = list(args.changed)

    report = build_report(REPO, only)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(
            f"Self-root census ({report['scope']}): {report['sites_seen']} site(s) seen "
            f"({report['root_sites']} root, {report['overwalk_sites']} overwalk), "
            f"{report['registered']} registered, {len(report['unregistered'])} unregistered "
            f"| census {report['census_size']} (ceiling {report['ceiling']})"
        )
        for site in report["unregistered"][:40]:
            print(f"  NEW  {site['file']}:{site['line']}  {site['expr']}  [{site['kind']}]")
        if report.get("over_ceiling"):
            print(f"  CEILING BREACHED: census {report['census_size']} > {report['ceiling']}. "
                  "self_root_max may only go DOWN.")

    if args.check and not report["ok"]:
        print(
            "\nA module that computes the repository root from its own location "
            "carries a hard-coded claim about where it sits, and the claim breaks "
            "silently the moment the file moves. Use the one resolver instead:\n"
            "    from icdev.core.paths import repo_root\n"
            "    BASE_DIR = repo_root(__file__)\n"
            "(`python tools/ci/self_root_census.py --fix <file>` does the simple "
            "form.) A module-local path such as Path(__file__).parent / 'templates' "
            "is fine and is not a site. Registering a new site in "
            "args/self_root_census.txt is a debt you have written down, and it "
            "breaches the ceiling.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    os.chdir(REPO)
    raise SystemExit(main())
