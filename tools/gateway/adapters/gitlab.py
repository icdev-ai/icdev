#!/usr/bin/env python3
# CUI // SP-CTI
"""GitLab gateway adapter — receives webhook events from GitLab and routes
!icdev commands to the ICDEV™ security chain.

Supports self-hosted GitLab instances (set GITLAB_URL for on-prem/air-gap).

Inbound: POST /gateway/gitlab (Note events for issues and merge requests)
Outbound: GitLabConnector.write() → fallback to direct REST API call

Commands are embedded in note (comment) bodies with the !icdev prefix:
    !icdev /build Fix the CI pipeline
    !icdev /status
    !icdev /test

Signature verification: plaintext compare of X-Gitlab-Token header.

Requires environment variables:
    GITLAB_TOKEN           — Personal access token (api scope)
    GITLAB_WEBHOOK_SECRET  — Shared secret token on the GitLab webhook
    GITLAB_URL             — Base URL (default: https://gitlab.com)
    GITLAB_PROJECT_ID      — Target project ID

Decision D133: Channel adapters follow ABC pattern.
Decision D143: GitHub/GitLab use !icdev comment prefix (no App registration required).
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.gateway.adapters.base import BaseChannelAdapter  # noqa: E402
from tools.gateway.event_envelope import CommandEnvelope, parse_command_text  # noqa: E402

try:
    from tools.logging.icdev_logger import get_logger
    logger = get_logger("icdev.gateway.adapters.gitlab")
except Exception:
    logger = get_logger("icdev.gateway.adapters.gitlab")

_ICDEV_PREFIX = "!icdev "


class GitLabAdapter(BaseChannelAdapter):
    """Gateway adapter for GitLab issue/MR note commands."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__("gitlab", config)
        self.webhook_secret = os.environ.get("GITLAB_WEBHOOK_SECRET", "")
        self.token = os.environ.get("GITLAB_TOKEN", "")
        self.base_url = os.environ.get("GITLAB_URL", "https://gitlab.com").rstrip("/")
        self.project_id = os.environ.get("GITLAB_PROJECT_ID", "")
        self._last_issue_iid: int = 0

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Verify X-Gitlab-Token header (plaintext shared secret comparison)."""
        if not self.webhook_secret:
            return True  # unconfigured = no verification

        if not signature:
            return False

        return hmac.compare_digest(signature, self.webhook_secret)

    def parse_webhook(self, request_data: Dict[str, Any], headers: Dict[str, str]) -> Optional[CommandEnvelope]:
        """Parse GitLab Note event webhook.

        Handles object_kind=note for issues and merge requests.
        Only processes notes whose body starts with !icdev (case-insensitive).
        """
        if request_data.get("object_kind") != "note":
            return None

        note = request_data.get("object_attributes", {})
        body = (note.get("note") or note.get("body") or "").strip()
        if not body.lower().startswith(_ICDEV_PREFIX.lower()):
            return None

        noteable_type = note.get("noteable_type", "")
        if noteable_type not in ("Issue", "MergeRequest"):
            return None

        command_text = body[len(_ICDEV_PREFIX):].strip()
        command, args = parse_command_text(command_text)
        if not command:
            return None

        user = request_data.get("user", {})
        noteable_id = note.get("noteable_id", 0)
        issue_iid = (
            request_data.get("issue", {}).get("iid")
            or request_data.get("merge_request", {}).get("iid")
            or 0
        )
        self._last_issue_iid = int(issue_iid) if issue_iid else 0

        return CommandEnvelope(
            channel="gitlab",
            channel_user_id=str(user.get("id", "")),
            channel_user_name=user.get("username", ""),
            channel_message_id=str(note.get("id", "")),
            channel_thread_id=str(self._last_issue_iid),
            raw_text=body,
            command=command,
            args=args,
            project_id=args.get("project_id", ""),
            timestamp=note.get("created_at", datetime.now(timezone.utc).isoformat()),
            signature=headers.get("X-Gitlab-Token", ""),
        )

    def send_message(self, channel_user_id: str, text: str, thread_id: str = "") -> bool:
        """Post reply as a GitLab issue note.

        thread_id = issue IID (str). Falls back to last parsed IID if empty.
        """
        issue_iid = int(thread_id) if thread_id and thread_id.isdigit() else self._last_issue_iid
        if not issue_iid:
            logger.error("GitLab: No issue IID to reply to")
            return False

        # Delegate to DataBridge connector
        try:
            from tools.databridge.connectors.gitlab_connector import GitLabConnector
            from tools.databridge.connector import ConnectorRequest
            c = GitLabConnector()
            if c.connect({}):
                resp = c.write(
                    ConnectorRequest(table_name="send_note"),
                    {"body": text, "issue_iid": issue_iid},
                )
                if resp.status in ("ok", "success"):
                    return True
        except Exception as exc:
            logger.debug("GitLabConnector.write failed, falling back: %s", exc)

        # Fallback: direct REST API call
        if not self.token or not self.project_id:
            return False
        pid = quote(str(self.project_id), safe="")
        url = f"{self.base_url}/api/v4/projects/{pid}/issues/{issue_iid}/notes"
        payload = json.dumps({"body": text}).encode("utf-8")
        try:
            req = Request(
                url, data=payload,
                headers={
                    "PRIVATE-TOKEN": self.token,
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:  # nosec B310 -- configured GitLab endpoint only
                return resp.status in (200, 201)
        except (URLError, Exception) as exc:
            logger.error("GitLab send failed: %s", exc)
            return False
