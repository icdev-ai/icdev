#!/usr/bin/env python3
# CUI // SP-CTI
"""Jira Cloud DataBridge connector — projects, issues, comments, changelog.

What a modernization run wants from Jira is not a board: it is the SENTENCE
somebody wrote about why a thing changed. So `issues` carries the description
and `comments` carries the bodies, both flattened out of Atlassian Document
Format into text, and the issue's `changelog` is available because "this field
moved from X to Y on this date, by this person" is the most citable evidence a
tracker holds.

Tables:
    projects   — GET /rest/api/3/project/search
    issues     — POST /rest/api/3/search/jql   (filters: jql=..., fields=...)
    issue      — one issue by key (filters: key=PROJ-123)
    comments   — GET /rest/api/3/issue/{key}/comment   (filters: key=PROJ-123)
    changelog  — GET /rest/api/3/issue/{key}/changelog (filters: key=PROJ-123)
    fields     — GET /rest/api/3/field

Usage:
    python -m tools.databridge.connectors.jira_connector --health
    python -m tools.databridge.connectors.jira_connector --read issues --jql "project = ENG"
    python -m tools.databridge.connectors.jira_connector --read comments --key ENG-1 --json

Config (a connection record in args/databridge_connections.yaml):
    base_url         https://<site>.atlassian.net
    email            the Atlassian account the token belongs to
    auth_secret_ref  env:JIRA_API_TOKEN   (a REFERENCE, never a value)
"""

from __future__ import annotations

import json
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

logger = get_logger("databridge.jira")

_DEFAULT_FIELDS = (
    "summary,description,status,issuetype,priority,assignee,reporter,"
    "created,updated,resolutiondate,labels,components,project"
)


