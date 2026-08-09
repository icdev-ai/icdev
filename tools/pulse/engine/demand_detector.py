# CUI // SP-CTI
"""ICDEV™ Pulse Demand Signal Detector — identifies unmet capability gaps
from SAM.gov pain points and builds the capability graph.

Design decisions:
    D-PULSE-CAP-2: pulse_demand_signals append-only (NIST AU)
    D-PULSE-CAP-3: pulse_capability_graph separate from knowledge graph
    D-PULSE-CAP-5: 5+ opportunity threshold = "high demand" flag

Pipeline:
    1. Match pain point keywords against capability catalog
    2. Matched -> pulse_capability_graph edges
    3. Unmatched -> pulse_demand_signals (append-only)
    4. Aggregate demand signals by domain/keywords
    5. Flag high-demand signals (frequency >= threshold)
    6. Suggest positioning articles for high-demand unmet needs

Usage:
    python tools/pulse/engine/demand_detector.py --detect --json
    python tools/pulse/engine/demand_detector.py --aggregate --json
    python tools/pulse/engine/demand_detector.py --high-demand --json
    python tools/pulse/engine/demand_detector.py --suggest-articles --json
    python tools/pulse/engine/demand_detector.py --graph --json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# ── Project imports ───────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402
from tools.pulse.engine.capability_scanner import (  # noqa: E402
    match_capabilities,
)

logger = get_logger("pulse.demand_detector")

HIGH_DEMAND_THRESHOLD = 5


# ── Detection ─────────────────────────────────────────────────────────


def detect_demand_signals(
    pain_points: list[dict],
    opportunity_id: str = "",
    opportunity_title: str = "",
) -> dict:
    """Match pain points against capability catalog.

    Matched pain points become capability graph edges.
    Unmatched pain points become demand signals.

    Args:
        pain_points: List of dicts with 'text', 'domain', 'keywords'.
        opportunity_id: SAM.gov opportunity ID (for traceability).
        opportunity_title: Opportunity title (for context).

    Returns:
        Dict with matched_count, unmatched_count, edges, signals.
    """
    now = datetime.now(timezone.utc).isoformat()
    matched_count = 0
    unmatched_count = 0
    edges: list[dict] = []
    signals: list[dict] = []

    for pp in pain_points:
        pp_keywords = pp.get("keywords", [])
        pp_domain = pp.get("domain", "general")
        pp_text = pp.get("text", "")[:500]

        # Try to match against capabilities
        matched_caps = match_capabilities(pp_keywords, top_n=3)

        if matched_caps:
            matched_count += 1
            # Create graph edges for matched capabilities
            for cap in matched_caps:
                edge = {
                    "id": f"pcg-{uuid4().hex[:12]}",
                    "capability_slug": cap["slug"],
                    "capability_name": cap["name"],
                    "entity_type": "sam_opportunity",
                    "entity_id": opportunity_id,
                    "relationship": "addresses",
                    "confidence": min(cap.get("match_score", 1) / 10, 1.0),
                    "metadata": json.dumps(
                        {
                            "opportunity_title": opportunity_title[:200],
                            "domain": pp_domain,
                            "keywords": pp_keywords[:10],
                            "matched_keywords": cap.get("matched_keywords", []),
                        }
                    ),
                    "created_at": now,
                }
                edges.append(edge)
                _insert_graph_edge(edge)
        else:
            unmatched_count += 1
            # Create demand signal for unmet need
            signal = {
                "id": f"pds-{uuid4().hex[:12]}",
                "pain_point_text": pp_text,
                "domain_category": pp_domain,
                "keywords": json.dumps(pp_keywords),
                "frequency": 1,
                "velocity": 0.0,
                "sam_opportunity_ids": json.dumps([opportunity_id] if opportunity_id else []),
                "is_high_demand": 0,
                "status": "new",
                "article_generated": 0,
                "created_at": now,
            }
            signals.append(signal)
            _insert_demand_signal(signal)

    return {
        "status": "ok",
        "matched_count": matched_count,
        "unmatched_count": unmatched_count,
        "edges_created": len(edges),
        "signals_created": len(signals),
        "opportunity_id": opportunity_id,
    }


def _insert_demand_signal(signal: dict) -> None:
    """Insert a demand signal row (append-only, NIST AU)."""
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO pulse_demand_signals "
                "(id, pain_point_text, domain_category, keywords, frequency, "
                "velocity, sam_opportunity_ids, is_high_demand, status, "
                "article_generated, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    signal["id"],
                    signal["pain_point_text"],
                    signal["domain_category"],
                    signal["keywords"],
                    signal["frequency"],
                    signal["velocity"],
                    signal["sam_opportunity_ids"],
                    signal["is_high_demand"],
                    signal["status"],
                    signal["article_generated"],
                    signal["created_at"],
                ),
            )
            conn.commit()
    except Exception as e:
        logger.warning("Failed to insert demand signal: %s", e)


def _insert_graph_edge(edge: dict) -> None:
    """Insert a capability graph edge."""
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO pulse_capability_graph "
                "(id, capability_slug, capability_name, entity_type, "
                "entity_id, relationship, confidence, metadata, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    edge["id"],
                    edge["capability_slug"],
                    edge["capability_name"],
                    edge["entity_type"],
                    edge["entity_id"],
                    edge["relationship"],
                    edge["confidence"],
                    edge["metadata"],
                    edge["created_at"],
                ),
            )
            conn.commit()
    except Exception as e:
        logger.warning("Failed to insert graph edge: %s", e)


# ── Aggregation ───────────────────────────────────────────────────────


def aggregate_demand_signals() -> dict:
    """Compute frequency and velocity for demand signals.

    Groups by domain_category + overlapping keywords.
    Sets is_high_demand = 1 when frequency >= threshold.

    Returns:
        Dict with domain stats and high-demand count.
    """
    try:
        with get_connection() as conn:
            rows = conn.execute("SELECT * FROM pulse_demand_signals WHERE status != 'dismissed'").fetchall()

        if not rows:
            return {"status": "ok", "total": 0, "high_demand_count": 0, "by_domain": {}}

        # Group by domain
        by_domain: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            by_domain[dict(row).get("domain_category", "general")].append(dict(row))

        domain_stats: dict[str, dict] = {}
        high_demand_count = 0

        for domain, items in by_domain.items():
            freq = len(items)
            is_high = freq >= HIGH_DEMAND_THRESHOLD
            if is_high:
                high_demand_count += 1

            # Update is_high_demand flag in DB
            if is_high:
                with get_connection() as conn:
                    for item in items:
                        conn.execute(
                            "UPDATE pulse_demand_signals SET is_high_demand = 1, frequency = %s WHERE id = %s",
                            (freq, item["id"]),
                        )
                    conn.commit()

            domain_stats[domain] = {
                "signal_count": freq,
                "is_high_demand": is_high,
                "sample_keywords": _extract_top_keywords(items, 5),
            }

        return {
            "status": "ok",
            "total": len(rows),
            "high_demand_count": high_demand_count,
            "by_domain": domain_stats,
        }

    except Exception as e:
        logger.warning("Aggregate failed: %s", e)
        return {"status": "error", "error": str(e)}


def _extract_top_keywords(items: list[dict], top_n: int = 5) -> list[str]:
    """Extract most frequent keywords from a list of signal items."""
    counts: dict[str, int] = defaultdict(int)
    for item in items:
        kw_str = item.get("keywords", "[]")
        try:
            kws = json.loads(kw_str) if isinstance(kw_str, str) else kw_str
        except (json.JSONDecodeError, TypeError):
            kws = []
        for kw in kws:
            counts[kw.lower()] += 1

    sorted_kw = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    return [kw for kw, _ in sorted_kw[:top_n]]


# ── High-demand queries ──────────────────────────────────────────────


def get_high_demand_signals(threshold: int = HIGH_DEMAND_THRESHOLD) -> dict:
    """Return demand signals flagged as high-demand.

    Args:
        threshold: Minimum frequency to qualify as high demand.

    Returns:
        Dict with high-demand signals grouped by domain.
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM pulse_demand_signals "
                "WHERE is_high_demand = 1 AND status != 'dismissed' "
                "ORDER BY frequency DESC"
            ).fetchall()

        signals = [dict(r) for r in rows]
        return {
            "status": "ok",
            "threshold": threshold,
            "count": len(signals),
            "signals": signals,
        }

    except Exception as e:
        return {"status": "error", "error": str(e)}


