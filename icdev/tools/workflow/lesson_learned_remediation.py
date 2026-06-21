# CUI // SP-CTI
"""Lessons Learned → Remediation Card Bridge.

Creates `oracle_predictions` rows for systemic lessons so that
`suggested_card_writer.py` can materialize them as kanban cards.

This is a thin adapter — it does NOT create kanban_tasks directly.
It inserts into `oracle_predictions` with `prediction_type='lesson_learned'`
and lets the existing promotion pipeline handle the rest.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_prediction(lesson) -> Optional[str]:
    """Insert an oracle_predictions row for a systemic lesson.

    Parameters
    ----------
    lesson: a tools.workflow.lesson_learned.Lesson instance

    Returns
    -------
    str | None — the oracle_predictions.id on success
    """
    try:
        from tools.db.storage import get_connection  # noqa: PLC0415
    except ImportError:
        logger.warning("lesson_remediation: DB unavailable")
        return None

    conn = get_connection()
    try:
        # Deduplicate: don't create a duplicate open prediction for the same pattern+prefix
        prefix = _task_prefix(lesson.task_id)
        existing = conn.execute(
            "SELECT id FROM oracle_predictions "
            "WHERE prediction_type = %s "
            "  AND subject_id = %s "
            "  AND outcome = 'pending'",
            ("lesson_learned", f"{prefix}::{lesson.pattern}"),
        ).fetchone()
        if existing:
            logger.debug(
                "lesson_remediation: duplicate prediction exists for %s/%s — skipping",
                prefix,
                lesson.pattern,
            )
            return None

        pred_id = f"pred-ll-{uuid.uuid4().hex[:12]}"
        prediction_text = _build_prediction_text(lesson)
        severity = "high" if lesson.recurrence_score >= 0.5 else "medium"
        confidence = min(1.0, max(0.0, lesson.recurrence_score))
        evidence = {
            "task_id": lesson.task_id,
            "task_title": lesson.task_title,
            "failure_count": lesson.failure_count,
            "transitions_count": lesson.transitions_count,
            "last_failure_reason": lesson.last_failure_reason,
            "diff_stats": lesson.diff_stats,
            "commit_summary": lesson.commit_summary,
            "timestamp": lesson.timestamp,
        }

        conn.execute(
            "INSERT INTO oracle_predictions "
            "(id, lens_name, prediction_text, confidence, created_at, "
            " subject_type, subject_id, prediction_type, severity, "
            " horizon_days, evidence_json, outcome, classification) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                pred_id,
                lesson.pattern,  # lens_name = pattern category
                prediction_text,
                confidence,
                _now_iso(),
                "lesson_learned",  # subject_type
                f"{prefix}::{lesson.pattern}",  # subject_id
                "lesson_learned",  # prediction_type
                severity,
                7,  # horizon_days
                json.dumps(evidence, ensure_ascii=False),
                "pending",
                "CUI",
            ),
        )
        conn.commit()
        return pred_id
    except Exception as exc:
        logger.warning("lesson_remediation: DB insert failed: %s", exc)
        return None
    finally:
        conn.close()


def _build_prediction_text(lesson) -> str:
    """Markdown description for the suggested card."""
    lines = [
        f"## [LESSON-LEARNED] {lesson.category}",
        "",
        f"**Pattern:** `{lesson.pattern}`  ",
        f"**Task:** [{lesson.task_id}] {lesson.task_title}  ",
        f"**Recurrence score:** {lesson.recurrence_score:.2f}  ",
        f"**Failure count:** {lesson.failure_count}  ",
        f"**Status transitions:** {lesson.transitions_count}",
        "",
        "### Root Cause",
        lesson.last_failure_reason or "(no failure reason recorded)",
        "",
        "### Recommendation",
        lesson.recommendation,
        "",
        "### Evidence",
        f"- Files changed: {lesson.diff_stats.get('files_changed', 0)}",
        f"- Lines added: {lesson.diff_stats.get('lines_added', 0)}",
        f"- Lines removed: {lesson.diff_stats.get('lines_removed', 0)}",
    ]
    if lesson.commit_summary:
        lines.extend([
            "",
            "### Commits",
            f"```\n{lesson.commit_summary[:500]}\n```",
        ])
    lines.extend([
        "",
        f"_Generated at {lesson.timestamp}_",
    ])
    return "\n".join(lines)


def _task_prefix(task_id: str) -> str:
    """Extract prefix — same logic as lesson_learned.py."""
    import re  # noqa: PLC0415
    if not task_id:
        return ""
    m = re.match(r"^([a-z0-9]+)-", task_id)
    return m.group(1) if m else task_id.split("-")[0] if "-" in task_id else task_id[:10]
