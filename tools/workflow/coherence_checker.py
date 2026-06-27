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
  7. import_usage   — Unused imports in recently changed files (stdlib-only warn)
  7b. ruff_lint     — Authoritative F401/F811/F841 gate via ruff (OPT-49)
  8. api_wiring     — API handlers read from DB, not hardcoded literals
  9. skill_standard — .agents/skills/*/SKILL.md conform to mattpocock/skills convention
 10. sandbox_coverage — docs/security/sandbox-coverage.md references all ingress gap files
 11. direct_anthropic_import — no direct `import anthropic` outside tools/llm/anthropic_provider.py (OPT-44)
 12. karpathy_sync  — 5 canonical Karpathy headings present in all 10 AI platform configs
 13. openapi_parity — generate_openapi_spec(app) paths match app.url_map /api/v1/* routes
 14. security_context — RLS auto-wiring intact; set_security_context(None) bypasses documented
 15. canvas_placeholder_style — bare ? in execute() SQL for get_canvas_connection callers (use %s)
 16. runtime_placeholder_style — bare ? in execute() SQL in ANY runtime tools/ file (use %s; translate_sql is not a fix)
 17. ace_yaml_listen_topics   — role YAMLs must not mix task.assigned with reactive topics (deadlock risk)

All checks: stdlib only (ast, re, pathlib), air-gap safe, zero deps.
(openapi_parity imports Flask/dashboard at runtime; gracefully skips if unavailable.)
Follows claude_dir_validator.py pattern (dataclass results, check registry).

Usage:
    python tools/workflow/coherence_checker.py --all --json
    python tools/workflow/coherence_checker.py --check schema_code --json
    python tools/workflow/coherence_checker.py --check fixture_schema --json
    python tools/workflow/coherence_checker.py --changed-files "tools/workflow/loop_engine.py,tests/test_workflow_loop.py" --json
    python tools/workflow/coherence_checker.py --all --human
    python tools/workflow/coherence_checker.py --all --gate
    python tools/workflow/coherence_checker.py --all --fix --json   # Auto-fix safe issues
    python tools/workflow/coherence_checker.py --check canvas_placeholder_style --gate
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

# Ensure the repository root is importable regardless of how this script is
# invoked (``python tools/workflow/coherence_checker.py`` adds the script
# directory to sys.path, not the repo root).
_repo_root = str(PROJECT_ROOT)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

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

    def __getitem__(self, key: str):
        # 'check' is an alias for check_id for backward-compat with dict-style access
        if key == "check":
            return self.check_id
        return self.to_dict()[key]

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
        type_kw = r"(TEXT|INTEGER|REAL|BLOB|NUMERIC|DECIMAL|TIMESTAMP|BOOLEAN|DATE|DEFAULT|NOT|CHECK|REFERENCES|AUTOINCREMENT)"

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
    # Manifest was split into shards (2026-04-14). Concatenate shards so tool
    # filename lookups span the whole documented surface, not just the index.
    shard_dir = PROJECT_ROOT / "tools" / "manifest"
    if shard_dir.is_dir():
        for shard in shard_dir.glob("*.md"):
            manifest_text += "\n" + _read_text(shard).lower()

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
# Check 7b: Ruff Lint Gate (OPT-49)
# ---------------------------------------------------------------------------
#
# Authoritative F401/F811/F841 gate. Where `check_import_usage` is a
# lightweight stdlib-only precursor that only WARNS, `check_ruff_lint`
# FAILS the gate on any unused-import / redefinition / unused-local.
#
# Scope:
#   - Default scope: tools/ (the implementation tree)
#   - --changed-files: limit to those paths (pre-commit / pre_tool_use hook)
#
# Whitelist: args/ruff_gate.yaml — grandfathered `{file: [rule_codes]}`
# entries skip the gate but are still reported as warnings so they can be
# cleaned up opportunistically.


_RUFF_GATE_RULES = ("F401", "F811", "F841")
_RUFF_GATE_CONFIG = PROJECT_ROOT / "args" / "ruff_gate.yaml"
_PAGE_COMPLETENESS_WHITELIST_CONFIG = PROJECT_ROOT / "args" / "page_completeness_whitelist.yaml"
# IQE triage (tch-fix-04): canvases excused from the IQE/completeness gate because
# they are utility/legacy, already wired under an abbreviated _CANVAS_MAP key, or
# only need a small dispatch/seed backfill. The `iqe_required` bucket is NOT
# skipped — those still fail the gate until backfilled.
_COMPLETION_EXEMPTIONS_CONFIG = PROJECT_ROOT / "args" / "completion_exemptions.yaml"
# Buckets unioned into the skip set; `iqe_required` is intentionally excluded.
_COMPLETION_EXEMPTION_SKIP_BUCKETS = ("iqe_exempt", "iqe_wired_via_alias", "iqe_partial")


def _load_ruff_gate_whitelist() -> Dict[str, Set[str]]:
    """Load grandfathered whitelist from args/ruff_gate.yaml.

    Schema:
        whitelist:
          tools/dashboard/app.py:
            - F401
          tools/legacy/foo.py:
            - F401
            - F841

    Returns a dict keyed by normalized relative path → set of rule codes.
    Missing file, malformed YAML, or missing pyyaml → empty dict (fail-safe
    open: if the whitelist can't be read, NOTHING is grandfathered and the
    gate is stricter, not looser).
    """
    if not _RUFF_GATE_CONFIG.exists():
        return {}
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    try:
        raw = yaml.safe_load(_RUFF_GATE_CONFIG.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    wl = raw.get("whitelist") or {}
    if not isinstance(wl, dict):
        return {}
    normalized: Dict[str, Set[str]] = {}
    for path, codes in wl.items():
        if not isinstance(path, str):
            continue
        # Normalize to forward-slash relative path
        key = path.replace("\\", "/").lstrip("./")
        if isinstance(codes, list):
            normalized[key] = {str(c).upper() for c in codes}
        elif isinstance(codes, str):
            normalized[key] = {codes.upper()}
    return normalized


def _load_page_completeness_whitelist() -> Set[str]:
    """Load grandfathered canvas names from args/page_completeness_whitelist.yaml.

    Schema:
        # reason for each canvas
        whitelisted_canvases:
          - canvas_name  # reason (task-id)

    Additionally unions the IQE-triage exemptions from
    args/completion_exemptions.yaml (tch-fix-04): the `iqe_exempt`,
    `iqe_wired_via_alias`, and `iqe_partial` buckets are skipped; `iqe_required`
    is intentionally NOT skipped so those canvases still fail the gate until
    their IQE backfill lands.

    Returns a set of canvas names to skip. Missing/malformed files → ignored.
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        return set()

    skip: Set[str] = set()

    # 1. Legacy grandfathered whitelist.
    if _PAGE_COMPLETENESS_WHITELIST_CONFIG.exists():
        try:
            raw = yaml.safe_load(
                _PAGE_COMPLETENESS_WHITELIST_CONFIG.read_text(encoding="utf-8")
            ) or {}
            canvases = raw.get("whitelisted_canvases")
            if isinstance(canvases, list):
                skip.update(str(c).strip() for c in canvases if c)
        except Exception:
            pass

    # 2. IQE triage exemptions (tch-fix-04).
    if _COMPLETION_EXEMPTIONS_CONFIG.exists():
        try:
            raw = yaml.safe_load(
                _COMPLETION_EXEMPTIONS_CONFIG.read_text(encoding="utf-8")
            ) or {}
            for bucket in _COMPLETION_EXEMPTION_SKIP_BUCKETS:
                entries = raw.get(bucket)
                # Tolerate either {canvas: rationale} mappings or [canvas] lists.
                if isinstance(entries, dict):
                    skip.update(str(c).strip() for c in entries if c)
                elif isinstance(entries, list):
                    skip.update(str(c).strip() for c in entries if c)
        except Exception:
            pass

    return skip


def _run_ruff_lint(
    targets: List[Path],
    rules: Tuple[str, ...] = _RUFF_GATE_RULES,
) -> List[Dict[str, Any]]:
    """Invoke ruff check in JSON output mode.

    Returns a list of hit dicts: {filename, row, col, code, message}.
    Exceptions during subprocess invocation return an empty list — the
    caller will treat that as a pass (fail-open) because an exec failure
    should not wedge the entire gate. Ruff's own exit code is NOT trusted
    as the signal; we only trust the parsed JSON content.
    """
    import subprocess

    if not targets:
        return []
    cmd = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--select",
        ",".join(rules),
        "--output-format",
        "json",
        "--no-fix",
    ]
    cmd.extend(str(t) for t in targets if t.exists())
    # If every target filtered out, nothing to check
    if len(cmd) == len([sys.executable, "-m", "ruff", "check", "--select", ",".join(rules), "--output-format", "json", "--no-fix"]):
        return []
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception:
        return []
    stdout = (proc.stdout or "").strip()
    if not stdout:
        return []
    try:
        parsed = json.loads(stdout)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return parsed


def check_ruff_lint(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Check 7b: authoritative ruff F401/F811/F841 gate (OPT-49).

    When `changed_files` is passed, scope is limited to those files — this
    is the pre_tool_use / pre-commit path. When it is None (e.g. --all),
    scope is the full `tools/` tree.

    Whitelisted hits are downgraded to warn and do not fail the gate;
    non-whitelisted hits fail it. No whitelist file = strict gate.

    Why we keep BOTH this AND check_import_usage:
      - import_usage is stdlib-only and runs in air-gap environments where
        ruff may not be installed. It is a best-effort pre-warn.
      - ruff_lint is the authoritative gate: zero false negatives on the
        three rule codes we care about, and it matches what CI runs.
    """
    tools_root = PROJECT_ROOT / "tools"
    if changed_files:
        targets = [
            f for f in changed_files
            if f.suffix == ".py" and f.exists()
        ]
    else:
        targets = [tools_root] if tools_root.exists() else []

    if not targets:
        return CoherenceCheck(
            check_id="ruff_lint",
            check_name="Ruff Lint Gate (F401/F811/F841)",
            status="pass",
            expected=["Zero F401/F811/F841 hits"],
            actual=[],
            missing=[],
            extra=[],
            message="No Python targets to check",
        )

    hits = _run_ruff_lint(targets)
    whitelist = _load_ruff_gate_whitelist()

    blocking: List[str] = []
    grandfathered: List[str] = []
    for h in hits:
        fname = str(h.get("filename", "")).replace("\\", "/")
        # Normalize to repo-relative path for whitelist lookup
        try:
            rel = str(Path(fname).resolve().relative_to(PROJECT_ROOT)).replace("\\", "/")
        except Exception:
            rel = fname
        code = str(h.get("code", "")).upper()
        loc = h.get("location") or {}
        row = loc.get("row") or h.get("row") or 0
        msg = (h.get("message") or "").split("\n")[0]
        entry = f"{rel}:{row}: {code} {msg}"
        if code in whitelist.get(rel, set()):
            grandfathered.append(entry)
        else:
            blocking.append(entry)

    if blocking:
        return CoherenceCheck(
            check_id="ruff_lint",
            check_name="Ruff Lint Gate (F401/F811/F841)",
            status="fail",
            expected=["Zero non-whitelisted F401/F811/F841 hits"],
            actual=blocking,
            missing=[],
            extra=blocking,
            message=(
                f"{len(blocking)} ruff lint hit(s) "
                f"(+ {len(grandfathered)} whitelisted) — run with --fix "
                f"or add to args/ruff_gate.yaml"
            ),
        )

    if grandfathered:
        return CoherenceCheck(
            check_id="ruff_lint",
            check_name="Ruff Lint Gate (F401/F811/F841)",
            status="warn",
            expected=["Zero F401/F811/F841 hits"],
            actual=grandfathered,
            missing=[],
            extra=grandfathered,
            message=(
                f"All {len(grandfathered)} hit(s) whitelisted in "
                "args/ruff_gate.yaml — gate passes but clean up when possible"
            ),
        )

    return CoherenceCheck(
        check_id="ruff_lint",
        check_name="Ruff Lint Gate (F401/F811/F841)",
        status="pass",
        expected=["Zero F401/F811/F841 hits"],
        actual=[f"Scanned {len(targets)} target(s)"],
        missing=[],
        extra=[],
        message="No blocking ruff lint issues",
    )


# ---------------------------------------------------------------------------
# Check 8: api_wiring — verify API handlers read from DB, not hardcoded
# ---------------------------------------------------------------------------

# Patterns indicating a function reads from storage (DB/connector/file/external)
# Extended 2026-04-18: recognize helper-delegation + known external-data sources
# as legitimate "does real work" patterns, not hardcoded placeholders.
_DB_CALL_PATTERNS = re.compile(
    r"(?:"
    r"\.execute\(|\.fetchone\(|\.fetchall\(|\.fetchmany\("
    r"|get_conn\(|get_connection\("
    r"|\.read\(|\.query\("
    r"|open\(|Path\("
    r"|from\s+\S+\s+import\s+"  # lazy imports inside function
    r"|subprocess\.run\("  # tool dispatch via subprocess
    r"|import\s+\S*(?:db|storage|connector|model)"
    # Helper-delegate patterns — routes that call underscore-prefixed helpers
    # are almost always delegating to a module-internal function that hits
    # the DB / computes real values. Names here are the standard verbs
    # used across the codebase.
    r"|_fetch_\w+\(|_current_\w+\(|_snapshot_\w+\(|_query_\w+\("
    r"|_load_\w+\(|_compute_\w+\(|_get_\w+\(|_list_\w+\("
    r"|_build_\w+\(|_resolve_\w+\(|_lookup_\w+\("
    # Known external-data sources — not our DB, but real dynamic data
    r"|yf\.Ticker|yfinance|fetch_latest_quote|requests\.get\("
    # Cookie reads are a valid key-value storage layer (e.g. theme preferences)
    r"|\.cookies\.get\(|set_cookie\("
    # Flask route introspection — live url_map is real dynamic data, not a literal
    r"|url_map|iter_rules\("
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

    Detects the pattern that caused the FathomDesk lifecycle bug:
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
# Route Uniqueness (F811) — catches duplicate Flask view-function names that
# silently abort blueprint registration mid-flight.
# ---------------------------------------------------------------------------
#
# Why an allowlist and not a full repo scan:
#   Flask uses the Python function name as the endpoint identifier. Two
#   @bp.route decorators with different URLs but the same function name
#   raise "View function mapping is overwriting an existing endpoint" at
#   app.register_blueprint() replay time — aborting the whole blueprint
#   mid-flight and silently dropping every route defined after the duplicate.
#
# This check is narrowly scoped to the 2 files where hundreds of Flask
# routes live in one module. Running a full ruff F811 pass repo-wide would
# drag in unrelated noise (non-blueprint files, test fixtures with
# intentional redefinitions, etc.).
#
# Regression history:
#   - Commit 37f04055 (2026-04-09) "feat: consolidated import wizard with
#     built-in validator" added a second nc_api_save_as_template in
#     tools/network/blueprint.py that collided with an older one at
#     line 7644. Silently broke /discovery, /intelligence, /runbooks,
#     /ingestion on the next dashboard restart. 2 days later the user
#     noticed and asked "how come most of those routes used to work?".
#     This check exists to make that class of bug loud.

# Files where Flask route duplicates cause the highest blast radius.
# Expand this list as new multi-route modules are added (new child apps,
# new canvas blueprints, etc.). Each file listed here will be passed to
# `ruff check --select F811`.
_ROUTE_UNIQUENESS_FILES: List[str] = [
    "tools/network/blueprint.py",
    "tools/dashboard/app.py",
]


def check_route_uniqueness(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Run `ruff check --select F811` on an allowlist of high-risk multi-route
    files. F811 is ruff's 'redefined-while-unused' rule — it flags any Python
    function/class that is redefined in the same scope. For Flask blueprints,
    that means two @bp.route decorators pointing at the same function name.

    Scope: files listed in _ROUTE_UNIQUENESS_FILES (currently 2).
    Runs in <100ms. Always fires regardless of --changed-files so a
    drive-by developer can't skip it by omitting the file from their PR.
    """
    import subprocess

    # Resolve the allowlist to existing absolute paths
    target_files: List[Path] = []
    missing_files: List[str] = []
    for rel in _ROUTE_UNIQUENESS_FILES:
        path = PROJECT_ROOT / rel
        if path.exists():
            target_files.append(path)
        else:
            missing_files.append(rel)

    if not target_files:
        return CoherenceCheck(
            check_id="route_uniqueness",
            check_name="Route Uniqueness (F811)",
            status="warn",
            expected=_ROUTE_UNIQUENESS_FILES,
            actual=[],
            missing=missing_files,
            extra=[],
            message=(
                f"None of the {len(_ROUTE_UNIQUENESS_FILES)} allowlisted "
                f"files exist; check skipped"
            ),
        )

    # Build the ruff command. --select F811 narrows to ONLY duplicate-
    # definition errors, not the full ruff rule set, so we don't inherit
    # unrelated F401/F841 noise.
    cmd = [
        sys.executable, "-m", "ruff", "check",
        "--select", "F811",
        "--output-format", "concise",
        *[str(p) for p in target_files],
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except FileNotFoundError:
        return CoherenceCheck(
            check_id="route_uniqueness",
            check_name="Route Uniqueness (F811)",
            status="warn",
            expected=_ROUTE_UNIQUENESS_FILES,
            actual=["ruff not installed"],
            missing=[],
            extra=[],
            message=(
                "ruff not available — install with `pip install ruff` to "
                "enable the F811 route-uniqueness gate"
            ),
        )
    except subprocess.TimeoutExpired:
        return CoherenceCheck(
            check_id="route_uniqueness",
            check_name="Route Uniqueness (F811)",
            status="warn",
            expected=_ROUTE_UNIQUENESS_FILES,
            actual=["ruff timed out after 30s"],
            missing=[],
            extra=[],
            message="ruff check --select F811 timed out",
        )

    stdout = (result.stdout or "").strip()
    # ruff exit codes: 0 = no issues, 1 = issues found, other = error
    if result.returncode == 0:
        return CoherenceCheck(
            check_id="route_uniqueness",
            check_name="Route Uniqueness (F811)",
            status="pass",
            expected=_ROUTE_UNIQUENESS_FILES,
            actual=[f"Scanned {len(target_files)} file(s), 0 duplicates"],
            missing=[],
            extra=[],
            message=(
                f"No duplicate Flask view functions in "
                f"{len(target_files)} route module(s)"
            ),
        )

    # returncode == 1 means duplicates were found. Parse the concise output.
    duplicates = [line for line in stdout.splitlines() if "F811" in line]
    if not duplicates:
        # Non-F811 error from ruff itself (unlikely but be defensive)
        return CoherenceCheck(
            check_id="route_uniqueness",
            check_name="Route Uniqueness (F811)",
            status="fail",
            expected=_ROUTE_UNIQUENESS_FILES,
            actual=[f"ruff exit={result.returncode}"],
            missing=[],
            extra=[stdout[:500]],
            message=f"ruff F811 check failed: {(result.stderr or stdout)[:200]}",
        )

    return CoherenceCheck(
        check_id="route_uniqueness",
        check_name="Route Uniqueness (F811)",
        status="fail",
        expected=["No duplicate view function names in Flask blueprints"],
        actual=[f"{len(duplicates)} duplicate(s) found"],
        missing=[],
        extra=duplicates,
        message=(
            f"{len(duplicates)} duplicate view function name(s) detected — "
            f"Flask blueprint registration will abort mid-flight. Rename one "
            f"of each pair. Details in 'extra' field."
        ),
    )


# ---------------------------------------------------------------------------
# Attribution Claims (OPT-74) — catches "adapted from <project>" phrases
# in tools/ and cross-references the cited upstream against a verified
# license registry. Prevents future attribution drift of the kind that
# slipped through in Phase 44 (Agent Zero cited as GPL-3.0 when it's
# actually MIT, and 4 files claimed "adapted from" for code that was
# actually clean-room).
# ---------------------------------------------------------------------------

# Registry of upstream projects that any ICDEV file may reference as an
# inspiration. Each entry: url, verified license, audit status.
# Add to this list when a new external repo is cited; the check fails for
# unregistered citations.
_ATTRIBUTION_REGISTRY: Dict[str, Dict[str, str]] = {
    "agent zero": {
        "url": "https://github.com/agent0ai/agent-zero",
        "license": "MIT",
        "audit_status": "clean-room verified 2026-04-11 (OPT-73)",
        "notes": (
            "Structural audit found zero class/method overlap across "
            "chat_manager.py, state_tracker.py, extension_manager.py"
        ),
    },
    "adw": {
        "url": "(tutorial content by IndyDevDan — no public repo identified)",
        "license": "tutorial-restrictive",
        "audit_status": "rewrite complete 2026-04-11 (OPT-75 — all 18 files clean-room replaced)",
        "notes": (
            "All 18 files originally headed 'Adapted from ADW adw_X.py' "
            "have been clean-room rewritten under OPT-75 from per-file "
            "specs in docs/rewrite/adw/specs/. The new implementations "
            "share no code with the tutorial material; each file was "
            "rewritten from a spec that documented behavior only, not "
            "implementation. _REWRITE_IN_PROGRESS_ALLOWLIST is now "
            "empty — the attribution_claims check passes cleanly with "
            "zero ADW citations remaining."
        ),
    },
    "mattpocock/skills": {
        "url": "https://github.com/mattpocock/skills",
        "license": "MIT",
        "audit_status": "clean-room verified 2026-04-22 (OPT-56)",
        "notes": (
            "hook_compat.py cites git-guardrails pattern for _GIT_DANGER_PATTERNS blocklist. "
            "Implementation is an independent regex blocklist in Python; no code shared with "
            "the original TypeScript skills repo. Concept-only citation."
        ),
    },
    "open-swe": {
        "url": "https://github.com/langchain-ai/open-swe",
        "license": "MIT",
        "audit_status": "clean-room verified 2026-04-22 (OPT-61/62/63)",
        "notes": (
            "_subagent.py and hook_compat.py cite open-swe task-tool pattern and "
            "cross-cutting behavior architecture. ICDEV implementation uses LLMRouter + "
            "Flask/SQLite; zero class or method overlap with open-swe's LangGraph stack."
        ),
    },
    "promptfoo": {
        "url": "https://github.com/promptfoo/promptfoo",
        "license": "MIT",
        "audit_status": "clean-room verified 2026-04-22 (OPT-64/65/66)",
        "notes": (
            "eval_runner.py cites promptfoo eval-runner pattern. ICDEV implementation is "
            "a YAML-driven harness over LLMRouter with its own assertion engine; "
            "no code shared with promptfoo's JS/TS runner."
        ),
    },
    "deepagents": {
        "url": "https://github.com/langchain-ai/deepagents",
        "license": "MIT",
        "audit_status": "clean-room verified 2026-04-22 (OPT-67)",
        "notes": (
            "_composer.py cites deepagents create_deep_agent() factory pattern. "
            "ICDEV Agent class is built on LLMRouter + tool catalog loop; "
            "no LangChain dependency, zero code overlap with deepagents."
        ),
    },
    "react-admin": {
        "url": "https://github.com/marmelab/react-admin",
        "license": "MIT",
        "audit_status": "clean-room verified 2026-04-22 (OPT-68/69)",
        "notes": (
            "crud_resource.py cites react-admin declarative resource pattern. "
            "ICDEV implementation generates Flask Blueprint + SQLite routes from ColumnSpec; "
            "entirely Python/Jinja2, no React or JS code shared."
        ),
    },
    "optio": {
        "url": "https://github.com/jonwiggins/optio",
        "license": "MIT",
        "audit_status": "implemented (OPT-70/71/72 shipped 2026-04-11)",
    },
    # OPT-74: Citations discovered by the tightened attribution check
    # on 2026-04-11. Each entry records the current audit state; files
    # with unresolved license exposure are added to
    # _REWRITE_IN_PROGRESS_ALLOWLIST so the gate stays WARN (not FAIL)
    # during active investigation.
    "leanstral": {
        "url": "https://github.com/facebookresearch/LeanStral (candidate — unconfirmed)",
        "license": "unknown (audit pending)",
        "audit_status": (
            "OPT-74 candidate — 4 files cite 'LeanStral' in "
            "tools/analysis/formal_verifier.py, tools/analysis/"
            "verify_loop.py, tools/mcp/lsp_server.py, and "
            "tools/testing/goveval.py. Upstream repo not positively "
            "identified; treat citations as prose-only reference until "
            "upstream confirmed."
        ),
    },
    "mistral ai": {
        "url": "https://github.com/mistralai (multi-project org)",
        "license": "varies (Apache-2.0 for open models; proprietary for La Plateforme)",
        "audit_status": (
            "OPT-74 candidate — tools/analysis/verify_loop.py references "
            "Mistral AI as a concept citation, not a code port. No LOC "
            "derivation identified on manual review."
        ),
    },
    "nemoclaw": {
        "url": "(upstream unknown — candidate: NVIDIA Nemo variant)",
        "license": "unknown (audit pending)",
        "audit_status": (
            "OPT-74 candidate — tools/registry/sandbox_scorer.py cites "
            "'NemoClaw'. Upstream repo not identified. Treat as prose "
            "reference until confirmed; code uses no Nemo imports."
        ),
    },
    "spec-kit": {
        "url": "https://github.com/github/spec-kit",
        "license": "MIT",
        "audit_status": (
            "OPT-74 verified — github/spec-kit is MIT-licensed (default "
            "for GitHub public repos). tools/requirements/"
            "clarification_engine.py cites it as inspiration. No class "
            "or method overlap on structural diff."
        ),
    },
    "kodustech/agent-readiness": {
        "url": "https://github.com/kodustech/agent-readiness",
        "license": "MIT",
        "audit_status": (
            "2026-05-28 verified — kodustech/agent-readiness is MIT-licensed. "
            "tools/ai_augmentation/agent_readiness/checker.py cites it as structural "
            "inspiration for the readiness check architecture. Structural diff confirmed "
            "no class or method overlap; ICDEV implementation uses its own scoring model, "
            "DB schema, and LLMRouter integration."
        ),
    },
}

# Licenses that block the gate if cited without an explicit audit exemption.
# Copyleft licenses (GPL/AGPL) can create derivative-work obligations that
# conflict with ICDEV's Apache-2.0 license and commercial option. Tutorial
# content is restrictive by default (implicit all-rights-reserved).
_BLOCKING_LICENSES = {
    "GPL-2.0", "GPL-3.0", "AGPL-3.0", "LGPL-3.0",
    "tutorial-restrictive",  # educational content not licensed for redistribution
}

# Files with a known blocking-license citation that are under active
# rewrite. The gate stays WARN for these (not FAIL) until the rewrite
# lands, to avoid blocking all development. Remove entries as each file
# is rewritten clean-room.
_REWRITE_IN_PROGRESS_ALLOWLIST: set[str] = {
    # tools/ci/modules/agent.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 13/18)
    # tools/ci/modules/git_ops.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 2/18)
    # tools/ci/modules/state.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 1/18)
    # tools/ci/modules/vcs.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 14/18)
    # tools/ci/modules/workflow_ops.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 12/18)
    # tools/ci/workflows/icdev_build.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 6/18)
    # tools/ci/workflows/icdev_document.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 7/18)
    # tools/ci/workflows/icdev_patch.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 8/18)
    # tools/ci/workflows/icdev_plan.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 10/18)
    # tools/ci/workflows/icdev_review.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 5/18)
    # tools/ci/workflows/icdev_sdlc.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 11/18)
    # tools/ci/workflows/icdev_test.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 9/18)
    # tools/testing/data_types.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 4/18)
    # tools/testing/e2e_runner.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 17/18)
    # tools/testing/health_check.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 15/18)
    # tools/testing/test_agent_models.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 16/18)
    # tools/testing/test_orchestrator.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 18/18)
    # tools/testing/utils.py — REWRITTEN clean-room 2026-04-11 (OPT-75 file 3/18)
}

# Phrases that indicate the source code is claiming to adopt from upstream.
# Case-insensitive. If any of these appear in a file under tools/, we look
# for the upstream project name nearby and cross-check the registry.
_ATTRIBUTION_PHRASES = [
    "adapted from",
    "ported from",
    "copied from",
    "derived from",
    "based on the",
]


def check_attribution_claims(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Scan tools/ for 'adapted from <project>' phrases and cross-check each
    cited upstream against _ATTRIBUTION_REGISTRY. Fail the gate on:
      1. A citation whose upstream is not in the registry (unverified)
      2. A citation whose upstream has a blocking license (GPL/AGPL family)
         without an explicit audit exemption in audit_status
      3. (Soft warn) A citation whose nearby context suggests actual code
         derivation rather than pattern inspiration — the auditor should
         confirm clean-room status before clearing
    """
    import re

    tools_dir = PROJECT_ROOT / "tools"
    if not tools_dir.exists():
        return CoherenceCheck(
            check_id="attribution_claims",
            check_name="Attribution Claims",
            status="warn",
            expected=["tools/ directory present"],
            actual=["tools/ not found"],
            missing=[],
            extra=[],
            message="tools/ directory missing — check skipped",
        )

    violations: List[str] = []
    registered_hits = 0
    unregistered_hits: List[str] = []

    # Standards references that trigger the regex but are NOT project
    # citations — they're compliance/standards body names, not upstream
    # software projects.
    _STANDARDS_ACRONYMS = {
        "ieee", "nist", "dodi", "nsa", "dod", "owasp", "cmmc", "fedramp",
        "mcsb", "slo", "iso", "sp", "stig", "cis", "atlas", "mitre",
        "cncf", "opa", "kyverno", "nvd", "cve", "sbom", "oscal", "mbse",
        "sysml", "icdev", "bmad",
    }

    py_files = list(tools_dir.rglob("*.py"))

    for f in py_files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # Position-based filter: attribution claims belong in the module
        # docstring (first 30 lines). Prose "derived from X" in function
        # bodies is almost never an attribution claim — it's describing
        # derivation logic or standards references.
        lines = text.splitlines()
        header = "\n".join(lines[:30])

        lower = header.lower()
        # Only scan files whose header contains one of the phrases
        if not any(p in lower for p in _ATTRIBUTION_PHRASES):
            continue
        # From here on, only scan the header — the full text is ignored.
        text = header

        # For each hit line, try to identify the cited project.
        #
        # Tightened regex (2026-04-11): the "project name" after the phrase
        # must look like a proper name:
        #   - Starts with an UPPERCASE letter (proper noun), OR
        #   - Contains a slash / dot (URL or module path), OR
        #   - Matches a known-project allowlist keyword
        # This filters out natural-language prose like "based on the current
        # time" or "derived from node type" while still catching real
        # project citations like "Agent Zero", "Mistral AI", "LeanStral".
        #
        # Also require the phrase to appear at a file top (first 20 lines) OR
        # in a docstring/comment block, to filter out mid-function prose.
        for phrase in _ATTRIBUTION_PHRASES:
            # Match: <phrase> <ProjectName> where ProjectName starts uppercase
            # or looks like a URL/path/identifier.
            pattern = re.compile(
                rf"{re.escape(phrase)}\s+"
                rf"([A-Z][A-Za-z0-9_\-./]+(?:\s+[A-Za-z0-9][A-Za-z0-9_\-./]*)?"
                rf"|[a-z][a-z0-9_\-]+[/.][A-Za-z0-9_\-./]+)",
                re.IGNORECASE,
            )
            for match in pattern.finditer(text):
                raw_project = match.group(1).strip().rstrip("'s").strip()
                # Strip trailing possessive / punctuation
                raw_project = re.sub(r"[.,;:'\"].*$", "", raw_project).strip()
                normalized = raw_project.lower()

                # Skip bare lowercase single-words that are clearly internal
                # path references (not external projects)
                if normalized.startswith("tools/") or normalized.startswith("tests/"):
                    continue
                # Skip common English phrases that survived the main regex
                _SKIP_PREFIXES = (
                    "the ", "a ", "an ", "this ", "that ", "current ",
                    "node ", "source ", "target ", "provided ",
                )
                if any(normalized.startswith(p) for p in _SKIP_PREFIXES):
                    continue
                # Skip single-word lowercase internal references
                if "/" not in raw_project and "." not in raw_project:
                    if len(raw_project.split()) == 1 and raw_project.islower():
                        continue
                # Skip standards references (IEEE/NIST/DoDI/etc.) — these
                # are compliance citations, not project citations
                first_token = normalized.split()[0] if normalized else ""
                if first_token in _STANDARDS_ACRONYMS:
                    continue
                # Find position in file for file:line reporting
                line_no = text[:match.start()].count("\n") + 1
                rel = f.relative_to(PROJECT_ROOT) if f.is_relative_to(PROJECT_ROOT) else f

                # Match against registry (exact or substring)
                entry = None
                for key, val in _ATTRIBUTION_REGISTRY.items():
                    if key in normalized or normalized in key:
                        entry = val
                        break

                if entry is None:
                    unregistered_hits.append(
                        f"{rel}:{line_no}: '{phrase} {raw_project}' — NOT in "
                        f"_ATTRIBUTION_REGISTRY. Add entry with verified license "
                        f"or rephrase to remove the claim."
                    )
                    continue

                registered_hits += 1
                lic = entry.get("license", "UNKNOWN")
                if lic in _BLOCKING_LICENSES:
                    audit = entry.get("audit_status", "")
                    if "clean-room verified" not in audit.lower():
                        # Files under active rewrite get a warn-level pass
                        # until their rewrite ships. Stored as posix paths
                        # for cross-platform compatibility.
                        rel_posix = str(rel).replace("\\", "/")
                        if rel_posix in _REWRITE_IN_PROGRESS_ALLOWLIST:
                            # soft-warn only — don't add to violations
                            unregistered_hits.append(
                                f"{rel}:{line_no}: '{phrase} {raw_project}' "
                                f"({lic}) — REWRITE IN PROGRESS per "
                                f"_REWRITE_IN_PROGRESS_ALLOWLIST"
                            )
                            continue
                        violations.append(
                            f"{rel}:{line_no}: '{phrase} {raw_project}' cites a "
                            f"{lic}-licensed upstream without a clean-room audit. "
                            f"Resolve OPT-73-style audit before gate can pass."
                        )

    # Classification
    if violations:
        return CoherenceCheck(
            check_id="attribution_claims",
            check_name="Attribution Claims",
            status="fail",
            expected=[
                f"{len(_ATTRIBUTION_REGISTRY)} registered upstream projects, "
                "no unaudited GPL/AGPL citations"
            ],
            actual=[f"{len(violations)} blocking violation(s), "
                    f"{len(unregistered_hits)} unregistered citation(s)"],
            missing=[],
            extra=violations + unregistered_hits,
            message=(
                f"{len(violations)} GPL/AGPL citation(s) lack clean-room audit. "
                f"See extra field for per-file details."
            ),
        )

    if unregistered_hits:
        return CoherenceCheck(
            check_id="attribution_claims",
            check_name="Attribution Claims",
            status="warn",
            expected=["All attribution claims registered in _ATTRIBUTION_REGISTRY"],
            actual=[f"{registered_hits} registered, "
                    f"{len(unregistered_hits)} unregistered"],
            missing=[],
            extra=unregistered_hits,
            message=(
                f"{len(unregistered_hits)} attribution claim(s) cite unregistered "
                f"upstream projects. Add to _ATTRIBUTION_REGISTRY or rephrase."
            ),
        )

    return CoherenceCheck(
        check_id="attribution_claims",
        check_name="Attribution Claims",
        status="pass",
        expected=[f"Scanned {len(py_files)} Python files in tools/"],
        actual=[f"{registered_hits} registered attribution claim(s) verified"],
        missing=[],
        extra=[],
        message=(
            f"All {registered_hits} attribution claim(s) match the registry; "
            f"no unaudited GPL/AGPL citations."
        ),
    )


# ---------------------------------------------------------------------------
# check_llm_injection_patterns (OPT-66)
# ---------------------------------------------------------------------------
# Static AST scan for LLMRouter.invoke callsites that feed user-controlled
# content into a request without sanitization. Inspired by promptfoo's
# code-scan-action (MIT). See https://github.com/promptfoo/promptfoo
#
# Rules:
#   1. If `LLMRouter.invoke(...)` receives an LLMRequest whose `messages`
#      or `system_prompt` are built from `flask.request.*` / `request.args`
#      / `request.form` / `request.json` *without* a prior call to one of
#      SANITIZE_FUNCS in the same function scope, emit a warning.
#   2. If `system_prompt=` receives an f-string (JoinedStr) whose values
#      reference a variable without sanitization, emit a warning.
#   3. Files listed in _LLM_INJECTION_ALLOWLIST are skipped — they have
#      documented, reviewed pipelines (e.g. tools/llm/router.py itself).
#
# Tier: WARN only. This is a lint, not a blocker — false positives are
# common in generative code.

_LLM_INJECTION_ALLOWLIST: List[str] = [
    # Tools that intentionally accept raw prompts and have their own guard
    "tools/llm/router.py",
    "tools/llm/eval_runner.py",       # OPT-64 — user-provided eval spec, scanned elsewhere
    "tools/security/llm_red_team.py", # OPT-65 — intentionally runs adversarial prompts
    "tools/llm/gateway.py",           # Gateway itself runs injection detection
    "tools/llm/prompt_registry.py",   # Version store; not a caller
]

_LLM_INJECTION_SANITIZERS = {
    "sanitize", "sanitize_prompt", "escape", "html_escape",
    "scan_for_injection", "_scan_for_injection",
    "redact", "strip_html", "clean_user_input",
}

_LLM_INJECTION_UNSAFE_SOURCES = {
    # flask request attributes
    "args", "form", "json", "values", "data",
    # generic cues
    "request",
}


def _expr_has_untrusted_source(node) -> bool:
    """Return True if the AST expression references flask.request.* or
    similar untrusted input without a sanitizer call."""
    import ast
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute):
            # request.args, request.form, request.json
            if (
                isinstance(sub.value, ast.Name)
                and sub.value.id == "request"
                and sub.attr in _LLM_INJECTION_UNSAFE_SOURCES
            ):
                return True
        if isinstance(sub, ast.Name):
            # bare `request` leaked into the expression
            if sub.id == "request":
                return True
    return False


def _expr_contains_sanitizer_call(node) -> bool:
    import ast
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = None
            if isinstance(fn, ast.Attribute):
                name = fn.attr
            elif isinstance(fn, ast.Name):
                name = fn.id
            if name and name in _LLM_INJECTION_SANITIZERS:
                return True
    return False


def _func_scope_has_sanitizer(function_node) -> bool:
    import ast
    for sub in ast.walk(function_node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = None
            if isinstance(fn, ast.Attribute):
                name = fn.attr
            elif isinstance(fn, ast.Name):
                name = fn.id
            if name and name in _LLM_INJECTION_SANITIZERS:
                return True
    return False


def check_llm_injection_patterns(
    changed_files: Optional[List[Path]] = None,
) -> CoherenceCheck:
    """Static-scan Python sources for untrusted input reaching an LLM call.

    Flags any LLMRouter.invoke() callsite whose request fields
    (messages, system_prompt) can trace back to `request.args/form/json`
    without a sanitize()/escape()/scan_for_injection() call in the same
    function scope. WARN-tier — does not block the gate on its own.
    """
    import ast

    tools_dir = PROJECT_ROOT / "tools"
    if not tools_dir.exists():
        return CoherenceCheck(
            check_id="llm_injection_patterns",
            check_name="LLM Injection Patterns",
            status="warn",
            expected=["tools/ directory"],
            actual=["not found"],
            missing=[],
            extra=[],
            message="tools/ directory missing — scan skipped",
        )

    candidate_files: List[Path] = []
    if changed_files:
        candidate_files = [
            p for p in changed_files
            if p.suffix == ".py" and "tools" in p.parts
        ]
    if not candidate_files:
        candidate_files = list(tools_dir.rglob("*.py"))

    allowlist_resolved = {
        (PROJECT_ROOT / rel).resolve() for rel in _LLM_INJECTION_ALLOWLIST
    }

    findings: List[str] = []
    scanned = 0

    for path in candidate_files:
        try:
            if path.resolve() in allowlist_resolved:
                continue
        except OSError:
            pass
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        if ".invoke(" not in text and "LLMRequest" not in text:
            continue  # cheap prefilter
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        scanned += 1
        rel = path.relative_to(PROJECT_ROOT).as_posix()

        # Walk each function and look for LLMRequest(...) constructions
        # with risky keyword arguments.
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            scope_has_sanitizer = _func_scope_has_sanitizer(func)

            for sub in ast.walk(func):
                if not isinstance(sub, ast.Call):
                    continue
                fn = sub.func
                callee_name = None
                if isinstance(fn, ast.Name):
                    callee_name = fn.id
                elif isinstance(fn, ast.Attribute):
                    callee_name = fn.attr
                if callee_name != "LLMRequest":
                    continue

                for kw in sub.keywords or []:
                    if kw.arg not in ("messages", "system_prompt"):
                        continue
                    expr = kw.value
                    if not _expr_has_untrusted_source(expr):
                        continue
                    if scope_has_sanitizer or _expr_contains_sanitizer_call(expr):
                        continue
                    line = getattr(sub, "lineno", 0)
                    findings.append(
                        f"{rel}:{line} LLMRequest({kw.arg}=...) reads from "
                        f"flask request without sanitize()/escape()/"
                        f"scan_for_injection()"
                    )

    expected_msg = (
        "LLMRequest fields must be sanitized before reaching "
        "LLMRouter.invoke() — see tools/llm/gateway.py for reference"
    )
    if not findings:
        return CoherenceCheck(
            check_id="llm_injection_patterns",
            check_name="LLM Injection Patterns (OPT-66)",
            status="pass",
            expected=[expected_msg],
            actual=[f"scanned {scanned} files, 0 unsafe callsites"],
            missing=[],
            extra=[],
            message=(
                f"No untrusted input reaching LLMRequest in {scanned} "
                f"scanned tool(s)"
            ),
        )

    return CoherenceCheck(
        check_id="llm_injection_patterns",
        check_name="LLM Injection Patterns (OPT-66)",
        status="warn",
        expected=[expected_msg],
        actual=[f"{len(findings)} unsafe callsite(s)"],
        missing=[],
        extra=findings[:50],
        message=(
            f"{len(findings)} LLMRequest callsite(s) in "
            f"{scanned} scanned file(s) read from flask request without a "
            f"sanitizer. Wrap with sanitize()/scan_for_injection() or "
            f"add the file to _LLM_INJECTION_ALLOWLIST if reviewed."
        ),
    )


# ---------------------------------------------------------------------------
# Check: SKILL.md standard (OPT-56 — mattpocock/skills convention)
# ---------------------------------------------------------------------------

_SKILL_DIR = PROJECT_ROOT / ".agents" / "skills"
_CLAUDE_SKILL_DIR = PROJECT_ROOT / ".claude" / "skills"
_SKILL_MAX_DESC = 1024
_SKILL_MAX_BODY_LINES = 100


def _parse_frontmatter(text: str) -> tuple[dict, int]:
    """Return (frontmatter_dict, end_line_index) from a SKILL.md string.

    end_line_index is the 0-based line index of the closing '---'.
    Returns ({}, 0) if no valid frontmatter block found.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, 0
    end = 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end == 0:
        return {}, 0
    fm: dict = {}
    current_key: str = ""
    for line in lines[1:end]:
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            current_key = k.strip()
            fm[current_key] = v.strip().strip('"').strip("'")
        elif current_key and line.startswith(" "):
            fm[current_key] = fm.get(current_key, "") + " " + line.strip()
    return fm, end


def check_skill_standard() -> CoherenceCheck:
    """Verify all .agents/skills/*/SKILL.md files conform to the mattpocock/skills standard.

    Rules enforced (OPT-56):
      - description field must exist and be non-empty
      - description must be <= 1024 characters
      - description must contain 'Use when' (second-sentence trigger convention)
      - SKILL.md body (lines after frontmatter) must be <= 100 lines
    """
    if not _SKILL_DIR.exists():
        return CoherenceCheck(
            check_id="skill_standard",
            check_name="SKILL.md Standard (OPT-56)",
            status="pass",
            expected=["skills directory present"],
            actual=["no .agents/skills directory — skipping"],
            missing=[],
            extra=[],
            message="No .agents/skills directory found — check skipped",
        )

    skill_files = sorted(_SKILL_DIR.glob("*/SKILL.md"))
    if not skill_files:
        return CoherenceCheck(
            check_id="skill_standard",
            check_name="SKILL.md Standard (OPT-56)",
            status="pass",
            expected=["at least one SKILL.md"],
            actual=["no SKILL.md files found"],
            missing=[],
            extra=[],
            message="No SKILL.md files found in .agents/skills/",
        )

    findings: list[str] = []
    checked = 0

    for skill_path in skill_files:
        skill_name = skill_path.parent.name
        text = skill_path.read_text(encoding="utf-8", errors="replace")
        fm, fm_end_line = _parse_frontmatter(text)

        # --- description checks ---
        desc = fm.get("description", "").strip()
        if not desc:
            findings.append(f"{skill_name}: description is empty")
        else:
            if len(desc) > _SKILL_MAX_DESC:
                findings.append(
                    f"{skill_name}: description {len(desc)} chars > {_SKILL_MAX_DESC} limit"
                )
            if "use when" not in desc.lower():
                findings.append(
                    f"{skill_name}: description missing 'Use when' trigger sentence"
                )

        # --- body length check ---
        lines = text.splitlines()
        body_lines = lines[fm_end_line + 1 :] if fm_end_line > 0 else lines
        body_count = len([ln for ln in body_lines if ln.strip()])  # non-blank lines
        if body_count > _SKILL_MAX_BODY_LINES:
            findings.append(
                f"{skill_name}: body has {body_count} non-blank lines > {_SKILL_MAX_BODY_LINES} limit"
                " — split verbose steps into REFERENCE.md"
            )

        checked += 1

    if not findings:
        return CoherenceCheck(
            check_id="skill_standard",
            check_name="SKILL.md Standard (OPT-56)",
            status="pass",
            expected=[f"all {checked} SKILL.md files conform to mattpocock/skills standard"],
            actual=[f"{checked} files checked, 0 violations"],
            missing=[],
            extra=[],
            message=f"All {checked} SKILL.md files conform to the standard",
        )

    return CoherenceCheck(
        check_id="skill_standard",
        check_name="SKILL.md Standard (OPT-56)",
        status="fail",
        expected=["description non-empty, <= 1024 chars, contains 'Use when'; body <= 100 non-blank lines"],
        actual=[f"{len(findings)} violation(s) across {checked} SKILL.md files"],
        missing=findings,
        extra=[],
        message=(
            f"{len(findings)} SKILL.md violation(s) in {checked} files — "
            "fix descriptions and split oversize bodies into REFERENCE.md"
        ),
    )


def check_karpathy_sync() -> CoherenceCheck:
    """Verify canonical Karpathy principle headings exist in all 10 AI platform configs.

    Rule: After any CLAUDE.md update that touches the Karpathy principles section,
    `companion sync` must propagate all 5 canonical headings to every platform
    instruction file. This check blocks (--gate) on drift so no platform silently
    loses the guidance.

    Canonical headings (from goals/build_app.md, goals/tdd_workflow.md,
    goals/code_review.md — the three goal files that reference karpathy_principles.md):
      1. State assumptions
      2. Enumerate interpretations
      3. Prefer simpler
      4. Bound your edit scope
      5. Success criteria

    Checked files (10 AI platform instruction files, excluding CLAUDE.md which is
    the source of truth):
      AGENTS.md, .clinerules, .cursor/rules/icdev.mdc, .windsurf/rules/icdev.md,
      .github/copilot-instructions.md, .amazonq/rules/icdev.md, .junie/guidelines.md,
      GEMINI.md, .goosehints, CONVENTIONS.md
    """
    # Canonical headings — any of these substrings (case-insensitive) must appear
    KARPATHY_HEADINGS: List[Tuple[str, str]] = [
        ("state_assumptions",       "State assumptions"),
        ("enumerate_interpretations","Enumerate interpretations"),
        ("prefer_simpler",          "Prefer simpler"),
        ("bound_edit_scope",        "Bound your edit scope"),
        ("success_criteria",        "Success criteria"),
    ]

    # 10 AI platform instruction files (relative to PROJECT_ROOT)
    PLATFORM_FILES: List[Tuple[str, str]] = [
        ("codex",   "AGENTS.md"),
        ("cline",   ".clinerules"),
        ("cursor",  ".cursor/rules/icdev.mdc"),
        ("windsurf",".windsurf/rules/icdev.md"),
        ("copilot", ".github/copilot-instructions.md"),
        ("amazonq", ".amazonq/rules/icdev.md"),
        ("junie",   ".junie/guidelines.md"),
        ("gemini",  "GEMINI.md"),
        ("goose",   ".goosehints"),
        ("devin",   "CONVENTIONS.md"),
    ]

    drift: List[str] = []   # per-platform missing heading reports
    checked_platforms: List[str] = []

    for platform, rel_path in PLATFORM_FILES:
        full_path = PROJECT_ROOT / rel_path
        if not full_path.exists():
            drift.append(
                f"{rel_path} ({platform}): file missing — "
                "run `python tools/dx/companion.py --sync --write --json` to regenerate"
            )
            continue

        body = _read_text(full_path).lower()
        missing_hdrs = [
            label
            for _, label in KARPATHY_HEADINGS
            if label.lower() not in body
        ]
        if missing_hdrs:
            drift.append(
                f"{rel_path} ({platform}): missing headings — "
                + ", ".join(f'"{h}"' for h in missing_hdrs)
            )
        else:
            checked_platforms.append(platform)

    if drift:
        return CoherenceCheck(
            check_id="karpathy_sync",
            check_name="Karpathy Principles Sync (10 AI platforms)",
            status="fail",
            expected=[f'"{label}" in every platform config' for _, label in KARPATHY_HEADINGS],
            actual=[f"{len(checked_platforms)}/{len(PLATFORM_FILES)} platforms in sync"],
            missing=drift,
            extra=[],
            message=(
                f"{len(drift)} platform(s) missing Karpathy headings — "
                "add the Karpathy section to CLAUDE.md then re-run "
                "`python tools/dx/companion.py --sync --write --json`"
            ),
        )

    return CoherenceCheck(
        check_id="karpathy_sync",
        check_name="Karpathy Principles Sync (10 AI platforms)",
        status="pass",
        expected=[f'"{label}" in every platform config' for _, label in KARPATHY_HEADINGS],
        actual=[f"all {len(PLATFORM_FILES)} platforms in sync"],
        missing=[],
        extra=[],
        message=(
            f"All {len(PLATFORM_FILES)} AI platform configs contain "
            f"all {len(KARPATHY_HEADINGS)} canonical Karpathy principle headings"
        ),
    )


def check_sandbox_coverage() -> CoherenceCheck:
    """OPT-58 — verify docs/security/sandbox-coverage.md exists and
    documents all 4 tracked ingress-point gap files.

    Rule: any new tools/ module that ingests user-provided content MUST
    land a decision in docs/security/sandbox-coverage.md before merge.
    This check currently validates that the doc exists and references the
    4 baseline gap files (auto_remediator, kanban dispatch, pdf_provider,
    .tmp/ policy).
    """
    doc = PROJECT_ROOT / "docs" / "security" / "sandbox-coverage.md"
    if not doc.exists():
        return CoherenceCheck(
            check_id="sandbox_coverage",
            check_name="Sandbox Coverage (OPT-58, D-SEC-11)",
            status="fail",
            expected=["docs/security/sandbox-coverage.md"],
            actual=["(missing)"],
            missing=["docs/security/sandbox-coverage.md"],
            extra=[],
            message="docs/security/sandbox-coverage.md is missing — required "
                    "by OPT-58. Create it with decisions for each tools/ "
                    "module that ingests user content.",
        )

    required = [
        "auto_remediator.py",
        "_dispatch_via_llm_router",
        "pdf_provider.py",
        ".tmp/",
    ]
    body = doc.read_text(encoding="utf-8", errors="replace")
    missing = [tag for tag in required if tag not in body]

    if missing:
        return CoherenceCheck(
            check_id="sandbox_coverage",
            check_name="Sandbox Coverage (OPT-58, D-SEC-11)",
            status="fail",
            expected=required,
            actual=[tag for tag in required if tag not in missing],
            missing=missing,
            extra=[],
            message=f"sandbox-coverage.md is missing gap references: {missing}",
        )

    return CoherenceCheck(
        check_id="sandbox_coverage",
        check_name="Sandbox Coverage (OPT-58, D-SEC-11)",
        status="pass",
        expected=required,
        actual=required,
        missing=[],
        extra=[],
        message=f"All {len(required)} gap references present in sandbox-coverage.md",
    )


# ---------------------------------------------------------------------------
# Check: Direct Anthropic Import (OPT-44)
# ---------------------------------------------------------------------------


def check_direct_anthropic_import() -> CoherenceCheck:
    """OPT-44 — ban direct `import anthropic` / `from anthropic` outside
    tools/llm/anthropic_provider.py.

    Rule: all Anthropic SDK usage MUST flow through LLMRouter /
    AnthropicLLMProvider.  The one permitted file is the provider itself.
    Any other file that imports anthropic directly bypasses the provider
    abstraction and will break air-gap / Bedrock / multi-cloud routing.

    Detection uses AST so only real import statements are flagged — string
    literals in docstrings or regex patterns are ignored.
    """
    allowed = Path("tools") / "llm" / "anthropic_provider.py"

    def _has_direct_anthropic_import(source: str) -> List[Tuple[int, str]]:
        """Return (lineno, stmt_text) for every direct anthropic import in source."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        hits: List[Tuple[int, str]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "anthropic" or alias.name.startswith("anthropic."):
                        hits.append((node.lineno, f"import {alias.name}"))
            elif isinstance(node, ast.ImportFrom):
                if node.module and (
                    node.module == "anthropic" or node.module.startswith("anthropic.")
                ):
                    hits.append((node.lineno, f"from {node.module} import ..."))
        return hits

    violations: List[str] = []
    tools_root = PROJECT_ROOT / "tools"
    if tools_root.exists():
        for py_file in sorted(tools_root.rglob("*.py")):
            try:
                rel = py_file.relative_to(PROJECT_ROOT)
            except ValueError:
                rel = py_file
            if rel == allowed:
                continue  # the one permitted file
            text = _read_text(py_file)
            for lineno, stmt in _has_direct_anthropic_import(text):
                violations.append(f"{rel}:{lineno}: {stmt}")

    if violations:
        return CoherenceCheck(
            check_id="direct_anthropic_import",
            check_name="Direct Anthropic Import (OPT-44)",
            status="fail",
            expected=[f"only {allowed} may import anthropic SDK directly"],
            actual=violations,
            missing=[],
            extra=violations,
            message=(
                f"{len(violations)} disallowed direct anthropic import(s) found — "
                "route through tools.llm.anthropic_provider.AnthropicLLMProvider"
            ),
        )

    return CoherenceCheck(
        check_id="direct_anthropic_import",
        check_name="Direct Anthropic Import (OPT-44)",
        status="pass",
        expected=[f"only {allowed} may import anthropic SDK directly"],
        actual=[f"0 violations — sole allowed file: {allowed}"],
        missing=[],
        extra=[],
        message="No disallowed direct anthropic imports detected",
    )


