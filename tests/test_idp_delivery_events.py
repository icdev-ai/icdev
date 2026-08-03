# CUI // SP-CTI
"""idp-intel-01 — the DORA endpoint gets real inputs, and stays honest without them.

``/api/sre/dora`` was measured returning ``metrics_assessed: 0``: correct code,
zero inputs. ``tools/idp/delivery_events.py`` derives those inputs from the
kanban merge ledger. The contract has two halves and both are pinned here:

  1. after a sync, keys backed by emitted events carry a real rating
  2. ``mttr`` — which reads ``sre_incidents``, a table this platform genuinely
     has no source for — still reports ``Not Assessed``, not a default rating

Half 2 is the one worth guarding. The easy way to make a dashboard look
populated is to relax the ``NOT_ASSESSED`` sentinel; ``test_mttr_stays_not_assessed``
fails if anyone does.

Schema comes from the shipped DDL — ``init_kanban_tables``,
``incident_commander.init_tables``, and the ``audit_trail`` / ``ci_pipeline_runs``
blocks lifted out of ``init_icdev_db.SCHEMA_SQL`` — rather than hand-written
CREATE TABLEs. That matters twice over: it catches an INSERT naming a column the
live schema lacks (the swallowed-INSERT bug class), and it carries
``audit_trail``'s real ``event_type`` CHECK, so an event type outside the
platform vocabulary fails here instead of being silently rejected in production.

Connections come from ``tools.db.storage.get_connection`` because the module
writes ``%s`` placeholders for PostgreSQL and only the storage wrapper
translates them for SQLite — a raw ``sqlite3`` connection would make these tests
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

import tools.dashboard.api.sre as sre_mod  # noqa: E402
from tools.idp.delivery_events import (  # noqa: E402
    DEPLOY_EVENT_TYPE,
    DEPLOY_FAILED_EVENT_TYPE,
    collect_changes,
    dora_input_status,
    emitted_task_ids,
    sync_delivery_events,
)

# Anchored to wall-clock, not a literal: the endpoint computes its own cutoff
# from `datetime.now()`, so a frozen NOW would silently drift out of the 30-day
# window and fail these tests months from now for a reason that is not a bug.
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _naive(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat()


def _schema_block(table: str) -> str:
    """Lift one CREATE TABLE block out of the shipped platform schema."""
    from tools.db.init_icdev_db import SCHEMA_SQL

    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {table} \(.*?\n\);", SCHEMA_SQL, re.S
    )
    assert match, f"{table} is no longer declared in init_icdev_db.SCHEMA_SQL"
    return match.group(0)


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    """Storage connection over a temp SQLite DB carrying the shipped schema."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from tools.db.storage import get_connection

    connection = get_connection(db_path=str(tmp_path / "delivery.db"))

    from tools.kanban.init_db import init_kanban_tables
    from tools.sre.incident_commander import init_tables as init_sre_tables

    init_kanban_tables(conn=connection)
    init_sre_tables(connection)
    # `projects` first — audit_trail.project_id carries a real FK to it, and
    # SQLite enforces foreign keys here, so omitting it turns every audit write
    # into "no such table: main.projects".
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


def _task(
    conn,
    task_id: str,
    *,
    landed_hours_ago: float = 2.0,
    dispatched_hours_ago: float | None = 4.0,
    created_days_ago: float = 20.0,
    status: str = "done",
    bypass: int = 0,
):
    conn.execute(
        "INSERT INTO kanban_tasks (id, title, task_type, status, created_at, scheduled_at, "
        "completed_at, completed_via_bypass, files_changed) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            task_id,
            f"title for {task_id}",
            "build",
            status,
            _naive(NOW - timedelta(days=created_days_ago)),
            None if dispatched_hours_ago is None else _naive(NOW - timedelta(hours=dispatched_hours_ago)),
            None if status != "done" else _naive(NOW - timedelta(hours=landed_hours_ago)),
            bypass,
            3,
        ),
    )
    conn.commit()


def _verify(conn, task_id: str, result: str, *, hours_ago: float = 3.0, suffix: str = ""):
    conn.execute(
        "INSERT INTO kanban_verifications (id, task_id, verified_at, result) VALUES (%s, %s, %s, %s)",
        (f"kv-{task_id}{suffix}", task_id, _naive(NOW - timedelta(hours=hours_ago)), result),
    )
    conn.commit()


# ── derivation ───────────────────────────────────────────────────────────────


