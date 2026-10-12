# CUI // SP-CTI
"""kpr-watch-27: a red kanban PR whose worker has exited gets ONE repair session.

THE DEFECT. ``pr_watcher`` answers a red kanban PR by appending a resume line to
``.tmp/kanban/messages/<task>.jsonl`` and then waits for something to read it.
kpr-watch-19 gave that queue a CONSUMER (``tools/hooks/resume_inbox.py``, wired
to ``SessionStart`` / ``PostToolUse`` on the claude_cli path) -- but nothing
STARTS the session that would consume it. Every resume is post-run (the watcher
only resumes a task whose PR is already open), so the worker that could have
read it has always exited, and the scheduler never dispatches a ``pr_opened``
task. MEASURED 2026-10-11 on PR #2430 (omx-guard-05): one resume at 16:17, one
HITL escalation, then an ``undelivered hold`` every ~35 s for 4+ hours until a
human fixed it. Since 2026-09-05 the watcher log holds 52 ``resume undelivered``
escalations across 26 distinct tasks.

THE FIX, scheduler side (``tools/genesis/reflexes/kanban.py::_dispatch_pr_repairs``
owns the session; this module owns the parts that are not scheduler state):

* :func:`claim_messages` takes the queue ATOMICALLY (rename, then read), so a
  resume the watcher appends mid-dispatch lands in a fresh queue file for the
  next sweep instead of being lost or double-delivered.
* :func:`render_prompt` puts the claimed messages IN THE DISPATCH PROMPT. That is
  what makes delivery adapter-agnostic: opencode / pi sessions have no
  SessionStart inbox, but every adapter reads its prompt.
* :meth:`Claim.commit` writes the drain receipt (``resume_delivery.record_drain``)
  only AFTER the session spawned -- a receipt for words nobody got would be
  kpr-watch-13's defect under this card's name. :meth:`Claim.release` puts the
  messages back when the spawn failed.
* :func:`record_dispatch` / :func:`in_cooldown` -- a JSONL ledger beside the
  queue, so "one repair per resume, spaced by the watcher's cooldown" survives a
  scheduler re-exec. The cooldown defaults to
  ``pr_watcher.RESUME_COOLDOWN_SECONDS``; the watcher's ``max_resume_cycles``
  still bounds how many resumes (and so repairs) a PR can ever get.

What it deliberately does NOT do: move the task (it stays ``pr_opened`` --
kpr-watch-11 status churn), open a PR or a branch (the session works detached on
``origin/kanban/<id>`` and pushes to that same ref), or change pr_watcher's
budget/cooldown/escalation logic.

Survey::

    python -m tools.kanban.repair_dispatch --json     # ledger: repairs per task
    python -m tools.ci.resume_delivery --survey        # receipts should rise
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.ci import resume_delivery

#: The reader name stamped on the drain receipt and the ledger rows.
SOURCE = "repair_dispatch"

#: Fallback when pr_watcher cannot be imported; mirrors its constant.
_DEFAULT_COOLDOWN_SECONDS = 600


def cooldown_seconds() -> int:
    """Seconds between two repair dispatches for ONE task.

    ``KANBAN_REPAIR_COOLDOWN_SECONDS`` overrides; otherwise the watcher's own
    resume cooldown -- one spacing for both halves, so the repair cadence can
    never run ahead of the resume cadence it serves.
    """
    raw = os.environ.get("KANBAN_REPAIR_COOLDOWN_SECONDS", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    try:
        from tools.ci.pr_watcher import RESUME_COOLDOWN_SECONDS

        return int(RESUME_COOLDOWN_SECONDS)
    except Exception:  # noqa: BLE001 -- the fallback is the same number
        return _DEFAULT_COOLDOWN_SECONDS


# -- the ledger --------------------------------------------------------------


def ledger_path() -> Optional[Path]:
    """``.tmp/kanban/repair_dispatches.jsonl``, a sibling of the queue dir."""
    qd = resume_delivery.queue_dir()
    if qd is None:
        return None
    return qd.parent / "repair_dispatches.jsonl"


def read_ledger(task_id: Optional[str] = None) -> List[dict]:
    """Every ledger row (optionally for one task), oldest first. Never raises."""
    lp = ledger_path()
    if lp is None or not lp.exists():
        return []
    out: List[dict] = []
    try:
        with open(lp, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(row, dict) and (
                        task_id is None or row.get("task_id") == task_id):
                    out.append(row)
    except OSError:
        return []
    return out


def record_dispatch(task_id: str, *, adapter: str, worktree: str,
                    messages: int, pr_url: str = "") -> dict:
    """Append one ledger row for a repair session that STARTED. Never raises."""
    entry = {
        "task_id": task_id,
        "dispatched_at": datetime.now(timezone.utc).isoformat(),
        "adapter": adapter,
        "worktree": worktree,
        "messages": int(messages),
        "pr_url": pr_url,
        "pid": os.getpid(),
    }
    lp = ledger_path()
    if lp is None:
        return {"recorded": False, "entry": entry}
    try:
        lp.parent.mkdir(parents=True, exist_ok=True)
        with open(lp, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        return {"recorded": True, "entry": entry}
    except OSError as exc:
        return {"recorded": False, "entry": entry, "reason": str(exc)}


def _parse(ts: Any) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def last_dispatch_at(task_id: str) -> Optional[datetime]:
    stamps = [_parse(r.get("dispatched_at")) for r in read_ledger(task_id)]
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None


def in_cooldown(task_id: str, now: Optional[datetime] = None) -> bool:
    """True when this task had a repair dispatched inside the cooldown."""
    last = last_dispatch_at(task_id)
    if last is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - last).total_seconds() < cooldown_seconds()


# -- the queue ---------------------------------------------------------------


def pending_resumes(task_id: str) -> List[dict]:
    """Undrained ``pr_watcher`` messages for a task (``[]`` when unreadable)."""
    return resume_delivery.pending_messages(task_id) or []


def _read_jsonl(path: Path) -> List[dict]:
    out: List[dict] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


@dataclass
class Claim:
    """Messages taken off a task's queue, not yet receipted."""

    task_id: str
    messages: List[dict] = field(default_factory=list)
    held_path: Optional[Path] = None
    settled: bool = False

    def commit(self) -> dict:
        """The session spawned: drop the held file and write the receipt."""
        if self.settled:
            return {"recorded": False, "reason": "already settled"}
        self.settled = True
        if self.held_path is not None:
            try:
                self.held_path.unlink()
            except OSError:
                pass
        return resume_delivery.record_drain(self.task_id, self.messages,
                                            reader=SOURCE)

    def release(self) -> bool:
        """The session did NOT spawn: put the messages back, oldest first."""
        if self.settled:
            return False
        self.settled = True
        qf = resume_delivery.queue_file(self.task_id)
        if qf is None:
            return False
        try:
            arrived: List[dict] = _read_jsonl(qf) if qf.exists() else []
            qf.parent.mkdir(parents=True, exist_ok=True)
            with open(qf, "w", encoding="utf-8") as fh:
                for m in list(self.messages) + arrived:
                    fh.write(json.dumps(m) + "\n")
            if self.held_path is not None:
                try:
                    self.held_path.unlink()
                except OSError:
                    pass
            return True
        except OSError:
            return False


