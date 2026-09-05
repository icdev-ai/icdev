#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: Proposal Writing Lifecycle Tracker.

Full GovCon proposal lifecycle — opportunities, volumes, sections,
compliance matrix (L/M/N), color team reviews, findings, and status history.
"""

import json as _mac_json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, make_response, request



BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.security.abac_engine import abac_protect  # noqa: E402
from tools.dashboard.auth import require_role  # noqa: E402
from tools.common.helpers import now_iso  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402
logger = get_logger("icdev.dashboard.api.proposals")

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

proposals_api = Blueprint("proposals_api", __name__, url_prefix="/api/proposals")


class _PGCompatConn:
    """Silently pre-translate ? → %s for PG so translate_sql never warns."""
    def __init__(self, conn):
        self._conn = conn
        self._pg = getattr(conn, "_backend", "sqlite") == "postgresql"
    def _fix(self, sql):
        return sql.replace("?", "%s") if self._pg and "?" in sql else sql
    def execute(self, sql, params=()):
        return self._conn.execute(self._fix(sql), params)
    def executemany(self, sql, seq):
        return self._conn.executemany(self._fix(sql), seq)
    def commit(self): return self._conn.commit()
    def rollback(self): return self._conn.rollback()
    def close(self): return self._conn.close()
    def __getattr__(self, name): return getattr(self._conn, name)


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return _PGCompatConn(conn)


# ---------------------------------------------------------------------------
# Bell-LaPadula MAC helpers (prop-sec-02)
# ---------------------------------------------------------------------------

def _mac_ctx():
    """Return current security context from Flask g (None = system/unauthenticated)."""
    try:
        from flask import g
        return getattr(g, "security_context", None)
    except RuntimeError:
        return None


def _mac_compartments(raw):
    """Parse compartments from JSON string or collection to a plain set."""
    if isinstance(raw, (list, tuple, set, frozenset)):
        return set(raw)
    try:
        return set(_mac_json.loads(raw or "[]"))
    except Exception:
        return set()


def _mac_filter(rows):
    """Filter row list — keep only those the current user can read per Bell-LaPadula.

    Applies no-read-up (clearance >= classification) and strict-subset
    compartment check (resource compartments ⊆ user compartments).
    """
    from tools.security.classification_enforcer import can_read, can_access_compartment
    ctx = _mac_ctx()
    if ctx is None:
        return [dict(r) if not isinstance(r, dict) else r for r in rows]
    result = []
    for row in rows:
        d = dict(row) if not isinstance(row, dict) else row
        cls = d.get("classification", "CUI") or "CUI"
        comps = _mac_compartments(d.get("compartments", "[]"))
        if can_read(cls, ctx) and can_access_compartment(comps, ctx):
            result.append(d)
    return result


def _mac_deny_read(row):
    """Return 403 JSON response if current user cannot read this row. Returns None on pass."""
    from tools.security.classification_enforcer import can_read, can_access_compartment
    ctx = _mac_ctx()
    if ctx is None:
        return None
    d = dict(row) if not isinstance(row, dict) else row
    cls = d.get("classification", "CUI") or "CUI"
    comps = _mac_compartments(d.get("compartments", "[]"))
    if not can_read(cls, ctx) or not can_access_compartment(comps, ctx):
        return make_response(
            jsonify({"error": "Insufficient clearance", "code": "MAC_DENIED"}), 403
        )
    return None


def _mac_deny_write(classification, compartments_raw="[]"):
    """Return 403 JSON response if current user cannot write at this classification.

    Implements no-write-down: user clearance must be >= target classification so
    a lower-cleared user cannot create or update a higher-classified resource.
    """
    from tools.security.classification_enforcer import can_read, can_access_compartment
    ctx = _mac_ctx()
    if ctx is None:
        return None
    cls = (classification or "CUI").upper()
    comps = _mac_compartments(compartments_raw)
    if not can_read(cls, ctx) or not can_access_compartment(comps, ctx):
        return make_response(
            jsonify({"error": "MAC write policy denied", "code": "MAC_WRITE_DENIED"}), 403
        )
    return None


def _mac_check_parent_opp(opp_id, conn):
    """Check MAC read access on the parent opportunity. Returns 403 response or None."""
    try:
        row = conn.execute(
            "SELECT classification, compartments FROM proposal_opportunities WHERE id = %s",
            (opp_id,),
        ).fetchone()
        if not row:
            return None  # Let child route return 404
        return _mac_deny_read(row)
    except Exception:
        return None


def _uuid():
    return str(uuid.uuid4())


def _record_status_change(conn, entity_type, entity_id, old_status, new_status, changed_by=None, reason=None):
    """Insert into append-only proposal_status_history."""
    conn.execute(
        "INSERT INTO proposal_status_history (entity_type, entity_id, old_status, new_status, changed_by, reason) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (entity_type, entity_id, old_status, new_status, changed_by, reason),
    )


def _section_resource_attrs(request):
    """Build ABAC resource dict for a proposal section endpoint."""
    sec_id = (request.view_args or {}).get("sec_id", "")
    writer_email = ""
    if sec_id:
        try:
            conn = _get_db()
            row = conn.execute(
                "SELECT writer_email FROM proposal_sections WHERE id = %s", (sec_id,)
            ).fetchone()
            conn.close()
            if row:
                writer_email = row["writer_email"] or ""
        except Exception:
            pass
    return {"type": "proposal_section", "writer_email": writer_email}


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

# Valid color team review types for proposal_reviews (matches DB CHECK constraint)
REVIEW_TYPES = ("pink_team", "red_team", "gold_team", "white_team", "white_glove", "internal")

# Ordered status list for pipeline rendering
SECTION_STATUS_ORDER = [
    "not_started",
    "outlining",
    "drafting",
    "internal_review",
    "pink_team_ready",
    "pink_team_review",
    "rework_pink",
    "red_team_ready",
    "red_team_review",
    "rework_red",
    "gold_team_ready",
    "gold_team_review",
    "white_glove",
    "final",
    "submitted",
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
        # Bell-LaPadula: filter to only rows the caller can read (no-read-up)
        items = _mac_filter(rows)
        return jsonify({"opportunities": items, "total": len(items)})
    finally:
        conn.close()


@proposals_api.route("/opportunities", methods=["POST"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def create_opportunity():
    """POST /api/proposals/opportunities — Create a new opportunity."""
    data = request.get_json(force=True, silent=True) or {}
    required = ["solicitation_number", "title", "agency", "due_date", "proposal_type"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    # Bell-LaPadula: no-write-down — caller must have clearance >= target classification
    denied = _mac_deny_write(data.get("classification", "CUI"), data.get("compartments", "[]"))
    if denied:
        return denied

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
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'intake', %s, %s, %s, %s, %s)""",
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
        row = conn.execute("SELECT * FROM proposal_opportunities WHERE id = %s", (opp_id,)).fetchone()
        if not row:
            return jsonify({"error": "Opportunity not found"}), 404
        # Bell-LaPadula: no-read-up check before serving data
        denied = _mac_deny_read(row)
        if denied:
            return denied
        opp = dict(row)

        # Aggregate stats
        sections = conn.execute("SELECT status FROM proposal_sections WHERE opportunity_id = %s", (opp_id,)).fetchall()
        total_sections = len(sections)
        complete_sections = sum(1 for s in sections if s["status"] in ("final", "submitted"))

        cm_rows = conn.execute(
            "SELECT compliance_status FROM proposal_compliance_matrix WHERE opportunity_id = %s", (opp_id,)
        ).fetchall()
        total_cm = len(cm_rows)
        addressed = sum(1 for c in cm_rows if c["compliance_status"] not in ("not_addressed",))
        coverage_pct = round((addressed / total_cm * 100) if total_cm > 0 else 0, 1)

        findings = conn.execute(
            """SELECT f.severity, f.status FROM proposal_review_findings f
               JOIN proposal_reviews r ON f.review_id = r.id
               WHERE r.opportunity_id = %s""",
            (opp_id,),
        ).fetchall()
        open_findings = sum(1 for f in findings if f["status"] in ("open", "in_progress"))
        critical_findings = sum(
            1 for f in findings if f["severity"] == "critical" and f["status"] in ("open", "in_progress")
        )

        # Days to deadline
        try:
            due = datetime.strptime(opp["due_date"], "%Y-%m-%d")
            days_left = (due - datetime.now(timezone.utc).replace(tzinfo=None)).days
        except (ValueError, TypeError):
            days_left = None

        # Review gate status
        reviews = conn.execute(
            "SELECT review_type, status, scheduled_date, overall_rating FROM proposal_reviews WHERE opportunity_id = %s ORDER BY created_at",  # noqa: E501
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
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def update_opportunity(opp_id):
    """PUT /api/proposals/opportunities/<id> — Update opportunity fields."""
    data = request.get_json(force=True, silent=True) or {}
    conn = _get_db()
    try:
        # Bell-LaPadula: check read + write access on existing record before updating
        existing = conn.execute(
            "SELECT classification, compartments FROM proposal_opportunities WHERE id = %s", (opp_id,)
        ).fetchone()
        if not existing:
            return jsonify({"error": "Opportunity not found"}), 404
        denied = _mac_deny_read(existing)
        if denied:
            return denied
        # If caller is upgrading classification, they must also hold that level
        new_cls = data.get("classification", existing["classification"] or "CUI")
        denied = _mac_deny_write(new_cls, data.get("compartments", existing.get("compartments", "[]")))
        if denied:
            return denied

        allowed = [
            "title",
            "agency",
            "sub_agency",
            "due_date",
            "due_time",
            "set_aside_type",
            "naics_code",
            "estimated_value_low",
            "estimated_value_high",
            "proposal_type",
            "rfp_document_path",
            "rfp_url",
            "capture_manager",
            "proposal_manager",
            "bid_decision",
            "bid_decision_date",
            "bid_decision_rationale",
            "questions_due_date",
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
        params.append(now_iso())
        params.append(opp_id)
        conn.execute(f"UPDATE proposal_opportunities SET {', '.join(sets)} WHERE id = %s", params)  # nosec B608 -- table/column names are internal constants, not user input
        conn.commit()
        return jsonify({"id": opp_id, "updated": True})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/language-config", methods=["GET"])
def get_language_config(opp_id):
    """GET /api/proposals/opportunities/<id>/language-config — Aggregated language settings.

    Returns the opportunity's glossary, wall-of-truth, taxonomy, and active style templates.
    """
    conn = _get_db()
    try:
        existing = conn.execute(
            "SELECT classification, compartments FROM proposal_opportunities WHERE id = %s", (opp_id,)
        ).fetchone()
        if not existing:
            return jsonify({"error": "Opportunity not found"}), 404
        denied = _mac_deny_read(existing)
        if denied:
            return denied

        glossary = conn.execute(
            "SELECT * FROM wg_glossary WHERE scope = 'project' AND scope_id = %s AND is_active = 1 ORDER BY term_type, term",
            (opp_id,),
        ).fetchall()
        taxonomy = conn.execute(
            "SELECT * FROM proposal_taxonomy WHERE opportunity_id = %s AND is_active = 1 ORDER BY label",
            (opp_id,),
        ).fetchall()
        style_guides = conn.execute(
            "SELECT id, guide_name, version, created_at FROM wg_style_guides WHERE scope = 'project' AND scope_id = %s AND is_active = 1 ORDER BY guide_name",
            (opp_id,),
        ).fetchall()
        return jsonify({
            "opportunity_id": opp_id,
            "glossary": [dict(r) for r in glossary],
            "wall_of_truth": [dict(r) for r in glossary if r["term_type"] in ("banned", "deprecated")],
            "taxonomy": [dict(r) for r in taxonomy],
            "style_guides": [dict(r) for r in style_guides],
        })
    finally:
        conn.close()


def _opportunity_content_signature(opp_id, conn):
    import hashlib

    rows = conn.execute(
        """SELECT d.draft_content
           FROM proposal_section_drafts d
           WHERE d.opportunity_id = %s
           ORDER BY d.section_id, d.created_at DESC""",
        (opp_id,),
    ).fetchall()
    joined = "\x1f".join((dict(r).get("draft_content") or "") for r in rows)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _opportunity_result_set(opp_id, conn):
    """Latest draft per section for this opportunity, as aggregation_guard elements.

    Avoids PostgreSQL-only `DISTINCT ON` (not SQLite-portable, and CLAUDE.md's
    PG-portability guardrail prefers computing reductions in Python over
    dialect-specific SQL) — fetches all drafts ordered newest-first and keeps
    the first (= latest) occurrence per section_id.
    """
    rows = conn.execute(
        """SELECT d.section_id, d.draft_content, d.classification, s.title
           FROM proposal_section_drafts d
           LEFT JOIN proposal_sections s ON s.id = d.section_id
           WHERE d.opportunity_id = %s
           ORDER BY d.created_at DESC""",
        (opp_id,),
    ).fetchall()
    latest_by_section = {}
    for r in rows:
        d = dict(r)
        latest_by_section.setdefault(d["section_id"], d)
    return [
        {
            "element_id": sec_id,
            "label": d.get("title") or sec_id,
            "text": d.get("draft_content") or "",
            "classification": d.get("classification") or "CUI",
        }
        for sec_id, d in latest_by_section.items()
    ]


def _check_aggregation_guard_for_opportunity(opp_id, conn):
    """Run the aggregation guard for an opportunity's section drafts.

    Returns None if not blocked, or a response-body dict (409 shape,
    gate='aggregation_guard') if a block-action rule fired and no resolved
    override exists for the current content signature.
    """
    from tools.security.aggregation_guard import guard_result

    result_set = _opportunity_result_set(opp_id, conn)
    signature = _opportunity_content_signature(opp_id, conn)
    guard = guard_result(result_set, ctx={"surface_ceiling": "CUI"}, surface="proposals/submit")

    if guard["action"] != "block":
        return None

    for rule in guard["fired_rules"]:
        if rule["action"] != "block":
            continue
        existing = conn.execute(
            """SELECT resolution FROM document_aggregation_findings
               WHERE surface=%s AND document_id=%s AND rule_id=%s AND content_signature=%s""",
            ("proposals", opp_id, rule["rule_id"], signature),
        ).fetchone()
        if existing and dict(existing).get("resolution") == "override":
            continue
        if not existing:
            conn.execute(
                """INSERT INTO document_aggregation_findings
                   (id, surface, document_id, rule_id, derived_classification,
                    matched_elements, content_signature, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    str(uuid.uuid4()),
                    "proposals",
                    opp_id,
                    rule["rule_id"],
                    guard["derived"],
                    _mac_json.dumps(rule["matched_elements"]),
                    signature,
                    now_iso(),
                ),
            )
            conn.commit()
        return {
            "error": "Aggregation guard: derived classification exceeds surface ceiling — review required",
            "gate": "aggregation_guard",
            "derived": guard["derived"],
            "fired_rules": guard["fired_rules"],
        }
    return None


@proposals_api.route("/opportunities/<opp_id>/aggregation-guard/override", methods=["POST"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def override_aggregation_guard(opp_id):
    """POST /api/proposals/opportunities/<id>/aggregation-guard/override — HITL clear."""
    data = request.get_json(force=True, silent=True) or {}
    comment = data.get("comment", "")
    resolved_by = data.get("resolved_by") or "unknown"
    conn = _get_db()
    try:
        signature = _opportunity_content_signature(opp_id, conn)
        rows = conn.execute(
            """SELECT id FROM document_aggregation_findings
               WHERE surface=%s AND document_id=%s AND content_signature=%s AND resolution IS NULL""",
            ("proposals", opp_id, signature),
        ).fetchall()
        now = now_iso()
        for row in rows:
            rid = dict(row)["id"]
            conn.execute(
                """UPDATE document_aggregation_findings
                   SET resolution='override', resolved_by=%s, resolved_at=%s, resolution_comment=%s
                   WHERE id=%s""",
                (resolved_by, now, comment, rid),
            )
        conn.commit()
        return jsonify({"ok": True, "resolved_count": len(rows)})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/status", methods=["PUT"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def change_opportunity_status(opp_id):
    """PUT /api/proposals/opportunities/<id>/status — Change opportunity status.

    Gate: transition to 'submitted' is blocked unless a 'gold_team' review
    exists with overall_rating IN ('pass', 'pass_with_findings') and
    status = 'completed'. Pass force=true to bypass (admin only).
    """
    data = request.get_json(force=True, silent=True) or {}
    new_status = data.get("status")
    if not new_status:
        return jsonify({"error": "status is required"}), 400
    conn = _get_db()
    try:
        row = conn.execute("SELECT status FROM proposal_opportunities WHERE id = %s", (opp_id,)).fetchone()
        if not row:
            return jsonify({"error": "Opportunity not found"}), 404
        old_status = row["status"]

        # Gold-team sign-off gate (D-PROP-GOLD): block 'submitted' until Gold passes
        if new_status == "submitted" and not data.get("force"):
            gold_review = conn.execute(
                """SELECT id, overall_rating FROM proposal_reviews
                   WHERE opportunity_id = %s AND review_type = 'gold_team'
                     AND status = 'completed'
                     AND overall_rating IN ('pass', 'pass_with_findings')
                   ORDER BY created_at DESC LIMIT 1""",
                (opp_id,),
            ).fetchone()
            if not gold_review:
                return jsonify({
                    "error": "Gold-team sign-off required before submitting",
                    "gate": "gold_team_signoff",
                    "detail": (
                        "A completed Gold Team review with overall_rating 'pass' or "
                        "'pass_with_findings' must exist before this opportunity can be submitted."
                    ),
                }), 409

            # Aggregation guard (prop-sec-07): classification-by-compilation /
            # mosaic-effect check across this opportunity's section drafts.
            # Unlike the Gold-team gate's unaudited `force` bypass, this gate
            # can only be cleared via the override endpoint below (recorded,
            # attributed, auditable) — "block + HITL review required" is a
            # locked-in decision, not to be weakened by force=true.
            blocked = _check_aggregation_guard_for_opportunity(opp_id, conn)
            if blocked:
                return jsonify(blocked), 409

        conn.execute(
            "UPDATE proposal_opportunities SET status = %s, updated_at = %s WHERE id = %s",
            (new_status, now_iso(), opp_id),
        )
        _record_status_change(
            conn, "opportunity", opp_id, old_status, new_status, data.get("changed_by"), data.get("reason")
        )
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
        # Bell-LaPadula: inherit parent opportunity classification
        denied = _mac_check_parent_opp(opp_id, conn)
        if denied:
            return denied
        rows = conn.execute(
            "SELECT * FROM proposal_volumes WHERE opportunity_id = %s ORDER BY sort_order, volume_number",
            (opp_id,),
        ).fetchall()
        return jsonify({"volumes": [dict(r) for r in rows]})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/volumes", methods=["POST"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
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
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                vol_id,
                opp_id,
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
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
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
        params.append(now_iso())
        params.append(vol_id)
        conn.execute(f"UPDATE proposal_volumes SET {', '.join(sets)} WHERE id = %s", params)  # nosec B608 -- table/column names are internal constants, not user input
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
        # Bell-LaPadula: inherit parent opportunity classification
        denied = _mac_check_parent_opp(opp_id, conn)
        if denied:
            return denied
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
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
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
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                sec_id,
                data["volume_id"],
                opp_id,
                data.get("parent_section_id"),
                data["section_number"],
                data["title"],
                data.get("description"),
                data.get("writer"),
                data.get("writer_email"),
                data.get("reviewer"),
                data.get("page_limit"),
                data.get("word_limit"),
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
               WHERE s.id = %s""",
            (sec_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "Section not found"}), 404
        section = dict(row)

        # Status history
        history = conn.execute(
            "SELECT * FROM proposal_status_history WHERE entity_type = 'section' AND entity_id = %s ORDER BY created_at DESC",  # noqa: E501
            (sec_id,),
        ).fetchall()
        section["history"] = [dict(h) for h in history]

        # Dependencies
        deps = conn.execute(
            """SELECT d.*, s.title as depends_on_title, s.status as depends_on_status
               FROM proposal_section_dependencies d
               JOIN proposal_sections s ON d.depends_on_section_id = s.id
               WHERE d.section_id = %s""",
            (sec_id,),
        ).fetchall()
        section["dependencies"] = [dict(d) for d in deps]

        # Compliance items linked to this section
        cm = conn.execute(
            "SELECT * FROM proposal_compliance_matrix WHERE proposal_section_id = %s ORDER BY sort_order",
            (sec_id,),
        ).fetchall()
        section["compliance_items"] = [dict(c) for c in cm]

        # Review findings for this section
        findings = conn.execute(
            """SELECT f.*, r.review_type FROM proposal_review_findings f
               JOIN proposal_reviews r ON f.review_id = r.id
               WHERE f.section_id = %s ORDER BY f.created_at DESC""",
            (sec_id,),
        ).fetchall()
        section["findings"] = [dict(f) for f in findings]

        # Valid next statuses
        section["valid_transitions"] = SECTION_TRANSITIONS.get(section["status"], [])

        return jsonify(section)
    finally:
        conn.close()


@proposals_api.route("/sections/<sec_id>", methods=["PUT"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
@abac_protect(_section_resource_attrs, "PUT")
def update_section(sec_id):
    """PUT /api/proposals/sections/<id> — Update section fields (not status)."""
    data = request.get_json(force=True, silent=True) or {}
    conn = _get_db()
    try:
        allowed = [
            "title",
            "description",
            "writer",
            "writer_email",
            "reviewer",
            "page_limit",
            "word_limit",
            "current_word_count",
            "current_page_count",
            "priority",
            "due_date",
            "notes",
            "content_path",
            "sort_order",
            "section_number",
            "parent_section_id",
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
        params.append(now_iso())
        params.append(sec_id)
        conn.execute(f"UPDATE proposal_sections SET {', '.join(sets)} WHERE id = %s", params)  # nosec B608 -- table/column names are internal constants, not user input
        conn.commit()
        return jsonify({"id": sec_id, "updated": True})
    finally:
        conn.close()


@proposals_api.route("/sections/<sec_id>/status", methods=["PUT"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
@abac_protect(_section_resource_attrs, "PUT")
def advance_section_status(sec_id):
    """PUT /api/proposals/sections/<id>/status — Advance section status (enforces transitions)."""
    data = request.get_json(force=True, silent=True) or {}
    new_status = data.get("status")
    if not new_status:
        return jsonify({"error": "status is required"}), 400

    conn = _get_db()
    try:
        row = conn.execute("SELECT status, opportunity_id FROM proposal_sections WHERE id = %s", (sec_id,)).fetchone()
        if not row:
            return jsonify({"error": "Section not found"}), 404

        old_status = row["status"]
        force = data.get("force", False)

        if not force:
            valid = SECTION_TRANSITIONS.get(old_status, [])
            if new_status not in valid:
                return jsonify(
                    {
                        "error": f"Invalid transition: {old_status} -> {new_status}",
                        "valid_transitions": valid,
                    }
                ), 400

            # Check dependencies (D-PROP-5)
            deps = conn.execute(
                """SELECT d.depends_on_section_id, d.required_status, s.status as current_dep_status, s.title
                   FROM proposal_section_dependencies d
                   JOIN proposal_sections s ON d.depends_on_section_id = s.id
                   WHERE d.section_id = %s""",
                (sec_id,),
            ).fetchall()
            blocked_by = []
            for dep in deps:
                req_idx = (
                    SECTION_STATUS_ORDER.index(dep["required_status"])
                    if dep["required_status"] in SECTION_STATUS_ORDER
                    else 0
                )
                cur_idx = (
                    SECTION_STATUS_ORDER.index(dep["current_dep_status"])
                    if dep["current_dep_status"] in SECTION_STATUS_ORDER
                    else 0
                )
                if cur_idx < req_idx:
                    blocked_by.append(
                        {
                            "section": dep["title"],
                            "current_status": dep["current_dep_status"],
                            "required_status": dep["required_status"],
                        }
                    )
            if blocked_by:
                return jsonify({"error": "Blocked by dependencies", "blocked_by": blocked_by}), 409

        conn.execute(
            "UPDATE proposal_sections SET status = %s, updated_at = %s WHERE id = %s",
            (new_status, now_iso(), sec_id),
        )
        _record_status_change(
            conn, "section", sec_id, old_status, new_status, data.get("changed_by"), data.get("reason")
        )
        conn.commit()
        return jsonify({"id": sec_id, "old_status": old_status, "new_status": new_status})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/sections/dependencies", methods=["POST"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
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
            "INSERT INTO proposal_section_dependencies (section_id, depends_on_section_id, dependency_type, required_status) VALUES (%s, %s, %s, %s)",  # noqa: E501
            (
                data["section_id"],
                data["depends_on_section_id"],
                data.get("dependency_type", "content"),
                data.get("required_status", "drafting"),
            ),
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
        stats = {
            "total": total,
            "compliant": 0,
            "partial": 0,
            "non_compliant": 0,
            "not_addressed": 0,
            "not_applicable": 0,
        }
        for item in items:
            st = item.get("compliance_status", "not_addressed")
            if st in stats:
                stats[st] += 1
        stats["coverage_pct"] = round(((total - stats["not_addressed"]) / total * 100) if total > 0 else 0, 1)

        return jsonify({"items": items, "stats": stats})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/compliance", methods=["POST"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
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
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                cm_id,
                opp_id,
                data["section_ref"],
                data.get("volume_ref"),
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
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def batch_create_compliance(opp_id):
    """POST /api/proposals/<opp_id>/compliance/batch — Batch create compliance items.

    Two payload shapes, one table (rmf-rfp-01):

      {"items": [...]}                       hand-built rows, unchanged
      {"parsed": {...}, "section_text": {}}  a solicitation: `parsed` is
                                             solicitation_parser output and
                                             `section_text` maps L/M/C to raw
                                             section bodies; either or both.
                                             compliance_matrix_builder extracts
                                             the L/M/C requirements and stores
                                             them, deduplicated on text.

    The second shape is what gives the Section L/M matrix builder a ROUTE --
    it had a CLI and zero callers.
    """
    data = request.get_json(force=True, silent=True) or {}
    items = data.get("items", [])
    parsed = data.get("parsed")
    section_text = data.get("section_text")
    if parsed is not None and not isinstance(parsed, dict):
        return jsonify({"error": "parsed must be an object (solicitation_parser output)"}), 400
    if section_text is not None and not isinstance(section_text, dict):
        return jsonify({"error": "section_text must map section letters to text"}), 400
    if not items and not parsed and not section_text:
        return jsonify({"error": "items array, or parsed / section_text, is required"}), 400

    if parsed or section_text:
        from tools.govcon.compliance_matrix_builder import build_from_parsed

        conn = _get_db()
        try:
            result = build_from_parsed(opp_id, parsed or {}, section_text or {}, conn=conn)
        finally:
            conn.close()
        return jsonify(result), 201

    conn = _get_db()
    try:
        created = 0
        for item in items:
            cm_id = _uuid()
            conn.execute(
                """INSERT INTO proposal_compliance_matrix
                   (id, opportunity_id, section_ref, volume_ref, requirement_text,
                    requirement_type, compliance_status, proposal_section_id, sort_order)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    cm_id,
                    opp_id,
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
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def update_compliance_item(item_id):
    """PUT /api/proposals/compliance/<id> — Update compliance item."""
    data = request.get_json(force=True, silent=True) or {}
    conn = _get_db()
    try:
        allowed = [
            "section_ref",
            "volume_ref",
            "requirement_text",
            "requirement_type",
            "compliance_status",
            "proposal_section_id",
            "response_summary",
            "notes",
            "sort_order",
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
            old = conn.execute(
                "SELECT compliance_status FROM proposal_compliance_matrix WHERE id = %s", (item_id,)
            ).fetchone()
            if old and old["compliance_status"] != data["compliance_status"]:
                _record_status_change(
                    conn, "compliance_item", item_id, old["compliance_status"], data["compliance_status"]
                )

        sets.append("updated_at = ?")
        params.append(now_iso())
        params.append(item_id)
        conn.execute(f"UPDATE proposal_compliance_matrix SET {', '.join(sets)} WHERE id = %s", params)  # nosec B608 -- table/column names are internal constants, not user input
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
               WHERE opportunity_id = %s AND compliance_status = 'not_addressed'
               ORDER BY sort_order, section_ref""",
            (opp_id,),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM proposal_compliance_matrix WHERE opportunity_id = %s", (opp_id,)
        ).fetchone()[0]
        gaps = [dict(r) for r in rows]
        return jsonify(
            {
                "gaps": gaps,
                "total_gaps": len(gaps),
                "total_requirements": total,
                "gap_pct": round((len(gaps) / total * 100) if total > 0 else 0, 1),
            }
        )
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
                "SELECT status, COUNT(*) as cnt FROM proposal_review_findings WHERE review_id = %s GROUP BY status",
                (rev["id"],),
            ).fetchall()
            rev["finding_counts"] = {c["status"]: c["cnt"] for c in counts}
            rev["total_findings"] = sum(c["cnt"] for c in counts)

        return jsonify({"reviews": reviews})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/reviews", methods=["POST"])
@require_role("admin", "pm", "reviewer")
def schedule_review(opp_id):
    """POST /api/proposals/<opp_id>/reviews — Schedule a color team review."""
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("review_type"):
        return jsonify({"error": "review_type is required"}), 400
    if data["review_type"] not in REVIEW_TYPES:
        return jsonify({"error": f"Invalid review_type. Must be one of: {', '.join(REVIEW_TYPES)}"}), 400
    rev_id = _uuid()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO proposal_reviews
               (id, opportunity_id, review_type, status, scheduled_date,
                lead_reviewer, participants, summary)
               VALUES (%s, %s, %s, 'scheduled', %s, %s, %s, %s)""",
            (
                rev_id,
                opp_id,
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
@require_role("admin", "pm", "reviewer")
def update_review(rev_id):
    """PUT /api/proposals/reviews/<id> — Update review (start, complete, rate)."""
    data = request.get_json(force=True, silent=True) or {}
    conn = _get_db()
    try:
        old = conn.execute("SELECT status FROM proposal_reviews WHERE id = %s", (rev_id,)).fetchone()
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
        conn.execute(f"UPDATE proposal_reviews SET {', '.join(sets)} WHERE id = %s", params)  # nosec B608 -- table/column names are internal constants, not user input

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
               WHERE f.review_id = %s
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
@require_role("admin", "pm", "reviewer")
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
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                find_id,
                rev_id,
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
@require_role("admin", "pm", "reviewer")
def update_finding(find_id):
    """PUT /api/proposals/findings/<id> — Update finding (resolve, assign)."""
    data = request.get_json(force=True, silent=True) or {}
    conn = _get_db()
    try:
        old = conn.execute("SELECT status FROM proposal_review_findings WHERE id = %s", (find_id,)).fetchone()
        if not old:
            return jsonify({"error": "Finding not found"}), 404

        allowed = ["status", "assigned_to", "resolved_at", "resolution_notes",
                   "description", "recommendation", "due_date",
                   "resolved_evidence", "closure_approved_by"]
        sets = []
        params = []
        for key in allowed:
            if key in data:
                sets.append(f"{key} = ?")
                params.append(data[key])
        if not sets:
            return jsonify({"error": "No valid fields to update"}), 400

        params.append(find_id)
        conn.execute(f"UPDATE proposal_review_findings SET {', '.join(sets)} WHERE id = %s", params)  # nosec B608 -- table/column names are internal constants, not user input

        if "status" in data and data["status"] != old["status"]:
            _record_status_change(conn, "finding", find_id, old["status"], data["status"], data.get("changed_by"))

        conn.commit()
        return jsonify({"id": find_id, "updated": True})
    finally:
        conn.close()


# =====================================================================
# HITL Reviewer Assignment + Hand-off (prop-rev-09)
# =====================================================================


@proposals_api.route("/reviews/<rev_id>/assignments", methods=["GET"])
def list_assignments(rev_id):
    """GET /api/proposals/reviews/<rev_id>/assignments — List all reviewer assignments."""
    conn = _get_db()
    try:
        rev = conn.execute("SELECT id FROM proposal_reviews WHERE id = %s", (rev_id,)).fetchone()
        if not rev:
            return jsonify({"error": "Review not found"}), 404
        rows = conn.execute(
            "SELECT * FROM proposal_reviewer_assignments WHERE review_id = %s ORDER BY created_at DESC",
            (rev_id,),
        ).fetchall()
        return jsonify({"assignments": [dict(r) for r in rows]})
    finally:
        conn.close()


@proposals_api.route("/reviews/<rev_id>/assign", methods=["POST"])
@require_role("reviewer", "admin")
def assign_reviewer(rev_id):
    """POST /api/proposals/reviews/<rev_id>/assign — Assign a HITL reviewer."""
    data = request.get_json(force=True, silent=True) or {}
    reviewer = data.get("reviewer", "").strip()
    assigned_by = data.get("assigned_by", "").strip()
    if not reviewer:
        return jsonify({"error": "reviewer is required"}), 400
    if not assigned_by:
        return jsonify({"error": "assigned_by is required"}), 400

    conn = _get_db()
    try:
        rev = conn.execute("SELECT id, status FROM proposal_reviews WHERE id = %s", (rev_id,)).fetchone()
        if not rev:
            return jsonify({"error": "Review not found"}), 404

        asgn_id = _uuid()
        conn.execute(
            """INSERT INTO proposal_reviewer_assignments
               (id, review_id, reviewer, assigned_by, status, notes, classification)
               VALUES (%s, %s, %s, %s, 'pending', %s, %s)""",
            (asgn_id, rev_id, reviewer, assigned_by, data.get("notes"), data.get("classification", "CUI")),
        )
        _record_status_change(conn, "review", rev_id, rev["status"], rev["status"], assigned_by,
                              reason=f"assignment:assigned:{reviewer}")
        conn.commit()
        return jsonify({"id": asgn_id, "reviewer": reviewer, "status": "pending"}), 201
    finally:
        conn.close()


@proposals_api.route("/assignments/<asgn_id>/accept", methods=["POST"])
@require_role("reviewer", "admin")
def accept_assignment(asgn_id):
    """POST /api/proposals/assignments/<asgn_id>/accept — Reviewer accepts the assignment."""
    data = request.get_json(force=True, silent=True) or {}
    conn = _get_db()
    try:
        asgn = conn.execute(
            "SELECT * FROM proposal_reviewer_assignments WHERE id = %s", (asgn_id,)
        ).fetchone()
        if not asgn:
            return jsonify({"error": "Assignment not found"}), 404
        if asgn["status"] != "pending":
            return jsonify({"error": f"Cannot accept assignment in status '{asgn['status']}'"}), 409

        conn.execute(
            "UPDATE proposal_reviewer_assignments SET status='accepted', accepted_at=datetime('now'), notes=%s WHERE id=%s",
            (data.get("notes", asgn["notes"]), asgn_id),
        )
        _record_status_change(conn, "review", asgn["review_id"], "scheduled", "in_progress",
                              asgn["reviewer"], reason=f"assignment:accepted:{asgn['reviewer']}")
        conn.execute(
            "UPDATE proposal_reviews SET status='in_progress', started_at=datetime('now') WHERE id=%s AND status='scheduled'",
            (asgn["review_id"],),
        )
        conn.commit()
        return jsonify({"id": asgn_id, "status": "accepted"})
    finally:
        conn.close()


@proposals_api.route("/assignments/<asgn_id>/reject", methods=["POST"])
@require_role("reviewer", "admin")
def reject_assignment(asgn_id):
    """POST /api/proposals/assignments/<asgn_id>/reject — Reviewer rejects the assignment."""
    data = request.get_json(force=True, silent=True) or {}
    conn = _get_db()
    try:
        asgn = conn.execute(
            "SELECT * FROM proposal_reviewer_assignments WHERE id = %s", (asgn_id,)
        ).fetchone()
        if not asgn:
            return jsonify({"error": "Assignment not found"}), 404
        if asgn["status"] not in ("pending", "accepted"):
            return jsonify({"error": f"Cannot reject assignment in status '{asgn['status']}'"}), 409

        reason = data.get("rejection_reason", "").strip()
        conn.execute(
            """UPDATE proposal_reviewer_assignments
               SET status='rejected', rejected_at=datetime('now'), rejection_reason=%s
               WHERE id=%s""",
            (reason, asgn_id),
        )
        _record_status_change(conn, "review", asgn["review_id"], asgn["status"], "scheduled",
                              asgn["reviewer"], reason=f"assignment:rejected:{asgn['reviewer']}")
        conn.commit()
        return jsonify({"id": asgn_id, "status": "rejected"})
    finally:
        conn.close()


@proposals_api.route("/assignments/<asgn_id>/reassign", methods=["POST"])
@require_role("admin")
def reassign_reviewer(asgn_id):
    """POST /api/proposals/assignments/<asgn_id>/reassign — Admin reassigns to a new reviewer."""
    data = request.get_json(force=True, silent=True) or {}
    new_reviewer = data.get("reviewer", "").strip()
    assigned_by = data.get("assigned_by", "").strip()
    if not new_reviewer:
        return jsonify({"error": "reviewer is required"}), 400
    if not assigned_by:
        return jsonify({"error": "assigned_by is required"}), 400

    conn = _get_db()
    try:
        asgn = conn.execute(
            "SELECT * FROM proposal_reviewer_assignments WHERE id = %s", (asgn_id,)
        ).fetchone()
        if not asgn:
            return jsonify({"error": "Assignment not found"}), 404

        # Mark old assignment as reassigned
        conn.execute(
            "UPDATE proposal_reviewer_assignments SET status='reassigned' WHERE id=%s", (asgn_id,)
        )
        # Create new assignment
        new_id = _uuid()
        conn.execute(
            """INSERT INTO proposal_reviewer_assignments
               (id, review_id, reviewer, assigned_by, status, notes, classification)
               VALUES (%s, %s, %s, %s, 'pending', %s, %s)""",
            (new_id, asgn["review_id"], new_reviewer, assigned_by,
             data.get("notes"), asgn["classification"]),
        )
        _record_status_change(conn, "review", asgn["review_id"], asgn["status"], "scheduled",
                              assigned_by,
                              reason=f"assignment:reassigned:{asgn['reviewer']}→{new_reviewer}")
        conn.commit()
        return jsonify({"id": new_id, "reviewer": new_reviewer, "status": "pending",
                        "supersedes": asgn_id})
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
               WHERE s.opportunity_id = %s
               ORDER BY v.sort_order, v.volume_number, s.sort_order, s.section_number""",
            (opp_id,),
        ).fetchall()
        items = [dict(s) for s in sections]

        # Attach dependencies
        for item in items:
            deps = conn.execute(
                "SELECT depends_on_section_id FROM proposal_section_dependencies WHERE section_id = %s",
                (item["id"],),
            ).fetchall()
            item["dependencies"] = [d["depends_on_section_id"] for d in deps]

        # Opportunity due date
        opp = conn.execute("SELECT due_date FROM proposal_opportunities WHERE id = %s", (opp_id,)).fetchone()
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
               WHERE s.opportunity_id = %s
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
        opp = conn.execute("SELECT due_date FROM proposal_opportunities WHERE id = %s", (opp_id,)).fetchone()
        if not opp:
            return jsonify({"error": "Opportunity not found"}), 404

        sections = conn.execute("SELECT status FROM proposal_sections WHERE opportunity_id = %s", (opp_id,)).fetchall()
        total = len(sections)
        complete = sum(1 for s in sections if s["status"] in ("final", "submitted"))

        # Section status distribution
        status_dist = {}
        for s in sections:
            st = s["status"]
            status_dist[st] = status_dist.get(st, 0) + 1

        cm = conn.execute(
            "SELECT compliance_status FROM proposal_compliance_matrix WHERE opportunity_id = %s", (opp_id,)
        ).fetchall()
        cm_total = len(cm)
        cm_addressed = sum(1 for c in cm if c["compliance_status"] != "not_addressed")

        findings = conn.execute(
            """SELECT f.severity, f.status FROM proposal_review_findings f
               JOIN proposal_reviews r ON f.review_id = r.id
               WHERE r.opportunity_id = %s""",
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
            days_left = (due - datetime.now(timezone.utc).replace(tzinfo=None)).days
        except (ValueError, TypeError):
            days_left = None

        # By-volume compliance breakdown (prop-cmp-05)
        volumes = conn.execute(
            "SELECT id, volume_number, title FROM proposal_volumes WHERE opportunity_id = %s ORDER BY volume_number",
            (opp_id,),
        ).fetchall()
        by_volume = []
        for vol in volumes:
            vc = conn.execute(
                """SELECT compliance_status FROM proposal_compliance_matrix
                   WHERE opportunity_id = %s AND volume_id = %s""",
                (opp_id, vol["id"]),
            ).fetchall()
            vc_total = len(vc)
            vc_compliant = sum(1 for c in vc if c["compliance_status"] == "compliant")
            vc_partial = sum(1 for c in vc if c["compliance_status"] == "partial")
            vc_not = sum(1 for c in vc if c["compliance_status"] == "not_addressed")
            by_volume.append({
                "volume_id": vol["id"],
                "volume_number": vol["volume_number"],
                "title": vol["title"],
                "total_reqs": vc_total,
                "compliant": vc_compliant,
                "partial": vc_partial,
                "not_addressed": vc_not,
                "coverage_pct": round(((vc_compliant + vc_partial) / vc_total * 100) if vc_total > 0 else 0, 1),
            })

        return jsonify(
            {
                "sections_total": total,
                "sections_complete": complete,
                "section_status_distribution": status_dist,
                "compliance_total": cm_total,
                "compliance_addressed": cm_addressed,
                "compliance_coverage_pct": round((cm_addressed / cm_total * 100) if cm_total > 0 else 0, 1),
                "open_findings": open_findings,
                "finding_severity_distribution": severity_dist,
                "days_to_deadline": days_left,
                "by_volume": by_volume,
            }
        )
    finally:
        conn.close()


@proposals_api.route("/status-history/<entity_type>/<entity_id>", methods=["GET"])
def get_status_history(entity_type, entity_id):
    """GET /api/proposals/status-history/<type>/<id> — Status history for any entity."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM proposal_status_history WHERE entity_type = %s AND entity_id = %s ORDER BY created_at DESC",
            (entity_type, entity_id),
        ).fetchall()
        return jsonify({"history": [dict(r) for r in rows]})
    finally:
        conn.close()


# =====================================================================
# Proposal → Contract Transition (Phase 60 — D-CPMP-9)
# =====================================================================


@proposals_api.route("/opportunities/<opp_id>/create-contract", methods=["POST"])
@require_role("admin", "pm")
def create_contract_from_opportunity(opp_id):
    """POST /api/proposals/opportunities/<opp_id>/create-contract

    Create a CPMP contract from a won proposal opportunity.
    Delegates to portfolio_manager.transition_from_opportunity().
    """
    try:
        from tools.govcon.portfolio_manager import transition_from_opportunity

        data = request.get_json(silent=True) or {}
        result = transition_from_opportunity(opp_id, created_by=data.get("created_by"))
        if result.get("status") == "error":
            return jsonify(result), 400
        return jsonify(result), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# =====================================================================
# Questions CRUD (prop-fix-03)
# =====================================================================


@proposals_api.route("/opportunities/<opp_id>/questions", methods=["GET"])
def list_questions(opp_id):
    """GET /api/proposals/opportunities/<opp_id>/questions"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM proposal_questions WHERE opportunity_id = %s ORDER BY question_number, created_at",
            (opp_id,),
        ).fetchall()
        return jsonify({"questions": [dict(r) for r in rows]})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/questions", methods=["POST"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def create_question(opp_id):
    """POST /api/proposals/opportunities/<opp_id>/questions"""
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("question_text"):
        return jsonify({"error": "question_text is required"}), 400
    q_id = _uuid()
    conn = _get_db()
    try:
        # Auto-assign question_number
        max_row = conn.execute(
            "SELECT MAX(question_number) as mx FROM proposal_questions WHERE opportunity_id = %s",
            (opp_id,),
        ).fetchone()
        q_num = (max_row["mx"] or 0) + 1
        conn.execute(
            """INSERT INTO proposal_questions
               (id, opportunity_id, question_number, question_text, category, priority,
                source, rfp_section_ref, status, ambiguity_trigger, created_by, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                q_id, opp_id, q_num,
                data["question_text"],
                data.get("category", "scope"),
                data.get("priority", "medium"),
                data.get("source", "manual"),
                data.get("rfp_section_ref"),
                data.get("status", "draft"),
                data.get("ambiguity_trigger"),
                data.get("created_by"),
                data.get("classification", "CUI"),
            ),
        )
        conn.commit()
        return jsonify({"id": q_id, "question_number": q_num}), 201
    finally:
        conn.close()


@proposals_api.route("/questions/<q_id>", methods=["GET"])
def get_question(q_id):
    """GET /api/proposals/questions/<q_id>"""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM proposal_questions WHERE id = %s", (q_id,)).fetchone()
        if not row:
            return jsonify({"error": "Question not found"}), 404
        q = dict(row)
        responses = conn.execute(
            "SELECT * FROM proposal_question_responses WHERE question_id = %s ORDER BY created_at",
            (q_id,),
        ).fetchall()
        q["responses"] = [dict(r) for r in responses]
        return jsonify(q)
    finally:
        conn.close()


@proposals_api.route("/questions/<q_id>", methods=["PUT"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def update_question(q_id):
    """PUT /api/proposals/questions/<q_id>"""
    data = request.get_json(force=True, silent=True) or {}
    allowed = {
        "question_text", "category", "priority", "rfp_section_ref", "status",
        "ambiguity_trigger", "approved_by", "approved_at", "submitted_at",
    }
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "No updatable fields provided"}), 400
    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn = _get_db()
    try:
        conn.execute(
            f"UPDATE proposal_questions SET {set_clause} WHERE id = %s",
            list(updates.values()) + [q_id],
        )
        conn.commit()
        return jsonify({"id": q_id})
    finally:
        conn.close()


@proposals_api.route("/questions/<q_id>", methods=["DELETE"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def delete_question(q_id):
    """DELETE /api/proposals/questions/<q_id>"""
    conn = _get_db()
    try:
        conn.execute("DELETE FROM proposal_question_responses WHERE question_id = %s", (q_id,))
        conn.execute("DELETE FROM proposal_questions WHERE id = %s", (q_id,))
        conn.commit()
        return jsonify({"deleted": q_id})
    finally:
        conn.close()


# =====================================================================
# Question Responses CRUD (prop-fix-04)
# =====================================================================


@proposals_api.route("/questions/<q_id>/responses", methods=["POST"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def create_question_response(q_id):
    """POST /api/proposals/questions/<q_id>/responses"""
    conn = _get_db()
    try:
        q = conn.execute("SELECT opportunity_id FROM proposal_questions WHERE id = %s", (q_id,)).fetchone()
        if not q:
            return jsonify({"error": "Question not found"}), 404
        data = request.get_json(force=True, silent=True) or {}
        if not data.get("response_text"):
            return jsonify({"error": "response_text is required"}), 400
        r_id = _uuid()
        conn.execute(
            """INSERT INTO proposal_question_responses
               (id, question_id, opportunity_id, amendment_id, response_text,
                response_date, impacts_requirements, impact_notes, recorded_by, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                r_id, q_id, q["opportunity_id"],
                data.get("amendment_id"),
                data["response_text"],
                data.get("response_date"),
                int(bool(data.get("impacts_requirements", 0))),
                data.get("impact_notes"),
                data.get("recorded_by"),
                data.get("classification", "CUI"),
            ),
        )
        # Mark question as answered
        conn.execute(
            "UPDATE proposal_questions SET status = 'answered', updated_at = %s WHERE id = %s",
            (now_iso(), q_id),
        )
        conn.commit()
        return jsonify({"id": r_id}), 201
    finally:
        conn.close()


@proposals_api.route("/question-responses/<qr_id>", methods=["PUT"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def update_question_response(qr_id):
    """PUT /api/proposals/question-responses/<qr_id>"""
    data = request.get_json(force=True, silent=True) or {}
    allowed = {"response_text", "response_date", "impacts_requirements", "impact_notes", "amendment_id"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if "impacts_requirements" in updates:
        updates["impacts_requirements"] = int(bool(updates["impacts_requirements"]))
    if not updates:
        return jsonify({"error": "No updatable fields provided"}), 400
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn = _get_db()
    try:
        conn.execute(
            f"UPDATE proposal_question_responses SET {set_clause} WHERE id = %s",
            list(updates.values()) + [qr_id],
        )
        conn.commit()
        return jsonify({"id": qr_id})
    finally:
        conn.close()


# =====================================================================
# Amendments CRUD (prop-fix-05)
# =====================================================================


@proposals_api.route("/opportunities/<opp_id>/amendments", methods=["GET"])
def list_amendments(opp_id):
    """GET /api/proposals/opportunities/<opp_id>/amendments"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM proposal_amendments WHERE opportunity_id = %s ORDER BY version_number",
            (opp_id,),
        ).fetchall()
        return jsonify({"amendments": [dict(r) for r in rows]})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/amendments", methods=["POST"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def create_amendment(opp_id):
    """POST /api/proposals/opportunities/<opp_id>/amendments"""
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("title"):
        return jsonify({"error": "title is required"}), 400
    source_type = data.get("source_type", "text")
    if source_type not in ("file", "text"):
        return jsonify({"error": "source_type must be 'file' or 'text'"}), 400
    a_id = _uuid()
    conn = _get_db()
    try:
        max_row = conn.execute(
            "SELECT MAX(version_number) as mx FROM proposal_amendments WHERE opportunity_id = %s",
            (opp_id,),
        ).fetchone()
        ver_num = (max_row["mx"] or 0) + 1
        conn.execute(
            """INSERT INTO proposal_amendments
               (id, opportunity_id, version_number, title, description, amendment_date,
                source_type, file_path, amendment_text, diff_summary, uploaded_by, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                a_id, opp_id, ver_num,
                data["title"],
                data.get("description"),
                data.get("amendment_date"),
                source_type,
                data.get("file_path"),
                data.get("amendment_text"),
                data.get("diff_summary"),
                data.get("uploaded_by"),
                data.get("classification", "CUI"),
            ),
        )
        conn.commit()
        return jsonify({"id": a_id, "version_number": ver_num}), 201
    finally:
        conn.close()


@proposals_api.route("/amendments/<amend_id>", methods=["PUT"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def update_amendment(amend_id):
    """PUT /api/proposals/amendments/<amend_id>"""
    data = request.get_json(force=True, silent=True) or {}
    allowed = {
        "title", "description", "amendment_date", "amendment_text",
        "diff_summary", "diff_data", "changes_detected",
    }
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "No updatable fields provided"}), 400
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn = _get_db()
    try:
        conn.execute(
            f"UPDATE proposal_amendments SET {set_clause} WHERE id = %s",
            list(updates.values()) + [amend_id],
        )
        conn.commit()
        return jsonify({"id": amend_id})
    finally:
        conn.close()


@proposals_api.route("/amendments/<amend_id>", methods=["DELETE"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def delete_amendment(amend_id):
    """DELETE /api/proposals/amendments/<amend_id>"""
    conn = _get_db()
    try:
        conn.execute("DELETE FROM proposal_amendments WHERE id = %s", (amend_id,))
        conn.commit()
        return jsonify({"deleted": amend_id})
    finally:
        conn.close()


# =====================================================================
# Capture Manager APIs (prop-cap-04, prop-cap-05, prop-cap-06)
# =====================================================================

_CAPTURE_PHASES = ("pipeline", "qualify", "capture", "bid_no_bid", "proposal", "submitted", "closed")


@proposals_api.route("/opportunities/<opp_id>/capture", methods=["PATCH"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def update_capture_fields(opp_id):
    """PATCH /api/proposals/opportunities/<opp_id>/capture"""
    data = request.get_json(force=True, silent=True) or {}
    allowed = {"win_probability", "capture_notes", "win_themes", "key_discriminators",
               "ptw_low", "ptw_high", "capture_phase"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if "capture_phase" in updates and updates["capture_phase"] not in _CAPTURE_PHASES:
        return jsonify({"error": f"capture_phase must be one of {_CAPTURE_PHASES}"}), 400
    if "win_probability" in updates:
        try:
            wp = int(updates["win_probability"])
            if not 0 <= wp <= 100:
                raise ValueError
            updates["win_probability"] = wp
        except (ValueError, TypeError):
            return jsonify({"error": "win_probability must be an integer 0-100"}), 400
    if not updates:
        return jsonify({"error": "No updatable fields provided"}), 400
    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn = _get_db()
    try:
        conn.execute(
            f"UPDATE proposal_opportunities SET {set_clause} WHERE id = %s",
            list(updates.values()) + [opp_id],
        )
        conn.commit()
        return jsonify({"id": opp_id})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/competitors", methods=["GET"])
def list_competitors(opp_id):
    """GET /api/proposals/opportunities/<opp_id>/competitors"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM proposal_competitors WHERE opportunity_id = %s ORDER BY company_name",
            (opp_id,),
        ).fetchall()
        return jsonify({"competitors": [dict(r) for r in rows]})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/competitors", methods=["POST"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def create_competitor(opp_id):
    """POST /api/proposals/opportunities/<opp_id>/competitors"""
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("company_name"):
        return jsonify({"error": "company_name is required"}), 400
    c_id = _uuid()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO proposal_competitors
               (id, opportunity_id, company_name, incumbent, strengths, weaknesses,
                estimated_price, win_probability_pct, notes, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (c_id, opp_id, data["company_name"],
             int(bool(data.get("incumbent", 0))),
             data.get("strengths"), data.get("weaknesses"),
             data.get("estimated_price"), data.get("win_probability_pct"),
             data.get("notes"), data.get("classification", "CUI")),
        )
        conn.commit()
        return jsonify({"id": c_id}), 201
    finally:
        conn.close()


@proposals_api.route("/competitors/<cid>", methods=["PUT"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def update_competitor(cid):
    """PUT /api/proposals/competitors/<cid>"""
    data = request.get_json(force=True, silent=True) or {}
    allowed = {"company_name", "incumbent", "strengths", "weaknesses",
               "estimated_price", "win_probability_pct", "notes"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if "incumbent" in updates:
        updates["incumbent"] = int(bool(updates["incumbent"]))
    if not updates:
        return jsonify({"error": "No updatable fields provided"}), 400
    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn = _get_db()
    try:
        conn.execute(
            f"UPDATE proposal_competitors SET {set_clause} WHERE id = %s",
            list(updates.values()) + [cid],
        )
        conn.commit()
        return jsonify({"id": cid})
    finally:
        conn.close()


@proposals_api.route("/competitors/<cid>", methods=["DELETE"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def delete_competitor(cid):
    """DELETE /api/proposals/competitors/<cid>"""
    conn = _get_db()
    try:
        conn.execute("DELETE FROM proposal_competitors WHERE id = %s", (cid,))
        conn.commit()
        return jsonify({"deleted": cid})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/teaming", methods=["GET"])
def list_teaming_partners(opp_id):
    """GET /api/proposals/opportunities/<opp_id>/teaming"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM proposal_teaming_partners WHERE opportunity_id = %s ORDER BY company_name",
            (opp_id,),
        ).fetchall()
        return jsonify({"partners": [dict(r) for r in rows]})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/teaming", methods=["POST"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def create_teaming_partner(opp_id):
    """POST /api/proposals/opportunities/<opp_id>/teaming"""
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("company_name"):
        return jsonify({"error": "company_name is required"}), 400
    role = data.get("role", "sub")
    if role not in ("prime", "sub", "key_sub", "mentor_protege"):
        return jsonify({"error": "role must be prime, sub, key_sub, or mentor_protege"}), 400
    t_id = _uuid()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO proposal_teaming_partners
               (id, opportunity_id, company_name, role, naics, cage_code,
                capabilities, workshare_pct, notes, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (t_id, opp_id, data["company_name"], role,
             data.get("naics"), data.get("cage_code"),
             data.get("capabilities"), data.get("workshare_pct"),
             data.get("notes"), data.get("classification", "CUI")),
        )
        conn.commit()
        return jsonify({"id": t_id}), 201
    finally:
        conn.close()


@proposals_api.route("/teaming/<tid>", methods=["PUT"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def update_teaming_partner(tid):
    """PUT /api/proposals/teaming/<tid>"""
    data = request.get_json(force=True, silent=True) or {}
    allowed = {"company_name", "role", "naics", "cage_code", "capabilities", "workshare_pct", "notes"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "No updatable fields provided"}), 400
    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn = _get_db()
    try:
        conn.execute(
            f"UPDATE proposal_teaming_partners SET {set_clause} WHERE id = %s",
            list(updates.values()) + [tid],
        )
        conn.commit()
        return jsonify({"id": tid})
    finally:
        conn.close()


@proposals_api.route("/teaming/<tid>", methods=["DELETE"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def delete_teaming_partner(tid):
    """DELETE /api/proposals/teaming/<tid>"""
    conn = _get_db()
    try:
        conn.execute("DELETE FROM proposal_teaming_partners WHERE id = %s", (tid,))
        conn.commit()
        return jsonify({"deleted": tid})
    finally:
        conn.close()


# =====================================================================
# Version Snapshots (prop-rev-05)
# =====================================================================


@proposals_api.route("/opportunities/<opp_id>/snapshots", methods=["POST"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def create_snapshot(opp_id):
    """POST /api/proposals/opportunities/<opp_id>/snapshots — serialize current state."""
    import json as _json
    conn = _get_db()
    try:
        opp = conn.execute("SELECT * FROM proposal_opportunities WHERE id = %s", (opp_id,)).fetchone()
        if not opp:
            return jsonify({"error": "Opportunity not found"}), 404
        sections = conn.execute(
            "SELECT * FROM proposal_sections WHERE opportunity_id = %s", (opp_id,)
        ).fetchall()
        compliance = conn.execute(
            "SELECT * FROM proposal_compliance_matrix WHERE opportunity_id = %s", (opp_id,)
        ).fetchall()
        snapshot = {
            "opportunity": dict(opp),
            "sections": [dict(s) for s in sections],
            "compliance": [dict(c) for c in compliance],
        }
        data = request.get_json(silent=True) or {}
        max_ver = conn.execute(
            "SELECT MAX(version_number) as mx FROM proposal_versions WHERE opportunity_id = %s",
            (opp_id,),
        ).fetchone()
        ver_num = (max_ver["mx"] or 0) + 1
        v_id = _uuid()
        conn.execute(
            """INSERT INTO proposal_versions
               (id, opportunity_id, version_number, label, snapshot_json, created_by, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (v_id, opp_id, ver_num, data.get("label"), _json.dumps(snapshot),
             data.get("created_by"), data.get("classification", "CUI")),
        )
        conn.commit()
        return jsonify({"id": v_id, "version_number": ver_num}), 201
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/snapshots", methods=["GET"])
def list_snapshots(opp_id):
    """GET /api/proposals/opportunities/<opp_id>/snapshots"""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, opportunity_id, version_number, label, created_by, classification, created_at "
            "FROM proposal_versions WHERE opportunity_id = %s ORDER BY version_number",
            (opp_id,),
        ).fetchall()
        return jsonify({"snapshots": [dict(r) for r in rows]})
    finally:
        conn.close()


@proposals_api.route("/snapshots/<v_id>/diff", methods=["GET"])
def diff_snapshots(v_id):
    """GET /api/proposals/snapshots/<v_id>/diff?against=<v_id2>"""
    import json as _json
    against = request.args.get("against")
    conn = _get_db()
    try:
        a = conn.execute("SELECT * FROM proposal_versions WHERE id = %s", (v_id,)).fetchone()
        if not a:
            return jsonify({"error": "Snapshot not found"}), 404
        if not against:
            return jsonify({"error": "against query param required"}), 400
        b = conn.execute("SELECT * FROM proposal_versions WHERE id = %s", (against,)).fetchone()
        if not b:
            return jsonify({"error": "Comparison snapshot not found"}), 404
        snap_a = _json.loads(a["snapshot_json"] or "{}")
        snap_b = _json.loads(b["snapshot_json"] or "{}")
        # Simple section-text diff
        secs_a = {s["id"]: s.get("content", "") for s in snap_a.get("sections", [])}
        secs_b = {s["id"]: s.get("content", "") for s in snap_b.get("sections", [])}
        diffs = []
        all_ids = set(secs_a) | set(secs_b)
        for sid in all_ids:
            ca = secs_a.get(sid, "")
            cb = secs_b.get(sid, "")
            if ca != cb:
                diffs.append({"section_id": sid, "from": cb, "to": ca})
        return jsonify({
            "version_a": a["version_number"],
            "version_b": b["version_number"],
            "section_diffs": diffs,
        })
    finally:
        conn.close()


# =====================================================================
# Compliance Editor APIs (prop-cmp-03 through prop-cmp-06)
# =====================================================================


@proposals_api.route("/opportunities/<opp_id>/shred", methods=["GET"])
def list_shred_items(opp_id):
    """GET /api/proposals/opportunities/<opp_id>/shred?status=unassigned"""
    status_filter = request.args.get("status")
    conn = _get_db()
    try:
        if status_filter:
            rows = conn.execute(
                "SELECT * FROM proposal_shred_items WHERE opportunity_id = %s AND status = %s ORDER BY rfp_section",
                (opp_id, status_filter),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM proposal_shred_items WHERE opportunity_id = %s ORDER BY rfp_section, statement_type",
                (opp_id,),
            ).fetchall()
        return jsonify({"shred_items": [dict(r) for r in rows]})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/shred", methods=["POST"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def create_shred_item(opp_id):
    """POST /api/proposals/opportunities/<opp_id>/shred"""
    data = request.get_json(force=True, silent=True) or {}
    if not data.get("statement_text"):
        return jsonify({"error": "statement_text is required"}), 400
    s_id = _uuid()
    conn = _get_db()
    try:
        conn.execute(
            """INSERT INTO proposal_shred_items
               (id, opportunity_id, statement_text, statement_type, rfp_section, rfp_page,
                section_id, writer, status, notes, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (s_id, opp_id, data["statement_text"],
             data.get("statement_type", "shall"),
             data.get("rfp_section"), data.get("rfp_page"),
             data.get("section_id"), data.get("writer"),
             data.get("status", "unassigned"),
             data.get("notes"), data.get("classification", "CUI")),
        )
        conn.commit()
        return jsonify({"id": s_id}), 201
    finally:
        conn.close()


@proposals_api.route("/shred/<item_id>", methods=["PUT"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def update_shred_item(item_id):
    """PUT /api/proposals/shred/<item_id>"""
    data = request.get_json(force=True, silent=True) or {}
    allowed = {"statement_text", "statement_type", "rfp_section", "rfp_page",
               "section_id", "writer", "status", "notes"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "No updatable fields provided"}), 400
    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn = _get_db()
    try:
        conn.execute(
            f"UPDATE proposal_shred_items SET {set_clause} WHERE id = %s",
            list(updates.values()) + [item_id],
        )
        conn.commit()
        return jsonify({"id": item_id})
    finally:
        conn.close()


@proposals_api.route("/shred/<item_id>", methods=["DELETE"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def delete_shred_item(item_id):
    """DELETE /api/proposals/shred/<item_id>"""
    conn = _get_db()
    try:
        conn.execute("DELETE FROM proposal_shred_items WHERE id = %s", (item_id,))
        conn.commit()
        return jsonify({"deleted": item_id})
    finally:
        conn.close()


@proposals_api.route("/amendments/<amend_id>/impact", methods=["PUT"])
@require_role("admin", "bd", "capture_mgr", "pm", "reviewer")
def update_amendment_impact(amend_id):
    """PUT /api/proposals/amendments/<amend_id>/impact"""
    import json as _json
    data = request.get_json(force=True, silent=True) or {}
    req_ids = data.get("requirement_ids", [])
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE proposal_amendments SET changed_requirement_ids = %s WHERE id = %s",
            (_json.dumps(req_ids), amend_id),
        )
        conn.commit()
        return jsonify({"id": amend_id, "requirement_ids": req_ids})
    finally:
        conn.close()


@proposals_api.route("/amendments/<amend_id>/impact", methods=["GET"])
def get_amendment_impact(amend_id):
    """GET /api/proposals/amendments/<amend_id>/impact — returns linked compliance items."""
    import json as _json
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT changed_requirement_ids, opportunity_id FROM proposal_amendments WHERE id = %s",
            (amend_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "Amendment not found"}), 404
        req_ids = _json.loads(row["changed_requirement_ids"] or "[]")
        items = []
        if req_ids:
            placeholders = ",".join("?" * len(req_ids))
            items = conn.execute(
                f"SELECT * FROM proposal_compliance_matrix WHERE id IN ({placeholders})",
                req_ids,
            ).fetchall()
        return jsonify({"requirement_ids": req_ids, "compliance_items": [dict(i) for i in items]})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/compliance/orphaned", methods=["GET"])
def get_orphaned_requirements(opp_id):
    """GET /api/proposals/opportunities/<opp_id>/compliance/orphaned"""
    conn = _get_db()
    try:
        rows = conn.execute(
            """SELECT * FROM proposal_compliance_matrix
               WHERE opportunity_id = %s AND proposal_section_id IS NULL
                 AND compliance_status != 'not_applicable'
               ORDER BY sort_order, section_ref""",
            (opp_id,),
        ).fetchall()
        return jsonify({"orphaned": [dict(r) for r in rows], "count": len(rows)})
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Black-hat / PTW Workspace (prop-cap-13)
# ---------------------------------------------------------------------------

_SCG_AGG_THRESHOLD = 3  # prop-sec-03: warn when aggregating ≥N competitors


def _ensure_blackhat_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS proposal_blackhat_assessments (
            id TEXT PRIMARY KEY,
            opportunity_id TEXT NOT NULL,
            competitor_name TEXT NOT NULL,
            approach_hypothesis TEXT,
            price_estimate_low REAL,
            price_estimate_high REAL,
            strengths TEXT,
            weaknesses TEXT,
            win_strategy TEXT,
            differentiators TEXT,
            risk_factors TEXT,
            ptw_posture TEXT DEFAULT 'competitive',
            leaderboard_rank INTEGER,
            award_count INTEGER,
            total_award_value REAL,
            naics_diversity INTEGER,
            agency_diversity INTEGER,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT NOT NULL,
            updated_at TEXT,
            created_by TEXT
        )
    """)
    conn.commit()


def _mask_ptw_sensitive(row):
    """Mask price fields per prop-sec-01 if MAC clearance insufficient."""
    ctx = _mac_ctx()
    if ctx is None:
        return row
    from tools.security.classification_enforcer import can_read
    if not can_read("SECRET", ctx):
        row = dict(row)
        for field in ("price_estimate_low", "price_estimate_high"):
            if field in row and row[field] is not None:
                row[field] = None
                row[f"{field}_masked"] = True
    return row


def _mask_ptw_analysis(result: dict) -> dict:
    """Strip recomputed PTW figures for roles denied them at rest (prop-sec-01)."""
    from tools.security.column_security import current_role, mask_ptw_payload
    return mask_ptw_payload(result, current_role())


@proposals_api.route("/opportunities/<opp_id>/ptw/analysis", methods=["GET"])
def ptw_analysis_endpoint(opp_id):
    """GET /api/proposals/opportunities/<opp_id>/ptw/analysis — rate_benchmarker PTW + SCG gate."""
    conn = _get_db()
    try:
        mac_err = _mac_check_parent_opp(opp_id, conn)
        if mac_err:
            return mac_err
    finally:
        conn.close()
    try:
        from tools.govcon.rate_benchmarker import ptw_analysis
        result = ptw_analysis(opp_id)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    # prop-sec-03 SCG aggregation warning
    n_competitors = result.get("competitor_count", 0)
    if n_competitors >= _SCG_AGG_THRESHOLD:
        result["scg_warning"] = (
            f"Aggregating pricing across {n_competitors} competitors may trigger SCG rule "
            "SCG-AGG-003 (pattern-of-life disclosure). Review disclosure permissions before sharing."
        )
    return jsonify(_mask_ptw_analysis(result))


@proposals_api.route("/opportunities/<opp_id>/ptw/leaderboard", methods=["GET"])
def ptw_leaderboard(opp_id):
    """GET /api/proposals/opportunities/<opp_id>/ptw/leaderboard — competitor_profiler leaderboard."""
    conn = _get_db()
    try:
        mac_err = _mac_check_parent_opp(opp_id, conn)
        if mac_err:
            return mac_err
        # Pull opportunity NAICS for default leaderboard filter
        opp_row = conn.execute(
            "SELECT naics_code FROM proposal_opportunities WHERE id = %s", (opp_id,)
        ).fetchone()
    finally:
        conn.close()
    naics = request.args.get("naics") or (opp_row["naics_code"] if opp_row else None)
    agency = request.args.get("agency")
    limit = int(request.args.get("limit", 20))
    try:
        from tools.govcon.competitor_profiler import get_leaderboard
        result = get_leaderboard(naics=naics, agency=agency, limit=limit)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    if result.get("leaderboard", []) and len(result["leaderboard"]) >= _SCG_AGG_THRESHOLD:
        result["scg_warning"] = (
            "Leaderboard aggregates award patterns across multiple vendors. "
            "Comply with SCG-AGG-003 before external disclosure."
        )
    return jsonify(result)


@proposals_api.route("/opportunities/<opp_id>/ptw/vendor-profile", methods=["POST"])
@require_role("admin", "capture_mgr", "pm")
def ptw_vendor_profile(opp_id):
    """POST /api/proposals/opportunities/<opp_id>/ptw/vendor-profile — profile a competitor."""
    conn = _get_db()
    try:
        mac_err = _mac_check_parent_opp(opp_id, conn)
        if mac_err:
            return mac_err
    finally:
        conn.close()
    data = request.get_json(force=True, silent=True) or {}
    vendor = data.get("vendor_name", "").strip()
    if not vendor:
        return jsonify({"error": "vendor_name is required"}), 400
    try:
        from tools.govcon.competitor_profiler import profile_vendor
        result = profile_vendor(vendor)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify(result)


@proposals_api.route("/opportunities/<opp_id>/ptw/bid-score", methods=["POST"])
@require_role("admin", "capture_mgr", "pm")
def ptw_bid_score(opp_id):
    """POST /api/proposals/opportunities/<opp_id>/ptw/bid-score — Bayesian bid scorer."""
    conn = _get_db()
    try:
        mac_err = _mac_check_parent_opp(opp_id, conn)
        if mac_err:
            return mac_err
    finally:
        conn.close()
    data = request.get_json(force=True, silent=True) or {}
    dimensions = data.get("dimensions", {})
    try:
        from tools.govcon.bayesian_bid_scorer import score_opportunity, optimal_assessment_order
        score_result = score_opportunity(opp_id, dimensions)
        order_result = optimal_assessment_order(opp_id)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"score": score_result, "optimal_order": order_result})


@proposals_api.route("/opportunities/<opp_id>/ptw/blackhat", methods=["GET"])
def list_blackhat_assessments(opp_id):
    """GET /api/proposals/opportunities/<opp_id>/ptw/blackhat — list black-hat competitor models."""
    conn = _get_db()
    try:
        mac_err = _mac_check_parent_opp(opp_id, conn)
        if mac_err:
            return mac_err
        _ensure_blackhat_table(conn)
        rows = conn.execute(
            "SELECT * FROM proposal_blackhat_assessments WHERE opportunity_id = %s ORDER BY created_at DESC",
            (opp_id,),
        ).fetchall()
        assessments = [_mask_ptw_sensitive(dict(r)) for r in rows]
        assessments = _mac_filter(assessments)
        return jsonify({"assessments": assessments, "count": len(assessments)})
    finally:
        conn.close()


@proposals_api.route("/opportunities/<opp_id>/ptw/blackhat", methods=["POST"])
@require_role("admin", "capture_mgr", "pm")
def create_blackhat_assessment(opp_id):
    """POST /api/proposals/opportunities/<opp_id>/ptw/blackhat — create competitor black-hat model."""
    conn = _get_db()
    try:
        mac_err = _mac_check_parent_opp(opp_id, conn)
        if mac_err:
            return mac_err
        data = request.get_json(force=True, silent=True) or {}
        if not data.get("competitor_name"):
            return jsonify({"error": "competitor_name is required"}), 400
        classification = data.get("classification", "CUI")
        deny = _mac_deny_write(classification)
        if deny:
            return deny
        _ensure_blackhat_table(conn)
        bh_id = _uuid()
        ptw_posture = data.get("ptw_posture", "competitive")
        if ptw_posture not in ("lpta", "aggressive", "competitive", "premium", "unknown"):
            ptw_posture = "competitive"
        conn.execute(
            """INSERT INTO proposal_blackhat_assessments
               (id, opportunity_id, competitor_name, approach_hypothesis,
                price_estimate_low, price_estimate_high, strengths, weaknesses,
                win_strategy, differentiators, risk_factors, ptw_posture,
                leaderboard_rank, award_count, total_award_value,
                naics_diversity, agency_diversity, classification, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                bh_id, opp_id, data["competitor_name"],
                data.get("approach_hypothesis"),
                data.get("price_estimate_low"), data.get("price_estimate_high"),
                data.get("strengths"), data.get("weaknesses"),
                data.get("win_strategy"), data.get("differentiators"),
                data.get("risk_factors"), ptw_posture,
                data.get("leaderboard_rank"), data.get("award_count"),
                data.get("total_award_value"),
                data.get("naics_diversity"), data.get("agency_diversity"),
                classification, now_iso(),
            ),
        )
        conn.commit()
        return jsonify({"id": bh_id}), 201
    finally:
        conn.close()


@proposals_api.route("/blackhat/<bh_id>", methods=["PUT"])
@require_role("admin", "capture_mgr", "pm")
def update_blackhat_assessment(bh_id):
    """PUT /api/proposals/blackhat/<bh_id> — update a black-hat competitor model."""
    data = request.get_json(force=True, silent=True) or {}
    allowed = {
        "approach_hypothesis", "price_estimate_low", "price_estimate_high",
        "strengths", "weaknesses", "win_strategy", "differentiators",
        "risk_factors", "ptw_posture",
    }
    updates = {k: v for k, v in data.items() if k in allowed}
    if "ptw_posture" in updates and updates["ptw_posture"] not in (
        "lpta", "aggressive", "competitive", "premium", "unknown"
    ):
        updates["ptw_posture"] = "competitive"
    if not updates:
        return jsonify({"error": "No updatable fields provided"}), 400
    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn = _get_db()
    try:
        _ensure_blackhat_table(conn)
        conn.execute(
            f"UPDATE proposal_blackhat_assessments SET {set_clause} WHERE id = %s",
            list(updates.values()) + [bh_id],
        )
        conn.commit()
        return jsonify({"id": bh_id})
    finally:
        conn.close()


@proposals_api.route("/blackhat/<bh_id>", methods=["DELETE"])
@require_role("admin", "capture_mgr", "pm")
def delete_blackhat_assessment(bh_id):
    """DELETE /api/proposals/blackhat/<bh_id> — remove a black-hat competitor model."""
    conn = _get_db()
    try:
        _ensure_blackhat_table(conn)
        conn.execute("DELETE FROM proposal_blackhat_assessments WHERE id = %s", (bh_id,))
        conn.commit()
        return jsonify({"deleted": bh_id})
    finally:
        conn.close()


@proposals_api.route("/iqe-query", methods=["POST"])
def proposals_iqe_query():
    """IQE NL-to-SQL for the Proposals canvas (prop-iqe-01). Mirrors
    tools/dashboard/api/govcon.py::govcon_iqe_query."""
    from tools.iqe.nl_to_iqe import nl_to_iqe
    from tools.iqe.parser import IQESyntaxError, parse
    from tools.iqe.executor import execute_query
    import tools.iqe.adapters.proposals  # noqa: F401

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    collections = [
        "proposals.opportunities", "proposals.sections",
        "proposals.compliance", "proposals.reviews",
    ]
    translation = nl_to_iqe(question, collections)
    iqe_str = translation.get("iqe", "")
    explanation = translation.get("explanation", "")

    if not data.get("execute", True):
        return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation}), 200

    try:
        ast = parse(iqe_str)
        rows = execute_query(ast, None)
        return jsonify({"ok": True, "iqe": iqe_str, "explanation": explanation,
                        "results": rows, "row_count": len(rows)}), 200
    except IQESyntaxError as exc:
        return jsonify({"error": f"IQE syntax error: {exc}", "iqe": iqe_str}), 400
    except Exception as exc:
        return jsonify({"error": str(exc), "iqe": iqe_str}), 500


# ---------------------------------------------------------------------------
# Inline Annotations
# ---------------------------------------------------------------------------

_ANNOTATION_CATEGORIES = {
    "question", "improvement", "compliance",
    "strength", "weakness", "risk", "editorial",
}


def _ensure_annotations_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS proposal_section_annotations (
            id TEXT PRIMARY KEY,
            section_id TEXT NOT NULL,
            draft_id TEXT,
            selected_text TEXT NOT NULL,
            category TEXT NOT NULL CHECK(category IN (
                'question','improvement','compliance',
                'strength','weakness','risk','editorial'
            )),
            comment TEXT NOT NULL,
            author TEXT DEFAULT 'reviewer',
            status TEXT DEFAULT 'open' CHECK(status IN ('open','resolved')),
            resolution_note TEXT,
            resolved_by TEXT,
            resolved_at TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    """)
    conn.commit()


def _annotation_row(row):
    d = dict(row)
    return d


@proposals_api.route("/sections/<sec_id>/annotations", methods=["GET"])
def list_annotations(sec_id):
    """GET /api/proposals/sections/<sec_id>/annotations — list annotations, optional ?status=open|resolved."""
    status_filter = request.args.get("status")
    conn = _get_db()
    try:
        _ensure_annotations_table(conn)
        if status_filter in ("open", "resolved"):
            rows = conn.execute(
                "SELECT * FROM proposal_section_annotations WHERE section_id = %s AND status = %s ORDER BY created_at ASC",
                (sec_id, status_filter),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM proposal_section_annotations WHERE section_id = %s ORDER BY created_at ASC",
                (sec_id,),
            ).fetchall()
        return jsonify({"annotations": [_annotation_row(r) for r in rows]})
    finally:
        conn.close()


@proposals_api.route("/sections/<sec_id>/annotations", methods=["POST"])
@require_role("admin", "pm", "reviewer")
def create_annotation(sec_id):
    """POST /api/proposals/sections/<sec_id>/annotations — create an inline annotation."""
    body = request.get_json(silent=True) or {}
    selected_text = (body.get("selected_text") or "").strip()
    category = (body.get("category") or "").strip().lower()
    comment = (body.get("comment") or "").strip()

    if not selected_text:
        return jsonify({"error": "selected_text is required"}), 400
    if category not in _ANNOTATION_CATEGORIES:
        return jsonify({"error": f"category must be one of: {', '.join(sorted(_ANNOTATION_CATEGORIES))}"}), 400
    if not comment:
        return jsonify({"error": "comment is required"}), 400

    ann_id = str(uuid.uuid4())
    ts = now_iso()
    conn = _get_db()
    try:
        _ensure_annotations_table(conn)
        conn.execute(
            """INSERT INTO proposal_section_annotations
               (id, section_id, draft_id, selected_text, category, comment, author, status, classification, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                ann_id,
                sec_id,
                body.get("draft_id"),
                selected_text,
                category,
                comment,
                body.get("author", "reviewer"),
                "open",
                "CUI",
                ts,
            ),
        )
        try:
            conn.execute(
                "INSERT INTO audit_trail (id,created_at,event_type,actor,action,details,session_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (str(uuid.uuid4()), ts, "proposals.annotation.create", body.get("author", "reviewer"),
                 "create_annotation", f"section={sec_id} category={category}", "proposals"),
            )
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("create_annotation: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM proposal_section_annotations WHERE id = %s", (ann_id,)
        ).fetchone()
        return jsonify(_annotation_row(row)), 201
    finally:
        conn.close()


@proposals_api.route("/annotations/<ann_id>", methods=["PUT"])
@require_role("admin", "pm", "reviewer")
def update_annotation(ann_id):
    """PUT /api/proposals/annotations/<ann_id> — update comment, category, status, or resolution_note."""
    body = request.get_json(silent=True) or {}
    allowed = {"comment", "category", "status", "resolution_note", "author"}
    updates = {k: v for k, v in body.items() if k in allowed and v is not None}

    if "category" in updates and updates["category"] not in _ANNOTATION_CATEGORIES:
        return jsonify({"error": f"category must be one of: {', '.join(sorted(_ANNOTATION_CATEGORIES))}"}), 400
    if "status" in updates and updates["status"] not in ("open", "resolved"):
        return jsonify({"error": "status must be open or resolved"}), 400

    if not updates:
        return jsonify({"error": "No updatable fields provided"}), 400

    updates["updated_at"] = now_iso()
    ts = now_iso()
    updates["updated_at"] = ts
    if updates.get("status") == "resolved":
        updates["resolved_by"] = body.get("resolved_by") or body.get("author", "reviewer")
        updates["resolved_at"] = ts
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn = _get_db()
    try:
        _ensure_annotations_table(conn)
        conn.execute(
            f"UPDATE proposal_section_annotations SET {set_clause} WHERE id = %s",  # noqa: S608
            list(updates.values()) + [ann_id],
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM proposal_section_annotations WHERE id = %s", (ann_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "Annotation not found"}), 404
        return jsonify(_annotation_row(row))
    finally:
        conn.close()


@proposals_api.route("/annotations/<ann_id>", methods=["DELETE"])
@require_role("admin", "pm", "reviewer")
def delete_annotation(ann_id):
    """DELETE /api/proposals/annotations/<ann_id> — remove an annotation."""
    conn = _get_db()
    try:
        _ensure_annotations_table(conn)
        conn.execute("DELETE FROM proposal_section_annotations WHERE id = %s", (ann_id,))
        conn.commit()
        return jsonify({"deleted": ann_id})
    finally:
        conn.close()


