# CUI // SP-CTI
"""I&W Indicators Board — Indicator and Warning system.

Tracks named intelligence indicators mapped to adversary Courses of Action (COAs).
Each indicator has a weight; the board computes a probability score for each COA
based on observed/not-observed/denied indicator states.

Distinct from the IW Effects (PMESII-PT) board at /strategos/iw.
This module covers classical J2 I&W: indicator → COA probability mapping.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.strategos.iw_indicators")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_indicator(
    name: str,
    coa_id: str,
    coa_name: str,
    description: str = "",
    weight: float = 1.0,
    category: str = "general",
    theater: str = "global",
) -> dict[str, Any]:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        ind_id = str(uuid.uuid4())
        now = _now_utc()
        conn.execute(
            "INSERT INTO sg_iw_indicators "
            "(id, name, description, coa_id, coa_name, weight, category, theater, "
            " status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'not_observed', ?, ?)",
            (ind_id, name, description, coa_id, coa_name,
             weight, category, theater, now, now),
        )
        conn.commit()
        return {"id": ind_id, "name": name, "coa_id": coa_id}
    finally:
        conn.close()


def record_observation(
    indicator_id: str,
    observed_status: str,
    source: str = "",
    notes: str = "",
    confidence: float = 0.5,
) -> dict[str, Any]:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        obs_id = str(uuid.uuid4())
        now = _now_utc()
        conn.execute(
            "INSERT INTO sg_iw_observations "
            "(id, indicator_id, observed_status, source, notes, confidence, "
            " observed_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (obs_id, indicator_id, observed_status, source, notes, confidence, now, now),
        )
        conn.execute(
            f"UPDATE sg_iw_indicators SET status = {ph}, updated_at = {ph} "  # nosec B608
            f"WHERE id = {ph}",
            (observed_status, now, indicator_id),
        )
        conn.commit()
        return {"observation_id": obs_id, "status": observed_status}
    finally:
        conn.close()


def compute_coa_probabilities(theater: str = "global") -> list[dict[str, Any]]:
    """Compute weighted probability for each COA from indicator observations.

    Score = sum(weight * multiplier) / sum(weight) for each COA
    where multiplier: observed=1.0, not_observed=0.0, denied=-0.5
    """
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        if theater == "global":
            rows = conn.execute(
                "SELECT id, coa_id, coa_name, weight, status "  # nosec B608
                "FROM sg_iw_indicators WHERE status != 'obsolete'"
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT id, coa_id, coa_name, weight, status "  # nosec B608
                f"FROM sg_iw_indicators WHERE theater = {ph} AND status != 'obsolete'",
                (theater,),
            ).fetchall()

        cols = ("id", "coa_id", "coa_name", "weight", "status")
        indicators = [dict(zip(cols, r)) for r in rows]

        multiplier = {"observed": 1.0, "not_observed": 0.0, "denied": -0.5}
        coa_scores: dict[str, dict] = defaultdict(
            lambda: {"coa_name": "", "weighted_sum": 0.0, "total_weight": 0.0, "count": 0}
        )

        for ind in indicators:
            cid = ind["coa_id"]
            w = float(ind["weight"] or 1.0)
            m = multiplier.get(ind["status"] or "not_observed", 0.0)
            coa_scores[cid]["coa_name"] = ind["coa_name"]
            coa_scores[cid]["weighted_sum"] += w * m
            coa_scores[cid]["total_weight"] += w
            coa_scores[cid]["count"] += 1

        results = []
        for coa_id, data in coa_scores.items():
            tw = data["total_weight"]
            raw_score = data["weighted_sum"] / tw if tw > 0 else 0.0
            # Normalise to [0, 1]
            probability = max(0.0, min(1.0, (raw_score + 0.5)))
            results.append({
                "coa_id": coa_id,
                "coa_name": data["coa_name"],
                "probability": round(probability, 3),
                "indicator_count": data["count"],
            })

        results.sort(key=lambda x: x["probability"], reverse=True)
        return results
    finally:
        conn.close()


def list_indicators(theater: str = "global", coa_id: str = "") -> list[dict]:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        base = (
            "SELECT id, name, description, coa_id, coa_name, weight, "
            "category, theater, status, created_at "
            "FROM sg_iw_indicators"
        )
        clauses, params = [], []
        if theater and theater != "global":
            clauses.append(f"theater = {ph}")
            params.append(theater)
        if coa_id:
            clauses.append(f"coa_id = {ph}")
            params.append(coa_id)
        if clauses:
            base += " WHERE " + " AND ".join(clauses)
        base += " ORDER BY coa_id, name"
        rows = conn.execute(base, params).fetchall()  # nosec B608
        cols = ("id", "name", "description", "coa_id", "coa_name",
                "weight", "category", "theater", "status", "created_at")
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def update_indicator_status(indicator_id: str, status: str) -> bool:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE sg_iw_indicators SET status = {ph}, updated_at = {ph} WHERE id = {ph}",  # nosec B608
            (status, _now_utc(), indicator_id),
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.error("update_indicator_status failed: %s", exc)
        return False
    finally:
        conn.close()


def delete_indicator(indicator_id: str) -> bool:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(
            f"DELETE FROM sg_iw_observations WHERE indicator_id = {ph}", (indicator_id,)  # nosec B608
        )
        conn.execute(
            f"DELETE FROM sg_iw_indicators WHERE id = {ph}", (indicator_id,)  # nosec B608
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.error("delete_indicator failed: %s", exc)
        return False
    finally:
        conn.close()
