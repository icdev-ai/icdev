# CUI // SP-CTI
"""ICDEV Cortex — RICOAS intake bridge (``/cortex/api/v1/intake/*``) (prem-ricoas-02).

External PMO surfaces (compass Requirements Portal, future premium apps) drive
the SAME RICOAS intake engine (``tools/requirements/intake_engine.py``) that
powers the full requirements experience, so the session a PMO captures is the
ONE session a Task Lead later continues in the full RICOAS UI at
``/chat/<session_id>`` — zero translation loss between customer capture and
engineering intake.

    POST /cortex/api/v1/intake/session       create session + verbatim first turn
    POST /cortex/api/v1/intake/turn           process a customer message turn
    GET  /cortex/api/v1/intake/session/<id>   session status + conversation

Provenance: the verbatim customer ask is persisted untouched — as the first
customer turn in ``intake_conversation`` and under ``verbatim_ask`` in the
session ``context_summary`` — so downstream scoping/estimation always traces
back to the customer's exact words.

Auth/scopes: session-authenticated dashboard users pass through; external
``icdev_ctx_`` service-key callers (tools/cortex/service_keys.py) must hold
``cortex:intake``. Bridge-created sessions record the caller's tenant
(``bridge_tenant`` in context_summary); a service key can only read/continue
sessions bound to its own tenant. Identity (tenant/classification) is derived
SERVER-SIDE from the key row, never from the request body.

Turns from network callers run through the TRUST governance pre-check
(injection screen; ``trusted_content`` is force-cleared by the key binding)
before reaching the engine.
"""
from __future__ import annotations

import functools
import json
from typing import Callable

from flask import g, jsonify

from tools.logging.icdev_logger import get_logger

from . import validators
from .governance import GovernanceBlockedError, GovernancePipeline
from .rest_v1 import _authenticated, _scope_denied, _server_context
from .schemas import CortexContext

logger = get_logger("icdev.cortex.rest_intake")

_API_V1 = "/api/v1"

# Session fields safe to expose over the bridge (never context_summary raw,
# which may hold internal fast-track/use-case config alongside provenance).
_SESSION_FIELDS = (
    "id",
    "customer_name",
    "customer_org",
    "status",
    "classification",
    "impact_level",
    "readiness_score",
    "requirement_count",
    "turn_count",
    "decomposition_count",
    "document_count",
    "created_at",
)


def _continue_url(session_id: str) -> str:
    """Where a Task Lead resumes this exact session in the full RICOAS UI."""
    return f"/chat/{session_id}"


def _db():
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        conn.set_security_context(None)  # rls-bypass: intake sessions carry no tenant_id column; bridge access is enforced per-session via bridge_tenant in context_summary (see _bridge_tenant_ok) — required for prem-ricoas-02 because service-key callers have no dashboard session context
    except Exception:
        pass
    return conn


def _bridge_tenant_ok(session_id: str) -> bool:
    """Tenant guard for service-key callers.

    Dashboard session users (no ``g.cortex_binding``) pass through — they are
    governed by the normal dashboard auth. External service keys may only
    touch sessions whose ``context_summary.bridge_tenant`` matches the key's
    tenant. Unknown sessions and non-bridge sessions read as denied so the
    surface never leaks existence of other tenants' (or internal) sessions.
    """
    binding = getattr(g, "cortex_binding", None)
    if binding is None:
        return True
    conn = _db()
    try:
        row = conn.execute(
            "SELECT context_summary FROM intake_sessions WHERE id = %s",
            (session_id,),
        ).fetchone()
        if not row:
            return False
        try:
            context = json.loads(dict(row).get("context_summary") or "{}")
        except (ValueError, TypeError):
            return False
        return context.get("bridge_tenant") == binding.get("tenant_id")
    except Exception as exc:  # noqa: BLE001 — deny on any doubt
        logger.warning("intake bridge tenant check failed for %s: %s", session_id, exc)
        return False
    finally:
        conn.close()


