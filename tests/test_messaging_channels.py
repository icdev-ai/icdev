"""Unit tests for messaging channel integrations (Teams, MatterMost, GitHub, GitLab, Skype).

Tests cover:
- Gateway adapter signature verification
- Gateway adapter webhook parsing
- Listener command dispatch via listener_base
- Notification adapter health checks
- DataBridge connector table dispatch

All tests are offline (no real network calls); external HTTP is patched where needed.
"""
# CUI // SP-CTI
from __future__ import annotations

import hashlib
import hmac
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ── listener_base ─────────────────────────────────────────────────────────────

class TestListenerBase(unittest.TestCase):
    """Tests for the platform-agnostic command dispatcher."""

    def setUp(self):
        import importlib
        import tools.notifications.adapters.listener_base as lb
        self.lb = importlib.reload(lb)

    def _mock_conn(self, monkeypatch=None):
        """Return a mock connection context with lastrowid and execute()."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value = mock_conn
        mock_conn.fetchall.return_value = []
        mock_conn.fetchone.return_value = None
        return mock_conn

    @patch("tools.notifications.adapters.listener_base._create_task")
    def test_process_command_build(self, mock_create):
        mock_create.return_value = {"id": "task-abc123", "status": "backlog"}
        reply = self.lb.process_command("/build Fix the login timeout", platform="GitHub")
        self.assertIn("Task Created", reply)
        self.assertIn("Fix the login timeout", reply)
        mock_create.assert_called_once()

    @patch("tools.notifications.adapters.listener_base._create_task")
    def test_icdev_prefix_stripped(self, mock_create):
        mock_create.return_value = {"id": "task-xyz", "status": "backlog"}
        reply = self.lb.process_command("!icdev /fix broken pipeline", platform="GitLab")
        self.assertIn("Task Created", reply)
        args = mock_create.call_args
        self.assertEqual(args.kwargs.get("task_type") or args[0][1], "fix")

    def test_status_command_returns_board(self):
        with patch("tools.notifications.adapters.listener_base._get_board_summary") as mock_summary:
            mock_summary.return_value = "📊 Task Board"
            reply = self.lb.process_command("/status", platform="Teams")
        self.assertEqual(reply, "📊 Task Board")

    def test_help_command_returns_text(self):
        reply = self.lb.process_command("/help", platform="MatterMost")
        self.assertIn("ICDEV", reply)

    def test_allowed_sender_enforcement(self):
        reply = self.lb.process_command("/status", sender_id="alice", allowed_sender_id="bob")
        self.assertIsNone(reply)

    def test_empty_text_returns_none(self):
        reply = self.lb.process_command("")
        self.assertIsNone(reply)

    def test_unknown_command(self):
        reply = self.lb.process_command("/unknown_cmd", platform="Skype")
        self.assertIn("Unknown command", reply)


# ── GitHubAdapter ─────────────────────────────────────────────────────────────

class TestGitHubAdapter(unittest.TestCase):

    def _make_adapter(self, secret="test-secret"):
        import os
        os.environ["GITHUB_WEBHOOK_SECRET"] = secret
        os.environ["GITHUB_TOKEN"] = "ghp_test"
        os.environ["GITHUB_OWNER"] = "acme"
        os.environ["GITHUB_REPO"] = "icdev"
        from tools.gateway.adapters.github import GitHubAdapter
        return GitHubAdapter({})

    def _make_signature(self, body: bytes, secret: str) -> str:
        return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def test_verify_signature_valid(self):
        adapter = self._make_adapter("mysecret")
        body = b'{"action":"created"}'
        sig = self._make_signature(body, "mysecret")
        self.assertTrue(adapter.verify_signature(body, sig))

    def test_verify_signature_invalid(self):
        adapter = self._make_adapter("mysecret")
        body = b'{"action":"created"}'
        self.assertFalse(adapter.verify_signature(body, "sha256=deadbeef"))

    def test_verify_signature_no_secret_allows(self):
        import os
        os.environ.pop("GITHUB_WEBHOOK_SECRET", None)
        from tools.gateway.adapters.github import GitHubAdapter
        adapter = GitHubAdapter({})
        self.assertTrue(adapter.verify_signature(b"any", ""))

    def test_parse_webhook_issue_comment(self):
        adapter = self._make_adapter()
        payload = {
            "action": "created",
            "sender": {"id": 42, "login": "dev"},
            "issue": {"number": 7},
            "comment": {"id": 101, "body": "!icdev /build Fix CI", "created_at": "2026-01-01T00:00:00Z"},
        }
        headers = {"X-GitHub-Event": "issue_comment"}
        envelope = adapter.parse_webhook(payload, headers)
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope.channel, "github")
        self.assertEqual(envelope.command, "build")

    def test_parse_webhook_no_icdev_prefix_returns_none(self):
        adapter = self._make_adapter()
        payload = {
            "action": "created",
            "sender": {"id": 42, "login": "dev"},
            "issue": {"number": 7},
            "comment": {"id": 102, "body": "just a comment", "created_at": "2026-01-01T00:00:00Z"},
        }
        headers = {"X-GitHub-Event": "issue_comment"}
        self.assertIsNone(adapter.parse_webhook(payload, headers))

    def test_parse_webhook_wrong_event_returns_none(self):
        adapter = self._make_adapter()
        headers = {"X-GitHub-Event": "push"}
        self.assertIsNone(adapter.parse_webhook({}, headers))


# ── GitLabAdapter ─────────────────────────────────────────────────────────────

class TestGitLabAdapter(unittest.TestCase):

    def _make_adapter(self, secret="gl-secret"):
        import os
        os.environ["GITLAB_WEBHOOK_SECRET"] = secret
        os.environ["GITLAB_TOKEN"] = "glpat_test"
        os.environ["GITLAB_PROJECT_ID"] = "123"
        from tools.gateway.adapters.gitlab import GitLabAdapter
        return GitLabAdapter({})

    def test_verify_signature_valid(self):
        adapter = self._make_adapter("gl-secret")
        self.assertTrue(adapter.verify_signature(b"any", "gl-secret"))

    def test_verify_signature_invalid(self):
        adapter = self._make_adapter("gl-secret")
        self.assertFalse(adapter.verify_signature(b"any", "wrong-secret"))

    def test_parse_webhook_note_event(self):
        adapter = self._make_adapter()
        payload = {
            "object_kind": "note",
            "user": {"id": 9, "username": "ops"},
            "object_attributes": {
                "id": 55, "note": "!icdev /test Run all tests",
                "noteable_type": "Issue", "created_at": "2026-01-01T00:00:00Z",
            },
            "issue": {"iid": 3},
        }
        headers = {"X-Gitlab-Token": "gl-secret"}
        envelope = adapter.parse_webhook(payload, headers)
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope.channel, "gitlab")
        self.assertEqual(envelope.command, "test")

    def test_parse_webhook_not_note_returns_none(self):
        adapter = self._make_adapter()
        self.assertIsNone(adapter.parse_webhook({"object_kind": "push"}, {}))


# ── TeamsAdapter ──────────────────────────────────────────────────────────────

class TestTeamsAdapter(unittest.TestCase):

    def _make_adapter(self):
        import os
        os.environ["TEAMS_APP_ID"] = "teams-app-id"
        os.environ["TEAMS_APP_SECRET"] = "teams-secret"
        os.environ["TEAMS_TENANT_ID"] = "common"
        from tools.gateway.adapters.teams import TeamsAdapter
        return TeamsAdapter({})

    def test_parse_webhook_message(self):
        adapter = self._make_adapter()
        # Use !icdev prefix — Teams users type "!icdev /build Fix..."
        payload = {
            "type": "message",
            "id": "msg-1",
            "text": "!icdev /build Fix database migration",
            "from": {"id": "user-1", "name": "Alice"},
            "conversation": {"id": "conv-123"},
            "serviceUrl": "https://smba.trafficmanager.net/apis",
            "timestamp": "2026-01-01T00:00:00Z",
        }
        headers = {}
        with patch("tools.gateway.adapters.botframework_base._fetch_jwks", return_value=[]):
            envelope = adapter.parse_webhook(payload, headers)
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope.channel, "teams")
        self.assertEqual(envelope.command, "build")

    def test_parse_webhook_non_command_returns_none(self):
        adapter = self._make_adapter()
        payload = {"type": "message", "text": "hello world", "from": {}, "conversation": {}}
        with patch("tools.gateway.adapters.botframework_base._fetch_jwks", return_value=[]):
            result = adapter.parse_webhook(payload, {})
        self.assertIsNone(result)


# ── SkypeAdapter ──────────────────────────────────────────────────────────────

class TestSkypeAdapter(unittest.TestCase):

    def _make_adapter(self):
        import os
        os.environ["SKYPE_APP_ID"] = "skype-app-id"
        os.environ["SKYPE_APP_SECRET"] = "skype-secret"
        from tools.gateway.adapters.skype import SkypeAdapter
        return SkypeAdapter({})

    def test_parse_webhook_writes_to_inbox(self):
        adapter = self._make_adapter()
        payload = {
            "type": "message",
            "id": "act-1",
            "text": "!icdev /status",
            "from": {"id": "user-2"},
            "conversation": {"id": "conv-456"},
            "serviceUrl": "https://smba.trafficmanager.net",
        }
        with patch("tools.gateway.adapters.botframework_base._fetch_jwks", return_value=[]), \
             patch.object(adapter, "_write_to_inbox") as mock_write:
            adapter.parse_webhook(payload, {})
        mock_write.assert_called_once()

    def test_app_id_env_set(self):
        adapter = self._make_adapter()
        self.assertEqual(adapter._app_id_env, "SKYPE_APP_ID")


# ── Notification adapter health checks ────────────────────────────────────────

class TestNotificationAdapterHealth(unittest.TestCase):

    def test_mattermost_unconfigured(self):
        import os
        for k in ("MATTERMOST_TOKEN", "MATTERMOST_URL", "MATTERMOST_CHANNEL_ID"):
            os.environ.pop(k, None)
        from tools.notifications.adapters.mattermost import MattermostNotificationAdapter
        result = MattermostNotificationAdapter().health_check()
        self.assertFalse(result["configured"])

    def test_github_notify_unconfigured(self):
        import os
        for k in ("GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPO", "GITHUB_ISSUE_NUMBER"):
            os.environ.pop(k, None)
        from tools.notifications.adapters.github_notify import GitHubNotificationAdapter
        result = GitHubNotificationAdapter().health_check()
        self.assertFalse(result["configured"])

    def test_gitlab_notify_unconfigured(self):
        import os
        for k in ("GITLAB_TOKEN", "GITLAB_PROJECT_ID", "GITLAB_ISSUE_IID"):
            os.environ.pop(k, None)
        from tools.notifications.adapters.gitlab_notify import GitLabNotificationAdapter
        result = GitLabNotificationAdapter().health_check()
        self.assertFalse(result["configured"])

    def test_skype_notify_unconfigured(self):
        import os
        for k in ("SKYPE_APP_ID", "SKYPE_APP_SECRET", "SKYPE_CONVERSATION_ID"):
            os.environ.pop(k, None)
        from tools.notifications.adapters.skype_notify import SkypeNotificationAdapter
        result = SkypeNotificationAdapter().health_check()
        self.assertFalse(result["configured"])


if __name__ == "__main__":
    unittest.main()
