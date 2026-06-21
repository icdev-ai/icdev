# CUI // SP-CTI
"""AI Augmentation Canvas — Pattern Classifier (Semgrep + AST fallback).

Primary path: Semgrep CLI is invoked via subprocess; its JSON output is parsed
and mapped to the canonical result schema.

Fallback (air-gap / Semgrep unavailable): Python stdlib ast walker handles
Python source files only.

Public API:
    detect_patterns(target_path: str) -> list[dict]

Each result dict contains:
    pattern_type    — one of tools.ai_augmentation.constants.PATTERN_TYPES
    module_path     — path to the analyzed source file
    function_name   — enclosing function name, or '<unknown>'
    line_start      — first line of the matched pattern (1-based)
    line_end        — last line of the matched pattern (1-based)
    language        — source language identifier (e.g. 'python')
    pattern_detail  — dict with pattern-specific metadata
"""

from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import shutil
from typing import Any

import yaml

# ── Config ────────────────────────────────────────────────────────────────────

_CONFIG_PATH = pathlib.Path(__file__).parent.parent.parent / "args" / "aac_config.yaml"
_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent


def _load_config() -> dict[str, Any]:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}


_cfg = _load_config()
_PATTERN_MIN_DEPTH: int = int(_cfg.get("pattern_min_depth", 3))
_RULE_MIN_KEYS: int = int(_cfg.get("rule_min_keys", 10))

_semgrep_cfg: dict[str, Any] = _cfg.get("semgrep", {})
_SEMGREP_RULES_DIR: str = _semgrep_cfg.get(
    "rules_dir", "context/ai_augmentation/semgrep_rules"
)
_SEMGREP_TIMEOUT: int = int(_semgrep_cfg.get("timeout_seconds", 60))
_SEMGREP_METRICS: str = str(_semgrep_cfg.get("metrics", "off"))

# ── Language detection ────────────────────────────────────────────────────────

_EXT_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".java": "java",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
}


def _language_from_path(path: str) -> str:
    return _EXT_LANGUAGE.get(pathlib.Path(path).suffix.lower(), "unknown")


# ── Semgrep wrapper ───────────────────────────────────────────────────────────


def _detect_via_semgrep(target_path: str) -> list[dict] | None:
    """Run Semgrep CLI on target_path and return parsed results.

    Returns None if Semgrep is not installed or the run fails — signals the
    caller to use the AST fallback instead.
    """
    if not shutil.which("semgrep"):
        return None

    rules_path = _REPO_ROOT / _SEMGREP_RULES_DIR
    if not rules_path.exists():
        return None

    cmd = [
        "semgrep",
        "--json",
        "--config", str(rules_path),
        "--metrics", _SEMGREP_METRICS,
        target_path,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=_SEMGREP_TIMEOUT,
        )
    except (FileNotFoundError, PermissionError, OSError):
        return None
    except subprocess.TimeoutExpired:
        return None

    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        return None

    return _map_semgrep_results(data.get("results", []))


def _map_semgrep_results(hits: list[dict]) -> list[dict]:
    """Map raw Semgrep result objects to the canonical AAC pattern dict."""
    results: list[dict] = []
    for hit in hits:
        extra = hit.get("extra", {})
        metadata = extra.get("metadata", {})
        pattern_type = metadata.get("aac_pattern")
        if not pattern_type:
            continue

        path = hit.get("path", "")
        language = _language_from_path(path)

        # Prefer metavariable capture of the enclosing function ($FUNC),
        # then fall back to metadata.function_name, then '<unknown>'.
        metavars = extra.get("metavars", {})
        func_meta = metavars.get("$FUNC", {})
        function_name: str = (
            func_meta.get("abstract_content")
            or metadata.get("function_name")
            or "<unknown>"
        )

        pattern_detail: dict[str, Any] = {
            k: v for k, v in metadata.items() if k != "aac_pattern"
        }
        message = extra.get("message", "")
        if message:
            pattern_detail["message"] = message

        results.append({
            "pattern_type": pattern_type,
            "module_path": path,
            "function_name": function_name,
            "line_start": hit.get("start", {}).get("line", 0),
            "line_end": hit.get("end", {}).get("line", 0),
            "language": language,
            "pattern_detail": pattern_detail,
        })
    return results


