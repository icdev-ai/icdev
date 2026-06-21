#!/usr/bin/env python3
# CUI // SP-CTI
"""YouTube adapters with automatic fallback: Data API v3 → yt-dlp search.

Primary:  YouTubeDataApiAdapter  — requires YOUTUBE_API_KEY env var
Fallback: YouTubeYtDlpAdapter    — uses yt-dlp (no key needed, slower)

The registry loads both; the router tries primary first, falls back on
auth_error or missing key.
"""

import os
import time
from typing import Any

from tools.platform_connectors.base import FetchResult, HealthResult, PlatformConnector

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

try:
    import yt_dlp as _ytdlp
    _HAS_YTDLP = True
except ImportError:
    _HAS_YTDLP = False

YT_API = "https://www.googleapis.com/youtube/v3"
DEFAULT_TIMEOUT = 15


class YouTubeDataApiAdapter(PlatformConnector):
    """YouTube Data API v3 adapter — requires YOUTUBE_API_KEY."""

    name = "youtube_data_api"
    platform = "youtube"
    priority = 0

    def _api_key(self) -> str | None:
        return os.environ.get("YOUTUBE_API_KEY") or os.environ.get("YT_API_KEY")

    def _get(self, path: str, params: dict) -> tuple[Any, str | None]:
        key = self._api_key()
        if not key:
            return None, "no_api_key"
        if not _HAS_REQUESTS:
            return None, "requests_not_installed"
        params["key"] = key
        try:
            resp = _requests.get(f"{YT_API}{path}", params=params, timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 403:
                body = resp.json()
                reason = body.get("error", {}).get("errors", [{}])[0].get("reason", "forbidden")
                return None, f"forbidden:{reason}"
            if resp.status_code == 400:
                return None, "bad_request"
            resp.raise_for_status()
            return resp.json(), None
        except _requests.exceptions.Timeout:
            return None, "timeout"
        except _requests.exceptions.ConnectionError:
            return None, "connection_error"
        except Exception as exc:
            return None, str(exc)

    def fetch(self, query: str, **kwargs: Any) -> FetchResult:
        max_results = int(kwargs.get("max_results", 10))
        order = kwargs.get("order", "relevance")

        data, err = self._get(
            "/search",
            {"part": "snippet", "q": query, "type": "video",
             "maxResults": min(max_results, 50), "order": order},
        )
        if err:
            result = self._err(query, err)
            result.query = query
            return result

        items = []
        for item in (data or {}).get("items", [])[:max_results]:
            vid_id = item.get("id", {}).get("videoId", "")
            snip = item.get("snippet", {})
            items.append({
                "id": vid_id,
                "title": snip.get("title", ""),
                "url": f"https://www.youtube.com/watch?v={vid_id}",
                "description": snip.get("description", "")[:500],
                "channel": snip.get("channelTitle", ""),
                "published_at": snip.get("publishedAt", ""),
                "platform": "youtube",
                "type": "video",
                "adapter": self.name,
            })

        return FetchResult(
            platform=self.platform,
            adapter_name=self.name,
            query=query,
            items=items,
            total=len(items),
        )

    def health(self) -> HealthResult:
        if not self._api_key():
            return HealthResult(
                adapter_name=self.name, platform=self.platform,
                status="auth_error", message="YOUTUBE_API_KEY not set",
            )
        if not _HAS_REQUESTS:
            return HealthResult(
                adapter_name=self.name, platform=self.platform,
                status="unreachable", message="requests_not_installed",
            )
        t0 = time.monotonic()
        _, err = self._get("/videos", {"part": "id", "chart": "mostPopular", "maxResults": "1"})
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        if err:
            status = "auth_error" if "forbidden" in err or "no_api_key" in err else "unreachable"
            return HealthResult(
                adapter_name=self.name, platform=self.platform,
                status=status, latency_ms=latency_ms, message=err,
            )
        return HealthResult(
            adapter_name=self.name, platform=self.platform,
            status="ok", latency_ms=latency_ms, message="data_api_ok",
        )


class YouTubeYtDlpAdapter(PlatformConnector):
    """YouTube yt-dlp fallback adapter — no API key needed, uses yt-dlp search."""

    name = "youtube_ytdlp"
    platform = "youtube"
    priority = 1

    def fetch(self, query: str, **kwargs: Any) -> FetchResult:
        if not _HAS_YTDLP:
            result = self._err(query, "yt_dlp_not_installed")
            result.query = query
            return result

        max_results = int(kwargs.get("max_results", 10))

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "playlistend": max_results,
        }

        try:
            with _ytdlp.YoutubeDL(ydl_opts) as ydl:
                data = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
        except Exception as exc:
            result = self._err(query, str(exc))
            result.query = query
            return result

        items = []
        for entry in (data or {}).get("entries", [])[:max_results]:
            if not entry:
                continue
            vid_id = entry.get("id", "")
            items.append({
                "id": vid_id,
                "title": entry.get("title", ""),
                "url": entry.get("url") or f"https://www.youtube.com/watch?v={vid_id}",
                "description": entry.get("description", "")[:500] if entry.get("description") else "",
                "channel": entry.get("channel") or entry.get("uploader", ""),
                "duration": entry.get("duration"),
                "view_count": entry.get("view_count"),
                "platform": "youtube",
                "type": "video",
                "adapter": self.name,
            })

        return FetchResult(
            platform=self.platform,
            adapter_name=self.name,
            query=query,
            items=items,
            total=len(items),
            metadata={"backend": "yt-dlp"},
        )

    def health(self) -> HealthResult:
        if not _HAS_YTDLP:
            return HealthResult(
                adapter_name=self.name, platform=self.platform,
                status="unreachable", message="yt_dlp_not_installed",
            )
        return HealthResult(
            adapter_name=self.name, platform=self.platform,
            status="ok", message="yt_dlp_available",
        )
