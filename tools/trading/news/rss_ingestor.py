# CUI // SP-CTI
"""FathomDesk News RSS Ingestor — Phase B2/B3.

Polls RSS/Atom feeds defined in args/news_feeds.yaml, deduplicates by
sha256(source||link)[:16], and persists new items to ad_news_items.

Security (OPT-58): All HTML summary content is stripped before storage —
RSS feeds are untrusted third-party input.

CLI:
  python -m tools.trading.news.rss_ingestor --once [--json] [--db PATH]
  python -m tools.trading.news.rss_ingestor --start [--interval N] [--json] [--db PATH]
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import logging
import sys
import time
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterator

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

from tools.logging.icdev_logger import get_logger  # noqa: E402

import feedparser
import yaml

from tools.db.storage import get_connection, sql_placeholder
from tools.trading.news.db import FATHOMDESK_DB, ensure_tables

# ---------------------------------------------------------------------------
# Config paths
# ---------------------------------------------------------------------------

_FEEDS_YAML = _ROOT / "args" / "news_feeds.yaml"
_DEFAULT_TIMEOUT = 10  # seconds per feed fetch
_DEFAULT_POLL_INTERVAL = 15  # minutes
_MAX_INPUT_BYTES = 50_000  # 50 KB — feed abuse guard (OPT-58)
_MAX_SUMMARY_CHARS = 500

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# HTML sanitizer — B3 (OPT-58)
# ---------------------------------------------------------------------------


class _TagStripper(HTMLParser):
    """Minimal HTML-to-text converter using stdlib only."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)


def _sanitize_html(html_str: str | None) -> str:
    """Strip HTML tags, decode entities, truncate to 500 chars.

    Input > 50 KB is truncated before parsing (feed abuse guard).
    Falls back to stdlib HTMLParser if BeautifulSoup is unavailable.
    """
    if not html_str:
        return ""
    # Size cap — untrusted input (OPT-58)
    if len(html_str) > _MAX_INPUT_BYTES:
        html_str = html_str[:_MAX_INPUT_BYTES]

    text = ""
    try:
        from bs4 import BeautifulSoup  # preferred — handles malformed HTML well

        text = BeautifulSoup(html_str, "html.parser").get_text(separator=" ")
    except Exception:
        # Stdlib fallback — no extra deps
        stripper = _TagStripper()
        try:
            stripper.feed(html_str)
            text = stripper.get_text()
        except Exception:
            text = html_str  # worst case: raw string, better than nothing

    # Collapse whitespace and truncate
    text = " ".join(text.split())
    return text[:_MAX_SUMMARY_CHARS]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_feeds_config(path: Path = _FEEDS_YAML) -> dict:
    """Load and return the parsed news_feeds.yaml config."""
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _iter_feeds(feeds_section: dict) -> Iterator[tuple[str, str]]:
    """Yield (feed_name, feed_url) from the 'feeds' section of the config."""
    for _group, feed_list in feeds_section.items():
        if not isinstance(feed_list, list):
            continue
        for entry in feed_list:
            name = entry.get("name", "").strip()
            url = entry.get("url", "").strip()
            if name and url:
                yield name, url


def _fetch_feed(url: str, timeout: int = _DEFAULT_TIMEOUT) -> feedparser.FeedParserDict:
    """Fetch and parse one RSS/Atom feed with an explicit per-feed timeout."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ICDEV-ADN-Ingestor/1.0 (+https://icdev.ai)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = resp.read()
    return feedparser.parse(content)


def _parse_published(struct: time.struct_time | None) -> str | None:
    """Convert feedparser's published_parsed (UTC struct_time) to ISO-8601 UTC."""
    if struct is None:
        return None
    try:
        # calendar.timegm treats struct as UTC → Unix timestamp
        ts = calendar.timegm(struct)
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:
        return None


