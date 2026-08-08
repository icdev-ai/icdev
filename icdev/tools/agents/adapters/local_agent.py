# CUI // SP-CTI
"""hgx-exec-02: the owned rubric-gated agent loop, behind the AgentAdapter seam.

`local_llm_router` (OPT-71) routes an agent session through a SINGLE-SHOT
``router.invoke("code_generation", ...)`` completion. That produces prose. It
cannot read a file, cannot write one, and cannot verify its own work — so the
adapter abstraction's only owned path could not actually build anything, and
every consumer that needed file edits had to reach for ``claude_cli``.

This adapter closes that gap using surface that already exists:

  * ``icdev.tools.llm.agent_loop.run_agent_loop_with_rubric`` — the durable
    multi-turn loop with native tool use, budgets, stop_event and in-session
    revision.
  * ``tools.genesis.rubric_build_tools.build_worktree_toolset`` (hgx-exec-01) —
    read/list/grep/search/write/patch/git_diff/run_command, all traversal-guarded
    to the worktree.
  * ``tools.workflow.pipeline_grader.make_pipeline_grader`` — the real delivery
    gates (CodeLens/ruff, coherence, pytest, conformance) as the rubric, so
    "done" is a gate verdict rather than the model's own say-so.

It does NOT introduce a second engine, board or ingress; it is a thin binding of
those three onto :class:`~tools.agents.adapter_base.AgentAdapter`.

LLM-agnostic
------------
No model id appears in this module. Every model call routes by FUNCTION name
(``llm_function``, default ``code_generation``) through the injected
``LLMRouter``, which ``args/llm_config.yaml`` maps to whatever provider is
configured. Native tool use only — no provider-specific payload is constructed
here. When the resolved provider cannot serve tools at all (the CLI bridge, or a
model with ``supports_tools: false``), the loop raises ``AgentLoopUnsupported``;
:meth:`LocalAgentAdapter.invoke` catches it and DEGRADES to the single-shot
prose path with the reason recorded, rather than failing the run. That is the
shape ACE already uses in ``coworker_thread._run_agent_loop`` (unsupported →
audit → step mode).

A degraded run reports ``completed=False``: prose is advisory, no file was
edited and no gate was passed. ``exit_code`` stays 0 because nothing errored —
callers must branch on ``completed`` / ``structured['degraded']``, not on the
exit code.

`local_llm_router` is untouched and remains correct for research/plan tasks that
only need prose.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from tools.agents.adapter_base import (
    AgentResult,
    AgentSession,
    NotInstalledError,
)
from tools.logging.icdev_logger import get_logger


logger = get_logger(__name__)

# Router function key. A FUNCTION name, never a model id — args/llm_config.yaml
# owns the provider/model mapping and the air-gap fallback chain.
_DEFAULT_LLM_FUNCTION = "code_generation"

# Share of the session timeout given to the whole rubric run. Held under the
# caller's ceiling so the loop stops itself and returns a real result with
# truncation_reason set, instead of being killed from outside with no result.
_WALL_CLOCK_SHARE = 0.9

# Share of the session timeout one gate sweep may spend. The loop grades up to
# max_grading_iterations times, so an ungoverned sweep can spend the entire
# window judging instead of building.
_GATE_BUDGET_SHARE = 0.25
_MIN_BUDGET_SECONDS = 60.0

_SYSTEM_PROMPT = (
    "You are an autonomous software engineer building ONE task inside an "
    "isolated git worktree. Inspect the tree with read_file / list_files / "
    "grep_files / search_files, implement the change with write_file / "
    "patch_file, check your own work with git_diff and run_command, then call "
    "done. Your work is graded by the delivery pipeline (code quality, "
    "coherence, conformance to the task's acceptance criteria, and tests); if "
    "it fails you receive specific feedback and must fix it in the same "
    "session. Make the smallest correct change that satisfies the task."
)

# Pure-string completion markers. The authoritative signal for this adapter is
# the grader's verdict (see invoke()); detect_completion() exists for consumers
# that only hold the output text.
_COMPLETION_MARKERS = (
    "All delivery-pipeline gates passed",
    "[DONE]",
)


def _budget(timeout_seconds: int, share: float) -> float:
    return max(_MIN_BUDGET_SECONDS, float(timeout_seconds or 0) * share)


def _changed_files_thunk(work_dir: str) -> Callable[[], List[str]]:
    """Resolve the worktree's changed files at grade time, never raising.

    A callable (not a fixed list) because the set grows as the agent edits.
    """

    def _changed() -> List[str]:
        try:
            from tools.integrity.pr_gates import _git_changed_files

            return _git_changed_files("origin/main", False, Path(work_dir))
        except Exception:  # noqa: BLE001 — a diff failure must not fail the grade
            return []

    return _changed


def _import_agent_loop() -> Any:
    """Return the canonical agent-loop module.

    ``tools/llm/agent_loop.py`` is a re-export shim of the icdev copy, so both
    paths yield the same objects; the icdev import is tried first so the
    canonical module is bound even if the shim is missing.
    """
    try:
        import icdev.tools.llm.agent_loop as mod
    except ImportError:  # pragma: no cover - packaging layouts without icdev/
        import tools.llm.agent_loop as mod
    return mod


class LocalAgentAdapter:
    """AgentAdapter over the owned rubric-gated, tool-using agent loop."""

    name = "local_agent"

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def available(self) -> bool:
        """True when the router can serve the agent function on this host.

        Cheap and local: no network call, and deliberately NO tool-support
        probe. A provider that cannot do tool use is still usable here because
        :meth:`invoke` degrades to prose rather than failing, so gating
        availability on tool support would remove the very path that handles it.
        """
        try:
            from tools.llm.router import LLMRouter

            router = LLMRouter()
            if hasattr(router, "is_no_llm_mode") and router.is_no_llm_mode():
                return False
            if not router._get_chain_for_function(_DEFAULT_LLM_FUNCTION):  # noqa: SLF001
                return False
            _import_agent_loop()
            from tools.genesis.rubric_build_tools import build_worktree_toolset  # noqa: F401

            return True
        except Exception:  # noqa: BLE001 — availability probes never raise
            return False

    def prepare_prompt(self, session: AgentSession) -> str:
        """Task text plus the loop's completion contract."""
        return (
            f"{session.prompt}\n\n"
            "Implement this in the worktree using the available tools, then "
            "call done. Do not answer with a plan alone — the delivery gates "
            "grade the files on disk."
        )

    def invoke(self, session: AgentSession) -> AgentResult:
        """Run one rubric-gated build session. Never raises for LLM reasons.

        Raises:
            NotInstalledError: the router cannot serve this host at all
                (:meth:`available` is False) — the protocol's contract.
        """
        if not self.available():
            raise NotInstalledError(
                "LLMRouter has no available chain for "
                f"{_DEFAULT_LLM_FUNCTION!r}, or the agent-loop toolset is "
                "not importable"
            )

        t0 = time.time()
        work_dir = Path(session.working_dir or "")
        if not work_dir.is_dir():
            return self._error_result(
                session,
                f"working_dir is not a directory: {session.working_dir!r}",
                t0,
                exit_code=2,
            )

        agent_loop = _import_agent_loop()
        try:
            tools_schema, tool_handlers, grader, router = self._build_session(
                session, str(work_dir)
            )
        except Exception as exc:  # noqa: BLE001 — setup failure, not the agent's
            return self._error_result(session, f"{type(exc).__name__}: {exc}", t0)

        meta = session.metadata or {}
        stop_event = meta.get("stop_event")
        if not isinstance(stop_event, threading.Event):
            stop_event = threading.Event()

        grades: List[Dict[str, Any]] = []

        def _on_grade(round_no: int, grade: Any) -> None:
            grades.append(
                {
                    "round": round_no,
                    "verdict": getattr(grade, "verdict", ""),
                    "feedback": str(getattr(grade, "feedback", ""))[:2000],
                }
            )

        try:
            rubric_result = agent_loop.run_agent_loop_with_rubric(
                router,
                grader=grader,
                # None lets args/llm_config.yaml own the cap.
                max_grading_iterations=meta.get("max_grading_iterations"),
                on_grade=_on_grade,
                harness_task_id=session.task_id,
                system_prompt=session.system_prompt or _SYSTEM_PROMPT,
                user_prompt=self.prepare_prompt(session),
                tools=tools_schema,
                tool_handlers=tool_handlers,
                llm_function=meta.get("llm_function") or _DEFAULT_LLM_FUNCTION,
                max_iterations=session.max_turns,
                max_tokens=session.max_tokens,
                stop_event=stop_event,
                max_wall_clock_seconds=_budget(
                    session.timeout_seconds, _WALL_CLOCK_SHARE
                ),
            )
        except agent_loop.AgentLoopUnsupported as exc:
            # The resolved provider cannot do native tool use (CLI bridge, or
            # supports_tools=false). Degrade with the reason recorded — the run
            # continues, it does not fail. Mirrors ACE's unsupported → step mode.
            return self._degrade(session, str(exc), t0)
        except Exception as exc:  # noqa: BLE001 — one failed session, not a crash
            logger.exception("local_agent: rubric loop failed for %s", session.task_id)
            return self._error_result(session, f"{type(exc).__name__}: {exc}", t0)

        return self._result_from_rubric(session, rubric_result, grades, t0)

    def detect_completion(self, output: str) -> bool:
        """Pure-string heuristic over an agent's output text.

        Advisory only. The authoritative completion signal for this adapter is
        the grader verdict carried in ``AgentResult.completed`` /
        ``structured['satisfied']`` — a build can produce no final text at all
        and still have passed every gate.
        """
        if not output:
            return False
        return any(marker in output for marker in _COMPLETION_MARKERS)

    def parse_response(self, raw: str) -> Dict[str, Any]:
        return {"content": raw or "", "tool_calls": [], "diff": ""}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_session(
        self, session: AgentSession, work_dir: str
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Callable[..., Any], Any]:
        """Assemble toolset, grader and router for one build session."""
        from tools.genesis.rubric_build_tools import build_worktree_toolset
        from tools.llm.router import LLMRouter
        from tools.workflow.pipeline_grader import make_pipeline_grader

        meta = session.metadata or {}
        tools_schema, tool_handlers = build_worktree_toolset(work_dir)
        grader = make_pipeline_grader(
            cwd=work_dir,
            task_id=session.task_id,
            modified_files=_changed_files_thunk(work_dir),
            run_e2e=bool(meta.get("run_e2e", False)),
            run_conformance=bool(meta.get("run_conformance", True)),
            compare_to_main=True,
            budget_sec=_budget(session.timeout_seconds, _GATE_BUDGET_SHARE),
        )
        return tools_schema, tool_handlers, grader, LLMRouter()

    def _result_from_rubric(
        self,
        session: AgentSession,
        rubric_result: Any,
        grades: List[Dict[str, Any]],
        t0: float,
    ) -> AgentResult:
        inner = getattr(rubric_result, "result", None)
        satisfied = bool(getattr(rubric_result, "satisfied", False))
        return AgentResult(
            task_id=session.task_id,
            adapter_name=self.name,
            # The gates decide, not the model: a loop that ended cleanly but
            # failed the pipeline is NOT complete.
            completed=satisfied,
            exit_code=0 if satisfied else 1,
            output=getattr(inner, "final_content", "") or "",
            error="" if satisfied else self._unsatisfied_reason(rubric_result, grades),
            duration_ms=int((time.time() - t0) * 1000),
            structured={
                "satisfied": satisfied,
                "degraded": False,
                "grading_attempts": getattr(rubric_result, "grading_attempts", 0),
                "grades": grades,
                "loop_done": bool(getattr(inner, "done", False)),
                "turns": getattr(inner, "turns", 0),
                "tool_calls": len(getattr(inner, "tool_call_log", []) or []),
                "result_subtype": getattr(inner, "result_subtype", ""),
                "truncation_reason": getattr(inner, "truncation_reason", ""),
                "session_id": getattr(inner, "session_id", ""),
                "provider": getattr(inner, "provider", ""),
                "total_cost_usd": getattr(inner, "total_cost_usd", 0.0),
                "input_tokens": getattr(inner, "total_input_tokens", 0),
                "output_tokens": getattr(inner, "total_output_tokens", 0),
            },
        )

    @staticmethod
    def _unsatisfied_reason(rubric_result: Any, grades: List[Dict[str, Any]]) -> str:
        attempts = getattr(rubric_result, "grading_attempts", 0)
        last = grades[-1]["feedback"] if grades else ""
        return (
            f"delivery pipeline not satisfied after {attempts} grading round(s)"
            + (f": {last}" if last else "")
        )

    def _degrade(self, session: AgentSession, reason: str, t0: float) -> AgentResult:
        """Fall back to the single-shot prose path, recording why.

        The degraded output is advisory: no file was edited and no gate was
        passed, so ``completed`` stays False. ``exit_code`` stays 0 — nothing
        errored, the host simply cannot serve tool use.
        """
        logger.warning(
            "local_agent: native tool use unavailable for task %s — degrading to "
            "local_llm_router (prose only): %s",
            session.task_id,
            reason,
        )
        structured: Dict[str, Any] = {
            "satisfied": False,
            "degraded": True,
            "degraded_reason": reason,
            "degraded_to": "local_llm_router",
        }

        fallback_output = ""
        fallback_error = ""
        try:
            from tools.agents.adapters.local_llm_router import ADAPTER as _fallback

            inner = _fallback.invoke(session)
            fallback_output = inner.output or ""
            fallback_error = inner.error or ""
            structured["fallback_exit_code"] = inner.exit_code
            structured.update(
                {
                    k: v
                    for k, v in (inner.structured or {}).items()
                    if k not in structured
                }
            )
        except Exception as exc:  # noqa: BLE001 — degradation must not raise
            fallback_error = f"{type(exc).__name__}: {exc}"
            structured["fallback_failed"] = True

        return AgentResult(
            task_id=session.task_id,
            adapter_name=self.name,
            completed=False,
            exit_code=0,
            output=fallback_output,
            error=fallback_error,
            duration_ms=int((time.time() - t0) * 1000),
            structured=structured,
        )

    def _error_result(
        self,
        session: AgentSession,
        error: str,
        t0: float,
        exit_code: int = 1,
    ) -> AgentResult:
        return AgentResult(
            task_id=session.task_id,
            adapter_name=self.name,
            completed=False,
            exit_code=exit_code,
            error=error,
            duration_ms=int((time.time() - t0) * 1000),
            structured={"satisfied": False, "degraded": False},
        )


ADAPTER = LocalAgentAdapter()

__all__ = ["LocalAgentAdapter", "ADAPTER"]
