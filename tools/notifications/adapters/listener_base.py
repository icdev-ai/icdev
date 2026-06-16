# CUI // SP-CTI
"""Shared listener base — platform-agnostic command dispatch for all channel listeners.

Extracted from telegram_listener.py so all platform listeners (Teams, MatterMost,
GitHub, GitLab, Skype) reuse the same command parsing, task creation, and HITL
approval logic without copying.

Exported API:
    COMMAND_MAP         — /cmd → (task_type, priority)
    HELP_TEXT           — formatted help string
    process_command()   — main dispatch, returns reply text or None
    _create_task()      — insert into kanban_tasks
    _get_board_summary() — board status string
    _get_task_list()    — active tasks string
    _parse_priority()   — extract !critical / !high / !low tags
    _handle_hitl_action() — approve/reject pending workflow gates
    _utcnow_iso()       — UTC ISO timestamp
"""

from __future__ import annotations

import json
import sys
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

_DASHBOARD_URL = "http://localhost:5050"


# ── Utilities ────────────────────────────────────────────────────────

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env")
    except ImportError:
        pass


# ── Command Definitions ──────────────────────────────────────────────

COMMAND_MAP: Dict[str, Tuple[str, str]] = {
    "/build":    ("build",    "high"),
    "/run":      ("run",      "medium"),
    "/fix":      ("fix",      "high"),
    "/research": ("research", "low"),
    "/deploy":   ("deploy",   "high"),
    "/test":     ("test",     "medium"),
    "/chore":    ("chore",    "medium"),
    "/task":     ("build",    "medium"),
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
    "Add <code>!critical</code> or <code>!low</code> to override priority.\n"
    "Prefix commands with <code>!icdev </code> in GitHub/GitLab comments."
)


# ── DB helpers ───────────────────────────────────────────────────────

def _create_task(
    title: str,
    task_type: str,
    priority: str,
    description: str = "",
    scheduled_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert a row into kanban_tasks. Returns {"id": ..., "status": ...}."""
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
            (task_id, title, description, task_type, priority,
             status, scheduled_at, now, now),
        )
        conn.commit()
        return {"id": task_id, "status": status}
    finally:
        conn.close()


def _get_board_summary() -> str:
    """Return formatted Kanban board summary."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM kanban_tasks GROUP BY status"
        ).fetchall()
        counts = {r["status"]: r["cnt"] for r in rows}
        total = sum(counts.values())
        token_line = ""
        if counts.get("token_exhausted", 0):
            token_line = f"⏳ Token Exhausted: {counts['token_exhausted']}\n"
        return (
            "\U0001f4ca <b>Task Board</b>\n\n"
            f"\U0001f4e5 Backlog: {counts.get('backlog', 0)}\n"
            f"\U0001f4c5 Scheduled: {counts.get('scheduled', 0)}\n"
            f"▶ In Progress: {counts.get('in_progress', 0)}\n"
            f"{token_line}"
            f"✅ Done: {counts.get('done', 0)}\n"
            f"\n<b>Total: {total}</b>"
        )
    finally:
        conn.close()


def _get_task_list() -> str:
    """Return formatted list of active tasks."""
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, title, task_type, priority, status, scheduled_at "
            "FROM kanban_tasks "
            "WHERE status IN ('backlog','scheduled','in_progress','token_exhausted') "
            "ORDER BY CASE priority "
            "WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, "
            "created_at DESC LIMIT 15"
        ).fetchall()
        if not rows:
            return "✅ No pending tasks!"

        icons = {
            "build": "\U0001f528", "run": "▶", "fix": "\U0001f41b",
            "research": "\U0001f50d", "deploy": "\U0001f680",
            "test": "✅", "chore": "\U0001f9f9",
        }
        status_icons = {"backlog": "\U0001f4e5", "scheduled": "\U0001f4c5", "in_progress": "▶"}
        lines = ["\U0001f4cb <b>Active Tasks</b>\n"]
        for r in rows:
            icon = icons.get(r["task_type"], "•")
            si = status_icons.get(r["status"], "")
            lines.append(f"{si} {icon} {r['title'][:50]} <i>({r['priority']})</i>")
        return "\n".join(lines)
    finally:
        conn.close()


def _parse_priority(text: str) -> Tuple[str, Optional[str]]:
    """Extract !critical / !high / !medium / !low tag from text. Returns (cleaned_text, priority_or_None)."""
    for tag, pri in [("!critical", "critical"), ("!high", "high"), ("!medium", "medium"), ("!low", "low")]:
        if tag in text.lower():
            clean = text.replace(tag, "").replace(tag.upper(), "").strip()
            return clean, pri
    return text, None


# ── HITL helpers ─────────────────────────────────────────────────────

def _get_pending_hitl_steps() -> List[Dict[str, Any]]:
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
    try:
        from tools.db.storage import get_connection
        actor_msg = f"{'Approved' if action == 'approve' else 'Rejected'} via channel"
        if reason:
            actor_msg += f": {reason}"
        new_status = "approved" if action == "approve" else "rejected"
        conn = get_connection()
        try:
            conn.execute(
                "UPDATE studio_workflow_run_steps SET status=%s, stderr=%s, completed_at=%s "
                "WHERE step_run_id=%s AND status='awaiting_approval'",
                (new_status, actor_msg, _utcnow_iso(), step_run_id),
            )
            conn.commit()
            updated = conn.execute(
                "SELECT status FROM studio_workflow_run_steps WHERE step_run_id=%s",
                (step_run_id,),
            ).fetchone()
            return bool(updated and updated["status"] == new_status)
        finally:
            conn.close()
    except Exception:
        return False


