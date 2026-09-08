#!/usr/bin/env python3
# CUI // SP-CTI
"""Confluence Cloud DataBridge connector — spaces, pages, and page BODIES.

The reason this connector exists is the body. A modernization run that wants to
say "the current text of this policy lives in Confluence" needs the prose, not
a page id and a title, so `pages` carries `body_storage` (Confluence's storage
format) and a `body_text` with the markup stripped -- and when the caller did
not ask for the body, both fields are None rather than empty strings, because
"nobody asked" and "the page is empty" are different facts and a redraft built
on the second when the first was true is a redraft built on nothing.

Tables:
    spaces       — GET /wiki/api/v2/spaces
    pages        — GET /wiki/api/v2/pages         (filters: space_id, title, with_body)
    page         — one page by id, always with its body (filters: id=<page id>)
    search       — GET /wiki/rest/api/search      (filters: cql=<CQL>)
    attachments  — GET /wiki/api/v2/attachments

Usage:
    python -m tools.databridge.connectors.confluence_connector --health
    python -m tools.databridge.connectors.confluence_connector --read spaces --json
    python -m tools.databridge.connectors.confluence_connector --read page --id 12345

Config (a connection record in args/databridge_connections.yaml):
    base_url         https://<site>.atlassian.net
    email            the Atlassian account the token belongs to
    auth_secret_ref  env:CONFLUENCE_API_TOKEN   (a REFERENCE, never a value)
"""

from __future__ import annotations

import html
import json
import re
import time
from typing import Any, Dict, List
from urllib.parse import quote

from tools.databridge.connector import (
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
    SchemaDefinition,
    SchemaField,
)
from tools.databridge.connectors.atlassian_base import AtlassianBaseConnector
from tools.databridge.registry import register_connector
from tools.logging.icdev_logger import get_logger

logger = get_logger("databridge.confluence")

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")
_BLOCK_END = re.compile(
    r"</(p|div|li|h[1-6]|tr|td|th|blockquote|pre)\s*>", re.IGNORECASE
)
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)


def storage_to_text(markup: str) -> str:
    """Confluence storage format -> readable text.

    Deliberately a small, dependency-free reduction and NOT a parser: block ends
    become newlines, tags are dropped, entities are unescaped. A macro's
    parameters vanish with its tags, which is the right trade for evidence text
    -- what a reviewer reads is the prose, and a half-rendered macro body is
    worse than none. Callers that need fidelity keep `body_storage`, returned
    verbatim beside this.
    """
    if not markup:
        return ""
    text = _BLOCK_END.sub("\n", markup)
    text = _BR.sub("\n", text)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()


def _q(value: str) -> str:
    return quote(str(value), safe="")


