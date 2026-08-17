#!/usr/bin/env python3
"""RSS/Atom DataBridge Connector.

Fetches and parses RSS 2.0 and Atom feeds via feedparser.
No authentication required.

Normalizes each feed entry to:
  headline     — entry title
  body_excerpt — summary truncated to 500 chars
  signal_date  — ISO 8601 datetime from published_parsed
  source       — feed_id parameter (defaults to feed URL)

Usage (via DataBridge registry):
    from tools.databridge.registry import get_connector_instance
    conn = get_connector_instance("rss")
    conn.connect({})
    result = conn.read(ConnectorRequest(
        table_name="https://feeds.example.com/rss",
        filters={"feed_id": "example_news"},
        limit=20,
    ))

Usage (standalone CLI — module form; this file uses package-relative imports
and cannot be run as a bare path):
    python -m tools.databridge.connectors.rss_connector --url URL --json
    python -m tools.databridge.connectors.rss_connector --url URL --feed-id my_feed --limit 10 --json
    python -m tools.databridge.connectors.rss_connector --health --json

Egress: every feed fetch is guarded by tools/http/egress_guard.py (https-only,
deny-beats-allow, DNS resolve-then-range-check) and stopped outright in air-gap
mode, the same control saas_base applies. This connector fetched bare before —
and it is the one connector reachable by an agent through the DataBridge broker,
whose docstring advertises that guard as step 5 of its chain.
"""

from __future__ import annotations

import argparse
import json
import time
from calendar import timegm
from datetime import datetime, timezone
from typing import Any, Dict, List

from tools.logging.icdev_logger import get_logger  # noqa: E402

try:
    import feedparser
except ImportError as exc:  # pragma: no cover
    raise ImportError("feedparser is required: pip install feedparser") from exc

# Relative, not "from tools.databridge.registry import ...". This file is
# mirrored at tools/ and icdev/tools/, and those two package roots carry two
# DISTINCT registry module objects with two DISTINCT _REGISTRY dicts. An
# absolute "tools." import registers into the root copy no matter which mirror
# was imported, so the agent broker — which reads the icdev copy — never saw
# this connector. Registering into whichever registry is a sibling of this
# module is the only spelling that is correct from both.
from ..connector import (  # noqa: E402
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
    ConnectorType,
    DataConnector,
    SchemaDefinition,
    SchemaField,
)
from ..registry import register_connector  # noqa: E402

logger = get_logger("databridge.rss")

DEFAULT_MAX_ITEMS = 50
USER_AGENT = "ICDEV-DataBridge/1.0"


