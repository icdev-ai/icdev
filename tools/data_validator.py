# CUI // SP-CTI
"""Data Validator — validates and ingests unstructured OSINT data sources.

Provides a structured ingestion hook for social media posts and other
unstructured OSINT content. Parses JSON input, deduplicates by content hash,
scores each post across three dimensions (source tier, content quality,
temporal recency), and returns a validation envelope.

Usage:
    python -m tools.data_validator --file posts.json --json
    python -m tools.data_validator --stdin --json
    echo '[{"platform":"twitter","text":"..."}]' | python -m tools.data_validator --stdin --json

Always exits 0 — structured errors are returned in the JSON envelope.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING, format="%(levelname)s  %(message)s")

# ── Constants ─────────────────────────────────────────────────────────────────

SUPPORTED_PLATFORMS = {"twitter", "x", "facebook", "instagram", "linkedin",
                       "reddit", "telegram", "mastodon", "threads", "youtube",
                       "tiktok", "bluesky", "unknown"}

_HIGH_VALUE_KEYWORDS = frozenset({
    "attack", "threat", "breach", "vulnerability", "exploit", "malware",
    "ransomware", "phishing", "zero-day", "cve", "compromise", "incident",
    "intrusion", "lateral", "exfil", "c2", "botnet", "ddos", "supply chain",
    "insider", "espionage", "intelligence", "classified", "leak", "dump",
    "credential", "token", "key", "certificate", "backdoor", "rootkit",
})

_URL_RE = re.compile(r"https?://\S+")
_HASHTAG_RE = re.compile(r"#\w+")
_MENTION_RE = re.compile(r"@\w+")


# ── Utilities ─────────────────────────────────────────────────────────────────

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(post: Dict[str, Any]) -> str:
    """SHA-256 deduplication key derived from post identity fields."""
    platform = (post.get("platform") or "").strip().lower()
    text = (post.get("text") or post.get("content") or "").strip()
    post_id = (post.get("id") or post.get("post_id") or "").strip()
    seed = post_id if post_id else (platform + text[:256])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _platform_tier(platform: str) -> float:
    """Return a source-tier score (0–3) based on platform reliability."""
    p = platform.lower()
    if p in ("linkedin", "reddit"):
        return 3.0
    if p in ("twitter", "x", "mastodon", "bluesky", "threads"):
        return 2.0
    if p in ("facebook", "instagram", "tiktok", "youtube"):
        return 1.5
    if p in ("telegram",):
        return 1.0
    return 0.5  # unknown / unsupported


def _content_score(text: str) -> float:
    """Score text content quality (0–4) based on length and keyword density."""
    if not text:
        return 0.0
    words = text.split()
    length_score = min(2.0, len(words) / 25)
    lower = text.lower()
    kw_hits = sum(1 for kw in _HIGH_VALUE_KEYWORDS if kw in lower)
    keyword_score = min(2.0, kw_hits * 0.5)
    return round(length_score + keyword_score, 2)


def _temporal_score(post: Dict[str, Any]) -> float:
    """Score recency (0–3). Posts < 1h score 3, < 24h score 2, < 7d score 1."""
    ts_raw = post.get("timestamp") or post.get("created_at") or post.get("date") or ""
    if not ts_raw:
        return 0.5
    try:
        if isinstance(ts_raw, (int, float)):
            dt = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
        else:
            for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z",
                        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(str(ts_raw), fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    continue
            else:
                return 0.5
        age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        if age_hours < 1:
            return 3.0
        if age_hours < 24:
            return 2.0
        if age_hours < 168:  # 7 days
            return 1.0
        return 0.5
    except Exception:
        return 0.5


def _extract_metadata(post: Dict[str, Any]) -> Dict[str, Any]:
    """Pull structured metadata out of raw post fields."""
    text = post.get("text") or post.get("content") or ""
    return {
        "urls": _URL_RE.findall(text),
        "hashtags": _HASHTAG_RE.findall(text),
        "mentions": _MENTION_RE.findall(text),
        "char_count": len(text),
        "word_count": len(text.split()) if text else 0,
        "has_media": bool(post.get("media") or post.get("attachments")),
        "engagement": {
            "likes": post.get("likes") or post.get("like_count") or 0,
            "shares": post.get("shares") or post.get("retweet_count") or post.get("share_count") or 0,
            "comments": post.get("comments") or post.get("reply_count") or 0,
        },
    }


# ── Core validation ────────────────────────────────────────────────────────────

def validate_post(post: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and score a single social media post dict.

    Returns a result dict with keys: hash, platform, score, issues, metadata.
    """
    issues: List[str] = []

    platform = (post.get("platform") or "unknown").strip().lower()
    if platform not in SUPPORTED_PLATFORMS:
        issues.append(f"unrecognized platform '{platform}'")

    text = post.get("text") or post.get("content") or ""
    if not text:
        issues.append("missing text/content field")

    tier = _platform_tier(platform)
    content = _content_score(text)
    temporal = _temporal_score(post)
    composite = round(min(10.0, tier + content + temporal), 2)

    return {
        "hash": _content_hash(post),
        "platform": platform,
        "score": composite,
        "tier_score": tier,
        "content_score": content,
        "temporal_score": temporal,
        "issues": issues,
        "metadata": _extract_metadata(post),
        "ingested_at": _utcnow_iso(),
    }


