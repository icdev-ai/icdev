# CUI // SP-CTI
"""Jira/Linear API key connector — sprint board and assigned tickets."""
from __future__ import annotations

import json
import os
import urllib.request
import base64
from typing import Any

from tools.logging.icdev_logger import get_logger
from tools.second_brain.connectors.base import BaseConnector

logger = get_logger(__name__)


class JiraConnector(BaseConnector):
    service = "jira"

    def verify(self, credentials: dict) -> bool:
        api_key = credentials.get("api_key") or credentials.get("access_token")
        email = credentials.get("email") or credentials.get("jira_email")
        base_url = credentials.get("base_url") or credentials.get("jira_base_url")
        if not all([api_key, email, base_url]):
            return False
        try:
            auth = base64.b64encode(f"{email}:{api_key}".encode()).decode()
            req = urllib.request.Request(
                f"{base_url.rstrip('/')}/rest/api/3/myself",
                headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status == 200
        except Exception as exc:
            logger.debug("[jira] verify failed: %s", exc)
            return False

    def get_todays_items(self, user_id: str) -> list[dict[str, Any]]:
        creds = self._get_credentials(user_id)
        if not creds:
            return []
        api_key = creds.get("api_key", "")
        email = creds.get("email", "")
        base_url = creds.get("base_url", "").rstrip("/")
        if not all([api_key, email, base_url]):
            return []
        try:
            auth = base64.b64encode(f"{email}:{api_key}".encode()).decode()
            jql = "assignee=currentUser() AND statusCategory != Done ORDER BY priority DESC"
            url = f"{base_url}/rest/api/3/search?jql={urllib.request.quote(jql)}&maxResults=15&fields=summary,status,priority,issuetype,duedate"
            req = urllib.request.Request(
                url, headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            items = []
            for issue in data.get("issues", []):
                fields = issue.get("fields", {})
                items.append({
                    "source": "jira",
                    "type": "ticket",
                    "key": issue.get("key", ""),
                    "title": fields.get("summary", ""),
                    "status": (fields.get("status") or {}).get("name", ""),
                    "priority": (fields.get("priority") or {}).get("name", ""),
                    "due": fields.get("duedate", ""),
                    "url": f"{base_url}/browse/{issue.get('key','')}",
                })
            return items
        except Exception as exc:
            logger.warning("[jira] get_todays_items failed: %s", exc)
            return []

    def sync_to_context(self, user_id: str) -> dict[str, Any]:
        items = self.get_todays_items(user_id)
        return {"service": "jira", "items_count": len(items), "items": items}

    def _get_credentials(self, user_id: str) -> dict | None:
        try:
            from tools.second_brain.integrations import get_integration_metadata
            meta = get_integration_metadata(user_id, "jira")
            return meta  # stores email + base_url; token comes from encrypted field
        except Exception:
            return None
