# CUI // SP-CTI
"""Tests for the five sibling canvas-compliance cards (oxf-fix-01).

SDC, PDC, BDC, DDC and IDC previously opened their per-canvas SQLite files
(``data/security_canvas.db``, ``pipeline_canvas.db``, ``boundary_canvas.db``,
``data_canvas.db``, ``infra_canvas.db``) directly via the raw ``_open()``
helper.  On PostgreSQL-primary deployments those files are absent, so every
card was permanently ``available=False``.  Each card now reads through its own
canvas connection accessor (``tools.<canvas>.db.init_db.get_connection``), which
resolves ``get_canvas_connection`` under PostgreSQL — mirroring the ODC fix in
PR #470.

conftest forces ICDEV_STORAGE_BACKEND=sqlite.  These tests seed rows through a
translating ``StorageConnection`` on a temp SQLite DB and patch each canvas
connection factory (shim-aware: ``importlib.import_module`` + ``setattr`` on the
source module so the call-time ``from ... import get_connection`` inside the
card resolves to the patched factory).  SDC and IDC also read the shared
icdev.db (threat_models / cf_provision_log) via ``compliance._open`` — that is
patched to return ``None`` so those tests isolate the canvas path.
"""

import importlib
import sqlite3

from tools.db.storage import StorageConnection

_COMPLIANCE = "tools.canvas_compliance.compliance"

_SDC_INIT = "tools.security_canvas.db.init_db"
_PDC_INIT = "tools.pipeline.db.init_db"
_BDC_INIT = "tools.boundary_canvas.db.init_db"
_DDC_INIT = "tools.data_canvas.db.init_db"
_IDC_INIT = "tools.infra_canvas.db.init_db"

_CARD_KEYS = {"key", "name", "path", "color", "badge", "available", "metrics"}

# ── DDL mirrors each canvas's db/init_db.py (canvas SQLite schema) ──────────────
SDC_SNAP_DDL = """
CREATE TABLE sdc_attack_snapshots (
    id            TEXT PRIMARY KEY,
    component_id  TEXT NOT NULL,
    nodes_json    TEXT NOT NULL DEFAULT '[]',
    edges_json    TEXT NOT NULL DEFAULT '[]',
    caldera_op_id TEXT,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""
SDC_ASSESS_DDL = """
CREATE TABLE sc_assessments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    assessment_type TEXT NOT NULL,
    risk_score      REAL DEFAULT 0,
    findings_json   TEXT DEFAULT '[]',
    ran_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
"""
PDC_CHECKS_DDL = """
CREATE TABLE pc_compliance_checks (
    id            TEXT PRIMARY KEY,
    pipeline_id   TEXT,
    check_type    TEXT NOT NULL,
    passed        INTEGER DEFAULT 0,
    failed        INTEGER DEFAULT 0,
    findings_json TEXT DEFAULT '[]',
    ran_at        TEXT DEFAULT CURRENT_TIMESTAMP
);
"""
BDC_ISA_DDL = """
CREATE TABLE bd_isa_tracker (
    id                 TEXT PRIMARY KEY,
    design_id          TEXT,
    interconnection_id TEXT NOT NULL,
    isa_doc_id         TEXT,
    status             TEXT DEFAULT 'draft',
    expiry_date        TEXT,
    review_date        TEXT,
    owner              TEXT,
    notes              TEXT,
    created_at         TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at         TEXT DEFAULT CURRENT_TIMESTAMP
);
"""
DDC_ASSESS_DDL = """
CREATE TABLE dd_assessments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    assessment_type TEXT NOT NULL,
    findings_json   TEXT DEFAULT '[]',
    score           REAL DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
"""
DDC_NODES_DDL = """
CREATE TABLE data_nodes (
    id             TEXT PRIMARY KEY,
    design_id      TEXT NOT NULL,
    node_type      TEXT NOT NULL DEFAULT 'table',
    label          TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP
);
"""
IDC_ASSESS_DDL = """
CREATE TABLE idc_assessments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    assessment_type TEXT DEFAULT 'compliance',
    findings_json   TEXT DEFAULT '[]',
    score           REAL DEFAULT 0.0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

# A recent timestamp so age-based badges are deterministic.
_RECENT = "2026-07-18T00:00:00"


# ── helpers ────────────────────────────────────────────────────────────────────

def _sc(path) -> StorageConnection:
    """A translating StorageConnection over a temp SQLite file."""
    raw = sqlite3.connect(str(path))
    raw.row_factory = sqlite3.Row
    return StorageConnection(raw, "sqlite")


def _patch_conn(monkeypatch, init_path: str, db_path) -> None:
    """Point a canvas connection factory at our temp DB (fresh conn each call)."""
    init_mod = importlib.import_module(init_path)
    monkeypatch.setattr(init_mod, "get_connection", lambda: _sc(db_path))


