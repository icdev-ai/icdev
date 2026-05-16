#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN (Proprietary Business Information)
# Distribution: D
# POC: GovProposal System Administrator
"""Contract Performance Management Portal (CPMP).

Post-award contract tracking: CDRLs (DD Form 1423), SOW obligations,
deliverable milestones, automated reminders, and CPARS early warning.

Closes the Shipley lifecycle loop:
  RFP shredder → proposal → award → delivery tracking →
  CPARS early warning → knowledge base → next proposal

Functions:
    activate_contract      — Create contract from awarded proposal, auto-import
    get_contract           — Get contract with CDRLs, obligations, stats
    list_contracts         — List all contracts with summary counts
    update_cdrl_status     — Update CDRL delivery/acceptance status
    update_obligation_status — Update SOW obligation compliance status
    generate_reminders     — Generate reminders at 30/14/7/1 day intervals
    get_pending_reminders  — Get all pending reminders across contracts
    acknowledge_reminder   — Mark reminder as acknowledged
    cpars_risk_score       — Calculate CPARS risk from delivery performance
    cpars_summary          — Generate CPARS preparation summary
    check_overdue          — Scan all contracts for overdue items
    contract_dashboard_data — Aggregate stats for dashboard home card

Usage:
    python tools/delivery/contract_manager.py --activate --proposal-id PROP-123 \\
        --contract-number W911QX-26-C-0001 --pop-start 2026-04-01 --pop-end 2027-03-31 --json
    python tools/delivery/contract_manager.py --get --contract-id CTR-abc --json
    python tools/delivery/contract_manager.py --list [--status active] --json
    python tools/delivery/contract_manager.py --update-cdrl --cdrl-id CDRL-abc \\
        --status delivered --delivery-date 2026-05-01 --json
    python tools/delivery/contract_manager.py --update-obligation --obligation-id OBL-abc \\
        --status compliant --evidence "See SOW 3.2" --json
    python tools/delivery/contract_manager.py --reminders --json
    python tools/delivery/contract_manager.py --check-overdue --json
    python tools/delivery/contract_manager.py --cpars-risk --contract-id CTR-abc --json
    python tools/delivery/contract_manager.py --cpars-summary --contract-id CTR-abc --json
"""

import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))

# -- Frequency to days mapping for recurring CDRLs --
_FREQ_DAYS = {
    "MTHLY": 30,
    "QRTLY": 90,
    "SEMI": 180,
    "ANNLY": 365,
    "DALI": 1,
}

# -- Reminder intervals (days before due) --
_REMINDER_INTERVALS = [30, 14, 7, 1]

