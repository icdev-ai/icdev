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
import datetime as _dt
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

#: The order a HUMAN wants these groups in, which is NOT the ladder order.
#: The ladder is ordered by what the merger asks FIRST (cheapest refusal wins);
#: a reader wants what is CLOSEST TO LANDING first. ``ready`` heads the list --
#: it is literally "awaiting merge" -- and ``behind_main`` follows because it is
#: the rung that merges cleanly and should not (kpr-stale-02). ``draft``,
#: ``held_label`` and ``merged`` sink to the bottom: each is somebody having
#: SAID "not yet", which is an answer and not a question.
#:
#: This is presentation only. It never reorders the ladder in
#: ``classify_merge_readiness`` -- doing that would change which refusal a PR
#: reports, and the module docstring's rule is that the report describes the
#: merger's own reasoning.
ATTENTION_ORDER: tuple = (
    READY, BEHIND_MAIN, CONFLICTING, CI_FAILED, AWAITING_CI, NO_CHECKS,
    CHANGES_REQUESTED, PROTECTED_PATH, WRONG_BASE, UNKNOWN,
    DRAFT, HELD_LABEL, LINKED, MERGED,
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
    required_checks: Optional[Iterable[str]] = None,
) -> MergeReadiness:
    """Why is this PR not merging? Pure — no I/O, no clock, no LLM.

    ``required_checks`` is the branch-protection required set, resolved by the
    caller through ``fetch_required_checks`` (task-det-295a9bb95e). With it,
    ONLY those checks decide the CI rungs and a failing check outside the set
    is NAMED on the ``ready`` reason rather than read as ``ci_failed`` — the
    forge merges on the required set, and a ladder that refused on an advisory
    red was the defect (PR #1859: five resumes and an escalation over a green
    required set). Unresolved (``None``/empty) reads every check, as before.

    ``pr`` is one entry of ``gh pr list --json url,isDraft,baseRefName,
    mergeable,labels,statusCheckRollup,reviews,state``. Anything missing is
    treated as unknown rather than as permission.

    Returns ``ready`` EXACTLY when ``_sweep_unlinked_prs`` would have merged:
    unlinked, not a draft, no hold label, based on ``default_branch``, reported
    MERGEABLE, every DECIDING check green (the required set when resolved,
    every check otherwise), and no reviewer asking for changes. That
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

    required = frozenset(
        str(n).strip() for n in (required_checks or ()) if str(n).strip()) or None
    if not ec.is_passing(pr, required=required):
        rollup = list(pr.get("statusCheckRollup") or [])
        if not rollup:
            return MergeReadiness(
                NO_CHECKS, "no check has reported yet -- empty status rollup")
        if ec.is_ci_failed(pr, required=required):
            deciding = ([c for c in rollup if _check_name(c) in required]
                        if required else rollup)
            return MergeReadiness(
                CI_FAILED, ("failing required checks: " if required else
                            "failing checks: ") + _failing_names(deciding))
        # Still running -- or, with a required set, a required check that has
        # not reported at all yet. Name the required ones first.
        pending = _pending_names(rollup)
        if required:
            seen = {_check_name(c) for c in rollup}
            missing = sorted(required - seen)
            if missing:
                pending = "required check(s) not yet reported: " + ", ".join(missing)
        return MergeReadiness(AWAITING_CI, "checks still running: " + pending)
    ignored = ec.ignored_failures(pr, required=required)
    ignored_note = (
        " -- ignored non-required failing check(s): " + ", ".join(ignored)
        if ignored else "")

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
            READY, "green, mergeable and unblocked (staleness UNMEASURED)"
            + ignored_note)
    return MergeReadiness(
        READY, "green, mergeable and %d commit(s) behind %s -- within the "
        "limit of %d" % (behind_by, default_branch, max_behind_commits)
        + ignored_note)


def staleness(
    behind_by: Optional[int],
    *,
    default_branch: str,
    max_behind_commits: int = DEFAULT_MAX_BEHIND_COMMITS,
    merge_state_status: str = "",
) -> tuple:
    """``(stale, reason)`` — is this branch too far behind? A THIRD AXIS.

    rem-hyg-12. ``classify_merge_readiness`` answers "why is this PR not
    merging", and its answer is the merger's FIRST refusal — deliberately, so
    the report describes the merger's own reasoning. That is exactly why a
    stale branch can go unreported: ``draft``, ``linked``, ``ci_failed`` and
    every other rung short-circuits BEFORE the staleness rung, so a PR whose
    state can never be ``behind_main`` renders identically whether it is level
    with main or 200 commits behind it.

    MEASURED 2026-08-20: #1850 was a ``draft``, ``MERGEABLE``,
    ``mergeStateStatus=CLEAN``, and 13 commits behind main, with a diff against
    main of +97/-1691 — one un-draft away from deleting ``posture.py``,
    ``cortex/metrics.py`` and ``kanban_project_sync.py``. #1845 was ``linked``
    and 16 behind, which is why its red-first proof compared against an ancient
    merge base. Neither was ever measured.

    So staleness is reported BESIDE the verdict rather than folded into it. The
    ladder is untouched and is NOT reordered: a stale draft still reports
    ``draft``, because that is what the merger saw first and it is true. What
    changes is that the row also says it is 13 commits behind.

    ``None`` — NEVER ``False`` — when the count could not be measured. The same
    posture ``behind_by`` already takes, and for the same reason: "measured and
    level with main" and "nobody looked" justify opposite decisions, and only
    the first is evidence. The reason is then empty, because a sentence about a
    branch nobody compared is a sentence about nothing.

    TWO SIGNALS, in the same order the ladder uses them. The forge's own
    ``mergeStateStatus == BEHIND`` is authoritative when it appears, but it
    appears only where the base branch has ``required_status_checks.strict``
    (FALSE on this repo — measured 2026-08-18), so the measured count is the
    real check and the forge verdict is the belt.
    """
    if (merge_state_status or "").strip().upper() == "BEHIND":
        return True, ("the forge reports mergeStateStatus=BEHIND -- %s requires "
                      "branches to be up to date" % default_branch)
    if behind_by is None:
        return None, ""
    if behind_by > max_behind_commits:
        return True, ("%d commit(s) behind %s (limit %d) -- it would re-apply "
                      "its diff over a tree that has moved on; rebase it"
                      % (behind_by, default_branch, max_behind_commits))
    return False, ("%d commit(s) behind %s -- within the limit of %d"
                   % (behind_by, default_branch, max_behind_commits))


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


#: The runner `fetch_required_checks` uses when none is injected. A test
#: session points this at a stub that answers "not protected" (tests/conftest.py),
#: so a watcher a unit test builds without injecting a runner never reaches the
#: live forge -- with local gh auth it would resolve the REAL required set and
#: classify every fixture PR (whose checks are named `ci`, `build`...) as
#: awaiting checks that will never report, while the same test passes on a CI
#: runner that has no gh auth. None here means subprocess.run.
_DEFAULT_GH_RUNNER = None


def _required_checks_only() -> bool:
    """`required_checks_only` from args/pr_watcher_config.yaml; default on."""
    try:
        from tools.ci.pr_watcher import load_config  # local: import cycle
        return bool(load_config().get("required_checks_only", True))
    except Exception:  # noqa: BLE001 -- an unreadable config keeps the old read
        return False


def fetch_required_checks(
    default_branch: str,
    *,
    runner=None,
    gh_bin: str = "gh",
    repo: Optional[str] = None,
    timeout: int = 30,
) -> Optional[FrozenSet[str]]:
    """The check names branch protection REQUIRES on ``default_branch``.

    ``None`` when it cannot be resolved -- never an empty set (task-det-295a9bb95e).

    THE ONE PLACE THE SET IS DECLARED. Lint / Test / Security Scan / Helm Lint
    are written in at least three other places in this tree (task_pipeline.js,
    seed_ahx_arr_clx.py, icdev-ci.yml's own comment) and every one of them
    drifts the day a check is promoted; the CLAUDE.md note on promoting
    `E2E (Playwright)` says exactly that. Reading the protection rule means the
    watcher's "CI is green" is the forge's "CI is green", by construction.

    NEVER RAISES. A branch with no protection (404 "Branch not protected"), a
    token that may not read it (403 -- GITHUB_TOKEN in a workflow lacks the
    administration scope this endpoint wants), a missing gh, a rate limit, a
    rule with NO required checks -- every one returns ``None``, and every
    caller reads ``None`` as "count every check", the pre-existing behaviour.
    An EMPTY required set is deliberately ``None`` too: the forge would accept
    any merge on such a branch, but a sweep that merged a PR with every check
    red because nothing was required is the same move as the `awk '{print $2}'`
    that could not see the failures, and it must stay a human decision.
    """
    branch = (default_branch or "").strip()
    if not branch:
        return None
    if runner is None:
        runner = _DEFAULT_GH_RUNNER or subprocess.run
    slug = repo or "{owner}/{repo}"   # gh substitutes from the current remote
    path = "repos/%s/branches/%s/protection" % (slug, branch)
    try:
        proc = runner(
            [gh_bin, "api", path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except Exception:  # noqa: BLE001 -- unresolved, never a crash in the poll
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    try:
        payload = json.loads(getattr(proc, "stdout", "") or "")
    except (TypeError, ValueError):
        return None
    rule = (payload or {}).get("required_status_checks") if isinstance(payload, dict) else None
    if not isinstance(rule, dict):
        return None
    names = set()
    for ctx in rule.get("contexts") or []:
        if isinstance(ctx, str) and ctx.strip():
            names.add(ctx.strip())
    for chk in rule.get("checks") or []:
        ctx = (chk or {}).get("context") if isinstance(chk, dict) else None
        if isinstance(ctx, str) and ctx.strip():
            names.add(ctx.strip())
    return frozenset(names) or None


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
# How long has it been like this? (kpr-watch-03)
# ────────────────────────────────────────────────────────────────────────────
#
# NOTHING PERSISTS A STATE TRANSITION, so "age in state" cannot be read off a
# ledger — and inventing one would be a table that has to be written before it
# can be trusted. What CAN be measured, from the record the forge already
# hands over, is a LOWER BOUND: take the LATEST timestamp of any observable
# event on the PR, and the state has certainly held since that moment, because
# nothing has changed since. It may well have held longer. The report says
# "at least", and never more than it knows.
#
# THE LATEST OF ALL OF THEM, not `updatedAt` alone, and this is not
# defensive coding. `updatedAt` does NOT bump when a check run completes.
# Measured on this repo 2026-08-19: PR #1817 reported updatedAt=01:10:24Z
# while one of its own checks completed at 01:11:09Z, 45 seconds LATER. Keying
# the age on `updatedAt` alone would have claimed the PR had sat unchanged for
# 45 seconds longer than it had — which turns a lower bound into a guess, in
# the direction that makes a stuck pipeline look MORE stuck than it is.

#: `gh` renders an absent check timestamp as the Go zero value rather than as
#: null. Parsed naively it is a real datetime in year 1, which would win no
#: `max()` — but it WOULD be reported as a basis, and an age of "2025 years"
#: is the kind of number a reader stops trusting the whole panel over.
_ZERO_TIME_YEAR = 1


def _parse_iso(value: Any) -> Optional[_dt.datetime]:
    """An ISO-8601 instant as an aware datetime, or None. Never raises.

    None is returned for anything unparseable AND for the Go zero value `gh`
    emits for a check that has not finished — those are "no timestamp", and a
    fabricated year-1 datetime is not a better answer than admitting that.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.year <= _ZERO_TIME_YEAR:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc)


