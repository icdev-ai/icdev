#!/usr/bin/env python3
# CUI // SP-CTI
"""Stranded-branch auditor (kph-A).

Retroactively reconciles kanban_tasks rows marked ``done`` or ``validating``
against ``origin/<default>``: a task whose ``kanban/<id>`` branch still has
commits NOT on origin is **stranded** — built-but-never-merged (the "42/42 done
LIED" class that hid the whole Cortex surface on 9 unmerged branches).

Why this is needed: the transition-time merge-verify gate
(``kanban._move_task`` -> ``_branch_has_unmerged_commits``) only fires at the
instant a task is written to ``done`` and is fail-open; nothing audits rows that
are ALREADY terminal, and the ``validating`` status is otherwise dead code
(nothing sets it, nothing detects "stuck"). This auditor closes that gap.

Surface, don't auto-act: findings are written to a JSON report and filed as HITL
``suggested`` remediation cards (quarantine state — never fed straight to the
enforcing runner). GREEN tier: reads git + kanban_tasks, writes suggested cards.

Headless:  python -m tools.kanban.stranded_audit --json
"""
from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 — git plumbing only, no user input in argv
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("kanban.stranded_audit")

_TERMINAL_STATES = ("done", "validating")

#: Most `suggested` cards one run may file. See :func:`_file_suggested_cards`.
_MAX_CARDS_PER_RUN = 25
_REPORT_PATH = BASE_DIR / ".tmp" / "kanban" / "stranded_audit.json"


def _default_branch() -> str:
    """Resolve the default branch name (origin/HEAD -> main), best-effort."""
    try:
        r = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().rsplit("/", 1)[-1]
    except Exception:  # noqa: BLE001
        pass
    return "main"


#: Set by :func:`prime_pr_state_cache` and reported as ``pr_state_source``.
#: "bulk" = every branch's PR state is known; "unavailable" = gh could not be
#: reached, so no branch can be recognised as already-merged and the run WILL
#: contain the false strandings this module exists to avoid. Never let that read
#: as a clean audit — the report states the posture instead (the same discipline
#: chain_sweep uses for `pre_cutover` and capability_consumption for
#: `telemetry_available: false`).
_PR_STATE_SOURCE = "unprimed"


#: Upper bound for the single bulk PR query. Must exceed the repo's lifetime PR
#: count or the listing silently truncates and every branch beyond it looks
#: "no PR" -> "not abandoned" -> falsely stranded. Measured 2026-08-15: 1,661
#: PRs across 1,594 branches, fetched in 3.9s, so this has ~3x headroom. A run
#: that hits the limit reports ``pr_state_source: "truncated"`` rather than
#: pretending the tail does not exist.
_PR_QUERY_LIMIT = 5000


def prime_pr_state_cache(refs=None, limit: int = _PR_QUERY_LIMIT) -> int:
    """Populate the GATE's abandoned-branch cache from ONE ``gh`` call.

    ``reflexes.kanban._branch_is_abandoned`` asks ``gh pr list --head <branch>``
    per branch and memoises the answer. That is correct but O(branches) network
    round-trips, and this auditor walks every terminal task — which is why the
    reflex died on ``watchdog_timeout_300s`` with the circuit breaker open and
    filed zero cards despite 25 runs. Pre-seeding its cache in bulk gives the
    audit the gate's exact semantics at one round-trip instead of hundreds.

    Returns the number of branches primed. On any failure returns 0 and leaves
    the cache alone; callers must then treat "merged" as UNKNOWN rather than
    assume it, which :func:`_stranded_git_check` does by consulting the cache
    only for entries that were actually primed.
    """
    global _PR_STATE_SOURCE
    try:
        from tools.genesis.reflexes import kanban as _k

        out = subprocess.run(
            ["gh", "pr", "list", "--state", "all", "--limit", str(limit),
             "--json", "headRefName,state"],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=120,
        )
        if out.returncode != 0 or not out.stdout.strip():
            _PR_STATE_SOURCE = "unavailable"
            return 0
        entries = json.loads(out.stdout)
        by_branch: Dict[str, set] = {}
        for entry in entries:
            name = (entry.get("headRefName") or "").strip()
            if name:
                by_branch.setdefault(name, set()).add(entry.get("state"))
        for name, states in by_branch.items():
            # setdefault, never overwrite: a value already in the cache came from
            # the gate's own authoritative per-branch query this process.
            _k._ABANDONED_BRANCH_CACHE.setdefault(
                name, bool(states) and states <= {"CLOSED", "MERGED"})

        # Was the listing COMPLETE? This must be decided BEFORE priming negatives:
        # on a truncated listing "absent" means "unknown", not "has no PR", and
        # caching those as not-abandoned would manufacture exactly the false
        # strandings this function exists to remove.
        truncated = len(entries) >= limit
        _PR_STATE_SOURCE = "truncated" if truncated else "bulk"
        if truncated:
            logger.warning(
                "stranded_audit: PR listing hit the %d limit — branches beyond it "
                "cannot be recognised as merged and may report as stranded", limit)
            return len(by_branch)

        # Prime the NEGATIVES. Without this, every branch that has no PR --
        # 3,007 of the repo's 5,355 refs, largely abandoned experiments and local
        # `merge/*` working branches -- misses the cache and falls through to a
        # live `gh pr list --head <branch>`. At 0.31s each that is 15.5 minutes,
        # which is how this stayed over the 300s watchdog even after the bulk
        # query was added. "Absent from a COMPLETE PR listing" and "gh said no
        # PRs for this branch" are the same fact, so caching False here changes
        # no answer -- it only stops asking a question already answered.
        for ref in (refs or []):
            name = ref.split("origin/", 1)[-1] if ref.startswith("origin/") else ref
            _k._ABANDONED_BRANCH_CACHE.setdefault(name, False)
        return len(by_branch)
    except Exception as exc:  # noqa: BLE001 — priming is an optimisation
        logger.warning("stranded_audit: PR-state priming failed (%s)", exc)
        _PR_STATE_SOURCE = "unavailable"
        return 0


