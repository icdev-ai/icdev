# CUI // SP-CTI
"""TTX Engine — AI scoring: receipt validation + LLM judge + time bonus."""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_connection
from .constants import TIME_BONUS_BRACKETS

log = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Step 1: Receipt validation
# ---------------------------------------------------------------------------

def validate_receipts(
    team_id: int,
    session_id: int,
    receipts: list[dict],
    bonus_per_call: int,
    max_bonus: int,
) -> tuple[int, int]:
    """Validate AI tool receipts against ttx_api_log.

    Returns (receipt_pts, valid_count).
    """
    conn = get_connection()
    valid_count = 0
    for r in receipts:
        call_id = r.get("call_id", "")
        if not call_id:
            continue
        row = conn.execute(
            """SELECT 1 FROM ttx_api_log
               WHERE team_id = %s AND session_id = %s AND call_id = %s""",
            (team_id, session_id, call_id),
        ).fetchone()
        if row:
            valid_count += 1

    receipt_pts = min(valid_count * bonus_per_call, max_bonus)
    return receipt_pts, valid_count


# ---------------------------------------------------------------------------
# Step 2: LLM judge
# ---------------------------------------------------------------------------

def judge_response(
    inject_body: str,
    team_response: str,
    rubric: dict,
    receipt_evidence: str = "",
) -> dict[str, Any]:
    """Score a team response using the LLM judge + rubric.

    Returns {dimension_scores, total, rationale, confidence}.
    Falls back to rubric average (50) if LLM unavailable.
    """
    try:
        from tools.llm.router import LLMRouter
        router = LLMRouter()
        if not router.has_any_llm():
            raise RuntimeError("no_llm")
    except Exception:
        return _fallback_score(rubric)

    dims = rubric.get("dimensions", [])
    if not dims:
        return {"dimension_scores": {}, "total": 50, "rationale": "No rubric", "confidence": 0.5}

    dim_block = "\n".join(
        f"- {d['id']} (weight {d['weight']}): {d['prompt']}" for d in dims
    )
    system_prompt = (
        "You are an objective tabletop exercise judge. Score the team response "
        "strictly on the rubric dimensions. Return JSON only — no prose.\n"
        "Format: {\"scores\": {\"<dim_id>\": <0-10>, ...}, \"rationale\": \"...\", \"confidence\": <0.0-1.0>}"
    )
    user_msg = (
        f"INJECT:\n{inject_body}\n\n"
        f"TEAM RESPONSE:\n{team_response}\n\n"
        f"AI TOOL EVIDENCE:\n{receipt_evidence or 'None provided'}\n\n"
        f"RUBRIC DIMENSIONS:\n{dim_block}\n\n"
        "Score each dimension 0-10. Return JSON only."
    )

    try:
        from tools.llm.router import LLMRequest
        req = LLMRequest(
            messages=[{"role": "user", "content": user_msg}],
            system_prompt=system_prompt,
            max_tokens=512,
        )
        resp = router.invoke("ttx_judge", req)
        raw = resp.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
    except Exception as exc:
        log.warning("LLM judge parse failed: %s", exc)
        return _fallback_score(rubric)

    scores = parsed.get("scores", {})
    total = _weighted_total(scores, dims)
    return {
        "dimension_scores": scores,
        "total": total,
        "rationale": parsed.get("rationale", ""),
        "confidence": parsed.get("confidence", 0.7),
    }


def _weighted_total(scores: dict, dims: list[dict]) -> int:
    total_weight = sum(d["weight"] for d in dims)
    if total_weight == 0:
        return 50
    weighted = sum(
        scores.get(d["id"], 5) * d["weight"] for d in dims
    )
    return round((weighted / total_weight) * 10)  # scale 0-10 → 0-100


def _fallback_score(rubric: dict) -> dict[str, Any]:
    dims = rubric.get("dimensions", [])
    scores = {d["id"]: 5 for d in dims}
    return {
        "dimension_scores": scores,
        "total": 50,
        "rationale": "LLM unavailable — default score assigned",
        "confidence": 0.3,
    }


# ---------------------------------------------------------------------------
# Step 3: Time bonus
# ---------------------------------------------------------------------------

def compute_time_bonus(time_taken_s: float | None) -> int:
    if time_taken_s is None:
        return 0
    for threshold, bonus in TIME_BONUS_BRACKETS:
        if time_taken_s <= threshold:
            return bonus
    return 0


# ---------------------------------------------------------------------------
# Orchestrate: score a response end-to-end and persist
# ---------------------------------------------------------------------------

