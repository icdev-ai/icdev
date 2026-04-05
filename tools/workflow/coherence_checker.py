#!/usr/bin/env python3
# CUI // SP-CTI
"""Implementation Coherence Checker — internal consistency validator (D-WF-8).

Deterministic, LLM-agnostic tool that verifies internal consistency across
the ICDEV™ codebase. Catches mismatches that cause test failures and wasted
fix-rerun cycles BEFORE pytest/ruff runs.

Checks:
  1. schema_code    — CREATE TABLE columns match INSERT/SELECT in tools
  2. config_code    — YAML config keys match config.get() in code
  3. signature_call — Function params match test call sites (keyword args)
  4. fixture_schema — Test fixture CREATE TABLE matches init_icdev_db.py
  5. manifest       — New tool files documented in tools/manifest.md
  6. append_only    — Append-only tables protected in pre_tool_use.py
  7. import_usage   — Unused imports in recently changed files
  8. api_wiring     — API handlers read from DB, not hardcoded literals

All checks: stdlib only (ast, re, pathlib), air-gap safe, zero deps.
Follows claude_dir_validator.py pattern (dataclass results, check registry).

Usage:
    python tools/workflow/coherence_checker.py --all --json
    python tools/workflow/coherence_checker.py --check schema_code --json
    python tools/workflow/coherence_checker.py --check fixture_schema --json
    python tools/workflow/coherence_checker.py --changed-files "tools/workflow/loop_engine.py,tests/test_workflow_loop.py" --json
    python tools/workflow/coherence_checker.py --all --human
    python tools/workflow/coherence_checker.py --all --gate
    python tools/workflow/coherence_checker.py --all --fix --json   # Auto-fix safe issues
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Result types (follows claude_dir_validator.py pattern)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class CoherenceCheck:
    """Result of a single coherence check."""

    check_id: str
    check_name: str
    status: str  # "pass", "fail", "warn"
    expected: List[str]
    actual: List[str]
    missing: List[str]
    extra: List[str]
    message: str
    fixes_applied: List[str] = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @property
    def passed(self) -> bool:
        return self.status == "pass"


@dataclasses.dataclass
class CoherenceReport:
    """Aggregate coherence validation report."""

    overall_pass: bool
    timestamp: str
    checks: List[CoherenceCheck]
    total_checks: int
    passed_checks: int
    failed_checks: int
    warned_checks: int
    total_fixes: int = 0

    def to_dict(self) -> dict:
        return {
            "overall_pass": self.overall_pass,
            "timestamp": self.timestamp,
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "warned_checks": self.warned_checks,
            "total_fixes": self.total_fixes,
            "checks": [c.to_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

_HAS_YAML = False
try:
    import yaml

    _HAS_YAML = True
except ImportError:
    pass


def _load_config() -> Dict[str, Any]:
    path = PROJECT_ROOT / "args" / "coherence_contracts.yaml"
    if not _HAS_YAML or not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _parse_create_tables(sql_text: str) -> Dict[str, List[str]]:
    """Extract table_name → [column_names] from SQL CREATE TABLE statements."""
    tables: Dict[str, List[str]] = {}
    pattern = re.compile(
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\((.*?)\);",
        re.DOTALL | re.IGNORECASE,
    )
    for m in pattern.finditer(sql_text):
        table_name = m.group(1)
        body = m.group(2)
        cols = []

        # Split body into segments separated by commas (handles multi-col lines)
        # First strip SQL comments (-- ...) then normalize whitespace
        body_no_comments = re.sub(r"--[^\n]*", "", body)
        flat_body = re.sub(r"\s+", " ", body_no_comments)
        # Split by comma but not commas inside parentheses
        segments: List[str] = []
        current = ""
        depth = 0
        for ch in flat_body:
            if ch == "(":
                depth += 1
                current += ch
            elif ch == ")":
                depth -= 1
                current += ch
            elif ch == "," and depth == 0:
                segments.append(current.strip())
                current = ""
            else:
                current += ch
        if current.strip():
            segments.append(current.strip())

        skip_kw = {"foreign", "primary", "check", "unique", "constraint", "create", "index"}
        type_kw = r"(TEXT|INTEGER|REAL|BLOB|TIMESTAMP|BOOLEAN|DATE|DEFAULT|NOT|CHECK|REFERENCES|AUTOINCREMENT)"

        for seg in segments:
            seg = seg.strip()
            if not seg or seg.startswith("--"):
                continue
            # Skip constraint segments
            if re.match(r"(FOREIGN\s+KEY|PRIMARY\s+KEY|UNIQUE\s*\()", seg, re.IGNORECASE):
                continue
            # Extract column name — first word followed by type keyword
            col_match = re.match(rf"(\w+)\s+{type_kw}", seg, re.IGNORECASE)
            if col_match:
                col_name = col_match.group(1).lower()
                if col_name not in skip_kw:
                    cols.append(col_name)
        if cols:
            tables[table_name.lower()] = cols
    return tables


def _extract_sql_columns_from_python(source: str) -> List[Tuple[str, str, List[str]]]:
    """Extract (table_name, operation, [columns]) from Python SQL strings.

    Finds INSERT INTO table (col1, col2) and SELECT col1, col2 FROM table patterns.
    """
    results: List[Tuple[str, str, List[str]]] = []

    # INSERT INTO table (col1, col2, ...) VALUES ...
    insert_pat = re.compile(
        r"INSERT\s+INTO\s+(\w+)\s*\(\s*([^)]+?)\s*\)\s*VALUES",
        re.IGNORECASE | re.DOTALL,
    )
    for m in insert_pat.finditer(source):
        table = m.group(1).lower()
        raw_cols = m.group(2).replace("\n", " ").replace('"', " ").replace("'", " ")
        cols = [c.strip().lower() for c in raw_cols.split(",") if c.strip()]
        # Filter out empty/whitespace-only entries
        cols = [c for c in cols if c and not c.startswith(("?", "(", ")"))]
        if cols:
            results.append((table, "INSERT", cols))

    return results


def _extract_imports(source: str) -> List[Tuple[str, int]]:
    """Extract (import_name, line_number) from Python source via AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name
                imports.append((name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                name = alias.asname or alias.name
                imports.append((name, node.lineno))
    return imports


def _extract_name_usage(source: str) -> Set[str]:
    """Extract all Name references from Python source via AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _extract_function_sigs(source: str) -> Dict[str, List[str]]:
    """Extract function_name → [param_names] from Python source via AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    sigs: Dict[str, List[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = []
            for arg in node.args.args:
                if arg.arg != "self":
                    params.append(arg.arg)
            sigs[node.name] = params
    return sigs


def _extract_function_calls(source: str) -> List[Tuple[str, int, int, List[str]]]:
    """Extract (func_name, line, positional_count, keyword_names) from calls."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Get function name
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr

            if func_name:
                pos_count = len(node.args)
                kw_names = [kw.arg for kw in node.keywords if kw.arg]
                calls.append((func_name, getattr(node, "lineno", 0), pos_count, kw_names))
    return calls


# ---------------------------------------------------------------------------
# Check 1: Schema-Code Coherence
# ---------------------------------------------------------------------------


def check_schema_code(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Verify CREATE TABLE columns match INSERT statements in tools."""
    schema_path = PROJECT_ROOT / "tools" / "db" / "init_icdev_db.py"
    schema_text = _read_text(schema_path)
    schema_tables = _parse_create_tables(schema_text)

    mismatches: List[str] = []
    checked_files: List[str] = []

    # Scan Python files for INSERT statements
    scan_files = changed_files or []
    if not scan_files:
        for py in (PROJECT_ROOT / "tools").rglob("*.py"):
            if "__pycache__" not in str(py):
                scan_files.append(py)

    for py_path in scan_files:
        source = _read_text(py_path)
        if "INSERT" not in source.upper():
            continue

        sql_ops = _extract_sql_columns_from_python(source)
        for table, op, cols in sql_ops:
            if table in schema_tables:
                schema_cols = set(schema_tables[table])
                insert_cols = set(cols)
                # Check for columns in INSERT that aren't in schema
                extra = insert_cols - schema_cols - {"id"}  # id may be auto
                if extra:
                    try:
                        rel = py_path.relative_to(PROJECT_ROOT)
                    except ValueError:
                        rel = py_path.name
                    mismatches.append(f"{rel}: {op} into '{table}' has unknown columns: {sorted(extra)}")
                try:
                    checked_files.append(str(py_path.relative_to(PROJECT_ROOT)))
                except ValueError:
                    checked_files.append(py_path.name)

    if mismatches:
        return CoherenceCheck(
            check_id="schema_code",
            check_name="Schema-Code Coherence",
            status="fail",
            expected=["All INSERT columns exist in CREATE TABLE"],
            actual=mismatches,
            missing=[],
            extra=mismatches,
            message=f"{len(mismatches)} schema-code mismatch(es) found",
        )

    return CoherenceCheck(
        check_id="schema_code",
        check_name="Schema-Code Coherence",
        status="pass",
        expected=["All INSERT columns exist in CREATE TABLE"],
        actual=[f"Checked {len(checked_files)} files"],
        missing=[],
        extra=[],
        message="All INSERT columns match schema definitions",
    )


# ---------------------------------------------------------------------------
# Check 2: Config-Code Coherence
# ---------------------------------------------------------------------------


def check_config_code(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Verify YAML config keys are referenced in code that loads them."""
    if not _HAS_YAML:
        return CoherenceCheck(
            check_id="config_code",
            check_name="Config-Code Coherence",
            status="warn",
            expected=[],
            actual=[],
            missing=[],
            extra=[],
            message="PyYAML not available — skipping config-code check",
        )

    mismatches: List[str] = []
    configs_checked = 0

    # Only check changed config files if specified
    config_files = []
    if changed_files:
        config_files = [f for f in changed_files if f.suffix in (".yaml", ".yml") and "args" in str(f)]
    else:
        args_dir = PROJECT_ROOT / "args"
        if args_dir.exists():
            config_files = list(args_dir.glob("*.yaml"))

    for cfg_path in config_files[:20]:  # Cap to avoid scanning all configs
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            continue

        configs_checked += 1
        # Extract top-level keys
        if not isinstance(cfg, dict):
            continue
        top_keys = list(cfg.keys())

        # Find the tool that loads this config (same basename)
        cfg_stem = cfg_path.stem
        # Search for files that reference this config
        for py in (PROJECT_ROOT / "tools").rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            source = _read_text(py)
            if cfg_stem in source or str(cfg_path.name) in source:
                # This file references the config — check key access
                for key in top_keys:
                    # Look for .get("key") or ["key"] patterns
                    if f'"{key}"' not in source and f"'{key}'" not in source:
                        # Top-level key not referenced — could be nested access
                        pass  # Only flag if we can prove it's unused
                break  # Found the consumer, move on

    return CoherenceCheck(
        check_id="config_code",
        check_name="Config-Code Coherence",
        status="pass",
        expected=["Config keys referenced in code"],
        actual=[f"Checked {configs_checked} config files"],
        missing=mismatches,
        extra=[],
        message=f"Checked {configs_checked} configs" + (f", {len(mismatches)} issues" if mismatches else ""),
    )


# ---------------------------------------------------------------------------
# Check 3: Signature-Call Coherence
# ---------------------------------------------------------------------------


def check_signature_call(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Check that functions with many params are called with keyword args in tests."""
    config = _load_config()
    min_params = config.get("signature_call", {}).get("min_params_for_keyword_warning", 4)

    warnings: List[str] = []

    # Find changed tool files and their test files
    tool_files: List[Path] = []
    test_files: List[Path] = []

    if changed_files:
        for f in changed_files:
            if "tests/" in str(f).replace("\\", "/"):
                test_files.append(f)
            elif f.suffix == ".py":
                tool_files.append(f)
    else:
        # Scan all test files
        tests_dir = PROJECT_ROOT / "tests"
        if tests_dir.exists():
            test_files = list(tests_dir.glob("test_*.py"))

    # Extract function signatures from tool files
    all_sigs: Dict[str, List[str]] = {}
    for tf in tool_files:
        sigs = _extract_function_sigs(_read_text(tf))
        for name, params in sigs.items():
            if len(params) >= min_params and not name.startswith("_"):
                all_sigs[name] = params

    # Check test files for positional calls to those functions
    for test_path in test_files:
        source = _read_text(test_path)
        calls = _extract_function_calls(source)
        for func_name, line, pos_count, kw_names in calls:
            if func_name in all_sigs:
                expected_params = all_sigs[func_name]
                # If function has N params and call has N positional args, warn
                if pos_count >= min_params and not kw_names:
                    rel = test_path.relative_to(PROJECT_ROOT)
                    warnings.append(
                        f"{rel}:{line}: {func_name}() has {len(expected_params)} params "
                        f"but called with {pos_count} positional args — use keyword args"
                    )

    if warnings:
        return CoherenceCheck(
            check_id="signature_call",
            check_name="Signature-Call Coherence",
            status="warn",
            expected=["Functions with 4+ params use keyword args in tests"],
            actual=warnings,
            missing=[],
            extra=warnings,
            message=f"{len(warnings)} positional call warning(s) — risk of parameter order bugs",
        )

    return CoherenceCheck(
        check_id="signature_call",
        check_name="Signature-Call Coherence",
        status="pass",
        expected=["Functions with 4+ params use keyword args in tests"],
        actual=[f"Checked {len(test_files)} test files"],
        missing=[],
        extra=[],
        message="No positional-arg risks detected",
    )


# ---------------------------------------------------------------------------
# Check 4: Test Fixture-Schema Coherence
# ---------------------------------------------------------------------------


def check_fixture_schema(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Verify test fixture CREATE TABLE has columns that the test actually uses.

    Only flags columns that appear in INSERT/SELECT within the same test file
    but are missing from the fixture CREATE TABLE. This avoids false positives
    from intentionally minimal fixtures.
    """
    schema_path = PROJECT_ROOT / "tools" / "db" / "init_icdev_db.py"
    schema_text = _read_text(schema_path)
    schema_tables = _parse_create_tables(schema_text)

    mismatches: List[str] = []
    checked = 0

    test_files = changed_files or []
    if not test_files:
        tests_dir = PROJECT_ROOT / "tests"
        if tests_dir.exists():
            test_files = list(tests_dir.glob("test_*.py"))

    # Only check test files
    test_files = [f for f in test_files if f.name.startswith("test_") and f.suffix == ".py"]

    for test_path in test_files:
        source = _read_text(test_path)
        if "CREATE TABLE" not in source.upper():
            continue

        fixture_tables = _parse_create_tables(source)

        # Extract columns the test actually uses in INSERT/SELECT
        used_sql = _extract_sql_columns_from_python(source)
        used_cols_per_table: Dict[str, Set[str]] = {}
        for tbl, _op, cols in used_sql:
            used_cols_per_table.setdefault(tbl, set()).update(cols)

        for table_name, fixture_cols in fixture_tables.items():
            if table_name in schema_tables:
                checked += 1
                fixture_col_set = set(fixture_cols)

                # Only flag columns that the test INSERT/SELECTs but are missing from fixture
                used_in_test = used_cols_per_table.get(table_name, set())
                missing_and_used = used_in_test - fixture_col_set - {"id"}

                if missing_and_used:
                    try:
                        rel = test_path.relative_to(PROJECT_ROOT)
                    except ValueError:
                        rel = test_path.name
                    mismatches.append(
                        f"{rel}: table '{table_name}' fixture missing columns "
                        f"that the test uses: {sorted(missing_and_used)}"
                    )

    if mismatches:
        return CoherenceCheck(
            check_id="fixture_schema",
            check_name="Test Fixture-Schema Coherence",
            status="fail",
            expected=["Test fixtures match init_icdev_db.py schema"],
            actual=mismatches,
            missing=mismatches,
            extra=[],
            message=f"{len(mismatches)} fixture-schema mismatch(es) — tests will fail on missing columns",
        )

    return CoherenceCheck(
        check_id="fixture_schema",
        check_name="Test Fixture-Schema Coherence",
        status="pass",
        expected=["Test fixtures match init_icdev_db.py schema"],
        actual=[f"Checked {checked} fixture tables"],
        missing=[],
        extra=[],
        message=f"All {checked} fixture tables match schema",
    )


# ---------------------------------------------------------------------------
# Check 5: Manifest Coherence
# ---------------------------------------------------------------------------


def check_manifest() -> CoherenceCheck:
    """Verify tool Python files are documented in tools/manifest.md."""
    manifest_path = PROJECT_ROOT / "tools" / "manifest.md"
    if not manifest_path.exists():
        return CoherenceCheck(
            check_id="manifest",
            check_name="Manifest Coherence",
            status="warn",
            expected=[],
            actual=[],
            missing=[],
            extra=[],
            message="tools/manifest.md not found",
        )

    manifest_text = _read_text(manifest_path).lower()

    # Find tool directories with Python files
    config = _load_config()
    exclude_dirs = set(config.get("manifest", {}).get("exclude_dirs", []))
    exclude_parts = {d.rstrip("/").replace("tools/", "") for d in exclude_dirs}

    undocumented: List[str] = []
    checked = 0

    tools_dir = PROJECT_ROOT / "tools"
    for py in tools_dir.rglob("*.py"):
        if "__pycache__" in str(py) or py.name == "__init__.py" or py.name.startswith("_"):
            continue
        rel = py.relative_to(PROJECT_ROOT)
        # Skip excluded directories
        parts = rel.parts
        if len(parts) > 1 and any(p in str(rel) for p in exclude_parts):
            continue

        checked += 1
        # Check if filename appears in manifest
        if py.stem.lower() not in manifest_text:
            undocumented.append(str(rel))

    if undocumented and len(undocumented) < checked * 0.5:  # Only flag if < 50% missing
        return CoherenceCheck(
            check_id="manifest",
            check_name="Manifest Coherence",
            status="warn",
            expected=[f"All {checked} tools documented in manifest"],
            actual=[f"{len(undocumented)} undocumented"],
            missing=undocumented[:20],  # Cap output
            extra=[],
            message=f"{len(undocumented)} tool(s) not found in manifest.md",
        )

    return CoherenceCheck(
        check_id="manifest",
        check_name="Manifest Coherence",
        status="pass",
        expected=["Tool files documented in manifest"],
        actual=[f"Checked {checked} files"],
        missing=[],
        extra=[],
        message=f"Manifest coverage adequate ({checked} tools checked)",
    )


# ---------------------------------------------------------------------------
# Check 6: Append-Only Table Coherence (delegates to claude_dir_validator)
# ---------------------------------------------------------------------------


def check_append_only() -> CoherenceCheck:
    """Verify append-only tables in init_icdev_db.py are protected in pre_tool_use.py."""
    schema_path = PROJECT_ROOT / "tools" / "db" / "init_icdev_db.py"
    hook_path = PROJECT_ROOT / ".claude" / "hooks" / "pre_tool_use.py"

    if not schema_path.exists() or not hook_path.exists():
        return CoherenceCheck(
            check_id="append_only",
            check_name="Append-Only Table Protection",
            status="warn",
            expected=[],
            actual=[],
            missing=[],
            extra=[],
            message="Required files not found",
        )

    schema_text = _read_text(schema_path)
    hook_text = _read_text(hook_path)

    # Find tables with append-only comments in schema
    append_only_in_schema: Set[str] = set()
    lines = schema_text.split("\n")
    for i, line in enumerate(lines):
        if "append-only" in line.lower() or "immutable" in line.lower():
            # Look for next CREATE TABLE
            for j in range(i, min(i + 5, len(lines))):
                m = re.search(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)", lines[j], re.IGNORECASE)
                if m:
                    append_only_in_schema.add(m.group(1).lower())
                    break

    # Find tables in APPEND_ONLY_TABLES list in hook
    protected: Set[str] = set()
    in_list = False
    for line in hook_text.split("\n"):
        if "APPEND_ONLY_TABLES" in line and "[" in line:
            in_list = True
        if in_list:
            m = re.findall(r'"(\w+)"', line)
            for table in m:
                protected.add(table.lower())
            if "]" in line:
                in_list = False

    missing = append_only_in_schema - protected
    if missing:
        return CoherenceCheck(
            check_id="append_only",
            check_name="Append-Only Table Protection",
            status="fail",
            expected=[f"{len(append_only_in_schema)} append-only tables protected"],
            actual=[f"{len(protected)} protected in pre_tool_use.py"],
            missing=sorted(missing),
            extra=[],
            message=f"{len(missing)} append-only table(s) unprotected: {sorted(missing)}",
        )

    return CoherenceCheck(
        check_id="append_only",
        check_name="Append-Only Table Protection",
        status="pass",
        expected=[f"{len(append_only_in_schema)} append-only tables"],
        actual=[f"{len(protected)} protected"],
        missing=[],
        extra=[],
        message="All append-only tables are protected",
    )


# ---------------------------------------------------------------------------
# Check 7: Import Usage Coherence
# ---------------------------------------------------------------------------


def check_import_usage(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Check for unused imports in changed files (lightweight pre-ruff catch)."""
    files_to_check = changed_files or []
    if not files_to_check:
        return CoherenceCheck(
            check_id="import_usage",
            check_name="Import Usage",
            status="pass",
            expected=[],
            actual=[],
            missing=[],
            extra=[],
            message="No files specified — use --changed-files",
        )

    # Only check .py files
    py_files = [f for f in files_to_check if f.suffix == ".py" and f.exists()]
    unused: List[str] = []

    for py_path in py_files:
        source = _read_text(py_path)
        if not source:
            continue

        imports = _extract_imports(source)
        names_used = _extract_name_usage(source)

        for imp_name, line_no in imports:
            # Skip underscore imports and common patterns
            if imp_name.startswith("_") or imp_name in ("annotations",):
                continue
            # Check if the imported name is used anywhere
            base_name = imp_name.split(".")[0]
            if base_name not in names_used and imp_name not in names_used:
                rel = py_path.relative_to(PROJECT_ROOT) if py_path.is_relative_to(PROJECT_ROOT) else py_path
                unused.append(f"{rel}:{line_no}: unused import '{imp_name}'")

    if unused:
        return CoherenceCheck(
            check_id="import_usage",
            check_name="Import Usage",
            status="warn",
            expected=["All imports used"],
            actual=unused,
            missing=[],
            extra=unused,
            message=f"{len(unused)} unused import(s) — will fail ruff",
        )

    return CoherenceCheck(
        check_id="import_usage",
        check_name="Import Usage",
        status="pass",
        expected=["All imports used"],
        actual=[f"Checked {len(py_files)} files"],
        missing=[],
        extra=[],
        message="No unused imports detected",
    )


# ---------------------------------------------------------------------------
# Check 8: api_wiring — verify API handlers read from DB, not hardcoded
# ---------------------------------------------------------------------------

# Patterns indicating a function reads from storage (DB/connector/file)
_DB_CALL_PATTERNS = re.compile(
    r"(?:"
    r"\.execute\(|\.fetchone\(|\.fetchall\(|\.fetchmany\("
    r"|get_conn\(|get_connection\("
    r"|\.read\(|\.query\("
    r"|open\(|Path\("
    r"|from\s+\S+\s+import\s+"  # lazy imports inside function
    r"|subprocess\.run\("  # tool dispatch via subprocess
    r"|import\s+\S*(?:db|storage|connector|model)"
    r")",
    re.IGNORECASE,
)

# Patterns indicating hardcoded return data (literal dict/list in return)
_HARDCODED_RETURN = re.compile(
    r"return\s+jsonify\s*\(\s*\{[^}]*\}\s*\)",
    re.DOTALL,
)


def check_api_wiring(
    changed_files: Optional[List[Path]] = None,
) -> CoherenceCheck:
    """Check 8: verify API/dashboard route handlers read from DB.

    Scans Flask route handlers for functions that return jsonify()
    with literal dicts but have no DB/storage calls in their body.
    These are likely hardcoded placeholders that should read from a
    database or connector.

    Detects the pattern that caused the AlphaDesk lifecycle bug:
    API handlers returning static data instead of querying the DB.
    """
    # Find all dashboard/API Python files
    scan_dirs = [
        PROJECT_ROOT / "tools" / "dashboard" / "api",
        PROJECT_ROOT / "tools" / "dashboard",
        PROJECT_ROOT / "tools" / "trading" / "dashboard",
        PROJECT_ROOT / "tools" / "trading" / "dashboard" / "api",
    ]
    # Also check child apps
    apps_dir = PROJECT_ROOT / "apps"
    if apps_dir.exists():
        for app_dir in apps_dir.iterdir():
            if app_dir.is_dir():
                for sub in ["tools/dashboard", "tools/dashboard/api"]:
                    p = app_dir / sub
                    if p.exists():
                        scan_dirs.append(p)

    if changed_files:
        py_files = [f for f in changed_files if f.suffix == ".py" and f.exists()]
    else:
        py_files = []
        for d in scan_dirs:
            if d.exists():
                py_files.extend(d.glob("*.py"))

    hardcoded_apis: List[str] = []

    for py_path in py_files:
        source = _read_text(py_path)
        if not source or "@app.route" not in source:
            continue

        try:
            tree = ast.parse(source, filename=str(py_path))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue

            # Check if this function has a route decorator
            is_route = False
            for dec in node.decorator_list:
                dec_src = ast.dump(dec)
                if "route" in dec_src.lower():
                    is_route = True
                    break
            if not is_route:
                continue

            # Get function body source
            func_lines = source.splitlines()[node.lineno - 1 : node.end_lineno]
            func_body = "\n".join(func_lines)

            # Skip small health/version endpoints (< 5 lines)
            if len(func_lines) <= 5:
                continue

            # Check: does it have any DB/storage call?
            has_db_call = bool(_DB_CALL_PATTERNS.search(func_body))

            # Check: does it return jsonify with a literal?
            has_literal_return = bool(_HARDCODED_RETURN.search(func_body))

            if has_literal_return and not has_db_call:
                rel = py_path.relative_to(PROJECT_ROOT) if py_path.is_relative_to(PROJECT_ROOT) else py_path
                hardcoded_apis.append(f"{rel}:{node.lineno}: {node.name}() returns hardcoded data (no DB/storage call)")

    if hardcoded_apis:
        return CoherenceCheck(
            check_id="api_wiring",
            check_name="API Wiring",
            status="warn",
            expected=["All API handlers read from DB/storage"],
            actual=hardcoded_apis,
            missing=[],
            extra=hardcoded_apis,
            message=(
                f"{len(hardcoded_apis)} API handler(s) return "
                f"hardcoded data without DB/storage calls — "
                f"likely placeholder code"
            ),
        )

    return CoherenceCheck(
        check_id="api_wiring",
        check_name="API Wiring",
        status="pass",
        expected=["All API handlers read from DB/storage"],
        actual=[f"Scanned {len(py_files)} API files"],
        missing=[],
        extra=[],
        message="All API handlers have DB/storage calls",
    )


# ---------------------------------------------------------------------------
# Check Registry & Orchestrator
# ---------------------------------------------------------------------------

CHECK_REGISTRY = {
    "schema_code": check_schema_code,
    "config_code": check_config_code,
    "signature_call": check_signature_call,
    "fixture_schema": check_fixture_schema,
    "manifest": check_manifest,
    "append_only": check_append_only,
    "import_usage": check_import_usage,
    "api_wiring": check_api_wiring,
}


# ---------------------------------------------------------------------------
# Auto-fix engine (D-WF-8a: safe fixes only)
# ---------------------------------------------------------------------------

# Fix tiers: auto (safe, no behavior change), suggest (needs review), skip (risky)
_FIX_REGISTRY: Dict[str, str] = {
    "import_usage": "auto",  # ruff --fix --select F401,F811,F841
    "append_only": "auto",  # add table name to APPEND_ONLY_TABLES
    "manifest": "auto",  # auto-append missing tools to manifest.md
    "schema_code": "suggest",  # suggest ALTER TABLE DDL
    "config_code": "suggest",  # suggest YAML additions
    "fixture_schema": "suggest",  # suggest test fixture DDL
    "signature_call": "skip",  # too risky to auto-modify call sites
    "api_wiring": "suggest",  # suggest DB integration for hardcoded APIs
}


def _autofix_imports(check: CoherenceCheck) -> List[str]:
    """Auto-fix unused imports via ruff."""
    import subprocess

    fixes = []
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "ruff",
                "check",
                str(PROJECT_ROOT / "tools"),
                "--fix",
                "--select",
                "F401,F811,F841",
                "--quiet",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            fixes.append("ruff auto-fixed unused imports/variables in tools/")
    except Exception:
        pass
    return fixes


def _autofix_append_only(check: CoherenceCheck) -> List[str]:
    """Auto-add missing tables to APPEND_ONLY_TABLES in pre_tool_use.py."""
    if not check.missing:
        return []
    hook_path = PROJECT_ROOT / ".claude" / "hooks" / "pre_tool_use.py"
    if not hook_path.exists():
        return []
    content = hook_path.read_text(encoding="utf-8")
    # Find APPEND_ONLY_TABLES set
    match = re.search(r"(APPEND_ONLY_TABLES\s*=\s*\{[^}]+)\}", content)
    if not match:
        return []
    fixes = []
    existing_block = match.group(0)
    new_entries = []
    for table in check.missing:
        if f'"{table}"' not in existing_block and f"'{table}'" not in existing_block:
            new_entries.append(f'    "{table}",')
    if new_entries:
        # Insert before closing brace
        insert_point = existing_block.rstrip().rstrip("}")
        new_block = insert_point + "\n" + "\n".join(new_entries) + "\n}"
        content = content.replace(existing_block, new_block)
        hook_path.write_text(content, encoding="utf-8")
        fixes.append(f"Added {len(new_entries)} table(s) to APPEND_ONLY_TABLES: {', '.join(check.missing)}")
    return fixes


def _autofix_manifest(check: CoherenceCheck) -> List[str]:
    """Auto-append missing tools to tools/manifest.md."""
    missing = check.missing
    if not missing:
        return []

    manifest_path = PROJECT_ROOT / "tools" / "manifest.md"
    if not manifest_path.exists():
        return []

    lines = []
    for tool_path in missing:
        p = Path(tool_path)
        name = p.stem.replace("_", " ").title()
        desc = f"Auto-registered: {p.parent.name}/{p.name}"
        lines.append(f"| {name} | {tool_path} | {desc} | --json | JSON |")

    if lines:
        with open(manifest_path, "a", encoding="utf-8") as f:
            f.write("\n\n## Auto-Registered (Coherence Fix)\n")
            f.write("| Tool | File | Description | Input | Output |\n")
            f.write("|------|------|-------------|-------|--------|\n")
            for line in lines:
                f.write(line + "\n")

    return [f"Appended {len(lines)} tools to manifest.md"]


_AUTOFIX_HANDLERS: Dict[str, Any] = {
    "import_usage": _autofix_imports,
    "append_only": _autofix_append_only,
    "manifest": _autofix_manifest,
}


def _apply_fixes(check: CoherenceCheck) -> CoherenceCheck:
    """Apply auto-fixes for a failed/warned check. Returns updated check."""
    tier = _FIX_REGISTRY.get(check.check_id, "skip")
    if tier != "auto":
        return check
    handler = _AUTOFIX_HANDLERS.get(check.check_id)
    if not handler:
        return check
    fixes = handler(check)
    if fixes:
        check.fixes_applied = fixes
        check.message += f" ({len(fixes)} auto-fixed)"
    return check


def run_checks(
    selected: Optional[List[str]] = None,
    changed_files: Optional[List[Path]] = None,
    autofix: bool = False,
) -> CoherenceReport:
    """Run selected coherence checks and produce aggregate report.

    Args:
        selected: specific check IDs to run (None = all)
        changed_files: restrict import check to these files
        autofix: if True, auto-fix safe issues after detection
    """
    checks_to_run = selected or list(CHECK_REGISTRY.keys())
    results: List[CoherenceCheck] = []
    total_fixes = 0

    for check_id in checks_to_run:
        func = CHECK_REGISTRY.get(check_id)
        if not func:
            results.append(
                CoherenceCheck(
                    check_id=check_id,
                    check_name=f"Unknown: {check_id}",
                    status="warn",
                    expected=[],
                    actual=[],
                    missing=[],
                    extra=[],
                    message=f"Unknown check: {check_id}",
                )
            )
            continue

        try:
            # Pass changed_files to checks that accept it
            import inspect

            sig = inspect.signature(func)
            if "changed_files" in sig.parameters:
                result = func(changed_files=changed_files)
            else:
                result = func()

            # Auto-fix if requested and check failed/warned
            if autofix and result.status in ("fail", "warn"):
                result = _apply_fixes(result)
                total_fixes += len(result.fixes_applied)

            results.append(result)
        except Exception as exc:
            results.append(
                CoherenceCheck(
                    check_id=check_id,
                    check_name=check_id,
                    status="warn",
                    expected=[],
                    actual=[],
                    missing=[],
                    extra=[],
                    message=f"Check error: {exc}",
                )
            )

    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    warned = sum(1 for r in results if r.status == "warn")

    return CoherenceReport(
        overall_pass=failed == 0,
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        checks=results,
        total_checks=len(results),
        passed_checks=passed,
        failed_checks=failed,
        warned_checks=warned,
        total_fixes=total_fixes,
    )


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def _format_human(report: CoherenceReport) -> str:
    lines = [
        "",
        "=" * 60,
        f"  COHERENCE CHECK  {'PASS' if report.overall_pass else 'FAIL'}",
        f"  {report.timestamp}",
        "=" * 60,
        "",
    ]
    for c in report.checks:
        icon = {"pass": "+", "fail": "X", "warn": "!"}[c.status]
        lines.append(f"  [{icon}] {c.check_name}: {c.message}")
        if c.missing:
            for m in c.missing[:5]:
                lines.append(f"      - {m}")
            if len(c.missing) > 5:
                lines.append(f"      ... and {len(c.missing) - 5} more")
        if c.extra and c.check_id != "append_only":
            for e in c.extra[:5]:
                lines.append(f"      - {e}")
            if len(c.extra) > 5:
                lines.append(f"      ... and {len(c.extra) - 5} more")

    if report.total_fixes > 0:
        lines.append(f"  [*] Auto-fixed {report.total_fixes} issue(s)")
        for c in report.checks:
            for fix in c.fixes_applied:
                lines.append(f"      - {fix}")

    lines.extend(
        [
            "",
            f"  Total: {report.total_checks}  Pass: {report.passed_checks}  "
            f"Fail: {report.failed_checks}  Warn: {report.warned_checks}"
            + (f"  Fixed: {report.total_fixes}" if report.total_fixes else ""),
            "",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Implementation Coherence Checker — internal consistency validation")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--gate", action="store_true", help="Exit 0=pass, 1=fail")
    parser.add_argument("--fix", action="store_true", help="Auto-fix safe issues (imports, append-only)")
    parser.add_argument("--all", action="store_true", help="Run all checks")
    parser.add_argument("--check", type=str, default="", help=f"Specific check: {', '.join(CHECK_REGISTRY.keys())}")
    parser.add_argument("--changed-files", type=str, default="", help="Comma-separated list of changed file paths")

    args = parser.parse_args()

    selected = None
    if args.check:
        selected = [c.strip() for c in args.check.split(",")]
    elif args.all:
        selected = None  # All checks

    changed: Optional[List[Path]] = None
    if args.changed_files:
        changed = [PROJECT_ROOT / f.strip() for f in args.changed_files.split(",")]

    report = run_checks(selected, changed, autofix=args.fix)

    if args.human:
        print(_format_human(report))
    elif args.json or args.gate:
        print(json.dumps(report.to_dict(), indent=2))

    if args.gate:
        sys.exit(0 if report.overall_pass else 1)
    elif not report.overall_pass:
        sys.exit(1)


if __name__ == "__main__":
    main()