def merged_refs(default_branch: str) -> set:
    """Every ref fully contained in ``origin/<default_branch>``, in ONE git call.

    ``git for-each-ref --merged`` answers for all 5,355 refs at once what
    ``git rev-list --count origin/main..<ref>`` answers for one. Per-ref it cost
    ~0.29s/task and projected to 932s; as a set membership test it is free.

    Returns an EMPTY set on any error, which is the safe direction: an empty set
    means "nothing is known-merged", so every ref falls through to the exact
    per-ref comparison this is accelerating. Slow, never wrong.
    """
    try:
        out = subprocess.run(
            ["git", "for-each-ref", f"--merged=origin/{default_branch}",
             "--format=%(refname:short)", "refs/heads", "refs/remotes/origin"],
            cwd=str(BASE_DIR), capture_output=True, text=True, timeout=60,
        )
        if out.returncode != 0:
            return set()
        return {ln.strip() for ln in out.stdout.splitlines() if ln.strip()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("stranded_audit: merged-ref listing failed (%s)", exc)
        return set()


#: ref -> unmerged patch count, for ONE audit run. `git cherry`'s answer depends
#: only on (ref, origin/default), never on which task asked, and task ids match
#: refs many-to-many — a parent id matches every child's branch, so the same ref
#: is re-costed once per related task. Memoising caps the expensive compare at
#: the number of DISTINCT unmerged refs instead of the number of task lookups.
#: Cleared per run by :func:`audit_stranded_tasks`; never persisted, because a
#: stale entry would report yesterday's merge state as today's.
_CHERRY_CACHE: Dict[str, int] = {}


def _stranded_git_check(task_id: str, default_branch: str, refs=None,
                        merged=None) -> Tuple[bool, int]:
    """Return (branch_exists, unmerged_commit_count) using the GATE's semantics.

    This used to be an independent implementation, and it disagreed with the
    merge-verify gate three ways — every one of which produced a wrong answer:

      * ``git log origin/<default>..<ref>`` counts by ANCESTRY, so a squash-merge
        (which lands the patch under a new SHA with no ancestry link) and every
        rebase/cherry-pick re-land read as unmerged. ``git cherry`` compares by
        patch-id and is what the gate uses.
      * it never asked whether the branch's PR was already merged, so a finished
        branch counted forever. The gate skips those via ``_branch_is_abandoned``.
      * it matched only the exact name ``kanban/<task_id>``, missing the
        descriptive-suffix branches workers routinely push
        (``kanban/dwo-mcp-02-d5-audit``) that the gate finds by substring.

    Measured on the live board 2026-08-15 before this change: of 506 tasks
    reported stranded, 184 (36%) had branches whose PRs were ALL closed or
    merged — 159 of them MERGED — and the gate skipped every one. That 36% is a
    LOWER bound; only the most recent 1,000 PRs could be checked, so older
    merged branches are not even in the count. Six of the eight tasks landed
    that same day were reported stranded while their work was verifiably on
    main, and the two that were not are exactly the two merged with a merge
    commit rather than a squash.

    ``exists`` is deliberately computed BEFORE the already-merged filter: a
    ``validating`` row whose branch merged still HAS a branch, and reporting it
    as branchless would move it from one false finding (stranded) to another
    (orphan_validating).
    """
    try:
        from tools.genesis.reflexes import kanban as _k

        candidates = _k._branches_for_task(task_id, BASE_DIR, refs=refs)
        if not candidates:
            return False, 0
        max_unmerged = 0
        for ref in candidates:
            if _k._branch_is_abandoned(ref, BASE_DIR):
                continue

            # Fast path FIRST: `git cherry` computes a patch-id for every commit
            # in the symmetric difference, which measured 1.4s per task here and
            # projected to 4,503s for the board — an order of magnitude past the
            # 300s watchdog on its own. A ref fully contained in origin is merged
            # under any definition, so the expensive compare is never needed for
            # it. Both fast paths are pure accelerators: they can only skip work
            # whose answer is already known, never change one.
            if merged is not None:
                if ref in merged:
                    continue
            else:
                ahead = subprocess.run(
                    ["git", "rev-list", "--count", f"origin/{default_branch}..{ref}"],
                    cwd=str(BASE_DIR), capture_output=True, text=True, timeout=15,
                )
                if ahead.returncode == 0 and ahead.stdout.strip() in ("0", ""):
                    continue

            # Only now, for refs that genuinely sit ahead by ancestry, pay for
            # patch-id equivalence — the squash/rebase/cherry-pick re-land case
            # that ancestry alone gets wrong. Memoised per ref (see _CHERRY_CACHE).
            if ref in _CHERRY_CACHE:
                max_unmerged = max(max_unmerged, _CHERRY_CACHE[ref])
                continue
            cherry = subprocess.run(
                ["git", "cherry", f"origin/{default_branch}", ref],
                cwd=str(BASE_DIR), capture_output=True, text=True, timeout=15,
            )
            if cherry.returncode != 0:
                continue  # this compare errored — fail-open, and do NOT cache it
            n = len([ln for ln in cherry.stdout.splitlines() if ln.startswith("+")])
            _CHERRY_CACHE[ref] = n
            max_unmerged = max(max_unmerged, n)
        return True, max_unmerged
    except Exception as exc:  # noqa: BLE001
        logger.warning("stranded_audit: git check %s errored (fail-open): %s", task_id, exc)
        return False, 0


#: Back-compat alias. The old name said "count commits on kanban/<id>", which is
#: no longer what it does, but external callers should not break on a rename.
_branch_unmerged_count = _stranded_git_check


def audit_stranded_tasks(
    conn=None,
    git_check: Optional[Callable[[str], Tuple[bool, int]]] = None,
    fetch: bool = True,
) -> Dict:
    """Classify every terminal (done/validating) kanban task vs origin/main.

    Returns {default_branch, total, stranded[], orphan_validating[], clean_count}.
    ``git_check`` (task_id -> (exists, unmerged)) is injectable for tests.
    """
    default_branch = _default_branch()
    if fetch and git_check is None:
        try:
            subprocess.run(
                ["git", "fetch", "origin", default_branch, "--quiet"],
                cwd=str(BASE_DIR), capture_output=True, text=True, timeout=20,
            )
        except Exception:  # noqa: BLE001
            pass
        # One bulk PR query BEFORE the per-task walk. Without it the gate's
        # _branch_is_abandoned issues a `gh` round-trip per branch and the run
        # exceeds the 300s reflex watchdog — which is the state this module was
        # actually in: circuit breaker open, 0 cards filed, 14 failures in 25 runs.
    # One ref listing for the whole walk. Per-call it is a subprocess each, and
    # this loop runs once per terminal task — the second half of the watchdog
    # timeout. Snapshotting is safe HERE (unlike in the gate) because a branch
    # pushed mid-audit belongs to work that is still in flight, which this
    # retrospective audit is not trying to judge.
    _refs = None
    _merged = None
    if git_check is None:
        try:
            from tools.genesis.reflexes.kanban import all_task_refs
            _refs = all_task_refs(BASE_DIR)
        except Exception as exc:  # noqa: BLE001 — fall back to per-call listing
            logger.warning("stranded_audit: ref snapshot failed (%s)", exc)
        # Priming needs the ref list, so it must come after the snapshot.
        if fetch:
            prime_pr_state_cache(refs=_refs)
        _merged = merged_refs(default_branch)
        _CHERRY_CACHE.clear()  # per-run: merge state changes between runs
    check = git_check or (
        lambda tid: _stranded_git_check(tid, default_branch, refs=_refs, merged=_merged))

    own = conn is None
    if own:
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
        except Exception as exc:  # noqa: BLE001
            logger.warning("stranded_audit: DB unavailable (%s)", exc)
            return {"default_branch": default_branch, "total": 0, "stranded": [],
                    "orphan_validating": [], "validating_with_branch": [],
                    "clean_count": 0, "error": str(exc)}
    try:
        try:
            rows = conn.execute(
                "SELECT id, status, title FROM kanban_tasks WHERE status IN (%s, %s)",
                _TERMINAL_STATES,
            ).fetchall()
        except Exception as exc:  # noqa: BLE001 — table may not exist yet
            logger.warning("stranded_audit: kanban_tasks query failed (%s)", exc)
            rows = []
    finally:
        if own:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    stranded: List[dict] = []
    orphan_validating: List[dict] = []
    validating_with_branch: List[dict] = []
    clean = 0
    for row in rows:
        tid = row["id"] if not isinstance(row, (tuple, list)) else row[0]
        status = row["status"] if not isinstance(row, (tuple, list)) else row[1]
        title = (row["title"] if not isinstance(row, (tuple, list)) else row[2]) or ""
        exists, unmerged = check(tid)
        if exists and unmerged > 0:
            stranded.append({"id": tid, "status": status, "title": title,
                             "unmerged_commits": unmerged, "branch": f"kanban/{tid}"})
        elif status == "validating" and not exists:
            # validating is dead code: a validating row with no branch is stuck.
            orphan_validating.append({"id": tid, "status": status, "title": title})
        else:
            clean += 1
            if status == "validating":
                # NEITHER stranded NOR orphan (kpr-stale-06): a validating row
                # whose branch exists with nothing unmerged. Still counted clean
                # here -- nothing is stranded -- but it is stuck all the same,
                # and this list is what orphan_requeue.act_on_empty_checkouts
                # re-proves (branch provably empty, worktree provably clean)
                # before it removes the checkout and requeues the row.
                validating_with_branch.append({"id": tid, "status": status,
                                               "title": title,
                                               "unmerged_commits": unmerged})

    return {
        "default_branch": default_branch,
        "total": len(rows),
        "stranded": stranded,
        "orphan_validating": orphan_validating,
        "validating_with_branch": validating_with_branch,
        "clean_count": clean,
        # State the posture rather than let an unmeasurable run look clean. When
        # this is "unavailable" the already-merged filter could not run, so the
        # stranded list is an UPPER bound carrying the 36% false-positive rate
        # measured before the fix; "injected" means a caller supplied git_check
        # and no PR state was consulted at all.
        "pr_state_source": "injected" if git_check is not None else _PR_STATE_SOURCE,
    }


def _file_suggested_cards(findings: Dict) -> List[str]:
    """File HITL 'suggested' remediation cards for stranded/orphan tasks (best-effort).

    Uses the canonical task_factory.create_tasks (never raw INSERT); ids are stable
    so daily re-runs dedupe rather than pile up.
    """
    default_branch = findings.get("default_branch", "main")
    specs: List[dict] = []
    for f in findings.get("stranded", []):
        specs.append({
            "id": f"kph-stranded-{f['id']}",
            "title": f"[STRANDED] {f['id']}: {f['unmerged_commits']} commit(s) not on origin/{default_branch}",
            "description": (
                f"Task {f['id']} (status={f['status']}) has branch {f['branch']} with "
                f"{f['unmerged_commits']} commit(s) not merged to origin/{default_branch}. "
                f"Its artifacts are NOT on main — the card lies about being complete. "
                f"HITL: cherry-pick/reconcile onto origin/main via a fresh worktree, or re-open the task."
            ),
            "task_type": "bug",
            "priority": "high",
            "status": "suggested",
            "idempotency_key": f"stranded-audit-{f['id']}",
        })
    for f in findings.get("orphan_validating", []):
        specs.append({
            "id": f"kph-orphan-{f['id']}",
            "title": f"[STRANDED] {f['id']}: stuck in 'validating' with no branch",
            "description": (
                f"Task {f['id']} is in 'validating' but has no kanban/{f['id']} branch "
                f"(local or origin). 'validating' is otherwise dead in the runner, so this "
                f"row is stuck. HITL: verify whether the work merged and re-classify."
            ),
            "task_type": "chore",
            "priority": "medium",
            "status": "suggested",
            "idempotency_key": f"stranded-audit-{f['id']}",
        })
    # Bound the batch. Ids are stable so re-runs dedupe rather than pile up, but
    # the FIRST run after this fix faces a backlog that had been accumulating
    # unreported while the reflex sat circuit-broken — ~322 genuine findings on
    # 2026-08-15. Filing all of them at once buries the board in `suggested`
    # cards, and a remediation queue nobody can read gets ignored wholesale,
    # which leaves the findings exactly as invisible as they were. The cap is
    # per-run, not per-lifetime: the remainder arrives tomorrow, worst-first.
    _dropped = 0
    if len(specs) > _MAX_CARDS_PER_RUN:
        _dropped = len(specs) - _MAX_CARDS_PER_RUN
        specs = specs[:_MAX_CARDS_PER_RUN]
        # Never let a cap read as "that was all of them" (CLAUDE.md: no silent
        # truncation — a bounded run must say what it dropped).
        logger.warning(
            "stranded_audit: filed %d cards and DEFERRED %d more to the next run "
            "(cap=%d); the findings still exist — see the JSON report for the full list",
            len(specs), _dropped, _MAX_CARDS_PER_RUN)
        findings["cards_deferred"] = _dropped

    if not specs:
        return []
    try:
        from tools.kanban.task_factory import create_tasks
        return create_tasks(specs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("stranded_audit: could not file suggested cards (%s)", exc)
        return []


def _record_lessons(findings: Dict) -> int:
    """Record an UNMERGED_STRANDED lesson per stranded/orphan task (best-effort).

    Feeds the Lessons-Learned engine so persistent stranding builds a recurrence
    score and escalates to remediation — complementary to the suggested cards,
    which are the direct surface. De-dup + remediation gating live in the lessons
    engine (write_lesson / maybe_create_remediation_card).
    """
    ids = [f["id"] for f in findings.get("stranded", [])] + \
          [f["id"] for f in findings.get("orphan_validating", [])]
    if not ids:
        return 0
    n = 0
    try:
        from tools.workflow.lesson_learned import analyze_task, write_lesson
    except Exception as exc:  # noqa: BLE001
        logger.warning("stranded_audit: lessons engine unavailable (%s)", exc)
        return 0
    for tid in ids:
        try:
            write_lesson(analyze_task(tid, outcome="unmerged_stranded"))
            n += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("stranded_audit: lesson for %s failed (%s)", tid, exc)
    return n


def run(config: dict, state: object) -> dict:
    """Genesis reflex entry point."""
    try:
        findings = audit_stranded_tasks()
        created = _file_suggested_cards(findings)
        _record_lessons(findings)
        try:
            _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            _REPORT_PATH.write_text(json.dumps({**findings, "cards_filed": created}, indent=2),
                                    encoding="utf-8", newline="")
        except Exception:  # noqa: BLE001
            pass
        n_stranded = len(findings.get("stranded", [])) + len(findings.get("orphan_validating", []))
        logger.info("stranded_audit: %d stranded/orphan of %d terminal task(s); %d card(s) filed",
                    n_stranded, findings.get("total", 0), len(created))
        return {"success": True, "metric_value": float(n_stranded),
                "details": {**findings, "cards_filed": created}}
    except Exception as exc:  # noqa: BLE001
        logger.exception("stranded_audit failed: %s", exc)
        return {"success": False, "error": str(exc), "metric_value": 0.0}


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit kanban tasks stranded off origin/main.")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    ap.add_argument("--no-cards", action="store_true", help="Detect only; do not file suggested cards")
    ap.add_argument("--no-fetch", action="store_true", help="Skip git fetch")
    args = ap.parse_args()
    findings = audit_stranded_tasks(fetch=not args.no_fetch)
    created = [] if args.no_cards else _file_suggested_cards(findings)
    out = {**findings, "cards_filed": created}
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        s = len(findings["stranded"]) + len(findings["orphan_validating"])
        print(f"Terminal tasks: {findings['total']} | stranded/orphan: {s} | clean: {findings['clean_count']}")
        for f in findings["stranded"]:
            print(f"  STRANDED  {f['id']}: {f['unmerged_commits']} commit(s) off origin/{findings['default_branch']}")
        for f in findings["orphan_validating"]:
            print(f"  ORPHAN    {f['id']}: validating, no branch")
        if created:
            print(f"Filed {len(created)} suggested card(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
