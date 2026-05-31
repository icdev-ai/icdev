# CUI // SP-CTI
"""Kanban workflow metrics — process-health analytics for the Inspect & Adapt pipeline.

Provides deterministic SQL-backed metrics without LLM overhead.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


def dispatch_success_rate(days: int = 7) -> float:
    """Percentage of tasks that reached `done` on first dispatch (failure_count == 0).

    Returns 0.0-1.0 float. A healthy pipeline should be > 0.70.
    """
    from tools.db.storage import get_connection  # noqa: PLC0415

    conn = get_connection()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        total_row = conn.execute(
            "SELECT COUNT(*) FROM kanban_tasks WHERE status = 'done' AND completed_at >= %s",
            (since,),
        ).fetchone()
        total = int(total_row[0]) if total_row else 0
        if total == 0:
            return 0.0

        first_try_row = conn.execute(
            "SELECT COUNT(*) FROM kanban_tasks WHERE status = 'done' "
            "AND completed_at >= %s AND COALESCE(failure_count, 0) = 0",
            (since,),
        ).fetchone()
        first_try = int(first_try_row[0]) if first_try_row else 0
        return round(first_try / total, 3)
    finally:
        conn.close()


def retry_rate_by_prefix(prefix: str, days: int = 7) -> Dict[str, Any]:
    """Average failure_count per task matching the prefix.

    Returns dict with count, avg_failure_count, max_failure_count.
    """
    from tools.db.storage import get_connection  # noqa: PLC0415

    conn = get_connection()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        pattern = f"{prefix}-%"
        rows = conn.execute(
            "SELECT COALESCE(failure_count, 0) FROM kanban_tasks "
            "WHERE id LIKE %s AND updated_at >= %s",
            (pattern, since),
        ).fetchall()
        counts = [int(r[0]) for r in rows]
        if not counts:
            return {"count": 0, "avg_failure_count": 0.0, "max_failure_count": 0}
        return {
            "count": len(counts),
            "avg_failure_count": round(sum(counts) / len(counts), 2),
            "max_failure_count": max(counts),
        }
    finally:
        conn.close()


def top_lesson_categories(days: int = 30, limit: int = 10) -> List[Dict[str, Any]]:
    """Most frequent lesson patterns in the look-back window."""
    try:
        from tools.db.storage import get_connection  # noqa: PLC0415
    except ImportError:
        return []

    conn = get_connection()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT content FROM memory_entries "
            "WHERE type = %s AND created_at >= %s",
            ("lesson_learned", since),
        ).fetchall()

        import json  # noqa: PLC0415
        from collections import Counter  # noqa: PLC0415

        patterns = Counter()
        for r in rows:
            try:
                payload = json.loads(r[0])
                patterns[payload.get("pattern", "unknown")] += 1
            except Exception:
                continue

        return [
            {"pattern": p, "count": c}
            for p, c in patterns.most_common(limit)
        ]
    finally:
        conn.close()


def recurring_patterns(days: int = 30, min_score: float = 0.3) -> List[Dict[str, Any]]:
    """Patterns with recurrence_score >= min_score in recent lessons."""
    try:
        from tools.db.storage import get_connection  # noqa: PLC0415
    except ImportError:
        return []

    conn = get_connection()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT content FROM memory_entries "
            "WHERE type = %s AND created_at >= %s",
            ("lesson_learned", since),
        ).fetchall()

        import json  # noqa: PLC0415

        recurring: List[Dict[str, Any]] = []
        for r in rows:
            try:
                payload = json.loads(r[0])
                score = payload.get("recurrence_score", 0.0)
                if score >= min_score:
                    recurring.append({
                        "task_id": payload.get("task_id", ""),
                        "pattern": payload.get("pattern", "unknown"),
                        "category": payload.get("category", "Unknown"),
                        "recurrence_score": score,
                        "recommendation": payload.get("recommendation", ""),
                    })
            except Exception:
                continue

        # Deduplicate by task_id (keep highest score)
        seen: Dict[str, Dict[str, Any]] = {}
        for item in recurring:
            tid = item["task_id"]
            if tid not in seen or item["recurrence_score"] > seen[tid]["recurrence_score"]:
                seen[tid] = item
        return list(seen.values())
    finally:
        conn.close()


def _days_ago_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