def _intake_api(func: Callable) -> Callable:
    """Auth + ``cortex:intake`` scope + JSON body parse + uniform error mapping."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not _authenticated():
            return jsonify({"error": "authentication required"}), 401
        denied = _scope_denied("intake")
        if denied is not None:
            return denied
        from flask import request

        try:
            data = request.get_json(silent=True)
        except Exception:
            data = None
        try:
            return jsonify(func(data, *args, **kwargs))
        except validators.CortexValidationError as exc:
            return jsonify({"error": str(exc)}), 400
        except GovernanceBlockedError as exc:
            return jsonify({
                "error": exc.reason,
                "gate": exc.gate,
                "blocked": True,
                "governance": exc.report.to_dict(),
            }), 403
        except ValueError as exc:
            # intake_engine raises ValueError for unknown sessions
            return jsonify({"error": str(exc)}), 404
        except Exception as exc:  # pragma: no cover - defensive 500
            logger.exception("cortex intake endpoint failed: %s", exc)
            return jsonify({"error": "internal error"}), 500

    return wrapper


def _governed_turn(session_id: str, message: str, ctx: CortexContext) -> dict:
    """Run one intake turn behind the TRUST pre-check.

    ``attach=False``: the engine returns a plain dict (not a CortexResult) —
    every gate still runs (injection screen, audit, provenance), but the
    engine's native result passes through intact. The governed (screened)
    message is what reaches the engine.
    """
    pipeline = GovernancePipeline(operation="cortex.intake")
    result, _report = pipeline.wrap(
        lambda governed_message: _process_turn(session_id, governed_message),
        ctx,
        prompt=message,
        retrieval=False,
        attach=False,
    )
    return result


# Engine indirection — module-level so tests can monkeypatch without touching
# tools.requirements (which imports the full RICOAS stack).
def _create_session(**kwargs) -> dict:
    from tools.requirements.intake_engine import create_session

    return create_session(**kwargs)


def _process_turn(session_id: str, message: str) -> dict:
    from tools.requirements.intake_engine import process_turn

    return process_turn(session_id, message)


def _get_session(session_id: str) -> dict:
    from tools.requirements.intake_engine import get_session

    return get_session(session_id)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@_intake_api
def api_v1_intake_session_create(data):
    """Create a RICOAS intake session seeded with the verbatim customer ask."""
    params = validators.validate_intake_session(data)
    ctx = _server_context(validators.domain_of(data))
    binding = getattr(g, "cortex_binding", None)
    label = (binding or {}).get("label", "")

    extra_context = dict(params["extra_context"])
    extra_context.update({
        "origin": params["origin"] or (f"cortex-svc:{label}" if label else "cortex-bridge"),
        "verbatim_ask": params["verbatim_ask"],
        "bridge_tenant": ctx.tenant_id,
    })

    created = _create_session(
        project_id="",
        customer_name=params["customer_name"],
        customer_org=params["customer_org"],
        impact_level=params["impact_level"],
        classification=ctx.classification,
        created_by=(getattr(g, "current_user", None) or {}).get("id") or "cortex-bridge",
        role=params["role"],
        goal=params["goal"],
        extra_context=extra_context,
    )
    session_id = created["session_id"]

    # The verbatim ask IS the first customer turn: requirements extraction,
    # gap detection, and readiness all start from the customer's exact words.
    turn = _governed_turn(session_id, params["verbatim_ask"], ctx)

    return {
        "status": "ok",
        "session_id": session_id,
        "welcome_message": created.get("message", ""),
        "turn": turn,
        "continue_url": _continue_url(session_id),
        "verbatim_recorded": True,
    }


@_intake_api
def api_v1_intake_turn(data):
    """Process one customer message turn against an existing bridge session."""
    params = validators.validate_intake_turn(data)
    ctx = _server_context(validators.domain_of(data))
    session_id = params["session_id"]
    if not _bridge_tenant_ok(session_id):
        raise ValueError(f"Session '{session_id}' not found.")
    turn = _governed_turn(session_id, params["message"], ctx)
    return {
        "status": "ok",
        "session_id": session_id,
        "turn": turn,
        "continue_url": _continue_url(session_id),
    }


@_intake_api
def api_v1_intake_session_get(data, session_id: str):
    """Session status + conversation history (whitelisted fields only)."""
    if not _bridge_tenant_ok(session_id):
        raise ValueError(f"Session '{session_id}' not found.")
    session = _get_session(session_id)  # ValueError -> 404 via decorator
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT turn_number, role, content, created_at "
            "FROM intake_conversation WHERE session_id = %s AND role != 'system' "
            "ORDER BY turn_number",
            (session_id,),
        ).fetchall()
        messages = [dict(r) for r in rows]
    finally:
        conn.close()
    return {
        "status": "ok",
        "session": {k: session.get(k) for k in _SESSION_FIELDS},
        "messages": messages,
        "continue_url": _continue_url(session_id),
    }


def register_rest_intake(cortex_bp) -> None:
    """Attach the intake bridge endpoints to the shared cortex blueprint.

    Called once from ``blueprint.py`` right after ``register_rest_v1`` so the
    bridge shares the canvas blueprint's prefix and auth path.
    """
    cortex_bp.add_url_rule(
        f"{_API_V1}/intake/session",
        "api_v1_intake_session_create",
        api_v1_intake_session_create,
        methods=["POST"],
    )
    cortex_bp.add_url_rule(
        f"{_API_V1}/intake/turn",
        "api_v1_intake_turn",
        api_v1_intake_turn,
        methods=["POST"],
    )
    cortex_bp.add_url_rule(
        f"{_API_V1}/intake/session/<session_id>",
        "api_v1_intake_session_get",
        api_v1_intake_session_get,
        methods=["GET"],
    )
