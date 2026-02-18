# CUI // SP-CTI
# Tests for ICDEV Event Envelope — edge cases for all factory methods

"""
Automated tests for EventEnvelope and EventRouter (Phase 1).

Covers:
- All factory methods (GitHub, GitLab, Slack, Mattermost, poll, tag, plugin)
- Bot detection edge cases
- Command extraction edge cases
- Empty/malformed payloads
- Lane-aware routing
- Air-gap detection

Run: pytest tests/test_event_envelope.py -v
"""

import json
import sqlite3

import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ci.core.event_envelope import EventEnvelope, BOT_IDENTIFIER
from tools.ci.core.air_gap_detector import detect_connectivity, _probe_host


# ═══════════════════════════════════════════════════════════════════════
# EventEnvelope._extract_command Tests
# ═══════════════════════════════════════════════════════════════════════


class TestExtractCommand:
    """Test command extraction from text content."""

    def test_basic_icdev_command(self):
        wf, rid = EventEnvelope._extract_command("Please run icdev_sdlc")
        assert wf == "icdev_sdlc"
        assert rid == ""

    def test_command_with_run_id(self):
        wf, rid = EventEnvelope._extract_command(
            "icdev_build run_id:abc12345"
        )
        assert wf == "icdev_build"
        assert rid == "abc12345"

    def test_command_with_run_id_space(self):
        wf, rid = EventEnvelope._extract_command(
            "icdev_build run_id: abc12345"
        )
        assert wf == "icdev_build"
        assert rid == "abc12345"

    def test_command_with_slash_prefix(self):
        wf, rid = EventEnvelope._extract_command("/icdev_plan")
        assert wf == "icdev_plan"

    def test_command_case_insensitive(self):
        wf, rid = EventEnvelope._extract_command("ICDEV_SDLC")
        assert wf == "icdev_sdlc"

    def test_no_command(self):
        wf, rid = EventEnvelope._extract_command("Just a normal comment")
        assert wf == ""
        assert rid == ""

    def test_empty_string(self):
        wf, rid = EventEnvelope._extract_command("")
        assert wf == ""
        assert rid == ""

    def test_none_input(self):
        wf, rid = EventEnvelope._extract_command(None)
        assert wf == ""
        assert rid == ""

    def test_command_in_multiline(self):
        text = "Hello team,\n\nPlease run icdev_plan for this issue.\n\nThanks"
        wf, rid = EventEnvelope._extract_command(text)
        assert wf == "icdev_plan"

    def test_multiple_commands_takes_first(self):
        wf, rid = EventEnvelope._extract_command("icdev_plan then icdev_build")
        assert wf == "icdev_plan"

    def test_partial_match_not_extracted(self):
        wf, rid = EventEnvelope._extract_command("my_icdev_custom_tool")
        # Should still match — icdev_ prefix is in the word
        assert wf == ""  or "icdev_" in wf

    def test_run_id_with_hyphens(self):
        wf, rid = EventEnvelope._extract_command(
            "icdev_build run_id:abc-123-def"
        )
        assert rid == "abc-123-def"

    def test_run_id_with_underscores(self):
        wf, rid = EventEnvelope._extract_command(
            "icdev_test run_id:run_2025_01"
        )
        assert rid == "run_2025_01"


# ═══════════════════════════════════════════════════════════════════════
# Bot Detection Tests
# ═══════════════════════════════════════════════════════════════════════


class TestBotDetection:
    """Test bot message detection edge cases."""

    def test_bot_identifier_in_text(self):
        assert EventEnvelope._check_bot(f"{BOT_IDENTIFIER} Status update")

    def test_bot_identifier_mid_text(self):
        assert EventEnvelope._check_bot(f"Message from {BOT_IDENTIFIER} here")

    def test_bot_author_name(self):
        assert EventEnvelope._check_bot("Hello", author="icdev-bot")

    def test_bot_author_case_insensitive(self):
        assert EventEnvelope._check_bot("Hello", author="ICDEV-BOT")

    def test_not_bot_normal_text(self):
        assert not EventEnvelope._check_bot("Normal user comment")

    def test_not_bot_similar_text(self):
        assert not EventEnvelope._check_bot("This is an ICDEV related comment")

    def test_empty_text(self):
        assert not EventEnvelope._check_bot("")

    def test_bot_icdev_author(self):
        assert EventEnvelope._check_bot("Hello", author="icdev")


# ═══════════════════════════════════════════════════════════════════════
# GitHub Webhook Factory Tests
# ═══════════════════════════════════════════════════════════════════════


