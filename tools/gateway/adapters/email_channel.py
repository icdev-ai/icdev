#!/usr/bin/env python3
# CUI // SP-CTI
"""Email channel adapter for the Remote Command Gateway (sag-gw-02).

The SPIKE (docs/spikes/sag-gw-02-discord-email-adapters.md) landed **Email = GO,
Discord = NO-GO**. Email fits ICDEV's pure-Python / offline preference: it uses
only the standard-library ``imaplib`` (inbound poll) and ``smtplib`` (outbound) —
**no new third-party dependency**, and it works on a self-hosted mail server in an
IL5/air-gapped enclave. Discord was rejected because ``discord.py`` is a heavyweight
async gateway/websocket dependency that conflicts with that preference.

Unlike the webhook channels (Telegram/Slack/…), email is **polled**: there is no
inbound HTTP webhook and therefore no per-message HMAC to verify. Instead:

- inbound messages are fetched over an **authenticated IMAP session** by
  :meth:`poll_once`, normalised to the same dict shape a webhook body would have,
  and handed to :meth:`parse_webhook` so they flow through the **identical**
  :func:`tools.gateway.security_chain.run_security_chain` gates as every other
  channel — sender identity is enforced by the gateway's identity-binding gate on
  the ``From`` address, exactly as ``channel_user_id`` is for other channels;
- replies are sent by :meth:`send_message` over SMTP+STARTTLS, threaded via the
  ``In-Reply-To`` / ``References`` headers.

The security chain is **unchanged** — this adapter only produces
:class:`CommandEnvelope` objects and sends text; it adds no bypass.

Environment variables (all optional; the adapter self-skips when unset):
    IMAP_HOST / IMAP_PORT (993) / IMAP_USER / IMAP_PASSWORD / IMAP_MAILBOX (INBOX)
    SMTP_HOST / SMTP_PORT (587) / SMTP_USER / SMTP_PASSWORD / SMTP_FROM
"""
from __future__ import annotations

import email as _email
import imaplib
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.mime.text import MIMEText
from email.utils import make_msgid, parseaddr
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.gateway.adapters.base import BaseChannelAdapter  # noqa: E402
from tools.gateway.event_envelope import CommandEnvelope, parse_command_text  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.gateway.adapters.email")

# Only messages whose command begins with one of these are accepted (parity with
# the other adapters' "only process ICDEV commands" filter).
_COMMAND_PREFIXES = ("icdev-", "icdev", "bind")


