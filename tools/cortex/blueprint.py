# CUI // SP-CTI
"""ICDEV Cortex Canvas — Flask Blueprint (skeleton).

The Snowflake-Intelligence-style entry point over the unified Cortex facade
(tools/cortex/api.py). This is the 8-gate canvas skeleton: it renders the
branded landing page, exposes a chat stub, and wires the IQE natural-language
query widget. Facade wiring (complete/classify/extract/search/ask/govern/agent)
lands in follow-on tasks; the chat endpoint degrades gracefully until then.

Routes:
  GET  /cortex/                     index — mode + domain-lens picker
  POST /cortex/api/chat             message → intent routing → cortex.* facade
  GET  /cortex/api/session/<id>     reload a session's persisted turns
  POST /cortex/api/iqe-query        IQE natural-language query over cortex.* collections
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime, timezone

from flask import Blueprint, jsonify, render_template, request

from tools.cortex.constants import (
    CORTEX_DOMAIN_KEYS,
    CORTEX_DOMAIN_LENSES,
    CORTEX_MODE_KEYS,
    CORTEX_MODES,
    DEFAULT_DOMAIN,
    DEFAULT_MODE,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

cortex_bp = Blueprint(
    "cortex",
    __name__,
    url_prefix="/cortex",
    template_folder="../../tools/dashboard/templates",
)

_INIT_DONE = False


def _ensure_init() -> None:
    global _INIT_DONE
    if _INIT_DONE:
        return
    try:
        from tools.cortex.db.init_db import init_db
        init_db()
    except Exception as exc:
        logger.warning("cortex: DB init error: %s", exc)
    _INIT_DONE = True


@cortex_bp.before_request
def _init():
    _ensure_init()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _g_security_context():
    """Return ``flask.g.security_context`` if there is one, else None."""
    try:
        from flask import g, has_request_context
        if not has_request_context():
            return None
        return getattr(g, "security_context", None)
    except Exception:
        return None


def _ctx_field(ctx, name: str, default: str) -> str:
    """Read *name* off a security context that may be a mapping OR an object.

    The dashboard puts BOTH shapes on ``g.security_context``: a plain dict for a
    Cortex service-key binding (``auth.py``) and a ``SecurityContext`` dataclass
    for a session user (``security_context._extract_from_flask_g``). A
    mapping-only read (``ctx.get(...)``) raised AttributeError on the dataclass
    and fell through to the "default" tenant — so every session user's canvas
    activity was attributed to a tenant they are not in, and (ctx-trust-05, now
    that the IQE route SCOPES on this value) would be filtered against it too.
    """
    if ctx is None:
        return default
    value = ctx.get(name) if isinstance(ctx, Mapping) else getattr(ctx, name, None)
    return str(value) if value else default


def _security_context() -> tuple[str, str]:
    ctx = _g_security_context()
    return _ctx_field(ctx, "tenant_id", "default"), _ctx_field(ctx, "classification", "CUI")


def _current_user() -> str:
    ctx = _g_security_context()
    if ctx is None:
        return "current_user"
    user = _ctx_field(ctx, "user_id", "")
    return user or _ctx_field(ctx, "username", "current_user")


def _record_history(session_id: str, mode: str, domain: str, query_text: str,
                    result_count: int = 0, strategy: str = "", grounded: bool = False) -> None:
    """Best-effort insert into cortex_search_history (never raises)."""
    tenant_id, classification = _security_context()
    try:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO cortex_search_history "
                "(query_id, session_id, user_id, mode, domain, query_text, strategy, "
                "result_count, grounded, classification, tenant_id, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (uuid.uuid4().hex, session_id, _current_user(), mode, domain, query_text,
                 strategy, result_count, grounded, classification, tenant_id, _now()),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("cortex: history insert skipped: %s", exc)


# ── Page Routes ─────────────────────────────────────────────────────────────────

@cortex_bp.route("/")
def index():
    """Cortex canvas landing — mode + domain-lens picker and chat surface."""
    return render_template(
        "cortex/index.html",
        modes=CORTEX_MODES,
        domain_lenses=CORTEX_DOMAIN_LENSES,
        default_mode=DEFAULT_MODE,
        default_domain=DEFAULT_DOMAIN,
    )


# ── Observability panel (read-only over the cortex_audit trail) ──────────────────
#
# All three routes below call the same ``metrics.summarize()``, which memoizes
# successful results for a few seconds keyed by window + RLS boundary. Rendering
# the page while the home tile polls therefore costs ONE scan of cortex_audit,
# not three.

def _metrics_window() -> int:
    try:
        return max(1, min(int(request.args.get("hours", 24)), 24 * 30))
    except (TypeError, ValueError):
        return 24


@cortex_bp.route("/metrics")
def metrics_page():
    """GET /cortex/metrics — governance/usage/spend observability panel."""
    from tools.cortex.metrics import summarize
    stats = summarize(window_hours=_metrics_window())
    return render_template("cortex/metrics.html", stats=stats)


@cortex_bp.route("/api/metrics", methods=["GET"])
def api_metrics():
    """GET /cortex/api/metrics — JSON governance/usage/spend metrics."""
    from tools.cortex.metrics import summarize
    return jsonify(summarize(window_hours=_metrics_window()))


@cortex_bp.route("/api/metrics/tile", methods=["GET"])
def api_metrics_tile():
    """GET /cortex/api/metrics/tile — compact governance summary for the home
    monitor card. Governance-first (calls / block rate / cost); cache hits are a
    secondary field. Distinct from the LLM prompt-cache card (token economics)."""
    from tools.cortex.metrics import summarize
    stats = summarize(window_hours=_metrics_window())
    s = stats["summary"]
    return jsonify({
        "available": stats["available"],
        # "ok" | "idle" (healthy, no traffic in window) | "unavailable" (broken).
        # The tile renders idle and unavailable differently — an operator must be
        # able to tell "governance is quiet" from "governance metrics are down".
        "status": stats.get("status", "ok"),
        "last_call_at": stats.get("last_call_at", ""),
        "window_hours": stats["window_hours"],
        "calls": s["calls"],
        "blocked": s["blocked"],
        "block_rate_pct": s["block_rate_pct"],
        "redactions": s["redactions"],
        "cost_usd": s["cost_usd"],
        "cache_hits": s["cache_hits"],
        # calls/blocked/block_rate are exact over the whole window; cost is
        # derived from the bounded gates_json detail read, so say when that read
        # was capped rather than let a partial spend figure read as the total.
        "detail_truncated": bool((stats.get("detail") or {}).get("truncated")),
    })


# ── API Routes ──────────────────────────────────────────────────────────────────

def _cortex_context(domain: str):
    """Build a CortexContext from the request's security context + domain lens."""
    from tools.cortex.schemas import CortexContext

    tenant_id, classification = _security_context()
    return CortexContext(
        tenant_id=tenant_id,
        user_id=_current_user(),
        classification=classification,
        domain=domain if domain != DEFAULT_DOMAIN else "",
    )


