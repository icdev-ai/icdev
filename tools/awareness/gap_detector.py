# CUI // SP-CTI
"""Phase 3 — Gap Detector.

Scans the ICDEV repo for structural gaps and writes oracle_predictions
rows under lens_name='internal_awareness' with prediction_type
'gap::<rule_id>'. The suggested_card_writer from Phase 2 then
promotes them to kanban_tasks (no new plumbing needed — just a new
rule set with different prediction_types).

Seven rules shipped in Phase 3 (all default-on per user sign-off):

  route_not_listed           (0.90) — Flask route exists but not in
                                      .claude/commands/start.md Pages
  tool_not_in_manifest       (0.95) — tools/ .py file not documented
                                      in tools/manifest.md
  skill_references_missing_goal (0.95) — SKILL.md references a
                                         goals/*.md that doesn't exist
  orphan_db_table            (0.85) — INSERT/SELECT references a table
                                      that no migration CREATE TABLEs
  broken_test_reference      (0.90) — tests/*.py imports a symbol from
                                      a module that no longer defines it
  route_no_e2e               (0.70) — Dashboard route with no matching
                                      URL literal in any tests/e2e_*.py
  empty_mcp_server           (0.85) — tools/mcp/*_server.py with zero
                                      registered tools

One rule shipped DEFAULT-OFF (per user sign-off):
  stale_code                 (0.50) — .py file > 30 days unmodified and
                                      no referenced test file — noisy

CLI:
    python tools/awareness/gap_detector.py --detect --json
    python tools/awareness/gap_detector.py --rule route_not_listed --json
    python tools/awareness/gap_detector.py --dry-run --json
    python tools/awareness/gap_detector.py --stats --json
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import ast
import hashlib
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

LOG = get_logger("gap_detector")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from tools.db.storage import get_connection  # noqa: E402
except ImportError:
    get_connection = None  # type: ignore[assignment]

LENS_NAME = "internal_awareness"


# ---------------------------------------------------------------------------
# Rule configuration
# ---------------------------------------------------------------------------


GAP_RULES: Dict[str, Dict[str, Any]] = {
    "route_not_listed": {
        "confidence": 0.90,
        "severity": "low",
        "enabled": True,
        "description": "Flask route defined in app.py but missing from start.md Pages: line",
    },
    "tool_not_in_manifest": {
        "confidence": 0.95,
        "severity": "medium",
        "enabled": True,
        "description": "Python tool file in tools/ not documented in tools/manifest.md",
    },
    "skill_references_missing_goal": {
        "confidence": 0.95,
        "severity": "high",
        "enabled": True,
        "description": "SKILL.md references a goal markdown file that does not exist",
    },
    "orphan_db_table": {
        "confidence": 0.85,
        "severity": "medium",
        "enabled": True,
        "description": "SQL INSERT/SELECT references table without a matching CREATE TABLE",
    },
    "broken_test_reference": {
        "confidence": 0.90,
        "severity": "high",
        "enabled": True,
        "description": "Test file imports a symbol that no longer exists in its source module",
    },
    "route_no_e2e": {
        "confidence": 0.70,
        "severity": "low",
        "enabled": True,
        "description": "Dashboard route has no matching URL literal in any tests/e2e_*.py",
    },
    "empty_mcp_server": {
        "confidence": 0.85,
        "severity": "medium",
        "enabled": True,
        "description": "MCP server file has zero registered @mcp_tool decorators or register_tool() calls",
    },
    "stale_code": {
        "confidence": 0.50,
        "severity": "low",
        "enabled": False,  # default-off per user sign-off — too noisy
        "description": "Python file > 30 days unmodified with no referencing test file",
    },
}


# ---------------------------------------------------------------------------
# Finding dataclass (plain dict for simplicity)
# ---------------------------------------------------------------------------


def _finding(rule_id: str, subject: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "rule_id": rule_id,
        "subject": subject,
        "evidence": evidence,
    }


# ---------------------------------------------------------------------------
# File scanning helpers
# ---------------------------------------------------------------------------


_EXCLUDED_PARTS = {
    ".git", ".tmp", ".venv", "venv", "__pycache__", "node_modules",
    "build", "dist", ".pytest_cache", ".mypy_cache", ".ruff_cache",
}


def _walk_py(root: Path, subdir: str = "") -> List[Path]:
    """Yield .py files under root, excluding the usual noise dirs."""
    base = root / subdir if subdir else root
    result: List[Path] = []
    if not base.exists() or not base.is_dir():
        return result
    for item in base.rglob("*.py"):
        if any(part in _EXCLUDED_PARTS for part in item.parts):
            continue
        result.append(item)
    return result


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(BASE_DIR)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


# ---------------------------------------------------------------------------
# Route classification helpers  (used by _rule_route_not_listed)
# ---------------------------------------------------------------------------

#: Call names whose presence in a return statement marks the handler as a
#: browser-navigable page route.
_PAGE_RETURN_CALLERS: frozenset = frozenset({"render_template", "redirect"})

#: Call names whose presence in a return statement marks the handler as a
#: JSON / API endpoint.
_API_RETURN_CALLERS: frozenset = frozenset({"jsonify", "Response", "make_response"})


def _walk_shallow(node: ast.AST):
    """Yield *node* and all its descendants without descending into nested
    function or class bodies.

    Used to inspect a route handler's own return statements while ignoring
    returns that belong to inner helper closures (e.g. ``@wraps`` wrappers
    or auth-check factories defined inside the handler).
    """
    yield node
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return  # do not recurse into nested body
    for child in ast.iter_child_nodes(node):
        yield from _walk_shallow(child)


def _ast_call_name(call_func: ast.expr) -> str:
    """Return the bare callable name from a Call's func node.

    Returns ``"jsonify"`` for both ``jsonify(...)`` (``ast.Name``) and
    ``flask.jsonify(...)`` (``ast.Attribute``), so callers do not need
    to handle both forms separately.
    """
    if isinstance(call_func, ast.Name):
        return call_func.id
    if isinstance(call_func, ast.Attribute):
        return call_func.attr
    return ""


def _classify_handler(
    func_node: "ast.FunctionDef | ast.AsyncFunctionDef",
) -> str:
    """Return ``'page'``, ``'api'``, or ``'ambiguous'`` by inspecting
    the return statements in a route handler's body.

    Classification rules (applied in priority order):

    * ``'page'``      — handler has at least one ``return render_template(...)``
                        or ``return redirect(...)``; wins even if ``jsonify``
                        also appears in another branch.
    * ``'api'``       — handler has only ``return jsonify(...)`` /
                        ``Response(...)`` / ``make_response(...)`` returns.
    * ``'ambiguous'`` — no explicit typed return found (e.g. bare ``return``,
                        dict auto-jsonify); treated as API so the rule
                        under-flags rather than over-flags.

    The body walk is *shallow*: nested function / class bodies are skipped
    so that closures defined inside the handler do not pollute the result.
    """
    has_page = False
    has_api = False
    for stmt in func_node.body:
        for node in _walk_shallow(stmt):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            val = node.value
            # Unwrap status-code tuple: ``return render_template(...), 200``
            if isinstance(val, ast.Tuple) and val.elts:
                val = val.elts[0]
            if not isinstance(val, ast.Call):
                continue
            name = _ast_call_name(val.func)
            if name in _PAGE_RETURN_CALLERS:
                has_page = True
            elif name in _API_RETURN_CALLERS:
                has_api = True

    if has_page:
        return "page"
    if has_api:
        return "api"
    return "ambiguous"


def _extract_routes_with_type(src: str) -> Dict[str, str]:
    """Parse Python source and return ``{normalized_route: classification}``
    for every ``@*.route(...)``-decorated function.

    Classification values: ``'page'``, ``'api'``, or ``'ambiguous'``.

    If the file cannot be AST-parsed a regex fallback runs and marks all
    discovered routes ``'ambiguous'``, so they are silently skipped by
    the caller (better to under-flag than to crash on a syntax error).

    When the same normalised route appears under multiple decorators
    (e.g. different HTTP methods), ``'page'`` beats ``'api'`` beats
    ``'ambiguous'``.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        result: Dict[str, str] = {}
        for m in re.finditer(r'@\w+\.route\(\s*[\'"]([^\'"]+)[\'"]', src):
            normalized = re.sub(r"<[^>]+>", "<param>", m.group(1)).split("?", 1)[0]
            result[normalized] = "ambiguous"
        return result

    result: Dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not (
                isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "route"
            ):
                continue
            if not decorator.args:
                continue
            first = decorator.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            url = first.value.split("?", 1)[0]
            normalized = re.sub(r"<[^>]+>", "<param>", url)
            classification = _classify_handler(node)
            # 'page' beats 'api' beats 'ambiguous' when a route appears twice
            prev = result.get(normalized, "ambiguous")
            if prev == "page" or classification == "page":
                result[normalized] = "page"
            elif prev == "api" or classification == "api":
                result[normalized] = "api"
            else:
                result[normalized] = "ambiguous"

    return result


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------


