#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for ACE co-worker instance linking in chat_manager (task ace-chat-02).

Covers ``_check_coworker_trigger`` — the purely additive hook that persists a
``coworker_instance_id`` (set by the ``chat_message_before`` extension hook) into the
``chat_contexts.context_config`` JSON so the chat UI can show a "View Co-Worker Team"
button. Verifies: link stored, idempotent re-link, and no-op on missing/empty/bad input.
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import tools.dashboard.chat_manager as cm


@pytest.fixture
def linked_db(monkeypatch, tmp_path):
    """Temp-file chat_contexts table wired into the module's get_connection.

    A file-based DB lets _check_coworker_trigger open/commit/close its own
    connection (as it does in production) while the test reads back via a fresh one.
    """
    db_file = tmp_path / "chat.db"

    def _open(*_a, **_k):
        c = sqlite3.connect(str(db_file))
        c.row_factory = sqlite3.Row
        return c

    seed = _open()
    seed.execute(
        "CREATE TABLE chat_contexts (id TEXT PRIMARY KEY, context_config TEXT, updated_at TEXT)"
    )
    seed.execute(
        "INSERT INTO chat_contexts (id, context_config, updated_at) VALUES (?, ?, ?)",
        ("ctx-1", json.dumps({"reasoning_mode": "off"}), "2026-06-06T00:00:00Z"),
    )
    seed.commit()
    seed.close()

    monkeypatch.setattr(cm, "get_connection", _open)
    return _open


def _config(open_conn, context_id="ctx-1") -> dict:
    conn = open_conn()
    try:
        row = conn.execute(
            "SELECT context_config FROM chat_contexts WHERE id = ?", (context_id,)
        ).fetchone()
        return json.loads(row["context_config"])
    finally:
        conn.close()


class TestCheckCoworkerTrigger:
    def test_stores_link(self, linked_db):
        cm._check_coworker_trigger("ctx-1", "spin up a team", {"coworker_instance_id": "ace-abc"})
        cfg = _config(linked_db)
        assert cfg["coworker_instance_id"] == "ace-abc"
        # existing config preserved
        assert cfg["reasoning_mode"] == "off"

    def test_idempotent_relink(self, linked_db):
        cm._check_coworker_trigger("ctx-1", "x", {"coworker_instance_id": "ace-abc"})
        cm._check_coworker_trigger("ctx-1", "x", {"coworker_instance_id": "ace-abc"})
        assert _config(linked_db)["coworker_instance_id"] == "ace-abc"

    def test_relink_to_new_instance(self, linked_db):
        cm._check_coworker_trigger("ctx-1", "x", {"coworker_instance_id": "ace-abc"})
        cm._check_coworker_trigger("ctx-1", "x", {"coworker_instance_id": "ace-xyz"})
        assert _config(linked_db)["coworker_instance_id"] == "ace-xyz"

    def test_noop_when_absent(self, linked_db):
        cm._check_coworker_trigger("ctx-1", "just chatting", {"role": "user"})
        assert "coworker_instance_id" not in _config(linked_db)

    def test_noop_when_empty(self, linked_db):
        cm._check_coworker_trigger("ctx-1", "x", {"coworker_instance_id": ""})
        assert "coworker_instance_id" not in _config(linked_db)

    def test_noop_when_context_not_dict(self, linked_db):
        # Must not raise on unexpected hook return shapes.
        cm._check_coworker_trigger("ctx-1", "x", None)  # type: ignore[arg-type]
        assert "coworker_instance_id" not in _config(linked_db)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
