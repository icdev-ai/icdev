# CUI // SP-CTI
"""A union_candidate card closed before its refusal rows age out is HELD,
not re-filed (task-ucand-clear-01).

`union_candidates` reads `pr_watcher.union_refused` rows over a window
(720h by default), so a candidate CANNOT leave the report before
`last_refused_at + window_hours` whatever anybody does upstream. Measured
2026-10-07: finding 06d08d9b92d6d39f (`apps/forge_academy/content_loader.py`)
was repaired on main, its card was closed, and a `-r2` was filed and
DISPATCHED against a subject already delivered -- the defect task-f05d2bc8d1
fixed for `recovery` findings, in a second detector. Same rule, same hold.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tools.db.storage import get_connection
from tools.kanban import detector_findings as df
from tools.kanban import task_factory as tf
from tools.kanban import union_candidates as uc

PATH = "apps/forge_academy/content_loader.py"


def _report(last_refused_at, window_hours=720):
    return {
        "measurable": True, "window_hours": window_hours,
        "candidates": [{
            "file": PATH, "state": uc.STATE_MEASURABLE,
            "recommendation": uc.RECOMMEND_DO_NOT, "shape": uc.SHAPE_REWRITES_BASE,
            "refusals": 3, "hunks": 2, "hunks_rewriting_base": 1,
            "decisive_hunks": 2, "agreeing": 1, "lost_content": 4,
            "declaration": None, "last_refused_at": last_refused_at,
        }],
    }


def _finding(last_refused_at, window_hours=720):
    (f,) = df.union_candidate_findings(_report(last_refused_at, window_hours))
    return f


@pytest.fixture
def conn(icdev_db):
    c = get_connection(str(icdev_db))
    yield c
    c.close()


@pytest.fixture
def seeded(monkeypatch):
    monkeypatch.setattr(tf, "create_tasks", lambda specs, **_kw: [s["id"] for s in specs])


def test_union_candidate_findings_carry_last_refused_plus_window():
    at = datetime(2026, 10, 6, 10, 38, 17, tzinfo=timezone.utc)
    assert _finding(at.isoformat())["earliest_clear_at"] == (
        at + timedelta(hours=720)).isoformat()
    # a driver datetime (PG) reads the same as a string stamp (SQLite)
    assert _finding(at, window_hours=24)["earliest_clear_at"] == (
        at + timedelta(hours=24)).isoformat()
    # no readable stamp, or a LIFETIME window, claims no clear time
    assert _finding(None)["earliest_clear_at"] is None
    assert _finding(at.isoformat(), window_hours=None)["earliest_clear_at"] is None


def test_card_closed_before_refusals_age_out_is_held_not_refiled(conn, seeded):
    now = datetime.now(timezone.utc)
    f = _finding((now - timedelta(hours=26)).isoformat())
    earliest = datetime.fromisoformat(f["earliest_clear_at"])
    assert earliest > now
    runners = {df.DETECTOR_UNION_CANDIDATE: lambda c, cfg: df._result("findings", [f])}

    card = df.consume({}, conn=conn, runners=runners)["cards_seeded"][0]
    conn.execute("INSERT INTO kanban_tasks (id, title, status) VALUES (?, ?, ?)",
                 (card, "repaired upstream", "done"))
    conn.commit()

    again = df.consume({}, conn=conn, runners=runners)
    assert again["cards_seeded"] == [] and again["findings_recurring"] == 0
    assert again["detectors"][df.DETECTOR_UNION_CANDIDATE]["held_closed_early"] == 1
    assert not conn.execute("SELECT id FROM kanban_tasks WHERE id = ?",
                            (card + "-r2",)).fetchall()

    # a MEASURABLE run past that instant that still reports it IS a recurrence
    later = df.consume({}, conn=conn, runners=runners, now=earliest + timedelta(minutes=1))
    assert later["findings_recurring"] == 1
    assert later["cards_seeded"] == [df.card_id_for(f["finding_id"], 2)]
