#!/usr/bin/env python3
# CUI // SP-CTI
"""R5: Extract Reflex — shall statement mining + amendment re-extraction.

Wraps tools/govcon/requirement_extractor.py.
Scanner-tier only (zero Claude tokens).
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_for_opportunity(opp_id: str) -> Dict[str, Any]:
    """Extract shall statements for a single opportunity."""
    try:
        from tools.govcon.requirement_extractor import extract_and_store

        result = extract_and_store(opp_id)
        return result if isinstance(result, dict) else {"status": "ok", "extracted": 0}
    except ImportError:
        return {"status": "import_error", "message": "requirement_extractor not available"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _re_extract_amendments() -> Dict[str, Any]:
    """Re-extract requirements from amended sections."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT opportunity_id FROM pg_amendment_diffs WHERE re_extracted = 0 LIMIT 10"
        ).fetchall()

        re_extracted = 0
        for row in rows:
            opp_id = row["opportunity_id"]
            result = _extract_for_opportunity(opp_id)
            if result.get("status") != "error":
                # Mark amendment diffs as re-extracted
                conn.execute("UPDATE pg_amendment_diffs SET re_extracted = 1 WHERE opportunity_id = %s", (opp_id,))
                conn.commit()
                re_extracted += 1

        return {"re_extracted_opportunities": re_extracted}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Extract Reflex (R5).

    Triggered after R1 Discover. Extracts shall/must/should statements
    from new and amended opportunities.
    """
    conn = get_connection()
    try:
        # Find opportunities needing extraction (recently scanned, no statements yet)
        rows = conn.execute("""
            SELECT po.id, po.title
            FROM proposal_opportunities po
            LEFT JOIN rfp_shall_statements ss ON ss.opportunity_id = po.id
            WHERE po.status IN ('tracking', 'drafting')
            AND ss.id IS NULL
            ORDER BY po.created_at DESC
            LIMIT 10
        """).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    total_extracted = 0
    extraction_results = []

    for row in rows:
        result = _extract_for_opportunity(row["id"])
        extracted = result.get("extracted", result.get("total", 0))
        total_extracted += extracted if isinstance(extracted, int) else 0
        extraction_results.append(
            {
                "opportunity_id": row["id"],
                "title": row["title"],
                "result": result,
            }
        )

    # Also re-extract from amended opportunities
    amend_result = _re_extract_amendments()
    re_extracted = amend_result.get("re_extracted_opportunities", 0)

    return {
        "success": True,
        "metric_value": float(total_extracted + re_extracted),
        "details": {
            "opportunities_processed": len(rows),
            "total_statements_extracted": total_extracted,
            "amendment_re_extractions": re_extracted,
            "extraction_results": extraction_results,
        },
    }
