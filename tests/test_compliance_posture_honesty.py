# CUI // SP-CTI
"""The Compliance Posture widget must not report what nobody measured (rem-hyg-09).

Measured on the live board 2026-08-20:

  * THREE canvases scored a confident **100.0** having never been assessed —
    Network (`nc_compliance_checks`, 0 rows) and Pipeline
    (`pc_compliance_checks`, 0 rows) through an `else 100.0` fallback, and
    Migration (`mc_assessments`, 0 rows) through `100 - c1*20 - c2*10 - c3*5`
    over SUMs that are all NULL. On a COMPLIANCE surface they rendered as full
    green bars at perfect compliance, and they inflated the headline from 87.9
    to 90.7.

  * EVERY score was between 33 and 71 days old and the widget showed no
    timestamp, so a June score was indistinguishable from one taken that
    morning. Observability in particular is ONE row written 2026-06-28 and never
    updated: its 58.8 is frozen, not falling.

  * The 30-day trend arrow computed `scores[0] - scores[-1]` over rows ordered
    by timestamp across ALL designs, so a canvas holding one row per design
    (Boundary: 6 rows, 6 designs) had design F's score subtracted from design
    A's and the difference called a trend.

`tools/quality/component_scorer.py` (idp-score-01) already documented the first
defect in its own docstring and worked around it DOWNSTREAM with a NOT_ASSESSED
sentinel, leaving the source still fabricating. This fixes the source, and
reuses that convention — `score is None`, never a number.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.canvas_compliance.posture import _has_rows, _max_ts, daily_trend  # noqa: E402


class _Cur:
    def __init__(self, row, raises=False):
        self._row, self._raises = row, raises

    def execute(self, *_a, **_k):
        if self._raises:
            raise RuntimeError("table missing")
        return self

    def fetchone(self):
        if self._raises:
            raise RuntimeError("table missing")
        return self._row


class _Conn:
    def __init__(self, row=None, raises=False):
        self._row, self._raises = row, raises

    def execute(self, *_a, **_k):
        if self._raises:
            raise RuntimeError("table missing")
        return _Cur(self._row)


# --------------------------------------------------------------------------- #
# 1. An empty table is not evidence
# --------------------------------------------------------------------------- #
def test_an_empty_table_has_no_rows():
    """The predicate that stops `100 - 0 - 0 - 0` becoming a score."""
    assert _has_rows(_Conn({"c": 0}), "mc_assessments") is False


def test_a_populated_table_has_rows():
    assert _has_rows(_Conn({"c": 7}), "mc_assessments") is True


def test_an_unreadable_table_is_not_evidence():
    """Fail-closed: a table that cannot be read has not assessed anything, and
    must never license a score."""
    assert _has_rows(_Conn(raises=True), "mc_assessments") is False


def test_a_tuple_row_is_read_like_a_dict_row():
    """psycopg2 hands back either shape depending on the cursor factory."""
    assert _has_rows(_Conn((3,)), "t") is True
    assert _has_rows(_Conn((0,)), "t") is False


# --------------------------------------------------------------------------- #
# 2. Staleness is reported, and absence is not freshness
# --------------------------------------------------------------------------- #
def test_a_timestamp_is_returned_as_a_string():
    assert _max_ts(_Conn({"m": "2026-06-28T02:42:53"}), "od_assessments", "created_at") \
        == "2026-06-28T02:42:53"


def test_no_rows_reports_none_not_now():
    """None means "no evidence". Defaulting to the current time would make an
    unassessed canvas the FRESHEST thing on the board."""
    assert _max_ts(_Conn({"m": None}), "od_assessments", "created_at") is None


def test_an_unreadable_table_reports_none():
    assert _max_ts(_Conn(raises=True), "nope", "created_at") is None


# --------------------------------------------------------------------------- #
# 3. The scoring contract, exercised through the real aggregation
# --------------------------------------------------------------------------- #
def _rows_from(monkeypatch, per_canvas):
    """Drive `compute_canvas_posture` with stub canvas connections."""
    from tools.canvas_compliance import posture as mod

    class _Stub:
        def __init__(self, name):
            self.name = name

        def execute(self, sql, *_a, **_k):
            handler = per_canvas.get(self.name)
            if handler is None:
                raise RuntimeError("no such canvas")
            return _Cur(handler(sql))

        def close(self):
            return None

        def set_security_context(self, _):
            return None

    monkeypatch.setattr(mod, "_open_canvas_connection",
                        lambda name: _Stub(name) if name in per_canvas else None)
    return mod


def test_an_unassessed_canvas_scores_none_and_is_not_averaged(monkeypatch):
    """THE defect. An empty checks table must yield None, and None must not
    drag — or inflate — the overall average."""
    def empty_network(sql: str):
        low = sql.lower()
        if "count(*)" in low:
            return {"c": 0, "cnt": 0}
        if "max(" in low:
            return {"m": None}
        # SUM(passed)/SUM(failed) over an empty table
        return {"p": None, "f": None}

    mod = _rows_from(monkeypatch, {"Network": empty_network})

    class _MainConn:
        def execute(self, *_a, **_k):
            raise RuntimeError("no govlift/zig tables in this test")

    rows, overall = mod.compute_canvas_posture(_MainConn())
    network = next((r for r in rows if r["name"] == "Network"), None)
    assert network is not None
    assert network["score"] is None, (
        "an empty checks table used to score 100.0 and render a full green bar "
        "for a canvas nobody had ever assessed"
    )
    assert network["last_assessed"] is None
    assert overall == 0, "a None score must not enter the average"


def test_every_row_carries_a_last_assessed_key(monkeypatch):
    """A MISSING key renders differently from a known-absent timestamp, so the
    field must always be present even when its value is None."""
    def empty(sql: str):
        low = sql.lower()
        if "count(*)" in low:
            return {"c": 0, "cnt": 0}
        if "max(" in low:
            return {"m": None}
        return {"p": None, "f": None}

    mod = _rows_from(monkeypatch, {"Network": empty, "Pipeline": empty})

    class _MainConn:
        def execute(self, *_a, **_k):
            raise RuntimeError("nope")

    rows, _ = mod.compute_canvas_posture(_MainConn())
    assert rows, "the stub canvases should have produced rows"
    for row in rows:
        assert "last_assessed" in row, row["name"]


# --------------------------------------------------------------------------- #
# 4. The trend compares like with like
# --------------------------------------------------------------------------- #
#: The PRODUCTION rule, not a copy of it. An earlier draft re-derived the
#: daily-average comparison here, which would have tested the copy rather than
#: the code — they could drift and every assertion below would still pass.
_trend = daily_trend


def test_one_row_per_design_on_one_day_is_not_a_trend():
    """The Boundary shape: 6 designs, one assessment each, all the same day.

    The old `scores[0] - scores[-1]` subtracted design F's score from design
    A's and reported a confident direction from data that never moved.
    """
    same_day = [{"date": "2026-08-01", "score": s} for s in (95.0, 80.0, 60.0, 99.0, 70.0, 88.0)]
    direction, delta = _trend(same_day)
    assert direction == "unmeasured"
    assert delta is None


def test_a_real_decline_is_still_reported():
    """The fix must not make the indicator useless — a genuine drop still shows."""
    scores = [{"date": "2026-08-01", "score": 90.0}, {"date": "2026-08-20", "score": 70.0}]
    direction, delta = _trend(scores)
    assert direction == "down"
    assert delta == -20.0


def test_a_real_rise_is_reported():
    scores = [{"date": "2026-08-01", "score": 60.0}, {"date": "2026-08-20", "score": 85.0}]
    assert _trend(scores)[0] == "up"


def test_a_design_mix_change_does_not_manufacture_a_direction():
    """Two designs assessed on both days: the daily MEAN is what moves, so a
    canvas whose scores are unchanged reports flat even though the individual
    rows are in a different order."""
    scores = [
        {"date": "2026-08-01", "score": 90.0}, {"date": "2026-08-01", "score": 60.0},
        {"date": "2026-08-20", "score": 60.0}, {"date": "2026-08-20", "score": 90.0},
    ]
    direction, delta = _trend(scores)
    assert direction == "flat"
    assert delta == 0.0


@pytest.mark.parametrize("scores", [[], [{"date": "2026-08-01", "score": 50.0}]])
def test_too_little_data_is_unmeasured_never_flat(scores):
    """`flat` claims a measurement held steady. With fewer than two days there
    was no measurement — which is the state EVERY canvas is in on this board."""
    direction, delta = _trend(scores)
    assert direction == "unmeasured"
    assert delta is None


# --------------------------------------------------------------------------- #
# A scheduled refresh is not a review, and the surface must say which it has
# (rmf-inert-03)
# --------------------------------------------------------------------------- #
from tools.canvas_compliance.posture import (  # noqa: E402
    NON_CANVAS_ROWS,
    SCHEDULED_ASSESSMENT_TYPE,
    _ASSESSED_AT,
    _CANVAS_MODULES,
    _last_assessed_detail,
    surface_rows,
)


class _TypedConn:
    """Answers the three questions _last_assessed_detail asks, in order."""

    def __init__(self, newest, newest_type, newest_reviewed):
        self._answers = [newest, newest_type, newest_reviewed]
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append(sql)
        if " AS t " in sql:                      # which writer wrote the newest row
            return _Cur({"t": self._answers[1]})
        if "!=" in sql:                          # newest NON-scheduled row
            return _Cur({"m": self._answers[2]})
        return _Cur({"m": self._answers[0]})    # newest row of any kind

    def rollback(self):
        return None


def test_surface_rows_is_the_canvas_map_plus_the_non_canvas_rows():
    rows = surface_rows()
    assert rows[:len(_CANVAS_MODULES)] == list(_CANVAS_MODULES)
    assert rows[len(_CANVAS_MODULES):] == list(NON_CANVAS_ROWS)
    assert {"GovLift", "Zero Trust"} <= set(rows)


def test_a_scheduled_newest_row_is_reported_as_scheduled_with_the_last_review_beside_it():
    cc = _TypedConn("2026-09-07T20:24:33+00:00", SCHEDULED_ASSESSMENT_TYPE, "2026-06-09T22:56:48Z")
    d = _last_assessed_detail(cc, "Data")
    assert d["last_assessed"] == "2026-09-07T20:24:33+00:00"
    assert d["last_assessed_source"] == "scheduled"
    assert d["last_reviewed"] == "2026-06-09T22:56:48Z", (
        "the newest NON-scheduled row is what tells an 89-day-old estate from a fresh one")


def test_a_canvas_written_row_is_reported_as_canvas():
    cc = _TypedConn("2026-07-18T01:40:41+00:00", "auto_stride", "2026-07-18T01:40:41+00:00")
    d = _last_assessed_detail(cc, "Security")
    assert d["last_assessed_source"] == "canvas"
    assert d["last_reviewed"] == d["last_assessed"]


def test_a_table_with_no_writer_column_cannot_say_and_says_so():
    """aadc_assessments has no assessment_type. None, never 'canvas' — filling
    it in from last_assessed would report every scheduled refresh as a review."""
    cc = _TypedConn("2026-06-24T19:25:59+00:00", "anything", "anything")
    d = _last_assessed_detail(cc, "Agentic AI")
    assert d["last_assessed"] == "2026-06-24T19:25:59+00:00"
    assert d["last_assessed_source"] is None
    assert d["last_reviewed"] is None
    assert len(cc.executed) == 1, "no type query may run against a table with no type column"


def test_no_evidence_reports_none_on_all_three():
    d = _last_assessed_detail(_TypedConn(None, None, None), "Infra")
    assert d == {"last_assessed": None, "last_assessed_source": None, "last_reviewed": None}


def test_network_and_pipeline_age_reads_the_column_their_tables_have():
    """Both checks tables carry `ran_at` and have never carried `created_at`
    (DDL and the live PG catalogue agree, 2026-09-07). The old spelling raised,
    was swallowed, and both canvases rendered a score with NO age — which on
    this widget reads as fresh."""
    assert _ASSESSED_AT["Network"] == ("nc_compliance_checks", "ran_at")
    assert _ASSESSED_AT["Pipeline"] == ("pc_compliance_checks", "ran_at")


def test_every_row_carries_the_source_and_review_keys(monkeypatch):
    """The widget branches on `last_assessed_source`; a MISSING key renders
    differently from a known-None one."""
    from tools.canvas_compliance import posture as mod

    class _EmptyConn:
        def execute(self, sql, params=()):
            return _Cur({"c": 0, "m": None, "p": None, "f": None, "cnt": 0})
        def close(self):
            return None
        def rollback(self):
            return None

    monkeypatch.setattr(mod, "_open_canvas_connection", lambda _n: _EmptyConn())
    rows, _overall = mod.compute_canvas_posture(_EmptyConn())
    assert rows, "the empty-table path still emits a row per canvas"
    for r in rows:
        assert "last_assessed_source" in r, r
        assert "last_reviewed" in r, r


def test_the_widget_renders_the_source_not_only_the_age():
    """A scheduled row must be marked on screen and carry the last review's age;
    otherwise the surface has the field and the reader still cannot tell. Both
    template copies, because the wheel serves the icdev/ one."""
    for rel in ("tools/dashboard/templates/index.html",
                "icdev/tools/dashboard/templates/index.html"):
        html = (ROOT / rel).read_text(encoding="utf-8")
        assert "last_assessed_source" in html, rel
        assert "last_reviewed" in html, rel
        assert 'data-assessed-source="scheduled"' in html, rel
