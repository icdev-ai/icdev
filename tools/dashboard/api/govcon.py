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

import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

govcon_api = Blueprint("govcon_api", __name__, url_prefix="/api/govcon")


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return str(uuid.uuid4())


def _audit(conn, action, details="", actor="govcon_api"):
    """Append-only audit trail (NIST AU-2)."""
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_uuid(), _now(), "govcon.api", actor, action, details, "govcon"),
        )
    except Exception:
        pass


# =====================================================================
# SAM.gov Sync → Proposal Opportunities
# =====================================================================

@govcon_api.route("/sam/scan", methods=["POST"])
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
            query += " AND active = 1"
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
                "SELECT id, status FROM proposal_opportunities WHERE solicitation_number = ?",
                (opp.get("solicitation_number", ""),),
            ).fetchone()
            opp["linked_proposal_id"] = linked["id"] if linked else None
            opp["linked_proposal_status"] = linked["status"] if linked else None

        return jsonify({"opportunities": opportunities, "total": len(opportunities)})
    finally:
        conn.close()


@govcon_api.route("/sam/import/<sam_opp_id>", methods=["POST"])
def import_sam_to_proposal(sam_opp_id):
    """POST /api/govcon/sam/import/<id> — Create proposal_opportunity from SAM.gov record.

    Links sam_gov_opportunities → proposal_opportunities for full lifecycle tracking.
    """
    conn = _get_db()
    try:
        sam = conn.execute("SELECT * FROM sam_gov_opportunities WHERE id = ?", (sam_opp_id,)).fetchone()
        if not sam:
            return jsonify({"error": "SAM.gov opportunity not found"}), 404
        sam = dict(sam)

        # Check if already linked
        existing = conn.execute(
            "SELECT id FROM proposal_opportunities WHERE solicitation_number = ?",
            (sam.get("solicitation_number", ""),),
        ).fetchone()
        if existing:
            return jsonify({"error": "Already imported", "proposal_id": existing["id"]}), 409

        # Create proposal_opportunity
        prop_id = _uuid()
        conn.execute(
            """INSERT INTO proposal_opportunities
               (id, solicitation_number, title, agency, sub_agency, due_date,
                naics_code, set_aside_type, rfp_url, status, classification, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'intake', 'CUI', ?, ?)""",
            (
                prop_id,
                sam.get("solicitation_number", ""),
                sam.get("title", "Untitled"),
                sam.get("agency", ""),
                sam.get("agency_hierarchy", ""),
                sam.get("response_deadline", ""),
                sam.get("naics_code", ""),
                sam.get("set_aside_type", ""),
                sam.get("solicitation_number", ""),  # use as rfp_url placeholder
                _now(), _now(),
            ),
        )

        # Link SAM record to proposal
        conn.execute(
            "UPDATE sam_gov_opportunities SET proposal_opportunity_id = ? WHERE id = ?",
            (prop_id, sam_opp_id),
        )

        # Record status change
        conn.execute(
            "INSERT INTO proposal_status_history (entity_type, entity_id, old_status, new_status, changed_by, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
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

        return jsonify({
            "statements": statements,
            "total": len(statements),
            "by_domain": domains,
        })
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
def map_capabilities(opp_id):
    """POST /api/govcon/opportunities/<id>/map-capabilities

    Map ICDEV capabilities against extracted requirements for this opportunity.
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
                        "SELECT id FROM proposal_compliance_matrix WHERE opportunity_id = ? AND requirement_text = ?",
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
                               VALUES (?, ?, ?, ?, ?, ?, ?, 'CUI', ?, ?)""",
                            (
                                _uuid(), opp_id,
                                item.get("domain", ""),
                                item["statement"][:500],
                                grade,
                                status_map.get(grade, "not_addressed"),
                                f"Auto: {item.get('best_capability', 'none')} ({item.get('coverage_score', 0):.0%})",
                                _now(), _now(),
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
def bid_recommendation(opp_id):
    """GET /api/govcon/opportunities/<id>/bid-recommendation — Get bid/no-bid recommendation."""
    try:
        from tools.govcon.compliance_populator import get_summary
        result = get_summary(opp_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =====================================================================
# AI Drafting → proposal_section_drafts
# =====================================================================

@govcon_api.route("/opportunities/<opp_id>/auto-draft", methods=["POST"])
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

        # Enrich with shall statement text
        for d in drafts:
            if d.get("shall_statement_id"):
                shall = conn.execute(
                    "SELECT statement_text, domain_category FROM rfp_shall_statements WHERE id = ?",
                    (d["shall_statement_id"],),
                ).fetchone()
                if shall:
                    d["shall_text"] = shall["statement_text"]
                    d["domain"] = shall["domain_category"]

        return jsonify({"drafts": drafts, "total": len(drafts)})
    finally:
        conn.close()


@govcon_api.route("/drafts/<draft_id>/approve", methods=["PUT"])
def approve_draft(draft_id):
    """PUT /api/govcon/drafts/<id>/approve — Approve a draft.

    When approved, the draft content flows into the linked proposal_section
    and advances the section to 'drafting' status if currently 'not_started' or 'outlining'.
    """
    conn = _get_db()
    try:
        data = request.get_json(silent=True) or {}
        reviewer = data.get("reviewed_by", "govcon_api")

        draft = conn.execute("SELECT * FROM proposal_section_drafts WHERE id = ?", (draft_id,)).fetchone()
        if not draft:
            return jsonify({"error": "Draft not found"}), 404
        draft = dict(draft)

        # Update draft status (new row for audit trail)
        conn.execute(
            """INSERT INTO proposal_section_drafts
               (id, section_id, opportunity_id, shall_statement_id, capability_ids,
                draft_content, confidence, generation_model, knowledge_block_ids,
                status, reviewed_by, reviewed_at, review_notes, created_at, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?, ?, ?, 'CUI')""",
            (
                _uuid(), draft.get("section_id"), draft.get("opportunity_id"),
                draft.get("shall_statement_id"), draft.get("capability_ids"),
                draft.get("draft_content"), draft.get("confidence"),
                draft.get("generation_model"), draft.get("knowledge_block_ids"),
                reviewer, _now(), data.get("review_notes", ""),
                _now(),
            ),
        )

        # If section linked, update section content and advance status
        section_id = draft.get("section_id")
        if section_id:
            section = conn.execute("SELECT status FROM proposal_sections WHERE id = ?", (section_id,)).fetchone()
            if section and section["status"] in ("not_started", "outlining"):
                conn.execute(
                    "UPDATE proposal_sections SET status = 'drafting', notes = ?, updated_at = ? WHERE id = ?",
                    (f"AI draft approved by {reviewer}", _now(), section_id),
                )
                conn.execute(
                    "INSERT INTO proposal_status_history (entity_type, entity_id, old_status, new_status, changed_by, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("section", section_id, section["status"], "drafting", reviewer, "AI draft approved"),
                )

        _audit(conn, "approve_draft", f"Draft {draft_id} approved by {reviewer}")
        conn.commit()
        return jsonify({"status": "ok", "draft_id": draft_id, "approved": True})
    finally:
        conn.close()


@govcon_api.route("/drafts/<draft_id>/reject", methods=["PUT"])
def reject_draft(draft_id):
    """PUT /api/govcon/drafts/<id>/reject — Reject a draft with feedback."""
    conn = _get_db()
    try:
        data = request.get_json(silent=True) or {}
        reviewer = data.get("reviewed_by", "govcon_api")

        draft = conn.execute("SELECT * FROM proposal_section_drafts WHERE id = ?", (draft_id,)).fetchone()
        if not draft:
            return jsonify({"error": "Draft not found"}), 404
        draft = dict(draft)

        # Append-only: create new row with rejected status
        conn.execute(
            """INSERT INTO proposal_section_drafts
               (id, section_id, opportunity_id, shall_statement_id, capability_ids,
                draft_content, confidence, generation_model, knowledge_block_ids,
                status, reviewed_by, reviewed_at, review_notes, created_at, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'rejected', ?, ?, ?, ?, 'CUI')""",
            (
                _uuid(), draft.get("section_id"), draft.get("opportunity_id"),
                draft.get("shall_statement_id"), draft.get("capability_ids"),
                draft.get("draft_content"), draft.get("confidence"),
                draft.get("generation_model"), draft.get("knowledge_block_ids"),
                reviewer, _now(), data.get("review_notes", "Rejected"),
                _now(),
            ),
        )

        _audit(conn, "reject_draft", f"Draft {draft_id} rejected by {reviewer}: {data.get('review_notes', '')}")
        conn.commit()
        return jsonify({"status": "ok", "draft_id": draft_id, "rejected": True})
    finally:
        conn.close()


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
            "SELECT MAX(question_number) as mx FROM proposal_questions WHERE opportunity_id = ?",
            (opp_id,),
        ).fetchone()
        next_num = (row["mx"] or 0) + 1

        q_id = _uuid()
        now = _now()
        conn.execute(
            """INSERT INTO proposal_questions
               (id, opportunity_id, question_number, question_text, category, priority,
                source, rfp_section_ref, status, created_by, classification, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'manual', ?, 'draft', ?, 'CUI', ?, ?)""",
            (
                q_id, opp_id, next_num,
                data["question_text"],
                data.get("category", "scope"),
                data.get("priority", "medium"),
                data.get("rfp_section_ref", ""),
                data.get("created_by", "govcon_api"),
                now, now,
            ),
        )

        # Update question_count
        total = conn.execute(
            "SELECT COUNT(*) as c FROM proposal_questions WHERE opportunity_id = ?",
            (opp_id,),
        ).fetchone()["c"]
        conn.execute(
            "UPDATE proposal_opportunities SET question_count = ?, updated_at = ? WHERE id = ?",
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
        vals = list(updates.values()) + [_now(), q_id]
        conn.execute(
            f"UPDATE proposal_questions SET {sets}, updated_at = ? WHERE id = ?", vals
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

        q = conn.execute("SELECT * FROM proposal_questions WHERE id = ?", (q_id,)).fetchone()
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
            return jsonify({
                "error": f"Invalid transition: {old_status} → {new_status}. Allowed: {allowed}"
            }), 400

        now = _now()
        extra_fields = ""
        extra_vals = []

        if new_status == "approved":
            extra_fields = ", approved_by = ?, approved_at = ?"
            extra_vals = [data.get("changed_by", "govcon_api"), now]
        elif new_status == "submitted":
            extra_fields = ", submitted_at = ?"
            extra_vals = [now]

        conn.execute(
            f"UPDATE proposal_questions SET status = ?, updated_at = ?{extra_fields} WHERE id = ?",
            [new_status, now] + extra_vals + [q_id],
        )

        # Status history (id is AUTOINCREMENT, created_at has default)
        conn.execute(
            "INSERT INTO proposal_status_history "
            "(entity_type, entity_id, old_status, new_status, changed_by, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("question", q_id, old_status, new_status,
             data.get("changed_by", "govcon_api"), data.get("notes", "")),
        )

        _audit(conn, "change_question_status", f"question={q_id}, {old_status}→{new_status}")
        conn.commit()
        return jsonify({"status": "ok", "question_id": q_id,
                        "old_status": old_status, "new_status": new_status})
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

        now = _now()
        changed = 0
        skipped = 0

        for qid in question_ids:
            q = conn.execute(
                "SELECT id, status FROM proposal_questions WHERE id = ? AND opportunity_id = ?",
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
                "UPDATE proposal_questions SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now, qid),
            )
            conn.execute(
                "INSERT INTO proposal_status_history "
                "(entity_type, entity_id, old_status, new_status, changed_by, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
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
        sam_active = conn.execute("SELECT COUNT(*) as c FROM sam_gov_opportunities WHERE active = 1").fetchone()["c"]

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
        kb_total = conn.execute(
            "SELECT COUNT(*) as c FROM proposal_knowledge_base WHERE status = 'active'"
        ).fetchone()["c"]

        # Awards
        awards_total = conn.execute("SELECT COUNT(*) as c FROM govcon_awards").fetchone()["c"]

        # Domain distribution
        domains = conn.execute(
            "SELECT domain_category, COUNT(*) as c FROM rfp_shall_statements GROUP BY domain_category ORDER BY c DESC"
        ).fetchall()

        return jsonify({
            "status": "ok",
            "sam_gov": {"total": sam_total, "active": sam_active},
            "requirements": {"shall_statements": shall_total, "patterns": pattern_total},
            "capability_mapping": {"mapped": mapped},
            "drafts": {"total": drafts_total, "pending_review": drafts_pending, "approved": drafts_approved},
            "knowledge_base": {"active_blocks": kb_total},
            "awards": {"total": awards_total},
            "domain_distribution": {d["domain_category"]: d["c"] for d in domains},
        })
    finally:
        conn.close()
