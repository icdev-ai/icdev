# CUI // SP-CTI
"""autonomy-act-08 — the `--merge` door records the landing itself.

`tools/kanban/cli.py --set-status <id> done --merge` (mfx-mrg-04) is a merge
door: it reuses `PRWatcher._auto_merge` to land the PR and then writes `done`.
It wrote no record of the landing that any door-agnostic consumer could read, so
the only trace was the 6-hourly merge-ledger backfill — measured 2026-09-12 at
p50 3.5h / p95 9.2h / max 96.8h behind the landing it describes.

`tests/test_landing_at_merge_time.py` pins the writer. This file pins the
WIRING, and the three properties the wiring must have:

  * the door calls the ONE writer (not a copy of the row)
  * a ledger failure cannot un-write a confirmed merge — the `done` row stands
    and the 6-hourly sweep remains the backstop
  * no other door acquires a landing row by accident: `--force-done` and a
    plain `done` are not merges, and this card did not widen them
"""
from __future__ import annotations

import importlib

import pytest

cli = importlib.import_module("tools.kanban.cli")
kb = importlib.import_module("tools.genesis.reflexes.kanban")
delivery = importlib.import_module("tools.idp.delivery_events")

PR = "https://github.com/icdev-ai/ICDev/pull/4321"


def _merged(tid, **_kw):
    return {"task_id": tid, "ok": True, "merged": True, "pr_url": PR,
            "reason": "merged and confirmed", "checks": []}


def _seed_task(task_id, status="in_progress"):
    from tools.kanban.init_db import init_kanban_tables

    init_kanban_tables()
    with cli.get_connection() as conn:
        conn.execute("DELETE FROM kanban_tasks WHERE id = %s", (task_id,))
        conn.execute(
            "INSERT INTO kanban_tasks (id, title, description, status, priority, "
            "task_type, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (task_id, "landing record test", "d", status, "high", "build",
             cli._now(), cli._now()),
        )
    return task_id


@pytest.fixture()
def spy(monkeypatch):
    """Watch the one writer without touching audit_trail."""
    calls: list = []

    def _emit(task_id, *_a, **_kw):
        calls.append(task_id)
        return {"task_id": task_id, "emitted": True, "state": "recorded",
                "why": "landing recorded on the merge ledger at merge time"}

    monkeypatch.setattr(delivery, "emit_landing", _emit)
    return calls


def test_the_merge_door_records_the_landing(spy, capsys):
    task_id = _seed_task("t-landing-merge")
    rc = cli.cmd_set_status([task_id], "done", json_out=False, merge=True,
                            lander=_merged)
    assert rc == 0
    assert spy == [task_id], (
        "the --merge door landed a PR and told the merge ledger nothing — the "
        "ordering rule stays blind until the 6-hourly sweep")
    assert "landing: recorded" in capsys.readouterr().out


def test_the_landing_is_recorded_after_done_is_committed(monkeypatch):
    """The board row IS the evidence: emitting before `done` is written would
    derive nothing, so the call must come after the status write."""
    task_id = _seed_task("t-landing-order")
    seen: list = []

    def _emit(tid, *_a, **_kw):
        with cli.get_connection() as conn:
            row = conn.execute(
                "SELECT status, completed_at FROM kanban_tasks WHERE id = %s",
                (tid,)).fetchone()
        seen.append(dict(row))
        return {"task_id": tid, "emitted": True, "state": "recorded", "why": ""}

    monkeypatch.setattr(delivery, "emit_landing", _emit)
    assert cli.cmd_set_status([task_id], "done", json_out=False, merge=True,
                              lander=_merged) == 0
    assert seen and seen[0]["status"] == "done" and seen[0]["completed_at"]


def test_a_failed_ledger_write_does_not_un_write_the_merge(monkeypatch, capsys):
    """Best-effort, like the lease release beside it: the `done` row is already
    written, the merge already happened, and the sweep is still the backstop."""
    task_id = _seed_task("t-landing-boom")

    def _boom(_task_id, *_a, **_kw):
        raise RuntimeError("audit_trail is unreachable")

    monkeypatch.setattr(delivery, "emit_landing", _boom)
    rc = cli.cmd_set_status([task_id], "done", json_out=False, merge=True,
                            lander=_merged)
    assert rc == 0
    with cli.get_connection() as conn:
        row = dict(conn.execute(
            "SELECT status FROM kanban_tasks WHERE id = %s", (task_id,)).fetchone())
    assert row["status"] == "done"
    assert "landing: error" in capsys.readouterr().out, "the failure was silent"


def test_a_refused_merge_records_no_landing(spy):
    task_id = _seed_task("t-landing-refused")
    rc = cli.cmd_set_status(
        [task_id], "done", json_out=False, merge=True,
        lander=lambda tid, **kw: {"task_id": tid, "ok": False, "merged": False,
                                  "pr_url": PR, "reason": "CI is red",
                                  "checks": []})
    assert rc == 1
    assert spy == [], "nothing landed, and a landing was recorded anyway"


@pytest.mark.parametrize("kwargs", [
    {},
    {"force_done": True, "reason": "landed elsewhere"},
])
def test_a_door_that_does_not_merge_records_no_landing(monkeypatch, spy, kwargs):
    """Scoped, deliberately: this card wires the doors that MERGE. A plain
    `done` and `--force-done` still reach the ledger through the 6-hourly
    sweep, and widening them is a separate decision with its own evidence."""
    task_id = _seed_task(f"t-landing-{'force' if kwargs else 'plain'}")
    monkeypatch.setattr(kb, "_branch_has_unmerged_commits", lambda _t: False)
    monkeypatch.setattr(cli, "_refuses_done", lambda _t: "")
    rc = cli.cmd_set_status([task_id], "done", json_out=False, **kwargs)
    assert rc == 0
    assert spy == []


def test_the_door_calls_the_one_writer_not_a_copy_of_the_row():
    """One writer function, not a copy per door (acceptance criterion 2)."""
    import inspect

    source = inspect.getsource(cli.cmd_set_status)
    assert "emit_landing" in source
    assert "change landed on main" not in source, (
        "the CLI is respelling the ledger row instead of calling the writer")
