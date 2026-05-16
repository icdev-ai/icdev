#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN (Proprietary Business Information)
# Distribution: D
# POC: GovProposal System Administrator
"""SBIR/STTR Proposal Manager — lifecycle tracking, phase transitions,
submission checklists, TRL assessment, and dashboard analytics.

Manages Small Business Innovation Research (SBIR) and Small Business
Technology Transfer (STTR) proposals across Phase I (feasibility),
Phase II (development), Phase III (commercialization), and Direct-to-
Phase-II programs.  Auto-creates linked opportunity and proposal records,
validates phase transitions, generates submission checklists with
completion status, performs TRL gap analysis, and produces dashboard
summary data.

Usage:
    python tools/proposal/sbir_manager.py --create --type sbir --phase phase_1 --agency "DoD" --topic "AF241-001" --pi "Dr. Smith" --json
    python tools/proposal/sbir_manager.py --update --sbir-id "SBIR-abc" --status submitted --json
    python tools/proposal/sbir_manager.py --get --sbir-id "SBIR-abc" --json
    python tools/proposal/sbir_manager.py --list [--type sbir] [--phase phase_1] [--agency "DoD"] [--status drafting] --json
    python tools/proposal/sbir_manager.py --phase-transition --sbir-id "SBIR-abc" --new-phase phase_2 --json
    python tools/proposal/sbir_manager.py --checklist --sbir-id "SBIR-abc" --json
    python tools/proposal/sbir_manager.py --trl --sbir-id "SBIR-abc" --json
    python tools/proposal/sbir_manager.py --dashboard --json
    python tools/proposal/sbir_manager.py --search-topics --keywords "AI,cybersecurity" [--agency "DoD"] --json
"""

import argparse
import json
import os
import re
import secrets
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))

try:
    import requests
