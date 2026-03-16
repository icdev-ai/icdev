#!/usr/bin/env python3
"""Narrative tracker — track how a story's framing evolves over time.

Monitors a topic across multiple analysis snapshots:
  - Framing shift detection (tone, loaded language, omission changes)
  - Source position changes (who flipped from neutral to biased?)
  - New entity appearances / disappearances
  - Coverage volume timeline

Generates: "Last week 15 sources covered this with GREEN avg.
This week 22 sources, avg shifted to YELLOW. 3 sources changed position."
"""
import logging
from datetime import datetime, timezone

import requests

from src import config
from src.db import dynamo
from src.services import entity_extractor

logger = logging.getLogger("intaas.services.narrative_tracker")


def take_snapshot(topic_id: str) -> dict:
    """Take a point-in-time snapshot of a topic's narrative state.

    Stores: source count, avg bias, entity list, per-source scores.
    """
    topic = dynamo.get_topic(topic_id)
    if not topic:
        return {"error": "Topic not found"}

    perspectives = dynamo.get_perspectives(topic_id)
    articles = dynamo.get_articles_for_topic(topic_id)

    scores = [float(p.get("bias_composite", 3.0)) for p in perspectives]
    avg_bias = round(sum(scores) / max(len(scores), 1), 2)

    colors = {}
    source_scores = []
    for p in perspectives:
        c = p.get("bias_color", "green")
        colors[c] = colors.get(c, 0) + 1
        source_scores.append({
            "source": p.get("source_name", ""),
            "score": float(p.get("bias_composite", 3.0)),
            "color": c,
        })

    # Extract top entities
    all_entities = set()
    for article in articles[:10]:
        entities = entity_extractor.extract_entities_deterministic(
            (article.get("title", "") + " " + article.get("content", "")),
            max_entities=10,
        )
        for e in entities:
            all_entities.add(e.get("name", ""))

    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source_count": len(perspectives),
        "avg_bias": avg_bias,
        "color_distribution": colors,
        "source_scores": source_scores,
        "top_entities": sorted(all_entities)[:20],
        "article_count": len(articles),
    }

    # Store snapshot
    dynamo.store_cached_result(
        topic_id,
        f"snapshot_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}",
        snapshot,
    )

    # Also update a "snapshots_index" with list of snapshot timestamps
    index = dynamo.get_cached_result(topic_id, "snapshots_index") or {"snapshots": []}
    index["snapshots"].append(snapshot["timestamp"])
    index["snapshots"] = index["snapshots"][-50:]  # Keep last 50
    dynamo.store_cached_result(topic_id, "snapshots_index", index)

    return snapshot


def get_timeline(topic_id: str) -> dict:
    """Get the narrative timeline for a topic — all snapshots over time."""
    index = dynamo.get_cached_result(topic_id, "snapshots_index")
    if not index or not index.get("snapshots"):
        # No snapshots yet — take one now and return
        snapshot = take_snapshot(topic_id)
        if snapshot.get("error"):
            return snapshot
        return {
            "topic_id": topic_id,
            "snapshots": [snapshot],
            "shift_detected": False,
            "analysis": "First snapshot taken. Return later to track changes.",
        }

    # Load all snapshots
    snapshots = []
    for ts in index["snapshots"]:
        key = f"snapshot_{ts[:10].replace('-', '')}_{ts[11:13]}{ts[14:16]}"
        data = dynamo.get_cached_result(topic_id, key)
        if data:
            snapshots.append(data)

    if not snapshots:
        snapshot = take_snapshot(topic_id)
        return {
            "topic_id": topic_id,
            "snapshots": [snapshot],
            "shift_detected": False,
            "analysis": "First snapshot taken.",
        }

    return {
        "topic_id": topic_id,
        "snapshots": snapshots,
        "snapshot_count": len(snapshots),
        "shift_detected": len(snapshots) > 1,
        "analysis": _analyze_shifts(snapshots) if len(snapshots) > 1 else "Need more snapshots to detect shifts.",
    }


