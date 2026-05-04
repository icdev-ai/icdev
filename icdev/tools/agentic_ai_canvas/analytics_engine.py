# CUI // SP-CTI
"""Agentic AI Design Canvas — Portfolio Analytics Engine (Phase 8).

Computes cross-design trends: score history (weekly buckets), pattern distribution,
compliance drift (30-day delta), risk density by domain, ATO readiness rate,
red team risk distribution, and lint score distribution.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone


def compute_analytics(
    designs: list[dict],
    assessments: list[dict],
    pattern_reports: list[dict],
    ato_reports: list[dict],
    red_team_reports: list[dict],
    lint_reports: list[dict],
    risk_items: list[dict],
) -> dict:
    """
    Compute portfolio-level analytics across all designs.

    Returns a flat analytics dict ready for template rendering.
    """
    now = datetime.now(timezone.utc)

    # Score trends: weekly buckets (last 8 weeks)
    score_trend = _score_trend(assessments, now)

    # Pattern distribution
    pattern_dist = _pattern_distribution(pattern_reports)

    # Compliance drift: score delta in last 30 days
    compliance_drift = _compliance_drift(assessments, now)

    # Risk density by domain
    risk_density = _risk_density(designs, risk_items)

    # ATO readiness rate
    ato_ready_count = sum(1 for r in ato_reports if r.get("ato_ready"))
    ato_rate = round(ato_ready_count / max(1, len(designs)) * 100, 1)

    # Red team risk distribution
    rt_dist = _rt_distribution(red_team_reports)

    # Lint score distribution (buckets: 90-100, 70-89, 50-69, <50)
    lint_dist = _lint_distribution(lint_reports)

    # Score percentiles
    scores = [a["score"] for a in assessments if "score" in a]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
    p90 = sorted(scores)[int(len(scores) * 0.9)] if len(scores) >= 2 else (scores[0] if scores else 0)

    # Designs with most open critical risks
    crit_by_design = defaultdict(int)
    for r in risk_items:
        if r.get("severity") == "CRITICAL" and r.get("status") == "open":
            crit_by_design[r["design_id"]] += 1
    top_risk_designs = sorted(
        [{"design_id": did, "name": _design_name(designs, did), "critical_open": cnt}
         for did, cnt in crit_by_design.items()],
        key=lambda x: x["critical_open"], reverse=True,
    )[:5]

    # Score improvement vs degradation in last 30 days
    improved = sum(1 for d in compliance_drift if d["delta"] > 0)
    degraded = sum(1 for d in compliance_drift if d["delta"] < 0)
    stable = len(compliance_drift) - improved - degraded

    return {
        "total_designs": len(designs),
        "assessed_designs": len(assessments),
        "avg_score": avg_score,
        "p90_score": p90,
        "score_trend": score_trend,
        "pattern_distribution": pattern_dist,
        "compliance_drift": compliance_drift,
        "improved_count": improved,
        "degraded_count": degraded,
        "stable_count": stable,
        "risk_density": risk_density,
        "ato_ready_rate": ato_rate,
        "ato_ready_count": ato_ready_count,
        "rt_distribution": rt_dist,
        "lint_distribution": lint_dist,
        "top_risk_designs": top_risk_designs,
        "generated_at": now.isoformat(),
    }


def _score_trend(assessments: list[dict], now: datetime) -> list[dict]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for a in assessments:
        try:
            ts = datetime.fromisoformat(a.get("created_at", "").replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        delta_weeks = int((now - ts).days / 7)
        if 0 <= delta_weeks < 8:
            week_label = f"W-{delta_weeks}" if delta_weeks > 0 else "This week"
            buckets[week_label].append(float(a.get("score", 0)))

    trend = []
    for label in ["W-7", "W-6", "W-5", "W-4", "W-3", "W-2", "W-1", "This week"]:
        vals = buckets.get(label, [])
        trend.append({
            "week": label,
            "avg_score": round(sum(vals) / len(vals), 1) if vals else None,
            "count": len(vals),
        })
    return trend


def _pattern_distribution(pattern_reports: list[dict]) -> list[dict]:
    counter: Counter = Counter()
    for r in pattern_reports:
        dom = r.get("dominant_pattern", "UNCLASSIFIED")
        counter[dom] += 1
    return [{"pattern": k, "count": v} for k, v in counter.most_common()]


def _compliance_drift(assessments: list[dict], now: datetime) -> list[dict]:
    by_design: dict[str, list] = defaultdict(list)
    for a in assessments:
        by_design[a["design_id"]].append(a)

    drift = []
    for did, rows in by_design.items():
        rows_sorted = sorted(rows, key=lambda x: x.get("created_at", ""))
        if len(rows_sorted) < 2:
            continue
        latest = rows_sorted[-1]
        # Find score ~30 days ago
        cutoff = (now.replace(tzinfo=None) if now.tzinfo else now)
        baseline = None
        for r in reversed(rows_sorted[:-1]):
            try:
                ts = datetime.fromisoformat(r["created_at"].replace("Z", "")).replace(tzinfo=None)
                if (cutoff.replace(tzinfo=None) - ts).days >= 25:
                    baseline = r
                    break
            except (ValueError, AttributeError):
                pass
        if not baseline:
            baseline = rows_sorted[0]
        delta = round(float(latest.get("score", 0)) - float(baseline.get("score", 0)), 1)
        drift.append({"design_id": did, "delta": delta, "current_score": latest.get("score", 0)})

    return sorted(drift, key=lambda x: abs(x["delta"]), reverse=True)[:10]


def _risk_density(designs: list[dict], risk_items: list[dict]) -> list[dict]:
    domain_counts: dict[str, dict] = defaultdict(lambda: {"total": 0, "critical": 0, "designs": 0})
    design_domain = {d["id"]: d.get("domain", "unspecified") for d in designs}

    for d in designs:
        domain_counts[d.get("domain", "unspecified")]["designs"] += 1
    for r in risk_items:
        domain = design_domain.get(r.get("design_id", ""), "unspecified")
        domain_counts[domain]["total"] += 1
        if r.get("severity") == "CRITICAL":
            domain_counts[domain]["critical"] += 1

    result = []
    for domain, c in sorted(domain_counts.items(), key=lambda x: x[1]["total"], reverse=True):
        result.append({
            "domain": domain,
            "total_risks": c["total"],
            "critical_risks": c["critical"],
            "design_count": c["designs"],
            "density": round(c["total"] / max(1, c["designs"]), 1),
        })
    return result


def _rt_distribution(red_team_reports: list[dict]) -> dict:
    dist: Counter = Counter()
    for r in red_team_reports:
        level = r.get("overall_risk", "UNKNOWN")
        dist[level] += 1
    return dict(dist)


def _lint_distribution(lint_reports: list[dict]) -> dict:
    buckets = {"excellent": 0, "good": 0, "fair": 0, "poor": 0}
    for r in lint_reports:
        score = float(r.get("lint_score", 100))
        if score >= 90:
            buckets["excellent"] += 1
        elif score >= 70:
            buckets["good"] += 1
        elif score >= 50:
            buckets["fair"] += 1
        else:
            buckets["poor"] += 1
    return buckets


def _design_name(designs: list[dict], design_id: str) -> str:
    for d in designs:
        if d["id"] == design_id:
            return d.get("name", design_id[:8])
    return design_id[:8]
