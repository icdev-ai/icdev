# CUI // SP-CTI
"""ACE Message Bus — routes ACE coworker messages over the agent mailbox.

Transport contract (mailbox DB CHECK is never modified):
  mailbox message_type : always 'notification'
  mailbox subject      : 'ACE:{cw_type}' (e.g. 'ACE:cw_verify_request')
  mailbox body         : JSON-encoded payload

ACE semantic type and payload are also persisted to ace_messages for
full traceability within the canvas.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ace.message_bus")


class NegotiationFailedError(Exception):
    """Raised by negotiate() when max_rounds are exhausted without consensus."""


class MessageBus:
    """Inter-coworker message bus for the ACE canvas.

    Wraps tools/agent/mailbox.py without modifying its DB CHECK constraint.
    All ACE traffic is sent as mailbox 'notification' messages with subject
    'ACE:<cw_type>'.  Every send also writes a record to ace_messages.

    Args:
        instance_id: ACE instance that owns this bus.
    """

    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(
        self,
        from_coworker_id: str,
        to_role: str,
        message_type: str,
        payload: dict[str, Any],
    ) -> str:
        """Send a typed message to a coworker identified by role.

        Args:
            from_coworker_id: Sender coworker ID (= their mailbox ID).
            to_role: Target role_id in ace_coworkers for this instance.
            message_type: ACE semantic type, e.g. 'cw_verify_request'.
            payload: Arbitrary dict serialised as the message body.

        Returns:
            mailbox message_id (UUID string).

        Raises:
            ValueError: if no active coworker with the given role exists.
        """
        mailbox_id = self._resolve_role(to_role)
        return self._send_raw(
            from_coworker_id=from_coworker_id,
            mailbox_id=mailbox_id,
            message_type=message_type,
            payload=payload,
            to_role=to_role,
        )

    def broadcast(
        self,
        from_coworker_id: str,
        message_type: str,
        payload: dict[str, Any],
    ) -> list[str]:
        """Send a message to every coworker in this instance (excluding self).

        Args:
            from_coworker_id: Sender coworker ID.
            message_type: ACE semantic type.
            payload: Message payload dict.

        Returns:
            List of mailbox message_ids (one per successful recipient).
        """
        coworkers = self._get_all_coworkers()
        message_ids: list[str] = []

        for cw in coworkers:
            if cw["id"] == from_coworker_id:
                continue
            try:
                mid = self._send_raw(
                    from_coworker_id=from_coworker_id,
                    mailbox_id=cw["id"],
                    message_type=message_type,
                    payload=payload,
                    to_role=cw["role_id"],
                )
                message_ids.append(mid)
            except Exception as exc:
                logger.warning(
                    "broadcast: skipping coworker %s (role=%s): %s",
                    cw["id"],
                    cw["role_id"],
                    exc,
                )

        logger.info(
            "ACE broadcast [%s] from %s -> %d coworkers",
            message_type,
            from_coworker_id,
            len(message_ids),
        )
        return message_ids

    def negotiate(
        self,
        from_coworker_id: str,
        to_role: str,
        payload: dict[str, Any],
        max_rounds: int = 3,
    ) -> dict[str, Any]:
        """Run a multi-round proposal/counter negotiation with a peer role.

        Sends 'cw_negotiate_proposal' and waits for 'cw_negotiate_accept' or
        'cw_negotiate_counter' back to from_coworker_id.  On a counter the
        updated payload is used for the next round.

        Args:
            from_coworker_id: Initiating coworker ID.
            to_role: Target role_id for the negotiation.
            payload: Initial proposal data.
            max_rounds: Maximum proposal/counter cycles before failing.

        Returns:
            dict with keys 'accepted' (True), 'rounds' (int), 'result' (dict).

        Raises:
            NegotiationFailedError: if max_rounds exhausted without acceptance.
        """
        current_payload: dict[str, Any] = dict(payload)

        for round_num in range(1, max_rounds + 1):
            self._send_raw(
                from_coworker_id=from_coworker_id,
                mailbox_id=self._resolve_role(to_role),
                message_type="cw_negotiate_proposal",
                payload={**current_payload, "_round": round_num},
                to_role=to_role,
            )

            responses = self._poll_negotiate_reply(
                coworker_id=from_coworker_id,
                timeout_s=10,
            )

            for resp in responses:
                subject = resp.get("subject", "")
                if subject == "ACE:cw_negotiate_accept":
                    try:
                        result = json.loads(resp.get("body") or "{}")
                    except (json.JSONDecodeError, ValueError):
                        result = {}
                    logger.info(
                        "Negotiation accepted after %d round(s): %s->role=%s",
                        round_num,
                        from_coworker_id,
                        to_role,
                    )
                    return {"accepted": True, "rounds": round_num, "result": result}

                if subject == "ACE:cw_negotiate_counter":
                    try:
                        current_payload = json.loads(resp.get("body") or "{}") or current_payload
                    except (json.JSONDecodeError, ValueError):
                        pass

        raise NegotiationFailedError(
            f"Negotiation between '{from_coworker_id}' and role='{to_role}' "
            f"failed after {max_rounds} round(s)"
        )

    def poll_inbox(
        self,
        coworker_id: str,
        timeout_s: float = 5,
    ) -> list[dict[str, Any]]:
        """Poll a coworker's mailbox for incoming ACE messages.

        Blocks up to timeout_s seconds waiting for at least one message.
        Returned messages are marked as read.

        Args:
            coworker_id: The coworker whose mailbox to poll.
            timeout_s: Maximum seconds to wait before returning empty.

        Returns:
            List of mailbox row dicts for ACE messages (subject starts 'ACE:').
        """
        from icdev.tools.agent import mailbox

        deadline = time.monotonic() + timeout_s
        poll_interval = 0.25

        while True:
            messages = mailbox.receive(
                agent_id=coworker_id,
                unread_only=True,
                message_type="notification",
            )
            ace_msgs = [m for m in messages if str(m.get("subject", "")).startswith("ACE:")]
            if ace_msgs:
                for m in ace_msgs:
                    mailbox.mark_read(m["id"])
                return ace_msgs
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            time.sleep(min(poll_interval, remaining))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_role(self, to_role: str) -> str:
        """Return the mailbox_id for an active coworker with the given role.

        Args:
            to_role: role_id value in ace_coworkers.

        Returns:
            coworker id string (used as to_agent_id in agent_mailbox).

        Raises:
            ValueError: if no active coworker with that role exists.
        """
        from icdev.tools.db.storage import get_canvas_connection

        conn = get_canvas_connection("ICDEV_ACE_DB_URL")
        try:
            row = conn.execute(
                """SELECT id FROM ace_coworkers
                   WHERE instance_id = %s AND role_id = %s
                     AND state NOT IN ('offline', 'suspended')
                   LIMIT 1""",
                (self.instance_id, to_role),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            raise ValueError(
                f"No active coworker with role_id='{to_role}' "
                f"in ACE instance '{self.instance_id}'"
            )
        return row[0]

    def _get_all_coworkers(self) -> list[dict[str, Any]]:
        """Return id, role_id, and state for every coworker in this instance."""
        from icdev.tools.db.storage import get_canvas_connection

        conn = get_canvas_connection("ICDEV_ACE_DB_URL")
        try:
            rows = conn.execute(
                "SELECT id, role_id, state FROM ace_coworkers WHERE instance_id = %s",
                (self.instance_id,),
            ).fetchall()
        finally:
            conn.close()

        return [{"id": r[0], "role_id": r[1], "state": r[2]} for r in rows]

    def _send_raw(
        self,
        from_coworker_id: str,
        mailbox_id: str,
        message_type: str,
        payload: dict[str, Any],
        to_role: str = "",
    ) -> str:
        """Write one message to agent_mailbox and one record to ace_messages.

        Args:
            from_coworker_id: Sender coworker ID.
            mailbox_id: Direct recipient mailbox ID (coworker id).
            message_type: ACE semantic type stored in ace_messages and subject.
            payload: Message payload dict.
            to_role: Role hint stored in ace_messages metadata.

        Returns:
            mailbox message_id.
        """
        from icdev.tools.agent import mailbox

        subject = f"ACE:{message_type}"
        body = json.dumps(payload, ensure_ascii=False)

        msg_id = mailbox.send(
            from_agent_id=from_coworker_id,
            to_agent_id=mailbox_id,
            message_type="notification",
            subject=subject,
            body=body,
        )

        self._record_ace_message(
            coworker_id=from_coworker_id,
            message_type=message_type,
            role="user",
            content=body,
            metadata={
                "mailbox_id": msg_id,
                "to_role": to_role,
                "to_mailbox_id": mailbox_id,
            },
        )

        logger.info(
            "ACE send [%s] %s -> %s (msg=%s)",
            message_type,
            from_coworker_id,
            mailbox_id,
            msg_id,
        )
        return msg_id

    def _record_ace_message(
        self,
        coworker_id: str,
        message_type: str,
        role: str,
        content: str,
        metadata: dict[str, Any],
    ) -> str:
        """Persist an entry to ace_messages for ACE-level traceability.

        Returns:
            ace_messages row id.
        """
        from icdev.tools.db.storage import get_canvas_connection

        msg_id = str(uuid.uuid4())
        conn = get_canvas_connection("ICDEV_ACE_DB_URL")
        try:
            conn.execute(
                """INSERT INTO ace_messages
                       (id, instance_id, coworker_id, message_type, role, content, metadata_json)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    msg_id,
                    self.instance_id,
                    coworker_id,
                    message_type,
                    role,
                    content,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        return msg_id

    def _poll_negotiate_reply(
        self,
        coworker_id: str,
        timeout_s: float,
    ) -> list[dict[str, Any]]:
        """Poll coworker_id's inbox for negotiate accept/counter messages."""
        from icdev.tools.agent import mailbox

        deadline = time.monotonic() + timeout_s
        poll_interval = 0.5

        while True:
            messages = mailbox.receive(
                agent_id=coworker_id,
                unread_only=True,
                message_type="notification",
            )
            negotiate_msgs = [
                m for m in messages
                if str(m.get("subject", "")).startswith("ACE:cw_negotiate_")
            ]
            if negotiate_msgs:
                for m in negotiate_msgs:
                    mailbox.mark_read(m["id"])
                return negotiate_msgs
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            time.sleep(min(poll_interval, remaining))
