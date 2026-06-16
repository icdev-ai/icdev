#!/usr/bin/env python3
# CUI // SP-CTI
"""GitHub gateway adapter — receives webhook events from GitHub and routes
!icdev commands to the ICDEV™ security chain.

Inbound: POST /gateway/github (issue_comment or pull_request_review_comment events)
Outbound: GitHubConnector.write() → fallback to direct REST API call

Commands are embedded in issue/PR comment bodies with the !icdev prefix:
    !icdev /build Fix the login timeout
    !icdev /status
    !icdev /test

Signature verification: HMAC-SHA256 of raw body vs X-Hub-Signature-256 header.

Requires environment variables:
    GITHUB_TOKEN           — Personal access token (repo, issues scopes)
    GITHUB_WEBHOOK_SECRET  — Shared secret configured on the GitHub webhook
    GITHUB_OWNER           — Repository owner
    GITHUB_REPO            — Repository name

Decision D133: Channel adapters follow ABC pattern.
Decision D143: GitHub/GitLab use !icdev comment prefix (no GitHub App required).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.gateway.adapters.base import BaseChannelAdapter  # noqa: E402
from tools.gateway.event_envelope import CommandEnvelope, parse_command_text  # noqa: E402

try:
    from tools.logging.icdev_logger import get_logger
    logger = get_logger("icdev.gateway.adapters.github")
except Exception:
    logger = logging.getLogger("icdev.gateway.adapters.github")

_ICDEV_PREFIX = "!icdev "


class GitHubAdapter(BaseChannelAdapter):
    """Gateway adapter for GitHub issue/PR comment commands."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__("github", config)
        self.webhook_secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
        self.token = os.environ.get("GITHUB_TOKEN", "")
        self.owner = os.environ.get("GITHUB_OWNER", "")
        self.repo = os.environ.get("GITHUB_REPO", "")
        self._last_issue_number: int = 0

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Verify X-Hub-Signature-256: sha256=HMAC(WEBHOOK_SECRET, body)."""
        if not self.webhook_secret:
            return True  # unconfigured = no verification (warn in prod)

        if not signature:
            return False

        expected = "sha256=" + hmac.new(
            self.webhook_secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_webhook(self, request_data: Dict[str, Any], headers: Dict[str, str]) -> Optional[CommandEnvelope]:
        """Parse GitHub issue_comment or pull_request_review_comment event.

        Only processes comments whose body starts with !icdev (case-insensitive).
        Strips the prefix before parsing the ICDEV command.
        """
        event_type = headers.get("X-GitHub-Event", headers.get("x-github-event", ""))
        action = request_data.get("action", "")

        # Only handle comment creation events
        if event_type not in ("issue_comment", "pull_request_review_comment"):
            return None
        if action != "created":
            return None

        comment = request_data.get("comment", {})
        body = (comment.get("body") or "").strip()
        if not body.lower().startswith(_ICDEV_PREFIX.lower()):
            return None

        # Strip prefix, parse as ICDEV command
        command_text = body[len(_ICDEV_PREFIX):].strip()
        command, args = parse_command_text(command_text)
        if not command:
            return None

        sender = request_data.get("sender", {})
        issue = request_data.get("issue", request_data.get("pull_request", {}))
        self._last_issue_number = issue.get("number", 0)

        return CommandEnvelope(
            channel="github",
            channel_user_id=str(sender.get("id", "")),
            channel_user_name=sender.get("login", ""),
            channel_message_id=str(comment.get("id", "")),
            channel_thread_id=str(self._last_issue_number),
            raw_text=body,
            command=command,
            args=args,
            project_id=args.get("project_id", ""),
            timestamp=comment.get("created_at", datetime.now(timezone.utc).isoformat()),
            signature=headers.get("X-Hub-Signature-256", ""),
        )

    def send_message(self, channel_user_id: str, text: str, thread_id: str = "") -> bool:
        """Post reply as a GitHub issue comment.

        thread_id = issue number (str). Falls back to last parsed issue if empty.
        """
        issue_number = int(thread_id) if thread_id and thread_id.isdigit() else self._last_issue_number
        if not issue_number:
            logger.error("GitHub: No issue number to reply to")
            return False

        # Delegate to DataBridge connector
        try:
            from tools.databridge.connectors.github_connector import GitHubConnector
            from tools.databridge.connector import ConnectorRequest
            c = GitHubConnector()
            if c.connect({}):
                resp = c.write(
                    ConnectorRequest(table_name="send_comment"),
                    {"body": text, "issue_number": issue_number},
                )
                if resp.status in ("ok", "success"):
                    return True
        except Exception as exc:
            logger.debug("GitHubConnector.write failed, falling back: %s", exc)

        # Fallback: direct REST API call
        if not self.token or not self.owner or not self.repo:
            return False
        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/issues/{issue_number}/comments"
        payload = json.dumps({"body": text}).encode("utf-8")
        try:
            req = Request(
                url, data=payload,
                headers={
                    "Authorization": f"token {self.token}",
                    "Accept": "application/vnd.github+json",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:  # nosec B310 -- GitHub API only
                return resp.status in (200, 201)
        except (URLError, Exception) as exc:
            logger.error("GitHub send failed: %s", exc)
            return False
