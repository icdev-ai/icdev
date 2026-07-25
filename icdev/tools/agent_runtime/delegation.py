# CUI // SP-CTI
"""Subagent delegation for the standalone agent runtime (sag-del-01).

:func:`delegate_task` spawns an **isolated child SAG runtime** — its own chat
context (``ctx_id``), a restricted toolset (sag-reg-02 bundles), inherited budget
caps, and a hard wall-clock timeout — to work a bounded sub-goal, then returns a
structured summary the parent loop can consume as a tool result.

Isolation is by **subprocess**: each child runs
``python -m tools.agent_runtime.delegation --child`` with the job on stdin, so a
child cannot race the parent on shared in-process state and a runaway child is
killed when the timeout expires. :func:`delegate_batch` fans out up to N children
concurrently (thread pool over independent subprocesses).

Bounded recursion (depth / re-delegation policy):

- ``role="leaf"`` (default): the child MUST NOT re-delegate.
- ``role="orchestrator"``: the child MAY delegate, but the chain is capped at
  :data:`MAX_ORCHESTRATOR_DEPTH` (2) hops total.

The policy is carried to children through the ``ICDEV_SAG_DEPTH`` /
``ICDEV_SAG_CAN_DELEGATE`` environment and enforced at the top of
:func:`delegate_task`, so it holds across the process boundary. Budget caps
(``max_total_tokens`` / ``max_cost_usd``) are passed into the child runtime, which
forwards them to ``run_agent_loop`` — the same budget mechanics the parent uses.

This module is intentionally thin: it composes the existing runtime, toolset,
and agent-loop primitives and leaves *orchestration policy* (which sub-goals to
spawn, how to combine results) to the caller — ACE co-workers and the A2A agents
already own richer multi-role orchestration.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.delegation")

DEFAULT_TIMEOUT_S = 300
MAX_ORCHESTRATOR_DEPTH = 2
DEFAULT_MAX_CONCURRENCY = 4
ROLE_LEAF = "leaf"
ROLE_ORCHESTRATOR = "orchestrator"
_ROLES = (ROLE_LEAF, ROLE_ORCHESTRATOR)

# Emitted by the child so the parent can pluck the result line out of stdout even
# if other libraries print to stdout.
_RESULT_SENTINEL = "__SAG_DELEGATE_RESULT__"


def _find_repo_root(start: Path) -> Path:
    """Walk upward to a repo sentinel (works from any namespace / worktree)."""
    for parent in [start, *start.parents]:
        if any((parent / s).exists() for s in ("pyproject.toml", ".git", "CLAUDE.md")):
            return parent
    return start


_REPO_ROOT = _find_repo_root(Path(__file__).resolve().parent)


# ---------------------------------------------------------------------------
# Depth / re-delegation policy
# ---------------------------------------------------------------------------


def _current_depth() -> int:
    try:
        return int(os.environ.get("ICDEV_SAG_DEPTH", "0"))
    except ValueError:
        return 0


def _can_delegate_here() -> tuple[bool, str]:
    """Whether the *current* process is permitted to delegate.

    Returns ``(allowed, reason_if_not)``. The top-level agent (depth 0) may always
    delegate; a child obeys the ``ICDEV_SAG_CAN_DELEGATE`` flag it was handed and
    the absolute depth cap.
    """
    depth = _current_depth()
    if depth >= MAX_ORCHESTRATOR_DEPTH:
        return False, f"delegation depth limit ({MAX_ORCHESTRATOR_DEPTH}) reached"
    if depth > 0 and os.environ.get("ICDEV_SAG_CAN_DELEGATE", "0") != "1":
        return False, "re-delegation not permitted for a leaf child"
    return True, ""


def _result(
    goal: str,
    role: str,
    status: str,
    *,
    summary: str = "",
    content: str = "",
    context_id: str = "",
    usage: dict[str, Any] | None = None,
    error: str | None = None,
    duration_ms: int = 0,
) -> dict[str, Any]:
    return {
        "status": status,
        "goal": goal,
        "role": role,
        "summary": summary,
        "content": content,
        "context_id": context_id,
        "usage": usage or {},
        "error": error,
        "duration_ms": duration_ms,
    }


# ---------------------------------------------------------------------------
# Parent side — spawn a child subprocess
# ---------------------------------------------------------------------------


def delegate_task(
    goal: str,
    *,
    context: str = "",
    toolsets: list[str] | None = None,
    role: str = ROLE_LEAF,
    timeout: int = DEFAULT_TIMEOUT_S,
    max_total_tokens: int | None = None,
    max_cost_usd: float | None = None,
    max_iterations: int = 12,
    user_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Delegate ``goal`` to an isolated child SAG runtime and return its summary.

    Args:
        goal: The sub-task for the child to accomplish.
        context: Extra grounding appended to the child's prompt.
        toolsets: sag-reg-02 bundle names restricting the child's tools (e.g.
            ``["file"]``). Empty → the small read-only starter toolset.
        role: ``"leaf"`` (child cannot re-delegate) or ``"orchestrator"`` (child
            may delegate, bounded to depth ``MAX_ORCHESTRATOR_DEPTH``).
        timeout: Wall-clock cap (s); the child is killed when it elapses.
        max_total_tokens / max_cost_usd / max_iterations: Budget caps forwarded to
            the child's ``run_agent_loop`` (inherited from the parent by the caller).
        user_id / tenant_id: Identity for the child's chat context (RLS).

    Returns:
        A structured dict — never raises. ``status`` is one of ``ok`` / ``error``
        / ``timeout`` / ``refused``; ``summary`` holds the child's final answer.
    """
    role = role if role in _ROLES else ROLE_LEAF

    allowed, reason = _can_delegate_here()
    if not allowed:
        logger.info("delegate_task refused: %s", reason)
        return _result(goal, role, "refused", error=reason)

    job = {
        "goal": goal,
        "context": context,
        "toolsets": list(toolsets or []),
        "max_total_tokens": max_total_tokens,
        "max_cost_usd": max_cost_usd,
        "max_iterations": max_iterations,
        "user_id": user_id,
        "tenant_id": tenant_id,
    }

    depth = _current_depth()
    child_depth = depth + 1
    child_can_delegate = (
        "1"
        if role == ROLE_ORCHESTRATOR and child_depth < MAX_ORCHESTRATOR_DEPTH
        else "0"
    )

    env = dict(os.environ)
    env["ICDEV_SAG_DEPTH"] = str(child_depth)
    env["ICDEV_SAG_CAN_DELEGATE"] = child_can_delegate
    root = str(_REPO_ROOT)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root + (os.pathsep + existing_pp if existing_pp else "")

    cmd = [sys.executable, "-m", "tools.agent_runtime.delegation", "--child"]
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            input=json.dumps(job),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=root,
        )
    except subprocess.TimeoutExpired:
        dur = int((time.time() - t0) * 1000)
        logger.warning("delegate_task timed out after %ss: %s", timeout, goal[:80])
        return _result(
            goal, role, "timeout", error=f"child exceeded {timeout}s", duration_ms=dur
        )
    except Exception as exc:  # noqa: BLE001 — never raise into the parent loop
        dur = int((time.time() - t0) * 1000)
        return _result(goal, role, "error", error=f"spawn failed: {exc}", duration_ms=dur)

    dur = int((time.time() - t0) * 1000)
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-500:]
        return _result(
            goal, role, "error",
            error=f"child exited {proc.returncode}: {tail}", duration_ms=dur,
        )

    line = _extract_result_line(proc.stdout or "")
    if line is None:
        tail = (proc.stdout or "")[-300:]
        return _result(
            goal, role, "error",
            error=f"no result from child; stdout tail: {tail}", duration_ms=dur,
        )
    try:
        data = json.loads(line)
    except Exception as exc:  # noqa: BLE001
        return _result(
            goal, role, "error",
            error=f"unparseable child result: {exc}", duration_ms=dur,
        )
    data["duration_ms"] = dur
    data.setdefault("goal", goal)
    data.setdefault("role", role)
    return data


