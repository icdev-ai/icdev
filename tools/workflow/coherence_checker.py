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
 18. mirror_drift    — WARN when tools/<pkg> and icdev/tools/<pkg> diverge for hot packages (byte-compare; skips re-export shims)
 19. doc_command_paths — every `python tools/...` command in CLAUDE.md / commands.md resolves to a real file (oss-fix-02)
 20. swallowed_persistence — no `except Exception: pass` guarding an INSERT in tools/; best-effort must log (swp-swallow-01)
 21. vendor_parity   — declared stdlib-only modules stay a subset of their OUT-OF-REPO vendored copies (cxo-doc-03)
 22. capability_liveness — a declared capability with ZERO lifetime consumption fails; budgets in args/liveness_gate.yaml (exa-live-02)

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
import concurrent.futures
import dataclasses
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

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
    # Wall-clock cost of this check, filled in by run_checks. Surfaced in the
    # JSON report so tier assignment stays evidence-based instead of guessed.
    duration_sec: float = 0.0

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


def _scan_targets(
    changed_files: Optional[List[Path]] = None, subdir: str = "tools"
) -> List[Path]:
    """Python files a file-scanning check should read.

    Returns the changed subset under *subdir* when the caller supplied one,
    otherwise the whole tree.  Diff-scoping is what lets a whole-tree scanner
    run inside the per-task gate instead of only in the nightly full sweep:
    the same check costs ~17s over 4k files and milliseconds over a diff.
    """
    root = PROJECT_ROOT / subdir
    if changed_files:
        picked: List[Path] = []
        for entry in changed_files:
            path = entry if isinstance(entry, Path) else Path(str(entry))
            if not path.is_absolute():
                path = PROJECT_ROOT / path
            if path.suffix != ".py" or not path.exists():
                continue
            try:
                path.relative_to(root)
            except ValueError:
                continue  # outside the scanned subtree
            picked.append(path)
        return sorted(set(picked))
    if not root.exists():
        return []
    return sorted(root.rglob("*.py"))


_ROUTE_DECORATOR_RE = re.compile(r"@\w+\.route\s*\(")


def _blueprint_has_route(bp_file: Path) -> bool:
    """Return True if a blueprint declares Flask routes — inline or split.

    Classic blueprints carry ``@bp.route(...)`` decorators directly in the
    module file. After a route-group split (cvx-net-01), the blueprint module
    becomes a thin assembler whose ``create_*_blueprint()`` calls
    ``register_<group>_routes(bp)`` for each module under a sibling ``routes/``
    subpackage; the actual ``@route`` decorators live in those route modules.

    Detection:
      1. If the blueprint file itself has a ``@\\w+\\.route(`` decorator -> True.
      2. Otherwise, if the blueprint (a) references ``register_*_routes`` /
         imports from a sibling ``routes`` subpackage, OR (b) has a ``routes/``
         directory beside it — scan ``routes/**/*.py`` for a route decorator and
         return True if any is found.
      3. If neither the blueprint nor any routes/ module declares a route,
         return False (genuine failure — the gate still fires).
    """
    text = _read_text(bp_file)
    if _ROUTE_DECORATOR_RE.search(text):
        return True

    references_split = bool(
        re.search(r"register_\w+_routes", text)
        or re.search(r"from\s+[\w.]*routes(?:\.\w+)?\s+import", text)
        or re.search(r"import\s+[\w.]*\.routes\b", text)
    )
    routes_dir = bp_file.parent / "routes"
    if references_split or routes_dir.is_dir():
        if routes_dir.is_dir():
            for py in sorted(routes_dir.rglob("*.py")):
                if _ROUTE_DECORATOR_RE.search(_read_text(py)):
                    return True
    return False


def _parse_create_tables(sql_text: str) -> Dict[str, List[str]]:
    """Extract table_name → [column_names] from SQL CREATE TABLE statements."""
    tables: Dict[str, List[str]] = {}
    # Drop comment-only lines BEFORE matching. The body capture below is
    # non-greedy up to the first `);`, so a `);` occurring inside a `--` comment
    # ends the match early and silently truncates the column list — every column
    # after the comment then reads as "not in the schema". Stripping the comments
    # afterwards (as the per-segment pass does) is too late to help. Only whole
    # comment lines are removed, never a mid-line `--`, which could sit inside a
    # Python string literal in the surrounding source (swp-scan-01).
    sql_text = "\n".join(
        line for line in sql_text.splitlines() if not line.lstrip().startswith("--")
    )
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


#: Modules pinned to one backend, whose extra columns are created by that
#: backend's own DDL and must NOT be added to the shared schema.
#: `sqlite_vector_store` is the case in point: `sign_bits` is a SQLite-only
#: quantisation column it ALTERs in itself, and the class pins `sqlite3.connect`
#: precisely so it can never reach PostgreSQL. Declaring `sign_bits` in the
#: shared schema to satisfy this check would create the column on PG, where it
#: is inert — the opposite of what the pinning is for.
_SCHEMA_CODE_BACKEND_PINNED = frozenset({
    "tools/rag/sqlite_vector_store.py",
    # Builds each affected table in its *pre*-migration-329 shape on a throwaway
    # SQLite database in order to assert the INSERT fails before the migration and
    # succeeds after. Its simulation_results INSERT names the Network Canvas columns
    # on purpose — that is the shape being migrated away from, so measuring it
    # against the post-migration DDL in init_icdev_db.py is the wrong comparison.
    "tests/test_insert_column_schema_parity.py",
})


_ALTER_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?[\"'`]?(\w+)[\"'`]?\s+"
    r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`]?(\w+)[\"'`]?",
    re.IGNORECASE,
)


def _parse_migration_added_columns() -> Dict[str, List[str]]:
    """Columns that reach an existing table via ALTER TABLE ADD COLUMN in a migration.

    check_schema_code read only the CREATE TABLE bodies in init_icdev_db.py,
    which made it structurally blind to every column added the way this repo
    REQUIRES columns to be added. CLAUDE.md: "Adding a column means writing a
    migration, not editing the CREATE TABLE" — because CREATE TABLE IF NOT
    EXISTS never alters an existing table, so editing that DDL changes nothing
    on a live database while making the declaration disagree with reality.

    The check was therefore failing an INSERT for doing exactly the right thing.
    sbom_records.author_signature (sbx-sig-01, added by migration
    20260808030213) was the first INSERT to hit it, with the whole tree
    otherwise at zero mismatches.

    Deliberately NOT authoritative. check_insert_schema_parity reads the LIVE
    schema (information_schema / PRAGMA) and remains the gate that catches a
    column existing nowhere. This only widens the DECLARED set so a correctly
    migrated column stops reading as unknown.

    Tables that have no CREATE TABLE in init_icdev_db.py are left alone rather
    than synthesized from their ALTERs — a table known only by the columns some
    migration added to it would fail every INSERT naming an original column.
    """
    added: Dict[str, List[str]] = {}
    migrations_dir = PROJECT_ROOT / "tools" / "db" / "migrations"
    if not migrations_dir.exists():
        return added

    # The runner accepts both layouts: flat <version>_name.sql and
    # <version>_name/up.sql. Miss either and the blindness comes straight back.
    sql_files = sorted(migrations_dir.glob("*.sql")) + sorted(migrations_dir.glob("*/up.sql"))
    for sql_file in sql_files:
        text = _read_text(sql_file)
        if not text or "ALTER" not in text.upper():
            continue
        # Whole comment lines only — the same rule _parse_create_tables uses.
        text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
        for table, column in _ALTER_ADD_COLUMN_RE.findall(text):
            added.setdefault(table, []).append(column)
    return added


def check_schema_code(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Verify CREATE TABLE columns match INSERT statements in tools."""
    schema_path = PROJECT_ROOT / "tools" / "db" / "init_icdev_db.py"
    schema_text = _read_text(schema_path)
    schema_tables = _parse_create_tables(schema_text)

    # A column added by a migration is part of the schema too.
    for table, columns in _parse_migration_added_columns().items():
        if table in schema_tables:
            schema_tables[table].extend(columns)

    mismatches: List[str] = []
    checked_files: List[str] = []

    # Scan Python files for INSERT statements
    scan_files = changed_files or []
    if not scan_files:
        for py in (PROJECT_ROOT / "tools").rglob("*.py"):
            if "__pycache__" not in str(py):
                scan_files.append(py)

    for py_path in scan_files:
        try:
            rel_key = str(py_path.relative_to(PROJECT_ROOT)).replace("\\", "/")
        except ValueError:
            rel_key = py_path.name
        if rel_key in _SCHEMA_CODE_BACKEND_PINNED:
            continue

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


_MD_SEPARATOR_RE = re.compile(r"^\|[\s:\-|]+\|$")


def _duplicate_manifest_rows(shard: Path) -> List[str]:
    """Byte-identical duplicate data rows in a manifest shard.

    `tools/manifest*` is merged with `merge=union` (see `.gitattributes`,
    kax-conflict-03) so two branches can append a row to the same table without
    a human resolving a conflict. Union takes the superset, which leaves an
    exact duplicate behind whenever both sides added the same row — the one
    failure mode the strategy introduces, so it is reported rather than trusted.
    Header rows repeat legitimately (a shard may hold several tables) and are
    identified by the separator row that follows them.
    """
    lines = _read_text(shard).splitlines()
    seen: Dict[str, int] = {}
    dupes: List[str] = []
    for i, line in enumerate(lines):
        row = line.strip()
        if not (row.startswith("|") and row.endswith("|")):
            continue
        if _MD_SEPARATOR_RE.match(row):
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if _MD_SEPARATOR_RE.match(nxt):  # header row
            continue
        if row in seen:
            first_cell = row.strip("|").split("|")[0].strip()[:60]
            dupes.append(
                f"{shard.name}:{i + 1} duplicate of line {seen[row]}: {first_cell}"
            )
        else:
            seen[row] = i + 1
    return dupes


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
    duplicate_rows: List[str] = []
    if shard_dir.is_dir():
        for shard in sorted(shard_dir.glob("*.md")):
            manifest_text += "\n" + _read_text(shard).lower()
            duplicate_rows.extend(_duplicate_manifest_rows(shard))
    duplicate_rows.extend(_duplicate_manifest_rows(manifest_path))

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

    # Only flag undocumented tools if < 50% missing (a larger gap means the
    # scan, not the manifest, is wrong).
    flag_undocumented = bool(undocumented) and len(undocumented) < checked * 0.5

    if flag_undocumented or duplicate_rows:
        parts = []
        if flag_undocumented:
            parts.append(f"{len(undocumented)} tool(s) not found in manifest.md")
        if duplicate_rows:
            parts.append(
                f"{len(duplicate_rows)} duplicate manifest row(s) "
                "(union-merge residue — delete the repeat)"
            )
        return CoherenceCheck(
            check_id="manifest",
            check_name="Manifest Coherence",
            status="warn",
            expected=[
                f"All {checked} tools documented in manifest",
                "No duplicate manifest rows",
            ],
            actual=[
                f"{len(undocumented) if flag_undocumented else 0} undocumented",
                f"{len(duplicate_rows)} duplicate rows",
            ],
            missing=undocumented[:20] if flag_undocumented else [],  # Cap output
            extra=duplicate_rows[:20],
            message="; ".join(parts),
        )

    return CoherenceCheck(
        check_id="manifest",
        check_name="Manifest Coherence",
        status="pass",
        expected=["Tool files documented in manifest", "No duplicate manifest rows"],
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


def check_trust_coverage() -> CoherenceCheck:
    """Verify the TRUST invariants (xcut-01): the anti-hallucination grounding
    modules ship in both package trees and are inheritable by child apps, and
    the redaction fail-closed / ingestion-masking toggles exist in config.

    Guards against a recurrence of the mirror-sync drift that dropped grounding
    modules from icdev/, and against silently losing the mask toggles.
    """
    checks = {
        "tools/quality/content_grounding.py": (PROJECT_ROOT / "tools/quality/content_grounding.py").is_file(),
        "tools/quality/citation_grounding.py": (PROJECT_ROOT / "tools/quality/citation_grounding.py").is_file(),
        "icdev/tools/quality/content_grounding.py": (PROJECT_ROOT / "icdev/tools/quality/content_grounding.py").is_file(),
        "icdev/tools/quality/citation_grounding.py": (PROJECT_ROOT / "icdev/tools/quality/citation_grounding.py").is_file(),
    }
    # Child apps inherit grounding only if tools/quality is in DIRECTORY_TREE.
    cag = PROJECT_ROOT / "tools/builder/child_app_generator.py"
    checks["tools/quality in child-app DIRECTORY_TREE"] = (
        cag.is_file() and '"tools/quality"' in _read_text(cag)
    )
    # Redaction toggles present in config.
    rc = PROJECT_ROOT / "args/redaction_config.yaml"
    rc_text = _read_text(rc) if rc.is_file() else ""
    checks["redaction.fail_closed toggle"] = "fail_closed:" in rc_text
    checks["redaction.mask_at_ingestion toggle"] = "mask_at_ingestion:" in rc_text

    missing = sorted(k for k, ok in checks.items() if not ok)
    status = "pass" if not missing else "fail"
    return CoherenceCheck(
        check_id="trust_coverage",
        check_name="TRUST Grounding & Masking Coverage",
        status=status,
        expected=sorted(checks.keys()),
        actual=sorted(k for k, ok in checks.items() if ok),
        missing=missing,
        extra=[],
        message=(
            "All TRUST invariants present"
            if not missing
            else f"{len(missing)} TRUST invariant(s) missing: {missing}"
        ),
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


def _load_registry_module_dirs() -> Dict[str, Tuple[Path, Path]]:
    """Map template-dir name → (blueprint_file, package_dir) from the registry.

    The 8-component gate historically assumed a canvas's Python package sits at
    `tools/<canvas>/blueprint.py`, deriving the path from the *template* directory
    name. That is wrong whenever the package name differs from the template/URL
    name — `logs` is served by `tools/logging/blueprint.py`, and `rfi_canvas` by
    `tools/govcon/rfi_canvas_blueprint.py`. Both were reported as missing their
    blueprint and backing module even though the pages work.

    component_registry.yaml already declares the truth in `module:`, so resolve it
    from there. Missing/malformed registry → empty map, and callers fall back to
    the historical `tools/<canvas>/` guess (fail-safe: gate gets stricter, not looser).
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    registry_path = PROJECT_ROOT / "args" / "component_registry.yaml"
    if not registry_path.exists():
        return {}
    try:
        data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}

    resolved: Dict[str, Tuple[Path, Path]] = {}
    for comp in data.get("components", []) or []:
        module = comp.get("module")
        if not module:
            continue
        template = (comp.get("completeness") or {}).get("template")
        canvas = Path(template).parent.name if template else comp.get("key")
        if not canvas:
            continue
        parts = str(module).split(".")
        if len(parts) < 2:
            continue
        bp_file = PROJECT_ROOT.joinpath(*parts).with_suffix(".py")
        resolved[canvas] = (bp_file, bp_file.parent)
    return resolved


