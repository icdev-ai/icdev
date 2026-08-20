# CUI // SP-CTI
"""The reflex that keeps canvas assessments from going stale (rem-hyg-11).

rem-hyg-09 made the Compliance Posture card's staleness VISIBLE. This is the
cause: a canvas assessment was only ever written by a human clicking "assess" in
the canvas UI, so the newest evidence was whenever somebody last happened to
look — 33 to 71 days on the live board, and 79 of Infra's 84 designs never
assessed at all.

The defect this file mostly guards is the one that would have been EASY to
ship. `auto_remediator.persist_verify_assessment` already inserts assessment
rows and was the obvious building block — and it writes a hardcoded
`score=100.0, grade="A"` with `cat1/cat2/cat3 = 0/0/0`, because
`auto_remediator.reassess_design` discards the engine's score one call earlier.
The posture card AVERAGES those rows and SUMS those cat columns, so a scheduled
writer built on that helper would have fabricated perfect compliance into the
database on a 24-hour cadence — re-creating, on the write path, exactly the
defect rem-hyg-09 removed from the read path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.genesis.reflexes import canvas_reassess as reflex  # noqa: E402


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _RecordingConn:
    """Captures executed SQL + params so a write can be asserted on."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        for key, rows in self.responses.items():
            if key in sql:
                return _Cur(rows)
        return _Cur([])

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


# --------------------------------------------------------------------------- #
# 1. The score that gets written is the score the engine returned
# --------------------------------------------------------------------------- #
def test_the_engine_score_is_persisted_not_a_placeholder():
    """`persist_verify_assessment` writes 100.0/'A' whatever the engine said.
    This must write what the engine actually returned."""
    conn = _RecordingConn()
    result = {"score": 58.8, "grade": "F", "findings": [{"id": "f1"}]}
    assert reflex._persist(conn, "observability", "d-1", result) is True

    sql, params = conn.executed[-1]
    assert "INSERT INTO od_assessments" in sql
    assert 58.8 in params, params
    assert "F" in params, params
    assert 100.0 not in params, "a placeholder score reached the database"


def test_boundary_cat_counts_come_from_the_engine():
    """The posture card SUMS cat1/cat2/cat3 for `open_findings`. Writing 0/0/0
    — which the existing helper does — reports ZERO findings for a design the
    engine found 17 in."""
    conn = _RecordingConn()
    result = {
        "score": 0.0, "grade": "FAIL",
        "cat1_findings": 9, "cat2_findings": 8, "cat3_findings": 0,
        "findings": [{"id": f"f{i}"} for i in range(17)],
        "nist_coverage": {"AC-2": "partial"},
    }
    assert reflex._persist(conn, "boundary", "d-2", result) is True
    _sql, params = conn.executed[-1]
    assert 9 in params and 8 in params, params
    assert json.dumps({"AC-2": "partial"}) in params, "nist_coverage was dropped"


def test_a_measured_zero_is_written_not_skipped():
    """`is None`, never `if not score`. Three boundary designs score a real 0.0
    on the live board; a falsiness check would skip them EVERY cycle, leaving
    the worst-scoring designs permanently unrefreshed."""
    conn = _RecordingConn()
    assert reflex._persist(conn, "observability", "d-3",
                           {"score": 0.0, "grade": "F", "findings": []}) is True
    _sql, params = conn.executed[-1]
    assert 0.0 in params


def test_a_missing_score_is_not_written():
    """An engine that produced no score must not put a NULL into the column the
    posture average reads."""
    conn = _RecordingConn()
    assert reflex._persist(conn, "observability", "d-4",
                           {"grade": "F", "findings": []}) is False
    assert conn.executed == []


def test_an_unknown_canvas_writes_nothing():
    conn = _RecordingConn()
    assert reflex._persist(conn, "security", "d-5", {"score": 90.0}) is False
    assert conn.executed == []


