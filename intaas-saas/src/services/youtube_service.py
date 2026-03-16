#!/usr/bin/env python3
"""YouTube service — extract video metadata and transcripts for analysis.

Uses youtube-transcript-api (no API key needed for transcripts).
Falls back to title/description only if transcript unavailable.
"""
import logging
import re

logger = logging.getLogger("intaas.services.youtube")


def is_youtube_url(url: str) -> bool:
    """Check if URL is a YouTube video."""
    return bool(re.match(
        r"https?://(www\.)?(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)",
        url,
    ))


def extract_video_id(url: str) -> str:
    """Extract video ID from YouTube URL."""
    patterns = [
        r"youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})",
        r"youtu\.be/([a-zA-Z0-9_-]{11})",
        r"youtube\.com/shorts/([a-zA-Z0-9_-]{11})",
        r"youtube\.com/embed/([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return ""


def get_transcript(video_id: str, max_chars: int = 8000) -> str:
    """Fetch transcript for a YouTube video.

    Uses youtube-transcript-api (no API key needed).
    Supports both old API (get_transcript) and new API (fetch).
    Falls back gracefully if not installed or unavailable.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        # New API (v1.0+): instance method .fetch()
        try:
            api = YouTubeTranscriptApi()
            transcript_data = api.fetch(video_id)
            snippets = list(transcript_data)
            text = " ".join(
                s.text if hasattr(s, "text") else str(s.get("text", ""))
                for s in snippets
            )
            return text[:max_chars]
        except (TypeError, AttributeError):
            pass

        # Old API fallback: class method .get_transcript()
        try:
            transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
            text = " ".join(entry["text"] for entry in transcript_list)
            return text[:max_chars]
        except AttributeError:
            pass

        return ""
    except ImportError:
        logger.info("youtube-transcript-api not installed")
        return ""
    except Exception as e:
        logger.warning("Transcript unavailable for %s: %s", video_id, e)
        return ""


def get_video_metadata(video_id: str) -> dict:
    """Fetch video title and description via oembed (no API key needed)."""
    import requests

    try:
        resp = requests.get(
            "https://www.youtube.com/oembed",
            params={"url": f"https://www.youtube.com/watch?v={video_id}", "format": "json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "title": data.get("title", ""),
            "author": data.get("author_name", ""),
            "thumbnail": data.get("thumbnail_url", ""),
        }
    except Exception as e:
        logger.warning("Video metadata failed for %s: %s", video_id, e)
        return {"title": "", "author": "", "thumbnail": ""}


def analyze_youtube_url(url: str) -> dict:
    """Full YouTube analysis: metadata + transcript + search keywords.

    Returns a dict ready to feed into the analysis pipeline.
    """
    video_id = extract_video_id(url)
    if not video_id:
        return {"error": "Invalid YouTube URL"}

    metadata = get_video_metadata(video_id)
    transcript = get_transcript(video_id)

    title = metadata.get("title", "")
    author = metadata.get("author", "")
    content = transcript or title

    # Build search query — strip common YouTube title noise
    search_query = title
    for noise in [
        " — ", " - ", " | ", "(Official", "(Full",
        "BREAKING", "LIVE", "UPDATE",
    ]:
        search_query = search_query.split(noise)[0]
    search_query = search_query.strip()[:80]

    return {
        "video_id": video_id,
        "title": title,
        "author": author,
        "thumbnail": metadata.get("thumbnail", ""),
        "transcript": transcript,
        "content": content,
        "has_transcript": bool(transcript),
        "url": url,
        "search_query": search_query,
    }
