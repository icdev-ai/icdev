#!/usr/bin/env python3
"""kanban CLI — one-stop wrapper for kanban_tasks operations via get_connection().

Replaces the pattern of writing throwaway .tmp/sqlite3 scripts.
Always routes through the configured storage backend (PostgreSQL in production).

Usage examples:
  # Mark one or more tasks done
  python tools/kanban/cli.py --set-status zig-ext-08 done
  python tools/kanban/cli.py --set-status zig-ext-08 zig-ext-09 zig-ext-10 done

  # Show a single task
  python tools/kanban/cli.py --show zig-ext-08

  # List tasks by prefix and/or status
  python tools/kanban/cli.py --list --prefix zig-ext
  python tools/kanban/cli.py --list --prefix zig-ext --status backlog
  python tools/kanban/cli.py --list --status in_progress

  # JSON output (pipe-friendly)
  python tools/kanban/cli.py --list --prefix zig-ext --json
  python tools/kanban/cli.py --show zig-ext-08 --json

  # Clear a stale done-gate block without re-dispatching the task
  python tools/kanban/cli.py --reverify zig-ext-08 --dry-run
  python tools/kanban/cli.py --reverify zig-ext-08 --json

  # SATISFY the done-gate: land the task's PR, then mark done
  python tools/kanban/cli.py --set-status zig-ext-08 done --merge --dry-run
  python tools/kanban/cli.py --set-status zig-ext-08 done --merge --json
"""

import argparse
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO_MARKERS = ("args/projects.yaml", "goals/manifest.md")


def _find_repo_root(start: Path) -> Path:
    """Walk up from ``start`` until a repo marker is found.

    A fixed ``parents[N]`` is fragile: from ``tools/kanban/cli.py`` the historic
    ``parents[3]`` resolves to ``C:\\ai`` — one level ABOVE the repo — so
    ``load_dotenv`` loaded nothing and ``from icdev.tools.*`` then bound to a
    globally-installed editable ``icdev`` package belonging to a DIFFERENT repo,
    making the CLI read/write the wrong database (silent "NOT FOUND" for real
    tasks). Marker-walking finds the true repo root regardless of whether this
    file is the canonical ``tools/kanban/`` copy, the ``icdev/tools/kanban/``
    mirror, or a relocated invocation.
    """
    for candidate in (start, *start.parents):
        if all((candidate / m).exists() for m in _REPO_MARKERS):
            return candidate
    # Fallback: canonical repo layout is two levels above tools/kanban/.
    return start.parents[1] if len(start.parents) > 1 else start


def _storage_shadow_error(storage_path, repo_root) -> str | None:
    """Return an error message iff ``storage_path`` lives OUTSIDE ``repo_root``.

    Guards against a globally-installed editable ``icdev``/``tools`` package
    shadowing the repo-local ``tools.db.storage`` — which would silently point
    the CLI at a DIFFERENT repo's database. Returns ``None`` when the resolved
    storage module is repo-local (the safe case).
    """
    storage_path = Path(storage_path).resolve()
    repo_root = Path(repo_root).resolve()
    try:
        is_local = storage_path.is_relative_to(repo_root)
    except AttributeError:  # Python < 3.9: is_relative_to() unavailable
        is_local = repo_root in storage_path.parents
    if is_local:
        return None
    return (
        "FATAL: tools.db.storage resolved OUTSIDE this repo — refusing to run.\n"
        f"  storage module: {storage_path}\n"
        f"  repo root:      {repo_root}\n"
        "A globally-installed 'icdev'/'tools' package is shadowing the repo-local\n"
        "module; the CLI would read/write the WRONG repo's database. Uninstall the\n"
        "shadow (pip uninstall icdev) or run from the repo root."
    )


_repo_root = _find_repo_root(Path(__file__).resolve().parent)
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(_repo_root / ".env")

# Must stay BELOW the sys.path bootstrap above. As a top-level import it ran
# before the marker-walk had put the repo root on sys.path, so
# `python tools/kanban/cli.py` — the invocation CLAUDE.md documents and worker
# sessions use to report their own completion — died at import with
# "ModuleNotFoundError: No module named 'tools'" whenever PYTHONPATH was unset.
# Running the script by path puts tools/kanban/ on sys.path[0], never the root.
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.kanban.cli")

# Import the repo-local shim (``tools.db.storage``) — NEVER ``icdev.tools.*``,
# which a globally-installed editable ``icdev`` package from a foreign repo can
# capture, silently pointing the CLI at another repo's database.
from tools.db import storage  # noqa: E402
from tools.db.storage import get_connection  # noqa: E402
from tools.kanban import deps as kanban_deps  # noqa: E402

# Fail-loud shadow guard: refuse to run against a foreign storage module.
_shadow_err = _storage_shadow_error(storage.__file__, _repo_root)
if _shadow_err:
    print(_shadow_err, file=sys.stderr)
    sys.exit(1)

VALID_STATUSES = frozenset({
    "backlog", "scheduled", "in_progress", "done",
    "failed", "suggested", "needs_decomposition",
})

