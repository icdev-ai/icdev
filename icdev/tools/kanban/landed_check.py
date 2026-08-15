#!/usr/bin/env python3
# CUI // SP-CTI
"""task -> main: is this task id ALREADY on the default branch? (trust-disc-05)

The board tracks **task -> PR** and nothing checked **task -> main**. Those are
different questions, and the gap between them files reverts.

Measured 2026-08-15: two of the five cards sitting in ``pr_opened`` had their
work already merged under a DIFFERENT PR number — ``ctx-perf-02`` landed as
#1641 and ``ctx-trust-02`` as #1638 — while #1646 and #1651 stayed open against
them. Both conflicted, because both re-apply changes already present against
files that have since moved on. #1651's diff against main was -38/+26 on
``rest_v1.py``: merging it would have DELETED 38 lines main currently has. A
revert wearing a feature's clothes, and every gate on the board said green,
because every gate on the board asks about the PR.

The check is one ``git log --grep`` against ``origin/<default>``. What makes it
usable rather than noisy is the classification underneath it:

``merge_ref``
    the commit is a merge whose subject names a branch carrying this task id
    (``Merge pull request #1647 from icdev-ai/kanban/ctx-enf-01``). The branch
    is in main. Strongest evidence there is.
``subject``
    the id appears in the commit SUBJECT — ``fix(cortex): REST v1 ran the TRUST
    chain twice (#ctx-trust-02) (#1638)``. This is the house convention, and it
    covers the squash-merge case where no merge commit survives.
``body``
    the id appears ONLY in the body. This one is **advisory and never blocks**,
    because a body mention is very often a reference rather than a landing:
    commit ``a758250c0`` says "that is exactly the defect ctx-trust-02 removed"
    while implementing a different task entirely. A gate that read that as
    "ctx-trust-02 landed here" would be confidently wrong about which commit did
    the work.

FAIL-OPEN throughout. No git, no origin ref, a timeout, an id that is not
id-shaped — all report ``checked: False`` and ``landed: False``. An unreachable
git must never wedge dispatch. Only a positive, boundary-matched hit is a
finding, and the report always says which of the two "no finding" cases it is,
so an unavailable check can never read as a clean one.

The companion half is :func:`rival_prs`. ``ctx-enf-01`` had two PRs open at once
(#1640 and #1647) and only the one on ``kanban/<task_id>`` can settle the card —
the done-gate resolves a task's branch by name, so work landed from a rival
branch leaves the card stranded even after it merges.

Headless::

    python -m tools.kanban.landed_check --task ctx-perf-02 --json
    python -m tools.kanban.landed_check --all --json      # every non-terminal task
    python -m tools.kanban.landed_check --all --gate      # exit 1 on a finding
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404 — git/gh plumbing; every argv element is validated
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("kanban.landed_check")

#: Evidence tiers, strongest first. See the module docstring.
CONFIDENCE_MERGE_REF = "merge_ref"
CONFIDENCE_SUBJECT = "subject"
CONFIDENCE_BODY = "body"

#: The tiers that justify refusing work. ``body`` is deliberately absent: a body
#: mention is a reference at least as often as it is a landing, and a check that
#: blocks on it stops legitimate work while pointing at the wrong commit.
BLOCKING_CONFIDENCE = (CONFIDENCE_MERGE_REF, CONFIDENCE_SUBJECT)

_CONFIDENCE_RANK = {
    CONFIDENCE_MERGE_REF: 3,
    CONFIDENCE_SUBJECT: 2,
    CONFIDENCE_BODY: 1,
}

#: What a task id is allowed to look like. Anything else is refused rather than
#: interpolated into a regex or an argv: `<task_prefix><epic_key>-<N>` is the
#: whole board contract, and an id outside it is a bug upstream, not input to
#: sanitise. Refusing reports ``checked: False``, i.e. fail-open.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,80}$")

#: Field/record separators for ``git log --format``. Chosen because they cannot
#: occur in a commit message, unlike any printable delimiter.
_FS = "\x1f"
_RS = "\x1e"

#: Max ids per ``git log`` invocation. One call answers for the whole batch
#: (git ORs repeated ``--grep``), and the full 7,347-commit history costs 0.26s
#: whether the batch is 1 id or 3 — but Windows caps a command line at ~32k, so
#: a board-wide sweep is chunked rather than handed over in one argv.
_MAX_IDS_PER_CALL = 150

#: ``off`` skips the check entirely; ``warn`` (default) reports and lets the work
#: proceed; ``enforce`` additionally refuses to dispatch or open a PR. Default is
#: warn because the first thing a new gate must do is be measured — an enforcing
#: check whose fire rate has never been observed is the failure mode this repo
#: has shipped before (the PreToolUse checks that spent months behind `|| true`).
#:
#: SURVEY, 2026-08-15, live board + 7,347 commits of origin/main:
#:
#:   * 3,176 ``done`` tasks swept in 56.8s (22 chunked ``git log`` calls).
#:     1,003 (31.6%) were found on main — 545 ``merge_ref``, 458 ``subject``.
#:     That is the detector's COVERAGE, not its error rate: the other 68% landed
#:     by squash-merge with no task id in the subject, which this check misses by
#:     design (a miss changes nothing; a false hit stops real work).
#:   * 207 (6.5%) matched on the BODY only. That tier does not block, and the
#:     sample shows why: ``dm-prod-02``, ``dm-domain-02`` and ``dm-contract-02``
#:     all matched the same multi-task commit body, and ``diag-716d970fdb``
#:     matched a commit that merely cites it.
#:   * Fire rate on the population the gate actually sees (the 10 non-terminal
#:     tasks on the board that day): 0.
#:
#: Re-run the survey before ever defaulting this to ``enforce``:
#: ``python -m tools.kanban.landed_check --all --status done --no-prs --json``.
_MODE_ENV = "KANBAN_LANDED_CHECK"
_VALID_MODES = ("off", "warn", "enforce")


def mode() -> str:
    """Current enforcement posture: ``off`` | ``warn`` | ``enforce``."""
    raw = (os.environ.get(_MODE_ENV) or "warn").strip().lower()
    if raw in ("0", "false", "no", "none"):
        return "off"
    if raw in ("1", "true", "yes"):
        return "enforce"
    return raw if raw in _VALID_MODES else "warn"


def _run_git(args: Sequence[str], cwd, timeout: int = 30):
    """Run a git command, returning the CompletedProcess or None on any error."""
    try:
        return subprocess.run(  # nosec B603 B607 — fixed argv, validated ids
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — every git failure is fail-open
        logger.debug("landed_check: git %s failed: %s", " ".join(args[:2]), exc)
        return None


def default_branch(repo_root=None) -> str:
    """Resolve the default branch name (origin/HEAD -> main), best-effort."""
    r = _run_git(["symbolic-ref", "refs/remotes/origin/HEAD"],
                 repo_root or BASE_DIR, timeout=10)
    if r is not None and r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().rsplit("/", 1)[-1]
    return "main"


def _grep_pattern(task_id: str) -> str:
    """An ERE that matches ``task_id`` only at a name boundary.

    ``-`` and ``_`` count as word characters here, so ``ctx-perf-02`` does not
    match ``ctx-perf-021`` and a PARENT id does not match its decomposed
    children's commits (``dwo-mcp-03-d5`` vs ``dwo-mcp-03-d5-d1``). That is the
    strict direction on purpose: a false "already landed" stops real work, while
    a miss only leaves today's behaviour unchanged.
    """
    escaped = task_id.replace(".", r"\.")
    return rf"(^|[^A-Za-z0-9_-]){escaped}([^A-Za-z0-9_-]|$)"


def _classify(task_id: str, subject: str, body: str) -> Optional[str]:
    """Strongest evidence this commit is ``task_id``'s landing, or None."""
    pat = re.compile(_grep_pattern(task_id))
    if pat.search(subject or ""):
        # A merge commit names the branch it merged, so an id inside one means
        # a branch carrying this task went into the default branch.
        if (subject or "").lstrip().startswith("Merge "):
            return CONFIDENCE_MERGE_REF
        return CONFIDENCE_SUBJECT
    if pat.search(body or ""):
        return CONFIDENCE_BODY
    return None


