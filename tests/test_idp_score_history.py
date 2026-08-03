# CUI // SP-CTI
"""Tests for per-component scorecard history (idp-score-03).

The acceptance contract is three sentences long and every one of them is a
test here:

  1. two scoring runs produce two persisted rows per component
  2. a trend query returns the series
  3. a ladder level change between runs is detectable

Everything is exercised through the **real migration** rather than a
hand-written CREATE TABLE in the fixture. That is deliberate: the bug class
this feature is most exposed to is an INSERT naming a column the live schema
does not have — it raises, gets swallowed by a caller's `except`, and the
feature reports success while persisting nothing. A fixture that invents its
own schema cannot catch that; applying the shipped migration can.

Connections come from `tools.db.storage.get_connection(db_path=…)` rather than
raw `sqlite3`, because the module writes `%s` placeholders for PostgreSQL and
only the storage wrapper translates them. A raw sqlite3 connection here would
make the tests assert their own no-op.
"""
from __future__ import annotations

import importlib.util
import json
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.idp.score_history import (
    INSERT_COLUMNS,
    ScoreHistoryError,
    get_level_changes,
    get_score_trend,
    is_due,
    parse_window,
    persist_evaluation,
    record_scorecard,
    window_start,
)
from tools.idp.scorecard import evaluate, load_scorecard, parse_scorecard
from tools.iqe.executor import register_collection

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_DIR = REPO_ROOT / "tools" / "db" / "migrations" / "20260802222900_idp_score_history"

# Two component shapes and two runs' worth of facts. `bravo` gains an owner
# between run 1 and run 2, which is exactly one ladder rung.
RUN_ONE = [
    {"key": "alpha", "rls_clean": True, "has_owner": True},
    {"key": "bravo", "rls_clean": True, "has_owner": False},
]
RUN_TWO = [
    {"key": "alpha", "rls_clean": True, "has_owner": True},
    {"key": "bravo", "rls_clean": True, "has_owner": True},
]

CARD_YAML = """
key: test-history
name: Test History
collection: test.history
adapter_module: tools.iqe
evaluation:
  window: 24h
ladder:
  levels:
  - name: Bronze
    rank: 1
  - name: Gold
    rank: 2
rules:
- identifier: rls-clean
  level: Bronze
  weight: 10
  expression: foreach c in test.history where c.rls_clean == true select c.key
- identifier: has-owner
  level: Gold
  weight: 10
  expression: foreach c in test.history where c.has_owner == true select c.key
"""


