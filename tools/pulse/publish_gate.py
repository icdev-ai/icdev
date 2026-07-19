# CUI // SP-CTI
"""ICDEV™ Pulse publish gate — LLM judge verdict hard-gates publishing.

nav-intel-09 (user decision 2026-07-19): a RED judge verdict BLOCKS publish.
Judge-not-run or judge-errored is treated as not-cleared (fail closed) with a
"run judge first" message. YELLOW and better verdicts remain publishable —
only RED blocks. An audited HITL force override (admin role + reason) mirrors
the citation_guard pattern (see idr_publish_audit, migration 276).

Call sites (every outward publish chokepoint):
  - tools/dashboard/api/pulse.py::api_pulse_publish  (409 + admin force)
  - tools/pulse/engine/wordpress_publisher.py::publish_post
  - tools/pulse/engine/hostinger_publisher.py::publish_post

The pulse_publish_audit table is APPEND-ONLY (migration 281; registered in
APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# FAR 15.305 color scale (tools/writing/llm_judge.py COLOR_ORDER):
# blue > purple > green > yellow > red. Only red blocks (user decision).
BLOCKING_VERDICTS = frozenset({"red"})

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS pulse_publish_audit (
    id TEXT PRIMARY KEY,
    post_id TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT 'force_publish',
    actor TEXT,
    reason TEXT,
    judge_verdict TEXT,
    source TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pulse_publish_audit_post ON pulse_publish_audit(post_id);
"""


def ensure_audit_table() -> None:
    """Create pulse_publish_audit if migration 281 hasn't run yet."""
    from tools.db.storage import get_connection

    with get_connection() as conn:
        conn.executescript(AUDIT_SCHEMA)
        conn.commit()


def get_latest_judge_verdict(post_id: str) -> str | None:
    """Return the latest judge color for a post, or None if the judge never ran.

    Prefers the newest llm_judge_evaluations row (the judge's own audit table);
    falls back to pulse_posts.judge_color for posts evaluated before that table
    existed or when it is unavailable.
    """
    from tools.db.storage import get_connection

    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT color_rating FROM llm_judge_evaluations "
                "WHERE post_id = %s ORDER BY created_at DESC LIMIT 1",
                (post_id,),
            ).fetchone()
            if row and row["color_rating"]:
                return str(row["color_rating"]).strip().lower()
    except Exception:
        # Table may not exist yet — a failed statement poisons a PG
        # transaction, so retry the fallback on a fresh connection below.
        pass

    with get_connection() as conn:
        row = conn.execute(
            "SELECT judge_color FROM pulse_posts WHERE id = %s", (post_id,)
        ).fetchone()
        if row and row["judge_color"]:
            return str(row["judge_color"]).strip().lower()
    return None


def check_publish_gate(post_id: str) -> dict:
    """Decide whether a post is cleared to publish.

    Returns {"cleared": bool, "verdict": str | None, "reason": str}.
    Fail closed: any lookup error, a missing verdict, or a RED verdict all
    return cleared=False.
    """
    try:
        verdict = get_latest_judge_verdict(post_id)
    except Exception as exc:
        logger.warning("publish gate: judge verdict lookup failed for %s: %s", post_id, exc)
        return {
            "cleared": False,
            "verdict": None,
            "reason": "Judge verdict could not be determined — run the LLM judge before publishing.",
        }
    if not verdict:
        return {
            "cleared": False,
            "verdict": None,
            "reason": "The LLM judge has not evaluated this post — run the judge first.",
        }
    if verdict in BLOCKING_VERDICTS:
        return {
            "cleared": False,
            "verdict": verdict,
            "reason": (
                "LLM judge verdict is RED (unacceptable) — publish is blocked. "
                "Revise the post and re-run the judge, or use an admin force override."
            ),
        }
    return {"cleared": True, "verdict": verdict, "reason": ""}


def log_publish_override(
    post_id: str,
    actor: str,
    reason: str,
    verdict: str | None,
    source: str = "api_publish",
) -> str:
    """Record an audited HITL force-publish override. Returns the audit row id."""
    from tools.db.storage import get_connection

    ensure_audit_table()
    audit_id = f"ppa-{uuid.uuid4().hex[:12]}"
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO pulse_publish_audit "
            "(id, post_id, action, actor, reason, judge_verdict, source, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                audit_id,
                post_id,
                "force_publish",
                actor,
                reason,
                verdict or "",
                source,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    logger.info(
        "publish gate override: post=%s actor=%s verdict=%s source=%s",
        post_id, actor, verdict, source,
    )
    return audit_id
