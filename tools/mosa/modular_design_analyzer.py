#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""MOSA Modular Design Analyzer — Static analysis for MOSA modularity metrics.

Analyzes source code to compute coupling, cohesion, interface coverage,
and circular dependency metrics per 10 U.S.C. Section 4401 and DoDI 5000.87.

Approach (D13):
  - Python: ``ast`` module for import/class analysis
  - Java/Go/TypeScript/Rust/C#: regex-based import extraction
  - Circular deps: ``graphlib.TopologicalSorter`` (D40)

CLI:
    python tools/mosa/modular_design_analyzer.py --project-dir /path [--project-id P] \\
        [--store] [--json] [--human]
"""

import argparse
import ast
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "mosa_config.yaml"

# ---------------------------------------------------------------------------
# Lightweight YAML parser (avoids pyyaml hard dep, mirrors sast_runner.py)
# ---------------------------------------------------------------------------

_DEFAULT_WEIGHTS: Dict[str, float] = {
    "coupling_score": 0.25,
    "cohesion_score": 0.20,
    "interface_coverage": 0.20,
    "circular_dep_penalty": 0.15,
    "icd_completeness": 0.10,
    "tsp_currency": 0.10,
}

_DEFAULT_THRESHOLDS: Dict[str, Any] = {
    "min_interface_coverage_pct": 80,
    "max_coupling_score": 0.4,
    "min_cohesion_score": 0.6,
    "max_circular_dependencies": 0,
    "min_modularity_score": 0.6,
}


def _parse_config() -> Dict[str, Any]:
    """Parse mosa_config.yaml for modularity_weights and thresholds."""
    cfg: Dict[str, Any] = {
        "modularity_weights": dict(_DEFAULT_WEIGHTS),
        "thresholds": dict(_DEFAULT_THRESHOLDS),
    }
    if not CONFIG_PATH.exists():
        return cfg

    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
        section = ""
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped == "modularity_weights:":
                section = "weights"
                continue
            if stripped == "thresholds:":
                section = "thresholds"
                continue
            # Exit section on unindented key
            if not line.startswith(" ") and ":" in stripped:
                section = ""
                continue
            if section in ("weights", "thresholds") and ":" in stripped:
                key, _, val = stripped.partition(":")
                key = key.strip()
                val = val.strip().split("#")[0].strip()  # strip inline comments
                try:
                    parsed = float(val)
                except ValueError:
                    continue
                if section == "weights":
                    cfg["modularity_weights"][key] = parsed
                else:
                    cfg["thresholds"][key] = parsed
    except Exception:
        pass
    return cfg


# ---------------------------------------------------------------------------
# Language-specific import extractors
# ---------------------------------------------------------------------------

_EXCLUDE_DIRS = {
    "venv", ".venv", "env", "node_modules", ".git", "__pycache__",
    "build", "dist", ".tox", ".eggs", "vendor", "target", "bin", "obj",
}


def _iter_source_files(root: Path, extensions: Tuple[str, ...]) -> List[Path]:
    """Recursively yield source files, skipping excluded directories."""
    results: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS]
        for fname in filenames:
            if fname.endswith(extensions):
                results.append(Path(dirpath) / fname)
    return results


def _module_name(path: Path, root: Path) -> str:
    """Derive a top-level module name from file path relative to root."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return path.stem
    parts = rel.parts
    if len(parts) >= 2:
        return parts[0]
    return rel.stem


def _extract_python_imports(filepath: Path) -> Tuple[Set[str], bool]:
    """Parse a Python file with ``ast`` and return imported module names.

    Returns:
        (set_of_imported_top_level_modules, has_abstract_definitions)
    """
    imports: Set[str] = set()
    has_abstract = False
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, UnicodeDecodeError):
        return imports, has_abstract

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                imports.add(top)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                imports.add(top)
                # Check for ABC / Protocol / abstractmethod
                if top in ("abc", "typing"):
                    for alias in (node.names or []):
                        if alias.name in ("ABC", "ABCMeta", "abstractmethod", "Protocol"):
                            has_abstract = True
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                name = ""
                if isinstance(base, ast.Name):
                    name = base.id
                elif isinstance(base, ast.Attribute):
                    name = base.attr
                if name in ("ABC", "ABCMeta", "Protocol"):
                    has_abstract = True
    return imports, has_abstract


