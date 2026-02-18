#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""MOSA Code Enforcer -- static analysis for Modular Open Systems Approach violations.

Scans code for MOSA violations: direct coupling bypassing interfaces, missing
OpenAPI specs, module boundary violations, missing interface abstractions, and
circular imports.  Output format matches the SAST runner pattern.

Violation types:
    MOSA-V001  Direct Coupling Violation     (HIGH)
    MOSA-V002  Missing OpenAPI Spec          (MEDIUM)
    MOSA-V003  Module Boundary Violation     (HIGH)
    MOSA-V004  Missing Interface Abstraction (MEDIUM)
    MOSA-V005  Circular Import               (HIGH)

Usage:
    python tools/mosa/mosa_code_enforcer.py --project-dir /path/to/project --json
    python tools/mosa/mosa_code_enforcer.py --project-dir /path --fix-suggestions --human
"""

import argparse
import ast
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = BASE_DIR / "args" / "mosa_config.yaml"


def _load_config() -> Dict[str, Any]:
    """Load code_enforcement settings from args/mosa_config.yaml."""
    defaults: Dict[str, Any] = {
        "enforce_interface_based_design": True, "generate_openapi_specs": True,
        "enforce_module_boundaries": True, "max_direct_coupling_violations": 0,
        "check_circular_dependencies": True, "supported_languages": ["python"],
    }
    if not CONFIG_PATH.exists():
        return defaults
    try:
        content = CONFIG_PATH.read_text(encoding="utf-8")
        in_section = False
        cfg: Dict[str, Any] = {}
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            if stripped == "code_enforcement:":
                in_section = True
                continue
            if in_section:
                indent = len(line) - len(line.lstrip())
                if indent <= 2 and ":" in stripped and not stripped.startswith("-"):
                    in_section = False
                    continue
                if ":" in stripped and not stripped.startswith("-"):
                    key, val = stripped.split(":", 1)
                    key, val = key.strip(), val.strip()
                    if val.lower() in ("true", "false"):
                        cfg[key] = val.lower() == "true"
                    elif val.isdigit():
                        cfg[key] = int(val)
                    elif val:
                        cfg[key] = val
                elif stripped.startswith("- "):
                    cfg.setdefault("supported_languages", []).append(
                        stripped[2:].strip().strip('"').strip("'"))
        return {**defaults, **cfg}
    except Exception:
        return defaults


def _build_module_map(project_dir: Path) -> Dict[str, Path]:
    """Return mapping of top-level Python package names to their directories."""
    modules: Dict[str, Path] = {}
    for child in sorted(project_dir.iterdir()):
        if not child.is_dir() or child.name.startswith((".", "_", "venv", "node_modules")):
            continue
        if (child / "__init__.py").exists():
            modules[child.name] = child
    return modules


def _python_files(directory: Path) -> List[Path]:
    """Collect all .py files under *directory*, skipping hidden/venv dirs."""
    results: List[Path] = []
    skip = {".git", "__pycache__", "venv", ".venv", "node_modules", "build", "dist"}
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in skip]
        for fname in files:
            if fname.endswith(".py"):
                results.append(Path(root) / fname)
    return results


def _extract_imports(filepath: Path) -> List[Dict[str, Any]]:
    """Parse a Python file with ast and return structured import details."""
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(filepath))
    except (SyntaxError, UnicodeDecodeError):
        return []
    imports: List[Dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({"module": alias.name, "line": node.lineno,
                                "names": [alias.name.split(".")[-1]]})
        elif isinstance(node, ast.ImportFrom):
            imports.append({"module": node.module or "", "line": node.lineno,
                            "names": [a.name for a in node.names]})
    return imports


# -- Violation detectors ----------------------------------------------------

def _check_direct_coupling(fp: Path, imports: List[Dict], modules: Dict[str, Path],
                           own: str, fix: bool) -> List[Dict]:
    """MOSA-V001: direct import from another module's internal package."""
    violations: List[Dict] = []
    for imp in imports:
        parts = imp["module"].split(".")
        if len(parts) < 2:
            continue
        top = parts[0]
        if top == own or top not in modules:
            continue
        if any(p.startswith("_") and p != "__init__" for p in parts[1:]):
            v: Dict[str, Any] = {
                "id": "MOSA-V001", "type": "direct_coupling_violation",
                "severity": "HIGH", "file": str(fp), "line": imp["line"],
                "message": f"Direct import from internal module '{imp['module']}'",
            }
            if fix:
                v["suggestion"] = f"Import from '{top}' public API instead of '{imp['module']}'"
            violations.append(v)
    return violations


