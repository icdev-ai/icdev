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

  # ...including a PR that touches a protected path: overrides that ONE rung,
  # runs all thirteen checks, audits the reason verbatim before merging
  python tools/kanban/cli.py --set-status mfx-mrg-04 done --merge       --protected-ok --reason 'this card changes _auto_merge itself; reviewed by <name>'
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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


NEW_UNIT_GATE_ENV = "KANBAN_NEW_UNIT_GATE"
#: `report`, matching the posture wire-req-01 shipped and for the same reason: nothing has ever
#: measured how often this fires, and CLAUDE.md treats a check refusing routine work as grounds
#: to stand it down. Arm it once a survey supports it -- never to get a card closed.
NEW_UNIT_GATE_DEFAULT = "report"


def _new_unit_gate_mode() -> str:
    """off | report | enforce. An unrecognised value falls back to the DEFAULT, never to
    `enforce` -- a typo in an env var must not silently arm a gate."""
    raw = os.environ.get(NEW_UNIT_GATE_ENV, "").strip().lower()
    return raw if raw in ("off", "report", "enforce") else NEW_UNIT_GATE_DEFAULT


def _task_diff_range(task_id: str):
    """(since, head) covering what THIS task added, or None if it cannot be determined.

    Not `origin/main...HEAD`. By the time a worker runs `--set-status <id> done` the work is
    usually already merged, so that range is empty and every task would report clean -- the
    shape of a V&V card dispatched after its subject landed, which can never go red.

    Two ways, in order of directness:
      1. the task's own branch, `kanban/<id>`, against its merge base with the default branch;
      2. failing that, the commits whose subject carries the id, from the parent of the earliest
         to the newest.
    Neither available -> None, and the caller reports UNMEASURABLE rather than clean.
    """
    def _git(*args):
        try:
            proc = subprocess.run(
                ["git", *args], cwd=str(_repo_root), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return proc.stdout.strip() if proc.returncode == 0 else None

    default = _git("symbolic-ref", "--short", "refs/remotes/origin/HEAD") or "origin/main"
    default = default.rsplit("/", 1)[-1] if default.startswith("origin/") else default
    base_ref = f"origin/{default}"

    for branch in (f"origin/kanban/{task_id}", f"kanban/{task_id}"):
        if _git("rev-parse", "--verify", "--quiet", branch):
            merge_base = _git("merge-base", base_ref, branch)
            if merge_base:
                return merge_base, branch

    shas = (_git("log", "--format=%H", f"--grep={task_id}", base_ref) or "").split()
    if shas:
        newest, earliest = shas[0], shas[-1]
        parent = _git("rev-parse", "--verify", "--quiet", f"{earliest}^")
        if parent:
            return parent, newest
    return None


def _unwired_units(task_id: str) -> str:
    """Reason this task declares a capability nothing has ever run, or '' .

    THE GAP. `check_capability_liveness` compares a whole-class count against a grandfathered
    budget, so a unit added by THIS card vanishes into a backlog of 510 units that are allowed
    to be inert -- and the author cannot tell their own omission from the backlog. Here the unit
    is NAMED and the remedy is to run it once.

    FAIL-OPEN, exactly like `_refuses_done`: an unreadable board, an undeterminable diff range
    or an absent module must never wedge a completion. Only a positive, named finding speaks.
    """
    mode = _new_unit_gate_mode()
    if mode == "off":
        return ""
    try:
        from tools.awareness.capability_consumption import new_units
    except Exception:  # noqa: BLE001 - fail open
        return ""

    def _note(message: str) -> str:
        """Say why the check did not run -- but only under `enforce`.

        "We could not tell" is not "there is nothing", so it must never be swallowed where
        somebody is RELYING on this rung to refuse. Under `report` the rung refuses nothing by
        construction, and a per-task note on every completion is noise that teaches people to
        ignore stderr -- which is how a real finding gets missed later. A FINDING is printed in
        both modes; only non-measurement is conditioned.
        """
        if mode == "enforce":
            print(f"NOTE: {task_id}: {message}", file=sys.stderr)
        return ""

    rng = _task_diff_range(task_id)
    if rng is None:
        return _note(
            "could not determine this task's diff range, so its new capability units were "
            "NOT checked (this is not a clean bill)."
        )

    since, head = rng
    try:
        result = new_units(since, head=head)
    except Exception as exc:  # noqa: BLE001 - fail open
        return _note(f"new-unit check could not run ({exc}) -- this is not a clean bill.")

    if result.get("state") != "measured":
        return _note(
            f"new-unit check UNMEASURABLE ({result.get('reason') or 'unknown'}) -- "
            "not a clean bill."
        )

    findings = result.get("findings") or []
    if not findings:
        return ""

    lines = [
        f"{task_id}: declares {len(findings)} capability unit(s) that have NEVER run:",
    ]
    for f in findings:
        lines.append(f"  - {f['capability_class']}: {f['unit']}")
        lines.append(f"      {f['remedy']}")
    lines.append(
        f"  Re-derive: python tools/awareness/capability_consumption.py --new-units "
        f"--since {since} --head {head}"
    )
    message = "\n".join(lines)

    if mode == "enforce":
        return message + (
            f"\n  Run it, or set {NEW_UNIT_GATE_ENV}=report if this is not the right check "
            f"for this task."
        )
    print(f"WARNING: {message}", file=sys.stderr)
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
    protected_ok: bool = False,
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
        if protected_ok and not reason.strip():
            # THE `--force-done --reason` PRECEDENT, and for the same reason: a
            # bypass whose justification is a default string records nothing. A
            # usage error, never a default.
            print("ERROR: --protected-ok requires --reason '<why>' — it "
                  "overrides pr_watcher's protected-path guard and the reason "
                  "is audited verbatim before the merge", file=sys.stderr)
            return 1
        if lander is None:
            from tools.kanban.land import land as lander  # noqa: PLC0415
        merge_verdict = lander(task_ids[0], dry_run=dry_run,
                               protected_ok=protected_ok,
                               override_reason=reason.strip())
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

    if protected_ok and not merge:
        print("ERROR: --protected-ok only applies to --set-status <id> done "
              "--merge — it overrides one rung INSIDE the merge door, and "
              "there is no other door it means anything at", file=sys.stderr)
        return 1

    # Merge-verify before writing anything: refuse the whole batch rather than
    # marking some tasks done and rejecting others halfway through. Skipped
    # after a --merge: a PR observed MERGED on GitHub is strictly stronger
    # evidence than the local `git cherry` heuristic, which would false-refuse
    # whenever this checkout's origin/<default> has not been fetched since.
    if status == "done" and not force_done and not merge:
        refusals = [r for r in (_refuses_done(t) for t in task_ids) if r]
        # A SECOND, INDEPENDENT question, deliberately its own rung: `_refuses_done` asks
        # whether the work LANDED, this asks whether what landed is WIRED. A task can pass the
        # first and fail the second -- that is precisely the "100% done, nothing consumes it"
        # defect. It ships `report`, so it appends nothing to `refusals` today.
        refusals += [r for r in (_unwired_units(t) for t in task_ids) if r]
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
                        f"MERGED via CLI --merge"
                        f"{' --protected-ok' if protected_ok else ''}: "
                        f"{merge_verdict.get('pr_url')} "
                        f"({merge_verdict.get('reason')})"
                        + (f" [protected-path override: {reason.strip()}]"
                           if protected_ok else ""))
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

    # The work landed: hand the task's coordination lease back. AFTER the
    # connection is closed, so a slow lease read cannot hold a PG transaction
    # open. Measured 2026-08-22 (claim-verif-a6a1517970): 17 of 20 litter
    # leases the claim verifier flagged were `create_tasks(claim=True)` seeds on
    # tasks this very command had already marked done — CLAUDE.md told the
    # session to take the claim and left `--release` to its memory. The runner's
    # `_move_task` releases on a terminal transition; this door did not.
    # SAME LADDER AS --release, never a second copy: ownership first, then the
    # two-signal verdict, so a live holder or a heartbeating worker is kept and
    # REPORTED. Best-effort — the `done` row is already written and a lease
    # failure must not un-write it — but never silent.
    if status == "done":
        for r in results:
            if "error" in r:
                continue
            try:
                r["lease"] = _release_task_lease(r["id"])
            except Exception as exc:  # noqa: BLE001
                r["lease"] = {"state": "error", "why": str(exc)}

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
                lease = r.get("lease")
                if lease and lease.get("state") != "none":
                    why = f" — {_ascii(lease['why'])}" if lease.get("why") else ""
                    print(f"    lease: {lease['state']}{why}")
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


