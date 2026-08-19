# CUI // SP-CTI
"""Would ``landed_check`` have REFUSED a real dispatch — and would it have been right?

The fire-rate survey CLAUDE.md demands before arming a check. ``landed_check``
already carried one, but it measured a different population: the *board* on a
single day (10 non-terminal tasks, 0 fires) and the *coverage* of the detector
over ``done`` rows. Neither answers the question that decides the dispatch
posture, which is what the gate would have done to the **dispatch stream** —
6,218 recorded dispatches, not 10 rows on one afternoon.

METHOD. For every recorded dispatch, ask whether the task id was on the default
branch AT THAT MOMENT — not now. Then split the fires by what happened next:

  * **correct** — nothing carrying that id ever landed after the dispatch, so
    the dispatch rebuilt work that was already delivered. Refusing costs an
    agent run that produced nothing, and avoids the duplicate PR that can only
    land as a revert (#1651).
  * **wrong** — a further commit carrying the id reached the branch after the
    dispatch, so the dispatch produced work the repo took. Refusing would have
    withheld it.

The `wrong` bucket is an UPPER BOUND and deliberately so: a later commit is
counted as real work without asking whether it was itself a revert or a
duplicate. The bound errs against arming the gate, which is the direction a
survey should err.

EVIDENCE TIERS ARE THE GATE'S, NOT A SECOND COPY. Matching reuses
``landed_check._grep_pattern`` and ``landed_check._classify``, so a subject or
merge-ref hit means here exactly what it means at dispatch, and a BODY-only
mention is not a landing in either place. A survey with its own matcher would
measure a gate that does not exist.

    python -m tools.kanban.landed_dispatch_survey --json
    python -m tools.kanban.landed_dispatch_survey --window-days 30
    python -m tools.kanban.landed_dispatch_survey --ref origin/main
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404 — fixed argv, no shell
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from tools.kanban import landed_check as _lc

#: Word characters for id boundaries, kept identical to ``_grep_pattern``'s
#: ``[^A-Za-z0-9_-]`` class. Tokenising a subject on the complement and testing
#: set membership is EXACTLY the boundary match that pattern performs, which is
#: what makes the fast path equivalent rather than merely similar.
_WORD_SPLIT = re.compile(r"[^A-Za-z0-9_-]+")

#: An id containing a character outside that class (``.`` is the realistic one —
#: ``_grep_pattern`` escapes it, so the pattern matches, but tokenising would
#: split the id in half) cannot use the fast path and is matched with the real
#: regex instead. Correctness first; the slow set is tiny in practice.
_TOKENISABLE = re.compile(r"^[A-Za-z0-9_-]+$")


def _parse_dt(value) -> Optional[datetime]:
    """UTC-aware datetime from a DB stamp or an ISO string, or None."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip().replace(" ", "T")
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def collect_dispatches(conn, window_days: Optional[int] = None) -> List[Tuple[str, datetime]]:
    """Every recorded scheduler dispatch as ``(task_id, when)``, oldest first.

    A dispatch is the ``-> in_progress`` transition written by the scheduler.
    That is the exact moment the pre-dispatch check runs, so it is the only
    population whose fire rate describes this gate.
    """
    sql = ("SELECT task_id, recorded_at FROM kanban_status_transitions "
           "WHERE to_status = 'in_progress' AND actor = 'scheduler'")
    params: list = []
    if window_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        sql += " AND recorded_at >= %s"
        params.append(cutoff.isoformat())
    sql += " ORDER BY recorded_at"

    out: List[Tuple[str, datetime]] = []
    for row in conn.execute(sql, tuple(params)).fetchall():
        record = dict(row)
        when = _parse_dt(record.get("recorded_at"))
        task_id = str(record.get("task_id") or "").strip()
        if task_id and when:
            out.append((task_id, when))
    return out


def _git_log(ref: str, repo_root) -> List[Tuple[datetime, str, str]]:
    """``(committed_at, sha, subject)`` for every commit on ``ref``, newest first."""
    proc = subprocess.run(  # nosec B603 B607 — fixed argv, no shell
        ["git", "log", ref, "--format=%h%x01%cI%x01%s"],
        cwd=str(repo_root), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180, check=False,
    )
    if proc.returncode != 0:
        return []
    commits: List[Tuple[datetime, str, str]] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x01")
        if len(parts) != 3:
            continue
        when = _parse_dt(parts[1])
        if when:
            commits.append((when, parts[0], parts[2]))
    return commits


