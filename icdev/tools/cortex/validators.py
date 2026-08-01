# CUI // SP-CTI
"""Shared request validation for the Cortex surfaces (REST v1 + MCP).

One schema source. The REST blueprint (``tools/cortex/blueprint.py``) and the
MCP tool family (``tools/mcp/cortex_server.py``, ctx-expose-01) both accept the
same operation parameters, so the per-operation coercion/validation lives here
instead of being duplicated at each entry point.

Each ``validate_*`` takes the raw request payload (a plain dict) and returns a
clean kwargs dict for the matching :mod:`tools.cortex.api` facade function.
Identity/policy fields (``tenant_id``, ``user_id``, ``classification``) are
NEVER read here: those are derived server-side from the authenticated session,
never from client input. ``domain`` is the one context field a caller may set,
because it only narrows backend selection — it cannot widen access. It is
returned alongside the operation params so the caller can thread it into the
server-built :class:`~tools.cortex.schemas.CortexContext`.

Raises :class:`CortexValidationError` (a ``ValueError``) on any malformed input
so entry points can map it uniformly to HTTP 400 / an MCP error.
"""
from __future__ import annotations

from typing import Any, Optional

from .search_service import CORTEX_STRATEGIES

# Analyst answer modes (mirrors analyst._VALID_MODES).
ASK_MODES = ("auto", "iqe", "nlq")

# Bounds kept deliberately generous — these guard against abuse/typos, not
# against policy (policy is the governance pipeline's job).
_TOP_K_MIN, _TOP_K_MAX = 1, 50
_MAX_TOKENS_MAX = 32000
_TEMPERATURE_MIN, _TEMPERATURE_MAX = 0.0, 2.0


class CortexValidationError(ValueError):
    """Raised when a Cortex request payload fails validation."""


# ---------------------------------------------------------------------------
# Primitive coercion helpers
# ---------------------------------------------------------------------------
def _require_dict(data: Any) -> dict:
    if data is None:
        raise CortexValidationError("request body must be a JSON object")
    if not isinstance(data, dict):
        raise CortexValidationError("request body must be a JSON object")
    return data


