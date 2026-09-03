# CUI // SP-CTI
"""rmf-cyc-01 — two compliance audit events that were written and never admitted.

``oscal_generator._log_audit`` has always written ``event_type='oscal_generated'``
and ``cato_monitor._log_audit_event`` has always written
``'cato_evidence_collected'``. Neither name was in
``tools.audit.audit_logger.VALID_EVENT_TYPES``, so the CHECK on
``audit_trail.event_type`` refused every row — and each writer wraps its INSERT
in ``except Exception: print("Warning: ...", file=sys.stderr)``, so the refusal
went to a stream nothing reads while the generator returned success.

These tests drive the REAL WRITERS against a table whose CHECK is generated the
way ``init_icdev_db.py`` generates it. Asserting that the two strings are
members of the tuple would pass while the constraint stayed narrow, and would
not have caught this defect: the tuple was never the thing that was wrong to
begin with — the absence of a row was.
"""
from __future__ import annotations

import sqlite3

import pytest


def _audit_table_sql() -> str:
    """audit_trail with the CHECK built from VALID_EVENT_TYPES, as init does."""
    from tools.audit.audit_logger import event_type_check_sql

    return f"""
        CREATE TABLE audit_trail (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id     TEXT,
            event_type     TEXT NOT NULL {event_type_check_sql()},
            actor          TEXT,
            action         TEXT,
            details        TEXT,
            affected_files TEXT,
            classification TEXT,
            timestamp      TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """


@pytest.fixture()
def audit_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "audit.db"
    raw = sqlite3.connect(db_path)
    raw.execute(_audit_table_sql())
    raw.commit()
    raw.close()
    return str(db_path)


def _rows(db_path: str) -> list[tuple]:
    from tools.db.storage import get_connection

    conn = get_connection(db_path=db_path)
    try:
        return [
            (dict(r)["event_type"], dict(r)["actor"])
            for r in conn.execute("SELECT event_type, actor FROM audit_trail").fetchall()
        ]
    finally:
        conn.close()


def test_oscal_generation_actually_lands_an_audit_row(audit_db, capsys):
    """The OSCAL writer's own audit call, run for real."""
    from tools.compliance.oscal_generator import _log_audit
    from tools.db.storage import get_connection

    conn = get_connection(db_path=audit_db)
    try:
        _log_audit(conn, "p1", "OSCAL SSP generated", {"affected_files": ["/tmp/ssp.json"]})
    finally:
        conn.close()

    # No warning on stderr, and — the part that matters — a row.
    assert "Could not log audit event" not in capsys.readouterr().err
    assert ("oscal_generated", "icdev-compliance-engine") in _rows(audit_db)


def test_cato_evidence_collection_actually_lands_an_audit_row(audit_db, capsys):
    """The cATO writer's own audit call, run for real."""
    from tools.compliance.cato_monitor import _log_audit_event
    from tools.db.storage import get_connection

    conn = get_connection(db_path=audit_db)
    try:
        _log_audit_event(conn, "p1", "Evidence collected", {"control_id": "AC-2"})
    finally:
        conn.close()

    assert "Could not log audit event" not in capsys.readouterr().err
    assert ("cato_evidence_collected", "icdev-cato-monitor") in _rows(audit_db)


def test_a_genuinely_invalid_event_type_is_still_refused(audit_db):
    """The constraint was widened by exactly two names, not disarmed."""
    from tools.db.storage import get_connection

    conn = get_connection(db_path=audit_db)
    try:
        with pytest.raises(Exception):
            conn.execute(
                "INSERT INTO audit_trail (project_id, event_type, actor) VALUES (%s, %s, %s)",
                ("p1", "definitely_not_an_event_type", "test"),
            )
            conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass
