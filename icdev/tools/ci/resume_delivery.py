# CUI // SP-CTI
"""kpr-watch-13: did a pr_watcher resume ever REACH anything?

THE DEFECT. ``pr_watcher._send_resume`` calls
:func:`tools.airgap.hook_compat.queue_message`, which APPENDS ONE JSONL LINE to
``.tmp/kanban/messages/<task-id>.jsonl`` and returns. The audit row the watcher
then wrote said ``injected resume context`` -- a sentence about a file write,
phrased as a sentence about an agent. Nothing on this deployment has ever read
one of those lines.

MEASURED on the live checkout and the live PG board, 2026-09-06 02:47 UTC::

    queue files under .tmp/kanban/messages/              187
    files holding UNDRAINED `sender: pr_watcher` lines   186
    undrained pr_watcher messages                        851
    lifetime `pr_watcher.resume` audit rows              849

A drain DELETES the file, so a drain leaves no trace and cannot be counted
directly -- but the count ON DISK EXCEEDS the count EVER RECORDED, so no
recorded resume has been drained. Both sides move on a live board (the same
inequality read 186 files / 849 messages / 847 rows six hours earlier); the
INEQUALITY carries the argument, and every new resume only widens it. Quote a
reading with its instant -- one figure off a live board is not a measurement.

WHY IT MATTERS. ``max_resume_cycles_per_task: 5`` is a budget of ATTEMPTS and
``RESUME_COOLDOWN_SECONDS = 600`` spaces them, both because #1742/#1744 burned
five cycles in three minutes at poll speed. If nothing reads the message, all
five are one thing repeated: a write to a dead file. The escalation that follows
is then recorded as ``manual intervention required`` after FIVE ATTEMPTS THAT
WERE NEVER MADE.

--------------------------------------------------------------------------------
WHAT THIS MODULE DOES, AND WHAT IT DELIBERATELY DOES NOT
--------------------------------------------------------------------------------

It makes DELIVERY MEASURABLE and says so on the audit row. It does NOT deliver.

There is deliberately no actuator here -- nothing re-dispatches a worker, nothing
promotes a wake, nothing raises ``max_resume_cycles_per_task`` or lowers
``RESUME_COOLDOWN_SECONDS``. **More undelivered messages is not more attempts**,
and a delivery path that re-dispatches a worker on every resume is a
dispatch-rate change that owes its own fire-rate survey first (this repo's
standing rule). Building the actuator before the measurement would ship a second
capability nobody could prove was consumed -- the exact defect this platform
ships most.

TWO PRIMARY SIGNALS, NEVER MERGED:

* **the message itself.** A ``sender: pr_watcher`` line still sitting in
  ``<task>.jsonl`` is PROOF that injection was never read. Nothing deletes a
  line; :func:`tools.airgap.hook_compat.check_message_queue` deletes the FILE.
* **a drain receipt.** ``check_message_queue`` now appends one line to
  ``.tmp/kanban/message_receipts/<task-id>.jsonl`` naming what it drained, who
  drained it and when -- so from this change forward a drain STOPS being
  traceless. Absence of a receipt is not evidence of non-delivery for anything
  drained before it existed, which is why the third verdict had to exist.

THREE VERDICTS, and ``unmeasured`` is NEVER folded into either other::

    undelivered  a pr_watcher line for this task is STILL IN THE QUEUE.
                 Proven unread. THE FINDING.
    delivered    a receipt records a drain that carried pr_watcher messages
                 for this task. Proven read by a receipt-writing reader.
    unmeasured   the first injection for a task (nothing prior to judge); or
                 the queue file is gone with NO receipt -- a pre-receipt reader,
                 or a `.tmp` sweep, and those are indistinguishable. NOT a
                 clean bill of health, and never counted as delivered.

``.tmp`` is disposable BY DESIGN, so every way the evidence can go missing lands
in ``unmeasured``. A sweep can make this module say "I cannot tell". It can
never make it say "delivered".

WHY NOT JUST TRUST ``queue_message``'s RETURN VALUE. It returns
``{"queued": True}`` when ``write()`` did not raise. That is the claim the defect
is made of: one computation (the append succeeded) trusted twice, once as "a
line exists" and once as "an agent was told". The verdict here is re-derived from
the FILESYSTEM -- the message, or the receipt -- and shares no code with the
writer.

Report only. No ``--gate``: this measures a DEPLOYMENT'S message queue, not a
diff, and a survey with a ``--gate`` earns itself a ``|| true`` (kpr-fix-03).
Exit 2 means the survey could not be produced, which is never a clean survey.

Re-derive the headline::

    python -m tools.ci.resume_delivery --survey
    python -m tools.ci.resume_delivery --task <task-id> --json
    # or, straight off the disk, from the repo root:
    ls .tmp/kanban/messages/*.jsonl | wc -l
    grep -ho '"sender": "pr_watcher"' .tmp/kanban/messages/*.jsonl | wc -l
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

# The sender pr_watcher stamps on every resume it enqueues. Spelled once here
# and read by the watcher, so the writer and the auditor cannot disagree about
# whose messages are being counted.
RESUME_SENDER = "pr_watcher"

#: The three verdicts. `unmeasured` is a real answer and is never folded into
#: either of the others -- see the module docstring.
DELIVERED = "delivered"
UNDELIVERED = "undelivered"
UNMEASURED = "unmeasured"

VERDICTS = (DELIVERED, UNDELIVERED, UNMEASURED)


# -- locations ---------------------------------------------------------------
#
# The queue directory is hook_compat's, imported LAZILY and never respelled: two
# modules each naming `.tmp/kanban/messages` is two claims that can drift, and
# the whole point of this module is that a second copy of a fact is not
# corroboration. The lazy import is also what breaks the cycle -- hook_compat
# calls record_drain() from inside check_message_queue().


def queue_dir() -> Optional[Path]:
    """``.tmp/kanban/messages`` as hook_compat spells it, or None."""
    try:
        from tools.airgap.hook_compat import MESSAGE_QUEUE_DIR

        return Path(MESSAGE_QUEUE_DIR)
    except Exception:  # noqa: BLE001 -- an unreadable location is `unmeasured`
        return None


def receipt_dir() -> Optional[Path]:
    """``.tmp/kanban/message_receipts``, a SIBLING of the queue directory.

    Deliberately not a subdirectory: the queue is addressed as
    ``<dir>/<task-id>.jsonl`` and surveyed with a flat ``*.jsonl`` glob, so a
    receipts folder living inside it would be one oddly-named task away from
    being read as a queue.
    """
    qd = queue_dir()
    if qd is None:
        return None
    return qd.parent / "message_receipts"


def queue_file(task_id: str) -> Optional[Path]:
    qd = queue_dir()
    if qd is None or not task_id:
        return None
    return qd / f"{task_id}.jsonl"


def receipt_file(task_id: str) -> Optional[Path]:
    rd = receipt_dir()
    if rd is None or not task_id:
        return None
    return rd / f"{task_id}.jsonl"


# -- the drain receipt -------------------------------------------------------


def record_drain(
    task_id: str,
    messages: List[dict],
    *,
    reader: str = "",
) -> dict:
    """Record that ``messages`` were drained for ``task_id``. Never raises.

    Called by :func:`tools.airgap.hook_compat.check_message_queue` immediately
    AFTER the queue file is unlinked. It is what turns a traceless delete into
    evidence: before this, a drained file and a file that never existed were the
    same observation, so "nothing was ever read" could only be argued from an
    inequality across the whole board rather than proven per task.

    Best-effort by construction -- a receipt that could not be written must never
    break the drain a running agent depends on. A missing receipt reads as
    ``unmeasured``, never as non-delivery.
    """
    if not task_id or not messages:
        return {"recorded": False, "reason": "nothing drained"}
    rf = receipt_file(task_id)
    if rf is None:
        return {"recorded": False, "reason": "receipt directory unavailable"}
    senders: Dict[str, int] = {}
    for m in messages:
        if not isinstance(m, dict):
            continue
        key = str(m.get("sender") or "unknown")
        senders[key] = senders.get(key, 0) + 1
    entry = {
        "task_id": task_id,
        "drained_at": datetime.now(timezone.utc).isoformat(),
        "count": len(messages),
        "senders": senders,
        "reader": reader or os.environ.get("ICDEV_AGENT", "") or "unknown",
        "reader_pid": os.getpid(),
        "session_id": os.environ.get("ICDEV_SESSION_ID", ""),
    }
    try:
        rf.parent.mkdir(parents=True, exist_ok=True)
        with open(rf, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        return {"recorded": True, "path": str(rf), "entry": entry}
    except OSError as exc:
        return {"recorded": False, "reason": str(exc), "path": str(rf)}


def read_receipts(task_id: str) -> Optional[List[dict]]:
    """Every drain receipt for a task, oldest first. None = could not read."""
    rf = receipt_file(task_id)
    if rf is None:
        return None
    if not rf.exists():
        return []
    out: List[dict] = []
    try:
        with open(rf, "r", encoding="utf-8", errors="replace") as fh:
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
    except OSError:
        return None
    return out


def receipted_count(receipts: List[dict], sender: str = RESUME_SENDER) -> int:
    """How many messages FROM ``sender`` these receipts account for."""
    total = 0
    for r in receipts:
        senders = r.get("senders")
        if isinstance(senders, dict):
            try:
                total += int(senders.get(sender, 0) or 0)
            except (TypeError, ValueError):
                continue
    return total


# -- the queue itself --------------------------------------------------------


def pending_messages(
    task_id: str, sender: Optional[str] = RESUME_SENDER,
) -> Optional[List[dict]]:
    """Undrained messages from ``sender`` for a task. None = could not read.

    ``[]`` and ``None`` are different answers and are never merged: an empty
    queue is a measurement, an unreadable one is not.
    """
    qf = queue_file(task_id)
    if qf is None:
        return None
    if not qf.exists():
        return []
    out: List[dict] = []
    try:
        with open(qf, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(row, dict) and (
                    sender is None or row.get("sender") == sender
                ):
                    out.append(row)
    except OSError:
        return None
    return out


# -- the verdict -------------------------------------------------------------


@dataclass
class DeliveryVerdict:
    """What happened to the injections BEFORE the one being written now.

    A verdict about the CURRENT message is not available at send time and this
    module refuses to invent one: nothing has had the chance to read a line
    written a microsecond ago. What IS knowable, and what the escalation
    actually turns on, is whether the PREVIOUS injections were ever read.
    """

    verdict: str = UNMEASURED
    detail: str = ""
    pending: Optional[int] = None
    receipted: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "detail": self.detail,
            "pending": self.pending,
            "receipted": self.receipted,
        }


def probe_prior_delivery(
    task_id: str,
    *,
    had_prior_injection: bool,
    sender: str = RESUME_SENDER,
) -> DeliveryVerdict:
    """Verdict on this task's PREVIOUS injections. Read-only; never raises.

    MUST be called BEFORE the new message is appended, or the message inflates
    its own evidence -- a probe that counts the line it just wrote reports
    ``undelivered`` on the first injection of every task, forever, and would be
    a constant wearing the name of a measurement.
    """
    try:
        pending = pending_messages(task_id, sender=sender)
        receipts = read_receipts(task_id)
    except Exception:  # noqa: BLE001 -- a probe must never break the watch loop
        return DeliveryVerdict(UNMEASURED, "probe failed")

    if pending is None:
        return DeliveryVerdict(UNMEASURED, "queue unreadable")

    n_pending = len(pending)
    n_receipted = receipted_count(receipts, sender) if receipts is not None else None

    # PRIMARY EVIDENCE, and it outranks everything else: the line is still
    # there. This holds even with no prior injection recorded against this
    # cycle counter -- a message left by an earlier era is still one nobody read.
    if n_pending > 0:
        return DeliveryVerdict(
            UNDELIVERED,
            f"{n_pending} {sender} message(s) still unread in the queue",
            pending=n_pending,
            receipted=n_receipted,
        )

    if n_receipted:
        newest = ""
        for r in reversed(receipts or []):
            senders = r.get("senders")
            if isinstance(senders, dict) and senders.get(sender):
                newest = str(r.get("drained_at") or "")
                break
        return DeliveryVerdict(
            DELIVERED,
            f"{n_receipted} {sender} message(s) drained"
            + (f", newest {newest}" if newest else ""),
            pending=0,
            receipted=n_receipted,
        )

    if not had_prior_injection:
        return DeliveryVerdict(
            UNMEASURED,
            "first injection for this task -- nothing prior to judge",
            pending=0,
            receipted=n_receipted,
        )

    # The queue is empty and nothing recorded a drain. Either a reader older
    # than the receipt writer took it, or `.tmp` was swept. Those are
    # indistinguishable from here and BOTH are "cannot tell" -- the one thing
    # this branch must never do is guess `delivered`.
    return DeliveryVerdict(
        UNMEASURED,
        "queue empty and no drain receipt -- pre-receipt reader or .tmp sweep",
        pending=0,
        receipted=n_receipted,
    )


def summarize_delivery(
    task_id: str,
    *,
    injections: int,
    sender: str = RESUME_SENDER,
) -> Dict[str, Any]:
    """Retrospective split across a task's whole resume budget.

    Written onto the ESCALATION row, which is the only place the full budget is
    knowable -- and the only place it is quoted to a human as "manual
    intervention required after N attempts".

    ``unaccounted`` is the pre-receipt residue: injections that are neither on
    disk nor named by a receipt. It is reported BESIDE ``delivered``, never
    inside it.
    """
    pending = pending_messages(task_id, sender=sender)
    receipts = read_receipts(task_id)
    if pending is None:
        return {
            "injections": injections,
            "delivered": None,
            "undelivered": None,
            "unaccounted": None,
            "verdict": UNMEASURED,
            "detail": "queue unreadable",
        }
    n_pending = len(pending)
    n_receipted = receipted_count(receipts, sender) if receipts is not None else 0
    unaccounted = max(0, injections - n_pending - n_receipted)
    if n_pending > 0:
        verdict = UNDELIVERED
    elif n_receipted > 0:
        verdict = DELIVERED
    else:
        verdict = UNMEASURED
    return {
        "injections": injections,
        "delivered": n_receipted,
        "undelivered": n_pending,
        "unaccounted": unaccounted,
        "verdict": verdict,
        "detail": (
            f"{n_receipted} delivered, {n_pending} still unread, "
            f"{unaccounted} unaccounted of {injections} injection(s)"
        ),
    }


def escalation_note(summary: Dict[str, Any]) -> str:
    """One clause a human can read on a `manual intervention required` alert."""
    if summary.get("undelivered") is None:
        return "delivery unmeasured (queue unreadable)"
    delivered = summary.get("delivered") or 0
    undelivered = summary.get("undelivered") or 0
    unaccounted = summary.get("unaccounted") or 0
    injections = summary.get("injections") or 0
    if undelivered and not delivered:
        return (
            f"NONE of the {injections} injection(s) were ever read "
            f"({undelivered} still unread in the queue)"
        )
    parts = [f"{delivered} of {injections} injection(s) delivered"]
    if undelivered:
        parts.append(f"{undelivered} still unread")
    if unaccounted:
        parts.append(f"{unaccounted} unaccounted")
    return "; ".join(parts)


# -- the board-wide survey ---------------------------------------------------


@dataclass
class Survey:
    measured_at: str = ""
    queue_files: Optional[int] = None
    files_with_pending: Optional[int] = None
    pending_messages: Optional[int] = None
    receipt_files: Optional[int] = None
    receipted_messages: Optional[int] = None
    recorded_resumes: Optional[int] = None
    state: str = UNMEASURED
    notes: List[str] = field(default_factory=list)
    worst: List[Dict[str, Any]] = field(default_factory=list)

    def never_drained(self) -> Optional[bool]:
        """True when the disk count MEETS OR EXCEEDS every resume ever recorded.

        That inequality is the whole argument and it needs no drain counter: a
        drain deletes the file, so it leaves no trace -- but a board holding at
        least as many undrained pr_watcher messages as it has ever recorded
        resumes cannot have drained one of them.

        ``False`` is returned ONLY on positive evidence -- a receipt naming a
        real drain. "Fewer messages on disk than resumes recorded" is NOT that
        evidence: a `.tmp` sweep, or a survey run from a worktree whose own
        `.tmp` was never written to, produces exactly the same shortfall. That
        case is ``None``, and reading it as False would be this card's own
        defect (a reduction asserting more than its data supports).
        """
        if (self.receipted_messages or 0) > 0:
            return False
        if self.pending_messages is None or self.recorded_resumes is None:
            return None
        if self.recorded_resumes == 0:
            return None
        if self.pending_messages >= self.recorded_resumes:
            return True
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "measured_at": self.measured_at,
            "queue_files": self.queue_files,
            "files_with_pending": self.files_with_pending,
            "pending_messages": self.pending_messages,
            "receipt_files": self.receipt_files,
            "receipted_messages": self.receipted_messages,
            "recorded_resumes": self.recorded_resumes,
            "never_drained": self.never_drained(),
            "state": self.state,
            "notes": list(self.notes),
            "worst": list(self.worst),
        }


def _count_recorded_resumes(get_connection=None) -> Optional[int]:
    """Lifetime ``pr_watcher.resume`` rows, or None if the board is unreadable."""
    try:
        if get_connection is None:
            from tools.db.storage import get_connection as _gc

            get_connection = _gc
        conn = get_connection()
    except Exception:  # noqa: BLE001
        return None
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_trail WHERE action = %s",
            ("pr_watcher.resume",),
        ).fetchone()
        if row is None:
            return None
        rec = dict(row) if not isinstance(row, dict) else row
        return int(rec.get("n") or 0)
    except Exception:  # noqa: BLE001
        return None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def survey(
    sender: str = RESUME_SENDER,
    *,
    get_connection=None,
    top: int = 10,
) -> Survey:
    """The card's headline measurement, re-derivable in one command."""
    out = Survey(measured_at=datetime.now(timezone.utc).isoformat())
    qd = queue_dir()
    rd = receipt_dir()
    if qd is None:
        out.notes.append("queue directory unavailable -- nothing measured")
        out.recorded_resumes = _count_recorded_resumes(get_connection)
        return out
    if not qd.exists():
        out.queue_files = 0
        out.files_with_pending = 0
        out.pending_messages = 0
        out.notes.append(
            f"{qd} does not exist -- no message has ever been queued on THIS "
            "checkout, which is not the same as none being undelivered. The "
            "queue is per-checkout (hook_compat resolves it from its own "
            "file), so run this from the checkout pr_watcher runs in; a "
            "worktree has its own empty .tmp"
        )
    else:
        try:
            files = sorted(p for p in qd.glob("*.jsonl") if p.is_file())
        except OSError as exc:
            out.notes.append(f"queue directory unreadable: {exc}")
            out.recorded_resumes = _count_recorded_resumes(get_connection)
            return out
        out.queue_files = len(files)
        out.files_with_pending = 0
        out.pending_messages = 0
        per_task: List[Dict[str, Any]] = []
        for p in files:
            task_id = p.stem
            msgs = pending_messages(task_id, sender=sender)
            if not msgs:
                continue
            out.files_with_pending += 1
            out.pending_messages += len(msgs)
            oldest = ""
            for m in msgs:
                ts = str(m.get("ts") or "")
                if ts and (not oldest or ts < oldest):
                    oldest = ts
            per_task.append(
                {"task_id": task_id, "pending": len(msgs), "oldest_ts": oldest}
            )
        per_task.sort(key=lambda r: (-r["pending"], r["task_id"]))
        out.worst = per_task[:top]

    if rd is not None and rd.exists():
        try:
            rfiles = sorted(p for p in rd.glob("*.jsonl") if p.is_file())
            out.receipt_files = len(rfiles)
            total = 0
            for p in rfiles:
                receipts = read_receipts(p.stem) or []
                total += receipted_count(receipts, sender)
            out.receipted_messages = total
        except OSError as exc:
            out.notes.append(f"receipt directory unreadable: {exc}")
    else:
        out.receipt_files = 0
        out.receipted_messages = 0
        out.notes.append(
            "no drain receipts exist yet -- receipts start at kpr-watch-13, so "
            "0 here says nothing about drains that predate it"
        )

    out.recorded_resumes = _count_recorded_resumes(get_connection)
    if out.recorded_resumes is None:
        out.notes.append("audit_trail unreadable -- resume count not measured")

    if out.pending_messages is None or out.recorded_resumes is None:
        out.state = UNMEASURED
    elif out.pending_messages == 0 and out.recorded_resumes == 0:
        out.state = UNMEASURED
        out.notes.append(
            "no queued messages and no recorded resumes -- this deployment has "
            "no operating history, which is not a clean bill of health"
        )
    elif out.recorded_resumes > 0 and out.pending_messages >= out.recorded_resumes:
        out.state = UNDELIVERED
        out.notes.append(
            f"{out.pending_messages} undrained {sender} message(s) on disk vs "
            f"{out.recorded_resumes} lifetime pr_watcher.resume rows -- a drain "
            "deletes the file, so a count on disk that meets or exceeds every "
            "resume ever recorded proves none was drained"
        )
    elif out.pending_messages == 0 and (out.receipted_messages or 0) > 0:
        out.state = DELIVERED
    else:
        out.state = UNMEASURED
        out.notes.append(
            "fewer undrained messages than recorded resumes -- some were "
            "removed, and without receipts a drain cannot be told apart from a "
            "`.tmp` sweep"
        )
    return out


