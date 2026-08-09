"""YouTube Scanner — 9th data stream for Industry Research Engine.

Provides 3 source adapters:
  - youtube_search: YouTube Data API v3 keyword search (requires YOUTUBE_API_KEY)
  - youtube_manual: User-provided video URLs (no API key needed, oEmbed metadata)
  - youtube_channel: Channel feed via YouTube Data API v3 (requires YOUTUBE_API_KEY)

Transcripts extracted via youtube-transcript-api (no API key needed).
Two-tier LLM: qwen3 summarizes long transcripts, Claude reviews.

Architecture Decisions:
  D-RES-14: YouTube is 9th source stream (source='video'), 3 source_types
  D-RES-15: YOUTUBE_API_KEY required for search/channel; graceful degradation
  D-RES-16: Two-tier LLM for transcript processing; metadata-only fallback

CUI // SP-CTI
"""

from __future__ import annotations
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.logging.icdev_logger import get_logger

import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _ROOT / "args" / "research_config.yaml"
_DB_PATH = _ROOT / "data" / "icdev.db"

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_OEMBED_URL = "https://www.youtube.com/oembed"
VIDEO_URL_TEMPLATE = "https://www.youtube.com/watch?v={video_id}"


# ---------------------------------------------------------------------------
# Helpers (copy-adapted from source_scanner.py per D358)
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _signal_id() -> str:
    return f"rsig-{uuid.uuid4().hex[:12]}"


def _safe_get(
    url: str,
    headers: Optional[Dict] = None,
    params: Optional[Dict] = None,
    timeout: int = 30,
) -> Tuple[Optional[Dict], Optional[str]]:
    """HTTP GET with error handling.  Returns (data, error)."""
    try:
        from tools.http.client import request as _http_request
    except ImportError:
        return None, "requests library not installed"
    try:
        resp = _http_request("GET", url, headers=headers, params=params, timeout=timeout)
        if resp.status_code == 429:
            return None, "rate_limited"
        if resp.status_code == 403:
            return None, f"forbidden: {resp.status_code}"
        if resp.status_code >= 400:
            return None, f"http_{resp.status_code}"
        try:
            return resp.json(), None
        except Exception:
            return {"_raw": resp.text}, None
    except Exception as exc:
        return None, str(exc)[:200]


def _rate_delay(config: Dict, source_key: str = "video") -> float:
    """Read delay from config."""
    sources = config.get("sources", {})
    src_cfg = sources.get(source_key, {})
    rl = src_cfg.get("rate_limit", {})
    return float(rl.get("delay_between_requests_seconds", 3))


def _error_signal(source_type: str, error_msg: str) -> Dict:
    """Create a scan_error signal for logging failures."""
    return {
        "id": _signal_id(),
        "session_id": None,
        "source": "video",
        "source_type": "scan_error",
        "title": f"[YouTube scan error] {source_type}",
        "body": error_msg[:4000],
        "url": None,
        "author": None,
        "upvotes": 0,
        "citations": 0,
        "sentiment": None,
        "content_hash": _content_hash(f"youtube_error_{error_msg}"),
        "keywords": "[]",
        "metadata": json.dumps({"error_source_type": source_type}),
        "discovered_at": _now(),
    }


def _get_youtube_api_key() -> Optional[str]:
    """Read YOUTUBE_API_KEY from environment."""
    return os.environ.get("YOUTUBE_API_KEY")