def _patch_boom(monkeypatch, init_path: str) -> None:
    """Make a canvas connection factory raise."""
    init_mod = importlib.import_module(init_path)

    def _boom():
        raise RuntimeError("canvas db unreachable")

    monkeypatch.setattr(init_mod, "get_connection", _boom)


def _patch_open_none(monkeypatch) -> None:
    """Disable the shared icdev.db reads (threat_models / cf_provision_log)."""
    comp = importlib.import_module(_COMPLIANCE)
    monkeypatch.setattr(comp, "_open", lambda _path: None)


def _empty_db(tmp_path, name="empty.db"):
    """A valid but table-less SQLite file."""
    db = tmp_path / name
    sqlite3.connect(str(db)).close()
    return db


def _metrics(card: dict) -> dict:
    return {m["label"]: m["value"] for m in card["metrics"]}


def _card(fn_name: str) -> dict:
    comp = importlib.import_module(_COMPLIANCE)
    return getattr(comp, fn_name)()


# ───────────────────────────────────────────────────────────────────────────────
# SDC — Security Canvas
# ───────────────────────────────────────────────────────────────────────────────

def test_sdc_card_computes(monkeypatch, tmp_path):
    db = tmp_path / "sdc.db"
    conn = _sc(db)
    conn.executescript(SDC_SNAP_DDL)
    conn.executescript(SDC_ASSESS_DDL)
    conn.execute(
        "INSERT INTO sdc_attack_snapshots (id, component_id) VALUES (?, ?)",
        ("s1", "c1"),
    )
    conn.execute(
        "INSERT INTO sdc_attack_snapshots (id, component_id) VALUES (?, ?)",
        ("s2", "c2"),
    )
    conn.execute(
        "INSERT INTO sc_assessments (id, assessment_type, ran_at) VALUES (?, ?, ?)",
        ("a1", "threat", _RECENT),
    )
    conn.commit()
    conn.close()

    _patch_open_none(monkeypatch)
    _patch_conn(monkeypatch, _SDC_INIT, db)
    card = _card("get_sdc_card")

    assert _CARD_KEYS.issubset(card)
    assert card["key"] == "SDC"
    assert card["available"] is True
    assert _metrics(card)["Attack Paths"] == 2


def test_sdc_card_empty_db_unavailable(monkeypatch, tmp_path):
    db = _empty_db(tmp_path, "sdc_empty.db")
    _patch_open_none(monkeypatch)
    _patch_conn(monkeypatch, _SDC_INIT, db)
    card = _card("get_sdc_card")

    assert card["available"] is False
    m = _metrics(card)
    assert m["Attack Paths"] == 0
    assert m["Threat Model Age"] == "n/a"
    assert card["badge"] == "yellow"


def test_sdc_card_missing_assessments_table_graceful(monkeypatch, tmp_path):
    """snapshots present, sc_assessments missing -> available=True, age n/a."""
    db = tmp_path / "sdc_partial.db"
    conn = _sc(db)
    conn.executescript(SDC_SNAP_DDL)
    conn.execute(
        "INSERT INTO sdc_attack_snapshots (id, component_id) VALUES (?, ?)",
        ("s1", "c1"),
    )
    conn.commit()
    conn.close()

    _patch_open_none(monkeypatch)
    _patch_conn(monkeypatch, _SDC_INIT, db)
    card = _card("get_sdc_card")

    assert card["available"] is True
    m = _metrics(card)
    assert m["Attack Paths"] == 1
    assert m["Threat Model Age"] == "n/a"


def test_sdc_card_present_but_empty(monkeypatch, tmp_path):
    db = tmp_path / "sdc_present.db"
    conn = _sc(db)
    conn.executescript(SDC_SNAP_DDL)
    conn.executescript(SDC_ASSESS_DDL)
    conn.commit()
    conn.close()

    _patch_open_none(monkeypatch)
    _patch_conn(monkeypatch, _SDC_INIT, db)
    card = _card("get_sdc_card")

    assert card["available"] is True
    assert _metrics(card)["Attack Paths"] == 0


def test_sdc_card_connection_error_graceful(monkeypatch):
    _patch_open_none(monkeypatch)
    _patch_boom(monkeypatch, _SDC_INIT)
    card = _card("get_sdc_card")

    assert _CARD_KEYS.issubset(card)
    assert card["available"] is False
    assert _metrics(card)["Attack Paths"] == 0


# ───────────────────────────────────────────────────────────────────────────────
# PDC — Pipeline Canvas
# ───────────────────────────────────────────────────────────────────────────────

