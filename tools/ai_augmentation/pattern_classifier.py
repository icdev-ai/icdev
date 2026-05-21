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
import re
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
    """AST-based fallback for Python/C#/Rust files when Semgrep is unavailable."""
    p = pathlib.Path(target_path)
    if p.is_file():
        suffix = p.suffix.lower()
        if suffix == ".py":
            return _ast_detect_file(target_path)
        if suffix == ".cs":
            return _cs_detect_file(target_path)
        if suffix == ".rs":
            return _rs_detect_file(target_path)
        return []
    # Directory: walk recursively for .py, .cs, and .rs files.
    results: list[dict] = []
    for py_file in sorted(p.rglob("*.py")):
        results.extend(_ast_detect_file(str(py_file)))
    for cs_file in sorted(p.rglob("*.cs")):
        results.extend(_cs_detect_file(str(cs_file)))
    for rs_file in sorted(p.rglob("*.rs")):
        results.extend(_rs_detect_file(str(rs_file)))
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


# ── C# tree-sitter / regex fallback ──────────────────────────────────────────

_CS_METHOD_NODE_TYPES: frozenset[str] = frozenset({
    "method_declaration",
    "constructor_declaration",
    "local_function_statement",
    "lambda_expression",
})

_CS_REGEX_METHODS: frozenset[str] = frozenset({
    "Match", "IsMatch", "Replace", "Split", "Matches", "Escape",
})
_CS_CRON_BASES: frozenset[str] = frozenset({"IHostedService", "BackgroundService"})
_CS_CRON_INVOC_OBJS: frozenset[str] = frozenset({"RecurringJob", "BackgroundJob"})
_CS_DB_CALL_NAMES: frozenset[str] = frozenset({
    "ToList", "ToListAsync", "FirstOrDefault", "FirstOrDefaultAsync",
    "Where", "Select", "Find", "FindAsync", "Single", "SingleOrDefault",
    "SingleOrDefaultAsync", "FromSqlRaw", "SaveChanges", "SaveChangesAsync",
})
_CS_RENDER_CALL_NAMES: frozenset[str] = frozenset({"View", "PartialView", "Json"})
_CS_NOTIFY_CALL_NAMES: frozenset[str] = frozenset({
    "Send", "SendAsync", "SendMailAsync", "Notify", "Publish",
})
_CS_NOTIFY_TYPE_NAMES: frozenset[str] = frozenset({"SmtpClient", "MailMessage"})

# Compiled regex patterns for the C# regex fallback
_CS_RE_REGEX_CALL = re.compile(
    r'\bRegex\s*\.\s*(Match|IsMatch|Replace|Split|Matches)\s*\('
)
_CS_RE_INTERP_STR = re.compile(r'\$"')
_CS_RE_STRING_FMT = re.compile(r'\b[Ss]tring\s*\.\s*Format\s*\(')
_CS_RE_HTML_RAW = re.compile(r'\bHtml\s*\.\s*Raw\s*\(')
_CS_RE_CRON_BASE = re.compile(r':\s*(IHostedService|BackgroundService)\b')
_CS_RE_CRON_CALL = re.compile(
    r'\b(RecurringJob|BackgroundJob)\s*\.\s*(AddOrUpdate(?:Async)?|Schedule|Enqueue)\s*\('
)
_CS_RE_THRESHOLD = re.compile(
    r'(?:[<>]=?)\s*(-?\d+(?:\.\d+)?)|(-?\d+(?:\.\d+)?)\s*(?:[<>]=?)'
)
_CS_RE_CONTAINS = re.compile(r'\.\s*Contains\s*\(')
_CS_RE_DICT_START = re.compile(r'\bnew\s+(?:Dictionary|Hashtable)\s*(?:<[^>]*>)?\s*\{')
_CS_RE_DICT_ENTRY = re.compile(r'^\s*\{|^\s*\[')
_CS_RE_IF_INDENT = re.compile(r'^(\s+)if\s*\(')


def _cs_walk_scoped(root: Any, src: bytes):
    """Iterative DFS generator yielding (node, parent_type, scope_name)."""
    stack: list[tuple[Any, str, str]] = [(root, "", "<module>")]
    while stack:
        node, parent_type, scope = stack.pop()
        new_scope = scope
        if node.type in _CS_METHOD_NODE_TYPES:
            name_child = node.child_by_field_name("name")
            if name_child is not None:
                new_scope = src[name_child.start_byte:name_child.end_byte].decode(
                    "utf-8", errors="replace"
                )
        yield node, parent_type, new_scope
        for child in reversed(node.children):
            stack.append((child, node.type, new_scope))