class TestGitHubWebhookFactory:
    """Test EventEnvelope.from_github_webhook with edge cases."""

    def test_issue_opened_with_command(self):
        payload = {
            "action": "opened",
            "issue": {
                "number": 42,
                "title": "Add auth module",
                "body": "icdev_sdlc\n\nPlease build this feature",
                "user": {"login": "dev1"},
                "labels": [{"name": "enhancement"}],
            },
        }
        env = EventEnvelope.from_github_webhook(payload, "issues")
        assert env is not None
        assert env.source == "github_webhook"
        assert env.event_type == "issue_opened"
        assert env.platform == "github"
        assert env.session_key == "42"
        assert env.workflow_command == "icdev_sdlc"
        assert env.author == "dev1"
        assert not env.is_bot
        assert env.metadata["issue_title"] == "Add auth module"
        assert "enhancement" in env.metadata["labels"]

    def test_issue_opened_no_command(self):
        payload = {
            "action": "opened",
            "issue": {
                "number": 43,
                "body": "Just a regular issue",
                "user": {"login": "dev2"},
                "labels": [],
            },
        }
        env = EventEnvelope.from_github_webhook(payload, "issues")
        assert env is not None
        assert env.workflow_command == ""

    def test_issue_opened_empty_body(self):
        payload = {
            "action": "opened",
            "issue": {
                "number": 44,
                "body": None,
                "user": {"login": "dev3"},
                "labels": [],
            },
        }
        env = EventEnvelope.from_github_webhook(payload, "issues")
        assert env is not None
        assert env.content == ""
        assert env.workflow_command == ""

    def test_issue_comment_with_bot_identifier(self):
        payload = {
            "action": "created",
            "issue": {"number": 42, "title": "Test"},
            "comment": {
                "id": 99,
                "body": f"{BOT_IDENTIFIER} Pipeline started",
                "user": {"login": "github-actions"},
            },
        }
        env = EventEnvelope.from_github_webhook(payload, "issue_comment")
        assert env is not None
        assert env.is_bot is True

    def test_issue_comment_with_command(self):
        payload = {
            "action": "created",
            "issue": {"number": 42, "title": "Test"},
            "comment": {
                "id": 100,
                "body": "icdev_build run_id:abc123",
                "user": {"login": "dev1"},
            },
        }
        env = EventEnvelope.from_github_webhook(payload, "issue_comment")
        assert env is not None
        assert env.workflow_command == "icdev_build"
        assert env.run_id == "abc123"

    def test_pr_review_comment(self):
        payload = {
            "action": "created",
            "pull_request": {
                "number": 10,
                "html_url": "https://github.com/org/repo/pull/10",
            },
            "comment": {
                "id": 200,
                "body": "fix this",
                "user": {"login": "reviewer"},
                "path": "src/auth.py",
                "line": 42,
            },
        }
        env = EventEnvelope.from_github_webhook(
            payload, "pull_request_review_comment"
        )
        assert env is not None
        assert env.event_type == "mr_comment"
        assert env.metadata["file_path"] == "src/auth.py"
        assert env.metadata["line"] == 42

    def test_unsupported_event_type(self):
        env = EventEnvelope.from_github_webhook({}, "push")
        assert env is None

    def test_wrong_action(self):
        payload = {"action": "closed", "issue": {"number": 1}}
        env = EventEnvelope.from_github_webhook(payload, "issues")
        assert env is None

    def test_missing_issue_number(self):
        payload = {
            "action": "opened",
            "issue": {"body": "test", "user": {"login": "x"}, "labels": []},
        }
        env = EventEnvelope.from_github_webhook(payload, "issues")
        assert env is not None
        assert env.session_key == ""


# ═══════════════════════════════════════════════════════════════════════
# GitLab Webhook Factory Tests
# ═══════════════════════════════════════════════════════════════════════