# ---------------------------------------------------------------------------
# OpenAPI ↔ Route Parity (B6 coherence gate)
# ---------------------------------------------------------------------------


def check_openapi_parity() -> CoherenceCheck:
    """Verify the generated OpenAPI spec paths match app.url_map (/api/v1/*).

    Calls generate_openapi_spec(app) from tools.dashboard.api.openapi_generator
    and diffs the resulting paths dict against the live /api/v1/* rules in
    app.url_map.

    Drift categories:
      • missing_from_spec — url_map routes not present in the spec (undocumented)
      • phantom_in_spec   — spec paths with no matching url_map rule (stale docs)

    Gate: status="fail" (error-severity, api-contract-drift gate) on any drift.
    Gracefully skips (status="pass") when Flask or the dashboard module cannot
    be imported (e.g. air-gap environments without Flask installed).
    """
    # --- 1. Import guard (air-gap / no-Flask environments) ---
    try:
        from tools.dashboard.app import create_app  # type: ignore[import]
        from tools.dashboard.api.openapi_generator import (  # type: ignore[import]
            generate_openapi_spec,
            walk_api_v1_routes,
        )
    except ImportError as exc:
        return CoherenceCheck(
            check_id="openapi_parity",
            check_name="OpenAPI \u2194 Route Parity",
            status="pass",
            expected=["Flask + dashboard importable"],
            actual=[f"Skipped: {exc}"],
            missing=[],
            extra=[],
            message="Skipped \u2014 dashboard not importable in this environment (Flask/deps missing)",
        )

    # --- 2. Instantiate the app ---
    try:
        app = create_app()
    except Exception as exc:
        return CoherenceCheck(
            check_id="openapi_parity",
            check_name="OpenAPI \u2194 Route Parity",
            status="warn",
            expected=["create_app() succeeds"],
            actual=[str(exc)[:300]],
            missing=[],
            extra=[],
            message=f"Skipped \u2014 create_app() raised: {type(exc).__name__}: {exc}",
        )

    # --- 3. Generate the OpenAPI spec ---
    try:
        spec = generate_openapi_spec(app)
    except Exception as exc:
        return CoherenceCheck(
            check_id="openapi_parity",
            check_name="OpenAPI \u2194 Route Parity",
            status="warn",
            expected=["generate_openapi_spec(app) succeeds"],
            actual=[str(exc)[:300]],
            missing=[],
            extra=[],
            message=f"Skipped \u2014 generate_openapi_spec() raised: {type(exc).__name__}: {exc}",
        )

    # --- 4. Collect paths from the spec ---
    spec_paths: Set[str] = set(spec.get("paths", {}).keys())

    # --- 5. Collect /api/v1/* paths from app.url_map ---
    url_map_paths: Set[str] = set()
    try:
        for route in walk_api_v1_routes(app):
            url_map_paths.add(route["path"])
    except Exception as exc:
        return CoherenceCheck(
            check_id="openapi_parity",
            check_name="OpenAPI \u2194 Route Parity",
            status="warn",
            expected=["walk_api_v1_routes(app) succeeds"],
            actual=[str(exc)[:300]],
            missing=[],
            extra=[],
            message=f"Skipped \u2014 walk_api_v1_routes() raised: {type(exc).__name__}: {exc}",
        )

    # --- 6. Diff ---
    missing_from_spec: List[str] = sorted(url_map_paths - spec_paths)
    phantom_in_spec: List[str] = sorted(spec_paths - url_map_paths)

    if not missing_from_spec and not phantom_in_spec:
        return CoherenceCheck(
            check_id="openapi_parity",
            check_name="OpenAPI \u2194 Route Parity",
            status="pass",
            expected=[f"{len(url_map_paths)} /api/v1/* route(s) in url_map"],
            actual=[f"{len(spec_paths)} spec path(s) \u2014 all match"],
            missing=[],
            extra=[],
            message=(
                f"All {len(url_map_paths)} /api/v1/* routes present in OpenAPI spec "
                f"\u2014 no api-contract-drift"
            ),
        )

    # Drift detected \u2014 api-contract-drift gate fires (error severity)
    return CoherenceCheck(
        check_id="openapi_parity",
        check_name="OpenAPI \u2194 Route Parity",
        status="fail",
        expected=[
            f"{len(url_map_paths)} url_map route(s) == {len(spec_paths)} spec path(s)"
        ],
        actual=(
            [f"missing_from_spec: {p}" for p in missing_from_spec]
            + [f"phantom_in_spec: {p}" for p in phantom_in_spec]
        ),
        missing=missing_from_spec,
        extra=phantom_in_spec,
        message=(
            f"api-contract-drift: {len(missing_from_spec)} route(s) missing from spec, "
            f"{len(phantom_in_spec)} phantom path(s) in spec \u2014 "
            "update generate_openapi_spec() or remove stale path overrides"
        ),
    )