def _rule_route_not_listed() -> List[Dict[str, Any]]:
    """Parse .claude/commands/start.md for the Pages: routes, compare
    against @app.route() declarations in tools/dashboard/app.py and
    any blueprint files in the same directory.

    Uses AST inspection to classify each route handler:

    * Handlers with ``render_template()`` / ``redirect()`` returns → **page**
      route → checked against the Pages: line.
    * Handlers with ``jsonify()`` / ``Response()`` / ``make_response()``
      returns → **API** endpoint → skipped.
    * Ambiguous handlers (dict return, no explicit response call) → skipped
      (better to under-flag than over-flag).

    Routes whose URL starts with ``/api/`` are always skipped as a
    fast-path guard, regardless of handler classification.
    """
    findings: List[Dict[str, Any]] = []

    # 1. Collect route→type from app.py via AST (avoids importing the
    #    dashboard which has heavy startup cost)
    dashboard_dir = BASE_DIR / "tools" / "dashboard"
    app_py = dashboard_dir / "app.py"
    if not app_py.exists():
        return findings

    route_types: Dict[str, str] = _extract_routes_with_type(_read_text(app_py))

    # 2. Also scan blueprint files in tools/dashboard/ (merge, page wins)
    for bp_file in sorted(dashboard_dir.glob("*.py")):
        if bp_file.name == "app.py":
            continue
        bp_src = _read_text(bp_file)
        if ".route(" not in bp_src:
            continue  # fast skip — no route decorators present
        for route, rtype in _extract_routes_with_type(bp_src).items():
            prev = route_types.get(route, "ambiguous")
            if prev == "page" or rtype == "page":
                route_types[route] = "page"
            elif prev == "api" or rtype == "api":
                route_types[route] = "api"

    # 3. Collect Pages: routes from start.md
    start_md = BASE_DIR / ".claude" / "commands" / "start.md"
    if not start_md.exists():
        return findings
    start_src = _read_text(start_md)
    pages_match = re.search(r"Pages:\s*(.+?)(?:\n\n|\n\s*-)", start_src, re.DOTALL)
    if not pages_match:
        return findings
    pages_blob = pages_match.group(1)
    listed_routes: Set[str] = set()
    for m in re.finditer(r"`([^`]+)`", pages_blob):
        val = m.group(1).strip()
        if not val.startswith("/"):
            continue
        normalized = re.sub(r"<[^>]+>", "<param>", val)
        listed_routes.add(normalized)

    # 4. Flag page routes missing from start.md
    for route in sorted(route_types):
        if route.startswith("/api/"):
            continue  # fast-path: /api/* prefixed routes are never page routes
        if route_types[route] != "page":
            continue  # api or ambiguous → skip
        if route in listed_routes:
            continue
        findings.append(
            _finding(
                "route_not_listed",
                route,
                {
                    "app_py_route": route,
                    "route_type": "page",
                    "total_app_routes": len(route_types),
                    "total_listed_routes": len(listed_routes),
                },
            )
        )
    return findings


