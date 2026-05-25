# CUI // SP-CTI
"""IPB Engine — Intelligence Preparation of the Battlespace (FM 2-01.3).

Four-step doctrinal workflow:
  Step 1 — Define the Battlespace Environment
  Step 2 — Describe the Battlespace Effects
  Step 3 — Evaluate the Threat
  Step 4 — Determine Threat Courses of Action

Each step produces a template output (Situation Template, Event Template,
Decision Support Template). Steps are persisted in sg_ipb_steps and can
be LLM-synthesized on demand.
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import uuid
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.strategos.ipb")

STEP_NAMES = [
    "Define Battlespace Environment",
    "Describe Battlespace Effects",
    "Evaluate the Threat",
    "Determine Threat COAs",
]

STEP_PROMPTS = [
    (
        "You are a military intelligence analyst conducting IPB Step 1 (Define Battlespace Environment). "
        "Theater: {theater}. Scenario: {scenario}.\n"
        "Write a concise assessment covering: (1) Area of Interest boundaries, "
        "(2) key terrain features and their military significance, "
        "(3) infrastructure nodes, (4) population centers and civil considerations. "
        "Use military intelligence prose. 3-5 sentences per sub-topic."
    ),
    (
        "You are a military intelligence analyst conducting IPB Step 2 (Describe Battlespace Effects). "
        "Theater: {theater}. Scenario: {scenario}.\n"
        "Write a concise assessment of how terrain, weather, and infrastructure affect operations: "
        "(1) mobility corridors and obstacles, (2) key terrain and observation/fields-of-fire, "
        "(3) cover and concealment, (4) weather effects on operations and ISR. "
        "Use military intelligence prose. 3-5 sentences per sub-topic."
    ),
    (
        "You are a military intelligence analyst conducting IPB Step 3 (Evaluate the Threat). "
        "Theater: {theater}. Scenario: {scenario}.\n"
        "Write a threat evaluation covering: (1) adversary order of battle and capabilities, "
        "(2) adversary doctrine and likely TTPs, (3) adversary strengths and vulnerabilities, "
        "(4) adversary Center of Gravity and critical requirements. "
        "Use military intelligence prose. 3-5 sentences per sub-topic."
    ),
    (
        "You are a military intelligence analyst conducting IPB Step 4 (Determine Threat COAs). "
        "Theater: {theater}. Scenario: {scenario}.\n"
        "Develop adversary COAs: (1) Most Likely COA (MLCOA) with rationale, "
        "(2) Most Dangerous COA (MDCOA) with rationale, "
        "(3) key decision points and named areas of interest, "
        "(4) indicators and warnings to confirm/deny each COA. "
        "Use military intelligence prose. 3-5 sentences per sub-topic."
    ),
]


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_session(theater: str, scenario: str = "") -> dict[str, Any]:
    """Create a new IPB session with 4 pending steps."""
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        session_id = str(uuid.uuid4())
        now = _now_utc()
        conn.execute(
            "INSERT INTO sg_ipb_sessions (id, theater, scenario, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'active', ?, ?)",
            (session_id, theater, scenario, now, now),
        )
        for i, name in enumerate(STEP_NAMES, start=1):
            conn.execute(
                "INSERT INTO sg_ipb_steps "
                "(id, session_id, step_num, step_name, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (str(uuid.uuid4()), session_id, i, name, now, now),
            )
        conn.commit()
        return {"session_id": session_id, "theater": theater, "scenario": scenario}
    finally:
        conn.close()


def synthesize_step(session_id: str, step_num: int) -> dict[str, Any]:
    """LLM-synthesize one IPB step and save the output."""
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        sess = conn.execute(
            f"SELECT theater, scenario FROM sg_ipb_sessions WHERE id = {ph}",
            (session_id,),
        ).fetchone()
        if not sess:
            return {"error": "session not found"}
        theater = sess[0] if not isinstance(sess, dict) else sess["theater"]
        scenario = sess[1] if not isinstance(sess, dict) else sess["scenario"]

        step = conn.execute(
            f"SELECT id FROM sg_ipb_steps "  # nosec B608
            f"WHERE session_id = {ph} AND step_num = {ph}",
            (session_id, step_num),
        ).fetchone()
        if not step:
            return {"error": "step not found"}
        step_id = step[0] if not isinstance(step, dict) else step["id"]

        prompt = STEP_PROMPTS[step_num - 1].format(
            theater=theater, scenario=scenario or "unspecified"
        )

        content = _llm_synthesize(prompt)
        now = _now_utc()
        conn.execute(
            f"UPDATE sg_ipb_steps SET content = {ph}, status = 'complete', "  # nosec B608
            f"updated_at = {ph} WHERE id = {ph}",
            (content, now, step_id),
        )
        conn.commit()
        return {"step_num": step_num, "content": content, "status": "complete"}
    finally:
        conn.close()


def _llm_synthesize(prompt: str) -> str:
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        router = LLMRouter()
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=(
                "You are a senior military intelligence analyst. "
                "Produce structured, classified IPB products in formal J2 prose."
            ),
            max_tokens=1024,
            classification="CUI // SP-CTI",
        )
        resp = router.invoke("chat_response", req)
        return (resp.content or "").strip()
    except Exception as exc:
        logger.warning("IPB LLM synthesis failed: %s", exc)
        return "[Synthesis unavailable — review source data manually.]"


def get_session(session_id: str) -> dict | None:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT id, theater, scenario, status, created_at FROM sg_ipb_sessions "  # nosec B608
            f"WHERE id = {ph}",
            (session_id,),
        ).fetchone()
        if not row:
            return None
        sess = dict(zip(("id", "theater", "scenario", "status", "created_at"), row))
        steps_rows = conn.execute(
            f"SELECT step_num, step_name, status, content "  # nosec B608
            f"FROM sg_ipb_steps WHERE session_id = {ph} ORDER BY step_num",
            (session_id,),
        ).fetchall()
        sess["steps"] = [
            dict(zip(("step_num", "step_name", "status", "content"), r))
            for r in steps_rows
        ]
        return sess
    finally:
        conn.close()


def list_sessions(limit: int = 20) -> list[dict]:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, theater, scenario, status, created_at "  # nosec B608
            "FROM sg_ipb_sessions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        cols = ("id", "theater", "scenario", "status", "created_at")
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def delete_session(session_id: str) -> bool:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(f"DELETE FROM sg_ipb_steps WHERE session_id = {ph}", (session_id,))  # nosec B608
        conn.execute(f"DELETE FROM sg_ipb_sessions WHERE id = {ph}", (session_id,))  # nosec B608
        conn.commit()
        return True
    except Exception as exc:
        logger.error("delete_session failed: %s", exc)
        return False
    finally:
        conn.close()