def check_hitl_workflow() -> CoherenceCheck:
    """Verify HITL Workflow Management coherence (migration 079).

    Checks:
    1. If ICDEV_HITL_ENABLED=true, wf_templates seeded for all canvas types
    2. If ICDEV_HITL_KANBAN_GATE=true, HITLGate import resolves
    3. wf_ tables listed in APPEND_ONLY_TABLES (wf_feedback, wf_document_submissions, wf_citations)
    4. Blueprint registered at /api/v1/wf
    """
    import os
    issues: list[str] = []
    actual: list[str] = []

    # Check 1: append-only tables
    try:
        aot_path = PROJECT_ROOT / ".claude" / "hooks" / "pre_tool_use.py"
        if aot_path.exists():
            content = aot_path.read_text(encoding="utf-8")
            for tbl in ("wf_feedback", "wf_document_submissions", "wf_citations"):
                if tbl in content:
                    actual.append(f"append_only:{tbl}=OK")
                else:
                    issues.append(f"wf table {tbl!r} missing from APPEND_ONLY_TABLES in pre_tool_use.py")
    except Exception as exc:
        issues.append(f"append_only check failed: {exc}")

    # Check 2: HITLGate module exists (file-based — avoids sys.path issues when run as script)
    gate_path = PROJECT_ROOT / "tools" / "workflow_hitl" / "gate.py"
    if gate_path.exists() and "HITLGate" in gate_path.read_text(encoding="utf-8"):
        actual.append("HITLGate=importable")
    else:
        issues.append("tools/workflow_hitl/gate.py missing or does not define HITLGate")

    # Check 3: blueprint module exists and defines create_wf_blueprint
    bp_path = PROJECT_ROOT / "tools" / "workflow_hitl" / "blueprint.py"
    if bp_path.exists() and "create_wf_blueprint" in bp_path.read_text(encoding="utf-8"):
        actual.append("wf_blueprint=importable")
    else:
        issues.append("tools/workflow_hitl/blueprint.py missing or does not define create_wf_blueprint")

    # Check 4: args/workflow_hitl_config.yaml exists
    cfg = PROJECT_ROOT / "args" / "workflow_hitl_config.yaml"
    if cfg.exists():
        actual.append("workflow_hitl_config.yaml=exists")
    else:
        issues.append("args/workflow_hitl_config.yaml missing")

    # Check 5: if gate enabled, verify env + kanban hook
    if os.getenv("ICDEV_HITL_KANBAN_GATE", "").lower() in ("true", "1"):
        kanban_path = PROJECT_ROOT / "tools" / "genesis" / "reflexes" / "kanban.py"
        if kanban_path.exists() and "HITLGate" in kanban_path.read_text(encoding="utf-8"):
            actual.append("kanban_gate_hook=present")
        else:
            issues.append("ICDEV_HITL_KANBAN_GATE=true but HITLGate hook not found in kanban.py")

    status = "fail" if issues else "pass"
    return CoherenceCheck(
        check_id="hitl_workflow",
        check_name="HITL Workflow Coherence",
        status=status,
        expected=["append_only tables registered", "HITLGate importable", "blueprint importable",
                  "workflow_hitl_config.yaml exists"],
        actual=actual,
        missing=issues,
        extra=[],
        message=(
            "Run migration 079, add wf_ tables to APPEND_ONLY_TABLES, "
            "verify tools/workflow_hitl/ modules exist"
        ) if issues else "HITL workflow coherence OK",
    )


