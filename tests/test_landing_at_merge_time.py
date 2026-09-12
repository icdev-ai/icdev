# CUI // SP-CTI
"""autonomy-act-08 — the merge ledger learns about a landing AT MERGE TIME.

The ledger row (``change landed on main: <task-id>`` carrying
``source: kanban_merge_ledger``) is the door-agnostic record autonomy-act-06
built, and ``detector_findings.ledger_landing`` is its consumer: it is the ONLY
way a ``land.py`` merge is visible to the record-not-card ordering rule, because
``pr_watcher.merge`` is written by the watcher's own loop and by nothing else.

Until this card the row's only writer was a 6-hourly reflex backfill. Measured
2026-09-12 over the 625 landings of the preceding 30 days the row arrived p50
3.5h / p95 9.2h / max 96.8h after the landing it describes — the six hours are
the reflex CADENCE, the tail is four days — and on 2026-09-03 ``rmf-ui-13``
landed at 18:43, had its detector card promoted at 20:11 and got its ledger row
at 22:40. A worker session was spent on delivered work inside that gap.

What is pinned here, in order:

  1. the door writes a row ``ledger_landing`` ACCEPTS — asserted through
     ``ledger_landing`` itself, not by re-reading the columns this module wrote
  2. idempotence in BOTH directions against the 6-hourly sweep, through the
     EXISTING ``emitted_task_ids`` dedupe
  3. it does not widen what counts as a landing
  4. ``landing_latency`` measures the lag the row cannot report about itself

Schema comes from the shipped DDL, and connections from
``tools.db.storage.get_connection``, for the reasons
``tests/test_idp_delivery_events.py`` states at length: a raw ``sqlite3``
connection would not translate the ``%s`` placeholders and these tests would
assert their own no-op.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.idp.delivery_events import (  # noqa: E402
    DEPLOY_EVENT_TYPE,
    SOURCE,
    collect_changes,
    emit_landing,
    emitted_task_ids,
    landing_latency,
    sync_delivery_events,
)
from tools.kanban.detector_findings import (  # noqa: E402
    LEDGER_ACTION_PREFIX,
    ledger_landing,
)

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _naive(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat()


def _schema_block(table: str) -> str:
    from tools.db.init_icdev_db import SCHEMA_SQL

    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {table} \(.*?\n\);", SCHEMA_SQL, re.S
    )
    assert match, f"{table} is no longer declared in init_icdev_db.SCHEMA_SQL"
    return match.group(0)


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from tools.db.storage import get_connection

    connection = get_connection(db_path=str(tmp_path / "landing.db"))

    from tools.kanban.init_db import init_kanban_tables

    init_kanban_tables(conn=connection)
    connection.executescript(
        "\n".join(
            _schema_block(table)
            for table in ("projects", "audit_trail", "ci_pipeline_runs")
        )
    )
    connection.commit()
    yield connection
    try:
        connection.close()
    except Exception:  # noqa: BLE001
        pass


def _landed(conn, task_id: str, *, hours_ago: float = 0.0, status: str = "done"):
    """A task the board says reached main ``hours_ago``."""
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, task_type, status, created_at, "
        "scheduled_at, completed_at, files_changed) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            task_id,
            f"title for {task_id}",
            "build",
            status,
            _naive(NOW - timedelta(days=3)),
            _naive(NOW - timedelta(hours=hours_ago + 4)),
            None if status != "done" else _naive(NOW - timedelta(hours=hours_ago)),
            2,
        ),
    )
    conn.commit()


def _ledger_rows(conn) -> list[dict]:
    """The rows ``ledger_landing`` is handed, in the shape it reads them."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT action, details AS d, created_at FROM audit_trail "
            "WHERE action LIKE %s ORDER BY created_at",
            (LEDGER_ACTION_PREFIX + "%",),
        ).fetchall()
    ]


#: Any event type OUTSIDE the ledger's two. A member of VALID_EVENT_TYPES, so
#: the real CHECK constraint lifted from the shipped DDL admits it.
FOREIGN_EVENT_TYPE = "project_updated"


