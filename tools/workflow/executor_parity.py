# CUI // SP-CTI
"""hgx-exec-04 — replay a fixed corpus of merged kanban tasks through two executors.

`_dispatch_via_rubric_loop` (the owned, file-editing agent loop) shipped behind
``KANBAN_RUBRIC_LOOP``, default OFF, with 13 unit tests and zero evidence that it
can build anything. This module produces the missing evidence: a reproducible
A/B replay of the SAME corpus through the SAME grader, so the eventual decision
about the executor chain is made against numbers rather than intuition.

What it does, per (task, executor) pair:

  1. ``git worktree add --detach`` a disposable tree at the task's ``base_commit``
     — the state of the repo immediately BEFORE the human fix landed.
  2. Hand the executor the task prompt (from
     ``args/executor_parity_corpus.yaml``) through the
     :mod:`tools.agents` AgentAdapter seam — ``claude_cli`` and ``local_agent``
     are two implementations of one interface, so neither gets a bespoke harness.
  3. Grade the resulting tree with
     :func:`tools.workflow.pipeline_grader.make_pipeline_grader` — the real
     delivery gates (ruff/bandit, coherence, pytest). The grade is computed by
     THIS module for both executors, so it is independent of whatever an
     executor claims about itself.
  4. Record gate verdict, wall-clock, cost, tokens and the changed-file set, then
     destroy the worktree.

Two numbers are reported per executor and they are not the same number:

  ``gate_pass_rate``    — this harness's independent verdict on the tree.
  ``self_report_rate``  — what the executor claimed (``AgentResult.completed``).

The gap between them is the interesting result: an executor that reports success
on a tree the gates reject is worse than one that reports failure honestly.

NON-GOAL: this module does not flip anything. It never reads or writes
``KANBAN_RUBRIC_LOOP``, never touches ``args/strategos_config.yaml``, and never
goes through the kanban dispatch path. It talks to the adapters directly.

LLM-agnostic
------------
No model id appears here. Executor selection is by ADAPTER NAME; every model
call happens inside an adapter, which routes by ``llm_function`` through
``LLMRouter``. ``local_agent`` already catches ``AgentLoopUnsupported`` and
degrades to prose — a degraded run is recorded as ``degraded=True`` with
``graded_pass`` decided by the gates like any other, never as a harness error.

OS-agnostic
-----------
``pathlib`` throughout, repo root from ``__file__`` (never ``os.getcwd()`` — this
runs from disposable worktrees), fixed-argv ``subprocess`` with ``shell=False``,
``shutil.which`` for git, ``encoding="utf-8"`` + ``newline=""`` on every file
read and write, and a two-sided worktree-removal path because Windows holds file
locks that POSIX does not.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess  # nosec B404 — git only, fixed argv, shell=False
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import yaml

def _find_repo_root() -> Path:
    """Repo root from this file, never the cwd — the harness runs from worktrees.

    Walks up rather than indexing ``parents[2]``, because this module is mirrored
    to ``icdev/tools/workflow/`` and a fixed index resolves to ``icdev/`` there —
    which has an ``args/`` of its own, so the wrong answer would look right until
    the corpus came back short.
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "args" / "executor_parity_corpus.yaml").is_file():
            return candidate
        if (candidate / ".git").exists() and (candidate / "tools").is_dir():
            return candidate
    return here.parents[2]


REPO_ROOT = _find_repo_root()

DEFAULT_CORPUS_PATH = REPO_ROOT / "args" / "executor_parity_corpus.yaml"

#: The two executors under test. `claude_cli` is and remains primary.
DEFAULT_EXECUTORS: Tuple[str, ...] = ("claude_cli", "local_agent")

#: Worktree actor. `verify` is disjoint from `kanban` and `cli`, so a benchmark
#: worktree can never land on a path a live task or an interactive session owns.
_WORKTREE_ACTOR = "verify"

#: Per (task, executor) build ceiling. The grader gets its own budget on top.
DEFAULT_TIMEOUT_SECONDS = 480

#: Turn ceiling handed to both adapters, so neither is measured with a budget
#: the other did not have.
DEFAULT_MAX_TURNS = 25

#: Share of the build timeout one gate sweep may spend.
_GRADE_BUDGET_SHARE = 0.5
_MIN_GRADE_BUDGET = 90.0

#: Extra seconds an adapter gets past ``timeout_seconds`` before the harness
#: intervenes. `claude_cli` enforces the session timeout itself (subprocess
#: timeout); `local_agent` only derives budget SHARES from it, and a gate sweep
#: that started before the deadline runs to completion — measured at 3x the
#: session timeout on the first smoke run. Comparing wall clock between an
#: executor that honours the ceiling and one that does not is not a comparison,
#: so the harness imposes it on both.
_TIMEOUT_GRACE_SECONDS = 90.0

#: How long a timed-out adapter gets to notice its stop_event and unwind.
_STOP_GRACE_SECONDS = 90.0

