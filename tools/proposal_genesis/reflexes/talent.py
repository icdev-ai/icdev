#!/usr/bin/env python3
# CUI // SP-CTI
"""R20: Talent Reflex — Talent Intelligence (section 3.16).

Monitors competitor job postings for competitive intelligence signals.
Analyzes hiring patterns to detect capability build-up and strategic shifts.

Schedule: daily scan, weekly aggregation.
GREEN tier (read-only DB queries + signal velocity metrics).
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


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pgtal") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Talent signal analysis
# ---------------------------------------------------------------------------

def _get_recent_signals(days: int = 30) -> List[Dict]:
    """Get talent signals from the last N days."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, competitor_name, role_title, skill_tags, "
            "location, clearance_required, scan_date "
            "FROM pg_talent_signals "
            "WHERE scan_date >= datetime('now', ?) "
            "ORDER BY scan_date DESC "
            "LIMIT 200",
            (f"-{days} days",),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _compute_velocity(signals: List[Dict]) -> Dict[str, Any]:
    """Compute hiring velocity metrics per competitor."""
    if not signals:
        return {"competitors": {}, "total_signals": 0}

    competitor_counts: Dict[str, int] = {}
    clearance_counts: Dict[str, int] = {}
    skill_counts: Dict[str, int] = {}

    for sig in signals:
        name = sig.get("competitor_name", "unknown")
        competitor_counts[name] = competitor_counts.get(name, 0) + 1

        if sig.get("clearance_required"):
            clearance_counts[name] = clearance_counts.get(name, 0) + 1

        # Parse skill tags (stored as comma-separated or JSON)
        tags = sig.get("skill_tags") or ""
        if tags.startswith("["):
            try:
                tag_list = json.loads(tags)
            except (json.JSONDecodeError, ValueError):
                tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        else:
            tag_list = [t.strip() for t in tags.split(",") if t.strip()]

        for tag in tag_list:
            skill_counts[tag.lower()] = skill_counts.get(tag.lower(), 0) + 1

    # Top competitors by hiring volume
    top_competitors = sorted(
        competitor_counts.items(), key=lambda x: x[1], reverse=True
    )[:10]

    # Top skills in demand
    top_skills = sorted(
        skill_counts.items(), key=lambda x: x[1], reverse=True
    )[:15]

    return {
        "competitors": dict(top_competitors),
        "clearance_heavy": {
            k: v for k, v in clearance_counts.items() if v >= 2
        },
        "top_skills": dict(top_skills),
        "total_signals": len(signals),
    }


def _detect_surges(signals: List[Dict], threshold: int = 5) -> List[Dict]:
    """Detect hiring surges — competitors with unusually high posting volume."""
    competitor_counts: Dict[str, int] = {}
    for sig in signals:
        name = sig.get("competitor_name", "unknown")
        competitor_counts[name] = competitor_counts.get(name, 0) + 1

    surges = []
    for name, count in competitor_counts.items():
        if count >= threshold:
            surges.append({
                "competitor": name,
                "postings": count,
                "signal": "hiring_surge",
            })

    return sorted(surges, key=lambda x: x["postings"], reverse=True)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Talent Reflex (R20).

    Steps:
      1. Query recent talent signals from pg_talent_signals
      2. Compute hiring velocity per competitor
      3. Detect hiring surges above threshold
      4. Identify clearance-heavy hiring (may indicate classified work)

    Returns standard reflex result dict.
    """
    lookback_days = config.get("talent_lookback_days", 30)
    surge_threshold = config.get("talent_surge_threshold", 5)

    signals = _get_recent_signals(days=lookback_days)
    velocity = _compute_velocity(signals)
    surges = _detect_surges(signals, threshold=surge_threshold)

    # Audit
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, details, success, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _generate_id("pgaudit"),
                "talent_scan",
                "talent",
                "green",
                json.dumps({
                    "signals_analyzed": len(signals),
                    "surges_detected": len(surges),
                    "lookback_days": lookback_days,
                }),
                1,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

    return {
        "success": True,
        "metric_value": float(len(signals)),
        "details": {
            "signals_analyzed": len(signals),
            "lookback_days": lookback_days,
            "top_competitors": velocity.get("competitors", {}),
            "top_skills": velocity.get("top_skills", {}),
            "clearance_heavy_hiring": velocity.get("clearance_heavy", {}),
            "hiring_surges": surges,
        },
    }
# CUI // SP-CTI
