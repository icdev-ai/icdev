#!/usr/bin/env python3
# CUI // SP-CTI
"""R6: Map Reflex — expanded capability matching (ICDEV + consulting + partners).

Wraps tools/govcon/capability_mapper.py with expanded catalog (D-PG-6).
Scanner-tier only (zero Claude tokens).
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _map_opportunity(opp_id: str) -> Dict[str, Any]:
    """Map capabilities for a single opportunity using existing mapper."""
    try:
        from tools.govcon.capability_mapper import map_all_patterns, get_coverage

        map_all_patterns(opp_id)
        coverage = get_coverage(opp_id)
        return coverage if isinstance(coverage, dict) else {"status": "ok"}
    except ImportError:
        return {"status": "import_error", "message": "capability_mapper not available"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _get_partner_capabilities() -> List[Dict[str, str]]:
    """Load teaming partner capabilities from DB (D-PG-6)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, capabilities FROM pg_teaming_partners "
            "WHERE status = 'active' AND capabilities IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _enrich_with_knowledge_base(opp_id: str) -> Dict[str, Any]:
    """Enrich capability mapping with knowledge base past performance (D-PG-4)."""
    try:
        from tools.govcon.knowledge_base import search_blocks

        conn = get_connection()
        try:
            # Get requirement patterns for this opportunity
            rows = conn.execute(
                "SELECT pattern_text FROM rfp_requirement_patterns WHERE opportunity_id = ? LIMIT 20", (opp_id,)
            ).fetchall()
        finally:
            conn.close()

        kb_matches = 0
        for row in rows:
            try:
                results = search_blocks(row["pattern_text"], limit=3)
                if isinstance(results, list) and len(results) > 0:
                    kb_matches += 1
            except Exception:
                pass

        return {"kb_patterns_matched": kb_matches, "patterns_checked": len(rows)}
    except ImportError:
        return {"status": "kb_not_available"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Map Reflex (R6).

    Triggered after R5 Extract. Maps extracted requirements to expanded
    capability catalog (ICDEV + consulting services + domain expertise +
    partner/teaming capabilities).
    """
    conn = get_connection()
    try:
        # Find opportunities with extracted statements but no capability mapping
        rows = conn.execute("""
            SELECT DISTINCT po.id, po.title
            FROM proposal_opportunities po
            INNER JOIN rfp_shall_statements ss ON ss.opportunity_id = po.id
            LEFT JOIN icdev_capability_map cm ON cm.opportunity_id = po.id
            WHERE po.status IN ('tracking', 'drafting')
            AND cm.id IS NULL
            ORDER BY po.created_at DESC
            LIMIT 10
        """).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    total_coverage = 0.0
    mapping_results = []
    partner_caps = _get_partner_capabilities()

    for row in rows:
        result = _map_opportunity(row["id"])
        coverage = result.get("coverage_score", result.get("score", 0))
        if isinstance(coverage, (int, float)):
            total_coverage += coverage

        # Enrich with knowledge base
        kb_result = _enrich_with_knowledge_base(row["id"])

        mapping_results.append(
            {
                "opportunity_id": row["id"],
                "title": row["title"],
                "coverage": result,
                "kb_enrichment": kb_result,
            }
        )

    avg_coverage = total_coverage / len(rows) if rows else 0

    return {
        "success": True,
        "metric_value": round(avg_coverage, 2),
        "details": {
            "opportunities_mapped": len(rows),
            "avg_coverage_score": round(avg_coverage, 2),
            "partner_capabilities_available": len(partner_caps),
            "mapping_results": mapping_results,
        },
    }