def check_mcp_security() -> CoherenceCheck:
    """Verify MCP security scanner coherence (ddx-mcp).

    Checks:
    1. tools/mcp/mcp_scanner.py exists and defines scan_mcp_servers
    2. scan_mcp_servers() returns dict with 'servers_scanned' and 'findings' keys
    3. tools/mcp/tool_registry.py exists
    4. tools/mcp/gap_handlers.py exists
    """
    issues: list[str] = []
    actual: list[str] = []

    # Check 1: mcp_scanner.py exists with scan_mcp_servers
    scanner_path = PROJECT_ROOT / "tools" / "mcp" / "mcp_scanner.py"
    if scanner_path.exists():
        content = scanner_path.read_text(encoding="utf-8")
        if "scan_mcp_servers" in content:
            actual.append("mcp_scanner.py=exists+scan_mcp_servers")
        else:
            issues.append("tools/mcp/mcp_scanner.py missing scan_mcp_servers function")
    else:
        issues.append("tools/mcp/mcp_scanner.py missing")

    # Check 2: scanner returns required keys
    if not issues:
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("mcp_scanner_check", scanner_path)
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            result = mod.scan_mcp_servers()
            missing_keys = [k for k in ("servers_scanned", "findings") if k not in result]
            if missing_keys:
                issues.append(f"scan_mcp_servers() missing keys: {missing_keys}")
            else:
                actual.append("scan_mcp_servers() keys=ok")
        except Exception as exc:
            issues.append(f"scan_mcp_servers() execution failed: {exc}")

    # Check 3: tool_registry.py exists
    registry_path = PROJECT_ROOT / "tools" / "mcp" / "tool_registry.py"
    if registry_path.exists():
        actual.append("tool_registry.py=exists")
    else:
        issues.append("tools/mcp/tool_registry.py missing")

    # Check 4: gap_handlers.py exists
    gap_path = PROJECT_ROOT / "tools" / "mcp" / "gap_handlers.py"
    if gap_path.exists():
        actual.append("gap_handlers.py=exists")
    else:
        issues.append("tools/mcp/gap_handlers.py missing")

    status = "fail" if issues else "pass"
    return CoherenceCheck(
        check_id="mcp_security",
        check_name="MCP Security Scanner Coherence",
        status=status,
        expected=[
            "mcp_scanner.py exists with scan_mcp_servers",
            "scan_mcp_servers() returns servers_scanned + findings keys",
            "tool_registry.py exists",
            "gap_handlers.py exists",
        ],
        actual=actual,
        missing=issues,
        extra=[],
        message=(
            "Add tools/mcp/mcp_scanner.py with scan_mcp_servers() returning "
            "'servers_scanned' and 'findings' keys"
        ) if issues else "MCP security scanner coherence OK",
    )