@register_connector
class RSSConnector(DataConnector):
    """RSS 2.0 and Atom feed connector.

    Normalizes feed entries with:
      headline     — entry title
      body_excerpt — summary[:500]
      signal_date  — ISO datetime from published_parsed (or updated_parsed)
      source       — feed_id parameter
    """

    _connector_name = "rss"

    def __init__(self) -> None:
        self._config: Dict[str, Any] = {}
        self._connected: bool = False

    # -- ABC properties -------------------------------------------------------

    @property
    def connector_name(self) -> str:
        return self._connector_name

    @property
    def connector_type(self) -> ConnectorType:
        return ConnectorType.SAAS_API

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_read=True,
            supports_write=False,
            supports_schema_inference=True,
            supports_incremental=True,
            max_batch_size=DEFAULT_MAX_ITEMS,
            supported_formats=["json"],
        )

    # -- Lifecycle ------------------------------------------------------------

    def connect(self, config: Dict[str, Any]) -> bool:
        self._config = config
        self._connected = True
        return True

    def disconnect(self) -> None:
        self._config = {}
        self._connected = False

    # -- Egress control -------------------------------------------------------
    #
    # feedparser.parse(url) opens a socket. Nothing checked the destination
    # first: this connector does not extend saas_base, so it inherited none of
    # that class's _guard_egress, and it is precisely the connector the agent
    # broker grants. The broker's own docstring lists "egress guard, applied in
    # saas_base before the socket opens" as step 5 of its chain — a claim that
    # was false for the only path an agent could take through it.
    #
    # Air-gap is an absolute stop; the guard is fail-closed on import failure,
    # because an unimportable guard means the destination is unchecked, which is
    # exactly when not to proceed. Redirects are NOT re-validated — feedparser
    # follows them itself — which is why the broker allowlists exact feed URLs
    # rather than accepting one from the agent.

    def _egress_config(self) -> Dict[str, Any]:
        return {
            "allowlist": list(self._config.get("egress_allowlist") or []),
            "denylist": list(self._config.get("egress_denylist") or []),
        }

    def _guard_egress(self, url: str) -> None:
        """Raise ``PermissionError`` unless *url* may be contacted."""
        try:
            from tools.airgap import is_airgap
            if is_airgap():
                raise PermissionError(
                    f"egress blocked: air-gap mode is active (target {url!r})"
                )
        except PermissionError:
            raise
        except Exception:  # noqa: BLE001 — airgap module optional
            pass

        try:
            from tools.http.egress_guard import egress_guard
        except Exception as exc:  # noqa: BLE001
            raise PermissionError(f"egress guard unavailable: {exc}") from exc

        allowed, reason, _ips = egress_guard(url, self._egress_config())
        if not allowed:
            raise PermissionError(f"egress blocked ({reason}) for {url!r}")

    def health_check(self) -> Dict[str, Any]:
        """Verify feedparser is importable; optionally probe a configured test URL."""
        test_url: str = self._config.get("health_url", "")
        if test_url:
            try:
                self._guard_egress(test_url)
                feed = feedparser.parse(test_url, agent=USER_AGENT, request_headers={"Connection": "close"})
                if feed.bozo and not feed.entries:
                    return {
                        "status": "unhealthy",
                        "error": str(feed.bozo_exception),
                        "connector": self._connector_name,
                    }
            except Exception as exc:
                return {
                    "status": "unhealthy",
                    "error": str(exc),
                    "connector": self._connector_name,
                }
        return {
            "status": "healthy",
            "connector": self._connector_name,
            "feedparser_version": feedparser.__version__,
        }

    # -- Data -----------------------------------------------------------------

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        """Fetch and parse an RSS 2.0 or Atom feed.

        request.table_name         — feed URL
        request.filters["feed_id"] — source identifier (defaults to feed URL)
        request.limit              — max items to return (default 50)
        """
        t0 = time.time()
        feed_url: str = request.table_name or request.query
        if not feed_url:
            return ConnectorResponse(
                status="error",
                errors=["feed URL required in table_name"],
            )

        max_items: int = request.limit if request.limit is not None else DEFAULT_MAX_ITEMS
        feed_id: str = (request.filters or {}).get("feed_id", feed_url)

        # Raised, not returned as an error row: the agent broker maps
        # PermissionError to a "blocked" denial and audits it, and a refused
        # egress must not be indistinguishable from a feed that failed to parse.
        self._guard_egress(feed_url)

        try:
            feed = feedparser.parse(
                feed_url,
                agent=USER_AGENT,
                request_headers={"Connection": "close"},
            )
        except Exception as exc:
            return ConnectorResponse(
                status="error",
                errors=[str(exc)],
                duration_ms=int((time.time() - t0) * 1000),
            )

        # An HTTP error is NOT an empty feed. feedparser returns a well-formed
        # object for a 404 — bozo False, entries [], no `version` key — so the
        # old code fell straight through to `feed.version` (AttributeError, a
        # missing key on a FeedParserDict) or, had that key existed, reported a
        # dead endpoint as a successful fetch of zero rows. That is the
        # difference between "the source published nothing" and "the URL is
        # gone", and only one of them is a defect to act on. Measured
        # 2026-08-17: the NIST CSRC publications feed this platform has been
        # configured against since it was written now 404s at every path while
        # csrc.nist.gov itself serves 200.
        #
        # `status` is absent when feedparser parsed a string or a local file
        # rather than fetching a URL; that is not an error.
        http_status = feed.get("status")
        if isinstance(http_status, int) and not (200 <= http_status < 300 or http_status == 304):
            return ConnectorResponse(
                status="error",
                errors=[f"HTTP {http_status} from {feed_url}"],
                duration_ms=int((time.time() - t0) * 1000),
                metadata={"feed_url": feed_url, "http_status": http_status},
            )

        # bozo=True means malformed XML, but entries may still be present
        if feed.bozo and not feed.entries:
            return ConnectorResponse(
                status="error",
                errors=[str(feed.bozo_exception)],
                duration_ms=int((time.time() - t0) * 1000),
            )

        rows = [self._normalize_entry(e, feed_id) for e in feed.entries[:max_items]]
        duration = int((time.time() - t0) * 1000)

        return ConnectorResponse(
            status="ok",
            data=rows,
            row_count=len(rows),
            duration_ms=duration,
            metadata={
                "feed_url": feed_url,
                "feed_id": feed_id,
                "feed_title": getattr(feed.feed, "title", ""),
                "total_entries": len(feed.entries),
                # .get, not .version — a FeedParserDict raises AttributeError
                # for an absent key, and the key IS absent on any response
                # feedparser did not manage to identify as a feed.
                "feed_version": feed.get("version") or "unknown",
                "http_status": http_status,
            },
        )

    # -- Helpers --------------------------------------------------------------

    def _normalize_entry(self, entry: Any, feed_id: str) -> Dict[str, Any]:
        """Normalize a feedparser entry to canonical signal fields."""
        headline: str = entry.get("title", "")
        body_excerpt: str = entry.get("summary", "")[:500]

        # published_parsed is time.struct_time (UTC); fall back to updated_parsed
        pub = entry.get("published_parsed") or entry.get("updated_parsed")
        if pub is not None:
            try:
                signal_date: str = datetime.fromtimestamp(timegm(pub), tz=timezone.utc).isoformat()
            except Exception:
                signal_date = ""
        else:
            signal_date = ""

        return {
            "headline": headline,
            "body_excerpt": body_excerpt,
            "signal_date": signal_date,
            "source": feed_id,
            "link": entry.get("link", ""),
            "entry_id": entry.get("id", ""),
            "author": entry.get("author", ""),
            "tags": [t.get("term", "") for t in entry.get("tags", [])],
        }

    def infer_schema(self, table_name: str) -> SchemaDefinition:
        return SchemaDefinition(
            fields=_RSS_SCHEMA_FIELDS,
            metadata={"source": self._connector_name, "table": table_name},
        )

    def list_tables(self) -> List[str]:
        return []