except ImportError:
    requests = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now():
    """UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat()


def _sbir_id():
    """Generate an SBIR-scoped identifier."""
    return "SBIR-" + secrets.token_hex(6)


def _prop_id():
    """Generate a proposal identifier."""
    return "PROP-" + secrets.token_hex(6)


def _opp_id():
    """Generate an opportunity identifier."""
    return "OPP-" + secrets.token_hex(6)


def _get_db(db_path=None):
    """Return an SQLite connection with WAL + FK enabled."""
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _audit(conn, event_type, action, entity_type=None, entity_id=None, details=None):
    """Append-only audit trail entry."""
    conn.execute(
        "INSERT INTO audit_trail (event_type, actor, action, entity_type, entity_id, details, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_type, "sbir_manager", action, entity_type, entity_id, details, _now()),
    )


def _row_to_dict(row):
    """Convert sqlite3.Row to plain dict."""
    if row is None:
        return None
    return dict(row)


def _parse_json_field(value, fallback=None):
    """Safely parse a JSON text column, returning *fallback* on failure."""
    if not value:
        return fallback if fallback is not None else []
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return fallback if fallback is not None else []


def _serialize_list(value):
    """Serialize a list or comma-separated string to JSON array for storage."""
    if value is None:
        return None
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, (list, tuple)):
        return json.dumps(list(value))
    return json.dumps([str(value)])


# ---------------------------------------------------------------------------
# Constants — SBIR/STTR business rules
# ---------------------------------------------------------------------------

VALID_PROGRAM_TYPES = ("sbir", "sttr")
VALID_PHASES = ("phase_1", "phase_2", "phase_3", "direct_to_phase_2")
VALID_STATUSES = (
    "drafting", "submitted", "under_review", "selected",
    "awarded", "not_selected", "withdrawn", "phase_2_invited",
)

# Allowed status transitions: current_status -> set of valid next statuses
_STATUS_TRANSITIONS = {
    "drafting":       {"submitted", "withdrawn"},
    "submitted":      {"under_review", "withdrawn"},
    "under_review":   {"selected", "not_selected", "phase_2_invited"},
    "selected":       {"awarded", "withdrawn"},
    "awarded":        {"phase_2_invited"},
    "not_selected":   set(),
    "withdrawn":      set(),
    "phase_2_invited": {"drafting"},  # start drafting Phase II
}

# Phase transition rules: (current_phase) -> allowed next phases
_PHASE_TRANSITIONS = {
    "phase_1":           {"phase_2"},
    "direct_to_phase_2": {"phase_3"},
    "phase_2":           {"phase_3"},
    "phase_3":           set(),  # terminal
}

# Status required before phase transition
_PHASE_TRANSITION_REQUIRED_STATUS = {
    "phase_1":           {"awarded", "phase_2_invited"},
    "direct_to_phase_2": {"awarded"},
    "phase_2":           {"awarded"},
}

# Expected TRL ranges at end of each phase
_PHASE_TRL_EXPECTATIONS = {
    "phase_1":           {"min": 3, "max": 4, "label": "Proof of concept / experimental validation"},
    "direct_to_phase_2": {"min": 3, "max": 4, "label": "Demonstrated in lab with prior feasibility"},
    "phase_2":           {"min": 5, "max": 7, "label": "Prototype validated in relevant environment"},
    "phase_3":           {"min": 7, "max": 9, "label": "System qualified and deployed"},
}

# Typical award amounts and PoP by phase
_PHASE_DEFAULTS = {
    "phase_1":           {"amount": 150000, "months": 6},
    "direct_to_phase_2": {"amount": 1000000, "months": 24},
    "phase_2":           {"amount": 1000000, "months": 24},
    "phase_3":           {"amount": 0, "months": 0},  # varies, no SBIR funding
}

# STTR minimum work allocation percentages
_STTR_MIN_SB_PCT = 30   # small business minimum
_STTR_MIN_RI_PCT = 40   # research institution minimum

# Key SBIR agencies
SBIR_AGENCIES = [
    "DoD", "NSF", "NIH", "DOE", "NASA", "USDA", "DHS", "DOT", "EPA", "ED", "DOC",
]


# ---------------------------------------------------------------------------
# create_sbir
# ---------------------------------------------------------------------------

def create_sbir(program_type, phase, agency, topic_number=None, topic_title=None,
                pi_name=None, research_institution=None, pi_email=None,
                keywords=None, db_path=None):
    """Create a new SBIR/STTR tracking record with linked proposal and opportunity.

    Args:
        program_type: 'sbir' or 'sttr'.
        phase: 'phase_1', 'phase_2', 'phase_3', or 'direct_to_phase_2'.
        agency: Sponsoring federal agency (e.g. 'DoD', 'NSF').
        topic_number: Agency topic/solicitation number.
        topic_title: Descriptive topic title.
        pi_name: Principal Investigator name.
        research_institution: Required for STTR.
        pi_email: PI contact email.
        keywords: Comma-separated or list of keywords.
        db_path: Optional database path override.

    Returns:
        dict with sbir_id, proposal_id, opportunity_id, and record details.
    """
    if program_type not in VALID_PROGRAM_TYPES:
        return {"error": f"Invalid program_type '{program_type}'. Must be one of {VALID_PROGRAM_TYPES}"}
    if phase not in VALID_PHASES:
        return {"error": f"Invalid phase '{phase}'. Must be one of {VALID_PHASES}"}
    if program_type == "sttr" and not research_institution:
        return {"error": "STTR proposals require a research_institution partnership"}

    sbir_id = _sbir_id()
    prop_id = _prop_id()
    opp_id = _opp_id()
    now = _now()

    opp_title = f"{program_type.upper()} {phase.replace('_', ' ').title()}"
    if topic_number:
        opp_title += f" — {topic_number}"
    if topic_title:
        opp_title += f": {topic_title}"

    prop_title = opp_title

    defaults = _PHASE_DEFAULTS.get(phase, {})
    keywords_json = _serialize_list(keywords)

    conn = _get_db(db_path)
    try:
        # Create linked opportunity
        conn.execute(
            "INSERT INTO opportunities (id, title, agency, solicitation_number, "
            "opportunity_type, status, discovered_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (opp_id, opp_title, agency, topic_number or "",
             "solicitation", "capture", now, now),
        )

        # Create linked proposal
        conn.execute(
            "INSERT INTO proposals (id, opportunity_id, title, status, classification, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (prop_id, opp_id, prop_title, "draft", "CUI // SP-PROPIN", now, now),
        )

        # Create SBIR record
        conn.execute(
            "INSERT INTO sbir_proposals (id, proposal_id, opportunity_id, program_type, "
            "phase, agency, topic_number, topic_title, research_institution, pi_name, "
            "pi_email, award_amount, period_of_performance_months, keywords, "
            "status, classification, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sbir_id, prop_id, opp_id, program_type, phase, agency,
             topic_number, topic_title, research_institution, pi_name, pi_email,
             defaults.get("amount"), defaults.get("months"),
             keywords_json, "drafting", "CUI // SP-PROPIN", now, now),
        )

        _audit(conn, "sbir.create", f"Created {program_type.upper()} {phase} for {agency}",
               "sbir_proposals", sbir_id, json.dumps({"proposal_id": prop_id, "agency": agency}))

        conn.commit()

        return {
            "sbir_id": sbir_id,
            "proposal_id": prop_id,
            "opportunity_id": opp_id,
            "program_type": program_type,
            "phase": phase,
            "agency": agency,
            "topic_number": topic_number,
            "topic_title": topic_title,
            "pi_name": pi_name,
            "research_institution": research_institution,
            "status": "drafting",
            "award_amount": defaults.get("amount"),
            "period_of_performance_months": defaults.get("months"),
            "created_at": now,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# update_sbir
# ---------------------------------------------------------------------------

def update_sbir(sbir_id, updates, db_path=None):
    """Update SBIR record fields with status transition validation.

    Args:
        sbir_id: SBIR record identifier.
        updates: dict of field -> new value.
        db_path: Optional database path override.

    Returns:
        dict with updated record.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM sbir_proposals WHERE id = ?", (sbir_id,)).fetchone()
        if not row:
            return {"error": f"SBIR record '{sbir_id}' not found"}

        record = _row_to_dict(row)

        # Validate status transition if status is being changed
        if "status" in updates:
            new_status = updates["status"]
            if new_status not in VALID_STATUSES:
                return {"error": f"Invalid status '{new_status}'. Must be one of {VALID_STATUSES}"}
            current_status = record["status"]
            allowed = _STATUS_TRANSITIONS.get(current_status, set())
            if new_status != current_status and new_status not in allowed:
                return {
                    "error": f"Cannot transition from '{current_status}' to '{new_status}'. "
                             f"Allowed transitions: {sorted(allowed) if allowed else 'none (terminal state)'}"
                }

        # Validate STTR research institution constraint
        if record["program_type"] == "sttr" and "research_institution" in updates:
            if not updates["research_institution"]:
                return {"error": "STTR proposals require a research_institution — cannot clear it"}

        # Validate TRL range
        for trl_field in ("trl_current", "trl_target"):
            if trl_field in updates:
                val = updates[trl_field]
                if val is not None and (not isinstance(val, int) or val < 1 or val > 9):
                    return {"error": f"{trl_field} must be an integer between 1 and 9"}

        # Build update query
        allowed_fields = {
            "topic_number", "topic_title", "research_institution", "pi_name", "pi_email",
            "technical_abstract", "innovation_description", "commercialization_plan",
            "trl_current", "trl_target", "award_amount", "award_date",
            "period_of_performance_months", "sba_company_id", "sba_proposal_id",
            "status", "phase_1_contract_id", "keywords",
        }

        set_clauses = []
        params = []
        for field, value in updates.items():
            if field not in allowed_fields:
                continue
            if field == "keywords":
                value = _serialize_list(value)
            set_clauses.append(f"{field} = ?")
            params.append(value)

        if not set_clauses:
            return {"error": "No valid fields to update"}

        set_clauses.append("updated_at = ?")
        params.append(_now())
        params.append(sbir_id)

        conn.execute(
            f"UPDATE sbir_proposals SET {', '.join(set_clauses)} WHERE id = ?",
            params,
        )

        _audit(conn, "sbir.update", f"Updated {sbir_id}: {list(updates.keys())}",
               "sbir_proposals", sbir_id, json.dumps(updates, default=str))

        conn.commit()

        updated = conn.execute("SELECT * FROM sbir_proposals WHERE id = ?", (sbir_id,)).fetchone()
        result = _row_to_dict(updated)
        result["keywords"] = _parse_json_field(result.get("keywords"))
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# get_sbir
# ---------------------------------------------------------------------------