def _cs_get_method_call(node: Any, src: bytes) -> tuple[str, str] | None:
    """For invocation_expression, return (receiver_text, method_name) or None."""
    if node.type != "invocation_expression":
        return None
    callee = node.children[0] if node.children else None
    if callee is None or callee.type != "member_access_expression":
        return None
    named_parts = [c for c in callee.children if c.is_named]
    if len(named_parts) < 2:
        return None
    obj = src[named_parts[0].start_byte:named_parts[0].end_byte].decode(
        "utf-8", errors="replace"
    )
    name = src[named_parts[-1].start_byte:named_parts[-1].end_byte].decode(
        "utf-8", errors="replace"
    )
    return obj, name


def _cs_if_depth(root: Any) -> int:
    """Max nesting depth of if_statements within root's subtree (iterative, no crossing methods)."""
    max_depth = 0
    stack: list[tuple[Any, int]] = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        if node.type == "if_statement":
            if depth > max_depth:
                max_depth = depth
        for child in node.children:
            if child.type in _CS_METHOD_NODE_TYPES:
                continue
            new_depth = depth + 1 if child.type == "if_statement" else depth
            stack.append((child, new_depth))
    return max_depth


def _cs_detect_nested_conditionals_ts(fp: str, root: Any, src: bytes) -> list[dict]:
    results: list[dict] = []
    for node, parent_type, scope in _cs_walk_scoped(root, src):
        if node.type != "if_statement":
            continue
        if parent_type in ("if_statement", "else_clause"):
            continue
        depth = _cs_if_depth(node)
        if depth >= _PATTERN_MIN_DEPTH:
            results.append({
                "pattern_type": "nested_conditionals",
                "module_path": fp,
                "function_name": scope,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "pattern_detail": {"max_depth": depth},
            })
    return results


def _cs_detect_regex_user_input_ts(fp: str, root: Any, src: bytes) -> list[dict]:
    results: list[dict] = []
    for node, _, scope in _cs_walk_scoped(root, src):
        call = _cs_get_method_call(node, src)
        if call and call[0] == "Regex" and call[1] in _CS_REGEX_METHODS:
            results.append({
                "pattern_type": "regex_user_input",
                "module_path": fp,
                "function_name": scope,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "pattern_detail": {"call": f"Regex.{call[1]}"},
            })
    return results


def _cs_detect_string_template_ts(fp: str, root: Any, src: bytes) -> list[dict]:
    results: list[dict] = []
    for node, _, scope in _cs_walk_scoped(root, src):
        kind: str | None = None
        detail: dict[str, Any] = {}
        if node.type == "interpolated_string_expression":
            kind = "interpolated_string"
        elif node.type == "invocation_expression":
            call = _cs_get_method_call(node, src)
            if call:
                obj, method = call
                if method == "Format" and obj.lower() == "string":
                    kind = "string_format"
                    detail = {"call": f"{obj}.Format"}
                elif method == "Raw" and obj == "Html":
                    kind = "html_raw"
                    detail = {"call": "Html.Raw"}
        if kind:
            results.append({
                "pattern_type": "string_template_rendering",
                "module_path": fp,
                "function_name": scope,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "pattern_detail": {"kind": kind, **detail},
            })
    return results


