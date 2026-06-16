#!/usr/bin/env python3
# CUI // SP-CTI
"""GitHub notification adapter — posts ICDEV notifications as issue comments.

Named `github_notify` to coexist with any future `github.py` gateway module.

Delegates to GitHubConnector.write("send_comment") when available; falls back
to direct REST API call.

Env vars:
    GITHUB_TOKEN        — personal access token (repo, issues scopes)
    GITHUB_OWNER        — repository owner
    GITHUB_REPO         — repository name
    GITHUB_ISSUE_NUMBER — target issue number for notification comments
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict
from urllib.error import URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.notifications.adapters.base import NotificationAdapter  # noqa: E402

try:
    from tools.logging.icdev_logger import get_logger
    logger = get_logger("icdev.notifications.github")
except Exception:
    logger = logging.getLogger("icdev.notifications.github")


class GitHubNotificationAdapter(NotificationAdapter):
    """Outbound notification adapter for GitHub issue comments."""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config or {})
        self.token = os.environ.get("GITHUB_TOKEN", "")
        self.owner = os.environ.get("GITHUB_OWNER", "")
        self.repo = os.environ.get("GITHUB_REPO", "")
        raw = os.environ.get("GITHUB_ISSUE_NUMBER", "")
        self.issue_number = int(raw) if raw.isdigit() else 0

    def send(self, message: str, subject: str = "", **kwargs) -> bool:
        body = f"### {subject}\n\n{message}" if subject else message
        issue_number = kwargs.get("issue_number", self.issue_number)

        if not issue_number:
            logger.warning("GitHub: no GITHUB_ISSUE_NUMBER configured for notifications")
            return False

        # Delegate to DataBridge connector
        try:
            from tools.databridge.connectors.github_connector import GitHubConnector
            from tools.databridge.connector import ConnectorRequest
            c = GitHubConnector()
            if c.connect({}):
                resp = c.write(
                    ConnectorRequest(table_name="send_comment"),
                    {"body": body, "issue_number": issue_number},
                )
                if resp.status in ("ok", "success"):
                    return True
        except Exception as exc:
            logger.debug("GitHubConnector.write failed, falling back: %s", exc)

        # Fallback: direct REST API
        if not self.token or not self.owner or not self.repo:
            logger.warning("GitHub: missing token, owner, or repo")
            return False

        url = f"https://api.github.com/repos/{self.owner}/{self.repo}/issues/{issue_number}/comments"
        payload = json.dumps({"body": body}).encode("utf-8")
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
            logger.error("GitHub notification failed: %s", exc)
            return False

    def health_check(self) -> Dict[str, Any]:
        configured = bool(self.token and self.owner and self.repo and self.issue_number)
        return {"adapter": "github", "configured": configured, "status": "ok" if configured else "unconfigured"}