def _check_missing_openapi(fp: Path, fix: bool) -> Optional[Dict]:
    """MOSA-V002: API module without a corresponding openapi.yaml/json."""
    try:
        source = fp.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    api_patterns = [
        r"@app\.route\(", r"@blueprint\.route\(",
        r"@router\.(get|post|put|delete|patch)\(",
        r"app\.add_url_rule\(", r"APIRouter\(",
    ]
    if not any(re.search(p, source) for p in api_patterns):
        return None
    spec_names = ["openapi.yaml", "openapi.yml", "openapi.json",
                  "swagger.yaml", "swagger.json"]
    for d in (fp.parent, fp.parent.parent):
        if any((d / s).exists() for s in spec_names):
            return None
    v: Dict[str, Any] = {
        "id": "MOSA-V002", "type": "missing_openapi_spec",
        "severity": "MEDIUM", "file": str(fp), "line": 1,
        "message": f"API module '{fp.name}' has no corresponding OpenAPI spec",
    }
    if fix:
        v["suggestion"] = f"Create '{fp.parent.name}/openapi.yaml' with OpenAPI 3.x specification"
    return v


def _check_boundary_violation(fp: Path, imports: List[Dict], modules: Dict[str, Path],
                              own: str, fix: bool) -> List[Dict]:
    """MOSA-V003: importing _private-prefixed names across module boundaries."""
    violations: List[Dict] = []
    for imp in imports:
        top = imp["module"].split(".")[0] if imp["module"] else ""
        if top == own or top not in modules:
            continue
        for name in imp["names"]:
            if name.startswith("_") and name != "__init__":
                v: Dict[str, Any] = {
                    "id": "MOSA-V003", "type": "module_boundary_violation",
                    "severity": "HIGH", "file": str(fp), "line": imp["line"],
                    "message": f"Cross-module import of private name '{name}' from '{top}'",
                }
                if fix:
                    v["suggestion"] = f"Use the module's public interface instead of accessing '{name}'"
                violations.append(v)
    return violations


def _check_missing_interface(fp: Path, imports: List[Dict], modules: Dict[str, Path],
                             own: str, fix: bool) -> List[Dict]:
    """MOSA-V004: cross-module dependency without abstract interface (ABC/Protocol)."""
    violations: List[Dict] = []
    for imp in imports:
        top = imp["module"].split(".")[0] if imp["module"] else ""
        if top == own or top not in modules:
            continue
        target = modules[top]
        has_iface = (target / "interfaces.py").exists() or (target / "protocols.py").exists()
        for name in imp["names"]:
            if name.startswith("_"):
                continue
            if not has_iface and not name.startswith("Abstract"):
                v: Dict[str, Any] = {
                    "id": "MOSA-V004", "type": "missing_interface_abstraction",
                    "severity": "MEDIUM", "file": str(fp), "line": imp["line"],
                    "message": f"Cross-module import of '{name}' from '{top}' without interface abstraction",
                }
                if fix:
                    v["suggestion"] = f"Create an abstract interface (ABC/Protocol) in '{top}/interfaces.py'"
                violations.append(v)
                break  # one per import statement
    return violations


def _check_circular_imports(modules: Dict[str, Path], project_dir: Path,
                            fix: bool) -> List[Dict]:
    """MOSA-V005: circular import chains via graphlib.TopologicalSorter (D40)."""
    try:
        from graphlib import TopologicalSorter, CycleError
    except ImportError:
        return []
    graph: Dict[str, Set[str]] = {m: set() for m in modules}
    for mod_name, mod_path in modules.items():
        for pyfile in _python_files(mod_path):
            for imp in _extract_imports(pyfile):
                top = imp["module"].split(".")[0] if imp["module"] else ""
                if top in modules and top != mod_name:
                    graph[mod_name].add(top)
    sorter = TopologicalSorter(graph)
    try:
        list(sorter.static_order())
        return []
    except CycleError as exc:
        v: Dict[str, Any] = {
            "id": "MOSA-V005", "type": "circular_import",
            "severity": "HIGH", "file": str(project_dir), "line": 0,
            "message": f"Circular import detected: {exc}",
        }
        if fix:
            v["suggestion"] = "Break circular dependency by extracting shared types into a common module"
        return [v]


