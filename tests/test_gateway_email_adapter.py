# CUI // SP-CTI
"""Unit tests for the Email gateway channel adapter (sag-gw-02).

No network: IMAP and SMTP are mocked. The adapter is stateless (no DB), so these
tests exercise parsing, threading, bot detection, send, and the IMAP poll purely
against fakes.
"""
from __future__ import annotations

from unittest import mock

import pytest

from tools.gateway.adapters.email_channel import EmailAdapter


@pytest.fixture()
def adapter(monkeypatch):
    for k in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD", "SMTP_HOST", "SMTP_FROM",
              "SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SMTP_HOST", "mail.example.mil")
    monkeypatch.setenv("SMTP_FROM", "icdev@example.mil")
    monkeypatch.setenv("IMAP_HOST", "mail.example.mil")
    monkeypatch.setenv("IMAP_USER", "icdev@example.mil")
    monkeypatch.setenv("IMAP_PASSWORD", "secret")  # nosec B105 — test fixture
    return EmailAdapter({"enabled": True})


# ---------------------------------------------------------------------------
# parse_webhook
# ---------------------------------------------------------------------------
def test_command_from_subject(adapter):
    env = adapter.parse_webhook(
        {"from_addr": "user@example.mil", "subject": "icdev-status", "body": "",
         "message_id": "<m1@x>", "in_reply_to": "", "references": ""},
        {},
    )
    assert env is not None
    assert env.channel == "email"
    assert env.command == "icdev-status"
    assert env.channel_user_id == "user@example.mil"


def test_command_from_body_fallback(adapter):
    env = adapter.parse_webhook(
        {"from_addr": "u@x.mil", "subject": "hello", "body": "please run\nicdev-test proj-9\nthanks",
         "message_id": "<m2@x>"},
        {},
    )
    assert env is not None
    assert env.command == "icdev-test"
    assert env.args.get("project_id") == "proj-9"


def test_non_icdev_message_ignored(adapter):
    env = adapter.parse_webhook(
        {"from_addr": "u@x.mil", "subject": "lunch?", "body": "are you free"},
        {},
    )
    assert env is None


def test_missing_sender_ignored(adapter):
    env = adapter.parse_webhook({"from_addr": "", "subject": "icdev-status"}, {})
    assert env is None


def test_threading_prefers_in_reply_to(adapter):
    env = adapter.parse_webhook(
        {"from_addr": "u@x.mil", "subject": "icdev-status",
         "message_id": "<new@x>", "in_reply_to": "<root@x>",
         "references": "<root@x> <mid@x>"},
        {},
    )
    assert env.channel_thread_id == "<root@x>"


def test_threading_falls_back_to_references_root(adapter):
    env = adapter.parse_webhook(
        {"from_addr": "u@x.mil", "subject": "icdev-status",
         "message_id": "<new@x>", "in_reply_to": "", "references": "<root@x> <mid@x>"},
        {},
    )
    assert env.channel_thread_id == "<root@x>"


def test_bot_flag_propagates(adapter):
    env = adapter.parse_webhook(
        {"from_addr": "u@x.mil", "subject": "icdev-status", "is_bot": True}, {}
    )
    assert env.is_bot is True


# ---------------------------------------------------------------------------
# verify_signature (not applicable for polled email)
# ---------------------------------------------------------------------------
def test_verify_signature_not_applicable(adapter):
    assert adapter.verify_signature(b"", "") is True


# ---------------------------------------------------------------------------
# send_message (SMTP mocked)
# ---------------------------------------------------------------------------
def test_send_message_smtp(adapter):
    with mock.patch("tools.gateway.adapters.email_channel.smtplib.SMTP") as smtp:
        server = smtp.return_value.__enter__.return_value
        ok = adapter.send_message("user@example.mil", "hi there", thread_id="<root@x>")
    assert ok is True
    server.sendmail.assert_called_once()
    sent_body = server.sendmail.call_args[0][2]
    assert "In-Reply-To: <root@x>" in sent_body
    assert "To: user@example.mil" in sent_body


def test_send_message_unconfigured(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    a = EmailAdapter({})
    assert a.send_message("user@example.mil", "hi") is False


# ---------------------------------------------------------------------------
# poll_once (IMAP mocked) + _normalise
# ---------------------------------------------------------------------------
_RAW = (
    b"From: Alice <alice@example.mil>\r\n"
    b"Subject: icdev-status\r\n"
    b"Message-ID: <abc@example.mil>\r\n"
    b"In-Reply-To: <root@example.mil>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"body text\r\n"
)


def test_normalise_parses_headers(adapter):
    d = adapter._normalise(_RAW)
    assert d["from_addr"] == "alice@example.mil"
    assert d["subject"] == "icdev-status"
    assert d["in_reply_to"] == "<root@example.mil>"
    assert d["is_bot"] is False


def test_poll_once_returns_envelopes(adapter):
    fake = mock.MagicMock()
    fake.search.return_value = ("OK", [b"1"])
    fake.fetch.return_value = ("OK", [(b"1 (RFC822 {N}", _RAW)])
    with mock.patch("tools.gateway.adapters.email_channel.imaplib.IMAP4_SSL", return_value=fake):
        envs = adapter.poll_once()
    assert len(envs) == 1
    assert envs[0].command == "icdev-status"
    assert envs[0].channel_user_id == "alice@example.mil"
    assert envs[0].channel_thread_id == "<root@example.mil>"


def test_poll_once_unconfigured_returns_empty(monkeypatch):
    for k in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    a = EmailAdapter({})
    assert a.poll_once() == []


def test_auto_submitted_marks_bot(adapter):
    raw = _RAW.replace(b"Subject: icdev-status\r\n",
                       b"Subject: icdev-status\r\nAuto-Submitted: auto-replied\r\n")
    d = adapter._normalise(raw)
    assert d["is_bot"] is True