# ---------------------------------------------------------------------------
# Transcript extraction
# ---------------------------------------------------------------------------
def _extract_transcript(video_id: str, max_length: int = 10000) -> Optional[str]:
    """Fetch transcript via youtube-transcript-api.  Returns text or None."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        logger.debug("youtube-transcript-api not installed — skipping transcript")
        return None

    try:
        transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
        text = " ".join(entry.get("text", "") for entry in transcript_list)
        if len(text) > max_length:
            text = text[:max_length] + "…[truncated]"
        return text if text.strip() else None
    except Exception as exc:
        logger.debug("Transcript unavailable for %s: %s", video_id, exc)
        return None


def _summarize_transcript(transcript: str, config: Dict) -> str:
    """Use two-tier LLM to summarize a long transcript.

    qwen3 produces compact summary; Claude reviews if two-tier enabled.
    Falls back to truncated transcript on any failure.
    """
    transcript_cfg = config.get("sources", {}).get("video", {}).get("transcript", {})
    if not transcript_cfg.get("summarize_with_llm", True):
        # LLM summarization disabled — return truncated
        return transcript[:2000]

    llm_function = transcript_cfg.get("llm_function", "research_youtube_summarize")

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        prompt = (
            "Summarize the following YouTube video transcript into key signals, "
            "insights, and notable claims. Focus on: technology trends, market "
            "shifts, competitive moves, regulatory changes, and actionable data "
            "points. Output as concise bullet points (max 400 words).\n\n"
            f"TRANSCRIPT:\n{transcript[:8000]}"
        )
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600,
        )
        result = router.invoke(llm_function, request)
        summary = result.content if result and result.content else ""
        if summary and len(summary) > 50:
            return summary[:4000]
    except Exception as exc:
        logger.debug("LLM transcript summarization failed: %s", exc)

    # Fallback: first 2000 chars of transcript
    return transcript[:2000]


# ---------------------------------------------------------------------------
# Video ID extraction
# ---------------------------------------------------------------------------
_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/embed/|/v/|/shorts/)([a-zA-Z0-9_-]{11})")


def _extract_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from various URL formats."""
    match = _VIDEO_ID_RE.search(url)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Source adapter 1: YouTube API search
# ---------------------------------------------------------------------------
def _scan_youtube_search(
    config: Dict,
    queries: List[str],
    delay: float,
) -> List[Dict]:
    """Search YouTube via Data API v3.  Requires YOUTUBE_API_KEY."""
    api_key = _get_youtube_api_key()
    if not api_key:
        logger.info("YOUTUBE_API_KEY not set — skipping youtube_search")
        return [_error_signal("youtube_search", "YOUTUBE_API_KEY not set")]

    video_cfg = config.get("sources", {}).get("video", {})
    platforms = {p["name"]: p for p in video_cfg.get("platforms", [])}
    search_cfg = platforms.get("youtube_search", {})
    max_results = search_cfg.get("max_results", 50)
    min_views = search_cfg.get("min_views", 100)
    transcript_cfg = video_cfg.get("transcript", {})
    transcript_enabled = transcript_cfg.get("enabled", True)
    transcript_max_len = transcript_cfg.get("max_length", 10000)

    signals = []
    seen_ids = set()

    for query in queries[:10]:  # cap at 10 queries
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": min(max_results, 50),
            "order": "relevance",
            "key": api_key,
        }
        data, err = _safe_get(f"{YOUTUBE_API_BASE}/search", params=params)
        if err:
            signals.append(_error_signal("youtube_search", f"Search '{query}': {err}"))
            continue

        items = (data or {}).get("items", [])
        for item in items:
            snippet = item.get("snippet", {})
            video_id = item.get("id", {}).get("videoId")
            if not video_id or video_id in seen_ids:
                continue
            seen_ids.add(video_id)

            # Fetch video statistics for view count
            view_count = 0
            comment_count = 0
            duration = None
            stat_params = {
                "part": "statistics,contentDetails",
                "id": video_id,
                "key": api_key,
            }
            stat_data, stat_err = _safe_get(f"{YOUTUBE_API_BASE}/videos", params=stat_params)
            if not stat_err and stat_data:
                stat_items = stat_data.get("items", [])
                if stat_items:
                    stats = stat_items[0].get("statistics", {})
                    view_count = int(stats.get("viewCount", 0))
                    comment_count = int(stats.get("commentCount", 0))
                    cd = stat_items[0].get("contentDetails", {})
                    duration = cd.get("duration")

            if view_count < min_views:
                continue

            # Transcript extraction
            body = snippet.get("description", "")[:2000]
            has_transcript = False
            if transcript_enabled:
                transcript = _extract_transcript(video_id, transcript_max_len)
                if transcript:
                    has_transcript = True
                    body = _summarize_transcript(transcript, config)

            channel_name = snippet.get("channelTitle", "")
            channel_id = snippet.get("channelId", "")
            published_at = snippet.get("publishedAt", "")

            signals.append(
                {
                    "id": _signal_id(),
                    "session_id": None,
                    "source": "video",
                    "source_type": "youtube_search",
                    "title": (snippet.get("title", "") or "")[:500],
                    "body": body,
                    "url": VIDEO_URL_TEMPLATE.format(video_id=video_id),
                    "author": channel_name,
                    "upvotes": view_count,
                    "citations": comment_count,
                    "sentiment": None,
                    "content_hash": _content_hash(f"youtube_{video_id}"),
                    "keywords": json.dumps([query]),
                    "metadata": json.dumps(
                        {
                            "video_id": video_id,
                            "channel_id": channel_id,
                            "published_at": published_at,
                            "duration": duration,
                            "has_transcript": has_transcript,
                            "search_query": query,
                        }
                    ),
                    "discovered_at": _now(),
                }
            )

            time.sleep(0.5)  # inter-video delay

        time.sleep(delay)

    return signals


