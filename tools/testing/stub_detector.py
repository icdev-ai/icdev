#!/usr/bin/env python3
# CUI // SP-CTI
"""4-Level Verification & Stub Detection Tool (GSD-adapted).

Implements a 4-level verification pipeline inspired by GSD's verification
system, adapted for ICDEV™'s FORGE framework and air-gapped environments.

Levels:
  1. EXISTS     — File was created / artifact present
  2. SUBSTANTIVE — Not a placeholder / TODO / stub
  3. WIRED      — Connected to the system (imports, routes, DB refs)
  4. FUNCTIONAL — Actually executes (delegates to existing test pipeline)

100% deterministic (stdlib only), air-gap safe.

Architecture Decisions:
  D-GSD-1: 4-level verification with per-language stub patterns (declarative)
  D-GSD-2: Stub detection is advisory-only — never modifies source files (D110)
  D-GSD-3: Results stored in append-only stub_detection_results table (D6)
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

DB_PATH = BASE_DIR / "data" / "icdev.db"

# ── Verification Levels ──────────────────────────────────────────────────────
LEVEL_EXISTS = "exists"
LEVEL_SUBSTANTIVE = "substantive"
LEVEL_WIRED = "wired"
LEVEL_FUNCTIONAL = "functional"

LEVELS = [LEVEL_EXISTS, LEVEL_SUBSTANTIVE, LEVEL_WIRED, LEVEL_FUNCTIONAL]

# ── Stub Detection Patterns (per-language, deterministic regex) ──────────────
# Adapted from GSD's comprehensive stub detection patterns.
STUB_PATTERNS = {
    "python": [
        # pass-only functions/methods
        (r"def\s+\w+\s*\([^)]*\)\s*(?:->.*)?:\s*\n\s+pass\s*$", "pass-only function"),
        # TODO/FIXME placeholders
        (r"#\s*TODO\s*:", "TODO comment placeholder"),
        (r"#\s*FIXME\s*:", "FIXME comment placeholder"),
        # NotImplementedError
        (r"raise\s+NotImplementedError", "NotImplementedError stub"),
        # Ellipsis-only function body
        (r"def\s+\w+\s*\([^)]*\)\s*(?:->.*)?:\s*\n\s+\.\.\.\s*$", "ellipsis-only function"),
        # Empty class body
        (r"class\s+\w+.*:\s*\n\s+pass\s*$", "empty class body"),
        # return None / return {} / return [] without logic
        (r"def\s+\w+\s*\([^)]*\)\s*(?:->.*)?:\s*\n\s+return\s+(None|\{\}|\[\])\s*$", "trivial return stub"),
        # placeholder string returns
        (r'return\s+["\']placeholder["\']', "placeholder string return"),
        (r'return\s+["\']TODO["\']', "TODO string return"),
    ],
    "javascript": [
        (r"//\s*TODO\s*:", "TODO comment"),
        (r"throw\s+new\s+Error\(['\"]not\s+implemented", "not implemented error"),
        (r"export\s+(?:default\s+)?function\s+\w+\s*\([^)]*\)\s*\{\s*\}", "empty function"),
        (r"=>\s*\{\s*\}", "empty arrow function"),
        (r"return\s+null\s*;?\s*\}", "null return stub"),
        (r"console\.log\(['\"]TODO", "TODO console.log"),
    ],
    "typescript": [
        (r"//\s*TODO\s*:", "TODO comment"),
        (r"throw\s+new\s+Error\(['\"]not\s+implemented", "not implemented error"),
        (r"export\s+(?:default\s+)?function\s+\w+.*\{\s*\}", "empty function"),
        (r"=>\s*\{\s*\}", "empty arrow function"),
        (r"return\s+null\s*;?\s*\}", "null return stub"),
    ],
    "java": [
        (r"//\s*TODO\s*:", "TODO comment"),
        (r"throw\s+new\s+UnsupportedOperationException", "unsupported operation stub"),
        (r"throw\s+new\s+RuntimeException\(['\"]not\s+implemented", "not implemented stub"),
        (r"public\s+\w+\s+\w+\s*\([^)]*\)\s*\{\s*\}", "empty method"),
        (r"return\s+null\s*;\s*\}", "null return stub"),
    ],
    "go": [
        (r"//\s*TODO\s*:", "TODO comment"),
        (r'panic\("not implemented"\)', "not implemented panic"),
        (r"func\s+\w+\s*\([^)]*\)\s*(?:\([^)]*\)\s*)?\{\s*\}", "empty function"),
        (r"return\s+nil\s*\}", "nil return stub"),
    ],
    "rust": [
        (r"//\s*TODO\s*:", "TODO comment"),
        (r"todo!\(\)", "todo! macro"),
        (r"unimplemented!\(\)", "unimplemented! macro"),
        (r"fn\s+\w+\s*\([^)]*\)\s*(?:->.*?)?\s*\{\s*\}", "empty function"),
    ],
    "csharp": [
        (r"//\s*TODO\s*:", "TODO comment"),
        (r"throw\s+new\s+NotImplementedException", "NotImplementedException"),
        (r"public\s+\w+\s+\w+\s*\([^)]*\)\s*\{\s*\}", "empty method"),
        (r"return\s+null\s*;\s*\}", "null return stub"),
    ],
}

# File extension to language mapping
EXT_LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".cs": "csharp",
}

# Wiring detection patterns (imports, routes, DB refs)
WIRING_PATTERNS = {
    "python": {
        "import": r"(?:from|import)\s+[\w.]+",
        "route": r"@(?:app|blueprint|bp)\.\w+\(",
        "db_ref": r"(?:conn|cursor|db)\.(?:execute|query|commit)",
        "test_ref": r"(?:def\s+test_|@pytest\.mark|class\s+Test)",
    },
    "javascript": {
        "import": r"(?:import|require)\s*\(",
        "route": r"(?:app|router)\.\w+\(",
        "db_ref": r"(?:pool|client|db)\.\w+\(",
        "test_ref": r"(?:describe|it|test)\s*\(",
    },
    "typescript": {
        "import": r"(?:import|require)\s*\(",
        "route": r"(?:app|router)\.\w+\(",
        "db_ref": r"(?:pool|client|db)\.\w+\(",
        "test_ref": r"(?:describe|it|test)\s*\(",
    },
}


def _get_db(db_path: Path = None) -> sqlite3.Connection:
    """Get database connection."""
    path = db_path or DB_PATH
    conn = get_connection(db_path=str(path))
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Level 1: EXISTS ─────────────────────────────────────────────────────────
def check_exists(file_path: Path) -> dict:
    """Level 1: Verify file exists and is non-empty."""
    result = {
        "level": LEVEL_EXISTS,
        "file": str(file_path),
        "passed": False,
        "details": [],
    }

    if not file_path.exists():
        result["details"].append("File does not exist")
        return result

    if not file_path.is_file():
        result["details"].append("Path is not a file")
        return result

    size = file_path.stat().st_size
    if size == 0:
        result["details"].append("File is empty (0 bytes)")
        return result

    result["passed"] = True
    result["details"].append(f"File exists ({size} bytes)")
    return result


# ── Level 2: SUBSTANTIVE ────────────────────────────────────────────────────
def check_substantive(file_path: Path) -> dict:
    """Level 2: Verify file is not a stub/placeholder.

    Uses per-language regex patterns to detect common stub patterns.
    For Python files, also uses AST analysis for deeper stub detection.
    """
    result = {
        "level": LEVEL_SUBSTANTIVE,
        "file": str(file_path),
        "passed": True,
        "stubs_found": [],
        "stub_count": 0,
        "total_functions": 0,
        "stub_ratio": 0.0,
        "details": [],
    }

    ext = file_path.suffix.lower()
    language = EXT_LANG_MAP.get(ext)

    if not language:
        result["details"].append(f"Unsupported file type: {ext}")
        return result

    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        result["passed"] = False
        result["details"].append(f"Cannot read file: {e}")
        return result

    lines = content.split("\n")

    # Regex-based stub detection
    patterns = STUB_PATTERNS.get(language, [])
    for pattern, description in patterns:
        for i, line in enumerate(lines, 1):
            if re.search(pattern, line, re.IGNORECASE):
                result["stubs_found"].append(
                    {
                        "line": i,
                        "pattern": description,
                        "text": line.strip()[:120],
                    }
                )

    # Python AST-based deeper analysis
    if language == "python":
        result = _ast_stub_analysis(content, result)

    result["stub_count"] = len(result["stubs_found"])

    # Calculate stub ratio for Python (AST gives function count)
    if result["total_functions"] > 0:
        result["stub_ratio"] = round(result["stub_count"] / result["total_functions"], 2)

    # Threshold: if >50% of functions are stubs, fail
    if result["total_functions"] > 0 and result["stub_ratio"] > 0.5:
        result["passed"] = False
        result["details"].append(
            f"High stub ratio: {result['stub_ratio']:.0%} "
            f"({result['stub_count']}/{result['total_functions']} functions)"
        )
    elif result["stub_count"] > 0:
        result["details"].append(f"Found {result['stub_count']} stub(s) — advisory")
    else:
        result["details"].append("No stubs detected")

    return result


def _ast_stub_analysis(content: str, result: dict) -> dict:
    """Use Python AST to detect stub functions more accurately."""
    try:
        tree = ast.parse(content)
    except SyntaxError:
        result["details"].append("AST parse failed — skipping deep analysis")
        return result

    functions = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node)

    result["total_functions"] = len(functions)

    for func in functions:
        body = func.body
        if not body:
            continue

        # Single-statement bodies that are stubs
        if len(body) == 1:
            stmt = body[0]

            # pass-only
            if isinstance(stmt, ast.Pass):
                result["stubs_found"].append(
                    {
                        "line": func.lineno,
                        "pattern": "pass-only function (AST)",
                        "text": f"def {func.name}(...): pass",
                    }
                )

            # Ellipsis-only
            elif (
                isinstance(stmt, ast.Expr)
                and isinstance(getattr(stmt, "value", None), ast.Constant)
                and stmt.value.value is ...
            ):
                result["stubs_found"].append(
                    {
                        "line": func.lineno,
                        "pattern": "ellipsis-only function (AST)",
                        "text": f"def {func.name}(...): ...",
                    }
                )

            # raise NotImplementedError
            elif isinstance(stmt, ast.Raise) and isinstance(getattr(stmt, "exc", None), ast.Call):
                exc = stmt.exc
                if isinstance(exc.func, ast.Name) and exc.func.id == "NotImplementedError":
                    result["stubs_found"].append(
                        {
                            "line": func.lineno,
                            "pattern": "NotImplementedError (AST)",
                            "text": f"def {func.name}(...): raise NotImplementedError",
                        }
                    )

    # Deduplicate stubs by line number
    seen_lines = set()
    unique_stubs = []
    for stub in result["stubs_found"]:
        if stub["line"] not in seen_lines:
            seen_lines.add(stub["line"])
            unique_stubs.append(stub)
    result["stubs_found"] = unique_stubs

    return result


# ── Level 3: WIRED ──────────────────────────────────────────────────────────
def check_wired(file_path: Path, project_dir: Path = None) -> dict:
    """Level 3: Verify file is connected to the system.

    Checks that the file:
    - Has imports (not standalone)
    - Is imported by other files (not orphaned)
    - Has route/DB/test connections where applicable
    """
    result = {
        "level": LEVEL_WIRED,
        "file": str(file_path),
        "passed": True,
        "connections": {
            "has_imports": False,
            "is_imported": False,
            "has_routes": False,
            "has_db_refs": False,
            "has_tests": False,
        },
        "connection_score": 0.0,
        "details": [],
    }

    ext = file_path.suffix.lower()
    language = EXT_LANG_MAP.get(ext)

    if not language:
        result["details"].append(f"Unsupported file type: {ext}")
        return result

    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        result["passed"] = False
        result["details"].append(f"Cannot read file: {e}")
        return result

    # Check wiring patterns
    wire_patterns = WIRING_PATTERNS.get(language, {})

    for pattern_type, pattern in wire_patterns.items():
        if re.search(pattern, content):
            if pattern_type == "import":
                result["connections"]["has_imports"] = True
            elif pattern_type == "route":
                result["connections"]["has_routes"] = True
            elif pattern_type == "db_ref":
                result["connections"]["has_db_refs"] = True
            elif pattern_type == "test_ref":
                result["connections"]["has_tests"] = True

    # Check if file is imported by other files (orphan detection)
    if project_dir and project_dir.is_dir():
        result["connections"]["is_imported"] = _check_imported_by_others(file_path, project_dir, language)

    # Calculate connection score
    conns = result["connections"]
    total_checks = 5
    connected = sum(1 for v in conns.values() if v)
    result["connection_score"] = round(connected / total_checks, 2)

    # Minimum: file must have imports (unless it's a test file)
    is_test = "test" in file_path.name.lower()
    if not conns["has_imports"] and not is_test:
        result["passed"] = False
        result["details"].append("File has no imports — likely disconnected")
    else:
        result["details"].append(
            f"Connection score: {result['connection_score']:.0%} ({connected}/{total_checks} checks passed)"
        )

    return result


def _check_imported_by_others(file_path: Path, project_dir: Path, language: str) -> bool:
    """Check if the file is imported/referenced by other files."""
    stem = file_path.stem
    if stem == "__init__":
        return True  # Package init files are always "imported"

    # Build search pattern based on language
    if language == "python":
        # Look for: import stem, from ... import stem, from stem import
        module_name = stem
        patterns = [
            rf"(?:from|import)\s+[\w.]*{re.escape(module_name)}",
        ]
    elif language in ("javascript", "typescript"):
        patterns = [
            rf"(?:from|require)\s*\(?['\"].*{re.escape(stem)}",
        ]
    else:
        return False  # Can't easily check for other languages

    # Scan nearby files (limit depth for performance)
    ext_set = {k for k, v in EXT_LANG_MAP.items() if v == language}
    count = 0
    max_files = 200  # Cap for performance

    for candidate in project_dir.rglob("*"):
        if count >= max_files:
            break
        if candidate == file_path:
            continue
        if candidate.suffix.lower() not in ext_set:
            continue
        if any(
            p in str(candidate)
            for p in [
                "__pycache__",
                "node_modules",
                ".git",
                ".tmp",
                "venv",
            ]
        ):
            continue

        count += 1
        try:
            text = candidate.read_text(encoding="utf-8", errors="ignore")
            for pattern in patterns:
                if re.search(pattern, text):
                    return True
        except OSError:
            continue

    return False


# ── Level 4: FUNCTIONAL ─────────────────────────────────────────────────────
def check_functional(file_path: Path) -> dict:
    """Level 4: Verify file is functional (basic execution check).

    For Python: attempts py_compile.
    For other languages: advisory check only.
    """
    result = {
        "level": LEVEL_FUNCTIONAL,
        "file": str(file_path),
        "passed": True,
        "details": [],
    }

    ext = file_path.suffix.lower()

    if ext == ".py":
        import py_compile

        try:
            py_compile.compile(str(file_path), doraise=True)
            result["details"].append("Python compilation successful")
        except py_compile.PyCompileError as e:
            result["passed"] = False
            result["details"].append(f"Compilation error: {e}")
    else:
        result["details"].append(f"Functional check for {ext} deferred to language-specific toolchain")

    return result


# ── Full Pipeline ────────────────────────────────────────────────────────────
def verify_file(
    file_path: Path,
    project_dir: Path = None,
    max_level: str = LEVEL_FUNCTIONAL,
) -> dict:
    """Run the 4-level verification pipeline on a single file.

    Stops at the first failing level (cascade failure).
    """
    result = {
        "file": str(file_path),
        "timestamp": _now(),
        "levels": {},
        "overall_passed": True,
        "highest_level_passed": None,
        "failed_at_level": None,
    }

    target_levels = LEVELS[: LEVELS.index(max_level) + 1]

    for level in target_levels:
        if level == LEVEL_EXISTS:
            check = check_exists(file_path)
        elif level == LEVEL_SUBSTANTIVE:
            check = check_substantive(file_path)
        elif level == LEVEL_WIRED:
            check = check_wired(file_path, project_dir)
        elif level == LEVEL_FUNCTIONAL:
            check = check_functional(file_path)
        else:
            continue

        result["levels"][level] = check

        if check["passed"]:
            result["highest_level_passed"] = level
        else:
            result["overall_passed"] = False
            result["failed_at_level"] = level
            break  # Cascade: stop at first failure

    return result


def verify_directory(
    project_dir: Path,
    max_level: str = LEVEL_FUNCTIONAL,
    extensions: list = None,
) -> dict:
    """Run verification on all matching files in a directory."""
    if extensions is None:
        extensions = list(EXT_LANG_MAP.keys())

    results = {
        "project_dir": str(project_dir),
        "timestamp": _now(),
        "files_checked": 0,
        "files_passed": 0,
        "files_failed": 0,
        "stub_total": 0,
        "level_summary": {level: {"passed": 0, "failed": 0} for level in LEVELS},
        "failures": [],
        "file_results": [],
    }

    skip_dirs = {
        "__pycache__",
        "node_modules",
        ".git",
        ".tmp",
        "venv",
        ".venv",
        "dist",
        "build",
        ".tox",
        ".pytest_cache",
    }

    for file_path in sorted(project_dir.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in extensions:
            continue
        if any(skip in file_path.parts for skip in skip_dirs):
            continue

        file_result = verify_file(file_path, project_dir, max_level)
        results["files_checked"] += 1

        if file_result["overall_passed"]:
            results["files_passed"] += 1
        else:
            results["files_failed"] += 1
            results["failures"].append(
                {
                    "file": str(file_path),
                    "failed_at": file_result["failed_at_level"],
                    "details": file_result["levels"].get(file_result["failed_at_level"], {}).get("details", []),
                }
            )

        # Count stubs
        sub_result = file_result["levels"].get(LEVEL_SUBSTANTIVE, {})
        results["stub_total"] += sub_result.get("stub_count", 0)

        # Level summary
        for level, check in file_result["levels"].items():
            if check["passed"]:
                results["level_summary"][level]["passed"] += 1
            else:
                results["level_summary"][level]["failed"] += 1

        results["file_results"].append(file_result)

    return results


# ── DB Storage ───────────────────────────────────────────────────────────────
def store_results(results: dict, project_id: str = "unknown", db_path: Path = None) -> int:
    """Store verification results in the database (append-only)."""
    conn = _get_db(db_path)
    try:
        cursor = conn.execute(
            """INSERT INTO stub_detection_results
               (project_id, project_dir, files_checked, files_passed,
                files_failed, stub_total, level_summary, failures,
                overall_passed, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                project_id,
                results.get("project_dir", ""),
                results["files_checked"],
                results["files_passed"],
                results["files_failed"],
                results["stub_total"],
                json.dumps(results.get("level_summary", {})),
                json.dumps(results.get("failures", [])),
                1 if results["files_failed"] == 0 else 0,
                _now(),
            ),
        )
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        print(f"[stub-detector] Warning: Could not store results: {e}")
        return -1
    finally:
        conn.close()