def _serialize_citations(citations) -> list:
    """Normalize a list of Citation dataclasses (or dicts) to JSON-able dicts."""
    out = []
    for c in citations or []:
        if hasattr(c, "to_dict"):
            out.append(c.to_dict())
        elif isinstance(c, dict):
            out.append(c)
    return out


def _resolve_facade(question: str, requested_mode: str) -> tuple[str, dict]:
    """Pick the cortex.* facade: explicit mode override, else intent routing.

    A concrete mode from the picker (one of CORTEX_MODE_KEYS) is honored as a
    manual override. Anything else — absent, ``auto``, or unknown — is routed
    by the intent classifier so a bare chat message reaches the right facade.
    """
    if requested_mode in CORTEX_MODE_KEYS:
        return requested_mode, {
            "intent": requested_mode,
            "facade": requested_mode,
            "confidence": 1.0,
            "reason": "explicit mode selection",
            "requires_confirm": requested_mode == "agent",
            "source": "user",
        }
    from tools.cortex import intent_router

    decision = intent_router.route(question)
    decision["source"] = "auto"
    return decision["facade"], decision


def _propose_roles(question: str) -> list[dict]:
    """Best-effort roster preview for an agent proposal.

    Runs the same classifier a launch would, so the confirm card shows the team
    the user is actually approving rather than an unspecified "an agent". Purely
    advisory: any failure yields an empty roster and the proposal still renders.
    """
    try:
        # Canonical namespace, and here it is load-bearing rather than stylistic:
        # in a source checkout `tools.ace.problem_classifier` loads a *second*
        # copy of the module, whose `ProblemClassifierLens` is a different class
        # object from the one `icdev.tools.ace.*` (i.e. ACE itself) uses, with its
        # own role-loader state. Keep the `icdev.` prefix. See
        # docs/features/cortex-unified-ai-layer.md, "Import namespace".
        from icdev.tools.ace.problem_classifier import ProblemClassifierLens

        lens = ProblemClassifierLens(question)
        manifest = lens.run()
        known = {r.role_id: r for r in lens._role_loader.list_roles()}
        roster: list[dict] = []
        for slot in getattr(manifest, "slots", []) or []:
            role = known.get(slot.role_id)
            roster.append({
                "role_id": slot.role_id,
                "display_name": getattr(role, "display_name", "") or slot.role_id,
                "count": getattr(slot, "count", 1),
                "exists": role is not None,
            })
        return roster
    except Exception as exc:  # noqa: BLE001 — proposal must render regardless
        logger.debug("cortex: role proposal unavailable: %s", exc)
        return []


