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
    /approve [step_run_id]   — Approve a pending HITL workflow gate
    /reject [step_run_id] [reason] — Reject a pending HITL workflow gate

    Plain text "approve" / "approved" — approve the single pending gate
    Plain text "reject <reason>"      — reject the single pending gate

Usage:
    python tools/notifications/adapters/telegram_listener.py --poll
    python tools/notifications/adapters/telegram_listener.py --health
    python tools/notifications/adapters/telegram_listener.py --inbox
    python tools/notifications/adapters/telegram_listener.py --replay
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

# Dashboard base URL for HITL API calls
_DASHBOARD_URL = "http://localhost:5050"


# ── HITL helpers ──────────────────────────────────────────────────

def _get_pending_hitl_steps() -> list:
    """Return list of pending HITL steps with status='awaiting_approval'."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT r.run_id, s.step_run_id, s.step_name, r.workflow_name "
                "FROM studio_workflow_run_steps s "
                "JOIN studio_workflow_runs r ON s.run_id = r.run_id "
                "WHERE s.status = 'awaiting_approval' "
                "ORDER BY s.started_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception:
        return []


def _write_hitl_decision_to_db(step_run_id: str, action: str, reason: str = "") -> bool:
    """Write approval/rejection directly to DB — works from any process.

    The workflow_runner polls DB every 10s and picks this up automatically.
    Also attempts the HTTP API for fast in-process signaling (best-effort).
    """
    try:
        from tools.db.storage import get_connection
        actor_msg = f"{'Approved' if action == 'approve' else 'Rejected'} via Telegram"
        if reason:
            actor_msg += f": {reason}"
        new_status = "approved" if action == "approve" else "rejected"
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE studio_workflow_run_steps SET status=%s, stderr=%s, completed_at=%s "
                "WHERE step_run_id=%s AND status='awaiting_approval'",
                (new_status, actor_msg, datetime.now(timezone.utc).isoformat(), step_run_id),
            )
            conn.commit()
            updated = conn.execute(
                "SELECT status FROM studio_workflow_run_steps WHERE step_run_id=%s",
                (step_run_id,),
            ).fetchone()
            released = bool(updated and updated["status"] == new_status)
        finally:
            conn.close()

        # This path writes the gate decision straight to the DB rather than
        # going through workflow_runner.approve_step, so it must close out the
        # mirrored workflow_hitl external step itself — otherwise a Telegram
        # approval leaves an orphan in the reviewer inbox (dwo-dur-04).
        if released:
            try:
                from tools.studio import gate_bridge
                gate_bridge.complete_external_step(step_run_id, new_status, "telegram")
            except Exception:
                pass
        return released
    except Exception:
        return False


def _try_hitl_api(run_id: str, step_run_id: str, action: str, reason: str = "") -> None:
    """Best-effort HTTP call to signal the in-process threading.Event for instant response."""
    try:
        payload = json.dumps({"actor": "telegram", "reason": reason}).encode()
        url = f"{_DASHBOARD_URL}/api/studio/workflows/runs/{run_id}/steps/{step_run_id}/{action}"
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=5)  # nosec B310 — localhost only
    except Exception:
        pass  # DB write already handled it; this is just for speed


def _handle_hitl_action(action: str, step_run_id: str = "", reason: str = "") -> str:
    """Approve or reject a pending HITL gate. Returns reply text.

    Works from any process — writes to DB directly; the workflow_runner
    polls DB every 10s and also receives an optional fast HTTP signal.
    """
    pending = _get_pending_hitl_steps()

    if not pending:
        return "⚠️ No workflow gates are currently awaiting approval."

    # Resolve target step
    if step_run_id:
        targets = [p for p in pending if p["step_run_id"] == step_run_id]
        if not targets:
            return f"⚠️ Step <code>{step_run_id}</code> not found or not pending."
        target = targets[0]
    elif len(pending) == 1:
        target = pending[0]
    else:
        lines = ["⚠️ Multiple gates pending — specify step ID:\n"]
        for p in pending:
            lines.append(
                f"• <b>{p['step_name']}</b> in <code>{p['run_id']}</code>\n"
                f"  ID: <code>{p['step_run_id']}</code>"
            )
        lines.append(f"\nUse: /{action} &lt;step_run_id&gt;")
        return "\n".join(lines)

    # Primary: write decision to DB (process-independent)
    ok = _write_hitl_decision_to_db(target["step_run_id"], action, reason)
    if not ok:
        return f"❌ Failed to write {action} decision to DB — step may no longer be pending."

    # Secondary: also signal in-process Event for instant response (best-effort)
    _try_hitl_api(target["run_id"], target["step_run_id"], action, reason)

    icon = "✅" if action == "approve" else "❌"
    verb = "Approved" if action == "approve" else "Rejected"
    msg = f"{icon} <b>{verb}</b>\n\n"
    msg += f"Step: <b>{target['step_name']}</b>\n"
    msg += f"Workflow: {target.get('workflow_name', target['run_id'])}\n"
    if reason:
        msg += f"Reason: {reason}\n"
    msg += f"\nRun <code>{target['run_id']}</code> will continue within ~10s."
    return msg


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


def _api_call(method: str, params: Optional[Dict] = None) -> Dict[str, Any]:
    """Call Telegram Bot API."""
    token = _get_token()
    if not token:
        return {"ok": False, "error": "No TELEGRAM_BOT_TOKEN"}

    url = f"https://api.telegram.org/bot{token}/{method}"
    if params:
        data = json.dumps(params).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
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
    _api_call(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )


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
    OFFSET_FILE.write_text(str(offset), encoding="utf-8", newline="")


# ── Inbox helpers ───────────────────────────────────────────────

def _write_to_inbox(update_id: int, message: Dict[str, Any]) -> None:
    """Persist a Telegram update to the local inbox table (dedup by update_id)."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO telegram_inbox (update_id, message_json, chat_id, text, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                update_id,
                json.dumps(message),
                str(message.get("chat", {}).get("id") or ""),
                message.get("text", "")[:500],
                _utcnow_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _process_inbox() -> Tuple[List[int], List[int]]:
    """Process all unprocessed inbox messages. Returns (processed_ids, failed_ids)."""
    from tools.db.storage import get_connection

    conn = get_connection()
    processed: List[int] = []
    failed: List[int] = []
    try:
        rows = conn.execute(
            "SELECT update_id, message_json FROM telegram_inbox "
            "WHERE processed_at IS NULL ORDER BY update_id"
        ).fetchall()

        for row in rows:
            update_id = row["update_id"]
            message = json.loads(row["message_json"])
            chat_id = message.get("chat", {}).get("id")
            try:
                reply = process_message(message)
            except Exception as exc:
                error_text = f"{type(exc).__name__}: {exc}"
                conn.execute(
                    "UPDATE telegram_inbox SET error = %s WHERE update_id = %s",
                    (error_text, update_id),
                )
                conn.commit()
                failed.append(update_id)
                if chat_id:
                    _reply(chat_id, f"⚠️ Task creation failed: {error_text}")
                continue

            if reply and chat_id:
                _reply(chat_id, reply)

            conn.execute(
                "UPDATE telegram_inbox SET processed_at = %s, error = NULL WHERE update_id = %s",
                (_utcnow_iso(), update_id),
            )
            conn.commit()
            processed.append(update_id)
    finally:
        conn.close()

    return processed, failed


