# CUI // SP-CTI
"""tools/sharepoint — On-prem SharePoint Server REST client + ingestion.

Targets SharePoint Server 2016/2019/SE via ``/_api/web/*``. SharePoint
Online / M365 (needs Microsoft Graph + MSAL) is out of scope.

Phase E / P4.1 public surface:
  client.SharePointClient   — REST client (E2)
  ingest                    — list/library walker + DB persistence (E4)
  (blueprint at /api/v1/sharepoint/*)  — dashboard API (E5)

Config lives at args/sharepoint.yaml (E1).
"""
from __future__ import annotations

from tools.sharepoint.client import SharePointClient, SharePointAuthError, SharePointError
from tools.sharepoint.browser_fallback import fetch_classic_page, FallbackDisabledError

__all__ = [
    "SharePointClient",
    "SharePointAuthError",
    "SharePointError",
    "fetch_classic_page",
    "FallbackDisabledError",
]
