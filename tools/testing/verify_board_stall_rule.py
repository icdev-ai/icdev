#!/usr/bin/env python3
# CUI // SP-CTI
"""Runtime proof of the board-throughput stall rule on the AMBIENT backend.

``tests/test_kanban_throughput_stall.py`` covers the logic, but it runs on
SQLite, where ``translate_sql`` rewrites ``%s`` and papers over dialect
differences. This drives the same scenarios through ``get_connection()`` on
whatever backend is configured — the point being to run it against real
PostgreSQL, where a portability bug fails loudly instead of silently.

SAFETY: this script WRITES kanban and alert rows, so it refuses to run against
any database that already holds kanban tasks, transitions, or alerts. Point it
at a throwaway database, never the live board::

    python -c "import psycopg2; c=psycopg2.connect(host='localhost',user='icdev',\\
        password='...',dbname='postgres'); c.autocommit=True; \\
        c.cursor().execute('CREATE DATABASE icdev_stall_verify')"

    ICDEV_DATABASE_URL=postgresql://icdev:...@localhost:5432/icdev_stall_verify \\
    ICDEV_STORAGE_BACKEND=postgresql ICDEV_PG_NO_FALLBACK=1 \\
      python tools/db/bootstrap_pg.py

    ICDEV_DATABASE_URL=postgresql://icdev:...@localhost:5432/icdev_stall_verify \\
    ICDEV_STORAGE_BACKEND=postgresql ICDEV_PG_NO_FALLBACK=1 \\
      python tools/testing/verify_board_stall_rule.py --json

Exit 0 iff every scenario holds. ``--force`` overrides the non-empty-board
refusal; it exists for a throwaway DB that already has residue, and should
never be pointed at production.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.genesis.reflexes import self_monitor  # noqa: E402
from tools.kanban import metrics  # noqa: E402
from tools.kanban.transition_reason import resolve_transition_reason  # noqa: E402

# Every row this script writes carries this marker so cleanup can be exact.
MARK = "stallverify"

CFG = {
    "board_throughput": {
        "enabled": True,
        "window_hours": 24,
        "min_active_tasks": 1,
        "cooldown_hours": 12,
        "severity": "critical",
    }
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _seed_done(conn, tid: str, at: datetime) -> None:
    conn.execute(
        "INSERT INTO kanban_status_transitions "
        "(id, task_id, from_status, to_status, actor, reason, recorded_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (f"kst-{MARK}-{tid}", f"{MARK}-{tid}", "in_progress", "done", MARK,
         resolve_transition_reason(
             "board throughput stall rule verification (kax-stall-01)",
             from_status="in_progress", to_status="done", actor=MARK,
         ),
         at.isoformat()),
    )
    conn.commit()


def _seed_task(conn, tid: str, status: str) -> None:
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, description, task_type, priority, status, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (f"{MARK}-{tid}", f"{MARK} {tid}", "board stall verification", "build",
         "high", status, _now().isoformat()),
    )
    conn.commit()


def _cleanup(conn) -> None:
    for sql, params in (
        ("DELETE FROM kanban_status_transitions WHERE actor = %s", (MARK,)),
        ("DELETE FROM kanban_tasks WHERE id LIKE %s", (f"{MARK}-%",)),
        ("DELETE FROM alerts WHERE source = %s", (self_monitor.STALL_SOURCE,)),
    ):
        try:
            conn.execute(sql, params)
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - cleanup is best-effort
            print(f"[warn] cleanup failed ({sql}): {exc}", file=sys.stderr)
            try:
                conn.rollback()
            except Exception:
                pass


def _stall_alerts(conn) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, status, title, severity FROM alerts WHERE source = %s ORDER BY id",
        (self_monitor.STALL_SOURCE,),
    ).fetchall()
    return [dict(r) if hasattr(r, "keys") else
            {"id": r[0], "status": r[1], "title": r[2], "severity": r[3]} for r in rows]


def _guard_empty(conn, force: bool) -> None:
    """Refuse to touch a database that holds real board data."""
    counts = {}
    for table in ("kanban_tasks", "kanban_status_transitions", "alerts"):
        row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()  # noqa: S608 - fixed names
        counts[table] = int(row["c"] if hasattr(row, "keys") else row[0])
    if any(counts.values()) and not force:
        raise SystemExit(
            "refusing to run: target database is not empty "
            f"({counts}). Point ICDEV_DATABASE_URL at a throwaway database, "
            "or pass --force if you are certain this is not the live board."
        )


def run(force: bool = False) -> Dict[str, Any]:
    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite")
    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"name": name, "passed": bool(ok), "detail": detail})

    conn = get_connection()
    try:
        conn.set_security_context(None)  # rls-bypass: verification harness, no user session
    except Exception:
        pass

    try:
        _guard_empty(conn, force)

        # --- 1. four-day flatline with scheduled work → alert opens ---------
        _seed_done(conn, "old", _now() - timedelta(days=4))
        _seed_task(conn, "a", "scheduled")
        _seed_task(conn, "b", "in_progress")

        signal = metrics.throughput_stall_check(conn=conn, window_hours=24, min_active_tasks=1)
        check("signal_detects_flatline",
              signal["stalled"] is True and signal["completed_in_window"] == 0
              and signal["active_tasks"] == 2, signal)

        r1 = self_monitor._check_board_throughput(conn, CFG)
        check("alert_opened", r1.get("action") == "opened", r1.get("action"))
        rows = _stall_alerts(conn)
        check("exactly_one_firing_alert",
              len(rows) == 1 and rows[0]["status"] == "firing" and rows[0]["severity"] == "critical",
              rows)

        # --- 2. consecutive cycles must not duplicate ----------------------
        actions = [self_monitor._check_board_throughput(conn, CFG).get("action") for _ in range(3)]
        rows = _stall_alerts(conn)
        check("no_duplicate_on_consecutive_cycles",
              actions == ["unchanged"] * 3 and len(rows) == 1, {"actions": actions, "rows": len(rows)})

        # --- 3. human resolves it mid-stall → cooldown suppresses re-open ---
        conn.execute("UPDATE alerts SET status = 'resolved' WHERE source = %s",
                     (self_monitor.STALL_SOURCE,))
        conn.commit()
        r3 = self_monitor._check_board_throughput(conn, CFG)
        check("cooldown_suppresses_reopen",
              r3.get("action") == "cooldown" and len(_stall_alerts(conn)) == 1, r3.get("action"))

        # --- 4. throughput returns → firing alert resolves ------------------
        conn.execute("UPDATE alerts SET status = 'firing', resolved_at = NULL WHERE source = %s",
                     (self_monitor.STALL_SOURCE,))
        conn.commit()
        _seed_done(conn, "fresh", _now())
        r4 = self_monitor._check_board_throughput(conn, CFG)
        rows = _stall_alerts(conn)
        check("recovery_resolves_alert",
              r4.get("action") == "resolved" and all(r["status"] == "resolved" for r in rows),
              {"action": r4.get("action"), "rows": rows})

        # --- 5. idle-but-empty board stays silent ---------------------------
        conn.execute("DELETE FROM kanban_tasks WHERE id LIKE %s", (f"{MARK}-%",))
        conn.execute("DELETE FROM kanban_status_transitions WHERE actor = %s", (MARK,))
        conn.execute("DELETE FROM alerts WHERE source = %s", (self_monitor.STALL_SOURCE,))
        conn.commit()
        _seed_done(conn, "ancient", _now() - timedelta(days=9))
        idle = metrics.throughput_stall_check(conn=conn, window_hours=24, min_active_tasks=1)
        r5 = self_monitor._check_board_throughput(conn, CFG)
        check("idle_board_is_silent",
              idle["stalled"] is False and idle["reason"] == "board_idle"
              and r5.get("action") == "healthy" and _stall_alerts(conn) == [],
              {"signal": idle, "action": r5.get("action")})

        # --- 6. the probe rule must not resolve a live stall alert ----------
        _seed_task(conn, "c", "scheduled")
        self_monitor._check_board_throughput(conn, CFG)
        self_monitor._sync_alerts(conn, {}, 1)
        rows = _stall_alerts(conn)
        check("probe_rule_leaves_stall_alert_firing",
              len(rows) == 1 and rows[0]["status"] == "firing", rows)
    finally:
        _cleanup(conn)
        try:
            conn.close()
        except Exception:
            pass

    passed = all(c["passed"] for c in checks)
    return {"backend": backend, "passed": passed, "checks": checks}


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify the board-throughput stall rule on the ambient backend")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="run even if the target database already holds board rows (throwaway DBs only)")
    args = ap.parse_args(argv)

    result = run(force=args.force)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"backend: {result['backend']}")
        for c in result["checks"]:
            print(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['name']}")
            if not c["passed"]:
                print(f"         detail: {c['detail']}")
        print(f"\n{'PASS' if result['passed'] else 'FAIL'}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
