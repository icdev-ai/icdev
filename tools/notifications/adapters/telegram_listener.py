# CUI // SP-CTI
"""Telegram Bot Listener — receives commands and creates Kanban tasks.

Polls Telegram getUpdates API for new messages, parses commands,
and creates tasks on the Kanban board via the kanban_tasks table.

Commands:
    /build <description>     — Create a build task (high priority)
    /run <description>       — Create a run task
    /fix <description>       — Create a fix task (high priority)
    /research <description>  — Create a research task (low priority)
    /deploy <description>    — Create a deploy task (high priority)
    /test <description>      — Create a test task
    /chore <description>     — Create a chore task
    /task <description>      — Generic task (medium priority)
    /status                  — Show board summary
    /list                    — List pending/scheduled tasks
    /help                    — Show available commands

Usage:
    python tools/notifications/adapters/telegram_listener.py --poll
    python tools/notifications/adapters/telegram_listener.py --health
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Track last processed update to avoid duplicates
OFFSET_FILE = BASE_DIR / ".tmp" / "telegram_offset.txt"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_env():
    """Load .env if not already loaded."""
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        pass


def _get_token() -> str:
    _load_env()
    return os.getenv("TELEGRAM_BOT_TOKEN", "")


def _get_chat_id() -> str:
    _load_env()
    return os.getenv("TELEGRAM_CHAT_ID", "")


def _api_call(
    method: str, params: Optional[Dict] = None
) -> Dict[str, Any]:
    """Call Telegram Bot API."""
    token = _get_token()
    if not token:
        return {"ok": False, "error": "No TELEGRAM_BOT_TOKEN"}

    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        data = json.dumps(params).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    else:
        req = urllib.request.Request(url)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 — Telegram Bot API only
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _reply(chat_id: int, text: str):
    """Send a reply message."""
    _api_call("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def _load_offset() -> int:
    """Load last processed update_id."""
    try:
        if OFFSET_FILE.exists():
            return int(OFFSET_FILE.read_text().strip())
    except Exception:
        pass
    return 0


def _save_offset(offset: int):
    """Save last processed update_id."""
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(offset), encoding="utf-8")


# ── Command Definitions ──────────────────────────────────────────

# Maps /command to (task_type, default_priority)
COMMAND_MAP = {
    "/build": ("build", "high"),
    "/run": ("run", "medium"),
    "/fix": ("fix", "high"),
    "/research": ("research", "low"),
    "/deploy": ("deploy", "high"),
    "/test": ("test", "medium"),
    "/chore": ("chore", "medium"),
    "/task": ("build", "medium"),
}

HELP_TEXT = (
    "\U0001F4CB <b>ICDEV\u2122 Task Bot Commands</b>\n\n"
    "/build &lt;description&gt; \u2014 Build task (high)\n"
    "/run &lt;description&gt; \u2014 Run task\n"
    "/fix &lt;description&gt; \u2014 Fix task (high)\n"
    "/research &lt;description&gt; \u2014 Research task (low)\n"
    "/deploy &lt;description&gt; \u2014 Deploy task (high)\n"
    "/test &lt;description&gt; \u2014 Test task\n"
    "/chore &lt;description&gt; \u2014 Chore task\n"
    "/task &lt;description&gt; \u2014 Generic task\n"
    "/status \u2014 Board summary\n"
    "/list \u2014 Pending/scheduled tasks\n"
    "/help \u2014 This message\n\n"
    "Add <code>!critical</code> or <code>!low</code> to "
    "override priority.\n"
    "Add <code>@5pm</code> or <code>@tomorrow 9am</code> "
    "to schedule."
)


def _create_task(
    title: str,
    task_type: str,
    priority: str,
    description: str = "",
    scheduled_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a task in the kanban_tasks table."""
    from tools.db.storage import get_connection

    task_id = f"task-{uuid.uuid4().hex[:10]}"
    now = _utcnow_iso()
    status = "scheduled" if scheduled_at else "backlog"

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO kanban_tasks "
            "(id, title, description, task_type, priority, "
            "status, scheduled_at, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                task_id, title, description, task_type,
                priority, status, scheduled_at, now, now,
            ),
        )
        conn.commit()
        return {"id": task_id, "status": status}
    finally:
        conn.close()


def _get_board_summary() -> str:
    """Get Kanban board summary."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt "
            "FROM kanban_tasks GROUP BY status"
        ).fetchall()
        counts = {r["status"]: r["cnt"] for r in rows}
        total = sum(counts.values())
        return (
            "\U0001F4CA <b>Task Board</b>\n\n"
            f"\U0001F4E5 Backlog: {counts.get('backlog', 0)}\n"
            f"\U0001F4C5 Scheduled: "
            f"{counts.get('scheduled', 0)}\n"
            f"\u25B6 In Progress: "
            f"{counts.get('in_progress', 0)}\n"
            f"\u2705 Done: {counts.get('done', 0)}\n"
            f"\n<b>Total: {total}</b>"
        )
    finally:
        conn.close()


def _get_task_list() -> str:
    """List pending and scheduled tasks."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, title, task_type, priority, status, "
            "scheduled_at FROM kanban_tasks "
            "WHERE status IN ('backlog', 'scheduled', "
            "'in_progress') "
            "ORDER BY CASE priority "
            "WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
            "WHEN 'medium' THEN 2 ELSE 3 END, "
            "created_at DESC LIMIT 15"
        ).fetchall()
        if not rows:
            return "\u2705 No pending tasks!"

        icons = {
            "build": "\U0001F528", "run": "\u25B6",
            "fix": "\U0001F41B", "research": "\U0001F50D",
            "deploy": "\U0001F680", "test": "\u2705",
            "chore": "\U0001F9F9",
        }
        status_icons = {
            "backlog": "\U0001F4E5",
            "scheduled": "\U0001F4C5",
            "in_progress": "\u25B6",
        }
        lines = ["\U0001F4CB <b>Active Tasks</b>\n"]
        for r in rows:
            icon = icons.get(r["task_type"], "\u2022")
            si = status_icons.get(r["status"], "")
            sched = ""
            if r["scheduled_at"]:
                try:
                    dt = r["scheduled_at"]
                    if hasattr(dt, "strftime"):
                        sched = f" \u23F0 {dt.strftime('%b %d %I:%M%p')}"
                except Exception:
                    pass
            lines.append(
                f"{si} {icon} {r['title'][:50]}"
                f" <i>({r['priority']})</i>{sched}"
            )
        return "\n".join(lines)
    finally:
        conn.close()


