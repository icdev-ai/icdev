#!/usr/bin/env python3

# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""ICDEV™ SaaS -- governed OpenAI-compatible endpoint (owui-api-01, D-OWUI-1).

Serves ``GET /v1/models`` and ``POST /v1/chat/completions`` on the SaaS API
gateway so an OpenAI-compatible client -- Open WebUI first -- reaches a model
ONLY through :class:`tools.llm.router.LLMRouter`. Redaction (fail-closed),
routing policy (classified content stays local), budgets and ``ai_telemetry``
therefore apply to every chat turn exactly as they do to ICDEV's own callers.

Why not the existing ``tools/llm/cli_bridge/http_bridge.py``: that bridge is a
loopback-only adapter that shells out to ``claude -p`` for opencode/Pi. It
never enters the router, so pointing a multi-user chat client at it would be a
governance bypass. Its OpenAI wire helpers are reused here; its backend is not.

What a client sees as a "model" is an ICDEV CAPABILITY from
``args/openai_compat.yaml`` (``icdev/general``, ``icdev/local-only`` ...), never
a vendor model: the router, not the client, decides who serves a request.

Streaming: ``stream=true`` is answered with the FINISHED completion framed as
``chat.completion.chunk`` events. ``LLMRouter.invoke_streaming`` applies input
redaction but neither de-anonymizes its output nor writes telemetry, so using it
here would return pseudonymised tokens unaudited. True token streaming waits on
that parity fix (owui-api-02).

