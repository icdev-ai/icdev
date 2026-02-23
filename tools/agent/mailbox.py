# CUI // SP-CTI
"""Agent Mailbox — HMAC-SHA256 signed inter-agent messaging.

Provides tamper-evident, append-only messaging between agents using the
agent_mailbox table in icdev.db. Every message is signed with HMAC-SHA256
and can be verified for integrity at any time.

Decision D41: SQLite-based, append-only, tamper-evident.
"""

import argparse
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"

try:
    from tools.compat.db_utils import get_db_connection
except ImportError:
    get_db_connection = None

logger = logging.getLogger("icdev.mailbox")

# HMAC secret from environment or default (override in production)
HMAC_SECRET = os.environ.get("ICDEV_MAILBOX_SECRET", "icdev-default-hmac-key")

# Valid message types matching the DB CHECK constraint
VALID_MESSAGE_TYPES = (
    "request", "response", "notification", "veto",
    "escalation", "collaboration_invite", "memory_share",
)

# Graceful audit import
try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:
    def audit_log_event(**kwargs):
        logger.debug("audit_logger unavailable — skipping audit: %s", kwargs.get("action", ""))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db(db_path=None) -> sqlite3.Connection:
    """Open a DB connection with row factory."""
    if get_db_connection:
        return get_db_connection(db_path or DB_PATH)
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _compute_hmac(from_agent: str, to_agent: str, subject: str, body: str) -> str:
    """Compute HMAC-SHA256 of message fields for tamper detection.

    The HMAC covers the core message fields (from, to, subject, body) to
    ensure message integrity. Any modification to these fields will
    invalidate the signature.

    Args:
        from_agent: Sender agent ID.
        to_agent: Recipient agent ID.
        subject: Message subject.
        body: Message body content.

    Returns:
        Hex-encoded HMAC-SHA256 digest.
    """
    message_bytes = f"{from_agent}|{to_agent}|{subject}|{body}".encode("utf-8")
    return hmac.new(
        HMAC_SECRET.encode("utf-8"),
        message_bytes,
        hashlib.sha256,
    ).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send(from_agent_id: str, to_agent_id: str, message_type: str,
         subject: str, body: str, priority: int = 5,
         in_reply_to: str = None, db_path=None) -> str:
    """Send a message from one agent to another.

    Creates an HMAC-signed message in the agent_mailbox table.

    Args:
        from_agent_id: Sender agent ID.
        to_agent_id: Recipient agent ID.
        message_type: One of VALID_MESSAGE_TYPES.
        subject: Message subject line.
        body: Message body (may be JSON string for structured data).
        priority: 1-10, default 5 (higher = more important).
        in_reply_to: Message ID this is replying to (optional).
        db_path: Optional database path override.

    Returns:
        The message ID (UUID).

    Raises:
        ValueError: If message_type is not valid.
    """
    if message_type not in VALID_MESSAGE_TYPES:
        raise ValueError(
            f"Invalid message_type '{message_type}'. "
            f"Valid: {VALID_MESSAGE_TYPES}"
        )

    message_id = str(uuid.uuid4())
    signature = _compute_hmac(from_agent_id, to_agent_id, subject, body)

    conn = _get_db(db_path)
    try:
        conn.execute(
            """INSERT INTO agent_mailbox
               (id, from_agent_id, to_agent_id, message_type, subject, body,
                priority, in_reply_to, hmac_signature)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (message_id, from_agent_id, to_agent_id, message_type,
             subject, body, priority, in_reply_to, signature),
        )
        conn.commit()
        logger.info("Message %s sent: %s -> %s [%s] %s",
                     message_id, from_agent_id, to_agent_id, message_type, subject)
    finally:
        conn.close()

    # Audit trail
    audit_log_event(
        event_type="agent_message_sent",
        actor=from_agent_id,
        action=f"Message sent to {to_agent_id}: [{message_type}] {subject}",
        details={
            "message_id": message_id,
            "to_agent_id": to_agent_id,
            "message_type": message_type,
            "priority": priority,
        },
        classification="CUI",
    )

    return message_id


def broadcast(from_agent_id: str, to_agent_ids: List[str], message_type: str,
              subject: str, body: str, priority: int = 5,
              db_path=None) -> list:
    """Send the same message to multiple agents.

    Creates individual signed messages for each recipient.

    Args:
        from_agent_id: Sender agent ID.
        to_agent_ids: List of recipient agent IDs.
        message_type: One of VALID_MESSAGE_TYPES.
        subject: Message subject line.
        body: Message body.
        priority: 1-10, default 5.
        db_path: Optional database path override.

    Returns:
        List of message IDs (one per recipient).
    """
    message_ids = []
    for to_agent_id in to_agent_ids:
        mid = send(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            message_type=message_type,
            subject=subject,
            body=body,
            priority=priority,
            db_path=db_path,
        )
        message_ids.append(mid)

    logger.info("Broadcast from %s to %d agents: %s",
                from_agent_id, len(to_agent_ids), subject)
    return message_ids


def receive(agent_id: str, unread_only: bool = True,
            message_type: str = None, limit: int = 50,
            db_path=None) -> list:
    """Get messages for an agent.

    Args:
        agent_id: The recipient agent ID.
        unread_only: If True, only return unread messages.
        message_type: Filter by message type (optional).
        limit: Maximum number of messages to return.
        db_path: Optional database path override.

    Returns:
        List of message dicts, ordered by priority DESC then created_at DESC.
    """
    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM agent_mailbox WHERE to_agent_id = ?"
        params: list = [agent_id]

        if unread_only:
            query += " AND read_at IS NULL"

        if message_type:
            if message_type not in VALID_MESSAGE_TYPES:
                raise ValueError(f"Invalid message_type filter: {message_type}")
            query += " AND message_type = ?"
            params.append(message_type)

        query += " ORDER BY priority DESC, created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def mark_read(message_id: str, db_path=None) -> bool:
    """Mark a message as read.

    Args:
        message_id: The message ID to mark.
        db_path: Optional database path override.

    Returns:
        True if the message was found and marked, False otherwise.
    """
    conn = _get_db(db_path)
    try:
        cursor = conn.execute(
            "UPDATE agent_mailbox SET read_at = datetime('now') WHERE id = ? AND read_at IS NULL",
            (message_id,),
        )
        conn.commit()
        updated = cursor.rowcount > 0
        if updated:
            logger.debug("Message %s marked as read", message_id)
        return updated
    finally:
        conn.close()


def verify_signature(message_id: str, db_path=None) -> bool:
    """Verify the HMAC signature of a message for tamper detection.

    Recomputes the HMAC from the stored message fields and compares
    it against the stored signature.

    Args:
        message_id: The message ID to verify.
        db_path: Optional database path override.

    Returns:
        True if signature matches (message intact), False if tampered or not found.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM agent_mailbox WHERE id = ?", (message_id,)
        ).fetchone()

        if not row:
            logger.error("Message %s not found for verification", message_id)
            return False

        expected = _compute_hmac(
            row["from_agent_id"],
            row["to_agent_id"],
            row["subject"],
            row["body"],
        )

        is_valid = hmac.compare_digest(expected, row["hmac_signature"])

        if not is_valid:
            logger.warning(
                "TAMPER DETECTED: Message %s HMAC mismatch! "
                "Expected %s, got %s",
                message_id, expected[:16] + "...", row["hmac_signature"][:16] + "...",
            )
        else:
            logger.debug("Message %s signature verified OK", message_id)

        return is_valid
    finally:
        conn.close()


