#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Publish Reflex — end-to-end Pulse article pipeline.

Picks a demand topic → drafts article → runs WriteGuard quality check →
stages as draft (NEVER auto-publishes to production).

YELLOW tier (reversible writes — staging/draft only).
Scanner-tier LLM (qwen3.5) for drafting, WriteGuard deterministic for QA.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_pending_topics(limit: int = 2) -> List[Dict[str, Any]]:
    """Get demand topics that haven't been drafted yet."""
    conn = get_connection()
    try:
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
        return [
            dict(r)
            if hasattr(r, "keys")
            else {"id": r[0], "topic": r[1], "pain_point": r[1], "category": r[2], "keywords": r[3], "created_at": r[4]}
            for r in rows
        ]
    except Exception as e:
        print(f"  WARN: Could not fetch demand topics: {e}")
        return []
    finally:
        conn.close()


def _draft_article(topic: str, pain_point: str) -> Optional[Dict[str, str]]:
    """Draft article using Pulse drafter (scanner-tier LLM)."""
    try:
        from tools.pulse.engine.drafter import draft_post

        result = draft_post(topic=topic, pain_point=pain_point, mode="genesis")
        if result and result.get("body"):
            return result
    except Exception as e:
        print(f"  WARN: Drafter failed: {e}")
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


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Publish Reflex."""
    max_articles = config.get("max_articles_per_day", 2)
    min_score = config.get("require_writeguard_pass", True) and 80 or 0

    topics = _get_pending_topics(limit=max_articles)
    if not topics:
        return {
            "success": True,
            "metric_value": 0.0,
            "details": {"status": "no_pending_topics"},
        }

    results = []
    best_score = 0.0

    for topic_data in topics:
        topic = topic_data.get("topic", "")
        pain_point = topic_data.get("pain_point", "")

        print(f"  Publish: drafting '{topic[:60]}...'")

        # Draft
        draft = _draft_article(topic, pain_point)
        if not draft:
            results.append({"topic": topic[:60], "status": "draft_failed"})
            continue

        # Quality check
        body = draft.get("body", "")
        qc = _run_writeguard(body)
        score = qc.get("score", 0)
        best_score = max(best_score, score)

        if score < min_score:
            results.append(
                {
                    "topic": topic[:60],
                    "status": "quality_below_threshold",
                    "score": score,
                    "threshold": min_score,
                }
            )
            continue

        # Generate hero image
        hero = _generate_hero_image(topic, category=topic_data.get("category", ""))

        # Stage as draft
        post_id = _stage_draft(
            topic,
            body,
            score,
            demand_signal_id=topic_data.get("id"),
            hero_image=hero,
        )

        results.append(
            {
                "topic": topic[:60],
                "status": "staged" if post_id else "stage_failed",
                "post_id": post_id,
                "quality_score": score,
            }
        )

    staged = [r for r in results if r.get("status") == "staged"]

    return {
        "success": len(staged) > 0,
        "metric_value": best_score,
        "details": {
            "topics_attempted": len(topics),
            "articles_staged": len(staged),
            "results": results,
        },
    }