def _extract_result_line(stdout: str) -> str | None:
    """Return the JSON payload following the last result sentinel, or None."""
    for raw in reversed(stdout.splitlines()):
        if raw.startswith(_RESULT_SENTINEL):
            return raw[len(_RESULT_SENTINEL):]
    return None


def delegate_batch(
    tasks: list[dict[str, Any]],
    *,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> list[dict[str, Any]]:
    """Delegate several sub-tasks in parallel (order-preserving results).

    Each element of ``tasks`` is a kwargs dict for :func:`delegate_task` (must
    include ``goal``). Up to ``max_concurrency`` children run at once; each is its
    own isolated subprocess.
    """
    if not tasks:
        return []
    workers = max(1, min(max_concurrency, len(tasks)))

    def _one(task: dict[str, Any]) -> dict[str, Any]:
        if "goal" not in task:
            return _result("", ROLE_LEAF, "error", error="task missing 'goal'")
        return delegate_task(**task)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_one, tasks))


# ---------------------------------------------------------------------------
# Child side — run one turn in an isolated runtime
# ---------------------------------------------------------------------------


def _run_child(job: dict[str, Any]) -> dict[str, Any]:
    """Construct an isolated :class:`AgentRuntime`, run one turn, summarise."""
    goal = str(job.get("goal") or "")
    if not goal:
        return _result("", ROLE_LEAF, "error", error="empty goal")
    context = str(job.get("context") or "")
    toolsets = list(job.get("toolsets") or [])

    from tools.agent_runtime.runtime import AgentRuntime
    from tools.agent_runtime.sessions import ensure_chat_tables

    ensure_chat_tables()

    kwargs: dict[str, Any] = {"max_iterations": int(job.get("max_iterations", 12) or 12)}
    if job.get("max_total_tokens") is not None:
        kwargs["max_total_tokens"] = job["max_total_tokens"]
    if job.get("max_cost_usd") is not None:
        kwargs["max_cost_usd"] = job["max_cost_usd"]
    if job.get("user_id"):
        kwargs["user_id"] = job["user_id"]
    if job.get("tenant_id") is not None:
        kwargs["tenant_id"] = job["tenant_id"]

    runtime = AgentRuntime(**kwargs)

    if toolsets:
        try:
            runtime.use_toolset(toolsets)
        except Exception as exc:  # noqa: BLE001
            return _result(
                goal, ROLE_LEAF, "error",
                context_id=runtime.session.context_id,
                error=f"toolset build failed: {exc}",
            )

    prompt = goal if not context else f"{goal}\n\n## Context\n{context}"
    try:
        result = runtime.run_turn(prompt)
    except Exception as exc:  # noqa: BLE001
        return _result(
            goal, ROLE_LEAF, "error",
            context_id=runtime.session.context_id,
            error=f"turn failed: {exc}",
        )

    content = getattr(result, "final_content", "") or ""
    return _result(
        goal, ROLE_LEAF, "ok",
        summary=content.strip(),
        content=content,
        context_id=runtime.session.context_id,
        usage=runtime.session.usage(),
    )


