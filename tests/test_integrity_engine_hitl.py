# CUI // SP-CTI
"""Tests for the SIPA engine HITL promote / reject path (sipa-engine-03).

Covers ``tools/integrity/engine.py``'s ``promote()`` / ``reject()`` — the
human-in-the-loop transition that is the ONLY way a quarantined assessment leaves
the pipeline:

  * **promote** flips ``integrity_assessments.status`` -> ``'approved'``, appends an
    ``integrity_authorizations`` row (``authorized=1``), and writes an append-only
    ``integrity_promoted`` ``audit_trail`` event.
  * **reject** flips ``status`` -> ``'rejected'``, appends an authorization row
    (``authorized=0``), and writes an ``integrity_rejected`` audit event.
  * **terminal guard** — a second promote/reject on an already-decided assessment
    is refused (append-only disposition; never re-decided).
  * **missing assessment** — returns an ``{"error": ...}`` dict, never raises.

Everything runs against an in-memory SQLite connection: the integrity schema via
``init_db`` plus a minimal ``audit_trail`` table matching ``log_event``'s INSERT
shape, so the audit write lands in the same connection and can be asserted.
"""
import sqlite3

import pytest

from tools.integrity import engine
from tools.integrity.db import init_db as init_db_mod

# audit_trail shape that tools.audit.audit_logger.log_event INSERTs into
# (mirrors tools/db/init_icdev_db.py, sans the event_type CHECK — log_event
# validates the type Python-side against VALID_EVENT_TYPES).
_AUDIT_TRAIL_DDL = """
CREATE TABLE IF NOT EXISTS audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    affected_files TEXT,
    classification TEXT DEFAULT 'CUI',
    ip_address TEXT,
    session_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
"""


@pytest.fixture
def conn(monkeypatch):
    # engine._backend_of falls back to ICDEV_STORAGE_BACKEND for a raw sqlite3
    # connection (no _backend attr), so force sqlite to get '?' placeholders.
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db_mod.init_db(c)
    c.execute(_AUDIT_TRAIL_DDL)
    c.commit()
    yield c
    c.close()


def _stage_quarantine(conn, *, source_ref="staging/quarantine/x", status="quarantine"):
    """Insert a minimal quarantined assessment row and return its id."""
    cur = conn.execute(
        "INSERT INTO integrity_assessments "
        "(source_type, source_ref, mode, status) VALUES (?, ?, ?, ?)",
        ("local", source_ref, "provenance_blind", status),
    )
    conn.commit()
    return int(cur.lastrowid)


# --------------------------------------------------------------------------- #
# promote
# --------------------------------------------------------------------------- #
def test_promote_flips_status_and_writes_rows(conn):
    aid = _stage_quarantine(conn)

    result = engine.promote(aid, reviewed_by="isso@dod.mil", reason="reviewed clean", conn=conn)

    assert result["success"] is True
    assert result["status"] == "approved"
    assert result["reviewed_by"] == "isso@dod.mil"
    assert result["authorization_id"] > 0

    # Assessment row transitioned to the terminal approved state.
    status = conn.execute(
        "SELECT status FROM integrity_assessments WHERE id = ?", (aid,)
    ).fetchone()["status"]
    assert status == "approved"

    # Append-only authorization row recorded (authorized=1) with reviewer + reason.
    auth = conn.execute(
        "SELECT assessment_id, authorized, reason, reviewed_by FROM integrity_authorizations "
        "WHERE id = ?",
        (result["authorization_id"],),
    ).fetchone()
    assert auth["assessment_id"] == aid
    assert auth["authorized"] in (1, True)
    assert auth["reason"] == "reviewed clean"
    assert auth["reviewed_by"] == "isso@dod.mil"

    # Append-only audit_trail event written.
    audit = conn.execute(
        "SELECT event_type, actor FROM audit_trail WHERE event_type = 'integrity_promoted'"
    ).fetchall()
    assert len(audit) == 1
    assert audit[0]["actor"] == "isso@dod.mil"
    assert result["audit_id"] > 0


def test_promote_is_the_only_path_to_approved(conn):
    # A freshly scored 'assessed' row only becomes 'approved' via promote().
    aid = _stage_quarantine(conn, status="assessed")
    assert engine._get_status(conn, aid) == "assessed"

    engine.promote(aid, reviewed_by="reviewer", reason=None, conn=conn)
    assert engine._get_status(conn, aid) == "approved"


# --------------------------------------------------------------------------- #
# reject
# --------------------------------------------------------------------------- #
def test_reject_flips_status_and_writes_rows(conn):
    aid = _stage_quarantine(conn)

    result = engine.reject(aid, reviewed_by="isso@dod.mil", reason="undisclosed network egress", conn=conn)

    assert result["success"] is True
    assert result["status"] == "rejected"
    assert result["authorization_id"] > 0

    status = conn.execute(
        "SELECT status FROM integrity_assessments WHERE id = ?", (aid,)
    ).fetchone()["status"]
    assert status == "rejected"

    auth = conn.execute(
        "SELECT authorized, reason, reviewed_by FROM integrity_authorizations WHERE id = ?",
        (result["authorization_id"],),
    ).fetchone()
    assert auth["authorized"] in (0, False)
    assert auth["reason"] == "undisclosed network egress"
    assert auth["reviewed_by"] == "isso@dod.mil"

    audit = conn.execute(
        "SELECT actor FROM audit_trail WHERE event_type = 'integrity_rejected'"
    ).fetchall()
    assert len(audit) == 1
    assert audit[0]["actor"] == "isso@dod.mil"


# --------------------------------------------------------------------------- #
# Guards — terminal state + missing assessment
# --------------------------------------------------------------------------- #
def test_double_promote_is_refused(conn):
    aid = _stage_quarantine(conn)
    assert engine.promote(aid, reviewed_by="r", reason=None, conn=conn)["success"] is True

    second = engine.promote(aid, reviewed_by="r", reason=None, conn=conn)
    assert "error" in second
    assert second["status"] == "approved"
    # No second authorization row was appended.
    n = conn.execute(
        "SELECT COUNT(*) FROM integrity_authorizations WHERE assessment_id = ?", (aid,)
    ).fetchone()[0]
    assert n == 1


def test_cannot_reject_after_promote(conn):
    aid = _stage_quarantine(conn)
    engine.promote(aid, reviewed_by="r", reason=None, conn=conn)

    result = engine.reject(aid, reviewed_by="r", reason="changed my mind", conn=conn)
    assert "error" in result
    assert engine._get_status(conn, aid) == "approved"


def test_cannot_promote_after_reject(conn):
    aid = _stage_quarantine(conn)
    engine.reject(aid, reviewed_by="r", reason="bad", conn=conn)

    result = engine.promote(aid, reviewed_by="r", reason=None, conn=conn)
    assert "error" in result
    assert engine._get_status(conn, aid) == "rejected"


def test_promote_missing_assessment_returns_error(conn):
    result = engine.promote(999999, reviewed_by="r", reason=None, conn=conn)
    assert "error" in result
    assert "not found" in result["error"]


def test_reject_missing_assessment_returns_error(conn):
    result = engine.reject(999999, reviewed_by="r", reason=None, conn=conn)
    assert "error" in result
    assert "not found" in result["error"]