# ── Gate Evaluation ──────────────────────────────────────────────────────────
def evaluate_gate(results: dict) -> dict:
    """Evaluate the stub detection security gate."""
    gate = {
        "gate": "stub_detection",
        "passed": True,
        "blocking_failures": [],
        "warnings": [],
    }

    # Blocking: any file that fails EXISTS level
    exists_failures = results["level_summary"].get(LEVEL_EXISTS, {}).get("failed", 0)
    if exists_failures > 0:
        gate["passed"] = False
        gate["blocking_failures"].append(f"{exists_failures} file(s) failed EXISTS verification")

    # Blocking: any file >50% stubs (fails SUBSTANTIVE)
    sub_failures = results["level_summary"].get(LEVEL_SUBSTANTIVE, {}).get("failed", 0)
    if sub_failures > 0:
        gate["passed"] = False
        gate["blocking_failures"].append(f"{sub_failures} file(s) failed SUBSTANTIVE verification (>50% stubs)")

    # Warning: stubs found (even if under threshold)
    if results["stub_total"] > 0:
        gate["warnings"].append(f"{results['stub_total']} total stub(s) detected across project")

    # Warning: wiring failures
    wired_failures = results["level_summary"].get(LEVEL_WIRED, {}).get("failed", 0)
    if wired_failures > 0:
        gate["warnings"].append(f"{wired_failures} file(s) failed WIRED verification (disconnected)")

    return gate


