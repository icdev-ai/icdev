#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: GovCon Intelligence — SAM.gov, requirement extraction,
capability mapping, AI drafting, compliance auto-population.

Bridges tools/govcon/ into the Proposal Writing Lifecycle Tracker
(tools/dashboard/api/proposals.py).  Every endpoint wraps a GovCon tool
function and connects the output to the existing proposal pipeline.

Integration points:
    sam_scanner.py        → proposal_opportunities (auto-create from SAM.gov)
    requirement_extractor → rfp_shall_statements    (extract "shall" from opp)
    capability_mapper     → icdev_capability_map     (score coverage per req)
    compliance_populator  → proposal_compliance_matrix (auto-populate L/M/N)
    response_drafter      → proposal_section_drafts  (AI draft → human review)
    gap_analyzer          → innovation_signals       (cross-register gaps)
    knowledge_base        → proposal_knowledge_base  (reusable content blocks)
    competitor_profiler   → govcon_awards            (vendor intelligence)
"""

import os
import sys
import uuid
from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = get_logger(__name__)

from tools.common.helpers import now_isoformat
from tools.dashboard.auth import require_role
from tools.dashboard.config import DEFAULT_CLASSIFICATION
from tools.security.abac_engine import abac_protect

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

govcon_api = Blueprint("govcon_api", __name__, url_prefix="/api/govcon")

# RBAC roles for write/sensitive GovCon operations (prop-fix-09, roles per
# prop-fix-08). These endpoints mutate data or trigger AI/SAM.gov scans, so they
# are restricted to the active capture roles. require_role() denies with 403 +
# log_auth_event("permission_denied"); 401 if unauthenticated.
GOVCON_WRITE_ROLES = ("admin", "bd", "capture_mgr")


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
    conn.execute("PRAGMA journal_mode=WAL")
    return _PGCompatConn(conn)


def _uuid():
    return str(uuid.uuid4())


def _audit(conn, action, details="", actor="govcon_api"):
    """Append-only audit trail (NIST AU-2).

    Uses a SAVEPOINT so a failure here never aborts the caller's transaction.
    id is omitted — both SQLite AUTOINCREMENT and PostgreSQL SERIAL auto-assign it.
    """
    try:
        conn.execute("SAVEPOINT govcon_audit")
        try:
            conn.execute(
                "INSERT INTO audit_trail (created_at, event_type, actor, action, details, session_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (now_isoformat(), "govcon.api", actor, action, details, "govcon"),
            )
            conn.execute("RELEASE SAVEPOINT govcon_audit")
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT govcon_audit")
    except Exception:
        pass


# =====================================================================
# SAM.gov Sync → Proposal Opportunities
# =====================================================================


@govcon_api.route("/sam/scan", methods=["POST"])
@require_role(*GOVCON_WRITE_ROLES)
def scan_sam_gov():
    """POST /api/govcon/sam/scan — Trigger SAM.gov scanner.

    Scans SAM.gov for opportunities matching configured NAICS codes.
    Auto-creates proposal_opportunities for each new find.
    """
    try:
        from tools.govcon.sam_scanner import scan_sam_gov as _scan_sam

        data = request.get_json(silent=True) or {}
        result = _scan_sam(
            naics_filter=data.get("naics"),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@govcon_api.route("/sam/opportunities", methods=["GET"])
def list_sam_opportunities():
    """GET /api/govcon/sam/opportunities — List cached SAM.gov opportunities."""
    conn = _get_db()
    try:
        naics = request.args.get("naics")
        agency = request.args.get("agency")
        active_only = request.args.get("active", "true").lower() == "true"

        query = "SELECT * FROM sam_gov_opportunities WHERE 1=1"
        params = []
        if active_only:
            query += " AND active = 'true'"
        if naics:
            query += " AND naics_code = ?"
            params.append(naics)
        if agency:
            query += " AND agency LIKE ?"
            params.append(f"%{agency}%")
        query += " ORDER BY posted_date DESC LIMIT 100"

        rows = conn.execute(query, params).fetchall()
        opportunities = [dict(r) for r in rows]

        # Enrich with linkage status
        for opp in opportunities:
            linked = conn.execute(
                "SELECT id, status FROM proposal_opportunities WHERE solicitation_number = %s",
                (opp.get("solicitation_number", ""),),
            ).fetchone()
            opp["linked_proposal_id"] = linked["id"] if linked else None
            opp["linked_proposal_status"] = linked["status"] if linked else None

        return jsonify({"opportunities": opportunities, "total": len(opportunities)})
    finally:
        conn.close()


@govcon_api.route("/sam/import/<sam_opp_id>", methods=["POST"])
@require_role(*GOVCON_WRITE_ROLES)
def import_sam_to_proposal(sam_opp_id):
    """POST /api/govcon/sam/import/<id> — Create proposal_opportunity from SAM.gov record.

    Links sam_gov_opportunities → proposal_opportunities for full lifecycle tracking.
    """
    conn = _get_db()
    try:
        sam = conn.execute("SELECT * FROM sam_gov_opportunities WHERE id = %s", (sam_opp_id,)).fetchone()
        if not sam:
            return jsonify({"error": "SAM.gov opportunity not found"}), 404
        sam = dict(sam)

        # Check if already linked
        existing = conn.execute(
            "SELECT id FROM proposal_opportunities WHERE solicitation_number = %s",
            (sam.get("solicitation_number", ""),),
        ).fetchone()
        if existing:
            return jsonify({"error": "Already imported", "proposal_id": existing["id"]}), 409

        # Create proposal_opportunity
        prop_id = _uuid()
        # Normalize set_aside_type — empty string is not a valid CHECK value
        raw_set_aside = sam.get("set_aside_type") or None
        valid_set_asides = {"full_open", "small_business", "8a", "hubzone", "sdvosb", "wosb", "edwosb", "sole_source", "other"}
        set_aside = raw_set_aside if raw_set_aside in valid_set_asides else None
        conn.execute(
            """INSERT INTO proposal_opportunities
               (id, solicitation_number, title, agency, sub_agency, due_date,
                naics_code, set_aside_type, rfp_url, proposal_type, status,
                classification, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'other', 'intake', %s, %s, %s)""",
            (
                prop_id,
                sam.get("solicitation_number", ""),
                sam.get("title", "Untitled"),
                sam.get("agency", ""),
                sam.get("agency_hierarchy", ""),
                sam.get("response_deadline", ""),
                sam.get("naics_code", ""),
                set_aside,
                sam.get("solicitation_number", ""),  # use as rfp_url placeholder
                DEFAULT_CLASSIFICATION,
                now_isoformat(),
                now_isoformat(),
            ),
        )

        # Link SAM record to proposal
        conn.execute(
            "UPDATE sam_gov_opportunities SET proposal_opportunity_id = %s WHERE id = %s",
            (prop_id, sam_opp_id),
        )

        # Record status change
        conn.execute(
            "INSERT INTO proposal_status_history (entity_type, entity_id, old_status, new_status, changed_by, reason) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ("opportunity", prop_id, None, "intake", "govcon_api", f"Imported from SAM.gov: {sam_opp_id}"),
        )

        _audit(conn, "import_sam_opportunity", f"SAM {sam_opp_id} → Proposal {prop_id}")
        conn.commit()

        return jsonify({"status": "ok", "proposal_id": prop_id, "sam_opp_id": sam_opp_id})
    finally:
        conn.close()


# =====================================================================
# Requirement Extraction → rfp_shall_statements
# =====================================================================


@govcon_api.route("/opportunities/<opp_id>/extract-requirements", methods=["POST"])
@require_role(*GOVCON_WRITE_ROLES)
def extract_requirements(opp_id):
    """POST /api/govcon/opportunities/<id>/extract-requirements

    Extract "shall/must/will" statements from the opportunity's RFP text.
    Stores results in rfp_shall_statements and clusters into rfp_requirement_patterns.
    """
    try:
        from tools.govcon.requirement_extractor import extract_and_store

        result = extract_and_store(opp_id=opp_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@govcon_api.route("/opportunities/<opp_id>/requirements", methods=["GET"])
def list_requirements(opp_id):
    """GET /api/govcon/opportunities/<id>/requirements — List extracted shall statements."""
    conn = _get_db()
    try:
        domain = request.args.get("domain")
        query = """SELECT * FROM rfp_shall_statements
                   WHERE (sam_opportunity_id = ? OR proposal_opportunity_id = ?)"""
        params = [opp_id, opp_id]
        if domain:
            query += " AND domain_category = ?"
            params.append(domain)
        query += " ORDER BY extracted_at DESC"

        rows = conn.execute(query, params).fetchall()
        statements = [dict(r) for r in rows]

        # Domain summary
        domains = {}
        for s in statements:
            d = s.get("domain_category", "other")
            domains[d] = domains.get(d, 0) + 1

        return jsonify(
            {
                "statements": statements,
                "total": len(statements),
                "by_domain": domains,
            }
        )
    finally:
        conn.close()


@govcon_api.route("/requirement-patterns", methods=["GET"])
def list_patterns():
    """GET /api/govcon/requirement-patterns — List clustered requirement patterns."""
    conn = _get_db()
    try:
        domain = request.args.get("domain")
        min_freq = int(request.args.get("min_frequency", 1))

        query = "SELECT * FROM rfp_requirement_patterns WHERE frequency >= ?"
        params = [min_freq]
        if domain:
            query += " AND domain_category = ?"
            params.append(domain)
        query += " ORDER BY frequency DESC LIMIT 100"

        rows = conn.execute(query, params).fetchall()
        return jsonify({"patterns": [dict(r) for r in rows], "total": len(rows)})
    finally:
        conn.close()


# =====================================================================
# Capability Mapping → icdev_capability_map
# =====================================================================


@govcon_api.route("/opportunities/<opp_id>/map-capabilities", methods=["POST"])
@require_role(*GOVCON_WRITE_ROLES)
def map_capabilities(opp_id):
    """POST /api/govcon/opportunities/<id>/map-capabilities

    Map ICDEV™ capabilities against extracted requirements for this opportunity.
    Computes coverage scores and L/M/N grades.
    """
    try:
        from tools.govcon.capability_mapper import map_all_patterns

        result = map_all_patterns()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@govcon_api.route("/opportunities/<opp_id>/coverage", methods=["GET"])
def get_coverage(opp_id):
    """GET /api/govcon/opportunities/<id>/coverage — Capability coverage for opportunity."""
    try:
        from tools.govcon.capability_mapper import get_compliance_matrix

        result = get_compliance_matrix(opp_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =====================================================================
# Compliance Auto-Population → proposal_compliance_matrix
# =====================================================================


@govcon_api.route("/opportunities/<opp_id>/auto-compliance", methods=["POST"])
@require_role(*GOVCON_WRITE_ROLES)
def auto_populate_compliance(opp_id):
    """POST /api/govcon/opportunities/<id>/auto-compliance

    Auto-populate L/M/N compliance matrix from capability coverage scores.
    Writes to proposal_compliance_matrix + returns bid/no-bid recommendation.
    """
    try:
        from tools.govcon.compliance_populator import populate_compliance_matrix

        result = populate_compliance_matrix(opp_id)

        # Also batch-create compliance items in proposal_compliance_matrix
        # if they don't already exist
        if result.get("status") == "ok" and result.get("matrix"):
            conn = _get_db()
            try:
                created = 0
                for item in result["matrix"]:
                    # Check if compliance item already exists
                    existing = conn.execute(
                        "SELECT id FROM proposal_compliance_matrix WHERE opportunity_id = %s AND requirement_text = %s",
                        (opp_id, item["statement"][:200]),
                    ).fetchone()
                    if not existing:
                        grade = item.get("grade", "N")
                        status_map = {"L": "compliant", "M": "partial", "N": "non_compliant"}
                        conn.execute(
                            """INSERT INTO proposal_compliance_matrix
                               (id, opportunity_id, section_ref, requirement_text,
                                requirement_type, compliance_status, response_summary,
                                classification, created_at, updated_at)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                            (
                                _uuid(),
                                opp_id,
                                item.get("domain", ""),
                                item["statement"][:500],
                                grade,
                                status_map.get(grade, "not_addressed"),
                                f"Auto: {item.get('best_capability', 'none')} ({item.get('coverage_score', 0):.0%})",
                                DEFAULT_CLASSIFICATION,
                                now_isoformat(),
                                now_isoformat(),
                            ),
                        )
                        created += 1
                _audit(conn, "auto_compliance", f"Opportunity {opp_id}: created {created} compliance items")
                conn.commit()
                result["compliance_items_created"] = created
            finally:
                conn.close()

        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@govcon_api.route("/opportunities/<opp_id>/bid-recommendation", methods=["GET"])