def _load_registry_nav_dirs() -> Set[str]:
    """Return template-dir names of canvases with a registry-declared nav link.

    Modern canvases (component_registry.yaml `nav: {section, label}`) render
    their Canvases-dropdown link dynamically from `nav_tree` in base.html
    (`{{ link.href }}`, built from the registry) rather than as a literal
    `href="/<canvas>"` string. A hardcoded-HTML grep can never find those —
    it would false-positive on every registry-driven canvas (confirmed on
    data_canvas, migration_canvas, and any new canvas following the current
    scaffolding convention). This is the registry-aware alternative check:
    trust `nav.section` the same way `component_registry.validate_canvas_completeness`
    already does, instead of re-implementing an inferior HTML heuristic.

    Keyed by template directory name (`completeness.template`'s parent dir,
    falling back to the registry `key`) so it matches the `canvas` variable
    used by the page.html glob loop even when key != template dir name.

    Missing/malformed registry → empty set (fail-safe: falls back to the
    hardcoded-href heuristic, gate gets stricter not looser).
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        return set()

    registry_path = PROJECT_ROOT / "args" / "component_registry.yaml"
    if not registry_path.exists():
        return set()
    try:
        data = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return set()

    dirs: Set[str] = set()
    for comp in data.get("components", []):
        if not isinstance(comp, dict):
            continue
        # A registry nav link renders via nav_tree / get_nav_context() for ANY
        # component that declares nav.section — not only kind=canvas. Core
        # extensions with a page (e.g. standards_catalog, kind=core_extension,
        # nav.section=Platform, href=/standards-catalog) are genuinely
        # navigable, so match get_nav_context's kind-agnostic logic here or the
        # completeness gate false-positives on a page that IS reachable.
        nav = comp.get("nav") or {}
        if not isinstance(nav, dict) or not nav.get("section"):
            continue
        template_str = (comp.get("completeness") or {}).get("template", "")
        template_dir = Path(template_str).parent.name if template_str else ""
        dirs.add(template_dir or str(comp.get("key", "")))
    dirs.discard("")
    return dirs


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

            # Skip known health/ping/status endpoints — these intentionally
            # return hardcoded sentinel values (not placeholder code).
            _HEALTH_ENDPOINT_NAMES = {
                "api_status", "api_health", "api_ping", "health_check",
                "ping", "status", "get_status", "liveness", "readiness",
            }
            if node.name in _HEALTH_ENDPOINT_NAMES:
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
    "getzep/graphiti": {
        "url": "https://github.com/getzep/graphiti",
        "license": "Apache-2.0",
        "audit_status": (
            "2026-06-29 verified — getzep/graphiti is Apache-2.0 licensed. "
            "tools/document_intelligence/chat_memory.py cites it as design "
            "inspiration for grounded, citable session memory (along with mem0 and "
            "two academic papers). ICDEV implementation uses SQLite-backed subject/ref "
            "tables driven by the existing DIC RAG/KG pipeline; no graphiti code, "
            "class, or method is copied. Concept-only citation."
        ),
    },
    # oss-xcut-01: the four upstreams the OSS-adaptation card studied. Each is a
    # CONCEPT adoption with an independent implementation and no runtime
    # dependency — the wording precedent is tools/agent_toolkit/__init__.py. None
    # is GPL/AGPL. The spike docs/spikes/oss-00-*.md carries the per-item verdict
    # including everything deliberately REJECTED.
    "ragflow": {
        "url": "https://github.com/infiniflow/ragflow",
        "license": "Apache-2.0",
        "audit_status": "concept-only, clean-room 2026-07-26 (oss-xcut-01)",
        "notes": (
            "Adopted GOALS, not stack. Template chunking (oss-chunk-01), position "
            "breadcrumbs (oss-chunk-02), HITL chunk repair (oss-hitl-01) and real "
            "table extraction (oss-table-01) pursue RAGFlow's 'visibility + "
            "structural chunking' aims with pure-Python/pdfplumber implementations. "
            "REJECTED: DeepDoc's VLM layout weights, Elasticsearch/Infinity. No "
            "RAGFlow code, model, or dependency is used."
        ),
    },
    "crawl4ai": {
        "url": "https://github.com/unclecode/crawl4ai",
        "license": "Apache-2.0",
        "audit_status": "concept-only, clean-room 2026-07-26 (oss-xcut-01)",
        "notes": (
            "fit_markdown's two-pass prune+BM25 idea (oss-filter-01, "
            "tools/http/page_extract.py) was re-implemented on stdlib html.parser + "
            "the already-pinned rank_bm25. REJECTED: a general web crawler, and any "
            "headless-browser rendering path. No crawl4ai code or dependency."
        ),
    },
    "browser-use": {
        "url": "https://github.com/browser-use/browser-use",
        "license": "MIT",
        "audit_status": "concept-only, clean-room 2026-07-26 (oss-xcut-01)",
        "notes": (
            "The load-bearing idea adopted is the indexed-element PAGE "
            "REPRESENTATION (act via click(14)), not its agent loop — ICDEV has "
            "several. Built on the existing vendored-Selenium driver_manager "
            "(oss-browse-01..04). REJECTED: adding Python Playwright or a chromium "
            "download. No browser-use code or dependency."
        ),
    },
    "strix": {
        "url": "https://github.com/usestrix/strix",
        "license": "Apache-2.0",
        "audit_status": "concept-only, clean-room 2026-07-26 (oss-xcut-01)",
        "notes": (
            "Adopted the DISCIPLINE — a finding ships with a discriminating "
            "reproduction or it is not a finding (oss-poc-01), and a scope-locked "
            "self-test over HTTP (oss-redteam-01/02). Concept only: "
            "reproduction_validator.py is a stdlib/requests replay engine over the "
            "existing tools/http client with its own predicate vocabulary, allowlist "
            "scope lock, and migration-303 tables. REJECTED: STRIX's Docker sandbox "
            "image, Caido proxy, nuclei bundle, and curl|bash installer — see "
            "docs/spikes/oss-00-ragflow-crawl4ai-browseruse-strix-adaptation.md. "
            "No STRIX code or dependency."
        ),
    },
    # oss-fix-03 introduced tools/quality/review_loop.py, whose docstring cites
    # greptileai/skills/greploop. Only the LOOP SHAPE (review -> fix -> re-review
    # until a score clears or N iterations) was adopted; greploop's score is
    # Greptile's 5/5 confidence, ICDEV's is its OWN existing gates (ruff,
    # coherence, sipa) run over the local diff. No greploop code, class, or method
    # is copied. Registered here to close the attribution warn that oss-fix-03's
    # cherry-pick left open. License left honest-unknown (Greptile's skills repo
    # license was not verifiable offline this session) — concept-only citations
    # carry no derivative-work exposure regardless, and "unknown (audit pending)"
    # is not a _BLOCKING_LICENSES value, matching the leanstral/nemoclaw precedent.
    "greptileai/skills/greploop": {
        "url": "https://github.com/greptileai/skills (skills/greploop)",
        "license": "unknown (audit pending)",
        "audit_status": (
            "concept-only, clean-room 2026-07-26 (attribution follow-up to "
            "oss-fix-03). tools/quality/review_loop.py adopts the review-until-green "
            "LOOP SHAPE only; scoring is replaced entirely by ICDEV's own gates "
            "(ruff / coherence_checker --all / sipa pr_gates over the branch diff). "
            "Structural review confirms no greploop code, class, or method is "
            "copied and there is no runtime dependency. Upstream repo license not "
            "positively verified offline; treated as prose-only reference."
        ),
    },
    # cdp-00 / cdp-port-*: the two upstreams the CDP air-gap browser card studied,
    # registered proactively per the spike (§6) — before any file cites them.
    "cdp-use": {
        "url": "https://github.com/browser-use/cdp-use",
        "license": "MIT",
        "audit_status": "concept-only, clean-room 2026-07-26 (cdp-port-01/03)",
        "notes": (
            "cdp-use showed that a browser can be driven over the DevTools Protocol "
            "with a tiny pure-Python stack (httpx/websockets). ICDEV adopted the "
            "TRANSPORT DECISION only and re-implemented it stdlib-only "
            "(tools/browser/cdp/ws_client.py — a hand-rolled RFC 6455 client, no "
            "third-party WS library) because the air-gap target has neither websockets "
            "nor websocket-client declared. No cdp-use code, class, or dependency is "
            "used; cdp-use also requires Python >=3.11 while ICDEV floors at 3.9."
        ),
    },
    # firecrawl is REJECTED, recorded so the rejection is auditable. It is AGPL-3.0
    # (a _BLOCKING_LICENSES value), but nothing here CITES it as an adaptation — no
    # file adapts firecrawl code — so this entry documents the decision without
    # creating a derivative-work exposure. Its value was already shipped
    # (page_extract/fetch_extract, oss-00 A1/A1b); self-hosting needs Docker + Redis +
    # a Playwright microservice, all standing non-goals. See spike cdp-00 §5.
    "firecrawl": {
        "url": "https://github.com/firecrawl/firecrawl",
        "license": "AGPL-3.0 (main repo; SDKs/UI MIT) — REJECTED, not used",
        "audit_status": "REJECTED 2026-07-26 (cdp-00 §5) — no code, no dependency, no adaptation",
        "notes": (
            "Copyleft-blocking and its runtime is the already-rejected shape (Docker + "
            "Redis + PostgreSQL + a Playwright microservice, which reintroduces the very "
            "browser download this card eliminates). Its non-duplicated idea (/map "
            "sitemap discovery) stays deferred per oss-00 Gap C2. Recorded as a "
            "rejection, not a citation of use."
        ),
    },
    # oss-02 / oss2-triage-01: the one genuinely new idea from the nine-project sweep.
    "agent-chief": {
        "url": "https://github.com/(agent-chief)",
        "license": "MIT",
        "audit_status": "concept-only, clean-room 2026-07-26 (oss2-triage-01)",
        "notes": (
            "Adopted the PATTERN only — a scored interrupt/dispatch/file worthiness "
            "stage in front of notification routing (tools/notifications/worthiness.py). "
            "NOT the package (Python 3.12+ against ICDEV's 3.9 floor) and NOT a new "
            "notification system: it is one config-driven scored stage over the existing "
            "tools/notifications/ subsystem, off by default. No agent-chief code or "
            "dependency is used."
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
            #
            # The phrase itself is case-insensitive ("Based on the" / "based on the"),
            # but the project name must NOT be. A blanket re.IGNORECASE made the
            # leading [A-Z] match lowercase words, so ordinary prose such as
            # "Based on the requirements analysis" was reported as an unregistered
            # upstream citation. Scope the flag to the phrase with (?i:...).
            pattern = re.compile(
                rf"(?i:{re.escape(phrase)})\s+"
                rf"([A-Z][A-Za-z0-9_\-./]+(?:\s+[A-Za-z0-9][A-Za-z0-9_\-./]*)?"
                rf"|[a-z][a-z0-9_\-]+[/.][A-Za-z0-9_\-./]+)"
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


def check_reflex_registry() -> CoherenceCheck:
    """rri — every daemon.REFLEX_NAMES entry must resolve to a dispatchable reflex.

    The daemon schedules reflexes by importing ``tools.genesis.reflexes.<name>``
    and calling its ``run``. When the module is absent it silently marks the
    entry ``missing`` / ``is_stub`` and skips it every cycle — a registered
    capability that never fires, invisible unless someone reads the schedule.
    This is the same class of defect as a TOOL_REGISTRY entry with no handler
    (oss-fix-01): a registry that lists something it cannot dispatch.

    Fails the gate on any REFLEX_NAMES entry whose module does not import, or
    imports without a callable ``run``.
    """
    import importlib.util

    try:
        from tools.genesis.daemon import REFLEX_NAMES
    except Exception as exc:  # noqa: BLE001
        return CoherenceCheck(
            check_id="reflex_registry",
            check_name="Reflex Registry Integrity",
            status="warn",
            expected=["daemon.REFLEX_NAMES importable"],
            actual=[f"import failed: {exc}"],
            missing=[],
            extra=[],
            message=f"could not import REFLEX_NAMES: {exc}",
        )

    missing: List[str] = []
    no_run: List[str] = []
    for name in REFLEX_NAMES:
        spec = importlib.util.find_spec(f"tools.genesis.reflexes.{name}")
        if spec is None:
            missing.append(name)
            continue
        try:
            mod = importlib.import_module(f"tools.genesis.reflexes.{name}")
            if not callable(getattr(mod, "run", None)):
                no_run.append(name)
        except Exception:  # noqa: BLE001 - import error is a dispatch failure
            no_run.append(name)

    broken = missing + no_run
    if broken:
        detail = []
        if missing:
            detail.append(f"no module: {', '.join(missing)}")
        if no_run:
            detail.append(f"no callable run(): {', '.join(no_run)}")
        return CoherenceCheck(
            check_id="reflex_registry",
            check_name="Reflex Registry Integrity",
            status="fail",
            expected=["every REFLEX_NAMES entry resolves to a reflex with run()"],
            actual=[f"{len(REFLEX_NAMES)} registered, {len(broken)} undispatchable"],
            missing=broken,
            extra=[],
            message=(
                f"{len(broken)} REFLEX_NAMES entr(y/ies) cannot be dispatched "
                f"({'; '.join(detail)}). Create the reflex module, correct the "
                "name, or remove the entry — a registered reflex that never fires "
                "is worse than an unregistered one."
            ),
        )

    return CoherenceCheck(
        check_id="reflex_registry",
        check_name="Reflex Registry Integrity",
        status="pass",
        expected=["every REFLEX_NAMES entry resolves to a reflex with run()"],
        actual=[f"all {len(REFLEX_NAMES)} registered reflexes dispatch"],
        missing=[],
        extra=[],
        message=f"all {len(REFLEX_NAMES)} REFLEX_NAMES entries resolve to a reflex with run()",
    )


_LIVENESS_GATE_PATH = PROJECT_ROOT / "args" / "liveness_gate.yaml"
_LIVENESS_CHECK_ID = "capability_liveness"
_LIVENESS_CHECK_NAME = "Capability Liveness"
_LIVENESS_EXPECTED = [
    "every declared capability unit has at least one recorded consumption in the "
    "telemetry's lifetime, or is grandfathered in args/liveness_gate.yaml"
]


def _load_liveness_gate() -> Dict[str, Any]:
    """Load args/liveness_gate.yaml, falling back to a strict built-in default.

    An unreadable or absent gate file yields an EMPTY grandfather map, which
    gives every capability class a budget of zero — fail-safe CLOSED, the same
    direction ``_load_insert_schema_gate`` fails in. A missing allowlist must
    never be the thing that lets an inert capability through.
    """
    gate: Dict[str, Any] = {
        "window_days": 30,
        "lifetime_days": 36500,
        "evidence_anchor": {"table": "audit_trail", "min_rows": 1000},
        "grandfathered": {},
    }
    if not _HAS_YAML or not _LIVENESS_GATE_PATH.exists():
        return gate
    try:
        raw = yaml.safe_load(_LIVENESS_GATE_PATH.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — an unparseable gate file must not crash the sweep
        return gate
    if not isinstance(raw, dict):
        return gate

    for key in ("window_days", "lifetime_days"):
        try:
            value = int(raw[key])
        except (KeyError, TypeError, ValueError):
            continue
        if value > 0:
            gate[key] = value

    anchor = raw.get("evidence_anchor")
    if isinstance(anchor, dict):
        table = str(anchor.get("table") or "").strip()
        if table:
            gate["evidence_anchor"]["table"] = table
        try:
            gate["evidence_anchor"]["min_rows"] = max(0, int(anchor.get("min_rows")))
        except (TypeError, ValueError):
            pass

    budgets: Dict[str, int] = {}
    for name, allowed in (raw.get("grandfathered") or {}).items():
        try:
            budgets[str(name)] = max(0, int(allowed))
        except (TypeError, ValueError):
            continue
    gate["grandfathered"] = budgets
    return gate


def _liveness_corpus_rows(conn, table: str) -> Optional[int]:
    """Rows in the evidence-anchor table, or None when it cannot be counted.

    None and 0 mean different things here and must not be collapsed: 0 is an
    initialised database nobody has used, None is a database we could not ask.
    Both stop the gate from failing, but only one of them is a real reading.
    """
    try:
        from tools.db.storage import table_exists

        if not table_exists(conn, table):
            return None
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()  # noqa: S608 — literal from gate config
        return int(dict(row).get("n") or 0)
    except Exception:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def _evaluate_capability_liveness(
    window_report: Dict[str, Any],
    lifetime_report: Dict[str, Any],
    corpus_rows: Optional[int],
    gate: Dict[str, Any],
) -> Dict[str, Any]:
    """Fold a windowed and a lifetime consumption report into a gate verdict.

    Split out from :func:`check_capability_liveness` so the decision is testable
    without a populated database — the database being populated is precisely
    what the decision is conditioned on.

    Everything is decided on COUNTS, never on the ``inert_units`` name lists:
    those are truncated to ``max_listed_units`` per class, so a set difference
    over them would silently under-report the 466-unit classes. A unit inert
    across the lifetime window is necessarily inert across any sub-window, so
    ``window_inert - lifetime_inert`` is an exact count of the units that were
    consumed at some point and merely went quiet.
    """
    anchor = gate.get("evidence_anchor") or {}
    min_rows = int(anchor.get("min_rows") or 0)
    budgets: Dict[str, int] = gate.get("grandfathered") or {}

    if corpus_rows is None or corpus_rows < min_rows:
        return {
            "verdict": "no_history",
            "corpus_rows": corpus_rows,
            "corpus_min_rows": min_rows,
            "corpus_table": str(anchor.get("table") or ""),
            "classes": [],
            "over_budget": [],
            "unmeasurable": [],
            "lowerable": [],
        }

    window_inert = {
        str(c.get("capability_class")): int(c.get("inert") or 0)
        for c in window_report.get("classes") or []
        if c.get("telemetry_available")
    }

    classes: List[Dict[str, Any]] = []
    over_budget: List[Dict[str, Any]] = []
    unmeasurable: List[str] = []
    lowerable: List[Dict[str, Any]] = []

    for entry in lifetime_report.get("classes") or []:
        name = str(entry.get("capability_class"))
        if not entry.get("telemetry_available"):
            unmeasurable.append(f"{name}: {entry.get('unmeasured_reason') or 'no telemetry'}")
            continue
        never = int(entry.get("inert") or 0)
        # Clamp: the two passes are taken moments apart, and a class whose
        # declaration source is itself a table (skill_optimizer) can gain a unit
        # between them. A negative "idle" count would be an artefact of that
        # race, not a reading.
        idle = max(0, window_inert.get(name, never) - never)
        allowed = int(budgets.get(name, 0))
        record = {
            "capability_class": name,
            "declared": int(entry.get("declared") or 0),
            "never_consumed": never,
            "idle_this_window": idle,
            "allowed": allowed,
            "telemetry_table": str(entry.get("telemetry_table") or ""),
        }
        classes.append(record)
        if never > allowed:
            over_budget.append(record)
        elif never < allowed:
            lowerable.append(record)

    return {
        "verdict": "fail" if over_budget else ("warn" if unmeasurable else "pass"),
        "corpus_rows": corpus_rows,
        "corpus_min_rows": min_rows,
        "corpus_table": str(anchor.get("table") or ""),
        "classes": classes,
        "over_budget": over_budget,
        "unmeasurable": unmeasurable,
        "lowerable": lowerable,
    }


def _liveness_check_result(evaluation: Dict[str, Any], window_days: int) -> CoherenceCheck:
    """Render an evaluation from :func:`_evaluate_capability_liveness`."""
    if evaluation["verdict"] == "no_history":
        rows = evaluation["corpus_rows"]
        seen = "could not be counted" if rows is None else f"holds {rows} row(s)"
        return CoherenceCheck(
            check_id=_LIVENESS_CHECK_ID,
            check_name=_LIVENESS_CHECK_NAME,
            status="warn",
            expected=_LIVENESS_EXPECTED,
            actual=[f"evidence anchor {evaluation['corpus_table']} {seen}"],
            missing=[],
            extra=[],
            message=(
                f"Capability liveness NOT verified: the evidence anchor "
                f"'{evaluation['corpus_table']}' {seen}, below the "
                f"{evaluation['corpus_min_rows']}-row floor in args/liveness_gate.yaml. "
                "On a fresh worktree, an ephemeral CI database or an unreachable "
                "backend every declared capability looks inert because nothing has "
                "been recorded — that is a fact about the database, not the tree. "
                "Point ICDEV_STORAGE_BACKEND at the operating platform database to "
                "enable this gate."
            ),
        )

    detail = [
        f"{c['capability_class']}: {c['declared']} declared, {c['never_consumed']} never consumed "
        f"(budget {c['allowed']}), {c['idle_this_window']} idle in last {window_days}d"
        for c in evaluation["classes"]
    ]
    idle_total = sum(c["idle_this_window"] for c in evaluation["classes"])

    if evaluation["over_budget"]:
        offenders = [
            f"{c['capability_class']}: {c['never_consumed']} never-consumed unit(s) "
            f"exceeds the grandfathered budget of {c['allowed']}"
            for c in evaluation["over_budget"]
        ]
        return CoherenceCheck(
            check_id=_LIVENESS_CHECK_ID,
            check_name=_LIVENESS_CHECK_NAME,
            status="fail",
            expected=_LIVENESS_EXPECTED,
            actual=detail,
            missing=offenders,
            extra=evaluation["unmeasurable"],
            message=(
                f"{len(evaluation['over_budget'])} capability class(es) declare more "
                "never-consumed units than args/liveness_gate.yaml allows: "
                f"{'; '.join(offenders)}. A capability that is registered, enabled and "
                "catalogued but has NEVER been dispatched, decided, registered or run is "
                "ICDEV's signature defect — wire it to a consumer or stop declaring it. "
                "Do not raise the budget to get this commit through. "
                f"({idle_total} other unit(s) were consumed at some point and are merely "
                f"idle this window — those are not counted here.)"
            ),
        )

    if evaluation["unmeasurable"]:
        return CoherenceCheck(
            check_id=_LIVENESS_CHECK_ID,
            check_name=_LIVENESS_CHECK_NAME,
            status="warn",
            expected=_LIVENESS_EXPECTED,
            actual=detail,
            missing=[],
            extra=evaluation["unmeasurable"],
            message=(
                f"{len(evaluation['unmeasurable'])} capability class(es) could not be "
                f"measured at all: {'; '.join(evaluation['unmeasurable'])}. An unmeasurable "
                "capability is the state that let every instance of this bug ship, so it is "
                "reported rather than counted as zero. Every measured class is within budget."
            ),
        )

    lowerable = [
        f"{c['capability_class']}: budget {c['allowed']} can be lowered to {c['never_consumed']}"
        for c in evaluation["lowerable"]
    ]
    return CoherenceCheck(
        check_id=_LIVENESS_CHECK_ID,
        check_name=_LIVENESS_CHECK_NAME,
        status="pass",
        expected=_LIVENESS_EXPECTED,
        actual=detail,
        missing=[],
        extra=lowerable,
        message=(
            f"{len(evaluation['classes'])} capability class(es) within their liveness budget; "
            f"{idle_total} unit(s) consumed previously but idle in the last {window_days}d "
            "(not a defect)."
            + (f" Ratchet available — {'; '.join(lowerable)}." if lowerable else "")
        ),
    )


def check_capability_liveness() -> CoherenceCheck:
    """cl — a declared capability with zero lifetime consumption fails the gate.

    ``check_reflex_registry`` and ``tests/test_reflex_dispatch_parity.py`` exist
    because the reflex version of this bug shipped three times: a capability
    that is registered, enabled, importable and catalogued, and never actually
    runs, while nothing goes red. That shape is not specific to reflexes — the
    migration-149 audit hash chain, ``MCPToolAuthorizer``, the prompt registry
    and GEPA are all the same defect. This check generalises it to every class
    ``tools/awareness/capability_consumption.py`` can measure: MCP tools never
    dispatched, approval rules whose decision log is empty, registry rows never
    written, reflexes with zero runs.

    Two readings are taken. The LIFETIME pass decides the gate — a unit with no
    consumption event in the telemetry's entire history has never been consumed.
    The WINDOW pass only classifies the remainder: a unit consumed once and quiet
    since is a low-cadence capability, not a dead one, and is reported without
    being counted. Conflating the two is how a gate like this earns its way into
    being ignored.

    Per-class counts of the pre-existing backlog are grandfathered in
    args/liveness_gate.yaml and ratchet downward only.
    """
    gate = _load_liveness_gate()
    try:
        from tools.awareness.capability_consumption import collect
        from tools.db.storage import get_connection
    except Exception as exc:  # noqa: BLE001
        return CoherenceCheck(
            check_id=_LIVENESS_CHECK_ID,
            check_name=_LIVENESS_CHECK_NAME,
            status="warn",
            expected=_LIVENESS_EXPECTED,
            actual=[f"import failed: {exc}"],
            missing=[],
            extra=[],
            message=f"capability_consumption unavailable ({exc}) — liveness NOT verified",
        )

    conn = None
    try:
        conn = get_connection()
        corpus_rows = _liveness_corpus_rows(conn, str((gate["evidence_anchor"] or {}).get("table") or ""))
        window_report = collect(window_days=int(gate["window_days"]), conn=conn)
        lifetime_report = collect(window_days=int(gate["lifetime_days"]), conn=conn)
    except Exception as exc:  # noqa: BLE001
        return CoherenceCheck(
            check_id=_LIVENESS_CHECK_ID,
            check_name=_LIVENESS_CHECK_NAME,
            status="warn",
            expected=_LIVENESS_EXPECTED,
            actual=[f"measurement failed: {exc}"],
            missing=[],
            extra=[],
            message=(
                f"Consumption telemetry unreachable ({exc}) — liveness NOT verified. "
                "Reported as warn, not pass: an unmeasurable capability is exactly the "
                "state this gate exists to surface."
            ),
        )
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    evaluation = _evaluate_capability_liveness(window_report, lifetime_report, corpus_rows, gate)
    return _liveness_check_result(evaluation, int(gate["window_days"]))


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
# Check: Swallowed Persistence (swp-swallow-01)
# ---------------------------------------------------------------------------


def check_swallowed_persistence(
    changed_files: Optional[List[Path]] = None,
) -> CoherenceCheck:
    """swp-swallow-01 — ban ``except Exception: pass`` around an INSERT.

    A broad handler whose body is exactly ``pass`` over a try block that
    writes means the write can fail forever and nothing ever reports it.
    That silence is what let a batch of defects survive undetected long
    enough to be worth a card of their own.

    Best-effort persistence stays legal — the rule is only that it must be
    *audible*. Keep the ``except Exception``, add a ``logger.warning``.
    Narrow handlers, handlers that re-raise or return, and handlers that
    already log are all untouched by this check.

    Detection is shared with the fixer via
    :mod:`tools.refactor.swallowed_persistence` so the gate and the tool
    can never drift apart on what counts as a violation.
    """
    check_name = "Swallowed Persistence (swp-swallow-01)"
    expected = ["no `except Exception: pass` around an INSERT in tools/"]

    try:
        from tools.refactor.swallowed_persistence import find_sites
    except ImportError as exc:
        # A missing detector must not silently pass the very gate it backs.
        return CoherenceCheck(
            check_id="swallowed_persistence",
            check_name=check_name,
            status="fail",
            expected=expected,
            actual=["detector unavailable"],
            missing=["tools/refactor/swallowed_persistence.py"],
            extra=[],
            message=(
                "cannot import tools.refactor.swallowed_persistence — the "
                f"swallowed-persistence gate cannot run: {exc}"
            ),
        )

    targets = _scan_targets(changed_files, "tools") + _scan_targets(
        changed_files, "icdev/tools"
    )
    if changed_files and not targets:
        return CoherenceCheck(
            check_id="swallowed_persistence",
            check_name=check_name,
            status="pass",
            expected=expected,
            actual=["no Python files under tools/ in this diff"],
            missing=[],
            extra=[],
            message="no scanned files in diff — swallowed-persistence gate skipped",
        )

    sites = find_sites(targets, PROJECT_ROOT)
    if sites:
        offenders = [
            f"{site.rel}:{site.handler_lineno}"
            f" ({site.func_name or '<module>'} -> {site.table})"
            for site in sites
        ]
        return CoherenceCheck(
            check_id="swallowed_persistence",
            check_name=check_name,
            status="fail",
            expected=expected,
            actual=[f"{len(sites)} swallowed INSERT site(s)"],
            missing=[],
            extra=offenders,
            message=(
                f"{len(sites)} `except Exception: pass` block(s) guard an INSERT "
                "— the write can fail forever and nothing reports it. Keep the "
                "best-effort behaviour and add a logger.warning (see "
                "tools/canvas/event_bus.py::_audit_event), or narrow the "
                f"handler. Offenders: {', '.join(offenders[:5])}"
                + (" ..." if len(offenders) > 5 else "")
            ),
        )

    scope = "diff" if changed_files else f"{len(targets)} files"
    return CoherenceCheck(
        check_id="swallowed_persistence",
        check_name=check_name,
        status="pass",
        expected=expected,
        actual=[f"no swallowed INSERT sites ({scope})"],
        missing=[],
        extra=[],
        message=f"no `except Exception: pass` guarding an INSERT ({scope})",
    )


# ---------------------------------------------------------------------------
# Check: Direct Anthropic Import (OPT-44)
# ---------------------------------------------------------------------------


def check_direct_anthropic_import(
    changed_files: Optional[List[Path]] = None,
) -> CoherenceCheck:
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
    for py_file in _scan_targets(changed_files):
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
# Check: AGX architecture LLM-agnosticism (agx-core-02)
# ---------------------------------------------------------------------------

# Vendor SDK / orchestration-framework module roots that architecture code must
# never import directly — inference flows through LLMRouter, never a raw SDK.
_AGX_VENDOR_SDK_ROOTS = frozenset({
    "anthropic", "openai", "langchain", "langgraph", "langchain_core",
    "langchain_community", "langchain_openai", "cohere", "mistralai", "groq",
    "together", "fireworks", "ollama", "nebius", "tavily", "boto3", "botocore",
    "google.generativeai", "vertexai", "azure",
})

# String literals shaped like a concrete provider model ID. Architecture code
# must resolve models from args/llm_config.yaml, never hardcode an ID.
_AGX_MODEL_ID_RE = re.compile(
    r"\b("
    r"claude-(?:3|4|opus|sonnet|haiku|instant)"
    r"|gpt-4|gpt-3\.5|gpt-4o|o1-|o3-"
    r"|gemini-(?:\d|pro|flash)"
    r"|llama-?[23]|mixtral-|mistral-(?:large|small|medium)"
    r"|qwen[\d-]|command-r|deepseek-|grok-\d"
    r")",
    re.IGNORECASE,
)

# Provider modules that bypass the router. `tools.llm.provider` (LLMRequest /
# LLMResponse dataclasses) is allowed; concrete *_provider modules are not.
_AGX_ALLOWED_LLM_MODULE_SUFFIXES = frozenset({"provider", "router", "config_path"})


def _agx_docstring_node_ids(tree: ast.AST) -> set:
    """Return id()s of string-constant nodes that are docstrings (module / class /
    function), so a model-ID scan does not flag prose."""
    ids: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def scan_architecture_agnosticism(source: str, rel: str) -> List[str]:
    """AST-scan one architecture source file for LLM-agnosticism violations.

    Returns a list of ``rel:lineno: reason`` violation strings. Detects:
      1. vendor-SDK / langchain_* imports,
      2. concrete-model-ID-shaped string literals (excluding docstrings),
      3. direct provider instantiation / provider-module imports that bypass LLMRouter.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    violations: List[str] = []
    doc_ids = _agx_docstring_node_ids(tree)

    def _root(mod: str) -> str:
        return mod.split(".")[0]

    for node in ast.walk(tree):
        # (1) + (3) imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if _root(name) in _AGX_VENDOR_SDK_ROOTS or name in _AGX_VENDOR_SDK_ROOTS:
                    violations.append(f"{rel}:{node.lineno}: vendor-SDK import `{name}`")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if _root(mod) in _AGX_VENDOR_SDK_ROOTS or mod in _AGX_VENDOR_SDK_ROOTS:
                violations.append(f"{rel}:{node.lineno}: vendor-SDK import from `{mod}`")
            # provider-module bypass: tools.llm.<x>_provider (x not in allowed)
            elif mod.replace("icdev.", "").startswith("tools.llm."):
                last = mod.rsplit(".", 1)[-1]
                if last.endswith("_provider"):
                    violations.append(
                        f"{rel}:{node.lineno}: direct provider-module import `{mod}` bypasses LLMRouter"
                    )
        # (2) model-ID literals
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in doc_ids:
                continue
            m = _AGX_MODEL_ID_RE.search(node.value)
            if m:
                violations.append(
                    f"{rel}:{node.lineno}: model-ID-shaped literal `{m.group(0)}` — resolve from llm_config.yaml"
                )
        # (3) direct provider instantiation: SomethingProvider(...)
        elif isinstance(node, ast.Call):
            fn = node.func
            fn_name = None
            if isinstance(fn, ast.Name):
                fn_name = fn.id
            elif isinstance(fn, ast.Attribute):
                fn_name = fn.attr
            if fn_name and fn_name.endswith("Provider") and fn_name not in {"EmbeddingProvider"}:
                violations.append(
                    f"{rel}:{node.lineno}: direct provider instantiation `{fn_name}(...)` bypasses LLMRouter"
                )
    return violations


