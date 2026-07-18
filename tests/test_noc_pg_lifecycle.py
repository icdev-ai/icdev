# CUI // SP-CTI
"""NOCC PostgreSQL-dialect lifecycle regression tests (fix/noc-pg-500s).

Reproduces the two PR #557 E2E 500s that only surfaced on PostgreSQL:

  1. POST /api/noc/alarms with an alarm_source outside the known-adapter enum
     (the E2E harness sends ``alarm_source='e2e-nms'``).  On PG the
     ``noc_alarms_alarm_source_check`` CHECK constraint rejects it → 500.
     The SQLite production schema omits that CHECK, so it passed locally.

  2. POST /api/noc/mops/generate.  ``generate_mop`` records
     ``generated_by='ai_template'`` whenever the LLM is unavailable (always in
     CI).  On PG the ``noc_mops_generated_by_check`` CHECK only allowed
     ('manual','ai') → 500.  The SQLite schema omits the CHECK, so it passed
     locally.

These tests deliberately create SQLite tables that MIRROR the PostgreSQL CHECK
constraints, so the PG-only defect class is reproducible on the SQLite test
backend and can never regress silently again.
"""
from __future__ import annotations

import sqlite3

import pytest

from tools.noc_canvas.alarm_correlator import create_alarm
from tools.noc_canvas.constants import ALARM_SOURCES, MOP_GENERATED_BY
from tools.noc_canvas.mop_generator import generate_mop, save_mop

# ── PG-mirrored DDL (CHECK constraints match tools/db/schema/pg_consolidated.sql) ──

_ALARMS_DDL_PG_MIRROR = f"""
CREATE TABLE noc_alarms (
    id              TEXT PRIMARY KEY,
    alarm_source    TEXT NOT NULL DEFAULT 'custom'
                        CHECK(alarm_source IN ({",".join("'" + s + "'" for s in ALARM_SOURCES)})),
    source_alarm_id TEXT DEFAULT '',
    severity        TEXT NOT NULL DEFAULT 'warning',
    alarm_type      TEXT NOT NULL DEFAULT 'interface',
    device_name     TEXT DEFAULT '',
    device_ip       TEXT DEFAULT '',
    circuit_id      TEXT DEFAULT '',
    carrier         TEXT DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    raw_payload     TEXT DEFAULT '{{}}',
    classification  TEXT DEFAULT 'CUI',
    first_seen      TEXT,
    last_seen       TEXT,
    acknowledged    INTEGER DEFAULT 0,
    acknowledged_by TEXT DEFAULT '',
    acknowledged_at TEXT,
    cleared         INTEGER DEFAULT 0,
    cleared_at      TEXT,
    suppressed      INTEGER DEFAULT 0
);
"""

_MOPS_DDL_PG_MIRROR = f"""
CREATE TABLE noc_mops (
    id             TEXT PRIMARY KEY,
    mop_number     TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL,
    rfc_id         TEXT,
    steps_json     TEXT NOT NULL DEFAULT '[]',
    generated_by   TEXT DEFAULT 'manual'
                       CHECK(generated_by IN ({",".join("'" + g + "'" for g in MOP_GENERATED_BY)})),
    ai_prompt      TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    created_at     TEXT,
    updated_at     TEXT
);
"""


@pytest.fixture
def alarms_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_ALARMS_DDL_PG_MIRROR)
    yield conn
    conn.close()


@pytest.fixture
def mops_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_MOPS_DDL_PG_MIRROR)
    yield conn
    conn.close()


# ── Alarm ingest ────────────────────────────────────────────────────────────


def test_unknown_alarm_source_is_coerced_to_custom(alarms_conn):
    """An out-of-enum alarm_source must be coerced to 'custom' (not 500)."""
    alarm_id = create_alarm(
        alarms_conn,
        {"alarm_source": "e2e-nms", "description": "E2E synthetic alarm", "severity": "major"},
    )
    assert alarm_id
    row = alarms_conn.execute(
        "SELECT alarm_source, severity, raw_payload FROM noc_alarms WHERE id = ?",
        (alarm_id,),
    ).fetchone()
    assert row["alarm_source"] == "custom"
    assert row["severity"] == "major"  # valid value preserved
    # Original source is preserved for traceability.
    assert "e2e-nms" in (row["raw_payload"] or "")


def test_known_alarm_source_is_preserved(alarms_conn):
    alarm_id = create_alarm(
        alarms_conn,
        {"alarm_source": "solarwinds", "description": "iface down", "severity": "critical"},
    )
    row = alarms_conn.execute(
        "SELECT alarm_source FROM noc_alarms WHERE id = ?", (alarm_id,)
    ).fetchone()
    assert row["alarm_source"] == "solarwinds"


def test_unknown_severity_and_type_are_coerced(alarms_conn):
    """Defensive: unknown severity/alarm_type fall back to safe enum defaults."""
    alarm_id = create_alarm(
        alarms_conn,
        {"alarm_source": "custom", "description": "x", "severity": "bogus", "alarm_type": "nope"},
    )
    row = alarms_conn.execute(
        "SELECT severity, alarm_type FROM noc_alarms WHERE id = ?", (alarm_id,)
    ).fetchone()
    assert row["severity"] == "warning"
    assert row["alarm_type"] == "interface"


# ── MOP generate / save ─────────────────────────────────────────────────────


def test_ai_template_generated_by_is_accepted(mops_conn):
    """generate_mop falls back to 'ai_template'; the DB enum must accept it."""
    mop = generate_mop(
        {"title": "E2E firmware upgrade", "change_type": "standard", "risk_level": "medium"},
        context="upgrade core routers",
    )
    # Whatever the generator produces MUST be a declared enum member.
    assert mop["generated_by"] in MOP_GENERATED_BY
    mop_id = save_mop(mops_conn, "rfc-123", mop)
    assert mop_id
    row = mops_conn.execute(
        "SELECT generated_by, steps_json FROM noc_mops WHERE id = ?", (mop_id,)
    ).fetchone()
    assert row["generated_by"] in MOP_GENERATED_BY
    assert row["steps_json"]


def test_ai_template_is_a_declared_enum_member():
    """Guard the exact code/schema contract that produced the 500."""
    assert "ai_template" in MOP_GENERATED_BY
    assert "ai" in MOP_GENERATED_BY
    assert "manual" in MOP_GENERATED_BY
