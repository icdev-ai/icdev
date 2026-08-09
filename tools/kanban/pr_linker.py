#!/usr/bin/env python3
"""Link kanban tasks to their open GitHub PRs (`kanban_tasks.executor_url`).

`pr_watcher` finds a task's PR *only* through `executor_url` (or a PR URL
scraped from the description). Exactly one code path ever writes that column
for a PR: ``_push_branch_and_open_pr`` in ``tools/genesis/reflexes/kanban.py``,
and it writes it only when

  1. ``ICDEV_KANBAN_PR_FLOW`` is on, **and**
  2. the local ref ``kanban/<task_id>`` exists with commits ahead of base, **and**
  3. the branch name is exactly ``kanban/<task_id>``.

Every other route to an open PR leaves the column empty and the PR invisible to
the watcher — forever, since nothing reconciles after the fact. Measured on the
live board 2026-08-02: 77 of 2702 tasks carried a PR URL, 2625 were empty, and
**zero** descriptions contained one (so the description fallback is dead code).
Ten green kanban PRs were stranded, three of them for a single task
(``gdx-aud-01`` had PRs on ``kanban/gdx-aud-01``, ``-r2`` and ``-land``) because
retry dispatches open PRs on suffixed branches that condition 3 rejects.

This module reconciles in the other direction: enumerate open PRs, map each head
branch back to a task, and fill in the missing link. It is deliberately
conservative — an ``executor_url`` that already names a PR is never overwritten.

CLI::

    python tools/kanban/pr_linker.py --dry-run --json
    python tools/kanban/pr_linker.py --json
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from typing import Callable, Dict, List, Optional

from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

BRANCH_PREFIX = "kanban/"
_PR_URL_RE = re.compile(r"https?://[^\s]*/pull/\d+")


def branch_to_task_id(branch: str, task_ids) -> Optional[str]:
    """Map a ``kanban/<id>[-suffix]`` head branch back to its task id.

    Retry dispatches append a suffix (``-r2``, ``-d5-r2``, ``-land``), so an
    exact match is not enough. Resolve to the **longest** task id that is a
    prefix of the branch remainder at a ``-`` boundary: with tasks ``gdx-aud-01``
    and ``gdx-aud-01-d2`` both present, ``kanban/gdx-aud-01-d2-r3`` must bind to
    ``gdx-aud-01-d2``, not to the shorter id. The boundary check is what stops
    ``tsr-core-011`` from binding to ``tsr-core-01``.
    """
    if not branch or not branch.startswith(BRANCH_PREFIX):
        return None
    remainder = branch[len(BRANCH_PREFIX):]
    if not remainder:
        return None
    ids = task_ids if isinstance(task_ids, (set, frozenset)) else set(task_ids)
    if remainder in ids:
        return remainder
    best: Optional[str] = None
    for tid in ids:
        if not remainder.startswith(tid):
            continue
        if len(remainder) != len(tid) and remainder[len(tid)] != "-":
            continue
        if best is None or len(tid) > len(best):
            best = tid
    return best


def fetch_open_prs(
    *,
    runner: Optional[Callable] = None,
    gh_bin: str = "gh",
    limit: int = 200,
) -> List[dict]:
    """Return open PRs as ``[{number, url, headRefName, createdAt}, ...]``."""
    cmd = [
        gh_bin, "pr", "list", "--state", "open", "--limit", str(limit),
        "--json", "number,url,headRefName,createdAt",
    ]
    run = runner or subprocess.run
    proc = run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh pr list failed: exit={proc.returncode} "
            f"stderr={(proc.stderr or '').strip()[:200]}"
        )
    try:
        return json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh pr list returned non-JSON: {exc}") from exc


def _stored_pr_is_open(stored_url: str, open_numbers: set) -> bool:
    """True when the PR named by ``stored_url`` is among the forge's open PRs.

    Derived from the open-PR listing already fetched, so detecting a stale link
    costs no extra API call. An unparseable url counts as OPEN: the caller only
    acts on a definite "not open", and being wrong in that direction would
    redirect a live link.
    """
    m = _PR_URL_RE.search(stored_url or "")
    if not m:
        return True
    number = next((g for g in m.groups() if g and g.isdigit()), None)
    if number is None:
        number = "".join(ch for ch in m.group(0) if ch.isdigit())
    return (not number) or (number in open_numbers)


def link_open_prs(
    get_connection,
    *,
    runner: Optional[Callable] = None,
    gh_bin: str = "gh",
    limit: int = 200,
    dry_run: bool = False,
) -> Dict[str, list]:
    """Fill in `executor_url` for tasks whose open PR is not linked.

    Returns ``{linked, ambiguous, already_linked, unmatched}``.

    Never overwrites an `executor_url` that already names a PR: a stale link is
    the watcher's problem to resolve, but a *wrong* link would make it merge
    someone else's branch. Silence beats a bad write here.
    """
    result: Dict[str, list] = {
        "linked": [], "ambiguous": [], "already_linked": [], "unmatched": [],
        "relinked": [], "stale_ambiguous": [],
    }
    prs = fetch_open_prs(runner=runner, gh_bin=gh_bin, limit=limit)
    if not prs:
        return result

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, executor_url FROM kanban_tasks"
        ).fetchall()
        existing = {r["id"]: (r["executor_url"] or "") for r in rows}

        # Group candidate PRs by the task their head branch resolves to.
        by_task: Dict[str, List[dict]] = {}
        for pr in prs:
            branch = pr.get("headRefName") or ""
            tid = branch_to_task_id(branch, existing.keys())
            if tid is None:
                if branch.startswith(BRANCH_PREFIX):
                    result["unmatched"].append(
                        {"branch": branch, "url": pr.get("url", "")}
                    )
                continue
            by_task.setdefault(tid, []).append(pr)

        # Every PR number the forge says is OPEN. A stored link whose number is
        # absent from this set is provably not open — no extra API call needed.
        open_numbers = {
            str(p.get("number")) for p in prs if p.get("number") is not None
        }

        for tid, cands in sorted(by_task.items()):
            current = existing.get(tid, "")
            match = _PR_URL_RE.search(current)
            if match:
                if _stored_pr_is_open(current, open_numbers):
                    result["already_linked"].append({"task_id": tid, "url": current})
                    continue
                # STALE LINK. The stored PR is closed or merged while an open PR
                # sits on this task's own branch. Until now this fell in the gap
                # between two components: the linker declined to overwrite (a
                # WRONG link would make the watcher merge someone else's branch,
                # which is the right instinct) and deferred to "the watcher's
                # problem to resolve" — but the watcher polls the stored url,
                # sees a closed PR, concludes there is nothing to do, and never
                # discovers the open one. Nobody owned it.
                #
                # Measured 2026-08-09: sbx-fld-05 pointed at #1355 (CLOSED) while
                # #1463 was open on kanban/sbx-fld-05. It stayed an unmergeable
                # draft until a human looked.
                #
                # Overwriting is safe HERE and only here, because every candidate
                # reached this point via branch_to_task_id — the PR is on the
                # task's own branch — and the old link is provably not open, so
                # nothing in flight is being redirected.
                if len(cands) > 1:
                    # Ambiguity plus staleness is a human's call; relinking to a
                    # guess is how the wrong branch gets merged.
                    result.setdefault("stale_ambiguous", []).append({
                        "task_id": tid, "was": current,
                        "candidates": [c.get("url", "") for c in cands],
                    })
                    logger.warning(
                        "pr_linker: %s has a stale link (%s) and %d open PRs — "
                        "refusing to guess", tid, current, len(cands),
                    )
                    continue
                fresh = cands[0]
                result.setdefault("relinked", []).append({
                    "task_id": tid, "was": current, "url": fresh.get("url", ""),
                    "branch": fresh.get("headRefName", ""),
                })
                logger.info("pr_linker: %s relinked from closed %s to open %s",
                            tid, current, fresh.get("url", ""))
                if not dry_run:
                    conn.execute(
                        "UPDATE kanban_tasks SET executor_url = %s WHERE id = %s",
                        (fresh.get("url", ""), tid),
                    )
                continue
            # Newest PR wins — a retry's PR supersedes the attempt it replaced.
            cands = sorted(
                cands, key=lambda p: (p.get("createdAt") or "", p.get("number") or 0)
            )
            chosen = cands[-1]
            if len(cands) > 1:
                result["ambiguous"].append({
                    "task_id": tid,
                    "chosen": chosen.get("url", ""),
                    "others": [c.get("url", "") for c in cands[:-1]],
                })
                logger.warning(
                    "pr_linker: %s has %d open PRs; linking newest %s (others: %s)",
                    tid, len(cands), chosen.get("url", ""),
                    ", ".join(c.get("url", "") for c in cands[:-1]),
                )
            entry = {
                "task_id": tid,
                "url": chosen.get("url", ""),
                "branch": chosen.get("headRefName", ""),
            }
            if not dry_run:
                conn.execute(
                    "UPDATE kanban_tasks SET executor_url = %s WHERE id = %s",
                    (entry["url"], tid),
                )
            result["linked"].append(entry)
        if result["linked"] and not dry_run:
            try:
                conn.commit()
            except Exception:  # noqa: BLE001 — autocommit backends
                pass
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Link kanban tasks to their open GitHub PRs"
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be linked without writing")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    ap.add_argument("--limit", type=int, default=200,
                    help="Max open PRs to enumerate")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from tools.db.storage import get_connection

    report = link_open_prs(
        get_connection, limit=args.limit, dry_run=args.dry_run
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        verb = "would link" if args.dry_run else "linked"
        print(f"pr_linker: {verb} {len(report['linked'])} task(s); "
              f"{len(report['already_linked'])} already linked; "
              f"{len(report['ambiguous'])} ambiguous; "
              f"{len(report['unmatched'])} unmatched branch(es)")
        for e in report["linked"]:
            print(f"  {e['task_id']:28} -> {e['url']}")
        for e in report["ambiguous"]:
            print(f"  ! {e['task_id']}: {len(e['others'])} other open PR(s): "
                  f"{', '.join(e['others'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
