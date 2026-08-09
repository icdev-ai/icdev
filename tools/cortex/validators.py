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

import re
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


# ---------------------------------------------------------------------------
# agent — team / single / graph launch (hgx-cx-02)
# ---------------------------------------------------------------------------
# Mirrors tools.cortex.api._AGENT_MODES. Kept as a literal rather than imported
# so validation stays importable without dragging in the facade module (and its
# ACE / agent-loop seams) — the same reason ASK_MODES mirrors analyst's.
AGENT_MODES = ("auto", "team", "single", "graph")

_MAX_ITERATIONS_MIN, _MAX_ITERATIONS_MAX = 1, 50
_MAX_ROLES = 20
_AGENT_TEXT_MAX = 20000
# A workflow/project id is a slug the runtime looks up, never a path. Bound the
# shape here so a lookup can never be talked into traversing anything.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
# llm_function names a routing chain in args/llm_config.yaml. An UNDECLARED
# function does not error — LLMRouter silently falls back to routing.default —
# so the only thing validation can do here is keep it a plain identifier and
# refuse the injection-shaped values. Choosing the chain is the caller's job;
# choosing the MODEL is never anybody's job in Python (LLM-agnostic rule).
_LLM_FUNCTION_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _agent_graph(data: dict) -> Optional[dict]:
    """Validate the ``graph`` spec for ``mode="graph"``.

    Shape: ``{"workflow_id": str, "project_id"?: str, "inputs"?: {..}}`` — the
    same three fields ``api.agent`` reads. ``inputs`` values are left as the
    caller sent them (they are workflow inputs, not identity), but the KEYS are
    bounded so a run row cannot be stuffed with arbitrary structure.
    """
    spec = data.get("graph")
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise CortexValidationError("'graph' must be a JSON object")

    workflow_id = str(spec.get("workflow_id") or "").strip()
    if not workflow_id:
        raise CortexValidationError("graph['workflow_id'] is required")
    if not _ID_RE.match(workflow_id):
        raise CortexValidationError(
            "graph['workflow_id'] must be a slug (letters, digits, . _ : -)"
        )

    out: dict = {"workflow_id": workflow_id}

    project_id = str(spec.get("project_id") or "").strip()
    if project_id:
        if not _ID_RE.match(project_id):
            raise CortexValidationError(
                "graph['project_id'] must be a slug (letters, digits, . _ : -)"
            )
        out["project_id"] = project_id

    inputs = spec.get("inputs")
    if inputs is not None:
        if not isinstance(inputs, dict):
            raise CortexValidationError("graph['inputs'] must be a JSON object")
        for key in inputs:
            if not isinstance(key, str) or not _ID_RE.match(key):
                raise CortexValidationError(
                    f"graph['inputs'] key {key!r} must be a slug"
                )
        out["inputs"] = inputs

    return out


def validate_agent(data: Any) -> dict:
    """Validate an agent-launch request into ``api.agent`` kwargs.

    NOT accepted from the wire, deliberately:

    * ``tools`` / ``tool_handlers`` — handlers are Python callables and a tool
      schema is an authorization decision. A remote caller that could name the
      tools an agent may run would be choosing its own privileges; over REST the
      single-agent loop therefore runs with NO tools, which makes it a governed
      completion with an iteration budget. Tool-bearing runs are what ``graph``
      mode is for — Studio already gates tools per node (MCP-WF-001).
    * ``rubric`` — grading a loop against the delivery pipeline shells out to the
      repo's own gates on the SERVER's checkout. That is a local-operator knob,
      not something a remote key gets to switch on.
    * ``webhook_url`` — a caller-supplied URL the platform POSTs to when the run
      finishes is a server-side request forgery primitive: the caller names an
      internal address and we reach it, from inside, with the run's payload. The
      launch is non-blocking and returns an id; polling is the supported way to
      learn the outcome. (Same reason ``/slides`` drops ``image_path``: on a
      remote surface, an address the server honours belongs to an allowlist, and
      we do not have one yet.)
    * identity of any kind — tenant/user/classification come from the key.
    """
    data = _require_dict(data)

    mode = (_opt_str(data, "mode", "auto") or "auto").strip().lower()
    if mode not in AGENT_MODES:
        raise CortexValidationError(f"'mode' must be one of {list(AGENT_MODES)}")

    roles = _str_list(data, "roles", required=False)
    if roles is not None:
        if len(roles) > _MAX_ROLES:
            raise CortexValidationError(
                f"'roles' may name at most {_MAX_ROLES} roles, got {len(roles)}"
            )
        for role in roles:
            if not _ID_RE.match(role):
                raise CortexValidationError(
                    f"role {role!r} must be a slug (letters, digits, . _ : -)"
                )

    graph = _agent_graph(data)
    if mode == "graph" and graph is None:
        raise CortexValidationError(
            'mode="graph" requires graph={"workflow_id": ...}'
        )

    out: dict = {
        "goal": _bounded_text(data, "goal"),
        "mode": mode,
        "roles": roles or None,
        "graph": graph,
        "max_iterations": _opt_int(
            data, "max_iterations", 12, _MAX_ITERATIONS_MIN, _MAX_ITERATIONS_MAX
        ),
    }

    system_prompt = _opt_str(data, "system_prompt", "")
    if system_prompt:
        if len(system_prompt) > _AGENT_TEXT_MAX:
            raise CortexValidationError(
                f"'system_prompt' must be at most {_AGENT_TEXT_MAX} characters"
            )
        out["system_prompt"] = system_prompt

    llm_function = _opt_str(data, "llm_function", "").strip()
    if llm_function:
        if not _LLM_FUNCTION_RE.match(llm_function):
            raise CortexValidationError(
                "'llm_function' must be a lowercase identifier naming a routing "
                "chain in args/llm_config.yaml (never a model id)"
            )
        out["llm_function"] = llm_function

    trigger_ref = _opt_str(data, "trigger_ref", "").strip()
    if trigger_ref:
        if not _ID_RE.match(trigger_ref):
            raise CortexValidationError(
                "'trigger_ref' must be a slug (letters, digits, . _ : -)"
            )
        out["trigger_ref"] = trigger_ref

    return out


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
