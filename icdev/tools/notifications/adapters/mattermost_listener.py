#!/usr/bin/env python3
# CUI // SP-CTI
"""MatterMost Listener — polls mattermost_inbox / MattermostConnector and creates Kanban tasks.

Uses TeamsConnector.read("posts") for incremental polling (newest posts since
the last saved offset). Optionally drains the local mattermost_inbox table if
the gateway webhook is in use.

Usage:
    python tools/notifications/adapters/mattermost_listener.py --poll
    python tools/notifications/adapters/mattermost_listener.py --health [--json]
    python tools/notifications/adapters/mattermost_listener.py --inbox [--json]
    python tools/notifications/adapters/mattermost_listener.py --replay [--json]

Env vars:
    MATTERMOST_TOKEN, MATTERMOST_URL, MATTERMOST_CHANNEL_ID,
    MATTERMOST_ALLOWED_USER_ID
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

_PLATFORM = "MatterMost"
_INBOX_TABLE = "mattermost_inbox"
_PK = "post_id"
_OFFSET_FILE = BASE_DIR / ".tmp" / "mattermost_offset.txt"


def _load_config() -> Dict[str, str]:
    _load_env()
    return {
        "token": os.environ.get("MATTERMOST_TOKEN", ""),
        "base_url": os.environ.get("MATTERMOST_URL", ""),
        "channel_id": os.environ.get("MATTERMOST_CHANNEL_ID", ""),
        "allowed_user": os.environ.get("MATTERMOST_ALLOWED_USER_ID", ""),
    }


def _load_offset() -> int:
    try:
        if _OFFSET_FILE.exists():
            return int(_OFFSET_FILE.read_text().strip())
    except Exception:
        pass
    return 0


def _save_offset(ms: int) -> None:
    _OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OFFSET_FILE.write_text(str(ms), encoding="utf-8", newline="")


def _reply(channel_id: str, text: str) -> None:
    try:
        from tools.databridge.connectors.mattermost_connector import MattermostConnector
        from tools.databridge.connector import ConnectorRequest
        c = MattermostConnector()
        if c.connect({}):
            c.write(ConnectorRequest(table_name="send_post"), {"message": text, "channel_id": channel_id})
    except Exception:
        pass


def _write_to_inbox(post_id: str, post_json: Dict, channel_id: str,
                    user_id: str, text: str) -> None:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        conn.execute(
            f"INSERT OR IGNORE INTO {_INBOX_TABLE} "
            "(post_id, message_json, channel_id, user_id, text, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (post_id, json.dumps(post_json), channel_id, user_id, text[:500], _utcnow_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _process_inbox() -> Tuple[List[str], List[str]]:
    from tools.db.storage import get_connection
    config = _load_config()
    conn = get_connection()
    processed: List[str] = []
    failed: List[str] = []
    try:
        rows = conn.execute(
            f"SELECT {_PK}, message_json, user_id FROM {_INBOX_TABLE} "
            f"WHERE processed_at IS NULL ORDER BY created_at"
        ).fetchall()
        for row in rows:
            pk_val = row[_PK]
            try:
                msg = json.loads(row["message_json"])
                text = (msg.get("message") or "").strip()
                user_id = row["user_id"] or ""
                reply = process_command(text, user_id, config["allowed_user"], _PLATFORM)
            except Exception as exc:
                conn.execute(
                    f"UPDATE {_INBOX_TABLE} SET error=%s WHERE {_PK}=%s", (str(exc), pk_val)
                )
                conn.commit()
                failed.append(pk_val)
                continue

            if reply:
                channel_id = json.loads(row["message_json"]).get("channel_id", config["channel_id"])
                _reply(channel_id, reply)

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
    """Poll MatterMost for new posts and process commands."""
    config = _load_config()
    offset_ms = _load_offset()
    new_max_ms = offset_ms

    try:
        from tools.databridge.connectors.mattermost_connector import MattermostConnector
        from tools.databridge.connector import ConnectorRequest
        c = MattermostConnector()
        if c.connect({}):
            result = c.read(
                ConnectorRequest(table_name="posts"),
                incremental_value=str(offset_ms) if offset_ms else None,
            )
            for post in (result.data or []):
                post_id = post.get("id", "")
                text = (post.get("message") or "").strip()
                channel_id = post.get("channel_id", config["channel_id"])
                user_id = post.get("user_id", "")
                update_at = post.get("update_at", 0)
                if not text:
                    continue
                _write_to_inbox(post_id, post, channel_id, user_id, text)
                if update_at > new_max_ms:
                    new_max_ms = update_at
    except Exception:
        pass

    processed, failed = _process_inbox()
    if new_max_ms > offset_ms:
        _save_offset(new_max_ms)
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
        ok = bool(cfg["token"] and cfg["base_url"] and cfg["channel_id"])
        result = {"platform": _PLATFORM, "configured": ok, "status": "ok" if ok else "unconfigured"}
        print(json.dumps(result, indent=2) if args.json else f"{_PLATFORM}: {'OK' if ok else 'unconfigured'}")
    elif args.inbox:
        msgs = _get_unprocessed()
        if args.json:
            print(json.dumps({"count": len(msgs), "messages": msgs}, indent=2))
        else:
            print(f"Unprocessed {_PLATFORM} posts: {len(msgs)}")
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
            print(f"Processed {len(results)} {_PLATFORM} posts")
    else:
        parser.print_help()
