# CUI // SP-CTI
"""Inspect & Adapt Reflex — Weekly SaFe-style retrospective for kanban lessons.

Aggregates `lesson_learned` memory entries from the past N days, identifies
trending patterns, generates a markdown retrospective report, and creates
remediation predictions for categories that are worsening.

Schedule: Mondays at 9am (configurable via genesis_config.yaml).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Genesis reflex entry point.

    Returns a summary dict compatible with genesis daemon metrics.
    """
    try:
        from tools.workflow.lesson_learned import _load_config as _ll_cfg  # noqa: PLC0415

        cfg = _ll_cfg()
    except Exception:
        cfg = {}

    if not cfg.get("enabled", True):
        return {
            "success": True,
            "details": {"status": "disabled", "message": "lesson_learned config disabled"},
        }

    retrospective_cfg = cfg.get("retrospective", {})
    window_days = retrospective_cfg.get("cadence_days", 7)
    min_lessons = retrospective_cfg.get("min_lessons_for_report", 3)
    trending_threshold = retrospective_cfg.get("trending_threshold", 2)

    lessons = _fetch_lessons(window_days)
    if len(lessons) < min_lessons:
        return {
            "success": True,
            "details": {
                "status": "insufficient_data",
                "lessons_count": len(lessons),
                "window_days": window_days,
            },
        }

    report = _build_report(lessons, window_days)
    trending = _detect_trending(lessons, trending_threshold)
    created = _create_remediation_for_trending(trending)

    _write_report_to_disk(report)

    return {
        "success": True,
        "metric_value": len(lessons),
        "details": {
            "status": "retrospective_generated",
            "lessons_count": len(lessons),
            "trending_categories": len(trending),
            "remediation_cards_created": len(created),
            "report_path": str(report.get("path", "")),
        },
    }


def _fetch_lessons(days: int) -> List[Dict[str, Any]]:
    """Query memory_entries for lesson_learned rows in the look-back window."""
    try:
        from tools.db.storage import get_connection  # noqa: PLC0415
    except ImportError:
        return []

    conn = get_connection()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT content, created_at FROM memory_entries "
            "WHERE type = %s AND created_at >= %s "
            "ORDER BY created_at DESC",
            ("lesson_learned", since),
        ).fetchall()
        lessons: List[Dict[str, Any]] = []
        for r in rows:
            try:
                payload = json.loads(r[0])
                payload["_created_at"] = str(r[1])
                lessons.append(payload)
            except Exception:
                continue
        return lessons
    finally:
        conn.close()


def _build_report(lessons: List[Dict[str, Any]], window_days: int) -> Dict[str, Any]:
    """Aggregate lessons into a structured report."""
    patterns = Counter(l.get("pattern", "unknown") for l in lessons)
    categories = Counter(l.get("category", "Unknown") for l in lessons)
    outcomes = Counter(l.get("outcome", "unknown") for l in lessons)
    systemic_count = sum(1 for l in lessons if l.get("is_systemic"))
    avg_recurrence = sum(l.get("recurrence_score", 0.0) for l in lessons) / max(len(lessons), 1)

    # Top prefixes
    prefix_counter: Counter = Counter()
    for l in lessons:
        prefix = _task_prefix(l.get("task_id", ""))
        if prefix:
            prefix_counter[prefix] += 1

    report = {
        "generated_at": _now_iso(),
        "window_days": window_days,
        "total_lessons": len(lessons),
        "systemic_lessons": systemic_count,
        "avg_recurrence_score": round(avg_recurrence, 3),
        "pattern_breakdown": dict(patterns.most_common(10)),
        "category_breakdown": dict(categories.most_common(10)),
        "outcome_breakdown": dict(outcomes.most_common(5)),
        "top_prefixes": dict(prefix_counter.most_common(10)),
    }
    return report


def _detect_trending(lessons: List[Dict[str, Any]], threshold: int) -> Dict[str, int]:
    """Return pattern categories with count >= threshold."""
    patterns = Counter(l.get("pattern", "unknown") for l in lessons)
    return {p: c for p, c in patterns.items() if c >= threshold}


def _create_remediation_for_trending(trending: Dict[str, int]) -> List[str]:
    """For each trending category without an open prediction, create one."""
    created: List[str] = []
    try:
        from tools.db.storage import get_connection  # noqa: PLC0415
        from tools.workflow.lesson_learned_remediation import create_prediction  # noqa: PLC0415
        from tools.workflow.lesson_learned import Lesson  # noqa: PLC0415
    except ImportError:
        return created

    conn = get_connection()
    try:
        for pattern, count in trending.items():
            # Skip if an open prediction already exists for this pattern
            existing = conn.execute(
                "SELECT id FROM oracle_predictions "
                "WHERE prediction_type = %s AND lens_name = %s AND outcome = 'pending'",
                ("lesson_learned", pattern),
            ).fetchone()
            if existing:
                continue

            # Build a synthetic Lesson for the category
            from tools.workflow.lesson_learned import _pattern_to_category  # noqa: PLC0415
            lesson = Lesson(
                task_id=f"trend-{pattern}",
                task_title=f"Trending: {pattern} ({count} occurrences)",
                outcome="trend",
                pattern=pattern,
                category=_pattern_to_category(pattern),
                failure_count=count,
                last_failure_reason=f"{count} tasks exhibited {pattern} in the last retrospective window",
                transitions_count=0,
                recurrence_score=min(1.0, count / 10.0),
                is_systemic=True,
                recommendation=f"Review and fix systemic issue: {pattern}",
            )
            pred_id = create_prediction(lesson)
            if pred_id:
                created.append(pred_id)
    finally:
        conn.close()

    return created


def _write_report_to_disk(report: Dict[str, Any]) -> None:
    """Save a markdown retrospective to memory/logs/."""
    log_dir = BASE_DIR / "memory" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{_today()}-retrospective.md"

    lines = [
        f"# Kanban Retrospective — {_today()}",
        "",
        f"**Window:** {report['window_days']} days  ",
        f"**Total lessons:** {report['total_lessons']}  ",
        f"**Systemic lessons:** {report['systemic_lessons']}  ",
        f"**Avg recurrence:** {report['avg_recurrence_score']:.2f}",
        "",
        "## Pattern Breakdown",
        "",
        "| Pattern | Count |",
        "|---------|-------|",
    ]
    for pattern, count in report.get("pattern_breakdown", {}).items():
        lines.append(f"| {pattern} | {count} |")

    lines.extend([
        "",
        "## Category Breakdown",
        "",
        "| Category | Count |",
        "|----------|-------|",
    ])
    for cat, count in report.get("category_breakdown", {}).items():
        lines.append(f"| {cat} | {count} |")

    lines.extend([
        "",
        "## Top Prefixes",
        "",
        "| Prefix | Count |",
        "|--------|-------|",
    ])
    for prefix, count in report.get("top_prefixes", {}).items():
        lines.append(f"| {prefix} | {count} |")

    lines.extend([
        "",
        "---",
        f"_Generated by inspect_adapt reflex at {report['generated_at']}_",
    ])

    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("inspect_adapt: retrospective written to %s", path)


def _task_prefix(task_id: str) -> str:
    """Extract prefix — same logic as lesson_learned.py."""
    import re  # noqa: PLC0415
    if not task_id:
        return ""
    m = re.match(r"^([a-z0-9]+)-", task_id)
    return m.group(1) if m else task_id.split("-")[0] if "-" in task_id else task_id[:10]