def adf_to_text(node: Any) -> str:
    """Atlassian Document Format -> readable text.

    ADF is a nested node tree, not markup, so this walks it rather than
    stripping tags: `text` nodes contribute their text, paragraphs and list
    items end a line, hard breaks break. A node type this does not know still
    contributes its children -- unknown structure loses its FORMATTING, never
    its words, which is the failure mode a citation can survive.

    A plain string is returned as-is: older Jira sites and some fields still
    serve wiki markup rather than ADF, and refusing it would drop the field.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(adf_to_text(child) for child in node)
    if not isinstance(node, dict):
        return ""

    kind = node.get("type", "")
    if kind == "text":
        return str(node.get("text", ""))
    if kind == "hardBreak":
        return "\n"

    inner = adf_to_text(node.get("content"))
    if kind in ("paragraph", "listItem", "heading", "blockquote", "codeBlock"):
        return inner.rstrip() + "\n"
    if kind in ("bulletList", "orderedList", "table", "tableRow"):
        return inner
    return inner


def _q(value: Any) -> str:
    return quote(str(value), safe="")


@register_connector
class JiraConnector(AtlassianBaseConnector):
    """Jira Cloud REST API v3."""

    _connector_name = "jira"
    _default_base_url = ""      # a site is operator-specific; there is no default
    _endpoints = {
        "projects": "/rest/api/3/project/search",
        "issues": "/rest/api/3/search/jql",
        "issue": "/rest/api/3/issue",
        "comments": "/rest/api/3/issue",
        "changelog": "/rest/api/3/issue",
        "fields": "/rest/api/3/field",
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
            data = self._http_get(f"{self._base_url}/rest/api/3/myself")
            return {
                "status": "healthy",
                "connector": self._connector_name,
                "site": self._base_url,
                "account_id": (data or {}).get("accountId", ""),
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
            if table == "issues":
                jql = filters.get("jql") or filters.get("q")
                if not jql:
                    return ConnectorResponse(
                        status="error",
                        errors=["table 'issues' needs filters={'jql': <JQL>}"],
                    )
                fields = str(filters.get("fields") or _DEFAULT_FIELDS).split(",")
                body = {
                    "jql": str(jql),
                    "maxResults": min(limit, 100),
                    "fields": [f.strip() for f in fields if f.strip()],
                }
                data = self._http_post(
                    f"{self._base_url}/rest/api/3/search/jql", body
                )
                rows = [
                    self._issue_row(i) for i in (data or {}).get("issues", [])
                ]

            elif table == "issue":
                key = self._require_key(filters)
                url = (
                    f"{self._base_url}/rest/api/3/issue/{_q(key)}"
                    f"?fields={_q(filters.get('fields') or _DEFAULT_FIELDS)}"
                )
                rows = [self._issue_row(self._http_get(url))]

            elif table == "comments":
                key = self._require_key(filters)
                url = (
                    f"{self._base_url}/rest/api/3/issue/{_q(key)}/comment"
                    f"?maxResults={min(limit, 100)}&orderBy=created"
                )
                data = self._http_get(url)
                rows = [
                    self._comment_row(key, c) for c in (data or {}).get("comments", [])
                ]

            elif table == "changelog":
                key = self._require_key(filters)
                url = (
                    f"{self._base_url}/rest/api/3/issue/{_q(key)}/changelog"
                    f"?maxResults={min(limit, 100)}"
                )
                data = self._http_get(url)
                rows = [
                    row
                    for entry in (data or {}).get("values", [])
                    for row in self._changelog_rows(key, entry)
                ]

            elif table == "projects":
                url = (
                    f"{self._base_url}/rest/api/3/project/search"
                    f"?maxResults={min(limit, 100)}"
                )
                data = self._http_get(url)
                rows = [
                    {
                        "project_id": str(p.get("id", "")),
                        "key": p.get("key", ""),
                        "name": p.get("name", ""),
                        "type": p.get("projectTypeKey", ""),
                        "url": p.get("self", ""),
                    }
                    for p in (data or {}).get("values", [])
                ]

            else:  # fields — a flat list, returned as the API gives it
                data = self._http_get(f"{self._base_url}/rest/api/3/field")
                rows = list(data) if isinstance(data, list) else []

            return ConnectorResponse(
                status="ok",
                data=rows,
                row_count=len(rows),
                duration_ms=int((time.time() - t0) * 1000),
                metadata={"table": table, "site": self._base_url},
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
    def _require_key(filters: Dict[str, Any]) -> str:
        key = str(filters.get("key") or filters.get("issue_key") or "").strip()
        if not key:
            raise ValueError("this table needs filters={'key': 'PROJ-123'}")
        return key

    # -- row shaping ----------------------------------------------------------

    def _issue_row(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        f = issue.get("fields") or {}
        description = adf_to_text(f.get("description")).strip()
        return {
            "issue_id": str(issue.get("id", "")),
            "key": issue.get("key", ""),
            "summary": f.get("summary", ""),
            # An issue with no description reads None, not "" -- a tracker full
            # of blank descriptions and a fetch that dropped the field look the
            # same otherwise.
            "description": description or None,
            "status": ((f.get("status") or {}).get("name")) or "",
            "issue_type": ((f.get("issuetype") or {}).get("name")) or "",
            "priority": ((f.get("priority") or {}).get("name")) or None,
            "assignee": ((f.get("assignee") or {}).get("displayName")) or None,
            "reporter": ((f.get("reporter") or {}).get("displayName")) or None,
            "project_key": ((f.get("project") or {}).get("key")) or "",
            "labels": list(f.get("labels") or []),
            "components": [
                c.get("name", "") for c in (f.get("components") or [])
            ],
            "created": f.get("created"),
            "updated": f.get("updated"),
            "resolved": f.get("resolutiondate"),
            "url": f"{self._base_url}/browse/{issue.get('key', '')}",
        }

    def _comment_row(self, key: str, comment: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "comment_id": str(comment.get("id", "")),
            "issue_key": key,
            "author": ((comment.get("author") or {}).get("displayName")) or "",
            "body_text": adf_to_text(comment.get("body")).strip() or None,
            "created": comment.get("created"),
            "updated": comment.get("updated"),
            "url": f"{self._base_url}/browse/{key}?focusedCommentId="
                   f"{comment.get('id', '')}",
        }

    def _changelog_rows(
        self, key: str, entry: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        author = ((entry.get("author") or {}).get("displayName")) or ""
        when = entry.get("created")
        return [
            {
                "issue_key": key,
                "changed_at": when,
                "author": author,
                "field": item.get("field", ""),
                "from_value": item.get("fromString"),
                "to_value": item.get("toString"),
            }
            for item in (entry.get("items") or [])
        ]

    # -- schema ---------------------------------------------------------------

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        if table_name in ("issues", "issue"):
            return SchemaDefinition(
                fields=[
                    SchemaField("issue_id", "utf8"),
                    SchemaField("key", "utf8"),
                    SchemaField("summary", "utf8"),
                    SchemaField("description", "utf8"),
                    SchemaField("status", "utf8"),
                    SchemaField("issue_type", "utf8"),
                    SchemaField("priority", "utf8"),
                    SchemaField("assignee", "utf8"),
                    SchemaField("reporter", "utf8"),
                    SchemaField("project_key", "utf8"),
                    SchemaField("labels", "json"),
                    SchemaField("components", "json"),
                    SchemaField("created", "utf8"),
                    SchemaField("updated", "utf8"),
                    SchemaField("resolved", "utf8"),
                    SchemaField("url", "utf8"),
                ],
                metadata={"source": "jira", "table": table_name},
            )
        if table_name == "comments":
            return SchemaDefinition(
                fields=[
                    SchemaField("comment_id", "utf8"),
                    SchemaField("issue_key", "utf8"),
                    SchemaField("author", "utf8"),
                    SchemaField("body_text", "utf8"),
                    SchemaField("created", "utf8"),
                    SchemaField("updated", "utf8"),
                    SchemaField("url", "utf8"),
                ],
                metadata={"source": "jira", "table": table_name},
            )
        if table_name == "changelog":
            return SchemaDefinition(
                fields=[
                    SchemaField("issue_key", "utf8"),
                    SchemaField("changed_at", "utf8"),
                    SchemaField("author", "utf8"),
                    SchemaField("field", "utf8"),
                    SchemaField("from_value", "utf8"),
                    SchemaField("to_value", "utf8"),
                ],
                metadata={"source": "jira", "table": table_name},
            )
        return super().infer_schema(table_name)

    def list_tables(self) -> List[str]:
        return list(self._endpoints.keys())


# -- CLI ---------------------------------------------------------------------

def _main() -> int:
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Jira DataBridge connector")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--read", metavar="TABLE")
    parser.add_argument("--jql", default="")
    parser.add_argument("--key", default="", metavar="PROJ-123")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    ns = parser.parse_args()

    connector = JiraConnector()
    if not connector.connect({
        "base_url": os.getenv("JIRA_BASE_URL", ""),
        "email": os.getenv("JIRA_EMAIL", ""),
        "auth_secret_ref": "env:JIRA_API_TOKEN",
    }):
        print("not connected: set JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN")
        return 2

    if ns.health:
        print(json.dumps(connector.health_check(), indent=2))
        return 0

    if not ns.read:
        parser.print_help()
        return 1

    filters: Dict[str, Any] = {}
    if ns.jql:
        filters["jql"] = ns.jql
    if ns.key:
        filters["key"] = ns.key

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
