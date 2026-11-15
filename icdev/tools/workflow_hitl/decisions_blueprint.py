# CUI // SP-CTI
"""Decisions — where a person decides what was only proposed (icx-cnf-02).

Mounts ``/decisions``. Most declared decisions are taken in one action on the
page that raises them (a launch, a confirm, an adjudication). Some cannot be:

* a won bid's delivery baseline (``decl-wft-cortex-award``), which a contracts
  person OTHER than the proposer accepts;
* a Cortex agent launch or a BOM designation that a service key, or a person
  without the stage's role, proposed.

Those land here as pending steps. A holder decides each in one action; the
template's manual stage, its holders, its reason rule and ``separate_from`` are
enforced by ``tools.workflow_hitl.declared``. Nothing here runs an effect: a
machine caller re-posts with the decided ``step_id`` and the route spends it.

Routes:
  GET  /decisions/                         the page
  GET  /decisions/api/steps?status=        steps (pending | decided | spent | withdrawn)
  GET  /decisions/api/templates            the declared templates
  POST /decisions/api/steps/<id>/decide    {decision, reason} -> the person's step
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

decisions_bp = Blueprint("wf_decisions", __name__, url_prefix="/decisions")


@decisions_bp.route("/")
@decisions_bp.route("")
def decisions_page():
    """Pending and recent declared decisions."""
    return render_template("workflow_decisions.html")


@decisions_bp.route("/api/steps", methods=["GET"])
def api_steps():
    from tools.workflow_hitl import declared

    status = (request.args.get("status") or "").strip()
    try:
        steps = declared.list_steps(status=status,
                                    limit=min(int(request.args.get("limit", 200) or 200), 1000))
    except Exception as exc:  # noqa: BLE001 — an unreadable ledger is an error, not "none"
        logger.warning("decisions: ledger unreadable: %s", exc)
        return jsonify({"error": f"the decision ledger cannot be read: {exc}"}), 503
    actor = declared.current_actor()
    for step in steps:
        step["can_decide"] = (step["status"] == "pending"
                              and declared.can_hold(step["template_id"], actor)
                              and step.get("proposed_by") != (actor or {}).get("id"))
    return jsonify({"steps": steps, "count": len(steps), "status": status or "all"})


@decisions_bp.route("/api/templates", methods=["GET"])
def api_templates():
    from tools.workflow_hitl import declared

    out = []
    for tid, tmpl in sorted(declared.load_templates().items()):
        stage = declared.manual_stage(tmpl)
        out.append({
            "id": tid, "name": tmpl.get("name", ""), "description": tmpl.get("description", ""),
            "decides": tmpl.get("decides") or [], "stage": stage["name"], "role": stage["role"],
            "holders": stage.get("holders") or [],
            "decisions": stage.get("decisions") or {},
            "reason_required": stage.get("reason_required") or [],
            "separate_from": stage.get("separate_from") or "",
        })
    return jsonify({"templates": out, "count": len(out)})


@decisions_bp.route("/api/steps/<step_id>/decide", methods=["POST"])
def api_decide(step_id: str):
    """A holder decides a pending step. Body: ``{decision, reason}``."""
    from tools.workflow_hitl import declared

    data = request.get_json(silent=True) or {}
    try:
        step = declared.decide(step_id, decision=str(data.get("decision") or "").strip(),
                               reason=str(data.get("reason") or "").strip())
    except declared.StepRefused as exc:
        return jsonify(exc.to_dict()), exc.status
    return jsonify(step)
