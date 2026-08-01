# CUI // SP-CTI
"""Eval suite runner — executes named test cases against the agent loop.

Load test definitions from ``args/eval_suite.yaml``, run each one against a
real or fake router, score with :func:`~icdev.tools.ace.evaluator.score_session`,
and persist results to ``agent_eval_runs``.

Each eval definition:
  name: read_and_summarize
  user_prompt: "Read src/main.py and summarize what it does"
  system_prompt: "You are a helpful assistant."
  expected_outcome: success
  expected_tools: [read_file, done]
  max_turns: 4
  pass_threshold: 0.7
  grading: rule_based
  reasoning_style: cod
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ace.eval_runner")

_SUITE_PATH = Path(__file__).resolve().parents[4] / "args" / "eval_suite.yaml"
_DB_ENV = "ACE_DB_PATH"


@dataclass
class EvalRunResult:
    run_id: str
    eval_name: str
    session_id: str = ""
    passed: bool = False
    expected_outcome: str = ""
    actual_outcome: str = ""
    expected_tools: list = field(default_factory=list)
    actual_tools: list = field(default_factory=list)
    eval_id: str = ""
    error: str = ""
    run_at: str = ""


def load_suite(path=None):
    """Load eval definitions from YAML. Returns empty list if file missing."""
    try:
        import yaml
        p = Path(path) if path else _SUITE_PATH
        if not p.exists():
            return []
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("evals", [])
    except Exception as exc:
        logger.warning("eval_runner: failed to load suite: %s", exc)
        return []


def run_eval(
    eval_def: dict,
    *,
    run_id: str,
    router: Any = None,
    tool_handlers=None,
    tools=None,
) -> EvalRunResult:
    """Run a single named eval. Returns an EvalRunResult."""
    from icdev.tools.ace.evaluator import score_session, save_eval

    name = eval_def.get("name", "unnamed")
    user_prompt = eval_def.get("user_prompt", "")
    system_prompt = eval_def.get("system_prompt", "You are a helpful agent. Complete the task.")
    max_turns = int(eval_def.get("max_turns", 6))
    expected_outcome = eval_def.get("expected_outcome", "")
    expected_tools = list(eval_def.get("expected_tools") or [])
    pass_threshold = float(eval_def.get("pass_threshold", 0.0))
    reasoning_style = eval_def.get("reasoning_style", "")

    result = EvalRunResult(
        run_id=run_id, eval_name=name,
        expected_outcome=expected_outcome, expected_tools=expected_tools,
        run_at=datetime.now(timezone.utc).isoformat(),
    )

    if not user_prompt:
        result.error = "missing user_prompt"
        return result

    if router is None:
        try:
            from tools.llm.router import LLMRouter
            router = LLMRouter()
        except Exception as exc:
            result.error = f"could not create router: {exc}"
            return result

    if tools is None or tool_handlers is None:
        from icdev.tools.ace.agent_tools import AgentToolRegistry

        class _MinSpec:
            coworker_id = f"eval-runner-{name[:20]}"
            trust_tier = "green"
            folder_access = []
            icdev_tools = []
            coordination_namespace = run_id

        reg = AgentToolRegistry(_MinSpec(), instance_id=f"eval-{uuid.uuid4().hex[:8]}")
        tools, tool_handlers = reg.build(["read_file", "list_files", "search_files", "done"])

    try:
        from icdev.tools.llm.agent_loop import run_agent_loop
        loop_result = run_agent_loop(
            router,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tools=tools,
            tool_handlers=tool_handlers,
            max_iterations=max_turns,
        )
    except Exception as exc:
        # AgentLoopUnsupported is a subclass of Exception
        result.error = f"loop error: {exc}"
        return result

    result.session_id = loop_result.session_id
    result.actual_outcome = (
        loop_result.result_subtype or ("success" if loop_result.done else "unknown")
    )

    eval_score = score_session(loop_result, max_iterations=max_turns, reasoning_style=reasoning_style)

    try:
        result.eval_id = save_eval(eval_score)
    except Exception:
        pass

    result.actual_tools = eval_score.unique_tools

    outcome_ok = (not expected_outcome) or (result.actual_outcome == expected_outcome)
    tools_ok = all(t in result.actual_tools for t in expected_tools) if expected_tools else True
    threshold_ok = eval_score.efficiency_score >= pass_threshold if pass_threshold else True
    result.passed = outcome_ok and tools_ok and threshold_ok

    _persist_run(result)
    return result


def run_all(suite_path=None, *, router: Any = None):
    """Run all evals in the suite. Returns results for each."""
    evals = load_suite(suite_path)
    if not evals:
        logger.info("eval_runner: no evals found in suite")
        return []

    run_id = f"run-{uuid.uuid4().hex[:10]}"
    results = []
    for eval_def in evals:
        try:
            r = run_eval(eval_def, run_id=run_id, router=router)
            results.append(r)
        except Exception as exc:
            results.append(EvalRunResult(
                run_id=run_id,
                eval_name=eval_def.get("name", "unknown"),
                error=str(exc),
                run_at=datetime.now(timezone.utc).isoformat(),
            ))
    return results


def _persist_run(run_result: EvalRunResult) -> None:
    conn = _get_conn()
    if conn is None:
        return
    try:
        conn.execute(
            "INSERT INTO agent_eval_runs "
            "(id, run_id, eval_name, session_id, passed, expected_outcome, "
            "actual_outcome, expected_tools_json, actual_tools_json, eval_id, run_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                f"aer-{uuid.uuid4().hex[:10]}",
                run_result.run_id, run_result.eval_name,
                run_result.session_id or "",
                int(run_result.passed),
                run_result.expected_outcome,
                run_result.actual_outcome,
                json.dumps(run_result.expected_tools),
                json.dumps(run_result.actual_tools),
                run_result.eval_id or "",
                run_result.run_at,
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("eval_runner: failed to persist run: %s", exc)
    finally:
        conn.close()


def _get_conn():
    try:
        from tools.db.storage import get_canvas_connection
        return get_canvas_connection(_DB_ENV)
    except Exception:
        return None
