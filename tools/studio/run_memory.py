"""ICDEV™ Studio — run-scoped memory for workflow steps (dwo-mem-01).

The single mechanism for inter-step data in a Studio workflow run.  Before
this, steps communicated only through stdout JSON scraped by the next
executor, so there was no shared state a step could rely on.

Scope
-----
Memory is **per run**.  Keys live under a ``run_id`` and are invisible to
every other run.  ``studio_run_memory`` is mutable (unlike the append-only
``studio_workflow_runs`` audit trail) — a step may overwrite its own keys.

Resume
------
``workflow_runner.resume_run()`` re-enters the *same* ``run_id``, so a
resumed run keeps its memory with no extra work.  If a resume ever forks a
new run id instead, call :func:`copy` at resume time: the parent's record
stays immutable and the child starts from a snapshot of it.

Reserved keys
-------------
``_inputs`` — run inputs supplied at trigger time (dwo-evt-04).  Run inputs
land here rather than in a parallel store.

``canvas`` — the canvas slug the run belongs to, written by the runner from
the workflow template's ``canvas:`` declaration (dwo-mem-02).

``artifacts`` — every step's declared artifacts, keyed by step name::

    {"Generate IaC": [{"name": "main.tf", "path": "...", "type": "tf"}, ...]}

The runner writes each step's entry as that step completes, so a later step
reads its inputs from here instead of re-parsing an earlier step's stdout.
Keyed by step name rather than flattened so that a consumer asking for the
IaC generator's ``.tf`` files cannot pick up a ``.tf`` emitted by some other
step in the same run.

This is NOT ``tools/memory/`` — that is the long-term session memory system,
a different concern entirely.

CLI (for non-Python executors)::

    python tools/studio/run_memory.py --run-id RUN --set key --value '{"a":1}'
    python tools/studio/run_memory.py --run-id RUN --get key
    python tools/studio/run_memory.py --run-id RUN --all
    python tools/studio/run_memory.py --run-id RUN --delete key

The runner exports ``ICDEV_RUN_ID`` into every step's environment, so
``--run-id`` may be omitted inside a step.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

#: Environment variable the runner sets so a step knows its own run.
RUN_ID_ENV = "ICDEV_RUN_ID"

#: Key under which dwo-evt-04 run inputs are stored.
INPUTS_KEY = "_inputs"

#: Key under which the run's canvas slug is stored (dwo-mem-02).
CANVAS_KEY = "canvas"

#: Key under which per-step artifact lists are stored (dwo-mem-02).
ARTIFACTS_KEY = "artifacts"

_MISSING = object()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_run_id() -> str:
    """The run id of the step we are executing inside, or ``''``."""
    return os.environ.get(RUN_ID_ENV, "") or ""


def _require_run_id(run_id: str | None) -> str:
    rid = (run_id or current_run_id()).strip()
    if not rid:
        raise ValueError(
            f"run_id is required (pass it explicitly or set {RUN_ID_ENV})"
        )
    return rid


def get(run_id: str, key: str, default=None):
    """Return the value stored under ``key`` for ``run_id``, else ``default``.

    The JSON column is read raw and parsed in Python — no SQL JSON functions.
    """
    rid = _require_run_id(run_id)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value_json FROM studio_run_memory WHERE run_id = %s AND key = %s",
            (rid, key),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return default
    raw = row[0] if not isinstance(row, dict) else row["value_json"]
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def get_inputs(run_id: str | None = None) -> dict:
    """Return the inputs the run was started with (dwo-evt-04).

    This is the contract a workflow step uses to see the event that triggered
    it.  ``run_id`` defaults to ``ICDEV_RUN_ID``, which the runner exports into
    every step's environment, so inside a step this is simply::

        from tools.studio import run_memory
        payload = run_memory.get_inputs()

    Returns ``{}`` for a manually started run with no inputs, so callers never
    need to branch on None.
    """
    value = get(_require_run_id(run_id), INPUTS_KEY, default={})
    return value if isinstance(value, dict) else {}


def set(run_id: str, key: str, value) -> dict:  # noqa: A001 — documented API name
    """Store ``value`` (any JSON-serializable object) under ``key``."""
    rid = _require_run_id(run_id)
    payload = json.dumps(value, default=str)
    ts = _now()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO studio_run_memory (run_id, key, value_json, updated_at)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (run_id, key)
            DO UPDATE SET value_json = EXCLUDED.value_json,
                          updated_at = EXCLUDED.updated_at
            """,
            (rid, key, payload, ts),
        )
        conn.commit()
    finally:
        conn.close()
    return {"run_id": rid, "key": key, "updated_at": ts}