# ---------------------------------------------------------------------------
# Source adapter 2: Manual URLs (no API key needed)
# ---------------------------------------------------------------------------
def _scan_youtube_manual(
    urls: List[str],
    config: Dict,
    delay: float,
) -> List[Dict]:
    """Process user-provided YouTube video URLs via oEmbed + transcript."""
    if not urls:
        return []

    video_cfg = config.get("sources", {}).get("video", {})
    transcript_cfg = video_cfg.get("transcript", {})
    transcript_enabled = transcript_cfg.get("enabled", True)
    transcript_max_len = transcript_cfg.get("max_length", 10000)

    signals = []
    seen_ids = set()

    for url in urls[:20]:  # cap
        video_id = _extract_video_id(url)
        if not video_id or video_id in seen_ids:
            continue
        seen_ids.add(video_id)

        # Fetch metadata via oEmbed (no API key required)
        oembed_params = {
            "url": VIDEO_URL_TEMPLATE.format(video_id=video_id),
            "format": "json",
        }
        data, err = _safe_get(YOUTUBE_OEMBED_URL, params=oembed_params)
        title = ""
        author = ""
        if not err and data:
            title = (data.get("title", "") or "")[:500]
            author = data.get("author_name", "")

        # Transcript
        body = ""
        has_transcript = False
        if transcript_enabled:
            transcript = _extract_transcript(video_id, transcript_max_len)
            if transcript:
                has_transcript = True
                body = _summarize_transcript(transcript, config)

        signals.append(
            {
                "id": _signal_id(),
                "session_id": None,
                "source": "video",
                "source_type": "youtube_manual",
                "title": title or f"Video {video_id}",
                "body": body,
                "url": VIDEO_URL_TEMPLATE.format(video_id=video_id),
                "author": author,
                "upvotes": 0,
                "citations": 0,
                "sentiment": None,
                "content_hash": _content_hash(f"youtube_{video_id}"),
                "keywords": "[]",
                "metadata": json.dumps(
                    {
                        "video_id": video_id,
                        "has_transcript": has_transcript,
                        "user_provided": True,
                    }
                ),
                "discovered_at": _now(),
            }
        )

        time.sleep(delay)

    return signals


# ---------------------------------------------------------------------------
# Source adapter 3: Channel feed
# ---------------------------------------------------------------------------
def _scan_youtube_channel(
    config: Dict,
    channel_ids: List[str],
    delay: float,
) -> List[Dict]:
    """Fetch recent videos from specified YouTube channels via API v3."""
    api_key = _get_youtube_api_key()
    if not api_key:
        logger.info("YOUTUBE_API_KEY not set — skipping youtube_channel")
        return [_error_signal("youtube_channel", "YOUTUBE_API_KEY not set")]

    video_cfg = config.get("sources", {}).get("video", {})
    platforms = {p["name"]: p for p in video_cfg.get("platforms", [])}
    channel_cfg = platforms.get("youtube_channel", {})
    max_results = channel_cfg.get("max_results", 30)
    transcript_cfg = video_cfg.get("transcript", {})
    transcript_enabled = transcript_cfg.get("enabled", True)
    transcript_max_len = transcript_cfg.get("max_length", 10000)

    signals = []
    seen_ids = set()

    for channel_id in channel_ids[:10]:  # cap
        params = {
            "part": "snippet",
            "channelId": channel_id,
            "type": "video",
            "maxResults": min(max_results, 50),
            "order": "date",
            "key": api_key,
        }
        data, err = _safe_get(f"{YOUTUBE_API_BASE}/search", params=params)
        if err:
            signals.append(_error_signal("youtube_channel", f"Channel {channel_id}: {err}"))
            continue

        items = (data or {}).get("items", [])
        for item in items:
            snippet = item.get("snippet", {})
            video_id = item.get("id", {}).get("videoId")
            if not video_id or video_id in seen_ids:
                continue
            seen_ids.add(video_id)

            body = snippet.get("description", "")[:2000]
            has_transcript = False
            if transcript_enabled:
                transcript = _extract_transcript(video_id, transcript_max_len)
                if transcript:
                    has_transcript = True
                    body = _summarize_transcript(transcript, config)

            signals.append(
                {
                    "id": _signal_id(),
                    "session_id": None,
                    "source": "video",
                    "source_type": "youtube_channel",
                    "title": (snippet.get("title", "") or "")[:500],
                    "body": body,
                    "url": VIDEO_URL_TEMPLATE.format(video_id=video_id),
                    "author": snippet.get("channelTitle", ""),
                    "upvotes": 0,
                    "citations": 0,
                    "sentiment": None,
                    "content_hash": _content_hash(f"youtube_{video_id}"),
                    "keywords": "[]",
                    "metadata": json.dumps(
                        {
                            "video_id": video_id,
                            "channel_id": channel_id,
                            "published_at": snippet.get("publishedAt", ""),
                            "has_transcript": has_transcript,
                        }
                    ),
                    "discovered_at": _now(),
                }
            )

            time.sleep(0.5)

        time.sleep(delay)

    return signals