def _req_str(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CortexValidationError(f"{key!r} is required and must be a non-empty string")
    return value.strip()


def _opt_str(data: dict, key: str, default: str = "") -> str:
    value = data.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise CortexValidationError(f"{key!r} must be a string")
    return value


def _opt_bool(data: dict, key: str, default: bool = False) -> bool:
    value = data.get(key, default)
    if isinstance(value, bool):
        return value
    raise CortexValidationError(f"{key!r} must be a boolean")


def _opt_int(data: dict, key: str, default: int, lo: int, hi: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CortexValidationError(f"{key!r} must be an integer")
    if not (lo <= value <= hi):
        raise CortexValidationError(f"{key!r} must be between {lo} and {hi}")
    return value


def _str_list(data: dict, key: str, *, required: bool) -> Optional[list]:
    value = data.get(key)
    if value is None:
        if required:
            raise CortexValidationError(f"{key!r} is required and must be a non-empty list of strings")
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise CortexValidationError(f"{key!r} must be a list of strings")
    cleaned = [v.strip() for v in value if v.strip()]
    if required and not cleaned:
        raise CortexValidationError(f"{key!r} must contain at least one non-empty string")
    return cleaned


def domain_of(data: Any) -> str:
    """The client-supplied ``domain`` scope (safe to honor), or ''."""
    data = _require_dict(data)
    return _opt_str(data, "domain", "")


# ---------------------------------------------------------------------------
# Per-operation validators — return facade kwargs (no identity fields)
# ---------------------------------------------------------------------------
def validate_search(data: Any) -> dict:
    data = _require_dict(data)
    strategy = (_opt_str(data, "strategy", "auto") or "auto").lower()
    if strategy not in CORTEX_STRATEGIES:
        raise CortexValidationError(
            f"strategy must be one of {CORTEX_STRATEGIES}"
        )
    return {
        "query": _req_str(data, "query"),
        "top_k": _opt_int(data, "top_k", 5, _TOP_K_MIN, _TOP_K_MAX),
        "strategy": strategy,
    }


def validate_ask(data: Any) -> dict:
    data = _require_dict(data)
    mode = (_opt_str(data, "mode", "auto") or "auto").lower()
    if mode not in ASK_MODES:
        raise CortexValidationError(f"mode must be one of {ASK_MODES}")
    canvas = _opt_str(data, "canvas", "") or None
    return {
        "question": _req_str(data, "question"),
        "mode": mode,
        "canvas": canvas,
        "collections": _str_list(data, "collections", required=False),
        "summarize": _opt_bool(data, "summarize", False),
    }


def validate_complete(data: Any) -> dict:
    data = _require_dict(data)
    out: dict = {
        "prompt": _req_str(data, "prompt"),
        "system_prompt": _opt_str(data, "system_prompt", ""),
    }
    if data.get("max_tokens") is not None:
        out["max_tokens"] = _opt_int(data, "max_tokens", 1, 1, _MAX_TOKENS_MAX)
    if data.get("temperature") is not None:
        temp = data.get("temperature")
        if isinstance(temp, bool) or not isinstance(temp, (int, float)):
            raise CortexValidationError("'temperature' must be a number")
        if not (_TEMPERATURE_MIN <= temp <= _TEMPERATURE_MAX):
            raise CortexValidationError(
                f"'temperature' must be between {_TEMPERATURE_MIN} and {_TEMPERATURE_MAX}"
            )
        out["temperature"] = float(temp)
    return out


_REASON_MODES = ("cot", "debate", "council")


def validate_reason(data: Any) -> dict:
    data = _require_dict(data)
    out: dict = {
        "prompt": _req_str(data, "prompt"),
        "system_prompt": _opt_str(data, "system_prompt", ""),
        "mode": _opt_str(data, "mode", "cot").strip().lower() or "cot",
    }
    if out["mode"] not in _REASON_MODES:
        raise CortexValidationError(
            f"'mode' must be one of {list(_REASON_MODES)}"
        )
    if data.get("max_tokens") is not None:
        out["max_tokens"] = _opt_int(data, "max_tokens", 1, 1, _MAX_TOKENS_MAX)
    if data.get("temperature") is not None:
        temp = data.get("temperature")
        if isinstance(temp, bool) or not isinstance(temp, (int, float)):
            raise CortexValidationError("'temperature' must be a number")
        if not (_TEMPERATURE_MIN <= temp <= _TEMPERATURE_MAX):
            raise CortexValidationError(
                f"'temperature' must be between {_TEMPERATURE_MIN} and {_TEMPERATURE_MAX}"
            )
        out["temperature"] = float(temp)
    return out


def validate_classify(data: Any) -> dict:
    data = _require_dict(data)
    return {
        "text": _req_str(data, "text"),
        "labels": _str_list(data, "labels", required=True),
    }


def validate_extract(data: Any) -> dict:
    data = _require_dict(data)
    schema = data.get("schema")
    if not isinstance(schema, dict) or not schema:
        raise CortexValidationError("'schema' is required and must be a non-empty JSON object")
    return {
        "text": _req_str(data, "text"),
        "schema": schema,
    }


# RICOAS intake bridge (prem-ricoas-02). Impact levels mirror the dashboard
# intake wizard; classification is NEVER read here (server-side, key-clamped).
INTAKE_IMPACT_LEVELS = ("IL2", "IL4", "IL5", "IL6")
_INTAKE_TEXT_MAX = 20000


def _bounded_text(data: dict, key: str) -> str:
    value = _req_str(data, key)
    if len(value) > _INTAKE_TEXT_MAX:
        raise CortexValidationError(
            f"{key!r} must be at most {_INTAKE_TEXT_MAX} characters"
        )
    return value


def validate_intake_session(data: Any) -> dict:
    data = _require_dict(data)
    impact_level = (_opt_str(data, "impact_level", "IL4") or "IL4").upper()
    if impact_level not in INTAKE_IMPACT_LEVELS:
        raise CortexValidationError(
            f"impact_level must be one of {INTAKE_IMPACT_LEVELS}"
        )
    extra_context = data.get("extra_context")
    if extra_context is None:
        extra_context = {}
    if not isinstance(extra_context, dict):
        raise CortexValidationError("'extra_context' must be a JSON object")
    return {
        "verbatim_ask": _bounded_text(data, "verbatim_ask"),
        "customer_name": _req_str(data, "customer_name"),
        "customer_org": _opt_str(data, "customer_org"),
        "goal": _opt_str(data, "goal", "build") or "build",
        "role": _opt_str(data, "role", "developer") or "developer",
        "impact_level": impact_level,
        "origin": _opt_str(data, "origin"),
        "extra_context": extra_context,
    }


def validate_intake_turn(data: Any) -> dict:
    data = _require_dict(data)
    return {
        "session_id": _req_str(data, "session_id"),
        "message": _bounded_text(data, "message"),
    }


def validate_govern(data: Any) -> dict:
    data = _require_dict(data)
    context_sources = data.get("context_sources")
    if context_sources is not None and not isinstance(context_sources, (list, int)):
        raise CortexValidationError(
            "'context_sources' must be a list of sources or an integer count"
        )
    return {
        "text": _req_str(data, "text"),
        "retrieval": _opt_bool(data, "retrieval", True),
        "context_sources": context_sources,
        "operation": _opt_str(data, "operation", "cortex.govern") or "cortex.govern",
    }