def _parse_priority(text: str) -> Tuple[str, str]:
    """Extract priority override from text."""
    priority = None
    clean = text
    for tag, pri in [
        ("!critical", "critical"),
        ("!high", "high"),
        ("!medium", "medium"),
        ("!low", "low"),
    ]:
        if tag in text.lower():
            priority = pri
            clean = text.replace(tag, "").replace(
                tag.upper(), ""
            ).strip()
            break
    return clean, priority


def process_message(
    message: Dict[str, Any],
) -> Optional[str]:
    """Process a single Telegram message. Returns reply text."""
    text = (message.get("text") or "").strip()
    chat_id = message.get("chat", {}).get("id")
    allowed_chat = _get_chat_id()

    # Security: only respond to the configured chat
    if allowed_chat and str(chat_id) != str(allowed_chat):
        return None

    if text.startswith("/"):
        parts = text.split(None, 1)
        cmd = parts[0].lower().split("@")[0]
        body = parts[1] if len(parts) > 1 else ""

        # Help
        if cmd in ("/help", "/start"):
            return HELP_TEXT

        # Status
        if cmd == "/status":
            return _get_board_summary()

        # List
        if cmd == "/list":
            return _get_task_list()

        # Task creation commands
        if cmd in COMMAND_MAP:
            if not body.strip():
                return (
                    f"\u26A0 Please provide a description.\n"
                    f"Example: <code>{cmd} Fix login "
                    f"timeout</code>"
                )

            task_type, default_priority = COMMAND_MAP[cmd]
            body, priority_override = _parse_priority(body)
            priority = priority_override or default_priority

            result = _create_task(
                title=body.strip(),
                task_type=task_type,
                priority=priority,
                description=f"Created via Telegram: {text}",
            )

            status_label = (
                "Scheduled"
                if result["status"] == "scheduled"
                else "Added to Backlog"
            )
            return (
                f"\u2705 <b>Task Created</b>\n\n"
                f"\U0001F4DD {body.strip()}\n"
                f"Type: {task_type} | Priority: {priority}\n"
                f"Status: {status_label}\n"
                f"ID: <code>{result['id']}</code>"
            )

        return (
            f"\u2753 Unknown command: {cmd}\n"
            f"Send /help for options."
        )

    # Free-text instruction (not a slash command)
    # Create as a task with the full text as title/description
    result = _create_task(
        title=text[:80],
        task_type="chore",
        priority="medium",
        description=f"Instruction via Telegram: {text}",
    )
    return (
        f"\u2705 <b>Task Created from Instruction</b>\n\n"
        f"\U0001F4DD {text[:80]}\n"
        f"Type: chore | Priority: medium\n"
        f"Status: Added to Backlog\n"
        f"ID: <code>{result['id']}</code>\n\n"
        f"<i>Tip: Use /build, /fix, /run for specific "
        f"task types</i>"
    )


def poll_updates() -> List[Dict[str, Any]]:
    """Poll for new messages and process them."""
    offset = _load_offset()
    params = {"timeout": 0, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = offset + 1

    data = _api_call("getUpdates", params)
    if not data.get("ok"):
        return []

    results = []
    for update in data.get("result", []):
        update_id = update.get("update_id", 0)
        message = update.get("message")
        if not message:
            _save_offset(update_id)
            continue

        chat_id = message.get("chat", {}).get("id")
        try:
            reply = process_message(message)
        except Exception as exc:
            # DB down or task creation failed — do NOT save offset
            # so the message will be retried on the next poll cycle.
            if chat_id:
                _reply(
                    chat_id,
                    f"\u26a0\ufe0f Task creation failed (will retry): "
                    f"{type(exc).__name__}: {exc}",
                )
            continue

        if reply and chat_id:
            _reply(chat_id, reply)
            results.append({
                "update_id": update_id,
                "text": message.get("text", ""),
                "reply": reply[:100],
            })

        _save_offset(update_id)

    return results


# ── CLI ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Telegram Bot Listener"
    )
    parser.add_argument(
        "--poll", action="store_true",
        help="Poll once for new messages",
    )
    parser.add_argument(
        "--health", action="store_true",
        help="Check bot health",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.health:
        data = _api_call("getMe")
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            if data.get("ok"):
                bot = data["result"]
                print(
                    f"Bot: @{bot['username']} "
                    f"({bot['first_name']})"
                )
            else:
                print(f"Error: {data}")

    elif args.poll:
        results = poll_updates()
        if args.json:
            print(json.dumps(
                {"processed": len(results), "results": results},
                indent=2,
            ))
        else:
            print(f"Processed {len(results)} messages")
            for r in results:
                print(f"  {r['text'][:60]} -> {r['reply']}")
    else:
        parser.print_help()