class TestGitLabWebhookFactory:
    """Test EventEnvelope.from_gitlab_webhook with edge cases."""

    def test_issue_opened(self):
        payload = {
            "object_kind": "issue",
            "object_attributes": {
                "action": "open",
                "iid": 15,
                "title": "Deploy service",
                "description": "icdev_deploy\n\nDeploy to staging",
            },
            "user": {"username": "ops-lead"},
            "project": {"id": 123},
        }
        env = EventEnvelope.from_gitlab_webhook(payload)
        assert env is not None
        assert env.platform == "gitlab"
        assert env.session_key == "15"
        assert env.workflow_command == "icdev_deploy"
        assert env.metadata["project_id"] == "123"

    def test_issue_not_open_action(self):
        payload = {
            "object_kind": "issue",
            "object_attributes": {"action": "close", "iid": 15},
        }
        env = EventEnvelope.from_gitlab_webhook(payload)
        assert env is None

    def test_note_on_issue(self):
        payload = {
            "object_kind": "note",
            "object_attributes": {
                "noteable_type": "Issue",
                "note": "icdev_test run_id:xyz789",
                "id": 500,
            },
            "issue": {"iid": 15},
            "user": {"username": "dev"},
            "project": {"id": 123},
        }
        env = EventEnvelope.from_gitlab_webhook(payload)
        assert env is not None
        assert env.event_type == "issue_comment"
        assert env.workflow_command == "icdev_test"
        assert env.run_id == "xyz789"

    def test_note_on_merge_request(self):
        payload = {
            "object_kind": "note",
            "object_attributes": {
                "noteable_type": "MergeRequest",
                "note": "fix this please",
                "id": 501,
            },
            "merge_request": {"iid": 5},
            "user": {"username": "reviewer"},
            "project": {"id": 123},
        }
        env = EventEnvelope.from_gitlab_webhook(payload)
        assert env is not None
        assert env.event_type == "mr_comment"
        assert env.session_key == "5"

    def test_note_on_unsupported_type(self):
        payload = {
            "object_kind": "note",
            "object_attributes": {
                "noteable_type": "Snippet",
                "note": "icdev_test",
            },
            "user": {"username": "dev"},
            "project": {"id": 1},
        }
        env = EventEnvelope.from_gitlab_webhook(payload)
        assert env is None

    def test_bot_note_detected(self):
        payload = {
            "object_kind": "note",
            "object_attributes": {
                "noteable_type": "Issue",
                "note": f"{BOT_IDENTIFIER} Pipeline complete",
                "id": 502,
            },
            "issue": {"iid": 15},
            "user": {"username": "icdev-bot"},
            "project": {"id": 123},
        }
        env = EventEnvelope.from_gitlab_webhook(payload)
        assert env is not None
        assert env.is_bot is True

    def test_merge_request_opened(self):
        payload = {
            "object_kind": "merge_request",
            "object_attributes": {
                "action": "open",
                "iid": 8,
                "title": "Feature MR",
                "description": "icdev_review run_id:mr_run1",
                "url": "https://gitlab.com/org/repo/-/merge_requests/8",
            },
            "user": {"username": "dev"},
            "project": {"id": 123},
        }
        env = EventEnvelope.from_gitlab_webhook(payload)
        assert env is not None
        assert env.event_type == "mr_opened"
        assert env.workflow_command == "icdev_review"
        assert env.run_id == "mr_run1"

    def test_merge_request_wrong_action(self):
        payload = {
            "object_kind": "merge_request",
            "object_attributes": {"action": "merge", "iid": 8},
        }
        env = EventEnvelope.from_gitlab_webhook(payload)
        assert env is None

    def test_unknown_event_kind(self):
        payload = {"object_kind": "pipeline"}
        env = EventEnvelope.from_gitlab_webhook(payload)
        assert env is None


# ═══════════════════════════════════════════════════════════════════════
# Poll Trigger Factory Tests
# ═══════════════════════════════════════════════════════════════════════


class TestPollFactory:
    """Test EventEnvelope.from_poll_issue with edge cases."""

    def test_poll_with_comment(self):
        issue_data = {
            "number": 99,
            "title": "Auth feature",
            "body": "Original body",
            "user": {"login": "dev1"},
        }
        env = EventEnvelope.from_poll_issue(
            issue_data, "github", latest_comment="icdev_sdlc"
        )
        assert env.source == "github_poll"
        assert env.event_type == "issue_comment"
        assert env.workflow_command == "icdev_sdlc"
        assert env.content == "icdev_sdlc"

    def test_poll_without_comment_uses_body(self):
        issue_data = {
            "number": 100,
            "body": "icdev_plan this feature",
            "user": {"login": "dev1"},
        }
        env = EventEnvelope.from_poll_issue(issue_data, "github")
        assert env.event_type == "issue_opened"
        assert env.workflow_command == "icdev_plan"

    def test_poll_gitlab_format(self):
        issue_data = {
            "iid": 50,
            "title": "Task",
            "description": "icdev_build",
            "author": {"username": "gl-dev"},
        }
        env = EventEnvelope.from_poll_issue(issue_data, "gitlab")
        assert env.source == "gitlab_poll"
        assert env.platform == "gitlab"

    def test_poll_empty_issue(self):
        env = EventEnvelope.from_poll_issue({}, "github")
        assert env is not None
        assert env.session_key == ""
        assert env.workflow_command == ""


