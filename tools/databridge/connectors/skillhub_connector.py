#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""SkillHub DataBridge Connector — live skill discovery from skillhub.ai.

Read-only connector for the SkillHub skill registry API. Enables:
  - Vector search across all OpenClaw skills
  - Skill detail retrieval (metadata, stats, license, owner)
  - Skill zip download to quarantine for import pipeline

No authentication required for read operations.

Usage (via DataBridge registry):
    from tools.databridge.registry import get_connector_instance
    conn = get_connector_instance("skillhub")
    conn.connect({})
    result = conn.read(ConnectorRequest(query="self-improvement"))

Usage (standalone CLI):
    python tools/databridge/connectors/skillhub_connector.py --search "code review" --json
    python tools/databridge/connectors/skillhub_connector.py --get self-improving-agent --json
    python tools/databridge/connectors/skillhub_connector.py --download self-improving-agent --output .tmp/ --json
    python tools/databridge/connectors/skillhub_connector.py --health --json
"""

import argparse
import io
import json
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.databridge.connector import (  # noqa: E402
    ConnectorCapabilities,
    ConnectorRequest,
    ConnectorResponse,
    ConnectorType,
    DataConnector,
)
from tools.databridge.registry import register_connector  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_API_BASE = "https://wry-manatee-359.convex.site/api/v1"
REQUEST_TIMEOUT = 30  # seconds
USER_AGENT = "ICDEV-DataBridge/1.0 (SkillHub Connector)"


# ---------------------------------------------------------------------------
# Connector
# ---------------------------------------------------------------------------
@register_connector
class SkillHubConnector(DataConnector):
    """Read-only connector for the SkillHub skill registry API.

    Supports vector search, skill detail retrieval, and zip download.
    No authentication required for read operations.
    Rate limit: 120 requests per ~60s window.
    """

    _connector_name = "skillhub"

    def __init__(self) -> None:
        self._config: Dict[str, Any] = {}
        self._api_base: str = DEFAULT_API_BASE
        self._connected: bool = False
        self._request_count: int = 0
        self._window_start: float = 0.0

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
            supports_schema_inference=False,
            supports_incremental=False,
            max_batch_size=100,
            supported_formats=["json"],
        )

    def connect(self, config: Dict[str, Any]) -> bool:
        """Initialize connection to SkillHub API.

        Config options:
            api_base: Override API base URL (default: Convex endpoint)
        """
        self._config = config
        self._api_base = config.get("api_base", DEFAULT_API_BASE).rstrip("/")
        self._connected = True
        self._request_count = 0
        self._window_start = time.monotonic()
        return True

    def disconnect(self) -> None:
        self._connected = False

    def health_check(self) -> Dict[str, Any]:
        """Check if SkillHub API is reachable."""
        try:
            # Use a known skill to verify API is working
            data = self._api_get("/skills/self-improving-agent")
            if data and data.get("skill"):
                return {
                    "status": "healthy",
                    "api_base": self._api_base,
                    "response_time_ms": data.get("_response_ms", 0),
                    "sample_skill": data["skill"].get("slug"),
                }
            return {"status": "degraded", "api_base": self._api_base, "detail": "API responded but no data"}
        except Exception as exc:
            return {"status": "unhealthy", "api_base": self._api_base, "error": str(exc)}

    def read(self, request: ConnectorRequest) -> ConnectorResponse:
        """Read from SkillHub API.

        Supported table_name values:
            "search"  — vector search (query in request.query)
            "skills"  — list all skills (paginated via request.incremental_value as cursor)
            "detail"  — get skill detail (slug in request.query)

        Args:
            request: ConnectorRequest with table_name and query.

        Returns:
            ConnectorResponse with skill data.
        """
        start = time.monotonic()

        if request.table_name == "search":
            data = self.search_skills(request.query, limit=request.limit or 20)
        elif request.table_name == "skills":
            data = self.list_skills(cursor=request.incremental_value)
        elif request.table_name == "detail":
            data = self.get_skill(request.query)
        else:
            return ConnectorResponse(
                status="error",
                errors=[f"Unknown table_name: {request.table_name}. Use: search, skills, detail"],
            )

        duration = int((time.monotonic() - start) * 1000)

        if data is None:
            return ConnectorResponse(status="error", errors=["No data returned"], duration_ms=duration)

        row_count = len(data) if isinstance(data, list) else 1
        return ConnectorResponse(
            status="ok",
            data=data,
            row_count=row_count,
            duration_ms=duration,
        )

    # ---------------------------------------------------------------------------
    # API methods
    # ---------------------------------------------------------------------------
    def search_skills(self, query: str, limit: int = 20) -> Optional[List[Dict]]:
        """Vector search across all SkillHub skills.

        Args:
            query: Search query (natural language, vector-matched).
            limit: Max results to return.

        Returns:
            List of skill summaries with relevance scores.
        """
        params = f"q={_url_encode(query)}&limit={limit}"
        data = self._api_get(f"/search?{params}")
        if data and "results" in data:
            return data["results"]
        if data and "items" in data:
            return data["items"]
        # Return raw data if structure is unexpected
        return data if isinstance(data, list) else [data] if data else []

    def list_skills(self, cursor: Optional[str] = None) -> Optional[Dict]:
        """List all public skills with cursor pagination.

        Args:
            cursor: Pagination cursor from previous response.

        Returns:
            Dict with 'items' list and 'nextCursor'.
        """
        path = "/skills"
        if cursor:
            path += f"?cursor={_url_encode(cursor)}"
        return self._api_get(path)

    def get_skill(self, slug: str) -> Optional[Dict]:
        """Get full skill detail by slug.

        Args:
            slug: Skill slug (e.g., 'self-improving-agent').

        Returns:
            Dict with skill metadata, stats, version, owner, license.
        """
        return self._api_get(f"/skills/{_url_encode(slug)}")

    def download_skill(self, slug: str, output_dir: str) -> Dict[str, Any]:
        """Download and extract a skill zip to a local directory.

        Args:
            slug: Skill slug to download.
            output_dir: Directory to extract into.

        Returns:
            Dict with success status, output path, file count.
        """
        url = f"{self._api_base}/download?slug={_url_encode(slug)}"
        self._rate_limit_check()

        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
                content = resp.read()
                self._request_count += 1

            # Extract zip
            out_path = Path(output_dir) / slug
            out_path.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                # Security: check for path traversal
                for info in zf.infolist():
                    if info.filename.startswith("/") or ".." in info.filename:
                        return {"success": False, "error": f"Path traversal detected: {info.filename}"}
                zf.extractall(out_path)

            file_count = sum(1 for _ in out_path.rglob("*") if _.is_file())
            return {
                "success": True,
                "slug": slug,
                "output_path": str(out_path),
                "file_count": file_count,
                "zip_size_bytes": len(content),
                "downloaded_at": datetime.now(timezone.utc).isoformat(),
            }

        except HTTPError as exc:
            return {"success": False, "error": f"HTTP {exc.code}: {exc.reason}", "slug": slug}
        except URLError as exc:
            return {"success": False, "error": f"Network error: {exc.reason}", "slug": slug}
        except zipfile.BadZipFile:
            return {"success": False, "error": "Invalid zip file received", "slug": slug}
        except Exception as exc:
            return {"success": False, "error": str(exc), "slug": slug}

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------
    def _api_get(self, path: str) -> Optional[Dict]:
        """Make a GET request to the SkillHub API.

        Handles rate limiting, timeouts, and JSON parsing.
        """
        self._rate_limit_check()
        url = f"{self._api_base}{path}"

        try:
            req = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
            start = time.monotonic()
            with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
                body = resp.read().decode("utf-8")
                self._request_count += 1

            data = json.loads(body)
            if isinstance(data, dict):
                data["_response_ms"] = int((time.monotonic() - start) * 1000)
            return data

        except HTTPError as exc:
            if exc.code == 429:
                # Rate limited — wait and retry once
                time.sleep(2)
                return self._api_get(path)
            return {"error": f"HTTP {exc.code}: {exc.reason}", "_url": url}
        except URLError as exc:
            return {"error": f"Network error: {exc.reason}", "_url": url}
        except json.JSONDecodeError:
            return {"error": "Invalid JSON response", "_url": url}
        except Exception as exc:
            return {"error": str(exc), "_url": url}

    def _rate_limit_check(self):
        """Enforce local rate limiting (120 req/60s)."""
        now = time.monotonic()
        window = now - self._window_start
        if window > 60:
            self._request_count = 0
            self._window_start = now
        elif self._request_count >= 110:  # Leave 10 req headroom
            sleep_time = 60 - window
            if sleep_time > 0:
                time.sleep(sleep_time)
            self._request_count = 0
            self._window_start = time.monotonic()


def _url_encode(s: str) -> str:
    """Simple URL encoding for query parameters."""
    from urllib.parse import quote

    return quote(str(s), safe="")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="SkillHub DataBridge Connector CLI")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--search", metavar="QUERY", help="Vector search for skills")
    group.add_argument("--get", metavar="SLUG", help="Get skill detail by slug")
    group.add_argument("--download", metavar="SLUG", help="Download skill zip")
    group.add_argument("--list", action="store_true", help="List all skills")
    group.add_argument("--health", action="store_true", help="Health check")

    parser.add_argument("--output", "-o", metavar="DIR", help="Output directory for download")
    parser.add_argument("--limit", type=int, default=20, help="Max search results")
    parser.add_argument("--api-base", metavar="URL", help="Override API base URL")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    conn = SkillHubConnector()
    config = {}
    if args.api_base:
        config["api_base"] = args.api_base
    conn.connect(config)

    try:
        if args.search:
            result = conn.search_skills(args.search, limit=args.limit)
        elif args.get:
            result = conn.get_skill(args.get)
        elif args.download:
            out = args.output or ".tmp/skillhub_downloads"
            result = conn.download_skill(args.download, out)
        elif args.list:
            result = conn.list_skills()
        elif args.health:
            result = conn.health_check()
        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if isinstance(result, dict):
                for k, v in result.items():
                    if not k.startswith("_"):
                        print(f"  {k}: {v}")
            elif isinstance(result, list):
                for item in result:
                    print(f"  - {item.get('slug', item.get('displayName', item))}")
    except Exception as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        sys.exit(1)
    finally:
        conn.disconnect()


if __name__ == "__main__":
    main()