@require_role(*GOVCON_WRITE_ROLES)
def bid_recommendation(opp_id):
    """GET /api/govcon/opportunities/<id>/bid-recommendation — Get bid/no-bid recommendation."""
    try:
        from tools.govcon.compliance_populator import get_summary

        result = get_summary(opp_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@govcon_api.route("/opportunities/<opp_id>/clause-risk", methods=["POST"])
@require_role(*GOVCON_WRITE_ROLES)
def clause_risk(opp_id):
    """POST /api/govcon/opportunities/<id>/clause-risk — deterministic clause risk scan.

    Body (all optional): {"text": "...", "assist": false, "persist": true}.
    Falls back to the opportunity's stored ``description`` when no text is given.
    Deterministic rulebook produces the score; the optional LLM narrative
    (assist=true) EXPLAINS the findings and never changes the score.
    """
    try:
        from tools.govcon.clause_risk_engine import assess, persist

        data = request.get_json(silent=True) or {}
        text = (data.get("text") or "").strip()
        if not text:
            conn = _get_db()
            try:
                row = conn.execute(
                    "SELECT description FROM proposal_opportunities WHERE id = %s",
                    (opp_id,),
                ).fetchone()
            finally:
                conn.close()
            if row:
                text = (row[0] if not isinstance(row, dict) else row.get("description")) or ""
        if not text.strip():
            return jsonify({"error": "no solicitation text available for this opportunity"}), 400

        report = assess(text, opportunity_id=opp_id, use_llm=bool(data.get("assist")))
        result = report.to_dict()
        if data.get("persist", True):
            result["assessment_id"] = persist(report)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =====================================================================
# AI Drafting → proposal_section_drafts
# =====================================================================


def _draft_resource_attrs(request):
    """ABAC resource dict for a draft approve/reject decision (prop-sec-01).

    Separation of duties, not ownership: privileged/reviewer roles decide;
    a section_writer never self-approves/rejects (see proposal_draft_*
    policies in args/security_config.yaml), so no per-draft lookup is needed.
    """
    return {"type": "proposal_draft"}


@govcon_api.route("/opportunities/<opp_id>/auto-draft", methods=["POST"])
@require_role(*GOVCON_WRITE_ROLES)
def auto_draft(opp_id):
    """POST /api/govcon/opportunities/<id>/auto-draft

    AI-draft responses for all unaddressed requirements using two-tier LLM
    (qwen3 worker → Claude reviewer).  Falls back to template-based drafting.
    Stores drafts in proposal_section_drafts (status='draft').
    """
    try:
        from tools.govcon.response_drafter import draft_all_for_opportunity

        data = request.get_json(silent=True) or {}
        result = draft_all_for_opportunity(
            opp_id,
            method=data.get("method", "auto"),  # auto, template, llm
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@govcon_api.route("/opportunities/<opp_id>/drafts", methods=["GET"])
def list_drafts(opp_id):
    """GET /api/govcon/opportunities/<id>/drafts — List AI-generated drafts."""
    conn = _get_db()
    try:
        status = request.args.get("status")  # draft, reviewed, approved, rejected
        query = "SELECT * FROM proposal_section_drafts WHERE opportunity_id = ?"
        params = [opp_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"

        rows = conn.execute(query, params).fetchall()
        drafts = [dict(r) for r in rows]

        # Enrich with shall statement text and composite quality_score
        import json as _json
        from tools.govcon.response_drafter import _compute_quality_score

        for d in drafts:
            if d.get("shall_statement_id"):
                shall = conn.execute(
                    "SELECT statement_text, domain_category FROM rfp_shall_statements WHERE id = %s",
                    (d["shall_statement_id"],),
                ).fetchone()
                if shall:
                    d["shall_text"] = shall["statement_text"]
                    d["domain"] = shall["domain_category"]

            meta = {}
            try:
                meta = _json.loads(d.get("metadata") or "{}")
            except (ValueError, TypeError):
                pass
            confidence = float(d.get("confidence_score") or 0.0)
            best_coverage = float(meta.get("best_coverage", 0.0))
            d["quality_score"] = _compute_quality_score(confidence, best_coverage)
            if not d.get("domain"):
                d["domain"] = d.get("domain_category") or ""

        return jsonify({"drafts": drafts, "total": len(drafts)})
    finally:
        conn.close()


@govcon_api.route("/drafts/<draft_id>/approve", methods=["PUT"])
@abac_protect(_draft_resource_attrs, "approve")
def approve_draft(draft_id):
    """PUT /api/govcon/drafts/<id>/approve — Approve a draft.

    When approved, the draft content flows into the linked proposal_section
    and advances the section to 'drafting' status if currently 'not_started' or 'outlining'.

    Blocked with 409 gate=placeholder_guard while unresolved [PLACEHOLDER]
    tokens remain in the draft (ground-prop-03, mirroring the RFI export
    gate), or 409 gate=citation_guard when the draft has citation defects
    (trust-cite-02). Body {"force_placeholders": true} / {"force_citations":
    true} bypasses the respective gate after human review and writes an
    explicit audit trail entry.
    """
    conn = _get_db()
    try:
        data = request.get_json(silent=True) or {}
        reviewer = data.get("reviewed_by", "govcon_api")
        force = bool(data.get("force_placeholders"))
        force_citations = bool(data.get("force_citations"))

        draft = conn.execute("SELECT * FROM proposal_section_drafts WHERE id = %s", (draft_id,)).fetchone()
        if not draft:
            return jsonify({"error": "Draft not found"}), 404
        draft = dict(draft)

        # Placeholder gate (ground-prop-03)
        try:
            from tools.govcon.response_drafter import unresolved_placeholders
            placeholder_tokens = unresolved_placeholders(draft)
        except Exception:
            import json as _json
            try:
                meta = _json.loads(draft.get("metadata") or "{}")
                placeholder_tokens = list(meta.get("placeholder_tokens") or [])
            except (ValueError, TypeError):
                placeholder_tokens = []
        if placeholder_tokens and not force:
            return (
                jsonify(
                    {
                        "error": "Placeholder gate: unresolved [PLACEHOLDER] tokens remain — resolve or force",
                        "gate": "placeholder_guard",
                        "placeholder_tokens": placeholder_tokens,
                        "draft_id": draft_id,
                    }
                ),
                409,
            )
        if placeholder_tokens:
            _audit(
                conn,
                "placeholder_guard_override",
                f"Draft {draft_id} approved by {reviewer} despite {len(placeholder_tokens)} "
                f"unresolved placeholder token(s): {', '.join(placeholder_tokens)}",
                actor=reviewer,
            )

        # Citation gate (trust-cite-02, mirrors placeholder_guard)
        try:
            from tools.govcon.response_drafter import citation_findings
            citation_issues = citation_findings(draft)
        except Exception:
            citation_issues = []
        if citation_issues and not force_citations:
            return (
                jsonify(
                    {
                        "error": "Citation gate: draft has citation defects — add "
                                 "[source: <id>] citations or force",
                        "gate": "citation_guard",
                        "citation_findings": citation_issues,
                        "draft_id": draft_id,
                    }
                ),
                409,
            )
        if citation_issues:
            import json as _json
            _audit(
                conn,
                "citation_guard_override",
                f"Draft {draft_id} approved by {reviewer} despite {len(citation_issues)} "
                f"citation defect(s): {_json.dumps(citation_issues)}",
                actor=reviewer,
            )

        # Update draft status (new row for audit trail)
        conn.execute(
            """INSERT INTO proposal_section_drafts
               (id, section_id, opportunity_id, shall_statement_id, capability_ids,
                draft_content, confidence, generation_model, knowledge_block_ids,
                status, reviewed_by, reviewed_at, review_notes, created_at, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'approved', %s, %s, %s, %s, %s)""",
            (
                _uuid(),
                draft.get("section_id"),
                draft.get("opportunity_id"),
                draft.get("shall_statement_id"),
                draft.get("capability_ids"),
                draft.get("draft_content"),
                draft.get("confidence"),
                draft.get("generation_model"),
                draft.get("knowledge_block_ids"),
                reviewer,
                now_isoformat(),
                data.get("review_notes", ""),
                now_isoformat(),
                DEFAULT_CLASSIFICATION,
            ),
        )

        # If section linked, update section content and advance status
        section_id = draft.get("section_id")
        if section_id:
            section = conn.execute("SELECT status FROM proposal_sections WHERE id = %s", (section_id,)).fetchone()
            if section and section["status"] in ("not_started", "outlining"):
                conn.execute(
                    "UPDATE proposal_sections SET status = 'drafting', notes = %s, updated_at = %s WHERE id = %s",
                    (f"AI draft approved by {reviewer}", now_isoformat(), section_id),
                )
                conn.execute(
                    "INSERT INTO proposal_status_history (entity_type, entity_id, old_status, new_status, changed_by, reason) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    ("section", section_id, section["status"], "drafting", reviewer, "AI draft approved"),
                )

        _audit(conn, "approve_draft", f"Draft {draft_id} approved by {reviewer}")
        conn.commit()
        return jsonify({"status": "ok", "draft_id": draft_id, "approved": True})
    finally:
        conn.close()


@govcon_api.route("/drafts/<draft_id>/reject", methods=["PUT"])
@abac_protect(_draft_resource_attrs, "reject")
def reject_draft(draft_id):
    """PUT /api/govcon/drafts/<id>/reject — Reject a draft with feedback."""
    conn = _get_db()
    try:
        data = request.get_json(silent=True) or {}
        reviewer = data.get("reviewed_by", "govcon_api")

        draft = conn.execute("SELECT * FROM proposal_section_drafts WHERE id = %s", (draft_id,)).fetchone()
        if not draft:
            return jsonify({"error": "Draft not found"}), 404
        draft = dict(draft)

        # Append-only: create new row with rejected status
        conn.execute(
            """INSERT INTO proposal_section_drafts
               (id, section_id, opportunity_id, shall_statement_id, capability_ids,
                draft_content, confidence, generation_model, knowledge_block_ids,
                status, reviewed_by, reviewed_at, review_notes, created_at, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'rejected', %s, %s, %s, %s, %s)""",
            (
                _uuid(),
                draft.get("section_id"),
                draft.get("opportunity_id"),
                draft.get("shall_statement_id"),
                draft.get("capability_ids"),
                draft.get("draft_content"),
                draft.get("confidence"),
                draft.get("generation_model"),
                draft.get("knowledge_block_ids"),
                reviewer,
                now_isoformat(),
                data.get("review_notes", "Rejected"),
                now_isoformat(),
                DEFAULT_CLASSIFICATION,
            ),
        )

        _audit(conn, "reject_draft", f"Draft {draft_id} rejected by {reviewer}: {data.get('review_notes', '')}")
        conn.commit()
        return jsonify({"status": "ok", "draft_id": draft_id, "rejected": True})
    finally:
        conn.close()


@govcon_api.route("/drafts/<draft_id>/rewrite-save", methods=["POST"])
@require_role(*GOVCON_WRITE_ROLES)
def rewrite_save_draft(draft_id):
    """POST /api/govcon/drafts/<id>/rewrite-save — Save a WriteGuard rewrite as a new draft row.

    Creates a new append-only draft row with the rewritten content,
    preserving the original draft's metadata and linking.
    """
    conn = _get_db()
    try:
        data = request.get_json(silent=True) or {}
        reviewer = data.get("reviewed_by", "writeguard_inline")
        reason = data.get("reason", "WriteGuard rewrite accepted")
        new_content = data.get("draft_content", "").strip()
        if not new_content:
            return jsonify({"error": "draft_content is required"}), 400

        # Fetch original draft to inherit links
        orig = conn.execute(
            "SELECT * FROM proposal_section_drafts WHERE id = %s ORDER BY created_at DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        if not orig:
            return jsonify({"error": "Draft not found"}), 404

        import json as _json
        metadata = {}
        try:
            metadata = _json.loads(orig.get("metadata") or "{}")
        except (ValueError, TypeError):
            pass
        metadata["writeguard"] = metadata.get("writeguard", {})
        metadata["writeguard"]["rewritten"] = True
        metadata["writeguard"]["rewrite_reason"] = reason
        metadata["writeguard"]["rewritten_at"] = now_isoformat()
        metadata["writeguard"]["original_draft_id"] = draft_id

        new_id = _uuid()
        conn.execute(
            """INSERT INTO proposal_section_drafts
               (id, section_id, opportunity_id, shall_statement_id, capability_ids,
                draft_content, draft_method, confidence, generation_model, knowledge_block_ids,
                status, reviewed_by, reviewed_at, review_notes, metadata, created_at, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s, %s, %s, %s)""",
            (
                new_id,
                orig.get("section_id"),
                orig.get("opportunity_id"),
                orig.get("shall_statement_id"),
                orig.get("capability_ids"),
                new_content,
                orig.get("draft_method"),
                orig.get("confidence"),
                orig.get("generation_model"),
                orig.get("knowledge_block_ids"),
                reviewer,
                now_isoformat(),
                reason,
                _json.dumps(metadata),
                now_isoformat(),
                orig.get("classification", DEFAULT_CLASSIFICATION),
            ),
        )
        _audit(conn, "rewrite_draft", f"Draft {draft_id} rewritten by {reviewer}: {reason}")
        conn.commit()
        return jsonify({"status": "ok", "draft_id": new_id, "previous_draft_id": draft_id})
    finally:
        conn.close()


@govcon_api.route("/opportunities/<opp_id>/bulk-writeguard", methods=["POST"])
@require_role(*GOVCON_WRITE_ROLES)
def bulk_writeguard(opp_id):
    """POST /api/govcon/opportunities/<id>/bulk-writeguard — Run WriteGuard on all drafts.

    Delegates to tools.govcon.run_writeguard_on_drafts.run(opp_id=...) and
    returns a summary with per-section scores and findings.
    """
    try:
        from tools.govcon.run_writeguard_on_drafts import run as _run_wg

        summary = _run_wg(opp_id=opp_id)
        return jsonify(summary)
    except Exception as e:
        logger.error("Bulk WriteGuard failed for %s: %s", opp_id, e)
        return jsonify({"error": str(e)}), 500


# =====================================================================
# Gap Analysis
# =====================================================================


@govcon_api.route("/gaps", methods=["GET"])
def get_gaps():
    """GET /api/govcon/gaps — Full gap analysis across all requirement patterns."""
    try:
        from tools.govcon.gap_analyzer import analyze_gaps

        result = analyze_gaps()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@govcon_api.route("/gaps/recommendations", methods=["GET"])
def get_gap_recommendations():
    """GET /api/govcon/gaps/recommendations — Enhancement recommendations for gaps."""
    try:
        from tools.govcon.gap_analyzer import generate_recommendations

        result = generate_recommendations()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@govcon_api.route("/gaps/heatmap", methods=["GET"])
def get_gap_heatmap():
    """GET /api/govcon/gaps/heatmap — Domain x Grade heatmap."""
    try:
        from tools.govcon.gap_analyzer import get_heatmap

        result = get_heatmap()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =====================================================================
# Knowledge Base
# =====================================================================


@govcon_api.route("/knowledge-base", methods=["GET"])
def search_knowledge_base():
    """GET /api/govcon/knowledge-base?q=&domain=&category= — Search KB."""
    try:
        from tools.govcon.knowledge_base import search_blocks, list_blocks

        query = request.args.get("q")
        domain = request.args.get("domain")
        category = request.args.get("category")

        if query:
            result = search_blocks(query, domain=domain, category=category)
        else:
            result = list_blocks(domain=domain, category=category)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@govcon_api.route("/knowledge-base", methods=["POST"])
def create_knowledge_block():
    """POST /api/govcon/knowledge-base — Create a knowledge block."""
    try:
        from tools.govcon.knowledge_base import add_block

        data = request.get_json(silent=True) or {}
        result = add_block(
            title=data.get("title", ""),
            content=data.get("content", ""),
            category=data.get("category", "capability_description"),
            domain=data.get("domain", "general"),
            volume_type=data.get("volume_type"),
            keywords=data.get("keywords"),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =====================================================================
# Competitor Intelligence
# =====================================================================


@govcon_api.route("/competitors/scan", methods=["POST"])
def scan_awards():
    """POST /api/govcon/competitors/scan — Scan SAM.gov for award notices."""
    try:
        from tools.govcon.award_tracker import scan_awards as _scan

        result = _scan()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@govcon_api.route("/competitors/leaderboard", methods=["GET"])
def competitor_leaderboard():
    """GET /api/govcon/competitors/leaderboard — Vendor leaderboard."""
    try:
        from tools.govcon.competitor_profiler import get_leaderboard

        naics = request.args.get("naics")
        agency = request.args.get("agency")
        limit = int(request.args.get("limit", 20))
        result = get_leaderboard(naics=naics, agency=agency, limit=limit)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@govcon_api.route("/competitors/profile/<vendor>", methods=["GET"])
def competitor_profile(vendor):
    """GET /api/govcon/competitors/profile/<vendor> — Vendor profile."""
    try:
        from tools.govcon.competitor_profiler import profile_vendor

        result = profile_vendor(vendor)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =====================================================================
# Pipeline — Full GovCon Intelligence Pipeline
# =====================================================================


@govcon_api.route("/pipeline/run", methods=["POST"])
def run_pipeline():
    """POST /api/govcon/pipeline/run — Run full GovCon Intelligence pipeline.

    Stages: DISCOVER → EXTRACT → MAP → DRAFT
    Can run specific stages or the full pipeline.
    """
    data = request.get_json(silent=True) or {}
    stages = data.get("stages", ["discover", "extract", "map", "draft"])
    opp_id = data.get("opportunity_id")

    results = {"status": "ok", "stages": {}}

    try:
        if "discover" in stages:
            try:
                from tools.govcon.sam_scanner import scan_sam_gov as _scan_sam

                results["stages"]["discover"] = _scan_sam()
            except Exception as e:
                results["stages"]["discover"] = {"status": "error", "error": str(e)}

        if "extract" in stages:
            try:
                from tools.govcon.requirement_extractor import extract_and_store

                results["stages"]["extract"] = extract_and_store(opp_id=opp_id)
            except Exception as e:
                results["stages"]["extract"] = {"status": "error", "error": str(e)}

        if "map" in stages:
            try:
                from tools.govcon.capability_mapper import map_all_patterns

                results["stages"]["map"] = map_all_patterns()
            except Exception as e:
                results["stages"]["map"] = {"status": "error", "error": str(e)}

        if "draft" in stages and opp_id:
            try:
                from tools.govcon.response_drafter import draft_all_for_opportunity

                results["stages"]["draft"] = draft_all_for_opportunity(opp_id)
            except Exception as e:
                results["stages"]["draft"] = {"status": "error", "error": str(e)}

        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =====================================================================
# Questions to Government (D-QTG-1 through D-QTG-5)
# =====================================================================


@govcon_api.route("/opportunities/<opp_id>/generate-questions", methods=["POST"])
def generate_questions(opp_id):
    """POST /api/govcon/opportunities/<id>/generate-questions

    Auto-generate strategic questions from RFP analysis (D-QTG-1).
    Deterministic regex/keyword extraction — no LLM needed.
    """
    try:
        from tools.govcon.question_generator import generate_and_store

        data = request.get_json(silent=True) or {}
        result = generate_and_store(
            opp_id=opp_id,
            created_by=data.get("created_by", "govcon_api"),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@govcon_api.route("/opportunities/<opp_id>/questions", methods=["GET"])
def list_questions(opp_id):
    """GET /api/govcon/opportunities/<id>/questions

    List questions with optional filters: category, status, priority, source.
    """
    conn = _get_db()
    try:
        query = "SELECT * FROM proposal_questions WHERE opportunity_id = ?"
        params = [opp_id]

        category = request.args.get("category")
        status = request.args.get("status")
        priority = request.args.get("priority")
        source = request.args.get("source")

        if category:
            query += " AND category = ?"
            params.append(category)
        if status:
            query += " AND status = ?"
            params.append(status)
        if priority:
            query += " AND priority = ?"
            params.append(priority)
        if source:
            query += " AND source = ?"
            params.append(source)

        query += " ORDER BY question_number ASC"
        rows = conn.execute(query, params).fetchall()
        questions = [dict(r) for r in rows]

        # Stats
        stats = {
            "total": len(questions),
            "by_category": {},
            "by_status": {},
            "by_priority": {},
        }
        for q in questions:
            cat = q.get("category", "other")
            stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1
            st = q.get("status", "draft")
            stats["by_status"][st] = stats["by_status"].get(st, 0) + 1
            pr = q.get("priority", "medium")
            stats["by_priority"][pr] = stats["by_priority"].get(pr, 0) + 1

        return jsonify({"questions": questions, "stats": stats})
    finally:
        conn.close()


@govcon_api.route("/opportunities/<opp_id>/questions", methods=["POST"])
def create_question(opp_id):
    """POST /api/govcon/opportunities/<id>/questions — Add a manual question."""
    conn = _get_db()
    try:
        data = request.get_json(silent=True) or {}
        if not data.get("question_text"):
            return jsonify({"error": "question_text is required"}), 400

        # Get next question number
        row = conn.execute(
            "SELECT MAX(question_number) as mx FROM proposal_questions WHERE opportunity_id = %s",
            (opp_id,),
        ).fetchone()
        next_num = (row["mx"] or 0) + 1

        q_id = _uuid()
        now = now_isoformat()
        conn.execute(
            """INSERT INTO proposal_questions
               (id, opportunity_id, question_number, question_text, category, priority,
                source, rfp_section_ref, status, created_by, classification, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, 'manual', %s, 'draft', %s, %s, %s, %s)""",
            (
                q_id,
                opp_id,
                next_num,
                data["question_text"],
                data.get("category", "scope"),
                data.get("priority", "medium"),
                data.get("rfp_section_ref", ""),
                data.get("created_by", "govcon_api"),
                DEFAULT_CLASSIFICATION,
                now,
                now,
            ),
        )

        # Update question_count
        total = conn.execute(
            "SELECT COUNT(*) as c FROM proposal_questions WHERE opportunity_id = %s",
            (opp_id,),
        ).fetchone()["c"]
        conn.execute(
            "UPDATE proposal_opportunities SET question_count = %s, updated_at = %s WHERE id = %s",
            (total, now, opp_id),
        )

        _audit(conn, "create_question", f"opp={opp_id}, manual, #{next_num}")
        conn.commit()
        return jsonify({"status": "ok", "question_id": q_id, "question_number": next_num})
    finally:
        conn.close()


@govcon_api.route("/questions/<q_id>", methods=["PUT"])
def update_question(q_id):
    """PUT /api/govcon/questions/<id> — Update question fields (text, category, priority, rfp_section_ref)."""
    conn = _get_db()
    try:
        data = request.get_json(silent=True) or {}
        allowed = {"question_text", "category", "priority", "rfp_section_ref"}
        updates = {k: v for k, v in data.items() if k in allowed and v is not None}
        if not updates:
            return jsonify({"error": "No valid fields to update"}), 400

        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [now_isoformat(), q_id]
        conn.execute(
            f"UPDATE proposal_questions SET {sets}, updated_at = %s WHERE id = %s",  # nosec B608 -- column names validated against allowlist above
            vals,
        )
        _audit(conn, "update_question", f"question={q_id}, fields={list(updates.keys())}")
        conn.commit()
        return jsonify({"status": "ok", "question_id": q_id, "updated_fields": list(updates.keys())})
    finally:
        conn.close()


@govcon_api.route("/questions/<q_id>/status", methods=["PUT"])
def change_question_status(q_id):
    """PUT /api/govcon/questions/<id>/status — Status transition with validation.

    Valid transitions: draft→approved, approved→submitted, approved→draft, submitted→answered
    """
    conn = _get_db()
    try:
        data = request.get_json(silent=True) or {}
        new_status = data.get("status")
        if not new_status:
            return jsonify({"error": "status is required"}), 400

        q = conn.execute("SELECT * FROM proposal_questions WHERE id = %s", (q_id,)).fetchone()
        if not q:
            return jsonify({"error": "Question not found"}), 404

        old_status = q["status"]

        # Enforce valid transitions
        valid_transitions = {
            "draft": ["approved"],
            "approved": ["submitted", "draft"],
            "submitted": ["answered"],
            "answered": [],
        }
        allowed = valid_transitions.get(old_status, [])
        if new_status not in allowed:
            return jsonify({"error": f"Invalid transition: {old_status} → {new_status}. Allowed: {allowed}"}), 400

        now = now_isoformat()
        extra_fields = ""
        extra_vals = []

        if new_status == "approved":
            extra_fields = ", approved_by = ?, approved_at = ?"
            extra_vals = [data.get("changed_by", "govcon_api"), now]
        elif new_status == "submitted":
            extra_fields = ", submitted_at = ?"
            extra_vals = [now]

        conn.execute(
            f"UPDATE proposal_questions SET status = %s, updated_at = %s{extra_fields} WHERE id = %s",  # nosec B608 -- table/column names are internal constants, not user input
            [new_status, now] + extra_vals + [q_id],
        )

        # Status history (id is AUTOINCREMENT, created_at has default)
        conn.execute(
            "INSERT INTO proposal_status_history "
            "(entity_type, entity_id, old_status, new_status, changed_by, reason) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            ("question", q_id, old_status, new_status, data.get("changed_by", "govcon_api"), data.get("notes", "")),
        )

        _audit(conn, "change_question_status", f"question={q_id}, {old_status}→{new_status}")
        conn.commit()
        return jsonify({"status": "ok", "question_id": q_id, "old_status": old_status, "new_status": new_status})
    finally:
        conn.close()


@govcon_api.route("/opportunities/<opp_id>/questions/bulk-status", methods=["PUT"])
def bulk_status_change(opp_id):
    """PUT /api/govcon/opportunities/<id>/questions/bulk-status — Bulk status change."""
    conn = _get_db()
    try:
        data = request.get_json(silent=True) or {}
        question_ids = data.get("question_ids", [])
        new_status = data.get("status")
        changed_by = data.get("changed_by", "govcon_api")

        if not question_ids or not new_status:
            return jsonify({"error": "question_ids and status are required"}), 400

        valid_transitions = {
            "draft": ["approved"],
            "approved": ["submitted", "draft"],
            "submitted": ["answered"],
            "answered": [],
        }

        now = now_isoformat()
        changed = 0
        skipped = 0

        for qid in question_ids:
            q = conn.execute(
                "SELECT id, status FROM proposal_questions WHERE id = %s AND opportunity_id = %s",
                (qid, opp_id),
            ).fetchone()
            if not q:
                skipped += 1
                continue

            old = q["status"]
            if new_status not in valid_transitions.get(old, []):
                skipped += 1
                continue

            conn.execute(
                "UPDATE proposal_questions SET status = %s, updated_at = %s WHERE id = %s",
                (new_status, now, qid),
            )
            conn.execute(
                "INSERT INTO proposal_status_history "
                "(entity_type, entity_id, old_status, new_status, changed_by, reason) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                ("question", qid, old, new_status, changed_by, "Bulk status change"),
            )
            changed += 1

        _audit(conn, "bulk_status_change", f"opp={opp_id}, changed={changed}, skipped={skipped}")
        conn.commit()
        return jsonify({"status": "ok", "changed": changed, "skipped": skipped})
    finally:
        conn.close()


@govcon_api.route("/opportunities/<opp_id>/questions/export", methods=["POST"])
def export_questions_endpoint(opp_id):
    """POST /api/govcon/opportunities/<id>/questions/export — Export to HTML document."""
    try:
        from tools.govcon.question_exporter import export_questions

        data = request.get_json(silent=True) or {}
        result = export_questions(
            opp_id=opp_id,
            status_filter=data.get("status_filter"),
            output_path=data.get("output_path"),
            company_name=data.get("company_name"),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@govcon_api.route("/opportunities/<opp_id>/amendments", methods=["POST"])
def upload_amendment_endpoint(opp_id):
    """POST /api/govcon/opportunities/<id>/amendments — Upload amendment (file or text)."""
    try:
        from tools.govcon.amendment_tracker import upload_amendment

        data = request.get_json(silent=True) or {}
        # Validate file_path to prevent path traversal — restrict to data/ and .tmp/
        file_path = data.get("file_path")
        if file_path:
            safe_bases = [BASE_DIR / "data", BASE_DIR / ".tmp"]
            resolved = Path(file_path).resolve()
            if not any(str(resolved).startswith(str(sb.resolve())) for sb in safe_bases):
                return jsonify({"error": "file_path must be within data/ or .tmp/"}), 400
            file_path = str(resolved)
        result = upload_amendment(
            opp_id=opp_id,
            title=data.get("title", "Untitled Amendment"),
            file_path=file_path,
            text=data.get("text"),
            description=data.get("description"),
            amendment_date=data.get("amendment_date"),
            uploaded_by=data.get("uploaded_by", "govcon_api"),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@govcon_api.route("/opportunities/<opp_id>/amendments", methods=["GET"])
def list_amendments_endpoint(opp_id):
    """GET /api/govcon/opportunities/<id>/amendments — List amendments."""
    try:
        from tools.govcon.amendment_tracker import list_amendments

        result = list_amendments(opp_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@govcon_api.route("/amendments/<amendment_id>/diff", methods=["GET"])
def get_amendment_diff(amendment_id):
    """GET /api/govcon/amendments/<id>/diff — Get diff data for amendment."""
    try:
        from tools.govcon.amendment_tracker import compute_diff

        result = compute_diff(amendment_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@govcon_api.route("/questions/<q_id>/response", methods=["POST"])
def record_response_endpoint(q_id):
    """POST /api/govcon/questions/<id>/response — Record government Q&A response."""
    try:
        from tools.govcon.amendment_tracker import record_response

        data = request.get_json(silent=True) or {}
        if not data.get("response_text"):
            return jsonify({"error": "response_text is required"}), 400

        result = record_response(
            question_id=q_id,
            response_text=data["response_text"],
            amendment_id=data.get("amendment_id"),
            response_date=data.get("response_date"),
            impacts_requirements=data.get("impacts_requirements", False),
            impact_notes=data.get("impact_notes"),
            recorded_by=data.get("recorded_by", "govcon_api"),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =====================================================================
# Pipeline — Full GovCon Intelligence Pipeline
# =====================================================================


@govcon_api.route("/pipeline/status", methods=["GET"])
def pipeline_status():
    """GET /api/govcon/pipeline/status — Pipeline health and statistics."""
    conn = _get_db()
    try:
        # SAM.gov opportunities
        sam_total = conn.execute("SELECT COUNT(*) as c FROM sam_gov_opportunities").fetchone()["c"]
        sam_active = conn.execute("SELECT COUNT(*) as c FROM sam_gov_opportunities WHERE active = 'true'").fetchone()["c"]

        # Requirements
        shall_total = conn.execute("SELECT COUNT(*) as c FROM rfp_shall_statements").fetchone()["c"]
        pattern_total = conn.execute("SELECT COUNT(*) as c FROM rfp_requirement_patterns").fetchone()["c"]

        # Capability mapping
        mapped = conn.execute("SELECT COUNT(*) as c FROM icdev_capability_map").fetchone()["c"]

        # Drafts
        drafts_total = conn.execute("SELECT COUNT(*) as c FROM proposal_section_drafts").fetchone()["c"]
        drafts_pending = conn.execute(
            "SELECT COUNT(*) as c FROM proposal_section_drafts WHERE status = 'draft'"
        ).fetchone()["c"]
        drafts_approved = conn.execute(
            "SELECT COUNT(*) as c FROM proposal_section_drafts WHERE status = 'approved'"
        ).fetchone()["c"]

        # Knowledge base
        kb_total = conn.execute("SELECT COUNT(*) as c FROM proposal_knowledge_base WHERE status = 'active'").fetchone()[
            "c"
        ]

        # Awards
        awards_total = conn.execute("SELECT COUNT(*) as c FROM govcon_awards").fetchone()["c"]

        # Domain distribution
        domains = conn.execute(
            "SELECT domain_category, COUNT(*) as c FROM rfp_shall_statements GROUP BY domain_category ORDER BY c DESC"
        ).fetchall()

        return jsonify(
            {
                "status": "ok",
                "sam_gov": {"total": sam_total, "active": sam_active},
                "requirements": {"shall_statements": shall_total, "patterns": pattern_total},
                "capability_mapping": {"mapped": mapped},
                "drafts": {"total": drafts_total, "pending_review": drafts_pending, "approved": drafts_approved},
                "knowledge_base": {"active_blocks": kb_total},
                "awards": {"total": awards_total},
                "domain_distribution": {d["domain_category"]: d["c"] for d in domains},
            }
        )
    finally:
        conn.close()


# =====================================================================
# Telco RFP Adapter — FCC Form 470, BEAD, RDOF
# =====================================================================


@govcon_api.route("/telco/form470", methods=["POST"])
def api_govcon_telco_form470():
    from tools.govcon.telco_rfp_adapter import parse_fcc_form470
    data = request.get_json(force=True) or {}
    try:
        return jsonify(parse_fcc_form470(data))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@govcon_api.route("/telco/bead")
def api_govcon_telco_bead():
    from tools.govcon.telco_rfp_adapter import generate_bead_compliance_matrix
    rfp = request.args.to_dict()
    try:
        return jsonify(generate_bead_compliance_matrix(rfp))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@govcon_api.route("/telco/rdof")
def api_govcon_telco_rdof():
    from tools.govcon.telco_rfp_adapter import score_rdof_eligibility
    network_data = request.args.to_dict()
    try:
        return jsonify(score_rdof_eligibility(network_data))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# =====================================================================
# pWin Model — capture-plan signal scoring + weighted pipeline value
# =====================================================================


@govcon_api.route("/proposals/<opp_id>/pwin", methods=["POST"])
@require_role(*GOVCON_WRITE_ROLES)
def compute_proposal_pwin(opp_id):
    """Compute pWin for a proposal from 5 capture-plan signals.

    POST body (all fields optional, default 0.5 = neutral):
    {
      "incumbency": 0.8,          // 0=new entrant, 1=strong incumbent
      "crm_engagement": 0.6,      // 0=no contact, 1=deep relationship
      "competitive_position": 0.5, // 0=weak, 1=clear differentiator
      "compliance_coverage": 0.9,  // 0=gaps, 1=full coverage
      "past_performance_fit": 0.7, // 0=misaligned, 1=direct match
      "estimated_value": 5000000   // optional, for weighted value calc
    }
    """
    from tools.govcon.bayesian_bid_scorer import compute_pwin, PWIN_FACTORS

    data = request.get_json(force=True) or {}
    factors = {f: data[f] for f in PWIN_FACTORS if f in data}
    estimated_value = data.get("estimated_value")

    # If not provided, try to pull from proposal_opportunities
    if estimated_value is None:
        conn = _get_db()
        try:
            row = conn.execute(
                "SELECT estimated_value_low, estimated_value_high FROM proposal_opportunities WHERE id = %s",
                (opp_id,),
            ).fetchone()
            if row:
                lo = row[0] or 0
                hi = row[1] or lo
                try:
                    estimated_value = (float(lo) + float(hi)) / 2.0
                except (TypeError, ValueError):
                    pass
        except Exception:
            pass
        finally:
            conn.close()

    try:
        result = compute_pwin(opp_id, factors, estimated_value)
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500

    # Write the computed pWin back to proposal_opportunities.win_probability
    conn = _get_db()
    try:
        conn.execute(
            "UPDATE proposal_opportunities SET win_probability = %s WHERE id = %s",
            (result["pwin_pct"], opp_id),
        )
        _audit(conn, "pwin.update_proposal", f"pwin={result['pwin_pct']}% → {opp_id}", opp_id)
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

    return jsonify(result)


@govcon_api.route("/proposals/<opp_id>/pwin", methods=["GET"])
def get_proposal_pwin(opp_id):
    """Return the most recent pWin assessment for a proposal."""
    from tools.govcon.bayesian_bid_scorer import get_pwin_assessment

    try:
        assessment = get_pwin_assessment(opp_id)
        if not assessment:
            return jsonify({"status": "not_found", "message": "No pWin assessment found"}), 404
        return jsonify({"status": "ok", **assessment})
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500


@govcon_api.route("/pipeline-value", methods=["GET"])
def get_pipeline_value():
    """Weighted pipeline value roll-up across all active proposals.

    Returns total weighted pipeline value, total potential value, per-opportunity
    breakdown with pWin scores and factor breakdowns.
    """
    from tools.govcon.bayesian_bid_scorer import pipeline_value_rollup

    try:
        return jsonify(pipeline_value_rollup())
    except Exception as exc:
        return jsonify({"status": "error", "error": str(exc)}), 500


_STATUS_TO_STAGE = {
    "intake": "discover",
    "bid_no_bid": "discover",
    "no_bid": "discover",
    "go": "extract",
    "map": "map",
    "writing": "draft",
    "review": "draft",
    "final": "draft",
    "submitted": "submit",
    "submit": "submit",
}


@govcon_api.route("/proposals/bubble-data", methods=["GET"])
@require_role(*GOVCON_WRITE_ROLES)
def get_proposals_bubble_data():
    """GET /api/govcon/proposals/bubble-data — Per-opportunity bubble chart data."""
    from datetime import datetime, timezone
    from tools.govcon.bayesian_bid_scorer import pipeline_value_rollup

    try:
        rollup = pipeline_value_rollup()
        rollup_opps = rollup.get("opportunities", [])
    except Exception:
        rollup_opps = []

    rollup_map = {item["opportunity_id"]: item for item in rollup_opps}

    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, title, status, estimated_value_high, due_date "
            "FROM proposal_opportunities "
            "WHERE status NOT IN ('won','lost','no_bid','cancelled')"
        ).fetchall()
    finally:
        conn.close()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    opportunities = []
    for row in rows:
        r = dict(row)
        opp_id = r["id"]
        item = rollup_map.get(opp_id, {})

        pwin_pct = item.get("pwin_pct")
        pwin = (pwin_pct / 100.0) if pwin_pct is not None else 0.5

        try:
            ceiling = float(r.get("estimated_value_high") or 0) or 1_000_000
        except (TypeError, ValueError):
            ceiling = 1_000_000

        weighted_value = item.get("weighted_value") or round(ceiling * pwin, 2)

        stage = _STATUS_TO_STAGE.get(r.get("status") or "", "discover")

        try:
            due = datetime.strptime(r["due_date"], "%Y-%m-%d")
            days_to_deadline = (due - now).days
        except (ValueError, TypeError, KeyError):
            days_to_deadline = 0

        opportunities.append({
            "opp_id": opp_id,
            "title": r.get("title", ""),
            "stage": stage,
            "pwin": round(pwin, 4),
            "weighted_value": round(weighted_value, 2),
            "ceiling": ceiling,
            "days_to_deadline": days_to_deadline,
        })

    return jsonify({"opportunities": opportunities, "count": len(opportunities)})


@govcon_api.route("/iqe-query", methods=["POST"])
def govcon_iqe_query():
    """IQE NL-to-SQL for GovCon / Proposals canvas."""
    from tools.iqe.nl_to_iqe import nl_to_iqe
    from tools.iqe.parser import IQESyntaxError, parse
    from tools.iqe.executor import execute_query
    import tools.iqe.adapters.govcon  # noqa: F401

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400

    collections = ["govcon.opportunities", "govcon.awards", "govcon.blackhat", "govcon.competitors", "govcon.requirements"]
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


@govcon_api.route("/opportunities/wg-scores", methods=["GET"])
@require_role("admin", "bd", "capture_mgr", "pm")
def get_wg_scores():
    """GET /api/govcon/opportunities/wg-scores?ids=1,2,3

    Returns the latest WriteGuard overall_quality_score for each requested
    opportunity ID.  IDs with no analysis record return null.
    """
    raw = request.args.get("ids", "")
    ids_list = [int(x) for x in raw.split(",") if x.strip().lstrip("-").isdigit()]
    if not ids_list:
        return jsonify({"scores": {}})

    placeholders = ",".join("?" * len(ids_list))
    sql = (
        "SELECT w.opp_id, w.overall_quality_score "
        "FROM wg_analysis_results w "
        "INNER JOIN ("
        "  SELECT opp_id, MAX(created_at) AS latest "
        "  FROM wg_analysis_results "
        f" WHERE opp_id IN ({placeholders}) "
        "  GROUP BY opp_id"
        ") m ON w.opp_id = m.opp_id AND w.created_at = m.latest"
    )
    conn = _get_db()
    try:
        rows = conn.execute(sql, ids_list).fetchall()
    finally:
        conn.close()

    scores = {str(opp_id): score for opp_id, score in rows}
    for opp_id in ids_list:
        scores.setdefault(str(opp_id), None)
    return jsonify({"scores": scores})
