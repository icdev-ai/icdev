# CUI // SP-CTI
"""Tests for reading ``runtime_invocations`` — the rollup arithmetic above all.

Split deliberately in two:

  * ``rollup_by_surface`` is a pure fold, so its arithmetic is tested against
    hand-computed numbers with no database in sight. That is the layer where a
    weighted mean can quietly become an unweighted one, and a DB fixture would
    only obscure it.
  * The SQL rollup is then tested against a real SQLite database to prove the
    columns the fold consumes are the columns the query produces — a pure test
    of the fold alone would pass even if ``timed`` were never selected.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from tools.observability import invocation_recorder as R
from tools.observability.invocation_store import (
    InvocationFilter,
    InvocationStore,
    error_rate,
    rollup_by_surface,
)

# Resolved by GLOB, not by a pinned number: this migration has already been
# renumbered three times (329 -> 333 -> 341) and each rename broke a test that
# hardcoded the path.
_MIGRATION = next(
    (Path(__file__).resolve().parent.parent / "tools/db/migrations")
    .glob("*_runtime_invocations/up.py")
)


@pytest.fixture()
def obs_db(tmp_path, monkeypatch):
    """Isolated SQLite DB with the runtime_invocations migration applied."""
    db = tmp_path / "obs.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    monkeypatch.delenv("ICDEV_OBS_INVOCATIONS", raising=False)
    monkeypatch.setattr(R, "_table_missing", False, raising=False)

    import tools.db.storage as storage

    monkeypatch.setattr(storage, "DB_PATH", str(db), raising=False)
    monkeypatch.setattr(storage, "_BACKEND", "sqlite", raising=False)

    spec = importlib.util.spec_from_file_location("m_runtime_inv", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.up()
    return db


def _insert(surface, name, *, status="ok", duration_ms=None, started_at="2026-08-01T00:00:00"):
    """Write a row directly, so a test can pin an exact duration.

    ``record()`` measures wall-clock time, which cannot produce the fixed
    durations that arithmetic assertions need. The id is a fresh uuid because
    several tests deliberately insert IDENTICAL invocations to check that they
    are COUNTED rather than collapsed.
    """
    import uuid

    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO runtime_invocations "
            "(id, surface, name, started_at, status, duration_ms) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (f"inv-{uuid.uuid4().hex[:16]}",
             surface, name, started_at, status, duration_ms),
        )
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------- pure rollup arithmetic

def test_rollup_sums_calls_and_errors_per_surface():
    rows = [
        {"surface": "mcp", "name": "a", "calls": 10, "errors": 1, "timed": 10,
         "avg_ms": 100.0, "max_ms": 500.0},
        {"surface": "mcp", "name": "b", "calls": 5, "errors": 4, "timed": 5,
         "avg_ms": 20.0, "max_ms": 30.0},
        {"surface": "agent", "name": "c", "calls": 2, "errors": 0, "timed": 2,
         "avg_ms": 900.0, "max_ms": 1000.0},
    ]
    by_surface = {s["surface"]: s for s in rollup_by_surface(rows)}

    assert by_surface["mcp"]["calls"] == 15
    assert by_surface["mcp"]["errors"] == 5
    assert by_surface["mcp"]["names"] == 2
    assert by_surface["agent"]["calls"] == 2
    assert by_surface["agent"]["names"] == 1


def test_rollup_average_is_weighted_by_call_volume():
    """The whole point of the fold: a mean of means is the wrong answer.

    Unweighted, (100 + 20) / 2 = 60.0. Weighted by the 10 and 5 invocations
    those averages came from, (100*10 + 20*5) / 15 = 73.33 — which is the true
    mean over all 15 timed invocations.
    """
    rows = [
        {"surface": "mcp", "name": "a", "calls": 10, "errors": 0, "timed": 10,
         "avg_ms": 100.0, "max_ms": 500.0},
        {"surface": "mcp", "name": "b", "calls": 5, "errors": 0, "timed": 5,
         "avg_ms": 20.0, "max_ms": 30.0},
    ]
    avg = rollup_by_surface(rows)[0]["avg_ms"]
    assert avg == pytest.approx((100.0 * 10 + 20.0 * 5) / 15)
    assert avg != pytest.approx(60.0), "the mean of means slipped through"


def test_rollup_weights_by_timed_rows_not_by_calls():
    """A name with in-flight rows has fewer timed rows than calls.

    ``a`` was called 10 times but only 2 have finished, so its average of
    1000ms speaks for 2 invocations, not 10. Weighting by ``calls`` would give
    (1000*10 + 10*10)/20 = 505; weighting by ``timed`` gives the honest
    (1000*2 + 10*10)/12 = 175.
    """
    rows = [
        {"surface": "mcp", "name": "a", "calls": 10, "errors": 0, "timed": 2,
         "avg_ms": 1000.0, "max_ms": 1000.0},
        {"surface": "mcp", "name": "b", "calls": 10, "errors": 0, "timed": 10,
         "avg_ms": 10.0, "max_ms": 10.0},
    ]
    rolled = rollup_by_surface(rows)[0]
    assert rolled["timed"] == 12
    assert rolled["avg_ms"] == pytest.approx((1000.0 * 2 + 10.0 * 10) / 12)
    assert rolled["calls"] == 20, "calls must still count every invocation"


def test_rollup_max_is_the_true_max_not_the_last_seen():
    rows = [
        {"surface": "mcp", "name": "a", "calls": 1, "errors": 0, "timed": 1,
         "avg_ms": 5.0, "max_ms": 9000.0},
        {"surface": "mcp", "name": "b", "calls": 1, "errors": 0, "timed": 1,
         "avg_ms": 5.0, "max_ms": 12.0},
    ]
    assert rollup_by_surface(rows)[0]["max_ms"] == 9000.0


def test_rollup_reports_none_when_nothing_has_completed():
    """"Nothing finished" and "everything finished instantly" differ."""
    rows = [{"surface": "mcp", "name": "a", "calls": 3, "errors": 0, "timed": 0,
             "avg_ms": None, "max_ms": None}]
    rolled = rollup_by_surface(rows)[0]
    assert rolled["calls"] == 3
    assert rolled["avg_ms"] is None and rolled["max_ms"] is None


def test_rollup_error_rate_is_a_fraction_of_calls():
    rows = [{"surface": "mcp", "name": "a", "calls": 8, "errors": 2, "timed": 8,
             "avg_ms": 1.0, "max_ms": 1.0}]
    assert rollup_by_surface(rows)[0]["error_rate"] == pytest.approx(0.25)


def test_error_rate_of_zero_calls_is_zero_not_a_crash():
    assert error_rate(0, 0) == 0.0


def test_rollup_orders_surfaces_by_call_volume():
    rows = [
        {"surface": "role", "name": "r", "calls": 1, "errors": 0, "timed": 1,
         "avg_ms": 1.0, "max_ms": 1.0},
        {"surface": "mcp", "name": "m", "calls": 99, "errors": 0, "timed": 1,
         "avg_ms": 1.0, "max_ms": 1.0},
    ]
    assert [s["surface"] for s in rollup_by_surface(rows)] == ["mcp", "role"]


def test_rollup_of_nothing_is_an_empty_list():
    assert rollup_by_surface([]) == []


def test_rollup_coerces_decimal_aggregates():
    """PostgreSQL returns Decimal from avg()/sum(); the fold must not choke."""
    from decimal import Decimal

    rows = [{"surface": "mcp", "name": "a", "calls": Decimal("4"),
             "errors": Decimal("1"), "timed": Decimal("4"),
             "avg_ms": Decimal("25.5"), "max_ms": Decimal("40")}]
    rolled = rollup_by_surface(rows)[0]
    assert rolled["calls"] == 4 and isinstance(rolled["calls"], int)
    assert rolled["avg_ms"] == pytest.approx(25.5)
    assert rolled["error_rate"] == pytest.approx(0.25)


# ------------------------------------------------- the SQL feeding that fold

def test_by_name_produces_the_columns_the_fold_consumes(obs_db):
    _insert("mcp", "rag_search", duration_ms=10)
    _insert("mcp", "rag_search", duration_ms=30)
    _insert("mcp", "rag_search", status="error", duration_ms=50)

    row = InvocationStore().by_name(InvocationFilter(surface="mcp"))[0]
    assert row["name"] == "rag_search"
    assert row["calls"] == 3
    assert row["errors"] == 1
    assert row["timed"] == 3
    assert row["avg_ms"] == pytest.approx(30.0)
    assert row["max_ms"] == pytest.approx(50.0)
    assert row["error_rate"] == pytest.approx(1 / 3)


def test_running_rows_count_as_calls_but_not_as_timed(obs_db):
    """A NULL duration must not be averaged in as a zero."""
    _insert("mcp", "slow_tool", duration_ms=100)
    _insert("mcp", "slow_tool", status="running", duration_ms=None)

    row = InvocationStore().by_name(InvocationFilter())[0]
    assert row["calls"] == 2, "an in-flight invocation is still a call"
    assert row["timed"] == 1
    assert row["avg_ms"] == pytest.approx(100.0), "NULL was averaged in as 0"


def test_by_surface_totals_are_not_truncated_by_limit(obs_db):
    """The headline number must cover the whole surface, not the top N.

    This is the bug the store exists to avoid: deriving totals from an
    already-truncated top-N list reports a fraction of the traffic as though
    it were all of it.
    """
    for i in range(5):
        for _ in range(i + 1):
            _insert("mcp", f"tool_{i}", duration_ms=10)
    # 1+2+3+4+5 = 15 calls across 5 names.

    store = InvocationStore()
    assert len(store.by_name(InvocationFilter(limit=2))) == 2
    totals = store.by_surface(InvocationFilter(limit=2))[0]
    assert totals["calls"] == 15
    assert totals["names"] == 5


def test_report_truncates_names_but_not_surfaces(obs_db):
    for i in range(4):
        _insert("mcp", f"tool_{i}", duration_ms=1)

    report = InvocationStore().report(InvocationFilter(limit=2))
    assert len(report["names"]) == 2
    assert report["total_names"] == 4
    assert report["surfaces"][0]["calls"] == 4


def test_surfaces_are_rolled_up_independently(obs_db):
    _insert("mcp", "t", duration_ms=10)
    _insert("agent", "a", status="error", duration_ms=20)
    _insert("persona", "p", duration_ms=30)
    _insert("role", "r", duration_ms=40)

    surfaces = {s["surface"]: s for s in InvocationStore().by_surface()}
    assert set(surfaces) == {"mcp", "agent", "persona", "role"}
    assert surfaces["agent"]["errors"] == 1
    assert surfaces["mcp"]["errors"] == 0


def test_filters_narrow_the_rollup(obs_db):
    _insert("mcp", "a", duration_ms=1, started_at="2026-08-01T00:00:00")
    _insert("mcp", "b", duration_ms=1, started_at="2026-08-05T00:00:00")
    _insert("agent", "a", duration_ms=1, started_at="2026-08-05T00:00:00")

    store = InvocationStore()
    assert len(store.by_name(InvocationFilter(surface="mcp"))) == 2
    assert len(store.by_name(InvocationFilter(name="a"))) == 2
    assert len(store.by_name(InvocationFilter(since="2026-08-03T00:00:00"))) == 2
    assert len(store.by_name(
        InvocationFilter(surface="mcp", since="2026-08-03T00:00:00"))) == 1


def test_missing_table_reports_nothing_rather_than_raising(tmp_path, monkeypatch):
    """A reporting command must not be what fails on an un-migrated database."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "empty.db"))
    import tools.db.storage as storage
    monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "empty.db"), raising=False)
    monkeypatch.setattr(storage, "_BACKEND", "sqlite", raising=False)

    assert InvocationStore().by_name() == []
    assert InvocationStore().by_surface() == []