def get_sbir(sbir_id, db_path=None):
    """Get SBIR record enriched with linked proposal status, section count, and compliance.

    Args:
        sbir_id: SBIR record identifier.
        db_path: Optional database path override.

    Returns:
        dict with full SBIR record and enrichment data.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM sbir_proposals WHERE id = ?", (sbir_id,)).fetchone()
        if not row:
            return {"error": f"SBIR record '{sbir_id}' not found"}

        result = _row_to_dict(row)
        result["keywords"] = _parse_json_field(result.get("keywords"))

        # Enrich with linked proposal data
        prop_id = result.get("proposal_id")
        if prop_id:
            prop_row = conn.execute(
                "SELECT id, status, version, win_themes, due_date FROM proposals WHERE id = ?",
                (prop_id,),
            ).fetchone()
            if prop_row:
                result["proposal_status"] = prop_row["status"]
                result["proposal_version"] = prop_row["version"]
                result["proposal_due_date"] = prop_row["due_date"]

            section_count = conn.execute(
                "SELECT COUNT(*) AS cnt FROM proposal_sections WHERE proposal_id = ?",
                (prop_id,),
            ).fetchone()
            result["section_count"] = section_count["cnt"] if section_count else 0

            # Section completion breakdown
            status_rows = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM proposal_sections "
                "WHERE proposal_id = ? GROUP BY status",
                (prop_id,),
            ).fetchall()
            result["section_status"] = {r["status"]: r["cnt"] for r in status_rows}

        # Enrich with opportunity data
        opp_id = result.get("opportunity_id")
        if opp_id:
            opp_row = conn.execute(
                "SELECT id, status, response_deadline, estimated_value_low, estimated_value_high "
                "FROM opportunities WHERE id = ?",
                (opp_id,),
            ).fetchone()
            if opp_row:
                result["opportunity_status"] = opp_row["status"]
                result["response_deadline"] = opp_row["response_deadline"]

        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# list_sbir
# ---------------------------------------------------------------------------

def list_sbir(program_type=None, phase=None, agency=None, status=None, db_path=None):
    """List SBIR/STTR proposals with optional filters.

    Args:
        program_type: Filter by 'sbir' or 'sttr'.
        phase: Filter by phase.
        agency: Filter by sponsoring agency.
        status: Filter by status.
        db_path: Optional database path override.

    Returns:
        dict with list of matching SBIR records and count.
    """
    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM sbir_proposals WHERE 1=1"
        params = []

        if program_type:
            query += " AND program_type = ?"
            params.append(program_type)
        if phase:
            query += " AND phase = ?"
            params.append(phase)
        if agency:
            query += " AND agency = ?"
            params.append(agency)
        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY created_at DESC"
        rows = conn.execute(query, params).fetchall()

        records = []
        for r in rows:
            d = _row_to_dict(r)
            d["keywords"] = _parse_json_field(d.get("keywords"))
            records.append(d)

        return {
            "count": len(records),
            "filters": {
                "program_type": program_type,
                "phase": phase,
                "agency": agency,
                "status": status,
            },
            "records": records,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# phase_transition
# ---------------------------------------------------------------------------

def phase_transition(sbir_id, new_phase, db_path=None):
    """Transition an SBIR/STTR proposal to the next phase.

    Creates a new SBIR record for the new phase while preserving the
    original record.  Phase I -> Phase II requires status 'awarded' or
    'phase_2_invited'.  The new record links back via phase_1_contract_id.

    Args:
        sbir_id: Current SBIR record identifier.
        new_phase: Target phase ('phase_2' or 'phase_3').
        db_path: Optional database path override.

    Returns:
        dict with new SBIR record details and transition metadata.
    """
    if new_phase not in VALID_PHASES:
        return {"error": f"Invalid target phase '{new_phase}'. Must be one of {VALID_PHASES}"}

    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM sbir_proposals WHERE id = ?", (sbir_id,)).fetchone()
        if not row:
            return {"error": f"SBIR record '{sbir_id}' not found"}

        record = _row_to_dict(row)
        current_phase = record["phase"]

        # Validate phase transition is allowed
        allowed_phases = _PHASE_TRANSITIONS.get(current_phase, set())
        if new_phase not in allowed_phases:
            return {
                "error": f"Cannot transition from '{current_phase}' to '{new_phase}'. "
                         f"Allowed: {sorted(allowed_phases) if allowed_phases else 'none (terminal phase)'}"
            }

        # Validate status prerequisite
        required_statuses = _PHASE_TRANSITION_REQUIRED_STATUS.get(current_phase, set())
        if required_statuses and record["status"] not in required_statuses:
            return {
                "error": f"Phase transition requires status in {sorted(required_statuses)}, "
                         f"but current status is '{record['status']}'"
            }

        # Create new records for the new phase
        new_sbir_id = _sbir_id()
        new_prop_id = _prop_id()
        new_opp_id = _opp_id()
        now = _now()
        defaults = _PHASE_DEFAULTS.get(new_phase, {})

        # Build title for new phase
        opp_title = f"{record['program_type'].upper()} {new_phase.replace('_', ' ').title()}"
        if record.get("topic_number"):
            opp_title += f" — {record['topic_number']}"
        if record.get("topic_title"):
            opp_title += f": {record['topic_title']}"

        # Determine contract linkage
        phase_1_ref = sbir_id if current_phase in ("phase_1", "direct_to_phase_2") else record.get("phase_1_contract_id")

        # Create new opportunity
        conn.execute(
            "INSERT INTO opportunities (id, title, agency, solicitation_number, "
            "opportunity_type, status, discovered_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_opp_id, opp_title, record["agency"], record.get("topic_number") or "",
             "solicitation", "capture", now, now),
        )

        # Create new proposal
        conn.execute(
            "INSERT INTO proposals (id, opportunity_id, title, status, classification, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_prop_id, new_opp_id, opp_title, "draft", "CUI // SP-PROPIN", now, now),
        )

        # Create new SBIR record for the next phase
        conn.execute(
            "INSERT INTO sbir_proposals (id, proposal_id, opportunity_id, program_type, "
            "phase, agency, topic_number, topic_title, research_institution, pi_name, "
            "pi_email, trl_current, trl_target, award_amount, period_of_performance_months, "
            "sba_company_id, sba_proposal_id, keywords, status, phase_1_contract_id, "
            "classification, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_sbir_id, new_prop_id, new_opp_id, record["program_type"],
             new_phase, record["agency"], record.get("topic_number"), record.get("topic_title"),
             record.get("research_institution"), record.get("pi_name"), record.get("pi_email"),
             record.get("trl_current"), record.get("trl_target"),
             defaults.get("amount"), defaults.get("months"),
             record.get("sba_company_id"), record.get("sba_proposal_id"),
             record.get("keywords"), "drafting", phase_1_ref,
             "CUI // SP-PROPIN", now, now),
        )

        _audit(conn, "sbir.phase_transition",
               f"Transitioned {sbir_id} from {current_phase} to {new_phase}",
               "sbir_proposals", new_sbir_id,
               json.dumps({"previous_id": sbir_id, "from_phase": current_phase, "to_phase": new_phase}))

        conn.commit()

        return {
            "new_sbir_id": new_sbir_id,
            "previous_sbir_id": sbir_id,
            "new_proposal_id": new_prop_id,
            "new_opportunity_id": new_opp_id,
            "program_type": record["program_type"],
            "from_phase": current_phase,
            "to_phase": new_phase,
            "agency": record["agency"],
            "phase_1_contract_id": phase_1_ref,
            "status": "drafting",
            "award_amount": defaults.get("amount"),
            "period_of_performance_months": defaults.get("months"),
            "transitioned_at": now,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# sbir_checklist
# ---------------------------------------------------------------------------

def sbir_checklist(sbir_id, db_path=None):
    """Generate a submission checklist with completion status.

    Phase I checklist: Cover sheet, technical proposal (25 pages), budget,
    SBA company registry, commercialization record.
    Phase II additions: Phase I results, updated commercialization plan,
    Phase I deliverables complete.
    STTR additions: Research institution letter of commitment, allocation
    of work (min 30% SB, min 40% RI).

    Args:
        sbir_id: SBIR record identifier.
        db_path: Optional database path override.

    Returns:
        dict with checklist_items, completion_pct, and summary.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM sbir_proposals WHERE id = ?", (sbir_id,)).fetchone()
        if not row:
            return {"error": f"SBIR record '{sbir_id}' not found"}

        record = _row_to_dict(row)
        phase = record["phase"]
        program_type = record["program_type"]
        items = []

        # --- Phase I / Direct-to-Phase-II base checklist ---
        items.append({
            "item": "Cover Sheet / SF 424 (R&R)",
            "required": True,
            "status": "complete" if record.get("pi_name") and record.get("agency") else "missing",
            "notes": "Agency, PI name, topic number required" if not record.get("pi_name") else None,
        })

        items.append({
            "item": "Technical Proposal (max 25 pages for Phase I)",
            "required": True,
            "status": "complete" if record.get("technical_abstract") else "missing",
            "notes": "Must include innovation, technical objectives, work plan, and key personnel",
        })

        items.append({
            "item": "Technical Abstract",
            "required": True,
            "status": "complete" if record.get("technical_abstract") else "missing",
            "notes": "200 words max, unclassified, publishable",
        })

        items.append({
            "item": "Innovation Description",
            "required": True,
            "status": "complete" if record.get("innovation_description") else "missing",
            "notes": "Describe the innovation and how it differs from existing solutions",
        })

        items.append({
            "item": "Budget Justification",
            "required": True,
            "status": "complete" if record.get("award_amount") else "missing",
            "notes": f"Phase I typical: $150K/6mo" if phase == "phase_1" else f"Phase II typical: $1M/24mo",
        })

        items.append({
            "item": "SBA Company Registry (firm profile)",
            "required": True,
            "status": "complete" if record.get("sba_company_id") else "missing",
            "notes": "Register at SBIR.gov company registry before submission",
        })

        items.append({
            "item": "Commercialization Plan / Strategy",
            "required": True,
            "status": "complete" if record.get("commercialization_plan") else "missing",
            "notes": "Phase I: brief strategy; Phase II: detailed plan with market analysis",
        })

        items.append({
            "item": "PI Biographical Sketch",
            "required": True,
            "status": "complete" if record.get("pi_name") and record.get("pi_email") else "missing",
            "notes": "NIH biosketch or NSF-style CV for Principal Investigator",
        })

        items.append({
            "item": "Current and Pending Support",
            "required": True,
            "status": "incomplete",
            "notes": "List all current and pending federal support for PI and key personnel",
        })

        items.append({
            "item": "Facilities & Equipment Description",
            "required": True,
            "status": "incomplete",
            "notes": "Describe available facilities, equipment, and any special requirements",
        })

        # --- Phase II additional items ---
        if phase in ("phase_2", "direct_to_phase_2"):
            items.append({
                "item": "Phase I Final Report / Results",
                "required": True,
                "status": "complete" if record.get("phase_1_contract_id") else "missing",
                "notes": "Summarize Phase I feasibility results and technical achievements",
            })

            items.append({
                "item": "Updated Commercialization Plan (detailed)",
                "required": True,
                "status": "complete" if record.get("commercialization_plan") else "missing",
                "notes": "Market analysis, revenue projections, partnership letters, transition plan",
            })

            items.append({
                "item": "Phase I Deliverables Completion Certification",
                "required": True,
                "status": "complete" if record.get("phase_1_contract_id") else "missing",
                "notes": "Certify all Phase I deliverables and milestones were completed",
            })

            items.append({
                "item": "Commercialization Achievement Record",
                "required": True,
                "status": "incomplete",
                "notes": "Revenue from prior SBIR/STTR awards, Phase III transitions, licenses",
            })

        # --- STTR additional items ---
        if program_type == "sttr":
            items.append({
                "item": "Research Institution Letter of Commitment",
                "required": True,
                "status": "complete" if record.get("research_institution") else "missing",
                "notes": f"From {record.get('research_institution', 'TBD')} — confirming partnership",
            })

            items.append({
                "item": f"Allocation of Work — min {_STTR_MIN_SB_PCT}% small business",
                "required": True,
                "status": "incomplete",
                "notes": f"Small business must perform at least {_STTR_MIN_SB_PCT}% of the work",
            })

            items.append({
                "item": f"Allocation of Work — min {_STTR_MIN_RI_PCT}% research institution",
                "required": True,
                "status": "incomplete",
                "notes": f"Research institution must perform at least {_STTR_MIN_RI_PCT}% of the work",
            })

            items.append({
                "item": "Cooperative R&D Agreement (CRADA) or equivalent",
                "required": True,
                "status": "incomplete",
                "notes": "IP rights agreement between small business and research institution",
            })

        # Compute completion
        required_items = [i for i in items if i["required"]]
        complete_items = [i for i in required_items if i["status"] == "complete"]
        total = len(required_items)
        done = len(complete_items)
        completion_pct = round(done / total * 100, 1) if total > 0 else 0.0

        return {
            "sbir_id": sbir_id,
            "program_type": program_type,
            "phase": phase,
            "checklist_items": items,
            "total_required": total,
            "completed": done,
            "missing": total - done,
            "completion_pct": completion_pct,
            "ready_to_submit": completion_pct == 100.0,
            "checked_at": _now(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# trl_assessment
# ---------------------------------------------------------------------------

def trl_assessment(sbir_id, db_path=None):
    """Technology Readiness Level gap analysis.

    Compares current TRL against phase expectations and target TRL,
    provides gap analysis and recommendations for advancement.

    Args:
        sbir_id: SBIR record identifier.
        db_path: Optional database path override.

    Returns:
        dict with current_trl, target_trl, gap, phase_expected_trl, recommendations.
    """
    _TRL_DESCRIPTIONS = {
        1: "Basic principles observed",
        2: "Technology concept formulated",
        3: "Experimental proof of concept",
        4: "Technology validated in lab",
        5: "Technology validated in relevant environment",
        6: "System/subsystem model demonstrated in relevant environment",
        7: "System prototype demonstrated in operational environment",
        8: "System complete and qualified",
        9: "Actual system proven in operational environment",
    }

    _TRL_RECOMMENDATIONS = {
        (1, 2): ["Conduct literature review and identify application domains",
                  "Develop initial concept and analytical studies"],
        (2, 3): ["Build proof-of-concept prototypes or simulations",
                  "Validate core algorithms or processes in controlled experiments"],
        (3, 4): ["Conduct lab-scale testing with representative inputs",
                  "Validate component integration in a controlled environment"],
        (4, 5): ["Test integrated system components in a relevant environment",
                  "Develop system/subsystem specifications and interfaces"],
        (5, 6): ["Build and test a high-fidelity model in a relevant environment",
                  "Address performance requirements and environmental factors"],
        (6, 7): ["Build and demonstrate a prototype in an operational environment",
                  "Conduct user acceptance testing and gather feedback"],
        (7, 8): ["Complete system qualification testing",
                  "Resolve all manufacturing and integration issues",
                  "Finalize documentation and training materials"],
        (8, 9): ["Deploy system in its intended operational environment",
                  "Complete operational testing and evaluation (OT&E)",
                  "Establish sustainment and maintenance procedures"],
    }

    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM sbir_proposals WHERE id = ?", (sbir_id,)).fetchone()
        if not row:
            return {"error": f"SBIR record '{sbir_id}' not found"}

        record = _row_to_dict(row)
        phase = record["phase"]
        current_trl = record.get("trl_current")
        target_trl = record.get("trl_target")
        phase_exp = _PHASE_TRL_EXPECTATIONS.get(phase, {})

        # Default TRL assumptions if not set
        if current_trl is None:
            current_trl = 1 if phase == "phase_1" else 3
        if target_trl is None:
            target_trl = phase_exp.get("max", current_trl + 2)

        gap = target_trl - current_trl
        phase_gap = phase_exp.get("min", target_trl) - current_trl

        # Build recommendations
        recommendations = []
        if gap > 0:
            for step in range(current_trl, target_trl):
                key = (step, step + 1)
                recs = _TRL_RECOMMENDATIONS.get(key, [])
                for r in recs:
                    recommendations.append(f"TRL {step}->{step+1}: {r}")

        # Phase alignment assessment
        if current_trl >= phase_exp.get("min", 99):
            alignment = "on_track"
            alignment_note = f"Current TRL {current_trl} meets or exceeds Phase expectations ({phase_exp.get('min')}-{phase_exp.get('max')})"
        elif current_trl >= phase_exp.get("min", 99) - 1:
            alignment = "at_risk"
            alignment_note = f"Current TRL {current_trl} is close but below Phase expectation minimum ({phase_exp.get('min')})"
        else:
            alignment = "behind"
            alignment_note = f"Current TRL {current_trl} is significantly below Phase expectation ({phase_exp.get('min')}-{phase_exp.get('max')})"

        return {
            "sbir_id": sbir_id,
            "program_type": record["program_type"],
            "phase": phase,
            "current_trl": current_trl,
            "current_trl_description": _TRL_DESCRIPTIONS.get(current_trl, "Unknown"),
            "target_trl": target_trl,
            "target_trl_description": _TRL_DESCRIPTIONS.get(target_trl, "Unknown"),
            "gap": gap,
            "phase_expected_trl": phase_exp,
            "phase_alignment": alignment,
            "phase_alignment_note": alignment_note,
            "recommendations": recommendations,
            "assessed_at": _now(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# sbir_dashboard_data
# ---------------------------------------------------------------------------

def sbir_dashboard_data(db_path=None):
    """Dashboard summary: counts by phase, win rates, funding totals, agency breakdown.

    Returns:
        dict with by_phase, by_agency, win_rate, total_funding, pipeline data.
    """
    conn = _get_db(db_path)
    try:
        all_rows = conn.execute("SELECT * FROM sbir_proposals ORDER BY created_at DESC").fetchall()

        if not all_rows:
            return {
                "total_count": 0,
                "by_phase": {},
                "by_agency": {},
                "by_status": {},
                "by_program_type": {},
                "win_rate": {"submitted": 0, "selected_or_awarded": 0, "rate_pct": 0.0},
                "total_funding": {"awarded": 0.0, "pending": 0.0},
                "pipeline": [],
            }

        records = [_row_to_dict(r) for r in all_rows]

        # By phase
        by_phase = defaultdict(int)
        for r in records:
            if r["status"] not in ("not_selected", "withdrawn"):
                by_phase[r["phase"]] += 1

        # By agency
        by_agency = defaultdict(int)
        for r in records:
            by_agency[r["agency"]] += 1

        # By status
        by_status = defaultdict(int)
        for r in records:
            by_status[r["status"]] += 1

        # By program type
        by_program_type = defaultdict(int)
        for r in records:
            by_program_type[r["program_type"]] += 1

        # Win / selection rates
        submitted = sum(1 for r in records if r["status"] in (
            "submitted", "under_review", "selected", "awarded",
            "not_selected", "phase_2_invited"))
        won = sum(1 for r in records if r["status"] in ("selected", "awarded", "phase_2_invited"))
        win_rate = round(won / submitted * 100, 1) if submitted > 0 else 0.0

        # Funding
        awarded_funding = sum(r.get("award_amount") or 0 for r in records if r["status"] == "awarded")
        pending_funding = sum(r.get("award_amount") or 0 for r in records
                             if r["status"] in ("drafting", "submitted", "under_review", "selected"))

        # Pipeline: active proposals needing attention
        pipeline = []
        for r in records:
            if r["status"] in ("drafting", "submitted", "under_review", "selected", "phase_2_invited"):
                pipeline.append({
                    "sbir_id": r["id"],
                    "program_type": r["program_type"],
                    "phase": r["phase"],
                    "agency": r["agency"],
                    "topic_number": r.get("topic_number"),
                    "status": r["status"],
                    "pi_name": r.get("pi_name"),
                    "award_amount": r.get("award_amount"),
                })

        return {
            "total_count": len(records),
            "by_phase": dict(by_phase),
            "by_agency": dict(by_agency),
            "by_status": dict(by_status),
            "by_program_type": dict(by_program_type),
            "win_rate": {
                "submitted": submitted,
                "selected_or_awarded": won,
                "rate_pct": win_rate,
            },
            "total_funding": {
                "awarded": awarded_funding,
                "pending": pending_funding,
            },
            "pipeline": pipeline,
            "generated_at": _now(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# search_topics
# ---------------------------------------------------------------------------

def search_topics(keywords, agency=None, db_path=None):
    """Search for SBIR/STTR topics by keywords.

    Attempts to query SBIR.gov API if requests is available; falls back
    to searching internal sbir_proposals records for matching topics.

    Args:
        keywords: Comma-separated keyword string or list.
        agency: Optional agency filter.
        db_path: Optional database path override.

    Returns:
        dict with matching topics and source information.
    """
    if isinstance(keywords, str):
        keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
    else:
        keyword_list = list(keywords)

    results = []
    source = "internal"

    # Attempt SBIR.gov API search
    if requests is not None:
        try:
            api_url = "https://www.sbir.gov/api/solicitations.json"
            params = {"keyword": " ".join(keyword_list)}
            if agency:
                params["agency"] = agency
            resp = requests.get(api_url, params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                topics = data if isinstance(data, list) else data.get("results", data.get("data", []))
                for topic in topics[:25]:  # cap at 25 results
                    results.append({
                        "topic_number": topic.get("solicitation_number") or topic.get("topic_number", ""),
                        "title": topic.get("solicitation_title") or topic.get("title", ""),
                        "agency": topic.get("agency", ""),
                        "program": topic.get("program", ""),
                        "open_date": topic.get("open_date", ""),
                        "close_date": topic.get("close_date", ""),
                        "description": (topic.get("abstract") or topic.get("description") or "")[:500],
                        "url": topic.get("solicitation_url") or topic.get("url", ""),
                    })
                source = "sbir.gov"
        except Exception:
            pass  # fall through to internal search

    # Internal fallback: search existing SBIR records
    if not results:
        conn = _get_db(db_path)
        try:
            all_rows = conn.execute(
                "SELECT id, topic_number, topic_title, agency, program_type, phase, "
                "keywords, status FROM sbir_proposals ORDER BY created_at DESC"
            ).fetchall()

            for r in all_rows:
                row_dict = _row_to_dict(r)
                searchable = " ".join(filter(None, [
                    row_dict.get("topic_number", ""),
                    row_dict.get("topic_title", ""),
                    row_dict.get("agency", ""),
                    " ".join(_parse_json_field(row_dict.get("keywords"))),
                ])).lower()

                if any(kw.lower() in searchable for kw in keyword_list):
                    if agency and row_dict.get("agency") != agency:
                        continue
                    results.append({
                        "topic_number": row_dict.get("topic_number"),
                        "title": row_dict.get("topic_title"),
                        "agency": row_dict.get("agency"),
                        "program": row_dict.get("program_type"),
                        "phase": row_dict.get("phase"),
                        "status": row_dict.get("status"),
                        "sbir_id": row_dict.get("id"),
                    })
            source = "internal"
        finally:
            conn.close()

    return {
        "keywords": keyword_list,
        "agency_filter": agency,
        "source": source,
        "count": len(results),
        "topics": results,
        "searched_at": _now(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="SBIR/STTR Proposal Manager — lifecycle tracking, phase transitions, "
                    "submission checklists, TRL assessment, and dashboard analytics."
    )
    parser.add_argument("--create", action="store_true",
                        help="Create a new SBIR/STTR tracking record")
    parser.add_argument("--update", action="store_true",
                        help="Update an existing SBIR record")
    parser.add_argument("--get", action="store_true",
                        help="Get SBIR record with enriched data")
    parser.add_argument("--list", action="store_true",
                        help="List SBIR/STTR proposals with filters")
    parser.add_argument("--phase-transition", action="store_true",
                        help="Transition to next SBIR phase")
    parser.add_argument("--checklist", action="store_true",
                        help="Generate submission checklist")
    parser.add_argument("--trl", action="store_true",
                        help="TRL gap analysis and assessment")
    parser.add_argument("--dashboard", action="store_true",
                        help="Dashboard summary data")
    parser.add_argument("--search-topics", action="store_true",
                        help="Search SBIR.gov topics by keywords")

    # Parameters
    parser.add_argument("--sbir-id", help="SBIR record identifier")
    parser.add_argument("--type", dest="program_type", choices=["sbir", "sttr"],
                        help="Program type (sbir or sttr)")
    parser.add_argument("--phase", choices=list(VALID_PHASES),
                        help="SBIR phase")
    parser.add_argument("--new-phase", choices=list(VALID_PHASES),
                        help="Target phase for --phase-transition")
    parser.add_argument("--agency", help="Sponsoring federal agency")
    parser.add_argument("--topic", help="Topic/solicitation number")
    parser.add_argument("--topic-title", help="Topic title")
    parser.add_argument("--pi", help="Principal Investigator name")
    parser.add_argument("--pi-email", help="PI email address")
    parser.add_argument("--institution", help="Research institution (required for STTR)")
    parser.add_argument("--keywords", help="Comma-separated keywords")
    parser.add_argument("--status", choices=list(VALID_STATUSES),
                        help="Status filter or update value")

    # Output
    parser.add_argument("--db-path", help="Override database path")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()
    db = args.db_path or None
    result = {}

    if args.create:
        if not args.program_type:
            print("Error: --type is required for --create", file=sys.stderr)
            sys.exit(1)
        if not args.phase:
            print("Error: --phase is required for --create", file=sys.stderr)
            sys.exit(1)
        if not args.agency:
            print("Error: --agency is required for --create", file=sys.stderr)
            sys.exit(1)
        result = create_sbir(
            program_type=args.program_type,
            phase=args.phase,
            agency=args.agency,
            topic_number=args.topic,
            topic_title=args.topic_title,
            pi_name=args.pi,
            pi_email=args.pi_email,
            research_institution=args.institution,
            keywords=args.keywords,
            db_path=db,
        )

    elif args.update:
        if not args.sbir_id:
            print("Error: --sbir-id is required for --update", file=sys.stderr)
            sys.exit(1)
        updates = {}
        if args.status:
            updates["status"] = args.status
        if args.pi:
            updates["pi_name"] = args.pi
        if args.pi_email:
            updates["pi_email"] = args.pi_email
        if args.institution:
            updates["research_institution"] = args.institution
        if args.topic:
            updates["topic_number"] = args.topic
        if args.topic_title:
            updates["topic_title"] = args.topic_title
        if args.keywords:
            updates["keywords"] = args.keywords
        if not updates:
            print("Error: no update fields provided", file=sys.stderr)
            sys.exit(1)
        result = update_sbir(args.sbir_id, updates, db_path=db)

    elif args.get:
        if not args.sbir_id:
            print("Error: --sbir-id is required for --get", file=sys.stderr)
            sys.exit(1)
        result = get_sbir(args.sbir_id, db_path=db)

    elif args.list:
        result = list_sbir(
            program_type=args.program_type,
            phase=args.phase,
            agency=args.agency,
            status=args.status,
            db_path=db,
        )

    elif args.phase_transition:
        if not args.sbir_id:
            print("Error: --sbir-id is required for --phase-transition", file=sys.stderr)
            sys.exit(1)
        if not args.new_phase:
            print("Error: --new-phase is required for --phase-transition", file=sys.stderr)
            sys.exit(1)
        result = phase_transition(args.sbir_id, args.new_phase, db_path=db)

    elif args.checklist:
        if not args.sbir_id:
            print("Error: --sbir-id is required for --checklist", file=sys.stderr)
            sys.exit(1)
        result = sbir_checklist(args.sbir_id, db_path=db)

    elif args.trl:
        if not args.sbir_id:
            print("Error: --sbir-id is required for --trl", file=sys.stderr)
            sys.exit(1)
        result = trl_assessment(args.sbir_id, db_path=db)

    elif args.dashboard:
        result = sbir_dashboard_data(db_path=db)

    elif args.search_topics:
        if not args.keywords:
            print("Error: --keywords is required for --search-topics", file=sys.stderr)
            sys.exit(1)
        result = search_topics(args.keywords, agency=args.agency, db_path=db)

    else:
        parser.print_help()
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