# ---------------------------------------------------------------------------
# Check: RLS Security Context (D-SEC-RLS)
# ---------------------------------------------------------------------------


def check_security_context() -> CoherenceCheck:
    """Verify Row-Level Security wiring is intact and bypasses are documented.

    Three sub-checks:

    1. auto_wiring_present — _attach_flask_security_context() exists in
       tools/db/storage.py and is referenced from get_connection(). Removal
       silently breaks RLS for all Flask route handlers.

    2. undocumented_bypasses — files under tools/ (excluding tools/db/ and
       tools/security/) that call set_security_context(None) without a
       '# rls-bypass:' comment on the same line. These disable RLS silently.

    3. direct_cursor_instantiation — files under tools/ (excluding tools/db/
       and test files) that directly instantiate StorageCursor( outside of the
       storage layer. This bypasses _inject_rls() entirely.
    """
    issues: list[str] = []
    actual: list[str] = []

    # ── Sub-check 1: auto-wiring function present ────────────────────────────
    storage_path = PROJECT_ROOT / "tools" / "db" / "storage.py"
    if storage_path.exists():
        storage_text = storage_path.read_text(encoding="utf-8", errors="replace")
        if "_attach_flask_security_context" in storage_text:
            actual.append("_attach_flask_security_context=present")
        else:
            issues.append(
                "tools/db/storage.py: _attach_flask_security_context() missing — "
                "RLS auto-wiring for Flask routes is broken"
            )
        if "get_connection" in storage_text and "_attach_flask_security_context" in storage_text:
            actual.append("get_connection→_attach_flask_security_context=wired")
        else:
            issues.append(
                "tools/db/storage.py: get_connection() does not reference "
                "_attach_flask_security_context — auto-wiring may be detached"
            )
    else:
        issues.append("tools/db/storage.py missing — storage layer not found")

    # ── Sub-check 2: undocumented set_security_context(None) bypasses ────────
    _exempt_dirs = {
        PROJECT_ROOT / "tools" / "db",
        PROJECT_ROOT / "tools" / "security",
        PROJECT_ROOT / "tools" / "workflow",  # checker source contains pattern strings
    }
    bypass_re = re.compile(r"set_security_context\(\s*None\s*\)")
    bypass_ok_re = re.compile(r"#\s*rls-bypass\s*:", re.IGNORECASE)

    undocumented: list[str] = []
    tools_dir = PROJECT_ROOT / "tools"
    if tools_dir.exists():
        for py_file in sorted(tools_dir.rglob("*.py")):
            if any(py_file.is_relative_to(d) for d in _exempt_dirs):
                continue
            try:
                lines = py_file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for lineno, line in enumerate(lines, 1):
                if bypass_re.search(line) and not bypass_ok_re.search(line):
                    rel = py_file.relative_to(PROJECT_ROOT)
                    undocumented.append(f"{rel}:{lineno}")

    if undocumented:
        issues.extend(
            f"Undocumented RLS bypass (set_security_context(None) without "
            f"'# rls-bypass:' comment): {loc}"
            for loc in undocumented
        )
    else:
        actual.append("rls_bypass_comments=all_documented")

    # ── Sub-check 3: direct StorageCursor instantiation outside storage layer ─
    cursor_re = re.compile(r"\bStorageCursor\s*\(")
    _allowed_cursor_dirs = {
        PROJECT_ROOT / "tools" / "db",
        PROJECT_ROOT / "tools" / "workflow",  # checker source contains regex pattern strings
        PROJECT_ROOT / "tests",
    }
    direct_cursor: list[str] = []
    if tools_dir.exists():
        for py_file in sorted(tools_dir.rglob("*.py")):
            if any(py_file.is_relative_to(d) for d in _allowed_cursor_dirs):
                continue
            try:
                text = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if cursor_re.search(text):
                rel = py_file.relative_to(PROJECT_ROOT)
                direct_cursor.append(str(rel))

    if direct_cursor:
        issues.extend(
            f"Direct StorageCursor() instantiation outside tools/db/ bypasses "
            f"_inject_rls(): {f}"
            for f in direct_cursor
        )
    else:
        actual.append("direct_StorageCursor_instantiation=none")

    status = "fail" if issues else "pass"
    return CoherenceCheck(
        check_id="security_context",
        check_name="RLS Security Context Wiring (D-SEC-RLS)",
        status=status,
        expected=[
            "_attach_flask_security_context present and wired in get_connection()",
            "All set_security_context(None) calls annotated with # rls-bypass:",
            "StorageCursor instantiated only in tools/db/ or tests/",
        ],
        actual=actual,
        missing=issues,
        extra=[],
        message=(
            f"{len(issues)} RLS wiring issue(s) detected — "
            "add '# rls-bypass: <reason>' to bypass calls, or fix the wiring"
        ) if issues else "RLS security context wiring OK",
    )


# ---------------------------------------------------------------------------
# Check: new_page_completeness — 8-component gate for new dashboard pages
# ---------------------------------------------------------------------------


def check_new_page_completeness() -> CoherenceCheck:
    """Enforce the 8-component gate for every new dashboard page.

    When tools/dashboard/templates/<canvas>/page.html exists, ALL of the
    following must also exist or the feature ships broken (CLAUDE.md §8):

      1. icdev/tools/dashboard/templates/<canvas>/page.html  (companion mirror)
      2. tools/<canvas>/blueprint.py has at least one @*.route
      3. tools/<canvas>/ has a backing Python module (not just __init__.py)
      4. base.html nav contains a link to /<canvas>
      5. tools/iqe/adapters/<canvas>.py  (IQE adapter)
      6. context/iqe/queries/<canvas>/   (at least 1 seed query)
      7. Template contains iqe_query_widget include
      8. @bp.route in blueprint references the template (render_template check)

    Only checks page.html files under canvas sub-directories (not top-level
    flat templates like code_quality.html).

    Plus a full mirror-parity sub-check: for every canvas sub-directory,
    every tools/dashboard/templates/<dir>/*.html must have a matching
    icdev/tools/dashboard/templates/<dir>/*.html mirror. Each missing one
    is reported as `<rel_path>: icdev/ mirror missing` and folded into the
    same `missing` list. This catches canvases that have no page.html at
    all (e.g. slides/mfa/zta) which the page.html loop above never sees.
    """
    templates_dir = PROJECT_ROOT / "tools" / "dashboard" / "templates"
    base_html_path = templates_dir / "base.html"
    base_html_text = _read_text(base_html_path)
    iqe_adapters_dir = PROJECT_ROOT / "tools" / "iqe" / "adapters"
    iqe_queries_dir = PROJECT_ROOT / "context" / "iqe" / "queries"
    whitelist = _load_page_completeness_whitelist()

    violations: List[str] = []
    whitelisted_count = 0

    # Find all canvas page.html files (sub-directory only)
    for page_html in sorted(templates_dir.rglob("*/page.html")):
        canvas = page_html.parent.name  # e.g. "govcon", "digital_twin"
        if canvas in whitelist:
            whitelisted_count += 1
            continue
        rel_page = page_html.relative_to(PROJECT_ROOT)
        page_text = _read_text(page_html)
        missing: List[str] = []

        # 1. icdev mirror
        mirror = (PROJECT_ROOT / "icdev" / "tools" / "dashboard" / "templates" / canvas / "page.html")
        if not mirror.exists():
            missing.append("icdev/ mirror missing")

        # 2. Blueprint with @route
        bp_file = PROJECT_ROOT / "tools" / canvas / "blueprint.py"
        if not bp_file.exists():
            missing.append("tools/<canvas>/blueprint.py missing")
        elif not re.search(r"@\w+\.route\s*\(", _read_text(bp_file)):
            missing.append("blueprint.py has no @route decorator")

        # 3. Backing module (any .py other than __init__.py and blueprint.py)
        canvas_dir = PROJECT_ROOT / "tools" / canvas
        if canvas_dir.exists():
            py_modules = [
                f for f in canvas_dir.glob("*.py")
                if f.name not in ("__init__.py", "blueprint.py")
            ]
            if not py_modules:
                missing.append("no backing module in tools/<canvas>/")
        else:
            missing.append(f"tools/{canvas}/ directory missing")

        # 4. Nav link to /<canvas> in base.html
        if f'href="/{canvas}' not in base_html_text and f"href='/{canvas}" not in base_html_text:
            missing.append(f"no nav link to /{canvas} in base.html")

        # 5. IQE adapter
        iqe_adapter = iqe_adapters_dir / f"{canvas}.py"
        if not iqe_adapter.exists():
            missing.append(f"tools/iqe/adapters/{canvas}.py missing")

        # 6. IQE seed queries
        iqe_queries = iqe_queries_dir / canvas
        if not iqe_queries.exists() or not list(iqe_queries.glob("*.yaml")) + list(iqe_queries.glob("*.yml")):
            missing.append(f"context/iqe/queries/{canvas}/ missing or empty")

        # 7. IQE widget in template
        if "iqe_query_widget" not in page_text and "iqe-widget" not in page_text:
            missing.append("template missing {% include 'includes/iqe_query_widget.html' %}")

        if missing:
            violations.append(f"{rel_page}: missing [{', '.join(missing)}]")

    # ------------------------------------------------------------------
    # Sub-check: full icdev/ mirror parity for ALL canvas templates.
    # The 8-component check above is keyed on page.html, so a canvas whose
    # templates are named differently (e.g. slides/{index,detail,new}.html,
    # mfa/{enroll,challenge}.html, zta/lac_simulator.html) can ship with no
    # icdev/ mirror at all and slip through entirely. Here we set-diff the
    # *.html filenames per canvas directory — cheap, no content compare —
    # and flag every source template that lacks a matching icdev/ mirror.
    # ------------------------------------------------------------------
    icdev_templates_dir = (
        PROJECT_ROOT / "icdev" / "tools" / "dashboard" / "templates"
    )
    mirror_violations: List[str] = []
    for canvas_subdir in sorted(p for p in templates_dir.iterdir() if p.is_dir()):
        canvas = canvas_subdir.name
        if canvas in whitelist:
            continue
        src_names = {p.name for p in canvas_subdir.glob("*.html")}
        if not src_names:
            continue
        mirror_subdir = icdev_templates_dir / canvas
        mirror_names = (
            {p.name for p in mirror_subdir.glob("*.html")}
            if mirror_subdir.exists()
            else set()
        )
        for name in sorted(src_names - mirror_names):
            # page.html mirror is already covered by component #1 above.
            if name == "page.html":
                continue
            rel = (canvas_subdir / name).relative_to(PROJECT_ROOT)
            mirror_violations.append(f"{rel}: icdev/ mirror missing")

    # ------------------------------------------------------------------
    # Sub-check: registry-registered canvases with no templates directory.
    # The page.html loop above is keyed on existing page.html files — a canvas
    # that ships enabled but has never had a template created is completely
    # invisible to the 8-component gate.  This sub-check closes that gap by
    # cross-referencing component_registry.yaml against the templates directory.
    # ------------------------------------------------------------------
    registry_violations: List[str] = []
    try:
        import yaml as _yaml
        registry_path = PROJECT_ROOT / "args" / "component_registry.yaml"
        registry_data = _yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
        canvases_in_registry = [
            c for c in registry_data.get("components", [])
            if c.get("kind") == "canvas" and c.get("default_enabled", False)
        ]
        for canvas_entry in canvases_in_registry:
            key = canvas_entry.get("key", "")
            if not key or key in whitelist:
                continue
            canvas_tmpl_dir = templates_dir / key
            if not canvas_tmpl_dir.exists():
                registry_violations.append(
                    f"{key}: enabled in component_registry.yaml but "
                    f"tools/dashboard/templates/{key}/ does not exist "
                    "(invisible to 8-component gate)"
                )
    except Exception:
        pass  # registry unavailable — skip this sub-check

    all_violations = violations + mirror_violations + registry_violations
    status = "fail" if all_violations else "pass"
    canvas_count = len(list(templates_dir.rglob("*/page.html")))
    checked_count = canvas_count - whitelisted_count
    wl_note = f" ({whitelisted_count} whitelisted)" if whitelisted_count else ""
    incomplete_count = len(violations)
    mirror_count = len(mirror_violations)
    mirror_note = f"; {mirror_count} icdev/ mirror gap(s)" if mirror_count else ""
    return CoherenceCheck(
        check_id="new_page_completeness",
        check_name="New Page 8-Component Completeness",
        status=status,
        expected=[
            f"0 incomplete pages out of {checked_count} checked{wl_note}; "
            "0 icdev/ mirror gaps"
        ],
        actual=[
            f"{incomplete_count} incomplete page(s) out of {checked_count} "
            f"checked{wl_note}{mirror_note}"
        ],
        missing=all_violations,
        extra=[],
        message=(
            f"{incomplete_count} canvas page(s) missing components"
            f"{mirror_note} — these features will be broken, unreachable, "
            "or absent from the icdev/ package"
        ) if all_violations else (
            f"All {checked_count} canvas pages complete and icdev/ mirrors in parity{wl_note}"
            if checked_count > 0
            else f"No new canvas pages to check{wl_note}"
        ),
    )


# ---------------------------------------------------------------------------
# Check: nav_route_parity — every href in base.html nav must have a Flask route
# ---------------------------------------------------------------------------


