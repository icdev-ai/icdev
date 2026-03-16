#!/usr/bin/env python3
"""Source report card — aggregate a source's bias scores across all topics.

"Fox News averages YELLOW across 8 topics, with omission as their weakest dimension."

Tracks per-dimension performance:
  - loaded_language, framing_balance, statistical_honesty,
    source_transparency, omission_severity
  - Identifies strongest and weakest dimensions
  - Shows trend over time (improving/stable/declining)
"""
import json
import logging
from collections import defaultdict

from src.db import dynamo
from src.services import credibility_db

logger = logging.getLogger("intaas.services.source_report_card")


def generate_report_card(source_domain: str) -> dict:
    """Generate a full report card with per-dimension breakdown."""
    source_domain = source_domain.lower().replace("www.", "")

    topics = dynamo.list_topics(limit=100)
    appearances = []
    dimension_scores = defaultdict(list)

    for topic in topics:
        tid = topic.get("topic_id", "")
        if not tid or int(topic.get("source_count", 0)) == 0:
            continue

        perspectives = dynamo.get_perspectives(tid)
        for p in perspectives:
            p_domain = (p.get("source_name", "") or "").lower().replace("www.", "")
            if p_domain == source_domain or source_domain in p_domain:
                article_id = p.get("article_id", "")
                composite = float(p.get("bias_composite", 3.0))

                appearance = {
                    "topic_id": tid,
                    "topic_title": topic.get("title", ""),
                    "bias_composite": composite,
                    "bias_color": p.get("bias_color", "green"),
                    "title": p.get("title", ""),
                    "article_id": article_id,
                }

                # Get per-dimension scores from bias table
                if article_id:
                    bias_records = dynamo.get_bias_scores(article_id)
                    if bias_records:
                        latest = bias_records[-1]
                        try:
                            scores = json.loads(latest.get("scores", "{}"))
                            appearance["dimensions"] = scores
                            for dim, val in scores.items():
                                dimension_scores[dim].append(float(val))
                        except (json.JSONDecodeError, TypeError):
                            pass

                appearances.append(appearance)

    if not appearances:
        cred = credibility_db.lookup_source(source_domain)
        return {
            "source": source_domain,
            "appearances": 0,
            "credibility": cred,
            "message": "No analyzed articles from this source yet.",
        }

    # Aggregate composites
    composites = [a["bias_composite"] for a in appearances]
    avg_score = round(sum(composites) / len(composites), 2)

    colors = defaultdict(int)
    for a in appearances:
        colors[a["bias_color"]] += 1

    # Per-dimension averages
    dim_averages = {}
    for dim, vals in dimension_scores.items():
        avg = round(sum(vals) / len(vals), 2)
        dim_averages[dim] = {
            "avg": avg,
            "min": round(min(vals), 2),
            "max": round(max(vals), 2),
            "count": len(vals),
        }

    # Find strongest and weakest dimensions
    strongest = ""
    weakest = ""
    if dim_averages:
        strongest = max(dim_averages, key=lambda d: dim_averages[d]["avg"])
        weakest = min(dim_averages, key=lambda d: dim_averages[d]["avg"])

    # Trend analysis
    trend = "insufficient_data"
    if len(composites) >= 3:
        mid = len(composites) // 2
        first_half = sum(composites[:mid]) / mid
        second_half = sum(composites[mid:]) / (len(composites) - mid)
        delta = second_half - first_half
        if delta > 0.3:
            trend = "improving"
        elif delta < -0.3:
            trend = "declining"
        else:
            trend = "stable"

    avg_color = _score_to_color(avg_score)
    cred = credibility_db.lookup_source(source_domain)

    return {
        "source": source_domain,
        "credibility": cred,
        "appearances": len(appearances),
        "topics_count": len({a["topic_id"] for a in appearances}),
        "avg_bias_score": avg_score,
        "avg_color": avg_color,
        "best_score": round(max(composites), 2),
        "worst_score": round(min(composites), 2),
        "color_distribution": dict(colors),
        "dimension_averages": dim_averages,
        "strongest_dimension": strongest,
        "weakest_dimension": weakest,
        "trend": trend,
        "recent_articles": [
            {
                "topic_title": a["topic_title"],
                "title": a["title"],
                "score": a["bias_composite"],
                "color": a["bias_color"],
                "dimensions": a.get("dimensions", {}),
            }
            for a in appearances[-5:]
        ],
        "summary": _generate_summary(
            source_domain, avg_score, avg_color, len(appearances),
            strongest, weakest, dim_averages, trend, cred,
        ),
    }


