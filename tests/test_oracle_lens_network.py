# CUI // SP-CTI
"""Tests for the Oracle Network Lens (ndc-brg-01).

The lens bridges NDC predictor result tables (nc_eol_predictions,
nc_bgp_predictions, nc_vuln_predictions, and the PNA/PVM tables) into the
platform Oracle prediction shape so oracle_kanban_bridge_sync can materialize
suggested kanban cards.

These tests seed a temp SQLite canvas DB via ``tools.network.db.init_db`` with
its backend/path monkeypatched to SQLite, then run the lens against it.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")


# ---------------------------------------------------------------------------
# Fixture: temp SQLite canvas DB seeded via init_db, with the lens's
# get_connection pointed at it.
# ---------------------------------------------------------------------------


def _sqlite_conn(db_path: Path):
    """A StorageConnection-wrapped sqlite connection (matches what the NDC
    get_connection() returns on the SQLite backend), or a bare sqlite3
    connection if the wrapper is unavailable."""
    raw = sqlite3.connect(str(db_path))
    raw.row_factory = sqlite3.Row
    try:
        from tools.db.storage import StorageConnection

        return StorageConnection(raw, "sqlite")
    except Exception:
        return raw


@pytest.fixture()
def canvas_db(tmp_path, monkeypatch):
    """Initialise a temp SQLite NDC canvas DB and route the lens to it.

    Returns the DB path.  ``init_db`` builds the full canvas schema (including
    the nc_*_predictions tables) in the temp file.
    """
    from tools.network.db import init_db as ndc

    db_path = tmp_path / "network_canvas.db"

    # Force the SQLite backend and point DB_PATH at the temp file.
    monkeypatch.setattr(ndc, "_NC_BACKEND", "sqlite", raising=False)
    monkeypatch.setattr(ndc, "DB_PATH", db_path, raising=False)

    # get_connection() (used by both init_db and the lens) returns a fresh
    # connection to the temp DB on each call.
    monkeypatch.setattr(ndc, "get_connection", lambda: _sqlite_conn(db_path))

    ndc.init_db()
    return db_path


def _seed(db_path: Path, table: str, **cols) -> None:
    conn = _sqlite_conn(db_path)
    try:
        keys = ", ".join(cols)
        marks = ", ".join(["?"] * len(cols))
        conn.execute(
            f"INSERT INTO {table} ({keys}) VALUES ({marks})",  # nosec B608 — test-only literal cols
            tuple(cols.values()),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# (a) seeded EOL + vuln rows → >=1 oracle prediction, source 'network', mapped severity
# ---------------------------------------------------------------------------


def test_seeded_predictions_map_to_oracle_records(canvas_db):
    # A critical EOL device already past end-of-support with active CVEs.
    _seed(
        canvas_db, "nc_eol_predictions",
        device_name="core-rtr-01", vendor="Cisco", model="ISR4321",
        os_version="17.3.1", eos_date="2020-01-01", days_remaining=-900,
        has_active_cves=1, active_cve_count=4,
        risk_score=0.92, risk_tier="critical", nqe_source="static_registry",
    )
    # A rising, high-composite vulnerability prediction (no explicit tier column).
    _seed(
        canvas_db, "nc_vuln_predictions",
        id="vp-1", advisory_id="ADV-2026-001",
        risk_score_composite=0.85, risk_score_30d=0.70, risk_score_90d=0.88,
        trend="rising", confidence=0.80,
    )

    from tools.oracle.lenses.lens_network import NetworkLens

    preds = NetworkLens().run()

    assert len(preds) >= 1
    # Every prediction is sourced from the network lens.
    assert all(p.lens == "network" for p in preds)
    assert all(p.data.get("source") == "network" for p in preds)
    # Severity is mapped into the Oracle vocabulary.
    assert all(p.severity in {"info", "warning", "critical"} for p in preds)

    # The critical EOL device is surfaced as a critical oracle prediction.
    eol = [p for p in preds if p.category == "eol_risk"]
    assert len(eol) == 1
    assert eol[0].severity == "critical"
    assert eol[0].data["device"] == "core-rtr-01"
    assert eol[0].data["predictor"] == "eol_predictor"
    assert eol[0].recommendations  # remediation attached for kanban materialization

    # The rising vuln prediction is surfaced (derived tier → critical severity).
    vuln = [p for p in preds if p.category == "vuln_risk"]
    assert len(vuln) == 1
    assert vuln[0].confidence == pytest.approx(0.80, abs=0.01)


def test_low_signal_rows_are_filtered_out(canvas_db):
    # A low-risk EOL device well below the high-signal floor → not surfaced.
    _seed(
        canvas_db, "nc_eol_predictions",
        device_name="edge-sw-99", vendor="Arista", model="7050",
        eos_date="2031-01-01", days_remaining=1800,
        risk_score=0.10, risk_tier="low", nqe_source="static_registry",
    )
    from tools.oracle.lenses.lens_network import NetworkLens

    preds = NetworkLens().run()
    assert [p for p in preds if p.category == "eol_risk"] == []


# ---------------------------------------------------------------------------
# (b) empty / absent tables → [] without raising
# ---------------------------------------------------------------------------


def test_empty_tables_return_no_predictions(canvas_db):
    from tools.oracle.lenses.lens_network import NetworkLens

    preds = NetworkLens().run()
    assert preds == []


def test_absent_canvas_db_returns_empty(monkeypatch):
    """If the canvas DB connection cannot be established, the lens degrades to
    an empty result rather than raising."""
    from tools.network.db import init_db as ndc
    from tools.oracle.lenses.lens_network import NetworkLens

    def _boom():
        raise RuntimeError("canvas DB unavailable")

    monkeypatch.setattr(ndc, "get_connection", _boom)

    lens = NetworkLens()
    assert lens.analyze() == {}
    assert lens.run() == []


def test_missing_prediction_table_is_skipped(canvas_db, monkeypatch):
    """A source whose table does not exist is skipped without raising."""
    from tools.oracle.lenses.lens_network import NetworkLens

    # Drop one prediction table to simulate an unrun migration.
    conn = _sqlite_conn(canvas_db)
    try:
        conn.execute("DROP TABLE IF EXISTS nc_bgp_predictions")
        conn.commit()
    finally:
        conn.close()

    # Seed a valid EOL row so the run still produces something.
    _seed(
        canvas_db, "nc_eol_predictions",
        device_name="core-rtr-02", vendor="Juniper", model="MX80",
        eos_date="2025-12-31", days_remaining=30,
        risk_score=0.88, risk_tier="high", nqe_source="static_registry",
    )

    preds = NetworkLens().run()  # must not raise despite the missing table
    assert any(p.category == "eol_risk" for p in preds)


# ---------------------------------------------------------------------------
# (c) lens appears in the oracle lens registry / status listing
# ---------------------------------------------------------------------------


def test_network_lens_registered():
    from tools.oracle.lenses import get_lens, get_lens_registry, list_lenses
    from tools.oracle.lenses.lens_network import NetworkLens

    assert "network" in list_lenses()
    assert "network" in get_lens_registry()
    assert get_lens("network") is NetworkLens
    # The lens self-identifies with the canonical Oracle lens name.
    assert NetworkLens.name == "network"