def detect_shifts(topic_id: str) -> dict:
    """Detect narrative shifts between snapshots.

    Compares latest snapshot to earliest and reports changes.
    """
    timeline = get_timeline(topic_id)
    snapshots = timeline.get("snapshots", [])

    if len(snapshots) < 2:
        # Take a new snapshot and compare to current state
        new_snapshot = take_snapshot(topic_id)
        if new_snapshot.get("error"):
            return new_snapshot
        snapshots.append(new_snapshot)

    if len(snapshots) < 2:
        return {
            "topic_id": topic_id,
            "shifts": [],
            "summary": "Not enough data points. Analyze this topic again later.",
        }

    first = snapshots[0]
    latest = snapshots[-1]

    shifts = []

    # Bias shift
    bias_delta = latest.get("avg_bias", 3.0) - first.get("avg_bias", 3.0)
    if abs(bias_delta) > 0.3:
        direction = "more biased" if bias_delta < 0 else "less biased"
        shifts.append({
            "type": "bias_shift",
            "severity": "HIGH" if abs(bias_delta) > 0.7 else "MODERATE",
            "description": (
                f"Average bias shifted from {first.get('avg_bias', 3.0):.1f} "
                f"to {latest.get('avg_bias', 3.0):.1f} ({direction})"
            ),
            "delta": round(bias_delta, 2),
        })

    # Coverage change
    count_delta = latest.get("source_count", 0) - first.get("source_count", 0)
    if abs(count_delta) > 3:
        shifts.append({
            "type": "coverage_change",
            "severity": "MODERATE",
            "description": (
                f"Source count changed from {first.get('source_count', 0)} "
                f"to {latest.get('source_count', 0)} "
                f"({'increased' if count_delta > 0 else 'decreased'})"
            ),
            "delta": count_delta,
        })

    # Entity changes
    first_entities = set(first.get("top_entities", []))
    latest_entities = set(latest.get("top_entities", []))
    new_entities = latest_entities - first_entities
    dropped_entities = first_entities - latest_entities

    if new_entities:
        shifts.append({
            "type": "new_entities",
            "severity": "MODERATE",
            "description": f"New entities appeared: {', '.join(sorted(new_entities)[:5])}",
            "entities": sorted(new_entities)[:10],
        })

    if dropped_entities:
        shifts.append({
            "type": "dropped_entities",
            "severity": "MODERATE",
            "description": f"Entities disappeared: {', '.join(sorted(dropped_entities)[:5])}",
            "entities": sorted(dropped_entities)[:10],
        })

    # Source position changes
    first_scores = {s["source"]: s for s in first.get("source_scores", [])}
    latest_scores = {s["source"]: s for s in latest.get("source_scores", [])}
    flipped = []
    for source, latest_s in latest_scores.items():
        first_s = first_scores.get(source)
        if first_s and first_s["color"] != latest_s["color"]:
            flipped.append({
                "source": source,
                "from_color": first_s["color"],
                "to_color": latest_s["color"],
                "from_score": first_s["score"],
                "to_score": latest_s["score"],
            })

    if flipped:
        shifts.append({
            "type": "source_position_change",
            "severity": "HIGH",
            "description": f"{len(flipped)} source(s) changed bias rating",
            "sources": flipped[:10],
        })

    # LLM summary
    summary = _generate_shift_summary(shifts, first, latest)

    return {
        "topic_id": topic_id,
        "shifts": shifts,
        "shift_count": len(shifts),
        "first_snapshot": first.get("timestamp", ""),
        "latest_snapshot": latest.get("timestamp", ""),
        "summary": summary,
    }


def _analyze_shifts(snapshots: list[dict]) -> str:
    """Generate human-readable shift analysis."""
    if len(snapshots) < 2:
        return "Need more snapshots to detect shifts."

    first = snapshots[0]
    latest = snapshots[-1]
    bias_delta = latest.get("avg_bias", 3.0) - first.get("avg_bias", 3.0)

    if abs(bias_delta) > 0.5:
        direction = "more biased" if bias_delta < 0 else "less biased"
        return (
            f"Narrative has shifted {direction} over {len(snapshots)} snapshots. "
            f"Bias moved from {first.get('avg_bias', 3.0):.1f} to "
            f"{latest.get('avg_bias', 3.0):.1f}."
        )
    return (
        f"Narrative relatively stable across {len(snapshots)} snapshots. "
        f"Avg bias: {latest.get('avg_bias', 3.0):.1f}/5.0."
    )


def _generate_shift_summary(
    shifts: list[dict], first: dict, latest: dict,
) -> str:
    """Generate LLM summary if available, otherwise deterministic."""
    if not shifts:
        return "No significant narrative shifts detected between snapshots."

    if not config.LLM_JUDGE_ENABLED or not config.ANTHROPIC_API_KEY:
        parts = [s["description"] for s in shifts]
        return " | ".join(parts)

    prompt = (
        "Summarize these narrative shifts in 2-3 sentences for a news analyst:\n\n"
        + "\n".join(f"- {s['description']}" for s in shifts)
        + f"\n\nFirst snapshot: {first.get('source_count', 0)} sources, "
        + f"avg bias {first.get('avg_bias', 3.0):.1f}/5.0"
        + f"\nLatest snapshot: {latest.get('source_count', 0)} sources, "
        + f"avg bias {latest.get('avg_bias', 3.0):.1f}/5.0"
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.LLM_JUDGE_MODEL,
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("content", [{}])[0].get("text", "").strip()
    except Exception as e:
        logger.warning("LLM summary failed: %s", e)
        return " | ".join(s["description"] for s in shifts)