def check_landed_bulk(
    task_ids: Iterable[str],
    repo_root=None,
    branch: Optional[str] = None,
    ref: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, dict]:
    """``{task_id: report}`` for every id, from ONE ``git log`` per chunk.

    The single-id path (:func:`check_landed`) is this function with one id, so
    there is exactly one implementation of "is it on main" and the board sweep
    cannot drift from the dispatch gate.

    ``ref`` overrides the compared ref outright (used by tests and by external
    repos); otherwise it is ``origin/<branch or default>``.
    """
    ids = [str(t) for t in task_ids if str(t or "").strip()]
    root = repo_root or BASE_DIR
    br = branch or default_branch(root)
    target = ref or f"origin/{br}"

    reports: Dict[str, dict] = {}
    valid: List[str] = []
    for tid in ids:
        if not _ID_RE.match(tid):
            reports[tid] = _empty_report(tid, target, "id is not id-shaped")
        else:
            valid.append(tid)
            reports[tid] = _empty_report(tid, target, "")

    # Does the ref even exist? Answering "nothing landed" from a repo that has
    # never fetched origin is the false-clean this module exists to avoid.
    if valid:
        probe = _run_git(["rev-parse", "--verify", "--quiet", f"{target}^{{commit}}"],
                         root, timeout=10)
        if probe is None or probe.returncode != 0:
            for tid in valid:
                reports[tid] = _empty_report(tid, target, f"ref {target} not resolvable")
            return reports

    for chunk in (valid[i:i + _MAX_IDS_PER_CALL]
                  for i in range(0, len(valid), _MAX_IDS_PER_CALL)):
        args = ["log", "-E", f"--format=%H{_FS}%s{_FS}%b{_RS}"]
        for tid in chunk:
            args.append(f"--grep={_grep_pattern(tid)}")
        args.append(target)
        out = _run_git(args, root, timeout=120)
        if out is None or out.returncode != 0:
            for tid in chunk:
                reports[tid] = _empty_report(tid, target, "git log failed")
            continue
        for record in out.stdout.split(_RS):
            record = record.strip("\n\r")
            if not record.strip():
                continue
            parts = record.split(_FS)
            if len(parts) < 2:
                continue
            sha, subject = parts[0].strip(), parts[1]
            body = parts[2] if len(parts) > 2 else ""
            for tid in chunk:
                evidence = _classify(tid, subject, body)
                if not evidence:
                    continue
                rep = reports[tid]
                rep["checked"] = True
                if len(rep["commits"]) < limit:
                    rep["commits"].append(
                        {"sha": sha[:9], "subject": subject.strip(), "evidence": evidence})
                if _CONFIDENCE_RANK[evidence] > _CONFIDENCE_RANK.get(
                        rep["confidence"] or "", 0):
                    rep["confidence"] = evidence
        for tid in chunk:
            rep = reports[tid]
            rep["checked"] = True
            rep["landed"] = rep["confidence"] in BLOCKING_CONFIDENCE
            rep["referenced"] = bool(rep["confidence"])
    return reports


