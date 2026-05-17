# CUI // SP-CTI
"""Flask Blueprint: cross-agency data transfer audit API.

All transfer submissions are intercepted by hook_transfer.run_transfer(),
which writes an append-only audit record BEFORE the transfer proceeds
(NIST 800-53 AU-2). Audit records are also dual-written to audit_trail
for AU-12 completeness. The table itself is protected against UPDATE/DELETE
by .claude/hooks/pre_tool_use.py (NIST AU-9).

Routes
------
POST /transfers          — submit a transfer request; returns audit event IDs
GET  /transfers/<id>     — query all audit events for a transfer_id
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

log = logging.getLogger(__name__)

cross_agency_transfer_api = Blueprint("cross_agency_transfer_api", __name__)


@cross_agency_transfer_api.route("/transfers", methods=["POST"])
def submit_transfer():
    """Submit a cross-agency data transfer request.

    The audit record is written to the append-only cross_agency_transfers
    table before the transfer proceeds (NIST AU-2 log-before-proceed).

    Request JSON:
        transfer_id         (str, required)
        source_agency       (str, required)
        target_agency       (str, required)
        data_type           (str, required)
        actor               (str, required)
        data_classification (str, optional) — CUI | SECRET | TOP_SECRET
        project_id          (str, optional)
        details             (dict, optional)

    Returns:
        200/422 JSON with success, transfer_id, event_id, initiated_event_id
    """
    body = request.get_json(silent=True) or {}
    if not body:
        return jsonify({"error": "JSON request body required"}), 400

    try:
        from services.ingestion.hook_transfer import run_transfer
        result = run_transfer(body)
    except ImportError:
        # Fallback: call the logger directly when the ingestion service layer
        # is unavailable (e.g. unit-test environment without services/ on path).
        log.warning("hook_transfer unavailable — logging via CrossAgencyTransferLogger directly")
        from tools.audit.cross_agency_transfer_logger import CrossAgencyTransferLogger
        logger = CrossAgencyTransferLogger()
        event_id = logger.log_initiated(
            transfer_id=body.get("transfer_id", ""),
            source_agency=body.get("source_agency", ""),
            target_agency=body.get("target_agency", ""),
            data_type=body.get("data_type", ""),
            actor=body.get("actor", "system"),
            data_classification=body.get("data_classification", "CUI"),
            project_id=body.get("project_id"),
            details=body.get("details"),
        )
        result = {
            "success": bool(event_id),
            "transfer_id": body.get("transfer_id", ""),
            "event_id": event_id,
            "initiated_event_id": event_id,
            "error": None,
        }

    status_code = 200 if result.get("success") else 422
    return jsonify(result), status_code


@cross_agency_transfer_api.route("/transfers/<transfer_id>", methods=["GET"])
def get_transfer_events(transfer_id: str):
    """Return all audit events for a given transfer_id (newest first, max 50).

    Returns:
        200 JSON with transfer_id and events list.
    """
    from tools.audit.cross_agency_transfer_logger import query_by_transfer_id
    events = query_by_transfer_id(transfer_id)
    return jsonify({"transfer_id": transfer_id, "events": events}), 200
