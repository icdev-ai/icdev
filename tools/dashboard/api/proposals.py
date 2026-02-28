#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: Proposal Writing Lifecycle Tracker.

Full GovCon proposal lifecycle — opportunities, volumes, sections,
compliance matrix (L/M/N), color team reviews, findings, and status history.
"""

import json
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"

proposals_api = Blueprint("proposals_api", __name__, url_prefix="/api/proposals")


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _uuid():
    return str(uuid.uuid4())


def _record_status_change(conn, entity_type, entity_id, old_status, new_status, changed_by=None, reason=None):
    """Insert into append-only proposal_status_history."""
    conn.execute(
        "INSERT INTO proposal_status_history (entity_type, entity_id, old_status, new_status, changed_by, reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (entity_type, entity_id, old_status, new_status, changed_by, reason),
    )


# Valid status transitions for sections (D-PROP-1)
SECTION_TRANSITIONS = {
    "not_started": ["outlining"],
    "outlining": ["drafting"],
    "drafting": ["internal_review"],
    "internal_review": ["pink_team_ready"],
    "pink_team_ready": ["pink_team_review"],
    "pink_team_review": ["rework_pink", "red_team_ready"],
    "rework_pink": ["pink_team_ready", "red_team_ready"],
    "red_team_ready": ["red_team_review"],
    "red_team_review": ["rework_red", "gold_team_ready"],
    "rework_red": ["red_team_ready", "gold_team_ready"],
    "gold_team_ready": ["gold_team_review"],
    "gold_team_review": ["white_glove"],
    "white_glove": ["final"],
    "final": ["submitted"],
    "submitted": [],
}

# Ordered status list for pipeline rendering
SECTION_STATUS_ORDER = [
    "not_started", "outlining", "drafting", "internal_review",
    "pink_team_ready", "pink_team_review", "rework_pink",
    "red_team_ready", "red_team_review", "rework_red",
    "gold_team_ready", "gold_team_review",
    "white_glove", "final", "submitted",
]


# =====================================================================
# Opportunity CRUD
# =====================================================================

@proposals_api.route("/opportunities", methods=["GET"])
def list_opportunities():
    """GET /api/proposals/opportunities — List all opportunities."""
    conn = _get_db()
    try:
        status_filter = request.args.get("status")
        sql = "SELECT * FROM proposal_opportunities"
        params = []
        if status_filter:
            sql += " WHERE status = ?"
            params.append(status_filter)
        sql += " ORDER BY due_date ASC"
        rows = conn.execute(sql, params).fetchall()
        items = [dict(r) for r in rows]
        return jsonify({"opportunities": items, "total": len(items)})
    finally:
        conn.close()


@proposals_api.route("/opportunities", methods=["POST"])
def create_opportunity():
    """POST /api/proposals/opportunities — Create a new opportunity."""
    data = request.get_json(force=True, silent=True) or {}
    required = ["solicitation_number", "title", "agency", "due_date", "proposal_type"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    opp_id = _uuid()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO proposal_opportunities
               (id, project_id, solicitation_number, title, agency, sub_agency,
                due_date, due_time, set_aside_type, naics_code,
                estimated_value_low, estimated_value_high, proposal_type,
                status, rfp_document_path, rfp_url,
                capture_manager, proposal_manager, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'intake', ?, ?, ?, ?, ?)""",
            (
                opp_id,
                data.get("project_id"),
                data["solicitation_number"],
                data["title"],
                data["agency"],
                data.get("sub_agency"),
                data["due_date"],
                data.get("due_time", "17:00"),
                data.get("set_aside_type"),
                data.get("naics_code"),
                data.get("estimated_value_low"),
                data.get("estimated_value_high"),
                data["proposal_type"],
                data.get("rfp_document_path"),
                data.get("rfp_url"),
                data.get("capture_manager"),
                data.get("proposal_manager"),
                data.get("created_by"),
            ),
        )
        _record_status_change(conn, "opportunity", opp_id, None, "intake", data.get("created_by"))
        conn.commit()
        return jsonify({"id": opp_id, "status": "intake"}), 201
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>", methods=["GET"])
def get_opportunity(opp_id):
    """GET /api/proposals/opportunities/<id> — Opportunity detail + aggregate stats."""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM proposal_opportunities WHERE id = ?", (opp_id,)).fetchone()
        if not row:
            return jsonify({"error": "Opportunity not found"}), 404
        opp = dict(row)

        # Aggregate stats
        sections = conn.execute(
            "SELECT status FROM proposal_sections WHERE opportunity_id = ?", (opp_id,)
        ).fetchall()
        total_sections = len(sections)
        complete_sections = sum(1 for s in sections if s["status"] in ("final", "submitted"))

        cm_rows = conn.execute(
            "SELECT compliance_status FROM proposal_compliance_matrix WHERE opportunity_id = ?", (opp_id,)
        ).fetchall()
        total_cm = len(cm_rows)
        addressed = sum(1 for c in cm_rows if c["compliance_status"] not in ("not_addressed",))
        coverage_pct = round((addressed / total_cm * 100) if total_cm > 0 else 0, 1)

        findings = conn.execute(
            """SELECT f.severity, f.status FROM proposal_review_findings f
               JOIN proposal_reviews r ON f.review_id = r.id
               WHERE r.opportunity_id = ?""",
            (opp_id,),
        ).fetchall()
        open_findings = sum(1 for f in findings if f["status"] in ("open", "in_progress"))
        critical_findings = sum(1 for f in findings if f["severity"] == "critical" and f["status"] in ("open", "in_progress"))

        # Days to deadline
        try:
            due = datetime.strptime(opp["due_date"], "%Y-%m-%d")
            days_left = (due - datetime.utcnow()).days
        except (ValueError, TypeError):
            days_left = None

        # Review gate status
        reviews = conn.execute(
            "SELECT review_type, status, scheduled_date, overall_rating FROM proposal_reviews WHERE opportunity_id = ? ORDER BY created_at",
            (opp_id,),
        ).fetchall()
        gate_status = {}
        for rv in reviews:
            gate_status[rv["review_type"]] = {
                "status": rv["status"],
                "scheduled_date": rv["scheduled_date"],
                "overall_rating": rv["overall_rating"],
            }

        opp["stats"] = {
            "sections_total": total_sections,
            "sections_complete": complete_sections,
            "compliance_total": total_cm,
            "compliance_addressed": addressed,
            "compliance_coverage_pct": coverage_pct,
            "open_findings": open_findings,
            "critical_findings": critical_findings,
            "days_to_deadline": days_left,
            "review_gate_status": gate_status,
        }
        return jsonify(opp)
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>", methods=["PUT"])
def update_opportunity(opp_id):
    """PUT /api/proposals/opportunities/<id> — Update opportunity fields."""
    data = request.get_json(force=True, silent=True) or {}
    conn = _get_db()
    try:
        allowed = [
            "title", "agency", "sub_agency", "due_date", "due_time",
            "set_aside_type", "naics_code", "estimated_value_low",
            "estimated_value_high", "proposal_type", "rfp_document_path",
            "rfp_url", "capture_manager", "proposal_manager",
            "bid_decision", "bid_decision_date", "bid_decision_rationale",
        ]
        sets = []
        params = []
        for key in allowed:
            if key in data:
                sets.append(f"{key} = ?")
                params.append(data[key])
        if not sets:
            return jsonify({"error": "No valid fields to update"}), 400
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(opp_id)
        conn.execute(f"UPDATE proposal_opportunities SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        return jsonify({"id": opp_id, "updated": True})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/status", methods=["PUT"])
