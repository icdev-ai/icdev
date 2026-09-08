#!/usr/bin/env python3
# CUI // SP-CTI
"""Atlassian Cloud base connector — the auth Confluence and Jira share.

Both products sit on one identity: an account email plus an API token, sent as
HTTP Basic. Writing that twice would mean two places to fix when Atlassian
rotates the scheme, and the repo already made this call once for the brokers
(`icdev_fin/brokers/_http.py` — one transport, three venues), so the shared
half lives here and each product keeps only what is genuinely its own: its base
URL, its tables, and how it turns a page of its API into rows.

WHAT THIS DOES NOT DO: it does not invent a credential. `auth_secret_ref` is a
reference (`env:`/`vault:`/`aws:`/`file:`) resolved at USE time by
tools/databridge/connection_manager.py::resolve_secret, exactly as every other
connector's is, and a literal in the connection record is refused by the seeder
before it reaches here. A connector that cannot resolve its token REFUSES to
connect rather than falling back to an anonymous session -- an anonymous read
of a Confluence space is not a smaller version of an authenticated one, it is a
different and usually empty answer wearing the same shape.
"""

from __future__ import annotations

import base64
from typing import Any, Dict

from tools.databridge.connectors.saas_base import SaaSBaseConnector
from tools.logging.icdev_logger import get_logger

logger = get_logger("databridge.atlassian_base")


class AtlassianBaseConnector(SaaSBaseConnector):
    """HTTP Basic (email + API token) over the SaaS base's guarded transport."""

    # Subclasses set these.
    _connector_name: str = ""
    _default_base_url: str = ""

    def __init__(self) -> None:
        super().__init__()
        self._email: str = ""

    # -- credential -----------------------------------------------------------

    def _resolve_token(self, config: Dict[str, Any]) -> str:
        """Resolve the API token from its reference, or return ''.

        `api_token` accepts a REFERENCE, never a value. `auth_secret_ref` is the
        column the connection record carries and is preferred when both appear.
        """
        ref = config.get("auth_secret_ref") or config.get("api_token") or ""
        if not ref:
            return ""
        try:
            from tools.databridge.connection_manager import resolve_secret
            return resolve_secret(ref)
        except Exception as exc:  # noqa: BLE001 — an unresolvable ref is a refusal
            logger.error("%s: could not resolve the API token reference: %s",
                         self._connector_name, exc)
            return ""

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        email = config.get("email", "") or self._email
        token = self._resolve_token(config)
        if not (email and token):
            return {}
        pair = base64.b64encode(f"{email}:{token}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {pair}"}

    # -- connection lifecycle -------------------------------------------------

    def connect(self, config: Dict[str, Any]) -> bool:
        """Bind the site and the credential, then probe.

        The base class's `connect` treats a failed health check as a failed
        connection and that is kept: a site that answers 401 is not connected,
        and reporting it as connected would move the failure to the first read,
        where it reads as an empty result set rather than a refusal.
        """
        self._email = config.get("email", "")
        base = (config.get("base_url") or config.get("site_url") or "").rstrip("/")
        if not base:
            logger.error("%s: base_url (the Atlassian site, https://<site>."
                         "atlassian.net) is required", self._connector_name)
            return False
        if not self._email:
            logger.error("%s: email (the Atlassian account the token belongs to) "
                         "is required", self._connector_name)
            return False

        cfg = dict(config)
        cfg["base_url"] = base
        headers = self._build_auth_headers(cfg)
        if not headers:
            logger.error("%s: no usable API token; refusing to connect anonymously",
                         self._connector_name)
            return False

        self._config = cfg
        self._base_url = base
        self._auth_headers = headers
        self._connected = True

        health = self.health_check()
        if health.get("status") != "healthy":
            logger.error("%s: connected but the site refused the probe: %s",
                         self._connector_name, health.get("error"))
            self._connected = False
            return False
        return True
