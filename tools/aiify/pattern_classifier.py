# CUI // SP-CTI
"""AI-ify Canvas — Pattern Classifier (Semgrep + AST fallback).

Primary path: Semgrep CLI is invoked via subprocess; its JSON output is parsed
and mapped to the canonical result schema.

Fallback (air-gap / Semgrep unavailable): Python stdlib ast walker handles
Python source files only.

Public API:
    detect_patterns(target_path: str) -> list[dict]

Each result dict contains:
    pattern_type    — one of tools.aiify.constants.PATTERN_TYPES
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
import os
import pathlib
import re
import subprocess
import shutil
from typing import Any

import yaml

# ── Config ────────────────────────────────────────────────────────────────────

_CONFIG_PATH = pathlib.Path(__file__).parent.parent.parent / "args" / "aiify_config.yaml"
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
_KEYWORD_LIST_MIN_STRINGS: int = int(_cfg.get("keyword_list_min_strings", 3))

_threshold_ad_cfg: dict[str, Any] = _cfg.get("threshold_anomaly_detection", {})
_AD_ENABLED: bool = bool(_threshold_ad_cfg.get("enabled", True))
_AD_Z_SCORE_THRESHOLD: float = float(_threshold_ad_cfg.get("z_score_threshold", 2.0))
_AD_IQR_MULTIPLIER: float = float(_threshold_ad_cfg.get("iqr_multiplier", 1.5))
_AD_MIN_SAMPLE_SIZE: int = int(_threshold_ad_cfg.get("min_sample_size", 5))
_AD_FALLBACK_TO_ALL: bool = bool(_threshold_ad_cfg.get("fallback_to_all", True))
_AD_MIN_CONSTANT_MAGNITUDE: float = float(
    _threshold_ad_cfg.get("min_constant_magnitude", 1.0)
)
_AD_Q1_PERCENTILE: float = float(_threshold_ad_cfg.get("q1_percentile", 25.0))
_AD_Q3_PERCENTILE: float = float(_threshold_ad_cfg.get("q3_percentile", 75.0))
_AD_PERCENTILE_SCALE: float = float(_threshold_ad_cfg.get("percentile_scale", 100.0))

_semgrep_cfg: dict[str, Any] = _cfg.get("semgrep", {})
_SEMGREP_RULES_DIR: str = _semgrep_cfg.get(
    "rules_dir", "context/aiify/semgrep_rules"
)
_SEMGREP_TIMEOUT: int = int(_semgrep_cfg.get("timeout_seconds", 60))
_SEMGREP_METRICS: str = str(_semgrep_cfg.get("metrics", "off"))

_ml_nc_cfg: dict[str, Any] = _cfg.get("ml_nested_conditionals", {})
# Env var ICDEV_AIIFY_ML_CLASSIFY takes precedence over yaml config.
_ML_NC_ENABLED: bool = (
    os.environ.get("ICDEV_AIIFY_ML_CLASSIFY", "").lower() in ("1", "true", "yes")
    or bool(_ml_nc_cfg.get("enabled", False))
)
_ML_NC_MODEL: str = str(_ml_nc_cfg.get("model", "claude-haiku-4-5-20251001"))
_ML_NC_MAX_TOKENS: int = int(_ml_nc_cfg.get("max_tokens", 256))
_ML_NC_CONTEXT_LINES: int = int(_ml_nc_cfg.get("context_lines", 5))

_ml_rui_cfg: dict[str, Any] = _cfg.get("ml_regex_user_input", {})
# Env var ICDEV_AIIFY_NLP_REGEX takes precedence over yaml config.
_ML_RUI_ENABLED: bool = (
    os.environ.get("ICDEV_AIIFY_NLP_REGEX", "").lower() in ("1", "true", "yes")
    or bool(_ml_rui_cfg.get("enabled", False))
)
_ML_RUI_MODEL: str = str(_ml_rui_cfg.get("model", "claude-haiku-4-5-20251001"))
_ML_RUI_MAX_TOKENS: int = int(_ml_rui_cfg.get("max_tokens", 256))
_ML_RUI_CONTEXT_LINES: int = int(_ml_rui_cfg.get("context_lines", 5))

_ML_NC_PROMPT = """\
You are a code analyst evaluating whether a nested conditional block is a \
strong candidate for replacement with an ML classifier.

Function  : {function_name}
If-depth  : {depth}
Snippet   :
```python
{snippet}
```

Rate the AI augmentation potential:
- high   — encodes complex, data-driven business rules that an ML model could learn
- medium — some potential but contains validation/structural logic that may not generalise
- low    — guard clauses, error handling, or structural necessity

Respond with JSON only (no markdown):
{{"label": "high", "score": 0.85, "rationale": "one sentence"}}"""

_ML_RUI_PROMPT = """\
You are a code analyst evaluating whether a regex-based user input pattern is a \
strong candidate for replacement with an NLP or ML approach.