def ingest_social_media_posts(
    posts: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Ingest and validate a list of social media post dicts.

    Deduplicates by content hash, scores each post, and returns a summary
    envelope suitable for downstream pipeline consumption.

    Args:
        posts: List of raw post dicts (from API, scrape, or simulation).

    Returns:
        Envelope with keys: status, total, valid, invalid, duplicates,
        by_platform, avg_score, results, ingested_at.
    """
    if not isinstance(posts, list):
        return {
            "status": "error",
            "error": f"expected list, got {type(posts).__name__}",
            "total": 0,
            "valid": 0,
            "invalid": 0,
            "duplicates": 0,
            "by_platform": {},
            "avg_score": 0.0,
            "results": [],
            "ingested_at": _utcnow_iso(),
        }

    seen_hashes: set[str] = set()
    results: List[Dict[str, Any]] = []
    valid_count = 0
    invalid_count = 0
    duplicate_count = 0
    by_platform: Dict[str, int] = {}
    score_sum = 0.0

    for raw in posts:
        if not isinstance(raw, dict):
            invalid_count += 1
            results.append({
                "hash": None,
                "platform": "unknown",
                "score": 0.0,
                "issues": [f"post is not a dict: {type(raw).__name__}"],
                "metadata": {},
                "ingested_at": _utcnow_iso(),
            })
            continue

        result = validate_post(raw)
        h = result["hash"]

        if h in seen_hashes:
            duplicate_count += 1
            result["issues"].append("duplicate (content hash already seen in batch)")
        else:
            seen_hashes.add(h)

        if result["issues"] and not any(
            i.startswith("duplicate") for i in result["issues"]
        ):
            invalid_count += 1
        else:
            valid_count += 1

        plat = result["platform"]
        by_platform[plat] = by_platform.get(plat, 0) + 1
        score_sum += result["score"]
        results.append(result)

    total = len(results)
    avg_score = round(score_sum / total, 2) if total else 0.0

    return {
        "status": "ok",
        "total": total,
        "valid": valid_count,
        "invalid": invalid_count,
        "duplicates": duplicate_count,
        "by_platform": by_platform,
        "avg_score": avg_score,
        "results": results,
        "ingested_at": _utcnow_iso(),
    }


# ── CLI ────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="data_validator",
        description="Validate and ingest OSINT social media posts from JSON input.",
    )
    src = p.add_mutually_exclusive_group()
    src.add_argument("--file", metavar="PATH", help="JSON file containing posts array")
    src.add_argument("--stdin", action="store_true", help="Read JSON from stdin")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON output")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    raw_json: str = ""
    if args.stdin:
        raw_json = sys.stdin.read()
    elif args.file:
        path = Path(args.file)
        if not path.exists():
            err = {"status": "error", "error": f"file not found: {path}"}
            print(json.dumps(err, indent=2) if args.json else f"ERROR: {err['error']}")
            return 0
        raw_json = path.read_text(encoding="utf-8")
    else:
        parser.print_help()
        return 0

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        err = {"status": "error", "error": f"invalid JSON: {exc}"}
        print(json.dumps(err, indent=2) if args.json else f"ERROR: {err['error']}")
        return 0

    if isinstance(data, dict) and "posts" in data:
        data = data["posts"]

    result = ingest_social_media_posts(data)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Status : {result['status']}")
        print(f"Total  : {result['total']}")
        print(f"Valid  : {result['valid']}")
        print(f"Invalid: {result['invalid']}")
        print(f"Dupes  : {result['duplicates']}")
        print(f"AvgScore: {result['avg_score']}")
        print(f"By platform: {result['by_platform']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
