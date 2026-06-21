#!/usr/bin/env python3
# CUI // SP-CTI
"""Skype Listener — drains skype_inbox and creates Kanban tasks.

Skype has no polling API. Messages arrive via the /gateway/skype webhook
(SkypeAdapter), which writes Bot Framework Activities to the local skype_inbox
DB table. This listener drains that inbox exactly like telegram_listener.py
drains telegram_inbox.

Usage:
    python tools/notifications/adapters/skype_listener.py --poll
    python tools/notifications/adapters/skype_listener.py --health [--json]
    python tools/notifications/adapters/skype_listener.py --inbox [--json]
    python tools/notifications/adapters/skype_listener.py --replay [--json]

Env vars:
    SKYPE_APP_ID, SKYPE_APP_SECRET,
    SKYPE_ALLOWED_SENDER_ID (optional; restrict to one user)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.notifications.adapters.listener_base import (  # noqa: E402
    _utcnow_iso,
    process_command,
    _load_env,
)

_PLATFORM = "Skype"
_INBOX_TABLE = "skype_inbox"
_PK = "activity_id"


def _load_config() -> Dict[str, str]:
    _load_env()
    return {
        "app_id": os.environ.get("SKYPE_APP_ID", ""),
        "app_secret": os.environ.get("SKYPE_APP_SECRET", ""),
        "allowed_sender": os.environ.get("SKYPE_ALLOWED_SENDER_ID", ""),
    }


def _reply(conversation_id: str, service_url: str, text: str) -> None:
    try:
        from tools.databridge.connectors.skype_connector import SkypeConnector
        from tools.databridge.connector import ConnectorRequest
        c = SkypeConnector()
        if c.connect({}):
            c.write(
                ConnectorRequest(table_name="send_message"),
                {"text": text, "conversation_id": conversation_id, "service_url": service_url},
            )
    except Exception:
        pass


def _process_inbox() -> Tuple[List[str], List[str]]:
    from tools.db.storage import get_connection
    config = _load_config()
    conn = get_connection()
    processed: List[str] = []
    failed: List[str] = []
    try:
        rows = conn.execute(
            f"SELECT {_PK}, message_json, sender_id FROM {_INBOX_TABLE} "
            f"WHERE processed_at IS NULL ORDER BY created_at"
        ).fetchall()
        for row in rows:
            pk_val = row[_PK]
            try:
                msg = json.loads(row["message_json"])
                text = (msg.get("text") or "").strip()
                sender_id = row["sender_id"] or ""
                reply = process_command(text, sender_id, config["allowed_sender"], _PLATFORM)
            except Exception as exc:
                conn.execute(
                    f"UPDATE {_INBOX_TABLE} SET error=%s WHERE {_PK}=%s", (str(exc), pk_val)
                )
                conn.commit()
                failed.append(pk_val)
                continue

            if reply:
                conversation_id = (msg.get("conversation") or {}).get("id", "")
                service_url = msg.get("serviceUrl", "").rstrip("/")
                if conversation_id:
                    _reply(conversation_id, service_url, reply)

            conn.execute(
                f"UPDATE {_INBOX_TABLE} SET processed_at=%s, error=NULL WHERE {_PK}=%s",
                (_utcnow_iso(), pk_val),
            )
            conn.commit()
            processed.append(pk_val)
    finally:
        conn.close()
    return processed, failed


def poll_updates() -> List[Dict[str, Any]]:
    """Drain skype_inbox and dispatch commands (no external API polling)."""
    processed, failed = _process_inbox()
    return [{"id": pk, "status": "processed"} for pk in processed]


def replay_inbox() -> Dict[str, Any]:
    processed, failed = _process_inbox()
    return {"replayed": len(processed), "failed": len(failed)}


def _get_unprocessed() -> List[Dict]:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT {_PK}, text, error, created_at FROM {_INBOX_TABLE} "
            f"WHERE processed_at IS NULL ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=f"{_PLATFORM} Listener")
    parser.add_argument("--poll", action="store_true")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--inbox", action="store_true")
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.health:
        cfg = _load_config()
        ok = bool(cfg["app_id"] and cfg["app_secret"])
        result = {"platform": _PLATFORM, "configured": ok, "status": "ok" if ok else "unconfigured"}
        print(json.dumps(result, indent=2) if args.json else f"{_PLATFORM}: {'OK' if ok else 'unconfigured'}")
    elif args.inbox:
        msgs = _get_unprocessed()
        if args.json:
            print(json.dumps({"count": len(msgs), "messages": msgs}, indent=2))
        else:
            print(f"Unprocessed {_PLATFORM} activities: {len(msgs)}")
            for m in msgs:
                print(f"  [{m[_PK]}] {(m.get('text') or '')[:60]}")
    elif args.replay:
        result = replay_inbox()
        print(json.dumps(result, indent=2) if args.json else
              f"Replayed {result['replayed']}, failed {result['failed']}")
    elif args.poll:
        results = poll_updates()
        if args.json:
            print(json.dumps({"processed": len(results), "results": results}, indent=2))
        else:
            print(f"Processed {len(results)} {_PLATFORM} activities")
    else:
        parser.print_help()
