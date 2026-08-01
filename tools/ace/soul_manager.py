# CUI // SP-CTI
"""NOVA SOUL — Coworker Memory & Identity Preamble Manager.

SOUL records per-role learned facts in `ace_coworker_memory` and synthesizes
an identity preamble for each coworker by combining the role's `SOUL.md`
identity file, a canonical capability scope, and recent accumulated knowledge.

All DB access is lazy-initialized via `tools.nova.db.init_db.init_nova_tables`
so the module is safe to import before the dashboard has finished startup.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# Maximum facts retained per role before old entries are pruned.
MAX_MEMORY_FACTS = 1000


def _roles_dir() -> Path:
    """ACE roles base directory.

    Honors ``ICDEV_ACE_ROLES_DIR`` so tests can redirect any on-disk persona
    artifact (SOUL.md, etc.) into a tmp dir instead of accumulating debris in
    the committed tree. Defaults to the package-local ``roles/`` directory.
    """
    override = os.environ.get("ICDEV_ACE_ROLES_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "roles"

# Patterns that are too routine to be worth a warning fact.
_TRIVIAL_PATTERNS: frozenset[str] = frozenset({"success_first_try"})


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _ensure_tables(conn) -> None:
    """Lazy-init NOVA tables on first use."""
    try:
        from tools.nova.db.init_db import init_nova_tables
        init_nova_tables(conn)
    except Exception as exc:
        logger.debug("[soul] lazy init failed (tables may already exist): %s", exc)


def _soul_path(role: str) -> Path:
    """Resolve the role's SOUL.md identity file inside the ACE roles directory."""
    return _roles_dir() / role / "SOUL.md"


def _prune_old_facts(conn, role: str) -> None:
    """Keep at most MAX_MEMORY_FACTS rows per role, deleting oldest first."""
    try:
        backend = getattr(conn, "_backend", "")
        is_pg = str(backend).lower().startswith(("postgre", "pg"))
        if is_pg:
            conn.execute(
                """
                DELETE FROM ace_coworker_memory
                WHERE id IN (
                    SELECT id FROM ace_coworker_memory
                    WHERE role_id = %s
                    ORDER BY created_at DESC, ctid DESC
                    OFFSET %s
                )
                """,
                (role, MAX_MEMORY_FACTS),
            )
        else:
            conn.execute(
                """
                DELETE FROM ace_coworker_memory
                WHERE id IN (
                    SELECT id FROM ace_coworker_memory
                    WHERE role_id = %s
                    ORDER BY created_at DESC, rowid DESC
                    LIMIT -1 OFFSET %s
                )
                """,
                (role, MAX_MEMORY_FACTS),
            )
    except Exception as exc:
        logger.debug("[soul] prune failed: %s", exc)


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────