def test_only_merged_tasks_become_deployments(conn):
    _task(conn, "t-done")
    _task(conn, "t-open", status="in_progress")

    changes = collect_changes(conn, days=90)
    assert [c["task_id"] for c in changes] == ["t-done"], (
        "a task that never reached done is not a change that landed"
    )


def test_latest_verification_decides_failure_not_the_first(conn):
    """A change that failed, was fixed, and then passed is not a failed deploy."""
    _task(conn, "t-recovered")
    _verify(conn, "t-recovered", "failed", hours_ago=6, suffix="-a")
    _verify(conn, "t-recovered", "passed", hours_ago=3, suffix="-b")

    _task(conn, "t-regressed")
    _verify(conn, "t-regressed", "passed", hours_ago=6, suffix="-a")
    _verify(conn, "t-regressed", "failed", hours_ago=3, suffix="-b")

    by_id = {c["task_id"]: c for c in collect_changes(conn, days=90)}
    assert by_id["t-recovered"]["failed"] is False
    assert by_id["t-regressed"]["failed"] is True


def test_bypassed_verification_is_not_a_change_failure(conn):
    """``bypassed`` means verification was skipped, not that the change failed.

    Counting it would inflate the change failure rate with unverified — not
    failed — deliveries, which is a different (and separately recorded) fact.
    """
    _task(conn, "t-bypassed", bypass=1)
    _verify(conn, "t-bypassed", "bypassed")

    change = collect_changes(conn, days=90)[0]
    assert change["failed"] is False
    assert change["completed_via_bypass"] is True, "the bypass is still recorded as provenance"


def test_backlog_wait_is_never_counted_as_lead_time(conn):
    """Work-start is the dispatch, not the day the card was filed."""
    _task(conn, "t-old-card", created_days_ago=45, dispatched_hours_ago=4, landed_hours_ago=2)

    change = collect_changes(conn, days=90)[0]
    assert change["started_at"] == NOW - timedelta(hours=4)


def test_first_verification_is_the_fallback_work_start(conn):
    _task(conn, "t-undispatched", dispatched_hours_ago=None)
    _verify(conn, "t-undispatched", "failed", hours_ago=9, suffix="-a")
    _verify(conn, "t-undispatched", "passed", hours_ago=3, suffix="-b")

    change = collect_changes(conn, days=90)[0]
    assert change["started_at"] == NOW - timedelta(hours=9)


# ── emission ─────────────────────────────────────────────────────────────────


def test_sync_emits_the_events_the_dora_query_reads(conn):
    _task(conn, "t-pass")
    _verify(conn, "t-pass", "passed")
    _task(conn, "t-fail")
    _verify(conn, "t-fail", "failed")

    summary = sync_delivery_events(days=90, conn=conn)

    assert summary["changes_in_window"] == 2
    assert summary["deploy_events"] == 2, "every landed change is one deployment"
    assert summary["failure_events"] == 1
    assert summary["pipeline_runs"] == 2

    deploys = conn.execute(
        "SELECT COUNT(*) AS n FROM audit_trail WHERE event_type = %s", (DEPLOY_EVENT_TYPE,)
    ).fetchone()
    failures = conn.execute(
        "SELECT COUNT(*) AS n FROM audit_trail WHERE event_type = %s", (DEPLOY_FAILED_EVENT_TYPE,)
    ).fetchone()
    assert dict(deploys)["n"] == 2
    assert dict(failures)["n"] == 1


def test_event_is_stamped_when_the_change_landed_not_when_it_was_backfilled(conn):
    """A backfill that stamped `now` would pile a quarter's deploys into one day."""
    _task(conn, "t-old", landed_hours_ago=30 * 24)
    sync_delivery_events(days=90, conn=conn)

    row = dict(
        conn.execute(
            "SELECT created_at AS c FROM audit_trail WHERE event_type = %s", (DEPLOY_EVENT_TYPE,)
        ).fetchone()
    )
    assert str(row["c"]).startswith(_naive(NOW - timedelta(hours=30 * 24))[:10])


def test_sync_is_idempotent(conn):
    _task(conn, "t-1")
    _verify(conn, "t-1", "failed")

    first = sync_delivery_events(days=90, conn=conn)
    second = sync_delivery_events(days=90, conn=conn)

    assert first["deploy_events"] == 1
    assert second["deploy_events"] == 0, "a re-run must not double-count a deployment"
    assert second["already_emitted"] == 1
    assert emitted_task_ids(conn) == {"t-1"}

    total = dict(conn.execute("SELECT COUNT(*) AS n FROM audit_trail").fetchone())["n"]
    assert total == 2, "one deploy + one failure event, not four"