def _item_id(source: str, link: str) -> str:
    """Stable 16-char hex id: sha256(source || link)[:16]."""
    return hashlib.sha256((source + link).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Core ingest logic
# ---------------------------------------------------------------------------


def _ingest_feed(
    feed_name: str,
    feed_url: str,
    conn,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict:
    """Ingest one feed.  Returns {new, skipped, errors}."""
    ph = sql_placeholder(conn)
    new_count = 0
    skip_count = 0
    errors: list[str] = []

    try:
        feed = _fetch_feed(feed_url, timeout=timeout)
    except Exception as exc:
        msg = f"fetch failed [{feed_name}] {feed_url}: {exc}"
        log.error(msg)
        return {"new": 0, "skipped": 0, "errors": [msg]}

    entries = feed.get("entries", [])
    if not entries:
        log.debug("No entries in feed [%s]", feed_name)

    for entry in entries:
        link = entry.get("link", "") or ""
        if not link:
            log.debug("Entry missing link in [%s], skipping", feed_name)
            skip_count += 1
            continue

        item_id = _item_id(feed_name, link)

        # Deduplicate
        existing = conn.execute(
            f"SELECT id FROM ad_news_items WHERE id = {ph}", (item_id,)
        ).fetchone()
        if existing:
            skip_count += 1
            continue

        # Build row
        title = (entry.get("title") or "").strip()
        summary_raw = (
            entry.get("summary")
            or entry.get("description")
            or ""
        )
        summary = _sanitize_html(summary_raw)
        published_at = _parse_published(entry.get("published_parsed"))
        ingested_at = datetime.now(timezone.utc).isoformat()

        try:
            conn.execute(
                f"""INSERT OR IGNORE INTO ad_news_items
                    (id, source, feed_url, title, link, published_at, ingested_at,
                     summary, mentioned_tickers, category, impact_level,
                     net_direction, classifier_version)
                    VALUES
                    ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
                (
                    item_id,
                    feed_name,
                    feed_url,
                    title,
                    link,
                    published_at,
                    ingested_at,
                    summary,
                    "[]",    # mentioned_tickers — Phase C fills
                    None,    # category          — Phase C fills
                    None,    # impact_level
                    None,    # net_direction
                    None,    # classifier_version
                ),
            )
            new_count += 1
        except Exception as exc:
            msg = f"insert error [{feed_name}] {link}: {exc}"
            log.error(msg)
            errors.append(msg)

    conn.commit()
    log.info("[%s] new=%d skipped=%d errors=%d", feed_name, new_count, skip_count, len(errors))
    return {"new": new_count, "skipped": skip_count, "errors": errors}


def run_once(
    feeds_path: Path = _FEEDS_YAML,
    db_path: str | Path | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict:
    """Single ingest pass across all configured feeds.

    Returns a summary dict suitable for --json output.
    """
    ensure_tables(db_path)
    cfg = _load_feeds_config(feeds_path)
    feeds_section = cfg.get("feeds", {})

    conn = get_connection(str(db_path) if db_path else str(FATHOMDESK_DB))
    total_new = 0
    total_skipped = 0
    all_errors: list[str] = []
    feed_results: dict[str, dict] = {}

    try:
        for feed_name, feed_url in _iter_feeds(feeds_section):
            # Per-feed isolation — one bad feed cannot block others
            try:
                result = _ingest_feed(feed_name, feed_url, conn, timeout=timeout)
            except Exception as exc:
                result = {"new": 0, "skipped": 0, "errors": [str(exc)]}
                log.error("Unexpected error on feed [%s]: %s", feed_name, exc)
            feed_results[feed_name] = result
            total_new += result["new"]
            total_skipped += result["skipped"]
            all_errors.extend(result["errors"])
    finally:
        conn.close()

    return {
        "status": "ok",
        "new": total_new,
        "skipped": total_skipped,
        "errors": all_errors,
        "feeds": feed_results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def run_daemon(
    feeds_path: Path = _FEEDS_YAML,
    db_path: str | Path | None = None,
    interval_minutes: int = _DEFAULT_POLL_INTERVAL,
    timeout: int = _DEFAULT_TIMEOUT,
) -> None:
    """Daemon loop — poll all feeds every interval_minutes.

    Reads poll_interval_minutes from YAML if not overridden via CLI.
    Runs until interrupted (SIGINT/SIGTERM).
    """
    cfg = _load_feeds_config(feeds_path)
    interval = interval_minutes or cfg.get("poll_interval_minutes", _DEFAULT_POLL_INTERVAL)
    log.info("ADN RSS daemon started — interval=%dm", interval)

    while True:
        try:
            result = run_once(feeds_path=feeds_path, db_path=db_path, timeout=timeout)
            log.info(
                "Ingest cycle done: new=%d skipped=%d errors=%d",
                result["new"],
                result["skipped"],
                len(result["errors"]),
            )
        except KeyboardInterrupt:
            log.info("ADN RSS daemon stopped by user")
            break
        except Exception as exc:
            log.error("Ingest cycle failed: %s", exc)

        try:
            time.sleep(interval * 60)
        except KeyboardInterrupt:
            log.info("ADN RSS daemon stopped by user")
            break


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="ADN RSS Ingestor — poll, dedupe, store news items"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--once",
        action="store_true",
        help="Run a single ingest pass then exit",
    )
    mode.add_argument(
        "--start",
        action="store_true",
        help="Run as a daemon, polling on interval",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--db", help="Override DB path (SQLite only)")
    parser.add_argument(
        "--feeds",
        help=f"Override feeds YAML path (default: {_FEEDS_YAML})",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=0,
        help="Poll interval in minutes (daemon mode; 0 = read from YAML)",
    )
    parser.add_argument("--timeout", type=int, default=_DEFAULT_TIMEOUT, help="Per-feed timeout (seconds)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    _setup_logging(args.verbose)

    feeds_path = Path(args.feeds) if args.feeds else _FEEDS_YAML
    db_path = args.db or None

    if args.once:
        result = run_once(feeds_path=feeds_path, db_path=db_path, timeout=args.timeout)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(
                f"Done — new={result['new']} skipped={result['skipped']}"
                f" errors={len(result['errors'])}"
            )
        sys.exit(0 if not result["errors"] else 1)

    elif args.start:
        run_daemon(
            feeds_path=feeds_path,
            db_path=db_path,
            interval_minutes=args.interval,
            timeout=args.timeout,
        )


if __name__ == "__main__":
    _main()
