# CUI // SP-CTI
"""Agent loop session evaluator — rule-based and LLM-graded quality metrics.

Phase 1 (free, always-on): ``score_session()`` extracts metrics from a stored
session or a live ``AgentLoopResult``.  No LLM calls required.

Phase 2 (opt-in, costs tokens): ``grade_output_quality()`` calls the LLM router
to assess faithfulness, completeness, and reasoning quality.

Metrics include CoT/CoD reasoning coverage signals that indicate whether the
agent reasoned before acting — a leading indicator of task success.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ace.evaluator")

_DB_ENV = "ACE_DB_PATH"
_GRADING_VERSION = "1.0"


# ---------------------------------------------------------------------------
# EvalResult dataclass
# ---------------------------------------------------------------------------

@dataclass
class EvalResult:
    session_id: str

    # --- Outcome ---
    outcome: str = ""
    done: bool = False

    # --- Efficiency ---
    turns_used: int = 0
    max_iterations: int = 12
    efficiency_score: float = 0.0

    # --- Tool quality ---
    total_tool_calls: int = 0
    error_tool_calls: int = 0
    tool_error_rate: float = 0.0
    unique_tools: list = field(default_factory=list)
    tool_precision: float = 0.0

    # --- Cost ---
    total_cost_usd: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    # --- CoT/CoD reasoning ---
    reasoning_coverage: float = 0.0
    avg_reasoning_chars: float = 0.0
    has_error_recovery_reasoning: bool = False
    plan_stated: bool = False

    # --- Safety / scope ---
    scope_violations: int = 0
    trust_denials: int = 0

    # --- Phase 2 (optional LLM grade) ---
    llm_grade: Any = None

    # --- Metadata ---
    graded_at: str = ""
    grading_version: str = _GRADING_VERSION
    reasoning_style: str = ""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_conn():
    try:
        from tools.db.storage import get_canvas_connection
        return get_canvas_connection(_DB_ENV)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Message parsing helpers
# ---------------------------------------------------------------------------

def _iter_messages(session_json_str: str):
    """Yield (role, content_list) from a session JSON string."""
    try:
        msgs = json.loads(session_json_str) if isinstance(session_json_str, str) else session_json_str
    except Exception:
        return
    for msg in msgs or []:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        elif not isinstance(content, list):
            content = []
        yield role, content


def _extract_reasoning_metrics(messages_json: str) -> dict:
    """Parse session messages to extract CoT/CoD reasoning signals."""
    tool_calling_turns = 0
    turns_with_reasoning = 0
    reasoning_chars_sum = 0
    scope_violations = 0
    trust_denials = 0
    plan_stated = False

    prev_had_error = False
    has_error_recovery = False

    for role, content in _iter_messages(messages_json):
        if role == "assistant":
            text_blocks = [b for b in content if b.get("type") == "text"]
            tool_blocks = [b for b in content if b.get("type") == "tool_use"]

            if tool_blocks:
                tool_calling_turns += 1
                if text_blocks:
                    turns_with_reasoning += 1
                    chars = sum(len(b.get("text", "")) for b in text_blocks)
                    reasoning_chars_sum += chars
                    if tool_calling_turns == 1 and chars > 100:
                        plan_stated = True
                    if prev_had_error and chars > 20:
                        has_error_recovery = True

            prev_had_error = False

        elif role == "user":
            for block in content:
                if block.get("type") == "tool_result" and block.get("is_error"):
                    prev_had_error = True
                    err_content = block.get("content", "")
                    if isinstance(err_content, list):
                        err_text = " ".join(
                            b.get("text", "") for b in err_content if b.get("type") == "text"
                        )
                    else:
                        err_text = str(err_content)
                    if "ScopeViolation" in err_text:
                        scope_violations += 1
                    if "TrustKernel" in err_text or "trust_tier" in err_text.lower():
                        trust_denials += 1

    coverage = (turns_with_reasoning / tool_calling_turns) if tool_calling_turns else 0.0
    avg_chars = (reasoning_chars_sum / tool_calling_turns) if tool_calling_turns else 0.0

    return {
        "reasoning_coverage": round(coverage, 3),
        "avg_reasoning_chars": round(avg_chars, 1),
        "has_error_recovery_reasoning": has_error_recovery,
        "plan_stated": plan_stated,
        "scope_violations": scope_violations,
        "trust_denials": trust_denials,
    }


def _extract_tool_metrics(tool_call_log_json: str) -> dict:
    """Parse tool_call_log (list of {name, error, result}) for quality metrics."""
    try:
        log = json.loads(tool_call_log_json) if isinstance(tool_call_log_json, str) else tool_call_log_json
    except Exception:
        log = []

    total = len(log)
    errors = sum(1 for e in log if e.get("error") is not None)
    unique = list(dict.fromkeys(e.get("name", "") for e in log))

    error_rate = round(errors / total, 3) if total else 0.0
    precision = round(len(unique) / total, 3) if total else 0.0

    return {
        "total_tool_calls": total,
        "error_tool_calls": errors,
        "tool_error_rate": error_rate,
        "unique_tools": unique,
        "tool_precision": precision,
    }


def _extract_tool_metrics_from_messages(session_json_str: str) -> dict:
    """Extract tool call metrics by parsing tool_use + tool_result blocks."""
    tool_names = []
    error_count = 0

    for role, content in _iter_messages(session_json_str):
        if role == "assistant":
            for block in content:
                if block.get("type") == "tool_use":
                    tool_names.append(block.get("name", ""))
        elif role == "user":
            for block in content:
                if block.get("type") == "tool_result" and block.get("is_error"):
                    error_count += 1

    total = len(tool_names)
    unique = list(dict.fromkeys(tool_names))
    error_rate = round(error_count / total, 3) if total else 0.0
    precision = round(len(unique) / total, 3) if total else 0.0

    return {
        "total_tool_calls": total,
        "error_tool_calls": error_count,
        "tool_error_rate": error_rate,
        "unique_tools": unique,
        "tool_precision": precision,
    }


# ---------------------------------------------------------------------------
# Phase 1: score_session
# ---------------------------------------------------------------------------

def score_session(
    session_id_or_result,
    *,
    max_iterations: int = 12,
    reasoning_style: str = "",
) -> EvalResult:
    """Score an agent loop session and return a structured EvalResult.

    Args:
        session_id_or_result: UUID string (looks up from DB) or an
            ``AgentLoopResult`` object (scores in-memory, no DB access).
        max_iterations: Max turns the loop was configured with.
        reasoning_style: "cod" | "cot" | "none" | "" — affects CoD grading.
    """
    try:
        from icdev.tools.llm.agent_loop import AgentLoopResult
        if isinstance(session_id_or_result, AgentLoopResult):
            return _score_from_result(session_id_or_result, max_iterations=max_iterations,
                                      reasoning_style=reasoning_style)
    except ImportError:
        pass

    session_id = str(session_id_or_result)
    conn = _get_conn()
    if conn is None:
        return EvalResult(
            session_id=session_id,
            outcome="error_db_unavailable",
            graded_at=datetime.now(timezone.utc).isoformat(),
        )

    try:
        row = conn.execute(
            "SELECT coworker_id, turns, total_input_tokens, total_output_tokens, "
            "total_cost_usd, result_subtype, done, session_json "
            "FROM agent_loop_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return EvalResult(session_id=session_id, outcome="not_found",
                          graded_at=datetime.now(timezone.utc).isoformat())

    turns = int(row[1] or 0)
    tok_in = int(row[2] or 0)
    tok_out = int(row[3] or 0)
    cost = float(row[4] or 0.0)
    subtype = row[5]
    done_val = row[6]
    session_json = row[7]

    done = bool(done_val)
    outcome = subtype or ("success" if done else "unknown")
    eff = round(max(0.0, 1.0 - (turns / max_iterations)), 3) if max_iterations else 0.0

    reasoning_metrics = _extract_reasoning_metrics(session_json or "[]")
    tool_metrics = _extract_tool_metrics_from_messages(session_json or "[]")

    return EvalResult(
        session_id=session_id,
        outcome=outcome,
        done=done,
        turns_used=turns,
        max_iterations=max_iterations,
        efficiency_score=eff,
        total_cost_usd=cost,
        total_input_tokens=tok_in,
        total_output_tokens=tok_out,
        reasoning_style=reasoning_style,
        graded_at=datetime.now(timezone.utc).isoformat(),
        **reasoning_metrics,
        **tool_metrics,
    )


def _score_from_result(result, *, max_iterations: int, reasoning_style: str) -> EvalResult:
    """Score from a live AgentLoopResult without DB access."""
    turns = getattr(result, "turns", 0) or 0
    eff = round(max(0.0, 1.0 - (turns / max_iterations)), 3) if max_iterations else 0.0
    done = bool(getattr(result, "done", False))
    subtype = getattr(result, "result_subtype", "") or ""
    outcome = subtype or ("success" if done else "unknown")

    messages_json = json.dumps(getattr(result, "messages", None) or [])
    reasoning_metrics = _extract_reasoning_metrics(messages_json)

    log = getattr(result, "tool_call_log", None) or []
    tool_metrics = _extract_tool_metrics(json.dumps(log))

    return EvalResult(
        session_id=getattr(result, "session_id", "") or "",
        outcome=outcome,
        done=done,
        turns_used=turns,
        max_iterations=max_iterations,
        efficiency_score=eff,
        total_cost_usd=float(getattr(result, "total_cost_usd", 0.0) or 0.0),
        total_input_tokens=int(getattr(result, "total_input_tokens", 0) or 0),
        total_output_tokens=int(getattr(result, "total_output_tokens", 0) or 0),
        reasoning_style=reasoning_style,
        graded_at=datetime.now(timezone.utc).isoformat(),
        **reasoning_metrics,
        **tool_metrics,
    )


# ---------------------------------------------------------------------------
# Phase 1: persist eval result
# ---------------------------------------------------------------------------

def save_eval(eval_result: EvalResult, *, overwrite: bool = True) -> str:
    """Persist an EvalResult to agent_evals. Returns the eval row id."""
    conn = _get_conn()
    if conn is None:
        raise RuntimeError("evaluator DB unavailable")

    eval_id = f"aev-{uuid.uuid4().hex[:12]}"
    try:
        if overwrite:
            try:
                conn.execute(
                    "DELETE FROM agent_evals WHERE session_id = ?",
                    (eval_result.session_id,)
                )
            except Exception:
                pass

        conn.execute(
            "INSERT INTO agent_evals ("
            "id, session_id, outcome, done, turns_used, efficiency_score, "
            "total_tool_calls, error_tool_calls, tool_error_rate, unique_tools_json, "
            "tool_precision, total_cost_usd, total_input_tokens, total_output_tokens, "
            "reasoning_coverage, avg_reasoning_chars, has_error_recovery_reasoning, "
            "plan_stated, scope_violations, trust_denials, llm_grade_json, "
            "reasoning_style, graded_at, grading_version"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                eval_id,
                eval_result.session_id,
                eval_result.outcome,
                int(eval_result.done),
                eval_result.turns_used,
                eval_result.efficiency_score,
                eval_result.total_tool_calls,
                eval_result.error_tool_calls,
                eval_result.tool_error_rate,
                json.dumps(eval_result.unique_tools),
                eval_result.tool_precision,
                eval_result.total_cost_usd,
                eval_result.total_input_tokens,
                eval_result.total_output_tokens,
                eval_result.reasoning_coverage,
                eval_result.avg_reasoning_chars,
                int(eval_result.has_error_recovery_reasoning),
                int(eval_result.plan_stated),
                eval_result.scope_violations,
                eval_result.trust_denials,
                json.dumps(eval_result.llm_grade) if eval_result.llm_grade else None,
                eval_result.reasoning_style,
                eval_result.graded_at,
                eval_result.grading_version,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return eval_id


def get_eval(session_id: str):
    """Retrieve a stored EvalResult for a session. Returns None if not found."""
    conn = _get_conn()
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT session_id, outcome, done, turns_used, efficiency_score, "
            "total_tool_calls, error_tool_calls, tool_error_rate, unique_tools_json, "
            "tool_precision, total_cost_usd, total_input_tokens, total_output_tokens, "
            "reasoning_coverage, avg_reasoning_chars, has_error_recovery_reasoning, "
            "plan_stated, scope_violations, trust_denials, llm_grade_json, "
            "reasoning_style, graded_at, grading_version "
            "FROM agent_evals WHERE session_id = ? "
            "ORDER BY graded_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    return EvalResult(
        session_id=row[0], outcome=row[1], done=bool(row[2]),
        turns_used=row[3], efficiency_score=row[4],
        total_tool_calls=row[5], error_tool_calls=row[6], tool_error_rate=row[7],
        unique_tools=json.loads(row[8] or "[]"), tool_precision=row[9],
        total_cost_usd=row[10], total_input_tokens=row[11], total_output_tokens=row[12],
        reasoning_coverage=row[13], avg_reasoning_chars=row[14],
        has_error_recovery_reasoning=bool(row[15]), plan_stated=bool(row[16]),
        scope_violations=row[17], trust_denials=row[18],
        llm_grade=json.loads(row[19]) if row[19] else None,
        reasoning_style=row[20] or "", graded_at=row[21] or "",
        grading_version=row[22] or _GRADING_VERSION,
    )


# ---------------------------------------------------------------------------
# Phase 2: LLM-as-judge (opt-in)
# ---------------------------------------------------------------------------

_JUDGE_PROMPT = """You are an expert evaluator of AI agent task performance.