def replay_inbox() -> Dict[str, Any]:
    """Replay all unprocessed inbox messages. Called by scheduler on startup."""
    processed, failed = _process_inbox()
    if processed or failed:
        max_processed = max(processed) if processed else 0
        if max_processed:
            _save_offset(max_processed)
    return {
        "replayed": len(processed),
        "failed": len(failed),
        "processed_ids": processed,
        "failed_ids": failed,
    }


def _inbox_count() -> int:
    """Count unprocessed inbox messages."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM telegram_inbox WHERE processed_at IS NULL"
        ).fetchone()
        return dict(row).get("cnt", 0)
    finally:
        conn.close()


def _get_unprocessed_messages() -> List[Dict[str, Any]]:
    """Return unprocessed inbox messages for inspection."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT update_id, text, error, created_at FROM telegram_inbox "
            "WHERE processed_at IS NULL ORDER BY update_id DESC LIMIT 20"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


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
    "\U0001f4cb <b>ICDEV™ Task Bot Commands</b>\n\n"
    "/build &lt;description&gt; — Build task (high)\n"
    "/run &lt;description&gt; — Run task\n"
    "/fix &lt;description&gt; — Fix task (high)\n"
    "/research &lt;description&gt; — Research task (low)\n"
    "/deploy &lt;description&gt; — Deploy task (high)\n"
    "/test &lt;description&gt; — Test task\n"
    "/chore &lt;description&gt; — Chore task\n"
    "/task &lt;description&gt; — Generic task\n"
    "/status — Board summary\n"
    "/list — Pending/scheduled tasks\n"
    "/approve [step_id] — Approve a workflow gate\n"
    "/reject [step_id] [reason] — Reject a workflow gate\n"
    "/help — This message\n\n"
    "<b>Workflow approvals:</b> Reply <code>approve</code> or "
    "<code>reject &lt;reason&gt;</code> to action a pending gate.\n\n"
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
                task_id,
                title,
                description,
                task_type,
                priority,
                status,
                scheduled_at,
                now,
                now,
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
        rows = conn.execute("SELECT status, COUNT(*) as cnt FROM kanban_tasks GROUP BY status").fetchall()
        counts = {r["status"]: r["cnt"] for r in rows}
        total = sum(counts.values())
        token_line = ""
        token_count = counts.get("token_exhausted", 0)
        if token_count:
            token_line = f"⏳ Token Exhausted (waiting): {token_count}\n"
        inbox = _inbox_count()
        inbox_line = f"📤 Inbox (unprocessed): {inbox}\n" if inbox else ""
        return (
            "\U0001f4ca <b>Task Board</b>\n\n"
            f"\U0001f4e5 Backlog: {counts.get('backlog', 0)}\n"
            f"\U0001f4c5 Scheduled: "
            f"{counts.get('scheduled', 0)}\n"
            f"▶ In Progress: "
            f"{counts.get('in_progress', 0)}\n"
            f"{token_line}"
            f"{inbox_line}"
            f"✅ Done: {counts.get('done', 0)}\n"
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
            "'in_progress', 'token_exhausted') "
            "ORDER BY CASE priority "
            "WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
            "WHEN 'medium' THEN 2 ELSE 3 END, "
            "created_at DESC LIMIT 15"
        ).fetchall()
        if not rows:
            return "✅ No pending tasks!"

        icons = {
            "build": "\U0001f528",
            "run": "▶",
            "fix": "\U0001f41b",
            "research": "\U0001f50d",
            "deploy": "\U0001f680",
            "test": "✅",
            "chore": "\U0001f9f9",
        }
        status_icons = {
            "backlog": "\U0001f4e5",
            "scheduled": "\U0001f4c5",
            "in_progress": "▶",
        }
        lines = ["\U0001f4cb <b>Active Tasks</b>\n"]
        for r in rows:
            icon = icons.get(r["task_type"], "•")
            si = status_icons.get(r["status"], "")
            sched = ""
            if r["scheduled_at"]:
                try:
                    dt = r["scheduled_at"]
                    if hasattr(dt, "strftime"):
                        sched = f" ⏰ {dt.strftime('%b %d %I:%M%p')}"
                except Exception:
                    pass
            lines.append(f"{si} {icon} {r['title'][:50]} <i>({r['priority']})</i>{sched}")
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
            clean = text.replace(tag, "").replace(tag.upper(), "").strip()
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

    # ── HITL plain-text shortcuts (checked before slash commands) ──
    text_lower = text.lower().strip()
    if text_lower in ("approve", "approved", "yes", "lgtm"):
        return _handle_hitl_action("approve")
    if text_lower.startswith("reject") or text_lower.startswith("denied") or text_lower.startswith("no "):
        reason = text[len(text_lower.split()[0]):].strip()
        return _handle_hitl_action("reject", reason=reason)

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

        # HITL approve
        if cmd == "/approve":
            args = body.strip().split(None, 1)
            step_run_id = args[0] if args else ""
            return _handle_hitl_action("approve", step_run_id=step_run_id)

        # HITL reject
        if cmd == "/reject":
            args = body.strip().split(None, 1)
            step_run_id = args[0] if args else ""
            reason = args[1] if len(args) > 1 else ""
            return _handle_hitl_action("reject", step_run_id=step_run_id, reason=reason)

        # Task creation commands
        if cmd in COMMAND_MAP:
            if not body.strip():
                return f"⚠ Please provide a description.\nExample: <code>{cmd} Fix login timeout</code>"

            task_type, default_priority = COMMAND_MAP[cmd]
            body, priority_override = _parse_priority(body)
            priority = priority_override or default_priority

            result = _create_task(
                title=body.strip(),
                task_type=task_type,
                priority=priority,
                description=f"Created via Telegram: {text}",
            )

            status_label = "Scheduled" if result["status"] == "scheduled" else "Added to Backlog"
            return (
                f"✅ <b>Task Created</b>\n\n"
                f"\U0001f4dd {body.strip()}\n"
                f"Type: {task_type} | Priority: {priority}\n"
                f"Status: {status_label}\n"
                f"ID: <code>{result['id']}</code>"
            )

        return f"❓ Unknown command: {cmd}\nSend /help for options."

    # Free-text instruction (not a slash command and not an approval keyword)
    # Create as a task with the full text as title/description
    result = _create_task(
        title=text[:80],
        task_type="chore",
        priority="medium",
        description=f"Instruction via Telegram: {text}",
    )
    return (
        f"✅ <b>Task Created from Instruction</b>\n\n"
        f"\U0001f4dd {text[:80]}\n"
        f"Type: chore | Priority: medium\n"
        f"Status: Added to Backlog\n"
        f"ID: <code>{result['id']}</code>\n\n"
        f"<i>Tip: Use /build, /fix, /run for specific "
        f"task types</i>"
    )