def _empty_report(task_id: str, target: str, reason: str) -> dict:
    return {
        "task_id": task_id,
        "ref": target,
        "checked": not reason,
        "reason": reason,
        "landed": False,
        "referenced": False,
        "confidence": None,
        "commits": [],
    }


def check_landed(task_id: str, repo_root=None, branch: Optional[str] = None,
                 ref: Optional[str] = None) -> dict:
    """Is ``task_id`` already on the default branch? See the module docstring."""
    return check_landed_bulk([task_id], repo_root=repo_root, branch=branch,
                             ref=ref)[str(task_id)]


def rival_prs(task_id: str, repo_root=None) -> dict:
    """Open PRs for this task, split by whether they can settle the card.

    Only a PR whose head is ``kanban/<task_id>`` settles it: the done-gate finds
    a task's work by resolving branches whose NAME carries the id, and the
    runner's own landing path pushes that exact branch. ``ctx-enf-01`` had #1640
    and #1647 open simultaneously; whichever merged first, the card could only
    be closed by the canonical one.

    ``settles`` is True when a canonical PR exists, False when there are open PRs
    but none is canonical, and None when there are none to judge (or the lookup
    was unavailable — ``checked`` distinguishes those).
    """
    report = {"task_id": task_id, "checked": False, "canonical": [],
              "rivals": [], "settles": None}
    if not _ID_RE.match(str(task_id or "")):
        return report
    root = repo_root or BASE_DIR
    try:
        from tools.genesis.reflexes import kanban as _k
        prs = _k._open_prs_for_task(task_id, root)
    except Exception as exc:  # noqa: BLE001 — discovery is best-effort
        logger.debug("landed_check: open-PR lookup failed for %s: %s", task_id, exc)
        return report

    report["checked"] = True
    canonical_branch = f"kanban/{task_id}"
    for pr in prs:
        branch = (pr.get("branch") or "").split("origin/", 1)[-1]
        entry = {"url": pr.get("url", ""), "number": pr.get("number"), "branch": branch}
        if branch == canonical_branch:
            report["canonical"].append(entry)
        else:
            report["rivals"].append(entry)
    if report["canonical"]:
        report["settles"] = True
    elif report["rivals"]:
        report["settles"] = False
    return report


