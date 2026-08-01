#!/usr/bin/env python3
# CUI // SP-CTI
"""Regression tests for BDC cATO Twin POA&M FK integrity (task bdr-sec-2).

Covers the fix in poam_auto_generator.generate_from_violations():

  1. The generated POAM ``id`` returned by ``INSERT ... RETURNING id`` equals
     the actual serial primary key, and each violation row's ``poam_id`` FK
     links to the correct POAM item. (The previous code read ``cur.lastrowid``,
     which on psycopg2 is the table OID / 0 — NOT the PK — silently corrupting
     the FK on PostgreSQL.)

  2. A simulated insert/link failure emits a log record and is surfaced in the
     returned result (``errors``) rather than being swallowed by the old
     ``except Exception: pass``.

These tests drive the real ``tools.db.storage`` translation layer via a
StorageConnection over in-memory SQLite so that PG-native ``%s`` placeholders
and ``RETURNING`` are exercised exactly as they are in production (raw sqlite3
connections bypass the ``%s`` → ``?`` translator and cannot be used here).
"""

import logging
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools.boundary_canvas.cato_twin import poam_auto_generator as _pag  # noqa: E402
from tools.boundary_canvas.cato_twin.poam_auto_generator import (  # noqa: E402
    generate_from_violations,
)
from tools.db.storage import StorageConnection  # noqa: E402

# Read the name off the module rather than hardcoding it. The module moved from
# `logging.getLogger(__name__)` to the structured ICDEV logger in d83c2a0f6,
# which renamed it to "icdev.boundary_canvas.poam_auto_generator" and silently
# broke every caplog assertion below.
_LOGGER_NAME = _pag.logger.name

