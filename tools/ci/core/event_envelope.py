# [TEMPLATE: CUI // SP-CTI]
# ICDEV Event Envelope — unified event format for all CI/CD triggers (D132)

"""
Unified event envelope for all CI/CD trigger sources.

Normalizes GitHub webhooks, GitLab webhooks, poll triggers, GitLab task
monitor events, Slack messages, and Mattermost messages into a single
EventEnvelope dataclass before routing. Inspired by OpenClaw's channel
adapter pattern.

Architecture Decision D132: All CI/CD trigger sources normalize into one
format before routing — channel adapters handle platform-specific parsing,
EventRouter handles workflow dispatch.

Usage:
    envelope = EventEnvelope.from_github_webhook(payload, "issues")
    envelope = EventEnvelope.from_slack_event(payload)
    result = router.route(envelope)
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


BOT_IDENTIFIER = "[ICDEV-BOT]"


@dataclass
class EventEnvelope:
    """Unified event format for all CI/CD trigger sources."""

    event_id: str
    source: str             # github_webhook, gitlab_webhook, github_poll, gitlab_poll,
                            # gitlab_task_monitor, slack, mattermost, chat_plugin
    event_type: str         # issue_opened, issue_comment, mr_opened, mr_comment,
                            # chat_message, slash_command
    platform: str           # github, gitlab, slack, mattermost
    session_key: str        # Issue/MR number or channel:thread — one active run per key
    raw_payload: dict
    content: str            # Text body (issue body, comment, chat message)
    author: str
    is_bot: bool
    workflow_command: str    # Extracted: "icdev_plan", "icdev_sdlc", etc. or ""
    run_id: str             # Extracted from content, or ""
    timestamp: str          # ISO 8601
    classification: str = "CUI"
    metadata: dict = field(default_factory=dict)

    @staticmethod
    def _extract_command(text: str) -> tuple:
        """Extract icdev workflow command and run_id from text.

        Uses lightweight regex matching instead of LLM for envelope creation.
        Falls back to extract_icdev_info() for complex cases at routing time.
        """
        import re

        if not text:
            return "", ""

        workflow_command = ""
        run_id = ""

        # Match icdev_ commands
        cmd_match = re.search(
            r"(?:/?)(icdev_\w+)", text, re.IGNORECASE
        )
        if cmd_match:
            workflow_command = cmd_match.group(1).lower()

        # Match run_id patterns: run_id:abc123 or run_id: abc123
        rid_match = re.search(
            r"run_id[:\s]+([a-zA-Z0-9_-]+)", text, re.IGNORECASE
        )
        if rid_match:
            run_id = rid_match.group(1)

        return workflow_command, run_id

    @staticmethod
    def _check_bot(text: str, author: str = "") -> bool:
        """Check if the message is from a bot."""
        if BOT_IDENTIFIER in text:
            return True
        if author and author.lower() in ("icdev-bot", "icdev"):
            return True
        return False

    @staticmethod
    def _make_id() -> str:
        return str(uuid.uuid4())[:12]

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── GitHub Webhook Factory ──────────────────────────────────────────

    @classmethod
    def from_github_webhook(cls, payload: dict, event_type: str) -> Optional["EventEnvelope"]:
        """Create envelope from a GitHub webhook payload.

        Handles: issues.opened, issue_comment.created, pull_request.*
        """
        action = payload.get("action", "")

        # Issue opened
        if event_type == "issues" and action == "opened":
            issue = payload.get("issue", {})
            content = issue.get("body", "") or ""
            author = issue.get("user", {}).get("login", "")
            issue_number = issue.get("number")
            wf, rid = cls._extract_command(content)
            return cls(
                event_id=cls._make_id(),
                source="github_webhook",
                event_type="issue_opened",
                platform="github",
                session_key=str(issue_number) if issue_number else "",
                raw_payload=payload,
                content=content,
                author=author,
                is_bot=cls._check_bot(content, author),
                workflow_command=wf,
                run_id=rid,
                timestamp=cls._now_iso(),
                metadata={
                    "issue_title": issue.get("title", ""),
                    "labels": [lbl.get("name", "") for lbl in issue.get("labels", [])],
                },
            )

        # Issue comment
        if event_type == "issue_comment" and action == "created":
            issue = payload.get("issue", {})
            comment = payload.get("comment", {})
            content = comment.get("body", "") or ""
            author = comment.get("user", {}).get("login", "")
            issue_number = issue.get("number")
            wf, rid = cls._extract_command(content)
            return cls(
                event_id=cls._make_id(),
                source="github_webhook",
                event_type="issue_comment",
                platform="github",
                session_key=str(issue_number) if issue_number else "",
                raw_payload=payload,
                content=content,
                author=author,
                is_bot=cls._check_bot(content, author),
                workflow_command=wf,
                run_id=rid,
                timestamp=cls._now_iso(),
                metadata={
                    "issue_title": issue.get("title", ""),
                    "comment_id": str(comment.get("id", "")),
                },
            )

        # Pull request review comment
        if event_type == "pull_request_review_comment" and action == "created":
            pr = payload.get("pull_request", {})
            comment = payload.get("comment", {})
            content = comment.get("body", "") or ""
            author = comment.get("user", {}).get("login", "")
            pr_number = pr.get("number")
            wf, rid = cls._extract_command(content)
            return cls(
                event_id=cls._make_id(),
                source="github_webhook",
                event_type="mr_comment",
                platform="github",
                session_key=str(pr_number) if pr_number else "",
                raw_payload=payload,
                content=content,
                author=author,
                is_bot=cls._check_bot(content, author),
                workflow_command=wf,
                run_id=rid,
                timestamp=cls._now_iso(),
                metadata={
                    "pr_url": pr.get("html_url", ""),
                    "comment_id": str(comment.get("id", "")),
                    "file_path": comment.get("path", ""),
                    "line": comment.get("line"),
                },
            )

        return None

    # ── GitLab Webhook Factory ──────────────────────────────────────────

    @classmethod
    def from_gitlab_webhook(cls, payload: dict) -> Optional["EventEnvelope"]:
        """Create envelope from a GitLab webhook payload.

        Handles: issue (open), note (comment), merge_request (open/reopen)
        """
        event_type = payload.get("object_kind", "")
        attrs = payload.get("object_attributes", {})

        # Issue opened
        if event_type == "issue":
            action = attrs.get("action", "")
            if action != "open":
                return None
            issue_number = attrs.get("iid")
            content = attrs.get("description", "") or ""
            author = payload.get("user", {}).get("username", "")
            wf, rid = cls._extract_command(content)
            project_id = payload.get("project", {}).get("id")
            return cls(
                event_id=cls._make_id(),
                source="gitlab_webhook",
                event_type="issue_opened",
                platform="gitlab",
                session_key=str(issue_number) if issue_number else "",
                raw_payload=payload,
                content=content,
                author=author,
                is_bot=cls._check_bot(content, author),
                workflow_command=wf,
                run_id=rid,
                timestamp=cls._now_iso(),
                metadata={
                    "issue_title": attrs.get("title", ""),
                    "project_id": str(project_id) if project_id else "",
                    "labels": attrs.get("labels", []),
                },
            )

        # Note (comment) on issue
        if event_type == "note":
            noteable_type = attrs.get("noteable_type", "")
            content = attrs.get("note", "") or ""
            author = payload.get("user", {}).get("username", "")

            if noteable_type == "Issue":
                issue = payload.get("issue", {})
                issue_number = issue.get("iid")
                env_type = "issue_comment"
            elif noteable_type == "MergeRequest":
                mr = payload.get("merge_request", {})
                issue_number = mr.get("iid")
                env_type = "mr_comment"
            else:
                return None

            wf, rid = cls._extract_command(content)
            project_id = payload.get("project", {}).get("id")
            return cls(
                event_id=cls._make_id(),
                source="gitlab_webhook",
                event_type=env_type,
                platform="gitlab",
                session_key=str(issue_number) if issue_number else "",
                raw_payload=payload,
                content=content,
                author=author,
                is_bot=cls._check_bot(content, author),
                workflow_command=wf,
                run_id=rid,
                timestamp=cls._now_iso(),
                metadata={
                    "comment_id": str(attrs.get("id", "")),
                    "project_id": str(project_id) if project_id else "",
                },
            )

        # Merge request opened/reopened
        if event_type == "merge_request":
            action = attrs.get("action", "")
            if action not in ("open", "reopen"):
                return None
            mr_iid = attrs.get("iid")
            content = attrs.get("description", "") or ""
            author = payload.get("user", {}).get("username", "")
            wf, rid = cls._extract_command(content)
            project_id = payload.get("project", {}).get("id")
            return cls(
                event_id=cls._make_id(),
                source="gitlab_webhook",
                event_type="mr_opened",
                platform="gitlab",
                session_key=str(mr_iid) if mr_iid else "",
                raw_payload=payload,
                content=content,
                author=author,
                is_bot=cls._check_bot(content, author),
                workflow_command=wf,
                run_id=rid,
                timestamp=cls._now_iso(),
                metadata={
                    "mr_title": attrs.get("title", ""),
                    "mr_url": attrs.get("url", ""),
                    "project_id": str(project_id) if project_id else "",
                },
            )

        return None

    # ── Poll Trigger Factory ────────────────────────────────────────────

    @classmethod
    def from_poll_issue(
        cls, issue_data: dict, platform: str, latest_comment: str = ""
    ) -> "EventEnvelope":
        """Create envelope from a polled issue."""
        content = latest_comment or issue_data.get("body", "") or ""
        issue_number = issue_data.get("number") or issue_data.get("iid")
        author = (
            issue_data.get("user", {}).get("login", "")
            or issue_data.get("author", {}).get("username", "")
        )
        wf, rid = cls._extract_command(content)
        return cls(
            event_id=cls._make_id(),
            source=f"{platform}_poll",
            event_type="issue_comment" if latest_comment else "issue_opened",
            platform=platform,
            session_key=str(issue_number) if issue_number else "",
            raw_payload=issue_data,
            content=content,
            author=author,
            is_bot=cls._check_bot(content, author),
            workflow_command=wf,
            run_id=rid,
            timestamp=cls._now_iso(),
            metadata={
                "issue_title": issue_data.get("title", ""),
            },
        )

    # ── GitLab Task Monitor Factory ─────────────────────────────────────

    @classmethod
    def from_gitlab_tag(cls, issue_data: dict, icdev_tag: str) -> "EventEnvelope":
        """Create envelope from a GitLab {{icdev: tag}} issue."""
        issue_number = issue_data.get("iid")
        content = issue_data.get("description", "") or ""
        author = issue_data.get("author", {}).get("username", "")

        # Map tag to workflow command
        tag_map = {
            "intake": "icdev_intake", "build": "icdev_build",
            "sdlc": "icdev_sdlc", "comply": "icdev_comply",
            "secure": "icdev_secure", "modernize": "icdev_modernize",
            "deploy": "icdev_deploy", "maintain": "icdev_maintain",
            "test": "icdev_test", "review": "icdev_review",
            "plan": "icdev_plan", "plan_build": "icdev_plan_build",
        }
        workflow_command = tag_map.get(icdev_tag.lower(), "")

        return cls(
            event_id=cls._make_id(),
            source="gitlab_task_monitor",
            event_type="slash_command",
            platform="gitlab",
            session_key=str(issue_number) if issue_number else "",
            raw_payload=issue_data,
            content=content,
            author=author,
            is_bot=False,
            workflow_command=workflow_command,
            run_id="",
            timestamp=cls._now_iso(),
            metadata={
                "issue_title": issue_data.get("title", ""),
                "icdev_tag": icdev_tag,
            },
        )

    # ── Slack Factory ───────────────────────────────────────────────────

    @classmethod
    def from_slack_event(cls, payload: dict) -> Optional["EventEnvelope"]:
        """Create envelope from a Slack Events API payload.

        Handles: message events and app_mention events.
        """
        event = payload.get("event", {})
        event_subtype = event.get("type", "")

        if event_subtype not in ("message", "app_mention"):
            return None

        # Ignore bot messages
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return None

        content = event.get("text", "") or ""
        author = event.get("user", "")
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts", "") or event.get("ts", "")
        session_key = f"{channel}:{thread_ts}" if thread_ts else channel

        wf, rid = cls._extract_command(content)

        return cls(
            event_id=cls._make_id(),
            source="slack",
            event_type="chat_message",
            platform="slack",
            session_key=session_key,
            raw_payload=payload,
            content=content,
            author=author,
            is_bot=cls._check_bot(content),
            workflow_command=wf,
            run_id=rid,
            timestamp=cls._now_iso(),
            metadata={
                "channel_id": channel,
                "thread_ts": thread_ts,
                "team_id": payload.get("team_id", ""),
            },
        )

    # ── Mattermost Factory ──────────────────────────────────────────────

    @classmethod
    def from_mattermost_event(cls, payload: dict) -> Optional["EventEnvelope"]:
        """Create envelope from a Mattermost outgoing webhook payload."""
        content = payload.get("text", "") or ""
        author = payload.get("user_name", "") or payload.get("user_id", "")
        channel = payload.get("channel_id", "")
        post_id = payload.get("post_id", "")
        root_id = payload.get("root_id", "") or post_id
        session_key = f"{channel}:{root_id}" if root_id else channel

        wf, rid = cls._extract_command(content)

        return cls(
            event_id=cls._make_id(),
            source="mattermost",
            event_type="chat_message",
            platform="mattermost",
            session_key=session_key,
            raw_payload=payload,
            content=content,
            author=author,
            is_bot=cls._check_bot(content, author),
            workflow_command=wf,
            run_id=rid,
            timestamp=cls._now_iso(),
            metadata={
                "channel_id": channel,
                "post_id": post_id,
                "root_id": root_id,
                "channel_name": payload.get("channel_name", ""),
            },
        )

    # ── Generic Chat Plugin Factory ─────────────────────────────────────

    @classmethod
    def from_chat_plugin(
        cls, source: str, channel_id: str, text: str, author: str,
        thread_id: str = "", metadata: dict = None,
    ) -> "EventEnvelope":
        """Create envelope from a marketplace chat connector plugin."""
        session_key = f"{channel_id}:{thread_id}" if thread_id else channel_id
        wf, rid = cls._extract_command(text)

        return cls(
            event_id=cls._make_id(),
            source=source,
            event_type="chat_message",
            platform=source,
            session_key=session_key,
            raw_payload={},
            content=text,
            author=author,
            is_bot=cls._check_bot(text, author),
            workflow_command=wf,
            run_id=rid,
            timestamp=cls._now_iso(),
            metadata=metadata or {},
        )

    def to_dict(self) -> dict:
        """Serialize envelope to dict for DB storage / JSON transport."""
        return {
            "event_id": self.event_id,
            "source": self.source,
            "event_type": self.event_type,
            "platform": self.platform,
            "session_key": self.session_key,
            "content": self.content,
            "author": self.author,
            "is_bot": self.is_bot,
            "workflow_command": self.workflow_command,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            "classification": self.classification,
            "metadata": self.metadata,
        }
