# [TEMPLATE: CUI // SP-CTI]
"""Tests for the pWin (probability-of-win) model in
tools/govcon/bayesian_bid_scorer.py (prop-cap-12).

Covers: compute_pwin (deterministic logistic/weighted score over 5
capture-plan signals), get_pwin_assessment (retrieval), pipeline_value_rollup
(weighted pipeline value roll-up). The backend model, API routes
(tools/dashboard/api/govcon.py), and frontend (govcon/pipeline.html: rollup
summary card, per-row pWin bar + weighted value, factor-breakdown popup via
showFactors()) were already implemented; this file closes the test-coverage
gap (zero tests existed for any of it before this task).
"""
import sqlite3

import pytest

from tools.db.storage import translate_sql


class _TranslatingConn:
    """Wraps a raw sqlite3 connection, translating %s -> ? (via
    tools.db.storage.translate_sql) before executing.

    tools/govcon/bayesian_bid_scorer.py's queries are written in Postgres-
    style %s syntax (correct for the real backend, reached in production via
    get_connection()'s own translation); a bare sqlite3.connect() mock
    doesn't understand %s at all. Same root cause already documented in
    prop-fix-10/11 and prop-cap-11's PRs, not something new here.
    """
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return self._conn.execute(translate_sql(sql, backend="sqlite"), params)

    def executemany(self, sql, seq):
        return self._conn.executemany(translate_sql(sql, backend="sqlite"), seq)

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Isolated SQLite DB with just the tables bayesian_bid_scorer touches."""
    db_file = tmp_path / "pwin_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE proposal_opportunities (
            id TEXT PRIMARY KEY,
            title TEXT,
            status TEXT DEFAULT 'intake',
            estimated_value_low REAL,
            estimated_value_high REAL,
            win_probability REAL
        );
        CREATE TABLE audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT, event_type TEXT, actor TEXT,
            action TEXT, details TEXT, project_id TEXT, session_id TEXT
        );
    """)
    conn.commit()
    conn.close()

    import tools.govcon.bayesian_bid_scorer as bbs

    def _get_conn():
        c = sqlite3.connect(str(db_file))
        c.row_factory = sqlite3.Row
        return _TranslatingConn(c)

    monkeypatch.setattr(bbs, "get_connection", _get_conn)
    return db_file


def _conn(db_file):
    c = sqlite3.connect(str(db_file))
    c.row_factory = sqlite3.Row
    return c


# ── compute_pwin ──────────────────────────────────────────────────────────


def test_compute_pwin_all_neutral_factors_yields_50_percent(db):
    from tools.govcon.bayesian_bid_scorer import compute_pwin

    result = compute_pwin("opp-1", {})
    assert result["status"] == "ok"
    # All factors default to 0.5 (neutral) -> z = 0 -> logistic(0) = 0.5
    assert result["pwin_score"] == 0.5
    assert result["pwin_pct"] == 50
    assert result["z_score"] == 0.0


def test_compute_pwin_all_favorable_factors_yields_high_pwin(db):
    from tools.govcon.bayesian_bid_scorer import compute_pwin

    result = compute_pwin("opp-1", {
        "incumbency": 1.0, "crm_engagement": 1.0, "competitive_position": 1.0,
        "compliance_coverage": 1.0, "past_performance_fit": 1.0,
    })
    assert result["pwin_pct"] > 90


def test_compute_pwin_all_unfavorable_factors_yields_low_pwin(db):
    from tools.govcon.bayesian_bid_scorer import compute_pwin

    result = compute_pwin("opp-1", {
        "incumbency": 0.0, "crm_engagement": 0.0, "competitive_position": 0.0,
        "compliance_coverage": 0.0, "past_performance_fit": 0.0,
    })
    assert result["pwin_pct"] < 10