def last_activity(pr: Dict[str, Any]) -> tuple:
    """``(datetime | None, basis)`` — the most recent observable event on a PR.

    Pure: no clock, no I/O. ``basis`` names WHICH field won, so a reader can
    tell "the branch was pushed 3h ago" from "a check finished 3h ago", and
    is ``"unmeasured"`` when nothing parseable was present.
    """
    best: Optional[_dt.datetime] = None
    basis = "unmeasured"
    for field, label in (("updatedAt", "pr_updated"), ("createdAt", "pr_created")):
        stamp = _parse_iso(pr.get(field))
        if stamp is not None and (best is None or stamp > best):
            best, basis = stamp, label
    for check in (pr.get("statusCheckRollup") or []):
        if not isinstance(check, dict):
            continue
        for field, label in (("completedAt", "check_completed"),
                             ("startedAt", "check_started")):
            stamp = _parse_iso(check.get(field))
            if stamp is not None and (best is None or stamp > best):
                best, basis = stamp, label
    return best, basis


def state_age_seconds(pr: Dict[str, Any],
                      *, now: Optional[_dt.datetime] = None) -> Optional[int]:
    """A LOWER BOUND on how long this PR has been in its current state.

    ``None`` when no timestamp could be measured — never 0, which would read
    as "it just changed" and is the one thing an unmeasured PR is not.
    """
    stamp, _basis = last_activity(pr)
    if stamp is None:
        return None
    reference = now or _dt.datetime.now(_dt.timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=_dt.timezone.utc)
    # A forge clock marginally ahead of ours must not print a negative age.
    return max(0, int((reference - stamp).total_seconds()))