# -- CLI ---------------------------------------------------------------------


def _render(s: Survey) -> str:
    def n(v: Any) -> str:
        return "?" if v is None else str(v)

    lines = [
        "pr_watcher resume delivery (kpr-watch-13)",
        f"  measured_at            {s.measured_at}",
        f"  queue files            {n(s.queue_files)}",
        f"  files with pending     {n(s.files_with_pending)}",
        f"  undrained messages     {n(s.pending_messages)}",
        f"  receipt files          {n(s.receipt_files)}",
        f"  receipted messages     {n(s.receipted_messages)}",
        f"  lifetime resume rows   {n(s.recorded_resumes)}",
        f"  never drained          {n(s.never_drained())}",
        f"  state                  {s.state}",
    ]
    if s.worst:
        lines.append("  worst offenders:")
        for r in s.worst:
            lines.append(
                f"    {r['task_id']:<40} {r['pending']:>4} pending"
                + (f"  oldest {r['oldest_ts']}" if r["oldest_ts"] else "")
            )
    for note in s.notes:
        lines.append(f"  note: {note}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "kpr-watch-13 -- measure whether pr_watcher resume messages are "
            "ever read. Report only; never merges, dispatches or deletes."
        )
    )
    ap.add_argument("--survey", action="store_true",
                    help="board-wide measurement (the default)")
    ap.add_argument("--task", help="verdict for ONE task id")
    ap.add_argument("--sender", default=RESUME_SENDER)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.task:
            verdict = probe_prior_delivery(
                args.task, had_prior_injection=True, sender=args.sender
            )
            queue = summarize_delivery(args.task, injections=0, sender=args.sender)
            payload = {
                "task_id": args.task,
                "verdict": verdict.to_dict(),
                "queue": queue,
            }
            if args.json:
                print(json.dumps(payload, indent=2))
            else:
                print(f"{args.task}: {verdict.verdict} -- {verdict.detail}")
            return 0
        s = survey(sender=args.sender, top=args.top)
    except Exception as exc:  # noqa: BLE001
        print(f"resume_delivery: could not produce the survey: {exc}",
              file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(s.to_dict(), indent=2))
    else:
        print(_render(s))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