# ---------------------------------------------------------------------------
# Main entry point — scan_videos()
# ---------------------------------------------------------------------------
def scan_videos(config: Dict, session_config: Optional[Dict] = None) -> List[Dict]:
    """Scan YouTube for video signals across 3 sub-adapters.

    Args:
        config: Full research_config.yaml dict.
        session_config: Vertical-specific JSON config dict (optional).

    Returns:
        List of normalized signal dicts with source='video'.
    """
    video_cfg = config.get("sources", {}).get("video", {})
    if not video_cfg.get("enabled", False):
        logger.debug("Video source disabled in config")
        return []

    delay = _rate_delay(config, "video")
    all_signals: List[Dict] = []

    # Determine search queries from session config or vertical config
    search_queries: List[str] = []
    manual_urls: List[str] = []
    channel_ids: List[str] = []

    if session_config:
        vs = session_config.get("video_sources", {})
        search_queries = vs.get("youtube_search_queries", [])
        manual_urls = vs.get("manual_urls", [])
        channel_ids = vs.get("youtube_channels", [])

    # Check which platforms are enabled
    platforms = {p["name"]: p for p in video_cfg.get("platforms", [])}

    # 1. YouTube API search
    yt_search = platforms.get("youtube_search", {})
    if yt_search.get("enabled", True) and search_queries:
        logger.info("Scanning YouTube search: %d queries", len(search_queries))
        results = _scan_youtube_search(config, search_queries, delay)
        all_signals.extend(results)

    # 2. Manual URLs (always available — no API key needed)
    yt_manual = platforms.get("youtube_manual", {})
    if yt_manual.get("enabled", True) and manual_urls:
        logger.info("Processing %d manual YouTube URLs", len(manual_urls))
        results = _scan_youtube_manual(manual_urls, config, delay)
        all_signals.extend(results)

    # 3. Channel feed
    yt_channel = platforms.get("youtube_channel", {})
    if yt_channel.get("enabled", True) and channel_ids:
        logger.info("Scanning %d YouTube channels", len(channel_ids))
        results = _scan_youtube_channel(config, channel_ids, delay)
        all_signals.extend(results)

    logger.info("YouTube scan complete: %d signals", len(all_signals))
    return all_signals


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    """CLI entry point for standalone testing."""
    import argparse
    import yaml

    parser = argparse.ArgumentParser(description="YouTube Scanner — Research Engine")
    parser.add_argument("--scan", action="store_true", help="Run video scan")
    parser.add_argument("--urls", nargs="*", help="Manual YouTube URLs")
    parser.add_argument("--channels", nargs="*", help="YouTube channel IDs")
    parser.add_argument("--queries", nargs="*", help="Search queries")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    # Load config
    config = {}
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            config = yaml.safe_load(f) or {}

    if args.scan or args.urls or args.channels or args.queries:
        # Build session_config from CLI args
        session_config = {"video_sources": {}}
        if args.urls:
            session_config["video_sources"]["manual_urls"] = args.urls
        if args.channels:
            session_config["video_sources"]["youtube_channels"] = args.channels
        if args.queries:
            session_config["video_sources"]["youtube_search_queries"] = args.queries

        # Enable video source for CLI usage
        config.setdefault("sources", {}).setdefault("video", {})["enabled"] = True

        signals = scan_videos(config, session_config)

        if args.json:
            print(json.dumps({"signals": signals, "count": len(signals)}, indent=2))
        else:
            print(f"Found {len(signals)} video signals")
            for sig in signals:
                print(f"  [{sig['source_type']}] {sig['title'][:80]}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
