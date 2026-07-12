# CUI // SP-CTI
"""Unit tests for the stranded-branch auditor (kph-A).

Hermetic: a fake connection supplies kanban_tasks rows and an injected git_check
stub supplies (branch_exists, unmerged_count) — no real git repo or DB needed.
"""
from __future__ import annotations

from tools.kanban import stranded_audit as sa


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    """Returns preset rows for the terminal-tasks query; closeable."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        return _FakeCursor(self._rows)

    def close(self):
        pass


def _rows(*specs):
    # each spec: (id, status) -> dict row like StorageConnection yields
    return [{"id": i, "status": s, "title": f"title {i}"} for i, s in specs]


def test_done_with_unmerged_is_stranded():
    conn = _FakeConn(_rows(("t-a", "done")))
    checks = {"t-a": (True, 3)}  # branch exists, 3 unmerged commits
    r = sa.audit_stranded_tasks(conn=conn, git_check=lambda tid: checks.get(tid, (False, 0)), fetch=False)
    assert r["total"] == 1
    assert len(r["stranded"]) == 1
    assert r["stranded"][0]["id"] == "t-a"
    assert r["stranded"][0]["unmerged_commits"] == 3
    assert r["clean_count"] == 0


def test_validating_without_branch_is_orphan():
    conn = _FakeConn(_rows(("t-v", "validating")))
    r = sa.audit_stranded_tasks(conn=conn, git_check=lambda tid: (False, 0), fetch=False)
    assert len(r["orphan_validating"]) == 1
    assert r["orphan_validating"][0]["id"] == "t-v"
    assert not r["stranded"]


def test_done_fully_merged_is_clean():
    # branch exists but 0 commits ahead of origin/main -> clean
    conn = _FakeConn(_rows(("t-m", "done")))
    r = sa.audit_stranded_tasks(conn=conn, git_check=lambda tid: (True, 0), fetch=False)
    assert r["clean_count"] == 1
    assert not r["stranded"] and not r["orphan_validating"]


def test_done_no_branch_is_clean():
    # merged long ago, branch deleted -> not stranded, not orphan (done, not validating)
    conn = _FakeConn(_rows(("t-old", "done")))
    r = sa.audit_stranded_tasks(conn=conn, git_check=lambda tid: (False, 0), fetch=False)
    assert r["clean_count"] == 1
    assert not r["stranded"] and not r["orphan_validating"]


def test_mixed_population():
    conn = _FakeConn(_rows(
        ("s1", "done"), ("s2", "validating"), ("c1", "done"), ("o1", "validating"),
    ))
    checks = {"s1": (True, 2), "s2": (True, 1), "c1": (True, 0), "o1": (False, 0)}
    r = sa.audit_stranded_tasks(conn=conn, git_check=lambda tid: checks[tid], fetch=False)
    assert {f["id"] for f in r["stranded"]} == {"s1", "s2"}
    assert {f["id"] for f in r["orphan_validating"]} == {"o1"}
    assert r["clean_count"] == 1
    assert r["total"] == 4


def test_empty_population():
    r = sa.audit_stranded_tasks(conn=_FakeConn([]), git_check=lambda tid: (False, 0), fetch=False)
    assert r["total"] == 0 and not r["stranded"] and not r["orphan_validating"]


def test_card_specs_are_suggested_and_stable(monkeypatch):
    # _file_suggested_cards must emit status='suggested' + stable ids, via task_factory.
    captured = {}

    def _fake_create_tasks(specs):
        captured["specs"] = specs
        return [s["id"] for s in specs]

    import tools.kanban.task_factory as tf
    monkeypatch.setattr(tf, "create_tasks", _fake_create_tasks)

    findings = {
        "default_branch": "main",
        "stranded": [{"id": "ctx-canvas-03", "status": "validating", "title": "x",
                      "unmerged_commits": 5, "branch": "kanban/ctx-canvas-03"}],
        "orphan_validating": [{"id": "old-1", "status": "validating", "title": "y"}],
    }
    ids = sa._file_suggested_cards(findings)
    assert ids == ["kph-stranded-ctx-canvas-03", "kph-orphan-old-1"]
    specs = captured["specs"]
    assert all(s["status"] == "suggested" for s in specs)
    assert all(s["idempotency_key"].startswith("stranded-audit-") for s in specs)
    assert specs[0]["title"].startswith("[STRANDED]")