def _rule_tool_not_in_manifest() -> List[Dict[str, Any]]:
    """Every tools/*.py that isn't __init__.py, tests, or a helper
    should have a row in tools/manifest.md. Reuse the coherence
    checker's existing `check_manifest` output when possible, else
    do a direct scan.
    """
    findings: List[Dict[str, Any]] = []
    manifest_path = BASE_DIR / "tools" / "manifest.md"
    if not manifest_path.exists():
        return findings
    manifest_src = _read_text(manifest_path)
    # Also read all manifest shards — the main manifest.md is now an index
    # of shard files; the actual tool entries live in tools/manifest/*.md.
    shard_dir = BASE_DIR / "tools" / "manifest"
    if shard_dir.exists():
        for shard in sorted(shard_dir.glob("*.md")):
            manifest_src += "\n" + _read_text(shard)

    # Collect every "tools/..." path mentioned in the manifest table.
    # The manifest uses mixed separators (forward slashes AND backslashes)
    # and sometimes wraps paths in backticks.  Normalize everything to
    # forward-slash for comparison.
    documented: Set[str] = set()
    # Pattern 1: table cells  | Name | tools/path/file.py |  (fwd or back slash)
    for m in re.finditer(r"\|\s*([^|]+?)\s*\|\s*`?(tools[/\\][^|`]+?)`?\s*\|", manifest_src):
        documented.add(m.group(2).strip().replace("\\", "/"))
    # Pattern 2: freeform references outside tables (backtick-wrapped)
    for m in re.finditer(r"`(tools[/\\][\w/\\.]+\.py)`", manifest_src):
        documented.add(m.group(1).strip().replace("\\", "/"))

    # Also build a set of documented basenames so that internal modules
    # documented under a different path format (e.g. "daemon.py" in the
    # appforge row) are recognised.
    documented_basenames: Dict[str, Set[str]] = {}  # parent_dir -> {basenames}
    for doc_path in documented:
        parts = doc_path.replace("\\", "/").split("/")
        if len(parts) >= 3:
            parent = parts[-2]  # e.g. "appforge" from "tools/appforge/daemon.py"
            documented_basenames.setdefault(parent, set()).add(parts[-1])

    # Internal module names that are helpers/config/base classes — not
    # standalone tools and should never be listed in the manifest.
    _INTERNAL_NAMES: Set[str] = {
        "config.py", "helpers.py", "base.py", "utils.py",
        "types.py", "models.py", "schemas.py", "exceptions.py",
    }

    # Walk tools/ and flag undocumented top-level modules. Skip helpers,
    # __init__.py, tests, nested tool packages' internals.
    skip_names = {"__init__.py", "constants.py", "conftest.py"}
    tools_dir = BASE_DIR / "tools"
    for py in _walk_py(tools_dir):
        rel = _rel(py)
        name = py.name
        if name in skip_names:
            continue
        # Only flag top-level tool scripts, not deep helpers
        depth = rel.count("/")
        if depth > 3:
            continue
        if rel in documented:
            continue
        # Skip private helpers (start with _)
        if name.startswith("_"):
            continue
        # Skip common internal module names (config, helpers, base, etc.)
        if name in _INTERNAL_NAMES:
            continue
        # Check if the parent dir + basename appear together in
        # documented paths (catches backslash/formatting mismatches)
        parent_dir = py.parent.name
        if parent_dir in documented_basenames and name in documented_basenames[parent_dir]:
            continue
        findings.append(
            _finding(
                "tool_not_in_manifest",
                rel,
                {"file_path": rel, "depth": depth},
            )
        )
    # Cap results to avoid flooding the kanban — top 20 most concerning
    return findings[:20]


def _rule_skill_references_missing_goal() -> List[Dict[str, Any]]:
    """Each .agents/skills/*/SKILL.md sometimes references goal files
    by path. Flag references where the goal file does not exist.
    """
    findings: List[Dict[str, Any]] = []
    skills_root = BASE_DIR / ".agents" / "skills"
    if not skills_root.exists():
        return findings
    goals_root = BASE_DIR / "goals"

    for skill_md in skills_root.glob("*/SKILL.md"):
        src = _read_text(skill_md)
        # Find all "goals/xxx.md" references in the text
        for m in re.finditer(r"goals/([a-zA-Z0-9_\-]+\.md)", src):
            goal_file = goals_root / m.group(1)
            if goal_file.exists():
                continue
            findings.append(
                _finding(
                    "skill_references_missing_goal",
                    _rel(skill_md),
                    {
                        "skill_file": _rel(skill_md),
                        "missing_goal": f"goals/{m.group(1)}",
                    },
                )
            )
    return findings


_SQL_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_SQL_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_sql_comments(sql: str) -> str:
    """Remove SQL line (``-- ...``) and block (``/* ... */``) comments.

    Called before running the CREATE/INSERT/UPDATE/FROM regexes so that
    English prose inside a SQL comment doesn't get parsed as a table
    reference. Common pattern to guard against::

        CREATE TABLE research_signals (  -- discovered from all 8 streams
            id TEXT PRIMARY KEY,
            ...
        )

    Without comment stripping, the ``FROM\\s+\\w+`` regex would match
    the ``from all`` inside the comment and emit ``all`` as a
    nonexistent orphan table. With comment stripping, only the real
    structural SQL is scanned.
    """
    s = _SQL_LINE_COMMENT_RE.sub("", sql)
    s = _SQL_BLOCK_COMMENT_RE.sub("", s)
    return s


def _extract_py_string_literals(py_path: Path) -> List[str]:
    """Return every ``str`` constant in a Python file.

    Combined with :func:`_is_likely_sql`, this is the foundation of a
    far less noisy ``orphan_db_table`` rule: SQL can only legally
    appear inside string literals AND real SQL has a characteristic
    structure (verb + bounded clause). Scanning only strings and
    only strings that look structurally like SQL eliminates two
    distinct classes of false positive from the pre-2026-04-11
    detector:

      1. Python ``from X import Y`` statements being parsed as
         ``FROM X`` (not string literals, so excluded here)
      2. Code-generation templates that embed Python source as a
         multiline string (these ARE string literals, so the
         ``_is_likely_sql`` structural check catches them instead)

    Non-ast-parseable files fall back to scanning the raw source so
    dynamically generated SQL files are still covered — best-effort,
    not a correctness boundary.
    """
    src = _read_text(py_path)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return [src]
    strings: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append(node.value)
        elif isinstance(node, ast.JoinedStr) and node.values:
            # f-strings are JoinedStr in the AST; their Constant children are
            # already visited individually by ast.walk, but a trailing segment
            # (e.g. the part after the last {var}) may not start with a SQL
            # keyword and fails _is_likely_sql on its own.  Joining all static
            # parts preserves the full schema text so create_re can find every
            # CREATE TABLE regardless of where f-string substitutions fall.
            parts = [
                n.value for n in node.values
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            ]
            if parts:
                strings.append("".join(parts))
    return strings


