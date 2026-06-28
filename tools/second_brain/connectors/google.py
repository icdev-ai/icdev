# CUI // SP-CTI
"""Google OAuth connector — Gmail + Google Calendar."""
from __future__ import annotations

import json
import os
from datetime import date
from typing import Any
from urllib.parse import urlencode

from tools.logging.icdev_logger import get_logger
from tools.second_brain.connectors.base import BaseConnector

logger = get_logger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/contacts.readonly",
]
_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
_GMAIL_API = "https://gmail.googleapis.com/gmail/v1"


class GoogleConnector(BaseConnector):
    service = "gcal"  # also covers gmail

    def verify(self, credentials: dict) -> bool:
        token = credentials.get("access_token")
        if not token:
            return False
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{_CALENDAR_API}/users/me/calendarList?maxResults=1",
                headers={"Authorization": f"Bearer {token}"},
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status == 200
        except Exception as exc:
            logger.debug("[google] verify failed: %s", exc)
            return False

    def get_oauth_authorize_url(self, state: str, redirect_uri: str) -> str:
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{_AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        import urllib.request
        client_id = os.environ.get("GOOGLE_CLIENT_ID", "")
        client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "")
        body = urlencode({
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }).encode()
        req = urllib.request.Request(_TOKEN_URL, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    def get_todays_items(self, user_id: str) -> list[dict[str, Any]]:
        """Return today's calendar events."""
        token = self._get_token(user_id)
        if not token:
            return []
        try:
            today = date.today().isoformat()
            tomorrow = date.fromordinal(date.today().toordinal() + 1).isoformat()
            import urllib.request
            url = (f"{_CALENDAR_API}/calendars/primary/events"
                   f"?timeMin={today}T00:00:00Z&timeMax={tomorrow}T00:00:00Z"
                   f"&singleEvents=true&orderBy=startTime&maxResults=20")
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            items = []
            for ev in data.get("items", []):
                start = ev.get("start", {})
                items.append({
                    "source": "gcal",
                    "type": "meeting",
                    "time": start.get("dateTime", start.get("date", "")),
                    "title": ev.get("summary", "(No title)"),
                    "attendees": [
                        a.get("email", "") for a in ev.get("attendees", [])[:8]
                    ],
                    "location": ev.get("location", ""),
                    "description": (ev.get("description") or "")[:200],
                })
            return items
        except Exception as exc:
            logger.warning("[google] get_todays_items failed: %s", exc)
            return []

    def sync_to_context(self, user_id: str) -> dict[str, Any]:
        items = self.get_todays_items(user_id)
        return {"service": "gcal", "items_count": len(items), "items": items}

    def _get_token(self, user_id: str) -> str | None:
        try:
            from tools.second_brain.integrations import get_decrypted_token
            return get_decrypted_token(user_id, "gcal")
        except Exception:
            return None
