# CUI // SP-CTI
"""Microsoft 365 connector — Graph API for Calendar, Outlook, Teams."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TENANT_ID = os.environ.get("AZURE_TENANT_ID", "common")
CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")


def _graph_get(token: str, path: str) -> dict:
    req = urllib.request.Request(
        f"{GRAPH_BASE}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _graph_post(token: str, path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{GRAPH_BASE}{path}",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def get_oauth_url(redirect_uri: str, state: str) -> str:
    """Build the Microsoft OAuth2 authorization URL."""
    params = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": "Calendars.Read Mail.Send Chat.ReadWrite offline_access User.Read",
        "state": state,
    })
    return f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/authorize?{params}"


def exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange auth code for access + refresh tokens."""
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def refresh_token(refresh_tok: str, redirect_uri: str = "") -> dict:
    """Refresh an expired M365 access token."""
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_tok,
        "grant_type": "refresh_token",
        "scope": "Calendars.Read Mail.Send Chat.ReadWrite offline_access User.Read",
    }).encode()
    req = urllib.request.Request(
        f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def get_todays_events(token: str) -> list[dict]:
    """Fetch today's calendar events from Microsoft Calendar."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    start = now.strftime("%Y-%m-%dT00:00:00Z")
    end = (now + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    try:
        data = _graph_get(
            token,
            f"/me/calendarView?startDateTime={start}&endDateTime={end}"
            "&$select=subject,start,end,attendees&$orderby=start/dateTime&$top=10",
        )
        events = []
        for e in data.get("value", []):
            events.append({
                "title": e.get("subject", ""),
                "start": e.get("start", {}).get("dateTime", "")[:16].replace("T", " "),
                "end": e.get("end", {}).get("dateTime", "")[:16].replace("T", " "),
                "attendees": [
                    a.get("emailAddress", {}).get("name", "")
                    for a in e.get("attendees", [])[:5]
                ],
            })
        return events
    except Exception as exc:
        logger.debug("[m365] calendar fetch failed: %s", exc)
        return []


def send_mail(token: str, to_email: str, subject: str, html_body: str) -> bool:
    """Send an email via Microsoft Graph (Outlook)."""
    try:
        _graph_post(token, "/me/sendMail", {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": html_body},
                "toRecipients": [{"emailAddress": {"address": to_email}}],
            }
        })
        return True
    except Exception as exc:
        logger.warning("[m365] sendMail failed: %s", exc)
        return False


def send_teams_message(token: str, teams_chat_id: str, text: str) -> bool:
    """Send a Teams chat message."""
    try:
        _graph_post(token, f"/me/chats/{teams_chat_id}/messages", {
            "body": {"contentType": "text", "content": text}
        })
        return True
    except Exception as exc:
        logger.warning("[m365] Teams message failed: %s", exc)
        return False


def get_user_profile(token: str) -> dict:
    """Fetch basic MS profile (display name, email, id)."""
    try:
        return _graph_get(token, "/me?$select=displayName,mail,userPrincipalName,id")
    except Exception:
        return {}
