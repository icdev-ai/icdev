#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV .claude Directory Governance Validator.

Cross-references .claude directory configuration (hooks, settings, commands,
E2E specs) against the ICDEV codebase to detect drift. Ensures append-only
table protection, route documentation, deny rules, E2E coverage, and hook
integrity stay aligned as new phases are added.

Usage:
    python tools/testing/claude_dir_validator.py --json
    python tools/testing/claude_dir_validator.py --human
    python tools/testing/claude_dir_validator.py --check append-only
    python tools/testing/claude_dir_validator.py --check routes
    python tools/testing/claude_dir_validator.py --check hooks-syntax
    python tools/testing/claude_dir_validator.py --check hooks-refs
    python tools/testing/claude_dir_validator.py --check settings
    python tools/testing/claude_dir_validator.py --check e2e
    python tools/testing/claude_dir_validator.py --check cli-json
    python tools/testing/claude_dir_validator.py --check cli-naming
    python tools/testing/claude_dir_validator.py --check db-path
    python tools/testing/claude_dir_validator.py --check all

Exit codes: 0 = all checks pass, 1 = at least one check failed
"""

import argparse
import ast
import dataclasses
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Result types (follows ConsistencyResult pattern from consistency_analyzer.py)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ClaudeConfigCheck:
    """Result of a single .claude configuration alignment check."""
    check_id: str
    check_name: str
    status: str  # "pass", "fail", "warn"
    expected: List[str]
    actual: List[str]
    missing: List[str]
    extra: List[str]
    message: str

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @property
    def passed(self) -> bool:
        return self.status == "pass"


@dataclasses.dataclass
class ClaudeConfigReport:
    """Aggregate validation report for .claude directory governance."""
    overall_pass: bool
    timestamp: str
    checks: List[ClaudeConfigCheck]
    total_checks: int
    passed_checks: int
    failed_checks: int
    warned_checks: int

    def to_dict(self) -> dict:
        return {
            "overall_pass": self.overall_pass,
            "timestamp": self.timestamp,
            "total_checks": self.total_checks,
            "passed_checks": self.passed_checks,
            "failed_checks": self.failed_checks,
            "warned_checks": self.warned_checks,
            "checks": [c.to_dict() for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Discovery functions
# ---------------------------------------------------------------------------

def discover_append_only_tables(init_db_path: Path) -> Set[str]:
    """Parse init_icdev_db.py to find all tables with append-only/immutable comments.

    Scans the SCHEMA_SQL string for CREATE TABLE statements that are
    preceded by comments containing 'append-only' or 'immutable'.
    """
    if not init_db_path.exists():
        return set()

    content = init_db_path.read_text(encoding="utf-8")

    # Extract the SCHEMA_SQL string content
    match = re.search(r'SCHEMA_SQL\s*=\s*"""(.*?)"""', content, re.DOTALL)
    if not match:
        # Fallback: scan the entire file
        schema_text = content
    else:
        schema_text = match.group(1)

    tables: Set[str] = set()
    lines = schema_text.split("\n")

    for i, line in enumerate(lines):
        # Look for CREATE TABLE
        table_match = re.match(
            r"\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
            line, re.IGNORECASE,
        )
        if not table_match:
            continue

        table_name = table_match.group(1)

        # Check preceding lines (up to 10, but stop at previous ');' boundary)
        start = max(0, i - 10)
        preceding_lines = []
        for j in range(i - 1, start - 1, -1):
            stripped = lines[j].strip()
            if stripped.endswith(");"):
                break  # Hit end of previous table — stop scanning
            preceding_lines.append(stripped)
        preceding = " ".join(preceding_lines).lower()
        if "append-only" in preceding or "append_only" in preceding or "immutable" in preceding:
            tables.add(table_name)

    return tables


def discover_protected_tables(pre_tool_use_path: Path) -> Set[str]:
    """Parse pre_tool_use.py to find all tables in APPEND_ONLY_TABLES list.

    Uses ast module to find the APPEND_ONLY_TABLES assignment and extract
    all string literals from the list.
    """
    if not pre_tool_use_path.exists():
        return set()

    content = pre_tool_use_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return set()

    tables: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "APPEND_ONLY_TABLES":
                    if isinstance(node.value, ast.List):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                tables.add(elt.value)
    return tables


def discover_dashboard_page_routes(app_path: Path) -> Set[str]:
    """Parse app.py to find all @app.route() page routes.

    Excludes /api/ routes, /health, and error handlers.
    Normalizes parameterized routes: /projects/<project_id> -> /projects/<id>.
    """
    if not app_path.exists():
        return set()

    content = app_path.read_text(encoding="utf-8")
    routes: Set[str] = set()

    # Match @app.route("/path") and @app.route("/path", methods=[...])
    for match in re.finditer(r'@app\.route\(\s*["\'](/[^"\']*)["\']', content):
        route = match.group(1)
        # Exclude API-only, health, static routes, and AJAX sub-routes
        if route.startswith("/api/") or route == "/health" or "/api/" in route:
            continue
        # Normalize parameterized routes
        route = re.sub(r"<\w+>", "<id>", route)
        routes.add(route)

    return routes


def discover_documented_routes(start_md_path: Path) -> Set[str]:
    """Parse start.md to find all documented page paths.

    Extracts paths from backtick-formatted entries like `/path`.
    """
    if not start_md_path.exists():
        return set()

    content = start_md_path.read_text(encoding="utf-8")
    routes: Set[str] = set()

    # Find the Pages: line and extract backtick paths
    for match in re.finditer(r"`(/[^`]*)`", content):
        path = match.group(1)
        if path.startswith("/") and not path.startswith("/api/"):
            routes.add(path)

    return routes


# ---------------------------------------------------------------------------
# Check functions (each returns a ClaudeConfigCheck)
# ---------------------------------------------------------------------------

def check_append_only_table_coverage(
    init_db_path: Optional[Path] = None,
    pre_tool_use_path: Optional[Path] = None,
) -> ClaudeConfigCheck:
    """Verify pre_tool_use.py protects ALL append-only tables from init_icdev_db.py."""
    if init_db_path is None:
        init_db_path = PROJECT_ROOT / "tools" / "db" / "init_icdev_db.py"
    if pre_tool_use_path is None:
        pre_tool_use_path = PROJECT_ROOT / ".claude" / "hooks" / "pre_tool_use.py"

    db_tables = discover_append_only_tables(init_db_path)
    hook_tables = discover_protected_tables(pre_tool_use_path)

    missing = sorted(db_tables - hook_tables)
    extra = sorted(hook_tables - db_tables)

    if missing:
        status = "fail"
        message = f"{len(missing)} append-only table(s) unprotected: {', '.join(missing)}"
    elif extra:
        status = "warn"
        message = f"{len(extra)} table(s) in hook but not marked append-only in DB: {', '.join(extra)}"
    else:
        status = "pass"
        message = f"All {len(db_tables)} append-only tables protected"

    return ClaudeConfigCheck(
        check_id="append_only_coverage",
        check_name="Append-Only Table Coverage",
        status=status,
        expected=sorted(db_tables),
        actual=sorted(hook_tables),
        missing=missing,
        extra=extra,
        message=message,
    )


def check_dashboard_route_documentation(
    app_path: Optional[Path] = None,
    start_md_path: Optional[Path] = None,
) -> ClaudeConfigCheck:
    """Verify start.md documents all dashboard page routes from app.py."""
    if app_path is None:
        app_path = PROJECT_ROOT / "tools" / "dashboard" / "app.py"
    if start_md_path is None:
        start_md_path = PROJECT_ROOT / ".claude" / "commands" / "start.md"

    app_routes = discover_dashboard_page_routes(app_path)
    doc_routes = discover_documented_routes(start_md_path)

    # For comparison, normalize parameterized doc routes too
    normalized_doc = {re.sub(r"<\w+>", "<id>", r) for r in doc_routes}

    missing = sorted(app_routes - normalized_doc)
    extra = sorted(normalized_doc - app_routes)

    if missing:
        status = "warn"
        message = f"{len(missing)} route(s) undocumented in start.md: {', '.join(missing)}"
    else:
        status = "pass"
        message = f"All {len(app_routes)} page routes documented"

    return ClaudeConfigCheck(
        check_id="route_documentation",
        check_name="Dashboard Route Documentation",
        status=status,
        expected=sorted(app_routes),
        actual=sorted(normalized_doc),
        missing=missing,
        extra=extra,
        message=message,
    )


def check_settings_deny_rules(
    settings_path: Optional[Path] = None,
) -> ClaudeConfigCheck:
    """Validate settings.json deny rules cover critical destructive operations."""
    if settings_path is None:
        settings_path = PROJECT_ROOT / ".claude" / "settings.json"

    required_patterns = [
        "git push --force",
        "git push -f",
        "rm -rf",
        "git reset --hard",
        "DROP TABLE",
        "TRUNCATE",
    ]

    if not settings_path.exists():
        return ClaudeConfigCheck(
            check_id="settings_deny_rules",
            check_name="Settings Deny Rules",
            status="fail",
            expected=required_patterns,
            actual=[],
            missing=required_patterns,
            extra=[],
            message="settings.json not found",
        )

    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ClaudeConfigCheck(
            check_id="settings_deny_rules",
            check_name="Settings Deny Rules",
            status="fail",
            expected=required_patterns,
            actual=[],
            missing=required_patterns,
            extra=[],
            message="settings.json is invalid JSON",
        )

    deny_list = data.get("permissions", {}).get("deny", [])
    deny_text = " ".join(deny_list).lower()

    missing = []
    for pattern in required_patterns:
        if pattern.lower() not in deny_text:
            missing.append(pattern)

    if missing:
        status = "warn"
        message = f"{len(missing)} deny rule(s) missing: {', '.join(missing)}"
    else:
        status = "pass"
        message = f"All {len(required_patterns)} required deny patterns present"

    return ClaudeConfigCheck(
        check_id="settings_deny_rules",
        check_name="Settings Deny Rules",
        status=status,
        expected=required_patterns,
        actual=deny_list,
        missing=missing,
        extra=[],
        message=message,
    )


def check_e2e_test_coverage(
    app_path: Optional[Path] = None,
    e2e_dir: Optional[Path] = None,
) -> ClaudeConfigCheck:
    """Validate E2E test specs exist for major dashboard page groups."""
    if app_path is None:
        app_path = PROJECT_ROOT / "tools" / "dashboard" / "app.py"
    if e2e_dir is None:
        e2e_dir = PROJECT_ROOT / ".claude" / "commands" / "e2e"

    # Major page groups that should have E2E coverage
    required_groups = [
        "dashboard",    # / (home)
        "agents",       # /agents, /monitoring
        "activity",     # /activity, /usage
        "compliance",   # compliance artifacts
        "security",     # security scan results
        "chat",         # /chat (unified multi-stream + RICOAS)
        "portal",       # SaaS portal
    ]

    if not e2e_dir.exists():
        return ClaudeConfigCheck(
            check_id="e2e_coverage",
            check_name="E2E Test Coverage",
            status="warn",
            expected=required_groups,
            actual=[],
            missing=required_groups,
            extra=[],
            message="E2E test directory not found",
        )

    # Get E2E spec names (strip .md extension)
    e2e_specs = {f.stem for f in e2e_dir.glob("*.md")}
    e2e_text = " ".join(e2e_specs).lower()

    covered = []
    missing = []
    for group in required_groups:
        if group in e2e_text:
            covered.append(group)
        else:
            missing.append(group)

    if missing:
        status = "warn"
        message = f"{len(missing)} page group(s) lack E2E specs: {', '.join(missing)}"
    else:
        status = "pass"
        message = f"All {len(required_groups)} major page groups have E2E coverage"

    return ClaudeConfigCheck(
        check_id="e2e_coverage",
        check_name="E2E Test Coverage",
        status=status,
        expected=required_groups,
        actual=sorted(e2e_specs),
        missing=missing,
        extra=[],
        message=message,
    )


def check_hook_syntax(
    hooks_dir: Optional[Path] = None,
) -> ClaudeConfigCheck:
    """Validate all .claude/hooks/*.py files are syntactically correct Python."""
    if hooks_dir is None:
        hooks_dir = PROJECT_ROOT / ".claude" / "hooks"

    if not hooks_dir.exists():
        return ClaudeConfigCheck(
            check_id="hook_syntax",
            check_name="Hook Syntax Validation",
            status="fail",
            expected=[], actual=[], missing=[], extra=[],
            message="Hooks directory not found",
        )

    py_files = [f for f in hooks_dir.glob("*.py") if f.name != "__init__.py"]
    errors = []
    checked = []

    for py_file in sorted(py_files):
        checked.append(py_file.name)
        try:
            ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError as e:
            errors.append(f"{py_file.name}:{e.lineno}: {e.msg}")

    if errors:
        status = "fail"
        message = f"{len(errors)} hook file(s) have syntax errors: {'; '.join(errors)}"
    else:
        status = "pass"
        message = f"All {len(checked)} hook files are syntactically valid"

    return ClaudeConfigCheck(
        check_id="hook_syntax",
        check_name="Hook Syntax Validation",
        status=status,
        expected=checked,
        actual=checked,
        missing=[],
        extra=errors,
        message=message,
    )


def check_settings_hook_references(
    settings_path: Optional[Path] = None,
    hooks_dir: Optional[Path] = None,
) -> ClaudeConfigCheck:
    """Validate settings.json hook entries reference existing Python files."""
    if settings_path is None:
        settings_path = PROJECT_ROOT / ".claude" / "settings.json"
    if hooks_dir is None:
        hooks_dir = PROJECT_ROOT / ".claude" / "hooks"

    if not settings_path.exists():
        return ClaudeConfigCheck(
            check_id="hook_references",
            check_name="Hook File References",
            status="fail",
            expected=[], actual=[], missing=[], extra=[],
            message="settings.json not found",
        )

    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ClaudeConfigCheck(
            check_id="hook_references",
            check_name="Hook File References",
            status="fail",
            expected=[], actual=[], missing=[], extra=[],
            message="settings.json is invalid JSON",
        )

    hooks_config = data.get("hooks", {})
    referenced_files = []
    missing_files = []

    for hook_type, hook_list in hooks_config.items():
        for hook_entry in hook_list:
            for hook in hook_entry.get("hooks", []):
                command = hook.get("command", "")
                # Extract Python file path from command like:
                # "python $CLAUDE_PROJECT_DIR/.claude/hooks/foo.py || true"
                match = re.search(r"\.claude/hooks/(\w+\.py)", command)
                if match:
                    filename = match.group(1)
                    referenced_files.append(filename)
                    if not (hooks_dir / filename).exists():
                        missing_files.append(filename)

    if missing_files:
        status = "fail"
        message = f"{len(missing_files)} referenced hook file(s) missing: {', '.join(missing_files)}"
    else:
        status = "pass"
        message = f"All {len(referenced_files)} hook file references are valid"

    return ClaudeConfigCheck(
        check_id="hook_references",
        check_name="Hook File References",
        status=status,
        expected=sorted(set(referenced_files)),
        actual=[f.name for f in sorted(hooks_dir.glob("*.py")) if f.name != "__init__.py"] if hooks_dir.exists() else [],
        missing=missing_files,
        extra=[],
        message=message,
    )


# ---------------------------------------------------------------------------
# CLI harmonization checks (continuous_harmonization.md)
# ---------------------------------------------------------------------------

# Tools that are Flask web servers or special-purpose scripts (not CLI tools)
_JSON_FLAG_EXCLUDES = {
    "tools/dashboard/app.py",
    "tools/saas/api_gateway.py",
    "tools/saas/portal/app.py",
    "tools/saas/db/pg_schema.py",
    "tools/db/init_icdev_db.py",
    "tools/cli/output_formatter.py",  # utility library with demo __main__
}

# Tools where --project refers to something other than ICDEV project ID
_PROJECT_NAMING_EXCLUDES = {
    "tools/testing/e2e_runner.py",  # --project = Playwright browser type
}


def _scan_argparse_tools(tools_dir: Path, excludes: Optional[Set[str]] = None) -> List[Path]:
    """Find all Python files under tools/ that use argparse + __main__."""
    results = []
    for py_file in sorted(tools_dir.rglob("*.py")):
        # Skip __init__, test files, migrations
        if py_file.name.startswith("__") or "/tests/" in str(py_file).replace("\\", "/"):
            continue
        rel = str(py_file.relative_to(PROJECT_ROOT)).replace("\\", "/")
        if excludes and rel in excludes:
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "ArgumentParser" in content and '__name__' in content and '"__main__"' in content:
            results.append(py_file)
    return results


def check_cli_json_flag(
    tools_dir: Optional[Path] = None,
) -> ClaudeConfigCheck:
    """Verify all CLI tools with argparse support the --json flag."""
    if tools_dir is None:
        tools_dir = PROJECT_ROOT / "tools"

    argparse_files = _scan_argparse_tools(tools_dir, excludes=_JSON_FLAG_EXCLUDES)
    missing = []
    checked = []

    for py_file in argparse_files:
        content = py_file.read_text(encoding="utf-8")
        rel = str(py_file.relative_to(PROJECT_ROOT)).replace("\\", "/")
        checked.append(rel)
        # Check for standard --json flag (not --format json)
        if '"--json"' not in content and "'--json'" not in content:
            missing.append(rel)

    if missing:
        status = "warn"
        message = f"{len(missing)}/{len(checked)} CLI tools missing --json flag"
    else:
        status = "pass"
        message = f"All {len(checked)} CLI tools support --json"

    return ClaudeConfigCheck(
        check_id="cli_json_flag",
        check_name="CLI --json Flag Coverage",
        status=status,
        expected=checked,
        actual=[f for f in checked if f not in missing],
        missing=missing,
        extra=[],
        message=message,
    )


def check_cli_project_naming(
    tools_dir: Optional[Path] = None,
) -> ClaudeConfigCheck:
    """Verify CLI tools use --project-id (not bare --project) for project identifiers."""
    if tools_dir is None:
        tools_dir = PROJECT_ROOT / "tools"

    argparse_files = _scan_argparse_tools(tools_dir, excludes=_PROJECT_NAMING_EXCLUDES)
    violations = []
    checked = []

    for py_file in argparse_files:
        content = py_file.read_text(encoding="utf-8")
        rel = str(py_file.relative_to(PROJECT_ROOT)).replace("\\", "/")
        # Only check files that reference project at all
        if '"--project"' not in content and "'--project'" not in content:
            continue
        checked.append(rel)
        # Check for bare --project (not --project-id, --project-dir, --project-path)
        if re.search(r'''['"]--project['"]''', content) and \
           not re.search(r'''['"]--project-(?:id|dir|path)['"]''', content):
            violations.append(rel)
        elif re.search(r'''['"]--project['"]''', content):
            # Has both --project and --project-id/dir/path — bare --project is a compat alias (ok)
            pass

    # Re-check: files with ONLY bare --project (no --project-id alias)
    real_violations = []
    for rel in violations:
        py_file = PROJECT_ROOT / rel.replace("/", "\\") if sys.platform == "win32" else PROJECT_ROOT / rel
        content = py_file.read_text(encoding="utf-8")
        if '"--project-id"' not in content and "'--project-id'" not in content:
            real_violations.append(rel)

    if real_violations:
        status = "warn"
        message = f"{len(real_violations)} tool(s) use --project instead of --project-id"
    else:
        status = "pass"
        message = f"All {len(checked)} tools with project args use --project-id"

    return ClaudeConfigCheck(
        check_id="cli_project_naming",
        check_name="CLI --project-id Naming",
        status=status,
        expected=checked,
        actual=[f for f in checked if f not in real_violations],
        missing=real_violations,
        extra=[],
        message=message,
    )


def check_db_path_centralization(
    tools_dir: Optional[Path] = None,
) -> ClaudeConfigCheck:
    """Verify tools use db_utils.py instead of hardcoding DB paths."""
    if tools_dir is None:
        tools_dir = PROJECT_ROOT / "tools"

    hardcoded_pattern = re.compile(
        r'''(?:Path\s*\([^)]*\)\s*(?:/\s*["']data["']\s*){1,2}/\s*["'](?:icdev|memory|platform)\.db["'])'''
        r'''|(?:["']\S*data[/\\](?:icdev|memory|platform)\.db["'])''',
    )

    violations = []
    checked_count = 0

    for py_file in sorted(tools_dir.rglob("*.py")):
        if py_file.name.startswith("__"):
            continue
        # Skip db_utils.py, test files, and template/code-gen files that
        # reference DB paths in generated output (not actual DB connections)
        rel = str(py_file.relative_to(PROJECT_ROOT)).replace("\\", "/")
        if "compat/db_utils.py" in rel or "/tests/" in rel:
            continue
        # instruction_generator.py and platform_setup.py emit DB paths in
        # generated instruction files and setup scripts — not actual connections
        if "dx/instruction_generator.py" in rel or "installer/platform_setup.py" in rel:
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "icdev.db" not in content and "memory.db" not in content and "platform.db" not in content:
            continue
        checked_count += 1
        # Check for hardcoded paths (not import of db_utils)
        if hardcoded_pattern.search(content) and "from tools.compat.db_utils" not in content:
            violations.append(rel)

    if violations:
        status = "warn"
        message = f"{len(violations)} tool(s) hardcode DB paths instead of using db_utils.py"
    else:
        status = "pass"
        message = f"All {checked_count} DB-using tools use centralized db_utils.py"

    return ClaudeConfigCheck(
        check_id="db_path_centralization",
        check_name="DB Path Centralization",
        status=status,
        expected=[],
        actual=[],
        missing=violations,
        extra=[],
        message=message,
    )


# ---------------------------------------------------------------------------
# Check registry and orchestrator
# ---------------------------------------------------------------------------

CHECK_REGISTRY: Dict[str, callable] = {
    "append-only": check_append_only_table_coverage,
    "routes": check_dashboard_route_documentation,
    "settings": check_settings_deny_rules,
    "e2e": check_e2e_test_coverage,
    "hooks-syntax": check_hook_syntax,
    "hooks-refs": check_settings_hook_references,
    "cli-json": check_cli_json_flag,
    "cli-naming": check_cli_project_naming,
    "db-path": check_db_path_centralization,
}


def run_all_checks(selected: Optional[List[str]] = None) -> ClaudeConfigReport:
    """Run selected (or all) checks and produce aggregate report."""
    checks_to_run = CHECK_REGISTRY if selected is None else {
        k: v for k, v in CHECK_REGISTRY.items() if k in selected
    }

    results: List[ClaudeConfigCheck] = []
    for _name, check_fn in sorted(checks_to_run.items()):
        results.append(check_fn())

    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    warned = sum(1 for r in results if r.status == "warn")

    return ClaudeConfigReport(
        overall_pass=(failed == 0),
        timestamp=datetime.now(timezone.utc).isoformat(),
        checks=results,
        total_checks=len(results),
        passed_checks=passed,
        failed_checks=failed,
        warned_checks=warned,
    )


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_human(report: ClaudeConfigReport) -> str:
    """Format report for terminal output with ANSI colors."""
    lines = []
    lines.append("=" * 60)
    lines.append("  ICDEV .claude Directory Governance Report")
    lines.append("=" * 60)
    lines.append("")

    status_icons = {"pass": "[PASS]", "fail": "[FAIL]", "warn": "[WARN]"}

    for check in report.checks:
        icon = status_icons.get(check.status, "[????]")
        lines.append(f"  {icon} {check.check_name}")
        lines.append(f"         {check.message}")
        if check.missing:
            for item in check.missing[:10]:
                lines.append(f"           - {item}")
            if len(check.missing) > 10:
                lines.append(f"           ... and {len(check.missing) - 10} more")
        lines.append("")

    lines.append("-" * 60)
    overall = "PASS" if report.overall_pass else "FAIL"
    lines.append(
        f"  Overall: {overall} "
        f"({report.passed_checks} passed, {report.failed_checks} failed, "
        f"{report.warned_checks} warned)"
    )
    lines.append("=" * 60)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Validate .claude directory alignment with ICDEV codebase"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Colored terminal output")
    parser.add_argument(
        "--check", default="all",
        help="Specific check or 'all'. Options: " + ", ".join(CHECK_REGISTRY.keys()),
    )
    args = parser.parse_args()

    selected = None if args.check == "all" else [args.check]
    report = run_all_checks(selected)

    if args.human:
        print(format_human(report))
    else:
        print(json.dumps(report.to_dict(), indent=2))

    sys.exit(0 if report.overall_pass else 1)


if __name__ == "__main__":
    main()
