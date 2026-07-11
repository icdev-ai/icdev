# CUI // SP-CTI
"""ICDEV Cortex Canvas — Flask Blueprint (skeleton).

The Snowflake-Intelligence-style entry point over the unified Cortex facade
(tools/cortex/api.py). This is the 8-gate canvas skeleton: it renders the
branded landing page, exposes a chat stub, and wires the IQE natural-language
query widget. Facade wiring (complete/classify/extract/search/ask/govern/agent)
lands in follow-on tasks; the chat endpoint degrades gracefully until then.

Routes:
  GET  /cortex/                 index — mode + domain-lens picker
  POST /cortex/api/chat         conversational stub (records session + history)
  POST /cortex/api/iqe-query    IQE natural-language query over cortex.* collections
"""
from __future__ import annotations

import uuid
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


def _security_context() -> tuple[str, str]:
    try:
        from flask import g
        ctx = getattr(g, "security_context", None) or {}
        return ctx.get("tenant_id", "default"), ctx.get("classification", "CUI")
    except Exception:
        return "default", "CUI"


def _current_user() -> str:
    try:
        from flask import g, has_request_context
        if has_request_context():
            ctx = getattr(g, "security_context", None) or {}
            return ctx.get("user_id") or ctx.get("username") or "current_user"
    except Exception:
        pass
    return "current_user"


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


# ── API Routes ──────────────────────────────────────────────────────────────────

@cortex_bp.route("/api/chat", methods=["POST"])
def api_chat():
    """Conversational stub over the Cortex facade.

    Body: {question, mode?, domain?, session_id?}
    Returns: {answer, mode, domain, session_id, grounded, citations, stub}

    Skeleton behaviour: validates input, normalizes mode/domain, records the
    turn in cortex_search_history, and returns a non-fabricated stub answer.
    No citations are invented — ``grounded`` is False and ``citations`` empty,
    so the TRUST contract (no uncited content presented as evidence) holds.
    """
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    mode = (data.get("mode") or DEFAULT_MODE).strip().lower()
    if mode not in CORTEX_MODE_KEYS:
        mode = DEFAULT_MODE
    domain = (data.get("domain") or DEFAULT_DOMAIN).strip().lower()
    if domain not in CORTEX_DOMAIN_KEYS:
        domain = DEFAULT_DOMAIN
    session_id = (data.get("session_id") or uuid.uuid4().hex).strip()

    _record_history(session_id, mode, domain, question)

    answer = (
        "Cortex canvas is live as a skeleton. The "
        f"'{mode}' facade over the '{domain}' domain lens is not yet wired into "
        "this endpoint — grounded answers with citations land in a follow-on task. "
        "Your query was recorded."
    )
    return jsonify({
        "answer": answer,
        "mode": mode,
        "domain": domain,
        "session_id": session_id,
        "grounded": False,
        "citations": [],
        "stub": True,
    })


@cortex_bp.route("/api/iqe-query", methods=["POST"])
def api_iqe_query():
    """IQE natural-language query over the cortex.* collections.

    Mirrors the canonical dispatcher (app.py::iqe_dispatch): translate the
    question to IQE via nl_to_iqe (which always degrades to a valid select-all
    fallback — never raises), parse, and execute. An IQE syntax error yields an
    empty result set rather than a 500, so an unparseable translation still
    returns 200.
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

        from tools.cortex.constants import IQE_COLLECTIONS

        result = nl_to_iqe(question, list(IQE_COLLECTIONS))
        iqe_str = result.get("iqe", "")
        explanation = result.get("explanation", "")
        try:
            ast = iqe_parse(iqe_str)
            rows = execute_query(ast, conn=None)
        except IQESyntaxError:
            rows = []
        return jsonify({
            "ok": True,
            "iqe": iqe_str,
            "explanation": explanation,
            "results": rows,
            "row_count": len(rows),
        })
    except Exception as exc:
        logger.warning("cortex: iqe-query error: %s", exc)
        return jsonify({"error": str(exc), "iqe": iqe_str}), 500
