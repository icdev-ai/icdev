# CUI // SP-CTI
"""Tests for tools/foundry/harness_bridge.py — ACF <-> Genesis Harness bridge (acf-ada-01).

Hermetic: a throwaway file-backed SQLite DB holds a minimal ``harness_eval`` slice
plus the ``foundry_concepts`` table. ``tools.db.storage.get_connection`` is repointed
at the temp DB so record_acf_decision / record_acf_outcome / compute_acf_metrics all
run end-to-end against a real backend without touching the platform database.

The test exercises BOTH halves of the bridge:
  1. record_acf_decision (called by engine._record_harness_decisions) writes a
     harness_eval row with decision_type='acf_approve' for an approved concept.
  2. record_acf_outcome (called by learner._forward_harness_outcome) updates the
     matching decision row with the actual_outcome.
  3. compute_acf_metrics() returns precision/recall/ECE for decision_type='acf_approve'
     (the standard harness compute_metrics shape, with the 'acf' reflex name).

Acceptance coverage (acf-ada-01):
  1. harness_bridge.py exists and imports cleanly
  2. a mocked engine cycle that emits an approved concept writes a harness_eval
     decision row with decision='acf_approve'
  3. a mocked learner outcome writes a harness_eval outcome row (actual_outcome
     populated) for the matching slug
  4. compute_metrics() for reflex='acf' returns the precision/recall/ECE shape
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path

import pytest


# Ensure repo root is on sys.path (mirrors conftest behavior for direct pytest runs).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# Minimal slices of the tables the bridge touches. harness_eval schema mirrors the
# column shape in tools/db/schema/pg_consolidated.sql.
_HARNESS_DDL = """
CREATE TABLE harness_eval (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL DEFAULT '',
    reflex          TEXT NOT NULL,
    decision        TEXT NOT NULL,
    confidence      REAL,
    metadata_json   TEXT DEFAULT '{}',
    actual_outcome  TEXT,
    resolved_at     TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX idx_harness_eval_reflex ON harness_eval(reflex, created_at);
CREATE INDEX idx_harness_eval_task ON harness_eval(task_id);
"""


def _new_conn(path: str) -> sqlite3.Connection:
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture
def db(tmp_path, monkeypatch):
    """File-backed SQLite DB with harness_eval table; get_connection repointed."""
    path = str(tmp_path / "harness.db")
    boot = _new_conn(path)
    boot.executescript(_HARNESS_DDL)
    boot.commit()
    boot.close()

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _new_conn(path))
    return path


@pytest.fixture
def bridge(db):
    """Import the bridge module against the repointed get_connection."""
    # Reload to pick up the patched get_connection; module is import-time-clean.
    from tools.foundry import harness_bridge
    importlib.reload(harness_bridge)
    return harness_bridge


# --------------------------------------------------------------------------- #
# Acceptance 1 — module exists and imports cleanly
# --------------------------------------------------------------------------- #
def test_module_imports_cleanly():
    from tools.foundry import harness_bridge
    assert hasattr(harness_bridge, "record_acf_decision")
    assert hasattr(harness_bridge, "record_acf_outcome")
    assert hasattr(harness_bridge, "compute_acf_metrics")
    assert hasattr(harness_bridge, "is_bridge_available")
    # Decision-type constants exported so engine.py / learner.py don't hard-code strings.
    assert harness_bridge.DECISION_APPROVE == "acf_approve"
    assert harness_bridge.DECISION_REJECT == "acf_reject"
    assert harness_bridge.DECISION_SKIP == "acf_skip"
    assert harness_bridge.REFLEX_ACF == "acf"


def test_is_bridge_available_true_when_table_exists(bridge, db):
    assert bridge.is_bridge_available() is True


# --------------------------------------------------------------------------- #
# Acceptance 2 — mocked engine run_cycle writes a decision row
# --------------------------------------------------------------------------- #
def test_record_acf_decision_writes_approved_row(bridge, db):
    """Simulate engine._record_harness_decisions emitting one approved concept."""
    row_id = bridge.record_acf_decision(
        slug="maritime-sanction-evasion-tracker",
        decision_type=bridge.DECISION_APPROVE,
        confidence=0.73,
        metadata={"run_id": "rc-2026-06-08-001", "novelty_score": 0.65},
    )
    # The bridge returns a row id (or the disabled sentinel). It must NOT be the
    # sentinel because the harness_eval table is seeded in the fixture.
    assert row_id != bridge._BRIDGE_DISABLED
    assert row_id  # non-empty string

    c = _new_conn(db)
    try:
        rows = c.execute(
            "SELECT task_id, reflex, decision, confidence, metadata_json "
            "FROM harness_eval WHERE id=?",
            (row_id,),
        ).fetchall()
    finally:
        c.close()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["task_id"] == "maritime-sanction-evasion-tracker"
    assert row["reflex"] == "acf"
    assert row["decision"] == "acf_approve"
    # Confidence clamped/coerced to float
    assert abs(row["confidence"] - 0.73) < 1e-6


def test_record_acf_decision_writes_rejected_row(bridge, db):
    """Negative-class row: rejected concept (CoD no-build OR score-gate miss)."""
    row_id = bridge.record_acf_decision(
        slug="internal-timesheet-reminder",
        decision_type=bridge.DECISION_REJECT,
        confidence=0.12,
        metadata={"run_id": "rc-2026-06-08-001", "reject_reason": "score_below_floor"},
    )
    c = _new_conn(db)
    try:
        row = c.execute(
            "SELECT task_id, reflex, decision, confidence FROM harness_eval WHERE id=?",
            (row_id,),
        ).fetchone()
    finally:
        c.close()
    assert dict(row)["decision"] == "acf_reject"


def test_record_acf_decision_clamps_confidence(bridge, db):
    """Confidence >1.0 clamped to 1.0; <0.0 clamped to 0.0; bad input -> NULL."""
    # High
    rid_hi = bridge.record_acf_decision(
        slug="hi", decision_type=bridge.DECISION_APPROVE, confidence=1.7,
    )
    # Low
    rid_lo = bridge.record_acf_decision(
        slug="lo", decision_type=bridge.DECISION_APPROVE, confidence=-0.3,
    )
    # Bad
    rid_bd = bridge.record_acf_decision(
        slug="bd", decision_type=bridge.DECISION_APPROVE, confidence="not-a-number",
    )

    c = _new_conn(db)
    try:
        rows = {
            r["task_id"]: r["confidence"]
            for r in c.execute("SELECT task_id, confidence FROM harness_eval").fetchall()
        }
    finally:
        c.close()
    assert rows["hi"] == 1.0
    assert rows["lo"] == 0.0
    assert rows["bd"] is None  # unparseable -> None


def test_record_acf_decision_normalizes_unknown_type(bridge, db):
    """Unknown decision_type falls back to 'acf_skip' (rate-limited, etc)."""
    row_id = bridge.record_acf_decision(
        slug="skipped", decision_type="something_weird", confidence=0.5,
    )
    c = _new_conn(db)
    try:
        row = c.execute(
            "SELECT decision FROM harness_eval WHERE id=?", (row_id,),
        ).fetchone()
    finally:
        c.close()
    assert row["decision"] == "acf_skip"


def test_record_acf_decision_no_harness_returns_disabled(monkeypatch, tmp_path):
    """If eval_harness is not importable, return the disabled sentinel (no crash)."""
    # Repoint get_connection at a path with NO harness_eval table — the import
    # path is the same as production (no DDL is auto-created), so we test the
    # disabled-sentinel by replacing _try_import_harness directly.
    from tools.foundry import harness_bridge
    monkeypatch.setattr(harness_bridge, "_try_import_harness", lambda: None)
    result = harness_bridge.record_acf_decision(
        slug="x", decision_type=harness_bridge.DECISION_APPROVE, confidence=0.5,
    )
    assert result == harness_bridge._BRIDGE_DISABLED


# --------------------------------------------------------------------------- #
# Acceptance 3 — mocked learner outcome writes the actual_outcome column
# --------------------------------------------------------------------------- #
def test_record_acf_outcome_updates_matching_decision(bridge, db):
    """Engine records an approve; learner later records shipped → row updated."""
    row_id = bridge.record_acf_decision(
        slug="alpha", decision_type=bridge.DECISION_APPROVE, confidence=0.80,
    )
    assert row_id != bridge._BRIDGE_DISABLED

    updated = bridge.record_acf_outcome(slug="alpha", actual_outcome="shipped")
    assert updated is True

    c = _new_conn(db)
    try:
        row = c.execute(
            "SELECT decision, actual_outcome, resolved_at FROM harness_eval WHERE id=?",
            (row_id,),
        ).fetchone()
    finally:
        c.close()
    # 'shipped' is normalized to 'resolved' so harness compute_metrics treats it
    # as a positive-class outcome.
    assert row["actual_outcome"] == "resolved"
    assert row["resolved_at"]  # populated, ISO-8601


def test_record_acf_outcome_vv_pass_normalized(bridge, db):
    """vv_pass is a positive outcome (V&V oracle passed) → normalized to 'resolved'."""
    bridge.record_acf_decision(
        slug="vv-1", decision_type=bridge.DECISION_APPROVE, confidence=0.65,
    )
    bridge.record_acf_outcome(slug="vv-1", actual_outcome="vv_pass")

    c = _new_conn(db)
    try:
        row = c.execute(
            "SELECT actual_outcome FROM harness_eval WHERE task_id='vv-1'"
        ).fetchone()
    finally:
        c.close()
    assert row["actual_outcome"] == "resolved"


def test_record_acf_outcome_vv_fail_kept_as_is(bridge, db):
    """vv_fail stays 'vv_fail' so it counts as a known negative outcome."""
    bridge.record_acf_decision(
        slug="vv-2", decision_type=bridge.DECISION_APPROVE, confidence=0.65,
    )
    bridge.record_acf_outcome(slug="vv-2", actual_outcome="vv_fail")

    c = _new_conn(db)
    try:
        row = c.execute(
            "SELECT actual_outcome FROM harness_eval WHERE task_id='vv-2'"
        ).fetchone()
    finally:
        c.close()
    assert row["actual_outcome"] == "vv_fail"


def test_record_acf_outcome_abandoned(bridge, db):
    """abandoned outcome records verbatim — counted as a known outcome (negative)."""
    bridge.record_acf_decision(
        slug="abd-1", decision_type=bridge.DECISION_APPROVE, confidence=0.65,
    )
    bridge.record_acf_outcome(slug="abd-1", actual_outcome="abandoned")

    c = _new_conn(db)
    try:
        row = c.execute(
            "SELECT actual_outcome FROM harness_eval WHERE task_id='abd-1'"
        ).fetchone()
    finally:
        c.close()
    assert row["actual_outcome"] == "abandoned"


def test_record_acf_outcome_no_matching_decision_returns_false(bridge, db):
    """Calling outcome before decision → False, no row written."""
    updated = bridge.record_acf_outcome(slug="never-decided", actual_outcome="shipped")
    assert updated is False

    c = _new_conn(db)
    try:
        n = c.execute("SELECT COUNT(*) AS n FROM harness_eval").fetchone()["n"]
    finally:
        c.close()
    assert n == 0


def test_record_acf_outcome_idempotent(bridge, db):
    """A second outcome call for the same slug is a no-op (only first wins)."""
    bridge.record_acf_decision(
        slug="once", decision_type=bridge.DECISION_APPROVE, confidence=0.5,
    )
    first = bridge.record_acf_outcome(slug="once", actual_outcome="shipped")
    second = bridge.record_acf_outcome(slug="once", actual_outcome="vv_fail")
    assert first is True
    assert second is False  # already resolved

    c = _new_conn(db)
    try:
        row = c.execute(
            "SELECT actual_outcome FROM harness_eval WHERE task_id='once'"
        ).fetchone()
    finally:
        c.close()
    # First-write-wins: stays 'resolved' (the positive normalization).
    assert row["actual_outcome"] == "resolved"


def test_record_acf_outcome_empty_slug_returns_false(bridge, db):
    assert bridge.record_acf_outcome(slug="", actual_outcome="shipped") is False


# --------------------------------------------------------------------------- #
# Acceptance 4 — compute_metrics returns the precision/recall/ECE shape
# --------------------------------------------------------------------------- #
def test_compute_acf_metrics_returns_full_shape(bridge, db):
    """Seed a deterministic set: 2 approved→resolved, 1 approved→vv_fail.
    The bridge delegates to harness.compute_metrics('acf') which returns
    precision/recall/ece/total_decisions."""
    # 2 positive outcomes (resolved) and 1 negative (vv_fail)
    for slug, conf in [("a", 0.80), ("b", 0.70), ("c", 0.60)]:
        bridge.record_acf_decision(
            slug=slug, decision_type=bridge.DECISION_APPROVE, confidence=conf,
        )
    bridge.record_acf_outcome(slug="a", actual_outcome="shipped")  # → resolved
    bridge.record_acf_outcome(slug="b", actual_outcome="vv_pass")  # → resolved
    bridge.record_acf_outcome(slug="c", actual_outcome="vv_fail")  # stays vv_fail

    m = bridge.compute_acf_metrics(window_days=30)
    assert m.get("available") is True
    assert m.get("reflex") == "acf"
    # compute_metrics is the standard harness function — verify its shape
    assert "precision" in m
    assert "recall" in m
    assert "ece" in m
    assert "total_decisions" in m
    # 2 resolved / 2 (resolved+vv_fail)  = 1.0
    # 2 resolved / 3 known outcomes      = 0.6667 (harness rounds to 4 dp)
    assert m["total_decisions"] == 3
    assert abs(m["precision"] - 1.0) < 1e-9
    # harness.compute_metrics rounds precision/recall/ece to 4 decimal places
    assert abs(m["recall"] - round(2 / 3, 4)) < 1e-4


def test_compute_acf_metrics_empty(bridge, db):
    """No decisions recorded → total_decisions==0, no precision/recall."""
    m = bridge.compute_acf_metrics(window_days=30)
    assert m.get("available") is True
    assert m.get("total_decisions") == 0
    # harness returns precision=None / recall=None when there are no known outcomes
    assert m.get("precision") is None
    assert m.get("recall") is None


def test_compute_acf_metrics_unavailable(monkeypatch):
    """If eval_harness is not importable, compute returns available=False."""
    from tools.foundry import harness_bridge
    monkeypatch.setattr(harness_bridge, "_try_import_harness", lambda: None)
    m = harness_bridge.compute_acf_metrics(window_days=30)
    assert m.get("available") is False
    assert m.get("total_decisions") == 0
    assert m.get("reflex") == "acf"


# --------------------------------------------------------------------------- #
# Engine integration — _record_harness_decisions end-to-end (smoke)
# --------------------------------------------------------------------------- #
def test_engine_records_decisions_for_all_concepts(bridge, db, monkeypatch):
    """Simulate the engine call site: a cycle with 1 approved + 1 rejected + 1 skip."""
    from tools.foundry.engine import _record_harness_decisions

    approved = [{
        "slug": "good-concept",
        "status": "approved",
        "composite_score": 0.78,
        "novelty_score": 0.65,
    }]
    all_concepts = approved + [
        {"slug": "bad-concept", "status": "rejected",
         "composite_score": 0.21, "reject_reason": "score_below_floor"},
        {"slug": "skipped-concept", "status": "proposed", "composite_score": 0.55},
    ]
    _record_harness_decisions("rc-test-001", approved, all_concepts)

    c = _new_conn(db)
    try:
        rows = {
            r["task_id"]: r["decision"]
            for r in c.execute(
                "SELECT task_id, decision FROM harness_eval ORDER BY task_id"
            ).fetchall()
        }
    finally:
        c.close()
    assert rows == {
        "bad-concept": "acf_reject",
        "good-concept": "acf_approve",
        "skipped-concept": "acf_skip",
    }


# --------------------------------------------------------------------------- #
# Learner integration — _forward_harness_outcome end-to-end (smoke)
# --------------------------------------------------------------------------- #
def test_learner_forward_records_outcome(bridge, db, monkeypatch):
    """Simulate the learner call site: a concept's outcome is forwarded."""
    from tools.foundry.learner import _forward_harness_outcome

    bridge.record_acf_decision(
        slug="learner-1",
        decision_type=bridge.DECISION_APPROVE,
        confidence=0.71,
    )
    ok = _forward_harness_outcome("learner-1", "shipped")
    assert ok is True

    c = _new_conn(db)
    try:
        row = c.execute(
            "SELECT actual_outcome FROM harness_eval WHERE task_id='learner-1'"
        ).fetchone()
    finally:
        c.close()
    assert row["actual_outcome"] == "resolved"


def test_learner_forward_handles_missing_bridge(monkeypatch):
    """A missing harness_bridge import must NOT crash the learner outcome loop."""
    from tools.foundry import learner

    # Simulate the bridge not being shipped yet (pre-init / air-gap).
    monkeypatch.setattr(learner, "harness_bridge", None, raising=False)
    # The learner path uses `from tools.foundry import harness_bridge` inside the
    # function, so a sys.modules scrub is the cleanest reproducer.
    monkeypatch.setitem(sys.modules, "tools.foundry.harness_bridge", None)

    # Should swallow the ImportError and return False — never raise.
    result = learner._forward_harness_outcome("any-slug", "shipped")
    assert result is False