Grade the following agent session on these dimensions (each 0.0-1.0):
- faithfulness: Does the final output accurately address the original user request?
- completeness: Does the output cover all key aspects of the request?
- reasoning_quality: Was the agent's step-by-step reasoning clear, relevant, and grounded? (0.0 if no reasoning present)
- cod_quality: If the reasoning style is Chain-of-Draft (concise reasoning before action), is the draft appropriately brief yet complete? Score 0.5 if unknown/N/A.
- error_adaptation: When tool errors occurred, did the agent adapt its approach or blindly retry?
- overall: Overall quality score.

Return ONLY valid JSON: {{"faithfulness": float, "completeness": float, "reasoning_quality": float, "cod_quality": float, "error_adaptation": float, "overall": float, "reasoning": "one-sentence explanation"}}

USER REQUEST:
{user_prompt}

AGENT FINAL RESPONSE:
{final_content}

REASONING STYLE: {reasoning_style}
TOOL ERROR COUNT: {error_tool_calls}
TURNS USED: {turns_used}
"""


def grade_output_quality(
    eval_result_or_session_id,
    *,
    user_prompt: str = "",
    final_content: str = "",
    llm_function: str = "code_generation",
) -> dict:
    """LLM-as-judge grading of agent output quality.

    Returns a dict with keys: faithfulness, completeness, reasoning_quality,
    cod_quality, error_adaptation, overall, reasoning.

    The result is NOT automatically persisted — caller decides whether to
    save via ``save_eval()``.
    """
    if isinstance(eval_result_or_session_id, str):
        er = get_eval(eval_result_or_session_id)
        if er is None:
            er = EvalResult(session_id=eval_result_or_session_id)
    else:
        er = eval_result_or_session_id

    if not user_prompt or not final_content:
        return {
            "error": "user_prompt and final_content are required for LLM grading",
            "overall": 0.0,
        }

    prompt = _JUDGE_PROMPT.format(
        user_prompt=user_prompt[:1000],
        final_content=final_content[:2000],
        reasoning_style=er.reasoning_style or "unknown",
        error_tool_calls=er.error_tool_calls,
        turns_used=er.turns_used,
    )

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
        router = LLMRouter()
        response = router.invoke(
            llm_function,
            LLMRequest(
                system_prompt="You are a precise AI evaluator. Return only valid JSON.",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
                temperature=0.0,
            ),
        )
        raw = (response.content or "").strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            grade = json.loads(raw[start:end])
        else:
            grade = {"error": "invalid JSON from judge", "overall": 0.0}
    except Exception as exc:
        logger.warning("evaluator: LLM judge failed: %s", exc)
        grade = {"error": str(exc), "overall": 0.0}

    return grade