# ---------------------------------------------------------------------------
# Runtime tool wrapper — expose delegation as a callable tool
# ---------------------------------------------------------------------------


def build_delegate_tool(
    *,
    role: str = ROLE_LEAF,
    toolsets: list[str] | None = None,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> tuple[dict[str, Any], Any]:
    """Return an ``(openai_tool_schema, handler)`` pair exposing ``delegate_task``.

    Lets a caller register delegation as a tool on an :class:`AgentRuntime` so the
    model can spin off a bounded sub-task mid-turn; the handler returns the
    child's summary as the tool result string. ``role`` / ``toolsets`` / ``timeout``
    are the defaults applied when the model does not override them.
    """
    schema = {
        "type": "function",
        "is_read_only": False,
        "function": {
            "name": "delegate_task",
            "description": (
                "Delegate a self-contained sub-task to an isolated child agent "
                "with its own session and a restricted toolset. Returns the "
                "child's final summary. Use for focused, parallelizable work; the "
                "child cannot see this conversation beyond the goal/context you "
                "pass."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "The sub-task to accomplish.",
                    },
                    "context": {
                        "type": "string",
                        "description": "Optional grounding for the child.",
                    },
                },
                "required": ["goal"],
            },
        },
    }

    def _handler(tool_input: dict[str, Any], _stop: Any = None) -> str:
        goal = str(tool_input.get("goal") or "").strip()
        if not goal:
            return "error: 'goal' is required"
        res = delegate_task(
            goal,
            context=str(tool_input.get("context") or ""),
            toolsets=toolsets,
            role=role,
            timeout=timeout,
        )
        if res.get("status") != "ok":
            return f"delegation {res.get('status')}: {res.get('error') or 'failed'}"
        return res.get("summary") or res.get("content") or "(child returned no output)"

    return schema, _handler


# ---------------------------------------------------------------------------
# Entry point — python -m tools.agent_runtime.delegation --child
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    _argv = list(sys.argv[1:] if argv is None else argv)
    if "--child" not in _argv:
        print(
            "usage: python -m tools.agent_runtime.delegation --child "
            "(job JSON on stdin)",
            file=sys.stderr,
        )
        return 2
    raw = sys.stdin.read()
    try:
        job = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        data = _result("", ROLE_LEAF, "error", error=f"bad job json: {exc}")
        print(_RESULT_SENTINEL + json.dumps(data))
        return 1
    data = _run_child(job)
    print(_RESULT_SENTINEL + json.dumps(data))
    return 0 if data.get("status") == "ok" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
