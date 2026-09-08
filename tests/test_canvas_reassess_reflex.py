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
    monkeypatch.setattr(reflex, "stale_designs_ranked",
                        lambda *a: [("a", None), ("b", None), ("c", None)])
    monkeypatch.setattr(reflex, "assess_design", lambda *a: {"score": 50.0, "findings": []})
    monkeypatch.setattr(reflex, "_persist", lambda *a: True)

    out = reflex.run({"max_per_run": 2})
    assert out["reassessed"] == 2
    assert out["skipped_over_budget"] == ["infra:c"], out["skipped_over_budget"]
    assert out["budget_saturated"] is True
    assert out["skipped_never_assessed"] == 1, "a never-assessed design was skipped and must be counted"


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


# --------------------------------------------------------------------------- #
# 4. Green while blind (rmf-inert-03): the reflex reports its own COVERAGE
# --------------------------------------------------------------------------- #
# Measured 2026-09-07: genesis_reflex_state read 17 runs / 17 successes for this
# reflex while the posture widget carried six canvases 51-89 days stale. Both
# numbers were right — the reflex could reach three of the widget's eleven
# canvases, and a run touching 3 of 13 subjects returned the same shape as one
# touching all 13.

from tools.canvas_compliance import posture  # noqa: E402


def test_coverage_is_measured_against_the_surfaces_own_list():
    """Covered + uncovered + undecided must partition posture.surface_rows() —
    the list the WIDGET renders, never a copy kept in the reflex."""
    cov = reflex.coverage_report()
    surface = posture.surface_rows()
    assert cov["surface"] == surface
    named = set(cov["covered"]) | set(cov["uncovered"]) | set(cov["undecided"])
    assert named == set(surface), sorted(set(surface) ^ named)
    assert not (set(cov["covered"]) & set(cov["uncovered"]))


def test_every_surface_row_has_a_coverage_decision():
    """An uncovered canvas is a STATED ABSENCE, never a silence. A new widget row
    with no decision fails here before it can ship as a green run."""
    cov = reflex.coverage_report()
    assert cov["undecided"] == [], cov["undecided"]
    assert cov["decisions_for_absent_rows"] == [], (
        "UNCOVERED names a row the surface no longer renders")


def test_every_uncovered_row_carries_a_written_reason():
    for name, reason in reflex.coverage_report()["uncovered"].items():
        assert isinstance(reason, str) and len(reason) > 40, f"{name}: {reason!r}"


def test_security_stays_excluded_and_visible():
    """Its exclusion was a code comment nobody reading the widget could see."""
    cov = reflex.coverage_report()
    assert "Security" in cov["uncovered"]
    assert "Security" not in cov["covered"]


def test_data_is_now_covered_and_writes_the_engines_risk_score():
    """Data was 89 days stale with a writable six-column shape all along. The
    engine spells its score `risk_score`; that — not a missing `score` — is
    what reaches dd_assessments.score, as the canvas's own route does."""
    assert "Data" in reflex.coverage_report()["covered"]
    conn = _RecordingConn()
    ok = reflex._persist(conn, "data", "d-7", {"risk_score": 62.5, "posture_grade": "D",
                                                "findings": [{"id": "f"}]})
    assert ok is True
    sql, params = conn.executed[-1]
    assert "INSERT INTO dd_assessments" in sql
    assert 62.5 in params and reflex.ASSESSMENT_TYPE in params, params


def test_a_run_reports_coverage_by_name_not_only_a_count(monkeypatch):
    monkeypatch.setattr(reflex, "_canvas_conn", lambda _c: None)
    out = reflex.run({"dry_run": True})
    assert out["coverage"]["covered_count"] < out["coverage"]["surface_count"]
    assert "Security" in out["coverage"]["uncovered"]
    assert set(out["coverage"]["covered"]) >= {"Infra", "Boundary", "Observability", "Data"}


def test_an_undecided_surface_row_is_an_error_not_a_silence(monkeypatch):
    """Registry shrunk to Infra alone: Boundary/Observability/Data are then
    neither written nor named, and the run must say so and fail."""
    cfg = {"design_table": "infra_designs", "asmt_table": "idc_assessments",
           "asmt_time_col": "created_at"}
    monkeypatch.setattr(reflex, "_registry", lambda: {"infra": cfg})
    monkeypatch.setattr(reflex, "_canvas_conn", lambda _c: _RecordingConn())
    out = reflex.run({"dry_run": True})
    assert set(out["coverage"]["undecided"]) == {"Boundary", "Observability", "Data"}
    assert out["success"] is False
    assert any("Boundary" in e and "no coverage decision" in e for e in out["errors"]), out["errors"]


def test_the_report_rides_under_details_because_that_is_all_the_daemon_persists(monkeypatch):
    """`daemon.run_reflex_impl` records result['details'] and nothing else. For
    17 runs this key was absent, so every field the module 'reported' —
    skipped_over_budget, by_canvas, errors — was persisted as {}."""
    monkeypatch.setattr(reflex, "_canvas_conn", lambda _c: None)
    out = reflex.run({"dry_run": True})
    d = out["details"]
    for key in ("coverage", "by_canvas", "skipped_over_budget", "skipped_never_assessed",
                "oldest_skipped_age_days", "budget_saturated", "errors", "status"):
        assert key in d, key
    assert d["coverage"] == out["coverage"]


# --------------------------------------------------------------------------- #
# 5. The budget is spent OLDEST-FIRST across canvases, never `ORDER BY d.id`
# --------------------------------------------------------------------------- #
# Measured 2026-09-07: last_metric_value sat at exactly the 25 budget on 14 of
# 17 runs while 8 Infra designs had NEVER been assessed — each daily cohort
# re-stales together a week later and, sorted by id, reaches the budget ahead of
# a never-assessed design whose id sorts after it. Forever.

