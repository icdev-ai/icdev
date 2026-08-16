# CUI // SP-CTI
"""The identity survey must not fabricate, and must not launder, a fire rate (rem-hyg-03).

Three properties carry the whole point of the survey, and each has a way of
failing that looks like success:

* a survey that could not run must report ``measured: false`` — never a 0% rate,
  which is the ``|| true`` failure in report form;
* a gate sentinel must never be counted as an orphan, or every manual-mode card
  contributes a phantom finding and arming looks more dangerous than it is;
* the narrowed rate must exclude opaque machine ids, or the report tells
  rem-hyg-04 to refuse the dashboard's own task-creation button.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tools.kanban.identity_survey import (
    SHAPE_CARD,
    SHAPE_OPAQUE,
    UNMEASURABLE_EMPTY_BOARD,
    UNMEASURABLE_NO_REGISTRY,
    classify_shape,
    read_board,
    render,
    survey,
)
from tools.kanban.task_identity import ACTIONABLE_REASONS, Card

NOW = datetime(2026, 8, 16, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def cards():
    """Two cards: one with epics, one whose prefix nests under the first."""
    return [
        Card(key="rem", prefix="rem-", epics=("hyg", "tst", "gate")),
        Card(key="noepics", prefix="ne-", epics=()),
    ]


def _row(task_id, status="done", age_days=1):
    return (task_id, status, (NOW - timedelta(days=age_days)).isoformat())


# --------------------------------------------------------------------------
# the two zeroes that are not a clean bill of health
# --------------------------------------------------------------------------
def test_unreadable_registry_is_unmeasurable_not_a_finding_per_row(cards):
    """No registry must not turn a missing config file into a finding per board row."""
    rows = [_row("rem-hyg-01"), _row("anything-01"), _row("other-02")]

    report = survey(rows, cards=[])

    assert report["measured"] is False
    assert report["unmeasurable_reason"] == UNMEASURABLE_NO_REGISTRY
    # The counts must be ABSENT, not zero — a caller must not be able to read a
    # 0% fire rate off a survey that never ran.
    assert "totals" not in report
    assert report["board"]["rows"] == 3


def test_empty_board_is_unmeasurable_not_a_zero_fire_rate(cards):
    """The worktree trap: a throwaway SQLite DB must not read as 'safe to arm'."""
    report = survey([], cards=cards)

    assert report["measured"] is False
    assert report["unmeasurable_reason"] == UNMEASURABLE_EMPTY_BOARD
    assert "totals" not in report
    assert "--env-file" in report["detail"]


def test_render_never_shows_a_percentage_for_an_unmeasured_survey(cards):
    """The human half must say so too, or the caveat dies at the display layer."""
    text = render(survey([], cards=cards))

    assert "NOT MEASURED" in text
    assert "This is NOT a 0% fire rate" in text
    # None of the counted-report headings may appear: there is nothing to count.
    for heading in ("ACTIONABLE", "NARROWED", "claimed", "gate sentinels"):
        assert heading not in text


# --------------------------------------------------------------------------
# bucketing
# --------------------------------------------------------------------------
def test_gate_sentinel_is_counted_separately_and_never_as_an_orphan(cards):
    rows = [_row("rem-gate-00"), _row("rem-hyg-01")]

    t = survey(rows, cards=cards)["totals"]

    assert t["gate_sentinels"] == 1
    assert t["claimed"] == 1
    assert t["no_epic"] == 0
    assert t["no_card"] == 0
    assert t["actionable"] == 0, "a gate sentinel must never contribute to the fire rate"


def test_no_epic_and_no_card_are_distinct_buckets(cards):
    """They send you to different fixes: register an epic vs register a card."""
    rows = [
        _row("rem-hyg-01"),      # claimed
        _row("rem-nosuch-01"),   # card owns rem-, no epic 'nosuch'  -> no_epic
        _row("zzz-work-01"),     # no card owns zzz-                 -> no_card
    ]

    t = survey(rows, cards=cards)["totals"]

    assert (t["claimed"], t["no_epic"], t["no_card"]) == (1, 1, 1)
    assert t["actionable"] == 2


def test_actionable_total_tracks_the_shared_reason_set(cards):
    """`actionable` must be exactly ACTIONABLE_REASONS, not a second local copy."""
    rows = [_row("rem-nosuch-01"), _row("zzz-work-01"), _row("rem-gate-00"), _row("rem-hyg-01")]

    report = survey(rows, cards=cards)

    assert {u["reason"] for u in report["unclaimed"]} <= set(ACTIONABLE_REASONS)
    assert report["totals"]["actionable"] == len(report["unclaimed"])


def test_a_card_declaring_no_epics_claims_nothing(cards):
    """The misconfiguration load_cards deliberately keeps rather than skips."""
    report = survey([_row("ne-work-01")], cards=cards)

    assert report["totals"]["no_epic"] == 1
    assert report["by_card"][0]["card"] == "noepics"


# --------------------------------------------------------------------------
# the narrowing — the finding that decides whether arming is safe
# --------------------------------------------------------------------------
@pytest.mark.parametrize("task_id,expected", [
    ("bdr-feat-1", SHAPE_CARD),
    ("prem-lcatq-01", SHAPE_CARD),
    ("mvs-audit-03-d1", SHAPE_CARD),           # decomposed child of card work
    ("obs-cov-02-d3-d1", SHAPE_CARD),          # nested decomposition
    ("task-fd99a9c8ae", SHAPE_OPAQUE),         # dashboard / awareness writer
    ("task-fd99a9c8ae-d5", SHAPE_OPAQUE),      # child of an opaque parent
    ("mc-reflex-0f01f09f", SHAPE_OPAQUE),
    ("cpmp-266fb1114b", SHAPE_OPAQUE),
    ("chore-yaml-dupkeys-de15d6f3", SHAPE_OPAQUE),
])
def test_classify_shape_separates_card_work_from_machine_ids(task_id, expected):
    assert classify_shape(task_id) == expected


def test_narrowed_rate_exempts_opaque_ids_and_the_wide_rate_does_not(cards):
    """Refusing `task-<hex>` would refuse the dashboard's own create-task button."""
    rows = [
        _row("rem-hyg-01"),          # claimed
        _row("zzz-work-01"),         # no_card, card-shaped -> a card IS missing
        _row("task-fd99a9c8ae"),     # no_card, opaque      -> never card work
        _row("mc-reflex-0f01f09f"),  # no_card, opaque
    ]

    t = survey(rows, cards=cards)["totals"]

    assert t["no_card"] == 3
    assert (t["no_card_card_shaped"], t["no_card_opaque"]) == (1, 2)
    assert t["actionable"] == 3 and t["fire_rate"] == pytest.approx(0.75)
    assert t["actionable_narrowed"] == 1 and t["fire_rate_narrowed"] == pytest.approx(0.25)