# -- Scan orchestrator ------------------------------------------------------

def scan_project(project_dir: str, fix_suggestions: bool = False) -> Dict[str, Any]:
    """Run all MOSA code enforcement checks on *project_dir*."""
    root = Path(project_dir).resolve()
    config = _load_config()
    modules = _build_module_map(root)
    violations: List[Dict] = []

    for pyfile in _python_files(root):
        try:
            rel = pyfile.relative_to(root)
        except ValueError:
            continue
        own = rel.parts[0] if rel.parts else ""
        imports = _extract_imports(pyfile)
        fix = fix_suggestions
        if config.get("enforce_module_boundaries", True):
            violations.extend(_check_direct_coupling(pyfile, imports, modules, own, fix))
            violations.extend(_check_boundary_violation(pyfile, imports, modules, own, fix))
        if config.get("generate_openapi_specs", True):
            v = _check_missing_openapi(pyfile, fix)
            if v:
                violations.append(v)
        if config.get("enforce_interface_based_design", True):
            violations.extend(_check_missing_interface(pyfile, imports, modules, own, fix))

    if config.get("check_circular_dependencies", True):
        violations.extend(_check_circular_imports(modules, root, fix_suggestions))

    sev_counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for v in violations:
        sev_counts[v.get("severity", "LOW")] = sev_counts.get(v.get("severity", "LOW"), 0) + 1

    max_high = config.get("max_direct_coupling_violations", 0)
    return {
        "tool": "mosa_code_enforcer",
        "project_dir": str(root),
        "scan_date": datetime.now(timezone.utc).isoformat(),
        "total_violations": len(violations),
        "violations_by_severity": sev_counts,
        "violations": violations,
        "pass": sev_counts["HIGH"] <= max_high,
    }


# -- Human-readable output -------------------------------------------------

def _human_output(result: Dict[str, Any]) -> str:
    """Render scan results as colored terminal output."""
    try:
        from tools.cli.output_formatter import C
    except Exception:
        class _C:
            @staticmethod
            def wrap(t: str, *s: str) -> str: return t
        C = _C  # type: ignore[assignment]

    lines: List[str] = []
    status = "PASS" if result["pass"] else "FAIL"
    lines.append(C.wrap(f"MOSA Code Enforcer  [{status}]", "green" if result["pass"] else "red", "bold"))
    lines.append(f"  Project: {result['project_dir']}")
    lines.append(f"  Scanned: {result['scan_date']}")
    lines.append(f"  Total violations: {result['total_violations']}")
    sev = result["violations_by_severity"]
    lines.append(f"  HIGH: {C.wrap(str(sev.get('HIGH', 0)), 'red')}  "
                 f"MEDIUM: {C.wrap(str(sev.get('MEDIUM', 0)), 'yellow')}  "
                 f"LOW: {C.wrap(str(sev.get('LOW', 0)), 'green')}")
    lines.append("")
    for v in result["violations"]:
        clr = {"HIGH": "red", "MEDIUM": "yellow", "LOW": "green"}.get(v["severity"], "dim")
        lines.append(f"  {C.wrap('[' + v['severity'] + ']', clr)} {v['id']}  {v['file']}:{v['line']}")
        lines.append(f"    {v['message']}")
        if "suggestion" in v:
            lines.append(f"    {C.wrap('Fix:', 'cyan')} {v['suggestion']}")
    return "\n".join(lines)


# -- CLI entry point --------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MOSA Code Enforcer -- scan for Modular Open Systems Approach violations")
    parser.add_argument("--project-dir", required=True, help="Path to the project to scan")
    parser.add_argument("--fix-suggestions", action="store_true", help="Include fix suggestions")
    parser.add_argument("--json", dest="json_output", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Colored terminal output")
    args = parser.parse_args()

    result = scan_project(args.project_dir, fix_suggestions=args.fix_suggestions)
    if args.json_output:
        print(json.dumps(result, indent=2))
    else:
        print(_human_output(result))


if __name__ == "__main__":
    main()