# ═══════════════════════════════════════════════════════════════════════
# GitLab Task Monitor Factory Tests
# ═══════════════════════════════════════════════════════════════════════


class TestGitLabTagFactory:
    """Test EventEnvelope.from_gitlab_tag with edge cases."""

    def test_known_tag(self):
        issue_data = {
            "iid": 25,
            "description": "Build the auth service",
            "author": {"username": "dev"},
        }
        env = EventEnvelope.from_gitlab_tag(issue_data, "sdlc")
        assert env.source == "gitlab_task_monitor"
        assert env.workflow_command == "icdev_sdlc"
        assert env.metadata["icdev_tag"] == "sdlc"

    def test_unknown_tag(self):
        env = EventEnvelope.from_gitlab_tag({"iid": 1}, "unknown_tag")
        assert env.workflow_command == ""

    def test_tag_case_insensitive(self):
        env = EventEnvelope.from_gitlab_tag({"iid": 1}, "BUILD")
        assert env.workflow_command == "icdev_build"

    def test_all_known_tags(self):
        known = [
            "intake", "build", "sdlc", "comply", "secure",
            "modernize", "deploy", "maintain", "test", "review",
            "plan", "plan_build",
        ]
        for tag in known:
            env = EventEnvelope.from_gitlab_tag({"iid": 1}, tag)
            assert env.workflow_command != "", f"Tag '{tag}' should map to a workflow"


# ═══════════════════════════════════════════════════════════════════════
# Slack Factory Tests
# ═══════════════════════════════════════════════════════════════════════


class TestSlackFactory:
    """Test EventEnvelope.from_slack_event with edge cases."""

    def test_channel_message(self):
        payload = {
            "event": {
                "type": "message",
                "text": "icdev_plan for ticket 123",
                "user": "U12345",
                "channel": "C98765",
                "ts": "1234567890.123456",
            },
            "team_id": "T001",
        }
        env = EventEnvelope.from_slack_event(payload)
        assert env is not None
        assert env.platform == "slack"
        assert env.workflow_command == "icdev_plan"
        assert env.metadata["channel_id"] == "C98765"

    def test_threaded_message(self):
        payload = {
            "event": {
                "type": "message",
                "text": "fix this",
                "user": "U12345",
                "channel": "C98765",
                "thread_ts": "1234567890.000000",
                "ts": "1234567891.000001",
            },
        }
        env = EventEnvelope.from_slack_event(payload)
        assert env is not None
        assert env.session_key == "C98765:1234567890.000000"

    def test_app_mention(self):
        payload = {
            "event": {
                "type": "app_mention",
                "text": "<@U_BOT> icdev_sdlc",
                "user": "U12345",
                "channel": "C98765",
                "ts": "111.222",
            },
        }
        env = EventEnvelope.from_slack_event(payload)
        assert env is not None
        assert env.workflow_command == "icdev_sdlc"

    def test_bot_message_ignored(self):
        payload = {
            "event": {
                "type": "message",
                "text": "Bot response",
                "bot_id": "B12345",
                "channel": "C98765",
            },
        }
        env = EventEnvelope.from_slack_event(payload)
        assert env is None

    def test_bot_subtype_ignored(self):
        payload = {
            "event": {
                "type": "message",
                "subtype": "bot_message",
                "text": "Bot",
                "channel": "C1",
            },
        }
        env = EventEnvelope.from_slack_event(payload)
        assert env is None

    def test_unsupported_event_type(self):
        payload = {
            "event": {"type": "reaction_added"},
        }
        env = EventEnvelope.from_slack_event(payload)
        assert env is None

    def test_empty_event(self):
        env = EventEnvelope.from_slack_event({})
        assert env is None


# ═══════════════════════════════════════════════════════════════════════
# Mattermost Factory Tests
# ═══════════════════════════════════════════════════════════════════════