def cmd_claim(task_id: str, json_out: bool, intent: Optional[str] = None,
              ttl_seconds: Optional[int] = None) -> int:
    """Take exclusive ownership of a task for interactive (CLI) work.

    Acquires the per-task coordination lease so the autonomous kanban runner
    skips it, moves it to in_progress, and prints the task's stored branch +
    commit so you continue that branch rather than rebuilding it.

    THE LEASE IS MADE TO HOLD (mfx-own-02). A shell has no registered session
    and its pid exits on the next line, so the lease this door took used to be
    litter to every reader within seconds -- measured 2026-09-03, an operator
    holding rmf-ui-13 by hand while a second session repaired the same branch.
    ``tools.kanban.interactive_claim.claim`` now takes the lease under this
    process's identity (the same refusal every claimant gets) and hands it to a
    detached KEEPER that registers a dedicated ``cli-claim-<id>-*`` session
    (``agent_type`` cli, the stated ``--intent``), re-takes the lease under its
    own live pid and heartbeats until the TTL (``--ttl``, default 2h; running
    ``--claim`` again renews it) or ``--release``. The inherited service
    identity is never reused (claim-verif-33c9f4cd11).
    """
    from tools.coordination import leases
    from tools.kanban import interactive_claim
    res = _task_lease_resource(task_id)
    outcome = interactive_claim.claim(task_id, intent=intent, ttl_seconds=ttl_seconds)
    if not outcome.get("claimed"):
        h = leases.holder(res) or {}
        out = {"claimed": False, "task_id": task_id,
               "held_by": outcome.get("held_by") or h.get("holder_session"),
               "reason": outcome.get("reason")}
        if json_out:
            print(json.dumps(out, indent=2))
        else:
            print(f"  CANNOT CLAIM {task_id}: held by session {out['held_by'] or '?'}")
            if out["reason"]:
                print(f"    {out['reason']}")
        return 1
    now = _now()
    info = {"claimed": True, "task_id": task_id, "renewed": bool(outcome.get("renewed"))}
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
        if prior != "in_progress":
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
    # WHAT THIS CLAIM IS WORTH, said at the moment it is taken -- and READ
    # BACK from the lease and the registry, never assumed from the keeper's
    # report. `session_linked` is the same ``session_is_live`` the dispatch
    # reaper and startup recovery consult, asked about the lease's own holder.
    holder = leases.holder(res) or {}
    sid = holder.get("holder_session")
    try:
        from tools.kanban.lease_liveness import session_is_live

        session_linked = bool(session_is_live(sid))
    except Exception:  # noqa: BLE001 -- the claim stands; only the advice degrades
        session_linked = False
    info.update({
        "holder_session": sid,
        "session_linked": session_linked,
        "keeper": outcome.get("keeper"),
        "keeper_pid": outcome.get("pid"),
        "expires_at": outcome.get("expires_at"),
        "keeper_reason": outcome.get("reason"),
        "keeper_log": outcome.get("log"),
    })
    if json_out:
        print(json.dumps(info, indent=2, default=str))
    else:
        verb = "RENEWED" if info["renewed"] else "CLAIMED"
        print(f"  {verb} {task_id} (was {info.get('prior_status')}) -> in_progress")
        print(f"    branch: {info.get('branch') or '(none recorded — start fresh off origin/main)'}")
        if info.get("commit"):
            print(f"    last commit: {str(info['commit'])[:100]}")
        if session_linked:
            print(f"    held by session {sid} (keeper pid {info['keeper_pid']}, "
                  f"until {info['expires_at']}): the runner and startup recovery "
                  "will honour this claim while that session heartbeats. "
                  "`--claim` again renews it; `--release` ends it.")
        else:
            print(f"    NOTE: this lease is NOT linked to a registered, heartbeating "
                  f"session (keeper: {info['keeper']} -- {info['keeper_reason']}). "
                  "Every reader will treat it as litter within seconds. Re-run "
                  "--claim once the cause is fixed, or gate the task behind a "
                  "manual gate (depends_on_task_id).")
            if info.get("keeper_log"):
                print(f"    keeper log: {info['keeper_log']}")
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
    from tools.kanban import lease_liveness

    out = _release_task_lease(task_id)
    released, reclaimed = out["released"], out["reclaimed"]
    prior, still = out["prior"], out["still"]
    worker_alive = out["worker_heartbeating"]
    why = out["why"]
    if json_out:
        print(json.dumps({
            "released": released,
            "reclaimed_from_exited_session": reclaimed,
            "interactive_claim_ended": bool(out.get("interactive")),
            "task_id": task_id,
            "prior_holder": (prior or {}).get("holder_session"),
            "still_held_by": (still or {}).get("holder_session"),
            "lease_state": out["verdict_state"],
            "worker_heartbeating": worker_alive,
        }, indent=2))
    elif released and reclaimed:
        print(f"  RELEASED: {task_id} (reclaimed from exited session "
              f"{(prior or {}).get('holder_session')}, pid {(prior or {}).get('pid')})")
    elif released and out.get("interactive"):
        print(f"  RELEASED: {task_id} (interactive claim by session "
              f"{(prior or {}).get('holder_session')} ended; its keeper stops on "
              "its next beat)")
    elif released:
        print(f"  RELEASED: {task_id}")
    elif still and worker_alive:
        # The holder's pid is gone but the task is heartbeating: a worker is
        # building it right now under a different pid. Reclaiming here is the
        # duplicate-build race, with a human's hand on the lever.
        print(f"  STILL CLAIMED — the worker is HEARTBEATING: {task_id}")
        print(f"    {why}")
        print("    release is refused while the task heartbeats; it ages out after "
              f"{lease_liveness.HEARTBEAT_LIVE_MINUTES} min once the worker stops.")
    elif still:
        # Not a failure to report as "not held by you" — the holder is ALIVE,
        # and that is the case the claim exists for.
        print(f"  STILL CLAIMED by a LIVE session "
              f"{still.get('holder_session')} (pid {still.get('pid')}): {task_id}")
        if why:
            print(f"    {why}")
    else:
        print(f"  NOT CLAIMED: {task_id}")
    return 0 if released else 1


