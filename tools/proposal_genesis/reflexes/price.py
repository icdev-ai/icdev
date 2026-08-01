#!/usr/bin/env python3
# CUI // SP-CTI
"""R17: Price Reflex — Cost Volume Automation (section 3.13).

Generates pricing from PWS task descriptions.  On-demand, triggered after
R6 Map.  Checks pg_cost_volumes for opportunities needing pricing updates.

Pipeline: on_demand (triggered after R6 Map).
GREEN tier (read-only DB queries + cost volume status checks).
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

logger = get_logger("icdev.proposal_genesis.reflexes.price")

# ---------------------------------------------------------------------------
# Module-level constants — Price Reflex (R17) thresholds & limits.
# Extracted from inline magic numbers (AI-ify opp 5427, hardcoded_threshold).
# Overridable from proposal_genesis_config.yaml under reflexes.price.
# Change config, not code.
# ---------------------------------------------------------------------------
_VOLUMES_UPDATE_LIMIT          = 20  # draft/needs_update cost volumes scanned per run
_OPPS_WITHOUT_PRICING_LIMIT    = 10  # tracked opps lacking a cost volume fetched per run
_MIN_LINE_ITEMS_FOR_COMPLETE   = 1   # line-item count at/above which a volume is "populated"
_INCOMPLETE_DETAILS_LIMIT      = 10  # incomplete volumes surfaced in the details payload
_MISSING_PRICING_DETAILS_LIMIT = 10  # missing-pricing opps surfaced in the details payload


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pgprc") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Cost volume status checking
# ---------------------------------------------------------------------------


def _get_volumes_needing_update() -> List[Dict]:
    """Find cost volumes in draft status that need pricing updates."""
    conn = get_connection()
    try:
        rows = conn.execute(f"""
            SELECT cv.id, cv.opportunity_id, cv.status, cv.total_price,
                   cv.updated_at, po.title
            FROM pg_cost_volumes cv
            JOIN proposal_opportunities po ON po.id = cv.opportunity_id
            WHERE cv.status IN ('draft', 'needs_update')
            AND po.status IN ('tracking', 'drafting')
            ORDER BY cv.updated_at ASC
            LIMIT {_VOLUMES_UPDATE_LIMIT}
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _get_opportunities_without_pricing() -> List[Dict]:
    """Find tracked opportunities that have no cost volume yet."""
    conn = get_connection()
    try:
        rows = conn.execute(f"""
            SELECT po.id, po.title, po.estimated_value
            FROM proposal_opportunities po
            LEFT JOIN pg_cost_volumes cv ON cv.opportunity_id = po.id
            WHERE po.status IN ('tracking', 'drafting')
            AND cv.id IS NULL
            ORDER BY po.created_at DESC
            LIMIT {_OPPS_WITHOUT_PRICING_LIMIT}
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _check_line_item_coverage(volume_id: str) -> Dict[str, Any]:
    """Check if cost volume has line items populated."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM pg_cost_line_items WHERE cost_volume_id = %s",
            (volume_id,),
        ).fetchone()
        count = row["cnt"] if row else 0
        return {"line_items": count, "has_items": count >= _MIN_LINE_ITEMS_FOR_COMPLETE}
    except Exception:
        return {"line_items": 0, "has_items": False}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Price Reflex (R17).

    Cost volume automation requires manual input for labor rates and
    indirect cost structures.  This reflex:
      1. Identifies opportunities needing pricing
      2. Checks existing cost volume completeness
      3. Flags volumes with missing line items
      4. Reports opportunities without any cost volume

    Returns standard reflex result dict.
    """
    # Check existing volumes needing updates
    volumes = _get_volumes_needing_update()
    volumes_checked = 0
    incomplete_volumes: List[Dict] = []

    for vol in volumes:
        coverage = _check_line_item_coverage(vol["id"])
        volumes_checked += 1
        if not coverage["has_items"]:
            incomplete_volumes.append(
                {
                    "volume_id": vol["id"],
                    "opportunity_id": vol["opportunity_id"],
                    "title": vol.get("title", ""),
                    "status": vol["status"],
                    "line_items": coverage["line_items"],
                }
            )

    # Check for opportunities without any cost volume
    missing_pricing = _get_opportunities_without_pricing()

    # Audit
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, details, success, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                _generate_id("pgaudit"),
                "price_check",
                "price",
                "green",
                json.dumps(
                    {
                        "volumes_checked": volumes_checked,
                        "incomplete": len(incomplete_volumes),
                        "missing_pricing": len(missing_pricing),
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

    total_issues = len(incomplete_volumes) + len(missing_pricing)

    return {
        "success": True,
        "metric_value": float(volumes_checked),
        "details": {
            "volumes_checked": volumes_checked,
            "incomplete_volumes": len(incomplete_volumes),
            "opportunities_missing_pricing": len(missing_pricing),
            "total_pricing_issues": total_issues,
            "incomplete_details": incomplete_volumes[:_INCOMPLETE_DETAILS_LIMIT],
            "missing_pricing_details": [
                {"opportunity_id": m["id"], "title": m["title"]}
                for m in missing_pricing[:_MISSING_PRICING_DETAILS_LIMIT]
            ],
        },
    }


# CUI // SP-CTI
