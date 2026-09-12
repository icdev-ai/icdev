#!/usr/bin/env python3
# CUI // SP-CTI
"""Evidence-backed handoff record for a run that did NOT finish (xrv-run-02).

WHY. A retry re-derives what the last run already learned. Three partial
analogues existed and none was complete:

* ``reflexes/kanban._get_retry_coaching`` prepended ONLY ``failure_count`` and
  ``last_failure_reason`` -- a classification and one truncated sentence, with
  no evidence under it. It could say "the last attempt failed on ruff" and
  never "it had already written these four files and its pytest run said this".
* ``reflexes/kanban._get_parent_handoff`` carries a PARENT's summary to a
  CHILD, which is a different question from carrying an ATTEMPT's state to its
  own retry.
* ``tools/workflow/handoff_generator.py`` has the right sections (loop
  position, next actions, blockers, decisions) and is keyed on ``project_id``
  and imported by nothing on the dispatch path.

WHAT. On the failure / timeout / token-exhaustion paths the scheduler now ALSO
writes a JSON record into the EXISTING ``kanban_tasks.last_run_metadata``
column (no migration), and ``_get_retry_coaching`` renders it into the next
attempt's prompt using ``handoff_generator``'s section ORDER so the two records
read alike.

EVERY UNREADABLE FIELD IS ``None``, NEVER ``[]`` OR ``0``, and the two are
rendered differently -- "not readable" against "none". That distinction is the
whole value of the record on a retry: "the last attempt touched no files" sends
you to a different repair than "nobody could tell what it touched". A record
that collapsed them would let a wiped worktree read as an agent that did
nothing, which is exactly the phantom-completion reading this platform already
refuses elsewhere.

THE RECORD IS NEVER SYNTHESISED FROM A SECOND OPINION. ``validation`` is the
metrics dict ``_run_post_task_validation`` ALREADY returned for the attempt
being recorded -- passed in by the caller, never re-run here. Re-running the
suite inside the failure path would cost the 30-60s that path exists to avoid
AND would answer a different question (what the tree looks like NOW, not what
the run saw), so a record with no captured validation says ``None`` and means
it.

Usage::

    python -m tools.kanban.handoff_record --task <task-id> --json
    python -m tools.kanban.handoff_record --task <task-id> --render
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404 -- fixed git argv, never a shell
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.tools.db.storage import get_connection
from icdev.tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.kanban.handoff_record")

#: Bumped only when a reader must change. ``load_record`` refuses any other
#: value rather than guessing at an older shape.
SCHEMA_VERSION = 1

TRIGGER_FAILURE = "failure"
TRIGGER_TIMEOUT = "timeout"
TRIGGER_TOKEN_EXHAUSTED = "token_exhausted"
TRIGGERS = (TRIGGER_FAILURE, TRIGGER_TIMEOUT, TRIGGER_TOKEN_EXHAUSTED)

#: The card's bound. ``render_record`` sheds evidence until the block fits and
#: NAMES what it shed -- a quietly short list reads as a short attempt.
DEFAULT_RENDER_TOKEN_BUDGET = 1200

#: Git calls are bounded: this runs inside the scheduler's reap loop, and a
#: hung ``git log`` there stalls every task in flight, not just this one.
_GIT_TIMEOUT_SECONDS = 15

#: ``=== 3 failed, 120 passed, 2 skipped in 41.20s ===`` and the bare form.
_PYTEST_SUMMARY_RE = re.compile(
    r"^.*?\b\d+\s+(?:passed|failed|error|errors|skipped|deselected|xfailed)\b"
    r".*?\bin\s+[\d.]+\s*s(?:econds)?\b.*$",
    re.IGNORECASE | re.MULTILINE,
)

_PR_URL_RE = re.compile(r"https://github\.com/[^\s/]+/[^\s/]+/pull/\d+")

#: A record is carried in the next prompt, so it must stay small at the SOURCE
#: and not only at render time -- ``_get_parent_handoff`` hands the raw JSON to
#: a child task.
_MAX_FILES = 40
_MAX_COMMITS = 20
_MAX_BLOCKERS = 12
_MAX_REASON_CHARS = 600
_MAX_AGENT_METADATA_CHARS = 2000


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_git(args: List[str], cwd: Path) -> Optional[str]:
    """stdout of ``git <args>`` in *cwd*, or ``None`` when it cannot be read.

    ``None`` is the load-bearing return: a non-zero exit, a missing directory
    and a timeout are all "we do not know", and the caller must not render any
    of them as an empty list.
    """
    try:
        proc = subprocess.run(  # nosec B603 B607 -- fixed argv, shell=False
            ["git", *args],
            cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_GIT_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 -- unreadable is None, never ""
        logger.debug("handoff_record: git %s in %s unreadable: %s", args[0], cwd, exc)
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _porcelain_paths(blob: str) -> List[str]:
    """Paths out of ``git status --porcelain`` v1 output.

    ``XY path`` and, for a rename, ``XY old -> new`` -- the NEW name is what a
    retry needs, since the old one no longer exists to be opened.
    """
    out: List[str] = []
    for line in blob.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        path = path.strip('"')
        if path:
            out.append(path)
    return out


def files_touched(work_dir: Optional[str],
                  base_ref: str = "origin/main") -> Optional[List[str]]:
    """Paths this attempt changed, or ``None`` when the worktree cannot answer.

    The union of what is COMMITTED on the branch (``<base>...HEAD``) and what
    is still IN THE WORKING TREE. A failed run very often has only the second,
    and that is the half a retry most needs to see.

    The working-tree half is ``git status --porcelain`` and NOT
    ``git diff --name-only``, which the positive control for this function
    caught: a bare diff shows only UNSTAGED changes, so an attempt that had
    ``git add``ed its work -- or written a NEW file and never added it, the
    commonest shape of all -- reported an empty list. An empty list there reads
    as "the agent wrote nothing", which is the phantom-completion reading this
    record exists to make impossible.

    Both halves unreadable is ``None``. ONE half readable is an answer: a
    worktree with no commits yet still reports its dirty files.
    """
    if not work_dir:
        return None
    cwd = Path(work_dir)
    if not cwd.is_dir():
        return None
    committed = _run_git(["diff", "--name-only", f"{base_ref}...HEAD"], cwd)
    working = _run_git(["status", "--porcelain"], cwd)
    if committed is None and working is None:
        return None
    names = set()
    for line in (committed or "").splitlines():
        stripped = line.strip()
        if stripped:
            names.add(stripped)
    names.update(_porcelain_paths(working or ""))
    return sorted(names)


def commits(work_dir: Optional[str],
            base_ref: str = "origin/main") -> Optional[List[str]]:
    """``git log <base>..HEAD --oneline``, or ``None`` when unreadable."""
    if not work_dir:
        return None
    cwd = Path(work_dir)
    if not cwd.is_dir():
        return None
    out = _run_git(["log", f"{base_ref}..HEAD", "--oneline", "--no-decorate"], cwd)
    if out is None:
        return None
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _capped(values: Optional[List[str]], cap: int) -> Optional[List[str]]:
    """The first *cap* entries, or ``None`` for an unreadable list.

    The record travels in the next prompt AND -- through
    ``_get_parent_handoff`` -- into a child task's, so the lists are bounded at
    the SOURCE and not only at render time. The COUNT is kept beside the
    capped list (``*_total``) so the trim can be NAMED: a list that was
    shortened and says nothing about it reads as a shorter attempt than the
    one that actually ran.
    """
    return None if values is None else values[:cap]


def tests_run(task_log: Optional[Path]) -> Optional[str]:
    """The LAST pytest summary line in the run's log, or ``None``.

    The last one, not the first: a run that fixed a failure and re-ran leaves
    both, and the earlier line would report a state the attempt had already
    moved past.
    """
    if not task_log:
        return None
    try:
        path = Path(task_log)
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        logger.debug("handoff_record: task log unreadable: %s", exc)
        return None
    matches = _PYTEST_SUMMARY_RE.findall(text)
    if not matches:
        return None
    return matches[-1].strip()[:300]


def _task_row(task_id: str, db_path: Optional[str]) -> Optional[Dict[str, Any]]:
    try:
        conn = get_connection(db_path=db_path) if db_path else get_connection()
        try:
            row = conn.execute(
                "SELECT executor_url, description, last_run_metadata "
                "FROM kanban_tasks WHERE id = %s",
                (str(task_id),),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("handoff_record: task row for %s unreadable: %s", task_id, exc)
        return None
    return dict(row) if row else None


def pr_url(task_row: Optional[Dict[str, Any]]) -> Optional[str]:
    """The PR this attempt opened, from the row the board already holds.

    ``kanban_tasks`` has no ``pr_url`` column -- ``land.py`` reads the same two
    places (``executor_url``, then the description) -- so this reads them and
    nothing else. No forge call: this runs on the failure path, and a network
    round-trip there would make a record fail to be written because GitHub was
    slow.
    """
    if not task_row:
        return None
    for field in ("executor_url", "description"):
        match = _PR_URL_RE.search(str(task_row.get(field) or ""))
        if match:
            return match.group(0)
    return None


def cost_usd(task_id: str, db_path: Optional[str] = None) -> Optional[float]:
    """This task's attributed spend (xrv-cost-02), or ``None``.

    ``None`` for BOTH "the ledger could not be read" and "no row was ever
    attributed" -- neither is evidence that the attempt was free, and a 0.0
    beside a run that burned a session would be a fabricated measurement.
    """
    try:
        from icdev.tools.cost.task_attribution import usage_rows
    except Exception as exc:  # noqa: BLE001
        logger.debug("handoff_record: cost reader unavailable: %s", exc)
        return None
    try:
        rows = usage_rows(task_id, db_path=Path(db_path) if db_path else None)
    except Exception as exc:  # noqa: BLE001
        logger.debug("handoff_record: cost rows for %s unreadable: %s", task_id, exc)
        return None
    if not rows:
        return None
    total = sum(float(r.get("cost_estimate_usd") or 0.0) for r in rows)
    return round(total, 6)


def _classify(reason: str) -> Optional[str]:
    """``auto_remediate.classify_failure``, or ``None`` when it cannot answer."""
    try:
        from icdev.tools.workflow.auto_remediate import classify_failure
        return classify_failure(reason or "")
    except Exception as exc:  # noqa: BLE001
        logger.debug("handoff_record: classify_failure unavailable: %s", exc)
        return None


def blockers_from(validation: Optional[Dict[str, Any]],
                  reason: Optional[str]) -> Optional[List[str]]:
    """What stood between this attempt and a green verdict.

    ``None`` when NOTHING could be read -- no validation metrics and no reason.
    ``[]`` when both were read and neither named an obstacle, which on a failed
    run is itself a finding (the run failed for a cause the validation suite
    did not see).
    """
    if validation is None and not (reason or "").strip():
        return None
    out: List[str] = []
    metrics = validation or {}
    # False is a MEASURED failure; None is "the stage did not run" and is NOT a
    # blocker -- reporting an unrun stage as one manufactures an obstacle.
    if metrics.get("codelens_passed") is False:
        out.append(
            "CodeLens failed (ruff {ruff}, bandit {bandit})".format(
                ruff=metrics.get("ruff_issues"), bandit=metrics.get("bandit_issues"),
            )
        )
    if metrics.get("coherence_passed") is False:
        out.append("Coherence failed against the main baseline")
    if metrics.get("e2e_ran") and metrics.get("e2e_passed") is False:
        out.append("E2E failed")
    if (reason or "").strip():
        out.append("verification: {0}".format((reason or "").strip()[:200]))
    return out[:_MAX_BLOCKERS]


def _prior_agent_metadata(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """The agent's OWN handoff metadata, preserved rather than clobbered.

    ``POST /api/kanban/tasks/<id>/handoff`` writes the same column, so a run
    that submitted a handoff and then failed verification would otherwise have
    its own account of itself overwritten by ours. It is preserved only when it
    is a dict, is not already one of our records, and is small enough that
    carrying it cannot blow the record's size budget.
    """
    text = (raw or "").strip()
    if not text or len(text) > _MAX_AGENT_METADATA_CHARS:
        return None
    try:
        parsed = json.loads(text)
    except Exception:  # noqa: BLE001 -- unparseable metadata is simply not carried
        return None
    if not isinstance(parsed, dict) or parsed.get("schema") == SCHEMA_VERSION:
        return None
    return parsed


def build_record(task_id: str, *, trigger: str, attempt: Optional[int],
                 reason: Optional[str] = None,
                 validation: Optional[Dict[str, Any]] = None,
                 work_dir: Optional[str] = None,
                 base_ref: str = "origin/main",
                 task_log: Optional[Path] = None,
                 db_path: Optional[str] = None) -> Dict[str, Any]:
    """Assemble the schema-1 record for one run that did not finish.

    Never re-runs anything: every field is either handed in by the caller
    (``validation``, ``attempt``, ``reason``) or read from evidence that
    already exists on disk or on the board.
    """
    reason_text = (reason or "").strip()
    row = _task_row(task_id, db_path)
    touched = files_touched(work_dir, base_ref)
    committed = commits(work_dir, base_ref)
    return {
        "schema": SCHEMA_VERSION,
        "task_id": str(task_id),
        "trigger": trigger if trigger in TRIGGERS else TRIGGER_FAILURE,
        "attempt": int(attempt) if attempt is not None else None,
        "failure_class": _classify(reason_text),
        "reason": reason_text[:_MAX_REASON_CHARS] or None,
        "validation": validation if isinstance(validation, dict) else None,
        "blockers": blockers_from(validation, reason_text),
        "files_touched": _capped(touched, _MAX_FILES),
        "files_touched_total": None if touched is None else len(touched),
        "tests_run": tests_run(task_log),
        "commits": _capped(committed, _MAX_COMMITS),
        "commits_total": None if committed is None else len(committed),
        "pr": pr_url(row),
        "cost_usd": cost_usd(task_id, db_path),
        "agent_metadata": _prior_agent_metadata(
            (row or {}).get("last_run_metadata")),
        "written_at": _utcnow_iso(),
    }


def write_record(task_id: str, record: Dict[str, Any], *,
                 db_path: Optional[str] = None) -> bool:
    """Persist *record* into ``kanban_tasks.last_run_metadata``. No migration.

    Best-effort by design: this runs on the failure path, and a record that
    could not be stored must never turn a recorded failure into an exception
    that loses the failure itself.
    """
    try:
        payload = json.dumps(record, ensure_ascii=False, default=str)
    except Exception as exc:  # noqa: BLE001
        logger.warning("handoff_record: %s not serialisable: %s", task_id, exc)
        return False
    try:
        conn = get_connection(db_path=db_path) if db_path else get_connection()
        try:
            conn.execute(
                "UPDATE kanban_tasks SET last_run_metadata = %s WHERE id = %s",
                (payload, str(task_id)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("handoff_record: write for %s failed: %s", task_id, exc)
        return False
    return True


def record_run(task_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
    """Build and persist in one call; ``None`` if either half could not happen."""
    db_path = kwargs.get("db_path")
    try:
        record = build_record(task_id, **kwargs)
    except Exception as exc:  # noqa: BLE001 -- never the failure path's problem
        logger.warning("handoff_record: build for %s failed: %s", task_id, exc)
        return None
    return record if write_record(task_id, record, db_path=db_path) else None


def is_record(obj: Any) -> bool:
    return isinstance(obj, dict) and obj.get("schema") == SCHEMA_VERSION


def load_record(task_id: str, *, db_path: Optional[str] = None,
                raw: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The stored record for *task_id*, or ``None``.

    ``None`` covers an absent column, unparseable JSON, an agent-submitted
    handoff that is not one of our records, and any other schema version --
    every one of which means "there is no record to render here", never "the
    last attempt produced nothing".
    """
    text = raw
    if text is None:
        row = _task_row(task_id, db_path)
        if row is None:
            return None
        text = row.get("last_run_metadata")
    if not (text or "").strip():
        return None
    try:
        parsed = json.loads(text)
    except Exception:  # noqa: BLE001
        return None
    return parsed if is_record(parsed) else None


