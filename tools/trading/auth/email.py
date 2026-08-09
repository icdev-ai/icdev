# CUI // SP-CTI
"""Email delivery — Phase 2A.2.

Two backends are wired:
  - Dev (default): writes the email to .tmp/sent_emails/ AND prints a
    one-line "MAIL → recipient | subject | preview" to stderr. Usable
    end-to-end without SMTP — perfect for local dev + air-gap-without-relay.
  - SMTP: configure via env vars; flask-mailman handles delivery.
    Activate by setting `ICDEV_MAIL_BACKEND=smtp` plus SMTP_* vars.

Either backend exposes `send_password_reset(email, reset_url, display_name)`.

Operator config (.env):
  ICDEV_MAIL_BACKEND=dev|smtp           # default: dev
  ICDEV_MAIL_FROM="FathomDesk <noreply@fathomdesk.example>"
  SMTP_HOST=smtp.example.com
  SMTP_PORT=587
  SMTP_USERNAME=...
  SMTP_PASSWORD=...
  SMTP_USE_TLS=true
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_DEV_OUTBOX = Path(__file__).resolve().parents[3] / ".tmp" / "sent_emails"


def _backend() -> str:
    return (os.environ.get("ICDEV_MAIL_BACKEND") or "dev").lower().strip()


def _from_address() -> str:
    return (os.environ.get("ICDEV_MAIL_FROM")
            or "FathomDesk <noreply@fathomdesk.local>")


def _send_dev(*, to: str, subject: str, text_body: str,
                html_body: str | None = None) -> dict:
    """Write to .tmp/sent_emails/<timestamp>-<to>.eml + log a one-liner."""
    _DEV_OUTBOX.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_to = "".join(c if c.isalnum() or c in "@.-_" else "_" for c in to)
    fpath = _DEV_OUTBOX / f"{ts}-{safe_to}.eml"
    eml = (
        f"From: {_from_address()}\n"
        f"To: {to}\n"
        f"Subject: {subject}\n"
        f"Content-Type: text/plain; charset=utf-8\n"
        f"X-FathomDesk-Backend: dev\n\n"
        f"{text_body}\n"
    )
    fpath.write_text(eml, encoding="utf-8", newline="")
    preview = (text_body or "")[:80].replace("\n", " ").strip()
    print(f"[mail/dev] → {to} | {subject} | {preview} | saved={fpath.name}",
            file=sys.stderr)
    return {"ok": True, "backend": "dev", "saved_to": str(fpath)}


def _send_smtp(*, to: str, subject: str, text_body: str,
                 html_body: str | None = None) -> dict:
    """Send via flask-mailman / smtplib. Requires SMTP_* env config."""
    try:
        from flask_mailman import EmailMessage
    except ImportError:
        return {"ok": False, "backend": "smtp",
                "error": "flask-mailman not installed"}
    msg = EmailMessage(
        subject=subject,
        body=text_body,
        from_email=_from_address(),
        to=[to],
    )
    if html_body:
        msg.content_subtype = "html"
        msg.body = html_body
    try:
        msg.send()
        return {"ok": True, "backend": "smtp"}
    except Exception as e:
        return {"ok": False, "backend": "smtp", "error": str(e)}


def send(*, to: str, subject: str, text_body: str,
         html_body: str | None = None) -> dict:
    """Dispatch via the active backend."""
    if _backend() == "smtp":
        return _send_smtp(to=to, subject=subject,
                            text_body=text_body, html_body=html_body)
    return _send_dev(to=to, subject=subject,
                       text_body=text_body, html_body=html_body)


def send_password_reset(*, to: str, reset_url: str,
                         display_name: str | None = None) -> dict:
    name = display_name or to
    text = (
        f"Hi {name},\n\n"
        f"We received a request to reset the password for your FathomDesk account.\n\n"
        f"To set a new password, open this link within 30 minutes:\n\n"
        f"  {reset_url}\n\n"
        f"If you did not request this, you can ignore this email — your account "
        f"is unchanged. The link is single-use and will expire on its own.\n\n"
        f"— FathomDesk\n"
    )
    return send(to=to, subject="Reset your FathomDesk password", text_body=text)
