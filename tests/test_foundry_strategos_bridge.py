# CUI // SP-CTI
"""Tests for tools/foundry/strategos_bridge.py — ACF → Strategos fan-out (acf-ada-06).

Hermetic: a throwaway file-backed SQLite DB holds minimal ``sg_*`` channel tables.
``tools.db.storage.get_connection`` is repointed at the temp DB (a fresh connection
per call, mirroring production where each channel opens its own short-lived
connection), so fan_out runs end-to-end against a real backend without touching the
platform database. ``conn=None`` is passed so the module exercises its own
connection-per-channel path.

Acceptance coverage (acf-ada-06):
  1. strategos_bridge exposes fan_out(concept) -> dict of channel results
  2. a high-compliance concept queues a hitl_item AND a pir_requirement
  3. a low-risk concept skips all channels (no rows written)
  4. channel failures are caught and logged without crashing the cycle
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest

from tools.foundry import strategos_bridge as sb

# Minimal slices of the four sg_* channel tables (columns the bridge writes).
_SCHEMA = """
CREATE TABLE sg_ghost_signals (
    id text PRIMARY KEY, signal_type text, source text, confidence real,
    detected_at text, behavior_profile_json text
);
CREATE TABLE sg_hitl_items (
    id text PRIMARY KEY, item_type text, ref_id text, payload text,
    status text, created_at text
);
CREATE TABLE sg_pir_requirements (
    id text PRIMARY KEY, pir_type text, topic text, description text,
    collection_priority integer, status text, created_at text, updated_at text
);
CREATE TABLE sg_intelligence_briefs (
    id text PRIMARY KEY, brief_type text, title text, content_md text,
    sio_confidence real, analyst_reviewed integer, created_at text
);
"""


def _new_conn(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture
def db(tmp_path, monkeypatch):
    """File-backed SQLite DB with the sg_* channel tables; get_connection repointed."""
    path = str(tmp_path / "strat.db")
    boot = _new_conn(path)
    boot.executescript(_SCHEMA)
    boot.commit()
    boot.close()

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _new_conn(path))
    return path


def _count(path, table):
    c = _new_conn(path)
    try:
        return c.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    finally:
        c.close()


def _high_compliance_concept(**over):
    base = {
        "id": 1,
        "name": "Maritime Sanction Evasion Tracker",
        "slug": "maritime-sanction-evasion-tracker",
        "status": "approved",
        "problem_statement": "Adversary vessels go dark to evade sanctions interdiction.",
        "proposed_capability": "Correlate dark-web leaks with AIS gaps to flag evasion.",
        "compliance_risk": 0.85,
        "market_score": 0.40,
        "novelty_score": 0.72,
        "composite_score": 0.66,
    }
    base.update(over)
    return base


def _low_risk_concept(**over):
    base = {
        "id": 2,
        "name": "Internal Timesheet Reminder",
        "slug": "internal-timesheet-reminder",
        "status": "approved",
        "problem_statement": "Staff forget to submit weekly timesheets.",
        "proposed_capability": "Nudge users with a friendly reminder.",
        "compliance_risk": 0.10,
        "market_score": 0.20,
        "novelty_score": 0.30,
        "composite_score": 0.35,
    }
    base.update(over)
    return base


# ── Acceptance 1: public API shape ───────────────────────────────────────────
def test_fan_out_returns_channel_result_dict(db):
    result = sb.fan_out(_high_compliance_concept())
    assert callable(sb.fan_out)
    assert set(result["channels"]) == set(sb.CHANNELS)
    assert result["qualified"] is True
    assert result["errors"] == []
    for name in sb.CHANNELS:
        assert "routed" in result["channels"][name]


# ── Acceptance 2: high-compliance concept queues hitl_item + pir_requirement ──
def test_high_compliance_queues_hitl_and_pir(db):
    result = sb.fan_out(_high_compliance_concept())

    assert result["channels"]["hitl_items"]["routed"] is True
    assert result["channels"]["pir_requirements"]["routed"] is True
    assert _count(db, "sg_hitl_items") == 1
    assert _count(db, "sg_pir_requirements") == 1

    # The hitl row references the concept and is pending analyst review.
    c = _new_conn(db)
    try:
        row = c.execute("SELECT item_type, ref_id, status FROM sg_hitl_items").fetchone()
    finally:
        c.close()
    assert row["item_type"] == "acf_concept"
    assert row["ref_id"] == "1"
    assert row["status"] == "pending"


def test_high_market_score_also_qualifies(db):
    """compliance_risk low but market_score high still qualifies (OR gate)."""
    concept = _high_compliance_concept(compliance_risk=0.10, market_score=0.80)
    result = sb.fan_out(concept)
    assert result["qualified"] is True
    assert _count(db, "sg_hitl_items") == 1
    assert _count(db, "sg_pir_requirements") == 1


def test_ghost_signal_keyword_gated(db):
    """A qualifying concept with national-security prose triggers a ghost signal."""
    result = sb.fan_out(_high_compliance_concept())
    assert result["channels"]["ghost_signals"]["routed"] is True
    assert _count(db, "sg_ghost_signals") == 1


def test_ghost_signal_skipped_without_keywords(db):
    """A qualifying concept with no threat keywords skips the ghost channel but
    still routes the other channels."""
    concept = _high_compliance_concept(
        problem_statement="Finance team needs faster invoice reconciliation.",
        proposed_capability="Auto-match invoices to purchase orders.",
        name="Invoice Reconciler",
        slug="invoice-reconciler",
    )
    result = sb.fan_out(concept)
    assert result["channels"]["ghost_signals"]["routed"] is False
    assert _count(db, "sg_ghost_signals") == 0
    # Other channels still routed.
    assert _count(db, "sg_hitl_items") == 1
    assert _count(db, "sg_pir_requirements") == 1
    assert _count(db, "sg_intelligence_briefs") == 1


def test_intelligence_brief_generated(db):
    result = sb.fan_out(_high_compliance_concept())
    assert result["channels"]["intelligence_briefs"]["routed"] is True
    c = _new_conn(db)
    try:
        row = c.execute("SELECT brief_type, title FROM sg_intelligence_briefs").fetchone()
    finally:
        c.close()
    assert row["brief_type"] == "assessment"
    assert "Maritime Sanction Evasion Tracker" in row["title"]


# ── Acceptance 3: low-risk concept skips all channels ────────────────────────
def test_low_risk_concept_skips_all_channels(db):
    result = sb.fan_out(_low_risk_concept())

    assert result["qualified"] is False
    assert result["skip_reason"]
    for name in sb.CHANNELS:
        assert result["channels"][name]["routed"] is False
    assert _count(db, "sg_ghost_signals") == 0
    assert _count(db, "sg_hitl_items") == 0
    assert _count(db, "sg_pir_requirements") == 0
    assert _count(db, "sg_intelligence_briefs") == 0


def test_non_approved_concept_skips_all(db):
    """Even a high-risk concept fans out nothing until it is approved."""
    concept = _high_compliance_concept(status="scored")
    result = sb.fan_out(concept)
    assert result["qualified"] is False
    assert _count(db, "sg_hitl_items") == 0


# ── Acceptance 4: channel failure is caught, others continue ─────────────────
def test_channel_failure_is_isolated(db, monkeypatch):
    """If one channel raises, the others still route and the error is recorded —
    the cycle does not crash."""
    def _boom(*a, **k):
        raise RuntimeError("pir table is on fire")

    monkeypatch.setitem(sb._ROUTERS, "pir_requirements", _boom)

    result = sb.fan_out(_high_compliance_concept())

    # The cycle returned normally (no exception escaped).
    assert result["channels"]["pir_requirements"]["routed"] is False
    assert "error" in result["channels"]["pir_requirements"]
    assert result["errors"] and result["errors"][0]["channel"] == "pir_requirements"

    # The other channels still wrote their rows.
    assert result["channels"]["hitl_items"]["routed"] is True
    assert _count(db, "sg_hitl_items") == 1
    assert _count(db, "sg_intelligence_briefs") == 1


# ── dry-run writes nothing ───────────────────────────────────────────────────
def test_dry_run_decides_but_writes_nothing(db):
    result = sb.fan_out(_high_compliance_concept(), dry_run=True)
    assert result["qualified"] is True
    assert result["dry_run"] is True
    # Routing decisions still reported…
    assert result["channels"]["hitl_items"]["routed"] is True
    # …but nothing persisted.
    assert _count(db, "sg_hitl_items") == 0
    assert _count(db, "sg_pir_requirements") == 0
    assert _count(db, "sg_ghost_signals") == 0
    assert _count(db, "sg_intelligence_briefs") == 0
