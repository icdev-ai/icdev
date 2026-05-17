# CUI // SP-CTI
"""ICDEV™ Threat Analysis Service — baseline score validation.

Pure business logic that compares an incoming indicator score against the
stored baseline threshold hierarchy:

    user → project → tenant → platform → global

The most specific active baseline wins.  If no baseline exists the score is
considered unbounded (valid but unclassified).

NIST 800-53: SI-4 (System Monitoring), RA-5 (Vulnerability Monitoring).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("icdev.threat_analysis.service")


# Scope specificity — higher index = more specific
_SCOPE_RANK = {
    "global": 0,
    "platform": 1,
    "tenant": 2,
    "project": 3,
    "user": 4,
}

_DEFAULT_SEVERITY_BAND = "medium"


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_conn(db_path: str | None = None) -> Any:
    from icdev.tools.db.storage import get_connection
    return get_connection(db_path)


def create_baseline(
    indicator_name: str,
    threshold_score: float,
    scope: str = "project",
    scope_id: str = "",
    indicator_category: str = "general",
    severity_band: str = "medium",
    operator_id: str = "",
    rationale: str = "",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Persist a new indicator baseline.

    Args:
        indicator_name: Human-readable indicator key.
        threshold_score: Numeric threshold the indicator must not exceed.
        scope: One of global, platform, tenant, project, user.
        scope_id: Identifier for the scoped entity (optional for global).
        indicator_category: Logical grouping, e.g. 'network', 'endpoint'.
        severity_band: low / medium / high / critical.
        operator_id: User or system that created the baseline.
        rationale: Why this threshold was chosen.

    Returns:
        Dict with ``id``, ``indicator_name``, ``threshold_score``.
    """
    if scope not in _SCOPE_RANK:
        raise ValueError(f"Invalid scope: {scope}")
    if threshold_score < 0:
        raise ValueError("threshold_score must be non-negative")

    baseline_id = str(uuid.uuid4())
    now = _now_utc()
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO indicator_baselines "
            "(id, indicator_name, indicator_category, scope, scope_id, "
            " threshold_score, severity_band, operator_id, rationale, "
            " is_active, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                baseline_id,
                indicator_name,
                indicator_category,
                scope,
                scope_id,
                threshold_score,
                severity_band,
                operator_id,
                rationale,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "id": baseline_id,
        "indicator_name": indicator_name,
        "threshold_score": threshold_score,
        "scope": scope,
        "scope_id": scope_id,
        "severity_band": severity_band,
    }