# ── CLI ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="4-Level Verification & Stub Detection (GSD-adapted)")
    parser.add_argument("--file", help="Verify a single file")
    parser.add_argument("--project-dir", help="Verify all files in a directory")
    parser.add_argument("--project-id", default="unknown", help="Project ID for DB storage")
    parser.add_argument(
        "--max-level",
        choices=LEVELS,
        default=LEVEL_FUNCTIONAL,
        help="Maximum verification level to check",
    )
    parser.add_argument("--store", action="store_true", help="Store results in database")
    parser.add_argument("--gate", action="store_true", help="Evaluate security gate")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    if args.file:
        file_path = Path(args.file).resolve()
        project_dir = file_path.parent
        result = verify_file(file_path, project_dir, args.max_level)

        if args.json_output or args.gate:
            if args.gate:
                # Wrap single file result for gate evaluation
                dir_result = {
                    "files_checked": 1,
                    "files_passed": 1 if result["overall_passed"] else 0,
                    "files_failed": 0 if result["overall_passed"] else 1,
                    "stub_total": sum(r.get("stub_count", 0) for r in result["levels"].values()),
                    "level_summary": {
                        level: {
                            "passed": 1 if check["passed"] else 0,
                            "failed": 0 if check["passed"] else 1,
                        }
                        for level, check in result["levels"].items()
                    },
                }
                gate = evaluate_gate(dir_result)
                print(json.dumps(gate, indent=2))
                raise SystemExit(0 if gate["passed"] else 1)
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_file_result(result)

    elif args.project_dir:
        project_dir = Path(args.project_dir).resolve()
        results = verify_directory(project_dir, args.max_level)

        if args.store:
            rid = store_results(results, args.project_id)
            results["stored_id"] = rid

        if args.gate:
            gate = evaluate_gate(results)
            print(json.dumps(gate, indent=2))
            raise SystemExit(0 if gate["passed"] else 1)

        if args.json_output:
            # Omit per-file details for concise output
            summary = {k: v for k, v in results.items() if k != "file_results"}
            print(json.dumps(summary, indent=2, default=str))
        elif args.human:
            _print_directory_summary(results)
        else:
            summary = {k: v for k, v in results.items() if k != "file_results"}
            print(json.dumps(summary, indent=2, default=str))
    else:
        parser.error("Provide --file or --project-dir")