#: Env vars never imported from .env by :func:`load_env_file`. KANBAN_RUBRIC_LOOP
#: is on this list on purpose: this benchmark must not be able to change the
#: kanban executor default even by accident, and a reader checking that claim
#: should find it enforced in code rather than asserted in a comment.
_ENV_DENYLIST = frozenset({"KANBAN_RUBRIC_LOOP"})

BENCHMARK_SYSTEM_PROMPT = (
    "You are an autonomous software engineer building ONE task inside an "
    "isolated, disposable git worktree checked out at the commit immediately "
    "before the change was originally made. Implement the change in the files "
    "on disk and stop.\n\n"
    "Hard constraints for this run:\n"
    "  - Do NOT git commit, git push, create a branch, or open a pull request.\n"
    "  - Do NOT call the kanban board API or move any task.\n"
    "  - Do NOT send notifications.\n"
    "  - Do NOT delete or modify anything outside this worktree.\n"
    "Your work is judged by the delivery gates (ruff/bandit, coherence, unit "
    "tests) run against the files you leave on disk. Make the smallest correct "
    "change that satisfies the task."
)


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------


@dataclass
class CorpusTask:
    """One already-merged task, replayable from its pre-fix parent commit."""

    task_id: str
    title: str
    prompt: str
    base_commit: str
    reference_commit: str
    task_type: str = "fix"
    reference_files: List[str] = field(default_factory=list)
    acceptance: List[str] = field(default_factory=list)

    def instruction(self) -> str:
        """The full text an executor receives: task, then acceptance criteria."""
        parts = [f"# Task {self.task_id}: {self.title}", "", self.prompt.strip()]
        if self.acceptance:
            parts += ["", "## Acceptance criteria", ""]
            parts += [f"- {line}" for line in self.acceptance]
        return "\n".join(parts) + "\n"


def load_corpus(path: Optional[Path] = None) -> List[CorpusTask]:
    """Parse the replay corpus. Raises ValueError on a malformed entry."""
    corpus_path = Path(path) if path else DEFAULT_CORPUS_PATH
    with open(corpus_path, "r", encoding="utf-8", newline="") as fh:
        doc = yaml.safe_load(fh) or {}

    raw_tasks = doc.get("tasks") or []
    if not raw_tasks:
        raise ValueError(f"corpus {corpus_path} declares no tasks")

    tasks: List[CorpusTask] = []
    seen: set = set()
    for i, raw in enumerate(raw_tasks):
        missing = [
            k for k in ("task_id", "title", "prompt", "base_commit", "reference_commit")
            if not (raw or {}).get(k)
        ]
        if missing:
            raise ValueError(f"corpus entry {i} is missing {missing}")
        task_id = str(raw["task_id"])
        if task_id in seen:
            raise ValueError(f"corpus declares {task_id} twice")
        seen.add(task_id)
        tasks.append(
            CorpusTask(
                task_id=task_id,
                title=str(raw["title"]),
                prompt=str(raw["prompt"]),
                base_commit=str(raw["base_commit"]),
                reference_commit=str(raw["reference_commit"]),
                task_type=str(raw.get("task_type") or "fix"),
                reference_files=[str(f) for f in (raw.get("reference_files") or [])],
                acceptance=[str(a) for a in (raw.get("acceptance") or [])],
            )
        )
    return tasks


