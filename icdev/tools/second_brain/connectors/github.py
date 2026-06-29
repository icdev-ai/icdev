# CUI // SP-CTI
"""GitHub/GitLab PAT connector — assigned issues, open PRs, review requests."""
from __future__ import annotations

import json
import urllib.request
from typing import Any

from tools.logging.icdev_logger import get_logger
from tools.second_brain.connectors.base import BaseConnector

logger = get_logger(__name__)

_GH_API = "https://api.github.com"


class GitHubConnector(BaseConnector):
    service = "github"

    def verify(self, credentials: dict) -> bool:
        token = credentials.get("access_token") or credentials.get("pat")
        if not token:
            return False
        try:
            req = urllib.request.Request(
                f"{_GH_API}/user",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status == 200
        except Exception as exc:
            logger.debug("[github] verify failed: %s", exc)
            return False

    def get_todays_items(self, user_id: str) -> list[dict[str, Any]]:
        token = self._get_token(user_id)
        if not token:
            return []
        items: list[dict] = []
        try:
            items.extend(self._get_assigned_issues(token))
            items.extend(self._get_open_prs(token))
            items.extend(self._get_review_requests(token))
        except Exception as exc:
            logger.warning("[github] get_todays_items failed: %s", exc)
        return items

    def _gh_get(self, token: str, path: str) -> list | dict:
        req = urllib.request.Request(
            f"{_GH_API}{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def _get_assigned_issues(self, token: str) -> list[dict]:
        data = self._gh_get(token, "/issues?filter=assigned&state=open&per_page=10")
        return [
            {
                "source": "github",
                "type": "issue",
                "repo": (i.get("repository", {}) or {}).get("full_name", ""),
                "number": i.get("number"),
                "title": i.get("title", ""),
                "url": i.get("html_url", ""),
                "labels": [l.get("name") for l in (i.get("labels") or [])],
            }
            for i in (data if isinstance(data, list) else [])
        ]

    def _get_open_prs(self, token: str) -> list[dict]:
        data = self._gh_get(token, "/search/issues?q=is:pr+is:open+author:@me&per_page=10")
        return [
            {
                "source": "github",
                "type": "pull_request",
                "title": i.get("title", ""),
                "url": i.get("html_url", ""),
                "repo": i.get("repository_url", "").replace(f"{_GH_API}/repos/", ""),
            }
            for i in (data.get("items") if isinstance(data, dict) else [])
        ]

    def _get_review_requests(self, token: str) -> list[dict]:
        data = self._gh_get(token, "/search/issues?q=is:pr+is:open+review-requested:@me&per_page=10")
        return [
            {
                "source": "github",
                "type": "review_request",
                "title": i.get("title", ""),
                "url": i.get("html_url", ""),
            }
            for i in (data.get("items") if isinstance(data, dict) else [])
        ]

    def sync_to_context(self, user_id: str) -> dict[str, Any]:
        items = self.get_todays_items(user_id)
        return {"service": "github", "items_count": len(items), "items": items}

    def _get_token(self, user_id: str) -> str | None:
        try:
            from tools.second_brain.integrations import get_decrypted_token
            return get_decrypted_token(user_id, "github")
        except Exception:
            return None