def _fmt_list(values: Optional[List[str]], cap: int, *,
              empty: str, unreadable: str = "not readable",
              total: Optional[int] = None) -> List[str]:
    """Render a list keeping ABSENCE and EMPTINESS apart, and naming any trim.

    *total* is the count BEFORE the record's own build-time cap, so an item
    dropped at write time is counted here exactly like one dropped to fit the
    prompt budget. Two silent trims in a row is how a 200-file attempt comes
    to read as a 40-file one.
    """
    if values is None:
        return ["- _{0}_".format(unreadable)]
    if not values:
        return ["- _{0}_".format(empty)]
    shown = values[:cap] if cap > 0 else []
    lines = ["- `{0}`".format(v) for v in shown]
    known_total = total if (total is not None and total >= len(values)) else len(values)
    remaining = known_total - len(shown)
    if remaining > 0:
        lines.append(
            "- _... and {0} more (trimmed to fit the prompt budget)_".format(remaining)
        )
    return lines


def _render(record: Dict[str, Any], *, file_cap: int, commit_cap: int) -> str:
    """One rendering pass at the given evidence caps.

    Section ORDER is ``handoff_generator``'s -- position, actions, blockers,
    then the evidence -- so a reader who has seen one document can read the
    other. The last heading is NOT ``## Recent Decisions``: a file an agent
    touched is not a decision it recorded, and borrowing that title would
    claim a rationale the run never stated.
    """
    attempt = record.get("attempt")
    attempt_txt = "#{0}".format(attempt) if attempt is not None else "(number unrecorded)"
    trigger = record.get("trigger") or TRIGGER_FAILURE
    klass = record.get("failure_class") or "unclassified"

    lines: List[str] = [
        "## Current Position",
        "- Attempt {0} ended as **{1}**, classified `{2}`.".format(
            attempt_txt, trigger, klass),
    ]
    if record.get("reason"):
        lines.append("- Reason: {0}".format(record["reason"]))
    validation = record.get("validation")
    if validation is None:
        lines.append("- Validation: _did not run, or its result was not captured_ "
                     "(this is NOT a clean bill of health).")
    else:
        lines.append(
            "- Validation: codelens={codelens}, coherence={coherence}, "
            "e2e_ran={e2e_ran}, e2e={e2e}, modified_files={mod}".format(
                codelens=validation.get("codelens_passed"),
                coherence=validation.get("coherence_passed"),
                e2e_ran=validation.get("e2e_ran"),
                e2e=validation.get("e2e_passed"),
                mod=validation.get("modified_files"),
            )
        )
    cost = record.get("cost_usd")
    lines.append("- Attributed spend: {0}".format(
        "${0:.4f}".format(cost) if cost is not None else "_not attributed_"))
    if record.get("pr"):
        lines.append("- PR already open: {0}".format(record["pr"]))

    lines += ["", "## Priority Actions",
              "1. Read the evidence below before changing anything - the last "
              "attempt already did part of this work.",
              "2. Fix the named blocker; do not restart the task from scratch.",
              "3. Keep the scope to the task title."]

    lines += ["", "## Blockers"]
    lines += _fmt_list(record.get("blockers"), _MAX_BLOCKERS,
                       empty="none were recorded - the run failed for a cause "
                             "the validation suite did not see",
                       unreadable="nothing could be read about what blocked it")

    lines += ["", "## Evidence from the last attempt", "Files touched:"]
    lines += _fmt_list(record.get("files_touched"), file_cap,
                       empty="the attempt changed no files",
                       unreadable="the worktree could not be read",
                       total=record.get("files_touched_total"))
    lines.append("Commits:")
    lines += _fmt_list(record.get("commits"), commit_cap,
                       empty="the attempt committed nothing",
                       unreadable="the branch could not be read",
                       total=record.get("commits_total"))
    tests = record.get("tests_run")
    lines.append("Tests: {0}".format(tests) if tests
                 else "Tests: _no pytest summary was found in the run log_")
    lines.append("")
    return "\n".join(lines)