def test_the_row_is_tagged_so_a_refresh_is_not_mistaken_for_a_review():
    """A scheduled refresh and a human sitting down to review a design are
    different events; `assessment_type` is what tells them apart."""
    conn = _RecordingConn()
    reflex._persist(conn, "observability", "d-6", {"score": 70.0, "grade": "C", "findings": []})
    _sql, params = conn.executed[-1]
    assert reflex.ASSESSMENT_TYPE in params
    assert reflex.ASSESSMENT_TYPE != "auto_remediator_verify", (
        "the two writers must stay distinguishable"
    )


# --------------------------------------------------------------------------- #
# 2. Which designs get picked
# --------------------------------------------------------------------------- #
def test_a_design_with_no_assessment_is_stale():
    """The 79-design Infra case — never assessed at all, and invisible to the
    posture card because it averages only what exists."""
    conn = _RecordingConn({"FROM infra_designs": [{"id": "d-new"}]})
    cfg = {"design_table": "infra_designs", "asmt_table": "idc_assessments",
           "asmt_time_col": "created_at"}
    assert reflex.stale_designs(conn, cfg, 7) == ["d-new"]
    sql, _params = conn.executed[-1]
    assert "IS NULL" in sql, "a design with no assessment row must be selected"


def test_the_cutoff_is_passed_as_a_parameter():
    conn = _RecordingConn({"FROM infra_designs": []})
    cfg = {"design_table": "infra_designs", "asmt_table": "idc_assessments",
           "asmt_time_col": "created_at"}
    reflex.stale_designs(conn, cfg, 7)
    _sql, params = conn.executed[-1]
    assert params and isinstance(params[0], str), "cutoff must be bound, not interpolated"


# --------------------------------------------------------------------------- #
# 3. The cycle contract
# --------------------------------------------------------------------------- #
def test_dry_run_writes_nothing(monkeypatch):
    writes = []
    monkeypatch.setattr(reflex, "_persist", lambda *a, **k: writes.append(a) or True)
    monkeypatch.setattr(reflex, "_canvas_conn", lambda _c: None)
    out = reflex.run({"dry_run": True})
    assert out["dry_run"] is True
    assert writes == []


def test_a_broken_canvas_costs_that_canvas_not_the_cycle(monkeypatch):
    """A reflex that raises takes the scheduler cycle with it."""
    def _boom(_canvas):
        raise RuntimeError("canvas database is gone")

    monkeypatch.setattr(reflex, "_canvas_conn", _boom)
    out = reflex.run({})
    assert out["status"] == "degraded"
    assert out["errors"], "the failure must be reported, not swallowed"
    assert out["reassessed"] == 0


def test_the_budget_reports_what_it_skipped_by_name(monkeypatch):
    """A truncated sweep that reports only its successes reads as full
    coverage."""
    cfg = {"design_table": "infra_designs", "asmt_table": "idc_assessments",
           "asmt_time_col": "created_at"}
    monkeypatch.setattr(reflex, "_registry", lambda: {"infra": cfg})
    monkeypatch.setattr(reflex, "_canvas_conn",
                        lambda _c: _RecordingConn({"graph_json": [{"graph_json": "{}"}]}))
    monkeypatch.setattr(reflex, "stale_designs", lambda *a: ["a", "b", "c"])
    monkeypatch.setattr(reflex, "assess_design", lambda *a: {"score": 50.0, "findings": []})
    monkeypatch.setattr(reflex, "_persist", lambda *a: True)

    out = reflex.run({"max_per_run": 2})
    assert out["reassessed"] == 2
    assert out["skipped_over_budget"] == ["infra:c"], out["skipped_over_budget"]


def test_the_reflex_declares_its_cadence():
    assert reflex.CADENCE_HOURS == 24


def test_it_is_registered_and_enabled():
    """A reflex nobody schedules is the defect this whole card is about."""
    import yaml   # a hard ICDEV dependency; a skip here would assert nothing

    cfg = yaml.safe_load((ROOT / "args" / "genesis_config.yaml").read_text(encoding="utf-8"))
    entry = (cfg.get("reflexes") or {}).get("canvas_reassess")
    assert entry, "canvas_reassess is not registered in args/genesis_config.yaml"
    assert entry["enabled"] is True
    assert entry["interval_seconds"] == 86400