def landings_for(
    task_ids: Sequence[str], commits: Sequence[Tuple[datetime, str, str]]
) -> Dict[str, List[Tuple[datetime, str, str]]]:
    """``{task_id: [(when, sha, subject), ...]}`` for the BLOCKING tiers only.

    ``merge_ref`` and ``subject`` are the tiers that stop a dispatch; a BODY
    mention is a citation as often as a landing and never blocks, so it is not
    a landing here either.
    """
    wanted = {t for t in task_ids}
    fast = {t for t in wanted if _TOKENISABLE.match(t)}
    slow = [(t, re.compile(_lc._grep_pattern(t))) for t in wanted - fast]

    found: Dict[str, List[Tuple[datetime, str, str]]] = defaultdict(list)
    for when, sha, subject in commits:
        hits = fast.intersection(_WORD_SPLIT.split(subject))
        for task_id, pattern in slow:
            if pattern.search(subject):
                hits.add(task_id)
        for task_id in hits:
            # Ask the gate's own classifier, so a tier change there changes the
            # survey too. Body is empty: a body match must not count as landed.
            if _lc._classify(task_id, subject, "") in (
                _lc.CONFIDENCE_MERGE_REF, _lc.CONFIDENCE_SUBJECT
            ):
                found[task_id].append((when, sha, subject))
    for entries in found.values():
        entries.sort()
    return dict(found)


def survey(conn, repo_root=None, ref: Optional[str] = None,
           window_days: Optional[int] = None) -> dict:
    """Fire rate and correctness of the pre-dispatch landed check."""
    root = repo_root or _lc.BASE_DIR
    target = ref or f"origin/{_lc.default_branch(root)}"

    dispatches = collect_dispatches(conn, window_days=window_days)
    commits = _git_log(target, root)

    #: A database with no operating history and a repo with no reachable ref
    #: are UNMEASURABLE, never a clean 0% — a fresh worktree or an ephemeral CI
    #: database would otherwise report "this gate never fires" and read as
    #: proof that arming it is free.
    if not dispatches or not commits:
        return {
            "measurable": False,
            "reason": ("no recorded scheduler dispatches" if not dispatches
                       else f"no commits reachable from {target}"),
            "ref": target, "dispatches": len(dispatches), "commits": len(commits),
        }

    landings = landings_for({t for t, _ in dispatches}, commits)

    correct: List[dict] = []
    wrong: List[dict] = []
    for task_id, when in dispatches:
        entries = landings.get(task_id) or []
        before = [e for e in entries if e[0] < when]
        if not before:
            continue                      # not on the branch yet — no fire
        after = [e for e in entries if e[0] > when]
        record = {
            "task_id": task_id,
            "dispatched_at": when.isoformat(),
            "landed_at": before[-1][0].isoformat(),
            "landed_sha": before[-1][1],
            "gap_hours": round((when - before[-1][0]).total_seconds() / 3600.0, 2),
        }
        if after:
            record["later_sha"] = after[0][1]
            record["later_subject"] = after[0][2]
            wrong.append(record)
        else:
            correct.append(record)

    total = len(dispatches)
    fires = len(correct) + len(wrong)
    return {
        "measurable": True,
        "ref": target,
        "window_days": window_days,
        "dispatches": total,
        "distinct_task_ids": len({t for t, _ in dispatches}),
        "commits_scanned": len(commits),
        "fires": fires,
        "fire_rate_pct": round(100.0 * fires / total, 2),
        "correct": len(correct),
        "correct_pct": round(100.0 * len(correct) / total, 2),
        "wrong": len(wrong),
        "wrong_pct": round(100.0 * len(wrong) / total, 2),
        "wrong_share_of_fires_pct": round(100.0 * len(wrong) / fires, 2) if fires else 0.0,
        "top_rebuilt": Counter(r["task_id"] for r in correct).most_common(10),
        "wrong_examples": wrong[:20],
        "correct_examples": correct[-10:],
    }


def _render(report: dict) -> str:
    if not report.get("measurable"):
        return (f"UNMEASURABLE — {report['reason']}.\n"
                "A database with no dispatch history cannot say what this gate "
                "would do; it is not evidence that arming it is free.")
    lines = [
        f"dispatches surveyed        : {report['dispatches']} "
        f"({report['distinct_task_ids']} distinct ids, ref {report['ref']})",
        f"WOULD REFUSE               : {report['fires']} ({report['fire_rate_pct']}%)",
        f"  correct (nothing landed) : {report['correct']} ({report['correct_pct']}%)",
        f"  WRONG   (work landed)    : {report['wrong']} ({report['wrong_pct']}% of "
        f"dispatches, {report['wrong_share_of_fires_pct']}% of fires)",
        "",
        "most-rebuilt already-landed ids:",
    ]
    lines += [f"   {n:>4}x  {tid}" for tid, n in report["top_rebuilt"]]
    if report["wrong_examples"]:
        lines += ["", "refusals that would have withheld real work:"]
        lines += [f"   {r['task_id']:<22} then {r['later_sha']} {r['later_subject'][:52]}"
                  for r in report["wrong_examples"][:8]]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable")
    parser.add_argument("--ref", help="ref to compare against (default origin/<default>)")
    parser.add_argument("--window-days", type=int,
                        help="only dispatches within the last N days")
    args = parser.parse_args(argv)

    from tools.db.storage import get_connection

    report = survey(get_connection(), ref=args.ref, window_days=args.window_days)
    print(json.dumps(report, indent=2) if args.json else _render(report))
    #: Report-only, like the identity survey. A survey that could fail a build
    #: would get a `|| true` inside a week, and its whole job is to be run and
    #: read before a posture changes.
    return 0


if __name__ == "__main__":
    sys.exit(main())
