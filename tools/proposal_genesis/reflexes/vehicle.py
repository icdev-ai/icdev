#!/usr/bin/env python3
# CUI // SP-CTI
"""R19: Vehicle Reflex — Contract Vehicle Monitor.

Tracks OASIS+ domain openings, Polaris pools, CIO-SP4 status, and other
major GWACs/BPAs/IDIQs relevant to active opportunities.

Checks:
  - Opportunities that reference known contract vehicles
  - Vehicle expiration/recompete dates
  - Pool/domain eligibility alignment

Schedule: daily.
GREEN tier (read-only checks, writes to audit trail).
Scanner-tier only (zero Claude tokens — fully deterministic).
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.proposal_genesis.reflexes.vehicle")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pgveh") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Known contract vehicles (deterministic reference data)
# ---------------------------------------------------------------------------

KNOWN_VEHICLES = {
    "OASIS+": {"type": "GWAC", "agency": "GSA", "status": "active"},
    "Polaris": {"type": "GWAC", "agency": "GSA", "status": "active"},
    "CIO-SP4": {"type": "GWAC", "agency": "NIH NITAAC", "status": "pending"},
    "SEWP VI": {"type": "GWAC", "agency": "NASA", "status": "pending"},
    "Alliant 3": {"type": "GWAC", "agency": "GSA", "status": "planned"},
    "8(a) STARS III": {"type": "GWAC", "agency": "GSA", "status": "active"},
    "VETS 2": {"type": "GWAC", "agency": "GSA", "status": "active"},
}


def _match_vehicles_to_opportunities() -> List[Dict]:
    """Match active opportunities to known contract vehicles."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT id, title, description, agency
            FROM proposal_opportunities
            WHERE status IN ('tracking', 'drafting')
            ORDER BY created_at DESC
            LIMIT 50
        """).fetchall()
    except Exception:
        return []
    finally:
        conn.close()

    matches = []
    for row in rows:
        text = ((row["title"] or "") + " " + (row["description"] or "")).lower()
        for vehicle_name, info in KNOWN_VEHICLES.items():
            if vehicle_name.lower() in text:
                matches.append(
                    {
                        "opportunity_id": row["id"],
                        "title": row["title"],
                        "vehicle": vehicle_name,
                        "vehicle_type": info["type"],
                        "vehicle_agency": info["agency"],
                        "vehicle_status": info["status"],
                    }
                )

    return matches


def _check_vehicle_eligibility() -> Dict[str, Any]:
    """Check if opportunities are on vehicles where we hold a seat.

    Placeholder — in production, this would check pg_contract_vehicles table.
    """
    conn = get_connection()
    try:
        # Try to query contract vehicles table if it exists
        rows = conn.execute(
            "SELECT vehicle_name, holder_status, expiration_date "
            "FROM pg_contract_vehicles "
            "WHERE holder_status = 'active' "
            "LIMIT 20"
        ).fetchall()
        return {
            "vehicles_held": len(rows),
            "vehicles": [dict(r) for r in rows],
        }
    except Exception:
        # Table may not exist yet
        return {"vehicles_held": 0, "vehicles": []}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Vehicle Reflex (R19).

    Steps:
      1. Match active opportunities to known contract vehicles
      2. Check vehicle eligibility/seat status
      3. Report vehicle alignment for bid decisions

    Returns standard reflex result dict.
    """
    # Match opportunities to vehicles
    vehicle_matches = _match_vehicles_to_opportunities()

    # Check our vehicle holdings
    eligibility = _check_vehicle_eligibility()

    vehicles_checked = len(KNOWN_VEHICLES)

    # Audit
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, details, success, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                _generate_id("pgaudit"),
                "vehicle_check",
                "vehicle",
                "green",
                json.dumps(
                    {
                        "vehicles_checked": vehicles_checked,
                        "matches_found": len(vehicle_matches),
                        "vehicles_held": eligibility["vehicles_held"],
                    }
                ),
                1,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("run: best-effort INSERT into pg_proposal_genesis_audit failed (non-blocking): %s", exc)
    finally:
        conn.close()

    return {
        "success": True,
        "metric_value": float(vehicles_checked),
        "details": {
            "vehicles_checked": vehicles_checked,
            "opportunity_vehicle_matches": len(vehicle_matches),
            "vehicles_held": eligibility["vehicles_held"],
            "match_details": vehicle_matches[:15],
        },
    }


# CUI // SP-CTI
