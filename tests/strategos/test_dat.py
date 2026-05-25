#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Diplomatic Activity Tracker (DAT) — tools/strategos/dat.py."""
import os
import sqlite3
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


@pytest.fixture(autouse=True)
def _patch_conn(monkeypatch, tmp_path):
    """Redirect storage to an isolated SQLite DB for each test."""
    db_path = str(tmp_path / "test_dat.db")

    def _fake_get_connection():
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c

    import tools.strategos.dat as dat_mod
    monkeypatch.setattr(dat_mod, "_conn", _fake_get_connection)
    monkeypatch.setattr(dat_mod, "_is_pg", lambda conn: False)
    yield


def test_ensure_tables():
    from tools.strategos.dat import ensure_tables, _conn
    ensure_tables()
    conn = _conn()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "sg_dat_cables" in tables
    assert "sg_dat_unsc_events" in tables
    assert "sg_dat_backchannel" in tables
    assert "sg_dat_dti_snapshots" in tables


def test_ingest_cable():
    from tools.strategos.dat import ingest_cable, get_cables
    result = ingest_cable(
        theater_id="ukraine",
        cable_ref="STATE-2026-TEST",
        origin="AMEMBASSY KYIV",
        subject="Escalation update",
        tension_level="high",
        keywords=["military", "escalation"],
        summary="Test cable.",
    )
    assert result["tension_level"] == "high"
    assert result["theater_id"] == "ukraine"

    cables = get_cables("ukraine")
    assert len(cables) == 1
    assert cables[0]["cable_ref"] == "STATE-2026-TEST"


def test_ingest_unsc_event():
    from tools.strategos.dat import ingest_unsc_event, get_unsc_events
    result = ingest_unsc_event(
        theater_id="global",
        event_type="emergency",
        topic="Ceasefire vote",
        emergency=True,
        veto_cast=True,
    )
    assert result["emergency"] is True

    events = get_unsc_events("global")
    assert len(events) == 1
    assert int(events[0]["emergency"]) == 1
    assert int(events[0]["veto_cast"]) == 1


def test_ingest_backchannel():
    from tools.strategos.dat import ingest_backchannel, get_backchannels
    result = ingest_backchannel(
        theater_id="pacific",
        channel_type="intelligence",
        parties=["USA", "CHN"],
        frequency_delta=-0.5,
        escalation_flag=True,
    )
    assert result["escalation_flag"] is True

    bc = get_backchannels("pacific")
    assert len(bc) == 1
    assert bc[0]["channel_type"] == "intelligence"


def test_compute_dti_empty():
    from tools.strategos.dat import compute_dti, ensure_tables
    ensure_tables()
    result = compute_dti("no_such_theater")
    assert result["dti_score"] == 0.0
    assert result["cable_count"] == 0
    assert result["unsc_count"] == 0
    assert result["backchannel_count"] == 0


def test_compute_dti_with_data():
    from tools.strategos.dat import compute_dti, ingest_cable, ingest_unsc_event, ingest_backchannel
    ingest_cable(theater_id="ukraine", tension_level="critical", cable_ref="C1")
    ingest_cable(theater_id="ukraine", tension_level="high", cable_ref="C2")
    ingest_unsc_event(theater_id="ukraine", emergency=True, veto_cast=True)
    ingest_backchannel(theater_id="ukraine", escalation_flag=True, frequency_delta=-0.8)

    result = compute_dti("ukraine")
    assert result["dti_score"] > 0
    assert result["cable_count"] == 2
    assert result["unsc_count"] == 1
    assert result["backchannel_count"] == 1


def test_refresh_dti_persists():
    from tools.strategos.dat import refresh_dti, get_latest_dti, ingest_cable
    ingest_cable(theater_id="global", tension_level="medium", cable_ref="X1")
    snap = refresh_dti("global")
    assert "snapshot_id" in snap

    latest = get_latest_dti("global")
    assert latest is not None
    assert latest["dti_score"] == snap["dti_score"]


def test_dti_history():
    from tools.strategos.dat import refresh_dti, get_dti_history
    refresh_dti("global")
    refresh_dti("global")
    history = get_dti_history("global", limit=10)
    assert len(history) >= 2
    assert "dti_score" in history[0]


def test_tension_level_normalization():
    from tools.strategos.dat import ingest_cable, get_cables
    ingest_cable(theater_id="global", tension_level="INVALID_LEVEL", cable_ref="BAD")
    cables = get_cables("global")
    assert cables[0]["tension_level"] == "low"
