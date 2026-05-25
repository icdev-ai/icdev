"""ICDEV™ Pulse video search engine.

Searches YouTube and Vimeo for relevant videos to embed in blog posts.
No API keys required -- uses web scraping (YouTube), oEmbed (Vimeo),
and DuckDuckGo site-scoped search as fallback.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
import os
import random
import re
import time
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

from tools.http.client import get_session


def _is_air_gapped() -> bool:
    return os.environ.get("ICDEV_ENVIRONMENT", "").lower() == "air-gapped"


logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

USER_AGENTS = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"),
]

DDG_URL = "https://html.duckduckgo.com/html/"
YOUTUBE_SEARCH_URL = "https://www.youtube.com/results"
VIMEO_OEMBED_URL = "https://vimeo.com/api/oembed.json"

# Rate limit delays (seconds)
REQUEST_DELAY_MIN = 1.0
REQUEST_DELAY_MAX = 2.5

# Relevance scoring keywords -- topics the Pulse blog cares about
RELEVANCE_KEYWORDS: list[str] = [
    "compliance",
    "security",
    "devsecops",
    "fedramp",
    "cmmc",
    "nist",
    "ato",
    "zero trust",
    "sbom",
    "supply chain",
    "governance",
    "automation",
    "agentic",
    "ai governance",
    "vulnerability",
    "audit",
    "certification",
    "authorization",
    "risk management",
    "software development",
    "devops",
    "continuous",
    "pipeline",
    "infrastructure",
    "cloud",
    "embedded",
    "firmware",
    "tutorial",
    "explained",
    "how to",
    "guide",
    "best practices",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session() -> requests.Session:
    """Build a requests session with realistic headers."""
    s = get_session()
    s.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"),
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return s


def _polite_delay() -> None:
    """Sleep between requests to respect rate limits."""
    time.sleep(random.uniform(REQUEST_DELAY_MIN, REQUEST_DELAY_MAX))


def _score_relevance(title: str, topic: str) -> float:
    """Score relevance 0.0-1.0 based on keyword overlap between title and topic.

    Two components:
      - Topic match (0.6 weight): how many topic words appear in the title
      - Keyword match (0.4 weight): how many general relevance keywords appear
    """
    title_lower = title.lower()
    topic_lower = topic.lower()

    # Topic word match (0.0-0.6)
    topic_words = [w for w in topic_lower.split() if len(w) > 2]
    if topic_words:
        topic_hits = sum(1 for w in topic_words if w in title_lower)
        topic_score = min(topic_hits / len(topic_words), 1.0)
    else:
        topic_score = 0.0

    # General keyword match (0.0-0.4)
    keyword_hits = sum(1 for kw in RELEVANCE_KEYWORDS if kw.lower() in title_lower)
    keyword_score = min(keyword_hits / max(len(RELEVANCE_KEYWORDS) * 0.1, 1), 1.0)

    score = (topic_score * 0.6) + (keyword_score * 0.4)
    return round(min(max(score, 0.0), 1.0), 3)


def _extract_youtube_id(url: str) -> str | None:
    """Extract YouTube video ID from various URL formats."""
    patterns = [
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})",
        r"youtube\.com/shorts/([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def _extract_vimeo_id(url: str) -> str | None:
    """Extract Vimeo video ID from URL."""
    match = re.search(r"vimeo\.com/(\d+)", url)
    return match.group(1) if match else None


def _detect_platform(url: str) -> str:
    """Detect video platform from URL."""
    url_lower = url.lower()
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "youtube"
    if "vimeo.com" in url_lower:
        return "vimeo"
    return "unknown"


# ---------------------------------------------------------------------------
# YouTube scraper
# ---------------------------------------------------------------------------


def _search_youtube(query: str, max_results: int = 5) -> list[dict]:
    """Search YouTube by scraping the search results page.

    Parses the embedded ytInitialData JSON from the HTML to extract
    video titles, URLs, channel names, and thumbnails.
    """
    session = _session()
    results: list[dict] = []

    params = {"search_query": query}
    try:
        resp = session.get(
            YOUTUBE_SEARCH_URL,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("YouTube search request failed for %r: %s", query, exc)
        return results

    # YouTube embeds search results as JSON in a script tag
    # Look for ytInitialData in the page source
    yt_data = _parse_yt_initial_data(resp.text)
    if not yt_data:
        logger.warning("Could not parse ytInitialData for query %r", query)
        return results

    # Navigate the nested JSON to find video renderers
    try:
        contents = (
            yt_data.get("contents", {})
            .get("twoColumnSearchResultsRenderer", {})
            .get("primaryContents", {})
            .get("sectionListRenderer", {})
            .get("contents", [])
        )
    except (AttributeError, TypeError):
        logger.warning("Unexpected ytInitialData structure for query %r", query)
        return results

    seen_ids: set[str] = set()

    for section in contents:
        items = section.get("itemSectionRenderer", {}).get("contents", [])
        for item in items:
            if len(results) >= max_results:
                break

            renderer = item.get("videoRenderer")
            if not renderer:
                continue

            video_id = renderer.get("videoId", "")
            if not video_id or video_id in seen_ids:
                continue
            seen_ids.add(video_id)

            # Title
            title_runs = renderer.get("title", {}).get("runs", [])
            title = "".join(r.get("text", "") for r in title_runs)

            # Channel
            channel_runs = renderer.get("ownerText", {}).get("runs", [])
            channel = "".join(r.get("text", "") for r in channel_runs)

            # Thumbnail
            thumbnails = renderer.get("thumbnail", {}).get("thumbnails", [])
            thumbnail_url = thumbnails[-1]["url"] if thumbnails else ""

            url = f"https://www.youtube.com/watch?v={video_id}"

            results.append(
                {
                    "url": url,
                    "title": title,
                    "platform": "youtube",
                    "channel": channel,
                    "thumbnail_url": thumbnail_url,
                    "video_id": video_id,
                }
            )

        if len(results) >= max_results:
            break

    logger.info("YouTube search %r: found %d videos", query, len(results))
    return results


def _parse_yt_initial_data(html: str) -> dict | None:
    """Extract the ytInitialData JSON object from YouTube HTML."""
    # YouTube puts this in a script tag: var ytInitialData = {...};
    pattern = r"var\s+ytInitialData\s*=\s*(\{.*?\});"
    match = re.search(pattern, html, re.DOTALL)
    if not match:
        # Alternative pattern used in some page variants
        pattern2 = r"window\[\"ytInitialData\"\]\s*=\s*(\{.*?\});"
        match = re.search(pattern2, html, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse ytInitialData JSON: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Vimeo oEmbed
# ---------------------------------------------------------------------------


def _get_vimeo_oembed(video_url: str) -> dict | None:
    """Fetch Vimeo video metadata via the free oEmbed API.

    Returns dict with title, author_name, thumbnail_url, html, etc.
    or None if the request fails.
    """
    session = _session()
    params = {"url": video_url, "format": "json"}

    try:
        resp = session.get(VIMEO_OEMBED_URL, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        logger.debug("Vimeo oEmbed failed for %s: %s", video_url, exc)
        return None
    except json.JSONDecodeError:
        logger.debug("Vimeo oEmbed returned non-JSON for %s", video_url)
        return None


# ---------------------------------------------------------------------------
# DuckDuckGo fallback search
# ---------------------------------------------------------------------------


def _search_ddg_videos(query: str, platform: str, max_results: int = 5) -> list[dict]:
    """Search DuckDuckGo with site: filter for video URLs.

    Used as a fallback when direct platform scraping fails.
    """
    site_filter = {
        "youtube": "site:youtube.com/watch",
        "vimeo": "site:vimeo.com",
    }.get(platform, "")

    full_query = f"{query} {site_filter}"
    session = _session()
    results: list[dict] = []
    seen_urls: set[str] = set()

    # Share circuit breaker with researcher module
    from tools.pulse.engine.researcher import _ddg_circuit_open

    if _ddg_circuit_open:
        logger.debug("DDG circuit open — skipping video search for %r", query)
        return results

    try:
        resp = session.post(
            DDG_URL,
            data={"q": full_query, "b": "", "kl": "us-en"},
            timeout=8,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("DuckDuckGo video search failed for %r: %s", query, exc)
        if "timeout" in str(exc).lower() or "connect" in str(exc).lower():
            import tools.pulse.engine.researcher as _res

            _res._ddg_circuit_open = True
            logger.warning("DDG circuit breaker OPEN from video_finder")
        return results

    soup = BeautifulSoup(resp.text, "html.parser")

    for result_div in soup.select(".result"):
        if len(results) >= max_results:
            break

        link_tag = result_div.select_one(".result__a")
        if not link_tag:
            continue

        title = link_tag.get_text(strip=True)
        href = str(link_tag.get("href", ""))

        url = _extract_ddg_url(href)
        if not url or url in seen_urls:
            continue

        detected = _detect_platform(url)
        if detected != platform:
            continue

        seen_urls.add(url)

        video_id = None
        if platform == "youtube":
            video_id = _extract_youtube_id(url)
            if not video_id:
                continue
            # Normalize URL
            url = f"https://www.youtube.com/watch?v={video_id}"
            thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        elif platform == "vimeo":
            vimeo_id = _extract_vimeo_id(url)
            if not vimeo_id:
                continue
            url = f"https://vimeo.com/{vimeo_id}"
            thumbnail_url = ""
            video_id = vimeo_id

        results.append(
            {
                "url": url,
                "title": title,
                "platform": platform,
                "channel": "",
                "thumbnail_url": thumbnail_url,
                "video_id": video_id or "",
            }
        )

    logger.info(
        "DuckDuckGo %s search %r: found %d videos",
        platform,
        query,
        len(results),
    )
    return results


def _extract_ddg_url(href: str) -> str:
    """Extract the real URL from a DuckDuckGo redirect link."""
    if "uddg=" in href:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        candidates = qs.get("uddg", [])
        if candidates:
            return candidates[0]
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    return href


# ---------------------------------------------------------------------------
# Embed HTML generation
# ---------------------------------------------------------------------------


def get_embed_html(video_url: str) -> str:
    """Generate responsive, lazy-loading iframe embed HTML for a video URL.

    Supports YouTube and Vimeo URLs. Returns empty string for
    unrecognized platforms.
    """
    platform = _detect_platform(video_url)

    if platform == "youtube":
        video_id = _extract_youtube_id(video_url)
        if not video_id:
            logger.warning("Could not extract YouTube ID from %s", video_url)
            return ""
        embed_src = f"https://www.youtube-nocookie.com/embed/{video_id}"
        title_attr = "YouTube video player"

    elif platform == "vimeo":
        vimeo_id = _extract_vimeo_id(video_url)
        if not vimeo_id:
            logger.warning("Could not extract Vimeo ID from %s", video_url)
            return ""
        embed_src = f"https://player.vimeo.com/video/{vimeo_id}"
        title_attr = "Vimeo video player"

    else:
        logger.warning("Unsupported video platform for URL: %s", video_url)
        return ""

    return (
        '<div style="position:relative;padding-bottom:56.25%;height:0;overflow:hidden;max-width:100%;">'
        f'<iframe src="{embed_src}" '
        f'title="{title_attr}" '
        'style="position:absolute;top:0;left:0;width:100%;height:100%;border:0;" '
        'loading="lazy" '
        'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" '
        "allowfullscreen>"
        "</iframe>"
        "</div>"
    )


# ---------------------------------------------------------------------------
# Vimeo metadata enrichment
# ---------------------------------------------------------------------------


def _enrich_vimeo(video: dict) -> dict:
    """Enrich a Vimeo video dict with oEmbed metadata (title, channel, thumbnail)."""
    oembed = _get_vimeo_oembed(video["url"])
    if oembed:
        video["title"] = video.get("title") or oembed.get("title", "")
        video["channel"] = oembed.get("author_name", "")
        video["thumbnail_url"] = oembed.get("thumbnail_url", "")
    return video


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search_videos(topic: str, max_results: int = 5) -> list[dict]:
    """Search YouTube and Vimeo for videos related to *topic*.

    Tries direct YouTube scraping first, then falls back to DuckDuckGo
    site-scoped search. Vimeo is searched via DuckDuckGo + oEmbed enrichment.

    Returns a list of video dicts sorted by relevance_score descending.
    Each dict contains: url, title, platform, channel, embed_html,
    thumbnail_url, relevance_score.
    """
    logger.info("Searching videos for topic: %r (max=%d)", topic, max_results)
    if _is_air_gapped():
        logger.info("Air-gapped environment — skipping video search")
        return []
    all_videos: list[dict] = []
    seen_urls: set[str] = set()

    # --- YouTube: direct scrape ---
    yt_limit = max(max_results * 3 // 4, 3)
    yt_results = _search_youtube(topic, max_results=yt_limit)
    _polite_delay()

    # Fallback to DuckDuckGo if direct scrape yields nothing
    if not yt_results:
        logger.info("YouTube direct scrape empty, falling back to DuckDuckGo")
        yt_results = _search_ddg_videos(topic, "youtube", max_results=yt_limit)
        _polite_delay()

    for v in yt_results:
        if v["url"] not in seen_urls:
            seen_urls.add(v["url"])
            all_videos.append(v)

    # --- Vimeo: DuckDuckGo + oEmbed enrichment ---
    vimeo_limit = max(max_results // 4, 2)
    vimeo_results = _search_ddg_videos(topic, "vimeo", max_results=vimeo_limit)
    _polite_delay()

    for v in vimeo_results:
        if v["url"] not in seen_urls:
            seen_urls.add(v["url"])
            v = _enrich_vimeo(v)
            _polite_delay()
            all_videos.append(v)

    # Score, generate embeds, and sort
    for video in all_videos:
        video["relevance_score"] = _score_relevance(video.get("title", ""), topic)
        video["embed_html"] = get_embed_html(video["url"])

    all_videos.sort(key=lambda v: v["relevance_score"], reverse=True)

    # Trim to max_results
    all_videos = all_videos[:max_results]

    # Remove internal-only keys
    for video in all_videos:
        video.pop("video_id", None)

    logger.info(
        "Video search %r: returning %d results (top score %.3f)",
        topic,
        len(all_videos),
        all_videos[0]["relevance_score"] if all_videos else 0.0,
    )
    return all_videos


def find_videos_for_post(pain_points: list[str]) -> list[dict]:
    """Find the best videos for a set of blog post pain points.

    Searches for each pain point, deduplicates across all results,
    and returns the top videos sorted by relevance_score.

    Args:
        pain_points: List of pain point strings to search for.

    Returns:
        List of video dicts, deduplicated and sorted by relevance.
    """
    if not pain_points:
        logger.warning("find_videos_for_post called with empty pain_points")
        return []

    logger.info(
        "Finding videos for %d pain points: %s",
        len(pain_points),
        [p[:50] for p in pain_points],
    )

    all_videos: list[dict] = []
    seen_urls: set[str] = set()

    # Distribute results across pain points
    per_point = max(3, 10 // len(pain_points))

    for pain_point in pain_points:
        try:
            results = search_videos(pain_point, max_results=per_point)
        except Exception as exc:
            logger.error("Video search failed for pain point %r: %s", pain_point, exc)
            continue

        for video in results:
            if video["url"] not in seen_urls:
                seen_urls.add(video["url"])
                video["source_pain_point"] = pain_point
                all_videos.append(video)

        _polite_delay()

    # Sort by relevance and return top results
    all_videos.sort(key=lambda v: v["relevance_score"], reverse=True)

    # Cap at reasonable number for a single blog post
    max_for_post = min(len(all_videos), 8)
    all_videos = all_videos[:max_for_post]

    logger.info(
        "Found %d videos across %d pain points",
        len(all_videos),
        len(pain_points),
    )
    return all_videos


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if len(sys.argv) < 2:
        print("Usage: python -m engine.video_finder <topic>")
        print("       python -m engine.video_finder --embed <video_url>")
        sys.exit(1)

    if sys.argv[1] == "--embed" and len(sys.argv) >= 3:
        html = get_embed_html(sys.argv[2])
        if html:
            print(html)
        else:
            print("Could not generate embed HTML for that URL.", file=sys.stderr)
            sys.exit(1)
    else:
        topic_arg = " ".join(sys.argv[1:])
        results = search_videos(topic_arg)
        print(json.dumps(results, indent=2))
        print(f"\n--- {len(results)} videos found ---")