def _agent_proposal(question: str) -> dict:
    """The unconfirmed branch: describe what WOULD run, and ask."""
    roster = _propose_roles(question)
    if roster:
        names = ", ".join(r["display_name"] for r in roster)
        answer = (
            "This looks like a multi-step goal best handled by a Cortex agent team. "
            f"Proposed roster: {names}. Agents can take actions across the platform, "
            "so they are not launched automatically — confirm to proceed."
        )
    else:
        answer = (
            "This looks like a multi-step goal best handled by a Cortex agent "
            "(cortex.agent). Agents can take actions across the platform, so "
            "they are not launched automatically — confirm to proceed."
        )
    return {
        "answer": answer,
        "grounded": False,
        "confidence": "",
        "citations": [],
        "governance": {"gates_run": [], "outcomes": {}, "blocked": False},
        "requires_confirm": True,
        "degraded": False,
        "proposed_roles": roster,
    }


def _launch_confirmed_agent(question: str, ctx) -> dict:
    """The confirmed branch: actually launch, through the governed facade.

    Goes through ``cortex_api.agent`` rather than ``ACEController`` directly so
    the launch inherits the TRUST pipeline — governance matters most at the
    moment something is authorised to act.
    """
    from tools.cortex import api as cortex_api

    roster = _propose_roles(question)
    role_ids = [r["role_id"] for r in roster if r.get("exists")]

    try:
        result = cortex_api.agent(
            question,
            roles=role_ids or None,
            ctx=ctx,
            mode="auto",          # roles present -> team; absent -> single loop
            trigger_source="cortex.chat",
        )
    except Exception as exc:  # noqa: BLE001 — never 500 the chat route
        logger.warning("cortex: confirmed agent launch failed: %s", exc)
        return {
            "answer": f"The agent could not be launched: {exc}",
            "grounded": False,
            "confidence": "",
            "citations": [],
            "governance": {"gates_run": [], "outcomes": {}, "blocked": False},
            "requires_confirm": False,
            "degraded": True,
        }

    data = getattr(result, "data", None) or {}
    instance_id = str(data.get("instance_id") or "")
    answer = getattr(result, "text", "") or "Agent launched."
    deep_link = f"/coworker/{instance_id}" if instance_id else ""
    if deep_link:
        answer = f"{answer}\n\n[View the team's progress]({deep_link})"

    return {
        "answer": answer,
        "grounded": False,
        "confidence": "",
        "citations": [],
        "governance": _governance_summary(result),
        "requires_confirm": False,
        "degraded": False,
        "instance_id": instance_id,
        "deep_link": deep_link,
        "launched_roles": role_ids,
    }


