# CUI // SP-CTI
"""Integration tests: ACE co-worker link storage via ChatManager.

Covers:
  * test_chat_manager_stores_coworker_link — _check_coworker_trigger persists
    coworker_instance_id in chat_contexts.context_config when the dispatch hook
    returns one, so the chat UI can render a "View Co-Worker Team" button.
  * test_no_link_when_hook_context_empty — no-op path when hook carries no id.
  * test_link_merges_into_existing_config — existing context_config keys survive.

The get_coworker_instances() function (tools/chat/cli_bridge.py) is the
read-side counterpart; its contract is verified in test_chat_cli_bridge_coworker.py.
This file focuses on the write-side (storage) path only.
"""
from __future__ import annotations

import importlib
import json
import sqlite3

import pytest

# Import the module object so we can monkeypatch DB_PATH and then call
# _check_coworker_trigger — which reads DB_PATH from its module globals at
# call time (not import time), making the monkeypatch effective.
_cm = importlib.import_module("tools.dashboard.chat_manager")
_check_coworker_trigger = _cm._check_coworker_trigger

_CHAT_CONTEXTS_DDL = """
CREATE TABLE IF NOT EXISTS chat_contexts (
    id               TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    tenant_id        TEXT,
    title            TEXT DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'active',
    project_id       TEXT,
    agent_model      TEXT DEFAULT 'sonnet',
    system_prompt    TEXT,
    context_config   TEXT,
    dirty_version    INTEGER DEFAULT 0,
    message_count    INTEGER DEFAULT 0,
    classification   TEXT DEFAULT 'CUI',
    created_at       TEXT DEFAULT (datetime('now')),
    updated_at       TEXT DEFAULT (datetime('now'))
)
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_context(db_path, context_id: str, config: dict | None = None) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO chat_contexts (id, user_id, status, context_config) "
        "VALUES (?, 'test-user', 'active', ?)",
        (context_id, json.dumps(config) if config else None),
    )
    conn.commit()
    conn.close()


def _read_config(db_path, context_id: str) -> dict:
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT context_config FROM chat_contexts WHERE id = ?", (context_id,)
    ).fetchone()
    conn.close()
    if row and row[0]:
        return json.loads(row[0])
    return {}


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def chat_db(tmp_path, monkeypatch):
    """Fresh temp SQLite DB with chat_contexts; DB_PATH and backend patched."""
    db_path = tmp_path / "chat_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_CHAT_CONTEXTS_DDL)
    conn.commit()
    conn.close()
    # Force SQLite backend so get_connection(db_path=...) hits the custom file,
    # not the PostgreSQL primary (or its SQLite fallback at data/icdev.db).
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    # Patch the module-level DB_PATH so get_connection(db_path=str(DB_PATH))
    # inside _check_coworker_trigger hits our temp DB.
    monkeypatch.setattr(_cm, "DB_PATH", db_path)
    return db_path


# ---------------------------------------------------------------------------
# Tests — write-side (storage)
# ---------------------------------------------------------------------------


def test_chat_manager_stores_coworker_link(chat_db):
    """After trigger, context_config contains the ACE instance id."""
    context_id = "ctx-ace-link-01"
    ace_instance_id = "ace-inst-abc123"

    _seed_context(chat_db, context_id)

    _check_coworker_trigger(
        context_id,
        "build a data pipeline with four RICOAS signals",
        {"coworker_instance_id": ace_instance_id},
    )

    config = _read_config(chat_db, context_id)
    assert config.get("coworker_instance_id") == ace_instance_id


def test_no_link_when_hook_context_empty(chat_db):
    """_check_coworker_trigger is a no-op when hook context has no coworker_instance_id."""
    context_id = "ctx-ace-noop-02"
    _seed_context(chat_db, context_id)

    _check_coworker_trigger(context_id, "just a quick hello", {})

    config = _read_config(chat_db, context_id)
    assert "coworker_instance_id" not in config


def test_link_merges_into_existing_config(chat_db):
    """coworker_instance_id is merged into existing context_config; other keys survive."""
    context_id = "ctx-ace-merge-03"
    _seed_context(chat_db, context_id, config={"reasoning_mode": "auto"})

    _check_coworker_trigger(
        context_id,
        "assemble a compliance team",
        {"coworker_instance_id": "ace-inst-xyz"},
    )

    config = _read_config(chat_db, context_id)
    assert config.get("coworker_instance_id") == "ace-inst-xyz"
    assert config.get("reasoning_mode") == "auto"