def test_never_assessed_designs_reach_the_front_ahead_of_a_restaling_cohort(monkeypatch):
    cfg = {"design_table": "t", "asmt_table": "a", "asmt_time_col": "created_at"}
    monkeypatch.setattr(reflex, "_registry", lambda: {"infra": cfg})
    monkeypatch.setattr(reflex, "_canvas_conn",
                        lambda _c: _RecordingConn({"graph_json": [{"graph_json": "{}"}]}))
    # The SQL already returns oldest-first; the queue must not re-sort by id.
    # A design nobody has assessed ("zz-never", id sorts LAST) must win over a
    # cohort member re-staling at 8 days ("aa-cohort", id sorts FIRST).
    monkeypatch.setattr(reflex, "stale_designs_ranked", lambda *a: [
        ("zz-never", None),
        ("mm-old", "2026-08-01T00:00:00+00:00"),
        ("aa-cohort", "2026-08-30T00:00:00+00:00"),
    ])
    seen = []
    monkeypatch.setattr(reflex, "assess_design", lambda c, d, g: seen.append(d) or {"score": 1.0})
    monkeypatch.setattr(reflex, "_persist", lambda *a: True)
    out = reflex.run({"max_per_run": 2})
    assert seen == ["zz-never", "mm-old"], seen
    assert out["skipped_over_budget"] == ["infra:aa-cohort"]
    assert out["skipped_never_assessed"] == 0
    assert out["oldest_skipped_age_days"] is not None and out["oldest_skipped_age_days"] > 7


def test_ordering_is_global_across_canvases_not_per_canvas(monkeypatch):
    """Registry order used to decide who got the budget: a canvas listed first
    with 25 stale designs starved every canvas behind it."""
    cfg = {"design_table": "t", "asmt_table": "a", "asmt_time_col": "created_at"}
    monkeypatch.setattr(reflex, "_registry", lambda: {"infra": cfg, "data": cfg})
    monkeypatch.setattr(reflex, "_canvas_conn",
                        lambda _c: _RecordingConn({"graph_json": [{"graph_json": "{}"}]}))
    ranked = {
        "t": None,
    }
    def _ranked(cc, cfg_, days):
        # both canvases share cfg; tell them apart by how many times we are called
        ranked["n"] = ranked.get("n", 0) + 1
        if ranked["n"] == 1:      # infra: fresh-ish cohort
            return [("i-1", "2026-08-30T00:00:00+00:00"), ("i-2", "2026-08-30T00:00:00+00:00")]
        return [("d-1", None)]    # data: never assessed
    monkeypatch.setattr(reflex, "stale_designs_ranked", _ranked)
    seen = []
    monkeypatch.setattr(reflex, "assess_design", lambda c, d, g: seen.append(f"{c}:{d}") or {"score": 1.0, "risk_score": 1.0})
    monkeypatch.setattr(reflex, "_persist", lambda *a: True)
    out = reflex.run({"max_per_run": 1})
    assert seen == ["data:d-1"], seen
    assert sorted(out["skipped_over_budget"]) == ["infra:i-1", "infra:i-2"]


def test_the_stale_query_sorts_never_assessed_first_on_both_backends():
    """`(newest IS NULL) DESC` orders identically on PostgreSQL and SQLite; a
    bare ASC puts NULLs last on one and first on the other."""
    conn = _RecordingConn({"FROM infra_designs": []})
    cfg = {"design_table": "infra_designs", "asmt_table": "idc_assessments",
           "asmt_time_col": "created_at"}
    reflex.stale_designs_ranked(conn, cfg, 7)
    sql, _params = conn.executed[-1]
    assert "IS NULL) DESC" in sql and "ORDER BY d.id" not in sql, sql


def test_the_budget_number_was_not_raised():
    """A starving roster is an ORDER problem before it is a size problem."""
    assert reflex.DEFAULT_MAX_PER_RUN == 25
    assert reflex.DEFAULT_STALE_AFTER_DAYS == 7


def test_the_starvation_survey_is_unmeasurable_over_runs_that_recorded_nothing(monkeypatch):
    """The 17 pre-fix rows carry {}. A survey over only those must say it cannot
    tell — never 'nothing starved'."""
    class _Conn:
        def set_security_context(self, _v):
            return None
        def execute(self, _sql, _params=()):
            return _Cur([{"details": "{}"}, {"details": None}])
        def close(self):
            return None
    import tools.db.storage as storage
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _Conn())
    out = reflex.starvation_survey(runs=5)
    assert out["status"] == "unmeasurable"
    assert out["runs_without_report"] == 2 and out["runs_with_report"] == 0
    assert out["starved"] == []


def test_the_starvation_survey_intersects_recorded_skips(monkeypatch):
    class _Conn:
        def set_security_context(self, _v):
            return None
        def execute(self, _sql, _params=()):
            return _Cur([
                {"details": json.dumps({"skipped_over_budget": ["infra:a", "infra:b"]})},
                {"details": json.dumps({"skipped_over_budget": ["infra:b", "infra:c"]})},
                {"details": "{}"},
            ])
        def close(self):
            return None
    import tools.db.storage as storage
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _Conn())
    out = reflex.starvation_survey(runs=5)
    assert out["status"] == "measured"
    assert out["starved"] == ["infra:b"]
    assert out["skip_counts"] == {"infra:a": 1, "infra:b": 2, "infra:c": 1}
    assert out["runs_without_report"] == 1