def _audit(conn, event_type: str, action: str, created_at: datetime):
    """A foreign audit row — what brackets an emission in ``landing_latency``."""
    conn.execute(
        "INSERT INTO audit_trail (event_type, actor, action, details, classification, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (event_type, "someone-else", action, "{}", "CUI", _naive(created_at)),
    )
    conn.commit()


# ── 1. the row the ordering rule accepts ────────────────────────────────────


def test_the_door_writes_a_row_ledger_landing_accepts(conn):
    """The consumer is the judge. ``ledger_landing`` is asked, not re-derived."""
    _landed(conn, "t-landed", hours_ago=0.0)
    escalated_at = NOW - timedelta(hours=2)

    before = ledger_landing(_ledger_rows(conn), "t-landed", escalated_at=escalated_at)
    assert before["measurable"] is True and before["landed"] is False

    out = emit_landing("t-landed", conn=conn)
    assert out["emitted"] is True and out["state"] == "recorded"

    after = ledger_landing(_ledger_rows(conn), "t-landed", escalated_at=escalated_at)
    assert after["landed"] is True, (
        "the door wrote a row the ordering rule does not accept — the whole "
        "point is that nothing downstream learns a new shape")
    assert after["ledger_rows"] == 1


def test_the_row_carries_the_ledgers_source_not_a_new_one(conn):
    """``event_type`` is a shared vocabulary word; ``source`` is what names the
    ledger, and ``ledger_landing`` re-checks it."""
    _landed(conn, "t-source")
    emit_landing("t-source", conn=conn)

    row = dict(conn.execute(
        "SELECT event_type, action, details FROM audit_trail WHERE action LIKE %s",
        (LEDGER_ACTION_PREFIX + "%",)).fetchone())
    assert row["event_type"] == DEPLOY_EVENT_TYPE
    assert row["action"] == f"{LEDGER_ACTION_PREFIX}t-source"
    assert json.loads(row["details"])["source"] == SOURCE


def test_the_door_does_not_write_a_pr_watcher_row(conn):
    """`pr_watcher.merge` is the WATCHER's own audit vocabulary (autonomy-act-08
    acceptance criterion 4). A second writer of it makes every pr_watcher
    survey wrong."""
    _landed(conn, "t-vocab")
    emit_landing("t-vocab", conn=conn)

    actions = [dict(r)["action"] for r in conn.execute(
        "SELECT action FROM audit_trail").fetchall()]
    assert not [a for a in actions if a.startswith("pr_watcher.")]


# ── 2. idempotence against the 6-hourly sweep, both directions ──────────────


def test_the_door_does_not_write_twice(conn):
    _landed(conn, "t-twice")
    first = emit_landing("t-twice", conn=conn)
    second = emit_landing("t-twice", conn=conn)

    assert first["emitted"] is True
    assert second["emitted"] is False and second["state"] == "already"
    assert len(_ledger_rows(conn)) == 1


def test_the_sweep_does_not_re_emit_what_the_door_recorded(conn):
    """Door first, sweep second — the 6-hourly backfill must add nothing."""
    _landed(conn, "t-door-first")
    emit_landing("t-door-first", conn=conn)

    summary = sync_delivery_events(days=90, conn=conn)
    assert summary["deploy_events"] == 0
    assert summary["already_emitted"] == 1
    assert len(_ledger_rows(conn)) == 1


def test_the_door_does_not_re_emit_what_the_sweep_recorded(conn):
    """Sweep first, door second — the same dedupe read from the other side."""
    _landed(conn, "t-sweep-first")
    sync_delivery_events(days=90, conn=conn)

    out = emit_landing("t-sweep-first", conn=conn)
    assert out["emitted"] is False and out["state"] == "already"
    assert len(_ledger_rows(conn)) == 1


def test_dedupe_is_the_existing_set_not_a_second_key(conn):
    """``emitted_task_ids`` is the one dedupe, and the door joins it."""
    _landed(conn, "t-set")
    assert emitted_task_ids(conn) == set()
    emit_landing("t-set", conn=conn)
    assert emitted_task_ids(conn) == {"t-set"}


