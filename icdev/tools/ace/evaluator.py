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
    model_id: str = ""
    """LLM model_id that ran this session — used by cross-grader enforcement."""


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
            "total_cost_usd, result_subtype, messages_json "
            "FROM agent_loop_sessions WHERE session_id = %s",
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
    session_json = row[6]

    done = subtype == "success"
    outcome = subtype or "unknown"
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
                    "DELETE FROM agent_evals WHERE session_id = %s",
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
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
            "FROM agent_evals WHERE session_id = %s "
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

_JUDGE_PROMPT = """You are a senior AI systems evaluator with experience grading 10,000+ agentic sessions across enterprise software development, security analysis, and research workflows. You think in signal-to-noise ratios: an agent that wastes turns is not just slow, it is consuming budget that could have been progress.

Grade the following agent session. For each dimension choose EXACTLY ONE label:
  supported   = the dimension is well met, directly evidenced in the session data
  partial     = partially met, or only inferable from limited signal
  unsupported = not met, or key data missing — do not guess a pass

Do NOT emit numeric scores. The platform composes the numeric grade from your
labels deterministically (this keeps grades comparable across model families).

DIMENSIONS:
1. faithfulness       — Does the final output accurately address the original user request? No hallucinated claims, no ignored requirements.
2. completeness       — Does the output cover all key aspects of the request? Partial completion is not full credit.
3. reasoning_quality  — Was reasoning clear, relevant, and grounded before each action? "unsupported" if no reasoning present.
4. cod_quality        — If Chain-of-Draft style: is draft appropriately concise yet complete? "partial" if style is N/A or unknown.
5. error_adaptation   — After tool errors, did the agent adapt its approach or blindly retry the same call?

RULES — these anti-patterns must lower the relevant label:
- Identical tool call repeated 3+ times with same inputs → error_adaptation "unsupported"
- Final response ignores an explicitly stated requirement → faithfulness "unsupported"
- No text before the first tool call in a multi-step task → reasoning_quality at most "partial"
- Agent declared done but final output contains explicit TODOs or unfilled placeholders → completeness at most "partial"
- Reasoning text is only trivial filler ("let me check", "I'll do this") with no analytical content → reasoning_quality at most "partial"
- Final output contradicts tool results visible in the session → faithfulness "unsupported"

Return ONLY valid JSON matching this schema exactly (labels only, no numbers):
{{"faithfulness": "supported"|"partial"|"unsupported", "completeness": "supported"|"partial"|"unsupported", "reasoning_quality": "supported"|"partial"|"unsupported", "cod_quality": "supported"|"partial"|"unsupported", "error_adaptation": "supported"|"partial"|"unsupported", "flags": ["<anti-pattern triggered or empty>"], "evidence": {{"what": "<one sentence finding with specific values>", "so_what": "<consequence or impact>", "now_what": "<specific action or NO ACTION REQUIRED>"}}, "reasoning": "<one sentence overall summary>"}}

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
    session_text: str = "",
    llm_function: str = "agent_eval_grading",
    exclude_model_id: str | None = None,
) -> dict:
    """LLM-as-judge grading of agent output quality.

    Returns a dict with keys: faithfulness, completeness, reasoning_quality,
    cod_quality, error_adaptation, overall, reasoning, grader_model_id.

    The result is NOT automatically persisted — caller decides whether to
    save via ``save_eval()``.

    Cross-grader enforcement:
        ``llm_function`` defaults to ``"agent_eval_grading"``, a routing chain
        that starts with tier-2 models (claude-sonnet, gpt-4o) — different from
        the ``code_generation`` chain used by the agent loop.

        Pass ``exclude_model_id`` (typically ``EvalResult.model_id``) to hard-
        block the session's own model from grading its output.  If the grading
        router would select the excluded model anyway, :class:`CrossGraderViolation`
        is raised.
    """
    if isinstance(eval_result_or_session_id, str):
        er = get_eval(eval_result_or_session_id)
        if er is None:
            er = EvalResult(session_id=eval_result_or_session_id)
    else:
        er = eval_result_or_session_id

    # session_text is a convenience alias for final_content
    effective_content = final_content or session_text

    if not user_prompt or not effective_content:
        return {
            "error": "user_prompt and final_content (or session_text) are required for LLM grading",
            "overall": 0.0,
        }

    # Auto-populate exclude_model_id from EvalResult if not explicitly passed
    effective_exclude = exclude_model_id or (er.model_id if er.model_id else None)

    prompt = _JUDGE_PROMPT.format(
        user_prompt=user_prompt[:1000],
        final_content=effective_content[:2000],
        reasoning_style=er.reasoning_style or "unknown",
        error_tool_calls=er.error_tool_calls,
        turns_used=er.turns_used,
    )

    from icdev.tools.llm.router import LLMRouter, CrossGraderViolation  # noqa: PLC0415
    from icdev.tools.llm.provider import LLMRequest  # noqa: PLC0415

    try:
        router = LLMRouter()
        invoke_kwargs: dict = {}
        if effective_exclude:
            invoke_kwargs["exclude_model_ids"] = [effective_exclude]
        response = router.invoke(
            llm_function,
            LLMRequest(
                system_prompt=(
                    "You are a precise AI systems evaluator. "
                    "Return only valid JSON — no prose, no markdown fences, no extra keys."
                ),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=768,
                temperature=0.0,
            ),
            **invoke_kwargs,
        )
        grader_model_id = getattr(response, "model_id", "") or ""

        # Hard cross-grader assertion: if the grader picked the same model anyway, reject.
        if effective_exclude and grader_model_id and grader_model_id == effective_exclude:
            raise CrossGraderViolation(
                f"Cross-grader violation: grader model {grader_model_id!r} is the same "
                f"as the evaluated session's model. Grading is invalid. "
                f"Add a different-tier model to the '{llm_function}' routing chain."
            )

        raw = (response.content or "").strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            grade = json.loads(raw[start:end])
            # Deterministic-picker (agx-pick-02): the judge now emits only a
            # per-dimension enum; Python composes the numeric floats + overall
            # (any-unsupported-on-faithfulness caps overall). Unknown/malformed
            # tokens degrade to the neutral midpoint inside compose_eval_overall.
            from icdev.tools.quality.categorical_scoring import (  # noqa: PLC0415
                EVAL_DIMENSIONS,
                VOCABULARY_VERSION,
                compose_eval_overall,
            )
            composed = compose_eval_overall(grade)
            for _dim in EVAL_DIMENSIONS:
                grade[_dim] = composed[_dim]
            grade["overall"] = composed["overall"]
            grade["faithfulness_failed"] = composed["faithfulness_failed"]
            grade["vocabulary_version"] = VOCABULARY_VERSION
        else:
            grade = {"error": "invalid JSON from judge", "overall": 0.0}
        grade["grader_model_id"] = grader_model_id
    except CrossGraderViolation:
        raise  # re-raise — callers must handle this explicitly
    except Exception as exc:
        logger.warning("evaluator: LLM judge failed: %s", exc)
        grade = {"error": str(exc), "overall": 0.0}

    return grade


# ---------------------------------------------------------------------------
# Improvement suggestions (rule-based, no LLM calls)
# ---------------------------------------------------------------------------

_SEVERITY_HIGH = "high"
_SEVERITY_MEDIUM = "medium"
_SEVERITY_LOW = "low"


def suggest_improvements(eval_result: EvalResult) -> list[dict]:
    """Rule-based improvement suggestions derived from eval metrics.

    Returns a list of suggestion dicts with keys:
        issue       — what is wrong
        suggestion  — actionable fix (usually a system prompt addition)
        field       — which config field to target: "system_prompt", "max_iterations",
                      "folder_access", or "reasoning_style"
        severity    — "high" | "medium" | "low"

    No LLM calls.  Pure rule evaluation against EvalResult fields.
    """
    suggestions: list[dict] = []

    # --- Outcome-based ---
    if eval_result.outcome == "error_max_turns":
        suggestions.append({
            "issue": f"Session hit max iterations limit ({eval_result.max_iterations} turns)",
            "suggestion": (
                f"Increase max_iterations to at least {eval_result.max_iterations + 6}. "
                "Consider breaking the task into smaller sub-tasks if it is complex."
            ),
            "field": "max_iterations",
            "severity": _SEVERITY_HIGH,
        })

    if eval_result.scope_violations > 0:
        suggestions.append({
            "issue": f"{eval_result.scope_violations} scope violation(s) detected",
            "suggestion": (
                "Restrict folder_access in the coworker spec to only the directories "
                "the task requires. The agent attempted to access paths outside its allowed scope."
            ),
            "field": "folder_access",
            "severity": _SEVERITY_HIGH,
        })

    if eval_result.trust_denials > 0:
        suggestions.append({
            "issue": f"{eval_result.trust_denials} trust-tier denial(s)",
            "suggestion": (
                "Upgrade the coworker trust_tier if the denied operations are legitimate, "
                "or add an explicit instruction: 'Do not attempt privileged operations.'"
            ),
            "field": "system_prompt",
            "severity": _SEVERITY_HIGH,
        })

    # --- Tool quality ---
    if eval_result.tool_error_rate > 0.35 and eval_result.total_tool_calls >= 3:
        suggestions.append({
            "issue": f"High tool error rate: {eval_result.tool_error_rate:.0%} of calls failed",
            "suggestion": (
                "Add to system prompt: 'Before calling a tool, verify the inputs are "
                "correct and within scope. If a tool fails, try a different approach "
                "rather than retrying the same call.'"
            ),
            "field": "system_prompt",
            "severity": _SEVERITY_HIGH if eval_result.tool_error_rate > 0.5 else _SEVERITY_MEDIUM,
        })

    if eval_result.tool_precision < 0.3 and eval_result.total_tool_calls >= 5:
        suggestions.append({
            "issue": (
                f"Low tool precision: only {len(eval_result.unique_tools)} unique tools "
                f"across {eval_result.total_tool_calls} calls — high repetition"
            ),
            "suggestion": (
                "Add to system prompt: 'Do not repeat a tool call with identical inputs. "
                "If a tool returned an error or unexpected result, change your approach.'"
            ),
            "field": "system_prompt",
            "severity": _SEVERITY_MEDIUM,
        })

    # --- CoT/CoD reasoning ---
    if not eval_result.plan_stated and eval_result.turns_used >= 2:
        suggestions.append({
            "issue": "No planning reasoning in first turn",
            "suggestion": (
                "Add to system prompt: 'Before taking any action, write a brief plan: "
                "what you will do, in what order, and what success looks like.'"
            ),
            "field": "system_prompt",
            "severity": _SEVERITY_MEDIUM,
        })

    if eval_result.reasoning_coverage < 0.5 and eval_result.total_tool_calls >= 3:
        suffix = (
            " Consider using Chain-of-Draft (CoD) style: short but complete reasoning drafts."
            if eval_result.reasoning_style == "cot" else ""
        )
        suggestions.append({
            "issue": f"Low reasoning coverage: only {eval_result.reasoning_coverage:.0%} of turns have reasoning text",
            "suggestion": (
                "Add to system prompt: 'Think step by step before each action. "
                "Write your reasoning before calling any tool — explain WHY, not just what.'"
                + suffix
            ),
            "field": "system_prompt",
            "severity": _SEVERITY_MEDIUM,
        })

    if eval_result.avg_reasoning_chars < 50 and eval_result.reasoning_coverage > 0.5:
        suggestions.append({
            "issue": f"Reasoning is very terse (avg {eval_result.avg_reasoning_chars:.0f} chars per turn)",
            "suggestion": (
                "Add to system prompt: 'When reasoning, include at least one sentence explaining "
                "your approach and what you expect the tool to return.'"
            ),
            "field": "system_prompt",
            "severity": _SEVERITY_LOW,
        })

    if not eval_result.has_error_recovery_reasoning and eval_result.error_tool_calls >= 2:
        suggestions.append({
            "issue": "No error-recovery reasoning after tool failures",
            "suggestion": (
                "Add to system prompt: 'After a tool error, reason explicitly about what "
                "went wrong before trying an alternative approach.'"
            ),
            "field": "system_prompt",
            "severity": _SEVERITY_MEDIUM,
        })

    # --- Efficiency ---
    if eval_result.efficiency_score < 0.25 and eval_result.outcome != "error_max_turns":
        suggestions.append({
            "issue": f"Low efficiency: used {eval_result.turns_used} of {eval_result.max_iterations} turns",
            "suggestion": (
                "Add to system prompt: 'Be concise and efficient. Combine related operations "
                "where possible and avoid redundant file reads.'"
            ),
            "field": "system_prompt",
            "severity": _SEVERITY_LOW,
        })

    if eval_result.reasoning_style == "" and eval_result.total_tool_calls >= 3:
        suggestions.append({
            "issue": "No reasoning style specified",
            "suggestion": (
                "Set reasoning_style to 'cod' (Chain-of-Draft) for concise, focused reasoning "
                "before each action, or 'cot' for more verbose step-by-step analysis."
            ),
            "field": "reasoning_style",
            "severity": _SEVERITY_LOW,
        })

    # Sort: high first, then medium, then low
    _order = {_SEVERITY_HIGH: 0, _SEVERITY_MEDIUM: 1, _SEVERITY_LOW: 2}
    suggestions.sort(key=lambda s: _order.get(s["severity"], 3))
    return suggestions


# ---------------------------------------------------------------------------
# Apply suggestions to a prompt
# ---------------------------------------------------------------------------


def apply_suggestions_to_prompt(system_prompt: str, suggestions: list[dict]) -> str:
    """Prepend high/medium severity suggestion text to a system prompt.

    Only includes suggestions whose ``field`` is ``"system_prompt"``.
    Returns the original prompt unchanged if no matching suggestions exist.
    """
    patches = [
        s["suggestion"]
        for s in suggestions
        if s.get("field") == "system_prompt" and s.get("severity") in ("high", "medium")
    ]
    if not patches:
        return system_prompt
    patch_block = "\n".join(f"- {p}" for p in patches)
    prefix = f"[Improvement guidance from prior run]\n{patch_block}\n\n"
    return prefix + (system_prompt or "")


# ---------------------------------------------------------------------------
# Before/after comparison
# ---------------------------------------------------------------------------


def compare_evals(session_a: str, session_b: str) -> dict:
    """Compare two EvalResult objects field by field.

    Returns a dict with:
        session_a, session_b, outcome_a, outcome_b,
        fields: [{name, a_value, b_value, delta, improved, higher_is_better}]
        improvements: int, regressions: int, overall_improved: bool
    """
    ea = get_eval(session_a) or score_session(session_a)
    eb = get_eval(session_b) or score_session(session_b)

    _NUMERIC_FIELDS = [
        ("efficiency_score", True),
        ("reasoning_coverage", True),
        ("tool_error_rate", False),
        ("tool_precision", True),
        ("avg_reasoning_chars", True),
        ("turns_used", False),
        ("scope_violations", False),
        ("trust_denials", False),
    ]
    _BOOL_FIELDS = ["plan_stated", "has_error_recovery_reasoning"]

    fields: list[dict] = []
    improvements = 0
    regressions = 0

    for fname, higher_is_better in _NUMERIC_FIELDS:
        va = getattr(ea, fname, None)
        vb = getattr(eb, fname, None)
        if va is None or vb is None:
            continue
        delta = (vb - va) if isinstance(vb, (int, float)) else None
        # delta==0 (unchanged) → neutral (None), not a regression
        improved: bool | None = (
            (delta > 0 if higher_is_better else delta < 0) if delta else None
        )
        if improved is True:
            improvements += 1
        elif improved is False:
            regressions += 1
        fields.append({
            "name": fname,
            "a_value": round(va, 3) if isinstance(va, float) else va,
            "b_value": round(vb, 3) if isinstance(vb, float) else vb,
            "delta": round(delta, 3) if delta is not None else None,
            "improved": improved,
            "higher_is_better": higher_is_better,
        })

    for fname in _BOOL_FIELDS:
        va = getattr(ea, fname, None)
        vb = getattr(eb, fname, None)
        # va==vb (unchanged) → neutral; False→True → improved; True→False → regressed
        imp: bool | None = None if (va is None or vb is None or va == vb) else (bool(vb) and not bool(va))
        if imp is True:
            improvements += 1
        elif imp is False:
            regressions += 1
        fields.append({
            "name": fname,
            "a_value": va,
            "b_value": vb,
            "delta": None,
            "improved": imp,
            "higher_is_better": True,
        })

    return {
        "session_a": session_a,
        "session_b": session_b,
        "outcome_a": ea.outcome,
        "outcome_b": eb.outcome,
        "fields": fields,
        "improvements": improvements,
        "regressions": regressions,
        "overall_improved": improvements > regressions,
    }


# ---------------------------------------------------------------------------
# Eval score trending (time-bucketed aggregation)
# ---------------------------------------------------------------------------


def get_eval_trends(
    days: int = 30,
    role: str | None = None,
    bucket: str = "week",
) -> list[dict]:
    """Aggregate eval scores over time, grouped by time bucket and optionally by role.

    Args:
        days:   How many days back to look (default 30, max 365).
        role:   Filter to a specific coworker role (None = all roles).
        bucket: Time bucket — 'day' | 'week' | 'month' (default 'week').

    Returns a list of dicts ordered by period ascending:
        [{period, role, count, avg_efficiency, avg_reasoning_coverage,
          avg_tool_error_rate, pct_done}]
    """
    from datetime import timezone, timedelta
    days = min(int(days), 365)
    bucket = bucket if bucket in ("day", "week", "month") else "week"

    conn = _get_conn()
    if conn is None:
        return []

    # Compute cutoff in Python — avoids dialect-specific datetime() calls
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    is_pg = getattr(conn, "_backend", "postgresql") == "postgresql"

    if is_pg:
        ph = "%s"
        if bucket == "day":
            trunc_expr = "DATE_TRUNC('day', graded_at)::date"
        elif bucket == "week":
            trunc_expr = "DATE_TRUNC('week', graded_at)::date"
        else:
            trunc_expr = "DATE_TRUNC('month', graded_at)::date"
        role_filter = "AND s.role = %s" if role else ""
    else:
        ph = "?"
        if bucket == "day":
            trunc_expr = "date(graded_at)"
        elif bucket == "week":
            trunc_expr = "date(graded_at, 'weekday 0', '-6 days')"
        else:
            trunc_expr = "strftime('%Y-%m-01', graded_at)"
        role_filter = "AND s.role = ?" if role else ""

    params: list = [cutoff]
    if role:
        params.append(role)

    try:
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                SELECT
                    {trunc_expr} AS period,
                    COALESCE(ai.role_id, 'unknown') AS role,
                    COUNT(*) AS count,
                    AVG(e.efficiency_score) AS avg_efficiency,
                    AVG(e.reasoning_coverage) AS avg_reasoning_coverage,
                    AVG(e.tool_error_rate) AS avg_tool_error_rate,
                    SUM(CASE WHEN e.done = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS pct_done
                FROM agent_evals e
                LEFT JOIN ace_sessions s ON s.session_id = e.session_id
                LEFT JOIN ace_instances ai ON ai.id = s.instance_id
                WHERE e.graded_at >= {ph}
                {role_filter}
                GROUP BY period, role
                ORDER BY period ASC
                """,
                params,
            )
            rows = cursor.fetchall()
            col_names = [d[0] for d in cursor.description]
        except Exception:
            # Fallback: no session/instance join — reset aborted PG txn first
            conn.rollback()
            cursor.execute(
                f"""
                SELECT
                    {trunc_expr} AS period,
                    'all' AS role,
                    COUNT(*) AS count,
                    AVG(efficiency_score) AS avg_efficiency,
                    AVG(reasoning_coverage) AS avg_reasoning_coverage,
                    AVG(tool_error_rate) AS avg_tool_error_rate,
                    SUM(CASE WHEN done = 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) AS pct_done
                FROM agent_evals
                WHERE graded_at >= {ph}
                GROUP BY period
                ORDER BY period ASC
                """,
                [cutoff],
            )
            rows = cursor.fetchall()
            col_names = [d[0] for d in cursor.description]

        results = []
        for row in rows:
            d = dict(zip(col_names, row))
            results.append({
                "period": str(d.get("period", "") or ""),
                "role": d.get("role", "all"),
                "count": int(d.get("count", 0)),
                "avg_efficiency": round(float(d.get("avg_efficiency") or 0), 3),
                "avg_reasoning_coverage": round(float(d.get("avg_reasoning_coverage") or 0), 3),
                "avg_tool_error_rate": round(float(d.get("avg_tool_error_rate") or 0), 3),
                "pct_done": round(float(d.get("pct_done") or 0), 1),
            })
        return results
    except Exception as exc:
        logger.warning("get_eval_trends failed: %s", exc)
        return []
    finally:
        conn.close()