def _cs_detect_scheduled_cron_ts(fp: str, root: Any, src: bytes) -> list[dict]:
    results: list[dict] = []
    for node, _, scope in _cs_walk_scoped(root, src):
        if node.type == "class_declaration":
            for child in node.children:
                if child.type != "base_list":
                    continue
                for base in child.children:
                    if not base.is_named:
                        continue
                    base_text = src[base.start_byte:base.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                    base_name = base_text.split("<")[0].strip()
                    if base_name in _CS_CRON_BASES:
                        name_child = node.child_by_field_name("name")
                        class_name = (
                            src[name_child.start_byte:name_child.end_byte].decode(
                                "utf-8", errors="replace"
                            )
                            if name_child
                            else "<unknown>"
                        )
                        results.append({
                            "pattern_type": "scheduled_cron",
                            "module_path": fp,
                            "function_name": class_name,
                            "line_start": node.start_point[0] + 1,
                            "line_end": node.end_point[0] + 1,
                            "pattern_detail": {"kind": "base_type", "base": base_name},
                        })
        elif node.type == "invocation_expression":
            call = _cs_get_method_call(node, src)
            if call and call[0] in _CS_CRON_INVOC_OBJS:
                results.append({
                    "pattern_type": "scheduled_cron",
                    "module_path": fp,
                    "function_name": scope,
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "pattern_detail": {"kind": "invocation", "call": f"{call[0]}.{call[1]}"},
                })
    return results


def _cs_detect_hardcoded_threshold_ts(fp: str, root: Any, src: bytes) -> list[dict]:
    results: list[dict] = []
    _CMP_OPS: frozenset[str] = frozenset({"<", ">", "<=", ">=", "==", "!="})
    for node, _, scope in _cs_walk_scoped(root, src):
        if node.type != "binary_expression":
            continue
        op_token: str | None = None
        for child in node.children:
            if not child.is_named:
                text = src[child.start_byte:child.end_byte].decode(
                    "utf-8", errors="replace"
                ).strip()
                if text in _CMP_OPS:
                    op_token = text
                    break
        if not op_token:
            continue
        named_parts = [c for c in node.children if c.is_named]
        numeric_literals = [
            src[c.start_byte:c.end_byte].decode("utf-8", errors="replace")
            for c in named_parts
            if c.type in ("integer_literal", "real_literal")
        ]
        if numeric_literals:
            results.append({
                "pattern_type": "hardcoded_threshold",
                "module_path": fp,
                "function_name": scope,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "pattern_detail": {"kind": "binary_expression", "constants": numeric_literals},
            })
    return results


def _cs_collect_calls_in_method(
    method_node: Any, src: bytes
) -> tuple[set[str], set[str]]:
    """Return (method_call_names, created_type_names) within method_node's subtree."""
    call_names: set[str] = set()
    created_types: set[str] = set()
    stack: list[Any] = [method_node]
    while stack:
        n = stack.pop()
        if n is not method_node and n.type in _CS_METHOD_NODE_TYPES:
            continue
        if n.type == "invocation_expression":
            call = _cs_get_method_call(n, src)
            if call:
                call_names.add(call[1])
        if n.type == "object_creation_expression":
            named = [c for c in n.children if c.is_named]
            if named:
                type_text = src[named[0].start_byte:named[0].end_byte].decode(
                    "utf-8", errors="replace"
                )
                created_types.add(type_text.split("<")[0].strip())
        for child in n.children:
            stack.append(child)
    return call_names, created_types


def _cs_detect_db_render_notify_ts(fp: str, root: Any, src: bytes) -> list[dict]:
    results: list[dict] = []
    for node, _, scope in _cs_walk_scoped(root, src):
        if node.type not in ("method_declaration", "constructor_declaration"):
            continue
        call_names, created_types = _cs_collect_calls_in_method(node, src)
        db_hits = call_names & _CS_DB_CALL_NAMES
        render_hits = call_names & _CS_RENDER_CALL_NAMES
        notify_hits = call_names & _CS_NOTIFY_CALL_NAMES
        notify_type_hits = created_types & _CS_NOTIFY_TYPE_NAMES
        if db_hits and render_hits and (notify_hits or notify_type_hits):
            results.append({
                "pattern_type": "db_render_notify_chain",
                "module_path": fp,
                "function_name": scope,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "pattern_detail": {
                    "matched_calls": {
                        "db": sorted(db_hits),
                        "render": sorted(render_hits),
                        "notify": sorted(notify_hits | notify_type_hits),
                    }
                },
            })
    return results


def _cs_detect_keyword_list_search_ts(fp: str, root: Any, src: bytes) -> list[dict]:
    results: list[dict] = []
    for node, _, scope in _cs_walk_scoped(root, src):
        call = _cs_get_method_call(node, src)
        if call and call[1] == "Contains":
            results.append({
                "pattern_type": "keyword_list_search",
                "module_path": fp,
                "function_name": scope,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "pattern_detail": {"method": "Contains", "receiver": call[0]},
            })
    return results


def _cs_detect_large_rule_table_ts(fp: str, root: Any, src: bytes) -> list[dict]:
    results: list[dict] = []
    for node, _, scope in _cs_walk_scoped(root, src):
        if node.type != "object_creation_expression":
            continue
        node_text = src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        if "Dictionary" not in node_text and "Hashtable" not in node_text:
            continue
        for child in node.children:
            if child.type == "initializer_expression":
                named_entries = [c for c in child.children if c.is_named]
                if len(named_entries) >= _RULE_MIN_KEYS:
                    results.append({
                        "pattern_type": "large_rule_table",
                        "module_path": fp,
                        "function_name": scope,
                        "line_start": node.start_point[0] + 1,
                        "line_end": node.end_point[0] + 1,
                        "pattern_detail": {"key_count": len(named_entries)},
                    })
                break
    return results


def _cs_detect_via_tree_sitter(file_path: str, tree: Any, src: bytes) -> list[dict]:
    """Run all 8 pattern detectors using a parsed tree-sitter C# tree."""
    root = tree.root_node
    results: list[dict] = []
    results.extend(_cs_detect_nested_conditionals_ts(file_path, root, src))
    results.extend(_cs_detect_regex_user_input_ts(file_path, root, src))
    results.extend(_cs_detect_string_template_ts(file_path, root, src))
    results.extend(_cs_detect_scheduled_cron_ts(file_path, root, src))
    results.extend(_cs_detect_hardcoded_threshold_ts(file_path, root, src))
    results.extend(_cs_detect_db_render_notify_ts(file_path, root, src))
    results.extend(_cs_detect_keyword_list_search_ts(file_path, root, src))
    results.extend(_cs_detect_large_rule_table_ts(file_path, root, src))
    for hit in results:
        hit.setdefault("language", "csharp")
    return results


# ── C# regex fallback (when tree-sitter-languages not installed) ──────────────

def _cs_regex_nested_ifs(file_path: str, lines: list[str]) -> list[dict]:
    """Indentation-based heuristic for nested if detection (regex fallback)."""
    results: list[dict] = []
    if_stack: list[tuple[int, int]] = []  # (indent_col, line_number)
    reported: set[int] = set()

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        m = _CS_RE_IF_INDENT.match(line)
        if m:
            indent = len(m.group(1).expandtabs(4))
            # Pop entries at same or deeper indent — they're out of scope
            if_stack = [(ind, ln) for ind, ln in if_stack if ind < indent]
            if_stack.append((indent, i))
            if len(if_stack) >= _PATTERN_MIN_DEPTH:
                outer_ln = if_stack[0][1]
                if outer_ln not in reported:
                    reported.add(outer_ln)
                    results.append({
                        "pattern_type": "nested_conditionals",
                        "module_path": file_path,
                        "function_name": "<unknown>",
                        "line_start": outer_ln,
                        "line_end": i,
                        "pattern_detail": {"max_depth": len(if_stack)},
                    })
    return results


def _cs_regex_large_dict(file_path: str, lines: list[str]) -> list[dict]:
    """Multi-line scanner for large Dictionary initializers (regex fallback)."""
    results: list[dict] = []
    n = len(lines)
    i = 0
    while i < n:
        if _CS_RE_DICT_START.search(lines[i]):
            start_line = i + 1
            entry_count = 0
            brace_depth = lines[i].count("{") - lines[i].count("}")
            j = i + 1
            while j < n and brace_depth > 0:
                brace_depth += lines[j].count("{") - lines[j].count("}")
                if brace_depth > 0 and _CS_RE_DICT_ENTRY.match(lines[j]):
                    entry_count += 1
                j += 1
            if entry_count >= _RULE_MIN_KEYS:
                results.append({
                    "pattern_type": "large_rule_table",
                    "module_path": file_path,
                    "function_name": "<unknown>",
                    "line_start": start_line,
                    "line_end": j,
                    "pattern_detail": {"key_count": entry_count},
                })
        i += 1
    return results


def _cs_detect_via_regex(file_path: str, source_text: str) -> list[dict]:
    """Regex-based C# pattern detection when tree-sitter-languages is unavailable."""
    results: list[dict] = []
    lines = source_text.splitlines()

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue

        # regex_user_input
        m = _CS_RE_REGEX_CALL.search(line)
        if m:
            results.append({
                "pattern_type": "regex_user_input",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i,
                "line_end": i,
                "pattern_detail": {"call": f"Regex.{m.group(1)}"},
            })

        # string_template_rendering (first match wins per line)
        for pat, kind, extra in (
            (_CS_RE_INTERP_STR, "interpolated_string", {}),
            (_CS_RE_STRING_FMT, "string_format", {"call": "string.Format"}),
            (_CS_RE_HTML_RAW, "html_raw", {"call": "Html.Raw"}),
        ):
            if pat.search(line):
                results.append({
                    "pattern_type": "string_template_rendering",
                    "module_path": file_path,
                    "function_name": "<unknown>",
                    "line_start": i,
                    "line_end": i,
                    "pattern_detail": {"kind": kind, **extra},
                })
                break

        # scheduled_cron — base type
        m = _CS_RE_CRON_BASE.search(line)
        if m:
            results.append({
                "pattern_type": "scheduled_cron",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i,
                "line_end": i,
                "pattern_detail": {"kind": "base_type", "base": m.group(1)},
            })

        # scheduled_cron — call site
        m = _CS_RE_CRON_CALL.search(line)
        if m:
            results.append({
                "pattern_type": "scheduled_cron",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i,
                "line_end": i,
                "pattern_detail": {"kind": "invocation", "call": f"{m.group(1)}.{m.group(2)}"},
            })

        # hardcoded_threshold
        m = _CS_RE_THRESHOLD.search(line)
        if m:
            const = m.group(1) or m.group(2) or "?"
            results.append({
                "pattern_type": "hardcoded_threshold",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i,
                "line_end": i,
                "pattern_detail": {"kind": "binary_expression", "constants": [const]},
            })

        # keyword_list_search
        if _CS_RE_CONTAINS.search(line):
            results.append({
                "pattern_type": "keyword_list_search",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i,
                "line_end": i,
                "pattern_detail": {"method": "Contains"},
            })

    results.extend(_cs_regex_nested_ifs(file_path, lines))
    results.extend(_cs_regex_large_dict(file_path, lines))
    return results


def _cs_detect_file(file_path: str) -> list[dict]:
    """Run C# pattern detection on a single .cs file.

    Tries tree-sitter-languages first; falls back to regex when unavailable.
    """
    src = pathlib.Path(file_path).read_bytes()
    try:
        from tree_sitter_languages import get_parser  # type: ignore[import]
        parser = get_parser("c_sharp")
        tree = parser.parse(src)
        return _cs_detect_via_tree_sitter(file_path, tree, src)
    except Exception:  # ImportError or any tree-sitter parse error
        pass
    results = _cs_detect_via_regex(file_path, src.decode("utf-8", errors="replace"))
    for hit in results:
        hit.setdefault("language", "csharp")
    return results


# ── Rust tree-sitter / regex fallback ────────────────────────────────────────

_RS_SCOPE_NODE_TYPES: frozenset[str] = frozenset({
    "function_item",
    "closure_expression",
})

_RS_DB_CALL_NAMES: frozenset[str] = frozenset({
    "query", "query_as", "query_scalar", "query_as_with",
    "fetch", "fetch_one", "fetch_optional", "fetch_all",
    "execute", "execute_many", "prepare",
})
_RS_RENDER_CALL_NAMES: frozenset[str] = frozenset({
    "render", "render_str", "render_to",
})
_RS_NOTIFY_CALL_NAMES: frozenset[str] = frozenset({
    "send", "send_message", "deliver",
})

_RS_RE_REGEX_NEW = re.compile(r'\bRegex\s*::\s*new\s*\(')
_RS_RE_FORMAT_MACRO = re.compile(r'\bformat\s*!\s*[({]')
_RS_RE_ASKAMA = re.compile(r'#\s*\[\s*derive\s*\([^)]*\bTemplate\b')
_RS_RE_TERA_RENDER = re.compile(r'\.render\s*\(')
_RS_RE_CRON = re.compile(
    r'\b(?:tokio::time::interval|tokio_cron_scheduler|clokwerk)\b'
)
_RS_RE_THRESHOLD = re.compile(
    r'(?:[<>]=?|==|!=)\s*(-?\d+(?:\.\d+)?[uif\d]*)|(-?\d+(?:\.\d+)?[uif\d]*)\s*(?:[<>]=?|==|!=)'
)
_RS_RE_CONTAINS = re.compile(r'\.contains\s*\(')
_RS_RE_HASHMAP_FROM = re.compile(r'\bHashMap\s*::\s*from\s*\(')
_RS_RE_PHF_MAP = re.compile(r'\bphf_map\s*!\s*\{')
_RS_RE_IF_INDENT = re.compile(r'^(\s+)if\b')


def _rs_walk_scoped(root: Any, src: bytes):
    """Iterative DFS generator yielding (node, parent_type, scope_name)."""
    stack: list[tuple[Any, str, str]] = [(root, "", "<module>")]
    while stack:
        node, parent_type, scope = stack.pop()
        new_scope = scope
        if node.type == "function_item":
            name_child = node.child_by_field_name("name")
            if name_child is not None:
                new_scope = src[name_child.start_byte:name_child.end_byte].decode(
                    "utf-8", errors="replace"
                )
        yield node, parent_type, new_scope
        for child in reversed(node.children):
            stack.append((child, node.type, new_scope))


def _rs_if_depth(root: Any) -> int:
    """Max nesting depth of if_expression/match_guard within root's subtree."""
    _COND_TYPES: frozenset[str] = frozenset({"if_expression", "match_guard"})
    max_depth = 0
    stack: list[tuple[Any, int]] = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        if node.type in _COND_TYPES:
            if depth > max_depth:
                max_depth = depth
        for child in node.children:
            if child.type in _RS_SCOPE_NODE_TYPES:
                continue
            new_depth = depth + 1 if child.type in _COND_TYPES else depth
            stack.append((child, new_depth))
    return max_depth


def _rs_detect_nested_conditionals_ts(fp: str, root: Any, src: bytes) -> list[dict]:
    results: list[dict] = []
    for node, parent_type, scope in _rs_walk_scoped(root, src):
        if node.type != "if_expression":
            continue
        if parent_type in ("if_expression", "else_clause"):
            continue
        depth = _rs_if_depth(node)
        if depth >= _PATTERN_MIN_DEPTH:
            results.append({
                "pattern_type": "nested_conditionals",
                "module_path": fp,
                "function_name": scope,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "pattern_detail": {"max_depth": depth},
            })
    return results


def _rs_detect_regex_user_input_ts(fp: str, root: Any, src: bytes) -> list[dict]:
    results: list[dict] = []
    for node, _, scope in _rs_walk_scoped(root, src):
        if node.type != "call_expression":
            continue
        func = node.child_by_field_name("function")
        if func is None:
            continue
        func_text = src[func.start_byte:func.end_byte].decode("utf-8", errors="replace")
        if "Regex::new" in func_text:
            results.append({
                "pattern_type": "regex_user_input",
                "module_path": fp,
                "function_name": scope,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "pattern_detail": {"call": func_text},
            })
    return results


def _rs_detect_string_template_ts(fp: str, root: Any, src: bytes) -> list[dict]:
    results: list[dict] = []
    for node, _, scope in _rs_walk_scoped(root, src):
        kind: str | None = None
        detail: dict[str, Any] = {}

        if node.type == "macro_invocation":
            macro_node = node.child_by_field_name("macro")
            if macro_node is None:
                named = [c for c in node.children if c.is_named]
                macro_node = named[0] if named else None
            if macro_node is not None:
                macro_text = src[macro_node.start_byte:macro_node.end_byte].decode(
                    "utf-8", errors="replace"
                )
                if macro_text == "format":
                    kind = "format_macro"
                    detail = {"macro": "format!"}

        elif node.type == "attribute_item":
            attr_text = src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            if "derive" in attr_text and "Template" in attr_text:
                kind = "askama_derive"
                detail = {"attribute": attr_text.strip()[:80]}

        elif node.type == "method_call_expression":
            name_child = node.child_by_field_name("name")
            if name_child is not None:
                method = src[name_child.start_byte:name_child.end_byte].decode(
                    "utf-8", errors="replace"
                )
                if method == "render":
                    kind = "template_render"
                    detail = {"method": ".render()"}

        if kind:
            results.append({
                "pattern_type": "string_template_rendering",
                "module_path": fp,
                "function_name": scope,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "pattern_detail": {"kind": kind, **detail},
            })
    return results


def _rs_detect_scheduled_cron_ts(fp: str, root: Any, src: bytes) -> list[dict]:
    _CRON_FRAGMENTS: frozenset[str] = frozenset({
        "tokio::time::interval", "tokio_cron_scheduler", "clokwerk",
    })
    results: list[dict] = []
    for node, _, scope in _rs_walk_scoped(root, src):
        if node.type != "call_expression":
            continue
        func = node.child_by_field_name("function")
        if func is None:
            continue
        func_text = src[func.start_byte:func.end_byte].decode("utf-8", errors="replace")
        if any(frag in func_text for frag in _CRON_FRAGMENTS):
            results.append({
                "pattern_type": "scheduled_cron",
                "module_path": fp,
                "function_name": scope,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "pattern_detail": {"kind": "call", "call": func_text},
            })
    return results


def _rs_detect_hardcoded_threshold_ts(fp: str, root: Any, src: bytes) -> list[dict]:
    _CMP_OPS: frozenset[str] = frozenset({"<", ">", "<=", ">=", "==", "!="})
    results: list[dict] = []
    for node, _, scope in _rs_walk_scoped(root, src):
        if node.type != "binary_expression":
            continue
        op_token: str | None = None
        for child in node.children:
            if not child.is_named:
                text = src[child.start_byte:child.end_byte].decode(
                    "utf-8", errors="replace"
                ).strip()
                if text in _CMP_OPS:
                    op_token = text
                    break
        if not op_token:
            continue
        named_parts = [c for c in node.children if c.is_named]
        numeric_literals = [
            src[c.start_byte:c.end_byte].decode("utf-8", errors="replace")
            for c in named_parts
            if c.type in ("integer_literal", "float_literal")
        ]
        if numeric_literals:
            results.append({
                "pattern_type": "hardcoded_threshold",
                "module_path": fp,
                "function_name": scope,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "pattern_detail": {"kind": "binary_expression", "constants": numeric_literals},
            })
    return results


def _rs_collect_calls_in_fn(fn_node: Any, src: bytes) -> set[str]:
    """Collect method and function call names within fn_node, not crossing nested functions."""
    calls: set[str] = set()
    stack: list[Any] = [fn_node]
    while stack:
        n = stack.pop()
        if n is not fn_node and n.type == "function_item":
            continue
        if n.type == "method_call_expression":
            name_child = n.child_by_field_name("name")
            if name_child:
                calls.add(
                    src[name_child.start_byte:name_child.end_byte].decode(
                        "utf-8", errors="replace"
                    )
                )
        elif n.type == "call_expression":
            func = n.child_by_field_name("function")
            if func:
                func_text = src[func.start_byte:func.end_byte].decode(
                    "utf-8", errors="replace"
                )
                calls.add(func_text.split("::")[-1])
        for child in n.children:
            stack.append(child)
    return calls


def _rs_detect_db_render_notify_ts(fp: str, root: Any, src: bytes) -> list[dict]:
    results: list[dict] = []
    for node, _, scope in _rs_walk_scoped(root, src):
        if node.type != "function_item":
            continue
        calls = _rs_collect_calls_in_fn(node, src)
        db_hits = calls & _RS_DB_CALL_NAMES
        render_hits = calls & _RS_RENDER_CALL_NAMES
        notify_hits = calls & _RS_NOTIFY_CALL_NAMES
        if db_hits and render_hits and notify_hits:
            results.append({
                "pattern_type": "db_render_notify_chain",
                "module_path": fp,
                "function_name": scope,
                "line_start": node.start_point[0] + 1,
                "line_end": node.end_point[0] + 1,
                "pattern_detail": {
                    "matched_calls": {
                        "db": sorted(db_hits),
                        "render": sorted(render_hits),
                        "notify": sorted(notify_hits),
                    }
                },
            })
    return results


def _rs_detect_keyword_list_search_ts(fp: str, root: Any, src: bytes) -> list[dict]:
    results: list[dict] = []
    for node, _, scope in _rs_walk_scoped(root, src):
        if node.type != "method_call_expression":
            continue
        name_child = node.child_by_field_name("name")
        if name_child is None:
            continue
        method = src[name_child.start_byte:name_child.end_byte].decode(
            "utf-8", errors="replace"
        )
        if method != "contains":
            continue
        receiver = node.child_by_field_name("receiver")
        receiver_text = (
            src[receiver.start_byte:receiver.end_byte].decode("utf-8", errors="replace")[:50]
            if receiver
            else "<unknown>"
        )
        results.append({
            "pattern_type": "keyword_list_search",
            "module_path": fp,
            "function_name": scope,
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
            "pattern_detail": {"method": "contains", "receiver": receiver_text},
        })
    return results


def _rs_detect_large_rule_table_ts(fp: str, root: Any, src: bytes) -> list[dict]:
    results: list[dict] = []
    for node, _, scope in _rs_walk_scoped(root, src):
        if node.type == "call_expression":
            func = node.child_by_field_name("function")
            if func is None:
                continue
            func_text = src[func.start_byte:func.end_byte].decode("utf-8", errors="replace")
            if "HashMap::from" not in func_text:
                continue
            args = node.child_by_field_name("arguments")
            if args is None:
                continue
            for child in args.children:
                if child.type == "array_expression":
                    entries = [c for c in child.children if c.is_named]
                    if len(entries) >= _RULE_MIN_KEYS:
                        results.append({
                            "pattern_type": "large_rule_table",
                            "module_path": fp,
                            "function_name": scope,
                            "line_start": node.start_point[0] + 1,
                            "line_end": node.end_point[0] + 1,
                            "pattern_detail": {
                                "kind": "HashMap::from",
                                "entry_count": len(entries),
                            },
                        })
                    break

        elif node.type == "macro_invocation":
            macro_node = node.child_by_field_name("macro")
            if macro_node is None:
                named = [c for c in node.children if c.is_named]
                macro_node = named[0] if named else None
            if macro_node is None:
                continue
            macro_text = src[macro_node.start_byte:macro_node.end_byte].decode(
                "utf-8", errors="replace"
            )
            if macro_text not in ("phf_map", "phf::phf_map"):
                continue
            node_text = src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            entry_count = node_text.count("=>")
            if entry_count >= _RULE_MIN_KEYS:
                results.append({
                    "pattern_type": "large_rule_table",
                    "module_path": fp,
                    "function_name": scope,
                    "line_start": node.start_point[0] + 1,
                    "line_end": node.end_point[0] + 1,
                    "pattern_detail": {"kind": "phf_map!", "entry_count": entry_count},
                })
    return results


def _rs_detect_via_tree_sitter(file_path: str, tree: Any, src: bytes) -> list[dict]:
    """Run all 8 pattern detectors using a parsed tree-sitter Rust tree."""
    root = tree.root_node
    results: list[dict] = []
    results.extend(_rs_detect_nested_conditionals_ts(file_path, root, src))
    results.extend(_rs_detect_regex_user_input_ts(file_path, root, src))
    results.extend(_rs_detect_string_template_ts(file_path, root, src))
    results.extend(_rs_detect_scheduled_cron_ts(file_path, root, src))
    results.extend(_rs_detect_hardcoded_threshold_ts(file_path, root, src))
    results.extend(_rs_detect_db_render_notify_ts(file_path, root, src))
    results.extend(_rs_detect_keyword_list_search_ts(file_path, root, src))
    results.extend(_rs_detect_large_rule_table_ts(file_path, root, src))
    for hit in results:
        hit.setdefault("language", "rust")
    return results


# ── Rust regex fallback ───────────────────────────────────────────────────────


def _rs_regex_nested_ifs(file_path: str, lines: list[str]) -> list[dict]:
    """Indentation-based heuristic for nested if detection (Rust regex fallback)."""
    results: list[dict] = []
    if_stack: list[tuple[int, int]] = []
    reported: set[int] = set()

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        m = _RS_RE_IF_INDENT.match(line)
        if m:
            indent = len(m.group(1).expandtabs(4))
            if_stack = [(ind, ln) for ind, ln in if_stack if ind < indent]
            if_stack.append((indent, i))
            if len(if_stack) >= _PATTERN_MIN_DEPTH:
                outer_ln = if_stack[0][1]
                if outer_ln not in reported:
                    reported.add(outer_ln)
                    results.append({
                        "pattern_type": "nested_conditionals",
                        "module_path": file_path,
                        "function_name": "<unknown>",
                        "line_start": outer_ln,
                        "line_end": i,
                        "pattern_detail": {"max_depth": len(if_stack)},
                    })
    return results


def _rs_regex_large_table(file_path: str, lines: list[str]) -> list[dict]:
    """Scanner for large HashMap::from / phf_map! initializers (Rust regex fallback)."""
    results: list[dict] = []
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        is_hashmap = bool(_RS_RE_HASHMAP_FROM.search(line))
        is_phf = bool(_RS_RE_PHF_MAP.search(line))
        if is_hashmap or is_phf:
            start_line = i + 1
            open_ch, close_ch = ("{", "}") if is_phf else ("[", "]")
            depth = line.count(open_ch) - line.count(close_ch)
            block = line
            j = i + 1
            while j < n and depth > 0:
                depth += lines[j].count(open_ch) - lines[j].count(close_ch)
                block += "\n" + lines[j]
                j += 1
            if is_phf:
                # phf_map! entries use `"key" => value` syntax
                entry_count = block.count("=>")
            else:
                # HashMap::from entries are tuples: ("key", value)
                # Count "), " separators between tuples, then +1 for last entry
                entry_count = block.count("),") + 1
            if entry_count >= _RULE_MIN_KEYS:
                kind = "phf_map!" if is_phf else "HashMap::from"
                results.append({
                    "pattern_type": "large_rule_table",
                    "module_path": file_path,
                    "function_name": "<unknown>",
                    "line_start": start_line,
                    "line_end": j,
                    "pattern_detail": {"kind": kind, "entry_count": entry_count},
                })
        i += 1
    return results


def _rs_detect_via_regex(file_path: str, source_text: str) -> list[dict]:
    """Regex-based Rust pattern detection when tree-sitter-languages is unavailable."""
    results: list[dict] = []
    lines = source_text.splitlines()

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue

        # regex_user_input
        if _RS_RE_REGEX_NEW.search(line):
            results.append({
                "pattern_type": "regex_user_input",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i,
                "line_end": i,
                "pattern_detail": {"call": "Regex::new"},
            })

        # string_template_rendering (first match wins per line)
        for pat, kind, extra in (
            (_RS_RE_FORMAT_MACRO, "format_macro", {"macro": "format!"}),
            (_RS_RE_ASKAMA, "askama_derive", {}),
            (_RS_RE_TERA_RENDER, "template_render", {"method": ".render()"}),
        ):
            if pat.search(line):
                results.append({
                    "pattern_type": "string_template_rendering",
                    "module_path": file_path,
                    "function_name": "<unknown>",
                    "line_start": i,
                    "line_end": i,
                    "pattern_detail": {"kind": kind, **extra},
                })
                break

        # scheduled_cron
        if _RS_RE_CRON.search(line):
            results.append({
                "pattern_type": "scheduled_cron",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i,
                "line_end": i,
                "pattern_detail": {"kind": "call"},
            })

        # hardcoded_threshold
        m = _RS_RE_THRESHOLD.search(line)
        if m:
            const = m.group(1) or m.group(2) or "?"
            results.append({
                "pattern_type": "hardcoded_threshold",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i,
                "line_end": i,
                "pattern_detail": {"kind": "binary_expression", "constants": [const]},
            })

        # keyword_list_search
        if _RS_RE_CONTAINS.search(line):
            results.append({
                "pattern_type": "keyword_list_search",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i,
                "line_end": i,
                "pattern_detail": {"method": "contains"},
            })

    results.extend(_rs_regex_nested_ifs(file_path, lines))
    results.extend(_rs_regex_large_table(file_path, lines))
    return results


def _rs_detect_file(file_path: str) -> list[dict]:
    """Run Rust pattern detection on a single .rs file.

    Tries tree-sitter-languages first; falls back to regex when unavailable.
    """
    src = pathlib.Path(file_path).read_bytes()
    try:
        from tree_sitter_languages import get_parser  # type: ignore[import]
        parser = get_parser("rust")
        tree = parser.parse(src)
        return _rs_detect_via_tree_sitter(file_path, tree, src)
    except Exception:
        pass
    results = _rs_detect_via_regex(file_path, src.decode("utf-8", errors="replace"))
    for hit in results:
        hit.setdefault("language", "rust")
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
