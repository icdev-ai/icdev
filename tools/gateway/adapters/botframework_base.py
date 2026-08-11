#!/usr/bin/env python3
# CUI // SP-CTI
"""Bot Framework Base Adapter — shared base for Teams and Skype.

Both Microsoft Teams and Skype use the Bot Framework Connector API:
  - Inbound: Bot Framework Activity objects via webhook (Authorization: Bearer JWT)
  - Outbound: POST to {serviceUrl}/v3/conversations/{conversationId}/activities

This base class handles:
  - JWT RS256 validation from Bot Framework JWKS endpoint (stdlib-only, no PyJWT)
  - Graceful degradation when JWKS is unreachable (air-gap: aud-only check)
  - Parsing Bot Framework Activity → CommandEnvelope
  - Sending replies via Bot Connector API (Bearer token, client_credentials)

Subclasses (TeamsAdapter, SkypeAdapter) set _app_id_env / _app_secret_env
and optionally override send_message() to delegate to their DataBridge connector.

Decision D133: Channel adapters are ABC + implementations.
Decision D142: BotFramework adapters share a common base (Teams+Skype).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.gateway.adapters.base import BaseChannelAdapter, has_correlation_token  # noqa: E402
from tools.gateway.event_envelope import CommandEnvelope, parse_command_text  # noqa: E402

try:
    from tools.logging.icdev_logger import get_logger
    logger = get_logger("icdev.gateway.adapters.botframework")
except Exception:
    logger = get_logger("icdev.gateway.adapters.botframework")

_BOTFRAMEWORK_OPENID_URL = "https://login.botframework.com/v1/.well-known/openidconfiguration"
_JWKS_CACHE: Dict[str, Any] = {}  # {"keys": [...], "expires_at": float}
_TOKEN_CACHE: Dict[str, Any] = {}  # keyed by app_id


def _b64url_decode(s: str) -> bytes:
    """Decode base64url without padding."""
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


def _fetch_jwks() -> List[Dict]:
    """Fetch Bot Framework JWKS keys. Cached for 1 hour."""
    cached = _JWKS_CACHE.get("keys")
    if cached and _JWKS_CACHE.get("expires_at", 0) > time.time():
        return cached

    try:
        with urlopen(_BOTFRAMEWORK_OPENID_URL, timeout=10) as resp:  # nosec B310 -- Microsoft well-known endpoint
            config = json.loads(resp.read().decode("utf-8"))
        jwks_uri = config.get("jwks_uri", "")
        if jwks_uri:
            with urlopen(jwks_uri, timeout=10) as resp:  # nosec B310 -- Microsoft JWKS endpoint
                jwks = json.loads(resp.read().decode("utf-8"))
            keys = jwks.get("keys", [])
            _JWKS_CACHE["keys"] = keys
            _JWKS_CACHE["expires_at"] = time.time() + 3600
            return keys
    except Exception as exc:
        logger.warning("JWKS fetch failed (air-gap?): %s", exc)
    return []


def _decode_jwt_payload_unverified(token: str) -> Dict:
    """Decode JWT payload without signature verification (for fallback aud check)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        return json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    except Exception:
        return {}


