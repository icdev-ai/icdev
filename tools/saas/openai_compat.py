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

Streaming: ``stream=true`` goes through ``LLMRouter.invoke_streaming``, which
de-anonymizes its output and writes ``ai_telemetry`` (owui-api-02); each router
text event becomes one ``chat.completion.chunk`` (owui-api-05).

Audit: every exchange -- served, refused or failed -- writes one append-only
``audit_trail`` row (``chat.message_received``) carrying hashes and the REQUEST's
classification (owui-flow-02). With ``audit_required`` a non-streamed answer whose
row cannot be written is withheld (503).

Auth is the gateway middleware's job -- by the time a request arrives here
``g.tenant_id`` / ``g.user_id`` are set (``icdev_`` API key, scope ``chat:write``
for completions). A request that somehow lacks a principal is refused rather
than served anonymously.
"""

import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
# kax-conflict-04: run by path, sys.path[0] is this file's own directory -- bootstrap
# the import root before the first first-party import.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

BASE_DIR = repo_root(__file__)

import yaml  # noqa: E402
from flask import Blueprint, Response, g, jsonify, request, stream_with_context  # noqa: E402

from tools.llm.cli_bridge.http_bridge import _text_of  # noqa: E402
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
        if isinstance(m, dict) and m.get("id")
        and (m.get("llm_function") or (m.get("kind") == "workflow" and m.get("workflow")))
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
    return {mid: m for mid, m in _models(cfg).items()
            if m.get("kind") == "workflow" or _declared(router, m["llm_function"])}


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
    """One governed, audited chat turn through ``LLMRouter`` (invoke, or invoke_streaming)."""
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
    if model.get("kind") == "workflow":
        return _run_workflow(cfg, model, body)
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
    exchange = {"model_id": model_id, "function": function, "classification": classification,
                "correlation_id": req.correlation_id, "prompt_sha256": _sha256(_prompt_text(req)),
                "tenant_id": str(g.tenant_id), "user_id": str(getattr(g, "user_id", "") or "")}
    try:
        if body.get("stream"):
            events = _router().invoke_streaming(function, req)
        else:
            resp = _router().invoke(function, req)
    except ForceLocalViolation as exc:
        logger.warning("openai_compat: policy refused %s for %s: %s", model_id, g.tenant_id, exc)
        _audit_exchange(cfg, exchange, "refused", error_code="routing_policy")
        return openai_error(403, f"routing policy refused this request: {exc}",
                            "policy_violation", "routing_policy")
    except RedactionUnavailableError as exc:
        _audit_exchange(cfg, exchange, "refused", error_code="redaction_unavailable")
        return openai_error(503, f"redaction unavailable and fail-closed: {exc}",
                            "service_unavailable", "redaction_unavailable")
    except LLMUnavailableError as exc:
        _audit_exchange(cfg, exchange, "refused", error_code="llm_unavailable")
        return openai_error(503, f"no model available for {model_id}: {exc}",
                            "service_unavailable", "llm_unavailable")

    headers = {"X-ICDEV-Classification": classification}
    if body.get("stream"):
        return Response(stream_with_context(_stream(cfg, events, exchange)),
                        mimetype="text/event-stream", headers=headers)

    completion = to_completion(resp, model_id)
    annotations = citation_annotations(cfg, resp.content)
    if annotations:
        completion["choices"][0]["message"]["annotations"] = annotations
    exchange.update(provider=resp.provider, served_model=resp.model_id, citations=len(annotations))
    if not _audit_exchange(cfg, exchange, "served", content=resp.content) and _audit_required(cfg):
        # NIST AU-2/AU-12: a governed turn that cannot be recorded is not handed out.
        return openai_error(503, "the exchange could not be written to the ICDEV audit trail",
                            "service_unavailable", "audit_unavailable")
    return jsonify(completion), 200, headers


# ---------------------------------------------------------------------------
# Streaming (owui-api-05) and the per-exchange audit row (owui-flow-02)
# ---------------------------------------------------------------------------
def _stream(cfg: Dict[str, Any], events, exchange: Dict[str, Any]):
    """Router stream events -> OpenAI ``chat.completion.chunk`` SSE frames.

    ``invoke_streaming`` de-anonymizes and writes ``ai_telemetry`` itself
    (owui-api-02); this adds the audit row once the stream ends. Policy and
    redaction refusals are raised BEFORE the first frame, so they still reach
    the client as OpenAI error bodies. Once frames have been sent the response
    cannot be withdrawn, so a failed audit write here is logged, not refused --
    the one place ``audit_required`` cannot hold, and it is documented as such.
    """
    base = {"id": f"chatcmpl-{uuid.uuid4().hex}", "object": "chat.completion.chunk",
            "created": int(time.time()), "model": exchange["model_id"]}

    def frame(delta: dict, finish: Optional[str] = None) -> bytes:
        chunk = {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode("utf-8")

    parts: List[str] = []
    status, error_code = "served", None
    yield frame({"role": "assistant"})
    try:
        for event in events:
            etype = event.get("type") if isinstance(event, dict) else None
            if etype == "text" and event.get("text"):
                parts.append(event["text"])
                yield frame({"content": event["text"]})
            elif etype == "error":
                status, error_code = "error", "provider_error"
                yield frame({"content": f"\n\n[ICDEV: the model provider failed: {event.get('error', '')}]"})
            elif etype == "message_stop":
                exchange["served_model"] = event.get("model_id", "")
        annotations = citation_annotations(cfg, "".join(parts))
        if annotations:
            exchange["citations"] = len(annotations)
            yield frame({"annotations": annotations})
        yield frame({}, "stop")
        yield b"data: [DONE]\n\n"
    finally:
        if not _audit_exchange(cfg, exchange, status, content="".join(parts), error_code=error_code):
            logger.error("openai_compat: streamed exchange %s was NOT audited", exchange["correlation_id"])


def citation_annotations(cfg: Dict[str, Any], text: str) -> List[Dict[str, Any]]:
    """``[source: ...]`` citations in ``text`` as OpenAI ``url_citation`` annotations (owui-rag-01).

    Retrieval stays in ICDEV; this only makes its provenance VISIBLE: Open WebUI
    renders a ``url_citation`` annotation as a source chip. Parsing is the shared
    ``citation_grounding.parse_citations`` -- not re-implemented here. Each link is
    a lookup into ICDEV (``citation_url_template``), never a fetch of the source.
    """
    from tools.quality.citation_grounding import parse_citations

    template = cfg.get("citation_url_template") or "{dashboard}/knowledge-search?q={id}"
    dashboard = (os.environ.get("ICDEV_DASHBOARD_URL") or "http://localhost:5050").rstrip("/")
    return [
        {"type": "url_citation",
         "url_citation": {"url": template.format(dashboard=dashboard, id=quote(sid, safe="")),
                          "title": f"ICDEV source {sid}"}}
        for sid in parse_citations(text or "")
    ]


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


def _prompt_text(req) -> str:
    return "\n".join([req.system_prompt or ""] + [m.get("content", "") for m in req.messages])


def _audit_required(cfg: Dict[str, Any]) -> bool:
    return cfg.get("audit_required", True) is not False


def _audit_exchange(cfg: Dict[str, Any], exchange: Dict[str, Any], status: str, *,
                    content: str = "", error_code: Optional[str] = None) -> bool:
    """One append-only ``audit_trail`` row per /v1 exchange. True when written.

    ICDEV -- not Open WebUI's database -- is the system of record (D-OWUI-1). The
    row carries hashes, never the text, and the REQUEST's classification: the
    router's ``ai_telemetry`` row records its own default, and a response-cache
    hit writes no telemetry at all, so this is the record that is always there.
    """
    from tools.audit.audit_logger import log_event

    details = {**exchange, "status": status, "response_sha256": _sha256(content) if content else None,
               "error_code": error_code, "surface": "openai_compat"}
    try:
        row = log_event(
            event_type="chat.message_received",
            actor=f"openai_compat:{exchange['tenant_id']}/{exchange['user_id']}",
            action=f"/v1/chat/completions {exchange['model_id']} {status}",
            details=details,
            classification=exchange["classification"],
            session_id=exchange["correlation_id"],
            raise_on_error=True,
        )
    except Exception as exc:  # noqa: BLE001 -- reported to the caller as False
        logger.error("openai_compat: audit write failed for %s: %s", exchange["correlation_id"], exc)
        return False
    return row is not None and row != -1


# ---------------------------------------------------------------------------
# Governed workflows as models (owui-flow-01)
# ---------------------------------------------------------------------------
def _run_workflow(cfg: Dict[str, Any], model: Dict[str, Any], body: dict):
    """Answer with an ICDEV workflow instead of a language model.

    Role-gated by the model's ``required_roles`` -- refused, not filtered, so a
    client cannot mistake "not allowed" for "nothing there". Audited exactly like
    an LLM exchange.
    """
    from tools.saas import openai_workflows

    model_id = model["id"]
    role = str(getattr(g, "user_role", "") or "")
    allowed = [str(r) for r in (model.get("required_roles") or [])]
    if role not in allowed:
        return openai_error(403, f"{model_id} requires one of the roles {allowed}",
                            "permission_error", "role_not_permitted")

    classification, err = _classification(cfg)
    if err:
        return openai_error(400, err, "invalid_request_error", "bad_classification")
    system_prompt, messages = split_messages(body.get("messages") or [])
    if not messages:
        return openai_error(400, "messages must contain at least one user turn", "invalid_request_error")

    exchange = {"model_id": model_id, "function": f"workflow:{model['workflow']}",
                "classification": classification, "correlation_id": _correlation_id(),
                "prompt_sha256": _sha256("\n".join(m["content"] for m in messages)),
                "tenant_id": str(g.tenant_id), "user_id": str(getattr(g, "user_id", "") or "")}
    try:
        result = openai_workflows.run(model["workflow"], messages, tenant_id=exchange["tenant_id"],
                                      user_id=exchange["user_id"],
                                      chat_id=request.headers.get("X-OpenWebUI-Chat-Id"))
    except openai_workflows.WorkflowError as exc:
        _audit_exchange(cfg, exchange, "error", error_code="workflow_failed")
        return openai_error(502, str(exc), "workflow_error", "workflow_failed")

    from tools.llm.provider import LLMResponse

    headers = {"X-ICDEV-Classification": classification}
    if body.get("stream"):
        events = iter([{"type": "text", "text": result["text"]},
                       {"type": "message_stop", "model_id": exchange["function"]}])
        return Response(stream_with_context(_stream(cfg, events, exchange)),
                        mimetype="text/event-stream", headers=headers)
    exchange["served_model"] = exchange["function"]
    if not _audit_exchange(cfg, exchange, "served", content=result["text"]) and _audit_required(cfg):
        return openai_error(503, "the exchange could not be written to the ICDEV audit trail",
                            "service_unavailable", "audit_unavailable")
    return jsonify(to_completion(LLMResponse(content=result["text"], model_id=exchange["function"]),
                                 model_id)), 200, headers

