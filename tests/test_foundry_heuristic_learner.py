# CUI // SP-CTI
"""Tests for tools/foundry/heuristic_learner.py — ACF scorer-weight co-learning (acf-ada-07).

Hermetic: a throwaway file-backed SQLite DB holds the foundry_* tables seeded with
concept score profiles + outcomes. ``tools.db.storage.get_connection`` is repointed
at the temp DB (fresh connection per call), so extract_error_cases / extract_baseline
run end-to-end against a real backend without touching the platform database.

Acceptance coverage (acf-ada-07):
  1. heuristic_learner exposes extract_error_cases() and propose_amendments()
  2. a vv_fail fixture yields >= 1 proposed amendment
  3. a shipped(-only) fixture yields zero proposals
  4. every proposal carries a confidence score and a trace_id
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest

from tools.foundry import heuristic_learner as hl
from tools.foundry.db.init_db import _SCHEMA_SQLITE


def _new_conn(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def _insert_concept(conn, cid, slug, *, nov, mkt, fit, eff, comp, composite):
    conn.execute(
        "INSERT INTO foundry_concepts "
        "(id, run_id, name, slug, novelty_score, market_score, fit_score, "
        " effort_estimate, compliance_risk, composite_score, status) "
        "VALUES (?, '1', ?, ?, ?, ?, ?, ?, ?, ?, 'approved')",
        (cid, slug.upper(), slug, nov, mkt, fit, eff, comp, composite),
    )


def _insert_outcome(conn, cid, outcome):
    conn.execute(
        "INSERT INTO foundry_outcomes (concept_id, outcome, metric) VALUES (?, ?, 1.0)",
        (cid, outcome),
    )


@pytest.fixture
def db(tmp_path, monkeypatch):
    """File-backed SQLite DB with foundry_* schema; get_connection repointed at it."""
    path = str(tmp_path / "heur.db")
    boot = _new_conn(path)
    boot.executescript(_SCHEMA_SQLITE)
    boot.commit()
    boot.close()

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _new_conn(path))
    return path


def _seed_vv_fail(path):
    """Two vv_fail concepts that share a high-compliance-risk / high-effort trait,
    plus one shipped concept that is clean on those dimensions (the contrast set)."""
    c = _new_conn(path)
    # Failed: risky on cost dims but cleared the gate.
    _insert_concept(c, 1, "alpha", nov=0.85, mkt=0.80, fit=0.80, eff=0.70, comp=0.85, composite=0.62)
    _insert_concept(c, 2, "beta", nov=0.80, mkt=0.78, fit=0.82, eff=0.72, comp=0.78, composite=0.61)
    _insert_outcome(c, 1, "vv_fail")
    _insert_outcome(c, 2, "vv_fail")
    # Shipped contrast: low cost dims.
    _insert_concept(c, 3, "gamma", nov=0.82, mkt=0.80, fit=0.81, eff=0.20, comp=0.15, composite=0.78)
    _insert_outcome(c, 3, "shipped")
    c.commit()
    c.close()


def _seed_shipped_only(path):
    """A single shipped concept — no vv_fail rows at all."""
    c = _new_conn(path)
    _insert_concept(c, 10, "delta", nov=0.9, mkt=0.9, fit=0.9, eff=0.1, comp=0.1, composite=0.9)
    _insert_outcome(c, 10, "shipped")
    c.commit()
    c.close()


# --------------------------------------------------------------------------- #
# Acceptance 1 — public API exists
# --------------------------------------------------------------------------- #
def test_public_api_exists():
    assert callable(hl.extract_error_cases)
    assert callable(hl.propose_amendments)


# --------------------------------------------------------------------------- #
# extract_error_cases — only vv_fail outcomes, with score profile joined
# --------------------------------------------------------------------------- #
def test_extract_error_cases_returns_only_vv_fail(db):
    _seed_vv_fail(db)
    cases = hl.extract_error_cases()
    assert len(cases) == 2
    assert {c["slug"] for c in cases} == {"alpha", "beta"}
    assert all(c["outcome"] == "vv_fail" for c in cases)
    # Score profile is joined through from foundry_concepts.
    assert all("compliance_risk" in c and "effort_estimate" in c for c in cases)


def test_extract_baseline_returns_shipped(db):
    _seed_vv_fail(db)
    base = hl.extract_baseline()
    assert {b["slug"] for b in base} == {"gamma"}


# --------------------------------------------------------------------------- #
# Acceptance 2 — a vv_fail fixture yields >= 1 proposal
# --------------------------------------------------------------------------- #
def test_vv_fail_fixture_yields_at_least_one_proposal(db):
    _seed_vv_fail(db)
    cases = hl.extract_error_cases()
    base = hl.extract_baseline()
    proposals = hl.propose_amendments(cases, base)
    assert len(proposals) >= 1
    # The shared high-compliance-risk trait should drive a compliance_risk bump.
    targets = {p["target"] for p in proposals}
    assert "compliance_risk" in targets
    for p in proposals:
        assert p["action"] == "raise_weight"
        assert p["proposed_weight"] > p["current_weight"]


# --------------------------------------------------------------------------- #
# Acceptance 3 — a shipped(-only) fixture yields zero proposals
# --------------------------------------------------------------------------- #
def test_shipped_only_fixture_yields_zero_proposals(db):
    _seed_shipped_only(db)
    cases = hl.extract_error_cases()
    assert cases == []
    assert hl.propose_amendments(cases, hl.extract_baseline()) == []


def test_propose_amendments_empty_input_is_empty():
    assert hl.propose_amendments([]) == []


# --------------------------------------------------------------------------- #
# Acceptance 4 — proposals include a confidence score and a trace_id
# --------------------------------------------------------------------------- #
def test_proposals_carry_confidence_and_trace_id(db):
    _seed_vv_fail(db)
    proposals = hl.propose_amendments(hl.extract_error_cases(), hl.extract_baseline())
    assert proposals
    for p in proposals:
        assert isinstance(p["confidence"], float)
        assert 0.0 <= p["confidence"] <= 1.0
        assert isinstance(p["trace_id"], str)
        assert p["trace_id"].startswith("acf-heur-")
        assert p["evidence_concept_ids"]


def test_trace_id_is_deterministic(db):
    _seed_vv_fail(db)
    cases = hl.extract_error_cases()
    base = hl.extract_baseline()
    a = hl.propose_amendments(cases, base)
    b = hl.propose_amendments(cases, base)
    assert [p["trace_id"] for p in a] == [p["trace_id"] for p in b]


# --------------------------------------------------------------------------- #
# Baseline contrast — a trait shared by shipped concepts is NOT proposed
# --------------------------------------------------------------------------- #
def test_trait_shared_with_shipped_is_not_proposed(db):
    """If shipped concepts are just as risky on a dimension, that dimension is not
    the cause of failure → no proposal for it."""
    c = _new_conn(db)
    # Failed and shipped both have high compliance_risk → not distinguishing.
    _insert_concept(c, 1, "a", nov=0.8, mkt=0.8, fit=0.8, eff=0.2, comp=0.85, composite=0.7)
    _insert_outcome(c, 1, "vv_fail")
    _insert_concept(c, 2, "b", nov=0.8, mkt=0.8, fit=0.8, eff=0.2, comp=0.85, composite=0.7)
    _insert_outcome(c, 2, "shipped")
    c.commit()
    c.close()

    proposals = hl.propose_amendments(hl.extract_error_cases(), hl.extract_baseline())
    assert "compliance_risk" not in {p["target"] for p in proposals}


# --------------------------------------------------------------------------- #
# run_colearn_pass — gate + dry-run wiring
# --------------------------------------------------------------------------- #
def test_run_colearn_pass_gated_off_by_default(db, monkeypatch):
    monkeypatch.delenv(hl.COLEARN_ENV, raising=False)
    _seed_vv_fail(db)
    result = hl.run_colearn_pass()
    assert result["skipped"] is True
    assert hl.COLEARN_ENV in result["reason"]


def test_run_colearn_pass_dry_run_when_enabled(db, monkeypatch):
    monkeypatch.setenv(hl.COLEARN_ENV, "true")
    _seed_vv_fail(db)
    result = hl.run_colearn_pass(dry_run=True)
    assert result.get("dry_run") is True
    assert result["error_cases_analyzed"] == 2
    assert len(result["proposals"]) >= 1


def test_run_colearn_pass_no_errors_skips(db, monkeypatch):
    monkeypatch.setenv(hl.COLEARN_ENV, "1")
    _seed_shipped_only(db)
    result = hl.run_colearn_pass()
    assert result["skipped"] is True
    assert "vv_fail" in result["reason"]


# --------------------------------------------------------------------------- #
# write_proposed_amendments — emits the foundry-scoped file (not the oracle one)
# --------------------------------------------------------------------------- #
def test_write_proposed_amendments_writes_file(db, tmp_path, monkeypatch):
    import yaml

    out = tmp_path / "foundry_scorer_heuristics_proposed.yaml"
    monkeypatch.setattr(hl, "PROPOSED_FILE", out)
    _seed_vv_fail(db)
    proposals = hl.propose_amendments(hl.extract_error_cases(), hl.extract_baseline())
    assert hl.write_proposed_amendments(proposals, 2, 1) is True

    parsed = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert parsed["gated_by"] == hl.COLEARN_ENV
    assert parsed["error_cases_analyzed"] == 2
    assert len(parsed["proposed"]) == len(proposals)


def test_write_proposed_amendments_empty_is_false(tmp_path, monkeypatch):
    monkeypatch.setattr(hl, "PROPOSED_FILE", tmp_path / "x.yaml")
    assert hl.write_proposed_amendments([], 0, 0) is False