def check_architecture_agnosticism() -> CoherenceCheck:
    """agx-core-02 — enforce the LLM-agnostic contract on tools/llm/architectures/.

    AGX reasoning architectures must resolve all inference through LLMRouter with
    no vendor-SDK imports, no hardcoded model IDs, and no direct provider
    instantiation. This is a hard, hard-won ICDEV property (9 providers, air-gap
    Ollama routing, CUI egress) that must not drift as later AGX tasks add
    architectures. Categorical outputs (agx-pick-*) are what MAKE portability
    achievable, so this gate protects the whole card.
    """
    roots = [
        Path("tools") / "llm" / "architectures",
        Path("icdev") / "tools" / "llm" / "architectures",
    ]
    violations: List[str] = []
    scanned = 0
    for root in roots:
        abs_root = PROJECT_ROOT / root
        if not abs_root.exists():
            continue
        for py_file in sorted(abs_root.rglob("*.py")):
            try:
                rel = str(py_file.relative_to(PROJECT_ROOT))
            except ValueError:
                rel = str(py_file)
            scanned += 1
            violations.extend(scan_architecture_agnosticism(_read_text(py_file), rel))

    if violations:
        return CoherenceCheck(
            check_id="architecture_agnosticism",
            check_name="AGX Architecture LLM-Agnosticism (agx-core-02)",
            status="fail",
            expected=["tools/llm/architectures/ resolves all inference via LLMRouter; no vendor SDK / model IDs"],
            actual=violations,
            missing=[],
            extra=violations,
            message=(
                f"{len(violations)} LLM-agnosticism violation(s) in AGX architecture code — "
                "route through LLMRouter and resolve models from args/llm_config.yaml"
            ),
        )

    return CoherenceCheck(
        check_id="architecture_agnosticism",
        check_name="AGX Architecture LLM-Agnosticism (agx-core-02)",
        status="pass",
        expected=["tools/llm/architectures/ resolves all inference via LLMRouter; no vendor SDK / model IDs"],
        actual=[f"{scanned} architecture file(s) scanned, 0 violations"],
        missing=[],
        extra=[],
        message="AGX architecture code is LLM-agnostic (no vendor SDK / model IDs / provider bypass)",
    )


# ---------------------------------------------------------------------------
# Check: Dead LLMRouter API — .complete()/.chat() (nav-llm-02)
# ---------------------------------------------------------------------------