def preflight(task_id: str, repo_root=None, branch: Optional[str] = None,
              ref: Optional[str] = None, with_prs: bool = True) -> dict:
    """Both halves plus the verdict: should this task be dispatched / PR'd?

    ``blocking`` is True only when the evidence is strong (:data:`BLOCKING_CONFIDENCE`)
    AND the mode is ``enforce``. ``warn`` never sets it — callers still log the
    finding, which is the whole acceptance criterion; refusing is opt-in until
    the fire rate has been observed on a real board.
    """
    current_mode = mode()
    if current_mode == "off":
        report = _empty_report(str(task_id), ref or "", "check disabled (%s=off)" % _MODE_ENV)
        report.update({"mode": "off", "blocking": False, "prs": None})
        return report

    report = check_landed(task_id, repo_root=repo_root, branch=branch, ref=ref)
    report["mode"] = current_mode
    report["prs"] = rival_prs(task_id, repo_root=repo_root) if with_prs else None
    report["blocking"] = bool(report["landed"]) and current_mode == "enforce"
    return report


def format_warning(report: dict) -> str:
    """A single human-readable line-block for a finding, or "" when clean.

    Rendered into the runner log, the dispatch prompt and the PR body — one
    wording, so the session that would re-implement merged work reads the same
    evidence a human reviewing the PR does.
    """
    if not report:
        return ""
    lines: List[str] = []
    tid = report.get("task_id", "?")
    if report.get("landed"):
        lines.append(
            f"ALREADY ON {report.get('ref', 'main')}: task {tid} appears in "
            f"{len(report.get('commits') or [])} commit(s) on the default branch "
            f"(evidence: {report.get('confidence')})."
        )
        for c in (report.get("commits") or [])[:5]:
            lines.append(f"  - {c['sha']} {c['subject']}  [{c['evidence']}]")
        lines.append(
            "  The board tracks task -> PR, not task -> main. If that work is "
            "this task's, do NOT re-apply it: a diff against a branch that has "
            "moved on deletes lines main currently has. Verify first."
        )
    elif report.get("referenced"):
        lines.append(
            f"REFERENCED on {report.get('ref', 'main')}: task {tid} is mentioned in "
            f"a commit BODY on the default branch — usually a citation rather than "
            f"a landing, but worth one look before re-implementing."
        )
        for c in (report.get("commits") or [])[:3]:
            lines.append(f"  - {c['sha']} {c['subject']}  [{c['evidence']}]")

    prs = report.get("prs") or {}
    if prs.get("settles") is False:
        rivals = ", ".join(
            f"#{p['number']} ({p['branch']})" for p in prs.get("rivals", [])[:5])
        lines.append(
            f"RIVAL PRs: task {tid} has {len(prs.get('rivals', []))} open PR(s) — "
            f"{rivals} — and NONE is on kanban/{tid}. Only the canonical branch "
            f"settles the card, so merging a rival leaves this task stranded."
        )
    elif len(prs.get("rivals") or []) > 0:
        rivals = ", ".join(
            f"#{p['number']} ({p['branch']})" for p in prs.get("rivals", [])[:5])
        lines.append(
            f"RIVAL PRs: task {tid} has a canonical PR AND {len(prs['rivals'])} "
            f"other open PR(s) — {rivals}. Close or supersede them."
        )
    return "\n".join(lines)


