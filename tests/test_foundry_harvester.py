# CUI // SP-CTI
"""Tests for the Foundry harvester — Genesis + telemetry sources + cross-source dedup.

Covers the acf-harvest-02 acceptance criteria:
  * a duplicate signal across two sources is stored once (cross-source dedup);
  * a telemetry gap (recurring gate failure) becomes a signal;
  * a Genesis prediction (oracle_predictions) becomes a signal.
"""

import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.foundry.harvester import (  # noqa: E402
    dedup_hash,
    _make_signal,
    _collapse,
    persist_signals,
    harvest_all,
    harvest_telemetry,
    harvest_genesis,
)
from tools.foundry.db.init_db import _SCHEMA_SQLITE  # noqa: E402
from tools.db.storage import get_connection  # noqa: E402
from tools.foundry import harvester as _harvester_mod  # noqa: E402
from tools.foundry import engine as _engine_mod  # noqa: E402


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_conn(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """A temp SQLite DB with foundry_signals + minimal source tables.

    Stubs out ``init_db`` (so it never touches the platform DB) and points
    ``get_connection`` at a temp file we boot with the SQLite DDL ourselves.
    """
    p = tmp_path / "foundry_test.db"
    path = str(p)
    boot = _new_conn(path)
    boot.executescript(_SCHEMA_SQLITE)
    boot.commit()
    boot.close()

    # Never touch the platform DB.
    monkeypatch.setattr(_harvester_mod, "init_db", lambda *a, **k: True)
    monkeypatch.setattr(_engine_mod, "init_db", lambda *a, **k: True)
    # Force the SQLite lastrowid path in _open_run.
    monkeypatch.setattr(_engine_mod, "_is_pg", lambda: False)

    # Point get_connection at the temp DB.
    import importlib

    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _new_conn(path))
    return path


def _seed(db_path, sql, rows=()):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql)
        conn.commit()
    finally:
        conn.close()