def _governance_summary(result) -> dict:
    """Normalise a CortexResult's governance report into the chat shape."""
    report = getattr(result, "governance", None)
    if report is None:
        return {"gates_run": [], "outcomes": {}, "blocked": False}
    return {
        "gates_run": list(getattr(report, "gates_run", []) or []),
        "outcomes": dict(getattr(report, "outcomes", {}) or {}),
        "blocked": bool(getattr(report, "blocked", False)),
    }


def _run_facade(facade: str, question: str, ctx, confirm_agent: bool) -> dict:
    """Dispatch one chat turn to the resolved cortex.* facade.

    Returns a normalized response dict. Facade errors degrade to an ungrounded,
    citation-free answer (HTTP 200) — the endpoint never fabricates evidence
    and never 500s on a downstream failure.
    """
    from tools.cortex import api as cortex_api

    if facade == "agent":
        # TRUST + safety: never auto-launch an agent loop / ACE team from chat.
        # An explicit confirm is the human approval; without it we only propose.
        if not confirm_agent:
            return _agent_proposal(question)
        return _launch_confirmed_agent(question, ctx)

    try:
        if facade == "search":
            results = cortex_api.search(question, ctx=ctx)
            return _response_from_search(results)
        if facade == "ask":
            result = cortex_api.ask(question, ctx=ctx)
            return _response_from_result(result)
        if facade == "complete":
            result = cortex_api.complete(question, ctx=ctx)
            # complete() is generative + ungrounded by construction.
            return _response_from_result(result, grounded_override=False)
        # classify / extract / govern need structured params a free-form chat
        # turn doesn't carry — surface that rather than guessing.
        return _degraded(
            f"The '{facade}' facade needs structured inputs (labels / schema / "
            "sources) not available from a chat message. Use the API directly "
            "or pick search / ask / complete.",
            facade,
        )
    except Exception as exc:  # noqa: BLE001 — degrade, never 500
        logger.warning("cortex.chat: facade %s failed: %s", facade, exc)
        return _degraded(
            f"The '{facade}' facade could not answer this right now ({exc}). "
            "Your message was recorded.",
            facade,
        )


def _response_from_result(result, grounded_override=None) -> dict:
    """Normalize a CortexResult into the chat response shape."""
    grounded = result.grounded if grounded_override is None else grounded_override
    return {
        "answer": result.text or "",
        "grounded": bool(grounded),
        "confidence": str(result.metadata.get("confidence", "")),
        "citations": _serialize_citations(result.citations),
        "governance": result.governance.to_dict() if result.governance else {},
        "requires_confirm": False,
        "degraded": False,
    }


def _response_from_search(results: list) -> dict:
    """Synthesize a chat answer from a list of CortexSearchResult hits.

    ``results`` may be a ``search_service.BackendResults`` carrying ``.errors``.
    Zero hits with a recorded backend failure is NOT "nothing matched" — saying
    so is how a dead embedding provider was reported to the user as an empty
    corpus (ctx-perf-04). Those two cases get different answers here.
    """
    citations = []
    lines = []
    for i, r in enumerate(results, 1):
        cit = getattr(r, "citation", None)
        if cit and getattr(cit, "source_id", ""):
            citations.append(cit.to_dict())
        snippet = (getattr(r, "content", "") or "").strip().replace("\n", " ")
        if snippet:
            lines.append(f"{i}. {snippet[:220]}")
    grounded = bool(citations)
    errors = list(getattr(results, "errors", ()) or ())
    degraded = bool(errors) and not lines
    if lines:
        answer = f"Found {len(results)} result(s):\n" + "\n".join(lines[:5])
    elif degraded:
        detail = "; ".join(
            f"{e.get('backend', '?')} ({e.get('stage', 'error')}): "
            f"{e.get('message', '')}".strip()
            for e in errors
        )
        answer = (
            "Search could not run — this is a retrieval FAILURE, not an empty "
            f"result set, so the corpus may well contain an answer: {detail}"
        )
    else:
        answer = "No matching results were found across the Cortex backends."
    return {
        "answer": answer,
        "grounded": grounded,
        "confidence": "include" if grounded else "abstain",
        "citations": citations,
        "governance": {
            "gates_run": ["retrieval"],
            "outcomes": {
                "retrieval": "pass" if results else ("error" if degraded else "warn")
            },
            "blocked": False,
            "backend_errors": errors,
        },
        "requires_confirm": False,
        "degraded": degraded,
    }