# ── 3. it does not widen what counts as a landing ───────────────────────────


def test_a_task_that_has_not_landed_gets_no_row(conn):
    _landed(conn, "t-open", status="in_progress")
    out = emit_landing("t-open", conn=conn)
    assert out["emitted"] is False and out["state"] == "skipped"
    assert _ledger_rows(conn) == []


def test_a_task_that_is_not_on_the_board_gets_no_row(conn):
    out = emit_landing("t-absent", conn=conn)
    assert out["emitted"] is False and out["state"] == "skipped"
    assert _ledger_rows(conn) == []


def test_the_door_and_the_sweep_derive_the_same_change(conn):
    """One derivation, not two: the door narrows ``collect_changes``, it does
    not re-project the row."""
    _landed(conn, "t-same", hours_ago=1.0)
    _landed(conn, "t-other", hours_ago=1.0)

    narrowed = collect_changes(conn, days=90, task_ids=["t-same"])
    full = [c for c in collect_changes(conn, days=90) if c["task_id"] == "t-same"]
    assert narrowed == full

    emit_landing("t-same", conn=conn)
    row = dict(conn.execute(
        "SELECT created_at, details FROM audit_trail WHERE action = %s",
        (f"{LEDGER_ACTION_PREFIX}t-same",)).fetchone())
    payload = json.loads(row["details"])
    assert payload["landed_at"] == _naive(NOW - timedelta(hours=1.0))
    assert row["created_at"] == payload["landed_at"], (
        "the row must still be stamped at the landing, not at emission — that "
        "is what deploy_frequency counts")


def test_an_empty_id_list_selects_nothing(conn):
    """A filter that falls back to 'everything' when it is empty is how a
    one-task door emits the whole board."""
    _landed(conn, "t-nothing")
    assert collect_changes(conn, days=90, task_ids=[]) == []


# ── 4. the latency the row cannot report about itself ───────────────────────


def test_latency_brackets_a_backfilled_row_by_the_next_foreign_row(conn):
    """A ledger row is stamped at the landing, so its own timestamp says the
    ledger is always current. The next non-ledger audit id is what bounds it."""
    _landed(conn, "t-late", hours_ago=5.0)
    emit_landing("t-late", conn=conn)
    # Someone else writes an audit row half an hour later: the emission happened
    # at or before that moment, so the landing was 5.5h stale at worst.
    _audit(conn, FOREIGN_EVENT_TYPE, "something else happened",
           NOW + timedelta(minutes=30))

    out = landing_latency(days=30, conn=conn)
    assert out["measured"] == 1 and out["unbracketed"] == 0
    assert 5.4 <= out["lag_hours"]["max"] <= 5.6
    assert out["buckets"]["3-6h"] == 1 and out["buckets"]["6-12h"] == 0


def test_latency_reports_an_at_merge_time_row_as_such(conn):
    """The fix's own verification: a door-written row is bracketed at ~zero."""
    _landed(conn, "t-prompt", hours_ago=0.0)
    emit_landing("t-prompt", conn=conn)
    _audit(conn, FOREIGN_EVENT_TYPE, "a moment later", NOW + timedelta(seconds=20))

    out = landing_latency(days=30, conn=conn)
    assert out["at_merge_time"] == 1
    assert out["buckets"]["<1m"] == 1


def test_latency_counts_what_it_cannot_bracket_rather_than_dropping_it(conn):
    """A survey that silently discards unmeasurable rows reports a cleaner
    distribution than the one it observed."""
    _landed(conn, "t-unbracketed")
    emit_landing("t-unbracketed", conn=conn)

    out = landing_latency(days=30, conn=conn)
    assert out["ledger_rows"] == 1
    assert out["measured"] == 0 and out["unbracketed"] == 1
    assert "lag_hours" not in out


def test_latency_is_honest_about_an_empty_window(conn):
    out = landing_latency(days=30, conn=conn)
    assert out["ledger_rows"] == 0 and out["measured"] == 0
    assert "no merge-ledger rows" in out["reason"]
