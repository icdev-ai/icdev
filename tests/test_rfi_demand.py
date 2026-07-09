# CUI // SP-CTI
"""Tests for the RFI Capability-Gap Demand Loop (tools/govcon/rfi_demand.py).

Unit coverage:
  * hybrid gap rule truth table (capability grade x workbench coverage verdict)
  * gap_hash cross-RFI dedup (order-independent, keyword-fingerprint based)
  * priority math  priority = frequency * (1 - best_coverage)

Integration coverage (isolated SQLite DB, no LLM):
  * aggregate -> emit -> decompose produces a SUGGESTED kanban card with the right
    provenance, aggregates frequency across RFIs, is idempotent, and honours the
    priority gate.
"""
import importlib

import pytest

rd = importlib.import_module("tools.govcon.rfi_demand")


# ---------------------------------------------------------------------------
# Unit: hybrid gap rule truth table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "grade,covered,expected",
    [
        ("N", False, True),      # capability miss + uncovered  -> gap
        ("N", "partial", True),  # capability miss + partial    -> gap
        ("N", True, False),      # capability miss but drafted   -> no gap (we can cover it)
        ("N", None, False),      # not yet judged                -> defer, no gap
        ("L", False, False),     # we have the capability        -> no gap
        ("M", False, False),     # partial capability            -> no gap
        ("L", True, False),
    ],
)
def test_hybrid_rule_truth_table(grade, covered, expected):
    assert rd.is_gap({"covered": covered}, {"grade": grade}) is expected


# ---------------------------------------------------------------------------
# Unit: content-hash dedup
# ---------------------------------------------------------------------------

def test_gap_hash_is_order_independent():
    a = rd.gap_hash(["fedramp", "high", "authorization"])
    b = rd.gap_hash(["authorization", "FedRAMP", "High"])  # different order + case
    assert a == b


def test_gap_hash_distinguishes_different_needs():
    assert rd.gap_hash(["fedramp", "high"]) != rd.gap_hash(["stig", "hardening"])


def test_gap_hash_falls_back_to_text_when_no_keywords():
    h = rd.gap_hash([], "some free text requirement")
    assert h and len(h) == 32


# ---------------------------------------------------------------------------
# Unit: priority math
# ---------------------------------------------------------------------------

def test_priority_math():
    assert rd._compute_priority(1, 0.0) == 1.0
    assert rd._compute_priority(3, 0.0) == 3.0
    assert rd._compute_priority(2, 0.5) == 1.0
    assert rd._compute_priority(5, 0.2) == 4.0


def test_severity_mapping():
    assert rd._severity_for(2.5, 0) == "high"
    assert rd._severity_for(0.5, 1) == "high"     # high-demand overrides low priority
    assert rd._severity_for(1.2, 0) == "medium"
    assert rd._severity_for(0.6, 0) == "low"


def test_grade_requirement_absent_capability_is_gap_grade():
    """A requirement matching nothing in the catalog grades 'N' with 0 coverage."""
    gr = rd.grade_requirement(
        "provide underwater basket weaving telemetry with quantum entanglement sync"
    )
    assert gr["grade"] == "N"
    assert gr["coverage"] == 0.0


# ---------------------------------------------------------------------------
# Integration: aggregate -> emit -> decompose (isolated SQLite DB, no LLM)
# ---------------------------------------------------------------------------