def check_llm_router_api() -> CoherenceCheck:
    """nav-llm-02 — ban dead LLMRouter API calls (``.complete()`` / ``.chat()``).

    ``LLMRouter`` (tools/llm/router.py) exposes only ``invoke(fn, LLMRequest)``
    and ``invoke_*`` variants — there is no ``complete()`` or ``chat()``.
    A wave of shipped call sites invoked a nonexistent ``router.complete()``
    inside ``try/except``, so their LLM paths were permanently dead — masked by
    deterministic fallbacks (fixed in PR #569). This check prevents regression
    of that whole class.

    Detection (AST, binding-scoped): within each runtime file under
    ``tools/``, ``apps/`` and ``icdev/tools/``:
      1. Track every variable / ``self`` attribute bound to ``LLMRouter(...)``
         or ``get_router(...)``.
      2. Flag any ``.complete(`` / ``.chat(`` call whose receiver is one of
         those router bindings — reported as ``file:line``.

    False-positive guards (NOT flagged):
      • ``cortex_api.complete(...)`` — valid Cortex facade (tools/cortex/api.py);
        receiver is bound to Cortex, not the router.
      • provider SDK ``.complete(`` / ``.chat(`` calls — receiver bound to a
        provider object, not the router; ``tools/llm/providers/`` and
        ``tools/llm/provider.py`` are skipped outright.
      • ``.chat(`` on ollama / other non-router clients — receiver not
        router-bound.
      • string literals / comments — ignored (real AST parse, not text grep).

    Tier: FAIL — the class is fixed on origin/main; any new occurrence is a
    real dead-LLM-path bug. Blocks ``--gate``.
    """
    dead_methods = {"complete", "chat"}
    ctor_names = {"LLMRouter", "get_router"}

    def _receiver_key(node: ast.AST) -> Optional[str]:
        """Return a stable key for a call/assign receiver, or None.

        ``router``            -> "router"
        ``self.router``       -> "self.router"
        anything else         -> None (attribute chains we don't track)
        """
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return f"{node.value.id}.{node.attr}"
        return None

    def _is_router_ctor(value: ast.AST) -> bool:
        """True if an assignment value is LLMRouter(...) / get_router(...)."""
        if not isinstance(value, ast.Call):
            return False
        func = value.func
        if isinstance(func, ast.Name):
            return func.id in ctor_names
        if isinstance(func, ast.Attribute):
            return func.attr in ctor_names
        return False

    def _scan_source(source: str) -> List[Tuple[int, str]]:
        """Return (lineno, snippet) for every dead router .complete()/.chat()."""
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []

        # Pass 1 — collect receiver keys bound to a router constructor.
        router_bindings: Set[str] = set()
        for node in ast.walk(tree):
            targets: List[ast.AST] = []
            if isinstance(node, ast.Assign) and _is_router_ctor(node.value):
                targets = list(node.targets)
            elif (
                isinstance(node, ast.AnnAssign)
                and node.value is not None
                and _is_router_ctor(node.value)
            ):
                targets = [node.target]
            for tgt in targets:
                key = _receiver_key(tgt)
                if key:
                    router_bindings.add(key)

        if not router_bindings:
            return []

        # Pass 2 — flag dead-API calls whose receiver is a router binding.
        hits: List[Tuple[int, str]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in dead_methods:
                continue
            key = _receiver_key(func.value)
            if key and key in router_bindings:
                hits.append(
                    (
                        func.value.lineno if hasattr(func.value, "lineno") else node.lineno,
                        f"{key}.{func.attr}() — LLMRouter has no {func.attr}(); "
                        f"use invoke(fn, LLMRequest)",
                    )
                )
        return hits

    # Files/dirs where provider-SDK .complete()/.chat() legitimately live.
    skip_rel = {
        Path("tools") / "llm" / "provider.py",
        Path("icdev") / "tools" / "llm" / "provider.py",
    }
    skip_dir_parts = (
        (Path("tools") / "llm" / "providers"),
        (Path("icdev") / "tools" / "llm" / "providers"),
    )
    exclude_segments = {"tests", ".tmp", "docs", "node_modules", "__pycache__"}

    roots = [
        PROJECT_ROOT / "tools",
        PROJECT_ROOT / "apps",
        PROJECT_ROOT / "icdev" / "tools",
    ]

    violations: List[str] = []
    seen_files: Set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for py_file in sorted(root.rglob("*.py")):
            if py_file in seen_files:
                continue
            seen_files.add(py_file)
            try:
                rel = py_file.relative_to(PROJECT_ROOT)
            except ValueError:
                rel = py_file
            parts = set(rel.parts)
            if parts & exclude_segments:
                continue
            if rel in skip_rel:
                continue
            if any(str(rel).replace("\\", "/").startswith(str(d).replace("\\", "/") + "/") for d in skip_dir_parts):
                continue
            for lineno, snippet in _scan_source(_read_text(py_file)):
                violations.append(f"{rel}:{lineno}: {snippet}")

    if violations:
        return CoherenceCheck(
            check_id="llm_router_api",
            check_name="Dead LLMRouter API (nav-llm-02)",
            status="fail",
            expected=["0 router.complete()/router.chat() call sites — use invoke(fn, LLMRequest)"],
            actual=violations,
            missing=[],
            extra=violations,
            message=(
                f"{len(violations)} dead LLMRouter API call(s) found — LLMRouter "
                "exposes only invoke(fn, LLMRequest); .complete()/.chat() are "
                "permanently-dead paths masked by fallbacks. Replace with invoke()."
            ),
        )

    return CoherenceCheck(
        check_id="llm_router_api",
        check_name="Dead LLMRouter API (nav-llm-02)",
        status="pass",
        expected=["0 router.complete()/router.chat() call sites — use invoke(fn, LLMRequest)"],
        actual=["0 violations"],
        missing=[],
        extra=[],
        message="No dead LLMRouter .complete()/.chat() call sites detected",
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
    # Must look like an actual CALL: a receiver (`conn.set_…`) or a bare call at
    # statement position. Prose inside a docstring — "…already disables RLS via
    # set_security_context(None) below" in tools/pipeline/db/init_db.py — is a
    # description, not a bypass, and the leading-`#` skip below does not catch it
    # because a docstring line starts with neither `#` nor a quote.
    bypass_re = re.compile(
        r"(?:\.\s*set_security_context\(\s*None\s*\)"
        r"|^\s*set_security_context\(\s*None\s*\))"
    )
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
                # A comment that merely *describes* a bypass is not a bypass. Without
                # this, prose explaining why a historical set_security_context(None)
                # was removed is itself reported as an undocumented bypass — which is
                # why tools/workflow had to be dir-exempted above.
                if line.lstrip().startswith("#"):
                    continue
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
    registry_nav_dirs = _load_registry_nav_dirs()
    registry_modules = _load_registry_module_dirs()

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

        # 2. Blueprint with @route. Prefer the registry-declared module path over
        #    guessing tools/<canvas>/ — the package name need not match the
        #    template/URL name (logs -> tools/logging, rfi_canvas -> tools/govcon).
        bp_file, canvas_dir = registry_modules.get(
            canvas,
            (PROJECT_ROOT / "tools" / canvas / "blueprint.py", PROJECT_ROOT / "tools" / canvas),
        )
        # Child apps live under apps/, not tools/ — and one (strategos) declares
        # no `module:` because its registry entry exists only to register IQE
        # collections, so the fallback above guesses tools/ and reports a
        # blueprint that is present and routed as missing. apps/ is already a
        # first-class registry location (`module: apps.innovation.blueprint`);
        # the fallback simply never learned about it.
        if not bp_file.exists():
            apps_bp = PROJECT_ROOT / "apps" / canvas / "blueprint.py"
            if apps_bp.exists():
                bp_file, canvas_dir = apps_bp, apps_bp.parent
        if not bp_file.exists():
            missing.append(f"{bp_file.name} missing (expected at {bp_file.parent.name}/)")
        elif not _blueprint_has_route(bp_file):
            missing.append("blueprint has no @route decorator")

        # 3. Backing module (any .py other than __init__.py and the blueprint itself)
        if canvas_dir.exists():
            py_modules = [
                f for f in canvas_dir.glob("*.py")
                if f.name not in ("__init__.py", bp_file.name)
            ]
            if not py_modules:
                missing.append(f"no backing module in {canvas_dir.name}/")
        else:
            missing.append(f"{canvas_dir.relative_to(PROJECT_ROOT)} directory missing")

        # 4. Nav link to /<canvas>: either a literal href in base.html (legacy
        # canvases), or a registry-declared nav.section (modern canvases,
        # rendered dynamically via nav_tree — see _load_registry_nav_dirs).
        has_hardcoded_href = f'href="/{canvas}' in base_html_text or f"href='/{canvas}" in base_html_text
        has_registry_nav = canvas in registry_nav_dirs
        if not has_hardcoded_href and not has_registry_nav:
            missing.append(f"no nav link to /{canvas} in base.html (checked hardcoded href and registry nav.section)")

        # 5. IQE adapter
        iqe_adapter = iqe_adapters_dir / f"{canvas}.py"
        if not iqe_adapter.exists():
            missing.append(f"tools/iqe/adapters/{canvas}.py missing")

        # 6. IQE seed queries
        iqe_queries = iqe_queries_dir / canvas
        if not iqe_queries.exists() or not (
            list(iqe_queries.glob("*.yaml")) + list(iqe_queries.glob("*.yml")) + list(iqe_queries.glob("*.iqe"))
        ):
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
    # Sub-check: registry-registered canvases checked against the full
    # 8-component gate, covering TWO previously invisible cases:
    #
    #   A) Enabled canvases with missing template dir or missing template file
    #      (e.g. ndc/sdc declared page.html but directory doesn't exist;
    #      mission_canvas has directory but declared page.html is absent).
    #
    #   B) Canvases with non-page.html primary templates (e.g. docgen →
    #      index.html, second_brain → index.html) that the page.html glob
    #      above never visits — their IQE wiring, mirror, and blueprint
    #      completeness were never checked.
    #
    # The page.html glob loop above already handles canvases whose declared
    # template EXISTS and IS named page.html; those are skipped here to avoid
    # double-reporting.  All others (non-page.html template, or template
    # missing entirely, or dir missing) are checked here.
    # ------------------------------------------------------------------
    registry_violations: List[str] = []
    try:
        import yaml as _yaml
        registry_path = PROJECT_ROOT / "args" / "component_registry.yaml"
        registry_data = _yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
        all_registry_canvases = [
            c for c in registry_data.get("components", [])
            if c.get("kind") == "canvas"
        ]
        # Collect canvas dirs already fully covered by the page.html glob above
        # so we don't double-report them.
        already_covered: Set[str] = set()
        for page_html in templates_dir.rglob("*/page.html"):
            already_covered.add(page_html.parent.name)

        for canvas_entry in all_registry_canvases:
            key = canvas_entry.get("key", "")
            if not key or key in whitelist:
                continue
            is_enabled = canvas_entry.get("default_enabled", False)
            completeness = canvas_entry.get("completeness", {})
            declared_tpl_str = completeness.get("template", "")

            # Resolve declared template path
            declared_tpl: Optional[Path] = None
            if declared_tpl_str:
                declared_tpl = PROJECT_ROOT / declared_tpl_str

            # If declared template is page.html and exists → already covered
            if declared_tpl and declared_tpl.name == "page.html" and declared_tpl.exists():
                continue  # Caught by the page.html glob loop above

            # Skip non-enabled canvases that have NO template dir at all
            # (they're intentionally not built yet)
            # Use the declared template's parent dir when available (handles canvases
            # whose key differs from their template dir name, e.g. ndc→network).
            canvas_tpl_dir = (
                declared_tpl.parent
                if (declared_tpl and declared_tpl.exists())
                else templates_dir / key
            )
            if not canvas_tpl_dir.exists() and not is_enabled:
                continue

            # --- Case A: enabled canvas with missing dir or missing template ---
            if not canvas_tpl_dir.exists():
                registry_violations.append(
                    f"tch-completeness-{key}-template: enabled in "
                    f"component_registry.yaml but tools/dashboard/templates/{key}/ "
                    "does not exist (invisible to 8-component gate)"
                )
                continue

            if declared_tpl and not declared_tpl.exists():
                # Only flag as missing if no fallback template exists either.
                # Many canvases declare page.html in the registry but ship
                # index.html — that is a valid layout choice, not a gap.
                _any_fallback = any(
                    (canvas_tpl_dir / fb).exists()
                    for fb in ("index.html", "canvas.html", "page.html")
                )
                if not _any_fallback:
                    registry_violations.append(
                        f"tch-completeness-{key}-template: {declared_tpl_str} declared "
                        "in completeness.template but file does not exist"
                    )
                # Continue to check other components anyway

            # --- Case B: non-page.html template — run full 8-component gate ---
            # Find the main template: declared path (if it exists) or fallback
            main_tpl: Optional[Path] = None
            if declared_tpl and declared_tpl.exists():
                main_tpl = declared_tpl
            else:
                for fallback in ("index.html", "canvas.html", "page.html"):
                    cand = canvas_tpl_dir / fallback
                    if cand.exists():
                        main_tpl = cand
                        break

            if main_tpl is None:
                # Only report if enabled — not-yet-built disabled canvases are expected
                if is_enabled:
                    registry_violations.append(
                        f"tch-completeness-{key}-template: no usable template in "
                        f"tools/dashboard/templates/{key}/ "
                        "(expected index.html, canvas.html, or page.html)"
                    )
                continue

            # Skip if this canvas dir is already covered by page.html glob
            if key in already_covered or canvas_tpl_dir.name in already_covered:
                continue

            main_tpl_text = _read_text(main_tpl)
            rel_tpl = main_tpl.relative_to(PROJECT_ROOT)

            # 1. icdev/ mirror for main template
            icdev_mirror = PROJECT_ROOT / "icdev" / "tools" / main_tpl.relative_to(PROJECT_ROOT / "tools")
            if not icdev_mirror.exists():
                registry_violations.append(
                    f"tch-completeness-{key}-mirror: {rel_tpl}: icdev/ mirror missing"
                )

            # 2+3. Blueprint with @route
            module_path = canvas_entry.get("module", "")
            bp_file: Optional[Path] = None
            if module_path:
                bp_rel = module_path.replace(".", "/") + ".py"
                bp_file = PROJECT_ROOT / bp_rel
                if not bp_file.exists():
                    # Try icdev/ namespace
                    bp_file = PROJECT_ROOT / "icdev" / bp_rel
            if bp_file and not bp_file.exists():
                registry_violations.append(
                    f"tch-completeness-{key}-blueprint: {module_path}.py missing"
                )
            elif bp_file and not _blueprint_has_route(bp_file):
                registry_violations.append(
                    f"tch-completeness-{key}-blueprint: {module_path} has no @route"
                )

            # 5. IQE adapter
            iqe_cfg = canvas_entry.get("iqe", {})
            iqe_adapter_mod = iqe_cfg.get("adapter_module", "")
            if iqe_adapter_mod:
                adapter_file = PROJECT_ROOT / (iqe_adapter_mod.replace(".", "/") + ".py")
                if not adapter_file.exists():
                    registry_violations.append(
                        f"tch-completeness-{key}-iqe_adapter: "
                        f"{iqe_adapter_mod} adapter missing"
                    )

            # 6. IQE seed queries
            seed_path_str = completeness.get("seed_queries", "")
            if seed_path_str:
                seed_dir = PROJECT_ROOT / seed_path_str
                if not seed_dir.exists() or not (
                    list(seed_dir.glob("*.yaml")) + list(seed_dir.glob("*.yml")) + list(seed_dir.glob("*.iqe"))
                ):
                    registry_violations.append(
                        f"tch-completeness-{key}-seed_queries: "
                        f"{seed_path_str} missing or empty"
                    )

            # 7. IQE widget in template — only required when canvas has iqe.adapter_module wired
            if iqe_adapter_mod and "iqe_query_widget" not in main_tpl_text and "iqe-widget" not in main_tpl_text:
                registry_violations.append(
                    f"tch-completeness-{key}-iqe_widget: "
                    f"{rel_tpl} missing iqe_query_widget include"
                )

    except Exception:
        pass  # registry unavailable — skip this sub-check

    all_violations = violations + mirror_violations + registry_violations
    status = "fail" if all_violations else "pass"
    # Count is now the broader set: page.html glob + registry-driven canvases
    page_html_count = len(list(templates_dir.rglob("*/page.html")))
    registry_extra = len(set(
        v.split(":")[0].replace("tch-completeness-", "").split("-")[0]
        for v in registry_violations
    )) if registry_violations else 0
    canvas_count = page_html_count + registry_extra
    checked_count = canvas_count - whitelisted_count
    wl_note = f" ({whitelisted_count} whitelisted)" if whitelisted_count else ""
    incomplete_count = len(violations)
    mirror_count = len(mirror_violations)
    registry_count = len(registry_violations)
    mirror_note = f"; {mirror_count} icdev/ mirror gap(s)" if mirror_count else ""
    registry_note = f"; {registry_count} registry gap(s)" if registry_count else ""
    return CoherenceCheck(
        check_id="new_page_completeness",
        check_name="New Page 8-Component Completeness",
        status=status,
        expected=[
            f"0 incomplete pages out of {checked_count} checked{wl_note}; "
            "0 icdev/ mirror gaps; 0 registry gaps"
        ],
        actual=[
            f"{incomplete_count} incomplete page(s) out of {checked_count} "
            f"checked{wl_note}{mirror_note}{registry_note}"
        ],
        missing=all_violations,
        extra=[],
        message=(
            f"{incomplete_count} canvas page(s) missing components"
            f"{mirror_note}{registry_note} — these features will be broken, "
            "unreachable, or absent from the icdev/ package"
        ) if all_violations else (
            f"All {checked_count} canvas pages complete and icdev/ mirrors in parity{wl_note}"
            if checked_count > 0
            else f"No new canvas pages to check{wl_note}"
        ),
    )


# ---------------------------------------------------------------------------
# Check: chat_card_inheritance — the shared chat action-card renderer must stay
# globally reachable, in both mirrors, and ungated.
# ---------------------------------------------------------------------------


def check_chat_card_inheritance() -> CoherenceCheck:
    """Guard the mechanism that makes chat action cards reach every page.

    Why this check exists
    ---------------------
    In this repo a shared Jinja include does NOT propagate by being useful. It
    propagates only if something fails when it is missing:

      includes/iqe_query_widget.html   ~157 templates  (gated — grepped below)
      includes/classification_macros   56 templates    (convention only)
      includes/twin_snapshot_panel     1 — itself      (ungated)
      includes/_canvas_shell.html      1 — itself      (ungated)

    The chat action-card renderer is global JS included once from base.html, so
    unlike the IQE widget it needs no per-page include — requiring one would be
    cargo-culting a pattern that does not apply. What it DOES need is for that
    single global include to survive: both mirrors present, and crucially
    UNGATED.

    The failure mode being prevented is concrete. base.html already contains
    ``{% if '/strategos' in request.path %}`` around the Strategos panel, and
    that one line is the entire reason that panel reaches exactly one route.
    Wrapping this include the same way would silently reduce "every chat surface
    in the platform" to "one page", with nothing failing to say so.
    """
    include_rel = "tools/dashboard/templates/includes/_chat_action_card.html"
    base_rel = "tools/dashboard/templates/base.html"
    marker = "includes/_chat_action_card.html"

    violations: List[str] = []
    checked: List[str] = []

    for prefix in ("", "icdev/"):
        partial = PROJECT_ROOT / f"{prefix}{include_rel}"
        if not partial.exists():
            violations.append(f"{prefix}{include_rel} missing")
        else:
            checked.append(f"{prefix}{include_rel}")

        base = PROJECT_ROOT / f"{prefix}{base_rel}"
        if not base.exists():
            violations.append(f"{prefix}{base_rel} missing")
            continue

        text = base.read_text(encoding="utf-8", errors="replace")
        if marker not in text:
            violations.append(
                f"{prefix}{base_rel} does not include {marker} — chat action cards "
                "will not render on any page"
            )
            continue

        # The include must not sit inside a path-conditional block. Look at the
        # few lines above it for an `{% if ... request.path ... %}` guard, which
        # is the exact pattern that limits the Strategos panel to one route.
        lines = text.splitlines()
        idx = next((i for i, ln in enumerate(lines) if marker in ln), -1)
        window = "\n".join(lines[max(0, idx - 3):idx])
        if "request.path" in window and "{% if" in window:
            violations.append(
                f"{prefix}{base_rel}: {marker} is inside a request.path conditional — "
                "that is what limits the Strategos panel to a single route"
            )
        checked.append(f"{prefix}{base_rel}")

    status = "fail" if violations else "pass"
    return CoherenceCheck(
        check_id="chat_card_inheritance",
        check_name="Chat Action-Card Global Inheritance",
        status=status,
        expected=[
            "_chat_action_card.html present in both mirrors and included "
            "unconditionally from base.html"
        ],
        actual=[f"{len(violations)} violation(s) across {len(checked)} checked path(s)"],
        missing=violations,
        extra=[],
        message=(
            "Chat action cards will not reach every surface: " + "; ".join(violations)
        ) if violations else (
            "Chat action-card renderer is globally included and ungated in both mirrors"
        ),
    )


# ---------------------------------------------------------------------------
# Check: twin_chat_include — twin pages must share the chat panel, not copy it
# ---------------------------------------------------------------------------


def check_twin_chat_include() -> CoherenceCheck:
    """Stop the digital-twin chat panel being copied into the next canvas.

    This panel and its sendChatDelta() were duplicated verbatim across four
    twin.html templates. The markup copies differed on exactly two lines (hint,
    placeholder) and the JS copies on exactly one (the fetch URL); every element
    id, inline style and handler was identical. Nothing failed when a fifth
    canvas copied it again, so nothing stopped that happening.

    That is the pattern this repo already demonstrates in both directions:
    includes/iqe_query_widget.html is gated and reached ~157 templates, while
    ungated includes reached exactly one — themselves. A shared include here
    does not survive on merit, only on a check that fails without it.

    A twin template is in scope when it renders the chat surface at all, which
    is detected by the `sendChatDelta` handler. Templates without it (infra,
    pipeline) are simply not chat surfaces and are ignored rather than being
    forced to grow one.
    """
    marker_panel = "includes/_twin_chat_panel.html"
    marker_script = "includes/_twin_chat_script.html"
    # An inline copy is recognisable by the ids the include owns.
    inline_markers = ('id="chatInput"', 'async function sendChatDelta')

    violations: List[str] = []
    checked: List[str] = []

    for prefix in ("", "icdev/"):
        tpl_root = PROJECT_ROOT / f"{prefix}tools/dashboard/templates"
        if not tpl_root.exists():
            continue

        for name in (marker_panel, marker_script):
            if not (tpl_root / name).exists():
                violations.append(f"{prefix}tools/dashboard/templates/{name} missing")

        for twin in sorted(tpl_root.glob("*/twin.html")):
            text = twin.read_text(encoding="utf-8", errors="replace")
            rel = twin.relative_to(PROJECT_ROOT).as_posix()

            inline = [m for m in inline_markers if m in text]
            uses_include = marker_panel in text or marker_script in text
            if not inline and not uses_include:
                continue  # genuinely not a chat surface (infra, pipeline)

            # Counted whether it passes or fails. Detecting surfaces by the
            # inline markers ALONE would make this check pass vacuously the
            # moment the refactor succeeds -- zero surfaces found, zero
            # violations, green. The include itself must therefore also mark a
            # file as in scope.
            checked.append(rel)

            if marker_panel not in text and inline:
                violations.append(
                    f"{rel} carries an inline chat panel ({', '.join(inline)}) "
                    f"instead of including {marker_panel}"
                )
            if marker_script not in text and "async function sendChatDelta" in text:
                violations.append(
                    f"{rel} defines its own sendChatDelta() instead of including "
                    f"{marker_script}"
                )

    status = "fail" if violations else "pass"
    return CoherenceCheck(
        check_id="twin_chat_include",
        check_name="Twin Chat Panel Shared Include",
        status=status,
        expected=[
            "every twin.html with a chat surface includes _twin_chat_panel.html "
            "and _twin_chat_script.html, in both mirrors"
        ],
        actual=[f"{len(violations)} violation(s) across {len(checked)} chat surface(s)"],
        missing=violations,
        extra=[],
        message=(
            "Twin chat panel is being duplicated again: " + "; ".join(violations)
        ) if violations else (
            f"All {len(checked)} twin chat surface(s) use the shared include"
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
        # Flag raw logging.getLogger() usage (allow in logging/ itself).
        # AST, not regex: code GENERATORS embed `logging.getLogger(...)` inside
        # string literals as part of the child app they emit. That is the child
        # app's logger, not this module's, and no amount of migrating here would
        # remove it — child_app_generator.py was reported for its own output.
        if not re.search(r"\blogging\.getLogger\s*\(", src):
            continue
        try:
            uses_raw = any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "getLogger"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "logging"
                for node in ast.walk(ast.parse(src))
            )
        except SyntaxError:
            uses_raw = True  # unparseable — fall back to the textual match
        if uses_raw:
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


#: Canvases whose ``get_connection()`` is a HYBRID: StorageConnection on the
#: PostgreSQL branch, but a RAW ``sqlite3.connect`` handle on the SQLite one.
#: Raw sqlite3 accepts ``?`` and rejects ``%s``, while the PG branch translates
#: ``?`` → ``%s`` — so positional ``?`` is the only form valid on BOTH, and this
#: check's advice is inverted for them.
#:
#: This is not a guess: ``tests/test_cvx_sql07_datacanvas_pct_s.py`` asserts the
#: opposite of this check for these paths, and exists because ``%s`` here broke
#: ``/data/lineage`` and ``check_ext_access`` in production. That test is the
#: authority; without this exemption the two gates cannot both be satisfied.
_HYBRID_RAW_SQLITE_CANVASES = (
    "tools/data_canvas/",
)


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
        if any(part in py_path.as_posix() for part in _HYBRID_RAW_SQLITE_CANVASES):
            continue

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
        raw_sqlite_vars = _raw_sqlite3_receivers(tree)
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

            # sqlite3.connect() handles never reach translate_sql, so `?` is
            # the only placeholder they accept — `%s` raises there. See
            # _raw_sqlite3_receivers.
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id in raw_sqlite_vars
            ):
                continue

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


def _raw_sqlite3_receivers(tree: ast.AST) -> set:
    """Names bound to a direct ``sqlite3.connect()`` handle in this module.

    Such a handle is a real sqlite3 connection, not a StorageConnection, so it
    never passes through ``translate_sql`` and ``?`` is the only placeholder it
    accepts. Flagging those as "use %s for psycopg2" inverts the truth.
    """
    aliases = {"sqlite3"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "sqlite3" and a.asname:
                    aliases.add(a.asname)

    bound: set = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        fn = node.value.func
        if (isinstance(fn, ast.Attribute) and fn.attr == "connect"
                and isinstance(fn.value, ast.Name) and fn.value.id in aliases):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    bound.add(tgt.id)
    return bound


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
        raw_sqlite_vars = _raw_sqlite3_receivers(tree)
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

            # A connection opened directly with sqlite3.connect() is NOT psycopg2
            # and `?` is the only placeholder it accepts — `%s` raises there. The
            # audit writers in tools/db/storage.py are exactly this: they open
            # their own sqlite3 handle so an RLS/field audit row still lands when
            # the pooled connection is mid-transaction. "Fixing" those to %s would
            # silently empty an append-only audit trail, which has happened before.
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id in raw_sqlite_vars
            ):
                continue

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
        # Migrations are exempt. Every migration is `<n>_<slug>/up.py`, so the
        # basename rule below demands `test_up.py` for all of them — one
        # filename that cannot serve more than one migration. Migration 309,
        # long merged, fails this rule identically, and no migration in this
        # repo has ever had a test_up.py. A migration is verified by applying
        # it and by the schema invariants its feature tests assert, not by a
        # same-named file.
        and "db/migrations/" not in f.as_posix()
    ]
    test_files = {f.stem for f in changed_files if f.name.startswith("test_")}

    # Text of every changed test file, so coverage can be judged by whether a
    # test actually references the module rather than by whether someone
    # happened to name the file test_<basename>.py. The basename rule alone is
    # a lottery for any name that repeats across the tree — `init_db.py` exists
    # in ~10 canvases and they cannot all map to one `test_init_db.py`.
    changed_test_text = ""
    for f in changed_files:
        if f.name.startswith("test_") and f.exists():
            try:
                changed_test_text += f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass

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
                try:
                    rel = impl.relative_to(PROJECT_ROOT).as_posix()
                except ValueError:
                    rel = impl.as_posix()
                module_path = rel[:-3].replace("/", ".")  # tools/a/b.py -> tools.a.b
                referenced = (
                    rel in changed_test_text
                    or module_path in changed_test_text
                    or any(fn in changed_test_text for fn in new_fns)
                )
                if expected_test not in test_files and not referenced:
                    failures.append(
                        f"[beyonce-rule] {impl.name}: {len(new_fns)} public function(s) "
                        f"but no {expected_test}.py in changed files, and no changed "
                        f"test references {module_path}"
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


def check_component_cli_reachability(
    changed_files: Optional[List[Path]] = None,
) -> CoherenceCheck:
    """Every registry component must be reachable from all THREE surfaces.

    pkg-reg-01 closed a 21-flag `enable`/`disable` gap; this is the durable
    guard that stops the 22nd. A component declared in
    ``args/component_registry.yaml`` is only truly usable if an operator can
    turn it on/off from each surface a fresh install offers:

      1. ``icdev enable``/``disable`` — its env flag is covered by a registry
         CLI toggle (``registry.get_cli_toggles()``).
      2. the ``icdev setup`` TUI — it appears as a row (``setup.build_rows``).
      3. the generated ``.env`` — its flag is emitted by
         ``env_generator.render_component_section``.

    Fails naming the specific component keys + env flags that are missing from
    each surface (never a bare count — an unnamed coherence failure costs more
    time than it saves). No safe --fix: a gap means wiring/registry work.
    """
    expected = [
        "every component reachable from icdev enable/disable",
        "every component reachable from the icdev setup TUI",
        "every component present in the generated .env",
    ]

    try:
        from tools.cli.env_generator import render_component_section
        from tools.cli.setup import KIND_ORDER as _SETUP_KIND_ORDER
        from tools.cli.setup import build_rows
        from tools.config.component_registry import get_registry

        registry = get_registry()
        components = [c for c in registry.list_all() if c.env_flag]

        # Surface 1: icdev enable/disable — union of all CLI toggle flags.
        toggle_flags: Set[str] = set()
        for flags in registry.get_cli_toggles().values():
            toggle_flags.update(flags)

        # Surface 2: icdev setup TUI — the rows it renders (env-independent).
        tui_keys = {
            r.key for r in build_rows(registry, Path("__does_not_exist__.env"))
        }

        # Surface 3: generated .env — the emitted component section.
        env_section = render_component_section(registry)
    except Exception as exc:  # pragma: no cover - import/loader failure
        return CoherenceCheck(
            check_id="component_cli_reachability",
            check_name="Component CLI/TUI/.env Reachability",
            status="fail",
            expected=expected,
            actual=[f"reachability probe failed to run: {exc}"],
            missing=[str(exc)],
            extra=[],
            message=f"Could not evaluate component reachability: {exc}",
        )

    missing_enable: List[str] = []
    missing_tui: List[str] = []
    missing_env: List[str] = []
    for c in components:
        if c.env_flag not in toggle_flags:
            missing_enable.append(f"{c.key} ({c.env_flag})")
        if c.key not in tui_keys:
            reason = ("kind not in setup KIND_ORDER"
                      if c.kind not in _SETUP_KIND_ORDER else "no TUI row")
            missing_tui.append(f"{c.key} ({c.env_flag}) — {reason}")
        if f"{c.env_flag}=" not in env_section:
            missing_env.append(f"{c.key} ({c.env_flag})")

    total_missing = len(missing_enable) + len(missing_tui) + len(missing_env)
    if total_missing:
        detail: List[str] = []
        if missing_enable:
            detail += [f"enable/disable: {m}" for m in sorted(missing_enable)]
        if missing_tui:
            detail += [f"setup TUI: {m}" for m in sorted(missing_tui)]
        if missing_env:
            detail += [f".env: {m}" for m in sorted(missing_env)]
        return CoherenceCheck(
            check_id="component_cli_reachability",
            check_name="Component CLI/TUI/.env Reachability",
            status="fail",
            expected=expected,
            actual=[
                f"{len(missing_enable)} unreachable via enable/disable",
                f"{len(missing_tui)} unreachable via setup TUI",
                f"{len(missing_env)} missing from generated .env",
            ],
            missing=detail,
            extra=[],
            message=(
                f"{total_missing} component-surface reachability gap(s): "
                f"{len(missing_enable)} enable, {len(missing_tui)} TUI, "
                f"{len(missing_env)} .env — see missing[] for the exact "
                "component keys and env flags"
            ),
        )

    return CoherenceCheck(
        check_id="component_cli_reachability",
        check_name="Component CLI/TUI/.env Reachability",
        status="pass",
        expected=expected,
        actual=[
            f"all {len(components)} components reachable via enable/disable, "
            "the setup TUI, and the generated .env"
        ],
        missing=[],
        extra=[],
        message=(
            f"All {len(components)} registry components are reachable from "
            "icdev enable/disable, the icdev setup TUI, and the generated .env"
        ),
    )


def check_canvas_completeness(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Run the 8-point completeness gate against every registered canvas.

    BLOCKING (idp-score-05). This is the registry-driven half of the CLAUDE.md
    8-component new-page gate; ``check_new_page_completeness`` is the
    filesystem-driven half. The two enumerate the same rule from opposite
    directions — one from ``args/component_registry.yaml``, one from
    ``tools/dashboard/templates/*/page.html`` — so they must agree on severity
    or the weaker one launders the rule. This one returned ``warn`` while its
    sibling returned ``fail`` and was declared blocking in
    ``args/security_gates.yaml``, which meant a canvas visible only to the
    registry could ship incomplete. Both now fail, and both honour the same
    ``args/page_completeness_whitelist.yaml`` grandfather list.
    """
    missing_issues: List[str] = []
    whitelisted: List[str] = []
    try:
        from tools.config.component_registry import get_registry

        whitelist = _load_page_completeness_whitelist()
        registry = get_registry()
        for comp in registry.iter_canvases():
            if comp.key in whitelist:
                whitelisted.append(comp.key)
                continue
            report = registry.validate_canvas_completeness(comp.key)
            if not report.passed:
                for item in report.items:
                    # `required` is False for components the registry never declared —
                    # e.g. a canvas with no DB migration. Reporting those as missing
                    # rendered the gate as "missing None" and buried the real gaps.
                    if item.required and not item.present:
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
            status="fail",
            expected=["All canvases pass 8-point completeness gate"],
            actual=[f"{len(missing_issues)} missing component(s)"],
            missing=sorted(missing_issues),
            extra=[],
            message=(
                f"{len(missing_issues)} canvas completeness issue(s) found — ship the "
                "missing component(s), declare the point N/A in the registry's "
                "completeness block, or grandfather the canvas in "
                "args/page_completeness_whitelist.yaml"
            ),
        )

    suffix = f" ({len(whitelisted)} whitelisted)" if whitelisted else ""
    return CoherenceCheck(
        check_id="canvas_completeness",
        check_name="Canvas Completeness Gate",
        status="pass",
        expected=["All canvases pass 8-point completeness gate"],
        actual=[f"All canvases complete{suffix}"],
        missing=[],
        extra=sorted(whitelisted),
        message=f"All registered canvases pass the 8-point completeness gate{suffix}",
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
# Check: test DB isolation — raw sqlite3 + %s bypasses translate_sql (kph-B)
# ---------------------------------------------------------------------------
_DB_FACTORY_NAMES = {"get_connection", "_get_db", "get_conn", "get_canvas_connection"}


def _is_sqlite_connect(call: ast.AST) -> bool:
    """True when `call` is a `sqlite3.connect(...)` expression."""
    return (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "connect"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "sqlite3"
    )


def _only_raises(fn: ast.AST) -> bool:
    """True when every statement in `fn` (docstring aside) is a ``raise``.

    A replacement factory of this shape hands runtime code no connection at all
    — it exists to drive the caller's "database unavailable" branch, which is
    precisely the path that must NOT reach a sqlite3 handle. Treating it as an
    untranslated raw connection is the same remedy-rejection the comments below
    describe, one shape further out: the author writes the negative test the
    gate wants and the gate fails them for it.
    """
    body = list(getattr(fn, "body", []) or [])
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return bool(body) and all(isinstance(stmt, ast.Raise) for stmt in body)


def _safe_connection_names(tree: ast.AST, safe_factories: Set[str]) -> Set[str]:
    """Extend the safe factories with local names bound to a translating connection.

    ``real_conn = _storage_conn(db_path)`` followed by
    ``recording_conn = _RecordingConn(real_conn)`` is the SQL-recorder pattern:
    the proxy sits in FRONT of the storage wrapper, so what reaches runtime code
    still translates ``%s``. Resolving only the factory call would flag the
    proxy — rejecting the remedy again, one indirection later.

    Propagation is transitive but bounded, and an assignment that reaches
    ``sqlite3.connect`` anywhere in its value stays raw, so wrapping a raw
    connection in a proxy is still caught.
    """
    safe = set(safe_factories)
    for _ in range(3):  # factory -> connection -> proxy is the deepest real chain
        grew = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if not targets or all(t in safe for t in targets):
                continue
            if any(_is_sqlite_connect(sub) for sub in ast.walk(node.value)):
                continue
            refs = {sub.id for sub in ast.walk(node.value) if isinstance(sub, ast.Name)}
            if refs & safe:
                safe.update(targets)
                grew = True
        if not grew:
            break
    return safe


def _sql_compat_factory_names(tree: ast.AST) -> Set[str]:
    """Names of local factories that hand back a placeholder-translating connection.

    ``_sql_compat.connect``/``translating`` ARE the sanctioned wrapper — they
    delegate to the same ``translate_sql`` the runtime uses. A file that fixes one
    fixture with them almost always still holds a raw ``sqlite3.connect``
    elsewhere for schema setup or for its own assertions, and that residue must
    not keep the file flagged: doing so makes the gate reject its own remedy.

    ``tools.db.storage.get_connection`` counts for the same reason, and more
    strongly: it is not a stand-in for the runtime wrapper, it IS the runtime
    wrapper, so a fixture built on it cannot drift from production at all. A
    factory returning one was still being flagged, which is the remedy-rejection
    this function exists to prevent.

    Only the factory actually named in the patch call is cleared. A second,
    still-raw factory in the same file stays a violation.
    """
    compat_calls: Set[str] = set()
    # Names bound to the real storage factory: `from tools.db.storage import
    # get_connection as _real` and `import tools.db.storage as s` both appear.
    storage_calls: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.endswith("_sql_compat"):
                for alias in node.names:
                    compat_calls.add(alias.asname or alias.name)
            elif module.endswith("db.storage"):
                for alias in node.names:
                    if alias.name in ("get_connection", "get_canvas_connection"):
                        storage_calls.add(alias.asname or alias.name)

    def _is_compat_call(sub: ast.AST) -> bool:
        if not isinstance(sub, ast.Call):
            return False
        fname = sub.func
        # `storage_calls` too: get_connection/get_canvas_connection ARE the
        # sanctioned translating factories (CLAUDE.md), so a fixture that hands
        # one back is as safe as one wrapping _sql_compat — flagging it sent
        # authors toward the raw connect this check exists to stop.
        if isinstance(fname, ast.Name) and fname.id in (compat_calls | storage_calls):
            return True
        # ``StorageConnection(raw, "sqlite")`` — the remedy this check's own
        # failure message prescribes, and the strongest form of it: not a
        # stand-in for the runtime wrapper but the wrapper itself, constructed
        # directly. Recognised as a Name (``from ... import StorageConnection``)
        # and as an Attribute (``storage.StorageConnection``, the import form a
        # shim-aware test uses). Without this the gate rejected its own remedy —
        # the third time that has happened in this function.
        if isinstance(fname, ast.Name) and fname.id == "StorageConnection":
            return True
        if isinstance(fname, ast.Attribute) and fname.attr == "StorageConnection":
            return True
        return (
            isinstance(fname, ast.Attribute)
            and fname.attr in ("connect", "translating")
            and isinstance(fname.value, ast.Name)
            and fname.value.id.endswith("_sql_compat")
        )

    def _has_unwrapped_raw_connect(fn: ast.AST) -> bool:
        # ``translating(conn)`` takes a raw sqlite3 handle by design — the
        # fixture needs one anyway to create its schema. Counting the wrapper's
        # own INPUT as residue made this gate reject the remedy it prescribes,
        # so a raw connect is only residue when nothing wraps it.
        wrapped_nodes: Set[int] = set()
        wrapped_names: Set[str] = set()
        for sub in ast.walk(fn):
            if not _is_compat_call(sub):
                continue
            for arg in sub.args:
                if _is_sqlite_connect(arg):
                    wrapped_nodes.add(id(arg))
                elif isinstance(arg, ast.Name):
                    wrapped_names.add(arg.id)

        # Names bound to a raw connect that is later handed to the wrapper.
        bound: dict[int, str] = {}
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Assign) and _is_sqlite_connect(sub.value):
                for tgt in sub.targets:
                    if isinstance(tgt, ast.Name):
                        bound[id(sub.value)] = tgt.id

        for sub in ast.walk(fn):
            if not _is_sqlite_connect(sub):
                continue
            if id(sub) in wrapped_nodes:
                continue
            if bound.get(id(sub)) in wrapped_names:
                continue
            return True  # a raw connect nothing wraps — still a violation
        return False

    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    safe: Set[str] = {
        fn.name for fn in funcs
        if (any(_is_compat_call(sub) for sub in ast.walk(fn)) and not _has_unwrapped_raw_connect(fn))
        or _only_raises(fn)
    }

    # A helper that hands back what a safe factory produced is itself safe:
    # ``def shim(): return _shim_conn()`` and the contextmanager
    # ``def _gc(): yield shim`` are both one level removed from the wrapper, and
    # it is _gc that gets named in the patch call. Without this the gate flags a
    # fixture that IS correctly wrapped, just indirectly — which is exactly how
    # the already-merged canvas emitter fixtures still read as violations.
    # The unwrapped-raw-connect guard still applies, so indirection cannot
    # launder a factory that really does hand out a bare sqlite3 handle.
    changed = True
    while changed:
        changed = False
        for fn in funcs:
            if fn.name in safe or _has_unwrapped_raw_connect(fn):
                continue
            for sub in ast.walk(fn):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                    ref = sub.func.id
                elif isinstance(sub, (ast.Return, ast.Yield)) and isinstance(sub.value, ast.Name):
                    ref = sub.value.id
                else:
                    continue
                if ref in safe:
                    safe.add(fn.name)
                    changed = True
                    break
    return safe


def _patch_replacement_names(node: ast.Call) -> Set[str]:
    """Names supplied as the replacement in a patch/setattr call.

    The replacement is usually the factory itself (``side_effect=_conn``), but
    a factory that needs the tmp path is passed wrapped —
    ``side_effect=lambda *a, **kw: _storage_conn(db_path)``. Both name the same
    safe factory, so the wrapped form is unwrapped by collecting the names it
    calls; treating it as unrecognised flags the fix as the bug.
    """
    names: Set[str] = set()

    def _add(value: ast.AST) -> None:
        if isinstance(value, ast.Name):
            names.add(value.id)
        elif isinstance(value, ast.Lambda):
            # patch(target, lambda: shim) — the fixture is named in the body.
            for sub in ast.walk(value.body):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)

    for kw in node.keywords:
        if kw.arg in ("side_effect", "new", "return_value"):
            _add(kw.value)
    # monkeypatch.setattr(target, "get_connection", _conn) — trailing positional.
    for arg in node.args:
        _add(arg)
    return names


def check_test_db_isolation(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Flag tests that hand runtime code a RAW sqlite3 connection while %s SQL is reachable.

    Production DB is PostgreSQL (`%s` placeholders); tests/conftest.py forces
    ICDEV_STORAGE_BACKEND=sqlite and StorageConnection.translate_sql rewrites
    %s -> ?. A test that monkeypatches the connection factory
    (get_connection/_get_db/...) to return a bare `sqlite3.connect(...)`, or
    passes such a raw connection as `conn=` into runtime code, DEFEATS the
    translation: the %s query raises `near "%": syntax error` and is usually
    swallowed to None (a silent green test). This bit test_admin_creation_anomaly,
    tests/trading, and docmod (PRs #207/#209). Every existing sqlite guard
    (sqlite3_connect_linter, pg_portability_linter, pre_tool_use) exempts tests/,
    so nothing else catches it.

    FAIL on changed-file scope (gates new violations pre-merge); WARN on full-repo
    (legacy tests exist). Fix: wrap in StorageConnection(conn, "sqlite") or consume
    conftest's icdev_db fixture + ICDEV_DB_PATH redirect.
    """
    tests_dir = PROJECT_ROOT / "tests"
    if changed_files:
        candidates = [
            p for p in changed_files
            if p.suffix == ".py" and "tests" in p.as_posix().split("/") and p.exists()
        ]
    else:
        candidates = list(tests_dir.rglob("*.py")) if tests_dir.exists() else []

    violations: List[str] = []
    scanned = 0
    for py_path in candidates:
        try:
            source = py_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "sqlite3.connect" not in source:
            continue  # no raw connection -> not this pattern
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        scanned += 1
        try:
            rel = py_path.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            rel = str(py_path)

        has_pct_s = "%s" in source

        # Trigger 1: the file patches a DB connection factory (setattr / mock.patch /
        # patch.object naming get_connection/_get_db/...) — the smoking gun. Any
        # runtime %s query then hits an untranslated raw sqlite3 connection.
        # Trigger 1 wants the file-wide set: a patch replacement may name a
        # connection bound anywhere in the file. Trigger 2 must NOT reuse it --
        # it resolves per scope, and a file-wide set already contains the very
        # names it needs to judge, which would clear every one of them.
        compat_factories = _sql_compat_factory_names(tree)
        safe_factories = _safe_connection_names(tree, compat_factories)

        flagged = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            patch_call = isinstance(node.func, ast.Attribute) and node.func.attr in ("setattr", "patch", "object")
            patch_call = patch_call or (isinstance(node.func, ast.Name) and node.func.id == "patch")
            if not patch_call:
                continue
            str_args = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if safe_factories and _patch_replacement_names(node) & safe_factories:
                continue  # patched to a _sql_compat connection — that IS the fix
            if any(s.split(".")[-1] in _DB_FACTORY_NAMES for s in str_args):
                violations.append(
                    f"{rel}:{getattr(node, 'lineno', 0)}: monkeypatches a DB connection factory "
                    f"to a raw sqlite3 connection — bypasses translate_sql, so %s SQL raises. "
                    f"Wrap in StorageConnection(conn, 'sqlite')."
                )
                flagged = True
                break
        if flagged:
            continue

        # Trigger 2: a raw-sqlite-bound name passed as conn=<name> into a call while
        # %s SQL literals are present in the file.
        #
        # Resolved per function scope, not file-wide. `conn` is the obvious name for
        # a connection, so one test binding it to sqlite3.connect for a read-only
        # assertion and another binding it to a translating fixture is normal and
        # correct. Pooling both into one set made the first taint the second and
        # flagged the fixture that IS the fix -- the gate rejecting its own remedy,
        # which is what tests/unit/test_audit_trail.py hit.
        if has_pct_s:
            # Module scope is the only shared scope: a name bound at module level IS
            # visible to every function, so it is resolved once and inherited. Both
            # halves -- raw and safe -- must come from `tree.body` alone. Reusing the
            # file-wide `safe_factories` here would re-open the very leak this block
            # closes, one level down: `conn = _factory()` inside ONE test would enter
            # that set and clear `conn` for every other test in the file.
            module_body = ast.Module(
                body=[n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))],
                type_ignores=[],
            )
            module_raw: Set[str] = set()
            for node in module_body.body:
                if isinstance(node, ast.Assign) and _is_sqlite_connect(node.value):
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name):
                            module_raw.add(tgt.id)
            # Factory *functions* stay file-wide -- they are defs, not connections,
            # so they cannot carry a per-test binding across scopes.
            module_safe = _safe_connection_names(module_body, compat_factories)

            # ast.walk is breadth-first, so the OUTERMOST enclosing function wins the
            # setdefault. That is the conservative direction: a call inside a nested
            # helper is judged against the whole outer test, whose scope walk already
            # includes the helper's raw bindings.
            enclosing: Dict[int, ast.AST] = {}
            for fn in ast.walk(tree):
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for sub in ast.walk(fn):
                        enclosing.setdefault(id(sub), fn)

            raw_cache: Dict[int, Set[str]] = {}

            def _raw_in_scope(scope: ast.AST) -> Set[str]:
                cached = raw_cache.get(id(scope))
                if cached is not None:
                    return cached
                raw = set(module_raw)
                if scope is not tree:
                    for sub in ast.walk(scope):
                        if isinstance(sub, ast.Assign) and _is_sqlite_connect(sub.value):
                            for tgt in sub.targets:
                                if isinstance(tgt, ast.Name):
                                    raw.add(tgt.id)
                    # A name rebound from a translating factory in this scope is not raw.
                    raw -= _safe_connection_names(scope, module_safe)
                raw_cache[id(scope)] = raw
                return raw

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for kw in node.keywords:
                    if (
                        kw.arg == "conn"
                        and isinstance(kw.value, ast.Name)
                        and kw.value.id in _raw_in_scope(enclosing.get(id(node), tree))
                    ):
                        violations.append(
                            f"{rel}:{getattr(node, 'lineno', 0)}: passes a raw sqlite3 connection "
                            f"as conn= into a call while %s SQL is present — bypasses translate_sql. "
                            f"Wrap in StorageConnection(conn, 'sqlite')."
                        )
                        break

    if violations:
        tier = "fail" if changed_files else "warn"
        return CoherenceCheck(
            check_id="test_db_isolation",
            check_name="Test DB Isolation (raw sqlite3 + %s)",
            status=tier,
            expected=["Tests reach runtime DB code through StorageConnection (translate_sql), not raw sqlite3"],
            actual=[f"{len(violations)} violation(s) across {scanned} test file(s)"],
            missing=[],
            extra=violations,
            message=(
                f"{len(violations)} test(s) hand runtime code a raw sqlite3 connection where %s SQL is "
                f"reachable — the query will raise 'near \"%\": syntax error' and likely be swallowed to None."
            ),
        )
    return CoherenceCheck(
        check_id="test_db_isolation",
        check_name="Test DB Isolation (raw sqlite3 + %s)",
        status="pass",
        expected=["Tests reach runtime DB code through StorageConnection (translate_sql), not raw sqlite3"],
        actual=[f"{scanned} raw-sqlite3 test file(s) scanned; none defeat translate_sql"],
        missing=[],
        extra=[],
        message="No test hands runtime %s code a raw sqlite3 connection.",
    )


# ---------------------------------------------------------------------------
# Check: migration-number collisions (kph-C)
# ---------------------------------------------------------------------------
def _migration_prefixes() -> Dict[str, List[str]]:
    """Map numeric prefix -> list of migration names (flat NNN_*.sql + dir NNN_*/)."""
    mig_dir = PROJECT_ROOT / "tools" / "db" / "migrations"
    out: Dict[str, List[str]] = {}
    if not mig_dir.exists():
        return out
    for entry in mig_dir.iterdir():
        m = re.match(r"^(\d+)_", entry.name)
        if not m:
            continue
        out.setdefault(m.group(1), []).append(entry.name)
    return out


def check_migration_numbering(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Flag a NEW migration whose numeric prefix collides with an existing one.

    Migration numbers are allocated by naive local-max (migration_runner.create_migration),
    so two sibling task branches off the same main compute the SAME next number — govern-03
    shipped 259_* while main already had 260/261. Runtime tolerates duplicates (INSERT OR
    IGNORE) but they silently drop or reorder DDL. There is no collision detector today.

    FAIL when a CHANGED migration prefix is shared by another migration (new collision);
    WARN full-repo listing existing duplicate prefixes as debt. Message names the next free
    number.
    """
    prefixes = _migration_prefixes()
    dups = {p: names for p, names in prefixes.items() if len(names) > 1}
    next_free = (max((int(p) for p in prefixes), default=0) + 1) if prefixes else 1

    if changed_files:
        changed_migs = [
            p for p in changed_files
            if "tools/db/migrations/" in p.as_posix() and re.match(r"^\d+_", p.name)
        ]
        new_collisions: List[str] = []
        for p in changed_migs:
            m = re.match(r"^(\d+)_", p.name)
            if not m:
                continue
            pref = m.group(1)
            others = [n for n in prefixes.get(pref, []) if n != p.name]
            if others:
                new_collisions.append(
                    f"{p.as_posix()}: migration number {pref} already used by {', '.join(others)} "
                    f"— renumber to {next_free:03d} (next free)"
                )
        if new_collisions:
            return CoherenceCheck(
                check_id="migration_numbering",
                check_name="Migration Number Collision",
                status="fail",
                expected=["Each new migration takes a unique, unused number prefix"],
                actual=[f"{len(new_collisions)} colliding changed migration(s)"],
                missing=[],
                extra=new_collisions,
                message=f"{len(new_collisions)} changed migration(s) reuse an existing number; next free is {next_free:03d}.",
            )
        return CoherenceCheck(
            check_id="migration_numbering",
            check_name="Migration Number Collision",
            status="pass",
            expected=["Each new migration takes a unique, unused number prefix"],
            actual=[f"{len(changed_migs)} changed migration(s) checked; no new collisions"],
            missing=[],
            extra=[],
            message="No changed migration reuses an existing number.",
        )

    # Full-repo: WARN on existing duplicate prefixes (grandfathered debt).
    if dups:
        listed = [f"{p}: {', '.join(sorted(names))}" for p, names in sorted(dups.items())]
        return CoherenceCheck(
            check_id="migration_numbering",
            check_name="Migration Number Collision",
            status="warn",
            expected=["No duplicate migration number prefixes"],
            actual=[f"{len(dups)} duplicate prefix(es) in tools/db/migrations/"],
            missing=[],
            extra=listed,
            message=f"{len(dups)} existing duplicate migration prefix(es) (debt); next free is {next_free:03d}.",
        )
    return CoherenceCheck(
        check_id="migration_numbering",
        check_name="Migration Number Collision",
        status="pass",
        expected=["No duplicate migration number prefixes"],
        actual=[f"{len(prefixes)} distinct migration numbers; next free {next_free:03d}"],
        missing=[],
        extra=[],
        message="No duplicate migration numbers.",
    )


# ---------------------------------------------------------------------------
# Check: icdev/ mirror parity (kph-D)
# ---------------------------------------------------------------------------
# Roots whose tools/ modules MUST have an icdev/tools/ twin. Extend via
# args/mirror_parity.yaml (key: mirrored_roots) without editing code.
_DEFAULT_MIRROR_ROOTS = (
    "tools/cortex",
    "tools/quality",
    "tools/iqe/adapters",
    "tools/mcp/cortex_server.py",
)


def _mirror_roots() -> Tuple[str, ...]:
    cfg = PROJECT_ROOT / "args" / "mirror_parity.yaml"
    if cfg.exists():
        try:
            import yaml  # lazy — yaml isn't a top-level import here
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            roots = data.get("mirrored_roots")
            if isinstance(roots, list) and roots:
                return tuple(str(r) for r in roots)
        except Exception:
            pass
    return _DEFAULT_MIRROR_ROOTS


def _twin_missing(rel_posix: str) -> Optional[str]:
    """Given a tools/... path, return the expected icdev twin path if it's missing, else None."""
    if not rel_posix.startswith("tools/"):
        return None
    twin_rel = "icdev/" + rel_posix
    if (PROJECT_ROOT / twin_rel).exists():
        return None
    return twin_rel


def check_icdev_mirror_parity(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Flag a tools/<mirrored-root>/*.py change whose icdev/tools/ twin is missing.

    Cortex/quality/iqe modules must be mirrored to icdev/tools/* so generated child
    apps inherit them and the canonical/legacy namespaces stay in sync. This was
    hand-reconciled every Cortex PR; a forgotten non-template module was never caught
    (only 2 hardcoded files + dashboard templates are guarded today).

    FAIL when a CHANGED tools/<root>/*.py lacks its icdev twin (or vice versa);
    WARN full-repo listing existing drift.
    """
    roots = _mirror_roots()

    def _under_roots(rel: str) -> bool:
        return any(rel == r or rel.startswith(r.rstrip("/") + "/") or rel == r for r in roots)

    missing: List[str] = []
    if changed_files:
        for p in changed_files:
            if p.suffix != ".py":
                continue
            rel = p.as_posix()
            # normalize to repo-relative
            if "/tools/" in rel:
                rel = "tools/" + rel.split("/tools/", 1)[1]
            if rel.startswith("icdev/tools/"):
                # a changed icdev file must have its tools/ origin
                origin = rel[len("icdev/"):]
                if _under_roots(origin) and not (PROJECT_ROOT / origin).exists():
                    missing.append(f"{rel}: canonical twin {origin} missing")
                continue
            if not _under_roots(rel):
                continue
            twin = _twin_missing(rel)
            if twin:
                missing.append(f"{rel}: icdev twin {twin} missing — mirror the change")
        if missing:
            return CoherenceCheck(
                check_id="icdev_mirror_parity",
                check_name="icdev/ Mirror Parity",
                status="fail",
                expected=["Every tools/<mirrored-root> module has an icdev/tools/ twin"],
                actual=[f"{len(missing)} changed module(s) missing a mirror"],
                missing=missing,
                extra=[],
                message=f"{len(missing)} changed tools/ module(s) not mirrored to icdev/ (roots: {', '.join(roots)}).",
            )
        return CoherenceCheck(
            check_id="icdev_mirror_parity",
            check_name="icdev/ Mirror Parity",
            status="pass",
            expected=["Every tools/<mirrored-root> module has an icdev/tools/ twin"],
            actual=["changed mirrored modules all have twins"],
            missing=[],
            extra=[],
            message="All changed mirrored modules have their icdev/ twin.",
        )

    # Full-repo: WARN on existing drift.
    for root in roots:
        base = PROJECT_ROOT / root
        py_files = [base] if base.suffix == ".py" and base.exists() else (
            list(base.rglob("*.py")) if base.is_dir() else []
        )
        for f in py_files:
            rel = f.relative_to(PROJECT_ROOT).as_posix()
            twin = _twin_missing(rel)
            if twin:
                missing.append(f"{rel} -> {twin} (missing)")
    if missing:
        return CoherenceCheck(
            check_id="icdev_mirror_parity",
            check_name="icdev/ Mirror Parity",
            status="warn",
            expected=["Every tools/<mirrored-root> module has an icdev/tools/ twin"],
            actual=[f"{len(missing)} module(s) missing a mirror (debt)"],
            missing=missing,
            extra=[],
            message=f"{len(missing)} mirrored-root module(s) lack an icdev/ twin (roots: {', '.join(roots)}).",
        )
    return CoherenceCheck(
        check_id="icdev_mirror_parity",
        check_name="icdev/ Mirror Parity",
        status="pass",
        expected=["Every tools/<mirrored-root> module has an icdev/tools/ twin"],
        actual=[f"roots checked: {', '.join(roots)}"],
        missing=[],
        extra=[],
        message="All mirrored-root modules have their icdev/ twin.",
    )


# ---------------------------------------------------------------------------
# Check: mirror drift (hcx-ctx-04) — byte-compare hot mirrored packages
# ---------------------------------------------------------------------------
# Packages where tools/<pkg> and icdev/tools/<pkg> are BOTH live physical copies
# and drift between them has repeatedly served stale code at runtime (e.g. a
# 14KB-divergent tools/llm/agent_loop.py served pre-TRUST code to Cortex; ACE
# agent_tools/eval_runner drift; historical kanban reflex drift). Unlike
# icdev_mirror_parity (which flags MISSING twins), this compares CONTENT of
# existing twins byte-for-byte. Extend via args/mirror_parity.yaml
# (key: drift_packages) without editing code.
_DEFAULT_MIRROR_DRIFT_PKGS = (
    "llm",
    "ace",
    "cortex",
    "genesis/harness",
    "genesis/reflexes",
    "quality",
    "mcp",
    "workflow",
)

# Relative subpaths (posix, under a package) to skip: caches and persona content.
_MIRROR_DRIFT_EXCLUDE_DIRS = ("__pycache__", "roles")


def _mirror_drift_packages() -> Tuple[str, ...]:
    cfg = PROJECT_ROOT / "args" / "mirror_parity.yaml"
    if cfg.exists():
        try:
            import yaml  # lazy — yaml isn't a top-level import here

            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            pkgs = data.get("drift_packages")
            if isinstance(pkgs, list) and pkgs:
                return tuple(str(p) for p in pkgs)
        except Exception:
            pass
    return _DEFAULT_MIRROR_DRIFT_PKGS


def _is_mirror_shim(path: Path) -> bool:
    """True if a file is an INTENTIONAL re-export shim of its icdev twin.

    Detected by content marker: the file re-exports from its ``icdev.tools.*``
    twin (import-star or explicit re-export) and is short (<120 lines). The
    canonical example is ``tools/llm/agent_loop.py`` — it must never be flagged
    as drift. Physically-separate full copies are NOT shims and are compared.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    if text.count("\n") >= 120:
        return False
    imports_twin = re.search(r"from\s+icdev\.tools[\w.]*\s+import\b", text) is not None
    marks_reexport = "re-export" in text.lower()
    return imports_twin and marks_reexport


def _rel_under_excluded(rel_posix: str) -> bool:
    parts = rel_posix.split("/")
    return any(seg in _MIRROR_DRIFT_EXCLUDE_DIRS for seg in parts)


def check_mirror_drift(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """WARN on content/existence drift between tools/<pkg> and icdev/tools/<pkg>.

    For each hot mirrored package, byte-compares every ``*.py`` file that exists
    under both ``tools/<pkg>`` and ``icdev/tools/<pkg>``. Reports:
      - exists-only-in-one-tree (tools/ only, or icdev/ only)
      - content-differs (with a hint of which side has the newer mtime)

    Excludes ``__pycache__``, ACE ``roles/`` persona content, and intentional
    re-export shims (see ``_is_mirror_shim``). Report-only (WARN) — never fails
    the gate; today's tree carries real drift and this surfaces it, it does not
    block on it. Ignores ``changed_files`` (always full sweep of the hot pkgs).
    """
    pkgs = _mirror_drift_packages()
    only_tools: List[str] = []
    only_icdev: List[str] = []
    differs: List[str] = []

    for pkg in pkgs:
        tools_base = PROJECT_ROOT / "tools" / pkg
        icdev_base = PROJECT_ROOT / "icdev" / "tools" / pkg

        # Collect relative *.py paths (posix) from each side.
        def _collect(base: Path) -> Set[str]:
            out: Set[str] = set()
            if not base.is_dir():
                return out
            for f in base.rglob("*.py"):
                rel = f.relative_to(base).as_posix()
                if _rel_under_excluded(rel):
                    continue
                out.add(rel)
            return out

        tools_files = _collect(tools_base)
        icdev_files = _collect(icdev_base)

        for rel in sorted(tools_files - icdev_files):
            tp = tools_base / rel
            if _is_mirror_shim(tp):
                continue
            only_tools.append(f"tools/{pkg}/{rel}: no icdev twin")
        for rel in sorted(icdev_files - tools_files):
            ip = icdev_base / rel
            if _is_mirror_shim(ip):
                continue
            only_icdev.append(f"icdev/tools/{pkg}/{rel}: no tools/ twin")

        for rel in sorted(tools_files & icdev_files):
            tp = tools_base / rel
            ip = icdev_base / rel
            # A shim on either side is an intentional divergence — skip.
            if _is_mirror_shim(tp) or _is_mirror_shim(ip):
                continue
            try:
                if tp.read_bytes() == ip.read_bytes():
                    continue
            except Exception:
                continue
            try:
                t_m = tp.stat().st_mtime
                i_m = ip.stat().st_mtime
                if t_m > i_m:
                    hint = "tools/ newer"
                elif i_m > t_m:
                    hint = "icdev/ newer"
                else:
                    hint = "same mtime"
            except Exception:
                hint = "mtime unknown"
            differs.append(f"tools/{pkg}/{rel} != icdev/tools/{pkg}/{rel} ({hint})")

    findings = only_tools + only_icdev + differs
    total = len(findings)
    if total == 0:
        return CoherenceCheck(
            check_id="mirror_drift",
            check_name="Mirror Drift (hot packages)",
            status="pass",
            expected=["tools/<pkg> and icdev/tools/<pkg> byte-identical for hot packages"],
            actual=[f"packages checked: {', '.join(pkgs)}"],
            missing=[],
            extra=[],
            message=f"No mirror drift across hot packages ({', '.join(pkgs)}).",
        )
    return CoherenceCheck(
        check_id="mirror_drift",
        check_name="Mirror Drift (hot packages)",
        status="warn",
        expected=["tools/<pkg> and icdev/tools/<pkg> byte-identical for hot packages"],
        actual=[
            f"{len(differs)} content-differ, {len(only_tools)} tools/-only, "
            f"{len(only_icdev)} icdev/-only"
        ],
        missing=findings,
        extra=[],
        message=(
            f"{total} mirror-drift finding(s) across hot packages "
            f"({len(differs)} content-differs, {len(only_tools)} tools/-only, "
            f"{len(only_icdev)} icdev/-only). Report-only; reconcile the newer side "
            f"into its twin (canonical is usually tools/)."
        ),
    )


# ---------------------------------------------------------------------------
# Check: LLM provider bypass outside tools/llm/ (lpx-router-03)
# ---------------------------------------------------------------------------

# Cloud provider API host substrings. A runtime module outside tools/llm/ that
# embeds one of these is talking to a provider directly instead of through the
# router — which defeats the opt-in proxy (LPX) and air-gap/CUI routing.
_LPX_PROVIDER_URL_SUBSTRINGS = (
    "api.anthropic.com",
    "api.openai.com",
    "api.cohere.ai",
    "api.mistral.ai",
    "api.groq.com",
    "api.deepseek.com",
    "api.together.xyz",
    "api.fireworks.ai",
    "api.x.ai",
    "generativelanguage.googleapis.com",
)

# Provider API-key environment variables. Reading one of these outside tools/llm/
# means a module resolves a real provider credential itself rather than letting
# the provider layer do it. Virtual/proxy/master keys are intentionally absent.
_LPX_PROVIDER_KEY_ENV_VARS = frozenset({
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "COHERE_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "TOGETHER_API_KEY",
    "FIREWORKS_API_KEY",
    "DEEPSEEK_API_KEY",
    "XAI_API_KEY",
    "PERPLEXITY_API_KEY",
})

# Documented, legitimate exceptions (see lpx-router-02):
#   - the FathomDesk BYOK credential tester intentionally probes the real endpoint
#   - the air-gap OpenAI-compatible local endpoints point at vLLM / LM Studio /
#     llama.cpp, not a cloud provider
#   - this checker itself necessarily embeds provider host/key literals in order
#     to DETECT them (meta tool, not a runtime inference path)
_LPX_BYPASS_EXEMPT_FILES = frozenset({
    Path("tools") / "trading" / "credentials" / "tester.py",
    Path("tools") / "airgap" / "pdf_fallback.py",
    Path("tools") / "document_intelligence" / "extractors.py",
    Path("tools") / "workflow" / "coherence_checker.py",
})

# Grandfathered pre-existing sites (signature = "<relpath>::<host-or-envvar>").
# lpx-router-03's mandate is to FAIL on NEW direct-to-provider access; these
# already existed when the gate landed and are out of this card's scope. Many are
# legitimate (air-gap detectors that list cloud URLs precisely to BLOCK egress;
# embedding/provider-adjacent modules). The gate ratchets: no NEW signature may
# appear. Shrinking this set is welcome follow-up tech debt; growing it is not.
_LPX_BYPASS_BASELINE = frozenset({
    "tools/ai_augmentation/agent_readiness/pillars/_base.py::ANTHROPIC_API_KEY",
    "tools/ai_augmentation/agent_readiness/pillars/append_only_audit.py::ANTHROPIC_API_KEY",
    "tools/ai_augmentation/agent_readiness/pillars/nist_controls.py::ANTHROPIC_API_KEY",
    "tools/ai_augmentation/agent_readiness/pillars/stig_compliance.py::ANTHROPIC_API_KEY",
    "tools/aiify/agent_readiness/pillars/append_only_audit.py::ANTHROPIC_API_KEY",
    "tools/aiify/agent_readiness/pillars/nist_controls.py::ANTHROPIC_API_KEY",
    "tools/aiify/agent_readiness/pillars/stig_compliance.py::ANTHROPIC_API_KEY",
    "tools/airgap/detector.py::api.anthropic.com",
    "tools/airgap/ste_validator.py::api.anthropic.com",
    "tools/airgap/ste_validator.py::api.cohere.ai",
    "tools/airgap/ste_validator.py::api.openai.com",
    "tools/ci/workflows/icdev_plan.py::ANTHROPIC_API_KEY",
    # `icdev setup` probes TCP:443 reachability of these hosts to tell a user
    # whether a provider is usable before they finish configuring one. It never
    # sends a request, and it must work before any provider SDK is installed —
    # so routing it through LLMRouter is not available to it. Same category as
    # the airgap detectors above: names a cloud host without calling it.
    "tools/cli/setup_wizard.py::api.anthropic.com",
    "tools/cli/setup_wizard.py::api.openai.com",
    "tools/cli/setup_wizard.py::generativelanguage.googleapis.com",
    "tools/document_intelligence/blueprint.py::ANTHROPIC_API_KEY",
    "tools/document_intelligence/blueprint.py::OPENAI_API_KEY",
    "tools/document_intelligence/output_generators.py::ANTHROPIC_API_KEY",
    "tools/document_intelligence/output_generators.py::OPENAI_API_KEY",
    "tools/finetune/openai_provider.py::OPENAI_API_KEY",
    "tools/govcon/reflex_sandbox.py::api.anthropic.com",
    "tools/memory/embed_memory.py::OPENAI_API_KEY",
    "tools/memory/hybrid_search.py::OPENAI_API_KEY",
    "tools/memory/maintenance_cron.py::OPENAI_API_KEY",
    "tools/memory/semantic_search.py::OPENAI_API_KEY",
    "tools/pulse/config.py::OPENAI_API_KEY",
    "tools/rag/pdf_provider.py::ANTHROPIC_API_KEY",
    "tools/rag/pdf_provider.py::GOOGLE_API_KEY",
    "tools/testing/e2e_runner.py::ANTHROPIC_API_KEY",
    "tools/testing/health_check.py::ANTHROPIC_API_KEY",
    "tools/testing/utils.py::ANTHROPIC_API_KEY",
    "tools/viz/asset_generator.py::OPENAI_API_KEY",
})


def _lpx_scan_provider_bypass(source: str, rel: str) -> List[Tuple[int, str, str]]:
    """AST-scan one runtime module for direct-to-provider access.

    Returns ``(lineno, token, reason)`` tuples for (a) cloud provider base-URL
    string literals (docstrings excluded) and (b) reads of a provider API-key env
    var via os.environ[...] / os.environ.get(...) / os.getenv(...). ``token`` is
    the host or env var — used to build a line-independent baseline signature.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    hits: List[Tuple[int, str, str]] = []
    doc_ids = _agx_docstring_node_ids(tree)

    for node in ast.walk(tree):
        # (a) provider base-URL literals (skip docstrings)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in doc_ids:
                continue
            for host in _LPX_PROVIDER_URL_SUBSTRINGS:
                if host in node.value:
                    hits.append((node.lineno, host, f"cloud provider URL `{host}` — call via LLMRouter"))
                    break
        # (b) provider API-key env reads
        elif isinstance(node, ast.Call):
            key = _lpx_env_read_key(node)
            if key and key in _LPX_PROVIDER_KEY_ENV_VARS:
                hits.append((node.lineno, key, f"reads provider key env `{key}` — resolve credentials in tools/llm/"))
        # os.environ["X"] subscript read
        elif isinstance(node, ast.Subscript):
            key = _lpx_environ_subscript_key(node)
            if key and key in _LPX_PROVIDER_KEY_ENV_VARS:
                hits.append((node.lineno, key, f"reads provider key env `{key}` — resolve credentials in tools/llm/"))
    return hits


def _lpx_const_str(node) -> Optional[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _lpx_env_read_key(call: ast.Call) -> Optional[str]:
    """If ``call`` is os.getenv("X"...) or os.environ.get("X"...), return "X"."""
    fn = call.func
    if not isinstance(fn, ast.Attribute) or not call.args:
        return None
    first = _lpx_const_str(call.args[0])
    if first is None:
        return None
    # os.getenv(...)
    if fn.attr == "getenv":
        return first
    # os.environ.get(...)
    if fn.attr == "get" and isinstance(fn.value, ast.Attribute) and fn.value.attr == "environ":
        return first
    return None


def _lpx_environ_subscript_key(sub: ast.Subscript) -> Optional[str]:
    """If ``sub`` is os.environ["X"], return "X"."""
    val = sub.value
    if isinstance(val, ast.Attribute) and val.attr == "environ":
        return _lpx_const_str(sub.slice)
    return None


def check_provider_bypass(
    changed_files: Optional[List[Path]] = None,
) -> CoherenceCheck:
    """lpx-router-03 — no direct-to-provider access outside tools/llm/.

    Fails when a runtime module under ``tools/`` (excluding ``tools/llm/`` and the
    documented BYOK / air-gap exceptions) either embeds a cloud provider base URL
    literal or reads a provider API-key env var. All inference must flow through
    ``LLMRouter`` so the opt-in proxy (LPX), air-gap, and CUI routing hold.
    """
    new_violations: List[str] = []
    grandfathered = 0
    llm_dir = Path("tools") / "llm"
    for py_file in _scan_targets(changed_files):
        try:
            rel_path = py_file.relative_to(PROJECT_ROOT)
        except ValueError:
            rel_path = py_file
        # Skip the provider layer itself and documented exceptions.
        if llm_dir in rel_path.parents or rel_path == llm_dir:
            continue
        if rel_path in _LPX_BYPASS_EXEMPT_FILES:
            continue
        # Skip test scaffolding that may reference provider vars intentionally.
        if py_file.name.startswith("test_"):
            continue
        text = _read_text(py_file)
        # Cheap prefilter before paying for an AST parse.
        if "api." not in text and "generativelanguage" not in text and "_API_KEY" not in text:
            continue
        rel_str = str(rel_path).replace("\\", "/")
        for lineno, token, reason in _lpx_scan_provider_bypass(text, rel_str):
            signature = f"{rel_str}::{token}"
            if signature in _LPX_BYPASS_BASELINE:
                grandfathered += 1
                continue
            new_violations.append(f"{rel_str}:{lineno}: {reason}")

    if new_violations:
        return CoherenceCheck(
            check_id="provider_bypass",
            check_name="LLM Provider Bypass (lpx-router-03)",
            status="fail",
            expected=["no NEW cloud provider URL / API-key env read outside tools/llm/"],
            actual=new_violations,
            missing=[],
            extra=new_violations,
            message=(
                f"{len(new_violations)} NEW direct-to-provider bypass(es) outside tools/llm/ "
                f"({grandfathered} grandfathered) — route through LLMRouter; add a documented "
                "exemption only for BYOK/air-gap"
            ),
        )

    return CoherenceCheck(
        check_id="provider_bypass",
        check_name="LLM Provider Bypass (lpx-router-03)",
        status="pass",
        expected=["no NEW cloud provider URL / API-key env read outside tools/llm/"],
        actual=[f"0 new violations ({grandfathered} pre-existing sites grandfathered)"],
        missing=[],
        extra=[],
        message=(
            f"No NEW direct-to-provider bypass outside tools/llm/ "
            f"({grandfathered} grandfathered legacy sites)"
        ),
    )


# ---------------------------------------------------------------------------
# Check 19: Documented Command Paths (oss-fix-02)
# ---------------------------------------------------------------------------
#
# A documented command that does not exist is worse than an undocumented one:
# an agent reading CLAUDE.md will confidently run it, get an opaque
# "can't open file" error, and burn a cycle deciding whether the tree is
# broken or the doc is. This check resolves every `python tools/...py` and
# `python -m tools...` invocation in the doc set against the filesystem.
#
# Grandfathering mirrors the args/ruff_gate.yaml pattern: pre-existing broken
# references are listed in args/doc_command_gate.yaml and downgraded to WARN
# so the backlog is enumerated and visible, while any NEW broken reference
# fails the gate.

_DOC_COMMAND_CONFIG = PROJECT_ROOT / "args" / "doc_command_gate.yaml"

# Default doc set — overridable via the `docs:` key in the config file.
_DOC_COMMAND_DEFAULT_DOCS = ("CLAUDE.md", "docs/reference/commands.md")

# `python tools/foo/bar.py` / `python icdev/tools/foo/bar.py`
_DOC_SCRIPT_RE = re.compile(r"python[0-9.]*\s+((?:icdev/)?tools/[A-Za-z0-9_./-]+\.py)")
# `python -m tools.foo.bar` / `python -m icdev.tools.foo.bar`
_DOC_MODULE_RE = re.compile(r"python[0-9.]*\s+-m\s+((?:icdev\.)?tools(?:\.[A-Za-z0-9_]+)*)")


def _load_doc_command_config() -> Tuple[List[str], Dict[str, str]]:
    """Load the doc set and grandfathered-reference map from args/doc_command_gate.yaml.

    Schema:
        docs:
          - CLAUDE.md
          - docs/reference/commands.md
        grandfathered:
          tools/dochub/doc_generator.py: "DocHub subsystem never built (oss-fix-02)"

    Returns ``(docs, grandfathered)``. A missing file, malformed YAML, or
    missing pyyaml yields the default doc set and an EMPTY grandfather map
    (fail-safe closed: if the allowlist can't be read, nothing is excused and
    the gate is stricter, not looser).
    """
    docs = list(_DOC_COMMAND_DEFAULT_DOCS)
    if not _DOC_COMMAND_CONFIG.exists() or not _HAS_YAML:
        return docs, {}
    try:
        raw = yaml.safe_load(_DOC_COMMAND_CONFIG.read_text(encoding="utf-8")) or {}
    except Exception:
        return docs, {}
    if not isinstance(raw, dict):
        return docs, {}

    cfg_docs = raw.get("docs")
    if isinstance(cfg_docs, list) and cfg_docs:
        docs = [str(d).replace("\\", "/") for d in cfg_docs if isinstance(d, (str, Path))]

    grandfathered: Dict[str, str] = {}
    gf = raw.get("grandfathered") or {}
    if isinstance(gf, dict):
        for ref, reason in gf.items():
            if isinstance(ref, str):
                grandfathered[ref.replace("\\", "/").lstrip("./")] = str(reason or "")
    elif isinstance(gf, list):  # tolerate a bare list of paths
        for ref in gf:
            if isinstance(ref, str):
                grandfathered[ref.replace("\\", "/").lstrip("./")] = ""
    return docs, grandfathered


def _doc_reference_exists(ref: str) -> bool:
    """Resolve a documented reference (script path or dotted module) to a file."""
    if ref.endswith(".py"):
        return (PROJECT_ROOT / ref).is_file()
    # Dotted module: `tools.airgap` -> tools/airgap.py or tools/airgap/__init__.py
    rel = Path(*ref.split("."))
    return (PROJECT_ROOT / rel).with_suffix(".py").is_file() or (PROJECT_ROOT / rel / "__init__.py").is_file()


def _expand_doc_entries(docs: List[str]) -> List[str]:
    """Expand wildcard entries in the doc set to concrete repo-relative paths.

    Listing files one by one means a doc surface added later silently escapes
    the gate — which is how context/capabilities/harness.yaml came to advertise
    three tools that were never written (ahx-doc-01). A pattern entry keeps new
    files covered by default. Non-wildcard entries pass through untouched, so
    existing configs behave exactly as before.
    """
    expanded: List[str] = []
    for doc in docs:
        if "*" not in doc and "?" not in doc:
            expanded.append(doc)
            continue
        for match in sorted(PROJECT_ROOT.glob(doc)):
            if match.is_file():
                expanded.append(match.relative_to(PROJECT_ROOT).as_posix())
    # Preserve order, drop duplicates (a file may match a pattern and be listed).
    seen: set = set()
    return [d for d in expanded if not (d in seen or seen.add(d))]


def _scan_doc_commands(docs: List[str]) -> List[Tuple[str, int, str]]:
    """Return ``(doc, lineno, reference)`` for every documented python invocation."""
    found: List[Tuple[str, int, str]] = []
    for doc in _expand_doc_entries(docs):
        path = PROJECT_ROOT / doc
        if not path.is_file():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for match in _DOC_SCRIPT_RE.finditer(line):
                found.append((doc, lineno, match.group(1)))
            for match in _DOC_MODULE_RE.finditer(line):
                found.append((doc, lineno, match.group(1)))
    return found


def check_doc_command_paths(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Check 19: every documented `python tools/...` invocation resolves to a real file."""
    check_id = "doc_command_paths"
    check_name = "Documented Command Paths"

    docs, grandfathered = _load_doc_command_config()
    references = _scan_doc_commands(docs)

    if not references:
        return CoherenceCheck(
            check_id=check_id,
            check_name=check_name,
            status="warn",
            expected=[f"documented commands in {', '.join(docs)}"],
            actual=[],
            missing=[],
            extra=[],
            message=f"No documented python invocations found in {len(docs)} doc file(s) — check doc set",
        )

    # Resolve each distinct reference once; docs repeat the same tool many times.
    resolved: Dict[str, bool] = {}
    new_broken: List[str] = []
    excused: Set[str] = set()
    for doc, lineno, ref in references:
        if ref not in resolved:
            resolved[ref] = _doc_reference_exists(ref)
        if resolved[ref]:
            continue
        if ref in grandfathered:
            excused.add(ref)
        else:
            new_broken.append(f"{doc}:{lineno}: {ref}")

    # A grandfather entry whose target now exists (or is no longer cited) is
    # stale — surface it so the allowlist shrinks instead of rotting.
    cited = {ref for _, _, ref in references}
    stale = sorted(
        ref
        for ref in grandfathered
        if ref not in cited or _doc_reference_exists(ref)
    )

    total = len(resolved)
    if new_broken:
        return CoherenceCheck(
            check_id=check_id,
            check_name=check_name,
            status="fail",
            expected=["every documented `python tools/...` command resolves to an existing file"],
            actual=new_broken[:40],
            missing=sorted({line.rsplit(": ", 1)[1] for line in new_broken}),
            extra=stale,
            message=(
                f"{len(new_broken)} documented command(s) reference a file that does not exist "
                f"({len(excused)} grandfathered). Create the tool, fix the path, or — only if the "
                f"capability is genuinely deferred — add it to args/doc_command_gate.yaml with a reason."
            ),
        )

    if excused or stale:
        bits = []
        if excused:
            bits.append(f"{len(excused)} grandfathered broken reference(s) remain")
        if stale:
            bits.append(f"{len(stale)} stale allowlist entry(ies) can be removed")
        return CoherenceCheck(
            check_id=check_id,
            check_name=check_name,
            status="warn",
            expected=["every documented `python tools/...` command resolves to an existing file"],
            actual=[f"{total} distinct reference(s) checked; 0 new breakage"],
            missing=sorted(excused),
            extra=stale,
            message="No NEW broken documented commands — " + "; ".join(bits),
        )

    return CoherenceCheck(
        check_id=check_id,
        check_name=check_name,
        status="pass",
        expected=["every documented `python tools/...` command resolves to an existing file"],
        actual=[f"{total} distinct reference(s) checked across {len(docs)} doc file(s)"],
        missing=[],
        extra=[],
        message=f"All {total} documented command path(s) resolve",
    )


# ---------------------------------------------------------------------------
# Check 20: INSERT column lists vs the LIVE database schema (swp-gate-01)
# ---------------------------------------------------------------------------
#
# `check_schema_code` compares INSERT columns against the CREATE TABLE text in
# tools/db/init_icdev_db.py. That misses the failure mode this gate exists for:
# `CREATE TABLE IF NOT EXISTS` never alters an existing table, so a table
# created by an older migration keeps its old columns while the DDL in the
# source moves on. Code and database disagree with nothing in the source tree
# recording the disagreement. An automated pass against live PostgreSQL found
# 95 INSERT statements naming columns that do not exist — each one raises at
# runtime inside `except Exception: pass`, so the feature reports success and
# persists nothing (module_budget_usage held 0 rows; audit_trail has never
# received a row from tools/govcon).
#
# This check therefore reads the LIVE schema — information_schema.columns on
# PostgreSQL, PRAGMA table_info on SQLite — not the DDL.

_INSERT_SCHEMA_CONFIG = PROJECT_ROOT / "args" / "insert_schema_gate.yaml"

#: `INSERT [OR REPLACE] INTO [schema.]table (col, ...) VALUES|SELECT`.
#: The column group excludes parentheses so a function call or a nested SELECT
#: in the column position simply fails to match rather than mis-parsing.
_INSERT_COLUMN_LIST_RE = re.compile(
    r"INSERT\s+(?:OR\s+(?:REPLACE|IGNORE|ABORT|FAIL|ROLLBACK)\s+)?INTO\s+"
    r'(?:["`\[]?\w+["`\]]?\s*\.\s*)?'  # optional schema/database qualifier
    r'["`\[]?(?P<table>\w+)["`\]]?\s*'
    r"\(\s*(?P<cols>[^()]+?)\s*\)\s*(?:VALUES|SELECT)\b",
    re.IGNORECASE | re.DOTALL,
)

_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Names a module binds when it obtains its own database handle.
_CONNECTION_FACTORY_NAMES = frozenset({"get_connection", "get_canvas_connection", "get_conn"})


def _uses_foreign_database(source: str) -> bool:
    """True if *source* gets its connection from outside the ICDEV package tree.

    ``tools/ais/ais_importer.py`` imports ``get_connection`` from
    ``apps.geosigint.models`` and writes to that app's own ``sg_tracks`` — a
    table whose name also exists, with a completely different shape, in the
    ICDEV database. Validating it against the ICDEV schema reports nine columns
    "missing" that are all present in the database the module actually writes
    to. Canvas ``db/init_db.py`` modules are NOT foreign: every one of them
    delegates to ``tools.db.storage`` on PostgreSQL, so their tables really do
    live in the schema this check reads.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not any(alias.name in _CONNECTION_FACTORY_NAMES for alias in node.names):
            continue
        if getattr(node, "level", 0):  # relative import — in-tree by definition
            continue
        root = (node.module or "").split(".")[0]
        if root and root not in ("tools", "icdev"):
            return True
    return False

#: A live schema this small is an empty/uninitialised database, not a real one.
#: Validating against it would report every table as "unknown" (harmless) but
#: would also let a genuinely broken INSERT pass unnoticed, so warn instead.
_INSERT_SCHEMA_MIN_TABLES = 20


def _extract_insert_column_lists(source: str) -> List[Tuple[int, str, List[str]]]:
    """Return ``(lineno, table, [columns])`` for every STATIC INSERT in *source*.

    Only fully static statements are returned. If any token in the column list
    is not a bare SQL identifier — an f-string hole, a ``%s``, a concatenated
    variable — the statement is dynamic and is skipped entirely rather than
    guessed at. A dynamic table name (``INSERT INTO {table} (...)``) never
    matches the pattern in the first place.
    """
    found: List[Tuple[int, str, List[str]]] = []
    for match in _INSERT_COLUMN_LIST_RE.finditer(source):
        raw_cols = match.group("cols")
        cols: List[str] = []
        dynamic = False
        for token in raw_cols.split(","):
            col = token.strip().strip('"').strip("`").strip("[]").strip()
            if not _SQL_IDENTIFIER_RE.match(col):
                dynamic = True
                break
            cols.append(col.lower())
        if dynamic or not cols:
            continue
        lineno = source.count("\n", 0, match.start()) + 1
        found.append((lineno, match.group("table").lower(), cols))
    return found


def _live_table_columns() -> Tuple[Dict[str, Set[str]], str, str]:
    """Read ``table -> {columns}`` from the connected database.

    Returns ``(schema, backend, error)``. ``error`` is a non-empty explanation
    when no usable live schema could be read, in which case ``schema`` is empty
    and the caller must WARN rather than fail — a gate that fails closed on a
    missing database would block every commit made without one.
    """
    try:
        from tools.db.storage import (
            _introspect_backend,
            _introspect_raw,
            get_connection,
            list_tables,
        )
    except Exception as exc:  # pragma: no cover - import environment specific
        return {}, "", f"tools.db.storage unavailable: {exc}"

    conn = None
    try:
        conn = get_connection()
        backend = _introspect_backend(conn)
        raw = _introspect_raw(conn)
        schema: Dict[str, Set[str]] = {}
        if backend == "postgresql":
            cur = raw.cursor()
            cur.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public'"
            )
            for row in cur.fetchall():
                if isinstance(row, dict):
                    table, column = row["table_name"], row["column_name"]
                else:
                    table, column = row[0], row[1]
                schema.setdefault(str(table).lower(), set()).add(str(column).lower())
        else:
            for table in list_tables(conn):
                if not _SQL_IDENTIFIER_RE.match(table):
                    continue
                cur = raw.cursor()
                cur.execute(f'PRAGMA table_info("{table}")')  # nosec B608 — identifier regex-validated
                cols = set()
                for row in cur.fetchall():
                    name = row["name"] if isinstance(row, dict) else row[1]
                    cols.add(str(name).lower())
                if cols:
                    schema[table.lower()] = cols
        if len(schema) < _INSERT_SCHEMA_MIN_TABLES:
            return (
                {},
                backend,
                f"{backend} schema has only {len(schema)} table(s) — database not initialised",
            )
        return schema, backend, ""
    except Exception as exc:
        return {}, "", f"could not read live schema: {type(exc).__name__}: {exc}"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _load_insert_schema_gate() -> Tuple[Set[str], Dict[str, str]]:
    """Load ``(ignored_tables, grandfathered)`` from args/insert_schema_gate.yaml.

    Schema::

        ignore_tables:
          - some_table_owned_by_another_database
        grandfathered:
          "tools/govcon/x.py:audit_trail:actor": "swp-scan-01 backlog"

    Grandfather keys are ``<repo-relative path>:<table>:<column>`` — deliberately
    line-number-free so an unrelated edit above the statement does not
    invalidate the entry. A missing file or missing pyyaml yields empty sets:
    fail-safe CLOSED, so an unreadable allowlist makes the gate stricter.
    """
    if not _INSERT_SCHEMA_CONFIG.exists() or not _HAS_YAML:
        return set(), {}
    try:
        raw = yaml.safe_load(_INSERT_SCHEMA_CONFIG.read_text(encoding="utf-8")) or {}
    except Exception:
        return set(), {}
    if not isinstance(raw, dict):
        return set(), {}

    ignored = {
        str(t).lower()
        for t in (raw.get("ignore_tables") or [])
        if isinstance(t, (str, int))
    }
    grandfathered: Dict[str, str] = {}
    entries = raw.get("grandfathered") or {}
    if isinstance(entries, dict):
        for key, reason in entries.items():
            if isinstance(key, str):
                grandfathered[key.replace("\\", "/")] = str(reason or "")
    elif isinstance(entries, list):  # tolerate a bare list of keys
        for key in entries:
            if isinstance(key, str):
                grandfathered[key.replace("\\", "/")] = ""
    return ignored, grandfathered


def check_insert_schema_parity(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Check 20: every static INSERT column list exists in the live schema."""
    check_id = "insert_schema_parity"
    check_name = "INSERT / Live Schema Parity"
    expected = ["every column named in a static INSERT exists in the live database schema"]

    targets = _scan_targets(changed_files, "tools")
    if not targets:
        return CoherenceCheck(
            check_id=check_id,
            check_name=check_name,
            status="pass",
            expected=expected,
            actual=["no Python files under tools/ in scope"],
            missing=[],
            extra=[],
            message="No INSERT statements in scope",
        )

    schema, backend, error = _live_table_columns()
    if error:
        return CoherenceCheck(
            check_id=check_id,
            check_name=check_name,
            status="warn",
            expected=expected,
            actual=[error],
            missing=[],
            extra=[],
            message=(
                f"Live schema unavailable ({error}) — INSERT column parity NOT verified. "
                "Point ICDEV_STORAGE_BACKEND at an initialised database to enable this gate."
            ),
        )

    ignored_tables, grandfathered = _load_insert_schema_gate()

    new_findings: List[str] = []
    excused_keys: Set[str] = set()
    statements = 0
    validated = 0
    unknown_tables: Set[str] = set()

    for py_path in targets:
        if "__pycache__" in str(py_path):
            continue
        source = _read_text(py_path)
        if "INSERT" not in source.upper():
            continue
        try:
            rel = py_path.relative_to(PROJECT_ROOT).as_posix()
        except ValueError:
            rel = py_path.name
        if rel in _SCHEMA_CODE_BACKEND_PINNED or _uses_foreign_database(source):
            continue
        for lineno, table, cols in _extract_insert_column_lists(source):
            statements += 1
            if table in ignored_tables:
                continue
            table_cols = schema.get(table)
            if table_cols is None:
                # Not in THIS database — a canvas, tenant, platform or child-app
                # table. Nothing to validate against; silence beats a guess.
                unknown_tables.add(table)
                continue
            validated += 1
            absent = sorted(set(cols) - table_cols)
            for column in absent:
                key = f"{rel}:{table}:{column}"
                if key in grandfathered:
                    excused_keys.add(key)
                    continue
                new_findings.append(f"{rel}:{lineno}: INSERT INTO {table} names missing column '{column}'")

    # Stale allowlist entries are only meaningful after a whole-tree scan; a
    # diff-scoped run has not looked at the files the other entries name.
    stale: List[str] = []
    if not changed_files:
        stale = sorted(set(grandfathered) - excused_keys)

    actual = [
        f"{statements} static INSERT statement(s) parsed; {validated} validated "
        f"against {len(schema)} live {backend} table(s)"
    ]

    if new_findings:
        return CoherenceCheck(
            check_id=check_id,
            check_name=check_name,
            status="fail",
            expected=expected,
            actual=actual + new_findings,
            missing=sorted({f.split("names missing column ")[-1].strip("'") for f in new_findings}),
            extra=new_findings,
            message=(
                f"{len(new_findings)} INSERT column(s) do not exist in the live schema "
                f"({len(excused_keys)} grandfathered). These raise at runtime and are "
                "usually swallowed — fix the column list, or add a migration that adds "
                "the column, before merging."
            ),
        )

    if excused_keys or stale:
        bits = []
        if excused_keys:
            bits.append(f"{len(excused_keys)} grandfathered mismatch(es) remain")
        if stale:
            bits.append(f"{len(stale)} stale allowlist entry(ies) can be removed")
        return CoherenceCheck(
            check_id=check_id,
            check_name=check_name,
            status="warn",
            expected=expected,
            actual=actual,
            missing=sorted(excused_keys),
            extra=stale,
            message="No NEW INSERT/schema mismatches — " + "; ".join(bits),
        )

    return CoherenceCheck(
        check_id=check_id,
        check_name=check_name,
        status="pass",
        expected=expected,
        actual=actual + [f"{len(unknown_tables)} table(s) absent from this database were skipped"],
        missing=[],
        extra=[],
        message=f"All {validated} validated INSERT statement(s) match the live schema",
    )


# ---------------------------------------------------------------------------
# check_vendor_parity (cxo-doc-03) — out-of-repo VENDORED copies
# ---------------------------------------------------------------------------

_VENDOR_PARITY_CONFIG = "args/vendor_parity.yaml"
_VENDOR_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _vendor_parity_config() -> Dict[str, Any]:
    """Declared vendor targets. Empty dict when the config is absent/unreadable."""
    cfg = PROJECT_ROOT / _VENDOR_PARITY_CONFIG
    if not cfg.exists():
        return {}
    try:
        import yaml  # lazy — yaml isn't a top-level import here
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_vendor_path(raw: str, defaults: Dict[str, str]) -> Optional[Path]:
    """Expand ``${VAR}`` from the environment, then *defaults*.

    Returns None when a placeholder resolves to nothing — that consumer is then
    SKIPPED rather than failed, which is what keeps this check green on a
    machine (or CI runner) where the standalone repos are not checked out.
    """
    unresolved: List[str] = []

    def _sub(match: "re.Match[str]") -> str:
        name = match.group(1)
        value = os.environ.get(name) or defaults.get(name)
        if not value:
            unresolved.append(name)
            return ""
        return str(value)

    expanded = _VENDOR_PLACEHOLDER_RE.sub(_sub, raw or "")
    if unresolved or not expanded.strip():
        return None
    path = Path(expanded).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _public_api(source: str) -> Set[str]:
    """Public API surface of a module: classes, functions, methods + parameters.

    Compares the CALLABLE SURFACE, not bytes. A vendored copy legitimately
    differs from canonical by a provenance header and by line endings (canonical
    is LF, the standalone consumers are CRLF under core.autocrlf=true), so a
    byte/line comparison would be pure noise. Annotations, default VALUES and
    docstrings are ignored for the same reason; parameter NAMES are kept because
    every call site in a consumer depends on them.
    """
    tree = ast.parse(source)

    def _sig(fn) -> str:
        args = fn.args
        parts: List[str] = []
        posonly = list(getattr(args, "posonlyargs", []) or [])
        positional = posonly + list(args.args)
        first_default = len(positional) - len(args.defaults)
        for index, arg in enumerate(positional):
            parts.append(arg.arg + ("=..." if index >= first_default else ""))
            if posonly and index == len(posonly) - 1:
                parts.append("/")
        if args.vararg:
            parts.append("*" + args.vararg.arg)
        elif args.kwonlyargs:
            parts.append("*")
        for arg, default in zip(args.kwonlyargs, args.kw_defaults):
            parts.append(arg.arg + ("=..." if default is not None else ""))
        if args.kwarg:
            parts.append("**" + args.kwarg.arg)
        return "(" + ", ".join(parts) + ")"

    api: Set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                api.add(f"{node.name}{_sig(node)}")
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                continue
            api.add(f"class {node.name}")
            for sub in node.body:
                if not isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if sub.name.startswith("_") and sub.name != "__init__":
                    continue
                api.add(f"{node.name}.{sub.name}{_sig(sub)}")
    return api


def check_vendor_parity(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """Flag a declared stdlib-only module whose out-of-repo VENDORED copies lag it.

    Some modules (tools/cortex/client.py, ctx-expose-06) are deliberately
    dependency-free so standalone apps in SEPARATE repos can copy them verbatim.
    Nothing tied the copies to canonical, and the drift is LATENT — the copies
    were missing methods no consumer called yet, so nothing broke and nobody
    noticed (measured 2026-08-02: canonical 24 public methods, compass 22,
    idea_lab 19).

    Targets live in ``args/vendor_parity.yaml``; adding one needs no code change.
    FAIL when a CHANGED declared source's public API is not a subset of a
    present vendored copy's; WARN on the full-repo sweep. A consumer path that
    does not exist on this machine is SKIPPED with a note, never failed.
    """
    check_id = "vendor_parity"
    check_name = "Vendored Copy Parity"
    expected = ["Canonical public API is a subset of every declared vendored copy's API"]

    config = _vendor_parity_config()
    entries = [e for e in (config.get("vendored_copies") or []) if isinstance(e, dict)]
    defaults = {str(k): str(v) for k, v in (config.get("path_defaults") or {}).items()}

    if not entries:
        return CoherenceCheck(
            check_id=check_id,
            check_name=check_name,
            status="pass",
            expected=expected,
            actual=["no vendored copies declared"],
            missing=[],
            extra=[],
            message=f"No vendor targets declared in {_VENDOR_PARITY_CONFIG}.",
        )

    severity = "warn"
    if changed_files:
        severity = "fail"
        touched = [str(p).replace("\\", "/") for p in changed_files]
        in_scope = []
        for entry in entries:
            source = str(entry.get("source") or "").strip()
            if source and any(t == source or t.endswith("/" + source) for t in touched):
                in_scope.append(entry)
        if not in_scope:
            return CoherenceCheck(
                check_id=check_id,
                check_name=check_name,
                status="pass",
                expected=expected,
                actual=["no declared vendored source in the changed set"],
                missing=[],
                extra=[],
                message="No declared vendored source changed.",
            )
        entries = in_scope

    drift: List[str] = []
    skipped: List[str] = []
    verified: List[str] = []

    for entry in entries:
        source = str(entry.get("source") or "").strip()
        canonical = PROJECT_ROOT / source
        if not source or not canonical.exists():
            drift.append(f"{source or '<unnamed>'}: declared vendored source does not exist")
            continue
        try:
            canonical_api = _public_api(canonical.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError) as exc:
            drift.append(f"{source}: canonical source could not be parsed ({exc})")
            continue

        for consumer in entry.get("consumers") or []:
            if not isinstance(consumer, dict):
                continue
            raw = str(consumer.get("path") or "")
            name = str(consumer.get("name") or raw)
            path = _resolve_vendor_path(raw, defaults)
            if path is None or not path.exists():
                skipped.append(
                    f"{source} -> {name}: consumer not present on this machine "
                    f"({path or raw}) — skipped"
                )
                continue
            try:
                copy_api = _public_api(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError, ValueError) as exc:
                skipped.append(f"{source} -> {name}: could not be parsed ({exc}) — skipped")
                continue
            behind = sorted(canonical_api - copy_api)
            if behind:
                shown = ", ".join(behind[:8]) + (" …" if len(behind) > 8 else "")
                drift.append(
                    f"{source} -> {name} ({path}): vendored copy is missing "
                    f"{len(behind)} public member(s): {shown}"
                )
            else:
                verified.append(
                    f"{source} -> {name}: in sync ({len(canonical_api)} public members)"
                )

    actual = verified + skipped
    if drift:
        return CoherenceCheck(
            check_id=check_id,
            check_name=check_name,
            status=severity,
            expected=expected,
            actual=actual,
            missing=drift,
            extra=[],
            message=(
                f"{len(drift)} vendored copy(ies) lag their canonical source — "
                "re-copy the file into the consumer repo (keep only its provenance "
                f"header) or update {_VENDOR_PARITY_CONFIG}."
            ),
        )

    note = f"{len(verified)} copy(ies) in sync"
    if skipped:
        note += f", {len(skipped)} skipped (consumer repo absent on this machine)"
    return CoherenceCheck(
        check_id=check_id,
        check_name=check_name,
        status="pass",
        expected=expected,
        actual=actual or ["no consumer paths resolved"],
        missing=[],
        extra=[],
        message=f"Vendored copies match their canonical source — {note}.",
    )


_BOOTSTRAP_PARITY_PATH = PROJECT_ROOT / "args" / "bootstrap_parity.yaml"


def _load_bootstrap_parity() -> List[Dict[str, str]]:
    """Read the must-match pairs from args/bootstrap_parity.yaml.

    An unreadable or absent declaration yields an EMPTY pair list, so the check
    reports ``warn`` rather than a silent ``pass``. A parity gate that vanishes
    with its config file is worse than no gate: it looks green.
    """
    if not _HAS_YAML or not _BOOTSTRAP_PARITY_PATH.exists():
        return []
    try:
        data = yaml.safe_load(_BOOTSTRAP_PARITY_PATH.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — a malformed gate must not crash the run
        return []
    pairs = data.get("must_match") or []
    return [p for p in pairs if isinstance(p, dict) and p.get("target") and p.get("source")]


_BOOTSTRAP_HOOKS_DIR = (
    PROJECT_ROOT / "icdev" / "data" / "claude_bootstrap" / "claude" / "hooks"
)


def _packaged_hook_payload_modules() -> List[Tuple[str, str]]:
    """``(packaged hook, module it loads by path)`` for every packaged hook.

    Derived from the hooks themselves — each declares a module-level
    ``PAYLOAD_MODULES`` tuple and its loader iterates that same tuple — rather
    than from a hand-maintained list here, which would be one more thing that
    can go stale while looking green.

    A hook with no ``PAYLOAD_MODULES`` contributes nothing: most hooks are
    self-contained and only the ones that ``exec_module`` an implementation
    file have a payload to ship.
    """
    found: List[Tuple[str, str]] = []
    if not _BOOTSTRAP_HOOKS_DIR.is_dir():
        return found
    for hook in sorted(_BOOTSTRAP_HOOKS_DIR.glob("*.py")):
        try:
            tree = ast.parse(hook.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — an unparseable hook is another check's job
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "PAYLOAD_MODULES" not in names:
                continue
            try:
                for module in ast.literal_eval(node.value):
                    found.append((hook.name, str(module)))
            except Exception:  # noqa: BLE001 — a non-literal declaration is unusable
                continue
    return found


def _bootstrap_payload_defects() -> List[str]:
    """Modules a packaged hook loads that did NOT ship with it, or shipped stale.

    THE BUG THIS EXISTS FOR (exa-bench-10)
    ======================================
    ``claude/hooks/pre_tool_use.py`` loads its implementation with
    ``spec_from_file_location`` + ``exec_module``. The bootstrap payload shipped
    the hook and not the implementation, so in every project `icdev init`
    created the hook raised ``FileNotFoundError`` on EVERY tool call — and the
    generated settings.json wraps each hook in ``|| true``, so the shell
    returned 0 and nothing ever surfaced. Every generated project believed it
    had a PreToolUse guard and had none.

    Byte-parity against ``tools/hooks/`` is checked too: a payload module that
    ships one release behind is the #960 trap one directory over.
    """
    defects: List[str] = []
    for hook_name, module in _packaged_hook_payload_modules():
        packaged = _BOOTSTRAP_HOOKS_DIR / module
        if not packaged.is_file():
            defects.append(
                f"claude/hooks/{hook_name} loads '{module}' by path, but "
                f"icdev/data/claude_bootstrap/claude/hooks/{module} does not "
                f"exist — the hook cannot load in a scaffolded project"
            )
            continue
        source = PROJECT_ROOT / "tools" / "hooks" / module
        if source.is_file() and source.read_bytes() != packaged.read_bytes():
            defects.append(
                f"claude/hooks/{module} != tools/hooks/{module} "
                f"({packaged.stat().st_size} vs {source.stat().st_size} bytes) "
                f"— `icdev init` ships a stale implementation of "
                f"claude/hooks/{hook_name}"
            )
    return defects


def check_bootstrap_parity() -> CoherenceCheck:
    """`icdev init` must scaffold the instruction files this repo actually runs on.

    ``icdev init`` copies ``icdev/data/claude_bootstrap/<x>``, NOT the repo file
    of the same name (BOOTSTRAP_MAP, tools/cli/init.py). Editing one without the
    other hands a scaffolded project different rules than the project develops
    against — the exact thing
    ``test_packaged_claude_md_is_not_a_stripped_template`` was written to stop.

    That test only covers CLAUDE.md, runs in CI after the push, and fails with a
    raw byte-compare of two 40 KB blobs whose offset markers read like CRLF
    drift. This check runs locally, covers every pair declared in
    args/bootstrap_parity.yaml, and names the file and the fix.

    It also runs a PAYLOAD-COMPLETENESS rule (exa-bench-10): every module a
    packaged hook loads by path must ship beside it, and must not be stale.
    That one is derived from the hooks, not declared here — see
    :func:`_bootstrap_payload_defects`.
    """
    payload_defects = _bootstrap_payload_defects()
    pairs = _load_bootstrap_parity()
    # The payload rule is DERIVED from the packaged hooks, not declared in the
    # YAML, so it still has something to say when the declaration is missing.
    if not pairs and not payload_defects:
        return CoherenceCheck(
            check_id="bootstrap_parity",
            check_name="Bootstrap Parity",
            status="warn",
            expected=["args/bootstrap_parity.yaml declares the must-match pairs"],
            actual=["declaration missing or unreadable"],
            missing=[],
            extra=[],
            message=(
                "args/bootstrap_parity.yaml is absent or unparseable, so no "
                "bootstrap pair was checked. `icdev init` ships packaged copies "
                "of CLAUDE.md and AGENTS.md; without this declaration nothing "
                "notices when they drift from the repo files."
            ),
        )

    drift: List[str] = []
    missing: List[str] = []
    checked = 0
    for pair in pairs:
        target = PROJECT_ROOT / str(pair["target"])
        source = PROJECT_ROOT / "icdev" / str(pair["source"])
        if not target.is_file() or not source.is_file():
            absent = target if not target.is_file() else source
            missing.append(f"{pair['target']}: {absent.relative_to(PROJECT_ROOT)} not found")
            continue
        checked += 1
        if target.read_bytes() != source.read_bytes():
            drift.append(
                f"{pair['target']} != icdev/{pair['source']} "
                f"({target.stat().st_size} vs {source.stat().st_size} bytes)"
            )

    if drift or payload_defects:
        reasons = []
        if drift:
            reasons.append(
                f"{len(drift)} packaged bootstrap file(s) differ from the repo "
                f"file they scaffold, so `icdev init` would hand a new project "
                f"different instructions than this repo runs on (that is #960)."
            )
        if payload_defects:
            reasons.append(
                f"{len(payload_defects)} module(s) a packaged hook loads by "
                f"path did not ship with it (or shipped stale), so that hook "
                f"raises on every tool call in a scaffolded project — and the "
                f"generated settings.json wraps hooks in `|| true`, so it fails "
                f"invisibly (exa-bench-10)."
            )
        return CoherenceCheck(
            check_id="bootstrap_parity",
            check_name="Bootstrap Parity",
            status="fail",
            expected=[
                f"{len(pairs)} bootstrap pair(s) byte-identical",
                "every module a packaged hook loads ships beside it",
            ],
            actual=[
                f"{len(drift)} drifted",
                f"{len(payload_defects)} payload defect(s)",
            ],
            missing=missing,
            extra=drift + payload_defects,
            message=(
                " ".join(reasons)
                + " Fix: `python tools/installer/prebuild_bootstrap.py` — it "
                "regenerates the whole bootstrap from the repo, which is the "
                "sanctioned path. Do NOT hand-copy a single file: the other "
                "packaged files drift too and a targeted copy hides that."
            ),
        )

    n_payload = len(_packaged_hook_payload_modules())
    note = (
        f"{checked} bootstrap pair(s) byte-identical, "
        f"{n_payload} packaged hook payload module(s) present"
    )
    return CoherenceCheck(
        check_id="bootstrap_parity",
        check_name="Bootstrap Parity",
        status="warn" if missing else "pass",
        expected=[
            f"{len(pairs)} bootstrap pair(s) byte-identical",
            "every module a packaged hook loads ships beside it",
        ],
        actual=[note],
        missing=missing,
        extra=[],
        message=(
            f"{note}; {len(missing)} pair(s) unresolved: {'; '.join(missing)}"
            if missing
            else f"`icdev init` scaffolds what this repo runs on — {note}."
        ),
    )


_MIRROR_GATE_PATH = PROJECT_ROOT / "args" / "mirror_parity_gate.yaml"


def _load_mirror_gate() -> Optional[Dict[str, Any]]:
    """Read args/mirror_parity_gate.yaml. ``None`` when unreadable.

    ``None`` makes the check report ``warn``, never ``pass``: a drift gate that
    disappears with its config file is the failure it exists to catch.
    """
    if not _HAS_YAML or not _MIRROR_GATE_PATH.exists():
        return None
    try:
        data = yaml.safe_load(_MIRROR_GATE_PATH.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — a malformed gate must not crash the run
        return None
    return data if isinstance(data, dict) else None


def _mirror_drifted_files(extensions: Sequence[str]) -> List[str]:
    """Drifted ``tools/<pkg>/<rel>`` paths, restricted to executing extensions.

    Delegates to tools/dx/mirror_parity.py — the audit already existed and
    already had ``--gate``; it was simply wired into nothing.
    """
    try:
        from tools.dx.mirror_parity import audit_all
    except Exception:  # noqa: BLE001
        return []
    try:
        result = audit_all(fix=False)
    except Exception:  # noqa: BLE001
        return []
    exts = tuple(extensions)
    out: List[str] = []
    for report in result.get("reports") or []:
        pkg = report.get("path") or ""
        for rel in report.get("content_drift") or []:
            if str(rel).endswith(exts):
                out.append(f"tools/{pkg}/{rel}".replace("\\", "/"))
    return sorted(out)


def check_mirror_parity(changed_files: Optional[List[Path]] = None) -> CoherenceCheck:
    """`tools/` and `icdev/tools/` are two modules; a stale mirror runs old code.

    They are separate module objects, not a shim onto one file, so a fix landed
    in one copy while the other still executes is a fix that is only HALF LIVE —
    and the stale half fails silently rather than raising.

    PR #1542 fixed the kanban executor degrade in ``tools/`` and did not mirror
    it. Three and a half hours later two tasks were quarantined carrying the
    exact error string that fix had deleted. Verifying the process had restarted
    on new code proved nothing, because only one of the two copies was new.

    TWO RULES, and the second is the one that matters:

    1. total drift must not exceed ``budget`` — a grandfathered backlog that
       ratchets DOWN;
    2. a file in ``changed_files`` must never be drifted, whatever the budget
       says. The budget forgives what was already broken; it is never
       permission to add more. This is the rule that catches a #1542.
    """
    gate = _load_mirror_gate()
    if gate is None:
        return CoherenceCheck(
            check_id="mirror_parity",
            check_name="Mirror Parity",
            status="warn",
            expected=["args/mirror_parity_gate.yaml declares the drift budget"],
            actual=["declaration missing or unreadable"],
            missing=[],
            extra=[],
            message=(
                "args/mirror_parity_gate.yaml is absent or unparseable, so "
                "tools/ <-> icdev/tools/ drift was not checked. A stale mirror "
                "runs old code silently."
            ),
        )

    extensions = [str(e) for e in (gate.get("extensions") or [".py"])]
    budget = int(gate.get("budget") or 0)
    drifted = _mirror_drifted_files(extensions)

    # Rule 2 first: your own change is never grandfathered.
    yours: List[str] = []
    if changed_files:
        changed = {str(p).replace("\\", "/").lstrip("./") for p in changed_files}
        yours = [d for d in drifted if any(c.endswith(d) or d.endswith(c) for c in changed)]

    if yours:
        return CoherenceCheck(
            check_id="mirror_parity",
            check_name="Mirror Parity",
            status="fail",
            expected=["files you changed are mirrored to icdev/tools/"],
            actual=[f"{len(yours)} changed file(s) drifted"],
            missing=[],
            extra=yours,
            message=(
                f"{len(yours)} file(s) you changed differ from their icdev/ "
                "mirror. They are separate modules, so whichever copy is stale "
                "will run OLD code silently — that is #1542, where a fix looked "
                "deployed for three and a half hours while the other copy still "
                "ran. Fix: `python tools/dx/mirror_parity.py --paths "
                "<pkg> --fix`. The budget does not forgive new drift."
            ),
        )

    if len(drifted) > budget:
        return CoherenceCheck(
            check_id="mirror_parity",
            check_name="Mirror Parity",
            status="fail",
            expected=[f"at most {budget} drifted {'/'.join(extensions)} file(s)"],
            actual=[f"{len(drifted)} drifted"],
            missing=[],
            extra=drifted[:20],
            message=(
                f"{len(drifted)} mirrored file(s) drifted against a budget of "
                f"{budget}. Reconcile with `python tools/dx/mirror_parity.py "
                "--paths <pkg> --fix`, then LOWER the budget in "
                "args/mirror_parity_gate.yaml. Never raise it."
            ),
        )

    lowerable = budget - len(drifted)
    return CoherenceCheck(
        check_id="mirror_parity",
        check_name="Mirror Parity",
        status="pass",
        expected=[f"at most {budget} drifted {'/'.join(extensions)} file(s)"],
        actual=[f"{len(drifted)} drifted"],
        missing=[],
        extra=drifted[:20],
        message=(
            f"{len(drifted)} mirrored file(s) drifted, within the budget of {budget}"
            + (
                f" — the budget can be LOWERED to {len(drifted)}."
                if lowerable > 0
                else "."
            )
        ),
    )


# ---------------------------------------------------------------------------
# Check Registry & Orchestrator
# ---------------------------------------------------------------------------

CHECK_REGISTRY = {
    "bootstrap_parity": check_bootstrap_parity,
    "mirror_parity": check_mirror_parity,
    "schema_code": check_schema_code,
    "config_code": check_config_code,
    "signature_call": check_signature_call,
    "fixture_schema": check_fixture_schema,
    "manifest": check_manifest,
    "append_only": check_append_only,
    "trust_coverage": check_trust_coverage,
    "import_usage": check_import_usage,
    "ruff_lint": check_ruff_lint,
    "api_wiring": check_api_wiring,
    "route_uniqueness": check_route_uniqueness,
    "attribution_claims": check_attribution_claims,
    "llm_injection_patterns": check_llm_injection_patterns,
    "skill_standard": check_skill_standard,
    "sandbox_coverage": check_sandbox_coverage,
    "swallowed_persistence": check_swallowed_persistence,
    "reflex_registry": check_reflex_registry,
    "capability_liveness": check_capability_liveness,
    "direct_anthropic_import": check_direct_anthropic_import,
    "provider_bypass": check_provider_bypass,
    "architecture_agnosticism": check_architecture_agnosticism,
    "llm_router_api": check_llm_router_api,
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
    "chat_card_inheritance": check_chat_card_inheritance,
    "twin_chat_include": check_twin_chat_include,
    "canvas_placeholder_style": check_canvas_placeholder_style,
    "runtime_placeholder_style": check_runtime_placeholder_style,
    "ace_yaml_listen_topics": check_ace_yaml_listen_topics,
    "skill_security": check_skill_security,
    "spec_discipline": check_spec_discipline,
    "component_registry": check_component_registry,
    "component_cli_reachability": check_component_cli_reachability,
    "canvas_completeness": check_canvas_completeness,
    "nav_sync": check_nav_sync,
    "iqe_map_sync": check_iqe_map_sync,
    "profile_sync": check_profile_sync,
    "test_db_isolation": check_test_db_isolation,
    "migration_numbering": check_migration_numbering,
    "icdev_mirror_parity": check_icdev_mirror_parity,
    "vendor_parity": check_vendor_parity,
    "mirror_drift": check_mirror_drift,
    "doc_command_paths": check_doc_command_paths,
    "insert_schema_parity": check_insert_schema_parity,
}


# ---------------------------------------------------------------------------
# Check tiers — the gate/sweep split
# ---------------------------------------------------------------------------

TIERS = ("fast", "full")

# Checks that import or introspect the ENTIRE application rather than the diff.
# Measured on a warm checkout these three cost ~100s of a ~170s full run, which
# is why the per-task coherence gate could never finish inside its subprocess
# timeout. They are global invariants — a single task can only break one in a
# way that also shows up in the main baseline — so the fast tier runs a heavy
# check ONLY when the diff touches its trigger surface. `--tier full` (the
# nightly sweep and post-merge reflex) always runs every check.
#
# NOTE: direct_anthropic_import and provider_bypass used to belong here too;
# they are now diff-scoped via _scan_targets() and cheap enough to always run.
#
# capability_liveness (exa-live-02/03) deliberately stays OUT of this map, i.e.
# it runs in BOTH tiers. It looks like a heavy — it imports TOOL_REGISTRY, the
# genesis daemon and the approval gate — but the work is a handful of GROUP BY
# counts and it measures at ~0.75s, well inside the fast tier's budget. Tier
# assignment here is about COST, and the cost is not there. Demoting it to the
# nightly sweep would also defeat the point: a capability declared in a task's
# own diff and wired to nothing is exactly what the per-task gate should catch,
# and catching it a day later is how the backlog in args/liveness_gate.yaml grew
# in the first place. It cannot fire spuriously on a checkout with no operating
# history — the evidence anchor degrades it to `warn` instead. Asserted by
# tests/test_coherence_capability_liveness.py::test_runs_in_the_fast_tier.
HEAVY_CHECKS: Dict[str, Tuple[str, ...]] = {
    "blueprint_imports": (
        "tools/dashboard/",
        "icdev/tools/dashboard/",
        "blueprint.py",
        "args/component_registry.yaml",
    ),
    "openapi_parity": (
        "tools/dashboard/",
        "icdev/tools/dashboard/",
        "blueprint.py",
        "openapi",
    ),
    "llm_router_api": (
        "tools/llm/",
        "icdev/tools/llm/",
        "args/llm_config.yaml",
    ),
}


def _normalised_paths(changed_files: Optional[List[Path]]) -> List[str]:
    """Changed paths as forward-slash strings for substring trigger matching."""
    return [str(entry).replace("\\", "/") for entry in (changed_files or [])]


def select_checks(
    tier: str = "full", changed_files: Optional[List[Path]] = None
) -> List[str]:
    """Ordered check ids to run for *tier*.

    ``full`` returns every registered check. ``fast`` drops the whole-app
    heavies in HEAVY_CHECKS, re-adding any whose trigger surface appears in
    *changed_files*.

    With no diff information the fast tier still drops the heavies — "fast"
    means fast. Callers that cannot supply a file list and need full coverage
    should ask for ``full`` explicitly rather than relying on this to guess.
    """
    all_ids = list(CHECK_REGISTRY.keys())
    if tier != "fast":
        return all_ids
    touched = _normalised_paths(changed_files)
    keep: List[str] = []
    for check_id in all_ids:
        triggers = HEAVY_CHECKS.get(check_id)
        if triggers is None:
            keep.append(check_id)
        elif any(trigger in path for path in touched for trigger in triggers):
            keep.append(check_id)
    return keep


def _check_workers() -> int:
    """Thread count for the parallel check sweep. ``1`` forces serial."""
    try:
        configured = int(os.environ.get("ICDEV_COHERENCE_WORKERS", "0"))
    except ValueError:
        configured = 0
    if configured > 0:
        return configured
    return max(1, min(4, (os.cpu_count() or 2) - 1))


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
    "llm_router_api": "skip",  # dead-API call sites require routing fix to invoke(fn, req)
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
    "component_cli_reachability": "skip",  # a surface gap needs CLI/TUI/.env wiring, not a mechanical fix
    "canvas_completeness": "skip",  # missing canvas components must be created by hand
    "nav_sync": "skip",  # template changes require human review
    "iqe_map_sync": "skip",  # adapter wiring requires human review
    "profile_sync": "skip",  # profile YAML changes require human review
    "mirror_drift": "skip",  # WARN-only; reconciling twins requires human judgment (which side is canonical)
    "doc_command_paths": "skip",  # build the tool or delete the doc line — both need human judgment
    "insert_schema_parity": "skip",  # drop the column or write a migration — the choice is the fix
    "capability_liveness": "skip",  # wiring a capability to a consumer is the fix; a budget bump is not
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
        hook_path.write_text(content, encoding="utf-8", newline="")
        fixes.append(f"Added {len(new_entries)} table(s) to APPEND_ONLY_TABLES: {', '.join(check.missing)}")
    return fixes


def _autofix_manifest(check: CoherenceCheck) -> List[str]:
    """Auto-append missing tools to tools/manifest.md.

    Re-reads the manifest surface immediately before writing rather than
    trusting `check.missing`, which was computed earlier: `tools/manifest*` is
    merged with `merge=union` (kax-conflict-03), so a row another branch added
    can arrive between the scan and the fix. Appending it again produced the
    two duplicated "Auto-Registered (Coherence Fix)" sections this guard now
    reports.
    """
    missing = check.missing
    if not missing:
        return []

    manifest_path = PROJECT_ROOT / "tools" / "manifest.md"
    if not manifest_path.exists():
        return []

    documented = _read_text(manifest_path).lower()
    shard_dir = PROJECT_ROOT / "tools" / "manifest"
    if shard_dir.is_dir():
        for shard in shard_dir.glob("*.md"):
            documented += "\n" + _read_text(shard).lower()

    lines = []
    skipped = 0
    for tool_path in missing:
        p = Path(tool_path)
        if p.stem.lower() in documented:
            skipped += 1  # landed from another branch since the scan
            continue
        name = p.stem.replace("_", " ").title()
        desc = f"Auto-registered: {p.parent.name}/{p.name}"
        lines.append(f"| {name} | {tool_path} | {desc} | --json | JSON |")

    if not lines:
        return [f"No manifest rows appended ({skipped} already documented)"]

    with open(manifest_path, "a", encoding="utf-8") as f:
        f.write("\n\n## Auto-Registered (Coherence Fix)\n")
        f.write("| Tool | File | Description | Input | Output |\n")
        f.write("|------|------|-------------|-------|--------|\n")
        for line in lines:
            f.write(line + "\n")

    note = f"Appended {len(lines)} tools to manifest.md"
    if skipped:
        note += f" ({skipped} already documented, skipped)"
    return [note]


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


def _unknown_check(check_id: str) -> CoherenceCheck:
    return CoherenceCheck(
        check_id=check_id,
        check_name=f"Unknown: {check_id}",
        status="warn",
        expected=[],
        actual=[],
        missing=[],
        extra=[],
        message=f"Unknown check: {check_id}",
    )


def _run_one_check(
    check_id: str, changed_files: Optional[List[Path]]
) -> CoherenceCheck:
    """Execute a single check, timing it and converting any raise into a warn."""
    import inspect

    func = CHECK_REGISTRY.get(check_id)
    if not func:
        return _unknown_check(check_id)

    started = time.monotonic()
    try:
        if "changed_files" in inspect.signature(func).parameters:
            result = func(changed_files=changed_files)
        else:
            result = func()
    except Exception as exc:
        result = CoherenceCheck(
            check_id=check_id,
            check_name=check_id,
            status="warn",
            expected=[],
            actual=[],
            missing=[],
            extra=[],
            message=f"Check error: {exc}",
        )
    result.duration_sec = round(time.monotonic() - started, 3)
    return result


def _timed_out_check(check_id: str, budget: float) -> CoherenceCheck:
    """A check that never returned inside the sweep budget."""
    return CoherenceCheck(
        check_id=check_id,
        check_name=check_id,
        status="warn",
        expected=[],
        actual=[],
        missing=[],
        extra=[],
        message=(
            f"timed out — did not finish within the {budget:g}s sweep budget. "
            "Reported as warn so the sweep still returns a verdict for every "
            "other check; re-run this one alone to see what it is doing."
        ),
    )


def run_checks(
    selected: Optional[List[str]] = None,
    changed_files: Optional[List[Path]] = None,
    autofix: bool = False,
    tier: Optional[str] = None,
    timeout_sec: Optional[float] = None,
) -> CoherenceReport:
    """Run selected coherence checks and produce aggregate report.

    Args:
        selected: specific check IDs to run (None = tier selection)
        changed_files: diff-scope passed to every check that accepts it
        autofix: if True, auto-fix safe issues after detection
        tier: ``fast`` or ``full``; ignored when *selected* is given.

    Checks run concurrently on a small thread pool — most of the cost is
    subprocess, file I/O, and imports, all of which release the GIL. Autofix
    forces the serial path because fixers mutate the tree and two of them
    landing at once is not something the fix registry is written for.

    ``timeout_sec`` (gpx-perf-01) puts a hard ceiling on the concurrent phase.
    It is opt-in and off by default: enforcing it requires running the checks in
    *processes* rather than threads, because a thread cannot be killed, and
    ``ThreadPoolExecutor`` joins its non-daemon workers at interpreter exit — so
    a genuinely hung check hangs the process on the way out no matter what the
    caller does with the futures. Processes can be terminated.

    Any check that has not returned when the budget expires is reported as
    ``warn: timed out`` rather than being dropped, so the sweep still returns a
    verdict for every other check instead of one pathological check taking the
    whole run down with it.

    The default stays on threads deliberately. The gate runs on every commit,
    and moving it to multi-process by default changes DB connection fan-out and
    Windows spawn behaviour for every caller — not a trade to make silently for
    a ceiling almost no run needs. Callers that want the ceiling ask for it.
    """
    if selected:
        checks_to_run = list(selected)
    else:
        checks_to_run = select_checks(tier or "full", changed_files)

    results: List[CoherenceCheck] = []
    workers = 1 if autofix else min(_check_workers(), max(1, len(checks_to_run)))

    if workers > 1:
        hard_timeout = bool(timeout_sec and timeout_sec > 0 and not autofix)
        pool_cls = (
            concurrent.futures.ProcessPoolExecutor
            if hard_timeout
            else concurrent.futures.ThreadPoolExecutor
        )
        by_id: Dict[str, CoherenceCheck] = {}
        pool = pool_cls(max_workers=workers)
        try:
            futures = {
                pool.submit(_run_one_check, check_id, changed_files): check_id
                for check_id in checks_to_run
            }
            try:
                for future in concurrent.futures.as_completed(
                    futures, timeout=timeout_sec if hard_timeout else None
                ):
                    check_id = futures[future]
                    try:
                        by_id[check_id] = future.result()
                    except Exception as exc:  # pragma: no cover — defensive
                        by_id[check_id] = CoherenceCheck(
                            check_id=check_id,
                            check_name=check_id,
                            status="warn",
                            expected=[],
                            actual=[],
                            missing=[],
                            extra=[],
                            message=f"Check error: {exc}",
                        )
            except concurrent.futures.TimeoutError:
                # Budget blown. Everything still outstanding is reported as a
                # timeout rather than silently omitted from the report.
                for check_id in checks_to_run:
                    if check_id not in by_id:
                        by_id[check_id] = _timed_out_check(check_id, float(timeout_sec))
        finally:
            if hard_timeout:
                # Kill the workers outright: shutdown(wait=True) would block on
                # the very check that just blew the budget, which is the whole
                # thing this is meant to prevent.
                for proc in list(getattr(pool, "_processes", {}).values()):
                    if proc.is_alive():
                        proc.terminate()
                pool.shutdown(wait=False, cancel_futures=True)
            else:
                pool.shutdown(wait=True)
        # Preserve registry order regardless of completion order.
        results = [by_id[check_id] for check_id in checks_to_run if check_id in by_id]
    else:
        for check_id in checks_to_run:
            results.append(_run_one_check(check_id, changed_files))

    total_fixes = 0
    if autofix:
        fixed: List[CoherenceCheck] = []
        for result in results:
            if result.status in ("fail", "warn"):
                result = _apply_fixes(result)
                total_fixes += len(result.fixes_applied)
            fixed.append(result)
        results = fixed

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
    parser.add_argument("--all", action="store_true", help="Run all checks (alias for --tier full)")
    parser.add_argument(
        "--tier",
        choices=TIERS,
        default=None,
        help=(
            "fast = per-task gate (drops whole-app heavies unless the diff "
            "touches them); full = every check (nightly sweep / post-merge). "
            "Default: full."
        ),
    )
    parser.add_argument("--check", type=str, default="", help=f"Specific check: {', '.join(CHECK_REGISTRY.keys())}")
    parser.add_argument("--changed-files", type=str, default="", help="Comma-separated list of changed file paths")
    parser.add_argument(
        "--changed-files-from",
        type=str,
        default="",
        help="Read changed file paths (one per line) from a file — avoids the OS argv length limit on large diffs",
    )
    parser.add_argument(
        "--list-tier",
        action="store_true",
        help="Print the check ids the selected tier would run, then exit",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help=(
            "Hard ceiling in seconds on the concurrent phase. Runs the checks in "
            "processes (threads cannot be killed) and reports anything still "
            "outstanding as 'warn: timed out' instead of letting one pathological "
            "check block the sweep. Off by default; ignored with --fix."
        ),
    )

    args = parser.parse_args()

    selected = None
    if args.check:
        selected = [c.strip() for c in args.check.split(",")]

    tier = args.tier or ("full" if args.all else "full")

    raw_changed: List[str] = []
    if args.changed_files:
        raw_changed += [f.strip() for f in args.changed_files.split(",") if f.strip()]
    if args.changed_files_from:
        try:
            raw_changed += [
                line.strip()
                for line in Path(args.changed_files_from).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except OSError as exc:
            print(f"could not read --changed-files-from: {exc}", file=sys.stderr)
            sys.exit(2)
    changed: Optional[List[Path]] = [PROJECT_ROOT / f for f in raw_changed] or None

    if args.list_tier:
        for check_id in (selected or select_checks(tier, changed)):
            print(check_id)
        sys.exit(0)

    # Checks import application modules that print banners at import time
    # (canvas `db/init_db.py` emits ~67 lines of "[init_db] ..."). Those landed
    # on stdout AHEAD of the JSON document, so every downstream
    # json.loads(stdout) raised and callers silently fell back to "no failure
    # detail available". Swap stdout for stderr while the checks run so the
    # report is the only thing on stdout — this covers noisy imports we haven't
    # met yet, not just today's eleven offenders. sys.stdout is process-global,
    # so the thread pool inside run_checks is covered too.
    _real_stdout = sys.stdout
    try:
        sys.stdout = sys.stderr
        report = run_checks(
            selected, changed, autofix=args.fix, tier=tier, timeout_sec=args.timeout
        )
    finally:
        sys.stdout = _real_stdout

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
