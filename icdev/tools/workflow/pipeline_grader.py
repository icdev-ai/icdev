"""Delivery-pipeline rubric grader for :func:`run_agent_loop_with_rubric`.

Turns the governed delivery pipeline into a *code grader* the rubric-gated
agent loop can build against. Instead of an LLM judging the agent's prose, the
grader runs the real gates against the working tree and returns a
:class:`RubricGrade`:

  * :func:`validate_working_tree` — CodeLens / Coherence / (opt) Playwright E2E
    / pytest, the same ordered gate suite the kanban runner uses pre-done.
  * :func:`review_conformance` — the spec-conformance judge (was the change
    built to the task's acceptance criteria?), distinct from V&V.

The agent loop then treats a failing gate exactly like ``needs_revision`` from
an LLM rubric: the actionable feedback is injected and the SAME build session
resumes to fix it, up to the loop's ``max_grading_iterations`` / budget.

This module is a NEW primitive's grader — it is NOT wired into the kanban
runner or executor. Wiring an opt-in rubric-gated executor is a separate step.

LLM-agnostic by construction: this grader is pure Python and never touches an
LLM router. ``review_conformance`` does its own routing via
``LLMRouter().invoke("conformance_review", ...)`` (config-driven, air-gap
fallback) — we neither bypass it nor add any provider assumption here.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Union

from icdev.tools.llm.agent_loop import AgentLoopResult, RubricGrade, RubricVerdict
from icdev.tools.testing.conformance_reviewer import review_conformance
from icdev.tools.workflow.validated_commit import validate_working_tree

# modified_files may be a fixed list or a thunk resolved at grade time (the set
# of changed files grows as the agent edits, so a callable stays current).
ModifiedFiles = Union[List[str], Callable[[], List[str]]]


def _resolve_files(modified_files: Optional[ModifiedFiles]) -> List[str]:
    if callable(modified_files):
        modified_files = modified_files()
    return list(modified_files or [])


def _gate_bullets(metrics: dict) -> List[str]:
    """Actionable detail lines from a failing gate-suite metrics dict."""
    bullets: List[str] = []
    if metrics.get("codelens_passed") is False or metrics.get("ruff_issues"):
        bullets.append(
            f"CodeLens/ruff: {metrics.get('ruff_issues', 0)} issue(s); "
            f"bandit: {metrics.get('bandit_issues', 0)}"
        )
    if metrics.get("coherence_passed") is False:
        bullets.append(
            f"Coherence: {metrics.get('coherence_violations', '?')} violation(s)"
        )
    if metrics.get("pytest_passed") is False:
        failed = metrics.get("failed_tests")
        bullets.append(f"Unit tests failed: {failed if failed else 'see pytest output'}")
    if metrics.get("e2e_passed") is False:
        bullets.append("E2E Playwright: failing")
    return bullets


def make_pipeline_grader(
    cwd: str,
    task_id: str,
    modified_files: Optional[ModifiedFiles] = None,
    *,
    run_e2e: bool = False,
    run_conformance: bool = True,
    compare_to_main: bool = True,
    budget_sec: Optional[float] = None,
) -> Callable[[Optional[AgentLoopResult]], RubricGrade]:
    """Build a code grader that runs the delivery-pipeline gates as the rubric.

    Args:
        cwd: Worktree (or repo) path to validate.
        task_id: Kanban task id — supplies acceptance criteria + branch diff to
            the conformance reviewer.
        modified_files: Changed files (relative), a fixed list or a callable
            resolved at grade time. ``None`` lets the gates auto-detect.
        run_e2e: Run the per-task Playwright E2E gate (default off — it's slow
            and only meaningful for UI changes).
        run_conformance: Run the spec-conformance review (default on). When the
            reviewer can't judge (no criteria / no diff / LLM unavailable) it
            returns ``review_passed=None``, which does NOT block — the gate is
            recorded, not enforced, mirroring the record-only default.
        compare_to_main: Coherence only fails on NEW violations vs main.
        budget_sec: Wall-clock cap for one gate sweep. Defaults to the
            ``ICDEV_KANBAN_VERIFY_BUDGET_SEC`` value. The kanban runner derives
            it from the task's dispatch budget so the grader can never spend
            more time judging the work than building it.

    Returns:
        ``grader(result) -> RubricGrade`` suitable for
        :func:`run_agent_loop_with_rubric`'s ``grader=`` parameter. The
        ``result`` arg is accepted (and ignored) because the grader inspects the
        working tree, not the agent's text. Never raises.
    """

    def grader(result: Optional[AgentLoopResult] = None) -> RubricGrade:
        try:
            files = _resolve_files(modified_files)
        except Exception as exc:  # noqa: BLE001
            return RubricGrade(
                verdict=RubricVerdict.grader_error,
                feedback=f"could not resolve modified_files: {exc}",
            )

        # 1) Ordered gate suite (CodeLens -> Coherence -> [E2E] -> pytest).
        try:
            ok, reason, metrics = validate_working_tree(
                cwd,
                modified_files=files,
                compare_to_main=compare_to_main,
                run_e2e=run_e2e,
                run_companion=False,
                budget_sec=budget_sec,
            )
        except Exception as exc:  # noqa: BLE001 — infra failure, not the agent's fault
            return RubricGrade(
                verdict=RubricVerdict.grader_error,
                feedback=f"gate suite error: {exc}",
            )
        metrics = metrics or {}

        bullets: List[str] = []
        if not ok:
            bullets.append(f"Delivery gates failed: {reason}")
            bullets.extend(f"  {b}" for b in _gate_bullets(metrics))

        # 2) Spec-conformance review (build-the-right-thing). Never raises.
        #
        # Only asked once the mechanical gates are green. While ruff, coherence,
        # or pytest are still failing the agent already has concrete work queued,
        # and "did you build the right thing?" is an LLM round-trip whose answer
        # cannot be acted on yet — it was previously paid on every revision
        # round of the loop.
        review_passed: Optional[bool] = None
        if run_conformance and ok:
            cr = review_conformance(task_id, changed_files=files) or {}
            review_passed = cr.get("review_passed")
            if review_passed is False:
                bullets.append(
                    "Conformance review failed: "
                    + str(cr.get("reason") or "does not match acceptance criteria")
                )
                unmet = [
                    f for f in (cr.get("findings") or []) if not f.get("met")
                ]
                for f in unmet[:6]:
                    crit = f.get("criterion") or f.get("text") or f.get("id") or f
                    note = f.get("note") or f.get("evidence")
                    bullets.append(f"  - unmet: {crit}" + (f" ({note})" if note else ""))

        # 3) Map to a rubric verdict. review_passed is None (couldn't judge)
        #    does NOT block — only an explicit False fails conformance.
        satisfied = ok and (review_passed is not False)
        if satisfied:
            note = "" if review_passed else " (conformance not evaluated)"
            return RubricGrade(
                verdict=RubricVerdict.satisfied,
                feedback="All delivery-pipeline gates passed" + note + ".",
            )

        feedback = (
            "The change did not pass the delivery pipeline. "
            "Address these, then finish:\n" + "\n".join(f"- {b}" for b in bullets)
        )
        return RubricGrade(verdict=RubricVerdict.needs_revision, feedback=feedback)

    return grader


__all__ = ["make_pipeline_grader", "ModifiedFiles"]