def test_a_failed_query_rolls_the_connection_back(obs_db):
    """PostgreSQL poisons a transaction after a failed statement.

    Without the rollback, one missing table would make every LATER query on
    the same connection fail too. Asserted here on the store's own error path
    because the SQLite fixture cannot reproduce PG's aborted-transaction state.
    """
    calls = {"rollback": 0, "close": 0}

    class Boom:
        def execute(self, *_a, **_kw):
            raise RuntimeError("relation does not exist")

        def rollback(self):
            calls["rollback"] += 1

        def close(self):
            calls["close"] += 1

    assert InvocationStore(connection_factory=Boom).by_name() == []
    assert calls["rollback"] == 1, "aborted transaction was left poisoned"
    assert calls["close"] == 1, "connection leaked"


# ----------------------------------------------------------- recorder handoff

def test_recorder_summary_still_returns_its_documented_shape(obs_db):
    """``summary()`` delegates to the store; its contract must not have moved."""
    _insert("mcp", "dup", duration_ms=10)
    _insert("mcp", "dup", status="error", duration_ms=20)

    entry = next(s for s in R.summary(R.SURFACE_MCP) if s["name"] == "dup")
    assert entry["calls"] == 2 and entry["errors"] == 1
    assert entry["avg_ms"] == pytest.approx(15.0)
    assert entry["max_ms"] == pytest.approx(20.0)