def get_conversation(message_id: str, db_path=None) -> list:
    """Get a conversation thread by following in_reply_to chain.

    Args:
        message_id: Starting message ID.
        db_path: Optional database path override.

    Returns:
        List of messages in chronological order forming the thread.
    """
    conn = _get_db(db_path)
    try:
        thread = []
        current_id = message_id
        visited = set()

        # Walk backward through replies
        while current_id and current_id not in visited:
            visited.add(current_id)
            row = conn.execute(
                "SELECT * FROM agent_mailbox WHERE id = ?", (current_id,)
            ).fetchone()
            if not row:
                break
            thread.append(dict(row))
            current_id = row["in_reply_to"]

        # Reverse to get chronological order
        thread.reverse()

        # Walk forward to find replies to the original
        if thread:
            root_id = thread[0]["id"]
            replies = conn.execute(
                "SELECT * FROM agent_mailbox WHERE in_reply_to = ? ORDER BY created_at",
                (root_id,),
            ).fetchall()
            for r in replies:
                msg = dict(r)
                if msg["id"] not in visited:
                    thread.append(msg)
                    visited.add(msg["id"])

        return thread
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    """CLI for agent mailbox operations."""
    parser = argparse.ArgumentParser(
        description="ICDEV Agent Mailbox — HMAC-signed inter-agent messaging"
    )
    sub = parser.add_subparsers(dest="command", help="Mailbox command")

    # Send
    p_send = sub.add_parser("send", help="Send a message")
    p_send.add_argument("--from", dest="from_agent", required=True, help="Sender agent ID")
    p_send.add_argument("--to", dest="to_agent", required=True, help="Recipient agent ID")
    p_send.add_argument("--type", required=True, choices=VALID_MESSAGE_TYPES,
                        help="Message type")
    p_send.add_argument("--subject", required=True, help="Message subject")
    p_send.add_argument("--body", required=True, help="Message body")
    p_send.add_argument("--priority", type=int, default=5, help="Priority 1-10")
    p_send.add_argument("--reply-to", help="Message ID to reply to")

    # Inbox
    p_inbox = sub.add_parser("inbox", help="Check inbox for an agent")
    p_inbox.add_argument("--agent-id", required=True, help="Agent ID")
    p_inbox.add_argument("--all", action="store_true", help="Include read messages")
    p_inbox.add_argument("--type", dest="msg_type", choices=VALID_MESSAGE_TYPES,
                         help="Filter by type")
    p_inbox.add_argument("--limit", type=int, default=50, help="Max messages")

    # Verify
    p_verify = sub.add_parser("verify", help="Verify message HMAC signature")
    p_verify.add_argument("--message-id", required=True, help="Message ID to verify")

    # Mark read
    p_read = sub.add_parser("read", help="Mark a message as read")
    p_read.add_argument("--message-id", required=True, help="Message ID to mark")

    # Broadcast
    p_broadcast = sub.add_parser("broadcast", help="Send to multiple agents")
    p_broadcast.add_argument("--from", dest="from_agent", required=True, help="Sender agent ID")
    p_broadcast.add_argument("--to", dest="to_agents", required=True,
                             help="Comma-separated recipient agent IDs")
    p_broadcast.add_argument("--type", required=True, choices=VALID_MESSAGE_TYPES,
                             help="Message type")
    p_broadcast.add_argument("--subject", required=True, help="Message subject")
    p_broadcast.add_argument("--body", required=True, help="Message body")
    p_broadcast.add_argument("--priority", type=int, default=5, help="Priority 1-10")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "send":
        message_id = send(
            from_agent_id=args.from_agent,
            to_agent_id=args.to_agent,
            message_type=args.type,
            subject=args.subject,
            body=args.body,
            priority=args.priority,
            in_reply_to=args.reply_to,
        )
        print(json.dumps({"message_id": message_id, "status": "sent"}, indent=2))

    elif args.command == "inbox":
        messages = receive(
            agent_id=args.agent_id,
            unread_only=not args.all,
            message_type=args.msg_type,
            limit=args.limit,
        )
        print(json.dumps(messages, indent=2, default=str))
        if not messages:
            print("(no messages)")

    elif args.command == "verify":
        is_valid = verify_signature(args.message_id)
        status = "VALID" if is_valid else "TAMPERED"
        print(json.dumps({
            "message_id": args.message_id,
            "signature_status": status,
            "valid": is_valid,
        }, indent=2))

    elif args.command == "read":
        success = mark_read(args.message_id)
        print(json.dumps({
            "message_id": args.message_id,
            "marked_read": success,
        }, indent=2))

    elif args.command == "broadcast":
        to_agents = [a.strip() for a in args.to_agents.split(",")]
        message_ids = broadcast(
            from_agent_id=args.from_agent,
            to_agent_ids=to_agents,
            message_type=args.type,
            subject=args.subject,
            body=args.body,
            priority=args.priority,
        )
        print(json.dumps({
            "message_ids": message_ids,
            "recipient_count": len(to_agents),
        }, indent=2))


if __name__ == "__main__":
    main()