def _release_task_lease(task_id: str) -> dict:
    """Hand ``kanban:task:<id>`` back — THE ladder, climbed by every door.

    Ownership first (``leases.release`` frees only a lease THIS session took),
    then the two-signal verdict ``lease_liveness.reap_if_litter`` shares with
    the dispatch window: a dead pid alone never reaps, a heartbeating task is
    kept, and cannot-tell reads as alive. ``--release`` and ``--set-status done``
    both call this and neither may grow its own copy (a structural test reads
    their source) — pid-only readers each forming their own opinion is the
    defect rem-hyg-15 / autonomy-adm-03 was about.

    Returns a dict; ``state`` is one of:

      none       nothing held — not claimed, or already expired
      released   this session held it and let go — or an interactive claim's
                 keeper session was ended by name (``interactive`` is True)
      reclaimed  the holder had exited and the task was not heartbeating
      kept       a live holder, or a heartbeating worker — left alone, with ``why``
    """
    from tools.coordination import leases
    from tools.kanban import lease_liveness

    resource = _task_lease_resource(task_id)
    prior = leases.holder(resource)
    released = False
    reclaimed = False
    interactive = False
    verdict = None
    if prior is not None:
        # Rung 0 (mfx-own-02): an INTERACTIVE claim -- a ``cli-claim-<id>-*``
        # keeper session -- is ended by name. The shell releasing it has no
        # identity to match against and the keeper's pid is ALIVE, so neither
        # ownership nor the two-signal reap below could ever let it go.
        from tools.kanban import interactive_claim

        if interactive_claim.claim_session_for(task_id, prior.get("holder_session")):
            interactive = bool(interactive_claim.release(task_id).get("released"))
            released = interactive
    if not released and prior is not None:
        released = leases.release(resource)
    if not released and prior is not None:
        verdict, reclaimed = lease_liveness.reap_if_litter(task_id)
        released = reclaimed

    still = leases.holder(resource)
    worker_alive = verdict is not None and verdict.state == lease_liveness.STATE_WORKING
    if prior is None:
        state = "none"
    elif released and reclaimed:
        state = "reclaimed"
    elif released:
        state = "released"
    else:
        state = "kept"
    return {
        "state": state,
        "released": released,
        "reclaimed": reclaimed,
        "interactive": interactive,
        "prior": prior,
        "still": still,
        "prior_holder": (prior or {}).get("holder_session"),
        "still_held_by": (still or {}).get("holder_session"),
        "verdict_state": verdict.state if verdict is not None else None,
        "worker_heartbeating": worker_alive,
        "why": lease_liveness.describe(verdict) if verdict is not None else None,
    }


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
    parser.add_argument("--protected-ok", dest="protected_ok",
                        action="store_true",
                        help="With --merge: land a PR that touches one of "
                             "pr_watcher's protected_paths (the guard that "
                             "stops the merge ladder auto-merging a change to "
                             "itself). Overrides that ONE rung — all thirteen "
                             "checks still run — and is audited verbatim "
                             "BEFORE the merge, so it requires --reason. "
                             "Never set by any autonomous path.")
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
    parser.add_argument("--intent", metavar="TEXT",
                        help="With --claim: what you are doing with the task, recorded "
                             "on the keeper session (default: 'manual repair of <id>')")
    parser.add_argument("--ttl", dest="claim_ttl", type=int, metavar="SECONDS",
                        help="With --claim: how long the claim holds before its keeper "
                             "lets go (default 7200; --claim again renews it)")
    parser.add_argument("--release", metavar="TASK_ID",
                        help="Release a task lease so the runner can take it back "
                             "(ends an interactive claim's keeper session)")
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
        sys.exit(cmd_claim(args.claim, args.json_out, intent=args.intent,
                           ttl_seconds=args.claim_ttl))

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
                            merge=args.merge, dry_run=args.dry_run,
                            protected_ok=args.protected_ok))

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