def test_summary_honours_its_limit(obs_db):
    for i in range(5):
        _insert("mcp", f"t{i}", duration_ms=1)
    assert len(R.summary(limit=3)) == 3


# ------------------------------------------------------------------------ CLI

def test_cli_reports_per_surface_counts_errors_and_durations(obs_db, capsys):
    """AC1, end to end through the real argv path."""
    from tools.cli.runtime import main as runtime_main

    _insert("mcp", "rag_search", duration_ms=10)
    _insert("mcp", "rag_search", status="error", duration_ms=30)
    _insert("agent", "builder", duration_ms=2000)

    assert runtime_main([]) == 0
    out = capsys.readouterr().out
    assert "mcp" in out and "agent" in out
    assert "rag_search" in out and "builder" in out
    assert "50.0%" in out, "the MCP error rate (1 of 2) was not reported"


def test_cli_json_carries_both_rollups(obs_db, capsys):
    import json

    from tools.cli.runtime import main as runtime_main

    _insert("mcp", "a", duration_ms=10)
    _insert("mcp", "a", status="error", duration_ms=30)

    assert runtime_main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["surfaces"][0]["surface"] == "mcp"
    assert payload["surfaces"][0]["calls"] == 2
    assert payload["surfaces"][0]["errors"] == 1
    assert payload["surfaces"][0]["avg_ms"] == pytest.approx(20.0)
    assert payload["names"][0]["name"] == "a"