def test_incremental_sync_picks_up_only_the_new_change(conn):
    _task(conn, "t-old")
    sync_delivery_events(days=90, conn=conn)

    _task(conn, "t-new")
    summary = sync_delivery_events(days=90, conn=conn)

    assert summary["deploy_events"] == 1
    assert summary["already_emitted"] == 1


def test_unknown_lead_time_is_reported_not_invented(conn):
    """No dispatch and no verification ⇒ a deploy event but no pipeline row."""
    _task(conn, "t-no-start", dispatched_hours_ago=None)

    summary = sync_delivery_events(days=90, conn=conn)

    assert summary["deploy_events"] == 1
    assert summary["pipeline_runs"] == 0
    assert summary["no_start_signal"] == 1, "the gap is counted, not silently dropped"
    runs = dict(conn.execute("SELECT COUNT(*) AS n FROM ci_pipeline_runs").fetchone())["n"]
    assert runs == 0


def test_dry_run_writes_nothing(conn):
    _task(conn, "t-1")
    summary = sync_delivery_events(days=90, conn=conn, dry_run=True)

    assert summary["would_emit"] == 1
    assert summary["deploy_events"] == 0
    assert dict(conn.execute("SELECT COUNT(*) AS n FROM audit_trail").fetchone())["n"] == 0


def test_details_carry_the_provenance_for_the_classification(conn):
    _task(conn, "t-fail", bypass=1)
    _verify(conn, "t-fail", "failed")
    sync_delivery_events(days=90, conn=conn)

    row = dict(
        conn.execute(
            "SELECT details AS d FROM audit_trail WHERE event_type = %s", (DEPLOY_FAILED_EVENT_TYPE,)
        ).fetchone()
    )
    payload = json.loads(row["d"])
    assert payload["task_id"] == "t-fail"
    assert payload["verification_result"] == "failed"
    assert payload["completed_via_bypass"] is True
    assert payload["source"] == "kanban_merge_ledger"


def test_input_status_reports_the_incident_gap(conn):
    _task(conn, "t-1")
    sync_delivery_events(days=90, conn=conn)

    status = dora_input_status(days=90, conn=conn)
    assert status["deploy_events"] == 1
    assert status["completed_pipeline_runs"] == 1
    assert status["resolved_incidents"] == 0, "no incident ledger exists — say so"


# ── the endpoint, end to end ─────────────────────────────────────────────────


def _dora(conn, monkeypatch, days: int = 30):
    """Call the real endpoint against the seeded connection."""
    from flask import Flask

    monkeypatch.setattr(sre_mod, "get_connection", lambda *a, **k: conn)
    # The endpoint closes the connection it is handed; keep the fixture usable.
    monkeypatch.setattr(conn, "close", lambda: None, raising=False)

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(sre_mod.sre_api)
    with app.test_client() as client:
        resp = client.get(f"/api/sre/dora?days={days}")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        return resp.get_json()


def test_dora_baseline_has_nothing_to_measure(conn, monkeypatch):
    """The measured starting point: correct code, no inputs.

    Measured on the live database 2026-08-02: ``metrics_assessed: 1``. Three of
    the four keys are ``Not Assessed``; the fourth reports zero deploys per day
    as ``Low``, which is a real measurement of zero rather than an assessment of
    delivery — and, critically, never gets banded favourably.
    """
    _task(conn, "t-1")
    _verify(conn, "t-1", "passed")

    dora = _dora(conn, monkeypatch)
    assert dora["deploy_frequency"]["total_deploys"] == 0
    assert dora["deploy_frequency"]["rating"] == "Low"
    for key in ("lead_time", "change_failure_rate", "mttr"):
        assert dora[key]["rating"] == "Not Assessed", key
    assert dora["metrics_assessed"] == 1


