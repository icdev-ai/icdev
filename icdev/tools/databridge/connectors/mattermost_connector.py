#!/usr/bin/env python3
# CUI // SP-CTI
"""Mattermost DataBridge Connector — read posts and send messages
via the Mattermost REST API v4 using Bearer token auth.

Endpoints (tables):
    me        — Current user identity (GET /api/v4/users/me)
    posts     — Channel posts, incremental by update_at ms (GET)
    channels  — Team channels (GET)
    users     — Team members (GET)
    send_post — Create a post in a channel (POST) [write-only]

Usage:
    python tools/databridge/connectors/mattermost_connector.py --health
    python tools/databridge/connectors/mattermost_connector.py --read posts --json
    python tools/databridge/connectors/mattermost_connector.py --send "Hello" --json

Env vars:
    MATTERMOST_URL        — Base URL (e.g., https://mattermost.enclave.mil)
    MATTERMOST_TOKEN      — Personal access token or bot token
    MATTERMOST_CHANNEL_ID — Default channel to poll and post to
    MATTERMOST_TEAM_ID    — Team ID for channel listing
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

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
    logger = get_logger("databridge.mattermost")
except Exception:
    logger = get_logger("databridge.mattermost")

_OFFSET_FILE = BASE_DIR / ".tmp" / "mattermost_offset.txt"


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
class MattermostConnector(SaaSBaseConnector):
    """Mattermost REST API v4 connector."""

    _connector_name = "mattermost"
    _default_base_url = ""  # set from env in connect()
    _endpoints = {
        "me":        "/api/v4/users/me",
        "posts":     "",   # built dynamically
        "channels":  "",   # built dynamically
        "users":     "",   # built dynamically
        "send_post": "/api/v4/posts",
    }

    def __init__(self) -> None:
        super().__init__()
        self._channel_id: str = ""
        self._team_id: str = ""

    def connect(self, config: Dict[str, Any]) -> bool:
        try:
            from dotenv import load_dotenv
            load_dotenv(BASE_DIR / ".env")
        except ImportError:
            pass

        base_url = config.get("base_url", os.getenv("MATTERMOST_URL", "")).rstrip("/")
        token = config.get("api_key", os.getenv("MATTERMOST_TOKEN", ""))
        self._channel_id = config.get("channel_id", os.getenv("MATTERMOST_CHANNEL_ID", ""))
        self._team_id = config.get("team_id", os.getenv("MATTERMOST_TEAM_ID", ""))

        if not base_url or not token:
            logger.error("MATTERMOST_URL and MATTERMOST_TOKEN are required")
            return False

        self._config = {"base_url": base_url, "api_key": token}
        self._base_url = base_url
        self._auth_headers = self._build_auth_headers(self._config)
        self._connected = True
        return True

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        token = config.get("api_key", os.getenv("MATTERMOST_TOKEN", ""))
        return {"Authorization": f"Bearer {token}"} if token else {}

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=True,
            supports_schema_inference=True,
            supports_incremental=True,
            max_batch_size=200,
            supported_formats=["json"],
        )

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        t0 = time.time()
        table = request.table_name

        if table == "me":
            try:
                data = self._http_get(f"{self._base_url}/api/v4/users/me")
                return ConnectorResponse(status="ok", data=[data], row_count=1,
                                         duration_ms=int((time.time() - t0) * 1000))
            except Exception as exc:
                return ConnectorResponse(status="error", errors=[str(exc)])

        if table == "channels":
            if not self._team_id:
                return ConnectorResponse(status="error", errors=["MATTERMOST_TEAM_ID not set"])
            try:
                url = f"{self._base_url}/api/v4/teams/{self._team_id}/channels"
                data = self._http_get(url)
                rows = data if isinstance(data, list) else []
                return ConnectorResponse(status="ok", data=rows, row_count=len(rows),
                                         duration_ms=int((time.time() - t0) * 1000))
            except Exception as exc:
                return ConnectorResponse(status="error", errors=[str(exc)])

        if table == "users":
            if not self._team_id:
                return ConnectorResponse(status="error", errors=["MATTERMOST_TEAM_ID not set"])
            try:
                url = f"{self._base_url}/api/v4/teams/{self._team_id}/members"
                data = self._http_get(url)
                rows = data if isinstance(data, list) else []
                return ConnectorResponse(status="ok", data=rows, row_count=len(rows),
                                         duration_ms=int((time.time() - t0) * 1000))
            except Exception as exc:
                return ConnectorResponse(status="error", errors=[str(exc)])

        if table == "posts":
            channel_id = request.filters.get("channel_id", self._channel_id) if request.filters else self._channel_id
            if not channel_id:
                return ConnectorResponse(status="error", errors=["MATTERMOST_CHANNEL_ID not set"])

            since = request.incremental_value or _load_offset()
            url = f"{self._base_url}/api/v4/channels/{channel_id}/posts"
            params = []
            if since:
                params.append(f"since={since}")
            if request.limit:
                params.append(f"per_page={request.limit}")
            if params:
                url += "?" + "&".join(params)

            try:
                data = self._http_get(url)
                order = data.get("order", [])
                posts_dict = data.get("posts", {})
                rows = []
                latest_update = int(since) if since and since.isdigit() else 0
                for post_id in order:
                    p = posts_dict.get(post_id, {})
                    rows.append({
                        "post_id": p.get("id", ""),
                        "channel_id": p.get("channel_id", ""),
                        "user_id": p.get("user_id", ""),
                        "message": p.get("message", ""),
                        "create_at": p.get("create_at", 0),
                        "update_at": p.get("update_at", 0),
                    })
                    if p.get("update_at", 0) > latest_update:
                        latest_update = p["update_at"]
                if latest_update and str(latest_update) != since:
                    _save_offset(str(latest_update))
                return ConnectorResponse(
                    status="ok", data=rows, row_count=len(rows),
                    duration_ms=int((time.time() - t0) * 1000),
                    metadata={"offset": str(latest_update)},
                )
            except Exception as exc:
                return ConnectorResponse(status="error", errors=[str(exc)])

        return ConnectorResponse(status="error", errors=[f"Unknown table: {table}"])

    def write(self, request: ConnectorRequest, data: Any) -> ConnectorResponse:
        t0 = time.time()
        if not isinstance(data, dict):
            return ConnectorResponse(status="error", errors=["data must be a dict"])

        message = data.get("message", data.get("text", ""))
        if not message:
            return ConnectorResponse(status="error", errors=["'message' or 'text' is required"])

        channel_id = data.get("channel_id", self._channel_id)
        if not channel_id:
            return ConnectorResponse(status="error", errors=["channel_id required"])

        url = f"{self._base_url}/api/v4/posts"
        try:
            resp = self._http_post(url, {"channel_id": channel_id, "message": message})
            return ConnectorResponse(
                status="ok", data=[{"post_id": resp.get("id", "")}],
                row_count=1, duration_ms=int((time.time() - t0) * 1000),
            )
        except Exception as exc:
            return ConnectorResponse(status="error", errors=[str(exc)])

    def health_check(self) -> Dict[str, Any]:
        try:
            data = self._http_get(f"{self._base_url}/api/v4/users/me")
            return {"status": "healthy", "connector": "mattermost", "username": data.get("username", "")}
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc), "connector": "mattermost"}

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        if table_name == "posts":
            return SchemaDefinition(fields=[
                SchemaField("post_id", "string"), SchemaField("channel_id", "string"),
                SchemaField("user_id", "string"), SchemaField("message", "string"),
                SchemaField("create_at", "int64"), SchemaField("update_at", "int64"),
            ])
        return SchemaDefinition(fields=[])

    def list_tables(self) -> List[str]:
        return ["me", "posts", "channels", "users", "send_post"]


# ── CLI ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Mattermost DataBridge Connector")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--read", metavar="TABLE")
    parser.add_argument("--send", metavar="TEXT")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    c = MattermostConnector()
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
    elif args.send:
        req = ConnectorRequest(table_name="send_post")
        resp = c.write(req, {"message": args.send})
        print(json.dumps({"status": resp.status}, indent=2) if args.json else f"Sent: {resp.status}")
