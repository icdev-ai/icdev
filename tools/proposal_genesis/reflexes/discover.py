#!/usr/bin/env python3
# CUI // SP-CTI
"""R1: Discover Reflex — SAM.gov polling + pre-solicitation + amendment detection.

Wraps tools/govcon/sam_scanner.py and tools/govcon/amendment_tracker.py.
Scanner-tier only (zero Claude tokens).  Air-gap safe (graceful degradation).
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_air_gapped() -> bool:
    import os

    return os.environ.get("ICDEV_ENVIRONMENT", "").lower() == "air-gapped"


def _scan_sam_gov(config: Dict[str, Any]) -> Dict[str, Any]:
    """Run SAM.gov scanner and return results."""
    try:
        from tools.govcon.sam_scanner import scan_sam_gov

        result = scan_sam_gov()
        return result if isinstance(result, dict) else {"status": "ok", "raw": str(result)}
    except ImportError:
        return {"status": "import_error", "message": "sam_scanner not available"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _check_amendments(config: Dict[str, Any]) -> Dict[str, Any]:
    """Check for amendments on tracked opportunities."""
    try:
        from tools.govcon.amendment_tracker import list_amendments

        conn = get_connection()
        try:
            # Get active opportunities to check for amendments
            rows = conn.execute(
                "SELECT id, title, sam_gov_opportunity_id FROM proposal_opportunities "
                "WHERE status IN ('tracking', 'drafting', 'reviewing') "
                "ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        finally:
            conn.close()

        amendment_count = 0
        for row in rows:
            try:
                amendments = list_amendments(row["id"])
                if isinstance(amendments, dict):
                    amendment_count += amendments.get("total", 0)
            except Exception:
                pass

        return {"amendments_checked": len(rows), "amendments_found": amendment_count}
    except ImportError:
        return {"status": "import_error", "message": "amendment_tracker not available"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _store_amendment_diff(opp_id: str, diff_type: str, section: str, old_text: str, new_text: str) -> None:
    """Store an amendment diff for R5 re-extraction."""
    import uuid

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO pg_amendment_diffs
                (id, opportunity_id, diff_type, section, old_text, new_text, re_extracted, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?)
        """,
            (f"pgad-{uuid.uuid4().hex[:10]}", opp_id, diff_type, section, old_text, new_text, _utcnow_iso()),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Discover Reflex (R1).

    Returns:
        {"success": bool, "metric_value": float, "details": dict}
    """
    if _is_air_gapped():
        return {
            "success": True,
            "metric_value": 0,
            "details": {"status": "air_gapped", "message": "Skipped -- air-gapped mode"},
        }

    # Step 1: Scan SAM.gov for new opportunities
    scan_result = _scan_sam_gov(config)
    new_opportunities = 0
    if isinstance(scan_result, dict):
        new_opportunities = scan_result.get("new_count", scan_result.get("inserted", 0))

    # Step 2: Check amendments on tracked opportunities
    amend_result = _check_amendments(config)
    amendments_found = 0
    if isinstance(amend_result, dict):
        amendments_found = amend_result.get("amendments_found", 0)

    total_signals = new_opportunities + amendments_found

    return {
        "success": True,
        "metric_value": float(total_signals),
        "details": {
            "new_opportunities": new_opportunities,
            "amendments_found": amendments_found,
            "scan_result": scan_result,
            "amendment_result": amend_result,
        },
    }