@pytest.fixture
def demand_db(tmp_path, monkeypatch):
    """Isolated SQLite DB with govcon + kanban tables and a minimal
    oracle_predictions table. Forces the deterministic (no-LLM) decomposition."""
    db_path = tmp_path / "rfidem.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.setenv("ICDEV_RFI_DEMAND_ENABLED", "true")
    # Force the deterministic single-task fallback so the test needs no LLM.
    monkeypatch.setattr(rd, "_llm_decompose", lambda need, route, mx: [])

    from tools.db.storage import get_connection
    from tools.govcon.init_db import init_govcon_intelligence_tables
    from tools.kanban.init_db import init_kanban_tables

    init_govcon_intelligence_tables()
    init_kanban_tables()
    conn = get_connection()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS oracle_predictions (
                id TEXT PRIMARY KEY, lens_id TEXT, lens_name TEXT,
                prediction_text TEXT, confidence REAL, created_at TEXT,
                subject_type TEXT, subject_id TEXT, prediction_type TEXT,
                severity TEXT, horizon_days INTEGER, evidence_json TEXT,
                outcome TEXT, classification TEXT)"""
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _seed_gap(need="alpha beta gamma delta capability we lack"):
    kws = rd._keywords_for(need)
    return {"capability_need": need, "keywords": kws, "domain": "other", "coverage": 0.0}, kws


def test_aggregate_frequency_across_rfis(demand_db):
    gap, _ = _seed_gap()
    h1 = rd.aggregate_gap(gap, "rfi-A::sec1")
    h2 = rd.aggregate_gap(gap, "rfi-B::sec2")
    h_dup = rd.aggregate_gap(gap, "rfi-A::sec1")  # repeated ref must not double-count
    assert h1 == h2 == h_dup

    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        row = dict(conn.execute(
            "SELECT frequency, priority, is_high_demand FROM rfi_capability_gaps "
            "WHERE content_hash=%s", (h1,)).fetchone())
    finally:
        conn.close()
    assert row["frequency"] == 2
    assert row["priority"] == 2.0


def test_emit_and_decompose_creates_suggested_card(demand_db):
    gap, _ = _seed_gap("epsilon zeta eta theta capability missing entirely")
    h = rd.aggregate_gap(gap, "rfi-A::s1")
    rd.aggregate_gap(gap, "rfi-B::s2")  # push priority to 2.0 (>= gate)

    pred = rd.emit_gap_prediction(h)
    assert pred and pred.startswith("op-gap-rfi-")

    tasks = rd.decompose_gap_to_tasks(h)
    assert len(tasks) == 1

    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        t = dict(conn.execute(
            "SELECT status, dispatch_source, source_prediction_id "
            "FROM kanban_tasks WHERE id=%s", (tasks[0],)).fetchone())
        p = dict(conn.execute(
            "SELECT lens_name, prediction_type FROM oracle_predictions WHERE id=%s",
            (pred,)).fetchone())
        nlinks = conn.execute(
            "SELECT count(*) FROM rfi_gap_task_links WHERE gap_hash=%s", (h,)).fetchone()[0]
    finally:
        conn.close()

    assert t["status"] == "suggested"
    assert t["dispatch_source"] == "rfi_demand"
    assert t["source_prediction_id"] == pred
    assert p["lens_name"] == "rfi_demand"
    assert p["prediction_type"] == "gap::rfi_capability"
    assert nlinks == 1


def test_decompose_is_idempotent(demand_db):
    gap, _ = _seed_gap("iota kappa lambda capability absent from platform")
    h = rd.aggregate_gap(gap, "rfi-A::s1")
    rd.aggregate_gap(gap, "rfi-B::s2")
    rd.emit_gap_prediction(h)

    first = rd.decompose_gap_to_tasks(h)
    second = rd.decompose_gap_to_tasks(h)
    assert first == second

    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        n = conn.execute(
            "SELECT count(*) FROM kanban_tasks WHERE dispatch_source='rfi_demand'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == len(first)  # no duplicates on re-run


def test_priority_gate_blocks_low_priority(demand_db, monkeypatch):
    """A single-RFI gap (frequency 1, priority 1.0) is blocked when the threshold
    is raised above it — no card is emitted."""
    monkeypatch.setattr(rd, "_load_config", lambda: {**rd._DEFAULTS, "min_priority_for_task": 5.0})
    gap, _ = _seed_gap("mu nu xi omicron one-off capability")
    h = rd.aggregate_gap(gap, "rfi-solo::s1")
    rd.emit_gap_prediction(h)
    tasks = rd.decompose_gap_to_tasks(h)
    assert tasks == []
