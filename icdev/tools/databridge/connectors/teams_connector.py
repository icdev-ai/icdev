#!/usr/bin/env python3
# CUI // SP-CTI
"""Microsoft Teams DataBridge Connector — read channel messages and send replies
via the Microsoft Graph API using AAD client_credentials OAuth2.

Endpoints (tables):
    me           — Bot/app identity (GET /me)
    messages     — Channel messages (GET, incremental by lastModifiedDateTime)
    channels     — Team channels (GET)
    send_message — Send a message to a channel (POST) [write-only]

Usage:
    python tools/databridge/connectors/teams_connector.py --health
    python tools/databridge/connectors/teams_connector.py --read messages --json
    python tools/databridge/connectors/teams_connector.py --send "Hello" --json

Env vars:
    TEAMS_APP_ID        — Azure AD application (client) ID
    TEAMS_APP_SECRET    — Azure AD client secret
    TEAMS_TENANT_ID     — Azure AD tenant ID (default: common)
    TEAMS_TEAM_ID       — Target team ID
    TEAMS_CHANNEL_ID    — Target channel ID
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

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
    logger = get_logger("databridge.teams")
except Exception:
    logger = get_logger("databridge.teams")

_OFFSET_FILE = BASE_DIR / ".tmp" / "teams_offset.txt"
_TOKEN_CACHE: Dict[str, Any] = {}  # {"token": str, "expires_at": float}


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


def _get_aad_token(tenant_id: str, client_id: str, client_secret: str, scope: str) -> Optional[str]:
    """Fetch AAD OAuth2 client_credentials token. Cached with expiry-60s margin."""
    cache_key = f"{tenant_id}:{client_id}:{scope}"
    cached = _TOKEN_CACHE.get(cache_key)
    if cached and cached["expires_at"] > time.time():
        return cached["token"]

    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    payload = (
        f"client_id={client_id}&client_secret={client_secret}"
        f"&scope={scope}&grant_type=client_credentials"
    ).encode("utf-8")
    req = Request(url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(req, timeout=15) as resp:  # nosec B310 -- Microsoft login endpoint only
            data = json.loads(resp.read().decode("utf-8"))
        token = data.get("access_token")
        expires_in = data.get("expires_in", 3600)
        if token:
            _TOKEN_CACHE[cache_key] = {"token": token, "expires_at": time.time() + expires_in - 60}
            return token
    except Exception as exc:
        logger.error("AAD token fetch failed: %s", exc)
    return None


@register_connector
class TeamsConnector(SaaSBaseConnector):
    """Microsoft Teams connector via Graph API."""

    _connector_name = "teams"
    _default_base_url = "https://graph.microsoft.com/v1.0"
    _endpoints = {
        "me":           "/me",
        "messages":     "",   # built dynamically from team/channel IDs
        "channels":     "",   # built dynamically from team ID
        "send_message": "",   # built dynamically
    }

    def __init__(self) -> None:
        super().__init__()
        self._app_id: str = ""
        self._app_secret: str = ""
        self._tenant_id: str = "common"
        self._team_id: str = ""
        self._channel_id: str = ""

    def connect(self, config: Dict[str, Any]) -> bool:
        try:
            from dotenv import load_dotenv
            load_dotenv(BASE_DIR / ".env")
        except ImportError:
            pass

        self._app_id = config.get("app_id", os.getenv("TEAMS_APP_ID", ""))
        self._app_secret = config.get("app_secret", os.getenv("TEAMS_APP_SECRET", ""))
        self._tenant_id = config.get("tenant_id", os.getenv("TEAMS_TENANT_ID", "common"))
        self._team_id = config.get("team_id", os.getenv("TEAMS_TEAM_ID", ""))
        self._channel_id = config.get("channel_id", os.getenv("TEAMS_CHANNEL_ID", ""))

        if not self._app_id or not self._app_secret:
            logger.error("TEAMS_APP_ID and TEAMS_APP_SECRET are required")
            return False

        self._config = config
        self._base_url = self._default_base_url
        token = self._get_token()
        if not token:
            return False
        self._auth_headers = {"Authorization": f"Bearer {token}"}
        self._connected = True
        return True

    def _get_token(self) -> Optional[str]:
        return _get_aad_token(
            self._tenant_id, self._app_id, self._app_secret,
            "https://graph.microsoft.com/.default",
        )

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        token = self._get_token()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _refresh_auth(self) -> None:
        token = self._get_token()
        if token:
            self._auth_headers = {"Authorization": f"Bearer {token}"}

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=True,
            supports_schema_inference=True,
            supports_incremental=True,
            max_batch_size=50,
            supported_formats=["json"],
        )

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        t0 = time.time()
        table = request.table_name
        self._refresh_auth()

        if table == "me":
            try:
                data = self._http_get(f"{self._base_url}/me")
                return ConnectorResponse(
                    status="ok", data=[data], row_count=1,
                    duration_ms=int((time.time() - t0) * 1000),
                )
            except Exception as exc:
                return ConnectorResponse(status="error", errors=[str(exc)])

        if table == "channels":
            if not self._team_id:
                return ConnectorResponse(status="error", errors=["TEAMS_TEAM_ID not set"])
            try:
                url = f"{self._base_url}/teams/{self._team_id}/channels"
                data = self._http_get(url)
                rows = data.get("value", [])
                return ConnectorResponse(
                    status="ok", data=rows, row_count=len(rows),
                    duration_ms=int((time.time() - t0) * 1000),
                )
            except Exception as exc:
                return ConnectorResponse(status="error", errors=[str(exc)])

        if table == "messages":
            if not self._team_id or not self._channel_id:
                return ConnectorResponse(status="error", errors=["TEAMS_TEAM_ID and TEAMS_CHANNEL_ID required"])

            url = (f"{self._base_url}/teams/{self._team_id}"
                   f"/channels/{self._channel_id}/messages")
            filters = []
            since = request.incremental_value or _load_offset()
            if since:
                filters.append(f"lastModifiedDateTime gt {since}")
            if filters:
                url += "?$filter=" + " and ".join(filters)
            if request.limit:
                sep = "&" if "?" in url else "?"
                url += f"{sep}$top={request.limit}"

            try:
                data = self._http_get(url)
                raw_rows = data.get("value", [])
                rows = []
                latest_ts = since or ""
                for m in raw_rows:
                    row = {
                        "message_id": m.get("id", ""),
                        "created_at": m.get("createdDateTime", ""),
                        "last_modified": m.get("lastModifiedDateTime", ""),
                        "from_user_id": (m.get("from") or {}).get("user", {}).get("id", ""),
                        "from_user_name": (m.get("from") or {}).get("user", {}).get("displayName", ""),
                        "body_content": (m.get("body") or {}).get("content", ""),
                        "channel_id": self._channel_id,
                    }
                    rows.append(row)
                    ts = m.get("lastModifiedDateTime", "")
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

        return ConnectorResponse(status="error", errors=[f"Unknown table: {table}"])

    def write(self, request: ConnectorRequest, data: Any) -> ConnectorResponse:
        t0 = time.time()
        self._refresh_auth()

        if not isinstance(data, dict):
            return ConnectorResponse(status="error", errors=["data must be a dict"])

        text = data.get("text", "")
        if not text:
            return ConnectorResponse(status="error", errors=["'text' is required"])

        team_id = data.get("team_id", self._team_id)
        channel_id = data.get("channel_id", data.get("conversation_id", self._channel_id))
        if not team_id or not channel_id:
            return ConnectorResponse(status="error", errors=["team_id/channel_id required"])

        url = f"{self._base_url}/teams/{team_id}/channels/{channel_id}/messages"
        payload = {"body": {"contentType": "html", "content": text}}
        try:
            resp = self._http_post(url, payload)
            return ConnectorResponse(
                status="ok", data=[{"message_id": resp.get("id", "")}],
                row_count=1, duration_ms=int((time.time() - t0) * 1000),
            )
        except HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            return ConnectorResponse(status="error", errors=[f"HTTP {exc.code}", body])
        except Exception as exc:
            return ConnectorResponse(status="error", errors=[str(exc)])

    def health_check(self) -> Dict[str, Any]:
        self._refresh_auth()
        try:
            data = self._http_get(f"{self._base_url}/me")
            return {
                "status": "healthy",
                "connector": "teams",
                "display_name": data.get("displayName", ""),
            }
        except Exception as exc:
            return {"status": "unhealthy", "error": str(exc), "connector": "teams"}

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        if table_name == "messages":
            return SchemaDefinition(fields=[
                SchemaField("message_id", "string"),
                SchemaField("created_at", "string"),
                SchemaField("last_modified", "string"),
                SchemaField("from_user_id", "string"),
                SchemaField("from_user_name", "string"),
                SchemaField("body_content", "string"),
                SchemaField("channel_id", "string"),
            ])
        if table_name == "channels":
            return SchemaDefinition(fields=[
                SchemaField("id", "string"),
                SchemaField("displayName", "string"),
                SchemaField("description", "string"),
            ])
        return SchemaDefinition(fields=[])

    def list_tables(self) -> List[str]:
        return ["me", "messages", "channels", "send_message"]


# ── CLI ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Teams DataBridge Connector")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--read", metavar="TABLE")
    parser.add_argument("--send", metavar="TEXT")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    c = TeamsConnector()
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
        req = ConnectorRequest(table_name="send_message")
        resp = c.write(req, {"text": args.send})
        print(json.dumps({"status": resp.status}, indent=2) if args.json else f"Sent: {resp.status}")
