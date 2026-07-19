#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the removed raw-sqlite3 fail-soft fallback (nav-strat-04).

The STRATEGOS I&W modules previously fell back to a raw ``sqlite3.connect()``
when the ``tools.db.storage.get_connection`` import failed.  That fallback was
broken: the queries use ``%s`` placeholders, which the stdlib sqlite3 driver
rejects, so the fallback path itself threw and merely masked the real import
error.  These tests lock in the fix:

  * source scan — ``sqlite3.connect`` (and the ``import sqlite3`` fallback) is
    absent from ``iw_scorers`` and ``iw_bayesian`` in BOTH the canonical and
    ``icdev/`` mirror trees.
  * end-to-end — the scorers and the Bayesian updater run against the shared
    storage layer on the SQLite backend, proving the ``get_connection()`` path
    (and its ``%s`` -> ``?`` translation) works round-trip with real rows.
"""
import importlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")

# Shim-aware: resolve the concrete module objects so monkeypatch.setattr targets
# the same module the code-under-test imports (at call time) from.
storage = importlib.import_module("tools.db.storage")
iw_scorers = importlib.import_module("tools.strategos.iw_scorers")
iw_bayesian = importlib.import_module("tools.strategos.iw_bayesian")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── 1. Source scan — no raw sqlite3 fallback anywhere ────────────────────────

_MODULE_RELPATHS = [
    "tools/strategos/iw_scorers.py",
    "tools/strategos/iw_bayesian.py",
    "icdev/tools/strategos/iw_scorers.py",
    "icdev/tools/strategos/iw_bayesian.py",
]


@pytest.mark.parametrize("relpath", _MODULE_RELPATHS)
def test_no_raw_sqlite3_fallback_in_source(relpath):
    path = BASE_DIR / relpath
    assert path.exists(), f"expected module missing: {relpath}"
    src = path.read_text(encoding="utf-8")
    assert "sqlite3.connect" not in src, (
        f"{relpath} still contains a raw sqlite3.connect fallback — the %s "
        f"placeholders these queries use would be rejected by the stdlib driver."
    )
    assert "import sqlite3" not in src, (
        f"{relpath} still imports sqlite3 for a fail-soft fallback."
    )


def test_canonical_and_mirror_modules_are_in_sync():
    for name in ("iw_scorers.py", "iw_bayesian.py"):
        canon = (BASE_DIR / "tools" / "strategos" / name).read_text(encoding="utf-8")
        mirror = (BASE_DIR / "icdev" / "tools" / "strategos" / name).read_text(encoding="utf-8")
        assert canon == mirror, f"tools/ and icdev/ copies of {name} diverged"


# ── Shared fixture: route storage to a dedicated RLS-free SQLite file ─────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sg_economic_signals (
    id            TEXT PRIMARY KEY,
    theater_id    TEXT,
    signal_type   TEXT,
    value         REAL,
    metadata_json TEXT,
    signal_ts     TEXT,
    created_at    TEXT
);
CREATE TABLE IF NOT EXISTS sg_military_signal_scores (
    id              TEXT PRIMARY KEY,
    composite_score REAL,
    confidence      REAL,
    created_at      TEXT
);
CREATE TABLE IF NOT EXISTS sg_ghost_signals (
    id          TEXT PRIMARY KEY,
    confidence  REAL,
    detected_at TEXT
);
CREATE TABLE IF NOT EXISTS sg_iw_indicators (
    id       TEXT PRIMARY KEY,
    theater  TEXT,
    status   TEXT,
    weight   REAL,
    category TEXT
);
CREATE TABLE IF NOT EXISTS sg_infrastructure_signals (
    id           TEXT PRIMARY KEY,
    signal_value REAL,
    confidence   REAL,
    computed_at  TEXT
);
CREATE TABLE IF NOT EXISTS sg_information_scores (
    id                   TEXT PRIMARY KEY,
    information_score    REAL,
    rhetoric_score       REAL,
    dehumanization_index REAL,
    cyber_recon_score    REAL,
    disinformation_surge REAL,
    created_at           TEXT
);
CREATE TABLE IF NOT EXISTS sg_bayesian_war_posteriors (
    id               TEXT PRIMARY KEY,
    prior            REAL,
    evidence_type    TEXT,
    strength         REAL,
    likelihood_ratio REAL,
    posterior        REAL,
    metadata_json    TEXT,
    created_at       TEXT
);
"""


