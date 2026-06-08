# CUI // SP-CTI
"""Tests for tools/foundry/learner.py — ACF outcome capture + bounded weight tuning (acf-learn-01).

Hermetic: a throwaway file-backed SQLite DB holds the foundry_* tables + a minimal
kanban_tasks slice. ``tools.db.storage.get_connection`` is repointed at the temp DB
so ``record_outcomes`` / ``tune_weights`` run end-to-end against a real backend
without touching the platform database.

Acceptance coverage (acf-learn-01):
  1. learner exposes record_outcomes() and tune_weights()
  2. a fully-done concept yields outcome 'shipped' and status='shipped'
  3. a concept whose emitted task failed V&V yields outcome 'vv_fail' and status='failed'
  4. tune_weights nudges weights within safe bounds and preserves the rest of the YAML
  5. record_outcomes is idempotent (no duplicate foundry_outcomes rows on rerun)
  6. tune_weights with no shipped/failed contrast is a no-op (adjustments == [])
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import subprocess
import sys

import pytest
import yaml

from tools.foundry import learner as L
from tools.foundry.db.init_db import _SCHEMA_SQLITE


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _new_conn(path):
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


_KANBAN_DDL = """
CREATE TABLE kanban_tasks (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'backlog',
    last_failure_reason TEXT
);
"""


def _insert_concept(conn, cid, slug, *, status="approved"):
    conn.execute(
        "INSERT INTO foundry_concepts "
        "(id, run_id, name, slug, status) "
        "VALUES (?, '1', ?, ?, ?)",
        (cid, slug.upper(), slug, status),
    )


def _insert_emit(conn, concept_id, task_id, seq=0):
    conn.execute(
        "INSERT INTO foundry_tasks_emitted "
        "(concept_id, kanban_task_id, seq) VALUES (?, ?, ?)",
        (concept_id, task_id, seq),
    )


def _insert_task(conn, task_id, status, failure_reason=None):
    conn.execute(
        "INSERT INTO kanban_tasks "
        "(id, title, status, last_failure_reason) VALUES (?, ?, ?, ?)",
        (task_id, f"task {task_id}", status, failure_reason),
    )


def _insert_score_profile(conn, cid, *, novelty, market, fit, effort, compliance):
    conn.execute(
        "UPDATE foundry_concepts SET "
        "novelty_score=?, market_score=?, fit_score=?, "
        "effort_estimate=?, compliance_risk=?, composite_score=0.5 "
        "WHERE id=?",
        (novelty, market, fit, effort, compliance, cid),
    )


@pytest.fixture
def db(tmp_path, monkeypatch):
    """File-backed SQLite DB with foundry_* + minimal kanban_tasks; get_connection repointed."""
    path = str(tmp_path / "learner.db")
    boot = _new_conn(path)
    boot.executescript(_SCHEMA_SQLITE)
    boot.executescript(_KANBAN_DDL)
    boot.commit()
    boot.close()

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _new_conn(path))
    return path


def _seed_fully_done_concept(path, cid=1, slug="alpha"):
    """A concept with three emitted tasks, all done — should yield 'shipped'."""
    c = _new_conn(path)
    _insert_concept(c, cid, slug)
    for i, task_id in enumerate([f"{slug}-01", f"{slug}-02", f"{slug}-03"]):
        _insert_task(c, task_id, "done")
        _insert_emit(c, cid, task_id, seq=i)
    c.commit()
    c.close()


def _seed_vv_fail_concept(path, cid=2, slug="beta"):
    """A concept with one task that failed V&V — should yield 'vv_fail' / 'failed'."""
    c = _new_conn(path)
    _insert_concept(c, cid, slug)
    _insert_task(c, f"{slug}-01", "done")
    _insert_emit(c, cid, f"{slug}-01", seq=0)
    _insert_task(
        c, f"{slug}-02", "failed", failure_reason="vv_fail: oracle mismatch on schema"
    )
    _insert_emit(c, cid, f"{slug}-02", seq=1)
    _insert_task(c, f"{slug}-03", "done")
    _insert_emit(c, cid, f"{slug}-03", seq=2)
    c.commit()
    c.close()


def _seed_contrast_profiles(path):
    """Two shipped concepts (high novelty) + two failed concepts (low novelty) so
    tune_weights has a contrast to act on. Sets the post-record_outcomes status
    directly (shipped/failed) so tune_weights finds both sides of the contrast."""
    c = _new_conn(path)
    # Shipped
    _insert_concept(c, 100, "ship-1", status="shipped")
    _insert_emit(c, 100, "ship-1-a", 0)
    _insert_task(c, "ship-1-a", "done")
    _insert_score_profile(c, 100, novelty=0.90, market=0.5, fit=0.5, effort=0.2, compliance=0.1)
    _insert_concept(c, 101, "ship-2", status="shipped")
    _insert_emit(c, 101, "ship-2-a", 0)
    _insert_task(c, "ship-2-a", "done")
    _insert_score_profile(c, 101, novelty=0.85, market=0.5, fit=0.5, effort=0.2, compliance=0.1)
    # Failed (vv_fail outcomes + status='failed' is what record_outcomes would set)
    _insert_concept(c, 200, "fail-1", status="failed")
    _insert_emit(c, 200, "fail-1-a", 0)
    _insert_task(c, "fail-1-a", "failed", failure_reason="vv_fail: stale")
    _insert_score_profile(c, 200, novelty=0.30, market=0.5, fit=0.5, effort=0.2, compliance=0.1)
    _insert_concept(c, 201, "fail-2", status="failed")
    _insert_emit(c, 201, "fail-2-a", 0)
    _insert_task(c, "fail-2-a", "failed", failure_reason="vv_fail: empty")
    _insert_score_profile(c, 201, novelty=0.25, market=0.5, fit=0.5, effort=0.2, compliance=0.1)
    c.commit()
    c.close()


# --------------------------------------------------------------------------- #
# Acceptance 1 — public API exists
# --------------------------------------------------------------------------- #
def test_public_api_exists():
    assert callable(L.record_outcomes)
    assert callable(L.tune_weights)


# --------------------------------------------------------------------------- #
# Acceptance 2 — fully done concept → shipped
# --------------------------------------------------------------------------- #
def test_fully_done_concept_yields_shipped(db):
    _seed_fully_done_concept(db)
    result = L.record_outcomes()
    assert result["shipped"] == 1
    assert result["vv_fail"] == 0
    assert result["vv_pass"] == 0
    assert result["abandoned"] == 0

    c = _new_conn(db)
    rows = c.execute(
        "SELECT concept_id, outcome FROM foundry_outcomes ORDER BY id"
    ).fetchall()
    assert [dict(r) for r in rows] == [{"concept_id": 1, "outcome": "shipped"}]
    status = c.execute(
        "SELECT status FROM foundry_concepts WHERE id=1"
    ).fetchone()["status"]
    assert status == "shipped"
    c.close()


# --------------------------------------------------------------------------- #
# Acceptance 3 — vv_fail concept → failed
# --------------------------------------------------------------------------- #
def test_vv_fail_concept_yields_failed(db):
    _seed_vv_fail_concept(db)
    result = L.record_outcomes()
    assert result["vv_fail"] == 1
    assert result["shipped"] == 0

    c = _new_conn(db)
    row = c.execute(
        "SELECT outcome FROM foundry_outcomes WHERE concept_id=2"
    ).fetchone()
    assert row["outcome"] == "vv_fail"
    status = c.execute(
        "SELECT status FROM foundry_concepts WHERE id=2"
    ).fetchone()["status"]
    assert status == "failed"
    c.close()


# --------------------------------------------------------------------------- #
# Acceptance 4 — tune_weights stays in bounds and preserves other YAML keys
# --------------------------------------------------------------------------- #
def test_tune_weights_within_bounds(db, tmp_path, monkeypatch):
    _seed_contrast_profiles(db)
    cfg_path = tmp_path / "foundry_config.yaml"
    # Existing config with a non-scoring top-level key (rate_limits) and weights
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "foundry": {
                    "rate_limits": {"max_concepts_per_cycle": 7, "max_active_projects": 2},
                    "circuit": {"vv_fail_rate": 0.5, "window": 5},
                    "scoring": {
                        "weights": {
                            "novelty": 0.25,
                            "feasibility": 0.25,
                            "strategic_fit": 0.25,
                            "market_timing": 0.25,
                        },
                        "min_composite": 0.6,
                    },
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(L, "CONFIG_PATH", cfg_path)

    result = L.tune_weights()
    assert "adjustments" in result
    assert isinstance(result["adjustments"], list)
    # novelty was 0.90/0.85 shipped vs 0.30/0.25 failed → positive delta → bump
    targets = {a["dim"] for a in result["adjustments"]}
    assert "novelty" in targets

    # Re-load the YAML and verify bounds + key preservation
    parsed = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    weights = parsed["foundry"]["scoring"]["weights"]
    for k, v in weights.items():
        assert L._WEIGHT_FLOOR <= v <= L._WEIGHT_CEIL, (k, v)
    # Other keys untouched
    assert parsed["foundry"]["rate_limits"]["max_concepts_per_cycle"] == 7
    assert parsed["foundry"]["circuit"]["vv_fail_rate"] == 0.5
    assert parsed["foundry"]["scoring"]["min_composite"] == 0.6


# --------------------------------------------------------------------------- #
# Acceptance 5 — record_outcomes is idempotent (append-only but deduped per pass)
# --------------------------------------------------------------------------- #
def test_record_outcomes_idempotent(db):
    _seed_fully_done_concept(db)
    a = L.record_outcomes()
    b = L.record_outcomes()
    c = _new_conn(db)
    n_rows = c.execute("SELECT COUNT(*) FROM foundry_outcomes").fetchone()[0]
    c.close()
    assert a["recorded"] == 1
    assert b["recorded"] == 0  # nothing new the second time
    assert n_rows == 1


# --------------------------------------------------------------------------- #
# Acceptance 6 — tune_weights no contrast is a no-op
# --------------------------------------------------------------------------- #
def test_tune_weights_no_contrast(db, tmp_path, monkeypatch):
    """Only one shipped concept (no failed) → no contrast → no adjustments."""
    c = _new_conn(db)
    _insert_concept(c, 1, "solo")
    _insert_emit(c, 1, "solo-a", 0)
    _insert_task(c, "solo-a", "done")
    _insert_score_profile(c, 1, novelty=0.5, market=0.5, fit=0.5, effort=0.2, compliance=0.1)
    c.commit()
    c.close()

    cfg_path = tmp_path / "foundry_config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {"foundry": {"scoring": {"weights": {"novelty": 0.25}}}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(L, "CONFIG_PATH", cfg_path)

    result = L.tune_weights()
    assert result["adjustments"] == []
    # YAML untouched
    parsed = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert parsed["foundry"]["scoring"]["weights"]["novelty"] == 0.25


# --------------------------------------------------------------------------- #
# Acceptance 7 — proposed/rejected concepts are NOT touched
# --------------------------------------------------------------------------- #
def test_record_outcomes_does_not_touch_non_approved_concepts(db):
    c = _new_conn(db)
    _insert_concept(c, 1, "p", status="proposed")
    _insert_emit(c, 1, "p-a", 0)
    _insert_task(c, "p-a", "done")
    _insert_concept(c, 2, "r", status="rejected")
    _insert_emit(c, 2, "r-a", 0)
    _insert_task(c, "r-a", "done")
    c.commit()
    c.close()

    result = L.record_outcomes()
    assert result["recorded"] == 0

    c = _new_conn(db)
    n_outcomes = c.execute("SELECT COUNT(*) FROM foundry_outcomes").fetchone()[0]
    c.close()
    assert n_outcomes == 0


# --------------------------------------------------------------------------- #
# Acceptance 8 — CLI smoke (--record and --tune both run end-to-end)
# --------------------------------------------------------------------------- #
def test_cli_record_and_tune(db, tmp_path, monkeypatch):
    _seed_fully_done_concept(db)
    _seed_vv_fail_concept(db)

    cfg_path = tmp_path / "foundry_config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {"foundry": {"scoring": {"weights": {"novelty": 0.25}}}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(L, "CONFIG_PATH", cfg_path)

    # Run the CLI as a subprocess so the monkeypatched storage module is fresh.
    env_patch = {
        "ICDEV_STORAGE_BACKEND": "sqlite",
        "PYTHONPATH": str(tmp_path),
    }
    # Patch get_connection via a sitecustomize-style approach: import the module
    # in-process and call main() instead (subprocess wouldn't see monkeypatch).
    # We just import the CLI's main() directly:
    from tools.foundry.learner import main as learner_main

    argv_record = ["--record", "--json"]
    rc1 = learner_main(argv_record)
    assert rc1 == 0
    argv_tune = ["--tune", "--json"]
    rc2 = learner_main(argv_tune)
    assert rc2 == 0