# Regex patterns for non-Python languages
_JAVA_IMPORT = re.compile(r'^\s*import\s+([\w.]+)', re.MULTILINE)
_GO_IMPORT = re.compile(r'"([\w./-]+)"')
_TS_IMPORT = re.compile(r'''(?:import|require)\s*\(?\s*['"]([^'"]+)['"]''', re.MULTILINE)
_RUST_USE = re.compile(r'^\s*use\s+([\w:]+)', re.MULTILINE)
_CS_USING = re.compile(r'^\s*using\s+([\w.]+)', re.MULTILINE)

_IFACE_PATTERNS: Dict[str, re.Pattern] = {
    "java": re.compile(r'\b(interface|abstract\s+class)\b'),
    "go": re.compile(r'\btype\s+\w+\s+interface\b'),
    "typescript": re.compile(r'\b(interface|abstract\s+class)\b'),
    "rust": re.compile(r'\btrait\b'),
    "csharp": re.compile(r'\b(interface|abstract\s+class)\b'),
}

_LANG_EXT: Dict[str, Tuple[str, ...]] = {
    "python": (".py",),
    "java": (".java",),
    "go": (".go",),
    "typescript": (".ts", ".tsx"),
    "rust": (".rs",),
    "csharp": (".cs",),
}


def _regex_imports(filepath: Path, lang: str) -> Tuple[Set[str], bool]:
    """Extract imports via regex for non-Python languages."""
    imports: Set[str] = set()
    has_abstract = False
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return imports, has_abstract

    patterns = {
        "java": _JAVA_IMPORT,
        "go": _GO_IMPORT,
        "typescript": _TS_IMPORT,
        "rust": _RUST_USE,
        "csharp": _CS_USING,
    }
    pat = patterns.get(lang)
    if pat:
        for m in pat.finditer(source):
            top = m.group(1).split(".")[0].split("/")[0].split(":")[0]
            if top:
                imports.add(top)

    iface_pat = _IFACE_PATTERNS.get(lang)
    if iface_pat and iface_pat.search(source):
        has_abstract = True

    return imports, has_abstract


# ---------------------------------------------------------------------------
# Graph analysis
# ---------------------------------------------------------------------------

def _build_dependency_graph(
    root: Path,
) -> Tuple[Dict[str, Set[str]], Set[str], int, int]:
    """Scan project and build module-level dependency graph.

    Returns:
        (dep_graph, project_modules, interface_file_count, total_file_count)
    """
    dep_graph: Dict[str, Set[str]] = {}
    project_modules: Set[str] = set()
    interface_file_count = 0
    total_file_count = 0

    # Detect which languages are present
    for lang, exts in _LANG_EXT.items():
        files = _iter_source_files(root, exts)
        if not files:
            continue

        for fpath in files:
            total_file_count += 1
            mod = _module_name(fpath, root)
            project_modules.add(mod)

            if lang == "python":
                imports, has_iface = _extract_python_imports(fpath)
            else:
                imports, has_iface = _regex_imports(fpath, lang)

            if has_iface:
                interface_file_count += 1

            if mod not in dep_graph:
                dep_graph[mod] = set()
            dep_graph[mod].update(imports)

    return dep_graph, project_modules, interface_file_count, total_file_count


def _count_cycles(dep_graph: Dict[str, Set[str]], project_modules: Set[str]) -> int:
    """Count circular dependency cycles using DFS on project-internal edges."""
    # Build adjacency restricted to project modules
    adj: Dict[str, Set[str]] = {}
    for mod, deps in dep_graph.items():
        if mod in project_modules:
            adj[mod] = deps & project_modules

    visited: Set[str] = set()
    in_stack: Set[str] = set()
    cycles = 0

    def _dfs(node: str) -> None:
        nonlocal cycles
        visited.add(node)
        in_stack.add(node)
        for neighbor in adj.get(node, set()):
            if neighbor in in_stack:
                cycles += 1
            elif neighbor not in visited:
                _dfs(neighbor)
        in_stack.discard(node)

    for node in adj:
        if node not in visited:
            _dfs(node)

    return cycles