def format_age(seconds: Optional[int]) -> str:
    """``"3h12m"`` / ``"6d"`` / ``"?"``. One formatter, shared by every surface,
    so the CLI table and the dashboard panel cannot disagree about a duration."""
    if seconds is None:
        return "?"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "%ds" % seconds
    minutes = seconds // 60
    if minutes < 60:
        return "%dm" % minutes
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return "%dh%02dm" % (hours, minutes)
    days, hours = divmod(hours, 24)
    return "%dd%02dh" % (days, hours)


def group_by_state(report: Dict[str, Any],
                   *, order: Iterable[str] = ATTENTION_ORDER) -> List[Dict[str, Any]]:
    """The report's PRs bucketed by ``pipeline_state``, in attention order.

    Grouped on ``pipeline_state`` and NOT on ``state``: ``state`` short-circuits
    at the ``linked`` rung for every kanban PR, which is exactly the population
    a reader of this panel cares about, so grouping on it would collapse the
    whole board into one bucket labelled "a task owns it". ``linked`` stays a
    COLUMN — the ownership fact — and the group says why the PR is not landing.

    A state with no PRs yields no group. Any state not named in ``order``
    (a vocabulary that grew without this list) is appended rather than dropped.
    """
    wanted = list(order)
    rows = list(report.get("prs") or [])
    seen = [r.get("pipeline_state") or r.get("state") for r in rows]
    for state in seen:
        if state not in wanted:
            wanted.append(state)
    groups: List[Dict[str, Any]] = []
    for state in wanted:
        members = [r for r in rows
                   if (r.get("pipeline_state") or r.get("state")) == state]
        if not members:
            continue
        groups.append({
            "state": state,
            "count": len(members),
            "blocked_on_automation": state in BLOCKED_ON_AUTOMATION,
            "prs": members,
        })
    return groups


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


