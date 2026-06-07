# CUI // SP-CTI
"""Regression tests for tools/iqe/adapters/zta.py — PG-primary hardening (pgp-vfy-12).

The zta_lac_audit canvas table has NO classification/tenant_id columns.  On
PostgreSQL, a plain get_connection() attaches the Flask request RLS predicate,
which raises UndefinedColumn on this table — the adapter's broad except then
silently returns [] (empty IQE results).  These tests lock in that the adapter
fetches via the RLS-free get_canvas_connection() and parses its JSON columns.
"""
from __future__ import annotations

import importlib
import json
import sqlite3

from tools.iqe.executor import Executor
from tools.iqe.parser import parse

_SCHEMA = """
CREATE TABLE IF NOT EXISTS zta_lac_audit (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_name         TEXT NOT NULL,
    decision              TEXT NOT NULL,
    deny_reasons          TEXT DEFAULT '[]',
    principal_citizenship TEXT,
    principal_clearance   TEXT,
    principal_cois        TEXT DEFAULT '[]',
    resource_id           TEXT,
    resource_is_eci       INTEGER DEFAULT 0,
    action                TEXT DEFAULT 'read',
    environment           TEXT DEFAULT '{}',
    break_glass_activated INTEGER DEFAULT 0,
    audit_json            TEXT DEFAULT '{}',
    created_at            TEXT NOT NULL
);
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO zta_lac_audit (scenario_name, decision, deny_reasons, "
        "principal_citizenship, principal_clearance, principal_cois, resource_id, "
        "resource_is_eci, action, environment, break_glass_activated, audit_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "SCENARIO_ECI_SPILLAGE_PREVENTION",
            "DENY",
            json.dumps(["ECI export control violation: citizenship 'FVEY' not permitted."]),
            "FVEY",
            "TS//SCI",
            json.dumps(["COI_ALPHA"]),
            "res-eci-alpha-007",
            1,
            "read",
            json.dumps({"network": "JWICS", "location": "CONUS"}),
            0,
            json.dumps({"decision": "DENY"}),
            "2026-06-04T19:23:00",
        ),
    )
    conn.commit()
    return conn


# 1 — adapter parses JSON columns into Python objects -------------------------

def test_audit_adapter_parses_json_fields() -> None:
    from tools.iqe.adapters.zta import lac_audit_trail_adapter

    conn = _make_conn()
    rows = lac_audit_trail_adapter(conn)
    conn.close()

    assert len(rows) == 1
    row = rows[0]
    assert row["decision"] == "DENY"
    assert row["resource_id"] == "res-eci-alpha-007"
    # JSON columns are decoded, not left as raw strings
    assert isinstance(row["deny_reasons"], list)
    assert isinstance(row["principal_cois"], list)
    assert isinstance(row["environment"], dict)
    assert row["environment"]["network"] == "JWICS"
    assert isinstance(row["audit_json"], dict)


# 2 — REGRESSION: conn=None fetch uses RLS-free canvas connection (the fix) ----

def test_audit_adapter_uses_canvas_connection_not_rls(monkeypatch) -> None:
    """The fixed query: with no caller conn, the adapter must build its
    connection via get_canvas_connection() (RLS disabled), NOT get_connection()
    which attaches the Flask RLS predicate and breaks on this columnless table."""
    seeded = _make_conn()

    # Patch the exact module object the adapter's local import resolves from.
    storage = importlib.import_module("tools.db.storage")

    canvas_calls = {"n": 0}

    def _fake_canvas(*_a, **_k):
        canvas_calls["n"] += 1
        return seeded

    def _forbidden_get_connection(*_a, **_k):
        raise AssertionError(
            "adapter used get_connection() (RLS path) instead of get_canvas_connection()"
        )

    monkeypatch.setattr(storage, "get_canvas_connection", _fake_canvas)
    monkeypatch.setattr(storage, "get_connection", _forbidden_get_connection)

    from tools.iqe.adapters.zta import lac_audit_trail_adapter

    rows = lac_audit_trail_adapter(None)
    seeded.close()

    assert canvas_calls["n"] == 1
    assert len(rows) == 1
    assert rows[0]["decision"] == "DENY"


# 3 — scenarios adapter returns the canonical static set ----------------------

def test_scenarios_adapter_returns_canonical() -> None:
    from tools.iqe.adapters.zta import lac_scenarios_adapter

    rows = lac_scenarios_adapter(None)
    assert isinstance(rows, list)
    assert len(rows) >= 1


# 4 — smoke: parse + execute foreach over lac.audit_trail ---------------------

def test_smoke_parse_and_execute_audit_trail() -> None:
    from tools.iqe.adapters.zta import lac_audit_trail_adapter

    conn = _make_conn()
    ast = parse('foreach a in lac.audit_trail select decision, resource_id')
    ex = Executor()
    ex.register_collection("lac.audit_trail", lac_audit_trail_adapter)
    result = ex.run(ast, conn=conn)
    conn.close()

    assert len(result) == 1
    assert result[0]["decision"] == "DENY"
    assert result[0]["resource_id"] == "res-eci-alpha-007"
