# CUI // SP-CTI
"""kpr-watch-01: the merge-eligibility decision table — one copy, two consumers.

WHY THIS EXISTS. `pr_watcher._sweep_unlinked_prs` decided merge eligibility as a
ladder of bare ``continue`` statements — draft, hold label, wrong base, not
MERGEABLE, not passing, changes requested. Every one of those refusals was
SILENT except the label case, and the whole sweep returned immediately under
``--dry-run``. So the pipeline could MERGE a PR but could not ANSWER "which PRs
are awaiting merge, and why is each one not merging". The actor existed; the
observer did not.

THE PATTERN IS THE POINT. This is the same one-table-two-consumers shape
CLAUDE.md already mandates for ``decide_discrimination`` (shared by
``tools/ci/red_first_gate.py`` and ``tools/security/reproduction_validator.py``):
the merger and the report must read the SAME table, so a report can never drift
into describing a merge policy the merger does not have. Do not write a second
copy of this ladder anywhere.

  * ``pr_watcher._sweep_unlinked_prs`` consumes it to DECIDE (merges on ``ready``).
  * ``python -m tools.ci.merge_readiness`` consumes it to REPORT (never merges).

PURITY. ``classify_merge_readiness`` does no I/O: no subprocess, no database, no
network, no clock, no LLM. It takes the parsed ``gh pr list --json`` dict plus
the two facts the ladder needed from outside (the default branch, and the set of
PR urls a kanban task already points at) and returns ``(state, reason)``.

ORDER IS THE LADDER'S ORDER, deliberately. Which refusal a blocked PR reports is
a choice, and the honest choice is the one the merger actually makes first — so
a reader of the report is reading the merger's own reasoning, not a second
opinion about it. Reordering these branches would not change any merge decision
(they are a conjunction) but it WOULD make the report describe a different
process from the one that runs.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from typing import Any, Dict, FrozenSet, Iterable, List, NamedTuple, Optional

from tools.ci import error_classifier as ec

#: Repo root, resolved from THIS FILE and never from ``os.getcwd()`` — this
#: module is run from worktrees, from CI checkouts and as ``python -m``.
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ────────────────────────────────────────────────────────────────────────────
# Vocabulary
# ────────────────────────────────────────────────────────────────────────────

MERGED = "merged"
READY = "ready"
DRAFT = "draft"
HELD_LABEL = "held_label"
WRONG_BASE = "wrong_base"
CONFLICTING = "conflicting"
AWAITING_CI = "awaiting_ci"
CI_FAILED = "ci_failed"
NO_CHECKS = "no_checks"
CHANGES_REQUESTED = "changes_requested"
LINKED = "linked"
UNKNOWN = "unknown"

#: Every state this table can return, in ladder order. ``unknown`` is last
#: because it is the degenerate case (a PR record with no url), not a rung.
MERGE_STATES: tuple = (
    MERGED, LINKED, DRAFT, HELD_LABEL, WRONG_BASE, CONFLICTING,
    NO_CHECKS, CI_FAILED, AWAITING_CI, CHANGES_REQUESTED, READY, UNKNOWN,
)

#: States in which the pipeline is waiting on something OTHER than a human
#: decision about this PR's content. Used by the report's summary line.
BLOCKED_ON_AUTOMATION = frozenset({AWAITING_CI, CONFLICTING, CI_FAILED, NO_CHECKS})

#: A human may open a PR to discuss rather than to land, and the cost of
#: guessing wrong is a merge nobody asked for — so the escape hatch is cheap,
#: obvious, and checked early. Canonical home; ``pr_watcher`` re-exports this as
#: ``_NO_AUTOMERGE_LABELS`` so there is exactly one list.
NO_AUTOMERGE_LABELS: FrozenSet[str] = frozenset({
    "hold", "do-not-merge", "do not merge", "wip", "no-automerge", "blocked",
})


class MergeReadiness(NamedTuple):
    """The verdict. Unpacks as the ``(state, reason)`` 2-tuple."""

    state: str
    reason: str

    @property
    def ready(self) -> bool:
        return self.state == READY


# ────────────────────────────────────────────────────────────────────────────
# The decision table
# ────────────────────────────────────────────────────────────────────────────


def classify_merge_readiness(
    pr: Dict[str, Any],
    *,
    default_branch: str,
    linked_urls: Iterable[str] = (),
) -> MergeReadiness:
    """Why is this PR not merging? Pure — no I/O, no clock, no LLM.

    ``pr`` is one entry of ``gh pr list --json url,isDraft,baseRefName,
    mergeable,labels,statusCheckRollup,reviews,state``. Anything missing is
    treated as unknown rather than as permission.

    Returns ``ready`` EXACTLY when ``_sweep_unlinked_prs`` would have merged:
    unlinked, not a draft, no hold label, based on ``default_branch``, reported
    MERGEABLE, every check green, and no reviewer asking for changes. That
    equivalence is the acceptance test (``tests/test_merge_readiness.py``).

    Two distinctions this refuses to blur, because each sends you somewhere
    different — the same reason ``capability_consumption`` keeps ``empty``,
    ``absent`` and ``column_unpopulated`` apart:

    * ``no_checks`` (rollup is EMPTY — no workflow has reported, which may mean
      none is configured for this branch) is not ``awaiting_ci`` (checks exist
      and are still running). One needs a workflow; the other needs patience.
    * ``mergeable`` of ``UNKNOWN`` — GitHub has not finished computing
      mergeability — is not the same as ``CONFLICTING``. Both land on the
      ``conflicting`` STATE, because the merger refuses both identically and
      the state must not claim otherwise, but the ``reason`` names which it was
      so nobody rebases a branch that has no conflict.
    """
    linked = {(u or "").strip() for u in linked_urls}
    url = (pr.get("url") or "").strip()

    # A terminal fact, ahead of everything. `gh pr list --state open` never
    # yields one, so this rung is unreachable from the sweep and cannot affect
    # parity — but a report handed arbitrary PR json must not call a merged PR
    # "awaiting" anything.
    if (pr.get("state") or "").upper() == "MERGED":
        return MergeReadiness(MERGED, "already merged")

    if not url:
        # The sweep refuses here too (`if not url or url in linked`). Reported
        # separately from `linked` because it is a malformed record, not a PR
        # the task path owns.
        return MergeReadiness(
            UNKNOWN, "PR record carries no url -- cannot be identified or merged")

    if url in linked:
        return MergeReadiness(
            LINKED, "a kanban task points at this PR -- the task path owns it")

    if pr.get("isDraft"):
        # GitHub refuses `gh pr merge` on a draft outright. An unlinked PR is
        # never un-drafted: for a human the draft IS the "not ready" signal.
        return MergeReadiness(DRAFT, "draft -- mark it ready for review to land it")

    labels = {(lbl.get("name") or "").strip().lower()
              for lbl in (pr.get("labels") or [])}
    held = sorted(labels & NO_AUTOMERGE_LABELS)
    if held:
        return MergeReadiness(
            HELD_LABEL, "carries hold label(s): " + ", ".join(held))

    base = (pr.get("baseRefName") or "").strip()
    if base != default_branch:
        return MergeReadiness(
            WRONG_BASE,
            "base is %r, not the default branch %r"
            % (base or "<unset>", default_branch))

    mergeable = (pr.get("mergeable") or "").strip().upper()
    if mergeable != "MERGEABLE":
        if mergeable == "CONFLICTING":
            detail = "conflicts with the base branch -- rebase it"
        elif mergeable in ("", "UNKNOWN"):
            detail = ("GitHub has not finished computing mergeability "
                      "(mergeable=%s) -- not a conflict" % (mergeable or "<absent>"))
        else:
            detail = "mergeable=%s" % mergeable
        return MergeReadiness(CONFLICTING, detail)

    if not ec.is_passing(pr):
        rollup = list(pr.get("statusCheckRollup") or [])
        if not rollup:
            return MergeReadiness(
                NO_CHECKS, "no check has reported yet -- empty status rollup")
        if ec.is_ci_failed(pr):
            return MergeReadiness(CI_FAILED, "failing checks: " + _failing_names(rollup))
        return MergeReadiness(
            AWAITING_CI, "checks still running: " + _pending_names(rollup))

    if ec.is_changes_requested(pr):
        # A reviewer asked for changes. Merging over that is the one thing an
        # automation must never do.
        return MergeReadiness(
            CHANGES_REQUESTED, "a reviewer requested changes")

    return MergeReadiness(READY, "green, mergeable and unblocked")


def _check_name(check: Dict[str, Any]) -> str:
    return (check.get("name") or check.get("context") or "?").strip() or "?"


def _failing_names(rollup: List[Dict[str, Any]]) -> str:
    bad = [_check_name(c) for c in rollup
           if (c.get("conclusion") or "").upper()
           in ("FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED")
           or (c.get("state") or "").upper() == "FAILURE"]
    return ", ".join(sorted(set(bad))) or "unnamed check"


def _pending_names(rollup: List[Dict[str, Any]]) -> str:
    pending = [_check_name(c) for c in rollup
               if not (c.get("conclusion") or "").strip()
               and (c.get("state") or "").upper() != "SUCCESS"]
    return ", ".join(sorted(set(pending))) or "unnamed check"


# ────────────────────────────────────────────────────────────────────────────
# Report — READ ONLY. Nothing below merges, pushes, un-drafts or closes.
# ────────────────────────────────────────────────────────────────────────────

_GH_FIELDS = ("number,url,title,headRefName,baseRefName,isDraft,mergeable,"
              "labels,statusCheckRollup,reviews,state,updatedAt")


def list_open_prs(*, runner=None, limit: int = 100,
                  gh_bin: str = "gh") -> List[Dict[str, Any]]:
    """Every open PR as ``gh`` reports it. Raises on failure — see `build_report`."""
    if runner is None:
        runner = subprocess.run
    proc = runner(
        [gh_bin, "pr", "list", "--state", "open", "--limit", str(int(limit)),
         "--json", _GH_FIELDS],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60,
    )
    if getattr(proc, "returncode", 1) != 0:
        raise RuntimeError(
            "gh pr list failed (rc=%s): %s"
            % (getattr(proc, "returncode", "?"),
               (getattr(proc, "stderr", "") or "").strip()[:300]))
    return list(json.loads(getattr(proc, "stdout", "") or "[]"))


def linked_pr_urls(get_connection=None) -> FrozenSet[str]:
    """PR urls a kanban task already points at. Raises if the board is unreadable.

    Imported lazily so this module stays importable (and its pure half stays
    testable) on a box with no database and no ``psycopg2``.
    """
    from tools.ci.pr_watcher import list_pr_tasks  # local: avoids an import cycle

    if get_connection is None:
        from tools.db.storage import get_connection as get_connection  # type: ignore
    return frozenset(
        (t.get("pr_url") or "").strip()
        for t in list_pr_tasks(get_connection)
        if (t.get("pr_url") or "").strip()
    )


def build_report(
    prs: List[Dict[str, Any]],
    *,
    default_branch: str,
    linked_urls: Iterable[str] = (),
    linked_lookup_ok: bool = True,
) -> Dict[str, Any]:
    """Classify every PR. Pure — takes data, returns data."""
    linked = frozenset((u or "").strip() for u in linked_urls)
    rows: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for pr in prs:
        verdict = classify_merge_readiness(
            pr, default_branch=default_branch, linked_urls=linked)
        counts[verdict.state] = counts.get(verdict.state, 0) + 1
        rows.append({
            "number": pr.get("number"),
            "url": (pr.get("url") or "").strip(),
            "title": pr.get("title") or "",
            "head": pr.get("headRefName") or "",
            "base": pr.get("baseRefName") or "",
            "state": verdict.state,
            "reason": verdict.reason,
            "ready": verdict.ready,
            "mergeable": (pr.get("mergeable") or "").upper(),
            "is_draft": bool(pr.get("isDraft")),
            "labels": sorted((lbl.get("name") or "").strip()
                             for lbl in (pr.get("labels") or [])),
            "linked": (pr.get("url") or "").strip() in linked,
        })
    rows.sort(key=lambda r: (r["state"] != READY, r["number"] or 0))
    return {
        "default_branch": default_branch,
        # Never silent: without the board we cannot tell linked from unlinked,
        # so every PR is classified on its own merits and the caller is told.
        "linked_lookup_ok": bool(linked_lookup_ok),
        "linked_count": len(linked),
        "total": len(rows),
        "ready": counts.get(READY, 0),
        "counts": dict(sorted(counts.items())),
        "prs": rows,
    }


def render_table(report: Dict[str, Any]) -> str:
    """ASCII-only table — a box-drawing character raises on a cp1252 console."""
    lines: List[str] = []
    if not report.get("linked_lookup_ok", True):
        lines.append(
            "WARNING: the kanban board was unreadable, so no PR could be "
            "identified as task-linked. States below are otherwise accurate.")
    lines.append(
        "%d open PR(s) against %s -- %d ready to merge"
        % (report["total"], report["default_branch"], report["ready"]))
    if not report["prs"]:
        return "\n".join(lines)
    lines.append("")
    lines.append("%-7s %-18s %-38s %s" % ("PR", "STATE", "BRANCH", "REASON"))
    lines.append("%-7s %-18s %-38s %s" % ("-" * 7, "-" * 18, "-" * 38, "-" * 40))
    for row in report["prs"]:
        lines.append("%-7s %-18s %-38s %s" % (
            "#%s" % row["number"], row["state"],
            (row["head"] or "?")[:38], row["reason"]))
    lines.append("")
    lines.append("by state: " + ", ".join(
        "%s=%d" % (k, v) for k, v in report["counts"].items()) or "by state: -")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """Read-only. Exit 0 = report produced, 2 = the report COULD NOT BE PRODUCED.

    A report that listed nothing must not read the same as a repo with nothing
    open, so an unreadable ``gh pr list`` is exit 2 and never an empty table.
    """
    parser = argparse.ArgumentParser(
        prog="python -m tools.ci.merge_readiness",
        description="Which open PRs are awaiting merge, and why is each one "
                    "not merging? Read-only: never merges, pushes or closes.",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable")
    parser.add_argument("--state", action="append", default=None,
                        metavar="STATE",
                        help="only show PRs in this state (repeatable); one of: "
                             + ", ".join(MERGE_STATES))
    parser.add_argument("--limit", type=int, default=100,
                        help="max PRs to list (default 100)")
    parser.add_argument("--default-branch", default=None,
                        help="skip default-branch resolution and use this")
    parser.add_argument("--from-json", default=None, metavar="PATH",
                        help="classify a saved `gh pr list --json` file instead "
                             "of calling gh (offline / parity checking)")
    args = parser.parse_args(argv)

    if args.state:
        bad = [s for s in args.state if s not in MERGE_STATES]
        if bad:
            parser.error("unknown state(s): %s" % ", ".join(bad))

    # ── inputs ────────────────────────────────────────────────────────────
    if args.from_json:
        path = pathlib.Path(args.from_json)
        try:
            prs = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — reported, never swallowed
            return _fail(args.json, "cannot read %s: %s" % (path, exc))
        if not isinstance(prs, list):
            return _fail(args.json, "%s does not contain a PR list" % path)
    else:
        try:
            prs = list_open_prs(limit=args.limit)
        except Exception as exc:  # noqa: BLE001
            return _fail(args.json, "cannot list open PRs: %s" % exc)

    default_branch = args.default_branch
    if not default_branch:
        from tools.ci.pr_watcher import repo_default_branch  # local: import cycle
        default_branch = repo_default_branch()

    linked_lookup_ok = True
    try:
        linked = linked_pr_urls()
    except Exception as exc:  # noqa: BLE001 — degraded, and it SAYS so
        linked, linked_lookup_ok = frozenset(), False
        print("warning: kanban board unreadable (%s)" % exc, file=sys.stderr)

    report = build_report(prs, default_branch=default_branch,
                          linked_urls=linked, linked_lookup_ok=linked_lookup_ok)
    if args.state:
        wanted = set(args.state)
        report["prs"] = [r for r in report["prs"] if r["state"] in wanted]
        report["filtered_to"] = sorted(wanted)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_table(report))
    return 0


def _fail(as_json: bool, message: str) -> int:
    if as_json:
        print(json.dumps({"ok": False, "error": message}, indent=2))
    else:
        print("ERROR: %s" % message, file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover — exercised via main(argv)
    sys.exit(main())