# Moving a task into one of these is a deliberate revival: whatever failure text
# the row is carrying describes a run that is no longer the current attempt.
# Leaving it behind keeps the task showing as broken in the dashboard's
# Autonomous Recovery panel, which filters on `last_failure_reason IS NOT NULL`.
# The scheduler already does this on re-dispatch (reflexes/kanban.py, the
# `elif new_status == "in_progress"` branch); the CLI did not, so a task revived
# from the CLI stayed "failed"-looking until it happened to be dispatched.
# `failure_count` and `last_failure_at` are deliberately preserved — they are
# real history and the circuit breaker reads the count.
_REVIVAL_STATUSES = frozenset({"backlog", "scheduled", "in_progress"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_manual_transition(conn, task_id: str, from_status, to_status: str,
                              reason: str = "") -> None:
    """Append a 'manual' row to kanban_status_transitions (best-effort).

    The scheduler's stale-reaper (tools/genesis/reflexes/kanban.py::
    _reap_stale_in_progress) fast-reaps any in_progress task after just
    KANBAN_SILENT_DISPATCH_THRESHOLD_SECONDS (default 60s) if its
    .tmp/kanban/<id>.log is empty and it isn't tracked in the scheduler's
    own _running dict — a signal meant to catch a scheduler-dispatched
    subprocess that died before writing any output. This CLI never spawns
    a subprocess or writes that log file, so without this record the
    reaper cannot distinguish "scheduler dispatch died silently" from "a
    human/external session is legitimately working this task out-of-band"
    and reaps it back to backlog within a minute regardless of how long
    the real work actually takes. Recording actor='manual' here lets the
    reaper apply its normal (much longer) timeout instead. Never raises —
    audit-log failure (e.g. migration 025 not yet run) must not block the
    primary status update.
    """
    try:
        from tools.kanban.transition_reason import resolve_transition_reason
        conn.execute(
            "INSERT INTO kanban_status_transitions "
            "(id, task_id, from_status, to_status, actor, reason, recorded_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                "kst-" + secrets.token_hex(6),
                task_id, from_status, to_status, "manual",
                resolve_transition_reason(
                    reason or "tools/kanban/cli.py --set-status",
                    from_status=from_status, to_status=to_status, actor="manual",
                ),
                _now(),
            ),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "_record_manual_transition: best-effort INSERT into kanban_status_transitions failed (non-blocking): %s",
            exc,
        )


def _refuses_done(task_id: str) -> str:
    """Reason this task may not be marked done, or '' if it may.

    The runner (``reflexes/kanban.py::_move_task``) and the dashboard move API
    both verify that a task's work is actually on ``origin/<default>`` before
    allowing 'done'. This CLI did not, and it is the command dispatched worker
    sessions are told to use to complete their own tasks (CLAUDE.md) — so a
    worker that opened a PR and self-reported reached 'done' with the work
    still unmerged. That is the "board says done but it is not on main" bug.

    Same primitive, same FAIL-OPEN contract: only a positive "there is work for
    this task that is not on origin" signal refuses. Unreachable git, an absent
    branch, or an import error must never wedge completions.
    """
    if os.environ.get("KANBAN_REQUIRE_MERGE_FOR_DONE", "1").strip().lower() in ("0", "false", "no"):
        return ""
    try:
        from tools.genesis.reflexes.kanban import _branch_has_unmerged_commits
    except Exception:
        return ""
    try:
        if _branch_has_unmerged_commits(task_id):
            return (
                f"{task_id}: work is not on origin yet — a branch for this task has "
                f"commits that have not merged. Merge the PR first, or pass "
                f"--force-done --reason '<why>' to override (audit-logged)."
            )
    except Exception:
        return ""
    return ""


def cmd_set_status(
    task_ids: list,
    status: str,
    json_out: bool,
    force_done: bool = False,
    reason: str = "",
    merge: bool = False,
    dry_run: bool = False,
    lander=None,
) -> int:
    if status not in VALID_STATUSES:
        print(
            f"ERROR: invalid status '{status}'. Valid: {', '.join(sorted(VALID_STATUSES))}",
            file=sys.stderr,
        )
        return 1

    # --merge: SATISFY the done-gate instead of bypassing it. Lands the task's
    # PR (all gates in tools/kanban/land.py) and only then writes 'done'.
    merge_verdict = None
    if merge:
        if status != "done":
            print("ERROR: --merge only applies to --set-status <id> done",
                  file=sys.stderr)
            return 1
        if force_done:
            # A merge that needs forcing is not a merge. Allowing both would
            # turn --merge into the softer bypass it exists to replace.
            print("ERROR: --merge and --force-done are mutually exclusive",
                  file=sys.stderr)
            return 1
        if len(task_ids) != 1:
            # Merging is an irreversible per-task side effect, so there is no
            # honest all-or-nothing batch: a failure after the first merge would
            # leave landed work with no 'done' row.
            print("ERROR: --merge lands exactly one task's PR — pass a single "
                  "task id", file=sys.stderr)
            return 1
        if lander is None:
            from tools.kanban.land import land as lander  # noqa: PLC0415
        merge_verdict = lander(task_ids[0], dry_run=dry_run)
        if dry_run:
            # Nothing was merged, so nothing may be marked done. Exit code
            # reports the preflight verdict so it is scriptable.
            if json_out:
                print(json.dumps({"merge": merge_verdict}, indent=2, default=str))
            else:
                state = "WOULD LAND" if merge_verdict.get("ok") else "REFUSED"
                print(f"  {state}: {task_ids[0]}: "
                      f"{_ascii(merge_verdict.get('reason'))}")
                for c in merge_verdict.get("checks", []):
                    print(f"    [{'ok' if c.get('ok') else 'XX'}] {c.get('name')} "
                          f"{_ascii(c.get('detail'))}".rstrip())
            return 0 if merge_verdict.get("ok") else 1
        if not merge_verdict.get("merged"):
            if json_out:
                print(json.dumps({"error": "refused_merge",
                                  "merge": merge_verdict}, indent=2, default=str))
            else:
                print(f"  REFUSED: {task_ids[0]}: "
                      f"{_ascii(merge_verdict.get('reason'))}", file=sys.stderr)
                for c in merge_verdict.get("checks", []):
                    mark = "ok" if c.get("ok") else "XX"
                    print(f"    [{mark}] {c.get('name')} "
                          f"{_ascii(c.get('detail'))}".rstrip(), file=sys.stderr)
            return 1

    # Merge-verify before writing anything: refuse the whole batch rather than
    # marking some tasks done and rejecting others halfway through. Skipped
    # after a --merge: a PR observed MERGED on GitHub is strictly stronger
    # evidence than the local `git cherry` heuristic, which would false-refuse
    # whenever this checkout's origin/<default> has not been fetched since.
    if status == "done" and not force_done and not merge:
        refusals = [r for r in (_refuses_done(t) for t in task_ids) if r]
        if refusals:
            if json_out:
                print(json.dumps(
                    {"error": "refused_done_unmerged", "refusals": refusals}, indent=2))
            else:
                for r in refusals:
                    print(f"  REFUSED: {r}", file=sys.stderr)
            return 1
    if status == "done" and force_done and not reason.strip():
        print("ERROR: --force-done requires --reason '<why>'", file=sys.stderr)
        return 1

    now = _now()
    results = []
    with get_connection() as conn:
        for tid in task_ids:
            prior_row = conn.execute(
                "SELECT status FROM kanban_tasks WHERE id = %s", (tid,)
            ).fetchone()
            prior_status = dict(prior_row)["status"] if prior_row else None

            if status == "done":
                conn.execute(
                    "UPDATE kanban_tasks SET status = %s, updated_at = %s, completed_at = %s WHERE id = %s",
                    (status, now, now, tid),
                )
            else:
                sql = "UPDATE kanban_tasks SET status = %s, updated_at = %s"
                vals = [status, now]
                if status in _REVIVAL_STATUSES:
                    sql += ", last_failure_reason = NULL"
                if status == "scheduled":
                    # _get_due_tasks requires `scheduled_at IS NOT NULL AND
                    # scheduled_at <= now()`. Moving a task to 'scheduled'
                    # without stamping it leaves the row invisible to the
                    # dispatcher — the same silent-failure gap documented in
                    # reflexes/kanban.py's move-to-scheduled branch.
                    sql += ", scheduled_at = COALESCE(scheduled_at, %s)"
                    vals.append(now)
                sql += " WHERE id = %s"
                vals.append(tid)
                conn.execute(sql, tuple(vals))
            if prior_row:
                if status == "done" and force_done:
                    _transition_reason = (
                        f"FORCED done (merge check overridden): {reason}")
                elif status == "done" and merge_verdict is not None:
                    # Same audit path as --force-done, opposite meaning: the
                    # gate was satisfied, not overridden.
                    _transition_reason = (
                        f"MERGED via CLI --merge: {merge_verdict.get('pr_url')} "
                        f"({merge_verdict.get('reason')})")
                else:
                    _transition_reason = ""
                _record_manual_transition(
                    conn, tid, prior_status, status, reason=_transition_reason,
                )
            row = conn.execute(
                "SELECT id, title, status FROM kanban_tasks WHERE id = %s", (tid,)
            ).fetchone()
            if row:
                results.append(dict(row))
            else:
                results.append({"id": tid, "error": "not found"})

    if json_out:
        payload = ({"merge": merge_verdict, "tasks": results}
                   if merge_verdict is not None else results)
        print(json.dumps(payload, indent=2, default=str))
    else:
        if merge_verdict is not None:
            print(f"  MERGED: {_ascii(merge_verdict.get('pr_url'))}")
        for r in results:
            if "error" in r:
                print(f"  NOT FOUND: {r['id']}")
            else:
                print(f"  {r['id']}: {r['status']}  {r.get('title', '')[:70]}")
    return 0


def cmd_requeue(task_ids: list, status: str, json_out: bool,
                reason: str = "", force: bool = False) -> int:
    """Re-queue tasks for a clean rebuild — the supported alternative to a
    hand-written UPDATE.

    ``--set-status <id> backlog`` is NOT the same thing: it leaves
    ``branch_name`` pointing at the branch whose PR was just closed, and it
    cannot touch a task parked in a pipeline-owned state like ``pr_opened``
    (see VALID_STATUSES above). ``tools/kanban/requeue.py`` owns the field set;
    this is only the surface. Exit 1 if any task was refused, so a batch
    re-queue cannot silently half-apply.
    """
    from tools.kanban.requeue import requeue_task

    results = [
        requeue_task(tid, status=status, reason=reason, actor="cli", force=force)
        for tid in task_ids
    ]

    if json_out:
        print(json.dumps(results, indent=2, default=str))
    else:
        for r in results:
            if not r["requeued"]:
                print(f"  REFUSED: {r['task_id']}: {r['error']}", file=sys.stderr)
                continue
            print(
                f"  {r['task_id']}: {r['from_status']} -> {r['to_status']}  "
                f"(cleared {', '.join(r['cleared']) or 'nothing'}; "
                f"failure_count preserved at {r['failure_count']})"
            )
            if not r["transition_recorded"]:
                print("    WARNING: no kanban_status_transitions row was written",
                      file=sys.stderr)
    return 0 if all(r["requeued"] for r in results) else 1


def cmd_show(task_id: str, json_out: bool) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id, title, status, priority, task_type, "
            "       created_at, updated_at, completed_at, "
            "       failure_count, last_failure_reason "
            "FROM kanban_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()

    if not row:
        print(f"NOT FOUND: {task_id}", file=sys.stderr)
        return 1

    d = dict(row)
    if json_out:
        print(json.dumps(d, indent=2, default=str))
    else:
        print(f"  id:       {d['id']}")
        print(f"  title:    {d['title']}")
        print(f"  status:   {d['status']}")
        print(f"  priority: {d.get('priority', '-')}")
        print(f"  type:     {d.get('task_type', '-')}")
        print(f"  updated:  {d.get('updated_at', '-')}")
        if d.get("last_failure_reason"):
            print(f"  failure:  {d['last_failure_reason'][:120]}")
    return 0


def _ascii(s: object) -> str:
    """ASCII-fold a string so CLI output is safe on Windows cp1252 consoles
    (pipeline labels use '->' arrows, em-dashes, etc.)."""
    if s is None:
        return ""
    out = (str(s).replace("→", "->").replace("—", "-")
           .replace("–", "-").replace("…", "...")
           .replace("✓", "ok").replace("✗", "x"))
    return out.encode("ascii", "replace").decode("ascii")


def cmd_reverify(task_id: str, json_out: bool, dry_run: bool = False) -> int:
    """Recompute a task's verification from git and append a fresh verdict.

    This is the manual escape hatch from the enforced done-gate. `pr_watcher`
    reads only the LATEST `kanban_verifications` row and nothing writes one
    except a dispatch, so a task that verified badly once stays blocked from
    auto-merge until it is re-dispatched — which opens a second PR rather than
    reusing the first. Re-verifying clears the block without that.

    It does NOT weaken the gate: the verdict is recomputed from the branch's
    real state, so a task with no work still fails.

    Exit 0 = passed, 1 = failed, 2 = no such task.
    """
    from tools.db.storage import get_connection
    from tools.kanban.reverify import reverify

    try:
        verdict = reverify(task_id, get_connection, dry_run=dry_run)
    except LookupError as exc:
        # Explicit non-zero, never a silent success: this CLI is what worker
        # sessions use to report their own state, and a typo'd id that exits 0
        # reads as "cleared" when nothing happened.
        if json_out:
            print(json.dumps({"error": "not_found", "task_id": task_id}, indent=2))
        else:
            print(f"NOT_FOUND: {exc}", file=sys.stderr)
        return 2

    if json_out:
        print(json.dumps(verdict, indent=2, default=str, sort_keys=True))
    else:
        state = "would write" if dry_run else (
            "wrote" if verdict.get("written") else "did not write")
        print(f"{task_id}: {verdict['result']} ({state})")
        print(f"  branch: {_ascii(verdict.get('branch'))}")
        print(f"  {_ascii(verdict.get('reason'))}")
    return 0 if verdict["result"] == "passed" else 1


def cmd_pipeline(task_id: str, json_out: bool) -> int:
    """Print a task's delivery-pipeline (gate) status — the CLI mirror of the
    dashboard pipeline stepper. Pure view; provider/LLM-agnostic (no LLM call)."""
    from tools.kanban.pipeline import assemble
    r = assemble(task_id)
    if json_out:
        print(json.dumps(r, indent=2, default=str))
        return 1 if "error" in r else 0
    if "error" in r:
        print(f"{str(r['error']).upper()}: {task_id}", file=sys.stderr)
        return 1
    badge = {"completed": "[x]", "current": "[>]", "failed": "[X]",
             "pending": "[ ]", "not_run": "[-]"}
    mode = "ENFORCED" if r.get("enforce_mode") == "enforced" else "RECORD-ONLY"
    print(f"Pipeline: {task_id}  (current stage: {r.get('current_stage', '-')})")
    print(f"  mode: {mode}")
    for s in r.get("stages", []):
        mark = badge.get(s.get("state"), "[?]")
        det = f"  -- {_ascii(s['detail'])}" if s.get("detail") else ""
        print(f"  {mark} {_ascii(s.get('label', s.get('key')))}{det}")
    meta = r.get("meta", {})
    if meta.get("branch_name"):
        print(f"  branch: {_ascii(meta['branch_name'])}")
    if meta.get("commit_subject"):
        print(f"  commit: {_ascii(meta['commit_subject'])}")
    return 0


def cmd_needs_human(json_out: bool) -> int:
    """List the tasks the pipeline has given up on — the legitimate HITL queue.

    The pipeline is designed to run unattended, so the honest exception is a task
    whose automatic recovery is spent (pr_watcher: up to 2 rebases and 5 resume
    cycles, separate ledgers). Until it raised an alert, that state was reachable
    only by running the watcher by hand — the board reported an unrelated reason
    and the task waited for someone to notice. Three sat that way at once on
    2026-08-09.

    Reads the same `alerts` rows the dashboard lists, so the terminal and the web
    view cannot disagree about what needs a human.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT source, title, description, created_at FROM alerts "
            "WHERE status = 'firing' AND source LIKE %s "
            "ORDER BY created_at ASC",
            ("pr_watcher:hitl:%",),
        ).fetchall()

    items = [dict(r) for r in rows]
    if json_out:
        print(json.dumps(items, indent=2, default=str))
        return 0
    if not items:
        print("  nothing needs a human — the pipeline is unblocked")
        return 0
    print(f"  {len(items)} task(s) need a human:")
    for r in items:
        task = (r["source"] or "").rsplit(":", 1)[-1]
        print(f"    {task:20} {r['description']}")
    # Exit 1 so a cron/CI caller can act on it without parsing stdout.
    return 1


def cmd_awaiting_merge(json_out: bool, states: list | None = None,
                       measure_behind: bool = True) -> int:
    """Which open PRs are awaiting merge, and why is each one not merging?

    kpr-watch-03. ``--needs-human`` above answers "what has the pipeline GIVEN
    UP on"; this answers the question in between, which nothing on the board
    could answer from a terminal: a PR sitting in ``awaiting_ci`` for nine
    hours, or merging cleanly while 200 commits behind main, is not an alert
    and is not done -- it was invisible in both directions.

    READ ONLY, deliberately and completely: it never merges, pushes, un-drafts
    or closes. The point is to SEE what the automation is doing, not to add a
    second way to act on it -- the CLI already has ``--set-status <id> done
    --merge`` for that, and it is gated.

    Reads ``tools.ci.merge_readiness.collect_report``, the same gatherer
    ``python -m tools.ci.merge_readiness`` and the dashboard panel read, so the
    terminal and the web view cannot disagree about why a PR is stuck -- the
    same discipline ``--needs-human`` follows for the ``alerts`` table.

    Exit 0 = report produced (even if empty), 2 = it COULD NOT BE produced.
    A report that listed nothing must never read like a repo with nothing open.
    """
    from tools.ci import merge_readiness as mr

    try:
        report = mr.collect_report(measure_behind=measure_behind)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        msg = f"cannot produce the merge-readiness report: {exc}"
        if json_out:
            print(json.dumps({"ok": False, "error": msg}, indent=2))
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
        return 2

    if states:
        wanted = set(states)
        report["prs"] = [r for r in report["prs"]
                         if r.get("pipeline_state") in wanted
                         or r.get("state") in wanted]
        report["filtered_to"] = sorted(wanted)

    if json_out:
        report["ok"] = True
        report["groups"] = mr.group_by_state(report)
        print(json.dumps(report, indent=2, default=str))
        return 0
    print(_ascii(mr.render_grouped(report)))
    return 0


def cmd_list(prefix: str | None, status: str | None, json_out: bool) -> int:
    conditions = []
    params = []

    if prefix:
        conditions.append("t.id LIKE %s")
        params.append(prefix + "%")
    if status:
        if status not in VALID_STATUSES:
            print(f"ERROR: invalid status '{status}'.", file=sys.stderr)
            return 1
        conditions.append("t.status = %s")
        params.append(status)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with get_connection() as conn:
        # depends_on_task_id is selected because it is the field that decides
        # whether a task can run at all: promote_backlog_to_scheduled refuses to
        # promote a task whose dependency is not done/decomposed, so a held
        # dependency is the single most common reason the board looks stuck.
        #
        # Omitting it did not make this output terse, it made it MISLEADING. A
        # reader who checks ``task.get("depends_on_task_id")`` gets None for every
        # row and concludes the backlog is ready to run. That happened on
        # 2026-08-09: 37 backlog tasks read as ungated when every one was held
        # behind a manual gate, and the board was reported as broken while it was
        # obeying its own rules exactly.
        #
        # The blocker's STATUS is joined for the same reason — the id alone does
        # not say whether it is satisfied, and needing a second query per task to
        # answer that is what made the wrong answer easy to reach.
        rows = conn.execute(
            "SELECT t.id, t.title, t.status, t.priority, t.depends_on_task_id, "
            "       d.status AS depends_on_status "
            "FROM kanban_tasks t "
            "LEFT JOIN kanban_tasks d ON d.id = t.depends_on_task_id "  # nosec B608
            f"{where} ORDER BY t.id",
            params,
        ).fetchall()

        results = [dict(r) for r in rows]
        # WHICH dependency actually holds is tools.kanban.deps' answer, not the
        # scalar column's. Printing "blocked by <seeding predecessor>" for a task
        # the dispatcher will happily run is the same misleading-output defect
        # the comment above describes, one mechanism further in (kpr-fix-02).
        blocking_map = kanban_deps.blocking_dep_status_bulk(
            [r["id"] for r in results], conn
        )
    for r in results:
        r["blocking_deps"] = [
            f"{dep} ({status or 'MISSING'})"
            for dep, status in blocking_map.get(r["id"], [])
        ]

    if json_out:
        print(json.dumps(results, indent=2))
    else:
        if not results:
            print("  (no tasks found)")
        for r in results:
            held = r["blocking_deps"]
            blocked = (
                f"  <- blocked by {', '.join(held[:3])}" if held else ""
            )
            print(f"  [{r['status']:15s}] {r['id']:25s} {r.get('title','')[:60]}{blocked}")
    return 0


_RUNNER_PAUSE_RESOURCE = "kanban:runner:global"


def _task_lease_resource(task_id: str) -> str:
    return f"kanban:task:{task_id}"


def cmd_claim(task_id: str, json_out: bool) -> int:
    """Take exclusive ownership of a task for interactive (CLI) work.

    Acquires the per-task coordination lease so the autonomous kanban runner
    skips it, moves it to in_progress, and prints the task's stored branch +
    commit so you continue that branch rather than rebuilding it.
    """
    from tools.coordination import leases
    res = _task_lease_resource(task_id)
    lease = leases.acquire(res, intent="cli-manual", ttl_seconds=3600, block=False)
    if lease is None:
        h = leases.holder(res) or {}
        out = {"claimed": False, "task_id": task_id, "held_by": h.get("holder_session")}
        if json_out:
            print(json.dumps(out, indent=2))
        else:
            print(f"  CANNOT CLAIM {task_id}: held by session {h.get('holder_session', '?')}")
        return 1
    now = _now()
    info = {"claimed": True, "task_id": task_id}
    with get_connection() as conn:
        row = conn.execute(
            "SELECT status, branch_name, commit_summary FROM kanban_tasks WHERE id = %s",
            (task_id,),
        ).fetchone()
        if not row:
            print(f"  NOT FOUND: {task_id} (lease acquired; no such task row)", file=sys.stderr)
            return 1
        d = dict(row)
        prior = d.get("status")
        conn.execute(
            "UPDATE kanban_tasks SET status = %s, updated_at = %s WHERE id = %s",
            ("in_progress", now, task_id),
        )
        _record_manual_transition(conn, task_id, prior, "in_progress")
        info.update({
            "prior_status": prior,
            "branch": d.get("branch_name"),
            "commit": d.get("commit_summary"),
        })
    if json_out:
        print(json.dumps(info, indent=2, default=str))
    else:
        print(f"  CLAIMED {task_id} (was {info.get('prior_status')}) -> in_progress")
        print(f"    branch: {info.get('branch') or '(none recorded — start fresh off origin/main)'}")
        if info.get("commit"):
            print(f"    last commit: {str(info['commit'])[:100]}")
        print("    runner will skip this task until you --release it.")
    return 0


def cmd_release(task_id: str, json_out: bool) -> int:
    """Release the per-task lease so the runner (or another session) can take it.

    Falls back to :func:`leases.release_stale` when this session did not take the
    claim — which is almost always, for exactly the reason ``--resume-runner``
    documents a few lines below. ``leases.release`` matches on ``holder_session``,
    and ``get_session_id`` derives a fresh ``local-<uuid>`` per PROCESS unless
    CLAUDE_SESSION_ID is exported. So the claim and the release are two different
    processes with two different identities and the match can never succeed:

      * ``cli.py --claim <id>`` acquires and exits;
      * ``create_tasks(specs, claim=True)`` acquires inside whatever short-lived
        process ran the seeder.

    Either way ``--release`` reported "NOT HELD BY THIS SESSION" and the only way
    out was the TTL. Observed 2026-08-18 on kpr-fix-03: the work had merged, the
    row was ``done``, and the claim was still held by pid 28076, long exited —
    withholding a finished task from the runner for the rest of its 4-hour lease.
    That is the same defect ``--pause-runner`` had, found and fixed there and left
    unfixed here, and CLAUDE.md meanwhile documents ``--release`` as the way to
    hand a task back.

    A claim whose holder is still RUNNING is left alone. That one is real: another
    live session is building the task, and stealing its claim is precisely the
    duplicate-build race the claim exists to prevent.

    AND A DEAD PID IS NOT DEAD WORK (autonomy-adm-03). The pid on a runner's
    lease is the dispatcher's, which exits after handing off while the worker
    heartbeats on under another pid. ``release_stale`` reads only the pid, so
    this fallback used to reclaim a live worker's lease on request and hand the
    runner a second copy of the task. The reclaim now goes through
    :func:`tools.kanban.lease_liveness.reap_if_litter`, the SAME two-signal
    verdict the dispatch window uses: a task that is still heartbeating is
    refused with the reason, and the heartbeat ages out on its own
    (``HEARTBEAT_LIVE_MINUTES``) once the worker really is gone. An unreadable
    heartbeat reads as alive — cannot-tell must never license a reap.
    """
    from tools.coordination import leases
    from tools.kanban import lease_liveness

    resource = _task_lease_resource(task_id)
    prior = leases.holder(resource)
    released = leases.release(resource)
    reclaimed = False
    verdict = None
    if not released and prior is not None:
        verdict, reclaimed = lease_liveness.reap_if_litter(task_id)
        released = reclaimed

    still = leases.holder(resource)
    worker_alive = verdict is not None and verdict.state == lease_liveness.STATE_WORKING
    if json_out:
        print(json.dumps({
            "released": released,
            "reclaimed_from_exited_session": reclaimed,
            "task_id": task_id,
            "prior_holder": (prior or {}).get("holder_session"),
            "still_held_by": (still or {}).get("holder_session"),
            "lease_state": verdict.state if verdict is not None else None,
            "worker_heartbeating": worker_alive,
        }, indent=2))
    elif released and reclaimed:
        print(f"  RELEASED: {task_id} (reclaimed from exited session "
              f"{(prior or {}).get('holder_session')}, pid {(prior or {}).get('pid')})")
    elif released:
        print(f"  RELEASED: {task_id}")
    elif still and worker_alive:
        # The holder's pid is gone but the task is heartbeating: a worker is
        # building it right now under a different pid. Reclaiming here is the
        # duplicate-build race, with a human's hand on the lever.
        print(f"  STILL CLAIMED — the worker is HEARTBEATING: {task_id}")
        print(f"    {lease_liveness.describe(verdict)}")
        print("    release is refused while the task heartbeats; it ages out after "
              f"{lease_liveness.HEARTBEAT_LIVE_MINUTES} min once the worker stops.")
    elif still:
        # Not a failure to report as "not held by you" — the holder is ALIVE,
        # and that is the case the claim exists for.
        print(f"  STILL CLAIMED by a LIVE session "
              f"{still.get('holder_session')} (pid {still.get('pid')}): {task_id}")
        if verdict is not None:
            print(f"    {lease_liveness.describe(verdict)}")
    else:
        print(f"  NOT CLAIMED: {task_id}")
    return 0 if released else 1


def cmd_pause_runner(json_out: bool) -> int:
    """Pause the autonomous kanban runner for the current session (interactive work)."""
    from tools.coordination import leases
    lease = leases.acquire(_RUNNER_PAUSE_RESOURCE, intent="cli-session", ttl_seconds=14400, block=False)
    if lease is None:
        h = leases.holder(_RUNNER_PAUSE_RESOURCE) or {}
        if json_out:
            print(json.dumps({"paused": False, "held_by": h.get("holder_session")}, indent=2))
        else:
            print(f"  ALREADY PAUSED by session {h.get('holder_session', '?')}")
        return 1
    if json_out:
        print(json.dumps({"paused": True}, indent=2))
    else:
        print("  RUNNER PAUSED — the autonomous kanban runner will skip dispatch until --resume-runner.")
    return 0


def cmd_resume_runner(json_out: bool) -> int:
    """Resume the autonomous kanban runner (release the global pause lease).

    Falls back to :func:`leases.release_stale` when this session did not take the
    pause — which is almost always. ``--pause-runner`` exits immediately after
    acquiring, and each CLI invocation derives a fresh ``local-<uuid>`` session id
    unless CLAUDE_SESSION_ID is exported, so ``--resume-runner`` could never match
    its own ``--pause-runner``. Observed 2026-08-07: the board sat paused with its
    holder (pid 16700) long dead, and the only ways out were a 4-hour TTL or
    impersonating the dead session via ICDEV_SESSION_ID.

    A pause whose holder is still RUNNING is left alone — that one is deliberate
    and someone is relying on it.
    """
    from tools.coordination import leases

    prior = leases.holder(_RUNNER_PAUSE_RESOURCE)
    released = leases.release(_RUNNER_PAUSE_RESOURCE)
    reclaimed = False
    if not released:
        reclaimed = leases.release_stale(_RUNNER_PAUSE_RESOURCE)
        released = reclaimed

    still = leases.holder(_RUNNER_PAUSE_RESOURCE)
    if json_out:
        print(json.dumps({
            "resumed": released,
            "reclaimed_from_exited_session": reclaimed,
            "prior_holder": (prior or {}).get("holder_session"),
            "still_held_by": (still or {}).get("holder_session"),
        }, indent=2))
    elif released and reclaimed:
        print(f"  RUNNER RESUMED (reclaimed from exited session "
              f"{(prior or {}).get('holder_session')}, pid {(prior or {}).get('pid')})")
    elif released:
        print("  RUNNER RESUMED")
    elif still:
        print(f"  NOT RESUMED — paused by session {still.get('holder_session')} "
              f"(pid {still.get('pid')}), whose process is still running.")
    else:
        print("  NOT PAUSED — nothing to resume.")
    return 0 if released else 1


def cmd_build_mode(mode: str, json_out: bool) -> int:
    """Manual Build — promote and track as normal, but do not auto-dispatch."""
    from tools.kanban.build_mode import set_manual, status as bm_status

    if mode == "status":
        st = bm_status()
    else:
        st = set_manual(mode == "manual", actor="cli")

    if json_out:
        print(json.dumps(st, indent=2))
    elif st.get("manual"):
        print("  MANUAL BUILD — tasks still promote to scheduled and the board still "
              "tracks them; the runner will NOT dispatch. You build them.")
        print("  Pick up work with:  python -m tools.kanban.cli --list --status scheduled")
    else:
        print("  AUTOMATIC BUILD (default) — the runner dispatches tasks itself.")
    return 0


def cmd_build_model(model: str, json_out: bool) -> int:
    """Select the model the runner builds with."""
    from tools.kanban.model_override import available, set_model, status as m_status

    if model == "list":
        models = available()
        if json_out:
            print(json.dumps(models, indent=2))
        else:
            current = (m_status().get("model")) or "(default)"
            print(f"  current: {current}")
            for m in models:
                served = "claude-cli" if m["cli_capable"] else "llm-executor"
                print(f'  {m["name"]:<24} {m["provider"]:<14} -> {served}')
        return 0

    try:
        st = set_model(None if model == "default" else model, actor="cli")
    except ValueError as exc:
        print(f"  ERROR: {exc}")
        return 1

    if json_out:
        print(json.dumps(st, indent=2))
    elif not st.get("model"):
        print("  Model: default (config-driven routing)")
    else:
        r = st.get("resolved") or {}
        if r.get("cli_capable"):
            print(f'  Model: {st["model"]} — dispatched via the Claude CLI (--model {r.get("model_id")})')
        else:
            print(f'  Model: {st["model"]} ({r.get("provider")}) — the Claude CLI cannot '
                  f'serve it, so claude_cli is dropped from the executor chain and the '
                  f'LLM executor builds with it.')
    return 0


def main():
    # xit-decl-01: the board CLI moves rows; refuse when the process env names a
    # database this parent did not declare (two parents share one shell).
    from icdev.core.context import assert_identity

    assert_identity(anchor=__file__)

    from tools.kanban.requeue import REQUEUE_STATUSES

    parser = argparse.ArgumentParser(
        description="Kanban task manager — always routes through get_connection() (PG in prod).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--json", dest="json_out", action="store_true", help="JSON output")

    parser.add_subparsers(dest="cmd")

    # --set-status <id ...> <status>  (positional shorthand — args after flag)
    parser.add_argument(
        "--set-status",
        nargs="+",
        metavar=("TASK_ID", "STATUS"),
        help="Mark one or more tasks with a status. Last argument is the status.",
    )

    # --show <id>
    parser.add_argument("--force-done", dest="force_done", action="store_true",
                        help="Override the merge check on --set-status done "
                             "(requires --reason; audit-logged)")
    parser.add_argument("--reason", metavar="TEXT",
                        help="Justification recorded with --force-done or --requeue")
    parser.add_argument("--merge", action="store_true",
                        help="With --set-status <id> done: SATISFY the merge "
                             "gate instead of bypassing it — land the task's PR "
                             "(requires an OPEN PR based on the default branch, "
                             "green CI, no requested changes, and the enforced "
                             "done-gate) and mark done only once GitHub reports "
                             "it MERGED. One task id; not combinable with "
                             "--force-done. Add --dry-run to preflight only.")
    parser.add_argument("--show", metavar="TASK_ID", help="Show details of one task")

    # --requeue <id ...> — send tasks back for a clean rebuild
    parser.add_argument("--requeue", nargs="+", metavar="TASK_ID",
                        help="Re-queue tasks for a clean rebuild: clears "
                             "last_failure_reason and branch_name, preserves "
                             "failure_count, records the transition. Use this "
                             "instead of --set-status backlog, whose "
                             "fresh-updated_at + stale-failure_reason pairing "
                             "failure_triage reads as a brand-new failure.")
    parser.add_argument("--requeue-status", metavar="STATUS", default="backlog",
                        choices=sorted(REQUEUE_STATUSES),
                        help="Target status for --requeue (default: backlog)")
    parser.add_argument("--force", action="store_true",
                        help="With --requeue: re-queue a manual-mode gate "
                             "sentinel anyway (releases every task behind it)")

    # --pipeline <id> — delivery-pipeline (gate) status; CLI mirror of the dashboard stepper
    parser.add_argument("--pipeline", metavar="TASK_ID",
                        help="Show a task's delivery-pipeline (gate) status (CLI view)")

    # --reverify <id> — recompute the done-gate verdict from git state
    parser.add_argument("--reverify", metavar="TASK_ID",
                        help="Recompute a task's verification from its branch and "
                             "append a fresh verdict, clearing a stale done-gate block "
                             "without re-dispatching (exit 0=passed, 1=failed, 2=unknown)")
    parser.add_argument("--needs-human", dest="needs_human", action="store_true",
                        help="List tasks whose automatic recovery is exhausted and "
                             "which will not move without a person (exit 1 if any)")
    parser.add_argument("--awaiting-merge", dest="awaiting_merge",
                        action="store_true",
                        help="Which open PRs are awaiting merge and why each one "
                             "is not merging, grouped by state (READ ONLY -- it "
                             "never merges, pushes or un-drafts). Exit 2 if the "
                             "report could not be produced.")
    parser.add_argument("--merge-state", dest="merge_state", action="append",
                        metavar="STATE",
                        help="With --awaiting-merge: only show PRs in this state "
                             "(repeatable), e.g. --merge-state behind_main")
    parser.add_argument("--no-measure-behind", dest="no_measure_behind",
                        action="store_true",
                        help="With --awaiting-merge: skip the forge /compare call; "
                             "staleness then reports UNMEASURED, never fresh")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="With --reverify: compute the verdict without writing it. "
                             "With --merge: run the landing preflight only "
                             "(nothing is merged, nothing is marked done)")

    # --list [--prefix PREFIX] [--status STATUS]
    parser.add_argument("--list", action="store_true", help="List tasks")
    parser.add_argument("--prefix", metavar="PREFIX", help="Filter by id prefix (used with --list)")
    parser.add_argument("--status", metavar="STATUS", help="Filter by status (used with --list)")

    # Cross-session coordination (CLI <-> autonomous runner handoff)
    parser.add_argument("--claim", metavar="TASK_ID",
                        help="Take exclusive ownership of a task for interactive work "
                             "(runner will skip it); prints its branch/commit to continue")
    parser.add_argument("--release", metavar="TASK_ID",
                        help="Release a task lease so the runner can take it back")
    parser.add_argument("--pause-runner", action="store_true",
                        help="Pause the autonomous kanban runner for this session")
    parser.add_argument("--resume-runner", action="store_true",
                        help="Resume the autonomous kanban runner")
    parser.add_argument("--build-mode", choices=["manual", "auto", "status"],
                        help="Manual Build: promote+track as normal but do NOT "
                             "auto-dispatch (you build from the CLI). 'auto' restores "
                             "the default automatic build.")
    parser.add_argument("--build-model", metavar="MODEL",
                        help="Model the runner builds with (name from llm_config.yaml). "
                             "Pass 'default' to clear, 'list' to see the options.")

    args = parser.parse_args()

    if args.claim:
        sys.exit(cmd_claim(args.claim, args.json_out))

    elif args.release:
        sys.exit(cmd_release(args.release, args.json_out))

    elif args.build_mode:
        sys.exit(cmd_build_mode(args.build_mode, args.json_out))
    elif args.build_model:
        sys.exit(cmd_build_model(args.build_model, args.json_out))
    elif args.pause_runner:
        sys.exit(cmd_pause_runner(args.json_out))

    elif args.resume_runner:
        sys.exit(cmd_resume_runner(args.json_out))

    elif args.set_status:
        tokens = args.set_status
        if len(tokens) < 2:
            parser.error("--set-status requires at least one task ID and a status.")
        status = tokens[-1]
        task_ids = tokens[:-1]
        sys.exit(cmd_set_status(task_ids, status, args.json_out,
                            force_done=args.force_done, reason=args.reason or '',
                            merge=args.merge, dry_run=args.dry_run))

    elif args.requeue:
        sys.exit(cmd_requeue(args.requeue, args.requeue_status, args.json_out,
                             reason=args.reason or '', force=args.force))

    elif args.show:
        sys.exit(cmd_show(args.show, args.json_out))

    elif args.pipeline:
        sys.exit(cmd_pipeline(args.pipeline, args.json_out))

    elif args.needs_human:
        sys.exit(cmd_needs_human(args.json_out))
    elif args.awaiting_merge:
        sys.exit(cmd_awaiting_merge(
            args.json_out, states=args.merge_state,
            measure_behind=not args.no_measure_behind))
    elif args.reverify:
        sys.exit(cmd_reverify(args.reverify, args.json_out, dry_run=args.dry_run))

    elif args.list:
        sys.exit(cmd_list(args.prefix, args.status, args.json_out))

    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