def test_sync_moves_dora_keys_off_not_assessed(conn, monkeypatch):
    """The acceptance criterion: real emitted events produce real ratings."""
    for idx in range(10):
        _task(conn, f"t-{idx}", landed_hours_ago=2 + idx, dispatched_hours_ago=4 + idx)
        _verify(conn, f"t-{idx}", "passed", hours_ago=3 + idx)
    _task(conn, "t-bad")
    _verify(conn, "t-bad", "failed")

    sync_delivery_events(days=90, conn=conn)
    dora = _dora(conn, monkeypatch)

    assert dora["deploy_frequency"]["rating"] != "Not Assessed"
    assert dora["deploy_frequency"]["total_deploys"] == 11
    assert dora["lead_time"]["rating"] != "Not Assessed"
    assert dora["lead_time"]["samples"] == 11
    assert dora["change_failure_rate"]["rating"] != "Not Assessed"
    # 1 failure / 11 deploys = 9.1% — hand-verifiable, no rounding surprise.
    assert dora["change_failure_rate"]["value"] == pytest.approx(9.1, abs=0.05)
    assert dora["metrics_assessed"] == 3
    assert dora["overall_rating"] != "Not Assessed"


def test_mttr_stays_not_assessed(conn, monkeypatch):
    """The half of the contract that must NOT move.

    ``mttr`` reads ``sre_incidents``. This platform has no production incident
    ledger, so the honest answer is ``Not Assessed`` — and it stays that way
    after a full sync. Making this dashboard tile look populated by relaxing the
    sentinel, or by projecting bug tasks into ``sre_incidents``, breaks here.
    """
    for idx in range(5):
        _task(conn, f"t-{idx}")
        _verify(conn, f"t-{idx}", "passed")
    sync_delivery_events(days=90, conn=conn)

    dora = _dora(conn, monkeypatch)
    assert dora["mttr"]["rating"] == "Not Assessed"
    assert dora["mttr"]["value"] is None
    assert dora["mttr"]["incidents_resolved"] == 0
    assert dora["metrics_assessed"] == 3, "three assessed keys, never a fourth by default"


def test_no_data_outside_the_window_still_reads_not_assessed(conn, monkeypatch):
    """Events older than the requested window must not leak into a rating."""
    _task(conn, "t-ancient", landed_hours_ago=80 * 24, dispatched_hours_ago=81 * 24)
    _verify(conn, "t-ancient", "passed", hours_ago=80 * 24)
    sync_delivery_events(days=90, conn=conn)

    dora = _dora(conn, monkeypatch, days=7)
    assert dora["deploy_frequency"]["total_deploys"] == 0
    assert dora["deploy_frequency"]["rating"] == "Low", (
        "an empty window is a real zero — rated Low, never Elite"
    )
    assert dora["change_failure_rate"]["rating"] == "Not Assessed"
    assert dora["lead_time"]["rating"] == "Not Assessed"
    assert dora["mttr"]["rating"] == "Not Assessed"
    assert dora["metrics_assessed"] == 1


# ── the scorecard renderer ────────────────────────────────────────────────────


def _sre_template(root: Path) -> str:
    return (root / "tools" / "dashboard" / "templates" / "sre" / "dashboard.html").read_text(
        encoding="utf-8"
    )


def test_unassessed_metric_is_not_rendered_as_a_number():
    """A null value must never reach the card formatter.

    The formatters are arithmetic: MTTR's is
    ``v.value < 3600 ? Math.round(v.value/60) + ' min' : ...``, and JavaScript
    coerces ``null`` to 0 in both the comparison and the division — so an
    unassessed MTTR rendered as **"0 min"**, a perfect score, directly under its
    own "Not Assessed" badge. The other three rendered the literal "null".

    That is the same defect the endpoint's NOT_ASSESSED sentinel exists to
    prevent, one layer up: absence of measurement displayed as a favourable
    measurement.
    """
    template = _sre_template(ROOT)
    metrics_block = template.split("const metrics = [", 1)[1].split("// SLOs", 1)[0]

    assert "m.data.value == null" in metrics_block, (
        "the card renderer must branch on a null value before calling fmt()"
    )
    assert "Not measured" in metrics_block
    # The rating pill class must survive a two-word rating; `pill-not assessed`
    # is two classes, neither of which is styled.
    assert r"""replace(/\s+/g, "-")""" in metrics_block or r"""replace(/\s+/g, '-')""" in metrics_block
    assert ".pill-not-assessed" in template, "the neutral pill style must exist"


def test_sre_template_mirror_is_in_sync():
    """The icdev/ twin ships to generated child apps — drift means they render
    the old, lying card while the platform renders the fixed one."""
    canonical = _sre_template(ROOT)
    mirror = (
        ROOT / "icdev" / "tools" / "dashboard" / "templates" / "sre" / "dashboard.html"
    ).read_text(encoding="utf-8")
    assert canonical == mirror, "tools/ and icdev/ sre/dashboard.html have diverged"
