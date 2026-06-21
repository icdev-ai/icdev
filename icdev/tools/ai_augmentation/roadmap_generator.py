# CUI // SP-CTI
"""AI Augmentation Canvas (AAC) — Roadmap Generator.

generate_roadmap(scan_id, opportunities, scores) → dict

Phase bands (computed adaptively from score distribution; see _adaptive_thresholds):
  P1 — composite_score ≥ adaptive_p1  (Quick Wins)
  P2 — composite_score ≥ adaptive_p2  (Core Modernization)
  P3 — composite_score ≥ adaptive_p3  (Long-Horizon Investments)

effort_days = midpoint of pattern_catalog effort_days_min/max.
Persists result to aac_roadmaps via get_connection().
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.ai_augmentation.db.init_db import get_connection

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
_CATALOG_PATH = _ICDEV_ROOT / "context" / "ai_augmentation" / "pattern_catalog.json"

# Static fallback thresholds — used when the sample is too small for percentile
# estimation.  Replaced at runtime by _adaptive_thresholds() (AAC opp-560).
_DEFAULT_P1 = 0.7
_DEFAULT_P2 = 0.5
_DEFAULT_P3 = 0.3
_MIN_ADAPTIVE_SAMPLES = 5  # minimum scored opportunities needed for adaptive mode


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Return the pct-th percentile of a pre-sorted list (linear interpolation)."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    k = (n - 1) * pct / 100.0
    lo = int(k)
    hi = min(lo + 1, n - 1)
    return sorted_values[lo] + (k - lo) * (sorted_values[hi] - sorted_values[lo])


def _adaptive_thresholds(composite_scores: list[float]) -> tuple[float, float, float]:
    """Compute phase boundary thresholds from the actual score distribution.

    Uses percentile-based anomaly detection to detect natural breakpoints
    rather than hardcoded literal comparisons (AAC opportunity 560).
    Falls back to static defaults when fewer than _MIN_ADAPTIVE_SAMPLES
    valid scores are present.

    Returns:
        (p1, p2, p3) where p1 > p2 > p3 — the lower bound of each phase tier.
    """
    valid = [s for s in composite_scores if 0.0 <= s <= 1.0]
    if len(valid) < _MIN_ADAPTIVE_SAMPLES:
        return _DEFAULT_P1, _DEFAULT_P2, _DEFAULT_P3

    sorted_scores = sorted(valid)
    p1 = max(round(_percentile(sorted_scores, 66.7), 2), _DEFAULT_P3 + 0.1)
    p2 = max(round(_percentile(sorted_scores, 33.3), 2), _DEFAULT_P3)

    # Enforce strict ordering with a minimum 0.05 gap between tiers
    if p2 >= p1:
        p2 = round(p1 - 0.05, 2)
    p3 = _DEFAULT_P3  # P3 qualifying floor is kept fixed

    return p1, p2, p3


def _load_catalog() -> dict[str, dict]:
    try:
        with open(_CATALOG_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return {p["id"]: p for p in data.get("patterns", [])}
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return {}


def _effort_days(catalog_entry: dict) -> int:
    lo = catalog_entry.get("effort_days_min", 0)
    hi = catalog_entry.get("effort_days_max", lo)
    return (lo + hi) // 2


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dump(value: Any) -> Any:
    """Return value for storage: Json-wrapped for PG JSONB, serialized string for SQLite TEXT."""
    import os
    backend = os.environ.get(
        "AAC_STORAGE_BACKEND",
        os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
    ).lower()
    if backend == "postgresql":
        try:
            from psycopg2.extras import Json
            return Json(value)
        except ImportError:
            pass
    return json.dumps(value)


def _insert(conn: Any, scan_id: int, roadmap_id: str, title: str,
            phases: Any, total_effort: int, aimc: Any, aadc: Any) -> None:
    sql = (
        "INSERT INTO aac_roadmaps "
        "(scan_id, roadmap_id, title, phases, total_effort_days, aimc_links, aadc_links) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    params = (scan_id, roadmap_id, title, phases, total_effort, aimc, aadc)
    try:
        conn.execute(sql, params)
    except Exception:
        conn.execute(sql.replace("?", "%s"), params)


def generate_roadmap(scan_id: str, opportunities: list[dict], scores: list[dict]) -> dict:
    """Generate a prioritized AI augmentation roadmap.

    Args:
        scan_id:       Scan identifier (coerced to int for the FK column).
        opportunities: List of opportunity dicts from aac_opportunities.
        scores:        List of score dicts from aac_scores.

    Returns:
        Roadmap dict: phases (list), aimc_links, aadc_links, total_effort_days.
    """
    catalog = _load_catalog()

    score_index: dict[int, dict] = {int(s["opportunity_id"]): s for s in scores}

    enriched: list[dict] = []
    for opp in opportunities:
        opp_id = int(opp.get("opportunity_id", 0))
        score = score_index.get(opp_id, {})
        composite = float(score.get("composite_score", 0.0))
        pattern_type = opp.get("pattern_type", "")
        paradigm = opp.get("ai_paradigm", "")
        cat = catalog.get(pattern_type, {})

        enriched.append({
            "opportunity_id": opp_id,
            "module_path": opp.get("module_path", ""),
            "function_name": opp.get("function_name", ""),
            "pattern_type": pattern_type,
            "ai_paradigm": paradigm,
            "il_recommended_model": opp.get("il_recommended_model", ""),
            "effort_days": _effort_days(cat),
            "aimc_link": f"/ai-ml/models/new?paradigm={paradigm}",
            "aadc_link": (
                f"/agentic-ai/topologies/new?trigger={opp_id}"
                if paradigm == "agentic_trigger"
                else None
            ),
            "composite_score": composite,
            "value_score": float(score.get("value_score", 0.0)),
            "feasibility_score": float(score.get("feasibility_score", 0.0)),
            "risk_score": float(score.get("risk_score", 0.0)),
        })

    enriched.sort(key=lambda x: x["composite_score"], reverse=True)

    # Derive phase boundaries from the actual score distribution (adaptive anomaly
    # detection) rather than fixed literal thresholds.
    all_composite = [o["composite_score"] for o in enriched]
    p1, p2, p3 = _adaptive_thresholds(all_composite)
    phase_defs = [
        ("P1", "Phase 1 — Quick Wins", p1, None),
        ("P2", "Phase 2 — Core Modernization", p2, p1),
        ("P3", "Phase 3 — Long-Horizon Investments", p3, p2),
    ]

    phases: list[dict] = []
    for phase_id, label, lo, hi in phase_defs:
        if hi is None:
            bucket = [o for o in enriched if o["composite_score"] >= lo]
        else:
            bucket = [o for o in enriched if lo <= o["composite_score"] < hi]

        phase_effort = sum(o["effort_days"] for o in bucket)
        phases.append({
            "phase_id": phase_id,
            "label": label,
            "min_score": lo,
            "opportunities": bucket,
            "total_effort_days": phase_effort,
            "count": len(bucket),
        })

    total_effort_days = sum(p["total_effort_days"] for p in phases)

    aimc_links = [
        {"opportunity_id": o["opportunity_id"], "aimc_url": o["aimc_link"]}
        for o in enriched
    ]
    aadc_links = [
        {"opportunity_id": o["opportunity_id"], "aadc_url": o["aadc_link"]}
        for o in enriched
        if o["ai_paradigm"] == "agentic_trigger"
    ]

    roadmap_id = f"rm-{uuid.uuid4().hex[:10]}"
    title = f"AI Augmentation Roadmap — Scan {scan_id}"
    created_at = _now()

    roadmap = {
        "roadmap_id": roadmap_id,
        "scan_id": scan_id,
        "title": title,
        "phases": phases,
        "aimc_links": aimc_links,
        "aadc_links": aadc_links,
        "total_effort_days": total_effort_days,
        "created_at": created_at,
    }

    conn = get_connection()
    try:
        _insert(
            conn,
            int(scan_id),
            roadmap_id,
            title,
            _dump(phases),
            total_effort_days,
            _dump(aimc_links),
            _dump(aadc_links),
        )
        conn.commit()
    finally:
        conn.close()

    return roadmap
