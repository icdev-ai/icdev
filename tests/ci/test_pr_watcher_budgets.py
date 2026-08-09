# CUI // SP-CTI
"""Recovery budgets are per-PR, and the HITL alert is deduped.

A task that burned 5 resumes on an abandoned PR used to inherit 5/5 on its NEXT
one and could never be auto-recovered again — measured 2026-08-09, sbx-fld-05 sat
at 5/5 resumes and 2/2 rebases while holding a clean, green PR the watcher would
have refused to help. A new PR is a new attempt.
"""
from __future__ import annotations

import json

import tools.ci.pr_watcher as pw


class _Conn:
    def __init__(self, rows):
        self._rows = rows
        self.inserts = []

    def execute(self, sql, params=None):
        self._sql = sql
        if sql.strip().upper().startswith("SELECT"):
            return self
        self.inserts.append((sql, params))
        return self

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return None

    def commit(self):
        pass

    def close(self):
        pass


def _row(task_id, pr_url, action="escalate"):
    return {"d": json.dumps({"task_id": task_id, "pr_url": pr_url, "action": action})}


def _watcher(conn):
    return pw.PRWatcher(config={}, get_connection=lambda: conn)


def test_budget_counts_only_the_current_pr():
    conn = _Conn([_row("t-1", "https://x/pull/1"), _row("t-1", "https://x/pull/1"),
                  _row("t-1", "https://x/pull/2")])
    w = _watcher(conn)
    assert w._count_audit_actions("t-1", ("pr_watcher.resume",),
                                  pr_url="https://x/pull/2") == 1
    assert w._count_audit_actions("t-1", ("pr_watcher.resume",),
                                  pr_url="https://x/pull/1") == 2


def test_a_new_pr_starts_the_budget_fresh():
    """The whole point: a superseded PR's failures must not poison the next one."""
    conn = _Conn([_row("t-1", "https://x/pull/1") for _ in range(5)])
    w = _watcher(conn)
    assert w._count_audit_actions("t-1", ("pr_watcher.resume",),
                                  pr_url="https://x/pull/9") == 0


def test_another_tasks_rows_are_never_counted():
    """The payload embeds reasons naming other tasks' PRs, so a substring scan
    over the blob over-counts — it matched six tasks where one had escalated."""
    conn = _Conn([_row("t-2", "https://x/pull/1")])
    w = _watcher(conn)
    assert w._count_audit_actions("t-1", ("pr_watcher.resume",),
                                  pr_url="https://x/pull/1") == 0


def test_omitting_pr_url_keeps_the_old_lifetime_count():
    """Callers that have no PR in hand still get the task-wide number."""
    conn = _Conn([_row("t-1", "https://x/pull/1"), _row("t-2", "https://x/pull/2")])
    w = _watcher(conn)
    assert w._count_audit_actions("t-1", ("pr_watcher.resume",)) == 2
