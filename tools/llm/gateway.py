# [TEMPLATE: CUI // SP-CTI]
"""LLM Gateway/Proxy Layer for ICDEV™.

Provides pre-invoke and post-invoke guardrails around every LLM call:
    - Prompt injection detection (regex, air-gap safe)
    - PII scrubbing (SSN, email, phone, credit card)
    - Input/output length validation
    - Rate limiting per agent/function
    - Cost cap pre-check (integrates with token_tracker)
    - Output validation (refusal detection, hallucination flag)
    - Append-only audit trail in llm_gateway_audit

Configuration: args/llm_gateway_config.yaml
All detection is 100% local regex — no external API calls.

CLI:
    python tools/llm/gateway.py --stats --json
    python tools/llm/gateway.py --audit --json --limit 50
    python tools/llm/gateway.py --gate
    python tools/llm/gateway.py --check-text "some prompt" --json
"""

from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

import argparse
import hashlib
import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.db.storage import get_connection

try:
    import yaml
except ImportError:
    yaml = None

logger = get_logger("icdev.llm.gateway")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = BASE_DIR / "args" / "llm_gateway_config.yaml"

# ---------------------------------------------------------------------------
# Schema — append-only audit table (NIST AU)
# ---------------------------------------------------------------------------
CREATE_AUDIT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS llm_gateway_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    agent_id TEXT,
    function_name TEXT,
    model_id TEXT,
    pre_check_result TEXT NOT NULL DEFAULT 'pass',
    post_check_result TEXT,
    injection_score REAL NOT NULL DEFAULT 0.0,
    pii_detected TEXT,
    blocked_reason TEXT,
    input_hash TEXT,
    output_hash TEXT,
    input_length INTEGER NOT NULL DEFAULT 0,
    output_length INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
"""

CREATE_RATE_LIMIT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS llm_gateway_rate_limits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    function_name TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_type TEXT NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 1,
    UNIQUE(agent_id, function_name, window_start, window_type)
);
"""


def _ensure_tables(conn) -> None:
    """Create gateway tables if they don't exist."""
    conn.execute(CREATE_AUDIT_TABLE_SQL)
    conn.execute(CREATE_RATE_LIMIT_TABLE_SQL)
    conn.commit()


# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------
_config_cache: Optional[Dict] = None
_config_cache_time: float = 0.0
_CONFIG_CACHE_TTL: float = 300.0  # 5 minutes


def load_config(config_path: Optional[Path] = None, force: bool = False) -> Dict:
    """Load gateway config from YAML, with caching."""
    global _config_cache, _config_cache_time

    if not force and _config_cache and (time.time() - _config_cache_time) < _CONFIG_CACHE_TTL:
        return _config_cache

    path = config_path or CONFIG_PATH
    if yaml is None:
        logger.warning("PyYAML not installed; using defaults")
        _config_cache = _default_config()
        _config_cache_time = time.time()
        return _config_cache

    if not path.exists():
        logger.warning("Config not found at %s; using defaults", path)
        _config_cache = _default_config()
        _config_cache_time = time.time()
        return _config_cache

    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}

    _config_cache = cfg
    _config_cache_time = time.time()
    return _config_cache


def _default_config() -> Dict:
    """Minimal default config when YAML is unavailable."""
    return {
        "enabled": True,
        "pre_invoke": {
            "injection_detection": {"enabled": True, "block_threshold": 0.7, "patterns": []},
            "pii_detection": {"enabled": True, "action": "warn", "patterns": []},
            "input_length": {"enabled": True, "max_tokens": 128000, "chars_per_token": 4},
            "rate_limiting": {
                "enabled": True,
                "default": {"per_minute": 30, "per_hour": 500},
                "functions": {},
            },
            "cost_cap": {"enabled": True},
        },
        "post_invoke": {
            "output_validation": {
                "enabled": True,
                "refusal_patterns": [],
                "min_response_length": 1,
            },
            "hallucination_detection": {"enabled": True, "confidence_threshold": 0.3},
            "output_pii_detection": {"enabled": True, "action": "warn"},
            "response_length": {"enabled": True, "max_tokens": 32000, "chars_per_token": 4},
        },
        "audit": {"enabled": True, "retention_days": 0, "log_prompt_text": False, "log_response_text": False},
        "air_gap": True,
    }