def _degraded(message: str, facade: str) -> dict:
    return {
        "answer": message,
        "grounded": False,
        "confidence": "",
        "citations": [],
        "governance": {"gates_run": [], "outcomes": {}, "blocked": False},
        "requires_confirm": False,
        "degraded": True,
    }


@cortex_bp.route("/api/chat", methods=["POST"])
def api_chat():
    """Route a chat message to the right Cortex facade and persist the turn.

    Body: ``{question, mode?, domain?, session_id?, confirm_agent?}``

    A concrete ``mode`` (one of the eight facades) is honored as a manual
    override; otherwise the message is classified (retrieval → search,
    data-question → ask, generative → complete, multi-step goal → agent behind
    a confirm affordance). The user turn and the assistant turn are persisted to
    cortex_messages so the session reloads via ``GET /api/session/<id>``.

    Returns the answer plus routing provenance, citations, a grounded/confidence
    badge, and a GovernanceReport summary (which gates ran). Ungrounded answers
    carry ``grounded=False`` so the UI shows the visible banner. Facade failures
    degrade to an ungrounded answer — this route never 500s on a downstream error.
    """
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    requested_mode = (data.get("mode") or "auto").strip().lower()
    domain = (data.get("domain") or DEFAULT_DOMAIN).strip().lower()
    if domain not in CORTEX_DOMAIN_KEYS:
        domain = DEFAULT_DOMAIN
    session_id = (data.get("session_id") or uuid.uuid4().hex).strip()
    confirm_agent = bool(data.get("confirm_agent"))

    facade, routing = _resolve_facade(question, requested_mode)
    ctx = _cortex_context(domain)
    outcome = _run_facade(facade, question, ctx, confirm_agent)

    # THIN session persistence: session row + user turn + assistant turn.
    tenant_id, classification = _security_context()
    _persist_turn(session_id, facade, domain, question, outcome, tenant_id, classification)
    _record_history(session_id, facade, domain, question, grounded=outcome["grounded"])

    return jsonify({
        "answer": outcome["answer"],
        "mode": facade,
        "intent": routing.get("intent", facade),
        "routing": routing,
        "domain": domain,
        "session_id": session_id,
        "grounded": outcome["grounded"],
        "confidence": outcome["confidence"],
        "citations": outcome["citations"],
        "governance": outcome["governance"],
        "requires_confirm": outcome["requires_confirm"],
        "degraded": outcome["degraded"],
        # Agent-facade extras. Present only on the agent path: the roster the
        # user is being asked to approve, and — once confirmed — the launched
        # instance and its deep link.
        "proposed_roles": outcome.get("proposed_roles", []),
        "instance_id": outcome.get("instance_id", ""),
        "deep_link": outcome.get("deep_link", ""),
    })


def _persist_turn(session_id, facade, domain, question, outcome, tenant_id, classification):
    """Best-effort THIN persistence of one user+assistant exchange."""
    try:
        from tools.cortex import chat_session

        chat_session.ensure_session(
            session_id, user_id=_current_user(), mode=facade, domain=domain,
            tenant_id=tenant_id, classification=classification, title=question,
        )
        chat_session.record_turn(
            session_id, "user", question, facade=facade,
            tenant_id=tenant_id, classification=classification,
        )
        chat_session.record_turn(
            session_id, "assistant", outcome["answer"], facade=facade,
            grounded=outcome["grounded"], confidence=outcome["confidence"],
            citations=outcome["citations"], governance=outcome["governance"],
            tenant_id=tenant_id, classification=classification,
        )
        # facade-outcome audit is written by the governance pipeline into the
        # canonical cortex_audit table (ctx-govern-03/04) — not duplicated here.
    except Exception as exc:  # noqa: BLE001 — persistence never breaks the answer
        logger.debug("cortex.chat: turn persistence skipped: %s", exc)