@register_connector
class ConfluenceConnector(AtlassianBaseConnector):
    """Confluence Cloud REST (v2 where it exists, v1 for search)."""

    _connector_name = "confluence"
    _default_base_url = ""      # a site is operator-specific; there is no default
    _endpoints = {
        "spaces": "/wiki/api/v2/spaces",
        "pages": "/wiki/api/v2/pages",
        "page": "/wiki/api/v2/pages",
        "search": "/wiki/rest/api/search",
        "attachments": "/wiki/api/v2/attachments",
    }

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=False,   # this connector READS; writing back is its own card
            supports_schema_inference=True,
            supports_incremental=False,
            max_batch_size=100,
            supported_formats=["json"],
        )

    def health_check(self) -> Dict[str, Any]:
        try:
            data = self._http_get(f"{self._base_url}/wiki/api/v2/spaces?limit=1")
            results = (data or {}).get("results", [])
            return {
                "status": "healthy",
                "connector": self._connector_name,
                "site": self._base_url,
                "spaces_visible": len(results),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "status": "unhealthy",
                "connector": self._connector_name,
                "error": str(exc),
            }

    # -- read -----------------------------------------------------------------

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        t0 = time.time()
        table = request.table_name or request.query
        filters = dict(request.filters or {})
        limit = int(request.limit or 25)

        if table not in self._endpoints:
            return ConnectorResponse(
                status="error",
                errors=[f"Unknown table: {table!r}. Available: {self.list_tables()}"],
            )

        try:
            if table == "page":
                page_id = str(filters.get("id") or filters.get("page_id") or "").strip()
                if not page_id:
                    return ConnectorResponse(
                        status="error",
                        errors=["table 'page' needs filters={'id': <page id>}"],
                    )
                url = (
                    f"{self._base_url}/wiki/api/v2/pages/{_q(page_id)}"
                    "?body-format=storage"
                )
                rows: List[Dict[str, Any]] = [
                    self._page_row(self._http_get(url), with_body=True)
                ]

            elif table == "pages":
                params = [f"limit={min(limit, 250)}"]
                for key in ("space_id", "status", "title", "sort"):
                    if key in filters:
                        params.append(
                            f"{key.replace('_', '-')}={_q(filters[key])}"
                        )
                want_body = str(filters.get("with_body", "")).lower() in (
                    "1", "true", "yes",
                )
                if want_body:
                    params.append("body-format=storage")
                url = f"{self._base_url}/wiki/api/v2/pages?" + "&".join(params)
                data = self._http_get(url)
                rows = [
                    self._page_row(p, with_body=want_body)
                    for p in (data or {}).get("results", [])
                ]

            elif table == "search":
                cql = filters.get("cql") or filters.get("q")
                if not cql:
                    return ConnectorResponse(
                        status="error",
                        errors=["table 'search' needs filters={'cql': <CQL>}"],
                    )
                url = (
                    f"{self._base_url}/wiki/rest/api/search"
                    f"?cql={_q(cql)}&limit={min(limit, 100)}"
                )
                data = self._http_get(url)
                rows = [self._search_row(r) for r in (data or {}).get("results", [])]

            else:  # spaces, attachments — plain v2 collections
                url = (
                    f"{self._base_url}{self._endpoints[table]}"
                    f"?limit={min(limit, 250)}"
                )
                data = self._http_get(url)
                rows = list((data or {}).get("results", []))

            return ConnectorResponse(
                status="ok",
                data=rows,
                row_count=len(rows),
                duration_ms=int((time.time() - t0) * 1000),
                metadata={"table": table, "site": self._base_url},
            )
        except Exception as exc:  # noqa: BLE001
            return ConnectorResponse(
                status="error",
                errors=[str(exc)],
                duration_ms=int((time.time() - t0) * 1000),
            )

    # -- row shaping ----------------------------------------------------------

    def _page_row(self, page: Dict[str, Any], *, with_body: bool) -> Dict[str, Any]:
        body = ((page.get("body") or {}).get("storage") or {}).get("value")
        # A body that was NOT requested is None, not "". The difference decides
        # whether a caller believes it holds the current text of a document.
        storage = body if with_body else None
        version = page.get("version") or {}
        return {
            "page_id": str(page.get("id", "")),
            "title": page.get("title", ""),
            "space_id": str(page.get("spaceId", "")),
            "status": page.get("status", ""),
            "version": version.get("number"),
            "version_at": version.get("createdAt"),
            "author_id": page.get("authorId", ""),
            "url": self._page_url(page),
            "body_storage": storage,
            "body_text": storage_to_text(storage) if storage else None,
        }

    def _page_url(self, page: Dict[str, Any]) -> str:
        webui = (page.get("_links") or {}).get("webui") or ""
        return f"{self._base_url}/wiki{webui}" if webui else ""

    def _search_row(self, hit: Dict[str, Any]) -> Dict[str, Any]:
        content = hit.get("content") or {}
        return {
            "page_id": str(content.get("id", "")),
            "title": hit.get("title") or content.get("title", ""),
            "type": content.get("type", ""),
            "excerpt": storage_to_text(hit.get("excerpt", "")) or None,
            "last_modified": hit.get("lastModified"),
            "url": (
                f"{self._base_url}/wiki{hit.get('url', '')}" if hit.get("url") else ""
            ),
        }

    # -- schema ---------------------------------------------------------------

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        if table_name in ("pages", "page"):
            return SchemaDefinition(
                fields=[
                    SchemaField("page_id", "utf8"),
                    SchemaField("title", "utf8"),
                    SchemaField("space_id", "utf8"),
                    SchemaField("status", "utf8"),
                    SchemaField("version", "int64"),
                    SchemaField("version_at", "utf8"),
                    SchemaField("author_id", "utf8"),
                    SchemaField("url", "utf8"),
                    SchemaField("body_storage", "utf8"),
                    SchemaField("body_text", "utf8"),
                ],
                metadata={"source": "confluence", "table": table_name},
            )
        if table_name == "search":
            return SchemaDefinition(
                fields=[
                    SchemaField("page_id", "utf8"),
                    SchemaField("title", "utf8"),
                    SchemaField("type", "utf8"),
                    SchemaField("excerpt", "utf8"),
                    SchemaField("last_modified", "utf8"),
                    SchemaField("url", "utf8"),
                ],
                metadata={"source": "confluence", "table": table_name},
            )
        return super().infer_schema(table_name)

    def list_tables(self) -> List[str]:
        return list(self._endpoints.keys())


# -- CLI ---------------------------------------------------------------------

def _main() -> int:
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Confluence DataBridge connector")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--read", metavar="TABLE")
    parser.add_argument("--id", metavar="PAGE_ID", default="")
    parser.add_argument("--cql", default="")
    parser.add_argument("--space", default="")
    parser.add_argument("--body", action="store_true", help="include page bodies")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    ns = parser.parse_args()

    connector = ConfluenceConnector()
    if not connector.connect({
        "base_url": os.getenv("CONFLUENCE_BASE_URL", ""),
        "email": os.getenv("CONFLUENCE_EMAIL", ""),
        "auth_secret_ref": "env:CONFLUENCE_API_TOKEN",
    }):
        print(
            "not connected: set CONFLUENCE_BASE_URL, CONFLUENCE_EMAIL and "
            "CONFLUENCE_API_TOKEN"
        )
        return 2

    if ns.health:
        print(json.dumps(connector.health_check(), indent=2))
        return 0

    if not ns.read:
        parser.print_help()
        return 1

    filters: Dict[str, Any] = {}
    if ns.id:
        filters["id"] = ns.id
    if ns.cql:
        filters["cql"] = ns.cql
    if ns.space:
        filters["space_id"] = ns.space
    if ns.body:
        filters["with_body"] = "true"

    resp = connector.read(
        ConnectorRequest(table_name=ns.read, filters=filters, limit=ns.limit)
    )
    if ns.json:
        print(json.dumps(
            {
                "status": resp.status,
                "row_count": resp.row_count,
                "errors": resp.errors,
                "data": resp.data,
            },
            indent=2,
            default=str,
        ))
    else:
        print(f"{resp.status}  {resp.row_count} row(s)  {resp.errors or ''}")
        for row in resp.data or []:
            print(row)
    return 0 if resp.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(_main())