# ---------------------------------------------------------------------------
# Compiled pattern cache
# ---------------------------------------------------------------------------
_compiled_injection: Optional[List] = None
_compiled_pii: Optional[List] = None


def _get_injection_patterns(cfg: Dict) -> List[Dict]:
    """Compile and cache injection detection patterns."""
    global _compiled_injection
    if _compiled_injection is not None:
        return _compiled_injection

    raw = cfg.get("pre_invoke", {}).get("injection_detection", {}).get("patterns", [])
    compiled = []
    for entry in raw:
        try:
            compiled.append(
                {
                    "regex": re.compile(entry["pattern"], re.IGNORECASE),
                    "weight": float(entry.get("weight", 0.5)),
                    "label": entry.get("label", "unknown"),
                }
            )
        except re.error as exc:
            logger.warning("Invalid injection pattern %r: %s", entry.get("pattern"), exc)
    _compiled_injection = compiled
    return _compiled_injection


def _get_pii_patterns(cfg: Dict) -> List[Dict]:
    """Compile and cache PII detection patterns."""
    global _compiled_pii
    if _compiled_pii is not None:
        return _compiled_pii

    raw = cfg.get("pre_invoke", {}).get("pii_detection", {}).get("patterns", [])
    compiled = []
    for entry in raw:
        try:
            compiled.append(
                {
                    "regex": re.compile(entry["pattern"]),
                    "label": entry.get("label", "unknown"),
                    "description": entry.get("description", ""),
                }
            )
        except re.error as exc:
            logger.warning("Invalid PII pattern %r: %s", entry.get("pattern"), exc)
    _compiled_pii = compiled
    return _compiled_pii


def _invalidate_pattern_cache() -> None:
    """Clear compiled pattern caches (e.g. after config reload)."""
    global _compiled_injection, _compiled_pii
    _compiled_injection = None
    _compiled_pii = None


# ---------------------------------------------------------------------------
# Pre-invoke checks
# ---------------------------------------------------------------------------
def _check_injection(text: str, cfg: Dict) -> Dict:
    """Score text for prompt injection. Returns {score, matches}."""
    if not cfg.get("pre_invoke", {}).get("injection_detection", {}).get("enabled", True):
        return {"score": 0.0, "matches": []}

    patterns = _get_injection_patterns(cfg)
    if not patterns:
        return {"score": 0.0, "matches": []}

    matches = []
    max_score = 0.0
    for pat in patterns:
        if pat["regex"].search(text):
            matches.append(pat["label"])
            max_score = max(max_score, pat["weight"])

    return {"score": max_score, "matches": matches}


def _check_pii(text: str, cfg: Dict, source: str = "input") -> Dict:
    """Detect PII patterns in text. Returns {detected, labels}."""
    section = "pii_detection" if source == "input" else "output_pii_detection"
    parent = "pre_invoke" if source == "input" else "post_invoke"

    if not cfg.get(parent, {}).get(section, {}).get("enabled", True):
        return {"detected": False, "labels": []}

    patterns = _get_pii_patterns(cfg)
    labels = []
    for pat in patterns:
        if pat["regex"].search(text):
            labels.append(pat["label"])

    return {"detected": bool(labels), "labels": labels}