def _exec(db_path, sql, params=()):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _signals_in_db(db_path):
    conn = get_connection(db_path=db_path)
    try:
        rows = conn.execute(
            "SELECT id, source_engine, theme, dedup_hash, contributing_engines, "
            "source_type, status FROM foundry_signals"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# dedup_hash / normalization
# --------------------------------------------------------------------------- #
def test_dedup_hash_stable_across_formatting():
    h1 = dedup_hash("Add  K8s  Autoscaling!", ["build", "agents"])
    h2 = dedup_hash("add k8s autoscaling", ["agents", "build"])  # reordered + cased
    assert h1 == h2


def test_dedup_hash_differs_on_different_theme():
    assert dedup_hash("theme one", ["a"]) != dedup_hash("theme two", ["a"])


# --------------------------------------------------------------------------- #
# Cross-source dedup: same signal from two engines → stored once
# --------------------------------------------------------------------------- #
def test_collapse_merges_two_engines_into_one():
    s_genesis = _make_signal("genesis", "Flaky build cache", ["build", "cache"])
    s_telem = _make_signal("telemetry", "flaky  build  cache", ["cache", "build"])
    assert s_genesis["dedup_hash"] == s_telem["dedup_hash"]

    collapsed = _collapse([s_genesis, s_telem])
    assert len(collapsed) == 1
    merged = collapsed[0]
    assert merged["contributing_engines"] == ["genesis", "telemetry"]
    # telemetry outranks genesis in SOURCE_PRECEDENCE → becomes primary
    assert merged["source_engine"] == "telemetry"


def test_duplicate_across_two_sources_persisted_once(db_path):
    s_a = _make_signal("genesis", "Shared duplicate signal", ["alpha", "beta"])
    s_b = _make_signal("innovation", "shared duplicate signal", ["beta", "alpha"])
    assert s_a["dedup_hash"] == s_b["dedup_hash"]

    conn = get_connection(db_path=db_path)
    try:
        inserted = persist_signals(conn, _collapse([s_a, s_b]))
    finally:
        conn.close()

    assert inserted == 1
    rows = _signals_in_db(db_path)
    assert len(rows) == 1
    assert sorted(json.loads(rows[0]["contributing_engines"])) == ["genesis", "innovation"]


def test_persist_is_idempotent_across_runs(db_path):
    sig = _make_signal("telemetry", "Idempotent signal", ["x", "y"])
    conn = get_connection(db_path=db_path)
    try:
        first = persist_signals(conn, [sig])
        second = persist_signals(conn, [sig])  # same hash, second run
    finally:
        conn.close()
    assert first == 1
    assert second == 0
    assert len(_signals_in_db(db_path)) == 1


def test_harvest_all_dedups_innovation_and_research(db_path):
    """Two distinct source stores surfacing the same title collapse to one row."""
    title = "Autoscale build agents on demand"
    _seed(db_path, """CREATE TABLE innovation_signals (
        id TEXT PRIMARY KEY, source TEXT, source_type TEXT, title TEXT,
        description TEXT, category TEXT, composite_score REAL, score REAL,
        status TEXT)""")
    _exec(db_path,
          "INSERT INTO innovation_signals (id, title, description, status, source_type) "
          "VALUES (?, ?, ?, 'new', 'web')",
          (uuid.uuid4().hex, title, "from innovation engine"))
    _seed(db_path, """CREATE TABLE research_signals (
        id TEXT PRIMARY KEY, title TEXT, description TEXT, category TEXT,
        source_type TEXT)""")
    _exec(db_path,
          "INSERT INTO research_signals (id, title, description, source_type) "
          "VALUES (?, ?, ?, 'report')",
          (uuid.uuid4().hex, title, "from research engine"))

    summary = harvest_all(db_path=db_path, sources=["innovation", "research"])
    assert summary["raw_signals"] == 2
    assert summary["collapsed_signals"] == 1
    assert summary["deduped"] == 1
    assert summary["inserted"] == 1

    rows = _signals_in_db(db_path)
    assert len(rows) == 1
    assert sorted(json.loads(rows[0]["contributing_engines"])) == ["innovation", "research"]


# --------------------------------------------------------------------------- #
# Telemetry: a gate-failure gap becomes a signal
# --------------------------------------------------------------------------- #
def test_telemetry_gate_failure_becomes_signal(db_path):
    _seed(db_path, """CREATE TABLE audit_trail (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT,
        event_type TEXT, created_at TEXT)""")
    now = datetime.now(timezone.utc)
    for i in range(4):  # >= DEFAULT_GATE_FAILURE_MIN (3)
        _exec(db_path,
              "INSERT INTO audit_trail (project_id, event_type, created_at) VALUES (?, ?, ?)",
              ("proj-x", "test_failed", _iso(now - timedelta(days=i))))

    signals = harvest_telemetry(db_path=db_path)
    gate_sigs = [s for s in signals if s["source_type"] == "gate_failures"]
    assert gate_sigs, "expected a telemetry signal from recurring gate failures"
    assert gate_sigs[0]["source_engine"] == "telemetry"
    assert "gate_failure" in gate_sigs[0]["keywords"]


def test_harvest_all_persists_telemetry_signal(db_path):
    _seed(db_path, """CREATE TABLE audit_trail (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT,
        event_type TEXT, created_at TEXT)""")
    now = datetime.now(timezone.utc)
    for i in range(4):
        _exec(db_path,
              "INSERT INTO audit_trail (project_id, event_type, created_at) VALUES (?, ?, ?)",
              ("proj-y", "test_failed", _iso(now - timedelta(days=i))))

    summary = harvest_all(db_path=db_path, sources=["telemetry"])
    assert summary["inserted"] >= 1
    rows = _signals_in_db(db_path)
    assert any(r["source_type"] == "gate_failures" for r in rows)


# --------------------------------------------------------------------------- #
# Genesis: an Oracle prediction becomes a signal
# --------------------------------------------------------------------------- #
def _seed_oracle(db_path):
    _seed(db_path, """CREATE TABLE oracle_predictions (
        id TEXT PRIMARY KEY, lens_id TEXT, lens_name TEXT, subject_type TEXT,
        subject_id TEXT, prediction_type TEXT, prediction_text TEXT,
        confidence REAL, severity TEXT, horizon_days INTEGER,
        outcome TEXT, created_at TEXT)""")


def test_genesis_prediction_becomes_signal(db_path):
    _seed_oracle(db_path)
    _exec(db_path,
          "INSERT INTO oracle_predictions (id, lens_id, lens_name, subject_type, "
          "subject_id, prediction_type, prediction_text, confidence, severity, outcome) "
          "VALUES (?, 'lens-1', 'Risk Lens', 'pipeline', 'pl-1', 'gap', "
          "'Build pipeline will exceed SLA next sprint', 0.82, 'high', NULL)",
          (uuid.uuid4().hex,))

    signals = harvest_genesis(get_connection(db_path=db_path))
    assert len(signals) == 1
    assert signals[0]["source_engine"] == "genesis"
    assert signals[0]["severity"] == "high"

    summary = harvest_all(db_path=db_path, sources=["genesis"])
    assert summary["inserted"] == 1
    rows = _signals_in_db(db_path)
    assert rows[0]["source_engine"] == "genesis"


def test_genesis_skips_resolved_predictions(db_path):
    _seed_oracle(db_path)
    _exec(db_path,
          "INSERT INTO oracle_predictions (id, lens_id, lens_name, subject_type, "
          "subject_id, prediction_type, prediction_text, confidence, severity, outcome) "
          "VALUES (?, 'lens-1', 'Risk Lens', 'pipeline', 'pl-2', 'gap', "
          "'Already resolved condition', 0.5, 'medium', 'confirmed')",
          (uuid.uuid4().hex,))
    signals = harvest_genesis(get_connection(db_path=db_path))
    assert signals == []
