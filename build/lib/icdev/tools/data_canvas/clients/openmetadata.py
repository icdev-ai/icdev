#!/usr/bin/env python3
# CUI // SP-CTI
"""OpenMetadata REST client — fetch tables and lineage.

Configuration (env vars):
    OPENMETADATA_HOST   Base URL of the OpenMetadata instance (default: http://localhost:8585)
    OPENMETADATA_TOKEN  JWT bearer token (optional for public instances)
    OPENMETADATA_TIMEOUT  HTTP timeout in seconds (default: 15)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class OpenMetadataError(Exception):
    """Raised on unrecoverable OpenMetadata API errors."""


class OpenMetadataClient:
    """Read-only client for the OpenMetadata REST API v1.

    Uses stdlib urllib only — no external dependencies.
    All public methods return empty collections on connection failure
    rather than raising, to support graceful degradation.
    """

    def __init__(
        self,
        host: str | None = None,
        token: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.base_url = (
            (host or os.environ.get("OPENMETADATA_HOST", "http://localhost:8585"))
            .rstrip("/")
        )
        self.token = token if token is not None else os.environ.get("OPENMETADATA_TOKEN", "")
        self.timeout = timeout if timeout is not None else int(
            os.environ.get("OPENMETADATA_TIMEOUT", "15")
        )

    # ── Low-level HTTP ────────────────────────────────────────────────────────

    def _request(self, method: str, path: str) -> Any:
        url = f"{self.base_url}/api/v1{path}"
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310 — URL is admin-configured OpenMetadata endpoint
                raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise OpenMetadataError(
                f"OM {method} {path} → HTTP {exc.code}: {body_text[:300]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise OpenMetadataError(
                f"OM connection error ({url}): {exc.reason}"
            ) from exc

    # ── Public API ────────────────────────────────────────────────────────────

    def list_tables(self, limit: int = 100) -> list[dict]:
        """Return all tables from OpenMetadata (paginated).

        Returns an empty list when the host is unreachable.
        """
        results: list[dict] = []
        after: str | None = None
        while True:
            params: dict[str, Any] = {"limit": limit, "include": "non-deleted"}
            if after:
                params["after"] = after
            qs = urllib.parse.urlencode(params)
            try:
                data = self._request("GET", f"/tables?{qs}")
            except OpenMetadataError:
                break
            results.extend(data.get("data", []))
            paging = data.get("paging", {})
            after = paging.get("after")
            if not after:
                break
        return results

    def get_lineage(self, table_id: str, upstream_depth: int = 1, downstream_depth: int = 1) -> dict:
        """Return the lineage graph for a table by its entity ID.

        Returns an empty dict when the host is unreachable or the entity is not found.
        """
        params = urllib.parse.urlencode(
            {"upstreamDepth": upstream_depth, "downstreamDepth": downstream_depth}
        )
        try:
            return self._request("GET", f"/lineage/table/{table_id}?{params}") or {}
        except OpenMetadataError:
            return {}