def linked_pr_tasks(get_connection=None) -> Dict[str, Dict[str, Any]]:
    """PR url -> ``{"task_id", "task_status"}``. Raises if the board is unreadable.

    ``linked_pr_urls`` above answers the ladder's question ("does ANY task point
    here?"); this answers the reader's ("WHICH one?"). Kept as two functions
    rather than one because the ladder must not gain a dependency on a column it
    does not use — and because a caller with no board still needs the urls.
    Last writer wins when two tasks name the same PR; the ladder does not care
    which, and neither does the column.
    """
    from tools.ci.pr_watcher import list_pr_tasks  # local: avoids an import cycle

    if get_connection is None:
        from tools.db.storage import get_connection as get_connection  # type: ignore
    out: Dict[str, Dict[str, Any]] = {}
    for task in list_pr_tasks(get_connection):
        url = (task.get("pr_url") or "").strip()
        if not url:
            continue
        out[url] = {"task_id": task.get("id"),
                    "task_status": task.get("status")}
    return out
def load_protected_paths(config_path=None) -> List[str]:
    """`protected_paths` from args/pr_watcher_config.yaml, or [] if unreadable.

    Read from the WATCHER's config rather than a second list of its own: two
    lists would drift, and a report that names a different set than the merger
    enforces is the defect this function exists to close.
    """
    try:
        import yaml
    except Exception:  # noqa: BLE001 — a report must not require pyyaml
        return []
    path = config_path or (REPO_ROOT / "args" / "pr_watcher_config.yaml")
    try:
        data = yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return []
    return [str(p).strip() for p in (data.get("protected_paths") or [])
            if str(p or "").strip()]


def _changed_files(pr: Dict[str, Any]) -> Optional[List[str]]:
    """Changed paths from a ``gh pr list --json ...,files`` record.

    ``None`` when the record carries no ``files`` key at all, which is the
    honest answer for a ``--from-json`` dump taken before that field was
    fetched, and which FAILS CLOSED at the protected-path rung — the same
    answer the merger gives when it cannot see a PR's files.
    """
    if "files" not in pr:
        return None
    return [str(f.get("path") or "") for f in (pr.get("files") or [])
            if isinstance(f, dict) and f.get("path")]