def test_unregistered_prefixes_split_missing_cards_from_writer_namespaces(cards):
    rows = [_row("zzz-work-01"), _row("zzz-work-02"), _row("task-fd99a9c8ae")]

    unreg = survey(rows, cards=cards)["unregistered_prefixes"]

    assert unreg["zzz-"] == {"count": 2, "card_shaped": 2, "opaque": 0,
                             "ids": ["zzz-work-01", "zzz-work-02"]}
    assert unreg["task-"]["card_shaped"] == 0, "an all-opaque prefix is not a missing card"


# --------------------------------------------------------------------------
# windows
# --------------------------------------------------------------------------
def test_recent_window_counts_only_rows_created_inside_it(cards):
    rows = [_row("zzz-work-01", age_days=2), _row("zzz-work-02", age_days=45)]

    windows = {w["days"]: w for w in survey(rows, cards=cards, windows=(7, 90), now=NOW)["windows"]}

    assert (windows[7]["rows"], windows[7]["actionable"]) == (1, 1)
    assert (windows[90]["rows"], windows[90]["actionable"]) == (2, 2)


def test_unparseable_created_at_is_excluded_from_a_window_not_absorbed_into_it(cards):
    """A window that silently swallows every unreadable timestamp is not a window."""
    rows = [("zzz-work-01", "done", "not-a-timestamp"), ("zzz-work-02", "done", None)]

    windows = survey(rows, cards=cards, windows=(30,), now=NOW)["windows"]

    assert windows[0]["rows"] == 0
    assert windows[0]["fire_rate"] == 0.0


# --------------------------------------------------------------------------
# the DB seam
# --------------------------------------------------------------------------
def test_read_board_fails_open_when_the_table_is_unreachable(monkeypatch):
    """An unreachable board reports nothing read; survey() then calls it UNMEASURABLE."""
    class Boom:
        def execute(self, *a, **k):
            raise RuntimeError("relation \"kanban_tasks\" does not exist")

    assert read_board(conn=Boom()) == []


def test_read_board_borrows_the_caller_connection_and_never_closes_it():
    """Closing a caller-owned connection is the defect ctx-obs fixed in IQE."""
    class Conn:
        closed = False

        def execute(self, *a, **k):
            return self

        def fetchall(self):
            return [("rem-hyg-01", "done", "2026-08-16T00:00:00+00:00")]

        def close(self):
            Conn.closed = True

    assert read_board(conn=Conn()) == [("rem-hyg-01", "done", "2026-08-16T00:00:00+00:00")]
    assert Conn.closed is False