def get_all_source_cards() -> list[dict]:
    """Generate report cards for all sources across topics."""
    topics = dynamo.list_topics(limit=100)
    source_data = defaultdict(lambda: {
        "composites": [], "dimensions": defaultdict(list),
    })

    for topic in topics:
        tid = topic.get("topic_id", "")
        if not tid or int(topic.get("source_count", 0)) == 0:
            continue

        perspectives = dynamo.get_perspectives(tid)
        for p in perspectives:
            domain = (p.get("source_name", "") or "").lower().replace("www.", "")
            if not domain:
                continue

            composite = float(p.get("bias_composite", 3.0))
            source_data[domain]["composites"].append(composite)

            article_id = p.get("article_id", "")
            if article_id:
                bias_records = dynamo.get_bias_scores(article_id)
                if bias_records:
                    latest = bias_records[-1]
                    try:
                        scores = json.loads(latest.get("scores", "{}"))
                        for dim, val in scores.items():
                            source_data[domain]["dimensions"][dim].append(float(val))
                    except (json.JSONDecodeError, TypeError):
                        pass

    cards = []
    for domain, data in source_data.items():
        composites = data["composites"]
        avg = round(sum(composites) / len(composites), 2)
        cred = credibility_db.lookup_source(domain)
        avg_color = _score_to_color(avg)

        # Per-dimension averages
        dim_avgs = {}
        weakest = ""
        strongest = ""
        for dim, vals in data["dimensions"].items():
            dim_avgs[dim] = round(sum(vals) / len(vals), 2)
        if dim_avgs:
            weakest = min(dim_avgs, key=dim_avgs.get)
            strongest = max(dim_avgs, key=dim_avgs.get)

        cards.append({
            "source": domain,
            "appearances": len(composites),
            "avg_score": avg,
            "avg_color": avg_color,
            "credibility": cred.get("credibility", "?"),
            "political": cred.get("political", "unknown"),
            "dimension_averages": dim_avgs,
            "weakest_dimension": weakest,
            "strongest_dimension": strongest,
        })

    cards.sort(key=lambda x: x["appearances"], reverse=True)
    return cards


def _score_to_color(score: float) -> str:
    if score >= 4.5:
        return "blue"
    if score >= 3.8:
        return "purple"
    if score >= 3.0:
        return "green"
    if score >= 2.0:
        return "yellow"
    return "red"


def _dim_label(dim: str) -> str:
    return dim.replace("_", " ").title()


def _generate_summary(
    source: str, avg: float, color: str, count: int,
    strongest: str, weakest: str, dims: dict,
    trend: str, cred: dict,
) -> str:
    """Generate human-readable summary."""
    parts = []
    parts.append(
        f"{source} averages {color.upper()} ({avg}/5.0) "
        f"across {count} analyzed article(s)."
    )

    if strongest and weakest and strongest != weakest:
        s_avg = dims.get(strongest, {}).get("avg", 0)
        w_avg = dims.get(weakest, {}).get("avg", 0)
        parts.append(
            f"Strongest dimension: {_dim_label(strongest)} ({s_avg}/5.0). "
            f"Weakest: {_dim_label(weakest)} ({w_avg}/5.0)."
        )

    cred_grade = cred.get("credibility", "?")
    political = cred.get("political", "unknown")
    if cred.get("known"):
        parts.append(
            f"Credibility: {cred_grade} ({cred.get('credibility_label', '')}), "
            f"political leaning: {political}."
        )

    if trend in ("improving", "declining"):
        parts.append(f"Trend: {trend} over recent articles.")

    return " ".join(parts)