def _safe_print(text: str, stream=None) -> None:
    """Print text that came out of a commit message without dying on cp1252.

    Commit subjects in this repo routinely contain arrows and em-dashes, and the
    default Windows console codepage cannot encode them: printing one raises
    UnicodeEncodeError and takes the whole sweep down at the LAST step, after
    every git call has already been paid for. Measured while surveying 3,176
    tasks — the survey completed and then crashed on output.
    """
    out = stream or sys.stdout
    try:
        print(text, file=out)
    except UnicodeEncodeError:
        encoding = getattr(out, "encoding", None) or "ascii"
        print(text.encode(encoding, "replace").decode(encoding, "replace"), file=out)


def _board_task_ids(statuses: Optional[Sequence[str]] = None) -> List[str]:
    """Non-terminal task ids from the board. [] when the DB is unreachable."""
    wanted = tuple(statuses or ("backlog", "scheduled", "in_progress", "pr_opened"))
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            # The f-string interpolates only "%s" placeholders — one per status —
            # and every status value is bound as a parameter below. Nothing from
            # the caller reaches the SQL text.
            placeholders = ", ".join(["%s"] * len(wanted))
            rows = conn.execute(
                f"SELECT id FROM kanban_tasks WHERE status IN ({placeholders})",  # nosec B608
                wanted,
            ).fetchall()
        return [str(r[0]) for r in rows if r and r[0]]
    except Exception as exc:  # noqa: BLE001
        logger.warning("landed_check: board read failed (%s)", exc)
        return []


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Check whether a kanban task id is ALREADY on the default branch.")
    ap.add_argument("--task", action="append", default=[],
                    help="task id to check (repeatable)")
    ap.add_argument("--all", action="store_true",
                    help="check every non-terminal task on the board")
    ap.add_argument("--status", action="append", default=[],
                    help="with --all, restrict to these statuses")
    ap.add_argument("--branch", help="default branch name (default: origin/HEAD)")
    ap.add_argument("--no-prs", action="store_true",
                    help="skip the rival-PR half (no gh calls)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--gate", action="store_true",
                    help="exit 1 when any task is already on the default branch")
    args = ap.parse_args(argv)

    task_ids = list(args.task)
    if args.all:
        task_ids.extend(_board_task_ids(args.status or None))
    task_ids = sorted(set(task_ids))
    if not task_ids:
        print("landed_check: no task ids — pass --task <id> or --all", file=sys.stderr)
        return 2

    reports = check_landed_bulk(task_ids, branch=args.branch)
    if not args.no_prs:
        # Only for ids with a finding: the PR half costs a gh round-trip per
        # branch, and a board-wide sweep must not pay it 500 times to learn
        # nothing. A task that is not on main has no rival-PR question worth
        # the network cost at sweep time.
        for tid, rep in reports.items():
            if rep.get("landed") or rep.get("referenced"):
                rep["prs"] = rival_prs(tid)

    landed = [r for r in reports.values() if r.get("landed")]
    referenced = [r for r in reports.values()
                  if r.get("referenced") and not r.get("landed")]
    unchecked = [r for r in reports.values() if not r.get("checked")]
    payload = {
        "checked": len(task_ids) - len(unchecked),
        "unchecked": [{"task_id": r["task_id"], "reason": r["reason"]} for r in unchecked],
        "landed": landed,
        "referenced": referenced,
        "mode": mode(),
    }
    if args.json:
        _safe_print(json.dumps(payload, indent=2))
    else:
        _safe_print(f"landed_check: {payload['checked']}/{len(task_ids)} task(s) "
                    f"checked (mode={payload['mode']})")
        for rep in landed + referenced:
            _safe_print(format_warning(rep))
        if unchecked:
            _safe_print(f"  NOT CHECKED ({len(unchecked)}): "
                        + ", ".join(f"{r['task_id']} ({r['reason']})" for r in unchecked[:10]))
        if not landed and not referenced:
            _safe_print("  no task id found on the default branch")
    if args.gate and landed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