@cortex_bp.route("/api/session/<session_id>", methods=["GET"])
def api_session(session_id):
    """Reload a session's metadata and persisted turns (conversation history)."""
    from tools.cortex import chat_session

    session = chat_session.load_session(session_id)
    turns = chat_session.load_turns(session_id)
    return jsonify({
        "session_id": session_id,
        "session": session,
        "turns": turns,
        "turn_count": len(turns),
    })


def _open_query_connection():
    """Open the connection the IQE route executes on (seam for tests)."""
    return _conn()


@cortex_bp.route("/api/iqe-query", methods=["POST"])
def api_iqe_query():
    """IQE natural-language query over the cortex.* collections.

    Mirrors the canonical dispatcher (app.py::iqe_dispatch): translate the
    question to IQE via nl_to_iqe (which always degrades to a valid select-all
    fallback — never raises), parse, and execute. An IQE syntax error yields an
    empty result set rather than a 500, so an unparseable translation still
    returns 200.

    Two things it does NOT copy from that dispatcher (ctx-trust-05):

    **The security context is threaded explicitly, not inherited.** Calling
    ``execute_query(ast, conn=None)`` makes every adapter open its OWN
    connection, so tenant scope and Bell-LaPadula read-down hold only as far as
    ``get_connection()`` happens to find a populated ``flask.g.security_context``
    — and when it does not, the query runs UNSCOPED and reads every tenant's
    rows while returning 200. This route opens one connection and applies the
    caller's CortexContext to it through the same ``_apply_security_context``
    the analyst path uses, which refuses (or at minimum warns) rather than
    falling through unscoped. Cortex is the component that enforces tenant
    isolation, so its own query surface may not be the one that assumes it.

    **The result set is bounded.** See ``IQE_MAX_ROWS``.
    """
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    iqe_str = ""
    try:
        from tools.iqe.adapters import cortex as _  # noqa: F401  registers collections
        from tools.iqe.executor import execute_query
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import IQESyntaxError, parse as iqe_parse

        from tools.cortex.analyst import _apply_security_context
        from tools.cortex.constants import IQE_COLLECTIONS, IQE_MAX_ROWS

        result = nl_to_iqe(question, list(IQE_COLLECTIONS))
        iqe_str = result.get("iqe", "")
        explanation = result.get("explanation", "")
        try:
            ast = iqe_parse(iqe_str)
        except IQESyntaxError:
            ast = None

        if ast is None:
            rows = []
        else:
            conn = _open_query_connection()
            try:
                # Explicit tenant + classification, never ambient g state.
                # Raises (fail-closed) or warns when the connection cannot
                # carry it — either way it is closed, not leaked.
                _apply_security_context(conn, _cortex_context(DEFAULT_DOMAIN))
                rows = execute_query(ast, conn)
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001, S110
                    pass

        truncated = len(rows) > IQE_MAX_ROWS
        rows = rows[:IQE_MAX_ROWS]
        return jsonify({
            "ok": True,
            "iqe": iqe_str,
            "explanation": explanation,
            "results": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "max_rows": IQE_MAX_ROWS,
        })
    except Exception as exc:
        logger.warning("cortex: iqe-query error: %s", exc)
        return jsonify({"error": str(exc), "iqe": iqe_str}), 500


# ---- Cortex REST API v1 (ctx-expose-02) ----
# Fold the programmatic /cortex/api/v1/* surface onto THIS canvas blueprint so
# the web canvas and the machine API share one Blueprint, one url_prefix, and
# one auth path (the dashboard auth middleware, which does not defer
# /cortex/api/v1 to the JWT-only /api/v1 seam).
from .rest_v1 import register_rest_v1  # noqa: E402

register_rest_v1(cortex_bp)

# ---- RICOAS intake bridge (prem-ricoas-02) ----
# PMO-facing external apps (compass Requirements Portal) create/continue REAL
# RICOAS intake sessions over /cortex/api/v1/intake/* — same blueprint, same
# auth path, scope cortex:intake.
from .rest_intake import register_rest_intake  # noqa: E402

register_rest_intake(cortex_bp)
