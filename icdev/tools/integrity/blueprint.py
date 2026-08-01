# CUI // SP-CTI
"""SIPA — Software Integrity & Provenance Assessor — Flask Blueprint.

:func:`create_integrity_blueprint` returns the canvas blueprint, or ``None`` when
the ``ICDEV_INTEGRITY_ENABLED`` feature flag is off, so ``app.py`` simply skips
registration (the canvas is fully dark when toggled off).

Pages (mounted under ``/integrity``):
  GET  /integrity                              assessment list + verdict roll-up
  GET  /integrity/<assessment_id>              detail: capabilities + findings +
                                               verdict + HITL promote/reject controls

JSON API (mounted under ``/api/integrity``):
  POST /api/integrity/assess                   body {source, mode?, project_id?,
                                               session_id?, declared_purpose?}
  GET  /api/integrity/assessments              list (status / verdict / limit filters)
  GET  /api/integrity/assessment/<id>          detail (assessment + caps + findings + verdict)
  POST /api/integrity/assessment/<id>/promote  HITL approve  {reason?}  (reviewer = authed user)
  POST /api/integrity/assessment/<id>/reject   HITL reject   {reason?}  (reviewer = authed user)

Although the task spec names ``url_prefix='/integrity'``, the page and API mounts
live on two different roots (``/integrity`` vs ``/api/integrity``) — Flask cannot
serve both from one global ``url_prefix`` — so each route carries its explicit
path, matching the dominant ICDEV canvas idiom (``supply_chain``,
``migration_intelligence``). The factory returns a single blueprint that
``app.py`` registers with no prefix override.

SECURITY / RLS:
  * **Reads** run through ``tools.db.storage.get_connection()``, which
    auto-attaches the request's ``g.security_context`` (the integrity_* tables
    carry ``tenant_id`` / ``classification``, so the global RLS predicate filters
    every query to the caller's tenant + clearance).
  * **Writes** (assess / promote / reject) delegate to ``tools.integrity.engine``,
    which owns quarantine-first staging, append-only findings, the only path out
    of quarantine (HITL promote/reject), and the append-only audit trail.

Import-safe: every handler wraps its backing call in ``try/except`` so a partially
initialized schema (migration not yet run) or a missing page template (shipped by
the sibling templates task) degrades to an empty list / JSON fallback instead of a
500.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from flask import Blueprint, g, jsonify, render_template, request

from tools.integrity.constants import (
    ASSESS_MODES,
    FEATURE_FLAG,
    SEVERITY,
    VERDICTS,
)

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.integrity.blueprint")

# Default page-list size; callers can widen via ?limit=.
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500


# --------------------------------------------------------------------------- #
# Feature flag
# --------------------------------------------------------------------------- #
def _is_enabled() -> bool:
    """True when SIPA is toggled on via ``ICDEV_INTEGRITY_ENABLED``.

    Re-implemented here (rather than importing ``engine.is_enabled``) so the
    factory can decide whether to build the blueprint without importing the whole
    engine + scanner stack at app-startup time.
    """
    import os

    return str(os.environ.get(FEATURE_FLAG, "")).strip().lower() in (
        "1", "true", "yes", "on", "enabled",
    )


# --------------------------------------------------------------------------- #
# DB helpers
# --------------------------------------------------------------------------- #
def _db():
    """An RLS-aware connection. ``get_connection`` auto-attaches the request's
    ``g.security_context`` so every read is filtered to the caller's tenant +
    classification (the integrity_* tables carry both columns)."""
    from tools.db.storage import get_connection

    return get_connection()


def _rows(cur) -> list[dict]:
    """Materialize a cursor into a list of plain dicts (backend-agnostic)."""
    desc = getattr(cur, "description", None)
    if not desc:
        return []
    cols = [d[0] for d in desc]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _one(cur) -> Optional[dict]:
    """Materialize a single cursor row into a dict, or ``None``."""
    desc = getattr(cur, "description", None)
    row = cur.fetchone()
    if row is None or not desc:
        return None
    return dict(zip([d[0] for d in desc], row))


def _json(value: Any) -> Any:
    """Decode a JSON(B) column to a Python value, tolerating already-decoded
    objects (PG JSONB) and malformed / NULL text (SQLite)."""
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _q(conn, sql: str) -> str:
    """Translate ``?`` placeholders to ``%s`` for the PostgreSQL backend."""
    import os

    declared = getattr(conn, "_backend", None)
    if declared:
        is_pg = str(declared).lower().startswith(("postgre", "pg"))
    else:
        is_pg = os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower() in (
            "postgresql", "postgres", "pg",
        )
    return sql.replace("?", "%s") if is_pg else sql


def _reviewer() -> str:
    """The HITL reviewer recorded on promote/reject — the authenticated user when
    a request context carries one, else a generic ``dashboard`` actor."""
    try:
        user = getattr(g, "current_user", None)
        if isinstance(user, dict):
            return user.get("username") or user.get("email") or "dashboard"
        if user:
            return str(getattr(user, "username", None) or user)
    except RuntimeError:
        pass
    return "dashboard"


def _limit_arg() -> int:
    """Parse a bounded ``?limit=`` query argument."""
    try:
        n = int(request.args.get("limit", _DEFAULT_LIMIT))
    except (TypeError, ValueError):
        n = _DEFAULT_LIMIT
    return max(1, min(n, _MAX_LIMIT))


# --------------------------------------------------------------------------- #
# Queries (RLS-aware reads)
# --------------------------------------------------------------------------- #
def _list_assessments(conn, *, status: Optional[str], verdict: Optional[str], limit: int) -> list[dict]:
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if verdict:
        where.append("verdict = ?")
        params.append(verdict)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    sql = (
        "SELECT id, source_type, source_ref, mode, project_id, session_id, "
        "status, verdict, risk_score, created_at, updated_at "
        f"FROM integrity_assessments {clause} ORDER BY id DESC LIMIT ?"
    )
    params.append(limit)
    return _rows(conn.execute(_q(conn, sql), tuple(params)))


def _get_assessment(conn, assessment_id: int) -> Optional[dict]:
    sql = (
        "SELECT id, source_type, source_ref, mode, project_id, session_id, "
        "dir_digest, status, verdict, risk_score, classification, created_by, "
        "created_at, updated_at FROM integrity_assessments WHERE id = ?"
    )
    return _one(conn.execute(_q(conn, sql), (assessment_id,)))


def _get_capabilities(conn, assessment_id: int) -> list[dict]:
    sql = (
        "SELECT id, file_path, function_name, capability_type, evidence, "
        "line_start, line_end, risk_weight FROM integrity_capabilities "
        "WHERE assessment_id = ? ORDER BY risk_weight DESC, id ASC"
    )
    caps = _rows(conn.execute(_q(conn, sql), (assessment_id,)))
    for c in caps:
        c["evidence"] = _json(c.get("evidence"))
    return caps


def _get_findings(conn, assessment_id: int) -> list[dict]:
    sql = (
        "SELECT id, source_scanner, finding_type, severity, file_path, line, detail "
        "FROM integrity_findings WHERE assessment_id = ? ORDER BY id ASC"
    )
    findings = _rows(conn.execute(_q(conn, sql), (assessment_id,)))
    # Stable severity ordering for the UI (critical first).
    rank = {s: i for i, s in enumerate(SEVERITY)}
    for f in findings:
        f["detail"] = _json(f.get("detail"))
    findings.sort(key=lambda f: rank.get(f.get("severity"), len(SEVERITY)))
    return findings


def _get_verdict(conn, assessment_id: int) -> Optional[dict]:
    sql = (
        "SELECT id, verdict, risk_score, rationale, decided_by, created_at "
        "FROM integrity_verdicts WHERE assessment_id = ? ORDER BY id DESC LIMIT 1"
    )
    v = _one(conn.execute(_q(conn, sql), (assessment_id,)))
    if v:
        v["rationale"] = _json(v.get("rationale"))
    return v


def _get_authorizations(conn, assessment_id: int) -> list[dict]:
    sql = (
        "SELECT id, capability_id, requirement_id, claim_ref, authorized, reason, "
        "reviewed_by, created_at FROM integrity_authorizations "
        "WHERE assessment_id = ? ORDER BY id ASC"
    )
    return _rows(conn.execute(_q(conn, sql), (assessment_id,)))


def _assessment_detail(conn, assessment_id: int) -> Optional[dict]:
    """Assemble the full detail payload for one assessment, or ``None`` if absent."""
    assessment = _get_assessment(conn, assessment_id)
    if assessment is None:
        return None
    return {
        "assessment": assessment,
        "capabilities": _get_capabilities(conn, assessment_id),
        "findings": _get_findings(conn, assessment_id),
        "verdict": _get_verdict(conn, assessment_id),
        "authorizations": _get_authorizations(conn, assessment_id),
        "is_terminal": assessment.get("status") in ("approved", "rejected"),
    }


def _verdict_rollup(assessments: list[dict]) -> dict:
    """Count assessments per verdict + per status for the index summary cards."""
    by_verdict = {v: 0 for v in VERDICTS}
    by_status: dict[str, int] = {}
    for a in assessments:
        v = a.get("verdict")
        if v in by_verdict:
            by_verdict[v] += 1
        s = a.get("status")
        if s:
            by_status[s] = by_status.get(s, 0) + 1
    return {"by_verdict": by_verdict, "by_status": by_status, "total": len(assessments)}


# --------------------------------------------------------------------------- #
# Blueprint factory
# --------------------------------------------------------------------------- #
def create_integrity_blueprint() -> Optional[Blueprint]:
    """Build the SIPA canvas blueprint, or ``None`` when the canvas is toggled off.

    Returns ``None`` if ``ICDEV_INTEGRITY_ENABLED`` is not truthy so ``app.py`` can
    register-or-skip with a single guard. When enabled, returns a blueprint serving
    the two ``/integrity`` pages and the five ``/api/integrity`` JSON endpoints.
    """
    if not _is_enabled():
        logger.info("integrity blueprint: %s is off — canvas not mounted", FEATURE_FLAG)
        return None

    bp = Blueprint("integrity", __name__)

    # Idempotent one-time schema guard. ``init_db`` is CREATE-IF-NOT-EXISTS, but
    # running it on every request would open/close a connection needlessly, so we
    # fire it once on the blueprint's first served request.
    _state = {"db_ready": False}

    @bp.before_request
    def _ensure_db() -> None:
        if _state["db_ready"]:
            return
        try:
            from tools.integrity.db.init_db import init_db

            init_db()
            _state["db_ready"] = True
        except Exception as exc:  # noqa: BLE001 — never let init failure 500 a read
            logger.warning("integrity before_request init_db failed: %s", exc)

    # ── Pages ─────────────────────────────────────────────────────────────── #
    @bp.route("/integrity")
    @bp.route("/integrity/")
    def integrity_index():
        """Assessment list + verdict roll-up (RLS-filtered to the caller)."""
        assessments: list[dict] = []
        conn = None
        try:
            conn = _db()
            assessments = _list_assessments(conn, status=None, verdict=None, limit=_DEFAULT_LIMIT)
        except Exception as exc:  # noqa: BLE001 — empty state before first assessment / migration
            logger.warning("integrity_index read failed: %s", exc)
        finally:
            if conn is not None:
                conn.close()

        summary = _verdict_rollup(assessments)
        try:
            return render_template(
                "integrity/index.html",
                assessments=assessments,
                summary=summary,
                verdicts=VERDICTS,
                modes=ASSESS_MODES,
            )
        except Exception as exc:  # noqa: BLE001 — templates ship in the sibling task
            logger.info("integrity/index.html unavailable (%s); JSON fallback", exc)
            return jsonify({"assessments": assessments, "summary": summary})

    @bp.route("/integrity/<int:assessment_id>")
    def integrity_detail(assessment_id: int):
        """Detail page: capabilities + findings + verdict + promote/reject controls."""
        detail = None
        conn = None
        try:
            conn = _db()
            detail = _assessment_detail(conn, assessment_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("integrity_detail read failed for %s: %s", assessment_id, exc)
        finally:
            if conn is not None:
                conn.close()

        if detail is None:
            return jsonify({"error": f"assessment not found: {assessment_id}"}), 404

        try:
            return render_template(
                "integrity/assessment.html",
                assessment_id=assessment_id,
                verdicts=VERDICTS,
                **detail,
            )
        except Exception as exc:  # noqa: BLE001 — templates ship in the sibling task
            logger.info("integrity/assessment.html unavailable (%s); JSON fallback", exc)
            return jsonify(detail)

    # ── JSON API — reads ──────────────────────────────────────────────────── #
    @bp.route("/api/integrity/assessments", methods=["GET"])
    def api_assessments():
        status = request.args.get("status")
        verdict = request.args.get("verdict")
        limit = _limit_arg()
        conn = None
        try:
            conn = _db()
            items = _list_assessments(conn, status=status, verdict=verdict, limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_assessments read failed: %s", exc)
            items = []
        finally:
            if conn is not None:
                conn.close()
        return jsonify({"assessments": items, "count": len(items)})

    @bp.route("/api/integrity/assessment/<int:assessment_id>", methods=["GET"])
    def api_assessment(assessment_id: int):
        conn = None
        try:
            conn = _db()
            detail = _assessment_detail(conn, assessment_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_assessment read failed for %s: %s", assessment_id, exc)
            detail = None
        finally:
            if conn is not None:
                conn.close()
        if detail is None:
            return jsonify({"error": f"assessment not found: {assessment_id}"}), 404
        return jsonify(detail)

    # ── JSON API — IQE natural-language query ─────────────────────────────── #
    @bp.route("/integrity/api/iqe-query", methods=["POST"])
    def integrity_iqe_query():
        """Plain-English → IQE → rows over the integrity_* collections.

        Body: ``{question, execute?}``. Mirrors the canvas-aware dispatcher in
        ``app.py`` (``nl_to_iqe`` → ``parse`` → ``execute_query``) so the shared
        ``includes/iqe_query_widget.html`` widget renders the generated IQE plus
        the matching rows. Reads run RLS-aware via the registered adapters.
        """
        collections = [
            "integrity.assessments",
            "integrity.capabilities",
            "integrity.findings",
            "integrity.verdicts",
        ]
        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"error": "question is required"}), 400

        iqe_str = ""
        try:
            from tools.iqe import adapters as _adapters  # noqa: F401
            from tools.iqe.adapters import integrity as _  # noqa: F401  registers collections
            from tools.iqe.executor import execute_query
            from tools.iqe.nl_to_iqe import nl_to_iqe
            from tools.iqe.parser import IQESyntaxError, parse as iqe_parse

            translated = nl_to_iqe(question, collections)
            iqe_str = translated.get("iqe", "")
            explanation = translated.get("explanation", "")
            try:
                ast = iqe_parse(iqe_str)
                rows = execute_query(ast, conn=None)
            except IQESyntaxError:
                rows = []
            return jsonify({
                "ok": True, "canvas": "integrity", "iqe": iqe_str,
                "explanation": explanation, "results": rows, "row_count": len(rows),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("integrity iqe-query error: %s", exc)
            return jsonify({"error": str(exc), "canvas": "integrity", "iqe": iqe_str}), 500

    # ── JSON API — writes (delegate to the engine) ────────────────────────── #
    @bp.route("/api/integrity/assess", methods=["POST"])
    def api_assess():
        """Kick off a full static assessment of an untrusted ``source``.

        Body: ``{source, mode?, project_id?, session_id?, declared_purpose?}``.
        Delegates to ``engine.assess`` (quarantine-first; never executes the
        target) and returns its disposition. A refused source (bad scheme/host,
        missing path, failed clone) or an invalid mode is a 400.
        """
        data = request.get_json(force=True, silent=True) or {}
        source = (data.get("source") or "").strip()
        if not source:
            return jsonify({"error": "source required"}), 400

        from tools.integrity import engine
        from tools.integrity.ingest import IngestRejected

        try:
            result = engine.assess(
                source,
                mode=data.get("mode", "auto"),
                project_id=data.get("project_id"),
                session_id=data.get("session_id"),
                declared_purpose=data.get("declared_purpose"),
            )
        except IngestRejected as exc:
            return jsonify({"error": f"source rejected at ingest: {exc}"}), 400
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001 — surface as 500 with a message, not a stack
            logger.exception("api_assess failed for %s", source)
            return jsonify({"error": f"assessment failed: {exc}"}), 500
        return jsonify(result), 201

    @bp.route("/api/integrity/assessment/<int:assessment_id>/promote", methods=["POST"])
    def api_promote(assessment_id: int):
        return _hitl(assessment_id, authorized=True)

    @bp.route("/api/integrity/assessment/<int:assessment_id>/reject", methods=["POST"])
    def api_reject(assessment_id: int):
        return _hitl(assessment_id, authorized=False)

    def _hitl(assessment_id: int, *, authorized: bool):
        """Shared HITL transition for promote/reject — the only path out of
        quarantine. Delegates to the engine (which appends an authorization row +
        audit event); a missing / already-terminal assessment is a 409."""
        data = request.get_json(force=True, silent=True) or {}
        # nav-comp-06: the HITL reviewer recorded on promote/reject is bound to
        # the authenticated user (g.current_user via _reviewer()), NEVER the
        # request body — a caller must not attribute a quarantine decision to
        # someone else. Any body-supplied ``reviewed_by`` is intentionally ignored.
        reviewed_by = _reviewer()
        reason = data.get("reason")

        from tools.integrity import engine

        fn = engine.promote if authorized else engine.reject
        try:
            result = fn(assessment_id, reviewed_by, reason)
        except Exception as exc:  # noqa: BLE001
            logger.exception("integrity HITL %s failed for %s",
                             "promote" if authorized else "reject", assessment_id)
            return jsonify({"error": f"decision failed: {exc}"}), 500

        if result.get("error"):
            # Missing assessment vs. already-decided: 404 vs 409.
            code = 404 if "not found" in str(result["error"]) else 409
            return jsonify(result), code
        return jsonify(result)

    logger.info("integrity blueprint mounted (pages + JSON API)")
    return bp
