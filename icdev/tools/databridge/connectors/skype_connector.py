#!/usr/bin/env python3
# CUI // SP-CTI
"""Skype DataBridge Connector — send messages via the Bot Framework Connector API
using MSA OAuth2 client_credentials.

Skype has no polling API. Inbound messages arrive via Bot Framework webhook
and are stored in the local skype_inbox DB table. The read() method surfaces
those rows for IQE observability.

Endpoints (tables):
    me           — Bot identity probe (health check)
    messages     — Reads from local skype_inbox DB table (no polling API)
    send_message — Send a message via Bot Framework Connector API (POST) [write-only]

Usage:
    python tools/databridge/connectors/skype_connector.py --health
    python tools/databridge/connectors/skype_connector.py --read messages --json
    python tools/databridge/connectors/skype_connector.py --send "Hello" --convo "conv-id"

Env vars:
    SKYPE_APP_ID            — Bot application (client) ID
    SKYPE_APP_SECRET        — Bot application client secret
    SKYPE_CONVERSATION_ID   — Default conversation ID for sending
    SKYPE_SERVICE_URL       — Bot Framework service URL (from webhook Activity)
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
    logger = get_logger("databridge.skype")
except Exception:
    logger = get_logger("databridge.skype")

_TOKEN_CACHE: Dict[str, Any] = {}


def _get_botframework_token(app_id: str, app_secret: str) -> Optional[str]:
    """Fetch MSA Bot Framework token. Cached with expiry-60s margin."""
    cache_key = f"bf:{app_id}"
    cached = _TOKEN_CACHE.get(cache_key)
    if cached and cached["expires_at"] > time.time():
        return cached["token"]

    url = "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token"
    payload = (
        f"client_id={app_id}&client_secret={app_secret}"
        "&scope=https%3A%2F%2Fapi.botframework.com%2F.default"
        "&grant_type=client_credentials"
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
        logger.error("Bot Framework token fetch failed: %s", exc)
    return None


@register_connector
class SkypeConnector(SaaSBaseConnector):
    """Skype connector via Microsoft Bot Framework Connector API."""

    _connector_name = "skype"
    _default_base_url = "https://smba.trafficmanager.net/apis"
    _endpoints = {
        "me":           "",   # health probe only
        "messages":     "",   # reads from local DB
        "send_message": "",   # Bot Framework Connector API (dynamic URL)
    }

    def __init__(self) -> None:
        super().__init__()
        self._app_id: str = ""
        self._app_secret: str = ""
        self._conversation_id: str = ""
        self._service_url: str = ""

    def connect(self, config: Dict[str, Any]) -> bool:
        try:
            from dotenv import load_dotenv
            load_dotenv(BASE_DIR / ".env")
        except ImportError:
            pass

        self._app_id = config.get("app_id", os.getenv("SKYPE_APP_ID", ""))
        self._app_secret = config.get("app_secret", os.getenv("SKYPE_APP_SECRET", ""))
        self._conversation_id = config.get("conversation_id", os.getenv("SKYPE_CONVERSATION_ID", ""))
        self._service_url = config.get("service_url", os.getenv("SKYPE_SERVICE_URL",
                                                                  self._default_base_url)).rstrip("/")

        if not self._app_id or not self._app_secret:
            logger.error("SKYPE_APP_ID and SKYPE_APP_SECRET are required")
            return False

        self._config = config
        self._base_url = self._service_url
        self._connected = True
        return True

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        app_id = config.get("app_id", self._app_id)
        app_secret = config.get("app_secret", self._app_secret)
        token = _get_botframework_token(app_id, app_secret)
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _refresh_auth(self) -> None:
        token = _get_botframework_token(self._app_id, self._app_secret)
        if token:
            self._auth_headers = {"Authorization": f"Bearer {token}"}

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=True,
            supports_schema_inference=True,
            max_batch_size=100,
            supported_formats=["json"],
        )

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        """Read from local skype_inbox table (Skype has no polling API)."""
        t0 = time.time()
        table = request.table_name

        if table in ("me", "messages"):
            try:
                from tools.db.storage import get_connection
                conn = get_connection()
                try:
                    limit = request.limit or 100
                    rows = conn.execute(
                        "SELECT activity_id, sender_id, text, conversation_id, service_url, "
                        "created_at, processed_at "
                        "FROM skype_inbox ORDER BY created_at DESC LIMIT %s",
                        (limit,),
                    ).fetchall()
                    data = [dict(r) for r in rows]
                    return ConnectorResponse(
                        status="ok", data=data, row_count=len(data),
                        duration_ms=int((time.time() - t0) * 1000),
                    )
                finally:
                    conn.close()
            except Exception as exc:
                return ConnectorResponse(status="error", errors=[str(exc)])

        return ConnectorResponse(status="error", errors=[f"Unknown table: {table}"])

    def write(self, request: ConnectorRequest, data: Any) -> ConnectorResponse:
        """Send a message via Bot Framework Connector API."""
        t0 = time.time()
        if not isinstance(data, dict):
            return ConnectorResponse(status="error", errors=["data must be a dict"])

        text = data.get("text", "")
        if not text:
            return ConnectorResponse(status="error", errors=["'text' is required"])

        conversation_id = data.get("conversation_id", self._conversation_id)
        service_url = data.get("service_url", self._service_url).rstrip("/")
        if not conversation_id:
            return ConnectorResponse(status="error", errors=["conversation_id required"])

        self._refresh_auth()
        url = f"{service_url}/v3/conversations/{conversation_id}/activities"
        payload = {
            "type": "message",
            "from": {"id": self._app_id},
            "text": text,
            "textFormat": "plain",
        }
        try:
            token = _get_botframework_token(self._app_id, self._app_secret)
            if not token:
                return ConnectorResponse(status="error", errors=["Could not get Bot Framework token"])

            body = json.dumps(payload).encode("utf-8")
            req = Request(
                url, data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                method="POST",
            )
            with urlopen(req, timeout=15) as resp:  # nosec B310 -- configured Bot Framework endpoint only
                resp_data = json.loads(resp.read().decode("utf-8"))
            return ConnectorResponse(
                status="ok", data=[{"activity_id": resp_data.get("id", "")}],
                row_count=1, duration_ms=int((time.time() - t0) * 1000),
            )
        except HTTPError as exc:
            return ConnectorResponse(status="error", errors=[f"HTTP {exc.code}: {exc.reason}"])
        except Exception as exc:
            return ConnectorResponse(status="error", errors=[str(exc)])

    def health_check(self) -> Dict[str, Any]:
        token = _get_botframework_token(self._app_id, self._app_secret)
        if token:
            return {"status": "healthy", "connector": "skype", "has_token": True}
        return {"status": "unhealthy", "connector": "skype", "error": "Could not obtain Bot Framework token"}

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        if table_name == "messages":
            return SchemaDefinition(fields=[
                SchemaField("activity_id", "string"), SchemaField("sender_id", "string"),
                SchemaField("text", "string"), SchemaField("conversation_id", "string"),
                SchemaField("service_url", "string"), SchemaField("created_at", "string"),
                SchemaField("processed_at", "string"),
            ])
        return SchemaDefinition(fields=[])

    def list_tables(self) -> List[str]:
        return ["me", "messages", "send_message"]


# ── CLI ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Skype DataBridge Connector")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--read", metavar="TABLE")
    parser.add_argument("--send", metavar="TEXT")
    parser.add_argument("--convo", metavar="CONVERSATION_ID", default="")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    c = SkypeConnector()
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
        resp = c.write(req, {"text": args.send, "conversation_id": args.convo})
        print(json.dumps({"status": resp.status}, indent=2) if args.json else f"Sent: {resp.status}")