def change_opportunity_status(opp_id):
    """PUT /api/proposals/opportunities/<id>/status — Change opportunity status."""
    data = request.get_json(force=True, silent=True) or {}
    new_status = data.get("status")
    if not new_status:
        return jsonify({"error": "status is required"}), 400
    conn = _get_db()
    try:
        row = conn.execute("SELECT status FROM proposal_opportunities WHERE id = ?", (opp_id,)).fetchone()
        if not row:
            return jsonify({"error": "Opportunity not found"}), 404
        old_status = row["status"]
        conn.execute(
            "UPDATE proposal_opportunities SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, _now(), opp_id),
        )
        _record_status_change(conn, "opportunity", opp_id, old_status, new_status, data.get("changed_by"), data.get("reason"))
        conn.commit()
        return jsonify({"id": opp_id, "old_status": old_status, "new_status": new_status})
    finally:
        conn.close()


# =====================================================================
# Volume CRUD
# =====================================================================

@proposals_api.route("/opportunities/<opp_id>/volumes", methods=["GET"])
def list_volumes(opp_id):
    """GET /api/proposals/<opp_id>/volumes — List volumes for opportunity."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM proposal_volumes WHERE opportunity_id = ? ORDER BY sort_order, volume_number",
            (opp_id,),
        ).fetchall()
        return jsonify({"volumes": [dict(r) for r in rows]})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/volumes", methods=["POST"])
def create_volume(opp_id):
    """POST /api/proposals/<opp_id>/volumes — Create a volume."""
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("title"):
        return jsonify({"error": "title is required"}), 400
    vol_id = _uuid()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO proposal_volumes
               (id, opportunity_id, volume_number, title, description, page_limit, word_limit, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                vol_id, opp_id,
                data.get("volume_number", 1),
                data["title"],
                data.get("description"),
                data.get("page_limit"),
                data.get("word_limit"),
                data.get("sort_order", 0),
            ),
        )
        conn.commit()
        return jsonify({"id": vol_id}), 201
    finally:
        conn.close()


@proposals_api.route("/volumes/<vol_id>", methods=["PUT"])
def update_volume(vol_id):
    """PUT /api/proposals/volumes/<id> — Update volume."""
    data = request.get_json(force=True, silent=True) or {}
    conn = _get_db()
    try:
        allowed = ["title", "description", "page_limit", "word_limit", "volume_number", "sort_order", "status"]
        sets = []
        params = []
        for key in allowed:
            if key in data:
                sets.append(f"{key} = ?")
                params.append(data[key])
        if not sets:
            return jsonify({"error": "No valid fields to update"}), 400
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(vol_id)
        conn.execute(f"UPDATE proposal_volumes SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        return jsonify({"id": vol_id, "updated": True})
    finally:
        conn.close()


# =====================================================================
# Section CRUD
# =====================================================================

@proposals_api.route("/opportunities/<opp_id>/sections", methods=["GET"])
def list_sections(opp_id):
    """GET /api/proposals/<opp_id>/sections — List all sections for opportunity."""
    conn = _get_db()
    try:
        sql = """SELECT s.*, v.title as volume_title, v.volume_number
                 FROM proposal_sections s
                 LEFT JOIN proposal_volumes v ON s.volume_id = v.id
                 WHERE s.opportunity_id = ?"""
        params = [opp_id]
        status_filter = request.args.get("status")
        writer_filter = request.args.get("writer")
        if status_filter:
            sql += " AND s.status = ?"
            params.append(status_filter)
        if writer_filter:
            sql += " AND s.writer = ?"
            params.append(writer_filter)
        sql += " ORDER BY v.sort_order, v.volume_number, s.sort_order, s.section_number"
        rows = conn.execute(sql, params).fetchall()
        return jsonify({"sections": [dict(r) for r in rows], "total": len(rows)})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/sections", methods=["POST"])
def create_section(opp_id):
    """POST /api/proposals/<opp_id>/sections — Create a section."""
    data = request.get_json(force=True, silent=True) or {}
    required = ["volume_id", "title", "section_number"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    sec_id = _uuid()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO proposal_sections
               (id, volume_id, opportunity_id, parent_section_id, section_number, title,
                description, writer, writer_email, reviewer, page_limit, word_limit,
                priority, due_date, notes, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sec_id, data["volume_id"], opp_id,
                data.get("parent_section_id"),
                data["section_number"], data["title"],
                data.get("description"),
                data.get("writer"), data.get("writer_email"),
                data.get("reviewer"),
                data.get("page_limit"), data.get("word_limit"),
                data.get("priority", "standard"),
                data.get("due_date"),
                data.get("notes"),
                data.get("sort_order", 0),
            ),
        )
        _record_status_change(conn, "section", sec_id, None, "not_started", data.get("writer"))
        conn.commit()
        return jsonify({"id": sec_id}), 201
    finally:
        conn.close()


@proposals_api.route("/sections/<sec_id>", methods=["GET"])
def get_section(sec_id):
    """GET /api/proposals/sections/<id> — Section detail + history + dependencies + compliance."""
    conn = _get_db()
    try:
        row = conn.execute(
            """SELECT s.*, v.title as volume_title, v.volume_number
               FROM proposal_sections s
               LEFT JOIN proposal_volumes v ON s.volume_id = v.id
               WHERE s.id = ?""",
            (sec_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "Section not found"}), 404
        section = dict(row)

        # Status history
        history = conn.execute(
            "SELECT * FROM proposal_status_history WHERE entity_type = 'section' AND entity_id = ? ORDER BY created_at DESC",
            (sec_id,),
        ).fetchall()
        section["history"] = [dict(h) for h in history]

        # Dependencies
        deps = conn.execute(
            """SELECT d.*, s.title as depends_on_title, s.status as depends_on_status
               FROM proposal_section_dependencies d
               JOIN proposal_sections s ON d.depends_on_section_id = s.id
               WHERE d.section_id = ?""",
            (sec_id,),
        ).fetchall()
        section["dependencies"] = [dict(d) for d in deps]

        # Compliance items linked to this section
        cm = conn.execute(
            "SELECT * FROM proposal_compliance_matrix WHERE proposal_section_id = ? ORDER BY sort_order",
            (sec_id,),
        ).fetchall()
        section["compliance_items"] = [dict(c) for c in cm]

        # Review findings for this section
        findings = conn.execute(
            """SELECT f.*, r.review_type FROM proposal_review_findings f
               JOIN proposal_reviews r ON f.review_id = r.id
               WHERE f.section_id = ? ORDER BY f.created_at DESC""",
            (sec_id,),
        ).fetchall()
        section["findings"] = [dict(f) for f in findings]

        # Valid next statuses
        section["valid_transitions"] = SECTION_TRANSITIONS.get(section["status"], [])

        return jsonify(section)
    finally:
        conn.close()


@proposals_api.route("/sections/<sec_id>", methods=["PUT"])
def update_section(sec_id):
    """PUT /api/proposals/sections/<id> — Update section fields (not status)."""
    data = request.get_json(force=True, silent=True) or {}
    conn = _get_db()
    try:
        allowed = [
            "title", "description", "writer", "writer_email", "reviewer",
            "page_limit", "word_limit", "current_word_count", "current_page_count",
            "priority", "due_date", "notes", "content_path", "sort_order",
            "section_number", "parent_section_id",
        ]
        sets = []
        params = []
        for key in allowed:
            if key in data:
                sets.append(f"{key} = ?")
                params.append(data[key])
        if not sets:
            return jsonify({"error": "No valid fields to update"}), 400
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(sec_id)
        conn.execute(f"UPDATE proposal_sections SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        return jsonify({"id": sec_id, "updated": True})
    finally:
        conn.close()


@proposals_api.route("/sections/<sec_id>/status", methods=["PUT"])
def advance_section_status(sec_id):
    """PUT /api/proposals/sections/<id>/status — Advance section status (enforces transitions)."""
    data = request.get_json(force=True, silent=True) or {}
    new_status = data.get("status")
    if not new_status:
        return jsonify({"error": "status is required"}), 400

    conn = _get_db()
    try:
        row = conn.execute("SELECT status, opportunity_id FROM proposal_sections WHERE id = ?", (sec_id,)).fetchone()
        if not row:
            return jsonify({"error": "Section not found"}), 404

        old_status = row["status"]
        force = data.get("force", False)

        if not force:
            valid = SECTION_TRANSITIONS.get(old_status, [])
            if new_status not in valid:
                return jsonify({
                    "error": f"Invalid transition: {old_status} -> {new_status}",
                    "valid_transitions": valid,
                }), 400

            # Check dependencies (D-PROP-5)
            deps = conn.execute(
                """SELECT d.depends_on_section_id, d.required_status, s.status as current_dep_status, s.title
                   FROM proposal_section_dependencies d
                   JOIN proposal_sections s ON d.depends_on_section_id = s.id
                   WHERE d.section_id = ?""",
                (sec_id,),
            ).fetchall()
            blocked_by = []
            for dep in deps:
                req_idx = SECTION_STATUS_ORDER.index(dep["required_status"]) if dep["required_status"] in SECTION_STATUS_ORDER else 0
                cur_idx = SECTION_STATUS_ORDER.index(dep["current_dep_status"]) if dep["current_dep_status"] in SECTION_STATUS_ORDER else 0
                if cur_idx < req_idx:
                    blocked_by.append({
                        "section": dep["title"],
                        "current_status": dep["current_dep_status"],
                        "required_status": dep["required_status"],
                    })
            if blocked_by:
                return jsonify({"error": "Blocked by dependencies", "blocked_by": blocked_by}), 409

        conn.execute(
            "UPDATE proposal_sections SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, _now(), sec_id),
        )
        _record_status_change(conn, "section", sec_id, old_status, new_status, data.get("changed_by"), data.get("reason"))
        conn.commit()
        return jsonify({"id": sec_id, "old_status": old_status, "new_status": new_status})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/sections/dependencies", methods=["POST"])
def add_section_dependency(opp_id):
    """POST /api/proposals/<opp_id>/sections/dependencies — Add a section dependency."""
    data = request.get_json(force=True, silent=True) or {}
    required = ["section_id", "depends_on_section_id"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO proposal_section_dependencies (section_id, depends_on_section_id, dependency_type, required_status) VALUES (?, ?, ?, ?)",
            (data["section_id"], data["depends_on_section_id"], data.get("dependency_type", "content"), data.get("required_status", "drafting")),
        )
        conn.commit()
        return jsonify({"created": True}), 201
    finally:
        conn.close()


# =====================================================================
# Compliance Matrix
# =====================================================================

@proposals_api.route("/opportunities/<opp_id>/compliance", methods=["GET"])
def list_compliance(opp_id):
    """GET /api/proposals/<opp_id>/compliance — Full compliance matrix with stats."""
    conn = _get_db()
    try:
        status_filter = request.args.get("status")
        sql = """SELECT cm.*, s.title as section_title, s.section_number as section_num
                 FROM proposal_compliance_matrix cm
                 LEFT JOIN proposal_sections s ON cm.proposal_section_id = s.id
                 WHERE cm.opportunity_id = ?"""
        params = [opp_id]
        if status_filter:
            sql += " AND cm.compliance_status = ?"
            params.append(status_filter)
        sql += " ORDER BY cm.sort_order, cm.section_ref"
        rows = conn.execute(sql, params).fetchall()
        items = [dict(r) for r in rows]

        # Stats
        total = len(items)
        stats = {"total": total, "compliant": 0, "partial": 0, "non_compliant": 0, "not_addressed": 0, "not_applicable": 0}
        for item in items:
            st = item.get("compliance_status", "not_addressed")
            if st in stats:
                stats[st] += 1
        stats["coverage_pct"] = round(((total - stats["not_addressed"]) / total * 100) if total > 0 else 0, 1)

        return jsonify({"items": items, "stats": stats})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/compliance", methods=["POST"])
def create_compliance_item(opp_id):
    """POST /api/proposals/<opp_id>/compliance — Add a compliance matrix item."""
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("section_ref") or not data.get("requirement_text"):
        return jsonify({"error": "section_ref and requirement_text are required"}), 400
    cm_id = _uuid()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO proposal_compliance_matrix
               (id, opportunity_id, section_ref, volume_ref, requirement_text,
                requirement_type, compliance_status, proposal_section_id, response_summary, notes, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cm_id, opp_id,
                data["section_ref"], data.get("volume_ref"),
                data["requirement_text"],
                data.get("requirement_type", "L"),
                data.get("compliance_status", "not_addressed"),
                data.get("proposal_section_id"),
                data.get("response_summary"),
                data.get("notes"),
                data.get("sort_order", 0),
            ),
        )
        conn.commit()
        return jsonify({"id": cm_id}), 201
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/compliance/batch", methods=["POST"])
def batch_create_compliance(opp_id):
    """POST /api/proposals/<opp_id>/compliance/batch — Batch create compliance items."""
    data = request.get_json(force=True, silent=True) or {}
    items = data.get("items", [])
    if not items:
        return jsonify({"error": "items array is required"}), 400
    conn = _get_db()
    try:
        created = 0
        for item in items:
            cm_id = _uuid()
            conn.execute(
                """INSERT INTO proposal_compliance_matrix
                   (id, opportunity_id, section_ref, volume_ref, requirement_text,
                    requirement_type, compliance_status, proposal_section_id, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    cm_id, opp_id,
                    item.get("section_ref", ""),
                    item.get("volume_ref"),
                    item.get("requirement_text", ""),
                    item.get("requirement_type", "L"),
                    item.get("compliance_status", "not_addressed"),
                    item.get("proposal_section_id"),
                    item.get("sort_order", 0),
                ),
            )
            created += 1
        conn.commit()
        return jsonify({"created": created}), 201
    finally:
        conn.close()


