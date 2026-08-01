#!/usr/bin/env python3
# CUI // SP-CTI
"""R16: Regulate Reflex — FAR/DFARS/GSA Change Monitor (section 3.15 triggers).

Monitors Federal Register for acquisition policy changes that affect active
proposals.  Checks AI clause compliance for opportunities.

Schedule: weekly.
GREEN tier (read-only checks, writes to audit trail).
Scanner-tier only (zero Claude tokens — fully deterministic).
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.proposal_genesis.reflexes.regulate")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pgreg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _is_air_gapped() -> bool:
    return os.environ.get("ICDEV_AIR_GAPPED", "").lower() in ("true", "1")


# ---------------------------------------------------------------------------
# Regulation change detection
# ---------------------------------------------------------------------------


def _check_ai_clause_gaps() -> List[Dict]:
    """Find opportunities where AI clause compliance has not been assessed."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT po.id, po.title, po.agency
            FROM proposal_opportunities po
            LEFT JOIN pg_ai_clause_compliance ac ON ac.opportunity_id = po.id
            WHERE po.status IN ('tracking', 'drafting')
            AND ac.id IS NULL
            ORDER BY po.created_at DESC
            LIMIT 20
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _check_clause_staleness() -> List[Dict]:
    """Find AI clause assessments older than 90 days that may need refresh."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT ac.id, ac.opportunity_id, ac.clause_id, ac.clause_title,
                   ac.assessed_at, po.title
            FROM pg_ai_clause_compliance ac
            JOIN proposal_opportunities po ON po.id = ac.opportunity_id
            WHERE po.status IN ('tracking', 'drafting')
            AND ac.assessed_at < datetime('now', '-90 days')
            ORDER BY ac.assessed_at ASC
            LIMIT 20
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _scan_regulation_changes() -> Dict[str, Any]:
    """Placeholder for Federal Register scanning.

    Future: will scan federalregister.gov API for FAR/DFARS changes.
    Currently returns empty results (air-gap safe, no external calls).
    """
    if _is_air_gapped():
        return {"status": "air_gapped", "changes": 0}

    # Future: implement Federal Register API scanning
    # For now, return placeholder indicating no scan was performed
    return {"status": "not_implemented", "changes": 0}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Regulate Reflex (R16).

    Steps:
      1. Check for opportunities without AI clause assessment
      2. Find stale clause assessments (>90 days)
      3. Placeholder: scan Federal Register for policy changes

    Returns standard reflex result dict.
    """
    # Check for gaps in AI clause compliance
    clause_gaps = _check_ai_clause_gaps()

    # Check for stale assessments
    stale_assessments = _check_clause_staleness()

    # Placeholder for Federal Register scanning
    reg_scan = _scan_regulation_changes()

    total_changes = len(clause_gaps) + len(stale_assessments) + reg_scan.get("changes", 0)

    # Audit
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, details, success, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                _generate_id("pgaudit"),
                "regulation_check",
                "regulate",
                "green",
                json.dumps(
                    {
                        "clause_gaps": len(clause_gaps),
                        "stale_assessments": len(stale_assessments),
                        "reg_scan_status": reg_scan.get("status", "unknown"),
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
        "metric_value": float(total_changes),
        "details": {
            "clause_gaps": len(clause_gaps),
            "stale_assessments": len(stale_assessments),
            "regulation_scan": reg_scan,
            "total_changes_detected": total_changes,
            "gap_details": [{"opportunity_id": g["id"], "title": g["title"]} for g in clause_gaps[:10]],
            "stale_details": [
                {"opportunity_id": s["opportunity_id"], "clause": s["clause_title"], "assessed_at": s["assessed_at"]}
                for s in stale_assessments[:10]
            ],
        },
    }


# CUI // SP-CTI
