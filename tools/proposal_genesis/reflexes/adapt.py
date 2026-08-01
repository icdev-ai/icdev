#!/usr/bin/env python3
# CUI // SP-CTI
"""R21: Adapt Reflex — Lightweight Shipley Process Adaptation (section 3.20).

Analyzes team size and opportunity complexity to recommend an appropriate
Shipley process level.  Prevents over-engineering small proposals and
under-resourcing large ones.

Process levels:
  - Level 1 (Lite):     Team <= 3, value < $500K, simple/SBIR
  - Level 2 (Standard): Team 4-8, value $500K-$5M, moderate complexity
  - Level 3 (Full):     Team > 8, value > $5M or IL4+, full Shipley

Schedule: on_demand.
GREEN tier (read-only analysis).
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

logger = get_logger("icdev.proposal_genesis.reflexes.adapt")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pgadp") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Process level determination
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Module-level constants — Shipley process-level tiering, overridable from
# proposal_genesis_config.yaml under reflexes.adapt.  Change config, not code.
# ---------------------------------------------------------------------------
_LEVEL1_MAX_TEAM     = 3          # Lite: team ≤ this
_LEVEL2_MAX_TEAM     = 8          # Standard: team ≤ this
_LEVEL3_MAX_TEAM     = 999        # Full: effectively unlimited
_LEVEL1_MAX_VALUE    = 500_000    # Lite: value < $500K
_LEVEL3_MIN_VALUE    = 5_000_000  # Full: value > $5M
_OPP_FETCH_LIMIT     = 20         # max opportunities processed per run

PROCESS_LEVELS = {
    1: {
        "label": "Lite",
        "description": "Streamlined process for small proposals",
        "reviews": ["pink"],
        "max_team": _LEVEL1_MAX_TEAM,
    },
    2: {
        "label": "Standard",
        "description": "Standard Shipley process with key reviews",
        "reviews": ["pink", "red"],
        "max_team": _LEVEL2_MAX_TEAM,
    },
    3: {
        "label": "Full",
        "description": "Full Shipley process with all color reviews",
        "reviews": ["pink", "red", "gold", "white_glove"],
        "max_team": _LEVEL3_MAX_TEAM,
    },
}


def _estimate_value(opp: Dict) -> float:
    """Parse estimated value from opportunity data."""
    val = opp.get("estimated_value") or 0
    if isinstance(val, str):
        try:
            val = float(val.replace(",", "").replace("$", ""))
        except (ValueError, TypeError):
            val = 0
    return float(val)


def _get_team_size(opp_id: str) -> int:
    """Count team members (teaming partners + prime = 1)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM pg_teaming_workshare WHERE opportunity_id = %s",
            (opp_id,),
        ).fetchone()
        partners = row["cnt"] if row else 0
        return partners + 1  # +1 for prime
    except Exception:
        return 1
    finally:
        conn.close()


def _determine_process_level(opp: Dict, team_size: int) -> Dict[str, Any]:
    """Determine recommended Shipley process level."""
    value = _estimate_value(opp)
    set_aside = (opp.get("set_aside") or "").lower()

    # Level 3 triggers
    if value > _LEVEL3_MIN_VALUE or team_size > _LEVEL2_MAX_TEAM:
        level = 3
        rationale = []
        if value > _LEVEL3_MIN_VALUE:
            rationale.append(f"High value (${value:,.0f})")
        if team_size > _LEVEL2_MAX_TEAM:
            rationale.append(f"Large team ({team_size} members)")
        return {
            "level": level,
            "rationale": "; ".join(rationale),
            **PROCESS_LEVELS[level],
        }

    # Level 1 triggers
    if team_size <= _LEVEL1_MAX_TEAM and value < _LEVEL1_MAX_VALUE:
        rationale = f"Small team ({team_size}), low value (${value:,.0f})"
        if set_aside in ("sbir", "sttr"):
            rationale += ", SBIR/STTR"
        return {
            "level": 1,
            "rationale": rationale,
            **PROCESS_LEVELS[1],
        }

    # Default: Level 2
    return {
        "level": 2,
        "rationale": f"Team size {team_size}, value ${value:,.0f}",
        **PROCESS_LEVELS[2],
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Adapt Reflex (R21).

    Steps:
      1. Find active opportunities
      2. For each: determine team size and estimated value
      3. Recommend appropriate Shipley process level
      4. Report distribution of process levels

    Returns standard reflex result dict.
    """
    conn = get_connection()
    try:
        rows = conn.execute(f"""
            SELECT id, title, estimated_value, set_aside, status
            FROM proposal_opportunities
            WHERE status IN ('tracking', 'drafting')
            ORDER BY created_at DESC
            LIMIT {_OPP_FETCH_LIMIT}
        """).fetchall()
        opportunities = [dict(r) for r in rows]
    except Exception:
        opportunities = []
    finally:
        conn.close()

    adapt_results: List[Dict] = []
    level_counts = {1: 0, 2: 0, 3: 0}

    for opp in opportunities:
        team_size = _get_team_size(opp["id"])
        recommendation = _determine_process_level(opp, team_size)

        level = recommendation["level"]
        level_counts[level] = level_counts.get(level, 0) + 1

        adapt_results.append(
            {
                "opportunity_id": opp["id"],
                "title": opp["title"],
                "team_size": team_size,
                "estimated_value": _estimate_value(opp),
                "recommended_level": level,
                "level_label": recommendation["label"],
                "reviews": recommendation["reviews"],
                "rationale": recommendation["rationale"],
            }
        )

    # Compute adaptation score: weighted average of levels (1.0 = all lite, 3.0 = all full)
    if adapt_results:
        avg_level = sum(r["recommended_level"] for r in adapt_results) / len(adapt_results)
    else:
        avg_level = 0.0

    # Audit
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, details, success, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                _generate_id("pgaudit"),
                "process_adaptation",
                "adapt",
                "green",
                json.dumps(
                    {
                        "opportunities_assessed": len(opportunities),
                        "level_distribution": level_counts,
                        "avg_level": round(avg_level, 2),
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
        "metric_value": round(avg_level, 2),
        "details": {
            "opportunities_assessed": len(opportunities),
            "level_distribution": level_counts,
            "avg_level": round(avg_level, 2),
            "adapt_results": adapt_results,
        },
    }


# CUI // SP-CTI