def _try_hitl_api(run_id: str, step_run_id: str, action: str, reason: str = "") -> None:
    try:
        payload = json.dumps({"actor": "channel", "reason": reason}).encode()
        url = f"{_DASHBOARD_URL}/api/studio/workflows/runs/{run_id}/steps/{step_run_id}/{action}"
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)  # nosec B310 -- localhost only
    except Exception:
        pass


def _handle_hitl_action(action: str, step_run_id: str = "", reason: str = "") -> str:
    """Approve or reject a pending HITL gate. Returns reply text."""
    pending = _get_pending_hitl_steps()
    if not pending:
        return "⚠️ No workflow gates are currently awaiting approval."

    if step_run_id:
        targets = [p for p in pending if p["step_run_id"] == step_run_id]
        if not targets:
            return f"⚠️ Step <code>{step_run_id}</code> not found or not pending."
        target = targets[0]
    elif len(pending) == 1:
        target = pending[0]
    else:
        lines = [f"⚠️ Multiple gates pending — specify step ID:\n"]
        for p in pending:
            lines.append(
                f"• <b>{p['step_name']}</b> in <code>{p['run_id']}</code>\n"
                f"  ID: <code>{p['step_run_id']}</code>"
            )
        lines.append(f"\nUse: /{action} &lt;step_run_id&gt;")
        return "\n".join(lines)

    ok = _write_hitl_decision_to_db(target["step_run_id"], action, reason)
    if not ok:
        return f"❌ Failed to write {action} decision — step may no longer be pending."

    _try_hitl_api(target["run_id"], target["step_run_id"], action, reason)

    icon = "✅" if action == "approve" else "❌"
    verb = "Approved" if action == "approve" else "Rejected"
    msg = f"{icon} <b>{verb}</b>\n\nStep: <b>{target['step_name']}</b>\n"
    msg += f"Workflow: {target.get('workflow_name', target['run_id'])}\n"
    if reason:
        msg += f"Reason: {reason}\n"
    msg += f"\nRun <code>{target['run_id']}</code> will continue within ~10s."
    return msg


# ── Main command dispatcher ──────────────────────────────────────────

def process_command(
    text: str,
    sender_id: str = "",
    allowed_sender_id: str = "",
    platform: str = "channel",
) -> Optional[str]:
    """Parse and dispatch a command string. Returns reply text or None.

    Args:
        text: Raw message text (may start with / or !icdev ).
        sender_id: Platform user identifier (for security check).
        allowed_sender_id: If set, only this sender_id is accepted.
        platform: Display name used in task descriptions (e.g. "GitHub").
    """
    text = (text or "").strip()
    if not text:
        return None

    # Strip !icdev prefix (GitHub/GitLab convention)
    if text.lower().startswith("!icdev "):
        text = text[7:].strip()

    # Security: only respond to the configured sender (if restriction set)
    if allowed_sender_id and str(sender_id) != str(allowed_sender_id):
        return None

    text_lower = text.lower().strip()

    # ── HITL plain-text shortcuts ──────────────────────────────────
    if text_lower in ("approve", "approved", "yes", "lgtm"):
        return _handle_hitl_action("approve")
    if text_lower.startswith(("reject", "denied", "no ")):
        reason = text[len(text_lower.split()[0]):].strip()
        return _handle_hitl_action("reject", reason=reason)

    # ── Slash commands ─────────────────────────────────────────────
    if text.startswith("/"):
        parts = text.split(None, 1)
        cmd = parts[0].lower().split("@")[0]
        body = parts[1] if len(parts) > 1 else ""

        if cmd in ("/help", "/start"):
            return HELP_TEXT

        if cmd == "/status":
            return _get_board_summary()

        if cmd == "/list":
            return _get_task_list()

        if cmd == "/approve":
            args = body.strip().split(None, 1)
            step_run_id = args[0] if args else ""
            return _handle_hitl_action("approve", step_run_id=step_run_id)

        if cmd == "/reject":
            args = body.strip().split(None, 1)
            step_run_id = args[0] if args else ""
            reason = args[1] if len(args) > 1 else ""
            return _handle_hitl_action("reject", step_run_id=step_run_id, reason=reason)

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
                description=f"Created via {platform}: {text}",
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

    # ── Free-text instruction ──────────────────────────────────────
    result = _create_task(
        title=text[:80],
        task_type="chore",
        priority="medium",
        description=f"Instruction via {platform}: {text}",
    )
    return (
        f"✅ <b>Task Created from Instruction</b>\n\n"
        f"\U0001f4dd {text[:80]}\n"
        f"Type: chore | Priority: medium\n"
        f"Status: Added to Backlog\n"
        f"ID: <code>{result['id']}</code>\n\n"
        f"<i>Tip: Use /build, /fix, /run for specific task types</i>"
    )
