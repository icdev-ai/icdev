# CUI // SP-CTI
"""EQO centralized-logging dashboard blueprint (eqo-log-04).

:func:`create_logs_blueprint` returns the ``/logs`` canvas blueprint, or ``None``
when ``ICDEV_LOGS_ENABLED`` is off so ``app.py`` simply skips registration.

Pages:
  GET  /logs                       queryable log viewer (component/level/since/contains)

JSON API:
  GET  /api/logs                   same filters as query params → JSON rows
  POST /logs/api/iqe-query         plain-English → IQE → rows over logs.entries

All reads flow through :func:`tools.logging.log_query.query_logs`, which uses the
RLS-aware ``get_connection`` — every row is filtered to the caller's tenant +
classification. Every handler degrades gracefully (empty list / JSON fallback)
if the ``centralized_logs`` table or the page template is not yet present.
"""
from __future__ import annotations

from typing import Optional

from flask import Blueprint, jsonify, render_template, request

from tools.logging.constants import (
    DEFAULT_LIMIT,
    FEATURE_FLAG,
    LOG_LEVELS,
    MAX_LIMIT,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.logging.blueprint")


def _is_enabled() -> bool:
    """True when the /logs canvas is toggled on (defaults on)."""
    import os

    return str(os.environ.get(FEATURE_FLAG, "true")).strip().lower() in (
        "1", "true", "yes", "on", "enabled",
    )


def _limit_arg() -> int:
    try:
        n = int(request.args.get("limit", DEFAULT_LIMIT))
    except (TypeError, ValueError):
        n = DEFAULT_LIMIT
    return max(1, min(n, MAX_LIMIT))


def _filters() -> dict:
    """Extract the shared filter set from request query params."""
    return {
        "component": (request.args.get("component") or "").strip() or None,
        "level": (request.args.get("level") or "").strip() or None,
        "since": (request.args.get("since") or "").strip() or None,
        "contains": (request.args.get("contains") or "").strip() or None,
        "limit": _limit_arg(),
    }


def create_logs_blueprint() -> Optional[Blueprint]:
    """Build the /logs blueprint, or ``None`` when the canvas is toggled off."""
    if not _is_enabled():
        logger.info("logs blueprint: %s is off — page not mounted", FEATURE_FLAG)
        return None

    bp = Blueprint("logs", __name__)

    @bp.route("/logs")
    @bp.route("/logs/")
    def logs_index():
        """Queryable centralized-log viewer."""
        from tools.logging.log_query import query_logs

        f = _filters()
        rows: list[dict] = []
        try:
            rows = query_logs(**f)
        except Exception as exc:  # noqa: BLE001 — empty state before first log / migration
            logger.warning("logs_index read failed: %s", exc)

        try:
            return render_template(
                "logs/page.html",
                rows=rows,
                levels=LOG_LEVELS,
                filters=f,
                count=len(rows),
            )
        except Exception as exc:  # noqa: BLE001 — JSON fallback if template missing
            logger.info("logs/page.html unavailable (%s); JSON fallback", exc)
            return jsonify({"rows": rows, "count": len(rows)})

    @bp.route("/api/logs", methods=["GET"])
    def api_logs():
        """JSON log rows with the same filters as the page (query params)."""
        from tools.logging.log_query import query_logs

        f = _filters()
        try:
            rows = query_logs(**f)
        except Exception as exc:  # noqa: BLE001
            logger.warning("api_logs read failed: %s", exc)
            rows = []
        return jsonify({"rows": rows, "count": len(rows), "filters": f})

    @bp.route("/logs/api/iqe-query", methods=["POST"])
    def logs_iqe_query():
        """Plain-English → IQE → rows over the logs.entries collection."""
        collections = ["logs.entries"]
        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"error": "question is required"}), 400

        iqe_str = ""
        try:
            from tools.iqe.adapters import logs as _  # noqa: F401  registers collection
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
                "ok": True, "canvas": "logs", "iqe": iqe_str,
                "explanation": explanation, "results": rows, "row_count": len(rows),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("logs iqe-query error: %s", exc)
            return jsonify({"error": str(exc), "canvas": "logs", "iqe": iqe_str}), 500

    logger.info("logs blueprint mounted (/logs + /api/logs + IQE)")
    return bp