def _load_migration(name: str):
    """Import the shipped migration by path (its dir name is not an identifier)."""
    spec = importlib.util.spec_from_file_location(
        f"_mig_{name}", MIGRATION_DIR / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _card():
    import yaml

    return parse_scorecard(yaml.safe_load(textwrap.dedent(CARD_YAML)), source_path="<test>")


@pytest.fixture
def facts():
    """Mutable fact source behind the IQE collection `test.history`."""
    state = {"rows": [dict(r) for r in RUN_ONE]}
    register_collection("test.history", lambda conn=None: [dict(r) for r in state["rows"]])
    return state


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """A storage connection over a temp SQLite DB with the migration applied."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from tools.db.storage import get_connection

    connection = get_connection(db_path=str(tmp_path / "history.db"))
    _load_migration("up").up(connection)
    yield connection
    try:
        connection.close()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Acceptance 1 — two runs, two rows per component
# ---------------------------------------------------------------------------


def test_two_runs_persist_two_rows_per_component(conn, facts):
    card = _card()
    first = persist_evaluation(evaluate(card, conn=conn), conn, window=card.window)
    second = persist_evaluation(evaluate(card, conn=conn), conn, window=card.window)

    assert first["rows_written"] == 2
    assert second["rows_written"] == 2
    assert first["run_id"] != second["run_id"], "a run must be identifiable as one run"

    for component in ("alpha", "bravo"):
        rows = conn.execute(
            "SELECT COUNT(*) AS n FROM idp_scorecard_history WHERE component_key = %s",
            (component,),
        ).fetchone()
        assert dict(rows)["n"] == 2, f"{component} should have two history points"


def test_every_inserted_column_exists_in_the_live_schema(conn, facts):
    """The swallowed-INSERT bug class, pinned.

    `persist_evaluation` names its columns explicitly; if the migration and the
    INSERT drift, the write raises rather than silently dropping data. This
    asserts the two agree so the failure is caught here, not in production.
    """
    live = {
        str(dict(row)["name"])
        for row in conn.execute("PRAGMA table_info(idp_scorecard_history)").fetchall()
    }
    missing = set(INSERT_COLUMNS) - live
    assert not missing, f"INSERT names columns absent from the migration: {sorted(missing)}"

    # And the write really lands — a column count match proves nothing on its own.
    persist_evaluation(evaluate(_card(), conn=conn), conn)
    total = dict(conn.execute("SELECT COUNT(*) AS n FROM idp_scorecard_history").fetchone())
    assert total["n"] == 2


# ---------------------------------------------------------------------------
# Acceptance 2 — the trend query returns the series
# ---------------------------------------------------------------------------


def test_trend_returns_the_series_oldest_first(conn, facts):
    card = _card()
    base = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    for offset in range(3):
        persist_evaluation(
            evaluate(card, conn=conn),
            conn,
            now=base + timedelta(days=offset),
            window=card.window,
        )

    trend = get_score_trend("alpha", "test-history", conn)
    assert trend["data_points"] == 3
    stamps = [point["evaluated_at"] for point in trend["trend"]]
    assert stamps == sorted(stamps), "series must be oldest-first so it plots directly"
    assert trend["current_level"] == "Gold"
    assert trend["current_score"] == pytest.approx(100.0)


def test_trend_limit_keeps_the_most_recent_points(conn, facts):
    """LIMIT must not silently return the OLDEST points of a long series."""
    card = _card()
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for offset in range(5):
        persist_evaluation(
            evaluate(card, conn=conn), conn, now=base + timedelta(days=offset)
        )

    trend = get_score_trend("alpha", "test-history", conn, limit=2)
    assert trend["data_points"] == 2
    assert trend["trend"][-1]["evaluated_at"].startswith("2026-08-05")


def test_trend_of_an_unknown_component_is_empty_not_an_error(conn, facts):
    trend = get_score_trend("nope", "test-history", conn)
    assert trend["data_points"] == 0
    assert trend["direction"] == "unknown"
    assert trend["level_changes"] == []


def test_failing_rule_identifiers_travel_with_the_point(conn, facts):
    """A score of 50% is not actionable; knowing WHICH rule failed is."""
    persist_evaluation(evaluate(_card(), conn=conn), conn)
    trend = get_score_trend("bravo", "test-history", conn)
    outcomes = trend["trend"][0]["rule_outcomes"]
    assert outcomes["fail"] == ["has-owner"]
    assert "pass" not in outcomes, "passing rules are the complement — do not store them"
    assert trend["trend"][0]["failing_rules"] == 1


# ---------------------------------------------------------------------------
# Acceptance 3 — a ladder level change is detectable
# ---------------------------------------------------------------------------


def test_ladder_promotion_between_runs_is_detectable(conn, facts):
    """`bravo` gains an owner between the two runs and moves Bronze -> Gold.

    This is the case a score-only history cannot serve: the delta is 50 points,
    but what an operator wants told is that the component changed RUNG.
    """
    card = _card()
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)

    persist_evaluation(evaluate(card, conn=conn), conn, now=base)
    facts["rows"] = [dict(r) for r in RUN_TWO]
    persist_evaluation(evaluate(card, conn=conn), conn, now=base + timedelta(days=1))

    trend = get_score_trend("bravo", "test-history", conn)
    assert [p["ladder_level"] for p in trend["trend"]] == ["Bronze", "Gold"]

    assert len(trend["level_changes"]) == 1
    change = trend["level_changes"][0]
    assert (change["from"], change["to"]) == ("Bronze", "Gold")
    assert change["direction"] == "promotion"

    # alpha never moved, so it must not appear as a change.
    assert get_score_trend("alpha", "test-history", conn)["level_changes"] == []


def test_demotion_is_reported_as_a_demotion(conn, facts):
    card = _card()
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    facts["rows"] = [dict(r) for r in RUN_TWO]
    persist_evaluation(evaluate(card, conn=conn), conn, now=base)
    facts["rows"] = [dict(r) for r in RUN_ONE]
    persist_evaluation(evaluate(card, conn=conn), conn, now=base + timedelta(days=1))

    changes = get_level_changes("test-history", conn)
    assert changes["change_count"] == 1
    assert changes["demotions"] == 1
    assert changes["changes"][0]["component"] == "bravo"
    assert changes["changes"][0]["direction"] == "demotion"


def test_level_changes_scans_the_whole_estate(conn, facts):
    """Both components move in opposite directions in the same pair of runs."""
    card = _card()
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    facts["rows"] = [
        {"key": "alpha", "rls_clean": True, "has_owner": True},
        {"key": "bravo", "rls_clean": True, "has_owner": False},
    ]
    persist_evaluation(evaluate(card, conn=conn), conn, now=base)
    facts["rows"] = [
        {"key": "alpha", "rls_clean": False, "has_owner": True},
        {"key": "bravo", "rls_clean": True, "has_owner": True},
    ]
    persist_evaluation(evaluate(card, conn=conn), conn, now=base + timedelta(days=1))

    changes = get_level_changes("test-history", conn)
    assert changes["components_with_history"] == 2
    assert changes["promotions"] == 1 and changes["demotions"] == 1
    by_component = {c["component"]: c for c in changes["changes"]}
    # alpha loses Bronze and so cannot hold Gold either — it falls off the ladder.
    assert by_component["alpha"]["to"] is None
    assert by_component["bravo"]["to"] == "Gold"


# ---------------------------------------------------------------------------
# Windows and cadence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,seconds",
    [("4h", 14400), ("24h", 86400), ("30m", 1800), ("1d", 86400), ("2w", 1209600)],
)
def test_parse_window(text, seconds):
    assert parse_window(text) == seconds


def test_unparseable_window_raises_rather_than_defaulting():
    """A typo must not silently become the default cadence."""
    with pytest.raises(ScoreHistoryError):
        parse_window("4 hours")
    with pytest.raises(ScoreHistoryError):
        parse_window("0h")


def test_window_start_is_an_epoch_anchored_bucket():
    """Two processes computing the bucket must agree without coordinating."""
    # A 4h window aligns to 00/04/08/… UTC, so 05:59 and 07:59 share a bucket
    # and 08:00 opens the next one.
    early = datetime(2026, 8, 2, 5, 59, 59, tzinfo=timezone.utc)
    late = datetime(2026, 8, 2, 8, 0, 1, tzinfo=timezone.utc)
    assert window_start(early, 14400) == window_start(
        datetime(2026, 8, 2, 4, 0, tzinfo=timezone.utc), 14400
    )
    assert window_start(early, 14400).startswith("2026-08-02T04:00:00")
    assert window_start(late, 14400).startswith("2026-08-02T08:00:00")
    # A naive datetime is read as UTC rather than as local time, or two hosts
    # in different zones would disagree about which bucket a run belongs to.
    assert window_start(late.replace(tzinfo=None), 14400) == window_start(late, 14400)
    assert window_start(late, 86400).startswith("2026-08-02T00:00:00")


def test_if_due_skips_a_window_already_recorded(conn, facts):
    """The reflex runs every 3h against a 24h window — 7 of 8 cycles must skip."""
    card = _card()
    now = datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc)

    first = record_scorecard(card, conn, if_due=True, now=now)
    assert first["status"] == "recorded" and first["rows_written"] == 2

    later = record_scorecard(card, conn, if_due=True, now=now + timedelta(hours=3))
    assert later["status"] == "skipped" and later["rows_written"] == 0

    tomorrow = record_scorecard(card, conn, if_due=True, now=now + timedelta(days=1))
    assert tomorrow["status"] == "recorded"


def test_explicit_record_always_writes(conn, facts):
    """Re-scoring right after a fix must show the improvement immediately.

    Without this, `if_due` would be the only path and an operator who just made
    a component Gold would have to wait out the window to see it — which is
    when people stop trusting the board.
    """
    card = _card()
    now = datetime(2026, 8, 2, 1, 0, tzinfo=timezone.utc)
    record_scorecard(card, conn, if_due=False, now=now)
    second = record_scorecard(card, conn, if_due=False, now=now + timedelta(minutes=5))
    assert second["status"] == "recorded"
    assert get_score_trend("alpha", "test-history", conn)["data_points"] == 2


def test_is_due_on_an_empty_table(conn, facts):
    due, bucket, last = is_due(_card(), conn, now=datetime(2026, 8, 2, tzinfo=timezone.utc))
    assert due is True
    assert last is None
    assert bucket.startswith("2026-08-02T00:00:00")


# ---------------------------------------------------------------------------
# Shipped configuration
# ---------------------------------------------------------------------------


def test_shipped_scorecard_declares_a_parseable_window():
    """The cadence the recorder honours is config; a bad value must not ship."""
    card = load_scorecard("component-readiness")
    assert parse_window(card.window) > 0


def test_recorder_reflex_is_registered_on_the_awareness_cadence():
    """A scorecard nobody records is why 25 score tables hold zero rows."""
    import yaml

    config = yaml.safe_load(
        (REPO_ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8")
    )
    reflex = config["reflexes"]["idp_score_recorder"]
    assert reflex["enabled"] is True
    assert reflex["interval_seconds"] == config["reflexes"]["awareness"]["interval_seconds"]

    # The config key IS the module name — the daemon imports
    # tools.genesis.reflexes.<key>, so a mismatch is a silent no-op reflex.
    module = REPO_ROOT / "tools" / "genesis" / "reflexes" / "idp_score_recorder.py"
    assert module.is_file()


def test_history_table_is_registered_append_only():
    hook = (REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py").read_text(encoding="utf-8")
    assert '"idp_scorecard_history"' in hook


def test_migration_rollback_drops_the_table(conn, facts):
    persist_evaluation(evaluate(_card(), conn=conn), conn)
    _load_migration("down").down(conn)
    with pytest.raises(Exception):
        conn.execute("SELECT 1 FROM idp_scorecard_history").fetchall()


def test_rule_outcomes_round_trip_as_json_not_backend_sql(conn, facts):
    """Decoding happens in Python — SQLite-dialect JSON SQL must not be load-bearing."""
    persist_evaluation(evaluate(_card(), conn=conn), conn)
    raw = dict(
        conn.execute(
            "SELECT rule_outcomes FROM idp_scorecard_history WHERE component_key = %s",
            ("bravo",),
        ).fetchone()
    )["rule_outcomes"]
    assert isinstance(raw, str)
    assert json.loads(raw)["fail"] == ["has-owner"]