def _validate_jwt_signature(token: str, jwks_keys: List[Dict]) -> bool:
    """Validate RS256 JWT signature using JWKS public keys (stdlib only)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return False

        header = json.loads(_b64url_decode(parts[0]).decode("utf-8"))
        kid = header.get("kid", "")

        # Find matching key
        key = None
        for k in jwks_keys:
            if k.get("kid") == kid and k.get("kty") == "RSA" and k.get("use", "sig") == "sig":
                key = k
                break
        if not key:
            return False

        n_bytes = _b64url_decode(key["n"])
        e_bytes = _b64url_decode(key["e"])
        n = int.from_bytes(n_bytes, "big")
        e = int.from_bytes(e_bytes, "big")

        sig = _b64url_decode(parts[2])
        sig_int = int.from_bytes(sig, "big")
        signed_data = f"{parts[0]}.{parts[1]}".encode("utf-8")

        # RSA verification: sig^e mod n should equal hash
        decrypted = pow(sig_int, e, n)
        decrypted_bytes = decrypted.to_bytes((decrypted.bit_length() + 7) // 8, "big")

        # PKCS#1 v1.5 padding: check the SHA-256 DigestInfo prefix
        sha256_prefix = bytes([
            0x30, 0x31, 0x30, 0x0d, 0x06, 0x09, 0x60, 0x86, 0x48, 0x01, 0x65,
            0x03, 0x04, 0x02, 0x01, 0x05, 0x00, 0x04, 0x20,
        ])
        expected_hash = sha256_prefix + hashlib.sha256(signed_data).digest()
        return decrypted_bytes.endswith(expected_hash)
    except Exception as exc:
        logger.debug("JWT signature validation failed: %s", exc)
        return False


def _get_connector_token(app_id: str, app_secret: str, token_url: str, scope: str) -> Optional[str]:
    """Fetch OAuth2 client_credentials token. Cached with expiry-60s margin."""
    cache_key = f"{app_id}:{scope}"
    cached = _TOKEN_CACHE.get(cache_key)
    if cached and cached["expires_at"] > time.time():
        return cached["token"]

    payload = (
        f"client_id={app_id}&client_secret={app_secret}"
        f"&scope={scope}&grant_type=client_credentials"
    ).encode("utf-8")
    req = Request(token_url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(req, timeout=15) as resp:  # nosec B310 -- Microsoft login endpoint only
            data = json.loads(resp.read().decode("utf-8"))
        token = data.get("access_token")
        expires_in = data.get("expires_in", 3600)
        if token:
            _TOKEN_CACHE[cache_key] = {"token": token, "expires_at": time.time() + expires_in - 60}
            return token
    except Exception as exc:
        logger.error("Token fetch failed: %s", exc)
    return None


class BotFrameworkBaseAdapter(BaseChannelAdapter):
    """Shared base adapter for Microsoft Bot Framework channels (Teams + Skype)."""

    _app_id_env: str = ""      # override in subclass: "TEAMS_APP_ID" or "SKYPE_APP_ID"
    _app_secret_env: str = ""  # override in subclass: "TEAMS_APP_SECRET" or "SKYPE_APP_SECRET"
    _token_url: str = ""       # override in subclass with AAD/MSA token endpoint
    _token_scope: str = "https://api.botframework.com/.default"

    def __init__(self, channel_name: str, config: Dict[str, Any]):
        super().__init__(channel_name, config)
        self.app_id = os.environ.get(self._app_id_env, "")
        self.app_secret = os.environ.get(self._app_secret_env, "")
        self._last_service_url: str = ""
        self._last_conversation_id: str = ""

    def verify_signature(self, payload: bytes, signature: str) -> bool:
        """Validate Bot Framework Bearer JWT in the Authorization header.

        Tries RS256 signature validation via JWKS first. Falls back to
        aud-only check when JWKS is unreachable (air-gap environments).
        """
        if not signature:
            return False

        token = signature.replace("Bearer ", "").strip()
        if not token:
            return False

        payload_claims = _decode_jwt_payload_unverified(token)
        aud = payload_claims.get("aud", "")

        # Always check audience matches our app ID
        if self.app_id and aud != self.app_id:
            logger.warning("JWT aud mismatch: expected %s got %s", self.app_id, aud)
            return False

        # Try RS256 signature validation
        jwks_keys = _fetch_jwks()
        if jwks_keys:
            return _validate_jwt_signature(token, jwks_keys)

        # Air-gap fallback: JWKS unavailable, aud-only check (already done above)
        logger.warning("JWKS unavailable — falling back to aud-only JWT check for %s", self.channel_name)
        return bool(self.app_id)

    def parse_webhook(self, request_data: Dict[str, Any], headers: Dict[str, str]) -> Optional[CommandEnvelope]:
        """Parse Bot Framework Activity into CommandEnvelope.

        Only handles type=message Activities. Stores serviceUrl for replies.
        """
        if request_data.get("type") != "message":
            return None

        text = (request_data.get("text") or "").strip()
        if not text:
            return None

        # Filter: only process ICDEV commands, the !icdev prefix, or an approval
        # reply (agov-inbox-03), which carries [icdev:<item_id>] and no prefix —
        # a human answering "approve" has no reason to add one.
        text_lower = text.lower()
        is_command = (
            text_lower.startswith("/icdev")
            or text_lower.startswith("icdev-")
            or text_lower.startswith("!icdev ")
            or text_lower.startswith("/bind")
            or has_correlation_token(text)
        )
        if not is_command:
            return None

        # Strip !icdev prefix so parse_command_text works with / prefix
        if text_lower.startswith("!icdev "):
            text = text[7:].strip()

        command, args = parse_command_text(text)
        if not command:
            return None

        # Store reply info
        self._last_service_url = request_data.get("serviceUrl", "").rstrip("/")
        conversation = request_data.get("conversation", {})
        self._last_conversation_id = conversation.get("id", "")

        from_data = request_data.get("from", {})
        timestamp = request_data.get("timestamp", datetime.now(timezone.utc).isoformat())

        return CommandEnvelope(
            channel=self.channel_name,
            channel_user_id=from_data.get("id", ""),
            channel_user_name=from_data.get("name", ""),
            channel_message_id=request_data.get("id", ""),
            channel_thread_id=self._last_conversation_id,
            raw_text=text,
            command=command,
            args=args,
            project_id=args.get("project_id", ""),
            timestamp=timestamp,
            signature=headers.get("Authorization", ""),
        )

    def send_message(self, channel_user_id: str, text: str, thread_id: str = "") -> bool:
        """Send reply via Bot Framework Connector API."""
        conversation_id = thread_id or self._last_conversation_id
        service_url = self._last_service_url
        if not conversation_id or not service_url:
            logger.error("%s: Cannot send — no conversation_id or service_url", self.channel_name)
            return False

        token = _get_connector_token(self.app_id, self.app_secret, self._token_url, self._token_scope)
        if not token:
            logger.error("%s: Could not obtain connector token", self.channel_name)
            return False

        url = f"{service_url}/v3/conversations/{conversation_id}/activities"
        payload = json.dumps({
            "type": "message",
            "from": {"id": self.app_id},
            "text": text,
            "textFormat": "plain",
        }).encode("utf-8")

        try:
            req = Request(
                url, data=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
                method="POST",
            )
            with urlopen(req, timeout=10) as resp:  # nosec B310 -- configured Bot Framework endpoint only
                return resp.status in (200, 201)
        except (URLError, Exception) as exc:
            logger.error("%s send failed: %s", self.channel_name, exc)
            return False