def test_compute_pwin_incumbency_has_largest_single_factor_swing(db):
    """PWIN_WEIGHTS gives incumbency the highest weight (0.30) -- flipping it
    alone from 0 to 1 should move pWin more than flipping crm_engagement
    alone (weight 0.10, the smallest)."""
    from tools.govcon.bayesian_bid_scorer import compute_pwin

    base = compute_pwin("opp-1", {})["pwin_pct"]
    incumbency_high = compute_pwin("opp-2", {"incumbency": 1.0})["pwin_pct"]
    crm_high = compute_pwin("opp-3", {"crm_engagement": 1.0})["pwin_pct"]

    assert (incumbency_high - base) > (crm_high - base)


def test_compute_pwin_clamps_out_of_range_factors(db):
    from tools.govcon.bayesian_bid_scorer import compute_pwin

    result = compute_pwin("opp-1", {"incumbency": 5.0, "crm_engagement": -3.0})
    assert result["factor_breakdown"]["incumbency"]["score"] == 1.0
    assert result["factor_breakdown"]["crm_engagement"]["score"] == 0.0


def test_compute_pwin_factor_breakdown_has_all_five_factors_with_weights(db):
    from tools.govcon.bayesian_bid_scorer import compute_pwin, PWIN_FACTORS, PWIN_WEIGHTS

    result = compute_pwin("opp-1", {"incumbency": 0.8})
    breakdown = result["factor_breakdown"]
    assert set(breakdown.keys()) == set(PWIN_FACTORS)
    for f in PWIN_FACTORS:
        assert breakdown[f]["weight"] == PWIN_WEIGHTS[f]
    assert breakdown["incumbency"]["score"] == 0.8


def test_compute_pwin_computes_weighted_value_from_estimated_value(db):
    from tools.govcon.bayesian_bid_scorer import compute_pwin

    result = compute_pwin("opp-1", {}, estimated_value=1_000_000)
    # pwin_score is 0.5 for all-neutral factors
    assert result["weighted_value"] == 500_000.0


def test_compute_pwin_no_weighted_value_without_estimate(db):
    from tools.govcon.bayesian_bid_scorer import compute_pwin

    result = compute_pwin("opp-1", {})
    assert result["weighted_value"] is None


def test_compute_pwin_persists_assessment_row(db):
    from tools.govcon.bayesian_bid_scorer import compute_pwin

    compute_pwin("opp-1", {"incumbency": 0.9})
    row = _conn(db).execute(
        "SELECT * FROM pg_pwin_assessments WHERE opportunity_id = 'opp-1'"
    ).fetchone()
    assert row is not None
    assert row["incumbency"] == 0.9
    assert row["method"] == "logistic_weighted"


def test_compute_pwin_writes_audit_trail_entry(db):
    from tools.govcon.bayesian_bid_scorer import compute_pwin

    compute_pwin("opp-1", {})
    row = _conn(db).execute(
        "SELECT * FROM audit_trail WHERE event_type = 'pwin.compute'"
    ).fetchone()
    assert row is not None
    assert row["project_id"] == "opp-1"


# ── get_pwin_assessment ───────────────────────────────────────────────────


def test_get_pwin_assessment_returns_none_when_unscored(db):
    from tools.govcon.bayesian_bid_scorer import get_pwin_assessment

    assert get_pwin_assessment("no-such-opp") is None


def test_get_pwin_assessment_returns_most_recent(db):
    from tools.govcon.bayesian_bid_scorer import compute_pwin, get_pwin_assessment

    compute_pwin("opp-1", {"incumbency": 0.2})
    compute_pwin("opp-1", {"incumbency": 0.9})  # re-assessment, newer

    result = get_pwin_assessment("opp-1")
    assert result["incumbency"] == 0.9
    assert isinstance(result["factor_breakdown"], dict)


# ── pipeline_value_rollup ─────────────────────────────────────────────────


def test_pipeline_value_rollup_empty_pipeline(db):
    from tools.govcon.bayesian_bid_scorer import pipeline_value_rollup

    result = pipeline_value_rollup()
    assert result["total_weighted_pipeline_value"] == 0
    assert result["opportunities"] == []