# A SQL statement, ignoring any leading SQL line comments, must START
# with one of these clause heads followed by a structurally unambiguous
# follow-up (e.g. ``CREATE TABLE`` not ``Create a vector store...``,
# ``SELECT *`` not ``Select an option from the list``). This is how
# we distinguish real SQL string literals from Python docstrings and
# code-generation templates that happen to contain words like
# ``from`` or ``create``.
_SQL_HEAD_RE = re.compile(
    r"^\s*(?:--[^\n]*\n\s*)*"                    # optional leading -- comments
    r"(?:"
    r"CREATE\s+(?:TABLE|UNIQUE\s+INDEX|INDEX|VIEW|TRIGGER)\b|"
    r"INSERT\s+INTO\s+\w|"
    r"INSERT\s+OR\s+(?:REPLACE|IGNORE)\s+INTO\s+\w|"
    r"UPDATE\s+\w+\s+SET\b|"
    r"DELETE\s+FROM\s+\w|"
    r"SELECT\s+(?:\*|DISTINCT\b|ALL\b|COUNT\s*\(|[\w\.]+\s*(?:,|\(|FROM\b))|"
    r"WITH\s+\w+\s+AS\s*\(|"
    r"ALTER\s+TABLE\s+\w|"
    r"DROP\s+(?:TABLE|INDEX|VIEW|TRIGGER)\s+(?:IF\s+EXISTS\s+)?\w|"
    r"REPLACE\s+INTO\s+\w|"
    r"PRAGMA\s+\w"
    r")",
    re.IGNORECASE,
)


def _is_likely_sql(s: str) -> bool:
    """Return True when ``s`` structurally opens with a SQL statement.

    The check is deliberately start-anchored and strict: the string must
    begin (possibly after leading ``-- line comments``) with a SQL
    verb that has an unambiguous clause head — ``CREATE TABLE``,
    ``INSERT INTO <name>``, ``UPDATE <name> SET``, ``DELETE FROM
    <name>``, ``SELECT * | DISTINCT | <col>,| <col>( | <col> FROM``,
    ``WITH <cte> AS (``, ``ALTER TABLE <name>``, ``DROP TABLE | INDEX
    | VIEW | TRIGGER <name>``, or ``PRAGMA <name>``.

    A prior version of this function used substring matching
    (``"SELECT" in u and " FROM " in u``) and produced many false
    positives from Python docstrings like ``"Create a vector store
    instance based on config loaded from args/..."`` — the substring
    check saw ``CREATE`` and ``FROM`` as keywords and let them
    through. Start-anchoring with a structural follow-up eliminates
    that entire class. Short strings (<15 chars) are rejected outright
    because legal SQL is essentially never shorter.
    """
    if not s or len(s) < 15:
        return False
    return bool(_SQL_HEAD_RE.match(s))


