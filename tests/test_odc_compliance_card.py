# CUI // SP-CTI
"""Tests for the ODC canvas-compliance card (obx-fix-05).

The ODC card previously opened the raw SQLite file
``data/observability_canvas.db`` directly, so on PostgreSQL-primary
deployments (where that file is absent) it was permanently available=False and
MITRE coverage was never computed. It now reads through the ODC canvas
connection (``tools.observability_canvas.db.init_db.get_connection``).

conftest forces ICDEV_STORAGE_BACKEND=sqlite. These tests seed rows through a
translating ``StorageConnection`` on a temp SQLite DB and patch the canvas
connection factory (shim-aware: patch the source module attribute so the
call-time ``from ... import get_connection`` inside the card resolves to it).
"""

import importlib
import sqlite3

from tools.db.storage import StorageConnection

_ODC_INIT = "tools.observability_canvas.db.init_db"
_COMPLIANCE = "tools.canvas_compliance.compliance"

# DDL mirrors tools/observability_canvas/db/init_db.py (canvas SQLite schema).
ODC_GAP_DDL = """
CREATE TABLE odc_gap_scores (
    id                  TEXT PRIMARY KEY,
    design_id           TEXT NOT NULL,
    total_techniques    INTEGER NOT NULL DEFAULT 0,
    covered_count       INTEGER NOT NULL DEFAULT 0,
    partial_count       INTEGER NOT NULL DEFAULT 0,
    gap_count           INTEGER NOT NULL DEFAULT 0,
    overall_gap_score   REAL NOT NULL DEFAULT 0.0,
    by_tactic           TEXT NOT NULL DEFAULT '{}',
    assessed_at         TEXT NOT NULL
);
"""

ODC_TTP_DDL = """
CREATE TABLE od_ttp_coverage (
    id              TEXT PRIMARY KEY,
    ttp_id          TEXT NOT NULL,
    design_id       TEXT DEFAULT '',
    state           TEXT NOT NULL,
    sigma_match     INTEGER NOT NULL DEFAULT 0,
    baseline_match  INTEGER NOT NULL DEFAULT 0,
    detail          TEXT DEFAULT '{}',
    verified_at     TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

_CARD_KEYS = {"key", "name", "path", "color", "badge", "available", "metrics"}


def _sc(path) -> StorageConnection:
    """A translating StorageConnection over a temp SQLite file."""
    raw = sqlite3.connect(str(path))
    raw.row_factory = sqlite3.Row
    return StorageConnection(raw, "sqlite")


def _patch_conn(monkeypatch, path) -> None:
    """Point the ODC canvas connection factory at our temp DB (fresh each call)."""
    init_mod = importlib.import_module(_ODC_INIT)
    monkeypatch.setattr(init_mod, "get_connection", lambda: _sc(path))


def _get_card(monkeypatch, path) -> dict:
    _patch_conn(monkeypatch, path)
    comp = importlib.import_module(_COMPLIANCE)
    return comp.get_odc_card()


def _metrics(card: dict) -> dict:
    return {m["label"]: m["value"] for m in card["metrics"]}


def test_odc_card_computes_coverage(monkeypatch, tmp_path):
    """Seeded odc_gap_scores -> coverage %, sigma count, available=True."""
    db = tmp_path / "odc.db"
    conn = _sc(db)
    conn.executescript(ODC_GAP_DDL)
    # Older assessment (should be ignored: ORDER BY assessed_at DESC).
    conn.execute(
        "INSERT INTO odc_gap_scores"
        " (id, design_id, total_techniques, covered_count, gap_count, assessed_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        ("g0", "d1", 10, 1, 9, "2026-01-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO odc_gap_scores"
        " (id, design_id, total_techniques, covered_count, gap_count, assessed_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        ("g1", "d1", 10, 7, 3, "2026-07-18T00:00:00"),
    )
    conn.commit()
    conn.close()

    card = _get_card(monkeypatch, db)

    assert _CARD_KEYS.issubset(card)
    assert card["key"] == "ODC"
    assert card["available"] is True
    m = _metrics(card)
    assert m["MITRE Coverage"] == "70.0%"
    assert m["Sigma Needed"] == 3


def test_odc_card_empty_db_unavailable(monkeypatch, tmp_path):
    """No ODC tables at all -> available=False, no exception, n/a coverage."""
    db = tmp_path / "empty.db"
    sqlite3.connect(str(db)).close()  # valid empty SQLite file, no tables

    card = _get_card(monkeypatch, db)

    assert card["available"] is False
    m = _metrics(card)
    assert m["MITRE Coverage"] == "n/a"
    assert m["Sigma Needed"] == 0
    assert card["badge"] == "yellow"


def test_odc_card_missing_gap_table_falls_back(monkeypatch, tmp_path):
    """odc_gap_scores missing but od_ttp_coverage present -> graceful fallback."""
    db = tmp_path / "ttp.db"
    conn = _sc(db)
    conn.executescript(ODC_TTP_DDL)
    conn.execute(
        "INSERT INTO od_ttp_coverage (id, ttp_id, state) VALUES (?, ?, ?)",
        ("t1", "T1059", "full"),
    )
    conn.execute(
        "INSERT INTO od_ttp_coverage (id, ttp_id, state) VALUES (?, ?, ?)",
        ("t2", "T1078", "none"),
    )
    conn.commit()
    conn.close()

    card = _get_card(monkeypatch, db)

    assert card["available"] is True
    m = _metrics(card)
    assert m["MITRE Coverage"] == "50.0%"
    assert m["Sigma Needed"] == 1


def test_odc_card_present_but_empty_tables(monkeypatch, tmp_path):
    """Tables exist but hold no rows -> available=True, coverage n/a (no raise)."""
    db = tmp_path / "empty_tables.db"
    conn = _sc(db)
    conn.executescript(ODC_GAP_DDL)
    conn.executescript(ODC_TTP_DDL)
    conn.commit()
    conn.close()

    card = _get_card(monkeypatch, db)

    assert card["available"] is True
    m = _metrics(card)
    assert m["MITRE Coverage"] == "n/a"


def test_odc_card_connection_error_graceful(monkeypatch):
    """Connection factory raising -> card returns available=False, no exception."""
    init_mod = importlib.import_module(_ODC_INIT)

    def _boom():
        raise RuntimeError("canvas db unreachable")

    monkeypatch.setattr(init_mod, "get_connection", _boom)
    comp = importlib.import_module(_COMPLIANCE)

    card = comp.get_odc_card()

    assert _CARD_KEYS.issubset(card)
    assert card["key"] == "ODC"
    assert card["available"] is False
    assert _metrics(card)["MITRE Coverage"] == "n/a"