def all(run_id: str) -> dict:  # noqa: A001 — documented API name
    """Return every key for ``run_id`` as a plain dict."""
    rid = _require_run_id(run_id)
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT key, value_json FROM studio_run_memory WHERE run_id = %s ORDER BY key",
            (rid,),
        ).fetchall()
    finally:
        conn.close()

    out: dict = {}
    for row in rows:
        if isinstance(row, dict):
            key, raw = row["key"], row["value_json"]
        else:
            key, raw = row[0], row[1]
        try:
            out[key] = json.loads(raw)
        except (TypeError, ValueError):
            out[key] = None
    return out


def delete(run_id: str, key: str) -> bool:
    """Remove ``key`` from ``run_id``.  Returns True if a row was removed."""
    rid = _require_run_id(run_id)
    existing = get(rid, key, default=_MISSING)
    if existing is _MISSING:
        return False
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM studio_run_memory WHERE run_id = %s AND key = %s",
            (rid, key),
        )
        conn.commit()
    finally:
        conn.close()
    return True


def copy(src_run_id: str, dst_run_id: str) -> int:
    """Snapshot ``src_run_id``'s memory into ``dst_run_id``.

    Used when a resume forks a new run id: the parent's record stays
    immutable and the child starts from a copy.  Existing keys on the
    destination are overwritten.  Returns the number of keys copied.
    """
    src = _require_run_id(src_run_id)
    dst = _require_run_id(dst_run_id)
    if src == dst:
        return 0
    copied = 0
    for key, value in all(src).items():
        set(dst, key, value)
        copied += 1
    return copied


# ── CLI ────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run-scoped memory for ICDEV™ Studio workflow steps"
    )
    parser.add_argument(
        "--run-id",
        default="",
        help=f"Run id (defaults to ${RUN_ID_ENV} inside a workflow step)",
    )
    parser.add_argument("--get", metavar="KEY", help="Print the value of KEY")
    parser.add_argument("--set", metavar="KEY", help="Store --value under KEY")
    parser.add_argument(
        "--value",
        help="Value for --set; parsed as JSON, falling back to a plain string",
    )
    parser.add_argument("--delete", metavar="KEY", help="Delete KEY")
    parser.add_argument("--all", action="store_true", help="Print every key")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    try:
        run_id = _require_run_id(args.run_id)
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 2

    if args.set:
        if args.value is None:
            print(json.dumps({"status": "error", "error": "--set requires --value"}))
            return 2
        try:
            value = json.loads(args.value)
        except ValueError:
            value = args.value
        result = set(run_id, args.set, value)
        print(json.dumps({"status": "ok", **result}))
        return 0

    if args.get:
        value = get(run_id, args.get, default=None)
        print(json.dumps({"status": "ok", "run_id": run_id, "key": args.get, "value": value}))
        return 0

    if args.delete:
        removed = delete(run_id, args.delete)
        print(json.dumps({"status": "ok", "run_id": run_id, "key": args.delete, "deleted": removed}))
        return 0

    if args.all:
        print(json.dumps({"status": "ok", "run_id": run_id, "memory": all(run_id)}))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
