#!/usr/bin/env python3
# CUI // SP-CTI
"""GitHub DataBridge Connector — read issue/PR comments and post replies
via the GitHub REST API v3 using a personal access token.

Endpoints (tables):
    issues        — Issue comments since last offset (GET, incremental)
    pull_requests — Pull request review comments since last offset (GET)
    commits       — Recent commits (GET)
    send_comment  — Post a comment to an issue or PR (POST) [write-only]

Commands embedded in issue/PR comments use the !icdev prefix:
    !icdev /build Fix the login timeout
    !icdev /status

Usage:
    python tools/databridge/connectors/github_connector.py --health
    python tools/databridge/connectors/github_connector.py --read issues --json
    python tools/databridge/connectors/github_connector.py --send "comment" --issue 42

Env vars:
    GITHUB_TOKEN  — Personal access token (repo + issues scope)
    GITHUB_OWNER  — Repository owner (e.g., icdev-ai)
    GITHUB_REPO   — Repository name (e.g., icdev)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import HTTPError

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.databridge.connector import (  # noqa: E402
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
    SchemaDefinition,
    SchemaField,
)
from tools.databridge.connectors.saas_base import SaaSBaseConnector  # noqa: E402
from tools.databridge.registry import register_connector  # noqa: E402

try:
    from tools.logging.icdev_logger import get_logger
    logger = get_logger("databridge.github")
except Exception:
    logger = get_logger("databridge.github")

_OFFSET_FILE = BASE_DIR / ".tmp" / "github_offset.txt"
_ICDEV_PREFIX = "!icdev "


def _load_offset() -> str:
    try:
        if _OFFSET_FILE.exists():
            return _OFFSET_FILE.read_text().strip()
    except Exception:
        pass
    return ""


def _save_offset(value: str) -> None:
    _OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OFFSET_FILE.write_text(value, encoding="utf-8", newline="")


@register_connector
class GitHubConnector(SaaSBaseConnector):
    """GitHub REST API v3 connector."""

    _connector_name = "github"
    _default_base_url = "https://api.github.com"
    _endpoints = {
        "issues":        "",   # built dynamically
        "pull_requests": "",   # built dynamically
        "commits":       "",   # built dynamically
        "send_comment":  "",   # built dynamically
    }

    def __init__(self) -> None:
        super().__init__()
        self._owner: str = ""
        self._repo: str = ""

    def connect(self, config: Dict[str, Any]) -> bool:
        try:
            from dotenv import load_dotenv
            load_dotenv(BASE_DIR / ".env")
        except ImportError:
            pass

        token = config.get("api_key", os.getenv("GITHUB_TOKEN", ""))
        self._owner = config.get("owner", os.getenv("GITHUB_OWNER", ""))
        self._repo = config.get("repo", os.getenv("GITHUB_REPO", ""))

        if not token:
            logger.error("GITHUB_TOKEN is required")
            return False
        if not self._owner or not self._repo:
            logger.error("GITHUB_OWNER and GITHUB_REPO are required")
            return False

        self._config = {"api_key": token}
        self._base_url = self._default_base_url
        self._auth_headers = self._build_auth_headers(self._config)
        self._connected = True
        return True

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        token = config.get("api_key", os.getenv("GITHUB_TOKEN", ""))
        return {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        } if token else {}

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=True,
            supports_schema_inference=True,
            supports_incremental=True,
            max_batch_size=100,
            supported_formats=["json"],
        )

    def _repo_url(self) -> str:
        return f"{self._base_url}/repos/{self._owner}/{self._repo}"

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        t0 = time.time()
        table = request.table_name
        since = request.incremental_value or _load_offset()

        if table == "issues":
            url = f"{self._repo_url()}/issues/comments"
            params = ["sort=created", "direction=asc"]
            if since:
                params.append(f"since={since}")
            if request.limit:
                params.append(f"per_page={request.limit}")
            url += "?" + "&".join(params)
            return self._fetch_comments(url, "issue", since, t0)

        if table == "pull_requests":
            url = f"{self._repo_url()}/pulls/comments"
            params = ["sort=created", "direction=asc"]
            if since:
                params.append(f"since={since}")
            if request.limit:
                params.append(f"per_page={request.limit}")
            url += "?" + "&".join(params)
            return self._fetch_comments(url, "pull_request", since, t0)

        if table == "commits":
            url = f"{self._repo_url()}/commits"
            if request.limit:
                url += f"?per_page={request.limit}"
            try:
                data = self._http_get(url)
                rows = [
                    {
                        "sha": c.get("sha", "")[:7],
                        "message": (c.get("commit") or {}).get("message", "").split("\n")[0],
                        "author": ((c.get("commit") or {}).get("author") or {}).get("name", ""),
                        "date": ((c.get("commit") or {}).get("author") or {}).get("date", ""),
                        "html_url": c.get("html_url", ""),
                    }
                    for c in (data if isinstance(data, list) else [])
                ]
                return ConnectorResponse(status="ok", data=rows, row_count=len(rows),
                                         duration_ms=int((time.time() - t0) * 1000))
            except Exception as exc:
                return ConnectorResponse(status="error", errors=[str(exc)])

        return ConnectorResponse(status="error", errors=[f"Unknown table: {table}"])

    def _fetch_comments(self, url: str, source: str, since: str, t0: float) -> ConnectorResponse:
        try:
            data = self._http_get(url)
            comments = data if isinstance(data, list) else []
            rows = []
            latest_ts = since or ""
            for c in comments:
                body = c.get("body", "")
                row = {
                    "comment_id": c.get("id", 0),
                    "issue_number": self._extract_issue_number(c),
                    "body": body,
                    "user_login": (c.get("user") or {}).get("login", ""),
                    "created_at": c.get("created_at", ""),
                    "html_url": c.get("html_url", ""),
                    "is_icdev_command": body.lower().startswith(_ICDEV_PREFIX.lower()),
                    "source": source,
                }
                rows.append(row)
                ts = c.get("created_at", "")
                if ts and ts > latest_ts:
                    latest_ts = ts
            if latest_ts and latest_ts != since:
                _save_offset(latest_ts)
            return ConnectorResponse(
                status="ok", data=rows, row_count=len(rows),
                duration_ms=int((time.time() - t0) * 1000),
                metadata={"offset": latest_ts},
            )
        except HTTPError as exc:
            return ConnectorResponse(status="error", errors=[f"HTTP {exc.code}: {exc.reason}"])
        except Exception as exc:
            return ConnectorResponse(status="error", errors=[str(exc)])

    def _extract_issue_number(self, comment: Dict) -> int:
        url = comment.get("issue_url", comment.get("pull_request_url", ""))
        if url:
            try:
                return int(url.rstrip("/").split("/")[-1])
            except (ValueError, IndexError):
                pass
        return 0

    def write(self, request: ConnectorRequest, data: Any) -> ConnectorResponse:
        t0 = time.time()
        if not isinstance(data, dict):
            return ConnectorResponse(status="error", errors=["data must be a dict"])

        body = data.get("body", data.get("text", ""))
        issue_number = data.get("issue_number", 0)
        if not body:
            return ConnectorResponse(status="error", errors=["'body' or 'text' is required"])
        if not issue_number:
            return ConnectorResponse(status="error", errors=["'issue_number' is required"])

        url = f"{self._repo_url()}/issues/{issue_number}/comments"
        try:
            resp = self._http_post(url, {"body": body})
            return ConnectorResponse(
                status="ok", data=[{"comment_id": resp.get("id", 0), "html_url": resp.get("html_url", "")}],
                row_count=1, duration_ms=int((time.time() - t0) * 1000),
            )
        except HTTPError as exc:
            return ConnectorResponse(status="error", errors=[f"HTTP {exc.code}: {exc.reason}"])
        except Exception as exc:
            return ConnectorResponse(status="error", errors=[str(exc)])

    def health_check(self) -> Dict[str, Any]:
        try:
            data = self._http_get(f"{self._base_url}/repos/{self._owner}/{self._repo}")
            return {"status": "healthy", "connector": "github", "repo": data.get("full_name", "")}
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc), "connector": "github"}

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        if table_name in ("issues", "pull_requests"):
            return SchemaDefinition(fields=[
                SchemaField("comment_id", "int64"), SchemaField("issue_number", "int64"),
                SchemaField("body", "string"), SchemaField("user_login", "string"),
                SchemaField("created_at", "string"), SchemaField("html_url", "string"),
                SchemaField("is_icdev_command", "bool"), SchemaField("source", "string"),
            ])
        return SchemaDefinition(fields=[])

    def list_tables(self) -> List[str]:
        return ["issues", "pull_requests", "commits", "send_comment"]


# ── CLI ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GitHub DataBridge Connector")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--read", metavar="TABLE")
    parser.add_argument("--send", metavar="BODY")
    parser.add_argument("--issue", type=int, default=0)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    c = GitHubConnector()
    connected = c.connect({})

    if args.health:
        result = c.health_check()
        print(json.dumps(result, indent=2) if args.json else f"Status: {result.get('status')}")
    elif args.read:
        req = ConnectorRequest(table_name=args.read, limit=args.limit)
        resp = c.read(req)
        if args.json:
            print(json.dumps({"status": resp.status, "data": resp.data, "row_count": resp.row_count}, indent=2, default=str))
        else:
            for row in (resp.data or []):
                print(row)
    elif args.send and args.issue:
        req = ConnectorRequest(table_name="send_comment")
        resp = c.write(req, {"body": args.send, "issue_number": args.issue})
        print(json.dumps({"status": resp.status}, indent=2) if args.json else f"Sent: {resp.status}")
