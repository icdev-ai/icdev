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
  * ``pr_watcher.poll_once`` (the TASK-LINKED merge path) consumes
    ``hold_labels`` / ``held_label_reason`` — kpr-watch-04. It runs a longer
    ladder of its own (gates, dependencies, resumes, rebases) so it cannot call
    the whole table, but the ONE rung a human can reach from outside must not
    mean two different things depending on which door the work came through.
    It had meant nothing at all there: the list was referenced at exactly one
    site, and ``_GH_JSON_FIELDS`` never even requested ``labels``.

PURITY. ``classify_merge_readiness`` does no I/O: no subprocess, no database, no
network, no clock, no LLM. It takes the parsed ``gh pr list --json`` dict plus
the facts the ladder needed from outside (the default branch, the set of PR urls
a kanban task already points at, and — kpr-stale-02 — how many commits behind
its base the branch is) and returns ``(state, reason)``. The one measurement
that cannot come from the PR json lives in ``measure_behind_by``, deliberately
OUTSIDE the table, so the table stays a pure function of its inputs.

STALENESS (kpr-stale-02) is the rung that was missing. Every other rung reads a
field the forge hands over for free, and none of them asked how far behind the
branch was: ``mergeable`` is MERGEABLE for a branch arbitrarily far behind main
so long as nothing collides TEXTUALLY. The CONFLICTING interlock therefore only
ever caught the colliding subset; the non-conflicting subset merged cleanly and
re-applied its diff over a tree that had moved on. Both consumers refuse it now,
and they differ ONLY in the repair: the watcher rebases a ``kanban/*`` branch
it owns, while the unlinked sweep reports and leaves the branch alone (it never
pushes, by design).

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
BEHIND_MAIN = "behind_main"
LINKED = "linked"
PROTECTED_PATH = "protected_path"
UNKNOWN = "unknown"

#: Every state this table can return, in ladder order. ``unknown`` is last
#: because it is the degenerate case (a PR record with no url), not a rung.
MERGE_STATES: tuple = (
    MERGED, PROTECTED_PATH, LINKED, DRAFT, HELD_LABEL, WRONG_BASE, CONFLICTING,
    NO_CHECKS, CI_FAILED, AWAITING_CI, CHANGES_REQUESTED, BEHIND_MAIN,
    READY, UNKNOWN,
)

#: States in which the pipeline is waiting on something OTHER than a human
#: decision about this PR's content. Used by the report's summary line.
#: ``behind_main`` is here because the repair is a rebase, which the watcher
#: performs itself for a ``kanban/*`` branch.
BLOCKED_ON_AUTOMATION = frozenset(
    {AWAITING_CI, CONFLICTING, CI_FAILED, NO_CHECKS, BEHIND_MAIN})

#: How many commits a branch may sit behind the default branch and still be
#: merged. kpr-stale-02: `mergeable` is MERGEABLE for a branch arbitrarily far
#: behind main so long as nothing collides TEXTUALLY, so the CONFLICTING
#: interlock only ever caught the colliding subset. The default is overridden
#: from ``max_behind_commits`` in args/pr_watcher_config.yaml, where the
#: measured distribution that chose it is recorded.
DEFAULT_MAX_BEHIND_COMMITS = 10

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


def protected_hits(
    changed_files: Optional[Iterable[str]], protected_paths: Iterable[str]
) -> Optional[List[str]]:
    """Which protected paths this PR touches, or None when nothing is protected.

    An entry matches a path EXACTLY or as a directory prefix: ``e`` matches ``p``
    when ``p == e`` or ``p.startswith(e + "/")``. Both halves are load-bearing.
    A bare prefix test would make the entry ``tools/ci/pr_watcher.py`` also catch
    ``tools/ci/pr_watcher_helpers.py``, and a control that stops work it was
    never meant to stop gets switched off.

    FAIL-CLOSED, and this is the OPPOSITE default from the sibling-conflict map
    a few lines away in the watcher, so the asymmetry needs saying. That map
    answers "might these two PRs collide?" — degrading it to a warning costs a
    retry. This answers "may this PR edit the merger itself?" — degrading it to
    a warning costs the control entirely, because the case where the file list
    is unavailable is not distinguishable from the case where it is unavailable
    BECAUSE the PR is unusual. So a ``changed_files`` of None, with any path
    protected, is treated as a hit. A merge gate that opens when it cannot see
    is not a gate.

    Returns ``None`` (not an empty list) when no path is configured, so a caller
    can tell "protection is off" from "protection is on and this PR is clean".
    """
    entries = [str(e).strip().replace("\\", "/").strip("/")
               for e in (protected_paths or ()) if str(e or "").strip()]
    if not entries:
        return None
    if changed_files is None:
        return sorted(entries)          # fail closed — see above
    paths = [str(f).strip().replace("\\", "/").lstrip("/")
             for f in changed_files if str(f or "").strip()]
    hits = {e for e in entries
            for f in paths if f == e or f.startswith(e + "/")}
    return sorted(hits)


def hold_labels(pr: Dict[str, Any]) -> List[str]:
    """The hold labels this PR carries, lower-cased and sorted. [] when clean.

    ONE EXTRACTION, TWO MERGE PATHS (kpr-watch-04). The list itself already
    lived here, and it was still possible for a label to mean two different
    things: ``classify_merge_readiness`` read it for the UNLINKED sweep, and the
    task-linked auto-merge path in ``pr_watcher.poll_once`` never looked at
    labels at all — ``_GH_JSON_FIELDS`` did not even request them. So a human
    labelling a ``kanban/<task-id>`` PR ``do-not-merge`` got no warning and no
    effect, and the PR merged itself. A shared LIST is not a shared CHECK: the
    linked path needs to ask this question outside the ladder (it runs a
    different, longer ladder of its own), so the question is a function both
    sides call rather than a second transcription of ``.get("labels")``.

    Accepts the ``gh pr view --json labels`` shape — a list of
    ``{"name": ...}`` dicts. A bare string is accepted too, because
    ``gh`` is not the only thing that ever builds one of these records and
    silently ignoring a label is the failure mode this whole card is about.
    """
    names = set()
    for lbl in (pr.get("labels") or []):
        name = lbl.get("name") if isinstance(lbl, dict) else lbl
        name = (str(name or "")).strip().lower()
        if name:
            names.add(name)
    return sorted(names & NO_AUTOMERGE_LABELS)


def held_label_reason(held: Iterable[str]) -> str:
    """The one sentence both merge paths report for a hold label."""
    return "carries hold label(s): " + ", ".join(held)


def classify_merge_readiness(
    pr: Dict[str, Any],
    *,
    default_branch: str,
    linked_urls: Iterable[str] = (),
    behind_by: Optional[int] = None,
    max_behind_commits: int = DEFAULT_MAX_BEHIND_COMMITS,
    changed_files: Optional[Iterable[str]] = None,
    protected_paths: Iterable[str] = (),
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
    * ``behind_main`` (the branch is stale but merges cleanly) is not
      ``conflicting`` (it is stale AND collides). Same cause, opposite
      visibility: the forge refuses the second and cheerfully merges the first.

    ``behind_by`` is the number of commits on ``default_branch`` that this
    branch does not have — ``measure_behind_by`` below computes it, or pass
    ``None`` when it could not be measured. ``None`` is FAIL-OPEN and never
    silent: it is not a finding, and the caller is told the count is absent
    rather than being handed a reassuring zero. That is the posture
    ``landed_check`` already takes for the same question one layer up.
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

    # ── PROTECTED PATHS (kpr-watch-05) ──────────────────────────────────────
    # AHEAD OF `linked`, which is the whole point. Every other rung answers "is
    # this PR finished?"; this one answers "may an automation merge this at
    # all?", and the answer must not depend on which door the work came through.
    # The watcher auto-merges any CI-green kanban/* branch INCLUDING one that
    # edits the watcher, so a defect in the ladder below could merge itself into
    # main and every later cycle would run on the new, wrong rule with no human
    # in the path. A self-protection rung placed AFTER the logic it protects can
    # be bypassed by a defect in that logic, so it goes first.
    hits = protected_hits(changed_files, protected_paths)
    if hits:
        return MergeReadiness(
            PROTECTED_PATH,
            "touches protected path(s) %s -- a human must merge this"
            % ", ".join(hits)
            if changed_files is not None else
            "changed files could not be determined and %d path(s) are protected "
            "-- refusing rather than guessing" % len(hits))

    if url in linked:
        return MergeReadiness(
            LINKED, "a kanban task points at this PR -- the task path owns it")

    if pr.get("isDraft"):
        # GitHub refuses `gh pr merge` on a draft outright. An unlinked PR is
        # never un-drafted: for a human the draft IS the "not ready" signal.
        return MergeReadiness(DRAFT, "draft -- mark it ready for review to land it")

    held = hold_labels(pr)
    if held:
        return MergeReadiness(HELD_LABEL, held_label_reason(held))

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

    # ── STALE (kpr-stale-02) ────────────────────────────────────────────────
    # THE SAFETY HOLE. Every rung above reads a field GitHub hands over for
    # free, and none of them asks the one question that matters here: how far
    # behind the default branch is this? `mergeable` answers "does it collide
    # TEXTUALLY", which is MERGEABLE for a branch arbitrarily far behind main.
    # So a green, mergeable, months-old branch merged cleanly and re-applied
    # its whole diff over a tree that had moved on — a revert wearing a
    # feature's clothes. #1651 was -38/+26 on rest_v1.py and 36 commits behind
    # main when a human closed it by hand; nothing in this ladder saw it.
    # `auto_rebase_on_conflict` repairs a branch that has ALREADY gone DIRTY,
    # which is exactly the subset that never had this problem.
    #
    # LAST RUNG ON PURPOSE, and it is the one placement decision here that is
    # not free. Every rung above reads the `gh pr list --json` record the
    # caller already has; this one needs a count nobody gets for free (see
    # `measure_behind_by`). Putting it last means the merger only pays for it
    # once every cheaper refusal has passed — and the module docstring's rule
    # is that the report describes the merger's OWN order, so the rung goes
    # where the merger actually asks. A red-CI branch that is also stale is
    # reported `ci_failed`, which is true and is what the merger saw first.
    #
    # TWO SIGNALS, and the forge's own is not the load-bearing one.
    # `mergeStateStatus == BEHIND` is authoritative when it appears — GitHub
    # will refuse the merge outright — but it appears ONLY when the base
    # branch has "require branches to be up to date" (`required_status_checks.
    # strict`) turned on. Measured on this repo 2026-08-18: strict is FALSE, so
    # mergeStateStatus is CLEAN for a branch 200 commits behind and keying the
    # check on it alone would build a gate that can never fire. The measured
    # count is therefore the real check, and the forge verdict is the belt.
    merge_state = (pr.get("mergeStateStatus") or "").strip().upper()
    if merge_state == "BEHIND":
        return MergeReadiness(
            BEHIND_MAIN,
            "the forge reports mergeStateStatus=BEHIND -- %s requires branches "
            "to be up to date, so this merge is refused until it is rebased"
            % default_branch)
    if behind_by is not None and behind_by > max_behind_commits:
        return MergeReadiness(
            BEHIND_MAIN,
            "%d commits behind %s (limit %d) -- it merges CLEANLY and would "
            "re-apply its diff over a tree that has moved on; rebase it"
            % (behind_by, default_branch, max_behind_commits))

    if behind_by is None:
        # Not a refusal — but the reason must not claim a freshness nobody
        # measured. A silent fail-open is how the hole stayed open.
        return MergeReadiness(
            READY, "green, mergeable and unblocked (staleness UNMEASURED)")
    return MergeReadiness(
        READY, "green, mergeable and %d commit(s) behind %s -- within the "
        "limit of %d" % (behind_by, default_branch, max_behind_commits))


# ────────────────────────────────────────────────────────────────────────────
# Measuring staleness — the ONE impure part, kept out of the table
# ────────────────────────────────────────────────────────────────────────────


def measure_behind_by(
    base: str,
    head: str,
    *,
    runner=None,
    gh_bin: str = "gh",
    repo: Optional[str] = None,
    timeout: int = 30,
) -> Optional[int]:
    """How many commits on ``base`` is ``head`` missing? ``None`` if unmeasured.

    ``head`` should be a SHA (``headRefOid``) rather than a branch name: a sha
    survives a branch rename, a delete-on-merge and a re-push, and it is what
    the forge's own verdict was computed against.

    WHY THE FORGE AND NOT LOCAL GIT. A local ``git rev-list`` needs both objects
    present and an ``origin/<base>`` that is actually current, and this runs
    from worktrees, from CI checkouts and from a daemon that may not have
    fetched in hours. A stale local ``origin/main`` UNDERSTATES how far behind a
    branch is, which is the one direction that fails silently — it would report
    a stale branch as fresh. ``/compare`` is computed by the forge against the
    real tip.

    NEVER RAISES, and ``None`` is not zero. A missing gh, a fork PR whose head
    sha the base repo cannot resolve, a rate limit — all report "unmeasured", and
    every caller is required to keep that distinct from "measured as fresh".
    """
    base = (base or "").strip()
    head = (head or "").strip()
    if not base or not head:
        return None
    if runner is None:
        runner = subprocess.run
    slug = repo or "{owner}/{repo}"   # gh substitutes from the current remote
    # per_page=1 because the compare payload embeds up to 250 commits and every
    # changed file, and the only field wanted is a single integer.
    path = "repos/%s/compare/%s...%s?per_page=1" % (slug, base, head)
    try:
        proc = runner(
            [gh_bin, "api", path, "--jq", ".behind_by"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except Exception:  # noqa: BLE001 — unmeasured, never a crash in the poll
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    try:
        return int((getattr(proc, "stdout", "") or "").strip())
    except (TypeError, ValueError):
        return None


def measure_behind_map(
    prs: Iterable[Dict[str, Any]],
    *,
    default_branch: str,
    runner=None,
    gh_bin: str = "gh",
    repo: Optional[str] = None,
) -> Dict[str, Optional[int]]:
    """``url -> behind_by`` for each PR, measured against its OWN base.

    Measured against ``baseRefName``, falling back to ``default_branch``: a PR
    onto a release branch is stale relative to THAT branch, and comparing it to
    main would invent a number describing a merge nobody proposed. (The
    ``wrong_base`` rung refuses it long before this anyway.)

    Deduplicated by ``(base, head sha)`` so two PRs on one sha cost one call.
    """
    out: Dict[str, Optional[int]] = {}
    cache: Dict[tuple, Optional[int]] = {}
    for pr in prs:
        url = (pr.get("url") or "").strip()
        if not url:
            continue
        base = (pr.get("baseRefName") or "").strip() or default_branch
        head = (pr.get("headRefOid") or "").strip()
        key = (base, head)
        if key not in cache:
            cache[key] = measure_behind_by(
                base, head, runner=runner, gh_bin=gh_bin, repo=repo)
        out[url] = cache[key]
    return out


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

#: `mergeStateStatus` and `headRefOid` are kpr-stale-02: the first is the
#: forge's own staleness verdict (free, but only ever BEHIND on a base branch
#: with `strict` protection), the second is what `measure_behind_by` compares.
_GH_FIELDS = ("number,url,title,headRefName,headRefOid,baseRefName,isDraft,"
              "mergeable,mergeStateStatus,labels,statusCheckRollup,reviews,"
              "state,updatedAt,files")


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
    behind_by_url: Optional[Dict[str, Optional[int]]] = None,
    max_behind_commits: int = DEFAULT_MAX_BEHIND_COMMITS,
) -> Dict[str, Any]:
    """Classify every PR. Pure — takes data, returns data.

    ``behind_by_url`` maps PR url -> commits behind base, as
    ``measure_behind_map`` returns it. A url absent from the mapping, or
    mapped to ``None``, is UNMEASURED — reported as such and never as fresh.
    """
    linked = frozenset((u or "").strip() for u in linked_urls)
    behind_map = dict(behind_by_url or {})
    rows: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for pr in prs:
        url = (pr.get("url") or "").strip()
        behind = behind_map.get(url)
        verdict = classify_merge_readiness(
            pr, default_branch=default_branch, linked_urls=linked,
            behind_by=behind, max_behind_commits=max_behind_commits)
        counts[verdict.state] = counts.get(verdict.state, 0) + 1
        rows.append({
            "number": pr.get("number"),
            "url": url,
            "title": pr.get("title") or "",
            "head": pr.get("headRefName") or "",
            "base": pr.get("baseRefName") or "",
            "state": verdict.state,
            "reason": verdict.reason,
            "ready": verdict.ready,
            "mergeable": (pr.get("mergeable") or "").upper(),
            "merge_state_status": (pr.get("mergeStateStatus") or "").upper(),
            # None, never 0 — an unmeasured branch and a branch measured level
            # with main are different facts and only one of them is evidence.
            "behind_by": behind,
            "behind_measured": behind is not None,
            "is_draft": bool(pr.get("isDraft")),
            "labels": sorted((lbl.get("name") or "").strip()
                             for lbl in (pr.get("labels") or [])),
            "linked": url in linked,
        })
    rows.sort(key=lambda r: (r["state"] != READY, r["number"] or 0))
    return {
        "default_branch": default_branch,
        # Never silent: without the board we cannot tell linked from unlinked,
        # so every PR is classified on its own merits and the caller is told.
        "linked_lookup_ok": bool(linked_lookup_ok),
        "linked_count": len(linked),
        "max_behind_commits": int(max_behind_commits),
        "behind_measured_count": sum(1 for r in rows if r["behind_measured"]),
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
    lines.append("%-7s %-14s %-7s %-32s %s"
                 % ("PR", "STATE", "BEHIND", "BRANCH", "REASON"))
    lines.append("%-7s %-14s %-7s %-32s %s"
                 % ("-" * 7, "-" * 14, "-" * 7, "-" * 32, "-" * 40))
    for row in report["prs"]:
        # "?" not "0" -- an unmeasured branch must not read as an up-to-date one.
        behind = "?" if row.get("behind_by") is None else str(row["behind_by"])
        lines.append("%-7s %-14s %-7s %-32s %s" % (
            "#%s" % row["number"], row["state"], behind,
            (row["head"] or "?")[:32], row["reason"]))
    lines.append("")
    lines.append("by state: " + ", ".join(
        "%s=%d" % (k, v) for k, v in report["counts"].items()) or "by state: -")
    lines.append(
        "staleness: refused above %d commit(s) behind %s; measured for %d of %d PR(s)"
        % (report.get("max_behind_commits", DEFAULT_MAX_BEHIND_COMMITS),
           report["default_branch"], report.get("behind_measured_count", 0),
           report["total"]))
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
    parser.add_argument("--max-behind", type=int, default=None, metavar="N",
                        help="commits behind the base branch above which a PR is "
                             "refused as behind_main (default: max_behind_commits "
                             "in args/pr_watcher_config.yaml)")
    parser.add_argument("--no-measure-behind", action="store_true",
                        help="skip the /compare call; every PR then reports its "
                             "staleness as UNMEASURED rather than as fresh")
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

    max_behind = args.max_behind
    if max_behind is None:
        max_behind = _configured_max_behind()

    # TWO PHASES, because the staleness rung is the only one that costs a forge
    # round-trip. Classify with what `gh pr list` already gave us, then measure
    # ONLY the PRs that came back `ready` -- those are the ones whose verdict a
    # behind-count can still change, and the ones the merger would act on. The
    # rest keep the refusal the merger reached first.
    report = build_report(prs, default_branch=default_branch,
                          linked_urls=linked, linked_lookup_ok=linked_lookup_ok,
                          max_behind_commits=max_behind)
    if not args.no_measure_behind:
        ready_urls = {r["url"] for r in report["prs"] if r["state"] == READY}
        if ready_urls:
            behind = measure_behind_map(
                [pr for pr in prs if (pr.get("url") or "").strip() in ready_urls],
                default_branch=default_branch)
            report = build_report(
                prs, default_branch=default_branch, linked_urls=linked,
                linked_lookup_ok=linked_lookup_ok, behind_by_url=behind,
                max_behind_commits=max_behind)
    if args.state:
        wanted = set(args.state)
        report["prs"] = [r for r in report["prs"] if r["state"] in wanted]
        report["filtered_to"] = sorted(wanted)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_table(report))
    return 0


def _configured_max_behind() -> int:
    """``max_behind_commits`` from the watcher config, so the report and the
    merger cannot hold two different thresholds. Falls back to the module
    default when the file is unreadable — never to "no limit"."""
    try:
        import yaml  # noqa: PLC0415 — optional at import time

        raw = yaml.safe_load(
            (REPO_ROOT / "args" / "pr_watcher_config.yaml").read_text(
                encoding="utf-8")) or {}
        return int(raw.get("max_behind_commits", DEFAULT_MAX_BEHIND_COMMITS))
    except Exception:  # noqa: BLE001
        return DEFAULT_MAX_BEHIND_COMMITS


def _fail(as_json: bool, message: str) -> int:
    if as_json:
        print(json.dumps({"ok": False, "error": message}, indent=2))
    else:
        print("ERROR: %s" % message, file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover — exercised via main(argv)
    sys.exit(main())