def _rule_orphan_db_table() -> List[Dict[str, Any]]:
    """Find table names written by INSERT/UPDATE/SELECT that have no
    matching CREATE TABLE in any tool module or migration file.

    Fixed 2026-04-11: the previous version ran the CREATE/INSERT/FROM
    regexes over the raw text of every Python file, which meant
    Python's own ``from X import Y`` syntax got parsed as ``FROM X``
    and every imported module name ended up on the orphan list (the
    top of the findings list read like a Python stdlib dump:
    ``__future__, _check_, a2a, ast, above, active, actual...``).

    The fix is strictly structural: we AST-parse each file and pull
    only ``ast.Constant`` string values, which is where SQL can
    legally appear. Real SQL strings (single-line, multi-line, f-strings
    with literal prefix/suffix, etc.) are still captured because
    ``ast.Constant`` holds the raw unescaped value. Python import
    statements are ``ast.ImportFrom`` nodes, not ``ast.Constant``, so
    they're filtered out at the root of the scan — no blacklist
    whack-a-mole needed.

    The CREATE-TABLE pass ALSO scans ``*.sql`` files under tools/ and
    tools/db/migrations/ so raw-SQL migration files are covered.
    """
    findings: List[Dict[str, Any]] = []
    tools_dir = BASE_DIR / "tools"
    # CREATE TABLE statements legitimately live outside tools/ as well:
    # canvas / app schema modules under apps/ own their own DDL (e.g.
    # apps/innovation/db.py defines innov_ideas, innov_assessments, ...).
    # The reference scan below only looks at tools/, so a table created in
    # apps/ but read from a tools/ adapter (e.g. tools/iqe/adapters/
    # innovation.py SELECTs innov_ideas) was mis-flagged as an orphan.
    # Scan apps/ for schema too so those CREATE TABLEs are seen.
    # icdev/ is the canonical package location (tools/ is a shim); canvas
    # schemas defined under icdev/tools/ (e.g. aac_scans in
    # icdev/tools/ai_augmentation/db/init_db.py) must also be covered so
    # seed/adapter references in tools/ don't false-fire as orphans.
    # migrations/ (root) holds raw SQL migration files that create tables
    # referenced by production code (e.g. rag_queries, rag_citations).
    schema_roots = [tools_dir, BASE_DIR / "apps", BASE_DIR / "icdev", BASE_DIR / "migrations"]

    created: Set[str] = set()
    referenced: Dict[str, str] = {}  # table → first referencing file

    # Tables whose write path is intentionally deferred to a future phase.
    # The schema (CREATE TABLE) exists in a migration; only INSERT is deferred.
    # Add here to suppress recurring orphan_db_table gap tasks.
    deferred_write_tables = {
        "ad_trap_events",      # write path deferred to Phase 7.12 (trap scanner daemon)
        "ad_radar_snapshots",  # write path deferred pending radar reflex wiring
    }

    # Idealized table names referenced ONLY by the synthetic AI-ify
    # db -> render -> notify candidate handlers in tools/notification_service/
    # (handler_service.py, render_handler_service.py, report_service.py). Those
    # handler functions were generated as single-concern "AI augmentation
    # candidates" for the aiify scanner: each SELECTs from a plausible-sounding
    # subsystem table to build a notification body. They are NOT wired into any
    # production route, reflex, dispatch, or registry, and their tests fully
    # mock get_connection() (a _FakeConn), so these tables are never created and
    # never queried at runtime. The names are also fictional — they do not match
    # the real owning subsystems' schemas (DIC uses dic_documents/dic_sections/
    # dic_chunk_links, NOT dic_chunks/dic_entities; data mapping uses dm_* tables,
    # NOT data_mapping_*). Creating real CREATE TABLEs for these would add
    # unpopulated, RLS-burdened schema cruft that duplicates the real canvases.
    # Suppress here so this synthetic-handler class stops generating recurring
    # orphan_db_table batch cards. If a real table of one of these names is ever
    # added, its CREATE TABLE will be seen first (``if t in created: continue``)
    # and the entry becomes a harmless no-op.
    synthetic_handler_tables = {
        "agent_errors", "agent_metrics",                 # handle_agent_incident_handler
        "canvas_assessments", "canvas_findings",         # render_and_deliver_canvas_status
        "canvas_gate_results",                           # report_service canvas gate report
        "compliance_gates", "gate_failures",             # render compliance gate notice
        "data_mapping_runs", "data_mapping_field_matches",
        "data_mapping_schemas",                          # render_and_notify_data_mapping_run
        "dic_chunks", "dic_entities",                    # render_and_send_dic_document_summary
        "fedramp_ato_packages",                          # render fedramp ATO package notice
        "finetune_eval_results", "finetune_jobs", "finetune_metrics",  # render_handler_service finetune
        # notification_service handler stubs (aiify-scanner-generated candidates,
        # never wired to production routes; all tests mock get_connection())
"canvas_designs",                                # handler_service canvas status handler
        "cmmc_practice_gaps", "cmmc_systems",            # handler_service cmmc gap handler
        "fedramp_controls",                              # render_handler_service fedramp notice
        "gate_failures",                                 # render_handler_service gate report
        "genesis_designs",                               # multiple notification_service files
        # RAG tool uses a plausible SQL template string for demonstration; no real table.
        "entitlements",                                  # tools/rag/entitlement_rag.py template
        # CTE alias: gap_detector doesn't parse WITH ... AS (...) and flags the alias name.
        "domain_coverage",                               # tools/govcon/gap_analyzer.py CTE

    }

    # Table names that are legitimately referenced but are DBMS-provided
    # and have no CREATE TABLE anywhere in tool code. Filtered at the
    # end so they never enter the orphan list.
    system_tables = {
        "information_schema",  # Postgres catalog namespace
        "pg_catalog",          # Postgres catalog namespace
        # Oracle catalog view — appears in SQL documentation/examples in migration_canvas
        "all_tables",          # tools/migration_canvas/cam_refactor_engine.py example SQL
        # PostgreSQL schema name used schema-qualified as ``FROM cache.stats`` in
        # cache_savings/constants.py; from_re captures ``cache`` as the table name
        # because it stops at the dot — the real table is ``cache.stats``.
        "cache",               # tools/cache_savings/constants.py schema-qualified ref
        # pg_catalog tables used by migration introspection
        "pg_tables", "pg_indexes", "pg_class", "pg_namespace",
        "pg_constraint",       # constraint catalog (ALTER TABLE CHECK introspection)
        "pg_attribute",        # column catalog
        "pg_type",             # type catalog
        "pg_index",            # index catalog
        "pg_proc",             # procedure/function catalog
        "pg_roles",            # role catalog
        "pg_stat_user_tables", # stats view
        "sqlite_master",
        "sqlite_temp_master",
        "sqlite_sequence",
        "sys",
        "dual",        # Oracle pseudo-table
        "all_tables",  # Oracle data-dictionary view (SELECT FROM all_tables WHERE owner=...)
        # DuckDB table-valued functions: appear in FROM clauses but are never
        # CREATE TABLE'd — the from_re negative lookahead (?!\s*\() should
        # already exclude them, but f-string AST splitting can produce a
        # Constant that ends before the '(' and slip through in some
        # Python versions.
        "read_json_auto",
        "read_csv_auto",
        "read_parquet",
        "glob",
        # PostgreSQL schema name used schema-qualified as ``FROM cache.stats`` in
        # cache_savings/constants.py; from_re captures ``cache`` as the table name
        # because it stops at the dot — the real table is ``cache.stats``.
        "cache",               # tools/cache_savings/constants.py schema-qualified ref
    }

    create_re = re.compile(
        r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+(?:[a-zA-Z_][\w]*\.)?([a-zA-Z_][\w]*)",
        re.IGNORECASE,
    )
    # ALTER TABLE x RENAME TO y is equivalent to "CREATE TABLE y" for the
    # purposes of this check: the renamed table exists under the new name and
    # any subsequent INSERT/SELECT against it is valid.
    rename_re = re.compile(
        r"ALTER\s+TABLE\s+[a-zA-Z_][\w]*\s+RENAME\s+TO\s+([a-zA-Z_][\w]*)",
        re.IGNORECASE,
    )
    # INSERT INTO must be followed by a SQL-valid continuation: a column
    # list `(...)`, ``VALUES``, a sub-``SELECT``, or ``DEFAULT VALUES``.
    # The lookahead filters out English prose like "Insert into append-only
    # proposal_status_history" (where ``append`` is followed by ``-only``,
    # not a SQL clause head).
    insert_re = re.compile(
        r"INSERT\s+(?:OR\s+(?:REPLACE|IGNORE|ABORT|FAIL|ROLLBACK)\s+)?"
        r"INTO\s+([a-zA-Z_][\w]*)\s*"
        r"(?=\(|VALUES\b|SELECT\b|DEFAULT\s+VALUES\b)",
        re.IGNORECASE,
    )
    # Negative lookahead (?!\s*\() excludes table-valued function calls like
    # DuckDB's read_json_auto(...), read_csv_auto(...), glob(...), etc.
    from_re = re.compile(
        r"\bFROM\s+([a-zA-Z_][\w]*)(?!\s*\()\b",
        re.IGNORECASE,
    )

    # First pass: collect CREATE TABLE (and RENAME TO) from SQL-shaped string
    # literals in .py files. Comments are stripped first so documentation
    # inside SQL doesn't leak English prose into the regex matcher.
    for root in schema_roots:
        for py in _walk_py(root):
            for sql in _extract_py_string_literals(py):
                if not _is_likely_sql(sql):
                    continue
                sql_clean = _strip_sql_comments(sql)
                for m in create_re.finditer(sql_clean):
                    created.add(m.group(1).lower())
                for m in rename_re.finditer(sql_clean):
                    created.add(m.group(1).lower())

    # Also scan raw .sql files (migrations written as pure SQL). These
    # don't need the _is_likely_sql gate — if it's a .sql file, assume
    # it's SQL. Comments still get stripped.
    for root in schema_roots:
        for sql_path in root.rglob("*.sql"):
            if any(part in _EXCLUDED_PARTS for part in sql_path.parts):
                continue
            src = _strip_sql_comments(_read_text(sql_path))
            for m in create_re.finditer(src):
                created.add(m.group(1).lower())
            for m in rename_re.finditer(src):
                created.add(m.group(1).lower())

    # Second pass: references from SQL-shaped string literals. Python
    # ``from X import Y`` statements are ast.ImportFrom (not ast.Constant),
    # so they never reach this scan. And code-generation templates that
    # embed Python source get filtered by _is_likely_sql because Python
    # templates don't start with strict SQL heads.
    for py in _walk_py(tools_dir):
        rel = _rel(py)
        for sql in _extract_py_string_literals(py):
            if not _is_likely_sql(sql):
                continue
            sql_clean = _strip_sql_comments(sql)
            for m in insert_re.finditer(sql_clean):
                t = m.group(1).lower()
                if t not in referenced:
                    referenced[t] = rel
            for m in from_re.finditer(sql_clean):
                t = m.group(1).lower()
                if t not in referenced:
                    referenced[t] = rel

    for sql_path in tools_dir.rglob("*.sql"):
        if any(part in _EXCLUDED_PARTS for part in sql_path.parts):
            continue
        src = _strip_sql_comments(_read_text(sql_path))
        rel = _rel(sql_path)
        for m in insert_re.finditer(src):
            t = m.group(1).lower()
            if t not in referenced:
                referenced[t] = rel
        for m in from_re.finditer(src):
            t = m.group(1).lower()
            if t not in referenced:
                referenced[t] = rel

    # Find orphans: referenced but not created. Skip system-provided
    # tables (information_schema, pg_catalog, sqlite_*) since those
    # are never defined in tool code by design.
    for t, ref_file in sorted(referenced.items()):
        if t in created:
            continue
        if t in system_tables:
            continue
        if t in deferred_write_tables:
            continue
        if t in synthetic_handler_tables:
            continue
        # Catch any pg_*, sqlite_*, or information_schema.* catalog the
        # explicit set above hasn't enumerated yet.
        if t.startswith(("pg_", "sqlite_")) or t == "information_schema":
            continue
        if len(t) < 3:
            # Tables named ``a``, ``id`` etc. are almost certainly
            # sub-query aliases, not real missing tables.
            continue
        findings.append(
            _finding(
                "orphan_db_table",
                t,
                {"table": t, "first_reference": ref_file},
            )
        )
    return findings[:25]  # cap noise (was 15; now more signal per slot)


