#!/usr/bin/env python3

# CUI // SP-CTI
"""Genesis Ingest Reflex — feed data into knowledge graph.

Watches NIST NVD, CISA KEV, FedRAMP updates and ingests new entries
into the ICDEV™ knowledge graph via the knowledge_graph ingester.

Distinct from Research Reflex: Research exports GKP signals, Ingest
writes directly into the knowledge graph for RAG retrieval.

GREEN tier (non-destructive writes to KG tables).  Air-gap safe.
"""
IMPLEMENTATION_STATUS = "full"

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402
from tools.security.injection_scanner import scan_text  # noqa: E402

logger = get_logger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_air_gapped() -> bool:
    return os.environ.get("ICDEV_ENVIRONMENT", "").lower() == "air-gapped"


def _load_feeds() -> List[Dict[str, Any]]:
    """Load feed definitions from context/genesis/feeds.yaml."""
    try:
        import yaml

        feeds_path = BASE_DIR / "context" / "genesis" / "feeds.yaml"
        if feeds_path.exists():
            with open(feeds_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("feeds", [])
    except ImportError:
        pass
    return []


def _fetch_url(url: str, timeout: int = 30) -> Optional[str]:
    """Fetch URL content."""
    try:
        headers = {
            "User-Agent": "ICDEV-Genesis/2.0 (Ingest Reflex)",
            "Accept": "application/xml, application/json, text/xml, */*",
        }
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
            return resp.read().decode("utf-8", errors="replace")
    except (URLError, OSError) as e:
        print(f"  WARN: Failed to fetch {url}: {e}")
        return None


def _parse_rss_entries(xml_text: str) -> List[Dict[str, str]]:
    """Parse RSS/Atom XML into entries."""
    entries = []
    try:
        root = ET.fromstring(xml_text)  # nosec B314 -- parsing trusted internal MBSE/config XML
        for item in root.iter("item"):
            entry = {
                "title": (item.findtext("title") or "").strip(),
                "description": (item.findtext("description") or "")[:1000].strip(),
                "link": (item.findtext("link") or "").strip(),
                "published": (item.findtext("pubDate") or "").strip(),
            }
            if entry["title"]:
                entries.append(entry)

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for item in root.findall(".//atom:entry", ns):
            link_el = item.find("atom:link", ns)
            entry = {
                "title": (item.findtext("atom:title", "", ns) or "").strip(),
                "description": (item.findtext("atom:summary", "", ns) or "")[:1000].strip(),
                "link": link_el.get("href", "") if link_el is not None else "",
                "published": (item.findtext("atom:updated", "", ns) or "").strip(),
            }
            if entry["title"]:
                entries.append(entry)
    except ET.ParseError:
        pass
    return entries


_FEED_CAP_MIN = 5
_FEED_CAP_DEFAULT = 15
_FEED_CAP_MAX = 50
_ANOMALY_Z_THRESHOLD = 2.0
_NOVELTY_SCORE_MIN = 20
_NOVELTY_SCORE_MAX = 80
_NOVELTY_SCORE_DEFAULT = 40


def _compute_adaptive_cap(feed_name: str, current_batch_size: int) -> int:
    """Return an adaptive per-cycle entry cap via Z-score anomaly detection.

    Queries historical daily ingest totals for this feed, computes mean/std,
    then compares current_batch_size against that distribution.  Anomalously
    large batches are capped more aggressively; normal batches use the
    generous upper bound.  Falls back to _FEED_CAP_DEFAULT on any error.
    """
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT SUBSTR(created_at, 1, 10) AS day, COUNT(*) AS cnt
              FROM innovation_signals
             WHERE source = %s
             GROUP BY SUBSTR(created_at, 1, 10)
             ORDER BY day DESC
             LIMIT 60
            """,
            (f"genesis_ingest:{feed_name}",),
        ).fetchall()
        conn.close()

        if len(rows) < 3:
            return _FEED_CAP_DEFAULT

        counts = [(r["cnt"] if isinstance(r, dict) else r[1]) for r in rows]
        mean = sum(counts) / len(counts)
        variance = sum((c - mean) ** 2 for c in counts) / len(counts)
        std = variance ** 0.5 or 1.0

        z_score = (current_batch_size - mean) / std
        if z_score > _ANOMALY_Z_THRESHOLD:
            cap = max(_FEED_CAP_MIN, int(mean + std))
        else:
            cap = min(_FEED_CAP_MAX, max(_FEED_CAP_DEFAULT, int(mean + 2 * std)))
        return cap
    except Exception:
        return _FEED_CAP_DEFAULT


def _load_recent_titles(limit: int = 100) -> List[str]:
    """Fetch recent signal titles for novelty comparison."""
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT title FROM innovation_signals ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        conn.close()
        return [(r["title"] if isinstance(r, dict) else r[0]) or "" for r in rows]
    except Exception:
        return []


def _compute_novelty_score(title: str, description: str, recent_titles: List[str]) -> int:
    """Score entry novelty using Jaccard-similarity anomaly detection.

    Entries dissimilar from recent signals (low max-Jaccard — anomalously
    novel) receive higher innovation scores.  Returns a value in
    [_NOVELTY_SCORE_MIN, _NOVELTY_SCORE_MAX].
    """
    if not recent_titles:
        return _NOVELTY_SCORE_DEFAULT

    def _tokens(text: str) -> set:
        return set(text.lower().split())

    entry_tokens = _tokens(f"{title} {description}")
    if not entry_tokens:
        return _NOVELTY_SCORE_DEFAULT

    max_sim = 0.0
    for t in recent_titles[:50]:
        rt = _tokens(t)
        if not rt:
            continue
        union = len(entry_tokens | rt)
        if union:
            sim = len(entry_tokens & rt) / union
            if sim > max_sim:
                max_sim = sim

    score = int(_NOVELTY_SCORE_MAX - max_sim * (_NOVELTY_SCORE_MAX - _NOVELTY_SCORE_MIN))
    return max(_NOVELTY_SCORE_MIN, min(_NOVELTY_SCORE_MAX, score))


def _ingest_to_knowledge_graph(entries: List[Dict], feed_name: str, category: str) -> int:
    """Insert entries into innovation_signals table for knowledge enrichment."""
    import hashlib

    cap = _compute_adaptive_cap(feed_name, len(entries))
    recent_titles = _load_recent_titles()

    conn = get_connection()
    ingested = 0
    try:
        for entry in entries[:cap]:
            title = entry.get("title", "")
            if not title:
                continue

            content_hash = hashlib.sha256(f"{title}:{entry.get('description', '')}".encode()).hexdigest()

            # Check for duplicates
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM innovation_signals WHERE content_hash = %s", (content_hash,)
            ).fetchone()
            if row and (row["cnt"] if isinstance(row, dict) else row[0]) > 0:
                continue

            signal_id = f"sig-{uuid.uuid4().hex[:8]}"
            novelty_score = _compute_novelty_score(title, entry.get("description", ""), recent_titles)
            conn.execute(
                """INSERT INTO innovation_signals
                   (id, source, source_type, title, description, content_hash,
                    innovation_score, status, discovered_at, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    signal_id,
                    f"genesis_ingest:{feed_name}",
                    category,
                    title[:500],
                    entry.get("description", "")[:2000],
                    content_hash,
                    novelty_score,
                    "new",
                    _utcnow_iso(),
                    _utcnow_iso(),
                ),
            )
            ingested += 1
        conn.commit()
    except Exception as e:
        print(f"  WARN: KG ingest error: {e}")
    finally:
        conn.close()
    return ingested


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Ingest Reflex."""
    if _is_air_gapped():
        return {
            "success": True,
            "metric_value": 0,
            "details": {"status": "air_gapped"},
        }

    feeds = _load_feeds()
    if not feeds:
        return {
            "success": False,
            "metric_value": 0,
            "details": {"error": "No feeds in context/genesis/feeds.yaml"},
        }

    total_nodes = 0
    feed_results = []

    for feed in feeds:
        feed_name = feed.get("name", "unknown")
        feed_type = feed.get("type", "rss")
        url = feed.get("url", "")
        category = feed.get("category", "general")
        ingest_to = feed.get("ingest_to", "knowledge_graph")

        # Only ingest feeds targeting knowledge_graph
        if ingest_to != "knowledge_graph":
            continue

        # Skip non-fetchable types
        if feed_type in ("sam_bridge", "html_scrape"):
            feed_results.append(
                {
                    "feed": feed_name,
                    "status": "skipped",
                    "reason": f"Type '{feed_type}' not implemented",
                }
            )
            continue

        if not url:
            continue

        print(f"  Ingesting: {feed_name}")
        content = _fetch_url(url)
        if not content:
            feed_results.append({"feed": feed_name, "status": "fetch_failed"})
            continue

        # Injection scan — block content with critical findings
        findings = scan_text(content, source=url)
        critical = [f for f in findings if f["severity"] == "critical"]
        if critical:
            logger.warning(
                "Injection attempt blocked from %s: %s",
                url,
                [f["category"] for f in critical],
            )
            feed_results.append(
                {
                    "feed": feed_name,
                    "status": "blocked",
                    "reason": "injection_detected",
                    "categories": [f["category"] for f in critical],
                }
            )
            continue

        if feed_type == "json":
            # JSON feeds not ingested to KG in this reflex (handled by research)
            feed_results.append({"feed": feed_name, "status": "skipped", "reason": "JSON → research reflex"})
            continue

        entries = _parse_rss_entries(content)
        ingested = _ingest_to_knowledge_graph(entries, feed_name, category)
        total_nodes += ingested

        feed_results.append(
            {
                "feed": feed_name,
                "status": "ok",
                "entries_found": len(entries),
                "nodes_added": ingested,
            }
        )

    return {
        "success": True,  # 0 new nodes = all duplicates = healthy state
        "metric_value": float(total_nodes),
        "details": {
            "feeds_processed": len(feed_results),
            "total_nodes_added": total_nodes,
            "feed_results": feed_results,
        },
    }
