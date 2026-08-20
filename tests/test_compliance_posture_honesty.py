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