def _submodule_exists(pkg_mod: str, submod_name: str) -> bool:
    """Return True if ``from <pkg_mod> import <submod_name>`` would
    resolve to a submodule on disk — either ``<pkg_dir>/<submod>.py``
    or a nested package ``<pkg_dir>/<submod>/__init__.py``. This
    complements the top-level-names check in ``_load_module_names``,
    which only sees symbols DECLARED in ``__init__.py`` and misses
    Python's built-in submodule resolution (``from tools.awareness
    import gap_detector`` is a valid import even when ``gap_detector``
    is not re-exported by ``tools/awareness/__init__.py``).
    """
    parts = pkg_mod.split(".")
    pkg_dir = BASE_DIR / Path(*parts)
    if not pkg_dir.is_dir():
        return False
    if (pkg_dir / f"{submod_name}.py").exists():
        return True
    if (pkg_dir / submod_name / "__init__.py").exists():
        return True
    return False


def _rule_broken_test_reference() -> List[Dict[str, Any]]:
    """Walk tests/*.py, collect `from tools.xxx import name` statements,
    verify the target file exists and ``name`` resolves. A name is
    considered valid when any of the following hold:

      * It is defined at module-level in ``<mod>.py`` (top-level def/
        class/Assign/AnnAssign)
      * It is re-exported by an ``Import`` / ``ImportFrom`` statement
        in ``<pkg>/__init__.py`` (very common — ``from .sub import X``)
      * It is itself a **submodule** on disk — i.e. ``<pkg>/<name>.py``
        or ``<pkg>/<name>/__init__.py`` exists. Python resolves
        submodule imports via filesystem lookup, so ``from tools.awareness
        import gap_detector`` works even when ``gap_detector`` is not
        listed in ``tools/awareness/__init__.py``.

    AST-based; skips dynamic imports.
    """
    findings: List[Dict[str, Any]] = []
    tests_dir = BASE_DIR / "tests"
    if not tests_dir.exists():
        return findings

    def _collect_names(body_nodes) -> Set[str]:
        """Recursively collect top-level bindings from a list of AST
        statements. Descends into ``try/except``, ``if``, ``with``,
        ``for`` and ``while`` compound statements because conditional
        top-level definitions are extremely common in this codebase —
        for example::

            try:
                from tools.audit.audit_logger import log_event as audit_log_event
            except ImportError:
                def audit_log_event(**kwargs):
                    pass

        Both branches of the try/except bind ``audit_log_event`` at
        module level, but a naive walk of ``tree.body`` would only see
        the ``ast.Try`` node and miss the inner bindings entirely.
        """
        names: Set[str] = set()
        for node in body_nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
                    elif isinstance(t, (ast.Tuple, ast.List)):
                        # Tuple unpacking: `A, B = foo()` binds A and B.
                        for elt in t.elts:
                            if isinstance(elt, ast.Name):
                                names.add(elt.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
            elif isinstance(node, ast.ImportFrom):
                # ``from .foo import bar`` re-exports ``bar`` from this module.
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                # ``import foo`` binds ``foo``; ``import foo as bar``
                # binds ``bar``; ``import foo.bar`` binds ``foo``.
                for alias in node.names:
                    if alias.asname:
                        names.add(alias.asname)
                    else:
                        names.add(alias.name.split(".")[0])
            elif isinstance(node, ast.Try):
                names.update(_collect_names(node.body))
                for handler in node.handlers:
                    names.update(_collect_names(handler.body))
                names.update(_collect_names(node.orelse))
                names.update(_collect_names(node.finalbody))
            elif isinstance(node, ast.If):
                names.update(_collect_names(node.body))
                names.update(_collect_names(node.orelse))
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                names.update(_collect_names(node.body))
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                names.update(_collect_names(node.body))
                names.update(_collect_names(node.orelse))
        return names

    def _load_module_names(mod_path: str) -> Optional[Set[str]]:
        """Return the set of top-level names defined by (or re-exported
        from) a module, recursing into compound statements. ``None``
        means the module can't be located on disk."""
        parts = mod_path.split(".")
        py_path = BASE_DIR / Path(*parts).with_suffix(".py")
        pkg_init = BASE_DIR / Path(*parts) / "__init__.py"
        target = py_path if py_path.exists() else (pkg_init if pkg_init.exists() else None)
        if not target:
            return None
        try:
            tree = ast.parse(_read_text(target))
        except SyntaxError:
            return None
        return _collect_names(tree.body)

    module_cache: Dict[str, Optional[Set[str]]] = {}

    for py in _walk_py(tests_dir):
        src = _read_text(py)
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            mod = node.module or ""
            if not mod.startswith("tools."):
                continue
            # Resolve once per module
            if mod not in module_cache:
                module_cache[mod] = _load_module_names(mod)
            defined = module_cache[mod]
            if defined is None:
                # Module can't be located — import will fail
                for alias in node.names:
                    findings.append(
                        _finding(
                            "broken_test_reference",
                            f"{_rel(py)}:{alias.name}",
                            {
                                "test_file": _rel(py),
                                "module": mod,
                                "symbol": alias.name,
                                "error": "module not found on disk",
                            },
                        )
                    )
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                if alias.name in defined:
                    continue
                # Python submodule fallback: ``from pkg import sub`` is
                # valid when ``pkg/sub.py`` or ``pkg/sub/__init__.py``
                # exists, even if ``sub`` is not listed in ``pkg/__init__.py``.
                if _submodule_exists(mod, alias.name):
                    continue
                findings.append(
                    _finding(
                        "broken_test_reference",
                        f"{_rel(py)}:{alias.name}",
                        {
                            "test_file": _rel(py),
                            "module": mod,
                            "symbol": alias.name,
                            "error": "symbol not exported from module",
                        },
                    )
                )
    return findings[:25]  # cap


def _rule_route_no_e2e() -> List[Dict[str, Any]]:
    """Flag dashboard routes that no tests/e2e_*.py file references
    by URL literal. Uses regex substring matching — not perfect but
    catches most coverage gaps.
    """
    findings: List[Dict[str, Any]] = []
    app_py = BASE_DIR / "tools" / "dashboard" / "app.py"
    if not app_py.exists():
        return findings

    app_src = _read_text(app_py)
    routes: List[str] = []
    for m in re.finditer(r'@app\.route\(\s*[\'"]([^\'"]+)[\'"]', app_src):
        r = m.group(1).split("?", 1)[0]
        if r.startswith("/api/"):
            continue  # api routes aren't usually covered by e2e directly
        # Strip parameter placeholders for matching
        r_clean = re.sub(r"<[^>]+>", "", r).rstrip("/")
        if r_clean and r_clean not in routes:
            routes.append(r_clean)

    # Concatenate all e2e test sources so a single scan matches
    tests_dir = BASE_DIR / "tests"
    e2e_blob_parts: List[str] = []
    if tests_dir.exists():
        for e2e in tests_dir.glob("e2e_*.py"):
            e2e_blob_parts.append(_read_text(e2e))
    # Also include tools/testing/e2e_*.py if present
    tt = BASE_DIR / "tools" / "testing"
    if tt.exists():
        for e2e in tt.glob("e2e_*.py"):
            e2e_blob_parts.append(_read_text(e2e))
    e2e_blob = "\n".join(e2e_blob_parts)

    for route in routes:
        # A route is "covered" if any e2e file mentions it as a string
        if route in e2e_blob:
            continue
        findings.append(
            _finding(
                "route_no_e2e",
                route,
                {"route": route, "total_routes_checked": len(routes)},
            )
        )
    return findings[:20]  # cap noise


def _rule_empty_mcp_server() -> List[Dict[str, Any]]:
    """Each tools/mcp/*_server.py should have at least one @mcp_tool
    decorator or register_tool() call. Flag those with zero.
    """
    findings: List[Dict[str, Any]] = []
    mcp_dir = BASE_DIR / "tools" / "mcp"
    if not mcp_dir.exists():
        return findings

    for server in sorted(mcp_dir.glob("*_server.py")):
        src = _read_text(server)
        tool_count = len(re.findall(r"@mcp_tool\b|register_tool\(", src))
        if tool_count > 0:
            continue
        findings.append(
            _finding(
                "empty_mcp_server",
                _rel(server),
                {"file_path": _rel(server), "tool_count": tool_count},
            )
        )
    return findings


def _rule_stale_code() -> List[Dict[str, Any]]:
    """Default-OFF. Flag .py files > 30 days unmodified with no
    referencing test file. Noisy — opt-in only.
    """
    import time as _time
    findings: List[Dict[str, Any]] = []
    threshold = _time.time() - (30 * 86400)
    tests_blob = ""
    tests_dir = BASE_DIR / "tests"
    if tests_dir.exists():
        tests_blob = "\n".join(
            _read_text(p) for p in tests_dir.rglob("*.py")
        )
    for py in _walk_py(BASE_DIR / "tools"):
        try:
            mtime = py.stat().st_mtime
        except OSError:
            continue
        if mtime > threshold:
            continue
        rel = _rel(py)
        # Check if any test file mentions it
        if rel in tests_blob or py.stem in tests_blob:
            continue
        findings.append(
            _finding(
                "stale_code",
                rel,
                {"file_path": rel, "mtime": mtime},
            )
        )
    return findings[:10]


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------


_RULE_FUNCS = {
    "route_not_listed": _rule_route_not_listed,
    "tool_not_in_manifest": _rule_tool_not_in_manifest,
    "skill_references_missing_goal": _rule_skill_references_missing_goal,
    "orphan_db_table": _rule_orphan_db_table,
    "broken_test_reference": _rule_broken_test_reference,
    "route_no_e2e": _rule_route_no_e2e,
    "empty_mcp_server": _rule_empty_mcp_server,
    "stale_code": _rule_stale_code,
}


# ---------------------------------------------------------------------------
# Oracle prediction writer (reuses the Phase 2 pattern)
# ---------------------------------------------------------------------------


def _prediction_id(rule_id: str, subject: str) -> str:
    """Stable, content-addressable ID — same rule+subject always maps to
    the same prediction row. Timestamp intentionally excluded so
    repeated gap_detector runs are idempotent and don't mint fresh IDs
    that bypass the suggested_card_writer dedup."""
    h = hashlib.sha256(f"gap::{rule_id}::{subject}".encode("utf-8")).hexdigest()
    return f"op-gap-{h[:16]}"


# Outcomes that mean "this prediction is still open": card_writer may yet
# promote it. Anything else (promoted:*, dismissed*) is a closed row we must not
# touch — it either already minted a kanban card or the operator dismissed it.
_OPEN_OUTCOMES = frozenset({"", "pending"})


def _existing_prediction_state(conn: Any, pred_id: str) -> Optional[Dict[str, Any]]:
    """Return the current (outcome, confidence, severity) for this stable ID, or
    None when no row exists yet. Used to decide insert vs. refresh vs. skip."""
    try:
        row = conn.execute(
            "SELECT outcome, confidence, severity FROM oracle_predictions "
            "WHERE id = %s LIMIT 1",
            (pred_id,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    if hasattr(row, "keys"):
        return dict(row)
    return {"outcome": row[0], "confidence": row[1], "severity": row[2]}


def _write_gap_prediction(
    conn: Any, finding: Dict[str, Any], rule_config: Dict[str, Any]
) -> Optional[str]:
    pred_id = _prediction_id(finding["rule_id"], finding["subject"])
    new_confidence = float(rule_config["confidence"])
    new_severity = rule_config["severity"]

    # Idempotency + tombstone guard. The ID is content-addressable on
    # (rule, subject) with the timestamp excluded, so repeated cycles reuse the
    # same row instead of minting fresh IDs (which caused kanban card churn).
    #
    # But confidence and severity are per-rule CONSTANTS read from
    # awareness_config.yaml at write time, and the old guard skipped the row
    # entirely whenever it already existed. So editing a rule's confidence in the
    # config never reached rows already written: a subject first seen at 0.50
    # stayed pinned at 0.50 forever, and raising the rule to 0.90 would never
    # promote it — the stale value silently suppressed the finding. Refresh
    # still-open rows in place so the persisted confidence tracks the config.
    state = _existing_prediction_state(conn, pred_id)
    if state is not None:
        outcome = (state.get("outcome") or "").strip().lower()
        if outcome not in _OPEN_OUTCOMES:
            # promoted:* / dismissed* — a card exists or the operator ruled.
            # Never rewrite: that is the churn/override the guard exists to stop.
            return pred_id
        stored_conf = state.get("confidence")
        stored_sev = state.get("severity")
        conf_changed = stored_conf is None or abs(float(stored_conf) - new_confidence) > 1e-6
        sev_changed = stored_sev != new_severity
        if not (conf_changed or sev_changed):
            return pred_id  # nothing to do — same constants as last cycle
        text = (
            f"Gap detected: {finding['rule_id']} on {finding['subject']}. "
            f"{rule_config.get('description', '')}"
        )
        try:
            # created_at is intentionally NOT touched — refreshing the constant
            # must not re-age the finding or reset its horizon.
            conn.execute(
                "UPDATE oracle_predictions "
                "SET confidence = %s, severity = %s, prediction_text = %s "
                "WHERE id = %s",
                (new_confidence, new_severity, text, pred_id),
            )
            conn.commit()
        except Exception as exc:
            LOG.warning("gap prediction refresh failed: %s", exc)
            try:
                conn.rollback()
            except Exception:
                pass
        return pred_id

    now = datetime.now(timezone.utc).isoformat()
    text = (
        f"Gap detected: {finding['rule_id']} on {finding['subject']}. "
        f"{rule_config.get('description', '')}"
    )
    try:
        conn.execute(
            "INSERT INTO oracle_predictions "
            "(id, lens_id, lens_name, prediction_text, confidence, "
            " created_at, subject_type, subject_id, prediction_type, "
            " severity, horizon_days, evidence_json, classification) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                pred_id,
                "internal_awareness",
                LENS_NAME,
                text,
                float(rule_config["confidence"]),
                now,
                "gap",
                finding["subject"],
                f"gap::{finding['rule_id']}",
                rule_config["severity"],
                0,
                json.dumps(finding["evidence"], ensure_ascii=False),
                "CUI // SP-CTI",
            ),
        )
        conn.commit()
        return pred_id
    except Exception as exc:
        LOG.warning("gap prediction write failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None


# ---------------------------------------------------------------------------
# Main detect loop
# ---------------------------------------------------------------------------


def detect(
    rules: Optional[List[str]] = None,
    dry_run: bool = False,
    include_disabled: bool = False,
) -> Dict[str, Any]:
    """Run every enabled rule (or a subset) and persist findings to
    oracle_predictions. Returns summary counts per rule.
    """
    enabled_rules: List[str] = []
    if rules:
        enabled_rules = [r for r in rules if r in _RULE_FUNCS]
    else:
        for rid, cfg in GAP_RULES.items():
            if cfg["enabled"] or include_disabled:
                enabled_rules.append(rid)

    conn = None
    if not dry_run and get_connection is not None:
        conn = get_connection()

    results: Dict[str, Dict[str, Any]] = {}
    all_findings: List[Dict[str, Any]] = []
    total_written = 0

    for rule_id in enabled_rules:
        func = _RULE_FUNCS.get(rule_id)
        if not func:
            continue
        config = GAP_RULES[rule_id]
        try:
            findings = func()
        except Exception as exc:
            LOG.error("rule %s raised: %s", rule_id, exc)
            results[rule_id] = {"error": str(exc)[:200]}
            continue

        written = 0
        if not dry_run and conn is not None:
            for f in findings:
                if _write_gap_prediction(conn, f, config) is not None:
                    written += 1

        results[rule_id] = {
            "findings": len(findings),
            "written": written,
            "confidence": config["confidence"],
            "severity": config["severity"],
        }
        all_findings.extend(findings)
        total_written += written

    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

    return {
        "total_findings": len(all_findings),
        "total_written": total_written,
        "dry_run": dry_run,
        "rules_run": list(results.keys()),
        "by_rule": results,
        "findings_sample": all_findings[:10],
    }


def get_stats() -> Dict[str, Any]:
    if get_connection is None:
        return {"error": "get_connection unavailable"}
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM oracle_predictions "
            "WHERE lens_name = %s AND subject_type = %s",
            (LENS_NAME, "gap"),
        ).fetchone()
        total = dict(row).get("cnt", 0) if row else 0
        rows = conn.execute(
            "SELECT prediction_type, COUNT(*) AS cnt FROM oracle_predictions "
            "WHERE lens_name = %s AND subject_type = %s "
            "GROUP BY prediction_type ORDER BY cnt DESC",
            (LENS_NAME, "gap"),
        ).fetchall()
        by_type = {dict(r)["prediction_type"]: dict(r)["cnt"] for r in rows}
        return {
            "lens": LENS_NAME,
            "total_gap_predictions": total,
            "by_rule": by_type,
            "configured_rules": list(GAP_RULES.keys()),
            "enabled_by_default": [rid for rid, c in GAP_RULES.items() if c["enabled"]],
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gap_detector", description="Phase 3 — ICDEV gap detector"
    )
    parser.add_argument("--detect", action="store_true", help="Run gap detection")
    parser.add_argument("--rule", type=str, default=None, help="Run only this rule")
    parser.add_argument("--dry-run", action="store_true", help="Collect findings without writing")
    parser.add_argument("--include-disabled", action="store_true", help="Include default-off rules like stale_code")
    parser.add_argument("--stats", action="store_true", help="Show gap prediction stats")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.stats:
        result: Dict[str, Any] = get_stats()
    elif args.detect or args.dry_run:
        rules = [args.rule] if args.rule else None
        result = detect(
            rules=rules,
            dry_run=args.dry_run,
            include_disabled=args.include_disabled,
        )
    else:
        parser.print_help()
        return 1

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for k, v in result.items():
            if k == "findings_sample":
                print(f"  findings_sample: {len(v)} items")
            else:
                print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
