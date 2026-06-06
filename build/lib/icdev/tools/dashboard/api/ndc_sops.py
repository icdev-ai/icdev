# CUI // SP-CTI
"""NDC SOPs API — CRUD + approval workflow endpoints."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from tools.network import sops as sops_mod

ndc_sops_api = Blueprint("ndc_sops_api", __name__, url_prefix="/api/ndc/sops")


def _actor_from_request() -> str:
    """Extract actor from session/header/body; fall back to 'anonymous'."""
    try:
        from flask import session
        user = (session.get("user") or {}).get("username")
        if user:
            return user
    except Exception:
        pass
    hdr = request.headers.get("X-ICDEV-User")
    if hdr:
        return hdr
    body = request.get_json(silent=True) or {}
    return body.get("actor") or "anonymous"


@ndc_sops_api.route("", methods=["GET"])
def list_sops_route():
    category = request.args.get("category")
    status = request.args.get("status")
    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    items = sops_mod.list_sops(category=category, status=status, limit=limit)
    return jsonify({"ok": True, "count": len(items), "sops": items})


@ndc_sops_api.route("/<sop_id>", methods=["GET"])
def get_sop_route(sop_id: str):
    sop = sops_mod.get_sop(sop_id)
    if not sop:
        return jsonify({"ok": False, "error": "not_found"}), 404
    return jsonify({"ok": True, "sop": sop})


@ndc_sops_api.route("", methods=["POST"])
def create_sop_route():
    data = request.get_json(silent=True) or {}
    try:
        sop = sops_mod.create_sop(
            title=data.get("title", ""),
            category=data.get("category", ""),
            description=data.get("description", ""),
            steps=data.get("steps") or [],
            prerequisites=data.get("prerequisites") or [],
            validation=data.get("validation") or [],
            rollback=data.get("rollback") or {},
            escalation=data.get("escalation") or [],
            author=data.get("author") or _actor_from_request(),
            classification=data.get("classification", "CUI"),
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "sop": sop}), 201


@ndc_sops_api.route("/<sop_id>", methods=["PATCH"])
def update_sop_route(sop_id: str):
    data = request.get_json(silent=True) or {}
    data.setdefault("actor", _actor_from_request())
    try:
        sop = sops_mod.update_sop(sop_id, **data)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "sop": sop})


@ndc_sops_api.route("/<sop_id>", methods=["DELETE"])
def delete_sop_route(sop_id: str):
    result = sops_mod.delete_sop(sop_id, actor=_actor_from_request())
    status = 200 if result.get("ok") else 400
    if result.get("error") == "not_found":
        status = 404
    return jsonify(result), status


@ndc_sops_api.route("/<sop_id>/submit", methods=["POST"])
def submit_route(sop_id: str):
    data = request.get_json(silent=True) or {}
    reviewer = data.get("reviewer")
    if not reviewer:
        return jsonify({"ok": False, "error": "reviewer required"}), 400
    try:
        sop = sops_mod.submit_for_review(
            sop_id, reviewer=reviewer,
            submitted_by=data.get("submitted_by") or _actor_from_request(),
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "sop": sop})


@ndc_sops_api.route("/<sop_id>/approve", methods=["POST"])
def approve_route(sop_id: str):
    data = request.get_json(silent=True) or {}
    approver = data.get("approver") or _actor_from_request()
    try:
        sop = sops_mod.approve_sop(sop_id, approver=approver,
                                   comment=data.get("comment", ""))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "sop": sop})


@ndc_sops_api.route("/<sop_id>/reject", methods=["POST"])
def reject_route(sop_id: str):
    data = request.get_json(silent=True) or {}
    comment = data.get("comment", "")
    if not comment:
        return jsonify({"ok": False, "error": "comment required"}), 400
    approver = data.get("approver") or _actor_from_request()
    try:
        sop = sops_mod.reject_sop(sop_id, approver=approver, comment=comment)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "sop": sop})


@ndc_sops_api.route("/<sop_id>/deprecate", methods=["POST"])
def deprecate_route(sop_id: str):
    data = request.get_json(silent=True) or {}
    try:
        sop = sops_mod.deprecate_sop(
            sop_id, actor=data.get("actor") or _actor_from_request(),
            comment=data.get("comment", ""),
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "sop": sop})


@ndc_sops_api.route("/<sop_id>/history", methods=["GET"])
def history_route(sop_id: str):
    entries = sops_mod.get_approval_history(sop_id)
    return jsonify({"ok": True, "count": len(entries), "history": entries})
