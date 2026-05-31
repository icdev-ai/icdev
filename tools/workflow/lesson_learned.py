# CUI // SP-CTI
"""Lessons Learned Engine — Automated Inspect & Adapt for Kanban tasks.

Analyzes every kanban task that reaches `done` or is quarantined to `suggested`,
classifies the outcome into a pattern taxonomy, detects recurrence, and writes
structured lessons to `memory_entries`. Systemic lessons spawn suggested kanban
cards via `oracle_predictions` so the pipeline continuously improves itself.

SaFe alignment: "Inspect & Adapt" ceremony → automated post-task closure ritual.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ────────────────────────────────────────────────────────────────────────────
# Pattern taxonomy
# ────────────────────────────────────────────────────────────────────────────


class LessonPattern:
    """Deterministic outcome classification — no LLM required."""

    SUCCESS_FIRST_TRY = "success_first_try"
    SUCCESS_AFTER_RETRY = "success_after_retry"
    SUCCESS_AFTER_DECOMPOSITION = "success_after_decomposition"
    SUCCESS_AFTER_REMEDIATION = "success_after_remediation"
    TIMEOUT_QUARANTINE = "timeout_quarantine"
    TOKEN_EXHAUSTION = "token_exhaustion"
    PHANTOM_COMPLETION = "phantom_completion"
    PERMISSION_BLOCKED = "permission_blocked"
    VERIFICATION_FAIL = "verification_fail"
    SELF_DEBUG_QUARANTINED = "self_debug_quarantined"
    AUTO_DECOMPOSED = "auto_decomposed"
    STALE_CLEANUP = "stale_cleanup"
    AUTOFIXED = "autofixed"
    AUTO_REMEDIATED = "auto_remediated"
    UNKNOWN = "unknown"


# Patterns that are "systemic" (indicate a pipeline/tool problem, not a task problem)
_SYSTEMIC_PATTERNS = {
    LessonPattern.TIMEOUT_QUARANTINE,
    LessonPattern.TOKEN_EXHAUSTION,
    LessonPattern.PHANTOM_COMPLETION,
    LessonPattern.PERMISSION_BLOCKED,
    LessonPattern.SELF_DEBUG_QUARANTINED,
    LessonPattern.STALE_CLEANUP,
    LessonPattern.AUTO_DECOMPOSED,
    LessonPattern.VERIFICATION_FAIL,
}

# ────────────────────────────────────────────────────────────────────────────
# Config
# ────────────────────────────────────────────────────────────────────────────


_CONFIG_PATH = BASE_DIR / "args" / "lesson_learned_config.yaml"
_DEFAULT_CONFIG = {
    "enabled": True,
    "recurrence_threshold": 0.30,
    "systemic_threshold": 0.50,
    "llm_analysis": {"enabled": False, "model": "claude-sonnet-4-6", "max_tokens": 2048},
    "always_remediate": [
        LessonPattern.TOKEN_EXHAUSTION,
        LessonPattern.PHANTOM_COMPLETION,
        LessonPattern.PERMISSION_BLOCKED,
    ],
    "never_remediate": [LessonPattern.SUCCESS_FIRST_TRY],
}


def _load_config() -> Dict[str, Any]:
    try:
        import yaml  # noqa: PLC0415
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except Exception:
        raw = {}
    cfg = dict(_DEFAULT_CONFIG)
    for k in ("enabled", "recurrence_threshold", "systemic_threshold", "always_remediate", "never_remediate"):
        if k in raw:
            cfg[k] = raw[k]
    cfg["llm_analysis"] = dict(_DEFAULT_CONFIG["llm_analysis"])
    if "llm_analysis" in raw and isinstance(raw["llm_analysis"], dict):
        cfg["llm_analysis"].update(raw["llm_analysis"])
    return cfg


# ────────────────────────────────────────────────────────────────────────────
# Data structures
# ────────────────────────────────────────────────────────────────────────────


@dataclass
class Lesson:
    task_id: str
    task_title: str
    outcome: str  # 'success' | 'failure' | 'auto_decomposed' | 'stale_cleanup' | 'self_debug_quarantined' | 'autofixed' | 'auto_remediated'
    pattern: str  # LessonPattern constant
    category: str  # human-readable category label
    failure_count: int
    last_failure_reason: str
    transitions_count: int
    recurrence_score: float  # 0.0-1.0
    is_systemic: bool
    recommendation: str = ""
    commit_summary: str = ""
    diff_stats: Dict[str, int] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class RecurrenceReport:
    total_similar: int
    total_in_window: int
    recurrence_score: float
    recent_lessons: List[Dict[str, Any]]


# ────────────────────────────────────────────────────────────────────────────
# Core: analyze a single task
# ────────────────────────────────────────────────────────────────────────────


def analyze_task(task_id: str, outcome: str) -> Lesson:
    """Collect metadata, classify pattern, compute recurrence.

    Parameters
    ----------
    task_id: kanban_tasks.id
    outcome: one of the canonical outcome strings

    Returns
    -------
    Lesson dataclass ready for write_lesson()
    """
    from tools.db.storage import get_connection  # noqa: PLC0415

    cfg = _load_config()
    if not cfg.get("enabled"):
        # Return a minimal lesson so callers don't crash; it won't be written
        return Lesson(
            task_id=task_id, task_title="", outcome=outcome, pattern=LessonPattern.UNKNOWN,
            category="disabled", failure_count=0, last_failure_reason="",
            transitions_count=0, recurrence_score=0.0, is_systemic=False,
        )

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT title, failure_count, last_failure_reason, "
            "       files_changed, lines_added, lines_removed, "
            "       branch_name, commit_summary "
            "FROM kanban_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
        if not row:
            return Lesson(
                task_id=task_id, task_title="", outcome=outcome, pattern=LessonPattern.UNKNOWN,
                category="missing", failure_count=0, last_failure_reason="",
                transitions_count=0, recurrence_score=0.0, is_systemic=False,
            )

        d = dict(row)
        title = d.get("title") or ""
        failure_count = int(d.get("failure_count") or 0)
        last_reason = d.get("last_failure_reason") or ""
        diff_stats = {
            "files_changed": int(d.get("files_changed") or 0),
            "lines_added": int(d.get("lines_added") or 0),
            "lines_removed": int(d.get("lines_removed") or 0),
        }
        commit_summary = d.get("commit_summary") or ""

        # Count status transitions for this task (proxy for retry churn)
        tc_row = conn.execute(
            "SELECT COUNT(*) FROM kanban_status_transitions WHERE task_id = %s",
            (task_id,),
        ).fetchone()
        transitions_count = int(tc_row[0]) if tc_row else 0

        pattern = _classify_outcome(outcome, failure_count, last_reason, transitions_count)
        category = _pattern_to_category(pattern)

        rec = get_recurrence(pattern, _task_prefix(task_id), _task_type(task_id, conn))
        recurrence_score = rec.recurrence_score

        is_systemic = _is_systemic(pattern, recurrence_score, cfg)
        recommendation = _build_recommendation(pattern, last_reason, failure_count, transitions_count)

        return Lesson(
            task_id=task_id,
            task_title=title,
            outcome=outcome,
            pattern=pattern,
            category=category,
            failure_count=failure_count,
            last_failure_reason=last_reason,
            transitions_count=transitions_count,
            recurrence_score=recurrence_score,
            is_systemic=is_systemic,
            recommendation=recommendation,
            commit_summary=commit_summary,
            diff_stats=diff_stats,
        )
    finally:
        conn.close()


def _classify_outcome(outcome: str, failure_count: int, last_reason: str, transitions_count: int) -> str:
    """Heuristic classification — deterministic, fast, no LLM."""
    reason = (last_reason or "").lower()

    if outcome == "auto_decomposed":
        return LessonPattern.AUTO_DECOMPOSED
    if outcome == "stale_cleanup":
        return LessonPattern.STALE_CLEANUP
    if outcome == "self_debug_quarantined":
        return LessonPattern.SELF_DEBUG_QUARANTINED
    if outcome == "autofixed":
        return LessonPattern.AUTOFIXED
    if outcome == "auto_remediated":
        return LessonPattern.AUTO_REMEDIATED

    # Token exhaustion indicators
    if "token" in reason or "rate limit" in reason or "session limit" in reason:
        return LessonPattern.TOKEN_EXHAUSTION

    # Timeout
    if "timeout" in reason:
        if failure_count >= 3:
            return LessonPattern.TIMEOUT_QUARANTINE
        return LessonPattern.VERIFICATION_FAIL  # generic failure path

    # Permission blocked
    if "permission" in reason or "permission_blocked" in reason:
        return LessonPattern.PERMISSION_BLOCKED

    # Phantom completion
    if "phantom" in reason or "no commits" in reason or "unverified" in reason:
        return LessonPattern.PHANTOM_COMPLETION

    # Success paths
    if outcome == "success":
        if failure_count == 0 and transitions_count <= 2:
            return LessonPattern.SUCCESS_FIRST_TRY
        if failure_count > 0:
            return LessonPattern.SUCCESS_AFTER_RETRY
        if transitions_count > 2:
            # Multiple transitions without failure_count usually means decomposition
            return LessonPattern.SUCCESS_AFTER_DECOMPOSITION
        return LessonPattern.SUCCESS_FIRST_TRY

    # Default failure
    return LessonPattern.VERIFICATION_FAIL


def _pattern_to_category(pattern: str) -> str:
    """Human-readable label for the pattern."""
    labels = {
        LessonPattern.SUCCESS_FIRST_TRY: "First-try success",
        LessonPattern.SUCCESS_AFTER_RETRY: "Success after retry",
        LessonPattern.SUCCESS_AFTER_DECOMPOSITION: "Success after decomposition",
        LessonPattern.SUCCESS_AFTER_REMEDIATION: "Success after remediation",
        LessonPattern.TIMEOUT_QUARANTINE: "Timeout quarantine",
        LessonPattern.TOKEN_EXHAUSTION: "Token / rate limit",
        LessonPattern.PHANTOM_COMPLETION: "Phantom completion",
        LessonPattern.PERMISSION_BLOCKED: "Permission blocked",
        LessonPattern.VERIFICATION_FAIL: "Verification failure",
        LessonPattern.SELF_DEBUG_QUARANTINED: "Self-debug quarantine",
        LessonPattern.AUTO_DECOMPOSED: "Auto-decomposed",
        LessonPattern.STALE_CLEANUP: "Stale cleanup",
        LessonPattern.AUTOFIXED: "Autofixed",
        LessonPattern.AUTO_REMEDIATED: "Auto-remediated",
        LessonPattern.UNKNOWN: "Unknown",
    }
    return labels.get(pattern, pattern)


def _is_systemic(pattern: str, recurrence_score: float, cfg: Dict[str, Any]) -> bool:
    """True if this pattern indicates a systemic pipeline issue worth fixing."""
    if pattern in cfg.get("never_remediate", []):
        return False
    if pattern in cfg.get("always_remediate", []):
        return True
    if pattern in _SYSTEMIC_PATTERNS:
        return recurrence_score >= cfg.get("systemic_threshold", 0.50)
    return False


def _build_recommendation(pattern: str, last_reason: str, failure_count: int, transitions_count: int) -> str:
    """Generate a human-readable recommendation based on the pattern."""
    reason = last_reason or ""
    recommendations = {
        LessonPattern.TIMEOUT_QUARANTINE: (
            f"Task timed out {failure_count}x. Consider pre-decomposition, "
            "extending timeout budget, or splitting into smaller sub-tasks."
        ),
        LessonPattern.TOKEN_EXHAUSTION: (
            "Token/rate limit encountered. Review LLM provider capacity, "
            "add circuit-breaker backoff, or route to alternative executor."
        ),
        LessonPattern.PHANTOM_COMPLETION: (
            "Agent reported completion but produced no verifiable commits. "
            "Strengthen phantom-completion guard or add file-existence checks."
        ),
        LessonPattern.PERMISSION_BLOCKED: (
            "Agent blocked by write-permission prompt. Review --dangerously-skip-permissions "
            "usage or add pre-dispatch permission audit."
        ),
        LessonPattern.SELF_DEBUG_QUARANTINED: (
            f"Self-debug quarantined after {failure_count} identical failures. "
            "Review the failure signature and patch the root cause before retry."
        ),
        LessonPattern.AUTO_DECOMPOSED: (
            "Task was auto-decomposed into children. Parent task pattern suggests "
            "the work was too large for a single dispatch."
        ),
        LessonPattern.STALE_CLEANUP: (
            "Subprocess went stale mid-run. Verify worktree health, "
            "subprocess timeout logic, and scheduler pause-gate behavior."
        ),
        LessonPattern.VERIFICATION_FAIL: (
            f"Failed verification after {failure_count} attempts. "
            "Review verification criteria or decompose the task."
        ),
        LessonPattern.AUTOFIXED: (
            "Auto-remediation applied a patch. Verify the fix in the next cycle "
            "and consider promoting the handler to a permanent tool change."
        ),
        LessonPattern.AUTO_REMEDIATED: (
            "Pre-backlog auto-remediation resolved an idempotent issue. "
            "If this recurs, the fix should be pushed upstream."
        ),
    }
    return recommendations.get(pattern, f"Review failure reason: {reason[:200]}")


# ────────────────────────────────────────────────────────────────────────────
# Recurrence detection
# ────────────────────────────────────────────────────────────────────────────


def get_recurrence(pattern: str, prefix: str, task_type: str, days: int = 7) -> RecurrenceReport:
    """Count how many similar lessons exist in the look-back window.

    'Similar' = same pattern AND (same prefix OR same task_type).
    Recurrence_score = similar_count / max(total_in_window, 1), capped at 1.0.
    """
    from tools.db.storage import get_connection  # noqa: PLC0415

    conn = get_connection()
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        # Total lessons in window
        total_row = conn.execute(
            "SELECT COUNT(*) FROM memory_entries WHERE type = %s AND created_at >= %s",
            ("lesson_learned", since),
        ).fetchone()
        total_in_window = int(total_row[0]) if total_row else 0

        # Similar lessons (same pattern, matching prefix or task_type)
        # We store the pattern in the JSON content; we search via content LIKE
        similar = 0
        recent: List[Dict[str, Any]] = []
        if total_in_window > 0:
            # Approximate search: pattern appears in the JSON content
            pattern_q = f'"pattern": "{pattern}"'
            rows = conn.execute(
                "SELECT content, created_at FROM memory_entries "
                "WHERE type = %s AND created_at >= %s AND content LIKE %s",
                ("lesson_learned", since, f"%{pattern_q}%"),
            ).fetchall()
            for r in rows:
                try:
                    payload = json.loads(r[0])
                    r_prefix = _task_prefix(payload.get("task_id", ""))
                    r_type = payload.get("task_type", "")
                    if r_prefix == prefix or r_type == task_type:
                        similar += 1
                        recent.append(payload)
                except Exception:
                    continue

        score = min(1.0, similar / max(total_in_window, 1))
        return RecurrenceReport(
            total_similar=similar,
            total_in_window=total_in_window,
            recurrence_score=round(score, 3),
            recent_lessons=recent[:5],
        )
    finally:
        conn.close()


# ────────────────────────────────────────────────────────────────────────────
# Persistence
# ────────────────────────────────────────────────────────────────────────────


def write_lesson(lesson: Lesson) -> str:
    """Write a lesson to memory_entries via memory_write.py.

    Returns the memory entry id (or existing id on dedup).
    """
    cfg = _load_config()
    if not cfg.get("enabled"):
        return ""

    payload = {
        "task_id": lesson.task_id,
        "task_title": lesson.task_title,
        "outcome": lesson.outcome,
        "pattern": lesson.pattern,
        "category": lesson.category,
        "failure_count": lesson.failure_count,
        "last_failure_reason": lesson.last_failure_reason,
        "transitions_count": lesson.transitions_count,
        "recurrence_score": lesson.recurrence_score,
        "is_systemic": lesson.is_systemic,
        "recommendation": lesson.recommendation,
        "commit_summary": lesson.commit_summary,
        "diff_stats": lesson.diff_stats,
        "timestamp": lesson.timestamp,
    }
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    try:
        from tools.memory.memory_write import write_to_db  # noqa: PLC0415

        result = write_to_db(
            content=content,
            entry_type="lesson_learned",
            importance="high" if lesson.is_systemic else "medium",
            source="auto",
            classification="CUI",
        )
        entry_id = str(result.get("id", ""))
        status = result.get("status", "")
        logger.info(
            "lesson_learned: %s %s (pattern=%s, systemic=%s, recurrence=%.2f)",
            lesson.task_id,
            status,
            lesson.pattern,
            lesson.is_systemic,
            lesson.recurrence_score,
        )
        return entry_id
    except Exception as exc:
        logger.warning("lesson_learned write failed for %s: %s", lesson.task_id, exc)
        return ""


# ────────────────────────────────────────────────────────────────────────────
# Remediation card creation
# ────────────────────────────────────────────────────────────────────────────


def maybe_create_remediation_card(lesson: Lesson) -> Optional[str]:
    """If the lesson is systemic, create an oracle_predictions row so
    suggested_card_writer materializes a kanban card.

    Returns the oracle_predictions.id on success, None otherwise.
    """
    cfg = _load_config()
    if not cfg.get("enabled"):
        return None
    if not lesson.is_systemic:
        return None

    try:
        from tools.workflow.lesson_learned_remediation import create_prediction  # noqa: PLC0415

        pred_id = create_prediction(lesson)
        if pred_id:
            logger.info(
                "lesson_learned: created remediation prediction %s for %s (pattern=%s)",
                pred_id,
                lesson.task_id,
                lesson.pattern,
            )
        return pred_id
    except Exception as exc:
        logger.warning(
            "lesson_learned remediation card failed for %s: %s", lesson.task_id, exc
        )
        return None


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


def _task_prefix(task_id: str) -> str:
    """Extract the project prefix from a task ID.

    Examples:
        prop-sec-01      → "prop"
        efa-E3-build     → "efa"
        task-abc123      → "task"
        cdh-gap-xxx      → "cdh"
    """
    if not task_id:
        return ""
    m = re.match(r"^([a-z0-9]+)-", task_id)
    return m.group(1) if m else task_id.split("-")[0] if "-" in task_id else task_id[:10]


def _task_type(task_id: str, conn) -> str:
    """Query kanban_tasks for the task_type of a given task."""
    try:
        row = conn.execute(
            "SELECT task_type FROM kanban_tasks WHERE id = %s", (task_id,)
        ).fetchone()
        return dict(row).get("task_type") or "" if row else ""
    except Exception:
        return ""


# ────────────────────────────────────────────────────────────────────────────
# CLI / diagnostics
# ────────────────────────────────────────────────────────────────────────────


def main():
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Lessons Learned Engine")
    parser.add_argument("--task-id", required=True, help="Kanban task ID to analyze")
    parser.add_argument("--outcome", required=True, help="Outcome type")
    parser.add_argument("--write", action="store_true", help="Write to memory")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    lesson = analyze_task(args.task_id, args.outcome)
    if args.write:
        entry_id = write_lesson(lesson)
        pred_id = maybe_create_remediation_card(lesson)
        if args.json:
            print(
                json.dumps(
                    {
                        "lesson": asdict(lesson),
                        "memory_id": entry_id,
                        "prediction_id": pred_id,
                    }
                )
            )
        else:
            print(f"Written: memory={entry_id} prediction={pred_id}")
    else:
        if args.json:
            print(json.dumps(asdict(lesson)))
        else:
            print(f"Pattern: {lesson.pattern}")
            print(f"Category: {lesson.category}")
            print(f"Recurrence: {lesson.recurrence_score}")
            print(f"Systemic: {lesson.is_systemic}")
            print(f"Recommendation: {lesson.recommendation}")


if __name__ == "__main__":
    main()