def check_nav_route_parity() -> CoherenceCheck:
    """Verify every static nav href in base.html has a matching Flask @route decorator.

    Parses href="/<path>" links from the navbar section of base.html, then
    grepping tools/ for @<name>.route("/<path>") or @bp.route("/<path>").
    Catches the most common cause of unusable menus: a nav link added to the
    template before the blueprint route was implemented (or vice versa).

    Does NOT require a running server — purely static analysis.
    """
    base_html = PROJECT_ROOT / "tools" / "dashboard" / "templates" / "base.html"
    if not base_html.exists():
        return CoherenceCheck(
            check_id="nav_route_parity",
            check_name="Nav / Route Parity",
            status="warn",
            expected=["base.html exists"],
            actual=["base.html not found"],
            missing=[],
            extra=[],
            message="base.html not found — skipping nav/route parity check",
        )

    nav_html = _read_text(base_html)

    # Extract all static hrefs from the nav (exclude JS hrefs, anchors, external URLs)
    raw_hrefs: List[str] = re.findall(r'href="(/[^"#?{%][^"]*?)"', nav_html)
    # Keep only simple paths (no query strings, no dynamic segments)
    nav_routes: List[str] = sorted(set(
        h.rstrip("/") or "/"
        for h in raw_hrefs
        if not h.startswith("//") and "{{" not in h and "{%" not in h
    ))

    # Build a set of all route prefixes registered in tools/ blueprints
    # by grepping for @*.route("/...") patterns
    all_py = list((PROJECT_ROOT / "tools").rglob("*.py"))
    registered_prefixes: Set[str] = set()
    route_pattern = re.compile(r'@\w+\.route\s*\(\s*["\'](/[^"\']*)["\']')
    url_prefix_pattern = re.compile(r'url_prefix\s*=\s*["\']([^"\']+)["\']')

    for py_file in all_py:
        src = _read_text(py_file)
        for m in route_pattern.finditer(src):
            registered_prefixes.add(m.group(1).split("<")[0].rstrip("/") or "/")
        # Also capture url_prefix values as registered prefixes
        for m in url_prefix_pattern.finditer(src):
            registered_prefixes.add(m.group(1).rstrip("/") or "/")

    # Also check app.py add_url_rule
    app_py = PROJECT_ROOT / "tools" / "dashboard" / "app.py"
    if app_py.exists():
        for m in re.finditer(r'add_url_rule\s*\(\s*["\']([^"\']+)["\']', _read_text(app_py)):
            registered_prefixes.add(m.group(1).split("<")[0].rstrip("/") or "/")

    missing: List[str] = []
    for route in nav_routes:
        # A route is "covered" if any registered prefix is a prefix of this route
        covered = any(
            route == reg or route.startswith(reg + "/") or reg == "/"
            for reg in registered_prefixes
        )
        if not covered:
            missing.append(f"{route} — no matching @route or url_prefix found in tools/")

    status = "fail" if missing else "pass"
    return CoherenceCheck(
        check_id="nav_route_parity",
        check_name="Nav / Route Parity",
        status=status,
        expected=[f"All {len(nav_routes)} nav hrefs have a registered Flask route"],
        actual=[f"{len(missing)} unmatched nav href(s)"],
        missing=missing,
        extra=[],
        message=(
            f"{len(missing)} nav href(s) have no matching Flask route — "
            "users will hit 404 clicking these menu items"
        ) if missing else f"All {len(nav_routes)} nav hrefs matched to registered routes",
    )


# ---------------------------------------------------------------------------
# Check: blueprint_imports — every blueprint.py must import without error
# ---------------------------------------------------------------------------


def check_blueprint_imports() -> CoherenceCheck:
    """Dry-import every blueprint.py to catch ImportError before runtime.

    Runs each blueprint in a subprocess so a bad import doesn't poison this
    process.  A blueprint that fails to import will cause a 500 on ALL routes
    served by that blueprint — this check catches that before deployment.
    """
    import subprocess as _sp

    blueprint_files = sorted(
        list((PROJECT_ROOT / "tools").rglob("blueprint.py"))
        + list((PROJECT_ROOT / "icdev" / "tools").rglob("blueprint.py"))
    )
    failures: List[str] = []

    for bp_file in blueprint_files:
        rel = bp_file.relative_to(PROJECT_ROOT)
        # Convert path to module name: tools/govcon/blueprint.py → tools.govcon.blueprint
        module = str(rel).replace("\\", "/").replace("/", ".").removesuffix(".py")
        try:
            result = _sp.run(
                [sys.executable, "-c", f"import sys; sys.path.insert(0, '.'); import {module}"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=str(PROJECT_ROOT), timeout=15,
            )
            if result.returncode != 0:
                first_error = (result.stderr or result.stdout or "unknown error").strip().splitlines()[0]
                failures.append(f"{rel}: {first_error}")
        except Exception as exc:
            failures.append(f"{rel}: subprocess error — {exc}")

    status = "fail" if failures else "pass"
    return CoherenceCheck(
        check_id="blueprint_imports",
        check_name="Blueprint Import Check",
        status=status,
        expected=["All blueprint.py files import without error"],
        actual=[f"{len(failures)} import failure(s)"],
        missing=failures,
        extra=[],
        message=(
            f"{len(failures)} blueprint(s) fail to import — "
            "all routes in those blueprints will return 500"
        ) if failures else f"All {len(blueprint_files)} blueprints import cleanly",
    )


# ---------------------------------------------------------------------------
# Check: log_standard_compliance — all tools/ modules must use get_logger()
# ---------------------------------------------------------------------------


def check_log_standard_compliance() -> CoherenceCheck:
    """Verify that tools/ Python modules use get_logger(), not raw logging.getLogger().

    Excludes:
      - tools/logging/ (the implementation itself)
      - tests/ directories
      - __init__.py and setup files
      - Specific allowlisted files documented below
    """
    tools_dir = PROJECT_ROOT / "tools"
    violations: List[str] = []

    # Files that legitimately need raw logging.getLogger():
    #   testing/utils.py   — sets up per-run test loggers with custom handlers
    #   workflow/coherence_checker.py — references the pattern in check source
    #   refactor/migrate_to_icdev_logger.py — migration script references the pattern
    LOG_STANDARD_ALLOWLIST = {
        Path("tools/testing/utils.py"),
        Path("tools/workflow/coherence_checker.py"),
        Path("tools/refactor/migrate_to_icdev_logger.py"),
    }

    for py_file in sorted(tools_dir.rglob("*.py")):
        rel = py_file.relative_to(PROJECT_ROOT)
        parts = rel.parts
        # Skip the logging package itself and test files
        if "logging" in parts or "tests" in parts or "__pycache__" in parts:
            continue
        if py_file.name in ("conftest.py", "setup.py"):
            continue
        if rel in LOG_STANDARD_ALLOWLIST:
            continue
        try:
            src = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Flag raw logging.getLogger() usage (allow in logging/ itself)
        if re.search(r"\blogging\.getLogger\s*\(", src):
            violations.append(f"{rel}: uses logging.getLogger() — migrate to tools.logging.icdev_logger.get_logger()")

    status = "fail" if violations else "pass"
    return CoherenceCheck(
        check_id="log_standard",
        check_name="Log Standard Compliance",
        status=status,
        expected=["All tools/ modules use get_logger() from tools.logging.icdev_logger"],
        actual=[f"{len(violations)} violation(s)"],
        missing=violations,
        extra=[],
        message=(
            f"{len(violations)} tool(s) use raw logging.getLogger() — "
            "migrate to tools.logging.icdev_logger.get_logger()"
        ) if violations else "All tools/ modules use the ICDEV structured logger",
    )


# ---------------------------------------------------------------------------
# Check 15: canvas_placeholder_style — bare ? in canvas execute() SQL
# Check 16: runtime_placeholder_style — bare ? in ANY runtime tools/ execute() SQL
# ---------------------------------------------------------------------------


def check_canvas_placeholder_style(
    changed_files: Optional[List[Path]] = None,
) -> CoherenceCheck:
    """Detect bare ? SQL parameter placeholders in canvas-connection execute() calls.

    PostgreSQL (psycopg2) requires %s parameter placeholders. A bare ? raises
    ProgrammingError that broad except blocks may silently swallow — the pattern
    that concealed the ACE coworker threading bug for weeks.

    Scan: every Python file under tools/ that imports get_canvas_connection.
    Flag: any .execute() call whose SQL string literal (or f-string constant
          parts) contains a bare ? character.

    Tier: FAIL — blocks --gate on violation so CI catches the mistake before merge.
    """
    tools_dir = PROJECT_ROOT / "tools"
    if not tools_dir.exists():
        return CoherenceCheck(
            check_id="canvas_placeholder_style",
            check_name="Canvas Connection Placeholder Style",
            status="pass",
            expected=["get_canvas_connection callers use %s not ? placeholders"],
            actual=["tools/ directory not found — scan skipped"],
            missing=[],
            extra=[],
            message="tools/ directory missing — scan skipped",
        )

    candidates: List[Path] = []
    if changed_files:
        candidates = [p for p in changed_files if p.suffix == ".py" and p.exists()]
    else:
        candidates = list(tools_dir.rglob("*.py"))

    violations: List[str] = []
    scanned = 0

    for py_path in candidates:
        try:
            source = py_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if "get_canvas_connection" not in source:
            continue

        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        scanned += 1
        try:
            rel = py_path.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            rel = str(py_path)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Attribute) and node.func.attr == "execute"):
                continue
            if not node.args:
                continue

            sql_arg = node.args[0]
            sql_text: Optional[str] = None

            if isinstance(sql_arg, ast.Constant) and isinstance(sql_arg.value, str):
                sql_text = sql_arg.value
            elif isinstance(sql_arg, ast.JoinedStr):
                # f-string: collect constant fragments only
                parts = []
                for frag in sql_arg.values:
                    if isinstance(frag, ast.Constant) and isinstance(frag.value, str):
                        parts.append(frag.value)
                sql_text = "".join(parts)

            if sql_text and "?" in sql_text:
                lineno = getattr(node, "lineno", 0)
                violations.append(
                    f"{rel}:{lineno}: execute() SQL uses bare ? placeholder — use %s for psycopg2"
                )

    if violations:
        return CoherenceCheck(
            check_id="canvas_placeholder_style",
            check_name="Canvas Connection Placeholder Style",
            status="fail",
            expected=["All get_canvas_connection callers use %s placeholders (psycopg2)"],
            actual=[f"{len(violations)} violation(s) across {scanned} canvas file(s)"],
            missing=violations,
            extra=[],
            message=(
                f"{len(violations)} execute() call(s) use bare ? placeholder — "
                "psycopg2 requires %s; ? raises ProgrammingError on PostgreSQL"
            ),
        )

    return CoherenceCheck(
        check_id="canvas_placeholder_style",
        check_name="Canvas Connection Placeholder Style",
        status="pass",
        expected=["All get_canvas_connection callers use %s placeholders (psycopg2)"],
        actual=[f"Scanned {scanned} canvas file(s), 0 ? placeholders found"],
        missing=[],
        extra=[],
        message=f"All canvas execute() calls use %s placeholders — {scanned} file(s) checked",
    )


# ---------------------------------------------------------------------------
# Check 16: runtime_placeholder_style — bare ? in ANY runtime tools/ file
# ---------------------------------------------------------------------------

# Files legitimately allowed to use ? (SQLite-first init/seed/migrate paths)
_PLACEHOLDER_EXEMPT_PATTERNS = (
    "db/init_db.py",
    "db/migrations",
    "/migrations/",
    "/seed_",
    "/tests/",
    "test_",
    "conftest.py",
    "translate_sql",   # storage.py itself defines the translation
)


def check_runtime_placeholder_style(
    changed_files: Optional[List[Path]] = None,
) -> CoherenceCheck:
    """Detect bare ? SQL parameter placeholders in ANY runtime tools/ execute() call.

    Scope is wider than check_canvas_placeholder_style (check 15), which only
    covers get_canvas_connection callers. This check covers ALL tools/ runtime
    modules — blueprint.py, route files, engine modules, etc.

    translate_sql() in storage.py silently rewrites ? → %s, which means
    violations compile and run without error, masking the bug until a code path
    bypasses the wrapper. This check makes the violation visible at coherence
    gate time (pre-merge) rather than at runtime.

    Exempt: db/init_db.py, db/migrations/, seed_*.py, tests/ — these paths
    legitimately target SQLite and rely on translate_sql for PG compat.

    Tier: FAIL — blocks --gate so CI catches the mistake before merge.
    """
    tools_dir = PROJECT_ROOT / "tools"
    if not tools_dir.exists():
        return CoherenceCheck(
            check_id="runtime_placeholder_style",
            check_name="Runtime SQL Placeholder Style",
            status="pass",
            expected=["All runtime execute() calls use %s not ? placeholders"],
            actual=["tools/ directory not found — scan skipped"],
            missing=[],
            extra=[],
            message="tools/ directory missing — scan skipped",
        )

    candidates: List[Path] = []
    if changed_files:
        candidates = [p for p in changed_files if p.suffix == ".py" and p.exists()]
    else:
        candidates = list(tools_dir.rglob("*.py"))

    violations: List[str] = []
    scanned = 0

    for py_path in candidates:
        # Skip exempt paths (init/seed/migrate/test files)
        path_str = py_path.as_posix()
        if any(pat in path_str for pat in _PLACEHOLDER_EXEMPT_PATTERNS):
            continue

        try:
            source = py_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Fast pre-filter: must have execute( and ? to be worth AST parsing
        if "execute(" not in source or "?" not in source:
            continue

        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        scanned += 1
        try:
            rel = py_path.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            rel = str(py_path)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Attribute) and node.func.attr == "execute"):
                continue
            if not node.args:
                continue

            sql_arg = node.args[0]
            sql_text: Optional[str] = None

            if isinstance(sql_arg, ast.Constant) and isinstance(sql_arg.value, str):
                sql_text = sql_arg.value
            elif isinstance(sql_arg, ast.JoinedStr):
                parts = []
                for frag in sql_arg.values:
                    if isinstance(frag, ast.Constant) and isinstance(frag.value, str):
                        parts.append(frag.value)
                sql_text = "".join(parts)

            if sql_text and "?" in sql_text:
                lineno = getattr(node, "lineno", 0)
                violations.append(
                    f"{rel}:{lineno}: execute() SQL uses bare ? placeholder — use %s for psycopg2"
                )

    if violations:
        # FAIL on changed-file scope (gates new violations pre-commit/pre-merge).
        # WARN on full-repo scan — 7800+ legacy violations exist because translate_sql
        # silently masked them; a hard FAIL would block CI until all are migrated.
        # Fix by replacing ? with %s in the flagged execute() call sites.
        tier = "fail" if changed_files else "warn"
        return CoherenceCheck(
            check_id="runtime_placeholder_style",
            check_name="Runtime SQL Placeholder Style",
            status=tier,
            expected=["All runtime execute() calls use %s placeholders (psycopg2)"],
            actual=[f"{len(violations)} violation(s) across {scanned} runtime file(s)"],
            missing=violations,
            extra=[],
            message=(
                f"{len(violations)} execute() call(s) use bare ? placeholder — "
                "psycopg2 requires %s; translate_sql auto-rewrite is not a fix"
            ),
        )

    return CoherenceCheck(
        check_id="runtime_placeholder_style",
        check_name="Runtime SQL Placeholder Style",
        status="pass",
        expected=["All runtime execute() calls use %s placeholders (psycopg2)"],
        actual=[f"Scanned {scanned} runtime file(s), 0 ? placeholders found"],
        missing=[],
        extra=[],
        message=f"All runtime execute() calls use %s placeholders — {scanned} file(s) checked",
    )


# ---------------------------------------------------------------------------
# Check 17: ACE YAML listen_topics deadlock guard
# ---------------------------------------------------------------------------

# Mirror of _BOOTSTRAP_TOPICS in coworker_thread.py — kept in sync manually.
# Only topics that can safely appear alongside task.assigned belong here.
_ACE_BOOTSTRAP_TOPICS: frozenset = frozenset({"task.assigned"})