_RSS_SCHEMA_FIELDS: List[SchemaField] = [
    SchemaField(name="headline", data_type="utf8", description="Entry title"),
    SchemaField(name="body_excerpt", data_type="utf8", description="Summary truncated to 500 chars"),
    SchemaField(name="signal_date", data_type="utf8", description="ISO 8601 published datetime (UTC)"),
    SchemaField(name="source", data_type="utf8", description="Feed identifier"),
    SchemaField(name="link", data_type="utf8", description="Entry permalink URL"),
    SchemaField(name="entry_id", data_type="utf8", description="Entry unique identifier"),
    SchemaField(name="author", data_type="utf8", description="Entry author"),
    SchemaField(name="tags", data_type="json", description="Entry categories/tags"),
]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="RSS/Atom DataBridge Connector CLI")
    parser.add_argument("--url", metavar="URL", help="Feed URL to fetch")
    parser.add_argument("--feed-id", metavar="ID", help="Source identifier (defaults to URL)")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_MAX_ITEMS,
        help=f"Max items (default: {DEFAULT_MAX_ITEMS})",
    )
    parser.add_argument("--health", action="store_true", help="Health check")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    conn = RSSConnector()
    conn.connect({})

    if args.health:
        result = conn.health_check()
    elif args.url:
        filters: Dict[str, Any] = {}
        if args.feed_id:
            filters["feed_id"] = args.feed_id
        resp = conn.read(ConnectorRequest(
            table_name=args.url,
            limit=args.limit,
            filters=filters,
        ))
        result = {
            "status": resp.status,
            "data": resp.data,
            "row_count": resp.row_count,
            "errors": resp.errors,
            "duration_ms": resp.duration_ms,
            "metadata": resp.metadata,
        }
    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