# -- Severity mapping --
_SEVERITY_MAP = {30: "info", 14: "info", 7: "warning", 1: "urgent"}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _uid(prefix="CTR"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_db(db_path=None):
    path = db_path or str(DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _parse_xrefs(xref_text):
    """Parse cross_references JSON safely."""
    if not xref_text:
        return []
    try:
        return json.loads(xref_text)
    except (json.JSONDecodeError, TypeError):
        return []


def _detect_frequency(text):
    """Detect CDRL frequency from requirement text."""
    text_lower = text.lower()
    if "monthly" in text_lower or "each month" in text_lower:
        return "MTHLY"
    if "quarterly" in text_lower or "every quarter" in text_lower:
        return "QRTLY"
    if "semi-annual" in text_lower or "twice a year" in text_lower:
        return "SEMI"
    if "annual" in text_lower or "yearly" in text_lower:
        return "ANNLY"
    if "daily" in text_lower:
        return "DALI"
    if "as required" in text_lower or "as needed" in text_lower:
        return "AS_REQ"
    return "ONE/R"


def _next_due_from_frequency(pop_start, frequency, offset_days=None):
    """Calculate next due date from POP start and frequency."""
    try:
        start = datetime.strptime(pop_start, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None

    if offset_days:
        return (start + timedelta(days=offset_days)).strftime("%Y-%m-%d")

    days = _FREQ_DAYS.get(frequency)
    if days:
        return (start + timedelta(days=days)).strftime("%Y-%m-%d")

    return None


# ── Core Functions ───────────────────────────────────────────────────────


def activate_contract(proposal_id, contract_number=None, pop_start=None,
                      pop_end=None, db_path=None):
    """Create a contract from an awarded proposal, auto-import CDRLs and obligations.

    Reads shredded_requirements to import:
      - Section H CDRLs → contract_cdrls
      - Section C shall/must/will → contract_obligations (type=sow)
      - Section F deliverables → contract_obligations (type=deliverable)
    """
    conn = _get_db(db_path)
    try:
        # Validate proposal exists and is awarded
        prop = conn.execute(
            "SELECT id, title, opportunity_id, status FROM proposals WHERE id = ?",
            (proposal_id,)
        ).fetchone()
        if not prop:
            return {"error": f"Proposal {proposal_id} not found"}
        if prop["status"] != "awarded":
            return {"error": f"Proposal status is '{prop['status']}', must be 'awarded'"}

        # Get opportunity info
        opp = None
        if prop["opportunity_id"]:
            opp = conn.execute(
                "SELECT id, title, agency FROM opportunities WHERE id = ?",
                (prop["opportunity_id"],)
            ).fetchone()

        contract_id = _uid("CTR")
        contract_name = prop["title"] or (opp["title"] if opp else f"Contract {contract_number}")

        conn.execute(
            "INSERT INTO contracts "
            "(id, proposal_id, opportunity_id, contract_number, contract_name, "
            "contract_type, period_of_performance_start, period_of_performance_end, "
            "status, classification, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'other', ?, ?, 'active', 'CUI // SP-PROPIN', ?, ?)",
            (contract_id, proposal_id, prop["opportunity_id"],
             contract_number, contract_name, pop_start, pop_end, _now(), _now())
        )

        # ── Auto-import from shredded_requirements ───────────────────────
        shredded = conn.execute(
            "SELECT id, requirement_text, obligation_level, source_section, "
            "cross_references FROM shredded_requirements WHERE proposal_id = ?",
            (proposal_id,)
        ).fetchall()

        cdrls_imported = 0
        obligations_imported = 0

        for req in shredded:
            xrefs = _parse_xrefs(req["cross_references"])
            section = req["source_section"]

            # Section H → CDRLs (look for cdrl cross-references)
            if section == "section_h":
                cdrl_refs = [x for x in xrefs if x.get("type") == "cdrl"]
                if cdrl_refs:
                    for cref in cdrl_refs:
                        cdrl_num = cref.get("reference", "").replace("CDRL ", "")
                        di_refs = [x for x in xrefs
                                   if x.get("type") == "form"
                                   and x.get("reference", "").startswith("DI-")]
                        di_number = di_refs[0]["reference"] if di_refs else None
                        frequency = _detect_frequency(req["requirement_text"])
                        next_due = _next_due_from_frequency(pop_start, frequency)

                        conn.execute(
                            "INSERT INTO contract_cdrls "
                            "(id, contract_id, shredded_req_id, cdrl_number, "
                            "di_number, title, frequency, next_due_date, "
                            "status, created_at, updated_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'not_due', ?, ?)",
                            (_uid("CDRL"), contract_id, req["id"], cdrl_num,
                             di_number, req["requirement_text"][:200],
                             frequency, next_due, _now(), _now())
                        )
                        cdrls_imported += 1
                else:
                    # Section H without CDRL xref → still an obligation
                    if req["obligation_level"] in ("shall", "must", "will"):
                        conn.execute(
                            "INSERT INTO contract_obligations "
                            "(id, contract_id, shredded_req_id, obligation_type, "
                            "obligation_text, obligation_level, source_section, "
                            "status, created_at, updated_at) "
                            "VALUES (?, ?, ?, 'sow', ?, ?, 'section_h', "
                            "'not_started', ?, ?)",
                            (_uid("OBL"), contract_id, req["id"],
                             req["requirement_text"], req["obligation_level"],
                             _now(), _now())
                        )
                        obligations_imported += 1

            # Section C → SOW obligations
            elif section == "section_c":
                if req["obligation_level"] in ("shall", "must", "will"):
                    conn.execute(
                        "INSERT INTO contract_obligations "
                        "(id, contract_id, shredded_req_id, obligation_type, "
                        "obligation_text, obligation_level, source_section, "
                        "status, created_at, updated_at) "
                        "VALUES (?, ?, ?, 'sow', ?, ?, 'section_c', "
                        "'not_started', ?, ?)",
                        (_uid("OBL"), contract_id, req["id"],
                         req["requirement_text"], req["obligation_level"],
                         _now(), _now())
                    )
                    obligations_imported += 1

            # Section F → Deliverable milestones
            elif section == "section_f":
                conn.execute(
                    "INSERT INTO contract_obligations "
                    "(id, contract_id, shredded_req_id, obligation_type, "
                    "obligation_text, obligation_level, source_section, "
                    "status, created_at, updated_at) "
                    "VALUES (?, ?, ?, 'deliverable', ?, ?, 'section_f', "
                    "'not_started', ?, ?)",
                    (_uid("OBL"), contract_id, req["id"],
                     req["requirement_text"],
                     req["obligation_level"] or "shall",
                     _now(), _now())
                )
                obligations_imported += 1

        conn.commit()

        # Generate initial reminders
        reminder_result = generate_reminders(contract_id, db_path)

        # Audit trail
        conn.execute(
            "INSERT INTO audit_trail (event_type, actor, action, entity_type, "
            "entity_id, details, created_at) VALUES "
            "(?, ?, ?, ?, ?, ?, ?)",
            ("contract.activated", "contract_manager", "Contract activated from proposal",
             "contract", contract_id,
             json.dumps({"proposal_id": proposal_id,
                         "cdrls_imported": cdrls_imported,
                         "obligations_imported": obligations_imported}),
             _now())
        )
        conn.commit()

        return {
            "status": "activated",
            "contract_id": contract_id,
            "contract_number": contract_number,
            "contract_name": contract_name,
            "cdrls_imported": cdrls_imported,
            "obligations_imported": obligations_imported,
            "reminders_created": reminder_result.get("reminders_created", 0),
        }
    finally:
        conn.close()


def get_contract(contract_id, db_path=None):
    """Get contract detail with CDRLs, obligations, and summary stats."""
    conn = _get_db(db_path)
    try:
        contract = conn.execute(
            "SELECT c.*, o.title as opp_title, o.agency "
            "FROM contracts c "
            "LEFT JOIN opportunities o ON c.opportunity_id = o.id "
            "WHERE c.id = ?",
            (contract_id,)
        ).fetchone()
        if not contract:
            return {"error": f"Contract {contract_id} not found"}

        cdrls = conn.execute(
            "SELECT * FROM contract_cdrls WHERE contract_id = ? "
            "ORDER BY cdrl_number",
            (contract_id,)
        ).fetchall()

        obligations = conn.execute(
            "SELECT * FROM contract_obligations WHERE contract_id = ? "
            "ORDER BY obligation_type, created_at",
            (contract_id,)
        ).fetchall()

        reminders = conn.execute(
            "SELECT * FROM deliverable_reminders "
            "WHERE contract_id = ? AND status = 'pending' "
            "ORDER BY reminder_date",
            (contract_id,)
        ).fetchall()

        # Stats
        cdrl_total = len(cdrls)
        cdrl_delivered = sum(1 for c in cdrls if c["status"] in ("delivered", "accepted"))
        cdrl_overdue = sum(1 for c in cdrls if c["status"] == "overdue")

        obl_total = len(obligations)
        obl_compliant = sum(1 for o in obligations if o["status"] == "compliant")
        obl_noncompliant = sum(1 for o in obligations if o["status"] == "non_compliant")

        return {
            "contract": dict(contract),
            "cdrls": [dict(c) for c in cdrls],
            "obligations": [dict(o) for o in obligations],
            "reminders": [dict(r) for r in reminders],
            "stats": {
                "cdrl_total": cdrl_total,
                "cdrl_delivered": cdrl_delivered,
                "cdrl_overdue": cdrl_overdue,
                "obl_total": obl_total,
                "obl_compliant": obl_compliant,
                "obl_noncompliant": obl_noncompliant,
                "pending_reminders": len(reminders),
            }
        }
    finally:
        conn.close()


def list_contracts(status=None, db_path=None):
    """List all contracts with summary counts."""
    conn = _get_db(db_path)
    try:
        query = (
            "SELECT c.*, o.title as opp_title, o.agency, "
            "(SELECT COUNT(*) FROM contract_cdrls cc WHERE cc.contract_id = c.id) as cdrl_count, "
            "(SELECT COUNT(*) FROM contract_cdrls cc WHERE cc.contract_id = c.id "
            " AND cc.status IN ('delivered','accepted')) as cdrl_done, "
            "(SELECT COUNT(*) FROM contract_obligations co WHERE co.contract_id = c.id) as obl_count, "
            "(SELECT COUNT(*) FROM contract_obligations co WHERE co.contract_id = c.id "
            " AND co.status = 'compliant') as obl_done "
            "FROM contracts c "
            "LEFT JOIN opportunities o ON c.opportunity_id = o.id "
        )
        params = []
        if status:
            query += "WHERE c.status = ? "
            params.append(status)
        query += "ORDER BY c.created_at DESC"

        rows = conn.execute(query, params).fetchall()
        return {"contracts": [dict(r) for r in rows]}
    finally:
        conn.close()


def update_cdrl_status(cdrl_id, status, actual_delivery_date=None, db_path=None):
    """Update CDRL delivery/acceptance status."""
    conn = _get_db(db_path)
    try:
        cdrl = conn.execute(
            "SELECT * FROM contract_cdrls WHERE id = ?", (cdrl_id,)
        ).fetchone()
        if not cdrl:
            return {"error": f"CDRL {cdrl_id} not found"}

        updates = ["status = ?", "updated_at = ?"]
        params = [status, _now()]

        if actual_delivery_date:
            updates.append("actual_delivery_date = ?")
            params.append(actual_delivery_date)
        if status == "accepted":
            updates.append("acceptance_date = ?")
            params.append(_today())

        params.append(cdrl_id)
        conn.execute(
            f"UPDATE contract_cdrls SET {', '.join(updates)} WHERE id = ?",
            params
        )
        conn.commit()

        # Recalculate CPARS risk score
        cpars_risk_score(cdrl["contract_id"], db_path)

        return {"updated": True, "cdrl_id": cdrl_id, "new_status": status}
    finally:
        conn.close()


def update_obligation_status(obligation_id, status, evidence=None, db_path=None):
    """Update SOW obligation compliance status."""
    conn = _get_db(db_path)
    try:
        obl = conn.execute(
            "SELECT * FROM contract_obligations WHERE id = ?", (obligation_id,)
        ).fetchone()
        if not obl:
            return {"error": f"Obligation {obligation_id} not found"}

        updates = ["status = ?", "updated_at = ?"]
        params = [status, _now()]

        if evidence:
            updates.append("evidence = ?")
            params.append(evidence)

        params.append(obligation_id)
        conn.execute(
            f"UPDATE contract_obligations SET {', '.join(updates)} WHERE id = ?",
            params
        )
        conn.commit()

        # Recalculate CPARS risk score
        cpars_risk_score(obl["contract_id"], db_path)

        return {"updated": True, "obligation_id": obligation_id, "new_status": status}
    finally:
        conn.close()


def generate_reminders(contract_id, db_path=None):
    """Generate reminder records for upcoming due dates at 30/14/7/1 day intervals."""
    conn = _get_db(db_path)
    try:
        today = datetime.now(timezone.utc).date()
        reminders_created = 0

        # CDRLs with due dates
        cdrls = conn.execute(
            "SELECT id, contract_id, next_due_date, title "
            "FROM contract_cdrls WHERE contract_id = ? "
            "AND next_due_date IS NOT NULL "
            "AND status NOT IN ('accepted', 'delivered')",
            (contract_id,)
        ).fetchall()

        for cdrl in cdrls:
            try:
                due = datetime.strptime(cdrl["next_due_date"], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            days_until = (due - today).days

            if days_until < 0:
                # Overdue
                existing = conn.execute(
                    "SELECT id FROM deliverable_reminders "
                    "WHERE related_id = ? AND days_before = -1",
                    (cdrl["id"],)
                ).fetchone()
                if not existing:
                    conn.execute(
                        "INSERT INTO deliverable_reminders "
                        "(id, contract_id, related_type, related_id, "
                        "reminder_date, due_date, days_before, title, "
                        "severity, status, created_at) "
                        "VALUES (?, ?, 'cdrl', ?, ?, ?, -1, ?, 'overdue', 'pending', ?)",
                        (_uid("REM"), contract_id, cdrl["id"],
                         _today(), cdrl["next_due_date"],
                         f"OVERDUE: {cdrl['title'][:100]}", _now())
                    )
                    reminders_created += 1
            else:
                for interval in _REMINDER_INTERVALS:
                    if days_until >= interval:
                        existing = conn.execute(
                            "SELECT id FROM deliverable_reminders "
                            "WHERE related_id = ? AND days_before = ?",
                            (cdrl["id"], interval)
                        ).fetchone()
                        if not existing:
                            reminder_date = (due - timedelta(days=interval)).strftime("%Y-%m-%d")
                            conn.execute(
                                "INSERT INTO deliverable_reminders "
                                "(id, contract_id, related_type, related_id, "
                                "reminder_date, due_date, days_before, title, "
                                "severity, status, created_at) "
                                "VALUES (?, ?, 'cdrl', ?, ?, ?, ?, ?, ?, 'pending', ?)",
                                (_uid("REM"), contract_id, cdrl["id"],
                                 reminder_date, cdrl["next_due_date"], interval,
                                 f"CDRL {cdrl['title'][:100]} due in {interval} days",
                                 _SEVERITY_MAP.get(interval, "info"), _now())
                            )
                            reminders_created += 1

        # Obligations with due dates
        obligations = conn.execute(
            "SELECT id, contract_id, due_date, obligation_text "
            "FROM contract_obligations WHERE contract_id = ? "
            "AND due_date IS NOT NULL "
            "AND status NOT IN ('compliant', 'waived')",
            (contract_id,)
        ).fetchall()

        for obl in obligations:
            try:
                due = datetime.strptime(obl["due_date"], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                continue

            days_until = (due - today).days

            if days_until < 0:
                existing = conn.execute(
                    "SELECT id FROM deliverable_reminders "
                    "WHERE related_id = ? AND days_before = -1",
                    (obl["id"],)
                ).fetchone()
                if not existing:
                    conn.execute(
                        "INSERT INTO deliverable_reminders "
                        "(id, contract_id, related_type, related_id, "
                        "reminder_date, due_date, days_before, title, "
                        "severity, status, created_at) "
                        "VALUES (?, ?, 'obligation', ?, ?, ?, -1, ?, 'overdue', 'pending', ?)",
                        (_uid("REM"), contract_id, obl["id"],
                         _today(), obl["due_date"],
                         f"OVERDUE: {obl['obligation_text'][:100]}", _now())
                    )
                    reminders_created += 1
            else:
                for interval in _REMINDER_INTERVALS:
                    if days_until >= interval:
                        existing = conn.execute(
                            "SELECT id FROM deliverable_reminders "
                            "WHERE related_id = ? AND days_before = ?",
                            (obl["id"], interval)
                        ).fetchone()
                        if not existing:
                            reminder_date = (due - timedelta(days=interval)).strftime("%Y-%m-%d")
                            conn.execute(
                                "INSERT INTO deliverable_reminders "
                                "(id, contract_id, related_type, related_id, "
                                "reminder_date, due_date, days_before, title, "
                                "severity, status, created_at) "
                                "VALUES (?, ?, 'obligation', ?, ?, ?, ?, ?, ?, 'pending', ?)",
                                (_uid("REM"), contract_id, obl["id"],
                                 reminder_date, obl["due_date"], interval,
                                 f"Obligation due in {interval} days: {obl['obligation_text'][:80]}",
                                 _SEVERITY_MAP.get(interval, "info"), _now())
                            )
                            reminders_created += 1

        conn.commit()
        return {"reminders_created": reminders_created, "contract_id": contract_id}
    finally:
        conn.close()


def get_pending_reminders(db_path=None):
    """Get all pending reminders across contracts."""
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT r.*, c.contract_number, c.contract_name "
            "FROM deliverable_reminders r "
            "JOIN contracts c ON r.contract_id = c.id "
            "WHERE r.status = 'pending' AND r.reminder_date <= date('now') "
            "ORDER BY r.severity DESC, r.reminder_date ASC"
        ).fetchall()
        return {"reminders": [dict(r) for r in rows], "count": len(rows)}
    finally:
        conn.close()


def acknowledge_reminder(reminder_id, db_path=None):
    """Mark a reminder as acknowledged."""
    conn = _get_db(db_path)
    try:
        conn.execute(
            "UPDATE deliverable_reminders SET status = 'acknowledged', "
            "acknowledged_at = ? WHERE id = ?",
            (_now(), reminder_id)
        )
        conn.commit()
        return {"acknowledged": True, "reminder_id": reminder_id}
    finally:
        conn.close()


def cpars_risk_score(contract_id, db_path=None):
    """Calculate CPARS risk score from delivery performance.

    Score 0.0 (no risk) → 1.0 (critical risk).
    Factors (weighted):
      - overdue_cdrl_pct × 0.35
      - rejected_cdrl_pct × 0.25
      - non_compliant_pct × 0.25
      - late_delivery_pct × 0.15
    """
    conn = _get_db(db_path)
    try:
        # CDRL stats
        cdrls = conn.execute(
            "SELECT status, actual_delivery_date, next_due_date "
            "FROM contract_cdrls WHERE contract_id = ?",
            (contract_id,)
        ).fetchall()

        cdrl_total = len(cdrls)
        overdue = sum(1 for c in cdrls if c["status"] == "overdue")
        rejected = sum(1 for c in cdrls if c["status"] == "rejected")
        late = 0
        for c in cdrls:
            if c["actual_delivery_date"] and c["next_due_date"]:
                if c["actual_delivery_date"] > c["next_due_date"]:
                    late += 1

        # Obligation stats
        obligations = conn.execute(
            "SELECT status FROM contract_obligations WHERE contract_id = ?",
            (contract_id,)
        ).fetchall()

        obl_total = len(obligations)
        non_compliant = sum(1 for o in obligations if o["status"] == "non_compliant")

        # Calculate percentages (avoid division by zero)
        overdue_pct = (overdue / cdrl_total) if cdrl_total > 0 else 0.0
        rejected_pct = (rejected / cdrl_total) if cdrl_total > 0 else 0.0
        noncompliant_pct = (non_compliant / obl_total) if obl_total > 0 else 0.0
        late_pct = (late / cdrl_total) if cdrl_total > 0 else 0.0

        score = (
            overdue_pct * 0.35
            + rejected_pct * 0.25
            + noncompliant_pct * 0.25
            + late_pct * 0.15
        )
        score = min(score, 1.0)

        if score >= 0.8:
            risk_level = "critical"
        elif score >= 0.5:
            risk_level = "high"
        elif score >= 0.2:
            risk_level = "moderate"
        else:
            risk_level = "low"

        # Update contract
        conn.execute(
            "UPDATE contracts SET cpars_risk_score = ?, updated_at = ? WHERE id = ?",
            (round(score, 3), _now(), contract_id)
        )
        conn.commit()

        return {
            "contract_id": contract_id,
            "score": round(score, 3),
            "risk_level": risk_level,
            "factors": {
                "overdue_cdrl_pct": round(overdue_pct, 3),
                "rejected_cdrl_pct": round(rejected_pct, 3),
                "non_compliant_pct": round(noncompliant_pct, 3),
                "late_delivery_pct": round(late_pct, 3),
            },
            "totals": {
                "cdrls": cdrl_total,
                "obligations": obl_total,
            }
        }
    finally:
        conn.close()


def cpars_summary(contract_id, db_path=None):
    """Generate CPARS preparation summary with strengths and risk areas."""
    risk = cpars_risk_score(contract_id, db_path)
    if "error" in risk:
        return risk

    conn = _get_db(db_path)
    try:
        contract = conn.execute(
            "SELECT * FROM contracts WHERE id = ?", (contract_id,)
        ).fetchone()
        if not contract:
            return {"error": f"Contract {contract_id} not found"}

        cdrls = conn.execute(
            "SELECT * FROM contract_cdrls WHERE contract_id = ?",
            (contract_id,)
        ).fetchall()
        obligations = conn.execute(
            "SELECT * FROM contract_obligations WHERE contract_id = ?",
            (contract_id,)
        ).fetchall()

        # Strengths
        strengths = []
        accepted_cdrls = sum(1 for c in cdrls if c["status"] == "accepted")
        if accepted_cdrls > 0:
            strengths.append(f"{accepted_cdrls} CDRLs accepted by government")
        compliant_obls = sum(1 for o in obligations if o["status"] == "compliant")
        if compliant_obls > 0:
            strengths.append(f"{compliant_obls} SOW obligations met (compliant)")
        on_schedule = sum(1 for c in cdrls if c["status"] == "on_schedule")
        if on_schedule > 0:
            strengths.append(f"{on_schedule} CDRLs currently on schedule")

        # Risks
        risks = []
        overdue_cdrls = [c for c in cdrls if c["status"] == "overdue"]
        if overdue_cdrls:
            risks.append(f"{len(overdue_cdrls)} CDRLs overdue — immediate CPARS impact")
        rejected_cdrls = [c for c in cdrls if c["status"] == "rejected"]
        if rejected_cdrls:
            risks.append(f"{len(rejected_cdrls)} CDRLs rejected — rework needed")
        nc_obls = [o for o in obligations if o["status"] == "non_compliant"]
        if nc_obls:
            risks.append(f"{len(nc_obls)} SOW obligations non-compliant")

        # Recommendations
        recommendations = []
        if overdue_cdrls:
            recommendations.append("Deliver overdue CDRLs within 7 days to limit CPARS damage")
        if rejected_cdrls:
            recommendations.append("Address rejection reasons and resubmit CDRLs")
        if nc_obls:
            recommendations.append("Document corrective actions for non-compliant obligations")
        if risk["score"] >= 0.5:
            recommendations.append("Schedule COR meeting to discuss performance improvement plan")
        if not recommendations:
            recommendations.append("Continue current performance — on track for positive CPARS")

        return {
            "contract_id": contract_id,
            "contract_number": contract["contract_number"],
            "risk": risk,
            "strengths": strengths,
            "risks": risks,
            "recommendations": recommendations,
            "classification": "CUI // SP-PROPIN",
        }
    finally:
        conn.close()


def check_overdue(db_path=None):
    """Scan all active contracts for overdue CDRLs and obligations."""
    conn = _get_db(db_path)
    try:
        today = _today()

        # Overdue CDRLs
        overdue_cdrls = conn.execute(
            "SELECT cc.id, cc.contract_id, cc.cdrl_number, cc.title, "
            "cc.next_due_date, c.contract_number "
            "FROM contract_cdrls cc "
            "JOIN contracts c ON cc.contract_id = c.id "
            "WHERE cc.next_due_date < ? "
            "AND cc.status NOT IN ('delivered', 'accepted', 'overdue') "
            "AND c.status = 'active'",
            (today,)
        ).fetchall()

        # Mark them overdue
        for cdrl in overdue_cdrls:
            conn.execute(
                "UPDATE contract_cdrls SET status = 'overdue', updated_at = ? WHERE id = ?",
                (_now(), cdrl["id"])
            )

        # Overdue obligations
        overdue_obligations = conn.execute(
            "SELECT co.id, co.contract_id, co.obligation_text, "
            "co.due_date, c.contract_number "
            "FROM contract_obligations co "
            "JOIN contracts c ON co.contract_id = c.id "
            "WHERE co.due_date < ? "
            "AND co.status NOT IN ('compliant', 'waived', 'non_compliant') "
            "AND c.status = 'active'",
            (today,)
        ).fetchall()

        # Mark them non-compliant
        for obl in overdue_obligations:
            conn.execute(
                "UPDATE contract_obligations SET status = 'non_compliant', "
                "updated_at = ? WHERE id = ?",
                (_now(), obl["id"])
            )

        conn.commit()

        # Generate overdue reminders for affected contracts
        affected_contracts = set()
        for c in overdue_cdrls:
            affected_contracts.add(c["contract_id"])
        for o in overdue_obligations:
            affected_contracts.add(o["contract_id"])

        for cid in affected_contracts:
            generate_reminders(cid, db_path)
            cpars_risk_score(cid, db_path)

        return {
            "overdue_cdrls": [dict(c) for c in overdue_cdrls],
            "overdue_obligations": [dict(o) for o in overdue_obligations],
            "overdue_cdrl_count": len(overdue_cdrls),
            "overdue_obligation_count": len(overdue_obligations),
            "contracts_affected": len(affected_contracts),
        }
    finally:
        conn.close()


def contract_dashboard_data(db_path=None):
    """Aggregate stats for dashboard home card."""
    conn = _get_db(db_path)
    try:
        active = conn.execute(
            "SELECT COUNT(*) as cnt FROM contracts WHERE status = 'active'"
        ).fetchone()["cnt"]

        total_cdrls = conn.execute(
            "SELECT COUNT(*) as cnt FROM contract_cdrls cc "
            "JOIN contracts c ON cc.contract_id = c.id WHERE c.status = 'active'"
        ).fetchone()["cnt"]

        overdue = conn.execute(
            "SELECT COUNT(*) as cnt FROM contract_cdrls cc "
            "JOIN contracts c ON cc.contract_id = c.id "
            "WHERE cc.status = 'overdue' AND c.status = 'active'"
        ).fetchone()["cnt"]

        row = conn.execute(
            "SELECT AVG(cpars_risk_score) as avg_risk "
            "FROM contracts WHERE status = 'active'"
        ).fetchone()
        avg_risk = round(row["avg_risk"] or 0.0, 2)

        pending = conn.execute(
            "SELECT COUNT(*) as cnt FROM deliverable_reminders "
            "WHERE status = 'pending' AND reminder_date <= date('now')"
        ).fetchone()["cnt"]

        return {
            "active_contracts": active,
            "total_cdrls": total_cdrls,
            "overdue_items": overdue,
            "avg_cpars_risk": avg_risk,
            "pending_reminders": pending,
        }
    finally:
        conn.close()


# ── CLI ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Contract Performance Management Portal (CPMP)")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--activate", action="store_true",
                       help="Activate contract from awarded proposal")
    group.add_argument("--get", action="store_true",
                       help="Get contract detail")
    group.add_argument("--list", action="store_true",
                       help="List all contracts")
    group.add_argument("--update-cdrl", action="store_true",
                       help="Update CDRL status")
    group.add_argument("--update-obligation", action="store_true",
                       help="Update obligation status")
    group.add_argument("--reminders", action="store_true",
                       help="Get pending reminders")
    group.add_argument("--check-overdue", action="store_true",
                       help="Check for overdue items")
    group.add_argument("--cpars-risk", action="store_true",
                       help="Calculate CPARS risk score")
    group.add_argument("--cpars-summary", action="store_true",
                       help="Generate CPARS summary")
    group.add_argument("--dashboard", action="store_true",
                       help="Dashboard aggregate data")

    parser.add_argument("--proposal-id", help="Proposal ID (for --activate)")
    parser.add_argument("--contract-id", help="Contract ID")
    parser.add_argument("--contract-number", help="Contract number")
    parser.add_argument("--pop-start", help="Period of Performance start (YYYY-MM-DD)")
    parser.add_argument("--pop-end", help="Period of Performance end (YYYY-MM-DD)")
    parser.add_argument("--cdrl-id", help="CDRL ID (for --update-cdrl)")
    parser.add_argument("--obligation-id", help="Obligation ID")
    parser.add_argument("--status", help="Status value")
    parser.add_argument("--delivery-date", help="Actual delivery date")
    parser.add_argument("--evidence", help="Evidence text")
    parser.add_argument("--db-path", help="Override database path")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    result = {}

    if args.activate:
        if not args.proposal_id:
            print("ERROR: --proposal-id required for --activate")
            exit(1)
        result = activate_contract(
            args.proposal_id,
            contract_number=args.contract_number,
            pop_start=args.pop_start,
            pop_end=args.pop_end,
            db_path=args.db_path,
        )
    elif args.get:
        if not args.contract_id:
            print("ERROR: --contract-id required for --get")
            exit(1)
        result = get_contract(args.contract_id, db_path=args.db_path)
    elif args.list:
        result = list_contracts(status=args.status, db_path=args.db_path)
    elif args.update_cdrl:
        if not args.cdrl_id or not args.status:
            print("ERROR: --cdrl-id and --status required for --update-cdrl")
            exit(1)
        result = update_cdrl_status(
            args.cdrl_id, args.status,
            actual_delivery_date=args.delivery_date,
            db_path=args.db_path,
        )
    elif args.update_obligation:
        if not args.obligation_id or not args.status:
            print("ERROR: --obligation-id and --status required for --update-obligation")
            exit(1)
        result = update_obligation_status(
            args.obligation_id, args.status,
            evidence=args.evidence,
            db_path=args.db_path,
        )
    elif args.reminders:
        result = get_pending_reminders(db_path=args.db_path)
    elif args.check_overdue:
        result = check_overdue(db_path=args.db_path)
    elif args.cpars_risk:
        if not args.contract_id:
            print("ERROR: --contract-id required for --cpars-risk")
            exit(1)
        result = cpars_risk_score(args.contract_id, db_path=args.db_path)
    elif args.cpars_summary:
        if not args.contract_id:
            print("ERROR: --contract-id required for --cpars-summary")
            exit(1)
        result = cpars_summary(args.contract_id, db_path=args.db_path)
    elif args.dashboard:
        result = contract_dashboard_data(db_path=args.db_path)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        # Human-readable output
        if "error" in result:
            print(f"ERROR: {result['error']}")
        elif "contract_id" in result and "cdrls_imported" in result:
            print(f"Contract activated: {result['contract_id']}")
            print(f"  Number:       {result.get('contract_number', 'N/A')}")
            print(f"  CDRLs:        {result['cdrls_imported']} imported")
            print(f"  Obligations:  {result['obligations_imported']} imported")
            print(f"  Reminders:    {result.get('reminders_created', 0)} created")
        elif "contracts" in result:
            contracts = result["contracts"]
            print(f"Contracts: {len(contracts)}")
            for c in contracts:
                risk = c.get("cpars_risk_score", 0.0) or 0.0
                print(f"  [{c['status']}] {c.get('contract_number','N/A')} "
                      f"— {c['contract_name'][:50]} "
                      f"(CDRLs: {c.get('cdrl_done',0)}/{c.get('cdrl_count',0)}, "
                      f"Risk: {risk:.2f})")
        elif "reminders" in result:
            rems = result["reminders"]
            print(f"Pending reminders: {result.get('count', len(rems))}")
            for r in rems:
                print(f"  [{r['severity']}] {r['title'][:80]} (due {r['due_date']})")
        elif "score" in result:
            print(f"CPARS Risk Score: {result['score']:.3f} ({result['risk_level']})")
            for k, v in result.get("factors", {}).items():
                print(f"  {k}: {v:.3f}")
        elif "strengths" in result:
            print(f"CPARS Summary for {result.get('contract_number', 'N/A')}")
            print(f"  Risk: {result['risk']['score']:.3f} ({result['risk']['risk_level']})")
            print(f"\n  Strengths:")
            for s in result["strengths"]:
                print(f"    + {s}")
            print(f"\n  Risks:")
            for r in result["risks"]:
                print(f"    - {r}")
            print(f"\n  Recommendations:")
            for rec in result["recommendations"]:
                print(f"    > {rec}")
        elif "active_contracts" in result:
            print(f"Contract Dashboard:")
            print(f"  Active:     {result['active_contracts']}")
            print(f"  CDRLs:      {result['total_cdrls']}")
            print(f"  Overdue:    {result['overdue_items']}")
            print(f"  CPARS Avg:  {result['avg_cpars_risk']:.2f}")
            print(f"  Reminders:  {result['pending_reminders']}")
        else:
            print(json.dumps(result, indent=2, default=str))
