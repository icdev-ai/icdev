# CUI // SP-CTI
"""`--list` must expose the field that decides whether a task can run.

`promote_backlog_to_scheduled` refuses to promote a task whose
`depends_on_task_id` is not done/decomposed, so a held dependency is the single
most common reason a board that looks ready is not running anything.

`--list --json` emitted only id/title/status/priority. That did not make the
output terse, it made it MISLEADING: a reader who checks
``task.get("depends_on_task_id")`` gets None for every row and concludes nothing
is gated. On 2026-08-09 that produced a confident, wrong answer — 37 backlog
tasks read as ungated when every one of them was held behind a manual gate, and
the board was reported broken while it was obeying its own rules exactly.
"""
from __future__ import annotations

import importlib
import json

cli = importlib.import_module("tools.kanban.cli")


class _Row(dict):
    """Rows behave as mappings under both sqlite3.Row and psycopg2 factories."""


class _Conn:
    def __init__(self, rows):
        self._rows = rows
        self.sql = ""
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params
        return self

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _run(monkeypatch, rows, *, json_out=True, prefix=None, status=None):
    conn = _Conn(rows)
    monkeypatch.setattr(cli, "get_connection", lambda: conn)
    rc = cli.cmd_list(prefix, status, json_out)
    return rc, conn


def test_json_carries_the_dependency_and_its_status(monkeypatch, capsys):
    rows = [_Row(id="t-1", title="held", status="backlog", priority="high",
                 depends_on_task_id="g-00", depends_on_status="in_progress")]
    rc, _ = _run(monkeypatch, rows)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["depends_on_task_id"] == "g-00"
    # The id alone does not answer "is it satisfied?" — needing a second query
    # per task to find out is what made the wrong answer easy to reach.
    assert payload[0]["depends_on_status"] == "in_progress"


def test_human_output_names_the_blocker(monkeypatch, capsys):
    rows = [_Row(id="t-1", title="held", status="backlog", priority="high",
                 depends_on_task_id="g-00", depends_on_status="in_progress")]
    _run(monkeypatch, rows, json_out=False)
    out = capsys.readouterr().out
    assert "blocked by g-00" in out
    assert "in_progress" in out


def test_a_satisfied_dependency_is_not_reported_as_a_blocker(monkeypatch, capsys):
    """Noise on every row would train people to ignore the marker."""
    rows = [_Row(id="t-1", title="ready", status="backlog", priority="high",
                 depends_on_task_id="g-00", depends_on_status="done")]
    _run(monkeypatch, rows, json_out=False)
    assert "blocked by" not in capsys.readouterr().out


def test_a_dependency_pointing_at_a_missing_task_is_still_flagged(monkeypatch, capsys):
    """LEFT JOIN yields NULL for a dangling id — that is a blocker, not 'fine'."""
    rows = [_Row(id="t-1", title="dangling", status="backlog", priority="high",
                 depends_on_task_id="ghost", depends_on_status=None)]
    _run(monkeypatch, rows, json_out=False)
    out = capsys.readouterr().out
    assert "blocked by ghost" in out and "MISSING" in out


def test_filters_still_bind_after_the_join(monkeypatch):
    """The join aliases kanban_tasks, so unqualified filters would be ambiguous."""
    rc, conn = _run(monkeypatch, [], prefix="hgx", status="backlog")
    assert rc == 0
    assert "t.id LIKE" in conn.sql and "t.status =" in conn.sql
    assert conn.params == ["hgx%", "backlog"]