Auth is the gateway middleware's job -- by the time a request arrives here
``g.tenant_id`` / ``g.user_id`` are set (``icdev_`` API key, scope ``chat:write``
for completions). A request that somehow lacks a principal is refused rather
than served anonymously.
"""

import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
# kax-conflict-04: run by path, sys.path[0] is this file's own directory -- bootstrap
# the import root before the first first-party import.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

BASE_DIR = repo_root(__file__)

import yaml  # noqa: E402
from flask import Blueprint, Response, g, jsonify, request  # noqa: E402

from tools.llm.cli_bridge.http_bridge import _text_of, to_sse  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.saas.openai_compat")

CONFIG_PATH = BASE_DIR / "args" / "openai_compat.yaml"

openai_bp = Blueprint("openai_compat", __name__, url_prefix="/v1")

_router_instance = None


# ---------------------------------------------------------------------------
# Config + router
# ---------------------------------------------------------------------------
def load_config(path: Path = CONFIG_PATH) -> Dict[str, Any]:
    """Read ``args/openai_compat.yaml``. A missing file exposes no models."""
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        logger.warning("openai_compat: %s not found -- no models exposed", path)
        return {}


def _models(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        str(m["id"]): m
        for m in (cfg.get("models") or [])
        if isinstance(m, dict) and m.get("id") and m.get("llm_function")
    }


def _router():
    """The process-wide LLMRouter, built on first use."""
    global _router_instance
    if _router_instance is None:
        from tools.llm.router import LLMRouter

        _router_instance = LLMRouter()
    return _router_instance


def _declared(router, function: str) -> bool:
    """Is *function* declared in the config the router ACTUALLY loaded?

    The router silently routes an undeclared function through ``routing.default``,
    which is cloud-first. ``ICDEV_LLM_CONFIG`` can point the router at an operator
    file instead of ``args/llm_config.yaml``, so "declared in the repo" proves
    nothing: ``icdev/local-only`` served from the default chain is not local. A
    capability whose function the live config lacks is hidden and refused.
    """
    routing = (getattr(router, "_config", None) or {}).get("routing") or {}
    return function in routing


def _served_models(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    router = _router()
    return {mid: m for mid, m in _models(cfg).items() if _declared(router, m["llm_function"])}


# ---------------------------------------------------------------------------
# OpenAI wire format
# ---------------------------------------------------------------------------
def openai_error(status: int, message: str, type_: str, code: str = "") -> Tuple[Response, int]:
    """An OpenAI-shaped error body, which OpenAI clients know how to display."""
    return jsonify({"error": {"message": message, "type": type_, "code": code or type_}}), status


def split_messages(messages: List[dict]) -> Tuple[str, List[Dict[str, str]]]:
    """OpenAI messages -> (system_prompt, router messages).

    System turns are joined into the router's ``system_prompt``; user and
    assistant turns keep their order. Content parts other than text (images,
    files) are replaced by a marker rather than silently dropped.
    """
    system: List[str] = []
    out: List[Dict[str, str]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        text = _text_of(m.get("content"))
        if role in ("system", "developer"):
            system.append(text)
        elif role in ("user", "assistant"):
            out.append({"role": role, "content": text})
    return "\n\n".join(s for s in system if s), out


def to_completion(resp: Any, model_id: str) -> dict:
    """A router ``LLMResponse`` as an OpenAI ``chat.completion``."""
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": resp.content or ""},
            "finish_reason": "length" if resp.stop_reason in ("max_tokens", "length") else "stop",
        }],
        "usage": {
            "prompt_tokens": int(resp.input_tokens or 0),
            "completion_tokens": int(resp.output_tokens or 0),
            "total_tokens": int(resp.input_tokens or 0) + int(resp.output_tokens or 0),
        },
    }


def _classification(cfg: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """(classification, error). The header wins; an unknown value is an error."""
    header = cfg.get("classification_header") or "X-ICDEV-Classification"
    default = str(cfg.get("default_classification") or "CUI").upper()
    raw = (request.headers.get(header) or "").strip().upper()
    if not raw:
        return default, None
    allowed = {str(c).upper() for c in (cfg.get("allowed_classifications") or [default])}
    if raw not in allowed:
        return None, f"{header} {raw!r} is not one of {sorted(allowed)}"
    return raw, None


def _correlation_id() -> str:
    cid = getattr(g, "correlation_id", None) or request.headers.get("X-Correlation-ID")
    return str(cid or uuid.uuid4().hex)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@openai_bp.route("/models", methods=["GET"])
def list_models():
    """The configured ICDEV capabilities, in OpenAI ``list`` shape."""
    if not getattr(g, "tenant_id", None):
        return openai_error(401, "authentication required", "invalid_request_error", "unauthenticated")
    created = int(time.time())
    data = [
        {"id": mid, "object": "model", "created": created, "owned_by": "icdev",
         "description": m.get("description", "")}
        for mid, m in _served_models(load_config()).items()
    ]
    return jsonify({"object": "list", "data": data})


@openai_bp.route("/chat/completions", methods=["POST"])
def chat_completions():
    """One governed chat turn through ``LLMRouter.invoke``."""
    if not getattr(g, "tenant_id", None):
        return openai_error(401, "authentication required", "invalid_request_error", "unauthenticated")

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return openai_error(400, "request body must be a JSON object", "invalid_request_error")

    cfg = load_config()
    model_id = str(body.get("model") or "")
    model = _models(cfg).get(model_id)
    if model is None:
        return openai_error(404, f"model {model_id!r} does not exist", "invalid_request_error",
                            "model_not_found")
    if not _declared(_router(), model["llm_function"]):
        logger.error("openai_compat: %s -> %s is not declared in the active llm_config; refusing",
                     model_id, model["llm_function"])
        return openai_error(503, f"{model_id} is not configured on this deployment: router function "
                            f"{model['llm_function']!r} is missing from the active llm_config",
                            "service_unavailable", "capability_unrouted")

    if body.get("tools"):
        # Refused, not ignored: a client that sends tools and gets back plain text
        # would believe the model chose not to call one. Tool calling arrives with
        # the MCP surface (owui-mcp-01); Open WebUI's default function-calling mode
        # does not send this field.
        return openai_error(400, "native tool calling is not supported on this endpoint",
                            "invalid_request_error", "tools_unsupported")

    classification, err = _classification(cfg)
    if err:
        return openai_error(400, err, "invalid_request_error", "bad_classification")

    system_prompt, messages = split_messages(body.get("messages") or [])
    if not messages:
        return openai_error(400, "messages must contain at least one user turn",
                            "invalid_request_error")

    from tools.llm.provider import LLMRequest
    from tools.llm.router import (
        ForceLocalViolation,
        LLMUnavailableError,
        RedactionUnavailableError,
    )

    cap = int(cfg.get("max_tokens_cap") or 8192)
    try:
        max_tokens = min(int(body.get("max_tokens") or body.get("max_completion_tokens") or cap), cap)
    except (TypeError, ValueError):
        return openai_error(400, "max_tokens must be an integer", "invalid_request_error")
    req = LLMRequest(
        messages=messages,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        temperature=1.0 if body.get("temperature") is None else float(body["temperature"]),
        stop_sequences=body.get("stop") if isinstance(body.get("stop"), list) else None,
        tenant_id=str(g.tenant_id),
        agent_id=f"openai_compat:{getattr(g, 'user_id', '') or ''}",
        classification=classification,
        correlation_id=_correlation_id(),
    )

    function = model["llm_function"]
    try:
        resp = _router().invoke(function, req)
    except ForceLocalViolation as exc:
        logger.warning("openai_compat: policy refused %s for %s: %s", model_id, g.tenant_id, exc)
        return openai_error(403, f"routing policy refused this request: {exc}",
                            "policy_violation", "routing_policy")
    except RedactionUnavailableError as exc:
        return openai_error(503, f"redaction unavailable and fail-closed: {exc}",
                            "service_unavailable", "redaction_unavailable")
    except LLMUnavailableError as exc:
        return openai_error(503, f"no model available for {model_id}: {exc}",
                            "service_unavailable", "llm_unavailable")

    completion = to_completion(resp, model_id)
    headers = {"X-ICDEV-Classification": getattr(resp, "classification", None) or classification}
    if body.get("stream"):
        return Response(b"".join(to_sse(completion)), mimetype="text/event-stream", headers=headers)
    return jsonify(completion), 200, headers
