#!/usr/bin/env python3
# CUI // SP-CTI
"""Dispatch source pattern analysis for kanban tasks + verifications.

Usage:
    python tools/kanban/source_stats.py           # summary table
    python tools/kanban/source_stats.py --json    # machine-readable
    python tools/kanban/source_stats.py --phantom # show only phantom patterns
    python tools/kanban/source_stats.py --since "2026-04-10"  # date filter

Helps narrow down bugs: "All phantoms came from genesis_scheduler" or
"claude_interactive never fails verification" makes root cause obvious.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection


def summary(since: str | None = None) -> dict:
    conn = get_connection()
    params: list = []
    where = ""
    if since:
        where = "WHERE verified_at >= ?"
        params.append(since)

    try:
        # Verification results by source
        # nosec B608 -- `where` is an internally-constructed constant, not user input
        verif_sql = f"""
            SELECT dispatch_source, result, COUNT(*) AS n
            FROM kanban_verifications
            {where}
            GROUP BY dispatch_source, result
            ORDER BY dispatch_source, result
        """  # nosec B608
        verif = [dict(r) for r in conn.execute(verif_sql, tuple(params)).fetchall()]

        # Task status by source (current state)
        task_sql = """
            SELECT dispatch_source, status, COUNT(*) AS n
            FROM kanban_tasks
            GROUP BY dispatch_source, status
            ORDER BY dispatch_source, status
        """
        tasks = [dict(r) for r in conn.execute(task_sql).fetchall()]

        # Phantom patterns: which sources produce phantoms?
        phantom_sql = """
            SELECT dispatch_source, COUNT(*) AS phantom_count
            FROM kanban_verifications
            WHERE result = 'phantom' OR reason ILIKE '%phantom%'
            GROUP BY dispatch_source
        """ if get_connection()._backend == "postgresql" else """
            SELECT dispatch_source, COUNT(*) AS phantom_count
            FROM kanban_verifications
            WHERE result = 'phantom' OR reason LIKE '%phantom%' OR reason LIKE '%PHANTOM%'
            GROUP BY dispatch_source
        """
        phantoms = [dict(r) for r in conn.execute(phantom_sql).fetchall()]

        result = {
            "verifications_by_source": verif,
            "tasks_by_source": tasks,
            "phantom_by_source": phantoms,
        }

        # crx-kan-01: surface SLA (overdue/at-risk) + cycle-time / throughput on
        # the board summary. Guarded so a metrics hiccup never breaks the summary.
        try:
            from tools.kanban.metrics import board_metrics
            result.update(board_metrics(conn=conn))
        except Exception:
            pass

        return result
    finally:
        conn.close()


def _print_table(rows: list, cols: list, title: str) -> None:
    print(f"\n=== {title} ===")
    if not rows:
        print("  (no rows)")
        return
    # Compute widths
    widths = [max(len(str(r.get(c, ""))) for r in rows + [{c: c}]) for c in cols]
    header = "  " + "  ".join(c.ljust(w) for c, w in zip(cols, widths))
    sep = "  " + "  ".join("-" * w for w in widths)
    print(header)
    print(sep)
    for r in rows:
        print("  " + "  ".join(str(r.get(c, "")).ljust(w) for c, w in zip(cols, widths)))


def main():
    parser = argparse.ArgumentParser(description="Dispatch source stats")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--phantom", action="store_true", help="Only show phantom pattern")
    parser.add_argument("--since", help="ISO date filter (e.g. 2026-04-10)")
    args = parser.parse_args()

    data = summary(since=args.since)

    if args.json:
        print(json.dumps(data, indent=2, default=str))
        return

    if args.phantom:
        _print_table(data["phantom_by_source"], ["dispatch_source", "phantom_count"],
                     "Phantom completions by source")
        return

    _print_table(
        data["tasks_by_source"], ["dispatch_source", "status", "n"],
        "Current kanban_tasks state by dispatch_source",
    )
    _print_table(
        data["verifications_by_source"], ["dispatch_source", "result", "n"],
        "Verification outcomes by dispatch_source",
    )
    _print_table(
        data["phantom_by_source"], ["dispatch_source", "phantom_count"],
        "Phantom completions by source (for narrowing down bugs)",
    )


if __name__ == "__main__":
    main()
