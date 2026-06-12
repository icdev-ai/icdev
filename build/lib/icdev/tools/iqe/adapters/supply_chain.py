# CUI // SP-CTI
"""IQE supply chain collection adapters.

Importing this module registers four collections on the module-level Executor:
  supply_chain.vendors        — Vendor registry (supply_chain_vendors table).
  supply_chain.scrm_risks     — NIST 800-161 risk assessments (scrm_assessments).
  supply_chain.cve_triage     — CVE triage queue (cve_triage table).
  supply_chain.isa_agreements — ISA/MOU agreements (isa_agreements table).
"""
from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def vendors_adapter(conn: Any) -> list[dict]:
    """Return rows from supply_chain_vendors."""
    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection()
    cur = conn.execute(
        "SELECT id, vendor_name, vendor_type, country_of_origin, "
        "scrm_risk_tier, section_889_status, dod_approved, isa_required, "
        "last_assessed, classification, created_at "
        "FROM supply_chain_vendors"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def scrm_risks_adapter(conn: Any) -> list[dict]:
    """Return SCRM assessments joined with vendor names."""
    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection()
    cur = conn.execute(
        "SELECT a.id, a.vendor_id, v.vendor_name, a.package_name, "
        "a.assessment_type, a.risk_category, a.risk_score, "
        "a.likelihood, a.impact, a.residual_risk, a.assessed_by, a.assessed_at "
        "FROM scrm_assessments a "
        "LEFT JOIN supply_chain_vendors v ON v.id = a.vendor_id"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def cve_triage_adapter(conn: Any) -> list[dict]:
    """Return CVE triage rows."""
    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection()
    cur = conn.execute(
        "SELECT id, cve_id, package_name, package_version, severity, cvss_score, "
        "exploitability, triage_decision, triage_rationale, sla_deadline, "
        "triaged_by, triaged_at, remediated_at "
        "FROM cve_triage"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def isa_agreements_adapter(conn: Any) -> list[dict]:
    """Return ISA/MOU agreement rows."""
    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection()
    cur = conn.execute(
        "SELECT id, agreement_type, partner_system, partner_org, status, "
        "signed_date, expiry_date, next_review_date, poc_name, classification "
        "FROM isa_agreements"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


register_collection("supply_chain.vendors",        vendors_adapter)
register_collection("supply_chain.scrm_risks",     scrm_risks_adapter)
register_collection("supply_chain.cve_triage",     cve_triage_adapter)
register_collection("supply_chain.isa_agreements", isa_agreements_adapter)
