#!/usr/bin/env python3
# CUI // SP-CTI
"""SharePoint Online DataBridge connector, over Microsoft Graph.

SharePoint is where the current version of a policy actually lives in most
organisations, so the table that matters here is `file_content`: given a drive
item, return the BYTES decoded to text. Everything else -- sites, drives, the
item listing, search -- exists to find that item.

Auth is OAuth2 CLIENT CREDENTIALS (an app registration with
`Sites.Read.All` / `Files.Read.All` application permission), not a user token.
The client secret is a REFERENCE resolved at use time, never a value in the
connection record, and the token endpoint is contacted through the same egress
guard as every other call this connector makes -- an unguarded token fetch
would be a hole in exactly the control the read path is careful about.

The token is cached in memory with its expiry and refreshed with a 60-second
margin. It is never logged, never returned by `health_check`, and never written
anywhere.

Tables:
    sites         — GET /sites?search=          (filters: search=)
    site          — one site by id or hostname:path (filters: id= | path=)
    drives        — GET /sites/{site_id}/drives (filters: site_id=)
    items         — children of a drive folder  (filters: drive_id=, item_id=|path=)
    file_content  — the text of one file        (filters: drive_id=, item_id=)
    search        — GET /search/query           (filters: q=)
    lists         — GET /sites/{site_id}/lists  (filters: site_id=)

Usage:
    python tools/databridge/connectors/sharepoint_connector.py --health
    python tools/databridge/connectors/sharepoint_connector.py --read sites --search policy
    python tools/databridge/connectors/sharepoint_connector.py --read file_content \
        --drive <drive id> --item <item id>

Config (a connection record in args/databridge_connections.yaml):
    tenant_id        the Entra ID directory (GUID)
    client_id        the app registration
    auth_secret_ref  env:SHAREPOINT_CLIENT_SECRET  (a REFERENCE, never a value)
    base_url         https://graph.microsoft.com/v1.0   (the default)
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict, List
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from icdev.core.paths import repo_root

# The ONE resolver. A module that walks `parent.parent.parent.parent` from its
# own location carries a hard-coded claim about where it sits, and the claim
# breaks silently the moment the file moves (tools/ci/self_root_census.py).
BASE_DIR = repo_root(__file__)
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.databridge.connector import (  # noqa: E402
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
    SchemaDefinition,
    SchemaField,
)
from tools.databridge.connectors.saas_base import (  # noqa: E402
    REQUEST_TIMEOUT,
    USER_AGENT,
    SaaSBaseConnector,
)
from tools.databridge.registry import register_connector  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("databridge.sharepoint")

GRAPH_DEFAULT = "https://graph.microsoft.com/v1.0"
LOGIN_HOST = "https://login.microsoftonline.com"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
TOKEN_MARGIN_S = 60

# Content types this connector will decode to text. A .docx or a .pdf is bytes
# whose text needs an extractor, and this connector does not have one -- it
# says so by name rather than returning mojibake that reads like content.
_TEXT_TYPES = (
    "text/", "application/json", "application/xml", "application/xhtml",
    "application/javascript", "+json", "+xml",
)
_TEXT_SUFFIXES = (
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml", ".yaml",
    ".yml", ".html", ".htm", ".log", ".ini", ".cfg", ".rst",
)


def _q(value: Any) -> str:
    return quote(str(value), safe="")


def is_decodable(name: str, content_type: str) -> bool:
    """Can this connector turn the item's bytes into text on its own?"""
    ct = (content_type or "").lower()
    if any(marker in ct for marker in _TEXT_TYPES):
        return True
    return any((name or "").lower().endswith(sfx) for sfx in _TEXT_SUFFIXES)


