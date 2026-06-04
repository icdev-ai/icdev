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
import statistics
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    top_competitors = sorted(competitor_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    # Top skills in demand
    top_skills = sorted(skill_counts.items(), key=lambda x: x[1], reverse=True)[:15]

    return {
        "competitors": dict(top_competitors),
        "clearance_heavy": {k: v for k, v in clearance_counts.items() if v >= 2},
        "top_skills": dict(top_skills),
        "total_signals": len(signals),
    }


def _compute_surge_threshold(
    counts: List[int], multiplier: float = 1.5, floor: int = 2
) -> float:
    """Dynamically derive an anomaly threshold from the posting-volume
    distribution.

    A hiring "surge" is volume that is statistically unusual relative to the
    cohort, computed as ``mean + multiplier * population_stdev``. This adapts
    to the size and spread of the current signal set instead of relying on a
    fixed magic number. ``floor`` guards against flagging trivially small
    samples when the spread is near zero.
    """
    if not counts:
        return float(floor)
    mean = statistics.mean(counts)
    spread = statistics.pstdev(counts) if len(counts) > 1 else 0.0
    dynamic = mean + multiplier * spread
    return max(float(floor), dynamic)


def _detect_surges(
    signals: List[Dict],
    threshold: Optional[float] = None,
    multiplier: float = 1.5,
) -> List[Dict]:
    """Detect hiring surges — competitors with unusually high posting volume.

    When ``threshold`` is ``None`` the cutoff is derived dynamically from the
    distribution of competitor posting volumes (see
    :func:`_compute_surge_threshold`).
    """
    competitor_counts: Dict[str, int] = {}
    for sig in signals:
        name = sig.get("competitor_name", "unknown")
        competitor_counts[name] = competitor_counts.get(name, 0) + 1

    if threshold is None:
        threshold = _compute_surge_threshold(
            list(competitor_counts.values()), multiplier=multiplier
        )

    surges = []
    for name, count in competitor_counts.items():
        if count >= threshold:
            surges.append(
                {
                    "competitor": name,
                    "postings": count,
                    "signal": "hiring_surge",
                }
            )

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
    # Threshold defaults to a dynamic, distribution-derived cutoff. An explicit
    # override may be supplied via config; the multiplier tunes sensitivity of
    # the dynamic calculation.
    surge_threshold = config.get("talent_surge_threshold")
    surge_multiplier = config.get("talent_surge_multiplier", 1.5)

    signals = _get_recent_signals(days=lookback_days)
    velocity = _compute_velocity(signals)
    surges = _detect_surges(
        signals, threshold=surge_threshold, multiplier=surge_multiplier
    )

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
                json.dumps(
                    {
                        "signals_analyzed": len(signals),
                        "surges_detected": len(surges),
                        "lookback_days": lookback_days,
                    }
                ),
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
