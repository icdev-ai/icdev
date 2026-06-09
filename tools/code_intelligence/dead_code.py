#!/usr/bin/env python3
# CUI // SP-CTI
"""Dead-code & dependency-graph lens for ICDEV (CodeLens CL-1 + CL-2).

Inspired by Fallow (fallow.tools) static-layer "what is connected to what?"
analysis, adapted to ICDEV's deterministic / air-gap-safe contract. Pure
stdlib (ast, re, pathlib, json) — no LLM, no DB, no third-party deps.
Read-only and advisory: never modifies source.

Capabilities
------------
CL-1  Dead / unused-symbol detection
        * dead_function / dead_class  - module-level def never referenced
        * orphan_file                 - .py with no inbound import + no __main__
        * unused_dependency           - requirements.txt entry never imported
CL-2  Import graph + circular dependencies
        * builds intra-repo module graph, reports strongly-connected cycles

Every finding carries a confidence (high/medium/low), a plain-English
explanation, and a suggested action so downstream agents can act on it.

Usage
-----
    python tools/code_intelligence/dead_code.py --scan --json
    python tools/code_intelligence/dead_code.py --project-dir tools/ --explain --human
    python tools/code_intelligence/dead_code.py --check circular --json
    python tools/code_intelligence/dead_code.py --check deps --json
    python tools/code_intelligence/dead_code.py --graph --json
"""

import argparse
import ast
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Directories never worth scanning (consistent with code_analyzer.py).
_EXCLUDE_DIRS = {
    "venv", ".venv", "env", "node_modules", ".git", "__pycache__",
    "build", "dist", ".tox", ".eggs", "vendor", "target", "bin", "obj",
    ".tmp", "playwright",
}

# Module-level names that are entry points / framework hooks, not dead even
# when nothing in the repo references them by name.
_ENTRYPOINT_NAMES = {"main", "run", "setup", "create_app", "application", "app"}

# requirements that are CLI / dev tools, invoked by name not `import`ed.
# Flagging these as "unused" would be a false positive, so we down-rank them.
_TOOL_DEPS = {
    "ruff", "behave", "pytest", "pytest-cov", "schemathesis", "bandit",
    "cyclonedx-bom", "pip", "setuptools", "wheel", "pre-commit",
}

# Distribution-name -> import-name overrides (PyPI name != module name).
_DIST_TO_IMPORT = {
    "pyyaml": "yaml",
    "python-dotenv": "dotenv",
    "python-pptx": "pptx",
    "pillow": "PIL",
    "psycopg2-binary": "psycopg2",
    "beautifulsoup4": "bs4",
    "rank-bm25": "rank_bm25",
    "scikit-learn": "sklearn",
    "oscal-pydantic": "oscal_pydantic",
    "cyclonedx-bom": "cyclonedx",
}


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


def iter_python_files(root: Path) -> List[Path]:
    """Return all .py files under root, skipping excluded dirs (sorted)."""
    import os

    results: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS]
        for fname in filenames:
            if fname.endswith(".py"):
                fp = Path(dirpath) / fname
                try:
                    if fp.stat().st_size <= 1048576:
                        results.append(fp)
                except OSError:
                    pass
    return sorted(results)


def module_name_for(path: Path, base: Path) -> Optional[str]:
    """Dotted module name for a file relative to base.

    tools/foo/bar.py       -> tools.foo.bar
    tools/foo/__init__.py  -> tools.foo
    Returns None if path is outside base.
    """
    try:
        rel = path.resolve().relative_to(base.resolve())
    except ValueError:
        return None
    parts = list(rel.parts)
    if not parts or not parts[-1].endswith(".py"):
        return None
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]
    return ".".join(parts) if parts else None


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def _norm_module(dotted: str) -> str:
    """Collapse the icdev.tools.* backward-compat shim onto tools.* ."""
    if dotted.startswith("icdev.tools."):
        return dotted[len("icdev."):]
    return dotted


# ---------------------------------------------------------------------------
# Per-file AST extraction
# ---------------------------------------------------------------------------


class _FileFacts:
    """Names defined, names referenced, imports, and metadata for one file."""

    __slots__ = (
        "path", "rel", "module", "defs", "referenced", "string_values",
        "import_targets", "has_main", "all_exports",
    )

    def __init__(self, path: Path, rel: str, module: Optional[str]):
        self.path = path
        self.rel = rel
        self.module = module
        # name -> (kind, lineno, decorated)
        self.defs: Dict[str, Tuple[str, int, bool]] = {}
        self.referenced: Set[str] = set()
        self.string_values: Set[str] = set()
        # raw dotted import targets (e.g. "tools.foo.bar", "os.path")
        self.import_targets: List[str] = []
        self.has_main = False
        self.all_exports: Set[str] = set()