def _detect_circular_deps(
    dep_graph: Dict[str, Set[str]], project_modules: Set[str],
) -> int:
    """Use graphlib.TopologicalSorter to detect cycles (D40).

    Falls back to DFS cycle counting when CycleError is raised.
    """
    internal: Dict[str, Set[str]] = {}
    for mod in project_modules:
        internal[mod] = (dep_graph.get(mod, set()) & project_modules) - {mod}

    try:
        ts = TopologicalSorter(internal)
        ts.prepare()
        return 0
    except CycleError:
        return _count_cycles(dep_graph, project_modules)


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def analyze_modularity(project_dir: str) -> Dict[str, Any]:
    """Run full modularity analysis on *project_dir*.

    Returns a dict with all metric fields matching the DB schema.
    """
    root = Path(project_dir).resolve()
    if not root.is_dir():
        return {"error": f"Directory not found: {project_dir}"}

    dep_graph, project_modules, iface_file_count, total_files = \
        _build_dependency_graph(root)

    module_count = len(project_modules)
    interface_count = iface_file_count  # files declaring interfaces

    # --- coupling: cross-module edges / total possible directed edges ------
    cross_module_edges = 0
    total_import_edges = 0
    for mod, deps in dep_graph.items():
        if mod not in project_modules:
            continue
        internal_deps = deps & project_modules - {mod}
        cross_module_edges += len(internal_deps)
        total_import_edges += len(deps)

    max_possible = module_count * (module_count - 1) if module_count > 1 else 1
    coupling_score = round(min(cross_module_edges / max_possible, 1.0), 4) \
        if max_possible > 0 else 0.0

    # --- cohesion: avg(internal_refs / total_refs) per module --------------
    cohesion_values: List[float] = []
    for mod in project_modules:
        deps = dep_graph.get(mod, set())
        if not deps:
            cohesion_values.append(1.0)
            continue
        internal = len(deps - project_modules)  # stdlib / 3rd-party
        external = len(deps & project_modules - {mod})
        total = internal + external
        if total == 0:
            cohesion_values.append(1.0)
        else:
            cohesion_values.append(round(internal / total, 4))
    cohesion_score = round(
        sum(cohesion_values) / len(cohesion_values), 4
    ) if cohesion_values else 0.0

    # --- interface coverage ------------------------------------------------
    # Heuristic: % of modules that import at least one interface-bearing module
    iface_modules: Set[str] = set()
    for mod in project_modules:
        # A module is an "interface module" if any of its files declared ABCs
        # We approximate: if the module contributes to iface_file_count
        pass
    # Simpler: interface_coverage = interface_files / total_files * 100
    interface_coverage_pct = round(
        (iface_file_count / total_files * 100) if total_files > 0 else 0.0, 2
    )

    # --- circular dependencies (D40) --------------------------------------
    circular_deps = _detect_circular_deps(dep_graph, project_modules)

    # --- ICD / TSP placeholders (populated by other MOSA tools) -----------
    approved_icd_count = 0
    total_icd_required = max(cross_module_edges, 0)
    icd_completeness = (
        approved_icd_count / total_icd_required
        if total_icd_required > 0 else 1.0
    )
    tsp_currency = 0.0  # 0 or 1 — set by TSP manager externally

    # --- overall modularity score -----------------------------------------
    cfg = _parse_config()
    weights = cfg["modularity_weights"]
    overall = round(
        weights.get("coupling_score", 0.25) * (1.0 - coupling_score)
        + weights.get("cohesion_score", 0.20) * cohesion_score
        + weights.get("interface_coverage", 0.20) * (interface_coverage_pct / 100.0)
        + weights.get("circular_dep_penalty", 0.15) * (1.0 if circular_deps == 0 else 0.0)
        + weights.get("icd_completeness", 0.10) * icd_completeness
        + weights.get("tsp_currency", 0.10) * tsp_currency,
        4,
    )

    return {
        "module_count": module_count,
        "interface_count": interface_count,
        "coupling_score": coupling_score,
        "cohesion_score": cohesion_score,
        "interface_coverage_pct": interface_coverage_pct,
        "circular_deps": circular_deps,
        "approved_icd_count": approved_icd_count,
        "total_icd_required": total_icd_required,
        "tsp_current": 0,
        "overall_modularity_score": overall,
        "modules_discovered": sorted(project_modules),
        "total_files_scanned": total_files,
    }


