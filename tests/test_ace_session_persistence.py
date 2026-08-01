"""Tests for ace_sessions multi-turn conversation persistence."""
from __future__ import annotations

import json
import sqlite3
import uuid

import pytest

from icdev.tools.ace.db.init_db import SCHEMA


@pytest.fixture()
def ace_db(tmp_path):
    """SQLite DB with full ACE schema (including ace_sessions)."""
    db_path = tmp_path / "ace_sessions_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


@pytest.fixture()
def instance_id(ace_db):
    """Insert a minimal ace_instances row; return its id."""
    iid = str(uuid.uuid4())
    ace_db.execute(
        "INSERT INTO ace_instances (id, name, role_id) VALUES (?, ?, ?)",
        (iid, "test-instance", "ai_developer"),
    )
    ace_db.commit()
    return iid


# ---------------------------------------------------------------------------
# Acceptance: SELECT session_id FROM ace_sessions WHERE resume_token = ?
# returns 1 row after insert
# ---------------------------------------------------------------------------

def test_select_by_resume_token_returns_one_row(ace_db, instance_id):
    token = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    ace_db.execute(
        """INSERT INTO ace_sessions
               (session_id, instance_id, conversation_history, resume_token)
           VALUES (?, ?, ?, ?)""",
        (sid, instance_id, "[]", token),
    )
    ace_db.commit()

    rows = ace_db.execute(
        "SELECT session_id FROM ace_sessions WHERE resume_token = ?", (token,)
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == sid


# ---------------------------------------------------------------------------
# Core: create session, append turn, reload, assert history len == 1
# ---------------------------------------------------------------------------

def test_append_turn_and_reload(ace_db, instance_id):
    token = str(uuid.uuid4())
    sid = str(uuid.uuid4())

    # Create session with empty history
    ace_db.execute(
        """INSERT INTO ace_sessions
               (session_id, instance_id, conversation_history, resume_token)
           VALUES (?, ?, ?, ?)""",
        (sid, instance_id, json.dumps([]), token),
    )
    ace_db.commit()

    # Append one turn
    turn = {"role": "user", "content": "Hello, ACE!"}
    new_history = json.dumps([turn])
    ace_db.execute(
        """UPDATE ace_sessions
           SET conversation_history = ?,
               last_user_message    = ?,
               turn_count           = turn_count + 1
         WHERE session_id = ?""",
        (new_history, turn["content"], sid),
    )
    ace_db.commit()

    # Reload and assert
    row = ace_db.execute(
        "SELECT conversation_history, turn_count FROM ace_sessions WHERE session_id = ?",
        (sid,),
    ).fetchone()
    assert row is not None
    history = json.loads(row[0])
    assert len(history) == 1
    assert history[0]["role"] == "user"
    assert row[1] == 1


# ---------------------------------------------------------------------------
# resume_token uniqueness enforced by DB
# ---------------------------------------------------------------------------

def test_duplicate_resume_token_raises(ace_db, instance_id):
    token = str(uuid.uuid4())
    ace_db.execute(
        "INSERT INTO ace_sessions (session_id, instance_id, conversation_history, resume_token) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), instance_id, "[]", token),
    )
    ace_db.commit()

    with pytest.raises(sqlite3.IntegrityError):
        ace_db.execute(
            "INSERT INTO ace_sessions (session_id, instance_id, conversation_history, resume_token) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), instance_id, "[]", token),
        )

    # The failed INSERT opened an implicit transaction that the exception left
    # dangling. The test's intent is "the UNIQUE constraint fires", not "leave a
    # write transaction open" — so close it rather than suppress the leak guard
    # added in #1066, which is correctly reporting a real leak here.
    ace_db.rollback()
