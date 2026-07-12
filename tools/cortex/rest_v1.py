# CUI // SP-CTI
"""ICDEV Cortex — versioned REST surface (``/cortex/api/v1/*``) (ctx-expose-02).

The ``/cortex`` canvas and the programmatic REST API share ONE
``Blueprint("cortex")`` (the canvas blueprint in ``blueprint.py``).
``register_rest_v1(cortex_bp)`` attaches the six governed v1 endpoints to that
blueprint, so both the web canvas and the machine surface live under one prefix
and one auth path — no second blueprint, no double registration.

    POST /cortex/api/v1/search     unified retrieval (strategy router + CRAG)
    POST /cortex/api/v1/ask        Cortex Analyst (IQE / NL->SQL)
    POST /cortex/api/v1/complete   free-form completion (governed)
    POST /cortex/api/v1/classify   single-label classification (governed)
    POST /cortex/api/v1/extract    structured extraction (governed)
    POST /cortex/api/v1/govern     run the TRUST governance chain over text

Identity (tenant / user / classification) is derived SERVER-SIDE from the
authenticated session, never from the client body; ``domain`` is the only
caller-supplied context field and can only narrow, never widen, access.
"""
from __future__ import annotations

import functools
from typing import Callable

from flask import g, jsonify, request

from tools.logging.icdev_logger import get_logger

from . import validators
from .analyst import CortexAnalystError, CortexQueryBlocked
# Import ALL facades from .api — these are the GOVERNED wrappers (TRUST pipeline:
# gateway screen, redaction, grounding, provenance, append-only audit). Importing
# ask/search from .analyst/.search_service would reach the RAW ungoverned impls,
# so the REST /api/v1/search + /ask endpoints would bypass governance entirely.
from .api import ask, classify, complete, extract, search
from .governance import GovernanceBlockedError, GovernancePipeline
from .schemas import CortexContext, CortexResult

logger = get_logger("icdev.cortex.rest_v1")

_API_V1 = "/api/v1"


# ---------------------------------------------------------------------------
# Identity — derived server-side, never from the client body
# ---------------------------------------------------------------------------
def _sec_attr(sec, key: str, default=None):
    """Read a field from ``g.security_context`` (dataclass or dict)."""
    if sec is None:
        return default
    if isinstance(sec, dict):
        return sec.get(key, default)
    return getattr(sec, key, default)


def _server_context(domain: str = "") -> CortexContext:
    """Build a CortexContext from the authenticated session ONLY.

    tenant_id / user_id / classification come from ``g.security_context``
    (set by the dashboard auth middleware) with a fallback to the
    ``g.current_user`` dict. ``domain`` is the sole caller-supplied field —
    it narrows backend selection and cannot widen access.
    """
    sec = getattr(g, "security_context", None)
    user = getattr(g, "current_user", None) or {}

    tenant_id = _sec_attr(sec, "tenant_id") or user.get("tenant_id") or "default"
    user_id = (
        _sec_attr(sec, "user_id")
        or str(user.get("id") or user.get("user_id") or "")
    )
    classification = (
        _sec_attr(sec, "classification")
        or user.get("clearance_level")
        or user.get("classification")
        or "CUI"
    )
    return CortexContext(
        tenant_id=str(tenant_id),
        user_id=str(user_id),
        classification=str(classification),
        domain=domain or "",
    )


def _authenticated() -> bool:
    return bool(getattr(g, "current_user", None))


def _scope_denied(operation: str):
    """Enforce per-operation scopes for Cortex service-key callers.

    Session-authenticated dashboard users carry no ``g.cortex_binding`` and
    pass through unchanged. External icdev_ctx_ callers (compass, idea_lab —
    bound by tools/dashboard/auth.py) must hold ``cortex:<operation>`` from
    their key row. Returns an error response tuple or None.
    """
    binding = getattr(g, "cortex_binding", None)
    if binding is None:
        return None
    scope = f"cortex:{operation}"
    if scope in (binding.get("scopes") or []):
        return None
    return jsonify({
        "error": f"service key lacks required scope '{scope}'",
        "code": "forbidden",
        "label": binding.get("label", ""),
    }), 403


# ---------------------------------------------------------------------------
# Endpoint decorator — auth + JSON parse + uniform error mapping
# ---------------------------------------------------------------------------
def _cortex_api(func: Callable) -> Callable:
    """Wrap a Cortex endpoint with auth, body parsing, and error mapping.

    The wrapped function receives the parsed JSON body dict and returns a
    JSON-serializable dict (HTTP 200). Exceptions map to stable envelopes:
      * validation error            -> 400
      * governance / analyst block   -> 403 (+ serialized GovernanceReport)
      * analyst-unanswerable         -> 422
      * anything else                -> 500
    """

    operation = func.__name__.replace("api_v1_", "")

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not _authenticated():
            return jsonify({"error": "authentication required"}), 401
        denied = _scope_denied(operation)
        if denied is not None:
            return denied
        try:
            data = request.get_json(silent=True)
        except Exception:
            data = None
        try:
            payload = func(data, *args, **kwargs)
            return jsonify(payload)
        except validators.CortexValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        except GovernanceBlockedError as exc:
            return jsonify({
                "error": exc.reason,
                "gate": exc.gate,
                "blocked": True,
                "governance": exc.report.to_dict(),
            }), 403
        except CortexQueryBlocked as exc:
            return jsonify({
                "error": str(exc),
                "blocked": True,
                "governance": exc.governance.to_dict(),
            }), 403
        except CortexAnalystError as exc:
            return jsonify({
                "error": str(exc),
                "governance": exc.governance.to_dict(),
            }), 422
        except Exception as exc:  # pragma: no cover - defensive 500
            logger.exception("cortex REST endpoint failed: %s", exc)
            return jsonify({"error": "internal error"}), 500

    return wrapper