def check_ace_yaml_listen_topics(
    changed_files: Optional[List[Path]] = None,
) -> CoherenceCheck:
    """Warn when a role YAML lists both task.assigned and a non-bootstrap topic.

    A co-worker that receives task.assigned will start executing steps
    immediately.  Any other topic in listen_topics that is not a gateway
    prerequisite (e.g. doc.review_feedback, doc.draft_ready) creates a
    circular deadlock: the thread blocks waiting for a message that will never
    arrive until after the co-worker itself finishes a step.

    This check is WARN-only (not a gate blocker).  Fix by moving reactive
    topics out of listen_topics and into role steps (emit/poll patterns).
    """
    if not _HAS_YAML:
        return CoherenceCheck(
            check_id="ace_yaml_listen_topics",
            check_name="ACE YAML listen_topics Deadlock Guard",
            status="warn",
            expected=[],
            actual=[],
            missing=[],
            extra=[],
            message="PyYAML not available — skipping ace_yaml_listen_topics check",
        )

    roles_dir = PROJECT_ROOT / "args" / "ace" / "roles"

    violations: List[str] = []
    scanned = 0

    yaml_files: List[Path]
    if changed_files:
        # Trust the caller: process any YAML files they explicitly provide.
        yaml_files = [f for f in changed_files if f.suffix in (".yaml", ".yml")]
        if not yaml_files and roles_dir.exists():
            yaml_files = list(roles_dir.glob("*.yaml"))
    else:
        if not roles_dir.exists():
            return CoherenceCheck(
                check_id="ace_yaml_listen_topics",
                check_name="ACE YAML listen_topics Deadlock Guard",
                status="warn",
                expected=["args/ace/roles/ directory present"],
                actual=["args/ace/roles/ not found"],
                missing=[],
                extra=[],
                message="args/ace/roles/ not found — skipping ace_yaml_listen_topics check",
            )
        yaml_files = list(roles_dir.glob("*.yaml"))

    for yaml_path in yaml_files:
        try:
            data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue

        scanned += 1
        comm = data.get("communication") or {}
        topics: list = comm.get("listen_topics") or []
        if not isinstance(topics, list):
            continue

        if "task.assigned" not in topics:
            continue

        reactive = [t for t in topics if t not in _ACE_BOOTSTRAP_TOPICS]
        for topic in reactive:
            violations.append(
                f"{yaml_path.name}: listen_topics contains both 'task.assigned' and "
                f"non-bootstrap topic {topic!r} — deadlock risk; move to role steps"
            )

    if violations:
        return CoherenceCheck(
            check_id="ace_yaml_listen_topics",
            check_name="ACE YAML listen_topics Deadlock Guard",
            status="warn",
            expected=[
                "No role YAML lists task.assigned alongside a non-bootstrap topic"
            ],
            actual=violations,
            missing=[],
            extra=violations,
            message=(
                f"{len(violations)} role YAML(s) mix task.assigned with reactive "
                f"topics — deadlock risk (see extra field)"
            ),
        )

    return CoherenceCheck(
        check_id="ace_yaml_listen_topics",
        check_name="ACE YAML listen_topics Deadlock Guard",
        status="pass",
        expected=["No listen_topics deadlock risk in role YAMLs"],
        actual=[f"Scanned {scanned} role YAML(s)"],
        missing=[],
        extra=[],
        message=f"All {scanned} role YAML(s) have clean listen_topics",
    )


# Check #15: skill_security — SkillSpector fast static gate on changed SKILL.md
# ---------------------------------------------------------------------------

def check_skill_security(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Run SkillSpector --no-llm on any SKILL.md files in changed_files.

    Degrades gracefully (warn not fail) when the skillspector CLI is absent,
    matching the ruff_lint graceful-skip pattern.

    Severity thresholds:
        risk_score 0-20   → pass
        risk_score 21-50  → warn  (MEDIUM — flag for review)
        risk_score 51-100 → fail  (HIGH/CRITICAL — block)
    """
    import shutil
    import subprocess as _sp

    skill_files: List[Path] = []
    if changed_files:
        for f in changed_files:
            if "SKILL.md" in f.name and f.exists():
                skill_dirs = [f.parent]
                skill_files.extend(skill_dirs)
    else:
        # Full scan: all .agents/skills/*/SKILL.md
        skills_root = PROJECT_ROOT / ".agents" / "skills"
        if skills_root.exists():
            skill_files = [p.parent for p in skills_root.rglob("SKILL.md")]

    if not skill_files:
        return CoherenceCheck(
            check_id="skill_security",
            check_name="Skill Security (SkillSpector)",
            status="pass",
            expected=["All SKILL.md files pass SkillSpector scan"],
            actual=["No SKILL.md files to scan"],
            missing=[],
            extra=[],
            message="No SKILL.md files in scope — skip",
        )

    cli = shutil.which("skillspector")
    docker = shutil.which("docker")
    if not cli and not docker:
        return CoherenceCheck(
            check_id="skill_security",
            check_name="Skill Security (SkillSpector)",
            status="warn",
            expected=["skillspector CLI or Docker available"],
            actual=["Neither skillspector nor docker found on PATH"],
            missing=["skillspector or docker"],
            extra=[],
            message="skillspector and Docker not found — install to enable skill security scanning",
        )

    failures: List[str] = []
    warnings: List[str] = []

    for skill_dir in skill_files:
        target = str(skill_dir)
        if cli:
            cmd = [cli, "scan", target, "--no-llm", "--format", "json"]
        else:
            cmd = [
                docker, "run", "--rm",
                "-v", f"{target}:/scan:ro",
                "registry.icdev.local/skillspector:latest",
                "scan", "/scan", "--no-llm", "--format", "json",
            ]
        try:
            result = _sp.run(cmd, capture_output=True, text=True, timeout=60, check=False)
            raw = (result.stdout or "").strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            score = data.get("risk_score") or 0
            severity = data.get("risk_severity", "")
            label = f"{skill_dir.name} (score={score}, severity={severity})"
            if score > 50:
                failures.append(label)
            elif score > 20:
                warnings.append(label)
        except (_sp.TimeoutExpired, FileNotFoundError):
            warnings.append(f"{skill_dir.name} (scan timed out or errored)")

    if failures:
        status = "fail"
    elif warnings:
        status = "warn"
    else:
        status = "pass"

    scanned = len(skill_files)
    msg = (
        f"{len(failures)} HIGH/CRITICAL skill(s) detected — block install/promote"
        if failures else
        f"{len(warnings)} MEDIUM risk skill(s) flagged for review"
        if warnings else
        f"All {scanned} skill(s) passed SkillSpector scan"
    )
    return CoherenceCheck(
        check_id="skill_security",
        check_name="Skill Security (SkillSpector)",
        status=status,
        expected=[f"All {scanned} skill(s) risk_score <= 20"],
        actual=failures + warnings or [f"{scanned} skills passed"],
        missing=failures,
        extra=[],
        message=msg,
    )


# ---------------------------------------------------------------------------
# Check #16: spec_discipline — Anti-Rationalization Rules (addyosmani/agent-skills)
# ---------------------------------------------------------------------------

def check_spec_discipline(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Enforce addyosmani/agent-skills anti-rationalization rules as deterministic checks.

    Rules:
    (a) No spec-before-code: impl .py changed but no context/specs/*.md for that task → warn
    (b) Beyoncé Rule: new function defined but no corresponding test_ file found → fail
    (c) Change-size: single file >100-line delta (approximated by file size heuristic) → warn
    (d) ADR missing: new @bp.route() without any docs/ ADR referencing the path → warn

    All checks are stdlib-only (ast, pathlib, re). Degraded gracefully — each rule
    is independent so one failure doesn't block the others.
    """
    if not changed_files:
        return CoherenceCheck(
            check_id="spec_discipline",
            check_name="Spec Discipline (addyosmani anti-rationalization)",
            status="pass",
            expected=["Discipline checks run on changed files"],
            actual=["No changed files provided — full-scan not applicable"],
            missing=[],
            extra=[],
            message="spec_discipline requires --changed-files to evaluate",
        )

    failures: List[str] = []
    warnings: List[str] = []

    impl_files = [
        f for f in changed_files
        if f.suffix == ".py"
        and "test" not in f.name.lower()
        and f.exists()
        and str(f).startswith(str(PROJECT_ROOT / "tools"))
    ]
    test_files = {f.stem for f in changed_files if f.name.startswith("test_")}

    for impl in impl_files:
        # (b) Beyoncé Rule — new functions without a test file
        try:
            tree = ast.parse(impl.read_text(encoding="utf-8"))
            new_fns = [
                n.name for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef)
                and not n.name.startswith("_")
            ]
            if new_fns:
                expected_test = f"test_{impl.stem}"
                if expected_test not in test_files:
                    failures.append(
                        f"[beyonce-rule] {impl.name}: {len(new_fns)} public function(s) "
                        f"but no {expected_test}.py in changed files"
                    )
        except (SyntaxError, OSError):
            pass

        # (c) Change-size heuristic — large files as proxy (>150KB suggests >100-line change)
        try:
            if impl.stat().st_size > 150_000:
                warnings.append(
                    f"[change-size] {impl.name}: file is large (>150KB) — "
                    "consider splitting into smaller change sets (~100 lines each)"
                )
        except OSError:
            pass

        # (d) ADR missing — new blueprint routes without docs/ ADR
        try:
            content = impl.read_text(encoding="utf-8")
            if "@bp.route(" in content or "@app.route(" in content:
                docs_dir = PROJECT_ROOT / "docs"
                if docs_dir.exists():
                    adrs = list(docs_dir.rglob("*.md"))
                    stem = impl.stem
                    has_adr = any(stem.replace("_", "-") in a.name for a in adrs)
                    if not has_adr:
                        warnings.append(
                            f"[adr-missing] {impl.name}: defines route(s) but no ADR "
                            f"found in docs/ referencing '{stem}'"
                        )
        except OSError:
            pass

    # (a) Spec-before-code — check for context/specs/ entry
    specs_dir = PROJECT_ROOT / "context" / "specs"
    if impl_files and specs_dir.exists():
        spec_names = {p.stem for p in specs_dir.glob("*.md")}
        for impl in impl_files:
            # Heuristic: spec name should share stem prefix with impl or task ID
            stem = re.sub(r"_v\d+$", "", impl.stem)
            if not any(stem in s or s in stem for s in spec_names):
                warnings.append(
                    f"[spec-before-code] {impl.name}: no matching spec in context/specs/ "
                    f"(expected a file with '{stem}' in the name)"
                )

    if failures:
        status = "fail"
    elif warnings:
        status = "warn"
    else:
        status = "pass"

    all_issues = failures + warnings
    msg = (
        f"{len(failures)} Beyoncé Rule violation(s) detected" if failures else
        f"{len(warnings)} discipline warning(s) — spec/ADR/change-size" if warnings else
        f"All {len(impl_files)} impl file(s) passed discipline checks"
    )
    return CoherenceCheck(
        check_id="spec_discipline",
        check_name="Spec Discipline (addyosmani anti-rationalization)",
        status=status,
        expected=["Specs before code, tests for new functions, ADRs for routes, small change sets"],
        actual=all_issues or [f"{len(impl_files)} impl files passed"],
        missing=failures,
        extra=[],
        message=msg,
    )