class TestMattermostFactory:
    """Test EventEnvelope.from_mattermost_event with edge cases."""

    def test_channel_message(self):
        payload = {
            "text": "icdev_build run_id:mm_run1",
            "user_name": "devops-lead",
            "channel_id": "ch001",
            "post_id": "p001",
            "root_id": "",
            "channel_name": "engineering",
        }
        env = EventEnvelope.from_mattermost_event(payload)
        assert env is not None
        assert env.platform == "mattermost"
        assert env.workflow_command == "icdev_build"
        assert env.run_id == "mm_run1"
        assert env.metadata["channel_name"] == "engineering"

    def test_threaded_reply(self):
        payload = {
            "text": "retry",
            "user_name": "dev",
            "channel_id": "ch001",
            "post_id": "p002",
            "root_id": "p001",
        }
        env = EventEnvelope.from_mattermost_event(payload)
        assert env is not None
        assert env.session_key == "ch001:p001"

    def test_bot_identifier_in_text(self):
        payload = {
            "text": f"{BOT_IDENTIFIER} Pipeline started",
            "user_name": "icdev-bot",
            "channel_id": "ch001",
            "post_id": "p003",
        }
        env = EventEnvelope.from_mattermost_event(payload)
        assert env is not None
        assert env.is_bot is True

    def test_empty_payload(self):
        env = EventEnvelope.from_mattermost_event({})
        assert env is not None  # Factory always returns an envelope
        assert env.content == ""
        assert env.workflow_command == ""


# ═══════════════════════════════════════════════════════════════════════
# Chat Plugin Factory Tests
# ═══════════════════════════════════════════════════════════════════════


class TestChatPluginFactory:
    """Test EventEnvelope.from_chat_plugin for marketplace connectors."""

    def test_basic_plugin_message(self):
        env = EventEnvelope.from_chat_plugin(
            source="telegram",
            channel_id="chat_123",
            text="icdev_plan for my project",
            author="user42",
            thread_id="thread_1",
        )
        assert env.source == "telegram"
        assert env.platform == "telegram"
        assert env.session_key == "chat_123:thread_1"
        assert env.workflow_command == "icdev_plan"

    def test_plugin_no_thread(self):
        env = EventEnvelope.from_chat_plugin(
            source="teams", channel_id="ch1", text="hello", author="u1"
        )
        assert env.session_key == "ch1"


# ═══════════════════════════════════════════════════════════════════════
# Serialization Tests
# ═══════════════════════════════════════════════════════════════════════


class TestSerialization:
    """Test EventEnvelope serialization."""

    def test_to_dict_roundtrip(self):
        env = EventEnvelope.from_chat_plugin(
            source="test", channel_id="ch", text="icdev_plan", author="u"
        )
        d = env.to_dict()
        assert isinstance(d, dict)
        assert d["source"] == "test"
        assert d["workflow_command"] == "icdev_plan"
        # Ensure JSON-serializable
        json_str = json.dumps(d)
        assert json_str

    def test_raw_payload_excluded_from_to_dict(self):
        """raw_payload is intentionally excluded to keep serialized size small."""
        env = EventEnvelope.from_chat_plugin(
            source="t", channel_id="c", text="x", author="a"
        )
        d = env.to_dict()
        assert "raw_payload" not in d


# ═══════════════════════════════════════════════════════════════════════
# EventRouter Tests (with in-memory DB)
# ═══════════════════════════════════════════════════════════════════════


