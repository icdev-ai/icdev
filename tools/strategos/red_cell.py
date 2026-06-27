# CUI // SP-CTI
"""Red Cell — MLCOA / MDCOA Adversary Analysis (JP 5-0 / MDMP COA Analysis).

Red Cell produces the adversary perspective during wargaming:
  - MLCOA: Most Likely Course of Action
  - MDCOA: Most Dangerous Course of Action

Each COA includes rationale, probability estimate, indicators to watch,
and wargaming notes (friendly actions that counter or trigger the COA).
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import uuid
from datetime import datetime, timezone
from typing import Any

logger = get_logger("icdev.strategos.red_cell")

MLCOA_PROMPT = (
    "You are the Red Cell analyst (adversary perspective) conducting MDMP COA Analysis "
    "for theater: {theater}. Scenario: {scenario}.\n"
    "Develop the adversary MOST LIKELY COURSE OF ACTION (MLCOA):\n"
    "1. COA Title and brief description (1-2 sentences)\n"
    "2. Rationale — why this is most likely (adversary capabilities, historical behavior, "
    "current indicators, political constraints)\n"
    "3. Probability estimate (expressed as Low/Medium/High with reasoning)\n"
    "4. Key Indicators — what observable actions confirm this COA is unfolding (4-6 bullets)\n"
    "5. Wargaming Notes — friendly COA branches/sequels this triggers\n"
    "Write in formal J2 analytical prose."
)

MDCOA_PROMPT = (
    "You are the Red Cell analyst (adversary perspective) conducting MDMP COA Analysis "
    "for theater: {theater}. Scenario: {scenario}.\n"
    "Develop the adversary MOST DANGEROUS COURSE OF ACTION (MDCOA):\n"
    "1. COA Title and brief description (1-2 sentences)\n"
    "2. Rationale — why this is the greatest threat to friendly forces even if less likely "
    "(adversary capabilities that could be employed, worst-case scenario)\n"
    "3. Probability estimate (expressed as Low/Medium/High with reasoning)\n"
    "4. Key Indicators — what observable actions would warn that MDCOA is being prepared (4-6 bullets)\n"
    "5. Wargaming Notes — minimum friendly force requirements to defeat this COA\n"
    "Write in formal J2 analytical prose."
)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _llm_call(prompt: str) -> str:
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        router = LLMRouter()
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=(
                "You are a senior intelligence analyst conducting Red Cell analysis. "
                "Think and write from the adversary's perspective. "
                "Be analytically rigorous, cite observable indicators, and avoid mirror-imaging."
            ),
            max_tokens=900,
            classification="CUI // SP-CTI",
        )
        resp = router.invoke("chat_response", req)
        return (resp.content or "").strip()
    except Exception as exc:
        logger.warning("Red Cell LLM call failed: %s", exc)
        return "[Synthesis unavailable — complete manually.]"


def create_analysis(
    theater: str,
    scenario: str = "",
    created_by: str = "analyst",
) -> dict[str, Any]:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        analysis_id = str(uuid.uuid4())
        now = _now_utc()
        conn.execute(
            "INSERT INTO sg_red_cell_analyses "
            "(id, theater, scenario, created_by, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (analysis_id, theater, scenario, created_by, now, now),
        )
        conn.commit()
        return {"analysis_id": analysis_id, "theater": theater, "scenario": scenario}
    finally:
        conn.close()


def synthesize_mlcoa(analysis_id: str) -> dict[str, Any]:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT theater, scenario FROM sg_red_cell_analyses WHERE id = {ph}",  # nosec B608
            (analysis_id,),
        ).fetchone()
        if not row:
            return {"error": "analysis not found"}
        theater = row[0] if not isinstance(row, dict) else row["theater"]
        scenario = row[1] if not isinstance(row, dict) else row["scenario"]
        content = _llm_call(MLCOA_PROMPT.format(theater=theater, scenario=scenario or "unspecified"))
        now = _now_utc()
        conn.execute(
            f"UPDATE sg_red_cell_analyses SET mlcoa_rationale = {ph}, "  # nosec B608
            f"updated_at = {ph} WHERE id = {ph}",
            (content, now, analysis_id),
        )
        conn.commit()
        return {"coa_type": "MLCOA", "content": content}
    finally:
        conn.close()


def synthesize_mdcoa(analysis_id: str) -> dict[str, Any]:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT theater, scenario FROM sg_red_cell_analyses WHERE id = {ph}",  # nosec B608
            (analysis_id,),
        ).fetchone()
        if not row:
            return {"error": "analysis not found"}
        theater = row[0] if not isinstance(row, dict) else row["theater"]
        scenario = row[1] if not isinstance(row, dict) else row["scenario"]
        content = _llm_call(MDCOA_PROMPT.format(theater=theater, scenario=scenario or "unspecified"))
        now = _now_utc()
        conn.execute(
            f"UPDATE sg_red_cell_analyses SET mdcoa_rationale = {ph}, "  # nosec B608
            f"updated_at = {ph} WHERE id = {ph}",
            (content, now, analysis_id),
        )
        conn.commit()
        return {"coa_type": "MDCOA", "content": content}
    finally:
        conn.close()


def get_analysis(analysis_id: str) -> dict | None:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT id, theater, scenario, mlcoa_title, mlcoa_rationale, mlcoa_prob, "  # nosec B608
            f"mlcoa_indicators, mlcoa_wargame, mdcoa_title, mdcoa_rationale, mdcoa_prob, "
            f"mdcoa_indicators, mdcoa_wargame, analyst_notes, created_by, created_at "
            f"FROM sg_red_cell_analyses WHERE id = {ph}",
            (analysis_id,),
        ).fetchone()
        if not row:
            return None
        cols = ("id", "theater", "scenario", "mlcoa_title", "mlcoa_rationale", "mlcoa_prob",
                "mlcoa_indicators", "mlcoa_wargame", "mdcoa_title", "mdcoa_rationale",
                "mdcoa_prob", "mdcoa_indicators", "mdcoa_wargame", "analyst_notes",
                "created_by", "created_at")
        return dict(zip(cols, row))
    finally:
        conn.close()


def list_analyses(theater: str = "", limit: int = 20) -> list[dict]:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        if theater:
            rows = conn.execute(
                f"SELECT id, theater, scenario, created_by, created_at "  # nosec B608
                f"FROM sg_red_cell_analyses WHERE theater = {ph} ORDER BY created_at DESC LIMIT {ph}",
                (theater, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, theater, scenario, created_by, created_at "  # nosec B608
                "FROM sg_red_cell_analyses ORDER BY created_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        cols = ("id", "theater", "scenario", "created_by", "created_at")
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def update_analysis(analysis_id: str, fields: dict) -> bool:
    """Update mutable fields (titles, prob estimates, notes)."""
    allowed = {
        "mlcoa_title", "mlcoa_prob", "mlcoa_indicators", "mlcoa_wargame",
        "mdcoa_title", "mdcoa_prob", "mdcoa_indicators", "mdcoa_wargame",
        "analyst_notes",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        set_clause = ", ".join(f"{k} = {ph}" for k in updates)
        values = list(updates.values()) + [_now_utc(), analysis_id]
        conn.execute(
            f"UPDATE sg_red_cell_analyses SET {set_clause}, updated_at = {ph} WHERE id = {ph}",  # nosec B608
            values,
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.error("update_analysis failed: %s", exc)
        return False
    finally:
        conn.close()


def delete_analysis(analysis_id: str) -> bool:
    from tools.db.storage import get_connection, is_pg
    ph = "%s" if is_pg() else "?"
    conn = get_connection()
    try:
        conn.execute(
            f"DELETE FROM sg_red_cell_analyses WHERE id = {ph}", (analysis_id,)  # nosec B608
        )
        conn.commit()
        return True
    except Exception as exc:
        logger.error("delete_analysis failed: %s", exc)
        return False
    finally:
        conn.close()