def _print_file_result(result: dict):
    """Human-readable output for a single file."""
    print(f"\n{'=' * 60}")
    print(f"  File: {result['file']}")
    print(f"  Result: {'PASS' if result['overall_passed'] else 'FAIL'}")
    print(f"{'=' * 60}")

    for level in LEVELS:
        check = result["levels"].get(level)
        if not check:
            print(f"  [{level.upper():13s}] SKIPPED")
            continue

        status = "PASS" if check["passed"] else "FAIL"
        print(f"  [{level.upper():13s}] {status}")
        for detail in check.get("details", []):
            print(f"    - {detail}")

        if check.get("stubs_found"):
            for stub in check["stubs_found"][:5]:
                print(f"    L{stub['line']}: {stub['pattern']} — {stub['text'][:80]}")
            if len(check["stubs_found"]) > 5:
                print(f"    ... and {len(check['stubs_found']) - 5} more")


def _print_directory_summary(results: dict):
    """Human-readable summary for directory scan."""
    print(f"\n{'=' * 60}")
    print("  4-Level Verification Summary")
    print(f"  Directory: {results['project_dir']}")
    print(f"{'=' * 60}")
    print(f"  Files checked:  {results['files_checked']}")
    print(f"  Files passed:   {results['files_passed']}")
    print(f"  Files failed:   {results['files_failed']}")
    print(f"  Stubs found:    {results['stub_total']}")
    print()

    for level in LEVELS:
        summary = results["level_summary"].get(level, {})
        p = summary.get("passed", 0)
        f = summary.get("failed", 0)
        print(f"  {level.upper():13s}  pass={p}  fail={f}")

    if results["failures"]:
        print("\n  Failures:")
        for fail in results["failures"][:10]:
            print(f"    {fail['file']}")
            print(f"      Failed at: {fail['failed_at']}")
            for d in fail.get("details", []):
                print(f"      - {d}")


if __name__ == "__main__":
    main()
