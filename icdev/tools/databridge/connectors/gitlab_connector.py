#!/usr/bin/env python3
# CUI // SP-CTI
"""GitLab DataBridge Connector — read issue/MR notes and post replies
via the GitLab REST API v4 using a personal access token.

Supports self-hosted GitLab instances (set GITLAB_URL for on-prem/air-gap).

Endpoints (tables):
    issues         — Issue notes since last offset (GET, incremental)
    merge_requests — Merge request notes (GET)
    pipelines      — Recent pipelines (GET)
    send_note      — Post a note to an issue or MR (POST) [write-only]

Commands embedded in notes use the !icdev prefix:
    !icdev /build Fix the CI pipeline
    !icdev /status

Usage:
    python tools/databridge/connectors/gitlab_connector.py --health
    python tools/databridge/connectors/gitlab_connector.py --read issues --json
    python tools/databridge/connectors/gitlab_connector.py --send "note" --issue 5

Env vars:
    GITLAB_TOKEN      — Personal access token (api scope)
    GITLAB_URL        — Base URL (default: https://gitlab.com)
    GITLAB_PROJECT_ID — Target project ID (numeric or namespace/project)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import HTTPError
from urllib.parse import quote

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
    logger = get_logger("databridge.gitlab")
except Exception:
    logger = get_logger("databridge.gitlab")

_OFFSET_FILE = BASE_DIR / ".tmp" / "gitlab_offset.txt"
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
class GitLabConnector(SaaSBaseConnector):
    """GitLab REST API v4 connector (cloud + self-hosted)."""

    _connector_name = "gitlab"
    _default_base_url = "https://gitlab.com"
    _endpoints = {
        "issues":         "",   # built dynamically
        "merge_requests": "",   # built dynamically
        "pipelines":      "",   # built dynamically
        "send_note":      "",   # built dynamically
    }

    def __init__(self) -> None:
        super().__init__()
        self._project_id: str = ""

    def connect(self, config: Dict[str, Any]) -> bool:
        try:
            from dotenv import load_dotenv
            load_dotenv(BASE_DIR / ".env")
        except ImportError:
            pass

        token = config.get("api_key", os.getenv("GITLAB_TOKEN", ""))
        base_url = config.get("base_url", os.getenv("GITLAB_URL", "https://gitlab.com")).rstrip("/")
        self._project_id = config.get("project_id", os.getenv("GITLAB_PROJECT_ID", ""))

        if not token:
            logger.error("GITLAB_TOKEN is required")
            return False
        if not self._project_id:
            logger.error("GITLAB_PROJECT_ID is required")
            return False

        self._config = {"api_key": token, "base_url": base_url}
        self._base_url = base_url
        self._auth_headers = self._build_auth_headers(self._config)
        self._connected = True
        return True

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        token = config.get("api_key", os.getenv("GITLAB_TOKEN", ""))
        return {"PRIVATE-TOKEN": token} if token else {}

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

    def _project_url(self) -> str:
        pid = quote(str(self._project_id), safe="")
        return f"{self._base_url}/api/v4/projects/{pid}"

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        t0 = time.time()
        table = request.table_name
        since = request.incremental_value or _load_offset()

        if table == "issues":
            url = f"{self._project_url()}/issues/notes"
            params = ["sort=asc", "order_by=created_at"]
            if since:
                params.append(f"updated_after={since}")
            if request.limit:
                params.append(f"per_page={request.limit}")
            url += "?" + "&".join(params)
            return self._fetch_notes(url, "issue", since, t0)

        if table == "merge_requests":
            url = f"{self._project_url()}/merge_requests/notes"
            params = ["sort=asc", "order_by=created_at"]
            if since:
                params.append(f"updated_after={since}")
            if request.limit:
                params.append(f"per_page={request.limit}")
            url += "?" + "&".join(params)
            return self._fetch_notes(url, "merge_request", since, t0)

        if table == "pipelines":
            url = f"{self._project_url()}/pipelines"
            if request.limit:
                url += f"?per_page={request.limit}"
            try:
                data = self._http_get(url)
                rows = [
                    {
                        "id": p.get("id"), "status": p.get("status"),
                        "ref": p.get("ref"), "created_at": p.get("created_at"),
                        "web_url": p.get("web_url"),
                    }
                    for p in (data if isinstance(data, list) else [])
                ]
                return ConnectorResponse(status="ok", data=rows, row_count=len(rows),
                                         duration_ms=int((time.time() - t0) * 1000))
            except Exception as exc:
                return ConnectorResponse(status="error", errors=[str(exc)])

        return ConnectorResponse(status="error", errors=[f"Unknown table: {table}"])

    def _fetch_notes(self, url: str, source: str, since: str, t0: float) -> ConnectorResponse:
        try:
            data = self._http_get(url)
            notes = data if isinstance(data, list) else []
            rows = []
            latest_ts = since or ""
            for n in notes:
                body = n.get("body", "")
                noteable_iid = n.get("noteable_iid", 0)
                row = {
                    "note_id": n.get("id", 0),
                    "issue_iid": noteable_iid,
                    "body": body,
                    "author_username": (n.get("author") or {}).get("username", ""),
                    "created_at": n.get("created_at", ""),
                    "is_icdev_command": body.lower().startswith(_ICDEV_PREFIX.lower()),
                    "source": source,
                }
                rows.append(row)
                ts = n.get("created_at", "")
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

    def write(self, request: ConnectorRequest, data: Any) -> ConnectorResponse:
        t0 = time.time()
        if not isinstance(data, dict):
            return ConnectorResponse(status="error", errors=["data must be a dict"])

        body = data.get("body", data.get("text", ""))
        issue_iid = data.get("issue_iid", data.get("issue_number", 0))
        if not body:
            return ConnectorResponse(status="error", errors=["'body' is required"])
        if not issue_iid:
            return ConnectorResponse(status="error", errors=["'issue_iid' is required"])

        url = f"{self._project_url()}/issues/{issue_iid}/notes"
        try:
            resp = self._http_post(url, {"body": body})
            return ConnectorResponse(
                status="ok", data=[{"note_id": resp.get("id", 0)}],
                row_count=1, duration_ms=int((time.time() - t0) * 1000),
            )
        except HTTPError as exc:
            return ConnectorResponse(status="error", errors=[f"HTTP {exc.code}: {exc.reason}"])
        except Exception as exc:
            return ConnectorResponse(status="error", errors=[str(exc)])

    def health_check(self) -> Dict[str, Any]:
        try:
            data = self._http_get(self._project_url())
            return {"status": "healthy", "connector": "gitlab", "project": data.get("path_with_namespace", "")}
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc), "connector": "gitlab"}

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        if table_name in ("issues", "merge_requests"):
            return SchemaDefinition(fields=[
                SchemaField("note_id", "int64"), SchemaField("issue_iid", "int64"),
                SchemaField("body", "string"), SchemaField("author_username", "string"),
                SchemaField("created_at", "string"), SchemaField("is_icdev_command", "bool"),
                SchemaField("source", "string"),
            ])
        if table_name == "pipelines":
            return SchemaDefinition(fields=[
                SchemaField("id", "int64"), SchemaField("status", "string"),
                SchemaField("ref", "string"), SchemaField("created_at", "string"),
                SchemaField("web_url", "string"),
            ])
        return SchemaDefinition(fields=[])

    def list_tables(self) -> List[str]:
        return ["issues", "merge_requests", "pipelines", "send_note"]


# ── CLI ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="GitLab DataBridge Connector")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--read", metavar="TABLE")
    parser.add_argument("--send", metavar="BODY")
    parser.add_argument("--issue", type=int, default=0)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    c = GitLabConnector()
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
        req = ConnectorRequest(table_name="send_note")
        resp = c.write(req, {"body": args.send, "issue_iid": args.issue})
        print(json.dumps({"status": resp.status}, indent=2) if args.json else f"Sent: {resp.status}")
