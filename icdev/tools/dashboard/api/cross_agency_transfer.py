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
from tools.logging.icdev_logger import get_logger


from flask import Blueprint, jsonify, request

log = get_logger(__name__)

cross_agency_transfer_api = Blueprint("cross_agency_transfer_api", __name__)


@cross_agency_transfer_api.route("/transfers", methods=["POST"])
def submit_transfer():
    """Submit a cross-agency data transfer request.

    ABAC enforcement (AC-3, AC-4, AC-4(4)) is applied BEFORE audit logging.
    Requests are denied with HTTP 403 when the subject lacks the required
    entitlement, clearance level, or compartment access.  On permit, all
    returned data objects carry mandatory classification markings (SC-7(5)).

    The audit record is written to the append-only cross_agency_transfers
    table after ABAC permit (NIST AU-2 log-before-proceed).

    Request JSON:
        transfer_id         (str, required)
        source_agency       (str, required)
        target_agency       (str, required)
        data_type           (str, required)
        actor               (str, required)
        subject             (dict, required) — user_id, clearance_level,
                              entitlements, compartments
        data_classification (str, optional) — CUI | SECRET | TOP SECRET
        compartments        (list, optional) — resource compartment list
        data_objects        (list, optional) — raw objects to transfer
        project_id          (str, optional)
        details             (dict, optional)

    Returns:
        200 JSON with success, abac_permitted=True, transfer_id, event_id,
            and data_objects (each carrying classification_marking).
        403 JSON with permitted=False, reason, policy_name, data_objects=[].
        400 JSON when request body is missing.
    """
    body = request.get_json(silent=True) or {}
    if not body:
        return jsonify({"error": "JSON request body required"}), 400

    # ------------------------------------------------------------------
    # ABAC enforcement — must pass before any data is returned (AC-3/AC-4)
    # ------------------------------------------------------------------
    from tools.security.abac_engine import enforce_cross_domain_pull

    subject: dict = body.get("subject") or {}
    resource: dict = {
        "classification": body.get("data_classification", "CUI"),
        "compartments": body.get("compartments", []),
        "source_il": body.get("source_il", "IL4"),
        "target_il": body.get("target_il", "IL2"),
    }
    data_objects: list = body.get("data_objects", [])

    abac_result = enforce_cross_domain_pull(
        subject=subject,
        resource=resource,
        data_objects=data_objects,
    )

    if not abac_result.permitted:
        log.warning(
            "ABAC cross-domain deny: user=%s transfer_id=%s reason=%s",
            subject.get("user_id", "unknown"),
            body.get("transfer_id", ""),
            abac_result.reason,
        )
        return jsonify({
            "permitted": False,
            "abac_permitted": False,
            "policy_name": abac_result.policy_name,
            "reason": abac_result.reason,
            "data_objects": [],
        }), 403

    # ------------------------------------------------------------------
    # ABAC permit — write audit record then return marked data objects
    # ------------------------------------------------------------------
    try:
        from services.ingestion.hook_transfer import run_transfer
        result = run_transfer(body)
    except ImportError:
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

    result["abac_permitted"] = True
    result["data_objects"] = abac_result.data_objects
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