def _check_input_length(text: str, cfg: Dict) -> Dict:
    """Validate input length against configured max."""
    length_cfg = cfg.get("pre_invoke", {}).get("input_length", {})
    if not length_cfg.get("enabled", True):
        return {"passed": True, "estimated_tokens": 0, "max_tokens": 0}

    chars_per_token = length_cfg.get("chars_per_token", 4)
    max_tokens = length_cfg.get("max_tokens", 128000)
    estimated = len(text) // max(chars_per_token, 1)

    return {
        "passed": estimated <= max_tokens,
        "estimated_tokens": estimated,
        "max_tokens": max_tokens,
    }


def _check_rate_limit(agent_id: str, function_name: str, cfg: Dict) -> Dict:
    """Check per-agent/function rate limits. Returns {allowed, reason}."""
    rl_cfg = cfg.get("pre_invoke", {}).get("rate_limiting", {})
    if not rl_cfg.get("enabled", True) or not agent_id:
        return {"allowed": True, "reason": None}

    # Get limits for this function
    func_limits = rl_cfg.get("functions", {}).get(function_name, rl_cfg.get("default", {}))
    per_minute = func_limits.get("per_minute", 30)
    per_hour = func_limits.get("per_hour", 500)

    now = datetime.now(timezone.utc)
    minute_window = now.strftime("%Y-%m-%dT%H:%M")
    hour_window = now.strftime("%Y-%m-%dT%H")

    conn = get_connection()
    try:
        _ensure_tables(conn)

        # Check minute window
        row = conn.execute(
            "SELECT request_count FROM llm_gateway_rate_limits "
            "WHERE agent_id = %s AND function_name = %s AND window_start = %s AND window_type = %s",
            (agent_id, function_name, minute_window, "minute"),
        ).fetchone()
        minute_count = row[0] if row else 0

        if minute_count >= per_minute:
            return {"allowed": False, "reason": f"Rate limit exceeded: {minute_count}/{per_minute} per minute"}

        # Check hour window
        row = conn.execute(
            "SELECT request_count FROM llm_gateway_rate_limits "
            "WHERE agent_id = %s AND function_name = %s AND window_start = %s AND window_type = %s",
            (agent_id, function_name, hour_window, "hour"),
        ).fetchone()
        hour_count = row[0] if row else 0

        if hour_count >= per_hour:
            return {"allowed": False, "reason": f"Rate limit exceeded: {hour_count}/{per_hour} per hour"}

        # Increment counters
        conn.execute(
            "INSERT INTO llm_gateway_rate_limits (agent_id, function_name, window_start, window_type, request_count) "
            "VALUES (%s, %s, %s, %s, 1) "
            "ON CONFLICT(agent_id, function_name, window_start, window_type) "
            "DO UPDATE SET request_count = request_count + 1",
            (agent_id, function_name, minute_window, "minute"),
        )
        conn.execute(
            "INSERT INTO llm_gateway_rate_limits (agent_id, function_name, window_start, window_type, request_count) "
            "VALUES (%s, %s, %s, %s, 1) "
            "ON CONFLICT(agent_id, function_name, window_start, window_type) "
            "DO UPDATE SET request_count = request_count + 1",
            (agent_id, function_name, hour_window, "hour"),
        )
        conn.commit()
    finally:
        conn.close()

    return {"allowed": True, "reason": None}


def _cost_cap_fail_open() -> bool:
    """True when the operator has explicitly opted back into legacy fail-open."""
    import os

    return os.getenv("ICDEV_COST_CAP_FAIL_OPEN", "").strip().lower() in ("1", "true", "yes")


