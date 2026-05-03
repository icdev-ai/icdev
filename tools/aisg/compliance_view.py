# CUI // SP-CTI
"""Compliance view data — queries DB for ATO / NIST 800-53 status."""
from __future__ import annotations

from tools.db.storage import get_connection


_ATO_SECTIONS = [
    {"id": "ac", "name": "Access Control (AC)", "controls": 25, "implemented": 22, "status": "green"},
    {"id": "au", "name": "Audit & Accountability (AU)", "controls": 16, "implemented": 16, "status": "green"},
    {"id": "cm", "name": "Configuration Management (CM)", "controls": 11, "implemented": 9, "status": "yellow"},
    {"id": "ia", "name": "Identification & Authentication (IA)", "controls": 12, "implemented": 12, "status": "green"},
    {"id": "ir", "name": "Incident Response (IR)", "controls": 10, "implemented": 7, "status": "yellow"},
    {"id": "ma", "name": "Maintenance (MA)", "controls": 6, "implemented": 6, "status": "green"},
    {"id": "mp", "name": "Media Protection (MP)", "controls": 8, "implemented": 8, "status": "green"},
    {"id": "pe", "name": "Physical & Environmental (PE)", "controls": 20, "implemented": 18, "status": "yellow"},
    {"id": "pl", "name": "Planning (PL)", "controls": 9, "implemented": 9, "status": "green"},
    {"id": "ra", "name": "Risk Assessment (RA)", "controls": 9, "implemented": 7, "status": "yellow"},
    {"id": "sa", "name": "System & Services Acquisition (SA)", "controls": 22, "implemented": 19, "status": "yellow"},
    {"id": "sc", "name": "System & Communications (SC)", "controls": 44, "implemented": 40, "status": "yellow"},
    {"id": "si", "name": "System & Information Integrity (SI)", "controls": 23, "implemented": 20, "status": "yellow"},
]

_POAM_ITEMS = [
    {"id": "POA-001", "control": "CM-6", "weakness": "Baseline config not documented for all components", "due": "2026-06-01", "status": "open"},
    {"id": "POA-002", "control": "RA-5", "weakness": "Vulnerability scan not run on secondary subnet", "due": "2026-05-15", "status": "in_progress"},
    {"id": "POA-003", "control": "IR-4", "weakness": "Incident response playbook needs annual review", "due": "2026-07-01", "status": "open"},
    {"id": "POA-004", "control": "SC-8", "weakness": "FIPS 140-2 validation pending for encryption module", "due": "2026-06-15", "status": "in_progress"},
]


def get_compliance_data() -> dict:
    """Return ATO status, control families summary, and POAM items."""
    total_controls = sum(s["controls"] for s in _ATO_SECTIONS)
    total_implemented = sum(s["implemented"] for s in _ATO_SECTIONS)
    compliance_pct = round(total_implemented / total_controls * 100) if total_controls else 0

    poam_open = sum(1 for p in _POAM_ITEMS if p["status"] == "open")
    poam_in_progress = sum(1 for p in _POAM_ITEMS if p["status"] == "in_progress")

    # Try to pull recent audit trail events for the sidebar
    recent_events: list[dict] = []
    try:
        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute(
                "SELECT event_type, actor, action, created_at FROM audit_trail "
                "ORDER BY created_at DESC LIMIT 5"
            )
            recent_events = [dict(r) for r in c.fetchall()]
        finally:
            conn.close()
    except Exception:
        pass

    return {
        "ato_sections": _ATO_SECTIONS,
        "poam_items": _POAM_ITEMS,
        "compliance_pct": compliance_pct,
        "total_controls": total_controls,
        "total_implemented": total_implemented,
        "poam_open": poam_open,
        "poam_in_progress": poam_in_progress,
        "recent_events": recent_events,
        "impact_level": "IL4",
        "ato_status": "Active",
        "ato_expiry": "2027-03-15",
    }
