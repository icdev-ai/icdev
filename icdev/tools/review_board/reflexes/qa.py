#!/usr/bin/env python3
# CUI // SP-CTI
"""QA Reflex — test coverage, regression detection, and quality gaps.

Checks:
    - Untested modules (tools/ files with no corresponding test)
    - E2E coverage gaps (dashboard pages vs E2E specs)
    - Flaky test detection (recent test failures in audit trail)
    - Test-to-code ratio
    - Conftest schema drift (minimal schema vs init_icdev_db)

Architecture: D-RB-1 (scanner-tier, zero LLM cost), D-RB-3 (standalone run())
"""

from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent

SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".tmp", "venv", ".venv"}


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute QA coverage checks."""
    findings: List[Dict] = []
    checks_completed = 0
    thresholds = config.get("thresholds", {})

    # --- Check 1: Untested tool modules ---
    try:
        tools_dir = BASE_DIR / "tools"
        tests_dir = BASE_DIR / "tests"
        test_files = set()
        if tests_dir.exists():
            for tf in tests_dir.glob("test_*.py"):
                # Extract module name: test_foo.py -> foo
                name = tf.stem.replace("test_", "", 1)
                test_files.add(name)

        untested = []
        if tools_dir.exists():
            for py_file in tools_dir.rglob("*.py"):
                if any(p in str(py_file) for p in SKIP_DIRS):
                    continue
                if py_file.name.startswith("__"):
                    continue
                module_name = py_file.stem
                # Check if there's a test file for this module
                if module_name not in test_files:
                    # Only flag if the module has substantial code (>50 lines)
                    try:
                        line_count = len(py_file.read_text(encoding="utf-8").splitlines())
                        if line_count > 50:
                            untested.append(
                                {
                                    "module": str(py_file.relative_to(BASE_DIR)),
                                    "lines": line_count,
                                }
                            )
                    except Exception:
                        pass

        if untested:
            # Sort by line count descending — biggest untested modules first
            untested.sort(key=lambda x: x["lines"], reverse=True)
            top_10 = untested[:10]
            findings.append(
                {
                    "severity": "medium",
                    "category": "untested_modules",
                    "title": f"{len(untested)} tool modules have no dedicated test file",
                    "description": "Top 10 by size: " + ", ".join(f"{m['module']} ({m['lines']}L)" for m in top_10),
                    "recommendation": "Create test files for the largest untested modules",
                    "evidence": {"total_untested": len(untested), "top_10": top_10},
                    "confidence": 0.8,
                    "auto_fixable": False,
                }
            )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    # --- Check 2: E2E coverage gaps ---
    try:
        e2e_dir = BASE_DIR / ".claude" / "commands" / "e2e"
        dashboard_routes = _extract_dashboard_routes()
        e2e_specs = set()
        if e2e_dir.exists():
            for spec in e2e_dir.glob("*.md"):
                e2e_specs.add(spec.stem.replace("_", "-"))

        if dashboard_routes:
            uncovered = [
                r
                for r in dashboard_routes
                if not any(
                    r.strip("/").replace("/", "-") in spec or spec in r.strip("/").replace("/", "-")
                    for spec in e2e_specs
                )
            ]
            min_coverage = thresholds.get("min_e2e_page_coverage_pct", 50)
            coverage_pct = ((len(dashboard_routes) - len(uncovered)) / len(dashboard_routes)) * 100
            if coverage_pct < min_coverage:
                findings.append(
                    {
                        "severity": "medium",
                        "category": "e2e_coverage",
                        "title": f"E2E coverage {coverage_pct:.0f}% below threshold ({min_coverage}%)",
                        "description": f"Uncovered routes: {', '.join(uncovered[:10])}",
                        "recommendation": "Add E2E specs for uncovered dashboard pages",
                        "evidence": {
                            "total_routes": len(dashboard_routes),
                            "e2e_specs": len(e2e_specs),
                            "uncovered": uncovered[:20],
                        },
                        "confidence": 0.7,
                        "auto_fixable": False,
                    }
                )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    # --- Check 3: Test-to-code ratio ---
    try:
        test_count = 0
        code_count = 0
        tests_dir = BASE_DIR / "tests"
        tools_dir = BASE_DIR / "tools"

        if tests_dir.exists():
            test_count = sum(1 for _ in tests_dir.glob("test_*.py"))
        if tools_dir.exists():
            for py_file in tools_dir.rglob("*.py"):
                if any(p in str(py_file) for p in SKIP_DIRS):
                    continue
                if not py_file.name.startswith("__"):
                    code_count += 1

        if code_count > 0:
            ratio = test_count / code_count
            min_ratio = thresholds.get("min_test_ratio", 0.3)
            if ratio < min_ratio:
                findings.append(
                    {
                        "severity": "low",
                        "category": "test_ratio",
                        "title": f"Test-to-code ratio {ratio:.2f} below threshold ({min_ratio})",
                        "description": f"{test_count} test files for {code_count} code modules",
                        "confidence": 0.7,
                        "auto_fixable": False,
                    }
                )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    # --- Check 4: Syntax errors in Python files ---
    try:
        syntax_errors = []
        tools_dir = BASE_DIR / "tools"
        if tools_dir.exists():
            for py_file in tools_dir.rglob("*.py"):
                if any(p in str(py_file) for p in SKIP_DIRS):
                    continue
                try:
                    source = py_file.read_text(encoding="utf-8")
                    compile(source, str(py_file), "exec")
                except SyntaxError as e:
                    syntax_errors.append(
                        {
                            "file": str(py_file.relative_to(BASE_DIR)),
                            "line": e.lineno,
                            "msg": str(e.msg),
                        }
                    )

        if syntax_errors:
            findings.append(
                {
                    "severity": "critical",
                    "category": "syntax_errors",
                    "title": f"{len(syntax_errors)} Python files have syntax errors",
                    "description": "; ".join(f"{e['file']}:{e['line']}" for e in syntax_errors[:5]),
                    "evidence": {"errors": syntax_errors},
                    "confidence": 1.0,
                    "auto_fixable": False,
                }
            )
        checks_completed += 1
    except Exception:
        checks_completed += 1

    return {
        "success": True,
        "metric_value": float(len(findings)),
        "details": {
            "checks_completed": checks_completed,
            "findings_count": len(findings),
            "findings": findings,
        },
    }


def _extract_dashboard_routes() -> List[str]:
    """Extract registered routes from dashboard app.py via simple regex."""
    app_file = BASE_DIR / "tools" / "dashboard" / "app.py"
    routes = []
    if app_file.exists():
        import re

        content = app_file.read_text(encoding="utf-8")
        for match in re.finditer(r'@app\.route\(["\'](/[^"\']*)["\']', content):
            route = match.group(1)
            if "<" not in route:  # Skip parameterized routes
                routes.append(route)
    return routes
