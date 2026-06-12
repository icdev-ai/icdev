# [CUI // SP-CTI]
"""Seed demo migration projects, phases, and SOP links for Network Design Canvas.

Resolves SOP IDs dynamically from ndc_sops — never hardcodes IDs.
Safe to re-run: all INSERTs use OR IGNORE; existing data is preserved.

Usage:
    python tools/network/seed_migration_demo.py
    python tools/network/seed_migration_demo.py --json
    python tools/network/seed_migration_demo.py --reset  # drops + recreates demo data
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.network.db.init_db import get_connection  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _find_sop(conn, *keywords: str) -> str | None:
    """Return sop_id of best-matching SOP by title keyword.

    Prefers approved status; falls back to any status so demo data is always
    fully linked even when seed_sops.py hasn't run approval workflow yet.
    """
    for status in ("approved", "review", "draft"):
        for kw in keywords:
            row = conn.execute(
                "SELECT sop_id FROM ndc_sops WHERE status=? AND lower(title) LIKE ? LIMIT 1",
                (status, f"%{kw.lower()}%"),
            ).fetchone()
            if row:
                return row[0]
    return None


def _link_sop(conn, phase_id: str, project_id: str, sop_id: str, title: str,
              relevance: str = "auto-linked by seed") -> None:
    """Insert a nc_phase_documents row linking a phase to a real SOP."""
    conn.execute(
        """INSERT OR IGNORE INTO nc_phase_documents
           (id, phase_id, project_id, doc_source, doc_id,
            doc_title, doc_type, relevance_note, display_order, created_at)
           VALUES (?,?,?,'sop',?,?,  'sop',?,0,?)""",
        (str(uuid.uuid4()), phase_id, project_id, sop_id, title, relevance, _now()),
    )


# ── Demo project definitions ──────────────────────────────────────────────────

DEMO_PROJECTS = [
    {
        "id": "proj-dewie-mx304",
        "name": "Dewie Router Replacement",
        "description": (
            "Replace Juniper MX1003 (Dewie) with MX304. "
            "North: NIPR trunk + DISA BCAP eBGP. South: LAG to owned switch + ISP eBGP."
        ),
        "status": "active",
        "owner": "network-ops",
        "phases": [
            {
                "id": "ph-dewie-1",
                "phase_num": 1,
                "title": "Pre-Migration Planning and Inventory",
                "description": (
                    "Capture Dewie running config. "
                    "Verify BGP neighbor states (NIPR, DISA BCAP, ISP). "
                    "Validate LAG member links and LACP state. "
                    "Document AS numbers, prefix lists, route policies, community strings, IP addressing, VLANs. "
                    "Confirm MX304 hardware compatibility and JunOS version parity."
                ),
                "duration_days": 5,
                "maintenance_window": "Business hours",
                "rollback_criteria": "N/A - read-only phase",
                "properties": {"changing_devices": [], "new_devices": [], "retiring_devices": [], "context_devices": ["node-dewie-mx1003"]},
                "sop_keywords": [
                    ("bcap", "nIPRNet", "onboarding"),
                    ("bgp route filtering", "prefix list"),
                    ("bgp session", "diagnosis"),
                ],
            },
            {
                "id": "ph-dewie-2",
                "phase_num": 2,
                "title": "MX304 Staging Configuration",
                "description": (
                    "Pre-stage MX304 in lab: apply base config, VLANs, interfaces. "
                    "Configure BGP neighbors (NIPR AS, DISA BCAP AS, ISP AS). "
                    "Apply prefix-lists and route policies from inventory. "
                    "Validate LAG bundle, LACP PDU exchange, and member link health. "
                    "Run JunOS commit check and diff against MX1003 baseline."
                ),
                "duration_days": 7,
                "maintenance_window": "Business hours",
                "rollback_criteria": "Lab only — no production impact",
                "properties": {"changing_devices": ["node-dewie-mx1003"], "new_devices": ["node-dewie-mx304"], "retiring_devices": []},
                "sop_keywords": [
                    ("bgp route filtering", "prefix list"),
                    ("juniper", "junos", "vpn peer"),
                ],
            },
            {
                "id": "ph-dewie-3",
                "phase_num": 3,
                "title": "BGP Pre-Validation and Lab Verification",
                "description": (
                    "Establish BGP sessions to lab stubs for NIPR, BCAP, and ISP peers. "
                    "Verify prefix advertisements match production policy. "
                    "Test failover: bring down primary link, confirm secondary path active in < 60s. "
                    "Validate community tagging and MED values. "
                    "Document any config delta for production cut-sheet."
                ),
                "duration_days": 5,
                "maintenance_window": "Business hours",
                "rollback_criteria": "Lab — decommission lab BGP sessions",
                "properties": {"changing_devices": ["node-dewie-mx1003"], "new_devices": ["node-dewie-mx304"], "retiring_devices": []},
                "sop_keywords": [
                    ("ebgp multi-hop", "multi-hop"),
                    ("jwics connectivity troubleshooting", "connectivity troubleshooting"),
                ],
            },
            {
                "id": "ph-dewie-4",
                "phase_num": 4,
                "title": "DISA BCAP and NIPR Cutover",
                "description": (
                    "Schedule DISA BCAP maintenance window (SNAP ticket). "
                    "Redirect NIPR trunk from MX1003 to MX304 — patch panel swap. "
                    "Bring up DISA BCAP eBGP session on MX304, verify prefix exchange. "
                    "Confirm NIPR reachability from downstream hosts. "
                    "Hold 15-minute stability window before proceeding."
                ),
                "duration_days": 1,
                "maintenance_window": "0200-0600 EST (DISA window)",
                "rollback_criteria": "Revert patch panel to MX1003 within 10 minutes if BGP not up",
                "properties": {"changing_devices": ["node-dewie-mx1003"], "new_devices": ["node-dewie-mx304"], "retiring_devices": []},
                "sop_keywords": [
                    ("full network connectivity verification", "nipr + jwics"),
                    ("bcap onboarding", "nIPRNet cloud on-ramp"),
                ],
            },
            {
                "id": "ph-dewie-5",
                "phase_num": 5,
                "title": "ISP BGP Cutover",
                "description": (
                    "Coordinate with ISP NOC for BGP session migration window. "
                    "Move ISP physical handoff from MX1003 to MX304. "
                    "Establish eBGP session with ISP AS, verify default route and full-table receipt. "
                    "Confirm internet reachability from internal hosts. "
                    "Monitor for route flaps for 30 minutes post-cutover."
                ),
                "duration_days": 1,
                "maintenance_window": "0200-0600 EST",
                "rollback_criteria": "Move ISP handoff back to MX1003 if BGP not stable in 15 min",
                "properties": {"changing_devices": ["node-dewie-mx1003"], "new_devices": ["node-dewie-mx304"], "retiring_devices": []},
                "sop_keywords": [
                    ("bgp route filtering", "prefix list"),
                    ("bgp session", "diagnosis"),
                ],
            },
            {
                "id": "ph-dewie-6",
                "phase_num": 6,
                "title": "Decommission Dewie MX1003",
                "description": (
                    "Confirm all traffic flowing through MX304 for 48 hours. "
                    "Remove MX1003 BGP configurations and shut all interfaces. "
                    "Update IPAM, network diagrams, and runbook inventory. "
                    "Submit decommission change request and asset disposal ticket. "
                    "Archive MX1003 config to version control."
                ),
                "duration_days": 3,
                "maintenance_window": "Business hours",
                "rollback_criteria": "N/A - decom only after 48h stability confirmation",
                "properties": {"changing_devices": [], "new_devices": [], "retiring_devices": ["node-dewie-mx1003"]},
                "sop_keywords": [
                    ("full network connectivity verification", "nipr + jwics"),
                ],
            },
        ],
    },
]


def seed_demo_migrations(reset: bool = False) -> dict:
    """Seed demo migration projects. Returns counts dict."""
    conn = get_connection()
    try:
        projects_created = 0
        phases_created = 0
        links_created = 0

        for proj in DEMO_PROJECTS:
            if reset:
                conn.execute("DELETE FROM nc_phase_documents WHERE project_id=?", (proj["id"],))
                conn.execute("DELETE FROM nc_migration_phases WHERE project_id=?", (proj["id"],))
                conn.execute("DELETE FROM nc_projects WHERE id=?", (proj["id"],))

            # Create project
            conn.execute(
                """INSERT OR IGNORE INTO nc_projects
                   (id, name, description, status, owner, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (proj["id"], proj["name"], proj["description"],
                 proj["status"], proj["owner"], _now(), _now()),
            )
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                projects_created += 1

            for ph in proj["phases"]:
                props = json.dumps(ph.get("properties", {}))
                # Create phase
                conn.execute(
                    """INSERT OR IGNORE INTO nc_migration_phases
                       (id, project_id, phase_num, title, description,
                        duration_days, maintenance_window, rollback_criteria,
                        status, classification, impact_level, properties_json, created_at)
                       VALUES (?,?,?,?,?,?,?,?,'pending','CUI','IL4',?,?)""",
                    (ph["id"], proj["id"], ph["phase_num"], ph["title"],
                     ph["description"], ph["duration_days"],
                     ph["maintenance_window"], ph["rollback_criteria"], props, _now()),
                )
                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    phases_created += 1

                # Link SOPs by resolving real IDs from ndc_sops
                for keyword_group in ph["sop_keywords"]:
                    sop_id = _find_sop(conn, *keyword_group)
                    if sop_id:
                        title_row = conn.execute(
                            "SELECT title FROM ndc_sops WHERE sop_id=?", (sop_id,)
                        ).fetchone()
                        sop_title = title_row[0] if title_row else "SOP"
                        before = conn.execute(
                            "SELECT COUNT(*) FROM nc_phase_documents WHERE phase_id=? AND doc_id=?",
                            (ph["id"], sop_id),
                        ).fetchone()[0]
                        if before == 0:
                            _link_sop(conn, ph["id"], proj["id"], sop_id, sop_title)
                            links_created += 1

        conn.commit()
        return {
            "projects_created": projects_created,
            "phases_created": phases_created,
            "sop_links_created": links_created,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    as_json = "--json" in sys.argv
    result = seed_demo_migrations(reset=reset)
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(
            f"Demo migrations seeded — "
            f"projects:{result['projects_created']}  "
            f"phases:{result['phases_created']}  "
            f"sop_links:{result['sop_links_created']}"
        )