def validate_indicator_score(
    indicator_name: str,
    score: float,
    scope_id: str = "",
    scope: str = "project",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Compare an incoming indicator score against the active baseline hierarchy.

    Lookup order (most-specific first):
        1. ``scope`` + ``scope_id`` (e.g. project=proj-123)
        2. Less-specific scopes matching the provided scope_id context
        3. Global fallback

    If no baseline is found the score is marked ``unbounded`` — it passes
    validation but carries no severity classification.

    Args:
        indicator_name: The indicator key to look up.
        score: The observed numeric score.
        scope_id: The entity ID for the requested scope level.
        scope: The *starting* scope level to search from.

    Returns:
        Dict containing:
        - ``indicator_name``
        - ``score``
        - ``valid`` (bool) — ``True`` when score <= threshold or no baseline
        - ``exceeded`` (bool) — ``True`` when score > threshold
        - ``threshold`` (float | None)
        - ``severity_band`` (str | None)
        - ``baseline_id`` (str | None)
        - ``scope_used`` (str | None)
        - ``delta`` (float | None) — amount by which score exceeds threshold
        - ``unbounded`` (bool) — ``True`` when no baseline was found
    """
    if score < 0:
        raise ValueError("score must be non-negative")

    conn = _get_conn(db_path)
    try:
        # Fetch all active baselines for this indicator, ordered by specificity
        rows = conn.execute(
            "SELECT id, scope, scope_id, threshold_score, severity_band "
            "FROM indicator_baselines "
            "WHERE indicator_name = ? AND is_active = 1 "
            "ORDER BY CASE scope "
            "  WHEN 'user'    THEN 4 "
            "  WHEN 'project' THEN 3 "
            "  WHEN 'tenant'  THEN 2 "
            "  WHEN 'platform'THEN 1 "
            "  ELSE 0 "
            "END DESC",
            (indicator_name,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {
            "indicator_name": indicator_name,
            "score": score,
            "valid": True,
            "exceeded": False,
            "threshold": None,
            "severity_band": None,
            "baseline_id": None,
            "scope_used": None,
            "delta": None,
            "unbounded": True,
        }

    # Build lookup chain from most-specific to least-specific
    requested_rank = _SCOPE_RANK.get(scope, 0)
    chain = [s for s, r in sorted(_SCOPE_RANK.items(), key=lambda x: x[1], reverse=True)
             if r <= requested_rank]

    # Find the first matching baseline in the specificity chain
    chosen = None
    for scope_level in chain:
        for row in rows:
            row_scope = row[1]
            row_scope_id = row[2]
            if row_scope == scope_level:
                # For global scope, scope_id is optional; for others it must match
                if scope_level == "global" or row_scope_id == scope_id:
                    chosen = row
                    break
        if chosen:
            break

    # If no scoped match, fall back to global baseline
    if not chosen:
        for row in rows:
            if row[1] == "global":
                chosen = row
                break

    if not chosen:
        return {
            "indicator_name": indicator_name,
            "score": score,
            "valid": True,
            "exceeded": False,
            "threshold": None,
            "severity_band": None,
            "baseline_id": None,
            "scope_used": None,
            "delta": None,
            "unbounded": True,
        }

    baseline_id, scope_used, _, threshold, severity_band = chosen
    threshold = float(threshold)
    exceeded = score > threshold
    delta = round(score - threshold, 4) if exceeded else 0.0

    return {
        "indicator_name": indicator_name,
        "score": score,
        "valid": not exceeded,
        "exceeded": exceeded,
        "threshold": threshold,
        "severity_band": severity_band,
        "baseline_id": baseline_id,
        "scope_used": scope_used,
        "delta": delta,
        "unbounded": False,
    }


def auto_generate_pir_alert(
    indicator_name: str,
    score: float,
    scope: str = "project",
    scope_id: str = "",
    operator_id: str = "",
    db_path: str | None = None,
) -> dict[str, Any]:
    """Validate an indicator score and auto-generate a PIR when threshold is exceeded.

    Wraps ``validate_indicator_score`` and, on breach, calls the PIR manager to
    create an active Priority Intelligence Requirement with priority mapped
    from the baseline severity band.

    Args:
        indicator_name: The indicator key to evaluate.
        score: The observed numeric score.
        scope: Starting scope level for baseline lookup.
        scope_id: Entity ID for the scoped lookup.
        operator_id: Analyst or system to task the PIR to.

    Returns:
        Dict containing all validation fields plus:
        - ``pir_generated`` (bool)
        - ``pir_id`` (str | None)
    """
    result = validate_indicator_score(
        indicator_name=indicator_name,
        score=score,
        scope=scope,
        scope_id=scope_id,
        db_path=db_path,
    )

    if not result["exceeded"]:
        result["pir_generated"] = False
        result["pir_id"] = None
        return result

    severity = result.get("severity_band") or _DEFAULT_SEVERITY_BAND
    priority_mapping = {"critical": 1, "high": 2, "medium": 3, "low": 4}
    collection_priority = priority_mapping.get(severity, 3)

    from icdev.tools.intelligence.pir_manager import create_pir

    pir = create_pir(
        pir_type="PIR",
        topic=f"{indicator_name} threshold exceeded",
        description=(
            f"Indicator '{indicator_name}' scored {score}, "
            f"exceeding baseline {result['threshold']} by {result['delta']}. "
            f"Severity band: {severity}."
        ),
        collection_priority=collection_priority,
        tasked_to=operator_id,
    )

    result["pir_generated"] = True
    result["pir_id"] = pir.get("id")
    return result


def list_baselines(
    indicator_name: str = "",
    scope: str = "",
    scope_id: str = "",
    is_active: bool | None = None,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Return baselines matching the provided filters."""
    conn = _get_conn(db_path)
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if indicator_name:
            clauses.append("indicator_name = ?")
            params.append(indicator_name)
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        if scope_id:
            clauses.append("scope_id = ?")
            params.append(scope_id)
        if is_active is not None:
            clauses.append("is_active = ?")
            params.append(1 if is_active else 0)

        sql = "SELECT * FROM indicator_baselines"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY indicator_name, scope, created_at DESC"

        rows = conn.execute(sql, params).fetchall()
        cols = [d[0] for d in conn.execute(sql, params).description]  # type: ignore[arg-type]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()