def score_aadc_design(design_id: str, required_checks: list[str]) -> dict[str, Any]:
    """Score an AADC design challenge response by running assess_design().

    Returns {judge_pts, rationale, check_results} where judge_pts is 0-100
    based on the fraction of required_checks that pass.
    Falls back to 0 if AADC is unavailable.
    """
    if not design_id:
        return {"judge_pts": 0, "rationale": "No design_id provided", "check_results": []}
    try:
        from tools.agentic_ai_canvas.db.init_db import init_db as _aadc_init
        from tools.db.storage import get_connection as _gc
        _aadc_init()
        conn = _gc()
        row = conn.execute(
            "SELECT graph_json, metadata_json FROM aadc_designs WHERE design_id = %s",
            (design_id,),
        ).fetchone()
        if not row:
            return {"judge_pts": 0, "rationale": f"Design {design_id} not found", "check_results": []}
        from tools.agentic_ai_canvas.agentic_engine import assess_design
        result = assess_design(row["graph_json"], json.loads(row["metadata_json"] or "{}"))
        all_checks = result.get("checks", [])
        if required_checks:
            relevant = [c for c in all_checks if c.get("id") in required_checks]
        else:
            relevant = all_checks
        passing = [c for c in relevant if c.get("passed")]
        pct = len(passing) / max(len(relevant), 1)
        judge_pts = round(pct * 100)
        check_summary = [{"id": c["id"], "title": c.get("title", ""), "passed": c.get("passed")}
                         for c in relevant]
        rationale = (
            f"AADC assessment: {len(passing)}/{len(relevant)} required checks pass "
            f"({judge_pts}/100). "
            + (f"Still failing: {[c['id'] for c in relevant if not c.get('passed')]}"
               if judge_pts < 100 else "All checks green.")
        )
        return {"judge_pts": judge_pts, "rationale": rationale, "check_results": check_summary}
    except Exception as exc:
        log.warning("AADC design scorer failed: %s", exc)
        return {"judge_pts": 0, "rationale": f"AADC scorer error: {exc}", "check_results": []}


def score_response(
    response_id: int,
    team_id: int,
    inject_id: str,
    session_id: int,
    response_text: str,
    inject_body: str,
    receipts: list[dict],
    rubric: dict,
    bonus_per_call: int,
    max_receipt_bonus: int,
    time_taken_s: float | None,
    time_bonus_enabled: bool = True,
    inject_type: str = "",
) -> dict[str, Any]:
    """Run full scoring pipeline and write result to ttx_scores."""
    receipt_pts, receipt_count = validate_receipts(
        team_id, session_id, receipts, bonus_per_call, max_receipt_bonus
    )
    receipt_evidence = "\n".join(
        f"[{r.get('tool', '?')}] call_id={r.get('call_id', '?')}" for r in receipts
    )

    # AADC design challenge: replace LLM judge with automated compliance scoring
    if inject_type == "aadc_design_challenge":
        try:
            payload = json.loads(response_text) if response_text.strip().startswith("{") else {}
        except Exception:
            payload = {}
        design_id = payload.get("design_id", "")
        required_checks = rubric.get("required_checks", [])
        aadc_result = score_aadc_design(design_id, required_checks)
        judge_result = {
            "dimension_scores": {},
            "total": aadc_result["judge_pts"],
            "rationale": aadc_result["rationale"],
            "confidence": 1.0,
            "check_results": aadc_result["check_results"],
        }
    else:
        judge_result = judge_response(inject_body, response_text, rubric, receipt_evidence)

    judge_pts = judge_result["total"]
    time_bonus = compute_time_bonus(time_taken_s) if time_bonus_enabled else 0
    total_pts = receipt_pts + judge_pts + time_bonus

    conn = get_connection()
    conn.execute(
        """INSERT INTO ttx_scores
           (response_id, team_id, inject_id,
            receipt_pts, receipt_count, judge_pts, time_bonus_pts, total_pts,
            judge_rationale_json, judged_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            response_id, team_id, inject_id,
            receipt_pts, receipt_count, judge_pts, time_bonus, total_pts,
            json.dumps(judge_result), _now(),
        ),
    )
    conn.commit()

    # Update team aggregate
    conn.execute(
        "UPDATE ttx_teams SET total_score = total_score + %s WHERE team_id = %s",
        (total_pts, team_id),
    )
    conn.commit()

    out: dict[str, Any] = {
        "receipt_pts": receipt_pts,
        "receipt_count": receipt_count,
        "judge_pts": judge_pts,
        "time_bonus_pts": time_bonus,
        "total_pts": total_pts,
        "rationale": judge_result.get("rationale", ""),
    }
    if inject_type == "aadc_design_challenge":
        out["aadc_score"] = judge_pts
    return out
