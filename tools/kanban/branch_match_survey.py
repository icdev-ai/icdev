# CUI // SP-CTI
"""Which (task, ref) pairs does the branch matcher bind, and which does the
narrowed rule DROP? (mfx-own-05)

``tools/genesis/reflexes/kanban.py::_branches_for_task`` answers "which branch
refs carry this task id" for the done-gate ("does a branch for this task hold
unmerged work"), the stranded audit, the artifact-evidence gate and both
``orphan_requeue`` proofs. Its old boundary::

    (^|[/_-])<id>([/_.-]|$)

anticipated one direction of extension -- a decomposed CHILD appends to its
parent's id (``kanban/dwo-mcp-03-d5-d1`` for ``dwo-mcp-03-d5``) -- and admitted
the other: a REPARK card prepends (``kph-repark-<id>``, then
``kph-repark-kph-repark-<id>``). MEASURED 2026-09-06: ``mfx-ci-04`` sat in
``validating`` from 12:49 to ~18:00 while ``kanban_requeue_reflex`` refused it
every cycle with ``branch_not_ancestor:kanban/kph-repark-kph-repark-mfx-ci-04``
-- a branch belonging to a THIRD card, which built and landed something else
entirely (PR #2146). ``kph-repark-mfx-ci-04`` was refused on the same foreign
branch, so one rule stranded two cards.

The narrowed rule requires the id to START a path segment::

    (^|/)<id>([/_.-]|$)

This module replays BOTH over the live ref listing x every non-terminal task
id and reports what the narrowing adds (nothing, by construction -- the new
set is a subset of the old) and what it DROPS, each drop NAMED and classified:

    repark   the ref's segment starts with a repark prefix -- the finding
    other    anything else. Every one of these is a pair the done-gate used
             to consult and no longer will, so it is listed, never summed.

"Today" is asked of the SHIPPED ``_branches_for_task`` -- never a second copy.
The OLD rule exists only here, as ``legacy_matches``, labelled as history.

UNMEASURABLE, never a clean zero, when the ref listing or the board cannot be
read: a git worktree with no ``.env`` reads a throwaway SQLite database, and
"0 dropped" over 0 task ids is not evidence. Report only, deliberately no
``--gate``: it measures the REPO and the BOARD, not a diff (kpr-fix-03).

Usage::

    python -m tools.kanban.branch_match_survey --env-file C:/AI/ICDev/.env
    python -m tools.kanban.branch_match_survey --env-file C:/AI/ICDev/.env --json
    python -m tools.kanban.branch_match_survey --include-terminal
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable, Optional, Sequence

from icdev.core.paths import repo_root
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

BASE_DIR = repo_root(__file__)

#: Segment prefixes a repark card wears. ``orphan_requeue`` files a twice-parked
#: row as ``kph-repark-<id>``; a repark of a repark stacks the prefix.
REPARK_PREFIXES = ("kph-repark-",)

DROP_REPARK = "repark"
DROP_OTHER = "other"

UNMEASURABLE_NO_REFS = "no_refs"
UNMEASURABLE_NO_TASKS = "no_tasks"


def legacy_matches(task_id: str, name: str) -> bool:
    """The rule BEFORE mfx-own-05, kept here and nowhere else.

    ``name`` is the ref with any ``origin/`` stripped, as the matcher sees it.
    """
    pat = re.compile(rf"(^|[/_-]){re.escape(task_id)}([/_.-]|$)")
    return bool(pat.search(name))


def _name(ref: str) -> str:
    return ref.split("origin/", 1)[-1] if ref.startswith("origin/") else ref


def _segment(ref: str) -> str:
    return _name(ref).rsplit("/", 1)[-1]


def classify_drop(ref: str) -> str:
    seg = _segment(ref)
    return DROP_REPARK if seg.startswith(REPARK_PREFIXES) else DROP_OTHER


def _legacy_refs(task_id: str, refs: Sequence[str]) -> list:
    out, seen = [], set()
    for ref in refs:
        ref = ref.strip()
        if not ref or ref.endswith("/HEAD") or ref in seen:
            continue
        if legacy_matches(task_id, _name(ref)):
            seen.add(ref)
            out.append(ref)
    return out


def survey(refs: Sequence[str], task_ids: Iterable[str], *, resolver=None) -> dict:
    """Replay the legacy rule and the SHIPPED matcher over ``refs`` x ``task_ids``.

    ``resolver`` defaults to ``_branches_for_task`` and is what "today" means;
    it is injectable so a test can prove the survey reads the shipped predicate
    rather than a copy of it.
    """
    refs = [r.strip() for r in refs if r and r.strip()]
    ids = sorted({str(t).strip() for t in task_ids if str(t or "").strip()})
    if not refs:
        return {"measured": False, "unmeasurable_reason": UNMEASURABLE_NO_REFS,
                "refs": 0, "task_ids": len(ids)}
    if not ids:
        return {"measured": False, "unmeasurable_reason": UNMEASURABLE_NO_TASKS,
                "refs": len(refs), "task_ids": 0}

    if resolver is None:
        from tools.genesis.reflexes.kanban import _branches_for_task as resolver  # noqa: E501

    legacy_pairs = 0
    current_pairs = 0
    added: list = []
    dropped: list = []
    tasks_bound_legacy = 0
    tasks_bound_current = 0
    tasks_affected: set = set()
    for tid in ids:
        old = set(_legacy_refs(tid, refs))
        new = set(resolver(tid, BASE_DIR, refs=refs))
        legacy_pairs += len(old)
        current_pairs += len(new)
        tasks_bound_legacy += 1 if old else 0
        tasks_bound_current += 1 if new else 0
        for ref in sorted(new - old):
            added.append({"task_id": tid, "ref": ref})
            tasks_affected.add(tid)
        for ref in sorted(old - new):
            dropped.append({"task_id": tid, "ref": ref, "kind": classify_drop(ref)})
            tasks_affected.add(tid)

    by_kind = {DROP_REPARK: 0, DROP_OTHER: 0}
    for d in dropped:
        by_kind[d["kind"]] += 1
    return {
        "measured": True,
        "refs": len(refs),
        "task_ids": len(ids),
        "pairs": {"legacy": legacy_pairs, "current": current_pairs},
        "tasks_bound": {"legacy": tasks_bound_legacy, "current": tasks_bound_current},
        "tasks_affected": sorted(tasks_affected),
        "added": added,
        "dropped": dropped,
        "dropped_by_kind": by_kind,
    }


# --------------------------------------------------------------------------
# primary data
# --------------------------------------------------------------------------
def read_refs(root: Optional[Path] = None) -> list:
    """The live ref listing, through the matcher's own reader."""
    from tools.genesis.reflexes.kanban import all_task_refs

    return all_task_refs(root or BASE_DIR)