def test_pipeline_value_rollup_excludes_closed_opportunities(db):
    from tools.govcon.bayesian_bid_scorer import pipeline_value_rollup

    conn = _conn(db)
    conn.executemany(
        "INSERT INTO proposal_opportunities (id, title, status, estimated_value_low, estimated_value_high) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("opp-active", "Active Opp", "writing", 1_000_000, 1_000_000),
            ("opp-won", "Won Opp", "won", 2_000_000, 2_000_000),
            ("opp-lost", "Lost Opp", "lost", 3_000_000, 3_000_000),
            ("opp-nobid", "No-Bid Opp", "no_bid", 4_000_000, 4_000_000),
        ],
    )
    conn.commit()
    conn.close()

    result = pipeline_value_rollup()
    opp_ids = {o["opportunity_id"] for o in result["opportunities"]}
    assert opp_ids == {"opp-active"}


def test_pipeline_value_rollup_uses_computed_pwin_when_available(db):
    from tools.govcon.bayesian_bid_scorer import compute_pwin, pipeline_value_rollup

    conn = _conn(db)
    conn.execute(
        "INSERT INTO proposal_opportunities (id, title, status, estimated_value_low, estimated_value_high) "
        "VALUES ('opp-1', 'Test', 'writing', 1_000_000, 1_000_000)"
    )
    conn.commit()
    conn.close()

    compute_pwin("opp-1", {
        "incumbency": 1.0, "crm_engagement": 1.0, "competitive_position": 1.0,
        "compliance_coverage": 1.0, "past_performance_fit": 1.0,
    })

    result = pipeline_value_rollup()
    assert result["scored_count"] == 1
    assert result["unscored_count"] == 0
    item = result["opportunities"][0]
    assert item["has_pwin_model"] is True
    assert item["pwin_pct"] > 90
    assert item["factor_breakdown"] is not None


def test_pipeline_value_rollup_falls_back_to_stored_win_probability(db):
    from tools.govcon.bayesian_bid_scorer import pipeline_value_rollup

    conn = _conn(db)
    conn.execute(
        "INSERT INTO proposal_opportunities (id, title, status, estimated_value_low, estimated_value_high, win_probability) "
        "VALUES ('opp-1', 'Test', 'writing', 1_000_000, 1_000_000, 65)"
    )
    conn.commit()
    conn.close()

    result = pipeline_value_rollup()
    item = result["opportunities"][0]
    assert item["has_pwin_model"] is False
    assert item["pwin_pct"] == 65
    assert result["unscored_count"] == 1


def test_pipeline_value_rollup_defaults_to_neutral_when_fully_unscored(db):
    from tools.govcon.bayesian_bid_scorer import pipeline_value_rollup

    conn = _conn(db)
    conn.execute(
        "INSERT INTO proposal_opportunities (id, title, status, estimated_value_low, estimated_value_high) "
        "VALUES ('opp-1', 'Test', 'writing', 1_000_000, 1_000_000)"
    )
    conn.commit()
    conn.close()

    result = pipeline_value_rollup()
    item = result["opportunities"][0]
    assert item["pwin_pct"] == 50
    assert item["weighted_value"] == 500_000.0


def test_pipeline_value_rollup_sorts_by_weighted_value_descending(db):
    from tools.govcon.bayesian_bid_scorer import pipeline_value_rollup

    conn = _conn(db)
    conn.executemany(
        "INSERT INTO proposal_opportunities (id, title, status, estimated_value_low, estimated_value_high, win_probability) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("opp-low", "Low", "writing", 100_000, 100_000, 50),
            ("opp-high", "High", "writing", 5_000_000, 5_000_000, 80),
        ],
    )
    conn.commit()
    conn.close()

    result = pipeline_value_rollup()
    ids_in_order = [o["opportunity_id"] for o in result["opportunities"]]
    assert ids_in_order == ["opp-high", "opp-low"]
