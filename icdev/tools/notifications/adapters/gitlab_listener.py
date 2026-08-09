#!/usr/bin/env python3
# CUI // SP-CTI
"""GitLab Listener — polls gitlab_inbox / GitLabConnector for !icdev notes.

Polls issue notes incrementally. Only processes notes whose body starts with
the !icdev prefix. Replies by posting a new note on the same issue.

Usage:
    python tools/notifications/adapters/gitlab_listener.py --poll
    python tools/notifications/adapters/gitlab_listener.py --health [--json]
    python tools/notifications/adapters/gitlab_listener.py --inbox [--json]
    python tools/notifications/adapters/gitlab_listener.py --replay [--json]

Env vars:
    GITLAB_TOKEN, GITLAB_URL, GITLAB_PROJECT_ID,
    GITLAB_ALLOWED_USERNAME (optional)
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

_PLATFORM = "GitLab"
_INBOX_TABLE = "gitlab_inbox"
_PK = "note_id"
_ICDEV_PREFIX = "!icdev "
_OFFSET_FILE = BASE_DIR / ".tmp" / "gitlab_offset.txt"


def _load_config() -> Dict[str, str]:
    _load_env()
    return {
        "token": os.environ.get("GITLAB_TOKEN", ""),
        "base_url": os.environ.get("GITLAB_URL", "https://gitlab.com"),
        "project_id": os.environ.get("GITLAB_PROJECT_ID", ""),
        "allowed_username": os.environ.get("GITLAB_ALLOWED_USERNAME", ""),
    }


def _load_offset() -> str:
    try:
        if _OFFSET_FILE.exists():
            return _OFFSET_FILE.read_text().strip()
    except Exception:
        pass
    return ""


def _save_offset(iso: str) -> None:
    _OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    _OFFSET_FILE.write_text(iso, encoding="utf-8", newline="")


def _reply(issue_iid: int, text: str) -> None:
    try:
        from tools.databridge.connectors.gitlab_connector import GitLabConnector
        from tools.databridge.connector import ConnectorRequest
        c = GitLabConnector()
        if c.connect({}):
            c.write(
                ConnectorRequest(table_name="send_note"),
                {"body": text, "issue_iid": issue_iid},
            )
    except Exception:
        pass


def _write_to_inbox(note_id: int, note_json: Dict, issue_iid: int,
                    author_username: str, text: str) -> None:
    from tools.db.storage import get_connection
    conn = get_connection()
    try:
        conn.execute(
            f"INSERT OR IGNORE INTO {_INBOX_TABLE} "
            "(note_id, message_json, issue_iid, author_username, text, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (note_id, json.dumps(note_json), issue_iid, author_username,
             text[:500], _utcnow_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _process_inbox() -> Tuple[List[int], List[int]]:
    from tools.db.storage import get_connection
    config = _load_config()
    conn = get_connection()
    processed: List[int] = []
    failed: List[int] = []
    try:
        rows = conn.execute(
            f"SELECT {_PK}, message_json, author_username FROM {_INBOX_TABLE} "
            f"WHERE processed_at IS NULL ORDER BY created_at"
        ).fetchall()
        for row in rows:
            pk_val = row[_PK]
            try:
                msg = json.loads(row["message_json"])
                body = (msg.get("body") or "").strip()
                if body.lower().startswith(_ICDEV_PREFIX.lower()):
                    body = body[len(_ICDEV_PREFIX):].strip()
                username = row["author_username"] or ""
                reply = process_command(body, username, config["allowed_username"], _PLATFORM)
            except Exception as exc:
                conn.execute(
                    f"UPDATE {_INBOX_TABLE} SET error=%s WHERE {_PK}=%s", (str(exc), pk_val)
                )
                conn.commit()
                failed.append(pk_val)
                continue

            if reply:
                issue_iid = msg.get("issue_iid", 0)
                if issue_iid:
                    _reply(issue_iid, reply)

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
    """Poll GitLab issue notes for !icdev commands."""
    offset_iso = _load_offset()
    new_max_iso = offset_iso

    try:
        from tools.databridge.connectors.gitlab_connector import GitLabConnector
        from tools.databridge.connector import ConnectorRequest
        c = GitLabConnector()
        if c.connect({}):
            result = c.read(
                ConnectorRequest(table_name="issues"),
                incremental_value=offset_iso or None,
            )
            for note in (result.data or []):
                body = (note.get("body") or "").strip()
                if not body.lower().startswith(_ICDEV_PREFIX.lower()):
                    continue
                note_id = note.get("id", 0)
                author = (note.get("author") or {}).get("username", "")
                issue_iid = note.get("issue_iid", 0)
                updated_at = note.get("updated_at", "")
                _write_to_inbox(note_id, {**note, "issue_iid": issue_iid},
                                issue_iid, author, body)
                if updated_at > new_max_iso:
                    new_max_iso = updated_at
    except Exception:
        pass

    processed, failed = _process_inbox()
    if new_max_iso and new_max_iso != offset_iso:
        _save_offset(new_max_iso)
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
        ok = bool(cfg["token"] and cfg["project_id"])
        result = {"platform": _PLATFORM, "configured": ok, "status": "ok" if ok else "unconfigured"}
        print(json.dumps(result, indent=2) if args.json else f"{_PLATFORM}: {'OK' if ok else 'unconfigured'}")
    elif args.inbox:
        msgs = _get_unprocessed()
        if args.json:
            print(json.dumps({"count": len(msgs), "messages": msgs}, indent=2))
        else:
            print(f"Unprocessed {_PLATFORM} notes: {len(msgs)}")
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
            print(f"Processed {len(results)} {_PLATFORM} notes")
    else:
        parser.print_help()