@pytest.fixture
def iw_db(monkeypatch, tmp_path):
    """Route STRATEGOS storage to a dedicated SQLite file (RLS-free, %s->? translated).

    The '.db' path that is NOT the main icdev.db selects a dedicated, RLS-free
    SQLite connection, so the scorers' %s queries translate cleanly.
    """
    db_path = str(tmp_path / "test_iw_nav_strat_04.db")
    real_get = storage.get_connection

    def _fake_get(db_path_arg=None):
        return real_get(db_path=db_path)

    # Patch the concrete storage module attribute; the modules under test do
    # `from tools.db.storage import get_connection` at call time, so they pick
    # up the patched callable.
    monkeypatch.setattr(storage, "get_connection", _fake_get)

    conn = real_get(db_path=db_path)
    try:
        for stmt in _SCHEMA.strip().split(";"):
            if stmt.strip():
                conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()
    return db_path


# ── 2. Economic scorer — end-to-end through get_connection ───────────────────

def test_economic_scorer_reads_rows_via_get_connection(iw_db):
    now = _now_iso()
    conn = storage.get_connection()
    try:
        conn.execute(
            "INSERT INTO sg_economic_signals "
            "(id, theater_id, signal_type, value, metadata_json, signal_ts, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            ("e1", "ukraine", "fx", 1.0, '{"cusum_alert": true}', now, now),
        )
        conn.execute(
            "INSERT INTO sg_economic_signals "
            "(id, theater_id, signal_type, value, metadata_json, signal_ts, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            ("e2", "ukraine", "fx", 2.0, '{"cusum_alert": true}', now, now),
        )
        conn.execute(
            "INSERT INTO sg_economic_signals "
            "(id, theater_id, signal_type, value, metadata_json, signal_ts, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            ("e3", "ukraine", "fx", 3.0, "{}", now, now),
        )
        conn.commit()
    finally:
        conn.close()

    detail = iw_scorers.EconomicSignalScorer().score_detail("ukraine", window_days=90)
    assert detail["signals"] == 3
    assert detail["alerts"] == 2
    assert detail["score"] > 0.0
    # A different theater sees no rows -> graceful zero (not an exception).
    assert iw_scorers.EconomicSignalScorer().score("elsewhere", window_days=90) == 0.0


# ── 3. Military scorer — end-to-end ──────────────────────────────────────────

def test_military_scorer_reads_rows_via_get_connection(iw_db):
    now = _now_iso()
    conn = storage.get_connection()
    try:
        conn.execute(
            "INSERT INTO sg_military_signal_scores (id, composite_score, confidence, created_at) "
            "VALUES (%s,%s,%s,%s)",
            ("m1", 0.8, 0.9, now),
        )
        conn.execute(
            "INSERT INTO sg_ghost_signals (id, confidence, detected_at) VALUES (%s,%s,%s)",
            ("g1", 0.85, now),
        )
        conn.commit()
    finally:
        conn.close()

    detail = iw_scorers.MilitarySignalScorer().score_detail("ukraine", window_days=30)
    assert detail["military_assessments"] == 1
    assert detail["ghost_signals"] == 1
    assert detail["score"] > 0.0


# ── 4. score_all_domains — composite path runs without sqlite3 fallback ───────

def test_score_all_domains_runs(iw_db):
    result = iw_scorers.score_all_domains("ukraine", window_days=30)
    assert set(result["domains"]) == {
        "economic", "military", "political", "information", "infrastructure",
    }
    for v in result["domains"].values():
        assert 0.0 <= v <= 1.0


# ── 5. Bayesian updater — persist + read-back via get_connection ─────────────

def test_bayesian_updater_persists_and_reads_via_get_connection(iw_db):
    updater = iw_bayesian.BayesianIWUpdater()
    # No prior rows -> default prior.
    assert updater.get_latest_posterior("ukraine") == updater.DEFAULT_PRIOR

    result = updater.update("ukraine", evidence_type="military_buildup", strength=0.9)
    assert "error" not in result
    assert result["posterior"] > result["prior"]  # war-indicative evidence raises P(war)

    # The persisted row is now readable through the same get_connection path.
    latest = updater.get_latest_posterior("ukraine")
    assert latest == pytest.approx(result["posterior"], rel=1e-6)


# ── 6. Classifier — pure-Python path unaffected, still callable ──────────────

def test_exercise_classifier_predicts():
    clf = iw_bayesian.ExerciseVsPreWarClassifier()
    pred = clf.predict(
        {
            "military_buildup": 0.9,
            "logistics_surge": 0.85,
            "diplomatic_failure": 0.8,
            "nuclear_signaling": 0.6,
        }
    )
    assert pred["label"] in ("pre_war", "exercise")
    assert 0.0 <= pred["confidence"] <= 1.0