def _check_cost_cap(agent_id: str, cfg: Dict) -> Dict:
    """Pre-check cost budget via token_tracker. Returns {allowed, reason}.

    Fail-closed (exa-policy-06): when the cost cap is enabled but the budget
    backend raises, the request is DENIED. Previously any exception was
    swallowed and the call returned ``allowed: True``, so a DB outage or a
    corrupt ``token_budgets`` block silently lifted every spend cap — exactly
    when you can least afford it.

    Two states remain "allowed" by design and are not errors:
      * the cap is switched off in ``llm_gateway_config.yaml``;
      * ``token_tracker`` is not installed at all (ImportError) — documented in
        that config as "ignored gracefully", i.e. a deployment that never ships
        the budget module, not one whose module is broken.

    ``ICDEV_COST_CAP_FAIL_OPEN=1`` restores the legacy behaviour for a staged
    rollout; it logs at ERROR on every use so it cannot be left on quietly.
    """
    if not cfg.get("pre_invoke", {}).get("cost_cap", {}).get("enabled", True):
        return {"allowed": True, "reason": None}

    if not agent_id:
        return {"allowed": True, "reason": None}

    try:
        from tools.agent.token_tracker import check_budget
    except ImportError:
        return {"allowed": True, "reason": None}  # budget module not shipped

    try:
        budget = check_budget(agent_id)
    except Exception as exc:
        if _cost_cap_fail_open():
            logger.error(
                "Cost cap FAILING OPEN for %s (ICDEV_COST_CAP_FAIL_OPEN=1) — check failed: %s",
                agent_id, exc,
            )
            return {"allowed": True, "reason": f"Cost cap unenforceable (fail-open override): {exc}"}
        logger.error(
            "Cost cap check failed for %s — denying (fail-closed): %s", agent_id, exc,
        )
        return {
            "allowed": False,
            "reason": f"Cost cap unenforceable for agent {agent_id} ({type(exc).__name__}: {exc}) — denied fail-closed",
        }

    if budget["action"] == "block":
        return {"allowed": False, "reason": f"Budget exceeded for agent {agent_id}: {budget.get('message', '')}"}
    if budget["action"] == "warn":
        return {"allowed": True, "reason": f"Budget warning: {budget.get('message', '')}"}

    return {"allowed": True, "reason": None}


def pre_invoke_check(request: Dict, cfg: Optional[Dict] = None) -> Dict:
    """Run all pre-invoke guardrails.

    Args:
        request: Dict with keys: prompt (str), agent_id (str|None),
                 function_name (str|None), model_id (str|None)
        cfg: Optional config override (defaults to loaded YAML)

    Returns:
        {allowed: bool, warnings: list[str], blocked_reason: str|None,
         injection_score: float, pii_labels: list[str], request_id: str}
    """
    if cfg is None:
        cfg = load_config()

    if not cfg.get("enabled", True):
        return {
            "allowed": True,
            "warnings": [],
            "blocked_reason": None,
            "injection_score": 0.0,
            "pii_labels": [],
            "request_id": f"gw_{uuid.uuid4().hex[:12]}",
        }

    request_id = f"gw_{uuid.uuid4().hex[:12]}"
    prompt = request.get("prompt", "")
    agent_id = request.get("agent_id", "")
    function_name = request.get("function_name", "")

    warnings: List[str] = []
    blocked_reason: Optional[str] = None

    # 1. Injection detection
    injection = _check_injection(prompt, cfg)
    injection_score = injection["score"]
    threshold = cfg.get("pre_invoke", {}).get("injection_detection", {}).get("block_threshold", 0.7)

    if injection_score >= threshold:
        blocked_reason = f"Prompt injection detected (score={injection_score:.2f}, patterns={injection['matches']})"
    elif injection["matches"]:
        warnings.append(f"Injection patterns detected (score={injection_score:.2f}): {injection['matches']}")

    # 2. PII detection
    pii = _check_pii(prompt, cfg, source="input")
    pii_action = cfg.get("pre_invoke", {}).get("pii_detection", {}).get("action", "warn")

    if pii["detected"]:
        if pii_action == "block" and not blocked_reason:
            blocked_reason = f"PII detected in input: {pii['labels']}"
        elif pii_action == "warn":
            warnings.append(f"PII detected in input: {pii['labels']}")
        # redact action would be handled by caller

    # 3. Input length
    length_check = _check_input_length(prompt, cfg)
    if not length_check["passed"] and not blocked_reason:
        blocked_reason = (
            f"Input too long: ~{length_check['estimated_tokens']} tokens (max {length_check['max_tokens']})"
        )

    # 4. Rate limiting
    rate = _check_rate_limit(agent_id, function_name, cfg)
    if not rate["allowed"] and not blocked_reason:
        blocked_reason = rate["reason"]
    elif rate.get("reason"):
        warnings.append(rate["reason"])

    # 5. Cost cap
    cost = _check_cost_cap(agent_id, cfg)
    if not cost["allowed"] and not blocked_reason:
        blocked_reason = cost["reason"]
    elif cost.get("reason"):
        warnings.append(cost["reason"])

    allowed = blocked_reason is None

    return {
        "allowed": allowed,
        "warnings": warnings,
        "blocked_reason": blocked_reason,
        "injection_score": injection_score,
        "pii_labels": pii.get("labels", []),
        "request_id": request_id,
    }


