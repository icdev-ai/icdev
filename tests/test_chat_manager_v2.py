# CUI // SP-CTI
"""Tests for icdev.tools.chat.chat_manager — context lifecycle + coworker_instance_id."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# In-memory DB fixture
# ---------------------------------------------------------------------------


_SCHEMA = """
    CREATE TABLE IF NOT EXISTS chat_contexts (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        tenant_id TEXT,
        title TEXT DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active',
        intake_session_id TEXT,
        project_id TEXT,
        agent_model TEXT DEFAULT 'sonnet',
        system_prompt TEXT,
        context_config TEXT,
        dirty_version INTEGER DEFAULT 0,
        message_count INTEGER DEFAULT 0,
        last_activity_at TEXT,
        classification TEXT DEFAULT 'CUI',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        context_id TEXT NOT NULL,
        turn_number INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        content_type TEXT DEFAULT 'text',
        metadata TEXT,
        is_compressed INTEGER DEFAULT 0,
        compression_tier TEXT,
        classification TEXT DEFAULT 'CUI',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
"""


def _make_db(tmp_path: Path) -> str:
    """Create the SQLite DB file with schema and return its path."""
    db_path = str(tmp_path / "chat.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


def _patch_conn(db_path: str):
    """Patch get_connection to return a NEW connection on each call (same DB file).

    Each ChatManager method calls conn.close() in its finally block, so
    returning the same connection object would result in 'closed database'
    errors on the second call. Using side_effect instead of return_value
    creates a fresh connection per call.

    The factory delegates to the REAL get_connection rather than building a
    bare sqlite3 connection. ChatManager authors ``%s`` placeholders for
    PostgreSQL and it is StorageConnection that rewrites them to ``?`` for
    SQLite; skipping that layer made every statement raise
    ``near "%": syntax error`` and failed all 23 tests in this file.

    The real function is captured before patch() replaces the attribute, so
    the factory cannot recurse into its own replacement.
    """
    from icdev.tools.db.storage import get_connection as _real_get_connection

    def _factory(*_args, **_kwargs):
        return _real_get_connection(db_path)

    return patch("icdev.tools.db.storage.get_connection", side_effect=_factory)


# ---------------------------------------------------------------------------
# create_context
# ---------------------------------------------------------------------------


class TestCreateContext:
    def test_returns_ctx_prefixed_id(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1", tenant_id="t-1")
            ctx_id = mgr.create_context(title="Test")
        assert ctx_id.startswith("ctx-")

    def test_row_persisted(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1", tenant_id="t-1")
            ctx_id = mgr.create_context(title="My Chat", classification="CUI")
            ctx = mgr.get_context(ctx_id)
        assert ctx is not None
        assert ctx["title"] == "My Chat"
        assert ctx["user_id"] == "u-1"
        assert ctx["status"] == "active"

    def test_config_stored_as_json(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-2")
            ctx_id = mgr.create_context(config={"key": "value"})
            ctx = mgr.get_context(ctx_id)
        assert ctx["context_config"]["key"] == "value"

    def test_default_classification_applied(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-3", classification="SECRET")
            ctx_id = mgr.create_context()
            ctx = mgr.get_context(ctx_id)
        assert ctx["classification"] == "SECRET"


# ---------------------------------------------------------------------------
# get_context
# ---------------------------------------------------------------------------


class TestGetContext:
    def test_returns_dict_for_existing(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1")
            ctx_id = mgr.create_context(title="Query test")
            result = mgr.get_context(ctx_id)
        assert result is not None
        assert result["id"] == ctx_id
        assert isinstance(result["context_config"], dict)

    def test_returns_none_for_missing(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1")
            result = mgr.get_context("ctx-nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# coworker_instance_id — the key Phase 1e capability
# ---------------------------------------------------------------------------


class TestCoworkerInstance:
    def test_set_and_get_round_trip(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1")
            ctx_id = mgr.create_context()
            mgr.set_coworker_instance(ctx_id, "inst-abc123")
            result = mgr.get_coworker_instance(ctx_id)
        assert result == "inst-abc123"

    def test_set_preserves_existing_config_keys(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1")
            ctx_id = mgr.create_context(config={"existing_key": "keep_me"})
            mgr.set_coworker_instance(ctx_id, "inst-xyz")
            ctx = mgr.get_context(ctx_id)
        assert ctx["context_config"]["existing_key"] == "keep_me"
        assert ctx["context_config"]["coworker_instance_id"] == "inst-xyz"

    def test_get_returns_none_when_not_set(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1")
            ctx_id = mgr.create_context()
            result = mgr.get_coworker_instance(ctx_id)
        assert result is None

    def test_set_raises_for_nonexistent_context(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1")
            with pytest.raises(ValueError, match="not found"):
                mgr.set_coworker_instance("ctx-does-not-exist", "inst-x")

    def test_module_level_set_get(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import (
                ChatManager, set_coworker_instance, get_coworker_instance,
            )
            mgr = ChatManager(user_id="u-1")
            ctx_id = mgr.create_context()
            set_coworker_instance(ctx_id, "inst-module-level")
            result = get_coworker_instance(ctx_id)
        assert result == "inst-module-level"

    def test_overwrite_instance_id(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1")
            ctx_id = mgr.create_context()
            mgr.set_coworker_instance(ctx_id, "inst-first")
            mgr.set_coworker_instance(ctx_id, "inst-second")
            result = mgr.get_coworker_instance(ctx_id)
        assert result == "inst-second"


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class TestMessages:
    def test_add_message_increments_turn(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1")
            ctx_id = mgr.create_context()
            mgr.add_message(ctx_id, role="user", content="Hello")
            mgr.add_message(ctx_id, role="assistant", content="Hi there")
            msgs = mgr.get_messages(ctx_id)
        assert len(msgs) == 2
        assert msgs[0]["turn_number"] == 1
        assert msgs[1]["turn_number"] == 2

    def test_message_count_updated_on_context(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1")
            ctx_id = mgr.create_context()
            mgr.add_message(ctx_id, role="user", content="One")
            mgr.add_message(ctx_id, role="assistant", content="Two")
            ctx = mgr.get_context(ctx_id)
        assert ctx["message_count"] == 2

    def test_invalid_role_raises(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1")
            ctx_id = mgr.create_context()
            with pytest.raises(ValueError, match="Invalid role"):
                mgr.add_message(ctx_id, role="bot", content="bad role")

    def test_metadata_stored_as_json(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1")
            ctx_id = mgr.create_context()
            mgr.add_message(ctx_id, role="user", content="test", metadata={"source": "rag"})
            msgs = mgr.get_messages(ctx_id)
        assert msgs[0]["metadata"]["source"] == "rag"

    def test_get_last_message(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1")
            ctx_id = mgr.create_context()
            mgr.add_message(ctx_id, role="user", content="first")
            mgr.add_message(ctx_id, role="assistant", content="last")
            last = mgr.get_last_message(ctx_id)
        assert last["content"] == "last"
        assert last["role"] == "assistant"

    def test_get_last_message_none_when_empty(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1")
            ctx_id = mgr.create_context()
            last = mgr.get_last_message(ctx_id)
        assert last is None


# ---------------------------------------------------------------------------
# Status update
# ---------------------------------------------------------------------------


class TestStatusUpdate:
    def test_update_status(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1")
            ctx_id = mgr.create_context()
            mgr.update_status(ctx_id, "completed")
            ctx = mgr.get_context(ctx_id)
        assert ctx["status"] == "completed"

    def test_invalid_status_raises(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1")
            ctx_id = mgr.create_context()
            with pytest.raises(ValueError, match="Invalid status"):
                mgr.update_status(ctx_id, "flying")


# ---------------------------------------------------------------------------
# list_contexts
# ---------------------------------------------------------------------------


class TestListContexts:
    def test_list_returns_users_contexts(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-list", tenant_id="t-1")
            mgr.create_context(title="A")
            mgr.create_context(title="B")
            other = ChatManager(user_id="u-other", tenant_id="t-1")
            other.create_context(title="C")
            results = mgr.list_contexts()
        assert len(results) == 2
        titles = {r["title"] for r in results}
        assert titles == {"A", "B"}

    def test_list_filters_by_status(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-s")
            ctx_id = mgr.create_context()
            mgr.update_status(ctx_id, "archived")
            active = mgr.list_contexts(status="active")
            archived = mgr.list_contexts(status="archived")
        assert len(active) == 0
        assert len(archived) == 1


# ---------------------------------------------------------------------------
# update_config
# ---------------------------------------------------------------------------


class TestUpdateConfig:
    def test_merges_keys(self, tmp_path):
        db_path = _make_db(tmp_path)
        with _patch_conn(db_path):
            from icdev.tools.chat.chat_manager import ChatManager
            mgr = ChatManager(user_id="u-1")
            ctx_id = mgr.create_context(config={"a": 1})
            mgr.update_config(ctx_id, {"b": 2})
            ctx = mgr.get_context(ctx_id)
        assert ctx["context_config"]["a"] == 1
        assert ctx["context_config"]["b"] == 2