# ── AST fallback (Python only) ────────────────────────────────────────────────

# Constants for AST detection

_RE_DETECT_FUNCS: frozenset[str] = frozenset(
    {"match", "search", "fullmatch", "compile", "findall", "finditer", "sub", "subn", "split"}
)

_CRON_CALL_ATTRS: frozenset[str] = frozenset(
    {"add_job", "scheduled_job", "every", "crontab", "on_after_configure"}
)
_CRON_CALL_NAMES: frozenset[str] = frozenset({"crontab", "schedule"})

_DB_INDICATORS: frozenset[str] = frozenset({
    "query", "execute", "filter", "get", "fetch", "select",
    "fetchone", "fetchall", "fetchmany", "find", "find_one", "find_all",
    "scalar", "scalars", "all", "first",
})
_RENDER_INDICATORS: frozenset[str] = frozenset({
    "render", "render_template", "render_string", "render_to_string",
    "get_template", "from_string",
})
_NOTIFY_INDICATORS: frozenset[str] = frozenset({
    "send", "sendmail", "send_message", "send_mail",
    "notify", "emit", "publish", "dispatch", "deliver",
})
_RENDER_METHODS: frozenset[str] = frozenset(
    {"render", "render_template", "render_string", "render_to_string"}
)
_RENDER_FUNCS: frozenset[str] = frozenset(
    {"render_template", "render_string", "render_to_string"}
)
_CRON_DEC_KEYWORDS: frozenset[str] = frozenset(
    {"task", "cron", "schedule", "periodic", "job", "beat"}
)
_THRESHOLD_OPS = (ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def _build_parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    parent_map: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_map[id(child)] = node
    return parent_map


def _build_scope_map(tree: ast.AST) -> dict[int, str]:
    scope_map: dict[int, str] = {}

    def _walk(node: ast.AST, scope: str) -> None:
        scope_map[id(node)] = scope
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _walk(child, child.name)
            else:
                _walk(child, scope)

    _walk(tree, "<module>")
    return scope_map


def _collect_re_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    re_module_aliases: set[str] = set()
    re_func_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "re":
                    re_module_aliases.add(alias.asname or "re")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "re":
                for alias in node.names:
                    if alias.name in _RE_DETECT_FUNCS:
                        re_func_names.add(alias.asname or alias.name)
    return re_module_aliases, re_func_names


def _if_depth_in_subtree(node: ast.AST) -> int:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return 0
    if isinstance(node, ast.If):
        children = node.body + node.orelse
        child_max = max((_if_depth_in_subtree(c) for c in children), default=0)
        return 1 + child_max
    child_max = max(
        (_if_depth_in_subtree(c) for c in ast.iter_child_nodes(node)), default=0
    )
    return child_max


def _extract_decorator_name(dec: ast.expr) -> str | None:
    if isinstance(dec, ast.Name):
        return dec.id
    if isinstance(dec, ast.Attribute):
        base = _extract_decorator_name(dec.value)
        return f"{base}.{dec.attr}" if base else dec.attr
    if isinstance(dec, ast.Call):
        return _extract_decorator_name(dec.func)
    return None


def _calls_in_func(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.Call]:
    result: list[ast.Call] = []

    def _walk(node: ast.AST) -> None:
        if node is not func_node and isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            return
        if isinstance(node, ast.Call):
            result.append(node)
        for child in ast.iter_child_nodes(node):
            _walk(child)

    _walk(func_node)
    return result


def _call_attr(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _ast_detect_file(file_path: str) -> list[dict]:
    """Run all 8 AST pattern detectors on a single Python file."""
    source_text = pathlib.Path(file_path).read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source_text, filename=file_path)
    except SyntaxError:
        return []

    parent_map = _build_parent_map(tree)
    scope_map = _build_scope_map(tree)
    re_aliases, re_funcs = _collect_re_names(tree)

    raw: list[dict] = []
    raw.extend(_detect_nested_conditionals(file_path, tree, parent_map, scope_map))
    raw.extend(_detect_regex_user_input(file_path, tree, scope_map, re_aliases, re_funcs))
    raw.extend(_detect_string_template_rendering(file_path, tree, scope_map))
    raw.extend(_detect_scheduled_cron(file_path, tree, scope_map))
    raw.extend(_detect_hardcoded_threshold(file_path, tree, scope_map))
    raw.extend(_detect_db_render_notify_chain(file_path, tree, scope_map))
    raw.extend(_detect_keyword_list_search(file_path, tree, scope_map))
    raw.extend(_detect_large_rule_table(file_path, tree, scope_map))

    # Inject language field for consistency with Semgrep output schema.
    for hit in raw:
        hit.setdefault("language", "python")
    return raw


def _detect_via_ast_fallback(target_path: str) -> list[dict]:
    """AST-based fallback for Python and Java files when Semgrep is unavailable."""
    p = pathlib.Path(target_path)
    if p.is_file():
        suffix = p.suffix.lower()
        if suffix == ".py":
            return _ast_detect_file(target_path)
        if suffix == ".java":
            return _java_detect_file(target_path)
        return []
    results: list[dict] = []
    for py_file in sorted(p.rglob("*.py")):
        results.extend(_ast_detect_file(str(py_file)))
    for java_file in sorted(p.rglob("*.java")):
        results.extend(_java_detect_file(str(java_file)))
    return results


# ── AST pattern detectors ─────────────────────────────────────────────────────


def _detect_nested_conditionals(
    file_path: str,
    tree: ast.AST,
    parent_map: dict[int, ast.AST],
    scope_map: dict[int, str],
) -> list[dict]:
    results: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if isinstance(parent_map.get(id(node)), ast.If):
            continue
        depth = _if_depth_in_subtree(node)
        if depth >= _PATTERN_MIN_DEPTH:
            results.append({
                "pattern_type": "nested_conditionals",
                "module_path": file_path,
                "function_name": scope_map.get(id(node), "<module>"),
                "line_start": node.lineno,
                "line_end": node.end_lineno,
                "pattern_detail": {"max_depth": depth},
            })
    return results


def _detect_regex_user_input(
    file_path: str,
    tree: ast.AST,
    scope_map: dict[int, str],
    re_module_aliases: set[str],
    re_func_names: set[str],
) -> list[dict]:
    results: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        matched: str | None = None
        if isinstance(func, ast.Attribute):
            if func.attr in _RE_DETECT_FUNCS and isinstance(func.value, ast.Name):
                if func.value.id in re_module_aliases:
                    matched = f"{func.value.id}.{func.attr}"
        elif isinstance(func, ast.Name) and func.id in re_func_names:
            matched = func.id
        if matched:
            results.append({
                "pattern_type": "regex_user_input",
                "module_path": file_path,
                "function_name": scope_map.get(id(node), "<module>"),
                "line_start": node.lineno,
                "line_end": node.end_lineno,
                "pattern_detail": {"call": matched},
            })
    return results


def _detect_string_template_rendering(
    file_path: str,
    tree: ast.AST,
    scope_map: dict[int, str],
) -> list[dict]:
    results: list[dict] = []
    for node in ast.walk(tree):
        kind: str | None = None
        detail: dict[str, Any] = {}
        if isinstance(node, ast.JoinedStr):
            kind = "f_string"
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr == "format":
                    kind = "str_format"
                    detail = {"method": ".format()"}
                elif func.attr in _RENDER_METHODS:
                    kind = "template_render"
                    detail = {"method": f".{func.attr}()"}
            elif isinstance(func, ast.Name) and func.id in _RENDER_FUNCS:
                kind = "template_render"
                detail = {"method": f"{func.id}()"}
        if kind:
            results.append({
                "pattern_type": "string_template_rendering",
                "module_path": file_path,
                "function_name": scope_map.get(id(node), "<module>"),
                "line_start": node.lineno,
                "line_end": node.end_lineno,
                "pattern_detail": {"kind": kind, **detail},
            })
    return results


def _detect_scheduled_cron(
    file_path: str,
    tree: ast.AST,
    scope_map: dict[int, str],
) -> list[dict]:
    results: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                dec_name = _extract_decorator_name(dec)
                if dec_name and any(k in dec_name.lower() for k in _CRON_DEC_KEYWORDS):
                    results.append({
                        "pattern_type": "scheduled_cron",
                        "module_path": file_path,
                        "function_name": node.name,
                        "line_start": node.lineno,
                        "line_end": node.end_lineno,
                        "pattern_detail": {"kind": "decorator", "decorator": dec_name},
                    })
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _CRON_CALL_ATTRS:
                results.append({
                    "pattern_type": "scheduled_cron",
                    "module_path": file_path,
                    "function_name": scope_map.get(id(node), "<module>"),
                    "line_start": node.lineno,
                    "line_end": node.end_lineno,
                    "pattern_detail": {"kind": "call", "method": func.attr},
                })
            elif isinstance(func, ast.Name) and func.id in _CRON_CALL_NAMES:
                results.append({
                    "pattern_type": "scheduled_cron",
                    "module_path": file_path,
                    "function_name": scope_map.get(id(node), "<module>"),
                    "line_start": node.lineno,
                    "line_end": node.end_lineno,
                    "pattern_detail": {"kind": "call", "func": func.id},
                })
    return results


def _detect_hardcoded_threshold(
    file_path: str,
    tree: ast.AST,
    scope_map: dict[int, str],
) -> list[dict]:
    results: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            if not any(isinstance(op, _THRESHOLD_OPS) for op in node.ops):
                continue
            comparators = [node.left, *node.comparators]
            numeric_consts = [
                c.value
                for c in comparators
                if isinstance(c, ast.Constant) and isinstance(c.value, (int, float))
            ]
            if numeric_consts:
                results.append({
                    "pattern_type": "hardcoded_threshold",
                    "module_path": file_path,
                    "function_name": scope_map.get(id(node), "<module>"),
                    "line_start": node.lineno,
                    "line_end": node.end_lineno,
                    "pattern_detail": {"kind": "compare", "constants": numeric_consts},
                })
        elif isinstance(node, ast.BinOp):
            left_const = (
                isinstance(node.left, ast.Constant)
                and isinstance(node.left.value, (int, float))
            )
            right_const = (
                isinstance(node.right, ast.Constant)
                and isinstance(node.right.value, (int, float))
            )
            if left_const or right_const:
                const_val = node.left.value if left_const else node.right.value  # type: ignore[union-attr]
                results.append({
                    "pattern_type": "hardcoded_threshold",
                    "module_path": file_path,
                    "function_name": scope_map.get(id(node), "<module>"),
                    "line_start": node.lineno,
                    "line_end": node.end_lineno,
                    "pattern_detail": {
                        "kind": "binop",
                        "op": type(node.op).__name__,
                        "constants": [const_val],
                    },
                })
    return results


def _detect_db_render_notify_chain(
    file_path: str,
    tree: ast.AST,
    scope_map: dict[int, str],
) -> list[dict]:
    results: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        call_names: set[str] = {
            name
            for c in _calls_in_func(node)
            if (name := _call_attr(c)) is not None
        }
        db_hits = call_names & _DB_INDICATORS
        render_hits = call_names & _RENDER_INDICATORS
        notify_hits = call_names & _NOTIFY_INDICATORS
        if db_hits and render_hits and notify_hits:
            results.append({
                "pattern_type": "db_render_notify_chain",
                "module_path": file_path,
                "function_name": node.name,
                "line_start": node.lineno,
                "line_end": node.end_lineno,
                "pattern_detail": {
                    "matched_calls": {
                        "db": sorted(db_hits),
                        "render": sorted(render_hits),
                        "notify": sorted(notify_hits),
                    }
                },
            })
    return results


def _detect_keyword_list_search(
    file_path: str,
    tree: ast.AST,
    scope_map: dict[int, str],
) -> list[dict]:
    results: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for op, comp in zip(node.ops, node.comparators):
            if not isinstance(op, ast.In):
                continue
            str_count = 0
            if isinstance(comp, (ast.List, ast.Set, ast.Tuple)):
                str_count = sum(
                    1
                    for elt in comp.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                )
            elif isinstance(comp, ast.Dict):
                str_count = sum(
                    1
                    for k in comp.keys
                    if k is not None
                    and isinstance(k, ast.Constant)
                    and isinstance(k.value, str)
                )
            if str_count >= 3:
                results.append({
                    "pattern_type": "keyword_list_search",
                    "module_path": file_path,
                    "function_name": scope_map.get(id(node), "<module>"),
                    "line_start": node.lineno,
                    "line_end": node.end_lineno,
                    "pattern_detail": {
                        "container_type": type(comp).__name__,
                        "string_count": str_count,
                    },
                })
    return results


def _detect_large_rule_table(
    file_path: str,
    tree: ast.AST,
    scope_map: dict[int, str],
) -> list[dict]:
    results: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        key_count = sum(1 for k in node.keys if k is not None)
        if key_count >= _RULE_MIN_KEYS:
            results.append({
                "pattern_type": "large_rule_table",
                "module_path": file_path,
                "function_name": scope_map.get(id(node), "<module>"),
                "line_start": node.lineno,
                "line_end": node.end_lineno,
                "pattern_detail": {"key_count": key_count},
            })
    return results


# ── Java AST detection (javalang) ─────────────────────────────────────────────

_JAVA_COMPARISON_OPS: frozenset[str] = frozenset({"<", ">", "<=", ">="})

_JAVA_REGEX_METHODS: frozenset[str] = frozenset({"compile", "matches", "match", "find"})
_JAVA_REGEX_QUALIFIERS: frozenset[str] = frozenset({"Pattern", "Matcher"})

_JAVA_TEMPLATE_PAIRS: frozenset[tuple[str, str]] = frozenset({
    ("String", "format"),
    ("MessageFormat", "format"),
    ("String", "formatted"),
})
_JAVA_TEMPLATE_CLASSES: frozenset[str] = frozenset({
    "VelocityEngine", "Template", "VelocityContext",
})
_JAVA_TEMPLATE_METHOD_NAMES: frozenset[str] = frozenset({
    "evaluate", "merge", "getTemplate",
})

_JAVA_CRON_ANNOTATIONS: frozenset[str] = frozenset({
    "Scheduled", "Cron", "Job", "ScheduledTask", "EnableScheduling",
})
_JAVA_CRON_CLASSES: frozenset[str] = frozenset({
    "CronTrigger", "PeriodicTrigger", "ScheduledThreadPoolExecutor",
})
_JAVA_CRON_METHODS: frozenset[str] = frozenset({
    "scheduleAtFixedRate", "scheduleWithFixedDelay",
})

_JAVA_DB_METHODS: frozenset[str] = frozenset({
    "executeQuery", "execute", "executeUpdate", "prepareStatement", "prepareCall",
    "createQuery", "createNamedQuery", "find", "persist", "merge", "remove",
    "findById", "findAll", "save", "saveAll", "delete",
})
_JAVA_RENDER_METHODS: frozenset[str] = frozenset({
    "evaluate", "merge", "format", "getTemplate", "render",
})
_JAVA_NOTIFY_METHODS: frozenset[str] = frozenset({
    "send", "sendMail", "sendMessage", "sendEmail",
    "publish", "dispatch", "emit", "deliver",
})

_JAVA_MAP_CLASSES: frozenset[str] = frozenset({
    "HashMap", "LinkedHashMap", "TreeMap", "Hashtable", "ConcurrentHashMap",
})


def _java_enclosing_method(path: list) -> str:
    try:
        import javalang
    except ImportError:
        return "<unknown>"
    for ancestor in reversed(path):
        if isinstance(ancestor, (javalang.tree.MethodDeclaration,
                                  javalang.tree.ConstructorDeclaration)):
            return ancestor.name
    return "<unknown>"


def _java_if_subtree_depth(root_node: Any, jl: Any) -> int:
    """Return max depth of IfStatement nesting rooted at root_node (inclusive).

    node.filter() includes root_node itself (empty path) and all descendants.
    Descendants' paths contain root_node as an ancestor, so depth equals
    if_ancestor_count_in_path + 1 (for the descendant itself).
    """
    max_depth = 1  # root itself
    for nested_path, nested_node in root_node.filter(jl.tree.IfStatement):
        if nested_node is root_node:
            continue
        if_ancestors = sum(1 for a in nested_path if isinstance(a, jl.tree.IfStatement))
        max_depth = max(max_depth, if_ancestors + 1)
    return max_depth


def _java_detect_nested_conditionals(file_path: str, tree: Any, jl: Any) -> list[dict]:
    results: list[dict] = []
    for path, node in tree.filter(jl.tree.IfStatement):
        if any(isinstance(a, jl.tree.IfStatement) for a in path):
            continue
        depth = _java_if_subtree_depth(node, jl)
        if depth >= _PATTERN_MIN_DEPTH:
            line: int = node.position.line if node.position else 0
            results.append({
                "pattern_type": "nested_conditionals",
                "module_path": file_path,
                "function_name": _java_enclosing_method(path),
                "line_start": line,
                "line_end": line,
                "pattern_detail": {"max_depth": depth},
            })
    return results


def _java_detect_regex_user_input(file_path: str, tree: Any, jl: Any) -> list[dict]:
    results: list[dict] = []
    for path, node in tree.filter(jl.tree.MethodInvocation):
        qualifier: str = getattr(node, "qualifier", "") or ""
        member: str = getattr(node, "member", "") or ""
        if member not in _JAVA_REGEX_METHODS:
            continue
        if qualifier in _JAVA_REGEX_QUALIFIERS or member == "matches":
            line = node.position.line if node.position else 0
            call = f"{qualifier}.{member}" if qualifier else member
            results.append({
                "pattern_type": "regex_user_input",
                "module_path": file_path,
                "function_name": _java_enclosing_method(path),
                "line_start": line,
                "line_end": line,
                "pattern_detail": {"call": call},
            })
    return results


def _java_detect_string_template_rendering(file_path: str, tree: Any, jl: Any) -> list[dict]:
    results: list[dict] = []
    for path, node in tree.filter(jl.tree.MethodInvocation):
        qualifier = getattr(node, "qualifier", "") or ""
        member = getattr(node, "member", "") or ""
        matched: str | None = None
        if (qualifier, member) in _JAVA_TEMPLATE_PAIRS:
            matched = f"{qualifier}.{member}"
        elif qualifier in _JAVA_TEMPLATE_CLASSES or member in _JAVA_TEMPLATE_METHOD_NAMES:
            matched = f"{qualifier}.{member}" if qualifier else member
        if matched:
            line = node.position.line if node.position else 0
            results.append({
                "pattern_type": "string_template_rendering",
                "module_path": file_path,
                "function_name": _java_enclosing_method(path),
                "line_start": line,
                "line_end": line,
                "pattern_detail": {"kind": "java_template", "call": matched},
            })
    return results


def _java_leaf_type_name(ref_type: Any) -> str:
    """Return the leaf class name from a possibly-qualified ReferenceType."""
    name = getattr(ref_type, "name", "") or ""
    sub = getattr(ref_type, "sub_type", None)
    while sub is not None:
        name = getattr(sub, "name", "") or name
        sub = getattr(sub, "sub_type", None)
    return name


def _java_detect_scheduled_cron(file_path: str, tree: Any, jl: Any) -> list[dict]:
    results: list[dict] = []
    for path, node in tree.filter(jl.tree.MethodDeclaration):
        for ann in node.annotations or []:
            if ann.name in _JAVA_CRON_ANNOTATIONS:
                line = node.position.line if node.position else 0
                results.append({
                    "pattern_type": "scheduled_cron",
                    "module_path": file_path,
                    "function_name": node.name,
                    "line_start": line,
                    "line_end": line,
                    "pattern_detail": {"kind": "annotation", "annotation": f"@{ann.name}"},
                })
    for path, node in tree.filter(jl.tree.ClassCreator):
        type_name: str = _java_leaf_type_name(getattr(node, "type", None))
        if type_name in _JAVA_CRON_CLASSES:
            line = node.position.line if node.position else 0
            results.append({
                "pattern_type": "scheduled_cron",
                "module_path": file_path,
                "function_name": _java_enclosing_method(path),
                "line_start": line,
                "line_end": line,
                "pattern_detail": {"kind": "class_creation", "class": type_name},
            })
    for path, node in tree.filter(jl.tree.MethodInvocation):
        member = getattr(node, "member", "") or ""
        if member in _JAVA_CRON_METHODS:
            line = node.position.line if node.position else 0
            results.append({
                "pattern_type": "scheduled_cron",
                "module_path": file_path,
                "function_name": _java_enclosing_method(path),
                "line_start": line,
                "line_end": line,
                "pattern_detail": {"kind": "call", "method": member},
            })
    return results


def _java_detect_hardcoded_threshold(file_path: str, tree: Any, jl: Any) -> list[dict]:
    results: list[dict] = []
    for path, node in tree.filter(jl.tree.BinaryOperation):
        if node.operator not in _JAVA_COMPARISON_OPS:
            continue
        literals: list[str] = []
        if isinstance(node.operandl, jl.tree.Literal):
            literals.append(node.operandl.value)
        if isinstance(node.operandr, jl.tree.Literal):
            literals.append(node.operandr.value)
        if literals:
            line = node.position.line if node.position else 0
            results.append({
                "pattern_type": "hardcoded_threshold",
                "module_path": file_path,
                "function_name": _java_enclosing_method(path),
                "line_start": line,
                "line_end": line,
                "pattern_detail": {"kind": "compare", "operator": node.operator, "constants": literals},
            })
    return results


def _java_detect_db_render_notify_chain(file_path: str, tree: Any, jl: Any) -> list[dict]:
    results: list[dict] = []
    for _, method_node in tree.filter(jl.tree.MethodDeclaration):
        method_calls: set[str] = {
            getattr(inv, "member", "") or ""
            for _, inv in method_node.filter(jl.tree.MethodInvocation)
        }
        db_hits = method_calls & _JAVA_DB_METHODS
        render_hits = method_calls & _JAVA_RENDER_METHODS
        notify_hits = method_calls & _JAVA_NOTIFY_METHODS
        if db_hits and render_hits and notify_hits:
            line = method_node.position.line if method_node.position else 0
            results.append({
                "pattern_type": "db_render_notify_chain",
                "module_path": file_path,
                "function_name": method_node.name,
                "line_start": line,
                "line_end": line,
                "pattern_detail": {
                    "matched_calls": {
                        "db": sorted(db_hits),
                        "render": sorted(render_hits),
                        "notify": sorted(notify_hits),
                    }
                },
            })
    return results


def _java_detect_keyword_list_search(file_path: str, tree: Any, jl: Any) -> list[dict]:
    results: list[dict] = []
    for path, node in tree.filter(jl.tree.MethodInvocation):
        member = getattr(node, "member", "") or ""
        if member == "contains":
            qualifier = getattr(node, "qualifier", "") or ""
            line = node.position.line if node.position else 0
            results.append({
                "pattern_type": "keyword_list_search",
                "module_path": file_path,
                "function_name": _java_enclosing_method(path),
                "line_start": line,
                "line_end": line,
                "pattern_detail": {"call": f"{qualifier}.{member}" if qualifier else member},
            })
    return results


def _java_detect_large_rule_table(file_path: str, tree: Any, jl: Any) -> list[dict]:
    results: list[dict] = []
    for path, node in tree.filter(jl.tree.MethodInvocation):
        qualifier = getattr(node, "qualifier", "") or ""
        member = getattr(node, "member", "") or ""
        args = getattr(node, "arguments", []) or []
        if qualifier in ("Map", "ImmutableMap") and member in ("of", "ofEntries"):
            entry_count = len(args) // 2 if member == "of" else len(args)
            if entry_count >= _RULE_MIN_KEYS:
                line = node.position.line if node.position else 0
                results.append({
                    "pattern_type": "large_rule_table",
                    "module_path": file_path,
                    "function_name": _java_enclosing_method(path),
                    "line_start": line,
                    "line_end": line,
                    "pattern_detail": {"kind": "map_of", "entry_count": entry_count},
                })
    for _, method_node in tree.filter(jl.tree.MethodDeclaration):
        has_map_init = any(
            _java_leaf_type_name(getattr(creator, "type", None)) in _JAVA_MAP_CLASSES
            for _, creator in method_node.filter(jl.tree.ClassCreator)
        )
        if not has_map_init:
            continue
        put_count = sum(
            1
            for _, inv in method_node.filter(jl.tree.MethodInvocation)
            if getattr(inv, "member", "") == "put"
        )
        if put_count >= _RULE_MIN_KEYS:
            line = method_node.position.line if method_node.position else 0
            results.append({
                "pattern_type": "large_rule_table",
                "module_path": file_path,
                "function_name": method_node.name,
                "line_start": line,
                "line_end": line,
                "pattern_detail": {"kind": "map_put_sequence", "put_count": put_count},
            })
    return results


def _java_detect_file(file_path: str) -> list[dict]:
    """Run all 8 pattern detectors on a single Java file using javalang."""
    try:
        import javalang as jl
    except ImportError:
        return []
    source_text = pathlib.Path(file_path).read_text(encoding="utf-8", errors="replace")
    try:
        tree = jl.parse.parse(source_text)
    except jl.parser.JavaSyntaxError:
        return []
    except Exception:
        return []
    results: list[dict] = []
    results.extend(_java_detect_nested_conditionals(file_path, tree, jl))
    results.extend(_java_detect_regex_user_input(file_path, tree, jl))
    results.extend(_java_detect_string_template_rendering(file_path, tree, jl))
    results.extend(_java_detect_scheduled_cron(file_path, tree, jl))
    results.extend(_java_detect_hardcoded_threshold(file_path, tree, jl))
    results.extend(_java_detect_db_render_notify_chain(file_path, tree, jl))
    results.extend(_java_detect_keyword_list_search(file_path, tree, jl))
    results.extend(_java_detect_large_rule_table(file_path, tree, jl))
    for hit in results:
        hit.setdefault("language", "java")
    return results


# ── Public API ────────────────────────────────────────────────────────────────


def detect_patterns(target_path: str) -> list[dict]:
    """Detect AI-augmentable patterns in a source file or directory tree.

    Tries Semgrep CLI first (multi-language, all 8 patterns via YAML rules).
    Falls back to Python stdlib ast when Semgrep is unavailable (air-gap).

    Args:
        target_path: Path to a source file or directory to analyze.

    Returns:
        List of pattern dicts with keys: pattern_type, module_path,
        function_name, line_start, line_end, language, pattern_detail.
        Returns [] on error or if no patterns are found.
    """
    semgrep_results = _detect_via_semgrep(target_path)
    if semgrep_results is not None:
        return semgrep_results
    return _detect_via_ast_fallback(target_path)
