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
) -> int:
    if status not in VALID_STATUSES:
        print(
            f"ERROR: invalid status '{status}'. Valid: {', '.join(sorted(VALID_STATUSES))}",
            file=sys.stderr,
        )
        return 1

    # Merge-verify before writing anything: refuse the whole batch rather than
    # marking some tasks done and rejecting others halfway through.
    if status == "done" and not force_done:
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
                _record_manual_transition(
                    conn, tid, prior_status, status,
                    reason=(f"FORCED done (merge check overridden): {reason}"
                            if (status == 'done' and force_done) else ""),
                )
            row = conn.execute(
                "SELECT id, title, status FROM kanban_tasks WHERE id = %s", (tid,)
            ).fetchone()
            if row:
                results.append(dict(row))
            else:
                results.append({"id": tid, "error": "not found"})

    if json_out:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            if "error" in r:
                print(f"  NOT FOUND: {r['id']}")
            else:
                print(f"  {r['id']}: {r['status']}  {r.get('title', '')[:70]}")
    return 0


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


def cmd_list(prefix: str | None, status: str | None, json_out: bool) -> int:
    conditions = []
    params = []

    if prefix:
        conditions.append("id LIKE %s")
        params.append(prefix + "%")
    if status:
        if status not in VALID_STATUSES:
            print(f"ERROR: invalid status '{status}'.", file=sys.stderr)
            return 1
        conditions.append("status = %s")
        params.append(status)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT id, title, status, priority FROM kanban_tasks {where} ORDER BY id",
            params,
        ).fetchall()

    results = [dict(r) for r in rows]

    if json_out:
        print(json.dumps(results, indent=2))
    else:
        if not results:
            print("  (no tasks found)")
        for r in results:
            print(f"  [{r['status']:15s}] {r['id']:25s} {r.get('title','')[:60]}")
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
    """Release the per-task lease so the runner (or another session) can take it."""
    from tools.coordination import leases
    released = leases.release(_task_lease_resource(task_id))
    if json_out:
        print(json.dumps({"released": released, "task_id": task_id}, indent=2))
    else:
        print(f"  {'RELEASED' if released else 'NOT HELD BY THIS SESSION'}: {task_id}")
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
    """Resume the autonomous kanban runner (release the global pause lease)."""
    from tools.coordination import leases
    released = leases.release(_RUNNER_PAUSE_RESOURCE)
    if json_out:
        print(json.dumps({"resumed": released}, indent=2))
    else:
        print(f"  {'RUNNER RESUMED' if released else 'NOT PAUSED BY THIS SESSION'}")
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
                        help="Justification recorded with --force-done")
    parser.add_argument("--show", metavar="TASK_ID", help="Show details of one task")

    # --pipeline <id> — delivery-pipeline (gate) status; CLI mirror of the dashboard stepper
    parser.add_argument("--pipeline", metavar="TASK_ID",
                        help="Show a task's delivery-pipeline (gate) status (CLI view)")

    # --reverify <id> — recompute the done-gate verdict from git state
    parser.add_argument("--reverify", metavar="TASK_ID",
                        help="Recompute a task's verification from its branch and "
                             "append a fresh verdict, clearing a stale done-gate block "
                             "without re-dispatching (exit 0=passed, 1=failed, 2=unknown)")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="With --reverify: compute the verdict without writing it")

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
                            force_done=args.force_done, reason=args.reason or ''))

    elif args.show:
        sys.exit(cmd_show(args.show, args.json_out))

    elif args.pipeline:
        sys.exit(cmd_pipeline(args.pipeline, args.json_out))

    elif args.reverify:
        sys.exit(cmd_reverify(args.reverify, args.json_out, dry_run=args.dry_run))

    elif args.list:
        sys.exit(cmd_list(args.prefix, args.status, args.json_out))

    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