def read_task_ids(include_terminal: bool = False) -> list:
    """Every task id on the board, terminal ones excluded unless asked.

    The terminal set is IMPORTED from ``pr_linker`` -- one spelling.
    """
    from tools.db.storage import get_connection
    from tools.kanban.pr_linker import TERMINAL_STATUSES

    with get_connection() as conn:
        rows = conn.execute("SELECT id, status FROM kanban_tasks").fetchall()
    out = []
    for row in rows:
        tid, status = row[0], row[1]
        if not include_terminal and str(status or "") in TERMINAL_STATUSES:
            continue
        out.append(str(tid))
    return out


def _backend_name() -> str:
    import os

    return str(os.getenv("ICDEV_STORAGE_BACKEND") or "sqlite").strip().lower()


# --------------------------------------------------------------------------
# render
# --------------------------------------------------------------------------
def render(report: dict) -> str:
    if not report.get("measured"):
        return ("UNMEASURABLE (%s): refs=%s task_ids=%s backend=%s -- not a clean survey. "
                "From a worktree, point at the real board with --env-file."
                % (report.get("unmeasurable_reason"), report.get("refs"),
                   report.get("task_ids"), report.get("backend")))
    lines = [
        "Branch matcher survey (mfx-own-05) -- legacy `(^|[/_-])<id>` vs shipped `(^|/)<id>`",
        "",
        "  refs read           %6d" % report["refs"],
        "  task ids surveyed   %6d   (backend %s%s)" % (
            report["task_ids"], report.get("backend", "?"),
            ", terminal included" if report.get("include_terminal") else ""),
        "  (task, ref) pairs   legacy %d -> current %d" % (
            report["pairs"]["legacy"], report["pairs"]["current"]),
        "  tasks with >=1 ref  legacy %d -> current %d" % (
            report["tasks_bound"]["legacy"], report["tasks_bound"]["current"]),
        "  added               %d" % len(report["added"]),
        "  dropped             %d   (repark %d, other %d)" % (
            len(report["dropped"]), report["dropped_by_kind"][DROP_REPARK],
            report["dropped_by_kind"][DROP_OTHER]),
        "",
    ]
    if report["added"]:
        lines.append("ADDED (the narrowing should add nothing; every line here is a defect):")
        lines += ["  %-28s %s" % (a["task_id"], a["ref"]) for a in report["added"]]
        lines.append("")
    lines.append("DROPPED, by name:")
    if report["dropped"]:
        lines += ["  %-8s %-28s %s" % (d["kind"], d["task_id"], d["ref"])
                  for d in report["dropped"]]
    else:
        lines.append("  (none)")
    return "\n".join(lines)


def main(argv: Optional[Sequence] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Replay the legacy and the shipped branch matcher over the live refs "
                    "x board (mfx-own-05). REPORT ONLY -- refuses nothing, writes nothing.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--include-terminal", action="store_true",
                    help="survey every task id, terminal statuses included")
    ap.add_argument("--env-file",
                    help="load this .env before connecting -- a git worktree has none, so "
                         "get_connection() would otherwise read a throwaway SQLite database")
    args = ap.parse_args(argv)

    if args.env_file:
        try:
            from dotenv import load_dotenv

            load_dotenv(args.env_file, override=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("branch_match_survey: could not load %s (%s)", args.env_file, exc)

    try:
        ids = read_task_ids(include_terminal=args.include_terminal)
    except Exception as exc:  # noqa: BLE001
        logger.warning("branch_match_survey: board unreadable (%s)", exc)
        ids = []
    report = survey(read_refs(), ids)
    report["backend"] = _backend_name()
    report["include_terminal"] = bool(args.include_terminal)
    print(json.dumps(report, indent=2, default=str) if args.json else render(report))
    # Report only. Exit 2 = could not be produced, never the same as a clean survey.
    return 0 if report.get("measured") else 2


if __name__ == "__main__":
    raise SystemExit(main())
