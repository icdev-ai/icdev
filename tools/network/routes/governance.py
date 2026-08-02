# CUI // SP-CTI
"""Network Design Canvas — Governance routes.

Provides change request lifecycle management and intent policy enforcement.
Reads/writes: nc_change_requests, nc_change_request_items,
              nc_intent_policies, nc_intent_validations.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import uuid
from datetime import datetime, timezone

logger = get_logger("icdev.network.routes.governance")


def register_governance_routes(bp, get_conn=None, helpers=None):
    """Register governance routes on the NDC blueprint."""
    from flask import jsonify, request

    def _nc():
        from tools.network.db.init_db import get_connection
        return get_connection()

    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── Change Requests ───────────────────────────────────────────────────────

    @bp.route("/api/topologies/<topo_id>/governance/change-requests", methods=["GET", "POST"])
    def change_requests(topo_id):
        """List or create change requests for a topology."""
        if request.method == "GET":
            status_filter = request.args.get("status")
            with _nc() as db:
                if status_filter:
                    rows = db.execute(
                        "SELECT * FROM nc_change_requests WHERE topology_id=%s AND status=%s ORDER BY created_at DESC",
                        (topo_id, status_filter),
                    ).fetchall()
                else:
                    rows = db.execute(
                        "SELECT * FROM nc_change_requests WHERE topology_id=%s ORDER BY created_at DESC",
                        (topo_id,),
                    ).fetchall()
            return jsonify({"change_requests": [dict(r) for r in rows], "total": len(rows)})

        # POST — create new change request
        body = request.json or {}
        required = ("title", "change_type")
        missing = [f for f in required if not body.get(f)]
        if missing:
            return jsonify({"error": f"Missing required fields: {missing}"}), 400

        cr_id = "cr-" + uuid.uuid4().hex[:10]
        now = _now()
        user_id = _current_user()

        with _nc() as db:
            db.execute(
                # requested_by/requested_at are submitter_name/submitted_at, and
                # change_type/risk_level have no columns of their own — they go in
                # document_json, which exists for exactly this. Nothing here could
                # be created before (swp-scan-01).
                "INSERT INTO nc_change_requests "
                "(id, topology_id, title, description, document_json, status, "
                "submitter_name, submitted_at, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    cr_id, topo_id,
                    body["title"],
                    body.get("description", ""),
                    json.dumps({
                        "change_type": body["change_type"],
                        "risk_level": body.get("risk_level", "medium"),
                    }),
                    "draft",
                    user_id, now, now, now,
                ),
            )
            # Optional line items
            for item in body.get("items", []):
                db.execute(
                    # change_request_id/item_type/description/object_id are
                    # cr_id/action_type/justification/entity_id (swp-scan-01).
                    "INSERT INTO nc_change_request_items "
                    "(id, cr_id, action_type, justification, entity_id, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        "cri-" + uuid.uuid4().hex[:10],
                        cr_id,
                        item.get("item_type", "config_change"),
                        item.get("description", ""),
                        item.get("object_id", ""),
                        now,
                    ),
                )
            db.commit()

        return jsonify({"id": cr_id, "status": "draft"}), 201

    @bp.route(
        "/api/topologies/<topo_id>/governance/change-requests/<cr_id>",
        methods=["GET", "PATCH", "DELETE"],
    )
    def change_request_detail(topo_id, cr_id):
        """Fetch, update status, or delete a change request."""
        with _nc() as db:
            cr = db.execute(
                "SELECT * FROM nc_change_requests WHERE id=%s AND topology_id=%s",
                (cr_id, topo_id),
            ).fetchone()
            if not cr:
                return jsonify({"error": "Change request not found"}), 404

            if request.method == "GET":
                items = db.execute(
                    "SELECT * FROM nc_change_request_items WHERE change_request_id=%s",
                    (cr_id,),
                ).fetchall()
                return jsonify({
                    "change_request": dict(cr),
                    "items": [dict(i) for i in items],
                })

            if request.method == "DELETE":
                db.execute("DELETE FROM nc_change_request_items WHERE change_request_id=%s", (cr_id,))
                db.execute("DELETE FROM nc_change_requests WHERE id=%s", (cr_id,))
                db.commit()
                return jsonify({"deleted": cr_id})

            # PATCH — update status or fields
            body = request.json or {}
            allowed_fields = {"status", "risk_level", "description", "approved_by", "scheduled_at"}
            updates = {k: v for k, v in body.items() if k in allowed_fields}
            if not updates:
                return jsonify({"error": "No valid fields to update"}), 400

            now = _now()
            updates["updated_at"] = now
            set_clause = ", ".join(f"{k}=%s" for k in updates)
            db.execute(
                f"UPDATE nc_change_requests SET {set_clause} WHERE id=%s",
                list(updates.values()) + [cr_id],
            )
            db.commit()
            return jsonify({"id": cr_id, "updated": list(updates.keys())})

    # ── Intent Policies ───────────────────────────────────────────────────────

    @bp.route("/api/topologies/<topo_id>/governance/intent-policies", methods=["GET", "POST"])
    def intent_policies(topo_id):
        """List or create intent policies for a topology."""
        if request.method == "GET":
            with _nc() as db:
                rows = db.execute(
                    "SELECT * FROM nc_intent_policies WHERE topology_id=%s ORDER BY created_at DESC",
                    (topo_id,),
                ).fetchall()
            return jsonify({"intent_policies": [dict(r) for r in rows], "total": len(rows)})

        # POST — create new intent policy
        body = request.json or {}
        if not body.get("name"):
            return jsonify({"error": "name is required"}), 400

        policy_id = "ip-" + uuid.uuid4().hex[:10]
        now = _now()

        with _nc() as db:
            db.execute(
                # enabled is is_active. rule_json and severity genuinely did not
                # exist and are added by migration 323 — a policy without its rule
                # is not a policy, and the validation route below reads rule_json
                # back, so dropping them was not an option (swp-scan-01).
                "INSERT INTO nc_intent_policies "
                "(id, topology_id, name, description, rule_json, severity, is_active, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    policy_id, topo_id,
                    body["name"],
                    body.get("description", ""),
                    json.dumps(body.get("rule", {})),
                    body.get("severity", "cat2"),
                    1,
                    now, now,
                ),
            )
            db.commit()
        return jsonify({"id": policy_id, "status": "created"}), 201

    @bp.route(
        "/api/topologies/<topo_id>/governance/intent-policies/<policy_id>/validate",
        methods=["POST"],
    )
    def validate_intent_policy(topo_id, policy_id):
        """Run a single intent policy against the current topology.

        Writes result to nc_intent_validations.
        """
        with _nc() as db:
            policy = db.execute(
                "SELECT * FROM nc_intent_policies WHERE id=%s AND topology_id=%s",
                (policy_id, topo_id),
            ).fetchone()
            if not policy:
                return jsonify({"error": "Intent policy not found"}), 404

            policy_dict = dict(policy)
            rule_parse_error = None
            try:
                rule = json.loads(policy_dict.get("rule_json") or "{}")
            except Exception as exc:
                # Fail-closed (ndc-gov-01): an unparseable rule cannot be
                # evaluated, so the validation is an explicit error — never a
                # silent benign default.
                logger.warning(
                    "Intent policy %s (topo=%s) has invalid rule_json: %s",
                    policy_id, topo_id, exc,
                )
                rule = {}
                rule_parse_error = str(exc)

            if rule_parse_error:
                result, detail = "error", {
                    "reason": f"Invalid rule definition: {rule_parse_error}",
                    "error": "rule_json_parse",
                }
            else:
                # Evaluate rule against topology (delegate to twin.py if available)
                result, detail = _evaluate_intent_rule(db, topo_id, rule)

            val_id = "iv-" + uuid.uuid4().hex[:10]
            now = _now()
            # The live table summarises constraint COUNTS (total/passed/failed)
            # rather than storing a verdict string, so `result` cannot be written
            # as-is. One rule evaluated == one constraint; anything that is not an
            # explicit 'pass' counts as failed, which preserves the fail-closed
            # contract above ('error' and 'unknown' must never read as passing).
            # The verdict string itself is kept in violations_json (swp-scan-01).
            passed_count = 1 if result == "pass" else 0
            db.execute(
                "INSERT INTO nc_intent_validations "
                "(id, topology_id, policy_id, total_constraints, passed, failed, violations_json, ran_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    val_id, topo_id, policy_id,
                    1, passed_count, 1 - passed_count,
                    json.dumps({"result": result, "detail": detail}),
                    now,
                ),
            )
            db.commit()

        return jsonify({
            "validation_id": val_id,
            "policy_id": policy_id,
            "result": result,
            # Aggregate verdict — fail-closed: only an explicit "pass" passes;
            # "error"/"fail"/"unknown" all read as not-passing.
            "passed": _verdict_is_pass(result),
            "detail": detail,
            "validated_at": now,
        })

    # ── Governance Dashboard ──────────────────────────────────────────────────

    @bp.route("/api/governance/dashboard", methods=["GET"])
    def governance_dashboard():
        """Cross-topology summary: open CRs, failed intent policies, pending approvals."""
        with _nc() as db:
            open_crs = db.execute(
                "SELECT topology_id, COUNT(*) as cnt FROM nc_change_requests "
                "WHERE status NOT IN ('closed','cancelled') GROUP BY topology_id",
            ).fetchall()
            has_intents = _table_exists(db, "nc_intent_validations")
            failed_intents = db.execute(
                "SELECT topology_id, COUNT(*) as cnt FROM nc_intent_validations "
                "WHERE result='fail' GROUP BY topology_id",
            ).fetchall() if has_intents else []
            # Fail-closed (ndc-gov-01): validations that errored are NOT passes.
            # Surface them alongside failures so an evaluation error can never
            # be silently rolled up as a healthy/passing gate.
            errored_intents = db.execute(
                "SELECT topology_id, COUNT(*) as cnt FROM nc_intent_validations "
                "WHERE result='error' GROUP BY topology_id",
            ).fetchall() if has_intents else []
            pending_approval = db.execute(
                "SELECT topology_id, COUNT(*) as cnt FROM nc_change_requests "
                "WHERE status='pending_approval' GROUP BY topology_id",
            ).fetchall()

        total_failed = sum(r[1] for r in failed_intents)
        total_errored = sum(r[1] for r in errored_intents)
        # Overall gate verdict — errors are a distinct not-pass state.
        if total_errored:
            overall_intent_status = "error"
        elif total_failed:
            overall_intent_status = "fail"
        else:
            overall_intent_status = "pass"

        return jsonify({
            "open_change_requests": [dict(zip(["topology_id", "count"], r)) for r in open_crs],
            "failed_intent_policies": [dict(zip(["topology_id", "count"], r)) for r in failed_intents],
            "errored_intent_policies": [dict(zip(["topology_id", "count"], r)) for r in errored_intents],
            "pending_approvals": [dict(zip(["topology_id", "count"], r)) for r in pending_approval],
            "total_open_crs": sum(r[1] for r in open_crs),
            "total_failed_intents": total_failed,
            "total_errored_intents": total_errored,
            "overall_intent_status": overall_intent_status,
        })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _current_user() -> str:
    try:
        from flask import session
        return session.get("user_id", "system")
    except Exception:
        return "system"


def _table_exists(db, table: str) -> bool:
    from tools.network.blueprint_helpers import table_exists
    return table_exists(db, table)


def _verdict_is_pass(result: str) -> bool:
    """Fail-closed verdict test — only the explicit 'pass' state passes.

    'fail', 'error', and 'unknown' all read as not-passing. Any aggregation
    that rolls up gate verdicts MUST route through this so an evaluation error
    can never be counted as a pass (ndc-gov-01).
    """
    return result == "pass"


def _evaluate_intent_rule(db, topo_id: str, rule: dict) -> tuple[str, dict]:
    """Evaluate an intent rule against the current topology.

    Delegates to twin.py if available, otherwise returns a heuristic result.

    Returns: (result: 'pass' | 'fail' | 'error' | 'unknown', detail: dict)

    Fail-closed contract (ndc-gov-01): if any evaluation step raises, the
    result is the explicit 'error' state (with a short exception summary in
    the detail), NEVER a benign 'pass'. The exception is caught here so the
    calling route still returns 200 with a result, but callers/aggregators
    must treat 'error' as not-pass.
    """
    rule_type = rule.get("type", "")

    try:
        if rule_type == "no_single_points_of_failure":
            # Heuristic: devices with only 1 uplink connection
            from tools.network.twin import blast_radius
            br = blast_radius(topo_id)
            spof_devices = [n for n, data in br.get("nodes", {}).items() if data.get("is_spof")]
            result = "pass" if not spof_devices else "fail"
            return result, {"spof_devices": spof_devices, "rule_type": rule_type}

        if rule_type == "no_open_cat1_findings":
            count = db.execute(
                "SELECT COUNT(*) FROM nc_compliance_findings WHERE topology_id=%s AND severity='cat1' AND status='open'",
                (topo_id,),
            ).fetchone()[0]
            result = "pass" if count == 0 else "fail"
            return result, {"cat1_open_count": count, "rule_type": rule_type}

        if rule_type == "all_devices_managed":
            unmanaged = db.execute(
                "SELECT COUNT(*) FROM nc_objects WHERE topology_id=%s AND managed=0 AND object_type NOT IN ('label','group','link')",
                (topo_id,),
            ).fetchone()[0] if _col_exists_inner(db, "nc_objects", "managed") else 0
            result = "pass" if unmanaged == 0 else "fail"
            return result, {"unmanaged_count": unmanaged, "rule_type": rule_type}
    except Exception as exc:
        # Fail-closed: an evaluation error is an explicit 'error', not a 'pass'.
        logger.warning(
            "Intent rule evaluation failed (rule_type=%s, topo=%s): %s",
            rule_type or "?", topo_id, exc,
        )
        return "error", {
            "rule_type": rule_type,
            "error": type(exc).__name__,
            "reason": f"Evaluation error: {exc}",
        }

    # Unknown rule type — cannot evaluate
    return "unknown", {"message": f"Rule type '{rule_type}' not implemented", "rule": rule}


def _col_exists_inner(db, table: str, col: str) -> bool:
    from tools.network.blueprint_helpers import col_exists
    return col_exists(db, table, col)