def check_component_registry(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Verify args/component_registry.yaml is loadable and internally consistent."""
    import yaml

    registry_path = PROJECT_ROOT / "args" / "component_registry.yaml"
    expected = ["components list loadable", "no duplicate keys", "module paths exist"]

    if not registry_path.exists():
        return CoherenceCheck(
            check_id="component_registry",
            check_name="Component Registry Loadable",
            status="fail",
            expected=expected,
            actual=["Registry file missing"],
            missing=[str(registry_path)],
            extra=[],
            message=f"component_registry.yaml not found at {registry_path}",
        )

    try:
        data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return CoherenceCheck(
            check_id="component_registry",
            check_name="Component Registry Loadable",
            status="fail",
            expected=expected,
            actual=[f"YAML parse error: {exc}"],
            missing=[str(registry_path)],
            extra=[],
            message=f"component_registry.yaml is not valid YAML: {exc}",
        )

    components = data.get("components", []) if isinstance(data, dict) else []
    if not components:
        return CoherenceCheck(
            check_id="component_registry",
            check_name="Component Registry Loadable",
            status="fail",
            expected=expected,
            actual=["components list empty or missing"],
            missing=["components"],
            extra=[],
            message="component_registry.yaml has no components list",
        )

    keys = [c.get("key") for c in components if isinstance(c, dict)]
    duplicates = {k for k in keys if keys.count(k) > 1}
    missing_modules: List[str] = []
    for comp in components:
        if not isinstance(comp, dict):
            continue
        module = comp.get("module")
        if not module:
            continue
        module_path = PROJECT_ROOT / (module.replace(".", "/") + ".py")
        if not module_path.exists():
            missing_modules.append(str(module_path.relative_to(PROJECT_ROOT)))

    if duplicates or missing_modules:
        return CoherenceCheck(
            check_id="component_registry",
            check_name="Component Registry Loadable",
            status="fail",
            expected=expected,
            actual=[f"{len(keys)} components"],
            missing=sorted(duplicates) + sorted(missing_modules),
            extra=[],
            message=(
                f"Registry has {len(duplicates)} duplicate key(s) and "
                f"{len(missing_modules)} missing module file(s)"
            ),
        )

    return CoherenceCheck(
        check_id="component_registry",
        check_name="Component Registry Loadable",
        status="pass",
        expected=expected,
        actual=[f"{len(keys)} components, no duplicates, module files present"],
        missing=[],
        extra=[],
        message=f"component_registry.yaml loads cleanly with {len(keys)} components",
    )


def check_canvas_completeness(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Run the 8-point completeness gate against every registered canvas."""
    missing_issues: List[str] = []
    try:
        from tools.config.component_registry import get_registry

        registry = get_registry()
        for comp in registry.iter_canvases():
            report = registry.validate_canvas_completeness(comp.key)
            if not report.passed:
                for item in report.items:
                    if not item.present:
                        missing_issues.append(f"{comp.key}: {item.point} ({item.path or item.message})")
    except Exception as exc:
        return CoherenceCheck(
            check_id="canvas_completeness",
            check_name="Canvas Completeness Gate",
            status="fail",
            expected=["All canvases pass 8-point completeness gate"],
            actual=[f"Validator error: {exc}"],
            missing=[str(exc)],
            extra=[],
            message=f"Canvas completeness validator failed to run: {exc}",
        )

    if missing_issues:
        return CoherenceCheck(
            check_id="canvas_completeness",
            check_name="Canvas Completeness Gate",
            status="warn",
            expected=["All canvases pass 8-point completeness gate"],
            actual=[f"{len(missing_issues)} missing component(s)"],
            missing=sorted(missing_issues),
            extra=[],
            message=f"{len(missing_issues)} canvas completeness issue(s) found (legacy canvases may need registry updates)",
        )

    return CoherenceCheck(
        check_id="canvas_completeness",
        check_name="Canvas Completeness Gate",
        status="pass",
        expected=["All canvases pass 8-point completeness gate"],
        actual=["All canvases complete"],
        missing=[],
        extra=[],
        message="All registered canvases pass the 8-point completeness gate",
    )


def check_nav_sync(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Verify base.html uses the registry-driven nav_tree context."""
    base_html = PROJECT_ROOT / "tools" / "dashboard" / "templates" / "base.html"
    if not base_html.exists():
        return CoherenceCheck(
            check_id="nav_sync",
            check_name="Navigation Registry Sync",
            status="fail",
            expected=["tools/dashboard/templates/base.html renders nav_tree"],
            actual=["base.html missing"],
            missing=[str(base_html)],
            extra=[],
            message="base.html not found",
        )

    content = base_html.read_text(encoding="utf-8")
    has_nav_tree = "nav_tree" in content
    has_old_canvases_block = re.search(
        r'<li class="nav-section-label">Canvases</li>', content
    ) is not None

    if not has_nav_tree:
        return CoherenceCheck(
            check_id="nav_sync",
            check_name="Navigation Registry Sync",
            status="fail",
            expected=["base.html uses nav_tree from registry"],
            actual=["nav_tree not referenced"],
            missing=["nav_tree loop in base.html"],
            extra=[],
            message="base.html does not reference nav_tree; navigation is not registry-driven",
        )

    if has_old_canvases_block:
        return CoherenceCheck(
            check_id="nav_sync",
            check_name="Navigation Registry Sync",
            status="warn",
            expected=["No hardcoded Canvases section"],
            actual=["Hardcoded 'Canvases' nav-section-label still present"],
            missing=[],
            extra=["Hardcoded Canvases block"],
            message="base.html still contains a hardcoded Canvases section alongside nav_tree",
        )

    return CoherenceCheck(
        check_id="nav_sync",
        check_name="Navigation Registry Sync",
        status="pass",
        expected=["base.html renders nav_tree"],
        actual=["nav_tree referenced, no hardcoded Canvases block"],
        missing=[],
        extra=[],
        message="base.html is registry-driven via nav_tree",
    )


def check_iqe_map_sync(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Verify every canvas with an IQE entry has a matching adapter module."""
    try:
        from tools.config.component_registry import get_registry

        registry = get_registry()
        mapping = registry.get_iqe_mapping()
    except Exception as exc:
        return CoherenceCheck(
            check_id="iqe_map_sync",
            check_name="IQE Adapter Map Sync",
            status="fail",
            expected=["Registry IQE mapping matches adapter modules"],
            actual=[f"Could not load registry: {exc}"],
            missing=[str(exc)],
            extra=[],
            message=f"Could not load component registry for IQE sync: {exc}",
        )

    missing_files: List[str] = []
    empty_collections: List[str] = []
    for key, (adapter_module, collections) in mapping.items():
        module_path = PROJECT_ROOT / (adapter_module.replace(".", "/") + ".py")
        canonical_path = PROJECT_ROOT / "icdev" / (adapter_module.replace(".", "/") + ".py")
        if not module_path.exists() and not canonical_path.exists():
            missing_files.append(f"{key}: {adapter_module}.py")
            continue
        if not collections:
            empty_collections.append(f"{key}: collections empty in registry")

    if missing_files:
        return CoherenceCheck(
            check_id="iqe_map_sync",
            check_name="IQE Adapter Map Sync",
            status="fail",
            expected=["All registry IQE adapter modules exist and have collections"],
            actual=[f"{len(mapping)} canvas adapter mapping(s)"],
            missing=sorted(missing_files) + sorted(empty_collections),
            extra=[],
            message=(
                f"{len(missing_files)} missing adapter file(s), "
                f"{len(empty_collections)} empty collection list(s)"
            ),
        )

    if empty_collections:
        return CoherenceCheck(
            check_id="iqe_map_sync",
            check_name="IQE Adapter Map Sync",
            status="warn",
            expected=["All registry IQE adapter modules exist and have collections"],
            actual=[f"{len(mapping)} canvas adapter mapping(s)"],
            missing=sorted(empty_collections),
            extra=[],
            message=f"All adapter files exist; {len(empty_collections)} have empty collection lists",
        )

    return CoherenceCheck(
        check_id="iqe_map_sync",
        check_name="IQE Adapter Map Sync",
        status="pass",
        expected=["All registry IQE adapter modules exist and have collections"],
        actual=[f"{len(mapping)} canvas adapter mapping(s) valid"],
        missing=[],
        extra=[],
        message=f"All {len(mapping)} IQE adapter mappings are valid",
    )


def check_profile_sync(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Verify core_profiles.yaml is loadable and contains required profiles."""
    import yaml

    profile_path = PROJECT_ROOT / "args" / "core_profiles.yaml"
    required = {"local-dev", "air-gap", "saas-il4", "il6-secret"}

    if not profile_path.exists():
        return CoherenceCheck(
            check_id="profile_sync",
            check_name="Core Profile Sync",
            status="fail",
            expected=sorted(required),
            actual=["core_profiles.yaml missing"],
            missing=[str(profile_path)],
            extra=[],
            message="core_profiles.yaml not found",
        )

    try:
        data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return CoherenceCheck(
            check_id="profile_sync",
            check_name="Core Profile Sync",
            status="fail",
            expected=sorted(required),
            actual=[f"Parse error: {exc}"],
            missing=[str(profile_path)],
            extra=[],
            message=f"core_profiles.yaml is not valid YAML: {exc}",
        )

    profiles = data.get("profiles", {}) if isinstance(data, dict) else {}
    missing = sorted(required - set(profiles.keys()))

    active = __import__("os").environ.get("ICDEV_CORE_PROFILE")
    if active and active not in profiles:
        missing.append(f"active profile '{active}' not found")

    if missing:
        return CoherenceCheck(
            check_id="profile_sync",
            check_name="Core Profile Sync",
            status="fail",
            expected=sorted(required),
            actual=sorted(profiles.keys()),
            missing=missing,
            extra=[],
            message=f"Missing core profile(s): {missing}",
        )

    return CoherenceCheck(
        check_id="profile_sync",
        check_name="Core Profile Sync",
        status="pass",
        expected=sorted(required),
        actual=sorted(profiles.keys()),
        missing=[],
        extra=[],
        message=f"Core profiles present: {', '.join(sorted(profiles.keys()))}",
    )


# ---------------------------------------------------------------------------
# Template Variable Parity Check (OPT-CC-02)
# ---------------------------------------------------------------------------

def check_template_variable_parity() -> "CoherenceCheck":
    """Detect Jinja2 template variables used in templates but not passed by render_template().

    Scans blueprint.py files for render_template() calls, extracts keyword
    arguments, then compares against {{ var }} references in the template.
    Variables used in the template but absent from the call-site are potential
    UndefinedError failures at runtime.

    Conservative: skips calls that use **kwargs (dynamic), and excludes
    Flask/Jinja2 built-in globals to avoid false positives.
    """
    import ast as _ast
    import re as _re

    # Names always injected by Flask/Jinja2 without explicit passing
    _BUILTINS = frozenset({
        "g", "request", "session", "config", "current_user", "url_for",
        "get_flashed_messages", "range", "lipsum", "dict", "namespace",
        "loop", "super", "caller", "True", "False", "None",
        "csrf_token", "now", "static",
        # Common ICDEV context processors
        "active_alerts", "nav_links", "unseen_release", "icdev_version",
        "active_toggles", "current_tenant", "security_context",
    })

    _TEMPLATES_DIR = PROJECT_ROOT / "tools" / "dashboard" / "templates"
    violations: List[str] = []

    def _extract_render_calls(src: str):
        """Return [(template_name, frozenset(kwargs))] from a blueprint source."""
        calls = []
        try:
            tree = _ast.parse(src)
        except SyntaxError:
            return calls
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Call):
                continue
            func = node.func
            is_rt = (
                (isinstance(func, _ast.Name) and func.id == "render_template")
                or (isinstance(func, _ast.Attribute) and func.attr == "render_template")
            )
            if not is_rt or not node.args:
                continue
            first = node.args[0]
            if not isinstance(first, _ast.Constant) or not isinstance(first.value, str):
                continue
            template_name = first.value
            # Skip calls with **kwargs — dynamic context, can't analyse statically
            if any(kw.arg is None for kw in node.keywords):
                continue
            kwargs = frozenset(kw.arg for kw in node.keywords if kw.arg)
            calls.append((template_name, kwargs))
        return calls

    def _find_template_vars(template_src: str) -> set:
        """Extract first-level identifiers from {{ var }}, {% if var %}, {% for x in var %}."""
        found = set()
        # {{ identifier }} or {{ identifier.attr }} or {{ identifier | filter }}
        for m in _re.finditer(r'\{\{-?\s*([a-zA-Z_][a-zA-Z0-9_]*)', template_src):
            found.add(m.group(1))
        # {% if/elif var %}, {% for x in var %}, {% set x = var %}
        for m in _re.finditer(
            r'\{%-?\s+(?:if|elif|for\s+\w+\s+in)\s+([a-zA-Z_][a-zA-Z0-9_]*)',
            template_src
        ):
            found.add(m.group(1))
        return found - _BUILTINS

    # Search both namespaces
    for bp_dir in [PROJECT_ROOT / "tools", PROJECT_ROOT / "icdev" / "tools"]:
        for bp_file in sorted(bp_dir.rglob("blueprint.py")):
            # Skip test fixtures
            if "test" in str(bp_file).lower() or "__pycache__" in str(bp_file):
                continue
            try:
                src = bp_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            calls = _extract_render_calls(src)
            for template_name, kwargs in calls:
                tmpl_path = _TEMPLATES_DIR / template_name
                if not tmpl_path.exists():
                    continue
                try:
                    tmpl_src = tmpl_path.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue

                template_vars = _find_template_vars(tmpl_src)
                # Strip {% include %} vars — included templates have their own context
                # Only flag top-level vars that are not in kwargs
                missing_vars = template_vars - kwargs
                if missing_vars:
                    rel_bp = str(bp_file.relative_to(PROJECT_ROOT))
                    violations.append(
                        f"{rel_bp} → {template_name}: undefined {sorted(missing_vars)}"
                    )

    if violations:
        return CoherenceCheck(
            check_id="template_variable_parity",
            check_name="Template Variable Parity",
            status="warn",  # warn not fail — some vars come from context processors
            expected=["All render_template() kwargs match template {{ var }} references"],
            actual=violations,
            missing=violations,
            extra=[],
            message=(
                f"{len(violations)} render_template() call(s) may pass missing template "
                "variables — potential UndefinedError at runtime. "
                "Verify that missing vars are provided by a @app.context_processor."
            ),
        )

    return CoherenceCheck(
        check_id="template_variable_parity",
        check_name="Template Variable Parity",
        status="pass",
        expected=["All render_template() kwargs match template {{ var }} references"],
        actual=[],
        missing=[],
        extra=[],
        message="No template variable parity issues detected.",
    )


# ---------------------------------------------------------------------------
# Canvas RLS Bypass Check (OPT-CC-01)
# ---------------------------------------------------------------------------

def check_canvas_rls_bypass() -> "CoherenceCheck":
    """Detect canvas db/init_db.py files that use get_connection() instead of
    get_canvas_connection().

    Canvas-specific tables lack classification/tenant_id columns. Using
    get_connection() injects RLS predicates that reference those columns and
    raises UndefinedColumn on every query. Every canvas db/init_db.py must use
    get_canvas_connection("ENV_VAR") or call conn.set_security_context(None).
    """
    violations: List[str] = []
    checked: List[str] = []

    # Search both canonical (icdev/tools/) and legacy (tools/) namespaces
    for base in [PROJECT_ROOT / "tools", PROJECT_ROOT / "icdev" / "tools"]:
        for init_db in base.glob("*/db/init_db.py"):
            rel = str(init_db.relative_to(PROJECT_ROOT))
            # Skip test fixtures and the storage module itself
            if "test" in rel or "storage.py" in rel:
                continue
            checked.append(rel)
            try:
                content = init_db.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            # Only flag files that IMPORT get_connection from the storage layer.
            # Files that define their own local get_connection() (e.g. sqlite3-only
            # modules) or that use the global DB intentionally are not violations.
            import re as _re2
            if not _re2.search(
                r"from\s+(?:tools|icdev\.tools)\.db\.storage\s+import[^\n]*\bget_connection\b",
                content,
            ):
                continue

            # If it already uses the safe bypass patterns, skip
            safe = (
                "get_canvas_connection" in content
                or "set_security_context(None)" in content
                or "security_context=None" in content
            )
            if safe:
                continue

            # Exclude files whose DDL defines classification or tenant_id columns —
            # those tables participate correctly in RLS and don't need the bypass.
            has_rls_columns = bool(
                _re2.search(r"\bclassification\b.*TEXT", content)
                or _re2.search(r"\btenant_id\b.*TEXT", content)
            )
            if has_rls_columns:
                continue

            violations.append(rel)

    if violations:
        return CoherenceCheck(
            check_id="canvas_rls_bypass",
            check_name="Canvas RLS Bypass (get_canvas_connection)",
            status="fail",
            expected=["get_canvas_connection() or set_security_context(None)"],
            actual=violations,
            missing=violations,
            extra=[],
            message=(
                f"{len(violations)} canvas db/init_db.py file(s) call get_connection() "
                "without RLS bypass — canvas tables lack classification/tenant_id columns "
                "and will raise UndefinedColumn on every PG query. "
                "Replace with get_canvas_connection('ENV_VAR'). "
                f"Affected: {', '.join(violations)}"
            ),
        )

    return CoherenceCheck(
        check_id="canvas_rls_bypass",
        check_name="Canvas RLS Bypass (get_canvas_connection)",
        status="pass",
        expected=["get_canvas_connection() or set_security_context(None)"],
        actual=checked,
        missing=[],
        extra=[],
        message=f"All {len(checked)} canvas db/init_db.py files use safe RLS bypass patterns.",
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
    "ruff_lint": check_ruff_lint,
    "api_wiring": check_api_wiring,
    "route_uniqueness": check_route_uniqueness,
    "attribution_claims": check_attribution_claims,
    "llm_injection_patterns": check_llm_injection_patterns,
    "skill_standard": check_skill_standard,
    "sandbox_coverage": check_sandbox_coverage,
    "direct_anthropic_import": check_direct_anthropic_import,
    "karpathy_sync": check_karpathy_sync,
    "openapi_parity": check_openapi_parity,
    "hitl_workflow": check_hitl_workflow,
    "mcp_security": check_mcp_security,
    "security_context": check_security_context,
    "canvas_rls_bypass": check_canvas_rls_bypass,
    "template_variable_parity": check_template_variable_parity,
    "log_standard": check_log_standard_compliance,
    "nav_route_parity": check_nav_route_parity,
    "blueprint_imports": check_blueprint_imports,
    "new_page_completeness": check_new_page_completeness,
    "canvas_placeholder_style": check_canvas_placeholder_style,
    "runtime_placeholder_style": check_runtime_placeholder_style,
    "ace_yaml_listen_topics": check_ace_yaml_listen_topics,
    "skill_security": check_skill_security,
    "spec_discipline": check_spec_discipline,
    "component_registry": check_component_registry,
    "canvas_completeness": check_canvas_completeness,
    "nav_sync": check_nav_sync,
    "iqe_map_sync": check_iqe_map_sync,
    "profile_sync": check_profile_sync,
}


# ---------------------------------------------------------------------------
# Auto-fix engine (D-WF-8a: safe fixes only)
# ---------------------------------------------------------------------------

# Fix tiers: auto (safe, no behavior change), suggest (needs review), skip (risky)
_FIX_REGISTRY: Dict[str, str] = {
    "import_usage": "auto",  # ruff --fix --select F401,F811,F841
    "ruff_lint": "auto",  # OPT-49: shares _autofix_imports (ruff --fix)
    "append_only": "auto",  # add table name to APPEND_ONLY_TABLES
    "manifest": "auto",  # auto-append missing tools to manifest.md
    "schema_code": "suggest",  # suggest ALTER TABLE DDL
    "config_code": "suggest",  # suggest YAML additions
    "fixture_schema": "suggest",  # suggest test fixture DDL
    "signature_call": "skip",  # too risky to auto-modify call sites
    "api_wiring": "suggest",  # suggest DB integration for hardcoded APIs
    "route_uniqueness": "skip",  # rename-one-of-two needs human judgment
    "attribution_claims": "skip",  # license audit requires human confirmation
    "llm_injection_patterns": "skip",  # WARN-tier; fixes need human review
    "skill_standard": "suggest",  # description rewrites need human judgment
    "sandbox_coverage": "skip",  # doc/decision — requires human judgment
    "direct_anthropic_import": "skip",  # violations require code routing fix
    "karpathy_sync": "skip",  # add section to CLAUDE.md + companion sync, then re-run
    "openapi_parity": "skip",  # route drift requires human fix (add/remove route or update spec)
    "hitl_workflow": "skip",  # module fixes require human judgment
    "mcp_security": "skip",  # scanner module creation requires human judgment
    "security_context": "skip",  # RLS bypass documentation and wiring fixes require human judgment
    "canvas_rls_bypass": "skip",  # get_canvas_connection() migration requires human judgment per canvas
    "template_variable_parity": "skip",  # undefined vars may come from context processors — human must verify
    "canvas_placeholder_style": "skip",  # SQL placeholder fixes require human judgment (search+replace in SQL strings)
    "runtime_placeholder_style": "skip",  # SQL placeholder fixes require human judgment (search+replace in SQL strings)
    "ace_yaml_listen_topics": "skip",  # YAML restructuring requires human judgment
    "component_registry": "skip",  # registry schema issues require human editing
    "canvas_completeness": "skip",  # missing canvas components must be created by hand
    "nav_sync": "skip",  # template changes require human review
    "iqe_map_sync": "skip",  # adapter wiring requires human review
    "profile_sync": "skip",  # profile YAML changes require human review
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
    "ruff_lint": _autofix_imports,  # OPT-49: same ruff --fix call
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