def claim_messages(task_id: str) -> Claim:
    """Take every message on the task's queue (any sender), atomically.

    Rename first, read second: a line the watcher appends after the rename
    starts a NEW queue file, which the next sweep sees. Nothing is receipted
    here -- see :meth:`Claim.commit`.
    """
    claim = Claim(task_id=task_id)
    qf = resume_delivery.queue_file(task_id)
    if qf is None or not qf.exists():
        return claim
    held = qf.with_name(f"{qf.name}.{SOURCE}-{os.getpid()}")
    try:
        os.replace(qf, held)
    except OSError:
        return claim
    claim.held_path = held
    try:
        claim.messages = _read_jsonl(held)
    except OSError:
        claim.release()
        claim.messages = []
    return claim


# -- the prompt --------------------------------------------------------------


def render_prompt(task_id: str, title: str, messages: List[dict], *,
                  branch: str = "", pr_url: str = "") -> str:
    """The repair session's whole instruction, messages included.

    The inbox block is ``resume_inbox.render`` -- one rendering for both
    delivery windows, so a message reads the same in a prompt as in a hook.
    """
    from tools.hooks.resume_inbox import render

    branch = branch or f"kanban/{task_id}"
    inbox = render(task_id, messages, source=SOURCE)
    pr_line = f"PR: {pr_url}\n" if pr_url else ""
    return (
        f"# PR repair: {task_id} -- {title}\n\n"
        f"This task's PR is OPEN and needs repair. The worker that built it has "
        f"exited; you are the repair session (kpr-watch-27).\n"
        f"Branch: {branch}\n{pr_line}\n"
        f"Your working directory is a DETACHED checkout of origin/{branch}.\n\n"
        f"{inbox}\n\n"
        f"## What to do\n"
        f"1. Read the message(s) above -- they describe why the PR is not "
        f"merging (failing CI checks, conflicts). Reproduce the failure locally "
        f"(run the named tests / checks) before changing anything.\n"
        f"2. Fix the cause in this checkout. Bound your edit scope to the "
        f"failure.\n"
        f"3. Commit, then push to the SAME branch:\n"
        f"   git push origin HEAD:refs/heads/{branch}\n"
        f"   If the push is rejected, `git fetch origin {branch}` and rebase onto "
        f"origin/{branch}, then push again. NEVER force-push.\n\n"
        f"## Do NOT\n"
        f"- open a new PR or create a new branch -- the existing PR picks up "
        f"your push;\n"
        f"- move the kanban task (it stays pr_opened; pr_watcher merges it once "
        f"CI is green);\n"
        f"- touch unrelated files.\n"
    )


# -- survey ------------------------------------------------------------------


def survey() -> Dict[str, Any]:
    rows = read_ledger()
    per_task: Dict[str, int] = {}
    for r in rows:
        tid = str(r.get("task_id") or "")
        if tid:
            per_task[tid] = per_task.get(tid, 0) + 1
    return {
        "ledger": str(ledger_path() or ""),
        "repairs": len(rows),
        "tasks": len(per_task),
        "per_task": dict(sorted(per_task.items())),
        "last": rows[-1] if rows else None,
        "cooldown_seconds": cooldown_seconds(),
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--json", action="store_true", dest="json_out")
    args = ap.parse_args(argv)
    data = survey()
    if args.json_out:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(f"repair dispatches: {data['repairs']} across {data['tasks']} task(s)"
              f" (cooldown {data['cooldown_seconds']}s)")
        for tid, n in data["per_task"].items():
            print(f"  {tid}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
