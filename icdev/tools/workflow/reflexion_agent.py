# CUI // SP-CTI
"""NOVA ECHO — Reflexion Agent.

Post-task completion hook: reads an execution trace + lesson_learned outcome
and generates a structured improvement artifact.  The artifact is persisted
to `agent_improvement_artifacts` and injected into the *next* dispatch of
the same task_type — making each generation smarter than the last.

Inspired by hexo-ai/sia's feedback/improvement agent and DeepMind's Reflexion
paper (trajectory introspection → verbal self-reflection → improved policy).

Called by:
    - kanban scheduler after task close (via post-completion hook)
    - reflexion_loop reflex (weekly batch)

Co-learning flag: ICDEV_HARNESS_COLEARN=true  (safe-off by default)
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger
from tools.workflow.trace_logger import get_traces_for_task_type

logger = get_logger(__name__)

_COLEARN_ENABLED = os.getenv("ICDEV_HARNESS_COLEARN", "").lower() in ("true", "1")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _ensure_tables(conn) -> None:
    try:
        from tools.nova.db.init_db import init_nova_tables
        init_nova_tables(conn)
    except Exception as exc:
        logger.debug("[reflexion] lazy init: %s", exc)


# ────────────────────────────────────────────────────────────────────────────
# Core logic
# ────────────────────────────────────────────────────────────────────────────


def _get_generation_n(conn, task_type: str) -> int:
    """Return the next generation number for a given task_type."""
    try:
        row = conn.execute(
            "SELECT MAX(generation_n) FROM agent_improvement_artifacts WHERE task_type = %s",
            (task_type,),
        ).fetchone()
        val = row[0] if row else None
        return (val or 0) + 1
    except Exception:
        return 1


def _summarize_traces(traces: list[dict]) -> str:
    """Produce a compact summary of recent traces for LLM context."""
    lines = []
    for t in traces[:10]:
        lines.append(
            f"- task_id={t['task_id']} outcome={t['outcome']} "
            f"pattern={t.get('lesson_pattern', 'unknown')} "
            f"notes={t.get('improvement_notes', '')[:120]}"
        )
    return "\n".join(lines) if lines else "No recent traces available."


def _call_llm(prompt: str, skill_used: str) -> str:
    """Call LLMRouter to generate improvement text. Returns empty string on failure."""
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        router = LLMRouter()
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.4,
        )
        resp = router.invoke("code_generation", req)
        return resp.content.strip() if resp and resp.content else ""
    except Exception as exc:
        logger.warning("[reflexion] LLM call failed: %s", exc)
        return ""


# exa-refine-02: this was an f-string literal inside generate_improvement_artifact.
# The text is unchanged; interpolations became named placeholders so the template
# can be versioned in the prompt registry under IMPROVEMENT_PROMPT_NAME. `{x!r}`
# and `{x:.1%}` are applied by _build_improvement_prompt before substitution,
# because a stored template cannot carry Python conversions or format specs.
IMPROVEMENT_PROMPT_NAME = "call_site/reflexion_improvement"
IMPROVEMENT_PROMPT_TEMPLATE = (
    "You are an AI improvement agent for the ICDEV™ platform.\n\n"
    "Task type: {task_type}\n"
    "Skill invoked: {skill_used}\n\n"
    "Recent execution traces (last {trace_count} dispatches):\n{summary}\n\n"
    "Failure traces ({failure_count} total):\n{failure_summary}\n\n"
    "Current success rate: {success_rate}\n\n"
    "Analyze the failure patterns. Propose 2–4 CONCRETE, ACTIONABLE improvements "
    "to the skill instructions or task handling that would increase success rate. "
    "Focus on WHY tasks fail (root cause) and HOW to prevent it. "
    "Be specific: name files, steps, or instructions to change. "
    "Do NOT invent new features — improve the existing skill.\n\n"
    "Output format:\n"
    "## Root Cause\n<1-2 sentences>\n\n"
    "## Proposed Improvements\n<numbered list>\n\n"
    "## Expected Impact\n<1 sentence>"
)


def _build_improvement_prompt(
    task_type: str,
    skill_used: str,
    trace_count: int,
    summary: str,
    failure_count: int,
    failure_summary: str,
    baseline_score: float,
) -> str:
    """Render the Reflexion improvement prompt: active registry version, else default."""
    from tools.llm.prompt_registry import render_prompt

    return render_prompt(
        IMPROVEMENT_PROMPT_NAME,
        IMPROVEMENT_PROMPT_TEMPLATE,
        task_type=repr(task_type),
        skill_used=repr(skill_used),
        trace_count=trace_count,
        summary=summary,
        failure_count=failure_count,
        failure_summary=failure_summary,
        success_rate=f"{baseline_score:.1%}",
    )


def _compute_score(traces: list[dict]) -> float:
    """Deterministic score: fraction of successful outcomes."""
    if not traces:
        return 0.0
    successes = sum(1 for t in traces if t.get("outcome") == "success")
    return round(successes / len(traces), 3)


def generate_improvement_artifact(
    task_type: str,
    skill_used: str = "",
    window: int = 20,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Analyze recent traces for task_type and generate an improvement artifact.

    Returns a dict with artifact_id, improvement_text, composite_score, etc.
    """
    if not _COLEARN_ENABLED:
        logger.debug("[reflexion] co-learning disabled; skipping %s", task_type)
        return {"skipped": True, "reason": "ICDEV_HARNESS_COLEARN not enabled"}

    traces = get_traces_for_task_type(task_type, limit=window)
    if len(traces) < 3:
        return {"skipped": True, "reason": f"insufficient traces ({len(traces)} < 3)"}

    baseline_score = _compute_score(traces)

    # Build improvement prompt (Reflexion-style: summarize failures → propose fixes)
    failures = [t for t in traces if t.get("outcome") not in ("success",)]
    summary = _summarize_traces(traces)
    failure_summary = _summarize_traces(failures) if failures else "No failures detected."

    prompt = _build_improvement_prompt(
        task_type=task_type,
        skill_used=skill_used,
        trace_count=len(traces),
        summary=summary,
        failure_count=len(failures),
        failure_summary=failure_summary,
        baseline_score=baseline_score,
    )

    improvement_text = _call_llm(prompt, skill_used)
    if not improvement_text:
        improvement_text = (
            f"[deterministic] Success rate: {baseline_score:.1%}. "
            f"{len(failures)} failures out of {len(traces)} traces. "
            f"Common patterns: {', '.join(set(t.get('lesson_pattern','') for t in failures if t.get('lesson_pattern')))}"
        )

    artifact_id = f"impr-{task_type[:8]}-{uuid.uuid4().hex[:8]}"
    trace_ids = [t["trace_id"] for t in traces[:5]]

    if not dry_run:
        try:
            conn = _conn()
            _ensure_tables(conn)
            gen_n = _get_generation_n(conn, task_type)
            conn.execute(
                """
                INSERT INTO agent_improvement_artifacts
                    (artifact_id, task_type, skill_used, generation_n,
                     improvement_text, composite_score, baseline_score,
                     evidence_traces, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                """,
                (
                    artifact_id,
                    task_type,
                    skill_used,
                    gen_n,
                    improvement_text,
                    baseline_score,
                    baseline_score,
                    json.dumps(trace_ids),
                ),
            )
            conn.commit()
            logger.info("[reflexion] artifact %s created for %s (gen %d)", artifact_id, task_type, gen_n)
        except Exception as exc:
            logger.warning("[reflexion] failed to persist artifact: %s", exc)
            return {"error": str(exc)}

    return {
        "artifact_id": artifact_id,
        "task_type": task_type,
        "skill_used": skill_used,
        "baseline_score": baseline_score,
        "improvement_text": improvement_text[:500],
        "traces_analyzed": len(traces),
        "failures_found": len(failures),
        "dry_run": dry_run,
    }


