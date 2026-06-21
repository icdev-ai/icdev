# CUI // SP-CTI
"""AI Augmentation Canvas (AAC) — Roadmap Generator.

generate_roadmap(scan_id, opportunities, scores) → dict

Phase bands (thresholds loaded from args/aac_config.yaml anomaly_detection.phase):
  P1 — composite_score ≥ p1_min_score  (Quick Wins)
  P2 — composite_score ≥ p2_min_score  (Core Modernization)
  P3 — composite_score ≥ p3_min_score  (Long-Horizon Investments)

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
_AAC_CONFIG_PATH = _ICDEV_ROOT / "args" / "aac_config.yaml"


def _load_phase_thresholds() -> tuple[float, float, float]:
    """Load phase score thresholds from args/aac_config.yaml.

    Falls back to (0.7, 0.5, 0.3) if config is unavailable.
    """
    try:
        import yaml  # type: ignore[import-untyped]
        with open(_AAC_CONFIG_PATH, encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        phase = cfg.get("anomaly_detection", {}).get("phase", {})
        p1 = float(phase.get("p1_min_score", 0.7))
        p2 = float(phase.get("p2_min_score", 0.5))
        p3 = float(phase.get("p3_min_score", 0.3))
        return p1, p2, p3
    except Exception:
        return 0.7, 0.5, 0.3


def _build_phase_defs() -> list[tuple[str, str, float, float | None]]:
    p1, p2, p3 = _load_phase_thresholds()
    return [
        ("P1", "Phase 1 — Quick Wins", p1, None),
        ("P2", "Phase 2 — Core Modernization", p2, p1),
        ("P3", "Phase 3 — Long-Horizon Investments", p3, p2),
    ]


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

    phases: list[dict] = []
    for phase_id, label, lo, hi in _build_phase_defs():
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