def render_record(record: Dict[str, Any], *,
                  max_tokens: int = DEFAULT_RENDER_TOKEN_BUDGET) -> str:
    """Render *record* as a markdown block bounded at *max_tokens*.

    Sheds EVIDENCE first and narrative last, and every shed item is counted in
    the output -- a quietly short list reads as a short attempt, which is the
    opposite of what happened.
    """
    try:
        from icdev.tools.llm.context_budget import estimate_tokens
    except Exception:  # noqa: BLE001 -- a missing estimator must not lose the block
        def estimate_tokens(text: str) -> int:  # type: ignore[misc]
            return len(text) // 4

    text = ""
    for file_cap, commit_cap in ((_MAX_FILES, _MAX_COMMITS), (20, 10), (10, 5),
                                 (5, 3), (3, 1), (1, 1), (0, 0)):
        text = _render(record, file_cap=file_cap, commit_cap=commit_cap)
        if estimate_tokens(text) <= max_tokens:
            return text
    return text


# -- CLI ---------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read the stored handoff record for a kanban task.")
    parser.add_argument("--task", required=True, help="task id")
    parser.add_argument("--render", action="store_true",
                        help="print the prompt block instead of the record")
    parser.add_argument("--max-tokens", type=int,
                        default=DEFAULT_RENDER_TOKEN_BUDGET)
    parser.add_argument("--db-path", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    record = load_record(args.task, db_path=args.db_path)
    if record is None:
        if args.json:
            print(json.dumps({"task_id": args.task, "record": None,
                              "state": "no_record"}, indent=2))
        else:
            print("{0}: no schema-{1} handoff record. This is not a statement "
                  "that the last run was clean.".format(args.task, SCHEMA_VERSION))
        return 0

    if args.render:
        block = render_record(record, max_tokens=args.max_tokens)
        if args.json:
            print(json.dumps({"task_id": args.task, "block": block}, indent=2))
        else:
            print(block)
        return 0

    if args.json:
        print(json.dumps({"task_id": args.task, "record": record,
                          "state": "present"}, indent=2))
    else:
        print(json.dumps(record, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