def suggest_positioning_articles(limit: int = 5) -> dict:
    """Format high-demand unmet signals as pipeline-compatible clusters.

    These can be fed directly into run_full_pipeline(topic_override=...).

    Returns:
        Dict with suggested article topics.
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT domain_category, COUNT(*) as freq, "
                "GROUP_CONCAT(keywords, ', ') as all_keywords "
                "FROM pulse_demand_signals "
                "WHERE is_high_demand = 1 AND article_generated = 0 "
                "AND status != 'dismissed' "
                "GROUP BY domain_category "
                "ORDER BY freq DESC "
                "LIMIT %s",
                (limit,),
            ).fetchall()

        suggestions: list[dict] = []
        for row in rows:
            row_dict = dict(row)
            domain = row_dict.get("domain_category", "general")
            freq = row_dict.get("freq", 0)

            # Parse aggregated keywords
            all_kw_str = row_dict.get("all_keywords", "")
            try:
                all_keywords = set()
                for chunk in all_kw_str.split(", "):
                    chunk = chunk.strip()
                    if chunk.startswith("["):
                        for kw in json.loads(chunk):
                            all_keywords.add(kw.lower())
                    elif chunk:
                        all_keywords.add(chunk.lower())
            except Exception:
                all_keywords = set()

            top_kw = list(all_keywords)[:5]
            topic = f"Addressing Unmet {domain.replace('_', ' ').title()} Needs in Federal IT"
            if top_kw:
                topic += f": {', '.join(kw.title() for kw in top_kw[:3])}"

            suggestions.append(
                {
                    "topic": topic,
                    "domain": domain,
                    "frequency": freq,
                    "keywords": top_kw,
                    "cluster": {
                        "id": f"demand-{uuid4().hex[:8]}",
                        "name": topic,
                        "description": topic,
                        "pain_points": json.dumps(top_kw),
                        "research_ids": json.dumps([]),
                        "priority_score": min(freq / 10, 1.0),
                        "source": "demand_signal",
                    },
                }
            )

        return {
            "status": "ok",
            "count": len(suggestions),
            "suggestions": suggestions,
        }

    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_capability_graph(capability_slug: str | None = None) -> dict:
    """Query the capability graph.

    Args:
        capability_slug: Optional filter by capability slug.

    Returns:
        Dict with graph edges.
    """
    try:
        with get_connection() as conn:
            if capability_slug:
                rows = conn.execute(
                    "SELECT * FROM pulse_capability_graph WHERE capability_slug = %s ORDER BY created_at DESC LIMIT 100",
                    (capability_slug,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT capability_slug, capability_name, "
                    "COUNT(*) as edge_count, "
                    "AVG(confidence) as avg_confidence "
                    "FROM pulse_capability_graph "
                    "GROUP BY capability_slug, capability_name "
                    "ORDER BY edge_count DESC"
                ).fetchall()

        return {
            "status": "ok",
            "count": len(rows),
            "edges" if capability_slug else "capabilities": [dict(r) for r in rows],
        }

    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── CLI ───────────────────────────────────────────────────────────────


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="ICDEV™ Pulse Demand Signal Detector (CUI // SP-CTI)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--detect", action="store_true", help="Detect demand signals from existing SAM data")
    group.add_argument("--aggregate", action="store_true", help="Aggregate and flag high-demand signals")
    group.add_argument("--high-demand", action="store_true", help="List high-demand unmet signals")
    group.add_argument("--suggest-articles", action="store_true", help="Suggest positioning articles")
    group.add_argument("--graph", action="store_true", help="Query capability graph")

    parser.add_argument("--capability", type=str, help="Filter graph by capability slug")
    parser.add_argument(
        "--threshold",
        type=int,
        default=HIGH_DEMAND_THRESHOLD,
        help=f"High-demand threshold (default: {HIGH_DEMAND_THRESHOLD})",
    )
    parser.add_argument("--limit", type=int, default=5, help="Max suggestions (default: 5)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.detect:
        # Detect from existing SAM article log data
        try:
            with get_connection() as conn:
                rows = conn.execute(
                    "SELECT sam_opportunity_id, opportunity_title, "
                    "extracted_pain_points, domain_category "
                    "FROM pulse_sam_article_log "
                    "WHERE pipeline_status != 'failed' "
                    "ORDER BY created_at DESC LIMIT 100"
                ).fetchall()

            total_result = {
                "status": "ok",
                "opportunities_scanned": 0,
                "total_matched": 0,
                "total_unmatched": 0,
            }
            for row in rows:
                r = dict(row)
                kw_str = r.get("extracted_pain_points", "[]")
                try:
                    keywords = json.loads(kw_str) if isinstance(kw_str, str) else kw_str
                except (json.JSONDecodeError, TypeError):
                    keywords = []

                if not keywords:
                    continue

                pain_points = [
                    {
                        "text": r.get("opportunity_title", ""),
                        "domain": r.get("domain_category", "general"),
                        "keywords": keywords if isinstance(keywords, list) else [keywords],
                    }
                ]
                result = detect_demand_signals(
                    pain_points,
                    r.get("sam_opportunity_id", ""),
                    r.get("opportunity_title", ""),
                )
                total_result["opportunities_scanned"] += 1
                total_result["total_matched"] += result.get("matched_count", 0)
                total_result["total_unmatched"] += result.get("unmatched_count", 0)

            result = total_result

        except Exception as e:
            result = {"status": "error", "error": str(e)}

    elif args.aggregate:
        result = aggregate_demand_signals()
    elif args.high_demand:
        result = get_high_demand_signals(args.threshold)
    elif args.suggest_articles:
        result = suggest_positioning_articles(args.limit)
    elif args.graph:
        result = get_capability_graph(args.capability)
    else:
        result = {"status": "error", "error": "No action specified"}

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
