#!/usr/bin/env python3
# CUI // SP-CTI
"""Telegram DataBridge Connector — read messages and send notifications
via the Telegram Bot API using the SaaSBaseConnector pattern.

Endpoints (tables):
    me         — Bot identity (getMe)
    updates    — Incoming messages (getUpdates)
    messages   — Send messages (sendMessage) [write]

Usage:
    python tools/databridge/connectors/telegram_connector.py --health
    python tools/databridge/connectors/telegram_connector.py --read updates --json
    python tools/databridge/connectors/telegram_connector.py --send "Hello" --json
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import os
import sys
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
from tools.databridge.connectors.saas_base import (  # noqa: E402
    SaaSBaseConnector,
)
from tools.databridge.registry import register_connector  # noqa: E402

logger = get_logger("databridge.telegram")

# Offset tracking for getUpdates polling
_OFFSET_FILE = BASE_DIR / ".tmp" / "telegram_offset.txt"


def _load_offset() -> int:
    try:
        if _OFFSET_FILE.exists():
            return int(_OFFSET_FILE.read_text().strip())
    except Exception:
        pass
    return 0


def _save_offset(offset: int):
    _OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OFFSET_FILE.write_text(str(offset), encoding="utf-8", newline="")


@register_connector
class TelegramConnector(SaaSBaseConnector):
    """Telegram Bot API connector via DataBridge."""

    _connector_name = "telegram"
    _default_base_url = "https://api.telegram.org"
    _endpoints = {
        "me": "/getMe",
        "updates": "/getUpdates",
        "messages": "/sendMessage",
    }

    def connect(self, config: Dict[str, Any]) -> bool:
        """Connect using bot token from config or env."""
        try:
            from dotenv import load_dotenv

            load_dotenv(BASE_DIR / ".env")
        except ImportError:
            pass

        token = config.get(
            "api_key",
            os.getenv("TELEGRAM_BOT_TOKEN", ""),
        )
        if not token:
            logger.error("No Telegram bot token provided")
            return False

        # Telegram embeds token in URL path, not headers
        config["base_url"] = f"https://api.telegram.org/bot{token}"
        config["api_key"] = token
        self._chat_id = config.get(
            "chat_id",
            os.getenv("TELEGRAM_CHAT_ID", ""),
        )
        return super().connect(config)

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        # Telegram uses URL-embedded token, no auth headers
        return {}

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
        """Read from Telegram API.

        Tables:
            me      — Bot identity
            updates — Poll for new messages (tracks offset)
        """
        import time

        start = time.time()
        table = request.table_name

        if table == "me":
            data = self._http_get(f"{self._base_url}/getMe")
            if data and data.get("ok"):
                return ConnectorResponse(
                    status="success",
                    data=[data["result"]],
                    row_count=1,
                    duration_ms=int((time.time() - start) * 1000),
                )
            return ConnectorResponse(
                status="error",
                errors=[str(data)],
                duration_ms=int((time.time() - start) * 1000),
            )

        if table == "updates":
            offset = _load_offset()
            params = {
                "timeout": 0,
                "allowed_updates": ["message"],
            }
            if offset:
                params["offset"] = offset + 1
            if request.limit:
                params["limit"] = request.limit

            data = self._http_post(f"{self._base_url}/getUpdates", params)
            if not data or not data.get("ok"):
                return ConnectorResponse(
                    status="error",
                    errors=[str(data)],
                    duration_ms=int((time.time() - start) * 1000),
                )

            results = data.get("result", [])
            messages = []
            for update in results:
                uid = update.get("update_id", 0)
                msg = update.get("message", {})
                if msg:
                    messages.append(
                        {
                            "update_id": uid,
                            "chat_id": msg.get("chat", {}).get("id"),
                            "text": msg.get("text", ""),
                            "from": msg.get("from", {}).get("first_name", ""),
                            "date": msg.get("date"),
                        }
                    )
                _save_offset(uid)

            return ConnectorResponse(
                status="success",
                data=messages,
                row_count=len(messages),
                duration_ms=int((time.time() - start) * 1000),
            )

        return ConnectorResponse(
            status="error",
            errors=[f"Unknown table: {table}"],
        )

    def write(
        self,
        request: ConnectorRequest,
        data: Any,
    ) -> ConnectorResponse:
        """Send a message via Telegram.

        data should be a dict with:
            text     — Message text (required)
            chat_id  — Target chat (optional, uses default)
            parse_mode — HTML or Markdown (default: HTML)
        """
        import time

        start = time.time()

        if not isinstance(data, dict):
            return ConnectorResponse(
                status="error",
                errors=["data must be a dict with 'text'"],
            )

        text = data.get("text", "")
        if not text:
            return ConnectorResponse(
                status="error",
                errors=["'text' is required"],
            )

        chat_id = data.get("chat_id", self._chat_id)
        if not chat_id:
            return ConnectorResponse(
                status="error",
                errors=["No chat_id provided or configured"],
            )

        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": data.get("parse_mode", "HTML"),
            "disable_web_page_preview": True,
        }

        resp = self._http_post(f"{self._base_url}/sendMessage", payload)
        if resp and resp.get("ok"):
            msg_id = resp.get("result", {}).get("message_id")
            return ConnectorResponse(
                status="success",
                data=[{"message_id": msg_id}],
                row_count=1,
                duration_ms=int((time.time() - start) * 1000),
            )

        return ConnectorResponse(
            status="error",
            errors=[str(resp)],
            duration_ms=int((time.time() - start) * 1000),
        )

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        if table_name == "updates":
            return SchemaDefinition(
                fields=[
                    SchemaField("update_id", "integer"),
                    SchemaField("chat_id", "integer"),
                    SchemaField("text", "string"),
                    SchemaField("from", "string"),
                    SchemaField("date", "integer"),
                ]
            )
        if table_name == "messages":
            return SchemaDefinition(
                fields=[
                    SchemaField("chat_id", "integer"),
                    SchemaField("text", "string"),
                    SchemaField("parse_mode", "string"),
                    SchemaField("message_id", "integer"),
                ]
            )
        return SchemaDefinition(fields=[])

    def list_tables(self) -> List[str]:
        return list(self._endpoints.keys())


# ── CLI ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Telegram DataBridge Connector")
    parser.add_argument(
        "--health",
        action="store_true",
        help="Check bot connection",
    )
    parser.add_argument(
        "--read",
        metavar="TABLE",
        help="Read from table (me, updates)",
    )
    parser.add_argument(
        "--send",
        metavar="TEXT",
        help="Send a message",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    conn = TelegramConnector()
    connected = conn.connect({})

    if args.health:
        health = conn.health_check()
        if args.json:
            print(json.dumps(health, indent=2))
        else:
            print(f"Status: {health.get('status', '?')}")

    elif args.read:
        req = ConnectorRequest(table_name=args.read, limit=args.limit)
        resp = conn.read(req)
        if args.json:
            print(
                json.dumps(
                    {
                        "status": resp.status,
                        "data": resp.data,
                        "row_count": resp.row_count,
                        "duration_ms": resp.duration_ms,
                    },
                    indent=2,
                    default=str,
                )
            )
        else:
            for row in resp.data:
                print(row)

    elif args.send:
        req = ConnectorRequest(table_name="messages")
        resp = conn.write(req, {"text": args.send})
        if args.json:
            print(
                json.dumps(
                    {
                        "status": resp.status,
                        "data": resp.data,
                    },
                    indent=2,
                )
            )
        else:
            print(f"Sent: {resp.status}")