def test_pdc_card_computes(monkeypatch, tmp_path):
    db = tmp_path / "pdc.db"
    conn = _sc(db)
    conn.executescript(PDC_CHECKS_DDL)
    # Older check ignored (ORDER BY ran_at DESC).
    conn.execute(
        "INSERT INTO pc_compliance_checks (id, check_type, passed, failed, ran_at)"
        " VALUES (?, ?, ?, ?, ?)",
        ("c0", "stig", 1, 9, "2026-01-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO pc_compliance_checks (id, check_type, passed, failed, ran_at)"
        " VALUES (?, ?, ?, ?, ?)",
        ("c1", "stig", 9, 1, _RECENT),
    )
    conn.commit()
    conn.close()

    _patch_conn(monkeypatch, _PDC_INIT, db)
    card = _card("get_pdc_card")

    assert card["key"] == "PDC"
    assert card["available"] is True
    assert _metrics(card)["Compliance Score"] == "90.0%"
    assert card["badge"] == "green"


def test_pdc_card_empty_db_unavailable(monkeypatch, tmp_path):
    db = _empty_db(tmp_path, "pdc_empty.db")
    _patch_conn(monkeypatch, _PDC_INIT, db)
    card = _card("get_pdc_card")

    assert card["available"] is False
    assert _metrics(card)["Compliance Score"] == "n/a"
    assert card["badge"] == "yellow"


def test_pdc_card_present_but_empty(monkeypatch, tmp_path):
    db = tmp_path / "pdc_present.db"
    conn = _sc(db)
    conn.executescript(PDC_CHECKS_DDL)
    conn.commit()
    conn.close()

    _patch_conn(monkeypatch, _PDC_INIT, db)
    card = _card("get_pdc_card")

    assert card["available"] is True
    assert _metrics(card)["Compliance Score"] == "n/a"


def test_pdc_card_connection_error_graceful(monkeypatch):
    _patch_boom(monkeypatch, _PDC_INIT)
    card = _card("get_pdc_card")

    assert card["available"] is False
    assert _metrics(card)["Compliance Score"] == "n/a"


# ───────────────────────────────────────────────────────────────────────────────
# BDC — Boundary Canvas
# ───────────────────────────────────────────────────────────────────────────────

def test_bdc_card_computes(monkeypatch, tmp_path):
    db = tmp_path / "bdc.db"
    conn = _sc(db)
    conn.executescript(BDC_ISA_DDL)
    conn.execute(
        "INSERT INTO bd_isa_tracker (id, interconnection_id, status, expiry_date)"
        " VALUES (?, ?, ?, ?)",
        ("i1", "ic1", "active", "2030-01-01"),
    )
    conn.execute(
        "INSERT INTO bd_isa_tracker (id, interconnection_id, status, expiry_date)"
        " VALUES (?, ?, ?, ?)",
        ("i2", "ic2", "signed", "2031-06-01"),
    )
    conn.commit()
    conn.close()

    _patch_conn(monkeypatch, _BDC_INIT, db)
    card = _card("get_bdc_card")

    assert card["key"] == "BDC"
    assert card["available"] is True
    m = _metrics(card)
    assert m["Active ISAs"] == 2
    # Both expiries far in the future -> green.
    assert card["badge"] == "green"


def test_bdc_card_empty_db_unavailable(monkeypatch, tmp_path):
    db = _empty_db(tmp_path, "bdc_empty.db")
    _patch_conn(monkeypatch, _BDC_INIT, db)
    card = _card("get_bdc_card")

    assert card["available"] is False
    m = _metrics(card)
    assert m["Active ISAs"] == 0
    assert m["Min Days to Expiry"] == "n/a"
    assert card["badge"] == "yellow"


def test_bdc_card_present_but_empty(monkeypatch, tmp_path):
    db = tmp_path / "bdc_present.db"
    conn = _sc(db)
    conn.executescript(BDC_ISA_DDL)
    conn.commit()
    conn.close()

    _patch_conn(monkeypatch, _BDC_INIT, db)
    card = _card("get_bdc_card")

    assert card["available"] is True
    assert _metrics(card)["Active ISAs"] == 0
    assert card["badge"] == "yellow"  # no ISAs tracked yet


def test_bdc_card_connection_error_graceful(monkeypatch):
    _patch_boom(monkeypatch, _BDC_INIT)
    card = _card("get_bdc_card")

    assert card["available"] is False
    assert _metrics(card)["Active ISAs"] == 0


# ───────────────────────────────────────────────────────────────────────────────
# DDC — Data Canvas
# ───────────────────────────────────────────────────────────────────────────────

def test_ddc_card_computes(monkeypatch, tmp_path):
    import json

    db = tmp_path / "ddc.db"
    conn = _sc(db)
    conn.executescript(DDC_ASSESS_DDL)
    conn.executescript(DDC_NODES_DDL)
    conn.execute(
        "INSERT INTO dd_assessments (id, assessment_type, findings_json, created_at)"
        " VALUES (?, ?, ?, ?)",
        ("a1", "pii",
         json.dumps([{"rule_id": "R-PII-1", "title": "PII exposure detected"}]),
         _RECENT),
    )
    conn.execute(
        "INSERT INTO data_nodes (id, design_id, node_type, label) VALUES (?, ?, ?, ?)",
        ("n1", "d1", "pii_store", "Customers"),
    )
    conn.commit()
    conn.close()

    _patch_conn(monkeypatch, _DDC_INIT, db)
    card = _card("get_ddc_card")

    assert card["key"] == "DDC"
    assert card["available"] is True
    # 1 PII finding + 1 pii-typed node.
    assert _metrics(card)["PII Exposures"] == 2
    assert card["badge"] == "yellow"


def test_ddc_card_empty_db_unavailable(monkeypatch, tmp_path):
    db = _empty_db(tmp_path, "ddc_empty.db")
    _patch_conn(monkeypatch, _DDC_INIT, db)
    card = _card("get_ddc_card")

    assert card["available"] is False
    assert _metrics(card)["PII Exposures"] == 0
    # pii_count == 0 == DDC_PII_GREEN -> green (existing empty semantics).
    assert card["badge"] == "green"


def test_ddc_card_missing_nodes_table_graceful(monkeypatch, tmp_path):
    """dd_assessments present, data_nodes missing -> available=True, no raise."""
    import json

    db = tmp_path / "ddc_partial.db"
    conn = _sc(db)
    conn.executescript(DDC_ASSESS_DDL)
    conn.execute(
        "INSERT INTO dd_assessments (id, assessment_type, findings_json, created_at)"
        " VALUES (?, ?, ?, ?)",
        ("a1", "pii",
         json.dumps([{"rule_id": "R-PII-1", "title": "PII exposure detected"}]),
         _RECENT),
    )
    conn.commit()
    conn.close()

    _patch_conn(monkeypatch, _DDC_INIT, db)
    card = _card("get_ddc_card")

    assert card["available"] is True
    assert _metrics(card)["PII Exposures"] == 1


def test_ddc_card_connection_error_graceful(monkeypatch):
    _patch_boom(monkeypatch, _DDC_INIT)
    card = _card("get_ddc_card")

    assert card["available"] is False
    assert _metrics(card)["PII Exposures"] == 0


# ───────────────────────────────────────────────────────────────────────────────
# IDC — Infra Canvas
# ───────────────────────────────────────────────────────────────────────────────

def test_idc_card_computes(monkeypatch, tmp_path):
    import json

    db = tmp_path / "idc.db"
    conn = _sc(db)
    conn.executescript(IDC_ASSESS_DDL)
    conn.execute(
        "INSERT INTO idc_assessments (id, design_id, findings_json, created_at)"
        " VALUES (?, ?, ?, ?)",
        ("a1", "d1",
         json.dumps([{"rule_id": "D-1", "title": "Configuration drift found"}]),
         _RECENT),
    )
    conn.commit()
    conn.close()

    _patch_open_none(monkeypatch)  # skip cf_provision_log (shared icdev.db)
    _patch_conn(monkeypatch, _IDC_INIT, db)
    card = _card("get_idc_card")

    assert card["key"] == "IDC"
    assert card["available"] is True
    assert _metrics(card)["IaC Drift Count"] == 1
    assert card["badge"] == "yellow"


def test_idc_card_empty_db_unavailable(monkeypatch, tmp_path):
    db = _empty_db(tmp_path, "idc_empty.db")
    _patch_open_none(monkeypatch)
    _patch_conn(monkeypatch, _IDC_INIT, db)
    card = _card("get_idc_card")

    assert card["available"] is False
    assert _metrics(card)["IaC Drift Count"] == 0
    # drift_count == 0 == IDC_DRIFT_GREEN -> green (existing empty semantics).
    assert card["badge"] == "green"


def test_idc_card_present_but_empty(monkeypatch, tmp_path):
    db = tmp_path / "idc_present.db"
    conn = _sc(db)
    conn.executescript(IDC_ASSESS_DDL)
    conn.commit()
    conn.close()

    _patch_open_none(monkeypatch)
    _patch_conn(monkeypatch, _IDC_INIT, db)
    card = _card("get_idc_card")

    assert card["available"] is True
    assert _metrics(card)["IaC Drift Count"] == 0


def test_idc_card_connection_error_graceful(monkeypatch):
    _patch_open_none(monkeypatch)
    _patch_boom(monkeypatch, _IDC_INIT)
    card = _card("get_idc_card")

    assert _CARD_KEYS.issubset(card)
    assert card["available"] is False
    assert _metrics(card)["IaC Drift Count"] == 0