@register_connector
class SharePointConnector(SaaSBaseConnector):
    """SharePoint Online through Microsoft Graph, app-only."""

    _connector_name = "sharepoint"
    _default_base_url = GRAPH_DEFAULT
    _endpoints = {
        "sites": "/sites",
        "site": "/sites",
        "drives": "/drives",
        "items": "/items",
        "file_content": "/content",
        "search": "/search/query",
        "lists": "/lists",
    }

    def __init__(self) -> None:
        super().__init__()
        self._token: str = ""
        self._token_expires_at: float = 0.0

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=False,   # this connector READS; writing back is its own card
            supports_schema_inference=True,
            supports_incremental=False,
            max_batch_size=200,
            supported_formats=["json", "text"],
        )

    # -- auth -----------------------------------------------------------------

    def _resolve_secret(self, config: Dict[str, Any]) -> str:
        ref = config.get("auth_secret_ref") or config.get("client_secret") or ""
        if not ref:
            return ""
        try:
            from tools.databridge.connection_manager import resolve_secret
            return resolve_secret(ref)
        except Exception as exc:  # noqa: BLE001 — an unresolvable ref is a refusal
            logger.error("sharepoint: could not resolve the client secret "
                         "reference: %s", exc)
            return ""

    def _token_url(self) -> str:
        tenant = self._config.get("tenant_id", "")
        host = self._config.get("login_url", LOGIN_HOST).rstrip("/")
        return f"{host}/{_q(tenant)}/oauth2/v2.0/token"

    def _fetch_token(self) -> str:
        """Client-credentials grant. Guarded like every other outbound call."""
        secret = self._resolve_secret(self._config)
        client_id = self._config.get("client_id", "")
        if not (secret and client_id and self._config.get("tenant_id")):
            raise PermissionError(
                "sharepoint: tenant_id, client_id and a resolvable "
                "auth_secret_ref are all required"
            )

        url = self._token_url()
        body = urlencode({
            "client_id": client_id,
            "client_secret": secret,
            "scope": self._config.get("scope", GRAPH_SCOPE),
            "grant_type": "client_credentials",
        }).encode("utf-8")

        req = Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        self._guard_egress(url)
        timeout = self._config.get("timeout", REQUEST_TIMEOUT)
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310 -- destination validated by _guard_egress
            payload = json.loads(resp.read().decode("utf-8"))

        token = payload.get("access_token", "")
        if not token:
            # The error body can carry the secret back in some failure modes,
            # so only the code is surfaced.
            raise PermissionError(
                "sharepoint: the token endpoint returned no access_token "
                f"(error={payload.get('error', 'unknown')!r})"
            )
        self._token = token
        self._token_expires_at = time.time() + float(
            payload.get("expires_in", 3600)
        )
        return token

    def _ensure_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - TOKEN_MARGIN_S:
            return self._token
        return self._fetch_token()

    def _build_auth_headers(self, config: Dict[str, Any]) -> Dict[str, str]:
        # The base class calls this once at connect; the live header is rebuilt
        # per request in _http_get so an expired token refreshes itself.
        try:
            return {"Authorization": f"Bearer {self._ensure_token()}"}
        except Exception as exc:  # noqa: BLE001
            logger.error("sharepoint: token acquisition failed: %s", exc)
            return {}

    def _http_get(self, url: str) -> Any:
        self._auth_headers = {"Authorization": f"Bearer {self._ensure_token()}"}
        return super()._http_get(url)

    # -- connection lifecycle -------------------------------------------------

    def connect(self, config: Dict[str, Any]) -> bool:
        cfg = dict(config)
        cfg["base_url"] = (cfg.get("base_url") or GRAPH_DEFAULT).rstrip("/")
        if not (cfg.get("tenant_id") and cfg.get("client_id")):
            logger.error("sharepoint: tenant_id and client_id are required")
            return False
        self._config = cfg
        self._base_url = cfg["base_url"]
        self._token = ""
        self._token_expires_at = 0.0

        try:
            self._ensure_token()
        except Exception as exc:  # noqa: BLE001
            logger.error("sharepoint: refusing to connect — %s", exc)
            self._connected = False
            return False

        self._connected = True
        health = self.health_check()
        if health.get("status") != "healthy":
            logger.error("sharepoint: authenticated but Graph refused the probe: "
                         "%s", health.get("error"))
            self._connected = False
            return False
        return True

    def disconnect(self) -> None:
        self._token = ""
        self._token_expires_at = 0.0
        super().disconnect()

    def health_check(self) -> Dict[str, Any]:
        try:
            data = self._http_get(f"{self._base_url}/sites?search=&$top=1")
            return {
                "status": "healthy",
                "connector": self._connector_name,
                "graph": self._base_url,
                "tenant_id": self._config.get("tenant_id", ""),
                "sites_visible": len((data or {}).get("value", [])),
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
            if table == "sites":
                term = filters.get("search", "")
                url = (f"{self._base_url}/sites?search={_q(term)}"
                       f"&$top={min(limit, 200)}")
                rows = [self._site_row(s)
                        for s in (self._http_get(url) or {}).get("value", [])]

            elif table == "site":
                ident = filters.get("id") or filters.get("path")
                if not ident:
                    raise ValueError(
                        "table 'site' needs filters={'id': ...} or "
                        "{'path': 'host:/sites/Name'}"
                    )
                url = f"{self._base_url}/sites/{_q(ident)}"
                rows = [self._site_row(self._http_get(url))]

            elif table == "drives":
                site_id = self._require(filters, "site_id")
                url = f"{self._base_url}/sites/{_q(site_id)}/drives"
                rows = [self._drive_row(d)
                        for d in (self._http_get(url) or {}).get("value", [])]

            elif table == "items":
                drive_id = self._require(filters, "drive_id")
                item_id = filters.get("item_id")
                path = filters.get("path")
                if item_id:
                    url = (f"{self._base_url}/drives/{_q(drive_id)}/items/"
                           f"{_q(item_id)}/children")
                elif path:
                    url = (f"{self._base_url}/drives/{_q(drive_id)}/root:/"
                           f"{quote(str(path).lstrip('/'))}:/children")
                else:
                    url = f"{self._base_url}/drives/{_q(drive_id)}/root/children"
                url += f"?$top={min(limit, 200)}"
                rows = [self._item_row(i)
                        for i in (self._http_get(url) or {}).get("value", [])]

            elif table == "file_content":
                drive_id = self._require(filters, "drive_id")
                item_id = self._require(filters, "item_id")
                rows = [self._file_content_row(drive_id, item_id)]

            elif table == "search":
                term = filters.get("q") or filters.get("search")
                if not term:
                    raise ValueError("table 'search' needs filters={'q': <query>}")
                body = {
                    "requests": [{
                        "entityTypes": ["driveItem"],
                        "query": {"queryString": str(term)},
                        "size": min(limit, 200),
                    }]
                }
                self._auth_headers = {
                    "Authorization": f"Bearer {self._ensure_token()}"
                }
                data = self._http_post(f"{self._base_url}/search/query", body)
                rows = self._search_rows(data)

            else:  # lists
                site_id = self._require(filters, "site_id")
                url = f"{self._base_url}/sites/{_q(site_id)}/lists"
                rows = list((self._http_get(url) or {}).get("value", []))

            return ConnectorResponse(
                status="ok",
                data=rows,
                row_count=len(rows),
                duration_ms=int((time.time() - t0) * 1000),
                metadata={"table": table, "graph": self._base_url},
            )
        except ValueError as exc:      # a missing required filter, named
            return ConnectorResponse(
                status="error",
                errors=[str(exc)],
                duration_ms=int((time.time() - t0) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            return ConnectorResponse(
                status="error",
                errors=[str(exc)],
                duration_ms=int((time.time() - t0) * 1000),
            )

    @staticmethod
    def _require(filters: Dict[str, Any], name: str) -> str:
        value = str(filters.get(name) or "").strip()
        if not value:
            raise ValueError(f"this table needs filters={{{name!r}: ...}}")
        return value

    # -- row shaping ----------------------------------------------------------

    def _site_row(self, site: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "site_id": site.get("id", ""),
            "name": site.get("name", "") or site.get("displayName", ""),
            "display_name": site.get("displayName", ""),
            "web_url": site.get("webUrl", ""),
            "created": site.get("createdDateTime"),
            "last_modified": site.get("lastModifiedDateTime"),
        }

    def _drive_row(self, drive: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "drive_id": drive.get("id", ""),
            "name": drive.get("name", ""),
            "drive_type": drive.get("driveType", ""),
            "web_url": drive.get("webUrl", ""),
        }

    def _item_row(self, item: Dict[str, Any]) -> Dict[str, Any]:
        file_facet = item.get("file") or {}
        content_type = file_facet.get("mimeType", "")
        name = item.get("name", "")
        return {
            "item_id": item.get("id", ""),
            "name": name,
            "is_folder": "folder" in item,
            "size": item.get("size"),
            "content_type": content_type or None,
            "web_url": item.get("webUrl", ""),
            "created": item.get("createdDateTime"),
            "last_modified": item.get("lastModifiedDateTime"),
            "last_modified_by": (
                ((item.get("lastModifiedBy") or {}).get("user") or {})
                .get("displayName")
            ),
            "sha256": (file_facet.get("hashes") or {}).get("quickXorHash"),
            "text_readable": (
                False if "folder" in item else is_decodable(name, content_type)
            ),
        }

    def _file_content_row(self, drive_id: str, item_id: str) -> Dict[str, Any]:
        meta = self._http_get(
            f"{self._base_url}/drives/{_q(drive_id)}/items/{_q(item_id)}"
        )
        row = self._item_row(meta or {})
        row["drive_id"] = drive_id

        if row["is_folder"]:
            row["text"] = None
            row["text_status"] = "is_a_folder"
            return row

        if not is_decodable(row["name"], row.get("content_type") or ""):
            # NOT an error and NOT empty text. A .docx or .pdf holds real
            # content this connector cannot read; saying "needs_extractor"
            # sends the caller to document_intelligence's ingest path instead
            # of handing it bytes that decode to noise.
            row["text"] = None
            row["text_status"] = "needs_extractor"
            return row

        url = f"{self._base_url}/drives/{_q(drive_id)}/items/{_q(item_id)}/content"
        self._guard_egress(url)
        req = Request(
            url,
            headers={
                "Authorization": f"Bearer {self._ensure_token()}",
                "User-Agent": USER_AGENT,
            },
            method="GET",
        )
        timeout = self._config.get("timeout", REQUEST_TIMEOUT)
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310 -- destination validated by _guard_egress
            raw = resp.read()
        row["text"] = raw.decode("utf-8", errors="replace")
        row["text_status"] = "ok"
        row["byte_size"] = len(raw)
        return row

    def _search_rows(self, data: Any) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for response in (data or {}).get("value", []):
            for container in response.get("hitsContainers", []):
                for hit in container.get("hits", []):
                    resource = hit.get("resource") or {}
                    rows.append({
                        "item_id": resource.get("id", ""),
                        "name": resource.get("name", ""),
                        "web_url": resource.get("webUrl", ""),
                        "summary": hit.get("summary"),
                        "last_modified": resource.get("lastModifiedDateTime"),
                        "drive_id": (
                            (resource.get("parentReference") or {}).get("driveId")
                        ),
                    })
        return rows

    # -- schema ---------------------------------------------------------------

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        if table_name == "items":
            return SchemaDefinition(
                fields=[
                    SchemaField("item_id", "utf8"),
                    SchemaField("name", "utf8"),
                    SchemaField("is_folder", "bool"),
                    SchemaField("size", "int64"),
                    SchemaField("content_type", "utf8"),
                    SchemaField("web_url", "utf8"),
                    SchemaField("created", "utf8"),
                    SchemaField("last_modified", "utf8"),
                    SchemaField("last_modified_by", "utf8"),
                    SchemaField("sha256", "utf8"),
                    SchemaField("text_readable", "bool"),
                ],
                metadata={"source": "sharepoint", "table": table_name},
            )
        if table_name == "file_content":
            return SchemaDefinition(
                fields=[
                    SchemaField("item_id", "utf8"),
                    SchemaField("drive_id", "utf8"),
                    SchemaField("name", "utf8"),
                    SchemaField("content_type", "utf8"),
                    SchemaField("text", "utf8"),
                    SchemaField("text_status", "utf8"),
                    SchemaField("byte_size", "int64"),
                    SchemaField("web_url", "utf8"),
                    SchemaField("last_modified", "utf8"),
                ],
                metadata={"source": "sharepoint", "table": table_name},
            )
        if table_name in ("sites", "site"):
            return SchemaDefinition(
                fields=[
                    SchemaField("site_id", "utf8"),
                    SchemaField("name", "utf8"),
                    SchemaField("display_name", "utf8"),
                    SchemaField("web_url", "utf8"),
                    SchemaField("created", "utf8"),
                    SchemaField("last_modified", "utf8"),
                ],
                metadata={"source": "sharepoint", "table": table_name},
            )
        return super().infer_schema(table_name)

    def list_tables(self) -> List[str]:
        return list(self._endpoints.keys())


# -- CLI ---------------------------------------------------------------------

def _main() -> int:
    import argparse
    import os

    parser = argparse.ArgumentParser(description="SharePoint DataBridge connector")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--read", metavar="TABLE")
    parser.add_argument("--search", default="")
    parser.add_argument("--site", default="", metavar="SITE_ID")
    parser.add_argument("--drive", default="", metavar="DRIVE_ID")
    parser.add_argument("--item", default="", metavar="ITEM_ID")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    ns = parser.parse_args()

    connector = SharePointConnector()
    if not connector.connect({
        "tenant_id": os.getenv("SHAREPOINT_TENANT_ID", ""),
        "client_id": os.getenv("SHAREPOINT_CLIENT_ID", ""),
        "auth_secret_ref": "env:SHAREPOINT_CLIENT_SECRET",
    }):
        print(
            "not connected: set SHAREPOINT_TENANT_ID, SHAREPOINT_CLIENT_ID and "
            "SHAREPOINT_CLIENT_SECRET"
        )
        return 2

    if ns.health:
        print(json.dumps(connector.health_check(), indent=2))
        return 0

    if not ns.read:
        parser.print_help()
        return 1

    filters: Dict[str, Any] = {}
    if ns.search:
        filters["search"] = ns.search
        filters["q"] = ns.search
    if ns.site:
        filters["site_id"] = ns.site
    if ns.drive:
        filters["drive_id"] = ns.drive
    if ns.item:
        filters["item_id"] = ns.item

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
