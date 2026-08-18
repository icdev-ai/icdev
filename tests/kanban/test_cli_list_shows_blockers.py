# CUI // SP-CTI
"""`--list` must expose the field that decides whether a task can run.

`promote_backlog_to_scheduled` refuses to promote a task whose dependencies are
not done/decomposed, so a held dependency is the single most common reason a
board that looks ready is not running anything.

`--list --json` emitted only id/title/status/priority. That did not make the
output terse, it made it MISLEADING: a reader who checks
``task.get("depends_on_task_id")`` gets None for every row and concludes nothing
is gated. On 2026-08-09 that produced a confident, wrong answer — 37 backlog
tasks read as ungated when every one of them was held behind a manual gate, and
the board was reported broken while it was obeying its own rules exactly.

WHICH dependency holds is ``tools.kanban.deps``' answer, not the scalar column's
(kpr-fix-02). A task whose junction rows superseded its ``depends_on_task_id``
will be dispatched, so printing its seeding predecessor as a blocker is the same
misleading-output defect one mechanism further in: it names a hold that does not
exist, and the reader goes looking for work to unblock that is already free.
"""
from __future__ import annotations

import importlib
import json

cli = importlib.import_module("tools.kanban.cli")


class _Row(dict):
    """Rows behave as mappings under both sqlite3.Row and psycopg2 factories."""


class _Conn:
    """Answers the list query with ``rows`` and the dependency reads honestly.

    ``cmd_list`` now makes a bulk dependency read as well as its own SELECT, so a
    stub that returned the same rows for every query would feed the junction
    table task rows and answer a question nobody asked. Routed on the SQL, and
    ``junction`` lets a test declare the mechanism the scalar column cannot.
    """

    def __init__(self, rows, junction=()):
        self._rows = rows
        self._junction = list(junction)
        self.sql = ""
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params
        self._last = sql
        return self

    def fetchall(self):
        sql = getattr(self, "_last", "")
        if "kanban_task_deps" in sql:
            return [_Row(task_id=t, depends_on_id=d) for t, d in self._junction]
        if "depends_on_task_id IS NOT NULL" in sql:
            return [r for r in self._rows if r.get("depends_on_task_id")]
        if sql.strip().startswith("SELECT id, status, title"):
            # The dependency rows the list query joined to are board rows too.
            seen = {r["id"] for r in self._rows}
            extra = [
                _Row(id=r["depends_on_task_id"], status=r.get("depends_on_status"),
                     title="")
                for r in self._rows
                if r.get("depends_on_task_id")
                and r["depends_on_task_id"] not in seen
                and r.get("depends_on_status") is not None
            ]
            return list(self._rows) + extra
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _run(monkeypatch, rows, *, json_out=True, prefix=None, status=None, junction=()):
    conn = _Conn(rows, junction)
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
    """A dangling id is a blocker, not 'fine'. Refusing is the recoverable error."""
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


def test_a_scalar_the_junction_graph_superseded_is_not_a_blocker(monkeypatch, capsys):
    """The kpr-fix-02 case: seeding order printed as a hold that does not exist.

    ``cef-di-04`` was seeded after ``cef-di-03`` because a seeder walked a list.
    Its REAL prerequisite, ``cef-rsv-01``, is done, so the dispatcher runs it —
    and a report that still names ``cef-di-03`` sends a reader to unblock work
    that is already free.
    """
    rows = [
        _Row(id="cef-rsv-01", title="built", status="done", priority="high",
             depends_on_task_id=None, depends_on_status=None),
        _Row(id="cef-di-03", title="sibling", status="in_progress", priority="high",
             depends_on_task_id=None, depends_on_status=None),
        _Row(id="cef-di-04", title="freed", status="backlog", priority="high",
             depends_on_task_id="cef-di-03", depends_on_status="in_progress"),
    ]
    _run(monkeypatch, rows, json_out=False,
         junction=[("cef-di-04", "cef-rsv-01")])
    assert "blocked by" not in capsys.readouterr().out


def test_a_manual_gate_scalar_is_still_a_blocker_with_junction_rows(monkeypatch, capsys):
    """A gate is a HOLD, not seeding order — the junction must not release it."""
    rows = [
        _Row(id="kpr-gate-02", title="MANUAL-MODE GATE - hold for review",
             status="in_progress", priority="high",
             depends_on_task_id=None, depends_on_status=None),
        _Row(id="done-dep", title="built", status="done", priority="high",
             depends_on_task_id=None, depends_on_status=None),
        _Row(id="held-01", title="held", status="backlog", priority="high",
             depends_on_task_id="kpr-gate-02", depends_on_status="in_progress"),
    ]
    _run(monkeypatch, rows, json_out=False, junction=[("held-01", "done-dep")])
    out = capsys.readouterr().out
    assert "blocked by kpr-gate-02" in out and "in_progress" in out
