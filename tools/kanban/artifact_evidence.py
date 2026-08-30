#!/usr/bin/env python3
# CUI // SP-CTI
"""Does a `done` task have an ARTIFACT on the default branch? (kpr-rvfy-04)

The board records what a worker SAID and what a status writer DECIDED. Nothing
asked the tree. MEASURED 2026-08-29 on the live board: of 18 ``ftp-*`` tasks,
FIVE were marked ``done`` by ``genesis_scheduler`` with
``completed_via_bypass=0`` while their deliverable was absent from
``origin/main``. Real completion was 11 of 18, not 16 — a board overstating
completion by 45%.

WHAT THE FIVE ACTUALLY SHARE, read from ``kanban_status_transitions`` rather
than from the cards: every one of them was completed on a
``Verified (git-first): ...`` reason, and in four cases the evidence was
``N uncommitted change(s) in worktree``. Uncommitted changes are not a landing;
they are not even a commit. ``ftp-ezb-06`` was completed on 24 of them while its
worker was still running pytest, and that session exited without committing —
``kanban/ftp-ezb-06`` carries zero commits and ``git fsck`` finds no dangling
one, so roughly forty minutes of finished work is simply gone. The false ``done``
did not merely misreport it; it removed the pressure that would have caught the
loss.

The fifth, ``ftp-prd-07``, was completed on the OTHER arm — "18 file(s) changed
on kanban/ftp-prd-07" — and that arm compares ``<dispatch baseline>..<branch>``.
When the branch sits at or below the current default branch, every commit in
that range is work MAIN gained, contributed by other tasks. Measured the same
day: ``kanban/ftp-prd-07`` is 0 commits ahead of ``origin/main``.

Three arms, one defect: the evidence does not distinguish this task's delivered
work from anything else that touched the disk.

WHAT THIS MODULE DOES, and deliberately does not:

  * :func:`declared_artifacts` reads the paths a card says it will CREATE.
  * :func:`artifact_report` asks ``git cat-file -e <ref>:<path>`` — the tree, not
    the row.
  * :func:`delivery_evidence` answers "did anything at all build this?" for the
    done-gate: a dispatch record, a branch, commits ahead.
  * It REPORTS. There is no ``--gate``: this measures the BOARD, not a diff, and
    a gate that fails a commit for a row the committer did not write is a gate
    people learn to bypass (kpr-fix-03).

FOUR VERDICTS, and ``unmeasurable`` is never folded into any of the others:

``present``       every declared artifact exists on the default branch.
``partial``       some do. The card shipped part of what it declared.
``absent``        none does. THE FINDING.
``unmeasurable``  the card declares no artifact, or the repo/ref could not be
                  read. NOT a clean bill of health, and reported under its own
                  count so "no findings" can never be read over a board most of
                  which was never looked at.

TWO WAYS "NOT ON MAIN" IS NOT A FINDING, both measured as false positives on the
first board-wide run — 300 done tasks over 14 days, and its ONLY two findings
were both wrong. A report whose findings are all wrong is one people learn to
skip, so each is answered by RE-DERIVING, never by a blocklist:

  * a path git is told never to track (``git check-ignore``) cannot be on any
    branch, so its absence says nothing about the card. ``ftl-sched-03``
    declared ``args/ft_scheduler.local.yaml``, which is ``.gitignore`` line 40.
    It is kept out of BOTH ``present`` and ``missing``.
  * a path the card wrote relative to a subdirectory. ``ftl-val-05`` declared
    ``families/__init__.py``, which is on main at
    ``icdev_fin/backtest/families/__init__.py``. A UNIQUE suffix match against
    the branch's tracked files resolves it and the move is RECORDED under
    ``resolved_relative`` rather than silently applied; two or more matches is a
    guess, and a guess is not evidence, so an ambiguous path stays missing.

A TASK ID FOUND IN A FILE IS NOT AN ARTIFACT. The obvious survey — grep main for
the id — is the one that produced the wrong answer in the first place: three of
the five ids are on main only as forward references in comments naming the card
that WILL do the work. The tiering for that lives in
``tools.kanban.landed_check`` (``CONFIDENCE_FILE_CONTENT``, absent from
``BLOCKING_CONFIDENCE``) and is CONSUMED here rather than re-derived, so there
is one statement of the rule and the survey cannot drift from the dispatch gate.

Headless::

    python -m tools.kanban.artifact_evidence --survey --json
    python -m tools.kanban.artifact_evidence --survey --window-days 7
    python -m tools.kanban.artifact_evidence --task ftp-prd-08 --json
    python -m tools.kanban.artifact_evidence --prefix ftp- --json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import subprocess  # nosec B404 — git plumbing; every argv element is validated
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]  # sys.path bootstrap only
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402

from tools.kanban import landed_check as _landed  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

BASE_DIR = repo_root(__file__)

logger = get_logger("kanban.artifact_evidence")

STATE_PRESENT = "present"
STATE_PARTIAL = "partial"
STATE_ABSENT = "absent"
STATE_UNMEASURABLE = "unmeasurable"

#: A path a card DECLARES it will create. Requires a directory separator and an
#: extension: a bare word is prose, and ``auth.py`` on its own names no location
#: the tree can be asked about. Leading ``./`` and trailing punctuation are
#: stripped by :func:`_clean_path`.
_PATH_RE = re.compile(
    r"[A-Za-z0-9_.][A-Za-z0-9_.\-]*(?:/[A-Za-z0-9_.\-]+)+\.[A-Za-z0-9]{1,8}")

#: The verbs that turn a mentioned path into a DECLARED one. Without them the
#: extractor pulls every path a card cites for context — ``ft_api/routers/
#: governance.py``, ``ui/src/routes/ops.tsx`` — which already exist, so every
#: card would report ``present`` and the survey would find nothing. The card's
#: own wording is the discriminator: this repo writes "new ft_api/auth.py".
_CREATION_MARKERS = frozenset({
    "new", "create", "creates", "created", "creating",
    "add", "adds", "added", "adding",
    "write", "writes", "author", "authors", "scaffold", "scaffolds",
})

#: How far before a path a creation marker may sit and still govern it. Measured
#: against the ftp cards' own wording: "(1) new ft_api/auth.py + Starlette
#: middleware", "new ui/src/lib/glossary.ts exporting GLOSSARY". A marker more
#: than a few tokens away is describing something else.
_MARKER_WINDOW_CHARS = 40

#: Paths that are never a task's deliverable even when a card says "add ... to"
#: them. These are the repo's shared registries: every second card appends a row
#: to one, so their presence proves nothing about the card.
_NOT_A_DELIVERABLE = frozenset({
    "tools/manifest.md",
    "args/ci_test_files/core.txt",
    "requirements.txt",
    "claude.md",
    "readme.md",
})


def _clean_path(raw: str) -> str:
    """Strip the punctuation a path picks up from prose."""
    text = (raw or "").strip().strip("`'\"")
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip(").,;:'\"`")


def declared_artifacts(title: str, description: str, limit: int = 12) -> List[str]:
    """Paths this card says it will CREATE, in first-mention order.

    Returns ``[]`` when the card declares none — which the caller must report as
    ``unmeasurable``, never as clean. A card describing a behaviour change to an
    existing file genuinely has no new artifact to look for, and inventing one
    for it would manufacture findings.
    """
    text = f"{title or ''}\n{description or ''}"
    lowered = text.lower()
    found: List[str] = []
    for m in _PATH_RE.finditer(text):
        path = _clean_path(m.group(0))
        if not path or path in found:
            continue
        if path.lower() in _NOT_A_DELIVERABLE:
            continue
        window = lowered[max(0, m.start() - _MARKER_WINDOW_CHARS):m.start()]
        # Tokens only: "renew" must not satisfy "new", and "address" must not
        # satisfy "add".
        if not any(tok in _CREATION_MARKERS for tok in re.findall(r"[a-z]+", window)):
            continue
        found.append(path)
        if len(found) >= limit:
            break
    return found


def _run_git(args: Sequence[str], cwd, timeout: int = 30):
    """Run a git command, returning the CompletedProcess or None on any error."""
    try:
        return subprocess.run(  # nosec B603 B607 — fixed argv, validated paths
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — every git failure is unmeasurable
        logger.debug("artifact_evidence: git %s failed: %s", " ".join(args[:2]), exc)
        return None


def _task_repo(task_id: str) -> Tuple[Optional[Path], str]:
    """``(repo_root, base_branch)`` for this task, or ``(None, branch)``.

    Repo-aware through the existing registry: an ``ftp-*`` deliverable lives in
    ICDEV[FT], and asking ICDev's tree whether it exists always answers "no".
    """
    try:
        from tools.kanban.repo_registry import resolve_task_repo
        target = resolve_task_repo(task_id)
        return target.root, target.base_branch
    except Exception as exc:  # noqa: BLE001 — a broken registry is unmeasurable
        logger.debug("artifact_evidence: repo resolve failed for %s: %s", task_id, exc)
        return BASE_DIR, "main"


def artifact_report(
    task_id: str,
    title: str = "",
    description: str = "",
    repo_root=None,
    ref: Optional[str] = None,
    fetch: bool = False,
) -> dict:
    """Do this task's declared artifacts exist on the default branch?

    Never raises. Every failure to look is ``unmeasurable`` with the reason
    attached, because a survey that reports a clean board it could not read is
    the defect this module was written for.
    """
    report = {
        "task_id": task_id,
        "state": STATE_UNMEASURABLE,
        "reason": "",
        "ref": ref or "",
        "repo": None,
        "declared": [],
        "present": [],
        "missing": [],
        #: Declared paths git is told never to track. Kept OUT of both
        #: `present` and `missing`: a .gitignore'd path is not evidence either
        #: way, and counting it as missing manufactures a finding.
        "ignored": [],
        #: {declared, found} for a path the card wrote relative to a
        #: subdirectory and that resolved UNIQUELY on the default branch.
        #: Recorded rather than silently rewritten, so a reader can see that
        #: the survey moved the goalposts and check the move.
        "resolved_relative": [],
    }

    if repo_root is not None:
        root, base_branch = Path(repo_root), None
    else:
        root, base_branch = _task_repo(task_id)
    if root is None:
        report["reason"] = "task's repo root is not configured on this host"
        return report
    report["repo"] = str(root)

    declared = declared_artifacts(title, description)
    report["declared"] = declared
    if not declared:
        report["reason"] = "card declares no artifact path — nothing to verify"
        return report

    target = ref or f"origin/{base_branch or _landed.default_branch(root)}"
    report["ref"] = target

    if fetch:
        _run_git(["fetch", "origin", "--quiet"], root, timeout=60)

    probe = _run_git(["rev-parse", "--verify", "--quiet", f"{target}^{{commit}}"],
                     root, timeout=10)
    if probe is None or probe.returncode != 0:
        report["reason"] = f"ref {target} not resolvable"
        return report

    tracked: Optional[List[str]] = None
    for path in declared:
        r = _run_git(["cat-file", "-e", f"{target}:{path}"], root, timeout=15)
        if r is None:
            report["reason"] = f"git could not be asked about {path}"
            report["present"], report["missing"] = [], []
            return report
        if r.returncode == 0:
            report["present"].append(path)
            continue

        # Absent at the path as written. TWO ways that is not a finding, and
        # both were MEASURED as false positives on the first board-wide run
        # (300 done tasks, 14 days — its ONLY two findings, both wrong). A
        # report whose findings are all wrong is one people learn to skip.

        # (1) A path git is instructed never to track cannot be on any branch,
        # so "it is not on main" says nothing about the card.
        # `args/ft_scheduler.local.yaml` (ftl-sched-03) is .gitignore line 40.
        ignored = _run_git(["check-ignore", "-q", "--", path], root, timeout=10)
        if ignored is not None and ignored.returncode == 0:
            report["ignored"].append(path)
            continue

        # (2) The card wrote the path relative to a subdirectory.
        # `families/__init__.py` (ftl-val-05) is on main at
        # `icdev_fin/backtest/families/__init__.py`. A UNIQUE suffix match
        # resolves it; two or more matches is a guess, and a guess is not
        # evidence, so an ambiguous path stays missing.
        if tracked is None:
            listing = _run_git(["ls-tree", "-r", "--name-only", target], root, timeout=60)
            tracked = ([ln.strip() for ln in (listing.stdout or "").splitlines() if ln.strip()]
                       if listing is not None and listing.returncode == 0 else [])
        suffix = f"/{path}"
        hits = [t for t in tracked if t.endswith(suffix)]
        if len(hits) == 1:
            report["present"].append(hits[0])
            report["resolved_relative"].append({"declared": path, "found": hits[0]})
            continue

        report["missing"].append(path)

    # A card whose every declared path is untrackable was never measured.
    if report["ignored"] and not report["present"] and not report["missing"]:
        report["reason"] = (
            "every declared path is gitignored — nothing the default branch "
            "could ever carry"
        )
        return report

    if not report["missing"]:
        report["state"] = STATE_PRESENT
    elif report["present"]:
        report["state"] = STATE_PARTIAL
    else:
        report["state"] = STATE_ABSENT
    return report


def delivery_evidence(
    task_id: str,
    repo_root=None,
    base_branch: Optional[str] = None,
    dispatched: Optional[bool] = None,
) -> dict:
    """Did ANYTHING build this task? The done-gate's primitive.

    Answers three questions the board cannot answer about itself, and keeps them
    apart because they fail for different reasons:

    ``dispatched``     a dispatch record exists (a transition into
                       ``in_progress``, or a ``kanban_executions`` row). Passed
                       in by the caller, which already holds a connection — this
                       module does not open a second one inside a status write.
    ``branches``       refs whose name carries the id.
    ``commits_ahead``  commits on those refs that are not on the default branch.

    ``has_evidence`` is ``True`` | ``False`` | ``None``:

      * ``True``   at least one of the three is positive.
      * ``False``  ALL THREE were measured and all three are negative — nothing
                   dispatched it, no branch carries its name, and there is
                   nothing to merge. A task in that state has not been built.
      * ``None``   the question could not be answered (no git, no repo root, an
                   unresolvable ref, or the caller did not read the dispatch
                   record). NEVER ``False``: an unreachable git must not wedge
                   every completion on the board.
    """
    out = {
        "task_id": task_id,
        "has_evidence": None,
        "reason": "",
        "dispatched": dispatched,
        "branches": [],
        "commits_ahead": None,
    }
    if dispatched:
        out["has_evidence"] = True

    if not _landed._ID_RE.match(str(task_id or "")):
        out["reason"] = "id is not id-shaped"
        return out

    if repo_root is not None:
        root, branch = Path(repo_root), base_branch
    else:
        root, branch = _task_repo(task_id)
    if root is None:
        out["reason"] = "task's repo root is not configured on this host"
        return out
    branch = branch or "main"

    # Which refs carry this id is asked through the scheduler's OWN resolver,
    # not a second copy: `_branches_for_task` already knows that a worker adds a
    # descriptive suffix, that a parent id matches its decomposed children, and
    # where the name boundary sits. A gate that resolved branches differently
    # from the merge-verify gate beside it would refuse a different population
    # than the one it was surveyed on. Lazy-imported: at the call site inside
    # `_move_task` that module is already loaded, and an import failure is
    # UNMEASURABLE (None), never "no branch".
    try:
        from tools.genesis.reflexes.kanban import _branches_for_task
        branches = list(_branches_for_task(task_id, root))
    except Exception as exc:  # noqa: BLE001 — cannot resolve refs is not "none"
        out["reason"] = f"branch resolver unavailable ({exc})"
        return out
    out["branches"] = branches

    target = f"origin/{branch}"
    probe = _run_git(["rev-parse", "--verify", "--quiet", f"{target}^{{commit}}"],
                     root, timeout=10)
    if probe is None or probe.returncode != 0:
        out["reason"] = f"ref {target} not resolvable"
        return out

    ahead = 0
    for ref in branches:
        r = _run_git(["rev-list", "--count", f"{target}..{ref}"], root, timeout=20)
        if r is None or r.returncode != 0:
            out["reason"] = f"could not compare {ref} against {target}"
            return out
        try:
            ahead += int((r.stdout or "0").strip() or 0)
        except ValueError:
            out["reason"] = f"unparseable rev-list output for {ref}"
            return out
    out["commits_ahead"] = ahead

    if out["has_evidence"] is True:
        return out
    if branches or ahead > 0:
        out["has_evidence"] = True
        return out
    if dispatched is None:
        out["reason"] = "no branch and no commits, and the dispatch record was not read"
        return out
    out["has_evidence"] = False
    out["reason"] = "no dispatch record, no branch carrying the id, nothing to merge"
    return out


# ── survey ────────────────────────────────────────────────────────────────────

def _done_tasks(window_days: Optional[int] = None, prefix: str = "",
                limit: int = 500) -> Tuple[List[dict], str]:
    """``(rows, reason)`` — done tasks worth surveying, newest first."""
    try:
        from tools.db.storage import get_connection
    except Exception as exc:  # noqa: BLE001
        return [], f"storage unavailable: {exc}"
    sql = ("SELECT id, title, description, completed_at, updated_at "
           "FROM kanban_tasks WHERE status = 'done'")
    params: List[object] = []
    if prefix:
        sql += " AND id LIKE %s"
        params.append(f"{prefix}%")
    if window_days:
        sql += " AND COALESCE(completed_at, updated_at) >= %s"
        cutoff = (_dt.datetime.now(_dt.timezone.utc)
                  - _dt.timedelta(days=int(window_days)))
        params.append(cutoff.isoformat())
    sql += " ORDER BY COALESCE(completed_at, updated_at) DESC LIMIT %s"
    params.append(int(limit))
    try:
        conn = get_connection()
        try:
            rows = [dict(r) for r in conn.execute(sql, tuple(params)).fetchall()]
        finally:
            conn.close()
        return rows, ""
    except Exception as exc:  # noqa: BLE001 — an unreadable board is unmeasurable
        return [], f"board query failed: {exc}"


def survey(window_days: Optional[int] = None, prefix: str = "",
           limit: int = 500, fetch: bool = False) -> dict:
    """For every ``done`` task: does its declared artifact exist on main?

    ``state`` describes the SURVEY, not the board: ``unmeasurable`` when the
    board could not be read or produced nothing to look at, ``measured``
    otherwise. A survey that could not run is never a survey that found nothing.
    """
    rows, reason = _done_tasks(window_days=window_days, prefix=prefix, limit=limit)
    result = {
        "state": STATE_UNMEASURABLE,
        "reason": reason,
        "window_days": window_days,
        "prefix": prefix or None,
        "done_tasks": len(rows),
        "measurable_tasks": 0,
        "artifact_present_pct": None,
        "counts": {STATE_PRESENT: 0, STATE_PARTIAL: 0,
                   STATE_ABSENT: 0, STATE_UNMEASURABLE: 0},
        "findings": [],
        "tasks": [],
    }
    if not rows:
        result["reason"] = reason or "no done tasks in scope"
        return result

    fetched: set = set()
    for row in rows:
        tid = row["id"]
        root, _branch = _task_repo(tid)
        do_fetch = bool(fetch and root is not None and str(root) not in fetched)
        if do_fetch:
            fetched.add(str(root))
        rep = artifact_report(
            tid, row.get("title") or "", row.get("description") or "",
            fetch=do_fetch,
        )
        rep["completed_at"] = str(row.get("completed_at") or row.get("updated_at") or "")
        result["counts"][rep["state"]] += 1
        result["tasks"].append(rep)
        if rep["state"] in (STATE_ABSENT, STATE_PARTIAL):
            result["findings"].append(rep)

    result["state"] = "measured"
    result["reason"] = ""
    # Every rate this file emits is None when nothing was measured — a 100%
    # fallback over an empty denominator is the perfect-score defect
    # (args/perfect_score_gate.yaml, ratcheted to 0).
    checked = (result["counts"][STATE_PRESENT] + result["counts"][STATE_PARTIAL]
               + result["counts"][STATE_ABSENT])
    result["measurable_tasks"] = checked
    result["artifact_present_pct"] = (
        round(result["counts"][STATE_PRESENT] / checked * 100, 1) if checked else None
    )
    return result


def _render(result: dict) -> str:
    lines: List[str] = []
    if result["state"] != "measured":
        lines.append(f"UNMEASURABLE: {result['reason'] or 'no reason given'}")
        lines.append("A survey that could not run is not a survey that found nothing.")
        return "\n".join(lines)
    c = result["counts"]
    lines.append(
        f"done tasks surveyed: {result['done_tasks']}  "
        f"(measurable: {result['measurable_tasks']})"
    )
    lines.append(
        f"  present {c[STATE_PRESENT]}   partial {c[STATE_PARTIAL]}   "
        f"absent {c[STATE_ABSENT]}   unmeasurable {c[STATE_UNMEASURABLE]}"
    )
    pct = result.get("artifact_present_pct")
    lines.append(
        "  artifact present: "
        + (f"{pct}%" if pct is not None
           else "not measured (no task in scope declared an artifact)")
    )
    if c[STATE_UNMEASURABLE]:
        lines.append(
            f"  NOTE: {c[STATE_UNMEASURABLE]} task(s) declare no artifact path or "
            "could not be read. That is not a clean bill of health."
        )
    if not result["findings"]:
        lines.append("\nNo done task is missing a declared artifact.")
        return "\n".join(lines)
    lines.append(f"\nFINDINGS ({len(result['findings'])}) — done, artifact not on main:")
    for f in result["findings"]:
        lines.append(
            f"  {f['task_id']:20s} {f['state']:9s} missing: {', '.join(f['missing'][:4])}")
        first = f["missing"][0] if f["missing"] else "<path>"
        lines.append(
            f"  {'':20s} re-derive: git -C {f['repo']} cat-file -e {f['ref']}:{first}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Does a done task have an artifact on the default branch? (kpr-rvfy-04)")
    ap.add_argument("--survey", action="store_true", help="survey every done task")
    ap.add_argument("--task", help="report on one task id")
    ap.add_argument("--prefix", default="",
                    help="restrict the survey to ids with this prefix")
    ap.add_argument("--window-days", type=int, default=None)
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--fetch", action="store_true",
                    help="git fetch each repo once before comparing")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.task:
        rows: List[dict] = []
        reason = ""
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
            try:
                r = conn.execute(
                    "SELECT id, title, description FROM kanban_tasks WHERE id = %s",
                    (args.task,)).fetchone()
                rows = [dict(r)] if r else []
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            reason = f"board query failed: {exc}"
        if not rows:
            out = {"task_id": args.task, "state": STATE_UNMEASURABLE,
                   "reason": reason or "no such task on the board"}
        else:
            out = artifact_report(args.task, rows[0].get("title") or "",
                                  rows[0].get("description") or "", fetch=args.fetch)
        if args.json:
            print(json.dumps(out, indent=2, default=str))
        else:
            detail = out.get("reason") or "missing: " + ", ".join(out.get("missing") or [])
            print(f"{out['task_id']}: {out['state']} — {detail}")
        return 0

    if not args.survey:
        ap.print_help()
        return 0

    result = survey(window_days=args.window_days, prefix=args.prefix,
                    limit=args.limit, fetch=args.fetch)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(_render(result))
    # Exit 2 = the survey could not be produced, which is never the same as a
    # clean survey. Findings are REPORTED, never gated (kpr-fix-03).
    return 0 if result["state"] == "measured" else 2


if __name__ == "__main__":
    raise SystemExit(main())