def poll_updates() -> List[Dict[str, Any]]:
    """Poll for new messages, write them to the durable inbox, then process.

    Receipt and processing are separated so that:
    - A crash mid-batch never loses messages (they're already in the DB).
    - A failed message does not cause Telegram to discard earlier messages
      in the same batch (offset is only advanced past successfully processed
      messages).
    """
    offset = _load_offset()
    params = {"timeout": 0, "allowed_updates": ["message"]}
    if offset:
        params["offset"] = offset + 1

    data = _api_call("getUpdates", params)
    if not data.get("ok"):
        return []

    # ── Phase 1: Receipt — write every update to inbox (dedup) ──
    receipt_ids: List[int] = []
    for update in data.get("result", []):
        update_id = update.get("update_id", 0)
        message = update.get("message")
        if message:
            _write_to_inbox(update_id, message)
        receipt_ids.append(update_id)

    if not receipt_ids:
        return []

    # ── Phase 2: Processing — idempotent replay from inbox ──
    processed, failed = _process_inbox()

    # ── Phase 3: Ack — advance offset only past successfully processed msgs ──
    if processed:
        max_processed = max(processed)
        _save_offset(max_processed)

    # Build result summary for callers
    results: List[Dict[str, Any]] = []
    conn = None
    try:
        from tools.db.storage import get_connection

        conn = get_connection()
        for uid in processed:
            row = conn.execute(
                "SELECT text FROM telegram_inbox WHERE update_id = %s", (uid,)
            ).fetchone()
            if row:
                results.append(
                    {
                        "update_id": uid,
                        "text": row["text"] or "",
                        "reply": "processed",
                    }
                )
    finally:
        if conn:
            conn.close()

    return results


# ── CLI ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Telegram Bot Listener")
    parser.add_argument(
        "--poll",
        action="store_true",
        help="Poll once for new messages",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Check bot health",
    )
    parser.add_argument(
        "--inbox",
        action="store_true",
        help="Show unprocessed inbox messages",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Replay all unprocessed inbox messages",
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
                print(f"Bot: @{bot['username']} ({bot['first_name']})")
            else:
                print(f"Error: {data}")

    elif args.inbox:
        messages = _get_unprocessed_messages()
        if args.json:
            print(json.dumps({"count": len(messages), "messages": messages}, indent=2))
        else:
            print(f"Unprocessed inbox messages: {len(messages)}")
            for m in messages:
                print(f"  [{m['update_id']}] {m['text'][:60]} — {m['created_at']}")

    elif args.replay:
        result = replay_inbox()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Replayed {result['replayed']} messages, {result['failed']} failed")

    elif args.poll:
        results = poll_updates()
        if args.json:
            print(
                json.dumps(
                    {"processed": len(results), "results": results},
                    indent=2,
                )
            )
        else:
            print(f"Processed {len(results)} messages")
            for r in results:
                print(f"  {r['text'][:60]} -> {r['reply']}")
    else:
        parser.print_help()