def get_latest_improvement(task_type: str) -> str:
    """
    Return the most recent improvement artifact text for injection into dispatch context.
    Returns empty string if none exists or co-learning is disabled.
    """
    if not _COLEARN_ENABLED:
        return ""
    try:
        conn = _conn()
        _ensure_tables(conn)
        row = conn.execute(
            """
            SELECT improvement_text, generation_n
              FROM agent_improvement_artifacts
             WHERE task_type = %s AND status = 'pending'
             ORDER BY generation_n DESC
             LIMIT 1
            """,
            (task_type,),
        ).fetchone()
        if not row:
            return ""
        text = row[0] if not isinstance(row, dict) else row["improvement_text"]
        gen = row[1] if not isinstance(row, dict) else row["generation_n"]
        # Mark as applied
        conn.execute(
            "UPDATE agent_improvement_artifacts SET applied_count = applied_count + 1, "
            "applied_at = %s WHERE task_type = %s AND generation_n = %s",
            (_utcnow(), task_type, gen),
        )
        conn.commit()
        return f"\n\n---\n**[ECHO Gen-{gen} Improvement Note]**\n{text}\n---\n"
    except Exception as exc:
        logger.debug("[reflexion] get_latest_improvement failed: %s", exc)
        return ""


def run_batch_reflexion(task_types: list[str] | None = None, dry_run: bool = False) -> dict:
    """Run improvement generation for all known task_types (or a specified subset)."""
    if task_types is None:
        # Discover task types from recent traces
        try:
            conn = _conn()
            _ensure_tables(conn)
            rows = conn.execute(
                "SELECT DISTINCT task_type FROM agent_execution_traces "
                "WHERE task_type != '' ORDER BY task_type"
            ).fetchall()
            task_types = [
                (r["task_type"] if isinstance(r, dict) else r[0]) for r in rows
            ]
        except Exception:
            task_types = []

    results = {}
    for tt in task_types:
        results[tt] = generate_improvement_artifact(tt, dry_run=dry_run)
    return {"task_types_processed": len(task_types), "results": results}
