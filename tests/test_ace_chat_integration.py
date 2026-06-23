# CUI // SP-CTI
"""Integration tests for ACE chat trigger pipeline.

Tests six key scenarios:
1. Explicit @team trigger fires ACEController.launch()
2. Implicit trigger from 200+ char messages with 4+ RICOAS signals
3. Short casual messages do NOT trigger ACE
4. Chat manager stores coworker_instance_id in context_config
5. CLI bridge get_coworker_instances() returns instances for a context_id
6. coworker_thread._post_completion_chat_feedback() writes action_card to chat context
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, call, patch


# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_LONG_REQ_TEXT = (
    "The system shall support user authentication via SSO. "
    "The application must implement role-based access control. "
    "We need to build a REST API that should handle at minimum 1000 requests "
    "per second. The platform needs to integrate with existing LDAP directories. "
    "Requirement: all data must be encrypted at rest and in transit."
)
assert len(_LONG_REQ_TEXT) >= 200, "Sanity check: test message must be 200+ chars"

_SHORT_CASUAL = "hey there"


# ---------------------------------------------------------------------------
# 1. Explicit trigger: '@team ...' fires ACEController.launch()
# ---------------------------------------------------------------------------

def test_explicit_trigger_fires_ace():
    """'@team build REST API' calls ACEController.launch() with trigger_source='chat'."""
    from icdev.tools.ace.chat_trigger import maybe_launch_ace

    mock_ctrl = MagicMock()
    mock_ctrl.launch.return_value = "ace-abc123def456"

    with patch("icdev.tools.ace.controller.ACEController") as mock_cls:
        mock_cls.get_instance.return_value = mock_ctrl

        result = maybe_launch_ace(
            context_id="ctx-001",
            content="@team build REST API for user management",
            user_id="user-1",
        )

    assert result == "ace-abc123def456"
    mock_ctrl.launch.assert_called_once()

    args, kwargs = mock_ctrl.launch.call_args
    problem_text = kwargs.get("problem_text") or (args[0] if args else "")
    trigger_source = kwargs.get("trigger_source") or (args[1] if len(args) > 1 else "")
    trigger_ref = kwargs.get("trigger_ref") or (args[2] if len(args) > 2 else "")

    # @team prefix must be stripped from problem_text
    assert "@team" not in problem_text.lower()
    assert "build REST API" in problem_text

    assert trigger_source == "chat"
    assert trigger_ref == "ctx-001"


# ---------------------------------------------------------------------------
# 2. Implicit trigger: 200+ chars with 4+ RICOAS signals fires ACE
# ---------------------------------------------------------------------------

def test_implicit_trigger_long_requirements():
    """200+ char message with 4+ RICOAS signals fires ACEController.launch()."""
    from icdev.tools.ace.chat_trigger import maybe_launch_ace, count_ricoas_signals

    # Confirm the test message actually has 4+ signals
    signal_count = count_ricoas_signals(_LONG_REQ_TEXT)
    assert signal_count >= 4, f"Test message only has {signal_count} RICOAS signals; need 4+"

    mock_ctrl = MagicMock()
    mock_ctrl.launch.return_value = "ace-implicit001"

    with patch("icdev.tools.ace.controller.ACEController") as mock_cls:
        mock_cls.get_instance.return_value = mock_ctrl

        result = maybe_launch_ace(
            context_id="ctx-002",
            content=_LONG_REQ_TEXT,
            user_id="user-2",
        )

    assert result == "ace-implicit001"
    mock_ctrl.launch.assert_called_once()

    args, kwargs = mock_ctrl.launch.call_args
    trigger_source = kwargs.get("trigger_source") or (args[1] if len(args) > 1 else "")
    assert trigger_source == "chat"


# ---------------------------------------------------------------------------
# 3. No trigger: short casual message does NOT call launch()
# ---------------------------------------------------------------------------

def test_no_trigger_short_message():
    """Short casual message does NOT call ACEController.launch()."""
    from icdev.tools.ace.chat_trigger import maybe_launch_ace

    mock_ctrl = MagicMock()

    with patch("icdev.tools.ace.controller.ACEController") as mock_cls:
        mock_cls.get_instance.return_value = mock_ctrl

        result = maybe_launch_ace(
            context_id="ctx-003",
            content=_SHORT_CASUAL,
            user_id="user-3",
        )

    assert result is None
    mock_ctrl.launch.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Chat manager persists coworker_instance_id in context_config
# ---------------------------------------------------------------------------

def test_chat_manager_stores_coworker_link(tmp_path):
    """_check_coworker_trigger() persists coworker_instance_id to chat_contexts."""
    db_path = tmp_path / "chat_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE chat_contexts (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            title TEXT,
            context_config TEXT,
            created_at TEXT,
            updated_at TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO chat_contexts (id, user_id, title, context_config, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("ctx-004", "user-4", "Test Context", None, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    conn.commit()
    conn.close()

    # Use canonical icdev module (shim redirects tools.* -> icdev.tools.*)
    import icdev.tools.dashboard.chat_manager as cm_mod

    def _fake_get_conn(db_path=None, **_kwargs):
        c = sqlite3.connect(str(db_path) if db_path else ":memory:")
        return c

    with patch.object(cm_mod, "DB_PATH", db_path), \
         patch.object(cm_mod, "get_connection", side_effect=_fake_get_conn):
        cm_mod._check_coworker_trigger(
            "ctx-004",
            "@team build a pipeline",
            {"coworker_instance_id": "ace-link999"},
        )

    conn2 = sqlite3.connect(str(db_path))
    row = conn2.execute(
        "SELECT context_config FROM chat_contexts WHERE id = ?", ("ctx-004",)
    ).fetchone()
    conn2.close()

    assert row is not None, "Row must exist in chat_contexts"
    assert row[0] is not None, "context_config must have been written by _check_coworker_trigger"
    config = json.loads(row[0])
    assert config.get("coworker_instance_id") == "ace-link999"


# ---------------------------------------------------------------------------
# 5. CLI bridge get_coworker_instances() returns instances for context_id
# ---------------------------------------------------------------------------

def test_cli_bridge_get_coworker_instances():
    """get_coworker_instances() returns instance list from dashboard API."""
    import tools.chat.cli_bridge as bridge_mod

    mock_response = {
        "instances": [
            {"instance_id": "ace-bridge001", "state": "complete"},
        ]
    }

    with patch.object(bridge_mod, "_http", return_value=mock_response) as mock_http:
        result = bridge_mod.get_coworker_instances("ctx-005")

    mock_http.assert_called_once_with("GET", "/api/chat/ctx-005/coworker-instances")
    assert len(result) == 1
    assert result[0]["instance_id"] == "ace-bridge001"
    assert result[0]["state"] == "complete"


# ---------------------------------------------------------------------------
# 6. coworker_thread._post_completion_chat_feedback() → chat add_message
# ---------------------------------------------------------------------------

def test_coworker_done_writes_chat_message():
    """_post_completion_chat_feedback() calls ChatManager.add_message() with action_card."""
    import icdev.tools.ace.coworker_thread as cwt_mod

    # Minimal config_json stored in ace_instances for a chat-triggered run
    config_json = json.dumps({
        "trigger_source": "chat",
        "trigger_ref": "ctx-done-001",
        "user_id": "user-ace",
        "problem_text": "Build a REST API for user management.",
        "project_id": "",
    })

    # Stub get_canvas_connection to return a fake row
    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchone.return_value = (config_json,)
    fake_conn.__enter__ = lambda s: s
    fake_conn.__exit__ = MagicMock(return_value=False)

    mock_add_message = MagicMock(return_value=42)

    spec = MagicMock()
    spec.coworker_id = "cw-test-001"
    spec.role_id = "software_engineer"
    spec.trust_tier = "yellow"

    thread = cwt_mod.CoWorkerThread.__new__(cwt_mod.CoWorkerThread)
    thread.instance_id = "inst-done-001"
    thread.spec = spec

    role = MagicMock()
    role.display_name = "Software Engineer"

    with patch("icdev.tools.db.storage.get_canvas_connection", return_value=fake_conn), \
         patch("icdev.tools.chat.chat_manager.ChatManager.add_message", mock_add_message):
        thread._post_completion_chat_feedback(role, "all steps completed")

    mock_add_message.assert_called_once()
    args, kwargs = mock_add_message.call_args
    ctx_id = args[0] if args else kwargs.get("context_id")
    assert ctx_id == "ctx-done-001"
    assert kwargs.get("role") == "assistant"
    assert kwargs.get("content_type") == "action_card"
    assert "cw-test-001" in kwargs.get("content", "")


def test_coworker_done_no_op_when_not_chat_trigger():
    """_post_completion_chat_feedback() is a no-op when trigger_source != 'chat'."""
    import icdev.tools.ace.coworker_thread as cwt_mod

    config_json = json.dumps({
        "trigger_source": "dashboard",
        "trigger_ref": "",
        "user_id": "user-dash",
        "problem_text": "Some task.",
    })

    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchone.return_value = (config_json,)
    fake_conn.__enter__ = lambda s: s
    fake_conn.__exit__ = MagicMock(return_value=False)

    mock_add_message = MagicMock()

    spec = MagicMock()
    spec.coworker_id = "cw-dash-001"
    spec.role_id = "analyst"
    spec.trust_tier = "yellow"

    thread = cwt_mod.CoWorkerThread.__new__(cwt_mod.CoWorkerThread)
    thread.instance_id = "inst-dash-001"
    thread.spec = spec

    role = MagicMock()
    role.display_name = "Analyst"

    with patch("icdev.tools.db.storage.get_canvas_connection", return_value=fake_conn), \
         patch("icdev.tools.chat.chat_manager.ChatManager.add_message", mock_add_message):
        thread._post_completion_chat_feedback(role, "all steps completed")

    mock_add_message.assert_not_called()


def test_coworker_done_no_op_when_instance_missing():
    """_post_completion_chat_feedback() is a no-op when ace_instances row is absent."""
    import icdev.tools.ace.coworker_thread as cwt_mod

    fake_conn = MagicMock()
    fake_conn.execute.return_value.fetchone.return_value = None
    fake_conn.__enter__ = lambda s: s
    fake_conn.__exit__ = MagicMock(return_value=False)

    mock_add_message = MagicMock()

    spec = MagicMock()
    spec.coworker_id = "cw-missing-001"
    spec.role_id = "analyst"
    spec.trust_tier = "yellow"

    thread = cwt_mod.CoWorkerThread.__new__(cwt_mod.CoWorkerThread)
    thread.instance_id = "inst-missing-001"
    thread.spec = spec

    role = MagicMock()

    with patch("icdev.tools.db.storage.get_canvas_connection", return_value=fake_conn), \
         patch("icdev.tools.chat.chat_manager.ChatManager.add_message", mock_add_message):
        thread._post_completion_chat_feedback(role, "done")

    mock_add_message.assert_not_called()