# ---------------------------------------------------------------------------
# Threshold evaluation
# ---------------------------------------------------------------------------

def evaluate_thresholds(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """Compare metrics against mosa_config.yaml thresholds.

    Returns dict with per-metric pass/fail and overall verdict.
    """
    cfg = _parse_config()
    thresholds = cfg["thresholds"]
    checks: List[Dict[str, Any]] = []

    def _check(name: str, value: Any, op: str, threshold: Any) -> Dict[str, Any]:
        if op == "<=":
            passed = value <= threshold
        elif op == ">=":
            passed = value >= threshold
        else:
            passed = value == threshold
        return {"metric": name, "value": value, "threshold": threshold, "op": op, "passed": passed}

    checks.append(_check(
        "coupling_score", metrics["coupling_score"],
        "<=", thresholds.get("max_coupling_score", 0.4),
    ))
    checks.append(_check(
        "cohesion_score", metrics["cohesion_score"],
        ">=", thresholds.get("min_cohesion_score", 0.6),
    ))
    checks.append(_check(
        "interface_coverage_pct", metrics["interface_coverage_pct"],
        ">=", thresholds.get("min_interface_coverage_pct", 80),
    ))
    checks.append(_check(
        "circular_deps", metrics["circular_deps"],
        "<=", int(thresholds.get("max_circular_dependencies", 0)),
    ))
    checks.append(_check(
        "overall_modularity_score", metrics["overall_modularity_score"],
        ">=", thresholds.get("min_modularity_score", 0.6),
    ))

    overall_pass = all(c["passed"] for c in checks)
    return {"passed": overall_pass, "checks": checks}


# ---------------------------------------------------------------------------
# Database storage
# ---------------------------------------------------------------------------

def store_metrics(project_id: str, metrics: Dict[str, Any]) -> str:
    """Persist metrics to ``mosa_modularity_metrics`` table.

    Returns the generated metric record ID.
    """
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found: {DB_PATH}\n"
            "Run: python tools/db/init_icdev_db.py"
        )

    record_id = f"mosa-metric-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            """INSERT INTO mosa_modularity_metrics
               (id, project_id, assessment_date, module_count, interface_count,
                coupling_score, cohesion_score, interface_coverage_pct,
                circular_deps, approved_icd_count, total_icd_required,
                tsp_current, overall_modularity_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record_id,
                project_id,
                now,
                metrics["module_count"],
                metrics["interface_count"],
                metrics["coupling_score"],
                metrics["cohesion_score"],
                metrics["interface_coverage_pct"],
                metrics["circular_deps"],
                metrics["approved_icd_count"],
                metrics["total_icd_required"],
                metrics["tsp_current"],
                metrics["overall_modularity_score"],
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return record_id


# ---------------------------------------------------------------------------
# Human-readable output (--human)
# ---------------------------------------------------------------------------

def _color(code: str, text: str) -> str:
    """Wrap *text* in ANSI escape if stdout is a TTY."""
    if not sys.stdout.isatty():
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


def _pass_fail(passed: bool) -> str:
    return _color("32", "PASS") if passed else _color("31", "FAIL")


def print_human(metrics: Dict[str, Any], gate: Dict[str, Any]) -> None:
    """Pretty-print metrics and gate results to terminal."""
    print()
    print(_color("1;34", "=" * 60))
    print(_color("1;34", "  MOSA Modular Design Analysis"))
    print(_color("1;34", "=" * 60))

    print(f"\n  Modules discovered : {_color('1', str(metrics['module_count']))}")
    print(f"  Interface files    : {_color('1', str(metrics['interface_count']))}")
    print(f"  Files scanned      : {_color('1', str(metrics.get('total_files_scanned', '?')))}")
    print(f"  Coupling score     : {metrics['coupling_score']:.4f}  (lower is better)")
    print(f"  Cohesion score     : {metrics['cohesion_score']:.4f}  (higher is better)")
    print(f"  Interface coverage : {metrics['interface_coverage_pct']:.1f}%")
    print(f"  Circular deps      : {metrics['circular_deps']}")
    print(f"  ICD approved/req   : {metrics['approved_icd_count']}/{metrics['total_icd_required']}")
    print(f"  TSP current        : {'Yes' if metrics['tsp_current'] else 'No'}")
    score_val = metrics["overall_modularity_score"]
    print(f"  Overall score      : {_color('1', f'{score_val:.4f}')}")

    if metrics.get("modules_discovered"):
        print(f"\n  {_color('4', 'Modules')}:")
        for mod in metrics["modules_discovered"]:
            print(f"    - {mod}")

    print(f"\n  {_color('4', 'Gate Evaluation')}:")
    for check in gate["checks"]:
        status = _pass_fail(check["passed"])
        print(f"    [{status}] {check['metric']}: "
              f"{check['value']} {check['op']} {check['threshold']}")

    overall_status = _pass_fail(gate["passed"])
    print(f"\n  Overall Gate: [{overall_status}]")
    print()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MOSA Modular Design Analyzer — static modularity metrics",
    )
    parser.add_argument(
        "--project-dir", required=True,
        help="Path to the project source tree to analyze",
    )
    parser.add_argument(
        "--project-id", default=None,
        help="ICDEV project ID (required for --store)",
    )
    parser.add_argument(
        "--store", action="store_true",
        help="Persist results to mosa_modularity_metrics table",
    )
    parser.add_argument(
        "--json", dest="json_mode", action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--human", action="store_true",
        help="Output results as colored terminal tables",
    )
    args = parser.parse_args()

    # --- Run analysis -----------------------------------------------------
    metrics = analyze_modularity(args.project_dir)
    if "error" in metrics:
        print(json.dumps({"error": metrics["error"]}, indent=2), file=sys.stderr)
        sys.exit(1)

    gate = evaluate_thresholds(metrics)

    # --- Store if requested -----------------------------------------------
    record_id = None
    if args.store:
        if not args.project_id:
            print("ERROR: --project-id is required when using --store",
                  file=sys.stderr)
            sys.exit(1)
        try:
            record_id = store_metrics(args.project_id, metrics)
        except Exception as exc:
            print(f"ERROR storing metrics: {exc}", file=sys.stderr)
            sys.exit(1)

    # --- Output -----------------------------------------------------------
    result: Dict[str, Any] = {
        "project_dir": args.project_dir,
        "assessment_date": datetime.now(timezone.utc).isoformat(),
        "metrics": {
            "module_count": metrics["module_count"],
            "interface_count": metrics["interface_count"],
            "coupling_score": metrics["coupling_score"],
            "cohesion_score": metrics["cohesion_score"],
            "interface_coverage_pct": metrics["interface_coverage_pct"],
            "circular_deps": metrics["circular_deps"],
            "approved_icd_count": metrics["approved_icd_count"],
            "total_icd_required": metrics["total_icd_required"],
            "tsp_current": metrics["tsp_current"],
            "overall_modularity_score": metrics["overall_modularity_score"],
        },
        "modules_discovered": metrics.get("modules_discovered", []),
        "total_files_scanned": metrics.get("total_files_scanned", 0),
        "gate": gate,
    }
    if record_id:
        result["stored"] = True
        result["record_id"] = record_id
    if args.project_id:
        result["project_id"] = args.project_id

    if args.json_mode:
        print(json.dumps(result, indent=2))
    elif args.human:
        print_human(metrics, gate)
        if record_id:
            print(f"  Stored as: {record_id}")
    else:
        # Default: concise text summary
        print(f"MOSA Modularity Analysis — {args.project_dir}")
        print(f"  Modules: {metrics['module_count']}  |  "
              f"Interfaces: {metrics['interface_count']}  |  "
              f"Files: {metrics.get('total_files_scanned', '?')}")
        print(f"  Coupling: {metrics['coupling_score']:.4f}  |  "
              f"Cohesion: {metrics['cohesion_score']:.4f}  |  "
              f"Coverage: {metrics['interface_coverage_pct']:.1f}%")
        print(f"  Circular deps: {metrics['circular_deps']}  |  "
              f"Overall score: {metrics['overall_modularity_score']:.4f}")
        status = "PASSED" if gate["passed"] else "FAILED"
        print(f"  Gate: {status}")
        for check in gate["checks"]:
            if not check["passed"]:
                print(f"    VIOLATION: {check['metric']} = {check['value']} "
                      f"(threshold {check['op']} {check['threshold']})")
        if record_id:
            print(f"  Stored as: {record_id}")


if __name__ == "__main__":
    main()