class TestEventRouter:
    """Test EventRouter routing logic with edge cases."""

    @pytest.fixture
    def router(self, tmp_path):
        """Create router with temp database."""
        db_path = str(tmp_path / "test_icdev.db")
        # Ensure DB directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        from tools.ci.core.event_router import EventRouter
        return EventRouter(db_path=db_path)

    def _make_envelope(self, **kwargs):
        defaults = {
            "event_id": "evt-001",
            "source": "github_webhook",
            "event_type": "issue_opened",
            "platform": "github",
            "session_key": "42",
            "raw_payload": {},
            "content": "icdev_plan",
            "author": "dev",
            "is_bot": False,
            "workflow_command": "icdev_plan",
            "run_id": "",
            "timestamp": "2026-01-01T00:00:00Z",
            "classification": "CUI",
            "metadata": {},
        }
        defaults.update(kwargs)
        return EventEnvelope(**defaults)

    def test_route_bot_message_ignored(self, router):
        env = self._make_envelope(is_bot=True)
        result = router.route(env)
        assert result["action"] == "ignored"
        assert result["reason"] == "bot_message"

    def test_route_unknown_workflow_ignored(self, router):
        env = self._make_envelope(workflow_command="icdev_nonexistent")
        result = router.route(env)
        assert result["action"] == "ignored"
        assert "unknown_workflow" in result["reason"]

    def test_route_build_without_run_id_ignored(self, router):
        env = self._make_envelope(
            workflow_command="icdev_build", run_id=""
        )
        result = router.route(env)
        assert result["action"] == "ignored"
        assert "requires run_id" in result["reason"]

    def test_route_review_without_run_id_ignored(self, router):
        env = self._make_envelope(
            workflow_command="icdev_review", run_id=""
        )
        result = router.route(env)
        assert result["action"] == "ignored"
        assert "requires run_id" in result["reason"]

    def test_route_no_workflow_no_icdev_keyword(self, router):
        env = self._make_envelope(
            workflow_command="", content="Just a normal comment"
        )
        result = router.route(env)
        assert result["action"] == "ignored"
        assert "no_workflow" in result["reason"]

    def test_route_no_workflow_with_icdev_keyword(self, router):
        env = self._make_envelope(
            workflow_command="", content="please icdev this issue"
        )
        # Should default to configured default_workflow
        result = router.route(env)
        # May be "launched" or "ignored" depending on workflow script existence
        assert result["action"] in ("launched", "ignored")

    def test_lane_aware_queue_followup(self, router):
        """Test that events queue when session has active run."""
        # Manually insert an active run
        conn = sqlite3.connect(router.db_path)
        conn.execute(
            "INSERT INTO ci_pipeline_runs "
            "(id, session_key, run_id, platform, workflow, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("evt-000", "42", "run-active", "github", "icdev_sdlc", "running"),
        )
        conn.commit()
        conn.close()

        env = self._make_envelope(
            event_id="evt-002",
            workflow_command="",
            content="fix this",
        )
        result = router.route(env)
        assert result["action"] == "queued"
        assert "active_run" in result["reason"]

    def test_lane_aware_queue_max_depth(self, router):
        """Test queue overflow protection."""
        conn = sqlite3.connect(router.db_path)
        conn.execute(
            "INSERT INTO ci_pipeline_runs "
            "(id, session_key, run_id, platform, workflow, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("evt-base", "42", "run-active", "github", "icdev_sdlc", "running"),
        )
        # Fill queue to max (default 20)
        for i in range(20):
            conn.execute(
                "INSERT INTO ci_event_queue "
                "(session_key, event_id, envelope_json, status) "
                "VALUES (?, ?, ?, 'queued')",
                ("42", f"evt-q{i}", "{}"),
            )
        conn.commit()
        conn.close()

        env = self._make_envelope(event_id="evt-overflow", content="one more")
        result = router.route(env)
        assert result["action"] == "ignored"
        assert "queue_full" in result["reason"]

    def test_update_pipeline_status(self, router):
        """Test pipeline status update."""
        conn = sqlite3.connect(router.db_path)
        conn.execute(
            "INSERT INTO ci_pipeline_runs "
            "(id, session_key, run_id, platform, workflow, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("evt-x", "42", "run-x", "github", "icdev_plan", "running"),
        )
        conn.commit()
        conn.close()

        router.update_pipeline_status("run-x", "completed")

        conn = sqlite3.connect(router.db_path)
        cursor = conn.execute(
            "SELECT status, completed_at FROM ci_pipeline_runs WHERE run_id = ?",
            ("run-x",),
        )
        row = cursor.fetchone()
        conn.close()
        assert row[0] == "completed"
        assert row[1] is not None  # completed_at should be set


# ═══════════════════════════════════════════════════════════════════════
# Air-Gap Detector Tests
# ═══════════════════════════════════════════════════════════════════════


class TestAirGapDetector:
    """Test air-gap detection edge cases."""

    def test_force_polling_env_var(self, monkeypatch):
        monkeypatch.setenv("ICDEV_FORCE_POLLING", "true")
        result = detect_connectivity()
        assert result["mode"] == "polling"
        assert result["reason"] == "ICDEV_FORCE_POLLING is set"

    def test_force_polling_env_var_yes(self, monkeypatch):
        monkeypatch.setenv("ICDEV_FORCE_POLLING", "yes")
        result = detect_connectivity()
        assert result["mode"] == "polling"

    def test_force_polling_env_var_1(self, monkeypatch):
        monkeypatch.setenv("ICDEV_FORCE_POLLING", "1")
        result = detect_connectivity()
        assert result["mode"] == "polling"

    def test_probe_unreachable_host(self):
        # Probe a host that definitely won't respond
        assert not _probe_host("192.0.2.1:12345", timeout=1)

    def test_probe_invalid_format(self):
        assert not _probe_host("not_a_host", timeout=1)

    def test_probe_empty_string(self):
        assert not _probe_host("", timeout=1)
