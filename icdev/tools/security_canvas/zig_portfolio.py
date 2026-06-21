# [CUI // SP-CTI]
"""ZIG Portfolio — multi-target assessment comparisons and health rollups.

get_portfolio_health()      → summary metrics across all active targets
compare_targets(target_ids) → Chart.js radar datasets for 2–4 targets
get_target_assessment(target_id) → wraps run_zig_assessment for a specific target
"""

from tools.security_canvas.db.init_db import get_connection
from tools.security_canvas.zig_assessor import run_zig_assessment
from tools.security_canvas.constants import ZIG_PILLARS


def _list_active_targets() -> list:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, description, system_type, classification, status, created_at "
            "FROM zig_targets WHERE status='active' ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _latest_scores_for_target(target_id: str) -> list:
    """Return most recent score row per pillar for a target."""
    conn = get_connection()
    try:
        pillar_slugs = [p["slug"] for p in ZIG_PILLARS]
        rows = []
        for slug in pillar_slugs:
            row = conn.execute(
                "SELECT pillar_slug, score, maturity_level, assessment_run_at "
                "FROM zig_maturity_scores WHERE target_id=? AND pillar_slug=? "
                "ORDER BY created_at DESC LIMIT 1",
                (target_id, slug),
            ).fetchone()
            if row:
                rows.append(dict(row))
            else:
                rows.append({
                    "pillar_slug": slug, "score": 0.0,
                    "maturity_level": "preparation", "assessment_run_at": None,
                })
        return rows
    finally:
        conn.close()


def get_portfolio_health() -> dict:
    """Return aggregated health metrics for all active ZIG targets.

    Returns:
        {
            total_targets: int,
            avg_score: float,
            maturity_distribution: {level: count},
            targets_needing_attention: [...],   # score < 0.4
            targets: [{id, name, score, maturity_level}, ...]
        }
    """
    targets = _list_active_targets()
    if not targets:
        return {
            "total_targets": 0,
            "avg_score": 0.0,
            "maturity_distribution": {},
            "targets_needing_attention": [],
            "targets": [],
        }

    target_summaries = []
    maturity_dist = {}
    needing_attention = []

    for t in targets:
        scores = _latest_scores_for_target(t["id"])
        if scores:
            valid_scores = [s["score"] for s in scores if s["score"] is not None]
            avg = round(sum(valid_scores) / len(valid_scores), 4) if valid_scores else 0.0
        else:
            avg = 0.0

        # Determine overall maturity from avg score
        if avg >= 0.75:
            maturity = "advanced"
        elif avg >= 0.50:
            maturity = "intermediate"
        elif avg >= 0.25:
            maturity = "initial"
        else:
            maturity = "preparation"

        maturity_dist[maturity] = maturity_dist.get(maturity, 0) + 1

        summary = {
            "id": t["id"],
            "name": t["name"],
            "system_type": t.get("system_type", "general"),
            "classification": t.get("classification", "CUI"),
            "score": avg,
            "maturity_level": maturity,
        }
        target_summaries.append(summary)

        if avg < 0.40:
            needing_attention.append(summary)

    all_scores = [t["score"] for t in target_summaries]
    portfolio_avg = round(sum(all_scores) / len(all_scores), 4) if all_scores else 0.0

    # Sort attention list worst-first
    needing_attention.sort(key=lambda t: t["score"])

    return {
        "total_targets": len(targets),
        "avg_score": portfolio_avg,
        "maturity_distribution": maturity_dist,
        "targets_needing_attention": needing_attention,
        "targets": sorted(target_summaries, key=lambda t: t["score"], reverse=True),
    }


def compare_targets(target_ids: list) -> dict:
    """Generate Chart.js radar datasets for 2–4 targets.

    Args:
        target_ids: List of 2–4 target ID strings.

    Returns:
        {
            labels: [pillar labels],
            datasets: [{label, data, borderColor, backgroundColor}, ...]
        }
    """
    if len(target_ids) < 2 or len(target_ids) > 4:
        return {"error": "compare_targets requires 2–4 target IDs", "labels": [], "datasets": []}

    colors = [
        ("#6366f1", "rgba(99,102,241,0.15)"),
        ("#10b981", "rgba(16,185,129,0.15)"),
        ("#f59e0b", "rgba(245,158,11,0.15)"),
        ("#ef4444", "rgba(239,68,68,0.15)"),
    ]

    labels = [p["name"] for p in ZIG_PILLARS]

    # Get target names for labels
    conn = get_connection()
    try:
        name_map = {}
        for tid in target_ids:
            row = conn.execute(
                "SELECT id, name FROM zig_targets WHERE id=?", (tid,)
            ).fetchone()
            if row:
                name_map[tid] = row["name"]
            elif tid == "icdev-self":
                name_map[tid] = "ICDEV Platform"
            else:
                name_map[tid] = tid
    finally:
        conn.close()

    datasets = []
    for i, tid in enumerate(target_ids):
        scores = _latest_scores_for_target(tid)
        data = [round(s["score"] * 100, 1) for s in scores]  # percent for radar
        border, bg = colors[i % len(colors)]
        datasets.append({
            "label": name_map.get(tid, tid),
            "data": data,
            "borderColor": border,
            "backgroundColor": bg,
            "pointBackgroundColor": border,
        })

    return {"labels": labels, "datasets": datasets}


def get_target_assessment(target_id: str) -> dict:
    """Run a full ZIG assessment for a specific target.

    Thin wrapper around run_zig_assessment that enriches the result
    with target metadata.
    """
    result = run_zig_assessment(target_id=target_id)

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, name, description, system_type, classification, status "
            "FROM zig_targets WHERE id=?",
            (target_id,),
        ).fetchone()
        if row:
            result["target"] = dict(row)
        elif target_id == "icdev-self":
            result["target"] = {
                "id": "icdev-self",
                "name": "ICDEV Platform",
                "description": "Platform self-assessment",
                "system_type": "platform",
                "classification": "CUI // SP-CTI",
                "status": "active",
            }
    finally:
        conn.close()

    return result
