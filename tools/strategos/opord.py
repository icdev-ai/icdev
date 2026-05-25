# CUI // SP-CTI
"""OPORD Generator — Five-Paragraph Operations Order (FM 5-0 / JP 5-0).

Paragraphs:
  1 — Situation (enemy, friendly, attachments)
  2 — Mission (who, what, when, where, why)
  3 — Execution (commander's intent, concept, tasks, coordinating instructions)
  4 — Sustainment (logistics, medical, personnel)
  5 — Command & Signal (command succession, comms, reports)
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.strategos.opord")

PARA_PROMPTS = [
    (
        "You are a staff officer drafting OPORD Paragraph 1 (Situation) for {theater}. "
        "Operation: {title}. Scenario context: {scenario}.\n"
        "Write a formal Situation paragraph covering: (a) Enemy Forces — assessed strength, "
        "disposition, capabilities, and most likely COA; (b) Friendly Forces — higher HQ mission, "
        "adjacent units, supporting assets; (c) Attachments and Detachments. "
        "Use military OPORD format. Be concise and decisive."
    ),
    (
        "You are a staff officer drafting OPORD Paragraph 2 (Mission) for {theater}. "
        "Operation: {title}. Scenario context: {scenario}.\n"
        "Write a single clear mission statement answering Who, What (task), When, Where, and Why (purpose). "
        "Follow Army/Joint OPORD format. One paragraph, 2-4 sentences."
    ),
    (
        "You are a staff officer drafting OPORD Paragraph 3 (Execution) for {theater}. "
        "Operation: {title}. Scenario context: {scenario}.\n"
        "Write the Execution paragraph covering: (a) Commander's Intent — end state, key tasks, "
        "acceptable risk; (b) Concept of Operations — phasing, main effort, supporting efforts; "
        "(c) Tasks to Subordinate Units — key assignments; (d) Coordinating Instructions — "
        "ROE summary, timeline, phase lines. Use formal military prose."
    ),
    (
        "You are a staff officer drafting OPORD Paragraph 4 (Sustainment) for {theater}. "
        "Operation: {title}. Scenario context: {scenario}.\n"
        "Write the Sustainment paragraph covering: (a) Logistics — supply classes, transportation, "
        "maintenance priorities; (b) Personnel Services — casualty reporting, replacements; "
        "(c) Army Health System — medical support, MEDEVAC, treatment facilities. "
        "Use formal military logistics prose."
    ),
    (
        "You are a staff officer drafting OPORD Paragraph 5 (Command and Signal) for {theater}. "
        "Operation: {title}. Scenario context: {scenario}.\n"
        "Write the Command and Signal paragraph covering: (a) Command — succession of command, "
        "location of command posts, liaison requirements; (b) Control — decision points, "
        "battle rhythm, CCIR reporting; (c) Signal — primary/alternate/contingency comms, "
        "COMSEC instructions. Use formal military prose."
    ),
]

PARA_HEADINGS = [
    "1. SITUATION",
    "2. MISSION",
    "3. EXECUTION",
    "4. SUSTAINMENT",
    "5. COMMAND AND SIGNAL",
]

PARA_FIELDS = ["situation", "mission", "execution", "sustainment", "command_signal"]


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_opord(
    title: str,
    theater: str = "unspecified",
    scenario: str = "",
    task_org: str = "",
    created_by: str = "analyst",
) -> dict[str, Any]:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        opord_id = str(uuid.uuid4())
        now = _now_utc()
        conn.execute(
            "INSERT INTO sg_opords "
            "(id, title, theater, task_org, status, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'draft', ?, ?, ?)",
            (opord_id, title, theater, task_org or None, created_by, now, now),
        )
        conn.commit()
        return {"opord_id": opord_id, "title": title, "theater": theater, "status": "draft"}
    finally:
        conn.close()


def synthesize_paragraph(opord_id: str, para_num: int, scenario: str = "") -> dict[str, Any]:
    """LLM-synthesize one OPORD paragraph (1-5) and persist it."""
    if para_num < 1 or para_num > 5:
        return {"error": "para_num must be 1-5"}
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT title, theater FROM sg_opords WHERE id = {ph}", (opord_id,)  # nosec B608
        ).fetchone()
        if not row:
            return {"error": "OPORD not found"}
        title = row[0] if not isinstance(row, dict) else row["title"]
        theater = row[1] if not isinstance(row, dict) else row["theater"]

        prompt = PARA_PROMPTS[para_num - 1].format(
            title=title, theater=theater,
            scenario=scenario or "General operations"
        )
        content = _llm_call(prompt)
        field = PARA_FIELDS[para_num - 1]
        now = _now_utc()
        conn.execute(
            f"UPDATE sg_opords SET {field} = {ph}, updated_at = {ph} WHERE id = {ph}",  # nosec B608
            (content, now, opord_id),
        )
        conn.commit()
        return {"para_num": para_num, "heading": PARA_HEADINGS[para_num - 1], "content": content}
    finally:
        conn.close()


def synthesize_all(opord_id: str, scenario: str = "") -> dict[str, Any]:
    """Synthesize all 5 paragraphs sequentially."""
    results = []
    for i in range(1, 6):
        r = synthesize_paragraph(opord_id, i, scenario=scenario)
        if "error" in r:
            return r
        results.append(r)
    return {"opord_id": opord_id, "paragraphs": results}


def _llm_call(prompt: str) -> str:
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        router = LLMRouter()
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=(
                "You are a senior military staff officer. "
                "Produce formal, unclassified-style OPORD paragraphs "
                "in standard US Army/Joint format."
            ),
            max_tokens=800,
            classification="CUI // SP-CTI",
        )
        resp = router.invoke("chat_response", req)
        return (resp.content or "").strip()
    except Exception as exc:
        logger.warning("OPORD LLM synthesis failed: %s", exc)
        return "[Synthesis unavailable — complete manually.]"


def get_opord(opord_id: str) -> dict | None:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT id, title, theater, task_org, situation, mission, execution, "  # nosec B608
            f"sustainment, command_signal, status, created_by, created_at, updated_at "
            f"FROM sg_opords WHERE id = {ph}",
            (opord_id,),
        ).fetchone()
        if not row:
            return None
        cols = ("id", "title", "theater", "task_org", "situation", "mission",
                "execution", "sustainment", "command_signal", "status",
                "created_by", "created_at", "updated_at")
        return dict(zip(cols, row))
    finally:
        conn.close()


def list_opords(theater: str = "", limit: int = 30) -> list[dict]:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        if theater:
            rows = conn.execute(
                f"SELECT id, title, theater, status, created_by, created_at "  # nosec B608
                f"FROM sg_opords WHERE theater = {ph} ORDER BY created_at DESC LIMIT {ph}",
                (theater, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, theater, status, created_by, created_at "  # nosec B608
                "FROM sg_opords ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        cols = ("id", "title", "theater", "status", "created_by", "created_at")
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def approve_opord(opord_id: str, approved_by: str = "commander") -> bool:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE sg_opords SET status = 'approved', approved_by = {ph}, "  # nosec B608
            f"updated_at = {ph} WHERE id = {ph}",
            (approved_by, _now_utc(), opord_id),
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.error("approve_opord failed: %s", exc)
        return False
    finally:
        conn.close()


def delete_opord(opord_id: str) -> bool:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(f"DELETE FROM sg_opords WHERE id = {ph}", (opord_id,))  # nosec B608
        conn.commit()
        return True
    except Exception as exc:
        logger.error("delete_opord failed: %s", exc)
        return False
    finally:
        conn.close()