Function : {function_name}
Call     : {call}
Snippet  :
```python
{snippet}
```

Classify the NLP extraction opportunity:
- intent        — one of: validation, extraction, parsing, sanitization, unknown
- nlp_candidate — e.g. "email classifier", "NER", "intent classifier", "none"
- score         — 0.0–1.0 confidence that an NLP/ML approach adds value over regex
- rationale     — one sentence

Respond with JSON only (no markdown):
{{"intent": "validation", "nlp_candidate": "email classifier", "score": 0.75, "rationale": "one sentence"}}"""

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
    """Map raw Semgrep result objects to the canonical AI-ify pattern dict."""
    results: list[dict] = []
    for hit in hits:
        extra = hit.get("extra", {})
        metadata = extra.get("metadata", {})
        pattern_type = metadata.get("aiify_pattern")
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
            k: v for k, v in metadata.items() if k != "aiify_pattern"
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

# Compiled pattern for extracting numeric literals from source lines (used by
# C# and Java regex fallback paths to build the anomaly-detection population).
_RE_NUMERIC_LITERAL: re.Pattern = re.compile(r'\b\d+(?:\.\d+)?\b')


# ── Anomaly detection for hardcoded thresholds ────────────────────────────────


def _collect_all_numeric_thresholds(tree: ast.AST) -> list[float]:
    """Collect every numeric constant appearing in comparisons or binary ops."""
    values: list[float] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            if any(isinstance(op, _THRESHOLD_OPS) for op in node.ops):
                for c in [node.left, *node.comparators]:
                    if isinstance(c, ast.Constant) and isinstance(c.value, (int, float)):
                        values.append(float(c.value))
        elif isinstance(node, ast.BinOp):
            for side in (node.left, node.right):
                if isinstance(side, ast.Constant) and isinstance(side.value, (int, float)):
                    values.append(float(side.value))
    return values


def _compute_percentile_bounds(sorted_pop: list[float], n: int) -> tuple[float, float, float]:
    """Return (q1, q3, iqr) using _AD_Q1_PERCENTILE and _AD_Q3_PERCENTILE.

    Default 25/75 reproduces the prior hardcoded Q1/Q3 (n//4, 3n//4) exactly.
    """
    q1_idx = max(0, min(n - 1, int(n * _AD_Q1_PERCENTILE / _AD_PERCENTILE_SCALE)))
    q3_idx = max(0, min(n - 1, int(n * _AD_Q3_PERCENTILE / _AD_PERCENTILE_SCALE)))
    q1 = sorted_pop[q1_idx]
    q3 = sorted_pop[q3_idx]
    return q1, q3, q3 - q1


def _is_threshold_anomalous(value: float, population: list[float]) -> bool:
    """Return True if value is a statistical outlier in population.

    Uses z-score first; falls back to IQR fence when variance is zero.
    When the population is too small, respects _AD_FALLBACK_TO_ALL.
    """
    n = len(population)
    if n < _AD_MIN_SAMPLE_SIZE:
        return _AD_FALLBACK_TO_ALL

    mean = sum(population) / n
    variance = sum((x - mean) ** 2 for x in population) / n
    if variance > 0:
        z = abs(value - mean) / (variance ** 0.5)
        if z > _AD_Z_SCORE_THRESHOLD:
            return True

    sorted_pop = sorted(population)
    q1, q3, iqr = _compute_percentile_bounds(sorted_pop, n)
    if iqr > 0:
        lower = q1 - _AD_IQR_MULTIPLIER * iqr
        upper = q3 + _AD_IQR_MULTIPLIER * iqr
        if value < lower or value > upper:
            return True

    return False


def _anomaly_score(value: float, population: list[float]) -> float:
    """Return a continuous anomaly score for *value* within *population*.

    0.0 = value is within the normal range.
    1.0 = value is exactly at the anomaly threshold.
    >1.0 = value is anomalous (corresponds to _is_threshold_anomalous → True).

    Uses the same z-score / IQR logic as _is_threshold_anomalous so the two
    functions always agree on the anomaly boundary.
    """
    n = len(population)
    if n < _AD_MIN_SAMPLE_SIZE:
        return 1.0 if _AD_FALLBACK_TO_ALL else 0.0

    mean = sum(population) / n
    variance = sum((x - mean) ** 2 for x in population) / n
    if variance > 0:
        z = abs(value - mean) / (variance ** 0.5)
        return z / _AD_Z_SCORE_THRESHOLD  # >= 1.0 when anomalous

    sorted_pop = sorted(population)
    q1, q3, iqr = _compute_percentile_bounds(sorted_pop, n)
    if iqr > 0:
        lower = q1 - _AD_IQR_MULTIPLIER * iqr
        upper = q3 + _AD_IQR_MULTIPLIER * iqr
        if value < lower:
            return (lower - value) / (_AD_IQR_MULTIPLIER * iqr) + 1.0
        if value > upper:
            return (value - upper) / (_AD_IQR_MULTIPLIER * iqr) + 1.0

    return 0.0


def _collect_numeric_from_lines(lines: list[str]) -> list[float]:
    """Collect all numeric literals from source lines for anomaly detection.

    Used by the C# and Java regex-fallback paths to build the population that
    ``_is_threshold_anomalous`` needs to decide whether a given constant is an
    outlier.  Comment lines are skipped so doc-strings don't skew the sample.
    """
    values: list[float] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("//", "*", "#")):
            continue
        for m in _RE_NUMERIC_LITERAL.finditer(line):
            try:
                values.append(float(m.group()))
            except ValueError:
                pass
    return values


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

    # Optional ML enrichment: annotate nested_conditionals hits with a
    # Claude Haiku classification score when ICDEV_AIIFY_ML_CLASSIFY is set.
    if _ML_NC_ENABLED:
        raw = _enrich_nested_conditionals_with_ml(raw, source_text.splitlines())

    # Optional NLP enrichment: annotate regex_user_input hits with intent and
    # NLP candidate classification when ICDEV_AIIFY_NLP_REGEX is set.
    if _ML_RUI_ENABLED:
        raw = _enrich_regex_user_input_with_nlp(raw, source_text.splitlines())

    # Inject language field for consistency with Semgrep output schema.
    for hit in raw:
        hit.setdefault("language", "python")
    return raw


def _detect_via_ast_fallback(target_path: str) -> list[dict]:
    """AST-based fallback for Python/C# files when Semgrep is unavailable."""
    p = pathlib.Path(target_path)
    if p.is_file():
        suffix = p.suffix.lower()
        if suffix == ".py":
            return _ast_detect_file(target_path)
        if suffix == ".cs":
            return _cs_detect_file(target_path)
        return []
    # Directory: walk recursively for .py and .cs files.
    results: list[dict] = []
    for py_file in sorted(p.rglob("*.py")):
        results.extend(_ast_detect_file(str(py_file)))
    for cs_file in sorted(p.rglob("*.cs")):
        results.extend(_cs_detect_file(str(cs_file)))
    return results


# ── ML classifier for nested_conditionals ────────────────────────────────────


def _classify_nested_conditional_ml(
    function_name: str,
    depth: int,
    snippet: str,
) -> dict[str, Any]:
    """Call LLMRouter (claude-haiku) to classify a nested conditional pattern.

    Returns a dict with keys ``label``, ``score``, and ``rationale``, or an
    empty dict if the API call fails or ML classification is disabled.
    """
    try:
        from tools.llm.provider import LLMRequest  # lazy import — optional dep
        from tools.llm.router import LLMRouter
    except ImportError:
        return {}

    try:
        router = LLMRouter()
        request = LLMRequest(
            messages=[{
                "role": "user",
                "content": _ML_NC_PROMPT.format(
                    function_name=function_name or "<unknown>",
                    depth=depth,
                    snippet=snippet,
                ),
            }],
            system_prompt=(
                "You are a code analysis assistant. "
                "Reply only with the JSON object specified — no prose."
            ),
            agent_id="nested-conditional-ml-classifier",
            classification="CUI",
            max_tokens=_ML_NC_MAX_TOKENS,
            effort="low",
            preferred_model=_ML_NC_MODEL,
        )
        response = router.invoke("code_generation", request)
        raw = re.sub(r"```(?:json)?|```", "", response.content or "").strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data: dict[str, Any] = json.loads(m.group(0)) if m else {}
    except Exception:  # network error, parse error — degrade gracefully
        return {}

    label = str(data.get("label", "")).lower()
    if label not in ("high", "medium", "low"):
        label = ""
    return {
        "label": label,
        "score": float(data.get("score", 0.0)),
        "rationale": str(data.get("rationale", "")),
    }


def _enrich_nested_conditionals_with_ml(
    patterns: list[dict],
    source_lines: list[str],
) -> list[dict]:
    """Enrich ``nested_conditionals`` hits with ML classification metadata.

    Non-nested_conditionals patterns are returned unchanged.  When ML
    classification is disabled or fails, the original pattern dicts are returned
    without modification.
    """
    if not _ML_NC_ENABLED:
        return patterns

    enriched: list[dict] = []
    for p in patterns:
        if p.get("pattern_type") != "nested_conditionals":
            enriched.append(p)
            continue

        start = max(0, p["line_start"] - 1 - _ML_NC_CONTEXT_LINES)
        end = min(len(source_lines), p["line_end"] + _ML_NC_CONTEXT_LINES)
        snippet = "\n".join(source_lines[start:end])

        ml = _classify_nested_conditional_ml(
            function_name=p.get("function_name", "<unknown>"),
            depth=p.get("pattern_detail", {}).get("max_depth", 0),
            snippet=snippet,
        )
        if ml:
            p = {**p, "pattern_detail": {**p["pattern_detail"], **{
                "ml_label": ml["label"],
                "ml_score": ml["score"],
                "ml_rationale": ml["rationale"],
            }}}
        enriched.append(p)
    return enriched


# ── NLP extractor for regex_user_input ───────────────────────────────────────

_ML_RUI_VALID_INTENTS: frozenset[str] = frozenset(
    {"validation", "extraction", "parsing", "sanitization", "unknown"}
)


def _classify_regex_nlp(
    function_name: str,
    call: str,
    snippet: str,
) -> dict[str, Any]:
    """Call LLMRouter (claude-haiku) to classify a regex_user_input pattern.

    Returns a dict with keys ``intent``, ``nlp_candidate``, ``score``, and
    ``rationale``, or an empty dict if the API call fails or NLP is disabled.
    """
    try:
        from tools.llm.provider import LLMRequest  # lazy import — optional dep
        from tools.llm.router import LLMRouter
    except ImportError:
        return {}

    try:
        router = LLMRouter()
        request = LLMRequest(
            messages=[{
                "role": "user",
                "content": _ML_RUI_PROMPT.format(
                    function_name=function_name or "<unknown>",
                    call=call,
                    snippet=snippet,
                ),
            }],
            system_prompt=(
                "You are a code analysis assistant. "
                "Reply only with the JSON object specified — no prose."
            ),
            agent_id="regex-nlp-extractor",
            classification="CUI",
            max_tokens=_ML_RUI_MAX_TOKENS,
            effort="low",
            preferred_model=_ML_RUI_MODEL,
        )
        response = router.invoke("code_generation", request)
        raw = re.sub(r"```(?:json)?|```", "", response.content or "").strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data: dict[str, Any] = json.loads(m.group(0)) if m else {}
    except Exception:  # network error, parse error — degrade gracefully
        return {}

    intent = str(data.get("intent", "")).lower()
    if intent not in _ML_RUI_VALID_INTENTS:
        intent = "unknown"
    return {
        "intent": intent,
        "nlp_candidate": str(data.get("nlp_candidate", "")),
        "score": float(data.get("score", 0.0)),
        "rationale": str(data.get("rationale", "")),
    }


def _enrich_regex_user_input_with_nlp(
    patterns: list[dict],
    source_lines: list[str],
) -> list[dict]:
    """Enrich ``regex_user_input`` hits with NLP classification metadata.

    Non-regex_user_input patterns are returned unchanged.  When NLP
    classification is disabled or fails, original pattern dicts are returned
    without modification.
    """
    if not _ML_RUI_ENABLED:
        return patterns

    enriched: list[dict] = []
    for p in patterns:
        if p.get("pattern_type") != "regex_user_input":
            enriched.append(p)
            continue

        start = max(0, p["line_start"] - 1 - _ML_RUI_CONTEXT_LINES)
        end = min(len(source_lines), p["line_end"] + _ML_RUI_CONTEXT_LINES)
        snippet = "\n".join(source_lines[start:end])

        nlp = _classify_regex_nlp(
            function_name=p.get("function_name", "<unknown>"),
            call=p.get("pattern_detail", {}).get("call", ""),
            snippet=snippet,
        )
        if nlp:
            p = {**p, "pattern_detail": {**p["pattern_detail"], **{
                "nlp_intent": nlp["intent"],
                "nlp_candidate": nlp["nlp_candidate"],
                "nlp_score": nlp["score"],
                "nlp_rationale": nlp["rationale"],
            }}}
        enriched.append(p)
    return enriched


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

    # Pre-collect the full population of numeric constants so the anomaly
    # detector can decide which values are true outliers vs. routine literals.
    population: list[float] = (
        _collect_all_numeric_thresholds(tree) if _AD_ENABLED else []
    )

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
            if not numeric_consts:
                continue
            # When AD is on, skip trivially small constants (zero/one boundary checks).
            flaggable = (
                [v for v in numeric_consts if abs(float(v)) > _AD_MIN_CONSTANT_MAGNITUDE]
                if _AD_ENABLED else numeric_consts
            )
            if not flaggable:
                continue
            if _AD_ENABLED and not any(
                _is_threshold_anomalous(float(v), population) for v in flaggable
            ):
                continue
            scores = (
                {str(v): round(_anomaly_score(float(v), population), 3) for v in flaggable}
                if _AD_ENABLED else {}
            )
            detail: dict[str, Any] = {
                "kind": "compare",
                "constants": numeric_consts,
                "anomaly_detected": _AD_ENABLED,
            }
            if scores:
                detail["anomaly_scores"] = scores
            results.append({
                "pattern_type": "hardcoded_threshold",
                "module_path": file_path,
                "function_name": scope_map.get(id(node), "<module>"),
                "line_start": node.lineno,
                "line_end": node.end_lineno,
                "pattern_detail": detail,
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
            if not (left_const or right_const):
                continue
            const_val = node.left.value if left_const else node.right.value  # type: ignore[union-attr]
            if _AD_ENABLED and abs(float(const_val)) <= _AD_MIN_CONSTANT_MAGNITUDE:
                continue
            if _AD_ENABLED and not _is_threshold_anomalous(float(const_val), population):
                continue
            binop_score = _anomaly_score(float(const_val), population) if _AD_ENABLED else 0.0
            binop_detail: dict[str, Any] = {
                "kind": "binop",
                "op": type(node.op).__name__,
                "constants": [const_val],
                "anomaly_detected": _AD_ENABLED,
            }
            if _AD_ENABLED:
                binop_detail["anomaly_score"] = round(binop_score, 3)
            results.append({
                "pattern_type": "hardcoded_threshold",
                "module_path": file_path,
                "function_name": scope_map.get(id(node), "<module>"),
                "line_start": node.lineno,
                "line_end": node.end_lineno,
                "pattern_detail": binop_detail,
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
            if str_count >= _KEYWORD_LIST_MIN_STRINGS:
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
_CS_CMP_OPS: frozenset[str] = frozenset({"<", ">", "<=", ">=", "==", "!="})

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

# ── Java regex fallback constants ─────────────────────────────────────────────
_JAVA_RE_IF_INDENT      = re.compile(r'^(\s*)(?:else\s+)?if\s*\(')
_JAVA_RE_PATTERN_ANN    = re.compile(r'@Pattern\s*\(')
_JAVA_RE_PATTERN_COMPILE = re.compile(r'\bPattern\.compile\s*\(')
_JAVA_RE_STRING_MATCHES = re.compile(r'\.matches\s*\(\s*"')
# view return: any string literal return that is NOT a redirect/forward/error
_JAVA_RE_VIEW_RETURN    = re.compile(r'\breturn\s+"(?!redirect:|forward:|error)([a-zA-Z0-9_/\-.]+)"\s*;')
_JAVA_RE_MODEL_VIEW     = re.compile(r'\bnew\s+ModelAndView\s*\(')
_JAVA_RE_ADD_ATTR       = re.compile(r'\bmodel\.addAttribute\s*\(|\bmodel\.put\s*\(')
_JAVA_RE_SCHEDULED      = re.compile(r'@Scheduled\s*\(')
_JAVA_RE_PAGEREQUEST    = re.compile(r'PageRequest\.of\s*\([^,)]+,\s*(\d+)\s*\)')
_JAVA_RE_FINDBY_KEYWORD = re.compile(r'\b(findBy\w+(?:StartingWith|Containing|Like))\s*\(')
_JAVA_RE_EQUALS_IC      = re.compile(r'\.equalsIgnoreCase\s*\(')
_JAVA_RE_STREAM_FILTER  = re.compile(r'\.stream\s*\(\s*\)\s*\.\s*filter\s*\(')
_JAVA_RE_SWITCH         = re.compile(r'\bswitch\s*\(')
_JAVA_RE_CASE           = re.compile(r'^\s*case\s+\S')
_JAVA_RE_MODEL_ATTR     = re.compile(r'@ModelAttribute\b')
_JAVA_RE_REPO_FIND      = re.compile(r'\.\s*find(?:All|By\w+)\s*\(')
_JAVA_MIN_IF_DEPTH: int = int(_cfg.get("java_min_if_depth", 2))
_JAVA_STATIC_INT_MAX_DIGITS: int = max(1, int(_cfg.get("java_static_int_max_digits", 4)))
_JAVA_STATIC_INT_MIN_VALUE: int = int(_cfg.get("java_static_int_min_value", 1))
_JAVA_RE_STATIC_INT: re.Pattern = re.compile(
    r'(?:private|protected|public)?\s+(?:static\s+)?(?:final\s+)?int\s+\w+\s*=\s*'
    rf'(\d{{1,{_JAVA_STATIC_INT_MAX_DIGITS}}})\s*;'
)


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


def _cs_collect_all_numeric_thresholds_ts(root: Any, src: bytes) -> list[float]:
    """Collect every numeric literal from a C# tree-sitter tree."""
    values: list[float] = []
    stack: list[Any] = [root]
    while stack:
        node = stack.pop()
        if node.type in ("integer_literal", "real_literal"):
            text = src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            try:
                values.append(float(text.rstrip("fFdDmMuUlL")))
            except ValueError:
                pass
        for child in node.children:
            stack.append(child)
    return values


def _cs_detect_hardcoded_threshold_ts(fp: str, root: Any, src: bytes) -> list[dict]:
    results: list[dict] = []
    population: list[float] = (
        _cs_collect_all_numeric_thresholds_ts(root, src) if _AD_ENABLED else []
    )
    for node, _, scope in _cs_walk_scoped(root, src):
        if node.type != "binary_expression":
            continue
        op_token: str | None = None
        for child in node.children:
            if not child.is_named:
                text = src[child.start_byte:child.end_byte].decode(
                    "utf-8", errors="replace"
                ).strip()
                if text in _CS_CMP_OPS:
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
        if not numeric_literals:
            continue
        cs_scores: dict[str, float] = {}
        if _AD_ENABLED:
            numeric_floats: list[float] = []
            for lit in numeric_literals:
                try:
                    numeric_floats.append(float(lit.rstrip("fFdDmMuUlL")))
                except ValueError:
                    pass
            flaggable_floats = [v for v in numeric_floats if abs(v) > _AD_MIN_CONSTANT_MAGNITUDE]
            if not flaggable_floats or not any(
                _is_threshold_anomalous(v, population) for v in flaggable_floats
            ):
                continue
            cs_scores = {str(v): round(_anomaly_score(v, population), 3) for v in flaggable_floats}
        cs_detail: dict[str, Any] = {
            "kind": "binary_expression",
            "constants": numeric_literals,
            "anomaly_detected": _AD_ENABLED,
        }
        if cs_scores:
            cs_detail["anomaly_scores"] = cs_scores
        results.append({
            "pattern_type": "hardcoded_threshold",
            "module_path": fp,
            "function_name": scope,
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
            "pattern_detail": cs_detail,
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
    population: list[float] = _collect_numeric_from_lines(lines) if _AD_ENABLED else []

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
            try:
                const_float: float | None = float(const)
            except ValueError:
                const_float = None
            cs_rx_should_flag = (
                not _AD_ENABLED
                or const_float is None
                or (abs(const_float) > _AD_MIN_CONSTANT_MAGNITUDE
                    and _is_threshold_anomalous(const_float, population))
            )
            if cs_rx_should_flag:
                cs_rx_score = (
                    _anomaly_score(const_float, population)
                    if _AD_ENABLED and const_float is not None else 0.0
                )
                cs_rx_detail: dict[str, Any] = {
                    "kind": "binary_expression",
                    "constants": [const],
                    "anomaly_detected": _AD_ENABLED,
                }
                if _AD_ENABLED and const_float is not None:
                    cs_rx_detail["anomaly_score"] = round(cs_rx_score, 3)
                results.append({
                    "pattern_type": "hardcoded_threshold",
                    "module_path": file_path,
                    "function_name": "<unknown>",
                    "line_start": i,
                    "line_end": i,
                    "pattern_detail": cs_rx_detail,
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


# ── Java regex fallback ───────────────────────────────────────────────────────

def _java_regex_nested_ifs(file_path: str, lines: list[str]) -> list[dict]:
    """Indentation-based nested-if detection for Java source."""
    results: list[dict] = []
    if_stack: list[tuple[int, int]] = []
    reported: set[int] = set()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        m = _JAVA_RE_IF_INDENT.match(line)
        if m:
            indent = len(m.group(1).expandtabs(4))
            if_stack = [(ind, ln) for ind, ln in if_stack if ind < indent]
            if_stack.append((indent, i))
            if len(if_stack) >= _JAVA_MIN_IF_DEPTH:
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


def _java_regex_switch_cases(file_path: str, lines: list[str]) -> list[dict]:
    """Detect large switch-case blocks (≥ RULE_MIN_KEYS cases)."""
    results: list[dict] = []
    n = len(lines)
    i = 0
    while i < n:
        if _JAVA_RE_SWITCH.search(lines[i]):
            start_line = i + 1
            brace = lines[i].count("{") - lines[i].count("}")
            case_count = 0
            j = i + 1
            while j < n and (brace > 0 or j == i + 1):
                brace += lines[j].count("{") - lines[j].count("}")
                if _JAVA_RE_CASE.match(lines[j]):
                    case_count += 1
                j += 1
            if case_count >= _RULE_MIN_KEYS:
                results.append({
                    "pattern_type": "large_rule_table",
                    "module_path": file_path,
                    "function_name": "<unknown>",
                    "line_start": start_line,
                    "line_end": j,
                    "pattern_detail": {"case_count": case_count, "kind": "switch"},
                })
        i += 1
    return results


def _java_detect_db_render_chain(file_path: str, lines: list[str]) -> list[dict]:
    """Detect methods that fetch from a repository and return a view name."""
    results: list[dict] = []
    in_method = False
    method_start = 0
    brace_depth = 0
    has_find = False
    has_attr = False
    has_view_return = False

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        # Simple heuristic: track brace depth to detect method boundaries
        brace_depth += line.count("{") - line.count("}")
        if not in_method:
            # Look for method signature start (public/private return type methodName)
            if re.search(r'(?:public|private|protected)\s+\w[\w<>, ]*\s+\w+\s*\(', line):
                in_method = True
                method_start = i
                has_find = has_attr = has_view_return = False
        if in_method:
            if _JAVA_RE_REPO_FIND.search(line):
                has_find = True
            if _JAVA_RE_ADD_ATTR.search(line):
                has_attr = True
            if _JAVA_RE_VIEW_RETURN.search(line):
                has_view_return = True
            if brace_depth <= 0 and i > method_start:
                # Method ended
                if has_find and has_attr and has_view_return:
                    results.append({
                        "pattern_type": "db_render_notify_chain",
                        "module_path": file_path,
                        "function_name": "<unknown>",
                        "line_start": method_start,
                        "line_end": i,
                        "pattern_detail": {"kind": "db_fetch_template_render"},
                    })
                in_method = False
    return results


def _java_detect_via_regex(file_path: str, source_text: str) -> list[dict]:
    """Regex-based Java pattern detection for all 8 AI-ify pattern types."""
    results: list[dict] = []
    lines = source_text.splitlines()
    population: list[float] = _collect_numeric_from_lines(lines) if _AD_ENABLED else []

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue

        # regex_user_input — @Pattern annotation or Pattern.compile / String.matches
        if _JAVA_RE_PATTERN_ANN.search(line):
            m = re.search(r'regexp\s*=\s*"([^"]*)"', line)
            results.append({
                "pattern_type": "regex_user_input",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i, "line_end": i,
                "pattern_detail": {"kind": "@Pattern", "regexp": m.group(1) if m else ""},
            })
        elif _JAVA_RE_PATTERN_COMPILE.search(line):
            results.append({
                "pattern_type": "regex_user_input",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i, "line_end": i,
                "pattern_detail": {"kind": "Pattern.compile"},
            })
        elif _JAVA_RE_STRING_MATCHES.search(line):
            results.append({
                "pattern_type": "regex_user_input",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i, "line_end": i,
                "pattern_detail": {"kind": "String.matches"},
            })

        # string_template_rendering — view name return or ModelAndView
        m = _JAVA_RE_VIEW_RETURN.search(line)
        if m:
            results.append({
                "pattern_type": "string_template_rendering",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i, "line_end": i,
                "pattern_detail": {"kind": "view_return", "view": m.group(1)},
            })
        elif _JAVA_RE_MODEL_VIEW.search(line):
            results.append({
                "pattern_type": "string_template_rendering",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i, "line_end": i,
                "pattern_detail": {"kind": "ModelAndView"},
            })

        # scheduled_cron — @Scheduled annotation
        if _JAVA_RE_SCHEDULED.search(line):
            m = re.search(r'cron\s*=\s*"([^"]*)"', line)
            results.append({
                "pattern_type": "scheduled_cron",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i, "line_end": i,
                "pattern_detail": {"kind": "@Scheduled",
                                   "cron": m.group(1) if m else ""},
            })

        # hardcoded_threshold — PageRequest literal or static int constant
        m = _JAVA_RE_PAGEREQUEST.search(line)
        if m:
            # PageRequest page-size is always a business threshold — always flag.
            results.append({
                "pattern_type": "hardcoded_threshold",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i, "line_end": i,
                "pattern_detail": {"kind": "PageRequest.of", "page_size": int(m.group(1))},
            })
        else:
            m = _JAVA_RE_STATIC_INT.search(line)
            if m and int(m.group(1)) >= _JAVA_STATIC_INT_MIN_VALUE:
                val = int(m.group(1))
                java_should_flag = (
                    not _AD_ENABLED
                    or (abs(float(val)) > _AD_MIN_CONSTANT_MAGNITUDE
                        and _is_threshold_anomalous(float(val), population))
                )
                if java_should_flag:
                    java_score = (
                        _anomaly_score(float(val), population) if _AD_ENABLED else 0.0
                    )
                    java_detail: dict[str, Any] = {
                        "kind": "static_int_const",
                        "value": val,
                        "anomaly_detected": _AD_ENABLED,
                    }
                    if _AD_ENABLED:
                        java_detail["anomaly_score"] = round(java_score, 3)
                    results.append({
                        "pattern_type": "hardcoded_threshold",
                        "module_path": file_path,
                        "function_name": "<unknown>",
                        "line_start": i, "line_end": i,
                        "pattern_detail": java_detail,
                    })

        # keyword_list_search — Spring Data method or manual equalsIgnoreCase loop
        m = _JAVA_RE_FINDBY_KEYWORD.search(line)
        if m:
            results.append({
                "pattern_type": "keyword_list_search",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i, "line_end": i,
                "pattern_detail": {"method": m.group(1), "kind": "spring_data_derived"},
            })
        elif _JAVA_RE_EQUALS_IC.search(line):
            results.append({
                "pattern_type": "keyword_list_search",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i, "line_end": i,
                "pattern_detail": {"kind": "equalsIgnoreCase_loop"},
            })
        elif _JAVA_RE_STREAM_FILTER.search(line):
            results.append({
                "pattern_type": "keyword_list_search",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i, "line_end": i,
                "pattern_detail": {"kind": "stream_filter"},
            })

        # large_rule_table — @ModelAttribute returning a repository collection
        if _JAVA_RE_MODEL_ATTR.search(line):
            results.append({
                "pattern_type": "large_rule_table",
                "module_path": file_path,
                "function_name": "<unknown>",
                "line_start": i, "line_end": i,
                "pattern_detail": {"kind": "@ModelAttribute_lookup"},
            })

    results.extend(_java_regex_nested_ifs(file_path, lines))
    results.extend(_java_regex_switch_cases(file_path, lines))
    results.extend(_java_detect_db_render_chain(file_path, lines))
    return results


def _java_detect_file(file_path: str) -> list[dict]:
    """Run Java pattern detection on a single .java file."""
    try:
        source = pathlib.Path(file_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    results = _java_detect_via_regex(file_path, source)
    for hit in results:
        hit.setdefault("language", "java")
    return results


# ── Language-specific fallback walkers ────────────────────────────────────────
# Registry maps language string → single-file detector callable.
# Add a new entry here to support a new language without touching detect_patterns().

_LANG_FALLBACK_REGISTRY: dict[str, Any] = {
    "java":   _java_detect_file,
    "csharp": _cs_detect_file,
    # python is handled separately by _detect_via_ast_fallback (stdlib ast walker)
}


def _walk_files_by_language(target_path: str, language: str) -> list[str]:
    """Return all files of the given language under target_path."""
    ext_map = {v: k for k, v in _EXT_LANGUAGE.items()}
    ext = ext_map.get(language)
    if not ext:
        return []
    p = pathlib.Path(target_path)
    if p.is_file() and p.suffix.lower() == ext:
        return [str(p)]
    return [str(f) for f in p.rglob(f"*{ext}") if f.is_file()]


def _detect_via_language_fallbacks(target_path: str) -> list[dict]:
    """Run all registered language fallbacks on target_path.

    To add support for a new language, register its detector in
    _LANG_FALLBACK_REGISTRY — no changes to detect_patterns() needed.
    """
    results: list[dict] = []
    for language, detector in _LANG_FALLBACK_REGISTRY.items():
        for file_path in _walk_files_by_language(target_path, language):
            results.extend(detector(file_path))
    return results


# ── Public API ────────────────────────────────────────────────────────────────


def detect_patterns(target_path: str) -> list[dict]:
    """Detect AI-augmentable patterns in a source file or directory tree.

    Primary path  — Semgrep CLI (multi-language, YAML rules).
    Supplemental  — language-specific regex/AST fallbacks from
                    _LANG_FALLBACK_REGISTRY, always run and merged with
                    Semgrep results.  Semgrep wins on conflicts (same file +
                    line + pattern_type).  To add a new language, register
                    its detector in _LANG_FALLBACK_REGISTRY only.

    Args:
        target_path: Path to a source file or directory to analyze.

    Returns:
        List of pattern dicts with keys: pattern_type, module_path,
        function_name, line_start, line_end, language, pattern_detail.
        Returns [] on error or if no patterns are found.
    """
    semgrep_results = _detect_via_semgrep(target_path)

    if semgrep_results is None:
        # Semgrep unavailable — run all fallbacks (Python AST + registry)
        results = _detect_via_ast_fallback(target_path)
        results.extend(_detect_via_language_fallbacks(target_path))
        return results

    # Semgrep available: supplement with language fallbacks to catch idioms
    # that the Semgrep rules don't cover (e.g. Spring annotations, Spring Data
    # derived query methods, ORM fetch+render chains, …).
    supplemental = _detect_via_language_fallbacks(target_path)

    # Deduplicate: prefer Semgrep result when (file, line, pattern) collides.
    covered: set[tuple[str, int, str]] = {
        (r["module_path"], r["line_start"], r["pattern_type"])
        for r in semgrep_results
    }
    for hit in supplemental:
        key = (hit["module_path"], hit["line_start"], hit["pattern_type"])
        if key not in covered:
            semgrep_results.append(hit)
            covered.add(key)

    return semgrep_results
