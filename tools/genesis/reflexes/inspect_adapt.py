# CUI // SP-CTI
"""Inspect & Adapt Reflex — Weekly SaFe-style retrospective for kanban lessons.

Aggregates `lesson_learned` memory entries from the past N days, identifies
trending patterns, generates a markdown retrospective report, and creates
remediation predictions for categories that are worsening.

Schedule: Mondays at 9am (configurable via genesis_config.yaml).
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Module-level constants — retrospective thresholds & NLP limits. The first
# three are config-overridable from lesson_learned config under
# `retrospective`; these named values are the in-code fallbacks. Change
# config, not code.
# ---------------------------------------------------------------------------
_DEFAULT_CADENCE_DAYS        = 7      # look-back window if config absent
_DEFAULT_MIN_LESSONS         = 3      # min lessons before a report is built
_DEFAULT_TRENDING_THRESHOLD  = 2      # pattern count >= this is "trending"

_NLP_LESSON_CAP        = 50    # max lessons sent to NLP (token-spend cap)
_NLP_REASON_CHARS      = 200   # per-lesson failure-reason truncation
_NLP_MAX_TOKENS        = 512   # NLP response token budget

_REPORT_TOP_PATTERNS   = 10    # top-N patterns/categories/prefixes in report
_REPORT_TOP_OUTCOMES   = 5     # top-N outcome rows in report

_RECURRENCE_NORMALIZER = 10.0  # divisor mapping occurrence count -> [0,1]
_RECURRENCE_MAX        = 1.0   # recurrence score ceiling
_PREFIX_FALLBACK_CHARS = 10    # task_id slice when no prefix delimiter found

# ---------------------------------------------------------------------------
# Anomaly-detection fallbacks — adaptive "trending" cutoff. Rather than a fixed
# count (>= _DEFAULT_TRENDING_THRESHOLD), the cutoff is derived from the spread
# of pattern occurrence counts so a pattern trends only when it is a statistical
# outlier (mean + sigma * std_dev). Overridable from lesson_learned config under
# `retrospective.anomaly_detection`. Change config, not code.
# ---------------------------------------------------------------------------
_ANOMALY_SIGMA_MULTIPLIER = 1.0   # outlier cutoff = mean + this * std_dev
_ANOMALY_MIN_DISTINCT     = 3     # need >= this many distinct patterns to model spread
_ANOMALY_MAX_THRESHOLD    = 50    # ceiling so a noisy window can't suppress all trends


def _compute_trending_threshold(
    pattern_counts: List[int],
    static_threshold: int,
    anomaly_cfg: "Dict[str, Any] | None" = None,
) -> int:
    """Adaptively derive the "trending" count cutoff via anomaly detection.

    A pattern is trending when its occurrence count is a statistical outlier in
    the current window: ``cutoff = ceil(mean + sigma_multiplier * std_dev)``.
    The result is floored at ``static_threshold`` (so the adaptive cutoff is
    never *more permissive* than the configured minimum) and capped at
    ``max_threshold``. Falls back to ``static_threshold`` when anomaly detection
    is disabled or there are fewer than ``min_distinct_patterns`` patterns to
    model — too small a sample to estimate spread reliably.
    """
    cfg = anomaly_cfg or {}
    if not cfg.get("enabled", True):
        return static_threshold

    counts = [int(c) for c in pattern_counts if c is not None]
    min_distinct = int(cfg.get("min_distinct_patterns", _ANOMALY_MIN_DISTINCT))
    if len(counts) < min_distinct:
        return static_threshold

    sigma_multiplier = float(cfg.get("sigma_multiplier", _ANOMALY_SIGMA_MULTIPLIER))
    ceil_cap = int(cfg.get("max_threshold", _ANOMALY_MAX_THRESHOLD))

    import math  # noqa: PLC0415

    n = len(counts)
    mean = sum(counts) / n
    var = sum((c - mean) ** 2 for c in counts) / n
    std = math.sqrt(max(0.0, var))
    adaptive = int(math.ceil(mean + sigma_multiplier * std))
    return max(static_threshold, min(ceil_cap, adaptive))


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
    window_days = retrospective_cfg.get("cadence_days", _DEFAULT_CADENCE_DAYS)
    min_lessons = retrospective_cfg.get("min_lessons_for_report", _DEFAULT_MIN_LESSONS)
    static_threshold = retrospective_cfg.get("trending_threshold", _DEFAULT_TRENDING_THRESHOLD)

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

    # Anomaly-detection: derive the trending cutoff from the spread of pattern
    # counts so only statistical outliers trend, rather than a fixed count.
    pattern_counts = Counter(l.get("pattern", "unknown") for l in lessons)
    trending_threshold = _compute_trending_threshold(
        list(pattern_counts.values()),
        static_threshold,
        retrospective_cfg.get("anomaly_detection", {}),
    )

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
            "trending_threshold_used": trending_threshold,
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


def _nlp_extract_patterns(lessons: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Use Claude Haiku NLP to extract systemic patterns and themes from lesson content.

    Returns a dict with keys: systemic_patterns, root_cause_themes, recommendation.
    Falls back to empty dict if anthropic is unavailable or the call fails.
    """
    from tools.llm.anthropic_provider import anthropic_sdk  # noqa: PLC0415
    if anthropic_sdk is None:
        logger.debug("anthropic not available; skipping NLP pattern extraction")
        return {}

    model = os.environ.get("ICDEV_NLP_MODEL", "claude-haiku-4-5-20251001")
    client = anthropic_sdk.Anthropic()

    lesson_texts: List[str] = []
    for i, lesson in enumerate(lessons[:_NLP_LESSON_CAP]):  # cap to control token spend
        parts: List[str] = []
        if lesson.get("task_title"):
            parts.append(f"Task: {lesson['task_title']}")
        if lesson.get("pattern"):
            parts.append(f"Pattern: {lesson['pattern']}")
        if lesson.get("last_failure_reason"):
            parts.append(f"Reason: {lesson['last_failure_reason'][:_NLP_REASON_CHARS]}")
        if parts:
            lesson_texts.append(f"{i + 1}. " + " | ".join(parts))

    if not lesson_texts:
        return {}

    prompt = (
        "Analyze these kanban retrospective lessons and extract:\n"
        "1. top_systemic_patterns — up to 5 recurring issue patterns (short snake_case labels)\n"
        "2. root_cause_themes — up to 5 root causes (e.g. missing_tests, scope_creep, api_changes)\n"
        "3. recommendation — one concise actionable improvement sentence\n\n"
        "Lessons:\n" + "\n".join(lesson_texts) + "\n\n"
        'Respond with ONLY valid JSON: {"systemic_patterns": [...], "root_cause_themes": [...], "recommendation": "..."}'
    )

    try:
        response = client.messages.create(
            model=model,
            max_tokens=_NLP_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        if "{" in text:
            text = text[text.index("{") : text.rindex("}") + 1]
        return json.loads(text)
    except Exception as exc:
        logger.debug("NLP pattern extraction failed: %s", exc)
        return {}


def _build_report(lessons: List[Dict[str, Any]], window_days: int) -> Dict[str, Any]:
    """Aggregate lessons into a structured report, enhanced with NLP pattern extraction."""
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
        "pattern_breakdown": dict(patterns.most_common(_REPORT_TOP_PATTERNS)),
        "category_breakdown": dict(categories.most_common(_REPORT_TOP_PATTERNS)),
        "outcome_breakdown": dict(outcomes.most_common(_REPORT_TOP_OUTCOMES)),
        "top_prefixes": dict(prefix_counter.most_common(_REPORT_TOP_PATTERNS)),
    }

    nlp_insights = _nlp_extract_patterns(lessons)
    if nlp_insights:
        report["nlp_systemic_patterns"] = nlp_insights.get("systemic_patterns", [])
        report["nlp_root_cause_themes"] = nlp_insights.get("root_cause_themes", [])
        report["nlp_recommendation"] = nlp_insights.get("recommendation", "")

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
                recurrence_score=min(_RECURRENCE_MAX, count / _RECURRENCE_NORMALIZER),
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

    nlp_patterns = report.get("nlp_systemic_patterns", [])
    nlp_themes = report.get("nlp_root_cause_themes", [])
    nlp_rec = report.get("nlp_recommendation", "")
    if nlp_patterns or nlp_themes or nlp_rec:
        lines.extend([
            "",
            "## NLP Insights (Claude Haiku)",
            "",
        ])
        if nlp_patterns:
            lines.append("**Systemic patterns:** " + ", ".join(f"`{p}`" for p in nlp_patterns))
        if nlp_themes:
            lines.append("**Root cause themes:** " + ", ".join(f"`{t}`" for t in nlp_themes))
        if nlp_rec:
            lines.append(f"**Recommendation:** {nlp_rec}")

    lines.extend([
        "",
        "---",
        f"_Generated by inspect_adapt reflex at {report['generated_at']}_",
    ])

    path.write_text("\n".join(lines), encoding="utf-8", newline="")
    logger.info("inspect_adapt: retrospective written to %s", path)


def _task_prefix(task_id: str) -> str:
    """Extract prefix — same logic as lesson_learned.py."""
    import re  # noqa: PLC0415
    if not task_id:
        return ""
    m = re.match(r"^([a-z0-9]+)-", task_id)
    return m.group(1) if m else task_id.split("-")[0] if "-" in task_id else task_id[:_PREFIX_FALLBACK_CHARS]