# ---------------------------------------------------------------------------
# Post-invoke checks
# ---------------------------------------------------------------------------
def _check_refusal(text: str, cfg: Dict) -> Dict:
    """Detect refusal patterns in LLM output."""
    val_cfg = cfg.get("post_invoke", {}).get("output_validation", {})
    if not val_cfg.get("enabled", True):
        return {"is_refusal": False, "pattern": None}

    for pattern in val_cfg.get("refusal_patterns", []):
        if pattern.lower() in text.lower():
            return {"is_refusal": True, "pattern": pattern}

    min_len = val_cfg.get("min_response_length", 1)
    if min_len > 0 and len(text.strip()) < min_len:
        return {"is_refusal": True, "pattern": "empty_response"}

    return {"is_refusal": False, "pattern": None}


def _check_hallucination(response_meta: Dict, cfg: Dict) -> Dict:
    """Flag potential hallucination based on confidence metadata."""
    hal_cfg = cfg.get("post_invoke", {}).get("hallucination_detection", {})
    if not hal_cfg.get("enabled", True):
        return {"flagged": False, "confidence": None}

    confidence = response_meta.get("confidence")
    if confidence is None:
        return {"flagged": False, "confidence": None}

    threshold = hal_cfg.get("confidence_threshold", 0.3)
    return {
        "flagged": confidence < threshold,
        "confidence": confidence,
    }


def _check_output_length(text: str, cfg: Dict) -> Dict:
    """Validate response length."""
    len_cfg = cfg.get("post_invoke", {}).get("response_length", {})
    if not len_cfg.get("enabled", True):
        return {"passed": True, "estimated_tokens": 0}

    chars_per_token = len_cfg.get("chars_per_token", 4)
    max_tokens = len_cfg.get("max_tokens", 32000)
    estimated = len(text) // max(chars_per_token, 1)

    return {
        "passed": estimated <= max_tokens,
        "estimated_tokens": estimated,
        "max_tokens": max_tokens,
    }


def post_invoke_check(response: Dict, cfg: Optional[Dict] = None) -> Dict:
    """Run all post-invoke guardrails.

    Args:
        response: Dict with keys: text (str), metadata (dict|None)
        cfg: Optional config override

    Returns:
        {passed: bool, warnings: list[str], flagged_issues: list[str]}
    """
    if cfg is None:
        cfg = load_config()

    if not cfg.get("enabled", True):
        return {"passed": True, "warnings": [], "flagged_issues": []}

    text = response.get("text", "")
    metadata = response.get("metadata", {}) or {}
    warnings: List[str] = []
    flagged: List[str] = []

    # 1. Refusal detection
    refusal = _check_refusal(text, cfg)
    if refusal["is_refusal"]:
        flagged.append(f"Refusal detected: {refusal['pattern']}")

    # 2. Hallucination flag
    hal = _check_hallucination(metadata, cfg)
    if hal["flagged"]:
        flagged.append(f"Low confidence: {hal['confidence']}")

    # 3. Output PII detection
    pii = _check_pii(text, cfg, source="output")
    if pii["detected"]:
        action = cfg.get("post_invoke", {}).get("output_pii_detection", {}).get("action", "warn")
        if action == "flag":
            flagged.append(f"PII in output: {pii['labels']}")
        else:
            warnings.append(f"PII in output: {pii['labels']}")

    # 4. Output length
    length = _check_output_length(text, cfg)
    if not length["passed"]:
        warnings.append(
            f"Response too long: ~{length['estimated_tokens']} tokens (max {length.get('max_tokens', '?')})"
        )

    passed = len(flagged) == 0
    return {"passed": passed, "warnings": warnings, "flagged_issues": flagged}