SCHEMA = """
CREATE TABLE poam_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    weakness_id TEXT NOT NULL,
    weakness_description TEXT,
    severity TEXT,
    source TEXT,
    control_id TEXT,
    status TEXT DEFAULT 'open',
    corrective_action TEXT,
    milestone_date TEXT,
    completion_date TEXT,
    responsible_party TEXT,
    resources_required TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE compliance_twin_violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    framework TEXT NOT NULL,
    control_id TEXT NOT NULL,
    violation_type TEXT NOT NULL,
    severity TEXT DEFAULT 'moderate',
    details TEXT,
    poam_id INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

_SNAP = "snap-fk-1"
_PROJ = "proj-001"


@pytest.fixture
def sconn():
    """StorageConnection over in-memory SQLite (exercises the %s / RETURNING path)."""
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(SCHEMA)
    conn = StorageConnection(raw, "sqlite")
    yield conn
    raw.close()


def _seed_violations(conn, snapshot_id=_SNAP, project_id=_PROJ):
    """Insert two not_satisfied violations so the generator has work to do."""
    rows = [
        ("FedRAMP Moderate", "AC-2", "not_satisfied", "high"),
        ("FedRAMP Moderate", "IA-5", "not_satisfied", "moderate"),
    ]
    for framework, ctrl, vtype, sev in rows:
        conn.execute(
            "INSERT INTO compliance_twin_violations "
            "(snapshot_id, project_id, framework, control_id, violation_type, severity) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (snapshot_id, project_id, framework, ctrl, vtype, sev),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# 1. FK integrity — RETURNING id equals the real PK, links are correct
# ---------------------------------------------------------------------------

class TestPoamFkIntegrity:
    def test_poam_id_equals_actual_pk_and_links_correct_violation(self, sconn):
        _seed_violations(sconn)

        result = generate_from_violations(_SNAP, _PROJ, conn=sconn)

        assert result["new_items"] == 2
        assert result["errors"] == []

        # Every generated POAM has a real serial PK.
        poam_ids = {
            r["id"]
            for r in sconn.execute("SELECT id FROM poam_items").fetchall()
        }
        assert len(poam_ids) == 2
        assert all(isinstance(pid, int) and pid > 0 for pid in poam_ids)

        # The FK on each violation resolves to the POAM created for the SAME
        # control — proving RETURNING id gave the real PK (not an OID / 0).
        joined = sconn.execute(
            "SELECT v.control_id AS vctrl, p.control_id AS pctrl, "
            "       v.poam_id AS fk, p.id AS pk "
            "FROM compliance_twin_violations v "
            "JOIN poam_items p ON v.poam_id = p.id "
            "WHERE v.snapshot_id = %s",
            (_SNAP,),
        ).fetchall()

        assert len(joined) == 2, "both violations must link to a POAM item"
        for row in joined:
            assert row["fk"] == row["pk"]          # FK == actual PK
            assert row["vctrl"] == row["pctrl"]    # linked to the right POAM
            assert row["fk"] in poam_ids

    def test_all_violations_get_non_null_poam_id(self, sconn):
        _seed_violations(sconn)
        generate_from_violations(_SNAP, _PROJ, conn=sconn)

        viols = sconn.execute(
            "SELECT poam_id FROM compliance_twin_violations WHERE snapshot_id = %s",
            (_SNAP,),
        ).fetchall()
        assert len(viols) == 2
        assert all(v["poam_id"] is not None for v in viols)


# ---------------------------------------------------------------------------
# 2. Failure surfacing — no silent success
# ---------------------------------------------------------------------------

class _NoIdCursor:
    """Stand-in cursor whose RETURNING fetch yields no row."""

    def fetchone(self):
        return None


class TestFailureSurfacing:
    @pytest.fixture(autouse=True)
    def _propagate_to_caplog(self):
        """``get_logger`` sets ``propagate = False`` to avoid double-logging to
        the root handler — but caplog installs its handler ON the root, so the
        records below never reach it. Re-enable propagation for these tests."""
        prior = _pag.logger.propagate
        _pag.logger.propagate = True
        yield
        _pag.logger.propagate = prior

    def test_insert_returning_no_id_is_logged_and_surfaced(self, sconn, caplog):
        _seed_violations(sconn)
        real_execute = sconn.execute

        def fake_execute(sql, params=None):
            if sql.strip().upper().startswith("INSERT INTO POAM_ITEMS"):
                return _NoIdCursor()
            return real_execute(sql, params)

        with patch.object(sconn, "execute", side_effect=fake_execute):
            with caplog.at_level(logging.ERROR, logger=_LOGGER_NAME):
                result = generate_from_violations(_SNAP, _PROJ, conn=sconn)

        # Not silently successful: nothing counted, errors surfaced.
        assert result["new_items"] == 0
        assert result["errors"], "insert failure must be surfaced in result['errors']"
        # A log record was emitted.
        messages = [r.getMessage() for r in caplog.records]
        assert any("returned no id" in m for m in messages), messages

    def test_link_update_failure_is_logged_and_surfaced_but_poam_created(
        self, sconn, caplog
    ):
        _seed_violations(sconn)
        real_execute = sconn.execute

        def fake_execute(sql, params=None):
            if sql.strip().upper().startswith("UPDATE COMPLIANCE_TWIN_VIOLATIONS"):
                raise RuntimeError("simulated FK link failure")
            return real_execute(sql, params)

        with patch.object(sconn, "execute", side_effect=fake_execute):
            with caplog.at_level(logging.ERROR, logger=_LOGGER_NAME):
                result = generate_from_violations(_SNAP, _PROJ, conn=sconn)

        # POAM items were still created (insert succeeded)...
        assert result["new_items"] == 2
        count = sconn.execute("SELECT COUNT(*) AS c FROM poam_items").fetchone()["c"]
        assert count == 2
        # ...but the link failures are surfaced, NOT swallowed.
        assert len(result["errors"]) == 2
        messages = [r.getMessage() for r in caplog.records]
        assert any("Failed to link violation" in m for m in messages), messages