def build_report(
    prs: List[Dict[str, Any]],
    *,
    default_branch: str,
    linked_urls: Iterable[str] = (),
    linked_lookup_ok: bool = True,
    behind_by_url: Optional[Dict[str, Optional[int]]] = None,
    max_behind_commits: int = DEFAULT_MAX_BEHIND_COMMITS,
    linked_tasks: Optional[Dict[str, Dict[str, Any]]] = None,
    now: Optional[_dt.datetime] = None,
    protected_paths: Iterable[str] = (),
    required_checks: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Classify every PR. Pure — takes data, returns data.

    ``required_checks`` is the branch-protection required set (task-det-
    295a9bb95e); ``None`` reads every check. It is reported back as
    ``required_checks`` (sorted list, or ``None`` for UNRESOLVED) so a reader
    can see which rule the CI rungs applied.

    ``behind_by_url`` maps PR url -> commits behind base, as
    ``measure_behind_map`` returns it. A url absent from the mapping, or
    mapped to ``None``, is UNMEASURED — reported as such and never as fresh.

    ``linked_tasks`` maps PR url -> ``{"task_id": ..., "task_status": ...}``,
    which answers the "whether a task points at it" column WITHOUT changing any
    verdict; ``linked_urls`` remains the authority for the ``linked`` rung, so a
    caller holding only the urls behaves exactly as it did before.

    TWO VERDICTS PER PR, FROM ONE TABLE (kpr-watch-03). ``state``/``reason``
    are the merger's verdict, unchanged and authoritative. ``pipeline_state``/
    ``pipeline_reason`` are the SAME function called with ``linked_urls=()`` —
    "why would this PR not merge, setting aside who owns it". For an unlinked
    PR the two are identical by construction (asserted in the tests); for a
    linked one the second is the diagnosis the ``linked`` rung short-circuits,
    and without it every kanban PR reports "a task owns it" and nothing about
    whether it is red, stale, or simply waiting. This is not a second copy of
    the ladder — it is the one ladder, asked a second question.

    ``protected_paths`` (kpr-watch-10) must be passed for the report to
    evaluate the rung the MERGER evaluates. It was not, so a PR the watcher was
    actively refusing as ``protected_path`` was reported ``linked`` — the report
    describing a merge policy the merger does not have, which is the one thing
    this module's docstring promises cannot happen. A PR record with no
    ``files`` key fails closed, exactly as it does at the merge, so the report
    cannot be more optimistic than the merger either.
    """
    linked = frozenset((u or "").strip() for u in linked_urls)
    task_map = dict(linked_tasks or {})
    behind_map = dict(behind_by_url or {})
    reference = now or _dt.datetime.now(_dt.timezone.utc)
    guarded = [p for p in (protected_paths or ()) if str(p or "").strip()]
    required = frozenset(
        str(n).strip() for n in (required_checks or ()) if str(n).strip()) or None
    rows: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    pipeline_counts: Dict[str, int] = {}
    for pr in prs:
        url = (pr.get("url") or "").strip()
        behind = behind_map.get(url)
        changed = _changed_files(pr)
        verdict = classify_merge_readiness(
            pr, default_branch=default_branch, linked_urls=linked,
            behind_by=behind, max_behind_commits=max_behind_commits,
            changed_files=changed, protected_paths=guarded,
            required_checks=required)
        # BOTH verdicts get the protected-path inputs (kpr-watch-03 x
        # kpr-watch-10). The diagnosis asks the SAME ladder "why would this not
        # merge, setting aside who owns it" — answering that without the rung
        # the merger actually enforces would reintroduce the exact gap
        # kpr-watch-10 closed, one column over.
        diagnosis = (
            verdict if verdict.state != LINKED else classify_merge_readiness(
                pr, default_branch=default_branch, linked_urls=(),
                behind_by=behind, max_behind_commits=max_behind_commits,
                changed_files=changed, protected_paths=guarded,
                required_checks=required))
        counts[verdict.state] = counts.get(verdict.state, 0) + 1
        pipeline_counts[diagnosis.state] = pipeline_counts.get(
            diagnosis.state, 0) + 1
        stamp, basis = last_activity(pr)
        age = state_age_seconds(pr, now=reference)
        task = task_map.get(url) or {}
        # A THIRD AXIS (rem-hyg-12), computed for EVERY row and never only for
        # the ones the ladder let through to the staleness rung. A `draft` or
        # `linked` PR's state can never be `behind_main` — it short-circuits
        # earlier, correctly — so without this the report is silent about the
        # one fact that makes un-drafting it dangerous.
        stale, stale_reason = staleness(
            behind, default_branch=default_branch,
            max_behind_commits=max_behind_commits,
            merge_state_status=pr.get("mergeStateStatus") or "")
        rows.append({
            "number": pr.get("number"),
            "url": url,
            "title": pr.get("title") or "",
            "head": pr.get("headRefName") or "",
            "base": pr.get("baseRefName") or "",
            "state": verdict.state,
            "reason": verdict.reason,
            "pipeline_state": diagnosis.state,
            "pipeline_reason": diagnosis.reason,
            "ready": verdict.ready,
            "mergeable": (pr.get("mergeable") or "").upper(),
            "merge_state_status": (pr.get("mergeStateStatus") or "").upper(),
            # None, never 0 — an unmeasured branch and a branch measured level
            # with main are different facts and only one of them is evidence.
            "behind_by": behind,
            "behind_measured": behind is not None,
            # None, never False — see ``staleness``. True/False is a MEASURED
            # answer; None is "nobody compared this branch".
            "stale": stale,
            "stale_reason": stale_reason,
            "is_draft": bool(pr.get("isDraft")),
            "labels": sorted((lbl.get("name") or "").strip()
                             for lbl in (pr.get("labels") or [])),
            "linked": url in linked,
            "task_id": task.get("task_id"),
            "task_status": task.get("task_status"),
            # A LOWER BOUND (see ``last_activity``): None, never 0, when
            # nothing datable was on the record.
            "state_age_seconds": age,
            "state_age_measured": age is not None,
            "state_age_basis": basis,
            "last_activity_at": stamp.isoformat() if stamp else None,
        })
    rows.sort(key=lambda r: (r["state"] != READY, r["number"] or 0))
    return {
        "default_branch": default_branch,
        # Which CI rule the rungs applied: the required set, or None when it
        # was UNRESOLVED and every check counted (task-det-295a9bb95e).
        "required_checks": sorted(required) if required else None,
        # Never silent: without the board we cannot tell linked from unlinked,
        # so every PR is classified on its own merits and the caller is told.
        "linked_lookup_ok": bool(linked_lookup_ok),
        "linked_count": len(linked),
        "max_behind_commits": int(max_behind_commits),
        "behind_measured_count": sum(1 for r in rows if r["behind_measured"]),
        # Three counts, not two, and the third is the point (rem-hyg-12): a PR
        # nobody compared must not be absorbed into "not stale". `stale_count`
        # is what a reader acts on; `stale_unmeasured_count` is what says how
        # much of the board that number does not cover.
        "stale_count": sum(1 for r in rows if r["stale"] is True),
        "stale_unmeasured_count": sum(1 for r in rows if r["stale"] is None),
        "age_measured_count": sum(1 for r in rows if r["state_age_measured"]),
        "generated_at": reference.astimezone(_dt.timezone.utc).isoformat(),
        "total": len(rows),
        "ready": counts.get(READY, 0),
        "counts": dict(sorted(counts.items())),
        "pipeline_counts": dict(sorted(pipeline_counts.items())),
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
    lines.append("%-7s %-14s %-7s %-8s %-32s %s"
                 % ("PR", "STATE", "BEHIND", "AGE", "BRANCH", "REASON"))
    lines.append("%-7s %-14s %-7s %-8s %-32s %s"
                 % ("-" * 7, "-" * 14, "-" * 7, "-" * 8, "-" * 32, "-" * 40))
    for row in report["prs"]:
        # "?" not "0" -- an unmeasured branch must not read as an up-to-date one.
        # A "!" marks a row the staleness axis flags, which for a `draft` or
        # `linked` PR is the ONLY place the fact appears: its state can never
        # be `behind_main` (rem-hyg-12).
        behind = "?" if row.get("behind_by") is None else str(row["behind_by"])
        if row.get("stale") is True:
            behind = ("%s!" % behind) if behind != "?" else "BEHIND!"
        lines.append("%-7s %-14s %-7s %-8s %-32s %s" % (
            "#%s" % row["number"], row["state"], behind,
            format_age(row.get("state_age_seconds")),
            (row["head"] or "?")[:32], row["reason"]))
    lines.append("")
    lines.append("by state: " + ", ".join(
        "%s=%d" % (k, v) for k, v in report["counts"].items()) or "by state: -")
    lines.extend(_staleness_footer(report))
    return "\n".join(lines)


def _staleness_footer(report: Dict[str, Any]) -> List[str]:
    """The staleness summary both renderers print. One copy, so the flat table
    and the grouped view cannot report different coverage.

    Names the UNMEASURED count explicitly (rem-hyg-12): "2 stale" over a board
    where five PRs were never compared is a different statement from "2 stale"
    over a board where all of them were, and a reader cannot tell which without
    being told."""
    lines = [
        "staleness: refused above %d commit(s) behind %s; measured for %d of "
        "%d PR(s)"
        % (report.get("max_behind_commits", DEFAULT_MAX_BEHIND_COMMITS),
           report["default_branch"], report.get("behind_measured_count", 0),
           report["total"])]
    stale = report.get("stale_count")
    unmeasured = report.get("stale_unmeasured_count")
    if stale or unmeasured:
        lines.append(
            "  %s behind the limit (marked '!'); %s UNMEASURED -- not the same "
            "as up to date" % (stale or 0, unmeasured or 0))
    return lines


def render_grouped(report: Dict[str, Any]) -> str:
    """The same report, bucketed by state so ``ready`` and ``behind_main`` are
    visible without scrolling past everything that is fine.

    ASCII-only, for the same reason ``render_table`` is: a box-drawing
    character raises on a cp1252 console.
    """
    lines: List[str] = []
    if not report.get("linked_lookup_ok", True):
        lines.append(
            "WARNING: the kanban board was unreadable, so no PR could be "
            "identified as task-linked. States below are otherwise accurate.")
    lines.append(
        "%d open PR(s) against %s -- %d awaiting merge"
        % (report["total"], report["default_branch"], report["ready"]))
    groups = group_by_state(report)
    if not groups:
        return "\n".join(lines)
    for group in groups:
        lines.append("")
        lines.append("== %s (%d)%s" % (
            group["state"].upper(), group["count"],
            "  [waiting on automation]" if group["blocked_on_automation"] else ""))
        lines.append("   %-7s %-8s %-16s %-30s %s"
                     % ("PR", "AGE", "TASK", "BRANCH", "REASON"))
        for row in group["prs"]:
            lines.append("   %-7s %-8s %-16s %-30s %s" % (
                "#%s" % row["number"],
                format_age(row.get("state_age_seconds")),
                (row.get("task_id") or ("linked" if row.get("linked") else "-"))[:16],
                (row.get("head") or "?")[:30],
                (row.get("pipeline_reason") or row.get("reason") or "")[:70],
            ))
            # rem-hyg-12. This view has no BEHIND column, and it is the one the
            # kanban CLI and the dashboard read. A `draft` row's reason says
            # "mark it ready for review to land it" and nothing else -- so a
            # branch 13 commits behind main renders identically to a fresh one
            # unless the axis is printed on its own line.
            if row.get("stale") is True:
                lines.append("   %-7s %s" % ("", "STALE: " + (
                    row.get("stale_reason") or "behind the base branch")))
    lines.append("")
    lines.append(
        "age is a LOWER BOUND -- nothing persists a state transition, so it is "
        "measured from the newest event on the PR; '?' means unmeasured")
    lines.extend(_staleness_footer(report))
    return "\n".join(lines)


def collect_report(
    *,
    limit: int = 100,
    default_branch: Optional[str] = None,
    from_json: Optional[str] = None,
    max_behind_commits: Optional[int] = None,
    measure_behind: bool = True,
    required_checks: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Gather the inputs and return the classified report. READ ONLY.

    ``required_checks`` may be supplied (the watcher passes its cached set);
    otherwise it is resolved here through ``fetch_required_checks`` when the
    watcher's ``required_checks_only`` knob is on -- the same rule the merger
    applies, so the report cannot call ``ci_failed`` what the merger will
    merge. ``None`` (unresolved, or the knob off) reads every check.

    Extracted from ``main`` (kpr-watch-03) so the CLI, the kanban CLI and the
    dashboard panel all obtain the report the SAME way. A second caller that
    re-assembled these inputs by hand is how a surface starts describing a
    different pipeline from the one that runs -- the same failure mode the
    module docstring bans for the ladder itself.

    Raises on an input it could not obtain. An empty report and a report that
    could not be produced are different answers, and only the first is data.
    """
    if from_json:
        path = pathlib.Path(from_json)
        prs = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(prs, list):
            raise ValueError("%s does not contain a PR list" % path)
    else:
        prs = list_open_prs(limit=limit)

    if not default_branch:
        from tools.ci.pr_watcher import repo_default_branch  # local: import cycle
        default_branch = repo_default_branch()

    # FAIL-OPEN and never silent: without the board nothing can be identified as
    # task-linked, so every PR is classified on its own merits and the caller is
    # handed `linked_lookup_ok: False` to say so.
    linked_lookup_ok = True
    linked_lookup_error = None
    tasks: Dict[str, Dict[str, Any]] = {}
    try:
        tasks = linked_pr_tasks()
    except Exception as exc:  # noqa: BLE001 - degraded, and the report SAYS it is
        tasks, linked_lookup_ok = {}, False
        linked_lookup_error = str(exc)[:300]
    linked = frozenset(tasks)

    if max_behind_commits is None:
        max_behind_commits = _configured_max_behind()

    # STALENESS IS MEASURED FOR EVERY OPEN PR (rem-hyg-12), not for the
    # `ready` subset. This used to run in two phases -- classify, then
    # `/compare` only the urls already classified `ready` -- which is right for
    # the MERGER (a non-ready PR will not merge, so the count cannot change its
    # verdict) and wrong for the REPORT, which is what a human reads BEFORE
    # deciding to un-draft or merge something. #1850 was a draft, MERGEABLE,
    # CLEAN, and 13 commits behind main with a diff of +97/-1691 against it;
    # #1845 was linked and 16 behind. Both short-circuited the ladder before
    # the staleness rung, so neither was ever measured and the report said
    # nothing.
    #
    # THE LADDER IS NOT REORDERED and no merge verdict moves: every rung above
    # the staleness one short-circuits, so handing it a count it previously
    # lacked cannot change a non-ready PR's `state`. That equivalence is
    # asserted in tests/test_merge_readiness_staleness.py, and it is what
    # proves the cost optimisation was removed from the REPORT and not from the
    # MERGER -- `pr_watcher` keeps its own lazy probe, untouched.
    #
    # WHAT IT COSTS. One `/compare` per DISTINCT (base, head sha) rather than
    # per ready PR: on a board of ~15 open PRs that is ~15 calls against a
    # 5,000/hr budget, and the dashboard's 120s cache means a tab does not pay
    # it per refresh. `--no-measure-behind` turns the whole thing off, and then
    # every PR reports UNMEASURED rather than fresh.
    # The dashboard surface evaluates the SAME protected-path rung the merger
    # does (kpr-watch-10). Without it this report answers `linked` for a PR the
    # watcher is actively refusing — which is the defect kpr-watch-10 closed,
    # reappearing one surface over. Supplied only when the records carry
    # `files`, for the reason main() gives.
    guarded = load_protected_paths() if any("files" in pr for pr in prs) else []
    behind = None
    if measure_behind and prs:
        behind = measure_behind_map(prs, default_branch=default_branch)
    # Resolved from the forge only for a LIVE report. A `--from-json` replay is
    # a record of some other moment (or a test fixture) and the branch
    # protection of the repo this happens to run in says nothing about it --
    # pass `required_checks` explicitly to replay under a rule.
    if required_checks is None and not from_json and _required_checks_only():
        required_checks = fetch_required_checks(default_branch)
    report = build_report(
        prs, default_branch=default_branch, linked_urls=linked,
        linked_lookup_ok=linked_lookup_ok, linked_tasks=tasks,
        behind_by_url=behind, max_behind_commits=max_behind_commits,
        protected_paths=guarded, required_checks=required_checks)
    # Why the board was unreadable, not merely that it was. A degraded report
    # that cannot say what degraded it sends the reader to the wrong fix.
    report["linked_lookup_error"] = linked_lookup_error
    return report


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
    parser.add_argument("--group", action="store_true",
                        help="bucket the table by state (attention order: ready "
                             "and behind_main first) instead of one flat list")
    args = parser.parse_args(argv)

    if args.state:
        bad = [s for s in args.state if s not in MERGE_STATES]
        if bad:
            parser.error("unknown state(s): %s" % ", ".join(bad))

    # ONE gatherer, shared with the dashboard panel and the kanban CLI, so a
    # surface cannot describe a pipeline this command would report differently.
    try:
        report = collect_report(
            limit=args.limit,
            default_branch=args.default_branch,
            from_json=args.from_json,
            max_behind_commits=args.max_behind,
            measure_behind=not args.no_measure_behind,
        )
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        return _fail(args.json, "cannot produce the report: %s" % exc)
    if not report.get("linked_lookup_ok", True):
        print("warning: kanban board unreadable (%s) -- no PR could be "
              "identified as task-linked"
              % (report.get("linked_lookup_error") or "no detail"),
              file=sys.stderr)

    if args.state:
        wanted = set(args.state)
        report["prs"] = [r for r in report["prs"] if r["state"] in wanted]
        report["filtered_to"] = sorted(wanted)

    if args.json:
        if args.group:
            report["groups"] = group_by_state(report)
        print(json.dumps(report, indent=2))
    else:
        print(render_grouped(report) if args.group else render_table(report))
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