# ---------------------------------------------------------------------------
# Audit trail (append-only)
# ---------------------------------------------------------------------------
def record_audit(
    request_id: str,
    agent_id: Optional[str],
    function_name: Optional[str],
    model_id: Optional[str],
    pre_result: str,
    post_result: Optional[str],
    injection_score: float,
    pii_labels: List[str],
    blocked_reason: Optional[str],
    input_text: str,
    output_text: str,
    latency_ms: int,
    cfg: Optional[Dict] = None,
) -> None:
    """Write an append-only audit record."""
    if cfg is None:
        cfg = load_config()

    if not cfg.get("audit", {}).get("enabled", True):
        return

    input_hash = hashlib.sha256(input_text.encode("utf-8")).hexdigest()[:16] if input_text else None
    output_hash = hashlib.sha256(output_text.encode("utf-8")).hexdigest()[:16] if output_text else None
    pii_str = json.dumps(pii_labels) if pii_labels else None
    now = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    try:
        _ensure_tables(conn)
        conn.execute(
            "INSERT INTO llm_gateway_audit "
            "(request_id, agent_id, function_name, model_id, "
            "pre_check_result, post_check_result, injection_score, "
            "pii_detected, blocked_reason, input_hash, output_hash, "
            "input_length, output_length, latency_ms, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                request_id,
                agent_id,
                function_name,
                model_id,
                pre_result,
                post_result,
                injection_score,
                pii_str,
                blocked_reason,
                input_hash,
                output_hash,
                len(input_text),
                len(output_text),
                latency_ms,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_audit_log(filters: Optional[Dict] = None, limit: int = 50) -> List[Dict]:
    """Query the audit trail with optional filters.

    Args:
        filters: Dict with optional keys: agent_id, function_name,
                 pre_check_result, since (ISO timestamp)
        limit: Max rows to return

    Returns:
        List of audit records as dicts.
    """
    filters = filters or {}
    conn = get_connection()
    try:
        _ensure_tables(conn)
        clauses = []
        params: List[Any] = []

        if filters.get("agent_id"):
            clauses.append("agent_id = ?")
            params.append(filters["agent_id"])
        if filters.get("function_name"):
            clauses.append("function_name = ?")
            params.append(filters["function_name"])
        if filters.get("pre_check_result"):
            clauses.append("pre_check_result = ?")
            params.append(filters["pre_check_result"])
        if filters.get("since"):
            clauses.append("created_at >= ?")
            params.append(filters["since"])

        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)

        rows = conn.execute(
            f"SELECT * FROM llm_gateway_audit{where} ORDER BY id DESC LIMIT %s",  # nosec B608
            params,
        ).fetchall()

        # Convert rows to dicts
        result = []
        for row in rows:
            if hasattr(row, "keys"):
                result.append(dict(row))
            else:
                cols = [
                    "id",
                    "request_id",
                    "agent_id",
                    "function_name",
                    "model_id",
                    "pre_check_result",
                    "post_check_result",
                    "injection_score",
                    "pii_detected",
                    "blocked_reason",
                    "input_hash",
                    "output_hash",
                    "input_length",
                    "output_length",
                    "latency_ms",
                    "created_at",
                ]
                result.append(dict(zip(cols, row)))
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def get_gateway_stats() -> Dict:
    """Compute summary statistics from the audit trail."""
    conn = get_connection()
    try:
        _ensure_tables(conn)

        total = conn.execute("SELECT COUNT(*) FROM llm_gateway_audit").fetchone()[0]
        blocked = conn.execute("SELECT COUNT(*) FROM llm_gateway_audit WHERE pre_check_result = 'block'").fetchone()[0]
        warned = conn.execute("SELECT COUNT(*) FROM llm_gateway_audit WHERE pre_check_result = 'warn'").fetchone()[0]
        passed = conn.execute("SELECT COUNT(*) FROM llm_gateway_audit WHERE pre_check_result = 'pass'").fetchone()[0]
        post_flagged = conn.execute(
            "SELECT COUNT(*) FROM llm_gateway_audit WHERE post_check_result = 'flagged'"
        ).fetchone()[0]
        pii_count = conn.execute("SELECT COUNT(*) FROM llm_gateway_audit WHERE pii_detected IS NOT NULL").fetchone()[0]
        avg_latency_row = conn.execute("SELECT AVG(latency_ms) FROM llm_gateway_audit WHERE latency_ms > 0").fetchone()
        avg_latency = round(avg_latency_row[0], 1) if avg_latency_row and avg_latency_row[0] else 0.0

        # Top blocked functions
        top_blocked_rows = conn.execute(
            "SELECT function_name, COUNT(*) as cnt FROM llm_gateway_audit "
            "WHERE pre_check_result = 'block' AND function_name IS NOT NULL "
            "GROUP BY function_name ORDER BY cnt DESC LIMIT 5"
        ).fetchall()
        top_blocked = {row[0]: row[1] for row in top_blocked_rows}

        # Recent injection scores > 0
        high_injection = conn.execute("SELECT COUNT(*) FROM llm_gateway_audit WHERE injection_score > 0").fetchone()[0]

        return {
            "total_requests": total,
            "blocked": blocked,
            "warned": warned,
            "passed": passed,
            "post_flagged": post_flagged,
            "pii_detections": pii_count,
            "injection_triggers": high_injection,
            "avg_latency_ms": avg_latency,
            "top_blocked_functions": top_blocked,
            "status": "healthy",
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Gate check
# ---------------------------------------------------------------------------
def gate_check() -> Dict:
    """Run a health/gate check. Returns {passed: bool, issues: list}."""
    issues = []

    # 1. Config loadable
    try:
        cfg = load_config(force=True)
        _invalidate_pattern_cache()
    except Exception as exc:
        issues.append(f"Config load failed: {exc}")
        return {"passed": False, "issues": issues}

    if not cfg.get("enabled", True):
        return {"passed": True, "issues": ["Gateway disabled — no checks active"]}

    # 2. DB accessible
    try:
        conn = get_connection()
        _ensure_tables(conn)
        conn.close()
    except Exception as exc:
        issues.append(f"Database error: {exc}")

    # 3. Injection patterns compile
    try:
        patterns = _get_injection_patterns(cfg)
        if not patterns:
            issues.append("No injection detection patterns configured")
    except Exception as exc:
        issues.append(f"Injection pattern compilation failed: {exc}")

    # 4. PII patterns compile
    try:
        pii_patterns = _get_pii_patterns(cfg)
        if not pii_patterns:
            issues.append("No PII detection patterns configured")
    except Exception as exc:
        issues.append(f"PII pattern compilation failed: {exc}")

    # 5. Air-gap mode
    if not cfg.get("air_gap", True):
        issues.append("Air-gap mode disabled — external calls may occur")

    passed = all("failed" not in i.lower() and "error" not in i.lower() for i in issues)

    return {"passed": passed, "issues": issues}


# ---------------------------------------------------------------------------
# Dry-run text check (CLI helper)
# ---------------------------------------------------------------------------
def check_text(text: str) -> Dict:
    """Dry-run pre-invoke check on arbitrary text. Does not record audit."""
    cfg = load_config()
    result = pre_invoke_check({"prompt": text, "agent_id": "", "function_name": ""}, cfg)
    # Also run post-invoke on same text for completeness
    post = post_invoke_check({"text": text, "metadata": {}}, cfg)
    return {
        "pre_invoke": result,
        "post_invoke": post,
        "text_length": len(text),
        "estimated_tokens": len(text) // 4,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ LLM Gateway — pre/post-invoke guardrails and audit")
    parser.add_argument("--stats", action="store_true", help="Show gateway statistics")
    parser.add_argument("--audit", action="store_true", help="Show recent audit log")
    parser.add_argument("--limit", type=int, default=50, help="Limit audit rows (default 50)")
    parser.add_argument("--gate", action="store_true", help="Run gate check (exit 0=pass, 1=fail)")
    parser.add_argument("--check-text", type=str, help="Dry-run check on provided text")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Output as JSON")
    args = parser.parse_args()

    if args.gate:
        result = gate_check()
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            status = "PASS" if result["passed"] else "FAIL"
            print(f"Gate: {status}")
            for issue in result.get("issues", []):
                print(f"  - {issue}")
        sys.exit(0 if result["passed"] else 1)

    if args.check_text:
        result = check_text(args.check_text)
        if args.json_output:
            print(json.dumps(result, indent=2))
        else:
            pre = result["pre_invoke"]
            print(f"Allowed: {pre['allowed']}")
            if pre["blocked_reason"]:
                print(f"Blocked: {pre['blocked_reason']}")
            if pre["warnings"]:
                for w in pre["warnings"]:
                    print(f"Warning: {w}")
            if pre["injection_score"] > 0:
                print(f"Injection score: {pre['injection_score']:.2f}")
            if pre["pii_labels"]:
                print(f"PII detected: {pre['pii_labels']}")
            post = result["post_invoke"]
            if post["flagged_issues"]:
                for f in post["flagged_issues"]:
                    print(f"Flagged: {f}")
        sys.exit(0)

    if args.audit:
        rows = get_audit_log(limit=args.limit)
        if args.json_output:
            print(json.dumps(rows, indent=2, default=str))
        else:
            if not rows:
                print("No audit records found.")
            else:
                for row in rows:
                    print(
                        f"[{row.get('created_at', '?')}] "
                        f"{row.get('request_id', '?')} "
                        f"agent={row.get('agent_id', '-')} "
                        f"fn={row.get('function_name', '-')} "
                        f"pre={row.get('pre_check_result', '-')} "
                        f"post={row.get('post_check_result', '-')} "
                        f"inj={row.get('injection_score', 0):.2f}"
                    )
        sys.exit(0)

    if args.stats:
        stats = get_gateway_stats()
        if args.json_output:
            print(json.dumps(stats, indent=2))
        else:
            print("LLM Gateway Statistics")
            print(f"  Total requests:    {stats['total_requests']}")
            print(f"  Passed:            {stats['passed']}")
            print(f"  Warned:            {stats['warned']}")
            print(f"  Blocked:           {stats['blocked']}")
            print(f"  Post-flagged:      {stats['post_flagged']}")
            print(f"  PII detections:    {stats['pii_detections']}")
            print(f"  Injection triggers:{stats['injection_triggers']}")
            print(f"  Avg latency (ms):  {stats['avg_latency_ms']}")
            if stats["top_blocked_functions"]:
                print("  Top blocked functions:")
                for fn, cnt in stats["top_blocked_functions"].items():
                    print(f"    {fn}: {cnt}")
        sys.exit(0)

    # Default: show help
    parser.print_help()
    sys.exit(0)


if __name__ == "__main__":
    main()