@proposals_api.route("/compliance/<item_id>", methods=["PUT"])
def update_compliance_item(item_id):
    """PUT /api/proposals/compliance/<id> — Update compliance item."""
    data = request.get_json(force=True, silent=True) or {}
    conn = _get_db()
    try:
        allowed = [
            "section_ref", "volume_ref", "requirement_text", "requirement_type",
            "compliance_status", "proposal_section_id", "response_summary", "notes", "sort_order",
        ]
        sets = []
        params = []
        for key in allowed:
            if key in data:
                sets.append(f"{key} = ?")
                params.append(data[key])
        if not sets:
            return jsonify({"error": "No valid fields to update"}), 400

        # Record status change if compliance_status changed
        if "compliance_status" in data:
            old = conn.execute("SELECT compliance_status FROM proposal_compliance_matrix WHERE id = ?", (item_id,)).fetchone()
            if old and old["compliance_status"] != data["compliance_status"]:
                _record_status_change(conn, "compliance_item", item_id, old["compliance_status"], data["compliance_status"])

        sets.append("updated_at = ?")
        params.append(_now())
        params.append(item_id)
        conn.execute(f"UPDATE proposal_compliance_matrix SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
        return jsonify({"id": item_id, "updated": True})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/compliance/gaps", methods=["GET"])
def compliance_gaps(opp_id):
    """GET /api/proposals/<opp_id>/compliance/gaps — Unaddressed requirements."""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT * FROM proposal_compliance_matrix
               WHERE opportunity_id = ? AND compliance_status = 'not_addressed'
               ORDER BY sort_order, section_ref""",
            (opp_id,),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM proposal_compliance_matrix WHERE opportunity_id = ?", (opp_id,)
        ).fetchone()[0]
        gaps = [dict(r) for r in rows]
        return jsonify({
            "gaps": gaps,
            "total_gaps": len(gaps),
            "total_requirements": total,
            "gap_pct": round((len(gaps) / total * 100) if total > 0 else 0, 1),
        })
    finally:
        conn.close()


# =====================================================================
# Color Team Reviews & Findings
# =====================================================================

@proposals_api.route("/opportunities/<opp_id>/reviews", methods=["GET"])
def list_reviews(opp_id):
    """GET /api/proposals/<opp_id>/reviews — List all reviews."""
    conn = _get_db()
    try:
        review_type = request.args.get("review_type")
        sql = "SELECT * FROM proposal_reviews WHERE opportunity_id = ?"
        params = [opp_id]
        if review_type:
            sql += " AND review_type = ?"
            params.append(review_type)
        sql += " ORDER BY created_at"
        rows = conn.execute(sql, params).fetchall()
        reviews = [dict(r) for r in rows]

        # Attach finding counts per review
        for rev in reviews:
            counts = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM proposal_review_findings WHERE review_id = ? GROUP BY status",
                (rev["id"],),
            ).fetchall()
            rev["finding_counts"] = {c["status"]: c["cnt"] for c in counts}
            rev["total_findings"] = sum(c["cnt"] for c in counts)

        return jsonify({"reviews": reviews})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/reviews", methods=["POST"])
def schedule_review(opp_id):
    """POST /api/proposals/<opp_id>/reviews — Schedule a color team review."""
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("review_type"):
        return jsonify({"error": "review_type is required"}), 400
    rev_id = _uuid()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO proposal_reviews
               (id, opportunity_id, review_type, status, scheduled_date,
                lead_reviewer, participants, summary)
               VALUES (?, ?, ?, 'scheduled', ?, ?, ?, ?)""",
            (
                rev_id, opp_id,
                data["review_type"],
                data.get("scheduled_date"),
                data.get("lead_reviewer"),
                data.get("participants"),
                data.get("summary"),
            ),
        )
        _record_status_change(conn, "review", rev_id, None, "scheduled", data.get("lead_reviewer"))
        conn.commit()
        return jsonify({"id": rev_id}), 201
    finally:
        conn.close()