def _governed(
    operation: str,
    prompt: str,
    fn: Callable,
    ctx: CortexContext,
    *,
    retrieval: bool = False,
    context_sources=None,
) -> CortexResult:
    """Run ``fn(governed_prompt)`` through the TRUST governance chain.

    A blocked pre-check raises :class:`GovernanceBlockedError`, which the
    endpoint decorator maps to a 403 governance envelope.
    """
    pipeline = GovernancePipeline(operation=operation)
    result, _report = pipeline.wrap(
        fn, ctx, prompt=prompt, context_sources=context_sources, retrieval=retrieval
    )
    return result


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@_cortex_api
def api_v1_search(data):
    """Unified Cortex retrieval. Returns normalized CortexSearchResult rows."""
    params = validators.validate_search(data)
    ctx = _server_context(validators.domain_of(data))
    results = search(
        params["query"], top_k=params["top_k"], strategy=params["strategy"], ctx=ctx
    )
    return {"results": [r.to_dict() for r in results], "count": len(results)}


@_cortex_api
def api_v1_ask(data):
    """Cortex Analyst — natural-language data question over registered scopes."""
    params = validators.validate_ask(data)
    ctx = _server_context(validators.domain_of(data))
    result = ask(
        params["question"],
        mode=params["mode"],
        ctx=ctx,
        canvas=params["canvas"],
        collections=params["collections"],
        summarize=params["summarize"],
    )
    return result.to_dict()


@_cortex_api
def api_v1_complete(data):
    """Free-form completion via the config-routed LLM chain (governed)."""
    params = validators.validate_complete(data)
    ctx = _server_context(validators.domain_of(data))
    kwargs = {"system_prompt": params["system_prompt"]}
    if "max_tokens" in params:
        kwargs["max_tokens"] = params["max_tokens"]
    if "temperature" in params:
        kwargs["temperature"] = params["temperature"]
    result = _governed(
        "cortex.complete",
        params["prompt"],
        lambda governed_prompt: complete(governed_prompt, ctx=ctx, **kwargs),
        ctx,
        retrieval=False,
    )
    return result.to_dict()


@_cortex_api
def api_v1_classify(data):
    """Single-label classification with deterministic air-gap fallback (governed)."""
    params = validators.validate_classify(data)
    ctx = _server_context(validators.domain_of(data))
    labels = params["labels"]
    result = _governed(
        "cortex.classify",
        params["text"],
        lambda governed_text: classify(governed_text, labels, ctx=ctx),
        ctx,
        retrieval=False,
    )
    return result.to_dict()


@_cortex_api
def api_v1_extract(data):
    """Structured extraction conforming to a caller-supplied JSON schema (governed)."""
    params = validators.validate_extract(data)
    ctx = _server_context(validators.domain_of(data))
    schema = params["schema"]
    result = _governed(
        "cortex.extract",
        params["text"],
        lambda governed_text: extract(governed_text, schema, ctx=ctx),
        ctx,
        retrieval=False,
    )
    return result.to_dict()


@_cortex_api
def api_v1_govern(data):
    """Run the TRUST governance chain over caller-supplied text.

    A useful dry-run surface: submit text (optionally with the injected
    ``context_sources`` it should be grounded in) and get back the
    GovernanceReport plus the governed (redacted) text. A blocked pre-check
    returns 403 with the report.
    """
    params = validators.validate_govern(data)
    ctx = _server_context(validators.domain_of(data))
    result = _governed(
        params["operation"],
        params["text"],
        lambda governed_text: CortexResult(text=governed_text),
        ctx,
        retrieval=params["retrieval"],
        context_sources=params["context_sources"],
    )
    return {
        "text": result.text,
        "grounded": result.grounded,
        "blocked": result.governance.blocked,
        "governance": result.governance.to_dict(),
    }


def api_v1_health():
    """Liveness probe — config + air-gap posture, no LLM call, no auth.

    Registered in PUBLIC_ENDPOINTS (tools/dashboard/auth.py) so external
    consumers' ``is_available()`` probes work without a session or key.
    Status only — never returns data.
    """
    try:
        from .config import airgap_active, load_cortex_config

        load_cortex_config()
        return jsonify({
            "ok": True,
            "status": "healthy",
            "airgap": bool(airgap_active(None)),
            "operations": ["search", "ask", "complete", "classify", "extract", "govern"],
        })
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "status": "unhealthy", "error": str(exc)[:300]}), 503


def register_rest_v1(cortex_bp) -> None:
    """Attach the ``/cortex/api/v1/*`` endpoints to the shared canvas blueprint.

    Called once from ``blueprint.py`` after the canvas ``cortex_bp`` is defined,
    so the machine surface and the web canvas share a single Blueprint (no
    second registration, no url_prefix drift).
    """
    for name, view in (
        ("search", api_v1_search),
        ("ask", api_v1_ask),
        ("complete", api_v1_complete),
        ("classify", api_v1_classify),
        ("extract", api_v1_extract),
        ("govern", api_v1_govern),
    ):
        cortex_bp.add_url_rule(
            f"{_API_V1}/{name}", f"api_v1_{name}", view, methods=["POST"]
        )
    cortex_bp.add_url_rule(
        f"{_API_V1}/health", "api_v1_health", api_v1_health, methods=["GET"]
    )