def record_learning(
    role: str,
    content: str,
    fact_type: str = "observation",
    confidence: float = 0.8,
    source_task_id: str = "",
) -> str:
    """Persist a learned fact for a role. Returns the generated fact_id."""
    fact_id = f"fact-{uuid.uuid4().hex[:16]}"
    try:
        conn = _conn()
        _ensure_tables(conn)
        conn.execute(
            """
            INSERT INTO ace_coworker_memory
                (id, role_id, fact_type, content, confidence, source_task_id)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (fact_id, role, fact_type, content, confidence, source_task_id),
        )
        _prune_old_facts(conn, role)
        conn.commit()
        logger.debug("[soul] recorded fact %s for %s", fact_id, role)
    except Exception as exc:
        logger.warning("[soul] record_learning failed: %s", exc)
    return fact_id


def get_recent_learnings(role: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return recent learned facts for a role, newest first."""
    try:
        conn = _conn()
        _ensure_tables(conn)
        rows = conn.execute(
            """
            SELECT id, fact_type, content, confidence, source_task_id, created_at
              FROM ace_coworker_memory
             WHERE role_id = %s
             ORDER BY created_at DESC, rowid DESC
             LIMIT %s
            """,
            (role, limit),
        ).fetchall()
        cols = ["id", "fact_type", "content", "confidence", "source_task_id", "created_at"]
        return [
            dict(zip(cols, row)) if not isinstance(row, dict) else dict(row)
            for row in rows
        ]
    except Exception as exc:
        logger.debug("[soul] get_recent_learnings failed: %s", exc)
        return []


def extract_learnings_from_trace(
    role: str,
    trace: dict[str, Any],
    task_output: str = "",
    source_task_id: str = "",
) -> list[str]:
    """
    Derive one or more learned facts from an execution trace and store them.

    Returns a list of recorded fact_ids.  This deterministic path does not
    call an LLM; enrichment is performed by downstream NOVA reflexes.
    """
    trace = trace or {}
    task_type = trace.get("task_type") or "task"
    outcome = trace.get("outcome") or "unknown"
    lesson_pattern = trace.get("lesson_pattern") or ""

    fact_ids: list[str] = []

    if outcome == "success":
        content = f"Successful '{task_type}' execution"
        if lesson_pattern and lesson_pattern not in _TRIVIAL_PATTERNS:
            content += f" following pattern '{lesson_pattern}'"
        if task_output:
            content += f"; output: {task_output[:500]}"
        fact_ids.append(
            record_learning(
                role,
                content,
                fact_type="success",
                confidence=0.8,
                source_task_id=source_task_id,
            )
        )
    elif outcome == "failure":
        content = f"Failure during '{task_type}'"
        if lesson_pattern and lesson_pattern not in _TRIVIAL_PATTERNS:
            content += f" matching pattern '{lesson_pattern}'"
            fact_ids.append(
                record_learning(
                    role,
                    content,
                    fact_type="warning",
                    confidence=0.7,
                    source_task_id=source_task_id,
                )
            )
        else:
            fact_ids.append(
                record_learning(
                    role,
                    content,
                    fact_type="failure",
                    confidence=0.7,
                    source_task_id=source_task_id,
                )
            )
    else:
        fact_ids.append(
            record_learning(
                role,
                f"Trace outcome '{outcome}' for task type '{task_type}'",
                fact_type="observation",
                confidence=0.6,
                source_task_id=source_task_id,
            )
        )

    return fact_ids


def build_identity_preamble(role: str) -> str:
    """
    Build a markdown identity preamble for a coworker role.

    Combines the role's SOUL.md identity file with a canonical capability
    scope and recent DB-learned facts.  Returns an empty string for unknown
    roles that have no SOUL.md and no accumulated knowledge.
    """
    soul_path = _soul_path(role)
    facts = get_recent_learnings(role, limit=10)

    if not soul_path.exists() and not facts:
        return ""

    lines: list[str] = []

    if soul_path.exists():
        identity_text = soul_path.read_text(encoding="utf-8").strip()
    else:
        identity_text = ""

    lines.append("## Identity")
    if identity_text:
        lines.append(identity_text)
    else:
        lines.append(f"You are the **{role}** coworker in the ICDEV platform.")

    lines.append("")
    lines.append("## Capability Scope")
    lines.append(
        "You are responsible for maintaining the coworker's learned memory, "
        "recording facts from execution traces, and composing identity preambles "
        "that blend role values with accumulated knowledge."
    )

    if facts:
        lines.append("")
        lines.append("## Recent Learnings")
        for fact in facts:
            lines.append(f"- {fact['content']}")

    return "\n".join(lines)


def inject_user_profile_context(preamble: str, user_id: str, tenant_id: str = "default") -> str:
    """Append the launching user's Second Brain world-model context to a SOUL preamble.

    cnr-me-04(f): coworker_thread already calls this to enrich the identity
    preamble with the operator's profile, but the function did not exist —
    making `build_world_model_context`'s advertised "ACE SOUL injection" a dead
    claim. This wires it: when the Second Brain profile is complete, a compact
    "## Operator Context" section (name, role, objectives, challenges, comm
    style) is appended so the coworker tailors its work to the person it serves.

    Best-effort and additive: returns *preamble* unchanged if the Second Brain
    feature is absent, the profile is incomplete, or anything fails.
    """
    if not user_id or user_id in ("default", "system"):
        return preamble
    try:
        from icdev.tools.second_brain.profile import build_world_model_context
    except Exception:
        try:
            from tools.second_brain.profile import build_world_model_context
        except Exception:
            return preamble
    try:
        ctx = build_world_model_context(user_id, tenant_id)
    except Exception:
        ctx = None
    if not ctx:
        return preamble

    lines: list[str] = []
    lines.append("## Operator Context")
    lines.append(
        f"You are assisting **{ctx.get('name', user_id)}**"
        + (f", {ctx['title']}" if ctx.get("title") else "")
        + (f" at {ctx['org_name']}" if ctx.get("org_name") else "")
        + "."
    )
    if ctx.get("comm_style_label"):
        lines.append(f"Preferred communication style: {ctx['comm_style_label']}.")
    if ctx.get("objectives"):
        lines.append("Their current objectives:")
        for obj in ctx["objectives"][:5]:
            lines.append(f"- {obj}")
    if ctx.get("challenge_keys"):
        readable = ", ".join(k.replace("_", " ") for k in ctx["challenge_keys"][:5] if k)
        if readable:
            lines.append(f"Known challenges to be mindful of: {readable}.")

    section = "\n".join(lines)
    return f"{preamble}\n\n{section}" if preamble else section
