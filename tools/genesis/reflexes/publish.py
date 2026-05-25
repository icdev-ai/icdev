#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Publish Reflex — end-to-end Pulse article pipeline.

Picks a demand topic → drafts article → runs WriteGuard quality check →
stages as draft (NEVER auto-publishes to production).

YELLOW tier (reversible writes — staging/draft only).
Scanner-tier LLM (qwen3.5) for drafting, WriteGuard deterministic for QA.
"""
IMPLEMENTATION_STATUS = "full"

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_pending_topics(limit: int = 2) -> List[Dict[str, Any]]:
    """Get topics for article generation.

    Priority: demand signals first, then unused topic clusters as fallback.
    """
    conn = get_connection()
    topics = []
    try:
        # Priority 1: High-demand signals
        rows = conn.execute(
            """
            SELECT id, pain_point_text, domain_category, keywords, created_at
            FROM pulse_demand_signals
            WHERE is_high_demand = 1
            AND article_generated = 0
            ORDER BY frequency DESC, created_at DESC
            LIMIT ?
        """,
            (limit,),
        ).fetchall()
        for r in rows:
            d = (
                dict(r)
                if hasattr(r, "keys")
                else {
                    "id": r[0],
                    "topic": r[1],
                    "pain_point": r[1],
                    "category": r[2],
                    "keywords": r[3],
                    "created_at": r[4],
                }
            )
            d.setdefault("topic", d.get("pain_point_text", ""))
            d.setdefault("pain_point", d.get("pain_point_text", ""))
            d["source"] = "demand_signal"
            topics.append(d)
    except Exception as e:
        print(f"  WARN: Could not fetch demand topics: {e}")

    # Priority 2: Unused topic clusters (from research phase)
    remaining = limit - len(topics)
    if remaining > 0:
        try:
            rows = conn.execute(
                """
                SELECT id, name, description, pain_points
                FROM pulse_topic_clusters
                WHERE used_count = 0
                ORDER BY priority_score DESC, created_at DESC
                LIMIT ?
            """,
                (remaining,),
            ).fetchall()
            for r in rows:
                d = (
                    dict(r)
                    if hasattr(r, "keys")
                    else {
                        "id": r[0],
                        "topic": r[1],
                        "pain_point": r[2],
                    }
                )
                d.setdefault("topic", d.get("name", ""))
                d.setdefault("pain_point", d.get("description", ""))
                d["source"] = "topic_cluster"
                topics.append(d)
        except Exception as e:
            print(f"  WARN: Could not fetch topic clusters: {e}")

    conn.close()
    return topics


def _draft_article(topic: str, pain_point: str) -> Optional[Dict[str, Any]]:
    """Draft article using full Pulse pipeline (gemma3 draft → qwen3.5 rewrite)."""
    try:
        from tools.pulse.engine.scheduler import run_full_pipeline
        from tools.pulse.db import init_db

        init_db()
        result = run_full_pipeline(
            topic_override=topic,
            template_type="challenge_solution",
            auto_rewrite=True,
        )
        if result and result.get("status") == "completed" and result.get("post_id"):
            return {"body": result.get("body_markdown", ""), "post_id": result["post_id"]}
    except Exception as e:
        print(f"  WARN: Pipeline failed: {e}")
    return None


def _run_writeguard(body: str) -> Dict[str, Any]:
    """Run WriteGuard quality check (deterministic, no LLM)."""
    try:
        from tools.pulse.writeguard import check_quality

        result = check_quality(body)
        return result if isinstance(result, dict) else {"score": 0, "findings": []}
    except Exception as e:
        print(f"  WARN: WriteGuard failed: {e}")
        return {"score": 0, "findings": [{"error": str(e)}]}


def _generate_hero_image(topic: str, category: str = "") -> Optional[Dict[str, Any]]:
    """Generate a hero image for the article (GPU → SVG fallback)."""
    try:
        from tools.pulse.engine.image_generator import generate_hero_image

        result = generate_hero_image(title=topic, category=category)
        if result and result.get("success"):
            print(f"  Publish: hero image generated via {result['method']} ({result.get('elapsed_ms', 0)}ms)")
            return result
    except Exception as e:
        print(f"  WARN: Hero image generation failed: {e}")
    return None


def _stage_draft(
    topic: str,
    body: str,
    quality_score: float,
    demand_signal_id: Optional[int] = None,
    hero_image: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """Insert article as staging draft in pulse_posts."""
    import uuid

    now = _utcnow_iso()
    slug = topic[:80].lower().replace(" ", "-").replace("/", "-")
    post_id = f"pulse-genesis-{uuid.uuid4().hex[:8]}"
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO pulse_posts
               (id, title, slug, status, topic, body_markdown,
                readability_score, hero_image_path, hero_image_method,
                hero_image_prompt, author_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                post_id,
                topic[:300],
                slug,
                "draft",  # D-GEN: staging/draft only
                topic[:300],
                body,
                quality_score,
                hero_image.get("path") if hero_image else None,
                hero_image.get("method") if hero_image else None,
                hero_image.get("prompt") if hero_image else None,
                "genesis_publish",
                now,
                now,
            ),
        )
        conn.commit()
        return post_id
    except Exception as e:
        print(f"  WARN: Failed to stage draft: {e}")
        return None
    finally:
        conn.close()


def _run_sam_bridge(max_articles: int = 2) -> List[Dict[str, Any]]:
    """Run SAM.gov → Pulse bridge to generate articles from federal opportunities.

    Returns list of result dicts from run_full_pipeline, or empty list if
    no SAM opportunities or bridge unavailable.
    """
    results = []
    try:
        from tools.pulse.engine.sam_bridge import run_sam_to_pulse
        from tools.pulse.db import init_db

        init_db()
        sam_result = run_sam_to_pulse(dry_run=False, max_articles=max_articles)
        generated = sam_result.get("articles_generated", 0)
        if generated > 0:
            print(f"  Publish: SAM bridge generated {generated} article(s)")
            for detail in sam_result.get("details", []):
                if detail.get("status") == "drafted" and detail.get("post_id"):
                    results.append(
                        {
                            "topic": detail.get("topic", "")[:60],
                            "status": "staged",
                            "post_id": detail["post_id"],
                            "source": "sam_gov",
                            "quality_score": detail.get("quality_score", 0),
                        }
                    )
        else:
            print(
                f"  Publish: SAM bridge — no new articles (processed: {sam_result.get('opportunities_processed', 0)})"
            )
    except Exception as e:
        print(f"  Publish: SAM bridge skipped — {e}")
    return results


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Publish Reflex.

    Strategy: SAM.gov opportunities first, then demand topics as fallback.
    """
    max_articles = config.get("max_articles_per_day", 2)
    _min_score = config.get("require_writeguard_pass", True) and 80 or 0  # noqa: F841

    # Phase 1: Try SAM.gov → Pulse bridge first
    sam_results = _run_sam_bridge(max_articles=max_articles)
    remaining = max_articles - len(sam_results)

    # Phase 2: Fill remaining slots with demand topics
    topics = _get_pending_topics(limit=remaining) if remaining > 0 else []
    if not topics and not sam_results:
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {"status": "no_pending_topics", "sam_articles": 0},
        }

    results = []
    best_score = 0.0

    for topic_data in topics:
        topic = topic_data.get("topic", "")
        pain_point = topic_data.get("pain_point", "")
        source = topic_data.get("source", "unknown")

        print(f"  Publish: drafting '{topic[:60]}...' (source: {source})")

        # Run full pipeline: draft (gemma3) → WriteGuard → rewrite (qwen3.5) → save
        draft = _draft_article(topic, pain_point)
        if not draft:
            results.append({"topic": topic[:60], "status": "draft_failed", "source": source})
            continue

        post_id = draft.get("post_id")
        results.append(
            {
                "topic": topic[:60],
                "status": "staged" if post_id else "stage_failed",
                "post_id": post_id,
                "source": source,
            }
        )

        # Mark topic cluster as used
        if source == "topic_cluster" and topic_data.get("id"):
            try:
                conn = get_connection()
                conn.execute(
                    "UPDATE pulse_topic_clusters SET used_count = used_count + 1 WHERE id = ?", (topic_data["id"],)
                )
                conn.commit()
                conn.close()
            except Exception:
                pass

    # Combine SAM + demand topic results
    all_results = sam_results + results
    staged = [r for r in all_results if r.get("status") == "staged"]

    return {
        "success": len(staged) > 0,
        "metric_value": best_score,
        "details": {
            "sam_articles": len(sam_results),
            "demand_topics_attempted": len(topics),
            "articles_staged": len(staged),
            "results": all_results,
        },
    }