def _decorator_names(node: ast.AST) -> List[str]:
    names = []
    for dec in getattr(node, "decorator_list", []) or []:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, ast.Attribute):
            names.append(target.attr)
    return names


def _extract_all_exports(node: ast.Assign) -> Set[str]:
    """Pull string entries out of an `__all__ = [...]` assignment."""
    exports: Set[str] = set()
    for tgt in node.targets:
        if isinstance(tgt, ast.Name) and tgt.id == "__all__":
            seq = node.value
            if isinstance(seq, (ast.List, ast.Tuple, ast.Set)):
                for elt in seq.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        exports.add(elt.value)
    return exports


def analyze_file(path: Path, base: Path) -> Optional[_FileFacts]:
    """Parse one file into a _FileFacts. Returns None on unreadable/unparsable."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, ValueError, OSError):
        return None

    facts = _FileFacts(path, _rel(path, base), module_name_for(path, base))

    # Module-level defs (functions + classes only; methods are excluded to
    # avoid polymorphism/framework false positives).
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            facts.defs[node.name] = ("function", node.lineno, bool(node.decorator_list))
        elif isinstance(node, ast.ClassDef):
            facts.defs[node.name] = ("class", node.lineno, bool(node.decorator_list))
        elif isinstance(node, ast.Assign):
            facts.all_exports |= _extract_all_exports(node)

    # Walk the whole tree for references, strings, imports, __main__.
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            facts.referenced.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            facts.referenced.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # bound the set: only short, identifier-like strings matter for
            # dynamic dispatch (getattr / importlib by-name).
            if len(node.value) <= 120:
                facts.string_values.add(node.value)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                facts.import_targets.append(alias.name)
                facts.referenced.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level == 0 and mod:
                facts.import_targets.append(mod)
            for alias in node.names:
                # an imported symbol counts as a reference to that symbol
                facts.referenced.add(alias.name)
                facts.referenced.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dname in _decorator_names(node):
                facts.referenced.add(dname)

    # __main__ guard => this file is an executable entry point.
    if re.search(r'^if\s+__name__\s*==\s*["\']__main__["\']', source, re.MULTILINE):
        facts.has_main = True

    return facts


# ---------------------------------------------------------------------------
# CL-2: import graph + cycles
# ---------------------------------------------------------------------------


def build_import_graph(
    facts: List[_FileFacts],
) -> Tuple[Dict[str, str], Dict[str, Set[str]]]:
    """Build the intra-repo module dependency graph.

    Returns (module -> rel_path, module -> set(imported modules)).
    Only edges between modules present in `facts` are kept (external libs
    are ignored).
    """
    module_to_rel: Dict[str, str] = {}
    for f in facts:
        if f.module:
            module_to_rel[_norm_module(f.module)] = f.rel
    known = set(module_to_rel)

    adjacency: Dict[str, Set[str]] = {m: set() for m in known}
    for f in facts:
        if not f.module:
            continue
        src = _norm_module(f.module)
        for raw in f.import_targets:
            target = _norm_module(raw)
            # match longest known module prefix (handles `from pkg import sub`)
            resolved = None
            if target in known:
                resolved = target
            else:
                parts = target.split(".")
                for i in range(len(parts) - 1, 0, -1):
                    cand = ".".join(parts[:i])
                    if cand in known:
                        resolved = cand
                        break
            if resolved and resolved != src:
                adjacency[src].add(resolved)
    return module_to_rel, adjacency


def find_cycles(adjacency: Dict[str, Set[str]]) -> List[List[str]]:
    """Tarjan strongly-connected components; return SCCs that form cycles.

    A cycle is any SCC with >1 node, or a single node with a self-edge.
    Deterministic: nodes visited in sorted order, members returned sorted.
    """
    index_counter = [0]
    stack: List[str] = []
    on_stack: Set[str] = set()
    indices: Dict[str, int] = {}
    lowlink: Dict[str, int] = {}
    result: List[List[str]] = []

    def strongconnect(v: str) -> None:
        indices[v] = index_counter[0]
        lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(adjacency.get(v, ())):
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])
        if lowlink[v] == indices[v]:
            comp: List[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1 or (len(comp) == 1 and comp[0] in adjacency.get(comp[0], set())):
                result.append(sorted(comp))

    sys.setrecursionlimit(max(10000, sys.getrecursionlimit()))
    for node in sorted(adjacency):
        if node not in indices:
            strongconnect(node)
    return sorted(result, key=lambda c: (len(c), c))


# ---------------------------------------------------------------------------
# CL-1: dead symbols, orphan files, unused deps
# ---------------------------------------------------------------------------


def _finding(
    kind: str,
    name: str,
    file: Optional[str],
    line: Optional[int],
    confidence: str,
    explanation: str,
    suggested_action: str,
) -> Dict[str, Any]:
    return {
        "kind": kind,
        "name": name,
        "file": file,
        "line": line,
        "confidence": confidence,
        "explanation": explanation,
        "suggested_action": suggested_action,
    }


def find_dead_symbols(facts: List[_FileFacts]) -> List[Dict[str, Any]]:
    """Module-level functions/classes never referenced anywhere in the set."""
    global_refs: Set[str] = set()
    global_strings: Set[str] = set()
    for f in facts:
        global_refs |= f.referenced
        global_strings |= f.string_values

    findings: List[Dict[str, Any]] = []
    for f in facts:
        for name, (kind, lineno, decorated) in sorted(f.defs.items()):
            if name.startswith("__") and name.endswith("__"):
                continue
            if name in _ENTRYPOINT_NAMES or name in f.all_exports:
                continue
            if decorated:
                # decorated defs are usually framework-registered (routes,
                # fixtures, CLI commands) and referenced indirectly.
                continue
            if name in global_refs:
                continue
            # name appears as a string literal -> possible dynamic dispatch.
            dynamic = name in global_strings
            confidence = "low" if dynamic else "medium"
            note = (
                " The name also appears as a string literal, so it may be "
                "called dynamically (getattr/importlib)." if dynamic else ""
            )
            findings.append(_finding(
                kind="dead_function" if kind == "function" else "dead_class",
                name=name,
                file=f.rel,
                line=lineno,
                confidence=confidence,
                explanation=(
                    f"Module-level {kind} '{name}' is defined but never "
                    f"referenced (no call, import, or decorator use) across "
                    f"the scanned tree.{note}"
                ),
                suggested_action=(
                    f"Confirm '{name}' is not reached via reflection or an "
                    f"external entry point, then remove it or add it to "
                    f"__all__ if it is a public API."
                ),
            ))
    return findings


def find_orphan_files(
    facts: List[_FileFacts],
    adjacency: Dict[str, Set[str]],
) -> List[Dict[str, Any]]:
    """Files with no inbound import edge and no __main__ guard."""
    inbound: Dict[str, int] = {}
    for src, targets in adjacency.items():
        for t in targets:
            inbound[t] = inbound.get(t, 0) + 1

    findings: List[Dict[str, Any]] = []
    for f in facts:
        if not f.module or f.has_main:
            continue
        base = f.path.name
        if base == "__init__.py" or base.startswith("test_") or base == "conftest.py":
            continue
        mod = _norm_module(f.module)
        if inbound.get(mod, 0) == 0:
            findings.append(_finding(
                kind="orphan_file",
                name=mod,
                file=f.rel,
                line=None,
                confidence="medium",
                explanation=(
                    f"No other scanned module imports '{mod}', and it has no "
                    f"`if __name__ == \"__main__\"` entry point."
                ),
                suggested_action=(
                    "Verify the file is not a CLI script run by path or loaded "
                    "dynamically, then wire it into the import graph or remove it."
                ),
            ))
    return findings


def _parse_requirements(req_path: Path) -> List[str]:
    """Return distribution names from a requirements.txt (lowercased)."""
    if not req_path.exists():
        return []
    deps: List[str] = []
    for line in req_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        # strip inline comment, version specifiers, and extras
        line = line.split("#", 1)[0].strip()
        name = re.split(r"[<>=!~;\[ ]", line, maxsplit=1)[0].strip().lower()
        if name:
            deps.append(name)
    return deps


def find_unused_dependencies(
    req_path: Path,
    facts: List[_FileFacts],
) -> List[Dict[str, Any]]:
    """requirements.txt entries whose import name never appears in the tree."""
    imported_roots: Set[str] = set()
    for f in facts:
        for target in f.import_targets:
            imported_roots.add(target.split(".")[0].lower())

    findings: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for dist in _parse_requirements(req_path):
        if dist in seen:
            continue
        seen.add(dist)
        import_name = _DIST_TO_IMPORT.get(dist, dist.replace("-", "_")).lower()
        if import_name in imported_roots or dist.replace("-", "_") in imported_roots:
            continue
        is_tool = dist in _TOOL_DEPS
        findings.append(_finding(
            kind="unused_dependency",
            name=dist,
            file="requirements.txt",
            line=None,
            confidence="low" if is_tool else "medium",
            explanation=(
                f"Declared dependency '{dist}' (import '{import_name}') is "
                f"never imported in the scanned tree."
                + (" It is a known CLI/dev tool, so it may be used without an "
                   "`import`." if is_tool else "")
            ),
            suggested_action=(
                "Confirm it is unused (including by entry-point scripts and "
                "optional features), then remove it from requirements.txt."
            ),
        ))
    return findings


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

_CHECKS = ("dead-code", "orphans", "deps", "circular")


def run_scan(
    project_dir: Optional[str] = None,
    checks: Optional[List[str]] = None,
    base: Optional[Path] = None,
    req_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run the requested checks and return a structured report."""
    base = base or BASE_DIR
    target = Path(project_dir) if project_dir else (base / "tools")
    if not target.is_absolute():
        target = base / target
    checks = checks or list(_CHECKS)
    req_path = req_path or (base / "requirements.txt")

    files = iter_python_files(target)
    facts = [a for a in (analyze_file(fp, base) for fp in files) if a is not None]
    module_to_rel, adjacency = build_import_graph(facts)

    findings: List[Dict[str, Any]] = []
    if "dead-code" in checks:
        findings += find_dead_symbols(facts)
    if "orphans" in checks:
        findings += find_orphan_files(facts, adjacency)
    if "deps" in checks:
        findings += find_unused_dependencies(req_path, facts)
    cycles: List[List[str]] = []
    if "circular" in checks:
        cycles = find_cycles(adjacency)
        for cycle in cycles:
            files_in = [module_to_rel.get(m) for m in cycle]
            findings.append(_finding(
                kind="circular_dependency",
                name=" -> ".join(cycle + [cycle[0]]),
                file=next((x for x in files_in if x), None),
                line=None,
                confidence="high",
                explanation=(
                    "These modules form an import cycle: "
                    + " -> ".join(cycle + [cycle[0]])
                    + ". Cycles block clean teardown and make the modules "
                    "impossible to load or test in isolation."
                ),
                suggested_action=(
                    "Break the cycle by extracting the shared symbols into a "
                    "third module, or defer one import to function scope."
                ),
            ))

    findings.sort(key=lambda x: (x["kind"], x["file"] or "", x["name"]))
    edge_count = sum(len(v) for v in adjacency.values())
    counts: Dict[str, int] = {}
    for f in findings:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1

    return {
        "tool": "dead_code",
        "target": str(target),
        "checks": checks,
        "summary": {
            "files_scanned": len(facts),
            "findings": len(findings),
            "by_kind": counts,
            "graph": {
                "nodes": len(adjacency),
                "edges": edge_count,
                "cycles": len(cycles),
            },
        },
        "findings": findings,
        "graph": {
            "nodes": sorted(adjacency),
            "edges": sorted(
                [src, t] for src, targets in adjacency.items() for t in sorted(targets)
            ),
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_human(report: Dict[str, Any], explain: bool) -> None:
    s = report["summary"]
    print()
    print(f"  Dead-code lens: {report['target']}")
    print(f"  Files scanned: {s['files_scanned']}   "
          f"Graph: {s['graph']['nodes']} nodes / {s['graph']['edges']} edges / "
          f"{s['graph']['cycles']} cycles")
    print(f"  Findings: {s['findings']}  {s['by_kind']}")
    print()
    for f in report["findings"]:
        loc = f"{f['file']}:{f['line']}" if f.get("line") else (f["file"] or "-")
        print(f"  [{f['confidence'].upper():6}] {f['kind']:20} {f['name']}")
        print(f"           {loc}")
        if explain:
            print(f"           why: {f['explanation']}")
            print(f"           fix: {f['suggested_action']}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dead-code & dependency-graph lens (CodeLens CL-1 + CL-2)"
    )
    parser.add_argument("--scan", action="store_true", help="Run a scan (default)")
    parser.add_argument("--project-dir", default=None, help="Directory to scan (default: tools/)")
    parser.add_argument(
        "--check", default="all",
        choices=("all",) + _CHECKS,
        help="Limit to one check (default: all)",
    )
    parser.add_argument("--explain", action="store_true", help="Include why/fix in human output")
    parser.add_argument("--graph", action="store_true", help="Include the full edge list in JSON")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Formatted terminal output")
    parser.add_argument(
        "--gate", action="store_true",
        help="Exit 1 if any high-confidence finding exists",
    )
    args = parser.parse_args()

    checks = list(_CHECKS) if args.check == "all" else [args.check]
    report = run_scan(project_dir=args.project_dir, checks=checks)

    if not args.graph:
        report.pop("graph", None)

    if args.human:
        _print_human(report, args.explain)
    else:
        print(json.dumps(report, indent=2))

    if args.gate:
        high = [f for f in report["findings"] if f["confidence"] == "high"]
        return 1 if high else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