class EmailAdapter(BaseChannelAdapter):
    """IMAP-poll / SMTP-send adapter — stdlib only, no new dependency."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__("email", config)
        # IMAP (inbound)
        self.imap_host = os.environ.get("IMAP_HOST", "")
        self.imap_port = int(os.environ.get("IMAP_PORT", "993") or 993)
        self.imap_user = os.environ.get("IMAP_USER", "")
        self.imap_password = os.environ.get("IMAP_PASSWORD", "")
        self.mailbox = os.environ.get("IMAP_MAILBOX", "INBOX")
        # SMTP (outbound)
        self.smtp_host = os.environ.get("SMTP_HOST", "")
        self.smtp_port = int(os.environ.get("SMTP_PORT", "587") or 587)
        self.smtp_user = os.environ.get("SMTP_USER", "")
        self.smtp_password = os.environ.get("SMTP_PASSWORD", "")
        self.from_addr = os.environ.get("SMTP_FROM", "") or self.smtp_user
        self.max_poll = int(config.get("max_poll", 25))

    # -- BaseChannelAdapter contract --------------------------------------

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Email is polled over an authenticated IMAP session — there is no
        inbound webhook and hence no per-message HMAC to verify. Sender identity
        is enforced downstream by the gateway's identity-binding gate on the
        ``From`` address, so this returns True (verification not applicable).
        """
        return True

    def parse_webhook(
        self, request_data: Dict[str, Any], headers: Dict[str, str]
    ) -> Optional[CommandEnvelope]:
        """Adapt a normalised inbound-email dict into a :class:`CommandEnvelope`.

        ``request_data`` keys (produced by :meth:`poll_once`): ``from_addr``,
        ``subject``, ``body``, ``message_id``, ``in_reply_to``, ``references``,
        ``date``, ``is_bot``. The command is taken from the subject line first,
        falling back to the first non-empty body line — whichever names an ICDEV
        command.
        """
        from_addr = (request_data.get("from_addr") or "").strip().lower()
        if not from_addr:
            return None

        subject = (request_data.get("subject") or "").strip()
        body = (request_data.get("body") or "").strip()

        command, args, raw = self._extract_command(subject, body)
        if not command:
            return None

        # Thread grouping: prefer the message we're replying to so a whole email
        # conversation maps to one gateway thread (In-Reply-To > References root >
        # this message's own id).
        thread_id = (
            (request_data.get("in_reply_to") or "").strip()
            or _first_ref(request_data.get("references"))
            or (request_data.get("message_id") or "").strip()
        )

        return CommandEnvelope(
            channel="email",
            channel_user_id=from_addr,
            channel_user_name=from_addr,
            channel_message_id=(request_data.get("message_id") or "").strip(),
            channel_thread_id=thread_id,
            raw_text=raw,
            command=command,
            args=args,
            project_id=args.get("project_id", ""),
            is_bot=bool(request_data.get("is_bot", False)),
            timestamp=(request_data.get("date") or datetime.now(timezone.utc).isoformat()),
        )

    def _extract_command(self, subject: str, body: str) -> tuple[str, Dict[str, Any], str]:
        for candidate in (subject, *body.splitlines()):
            text = (candidate or "").strip()
            if not text:
                continue
            probe = text[1:] if text.startswith("/") else text
            if not probe.lower().startswith(_COMMAND_PREFIXES):
                continue
            command, args = parse_command_text(text)
            if command:
                return command, args, text
        return "", {}, ""

    def send_message(self, channel_user_id: str, text: str, thread_id: str = "") -> bool:
        """Send a reply via SMTP+STARTTLS, threaded via In-Reply-To/References."""
        if not self.smtp_host or not self.from_addr or not channel_user_id:
            logger.error("email adapter: SMTP not configured (SMTP_HOST/SMTP_FROM)")
            return False
        msg = MIMEText(text, "plain", "utf-8")
        msg["Subject"] = "Re: ICDEV command"
        msg["From"] = self.from_addr
        msg["To"] = channel_user_id
        msg["Message-ID"] = make_msgid(domain=self.from_addr.split("@")[-1] or "icdev.local")
        if thread_id:
            msg["In-Reply-To"] = thread_id
            msg["References"] = thread_id
        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as server:
                server.starttls()
                if self.smtp_user and self.smtp_password:
                    server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.from_addr, [channel_user_id], msg.as_string())
            return True
        except (smtplib.SMTPException, OSError) as exc:
            logger.error("email adapter: SMTP send failed: %s", exc)
            return False

    # -- Inbound poll ------------------------------------------------------

    def poll_once(self, *, mark_seen: bool = True) -> List[CommandEnvelope]:
        """Fetch UNSEEN inbound messages over IMAP and return command envelopes.

        Best-effort: returns ``[]`` when IMAP is unconfigured or unreachable.
        Each returned envelope must still pass the full security chain before any
        command runs — this method performs no authorization itself.
        """
        if not (self.imap_host and self.imap_user and self.imap_password):
            return []
        envelopes: List[CommandEnvelope] = []
        client = None
        try:
            client = imaplib.IMAP4_SSL(self.imap_host, self.imap_port)
            client.login(self.imap_user, self.imap_password)
            client.select(self.mailbox)
            typ, data = client.search(None, "UNSEEN")
            if typ != "OK":
                return []
            ids = (data[0].split() if data and data[0] else [])[: self.max_poll]
            for msg_id in ids:
                fetch_flags = "(RFC822)" if mark_seen else "(BODY.PEEK[])"
                typ, msg_data = client.fetch(msg_id, fetch_flags)
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw_bytes = msg_data[0][1]
                normalised = self._normalise(raw_bytes)
                if normalised is None:
                    continue
                env = self.parse_webhook(normalised, {})
                if env is not None:
                    envelopes.append(env)
        except (imaplib.IMAP4.error, OSError) as exc:
            logger.warning("email adapter: IMAP poll failed: %s", exc)
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:  # noqa: BLE001
                    pass
        return envelopes

    @staticmethod
    def _normalise(raw_bytes: bytes) -> Optional[Dict[str, Any]]:
        """Parse a raw RFC822 message into the normalised dict parse_webhook wants."""
        try:
            m = _email.message_from_bytes(raw_bytes)
        except Exception:  # noqa: BLE001
            return None
        from_addr = parseaddr(m.get("From", ""))[1]
        subject = _decode(m.get("Subject", ""))
        body = _extract_text_body(m)
        # RFC 3834 auto-submitted / precedence bulk => treat as bot (blocked by the
        # bot-detection gate, parity with is_bot on other channels).
        auto = (m.get("Auto-Submitted", "").lower() not in ("", "no")) or (
            m.get("Precedence", "").lower() in ("bulk", "list", "auto_reply")
        )
        return {
            "from_addr": from_addr,
            "subject": subject,
            "body": body,
            "message_id": (m.get("Message-ID", "") or "").strip(),
            "in_reply_to": (m.get("In-Reply-To", "") or "").strip(),
            "references": (m.get("References", "") or "").strip(),
            "date": _parse_date(m.get("Date")),
            "is_bot": auto,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _decode(value: str) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:  # noqa: BLE001
        return value or ""


def _extract_text_body(message: Any) -> str:
    """Return the first text/plain part's decoded text (skips attachments)."""
    try:
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_type() == "text/plain" and "attachment" not in str(
                    part.get("Content-Disposition", "")
                ):
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode(part.get_content_charset() or "utf-8", "replace")
            return ""
        payload = message.get_payload(decode=True)
        if payload:
            return payload.decode(message.get_content_charset() or "utf-8", "replace")
    except Exception:  # noqa: BLE001
        pass
    return ""


def _first_ref(references: Optional[str]) -> str:
    if not references:
        return ""
    parts = references.split()
    return parts[0].strip() if parts else ""


def _parse_date(raw: Optional[str]) -> str:
    if not raw:
        return datetime.now(timezone.utc).isoformat()
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:  # noqa: BLE001
        return datetime.now(timezone.utc).isoformat()
