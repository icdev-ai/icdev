#!/usr/bin/env python3
# CUI // SP-CTI
"""GitLab notification adapter — posts ICDEV notifications as issue notes.

Named `gitlab_notify` to coexist with any future `gitlab.py` gateway module.

Supports self-hosted GitLab instances (set GITLAB_URL for on-prem/air-gap).

Delegates to GitLabConnector.write("send_note") when available; falls back to
direct REST API call.

Env vars:
    GITLAB_TOKEN        — personal access token (api scope)
    GITLAB_URL          — base URL (default: https://gitlab.com)
    GITLAB_PROJECT_ID   — project ID (numeric or namespace/path)
    GITLAB_ISSUE_IID    — target issue IID for notification notes
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.notifications.adapters.base import NotificationAdapter  # noqa: E402

try:
    from tools.logging.icdev_logger import get_logger
    logger = get_logger("icdev.notifications.gitlab")
except Exception:
    logger = logging.getLogger("icdev.notifications.gitlab")


class GitLabNotificationAdapter(NotificationAdapter):
    """Outbound notification adapter for GitLab issue notes."""

    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config or {})
        self.token = os.environ.get("GITLAB_TOKEN", "")
        self.base_url = os.environ.get("GITLAB_URL", "https://gitlab.com").rstrip("/")
        self.project_id = os.environ.get("GITLAB_PROJECT_ID", "")
        raw = os.environ.get("GITLAB_ISSUE_IID", "")
        self.issue_iid = int(raw) if raw.isdigit() else 0

    def send(self, message: str, subject: str = "", **kwargs) -> bool:
        body = f"### {subject}\n\n{message}" if subject else message
        issue_iid = kwargs.get("issue_iid", self.issue_iid)

        if not issue_iid:
            logger.warning("GitLab: no GITLAB_ISSUE_IID configured for notifications")
            return False

        # Delegate to DataBridge connector
        try:
            from tools.databridge.connectors.gitlab_connector import GitLabConnector
            from tools.databridge.connector import ConnectorRequest
            c = GitLabConnector()
            if c.connect({}):
                resp = c.write(
                    ConnectorRequest(table_name="send_note"),
                    {"body": body, "issue_iid": issue_iid},
                )
                if resp.status in ("ok", "success"):
                    return True
        except Exception as exc:
            logger.debug("GitLabConnector.write failed, falling back: %s", exc)

        # Fallback: direct REST API
        if not self.token or not self.project_id:
            logger.warning("GitLab: missing token or project_id")
            return False

        pid = quote(str(self.project_id), safe="")
        url = f"{self.base_url}/api/v4/projects/{pid}/issues/{issue_iid}/notes"
        payload = json.dumps({"body": body}).encode("utf-8")
        try:
            req = Request(
                url, data=payload,
                headers={"PRIVATE-TOKEN": self.token, "Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:  # nosec B310 -- configured GitLab endpoint
                return resp.status in (200, 201)
        except (URLError, Exception) as exc:
            logger.error("GitLab notification failed: %s", exc)
            return False

    def health_check(self) -> Dict[str, Any]:
        configured = bool(self.token and self.project_id and self.issue_iid)
        return {"adapter": "gitlab", "configured": configured, "status": "ok" if configured else "unconfigured"}