@proposals_api.route("/reviews/<rev_id>", methods=["PUT"])
def update_review(rev_id):
    """PUT /api/proposals/reviews/<id> — Update review (start, complete, rate)."""
    data = request.get_json(force=True, silent=True) or {}
    conn = _get_db()
    try:
        old = conn.execute("SELECT status FROM proposal_reviews WHERE id = ?", (rev_id,)).fetchone()
        if not old:
            return jsonify({"error": "Review not found"}), 404

        allowed = ["status", "started_at", "completed_at", "lead_reviewer", "participants", "summary", "overall_rating"]
        sets = []
        params = []
        for key in allowed:
            if key in data:
                sets.append(f"{key} = ?")
                params.append(data[key])
        if not sets:
            return jsonify({"error": "No valid fields to update"}), 400

        params.append(rev_id)
        conn.execute(f"UPDATE proposal_reviews SET {', '.join(sets)} WHERE id = ?", params)

        if "status" in data and data["status"] != old["status"]:
            _record_status_change(conn, "review", rev_id, old["status"], data["status"], data.get("changed_by"))

        conn.commit()
        return jsonify({"id": rev_id, "updated": True})
    finally:
        conn.close()


@proposals_api.route("/reviews/<rev_id>/findings", methods=["GET"])
def list_findings(rev_id):
    """GET /api/proposals/reviews/<rev_id>/findings — List findings for a review."""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT f.*, s.title as section_title, s.section_number
               FROM proposal_review_findings f
               LEFT JOIN proposal_sections s ON f.section_id = s.id
               WHERE f.review_id = ?
               ORDER BY CASE f.severity
                   WHEN 'critical' THEN 1 WHEN 'major' THEN 2
                   WHEN 'minor' THEN 3 ELSE 4 END, f.created_at""",
            (rev_id,),
        ).fetchall()
        findings = [dict(r) for r in rows]
        open_count = sum(1 for f in findings if f["status"] in ("open", "in_progress"))
        return jsonify({"findings": findings, "total": len(findings), "open": open_count})
    finally:
        conn.close()


@proposals_api.route("/reviews/<rev_id>/findings", methods=["POST"])
def add_finding(rev_id):
    """POST /api/proposals/reviews/<rev_id>/findings — Add a review finding."""
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("description"):
        return jsonify({"error": "description is required"}), 400
    find_id = _uuid()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO proposal_review_findings
               (id, review_id, section_id, finding_type, severity, description,
                recommendation, assigned_to)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                find_id, rev_id,
                data.get("section_id"),
                data.get("finding_type", "other"),
                data.get("severity", "minor"),
                data["description"],
                data.get("recommendation"),
                data.get("assigned_to"),
            ),
        )
        conn.commit()
        return jsonify({"id": find_id}), 201
    finally:
        conn.close()


@proposals_api.route("/findings/<find_id>", methods=["PUT"])
def update_finding(find_id):
    """PUT /api/proposals/findings/<id> — Update finding (resolve, assign)."""
    data = request.get_json(force=True, silent=True) or {}
    conn = _get_db()
    try:
        old = conn.execute("SELECT status FROM proposal_review_findings WHERE id = ?", (find_id,)).fetchone()
        if not old:
            return jsonify({"error": "Finding not found"}), 404

        allowed = ["status", "assigned_to", "resolved_at", "resolution_notes"]
        sets = []
        params = []
        for key in allowed:
            if key in data:
                sets.append(f"{key} = ?")
                params.append(data[key])
        if not sets:
            return jsonify({"error": "No valid fields to update"}), 400

        params.append(find_id)
        conn.execute(f"UPDATE proposal_review_findings SET {', '.join(sets)} WHERE id = ?", params)

        if "status" in data and data["status"] != old["status"]:
            _record_status_change(conn, "finding", find_id, old["status"], data["status"], data.get("changed_by"))

        conn.commit()
        return jsonify({"id": find_id, "updated": True})
    finally:
        conn.close()


# =====================================================================
# Aggregate / Dashboard Data
# =====================================================================

@proposals_api.route("/opportunities/<opp_id>/timeline", methods=["GET"])
def get_timeline(opp_id):
    """GET /api/proposals/<opp_id>/timeline — Timeline data for Gantt view."""
    conn = _get_db()
    try:
        sections = conn.execute(
            """SELECT s.id, s.section_number, s.title, s.status, s.due_date, s.writer,
                      s.priority, s.sort_order, v.volume_number, v.title as volume_title
               FROM proposal_sections s
               LEFT JOIN proposal_volumes v ON s.volume_id = v.id
               WHERE s.opportunity_id = ?
               ORDER BY v.sort_order, v.volume_number, s.sort_order, s.section_number""",
            (opp_id,),
        ).fetchall()
        items = [dict(s) for s in sections]

        # Attach dependencies
        for item in items:
            deps = conn.execute(
                "SELECT depends_on_section_id FROM proposal_section_dependencies WHERE section_id = ?",
                (item["id"],),
            ).fetchall()
            item["dependencies"] = [d["depends_on_section_id"] for d in deps]

        # Opportunity due date
        opp = conn.execute("SELECT due_date FROM proposal_opportunities WHERE id = ?", (opp_id,)).fetchone()
        due_date = opp["due_date"] if opp else None

        return jsonify({"sections": items, "due_date": due_date, "status_order": SECTION_STATUS_ORDER})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/assignment-matrix", methods=["GET"])
def get_assignment_matrix(opp_id):
    """GET /api/proposals/<opp_id>/assignment-matrix — Section x Writer x Status matrix."""
    conn = _get_db()
    try:
        sections = conn.execute(
            """SELECT s.id, s.section_number, s.title, s.status, s.due_date,
                      s.writer, s.priority, v.title as volume_title
               FROM proposal_sections s
               LEFT JOIN proposal_volumes v ON s.volume_id = v.id
               WHERE s.opportunity_id = ?
               ORDER BY v.sort_order, s.sort_order""",
            (opp_id,),
        ).fetchall()

        # Group by writer
        matrix = {}
        writers = set()
        for s in sections:
            writer = s["writer"] or "Unassigned"
            writers.add(writer)
            if writer not in matrix:
                matrix[writer] = []
            matrix[writer].append(dict(s))

        return jsonify({"writers": sorted(writers), "matrix": matrix, "status_order": SECTION_STATUS_ORDER})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/stats", methods=["GET"])
def get_stats(opp_id):
    """GET /api/proposals/<opp_id>/stats — Aggregate stats for auto-refresh."""
    conn = _get_db()
    try:
        opp = conn.execute("SELECT due_date FROM proposal_opportunities WHERE id = ?", (opp_id,)).fetchone()
        if not opp:
            return jsonify({"error": "Opportunity not found"}), 404

        sections = conn.execute(
            "SELECT status FROM proposal_sections WHERE opportunity_id = ?", (opp_id,)
        ).fetchall()
        total = len(sections)
        complete = sum(1 for s in sections if s["status"] in ("final", "submitted"))

        # Section status distribution
        status_dist = {}
        for s in sections:
            st = s["status"]
            status_dist[st] = status_dist.get(st, 0) + 1

        cm = conn.execute(
            "SELECT compliance_status FROM proposal_compliance_matrix WHERE opportunity_id = ?", (opp_id,)
        ).fetchall()
        cm_total = len(cm)
        cm_addressed = sum(1 for c in cm if c["compliance_status"] != "not_addressed")

        findings = conn.execute(
            """SELECT f.severity, f.status FROM proposal_review_findings f
               JOIN proposal_reviews r ON f.review_id = r.id
               WHERE r.opportunity_id = ?""",
            (opp_id,),
        ).fetchall()
        open_findings = sum(1 for f in findings if f["status"] in ("open", "in_progress"))
        severity_dist = {}
        for f in findings:
            if f["status"] in ("open", "in_progress"):
                sev = f["severity"]
                severity_dist[sev] = severity_dist.get(sev, 0) + 1

        try:
            due = datetime.strptime(opp["due_date"], "%Y-%m-%d")
            days_left = (due - datetime.utcnow()).days
        except (ValueError, TypeError):
            days_left = None

        return jsonify({
            "sections_total": total,
            "sections_complete": complete,
            "section_status_distribution": status_dist,
            "compliance_total": cm_total,
            "compliance_addressed": cm_addressed,
            "compliance_coverage_pct": round((cm_addressed / cm_total * 100) if cm_total > 0 else 0, 1),
            "open_findings": open_findings,
            "finding_severity_distribution": severity_dist,
            "days_to_deadline": days_left,
        })
    finally:
        conn.close()


@proposals_api.route("/status-history/<entity_type>/<entity_id>", methods=["GET"])
def get_status_history(entity_type, entity_id):
    """GET /api/proposals/status-history/<type>/<id> — Status history for any entity."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM proposal_status_history WHERE entity_type = ? AND entity_id = ? ORDER BY created_at DESC",
            (entity_type, entity_id),
        ).fetchall()
        return jsonify({"history": [dict(r) for r in rows]})
    finally:
        conn.close()
