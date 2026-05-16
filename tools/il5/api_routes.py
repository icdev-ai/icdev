# CUI // SP-CTI
"""ICDEV™ IL5 API Routes — /api/v1/il5.

Connects the IL5 ingestion service and display service into a single endpoint
with enforced 30-second SLA (IL5_PIPELINE_TIMEOUT_SECONDS).

NIST 800-53: AU-2, AC-3, SC-28, SI-12.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, request

from tools.il5.il5_display_service import render_il5_ui
from tools.il5.ingestion import get_il5_events
from tools.pipeline.sla_handler import il5_timeout_context

il5_api = Blueprint("il5_api", __name__)


def fetch_il5_data(
    *,
    since: Optional[str] = None,
    limit: int = 25,
) -> List[Dict[str, Any]]:
    """Fetch IL5/CUI ingestion events from the data store.

    Thin wrapper around :func:`tools.il5.ingestion.get_il5_events` that
    provides a stable name for the API layer.
    """
    return get_il5_events(since=since, limit=limit)


@il5_api.route("/", methods=["GET"])
def get_il5() -> Response:
    """Return IL5 ingestion events formatted for UI rendering.

    Query parameters:
        since    ISO 8601 cursor — only events ingested after this time.
        limit    Max rows (default 25).
        component  ``table`` (default) or ``chart``.

    Returns 200 with JSON payload from render_il5_ui, or 504 if the SLA
    (30 seconds) is exceeded.
    """
    since = request.args.get("since") or None
    component = request.args.get("component", "table")
    try:
        limit = int(request.args.get("limit", 25))
    except (ValueError, TypeError):
        limit = 25

    try:
        with il5_timeout_context("ingest+display"):
            raw = fetch_il5_data(since=since, limit=limit)
            rendered = render_il5_ui(raw, component=component)
        return Response(rendered, status=200, mimetype="application/json")
    except TimeoutError as exc:
        body = json.dumps(
            {
                "error": "il5_sla_timeout",
                "message": str(exc),
                "classification": "CUI",
                "impact_level": "IL5",
            },
            ensure_ascii=False,
        )
        return Response(body, status=504, mimetype="application/json")