def load_env_file(path: Optional[Path] = None) -> int:
    """Populate os.environ from the canonical ``.env``, without overriding.

    A benchmark worktree has no ``.env`` (it is gitignored), so the LLM router
    would find no provider configured and ``local_agent`` would report itself
    unavailable — an environment artefact that would read as a real result.
    Existing values always win, and :data:`_ENV_DENYLIST` keys are never
    imported. Returns the number of variables set.
    """
    env_path = Path(path) if path else (canonical_root() / ".env")
    if not env_path.is_file():
        return 0
    count = 0
    with open(env_path, "r", encoding="utf-8", newline="", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if not key or key in _ENV_DENYLIST or key in os.environ:
                continue
            value = value.strip().strip('"').strip("'")
            os.environ[key] = value
            count += 1
    return count


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def module_budget_status() -> Dict[str, Any]:
    """Whether the router's module budget would block the owned executor.

    Only ``local_agent`` is subject to this: it routes through ``LLMRouter``,
    which enforces ``module_budgets`` from ``args/llm_config.yaml``.
    ``claude_cli`` shells out to the vendor CLI and is not metered by it at all,
    so the two executors are not governed by the same ceiling — a fact the
    report has to state rather than average away.
    """
    try:
        from tools.budget.module_budget_tracker import check_module_budget

        status = check_module_budget(
            "generative_intelligence", function="code_generation"
        )
        return {
            "action": status.get("action"),
            "budget_tokens": status.get("budget_tokens"),
            "spent_tokens": status.get("spent_tokens"),
            "month": status.get("month"),
            "message": str(status.get("message") or "")[:300],
        }
    except Exception as exc:  # noqa: BLE001 — a probe must never fail the run
        return {"action": "unknown", "message": f"{type(exc).__name__}: {exc}"}


class lifted_module_budget:  # noqa: N801 — used as a context manager, not a type
    """Suspend module-budget enforcement IN THIS PROCESS ONLY, opt-in.

    Measured on 2026-08-08: the ``generative_intelligence`` monthly token cap
    (400,000) was already exhausted on day 8, so every LLM-router call was
    hard-stopped and ``local_agent`` returned after zero turns. Benchmarking the
    owned executor in that state measures a spent budget, not a capability.

    This patches the tracker's in-process config cache and restores it on exit.
    Nothing on disk and nothing in the database changes. It is off by default
    (``--lift-module-budget``) and always recorded in the report metadata,
    because a number produced with governance suspended must never be mistaken
    for one produced under it.
    """

    def __init__(self, active: bool = True) -> None:
        self.active = active
        self._module: Any = None
        self._previous: Any = None

    def __enter__(self) -> "lifted_module_budget":
        if not self.active:
            return self
        import tools.budget.module_budget_tracker as tracker

        self._module = tracker
        self._previous = tracker._module_budget_config_cache  # noqa: SLF001
        tracker._module_budget_config_cache = {"enabled": False}  # noqa: SLF001
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        if self._module is not None:
            self._module._module_budget_config_cache = self._previous  # noqa: SLF001
        return False


def _git_exe() -> str:
    return shutil.which("git") or "git"


def _git(
    args: Sequence[str], cwd: Path, timeout: int = 120
) -> subprocess.CompletedProcess:
    """Fixed-argv git call. No shell, no interpolation, utf-8 decoded."""
    return subprocess.run(  # nosec B603 — fixed arg list, shell=False
        [_git_exe(), *args],
        cwd=str(cwd),
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def canonical_root() -> Path:
    """The MAIN worktree's root, resolved through git, falling back to REPO_ROOT."""
    try:
        from tools.git.worktree_paths import canonical_repo_root

        return canonical_repo_root(REPO_ROOT)
    except Exception:  # noqa: BLE001 — git absent or module missing
        return REPO_ROOT


def commit_exists(sha: str, repo: Optional[Path] = None) -> bool:
    """True when *sha* resolves to a commit in this clone."""
    proc = _git(["cat-file", "-t", sha], repo or REPO_ROOT, timeout=30)
    return proc.returncode == 0 and proc.stdout.strip() == "commit"


def changed_files_since(base: str, work_dir: Path) -> List[str]:
    """Repo-relative paths the executor left changed in *work_dir*.

    Deliberately NOT ``base...HEAD``: nothing is committed in a benchmark
    worktree, so a commit-range diff reports an empty set and the gates would
    grade a no-op as a pass. This is the working tree against *base*, plus
    untracked files, which is what the executor actually produced.
    """
    files: List[str] = []
    tracked = _git(["diff", "--name-only", base], work_dir, timeout=120)
    if tracked.returncode == 0:
        files += [ln.strip() for ln in tracked.stdout.splitlines() if ln.strip()]
    untracked = _git(
        ["ls-files", "--others", "--exclude-standard"], work_dir, timeout=120
    )
    if untracked.returncode == 0:
        files += [ln.strip() for ln in untracked.stdout.splitlines() if ln.strip()]
    return sorted({f.replace("\\", "/") for f in files})


def worktree_slug(task_id: str, executor: str) -> str:
    return f"parity-{task_id}-{executor}"


def replay_worktree_path(task_id: str, executor: str) -> Path:
    """Sanctioned, collision-free path for one replay worktree."""
    from tools.git.worktree_paths import worktree_path

    return worktree_path(_WORKTREE_ACTOR, worktree_slug(task_id, executor))


def create_replay_worktree(task: CorpusTask, executor: str) -> Path:
    """Create a detached worktree at the task's pre-fix base commit.

    Detached on purpose: with no branch and no upstream there is nothing for a
    stray ``git push`` to push to, which is a cheaper guarantee than trusting
    the prompt's prohibition.
    """
    path = replay_worktree_path(task.task_id, executor)
    remove_worktree(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = _git(
        ["worktree", "add", "--detach", str(path), task.base_commit],
        canonical_root(),
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git worktree add failed for {task.task_id}/{executor}: "
            f"{(proc.stderr or proc.stdout).strip()[:400]}"
        )
    return path


def remove_worktree(path: Path, attempts: int = 3) -> bool:
    """Destroy a replay worktree, tolerating Windows file locks.

    Two-sided by necessity: ``git worktree remove`` is enough on POSIX, but on
    Windows a just-exited agent subprocess can still hold a handle, so the
    removal is retried and then finished with an ignore-errors tree delete plus
    a prune so the registration never leaks.
    """
    root = canonical_root()
    for attempt in range(attempts):
        if not path.exists():
            _git(["worktree", "prune"], root, timeout=60)
            return True
        proc = _git(["worktree", "remove", "--force", str(path)], root, timeout=120)
        if proc.returncode == 0 and not path.exists():
            return True
        if attempt < attempts - 1:
            time.sleep(2.0)
    shutil.rmtree(str(path), ignore_errors=True)
    _git(["worktree", "prune"], root, timeout=60)
    return not path.exists()


# ---------------------------------------------------------------------------
# One replay
# ---------------------------------------------------------------------------


@dataclass
class ParityRun:
    """Outcome of one (task, executor) replay."""

    task_id: str
    executor: str
    status: str = "ok"                  # ok | unavailable | harness_error
    available: bool = True
    # The harness's own verdict, from make_pipeline_grader.
    graded_pass: Optional[bool] = None
    verdict: str = ""
    gate_feedback: str = ""
    # What the executor said about itself.
    self_reported_complete: bool = False
    degraded: bool = False
    degraded_reason: str = ""
    timed_out: bool = False
    abandoned: bool = False        # still running when the harness gave up
    exit_code: int = 0
    # Cost of the attempt.
    build_seconds: float = 0.0
    grade_seconds: float = 0.0
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    turns: int = 0
    # What it touched.
    files_changed: int = 0
    files: List[str] = field(default_factory=list)
    reference_overlap: int = 0
    error: str = ""

    @property
    def overclaimed(self) -> bool:
        """Said done, gates disagreed — the failure mode that matters most."""
        return self.self_reported_complete and self.graded_pass is not True


def _grade_budget(timeout_seconds: int) -> float:
    return max(_MIN_GRADE_BUDGET, float(timeout_seconds or 0) * _GRADE_BUDGET_SHARE)


@dataclass
class _Invocation:
    """What came back from a bounded ``adapter.invoke`` call."""

    result: Any = None
    exception: Optional[BaseException] = None
    timed_out: bool = False
    still_running: bool = False


def invoke_bounded(
    adapter: Any,
    session: Any,
    stop_event: "threading.Event",
    *,
    grace_seconds: float = _TIMEOUT_GRACE_SECONDS,
    stop_grace_seconds: float = _STOP_GRACE_SECONDS,
) -> _Invocation:
    """Run ``adapter.invoke`` under a ceiling the adapter cannot ignore.

    A thread rather than a process (D36: threads, not asyncio) — the adapters
    are in-process objects and one of them, ``local_agent``, is cooperative:
    it accepts a ``stop_event`` through ``session.metadata``, so the ceiling is
    signalled first and only reported as abandoned if it is also ignored.

    An abandoned thread is a daemon, so it never holds the interpreter open, but
    it may still be writing into the worktree — the caller must not delete a
    tree whose thread is still alive.
    """
    box = _Invocation()
    done = threading.Event()

    def _worker() -> None:
        try:
            box.result = adapter.invoke(session)
        except BaseException as exc:  # noqa: BLE001 — surfaced to the caller
            box.exception = exc
        finally:
            done.set()

    worker = threading.Thread(
        target=_worker,
        name=f"parity-{getattr(session, 'task_id', '?')}",
        daemon=True,
    )
    worker.start()

    ceiling = float(getattr(session, "timeout_seconds", 0) or 0) + grace_seconds
    if done.wait(ceiling):
        return box

    box.timed_out = True
    stop_event.set()
    if not done.wait(stop_grace_seconds):
        box.still_running = True
    return box


def grade_worktree(
    task: CorpusTask,
    work_dir: Path,
    *,
    run_conformance: bool = False,
    compare_to_main: bool = True,
    budget_sec: Optional[float] = None,
) -> Tuple[Optional[bool], str, str]:
    """Score a replay tree with the delivery-pipeline grader.

    Returns ``(passed, verdict, feedback)`` where ``passed`` is ``None`` when the
    grader itself failed (infrastructure, not the executor's fault) — recorded,
    never laundered into a pass or charged to the executor as a fail.

    Conformance review is OFF by default. It is an LLM round-trip, so leaving it
    on would make the published gate-pass rate depend on a model's mood and on a
    kanban DB being reachable; the mechanical gates are the reproducible part.
    """
    from icdev.tools.llm.agent_loop import RubricVerdict
    from tools.workflow.pipeline_grader import make_pipeline_grader

    grader = make_pipeline_grader(
        cwd=str(work_dir),
        task_id=task.task_id,
        modified_files=lambda: changed_files_since(task.base_commit, work_dir),
        run_e2e=False,
        run_conformance=run_conformance,
        compare_to_main=compare_to_main,
        budget_sec=budget_sec,
    )
    grade = grader(None)
    verdict = str(getattr(grade, "verdict", "") or "")
    feedback = str(getattr(grade, "feedback", "") or "")
    if verdict == RubricVerdict.grader_error:
        return None, verdict, feedback
    return (verdict == RubricVerdict.satisfied), verdict, feedback


def run_one(
    task: CorpusTask,
    executor: str,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_turns: int = DEFAULT_MAX_TURNS,
    run_conformance: bool = False,
    compare_to_main: bool = True,
    keep_worktree: bool = False,
    llm_function: str = "",
    adapter: Any = None,
    log: Callable[[str], None] = lambda _m: None,
) -> ParityRun:
    """Replay one task through one executor. Never raises."""
    from tools.agents.adapter_base import AgentSession, NotInstalledError

    run = ParityRun(task_id=task.task_id, executor=executor)

    if adapter is None:
        try:
            from tools.agents.registry import get_adapter

            adapter = get_adapter(executor)
        except Exception as exc:  # noqa: BLE001
            run.status = "harness_error"
            run.error = f"{type(exc).__name__}: {exc}"
            return run

    try:
        run.available = bool(adapter.available())
    except Exception as exc:  # noqa: BLE001 — an availability probe must not fail a run
        run.available = False
        run.error = f"available() raised: {type(exc).__name__}: {exc}"
    if not run.available:
        run.status = "unavailable"
        log(f"  {task.task_id}/{executor}: adapter unavailable on this host")
        return run

    work_dir: Optional[Path] = None
    try:
        work_dir = create_replay_worktree(task, executor)
    except Exception as exc:  # noqa: BLE001
        run.status = "harness_error"
        run.error = f"{type(exc).__name__}: {exc}"
        return run

    try:
        stop_event = threading.Event()
        session = AgentSession(
            task_id=task.task_id,
            prompt=task.instruction(),
            working_dir=str(work_dir),
            system_prompt=BENCHMARK_SYSTEM_PROMPT,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            metadata={
                "run_conformance": run_conformance,
                "run_e2e": False,
                "source": "executor_parity",
                "stop_event": stop_event,
                # A FUNCTION name, never a model id — args/llm_config.yaml owns
                # the provider mapping. Empty means the adapter's own default,
                # i.e. whatever this host is really configured to build with.
                **({"llm_function": llm_function} if llm_function else {}),
            },
        )
        t0 = time.monotonic()
        invocation = invoke_bounded(adapter, session, stop_event)
        run.build_seconds = round(time.monotonic() - t0, 2)
        run.timed_out = invocation.timed_out
        run.abandoned = invocation.still_running

        if isinstance(invocation.exception, NotInstalledError):
            run.status = "unavailable"
            run.available = False
            run.error = str(invocation.exception)
            return run
        if invocation.exception is not None:
            exc = invocation.exception
            run.status = "harness_error"
            run.error = f"invoke raised {type(exc).__name__}: {exc}"
            return run

        result = invocation.result
        if result is None:
            # Timed out with nothing returned. The tree is still graded: whatever
            # the executor managed to write before the ceiling is its output, and
            # scoring it is more honest than discarding the attempt.
            run.error = (
                f"executor exceeded the {timeout_seconds}s ceiling"
                + (" and ignored the stop signal" if run.abandoned else "")
            )
            structured = {}
        else:
            structured = dict(getattr(result, "structured", None) or {})
        run.self_reported_complete = bool(getattr(result, "completed", False))
        run.exit_code = int(getattr(result, "exit_code", -1) or 0) if result else -1
        run.degraded = bool(structured.get("degraded"))
        run.degraded_reason = str(structured.get("degraded_reason") or "")[:500]
        run.cost_usd = float(structured.get("total_cost_usd") or 0.0)
        run.input_tokens = int(structured.get("input_tokens") or 0)
        run.output_tokens = int(structured.get("output_tokens") or 0)
        run.turns = int(structured.get("turns") or 0)
        if getattr(result, "error", ""):
            run.error = str(result.error)[:500]
        if run.timed_out:
            run.error = (
                f"[timed out after {timeout_seconds}s] " + run.error
            ).strip()

        run.files = changed_files_since(task.base_commit, work_dir)
        run.files_changed = len(run.files)
        reference = {f.replace("\\", "/") for f in task.reference_files}
        run.reference_overlap = len(reference & set(run.files))

        t1 = time.monotonic()
        passed, verdict, feedback = grade_worktree(
            task,
            work_dir,
            run_conformance=run_conformance,
            compare_to_main=compare_to_main,
            budget_sec=_grade_budget(timeout_seconds),
        )
        run.grade_seconds = round(time.monotonic() - t1, 2)
        run.graded_pass = passed
        run.verdict = verdict
        run.gate_feedback = feedback[:1500]

        # A tree the executor never touched cannot pass: the gates are scoped to
        # the changed-file set, so an empty set means every gate is vacuously
        # green. Without this an executor that does nothing scores 100%.
        if run.files_changed == 0 and run.graded_pass:
            run.graded_pass = False
            run.verdict = "no_op"
            run.gate_feedback = (
                "Executor left the worktree unchanged; the gates were vacuously "
                "green because there was nothing to grade."
            )

        log(
            f"  {task.task_id}/{executor}: graded_pass={run.graded_pass} "
            f"self_reported={run.self_reported_complete} "
            f"files={run.files_changed} build={run.build_seconds:.0f}s "
            f"grade={run.grade_seconds:.0f}s cost=${run.cost_usd:.4f}"
        )
        return run
    finally:
        # An abandoned worker thread may still be writing into this tree.
        # Deleting it underneath the thread turns a slow executor into a stream
        # of unrelated I/O errors, so the tree is leaked deliberately and named.
        if work_dir is not None and not keep_worktree and not run.abandoned:
            remove_worktree(work_dir)
        elif run.abandoned:
            log(
                f"  {task.task_id}/{executor}: worktree left at {work_dir} — "
                "the executor thread is still running"
            )


# ---------------------------------------------------------------------------
# Aggregation + report
# ---------------------------------------------------------------------------


def _rate(numerator: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def summarize(runs: Sequence[ParityRun]) -> Dict[str, Any]:
    """Per-executor aggregates over a completed (or partial) run set."""
    by_executor: Dict[str, List[ParityRun]] = {}
    for run in runs:
        by_executor.setdefault(run.executor, []).append(run)

    summary: Dict[str, Any] = {}
    for executor, group in sorted(by_executor.items()):
        attempted = [r for r in group if r.status == "ok"]
        graded = [r for r in attempted if r.graded_pass is not None]
        passed = [r for r in graded if r.graded_pass]
        overclaimed = [
            r for r in attempted if r.self_reported_complete and r.graded_pass is not True
        ]
        build_times = [r.build_seconds for r in attempted if r.build_seconds > 0]
        costs = [r.cost_usd for r in attempted]
        summary[executor] = {
            "tasks": len(group),
            "attempted": len(attempted),
            "unavailable": len([r for r in group if r.status == "unavailable"]),
            "harness_errors": len([r for r in group if r.status == "harness_error"]),
            "graded": len(graded),
            "grader_errors": len(attempted) - len(graded),
            "gate_passes": len(passed),
            "gate_pass_rate": _rate(len(passed), len(graded)),
            "self_reports": len([r for r in attempted if r.self_reported_complete]),
            "self_report_rate": _rate(
                len([r for r in attempted if r.self_reported_complete]), len(attempted)
            ),
            "overclaimed": len(overclaimed),
            "no_op_runs": len([r for r in attempted if r.files_changed == 0]),
            "degraded_runs": len([r for r in attempted if r.degraded]),
            "timed_out_runs": len([r for r in attempted if r.timed_out]),
            "wall_clock_total_s": round(sum(build_times), 1) if build_times else 0.0,
            "wall_clock_mean_s": round(statistics.fmean(build_times), 1) if build_times else None,
            "wall_clock_median_s": round(statistics.median(build_times), 1) if build_times else None,
            "grade_total_s": round(sum(r.grade_seconds for r in attempted), 1),
            "cost_total_usd": round(sum(costs), 4),
            "cost_mean_usd": round(statistics.fmean(costs), 4) if costs else None,
            "cost_reported": any(c > 0 for c in costs),
            "input_tokens": sum(r.input_tokens for r in attempted),
            "output_tokens": sum(r.output_tokens for r in attempted),
            "mean_files_changed": (
                round(statistics.fmean([r.files_changed for r in attempted]), 2)
                if attempted else None
            ),
            "reference_overlap_total": sum(r.reference_overlap for r in attempted),
        }
    return summary


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:g}{suffix}"
    return f"{value}{suffix}"


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def render_markdown(report: Dict[str, Any]) -> str:
    """Render the results table published in docs/features/hgx-executor-parity.md."""
    summary = report.get("summary") or {}
    runs = report.get("runs") or []
    meta = report.get("meta") or {}
    executors = sorted(summary.keys())

    lines: List[str] = []
    lines.append("### Corpus")
    lines.append("")
    lines.append(
        f"`{meta.get('corpus_id', 'n/a')}` — {meta.get('task_count', 0)} tasks, "
        f"executors: {', '.join(executors) or 'none'}. "
        f"Build timeout {meta.get('timeout_seconds')}s, max_turns "
        f"{meta.get('max_turns')}, conformance "
        f"{'on' if meta.get('run_conformance') else 'off'}. "
        f"Platform `{meta.get('platform')}`, python {meta.get('python')}, "
        f"repo `{str(meta.get('repo_head') or '')[:9]}`."
    )
    lines.append("")
    if meta.get("module_budget_lifted"):
        budget = meta.get("module_budget_at_start") or {}
        lines.append(
            "> **Module-budget enforcement was suspended for this run** "
            "(`--lift-module-budget`). At start the "
            f"`generative_intelligence` cap for {budget.get('month')} stood at "
            f"{budget.get('spent_tokens')}/{budget.get('budget_tokens')} tokens "
            f"(`action: {budget.get('action')}`), which hard-stops every "
            "`LLMRouter` call and therefore the owned executor. `claude_cli` is "
            "not metered by that cap at all, so measuring under it would have "
            "compared a capability against a spent budget."
        )
        lines.append("")
    lines.append("### Results")
    lines.append("")
    header = ["Metric", *executors]
    rows = [
        ("Tasks attempted", "attempted", None),
        ("Gate-pass rate (harness verdict)", "gate_pass_rate", "pct"),
        ("Gate passes / graded", None, "passes"),
        ("Self-reported complete", "self_report_rate", "pct"),
        ("Reported done but gates failed", "overclaimed", None),
        ("Runs that changed no file", "no_op_runs", None),
        ("Degraded to prose", "degraded_runs", None),
        ("Hit the wall-clock ceiling", "timed_out_runs", None),
        ("Wall clock, mean (s)", "wall_clock_mean_s", None),
        ("Wall clock, median (s)", "wall_clock_median_s", None),
        ("Wall clock, total (s)", "wall_clock_total_s", None),
        ("Cost, total (USD)", "cost_total_usd", "usd"),
        ("Cost, mean per task (USD)", "cost_mean_usd", "usd"),
        ("Input tokens", "input_tokens", None),
        ("Output tokens", "output_tokens", None),
        ("Mean files changed", "mean_files_changed", None),
    ]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")
    for label, key, kind in rows:
        cells = []
        for executor in executors:
            data = summary.get(executor, {})
            if kind == "passes":
                cells.append(f"{data.get('gate_passes', 0)} / {data.get('graded', 0)}")
            elif kind == "pct":
                cells.append(_pct(data.get(key)))
            elif kind == "usd":
                value = data.get(key)
                if not data.get("cost_reported"):
                    cells.append("not reported")
                else:
                    cells.append("n/a" if value is None else f"${value:.4f}")
            else:
                cells.append(_fmt(data.get(key)))
        lines.append("| " + label + " | " + " | ".join(cells) + " |")

    lines.append("")
    lines.append("### Per task")
    lines.append("")
    lines.append(
        "| Task | " + " | ".join(f"{e} gate / self / files / s" for e in executors) + " |"
    )
    lines.append("|" + "|".join(["---"] * (len(executors) + 1)) + "|")
    task_ids: List[str] = []
    for run in runs:
        if run["task_id"] not in task_ids:
            task_ids.append(run["task_id"])
    for task_id in task_ids:
        cells = []
        for executor in executors:
            match = next(
                (r for r in runs if r["task_id"] == task_id and r["executor"] == executor),
                None,
            )
            if not match:
                cells.append("—")
                continue
            if match["status"] != "ok":
                cells.append(match["status"])
                continue
            gate = {True: "PASS", False: "fail", None: "grader-error"}[match["graded_pass"]]
            self_r = "yes" if match["self_reported_complete"] else "no"
            cells.append(
                f"{gate} / {self_r} / {match['files_changed']} / "
                f"{match['build_seconds']:.0f}"
            )
        lines.append(f"| `{task_id}` | " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def write_json(path: Path, report: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")


def build_report(
    runs: Sequence[ParityRun], meta: Dict[str, Any]
) -> Dict[str, Any]:
    return {
        "meta": meta,
        "summary": summarize(runs),
        "runs": [asdict(r) for r in runs],
    }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def run_parity(
    tasks: Sequence[CorpusTask],
    executors: Sequence[str] = DEFAULT_EXECUTORS,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_turns: int = DEFAULT_MAX_TURNS,
    run_conformance: bool = False,
    compare_to_main: bool = True,
    keep_worktrees: bool = False,
    lift_module_budget: bool = False,
    llm_function: str = "",
    out_path: Optional[Path] = None,
    corpus_id: str = "",
    log: Callable[[str], None] = lambda _m: None,
) -> Dict[str, Any]:
    """Replay every task through every executor, task-major.

    Task-major (not executor-major) so a run interrupted halfway still holds a
    comparable A/B pair for every task it finished, rather than a complete
    picture of one executor and nothing for the other.

    Results are flushed to *out_path* after every replay: these runs take
    minutes each, and a crash at task nine must not discard the first eight.
    """
    meta = {
        "corpus_id": corpus_id,
        "task_count": len(tasks),
        "executors": list(executors),
        "timeout_seconds": timeout_seconds,
        "max_turns": max_turns,
        "run_conformance": run_conformance,
        "compare_to_main": compare_to_main,
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "repo_head": (_git(["rev-parse", "HEAD"], REPO_ROOT, timeout=30).stdout or "").strip(),
        "llm_function": llm_function or "(adapter default)",
        "module_budget_lifted": lift_module_budget,
        "module_budget_at_start": module_budget_status(),
    }
    runs: List[ParityRun] = []
    with lifted_module_budget(active=lift_module_budget):
        if lift_module_budget:
            log(
                "  module-budget enforcement suspended IN THIS PROCESS for the "
                "benchmark — recorded in meta.module_budget_lifted"
            )
        for task in tasks:
            for executor in executors:
                log(f"[{task.task_id}] {executor} …")
                run = run_one(
                    task,
                    executor,
                    timeout_seconds=timeout_seconds,
                    max_turns=max_turns,
                    run_conformance=run_conformance,
                    compare_to_main=compare_to_main,
                    keep_worktree=keep_worktrees,
                    llm_function=llm_function,
                    log=log,
                )
                runs.append(run)
                if out_path:
                    write_json(out_path, build_report(runs, meta))
    return build_report(runs, meta)


def _select(tasks: List[CorpusTask], only: str, limit: int) -> List[CorpusTask]:
    if only:
        wanted = [t.strip() for t in only.split(",") if t.strip()]
        by_id = {t.task_id: t for t in tasks}
        unknown = [w for w in wanted if w not in by_id]
        if unknown:
            raise SystemExit(f"unknown corpus task id(s): {unknown}")
        tasks = [by_id[w] for w in wanted]
    if limit and limit > 0:
        tasks = tasks[:limit]
    return tasks


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark the owned executor against claude_cli over a fixed corpus "
            "of merged kanban tasks. Reports only — changes no default."
        )
    )
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS_PATH))
    parser.add_argument("--list", action="store_true", help="list the corpus and exit")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve corpus, adapters and base commits without building anything",
    )
    parser.add_argument("--run", action="store_true", help="execute the benchmark")
    parser.add_argument(
        "--executors",
        default=",".join(DEFAULT_EXECUTORS),
        help="comma-separated adapter names",
    )
    parser.add_argument("--tasks", default="", help="comma-separated corpus task ids")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    parser.add_argument("--conformance", action="store_true")
    parser.add_argument("--no-compare-to-main", action="store_true")
    parser.add_argument("--keep-worktrees", action="store_true")
    parser.add_argument(
        "--lift-module-budget",
        action="store_true",
        help=(
            "suspend module-budget enforcement in this process so the owned "
            "executor can be measured when the monthly cap is already spent; "
            "always recorded in the report metadata"
        ),
    )
    parser.add_argument(
        "--llm-function",
        default="",
        help=(
            "override the router FUNCTION the owned executor routes by "
            "(never a model id). Empty means the adapter default, i.e. what "
            "this host is actually configured to build with."
        ),
    )
    parser.add_argument("--no-dotenv", action="store_true")
    parser.add_argument("--out", default="", help="write the JSON report here")
    parser.add_argument("--report", default="", help="write the markdown table here")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    quiet = args.json
    def log(message: str) -> None:
        if not quiet:
            print(message, flush=True)

    corpus_path = Path(args.corpus)
    with open(corpus_path, "r", encoding="utf-8", newline="") as fh:
        corpus_id = str((yaml.safe_load(fh) or {}).get("corpus_id") or "")
    tasks = _select(load_corpus(corpus_path), args.tasks, args.limit)
    executors = [e.strip() for e in args.executors.split(",") if e.strip()]

    if not args.no_dotenv:
        load_env_file()

    if args.list or args.dry_run:
        payload: Dict[str, Any] = {
            "corpus_id": corpus_id,
            "corpus": str(corpus_path),
            "tasks": [
                {
                    "task_id": t.task_id,
                    "title": t.title,
                    "task_type": t.task_type,
                    "base_commit": t.base_commit,
                    "base_commit_present": commit_exists(t.base_commit),
                    "reference_files": len(t.reference_files),
                    "prompt_chars": len(t.instruction()),
                }
                for t in tasks
            ],
        }
        if args.dry_run:
            adapters: Dict[str, Any] = {}
            for name in executors:
                try:
                    from tools.agents.registry import get_adapter

                    adapter = get_adapter(name)
                    adapters[name] = {
                        "registered": True,
                        "available": bool(adapter.available()),
                    }
                except Exception as exc:  # noqa: BLE001
                    adapters[name] = {"registered": False, "error": str(exc)}
            payload["adapters"] = adapters
            payload["module_budget"] = module_budget_status()
            payload["worktree_paths"] = {
                name: str(replay_worktree_path(tasks[0].task_id, name))
                for name in executors
            } if tasks else {}
        print(json.dumps(payload, indent=2))
        return 0

    if not args.run:
        parser.error("pass --run to execute, or --list / --dry-run")

    out_path = Path(args.out) if args.out else None
    report = run_parity(
        tasks,
        executors,
        timeout_seconds=args.timeout,
        max_turns=args.max_turns,
        run_conformance=args.conformance,
        compare_to_main=not args.no_compare_to_main,
        keep_worktrees=args.keep_worktrees,
        lift_module_budget=args.lift_module_budget,
        llm_function=args.llm_function,
        out_path=out_path,
        corpus_id=corpus_id,
        log=log,
    )
    if out_path:
        write_json(out_path, report)
    markdown = render_markdown(report)
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(markdown)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(markdown)
    return 0


__all__ = [
    "CorpusTask",
    "ParityRun",
    "BENCHMARK_SYSTEM_PROMPT",
    "DEFAULT_CORPUS_PATH",
    "DEFAULT_EXECUTORS",
    "build_report",
    "changed_files_since",
    "commit_exists",
    "create_replay_worktree",
    "grade_worktree",
    "invoke_bounded",
    "lifted_module_budget",
    "load_corpus",
    "load_env_file",
    "module_budget_status",
    "remove_worktree",
    "render_markdown",
    "replay_worktree_path",
    "run_one",
    "run_parity",
    "summarize",
    "write_json",
]


if __name__ == "__main__":
    raise SystemExit(main())