def test_cli_errors_only_survives_a_small_limit(obs_db, capsys):
    """--errors-only filters before --limit, so a rare failure stays visible.

    Filtering after truncation would hide the one broken tool behind 20 busy
    healthy ones — exactly the case the flag exists for.
    """
    import json

    from tools.cli.runtime import main as runtime_main

    for i in range(10):
        for _ in range(10):
            _insert("mcp", f"busy_{i}", duration_ms=1)
    _insert("mcp", "broken", status="error", duration_ms=1)

    assert runtime_main(["--errors-only", "--limit", "3", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [r["name"] for r in payload["names"]] == ["broken"]
    assert payload["surfaces"][0]["calls"] == 101, "totals must ignore the filter"


def test_cli_sort_by_duration_finds_the_slow_tool(obs_db, capsys):
    import json

    from tools.cli.runtime import main as runtime_main

    for _ in range(50):
        _insert("mcp", "fast", duration_ms=1)
    _insert("mcp", "slow", duration_ms=9000)

    assert runtime_main(["--sort", "avg_ms", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["names"][0]["name"] == "slow"


def test_cli_rejects_an_unknown_surface(obs_db, capsys):
    from tools.cli.runtime import main as runtime_main

    assert runtime_main(["--surface", "nonsense"]) == 1
    assert "unknown surface" in capsys.readouterr().err


def test_cli_empty_table_names_the_database_it_read(obs_db, capsys):
    """"(no invocations)" and "you are pointed at the wrong DB" look alike."""
    from tools.cli.runtime import main as runtime_main

    assert runtime_main([]) == 0
    err = capsys.readouterr().err
    assert "no invocations recorded" in err
    assert "sqlite" in err, "the backend was not disclosed"


def test_icdev_runtime_is_reachable_from_the_dispatcher(obs_db, capsys):
    """`icdev runtime top` must actually route; a module nobody can invoke is not a CLI."""
    from tools.cli.__main__ import main as icdev_main

    _insert("mcp", "a", duration_ms=1)
    assert icdev_main(["runtime", "top", "--json"]) == 0
    assert '"surfaces"' in capsys.readouterr().out


def test_dispatcher_rejects_an_unknown_runtime_verb(capsys):
    from tools.cli.__main__ import main as icdev_main

    assert icdev_main(["runtime", "bogus"]) == 2
    assert "unknown verb" in capsys.readouterr().err


# ------------------------------------------------------------------ dashboard

def _api_client():
    """Minimal Flask app carrying just the SRE blueprint.

    The full dashboard app pulls in ~200 blueprints and an auth middleware;
    mounting the one blueprint under test keeps this about the endpoint.
    """
    from flask import Flask

    from tools.dashboard.api.sre import sre_api

    app = Flask(__name__)
    app.register_blueprint(sre_api)
    return app.test_client()


def test_dashboard_endpoint_returns_the_same_rollup_as_the_cli(obs_db):
    _insert("mcp", "rag_search", duration_ms=10)
    _insert("mcp", "rag_search", status="error", duration_ms=30)
    _insert("agent", "builder", duration_ms=2000)

    payload = _api_client().get("/api/sre/invocations").get_json()

    surfaces = {s["surface"]: s for s in payload["surfaces"]}
    assert surfaces["mcp"]["calls"] == 2
    assert surfaces["mcp"]["errors"] == 1
    assert surfaces["mcp"]["avg_ms"] == pytest.approx(20.0)
    assert surfaces["agent"]["calls"] == 1
    assert {r["name"] for r in payload["names"]} == {"rag_search", "builder"}


def test_dashboard_endpoint_honours_surface_and_limit(obs_db):
    for i in range(5):
        _insert("mcp", f"t{i}", duration_ms=1)
    _insert("agent", "a", duration_ms=1)

    payload = _api_client().get("/api/sre/invocations?surface=mcp&limit=2").get_json()
    assert len(payload["names"]) == 2
    assert payload["total_names"] == 5
    assert [s["surface"] for s in payload["surfaces"]] == ["mcp"]
    assert payload["surfaces"][0]["calls"] == 5, "totals must survive the limit"


def test_dashboard_endpoint_windows_by_default_and_says_so(obs_db):
    """The panel re-runs this on every load; an unbounded GROUP BY must not be
    the default. And the window has to be visible, or a 30-day count reads as a
    lifetime total.
    """
    from datetime import datetime, timedelta, timezone

    recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    _insert("mcp", "recent_tool", duration_ms=5, started_at=recent)
    _insert("mcp", "ancient_tool", duration_ms=5, started_at=old)

    payload = _api_client().get("/api/sre/invocations").get_json()
    assert payload["window_days"] == 30
    assert [r["name"] for r in payload["names"]] == ["recent_tool"]
    assert payload["surfaces"][0]["calls"] == 1, "the 200-day-old row was counted"

    wide = _api_client().get("/api/sre/invocations?days=365").get_json()
    assert wide["window_days"] == 365
    assert wide["surfaces"][0]["calls"] == 2


def test_dashboard_endpoint_explicit_since_overrides_the_window(obs_db):
    """An explicit --since means the caller owns the range; report no window."""
    _insert("mcp", "t", duration_ms=1, started_at="2020-01-01T00:00:00+00:00")

    payload = _api_client().get(
        "/api/sre/invocations?since=2019-01-01T00:00:00%2B00:00").get_json()
    assert payload["window_days"] is None
    assert payload["surfaces"][0]["calls"] == 1


def test_cli_is_all_time_by_default(obs_db, capsys):
    """The CLI deliberately does NOT window — an operator asked for this once.

    Pins the asymmetry with the dashboard so a future edit that "harmonises"
    the two has to do it on purpose.
    """
    import json
    from datetime import datetime, timedelta, timezone

    from tools.cli.runtime import main as runtime_main

    old = (datetime.now(timezone.utc) - timedelta(days=900)).isoformat()
    _insert("mcp", "ancient_tool", duration_ms=5, started_at=old)

    assert runtime_main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["surfaces"][0]["calls"] == 1


def test_dashboard_endpoint_does_not_500_when_the_table_is_absent(tmp_path, monkeypatch):
    """A telemetry panel must never take the SRE page down with it."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "empty.db"))
    import tools.db.storage as storage
    monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "empty.db"), raising=False)
    monkeypatch.setattr(storage, "_BACKEND", "sqlite", raising=False)

    resp = _api_client().get("/api/sre/invocations")
    assert resp.status_code == 200
    assert resp.get_json()["surfaces"] == []
