#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Research Reflex — scrape NIST/CISA/DoD feeds, GitHub trending.

Fetches RSS/Atom/JSON feeds configured in context/genesis/feeds.yaml,
extracts new entries, deduplicates against existing signals, and exports
as GKP research_signal artifacts.

Scanner-tier only (zero Claude tokens).  Air-gap safe (graceful degradation).
"""

import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    """Fetch URL content.  Returns None on failure."""
    try:
        headers = {
            "User-Agent": "ICDEV-Genesis/2.0 (Research Reflex)",
            "Accept": "application/xml, application/rss+xml, application/json, text/xml, */*",
        }
        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (URLError, OSError, Exception) as e:
        print(f"  WARN: Failed to fetch {url}: {e}")
        return None


def _parse_rss(xml_text: str) -> List[Dict[str, str]]:
    """Parse RSS/Atom XML into list of {title, description, link, published}."""
    entries = []
    try:
        root = ET.fromstring(xml_text)
        # RSS 2.0
        for item in root.iter("item"):
            entry = {
                "title": (item.findtext("title") or "").strip(),
                "description": (item.findtext("description") or "")[:500].strip(),
                "link": (item.findtext("link") or "").strip(),
                "published": (item.findtext("pubDate") or "").strip(),
            }
            if entry["title"]:
                entries.append(entry)

        # Atom
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for item in root.findall(".//atom:entry", ns):
            link_el = item.find("atom:link", ns)
            entry = {
                "title": (item.findtext("atom:title", "", ns) or "").strip(),
                "description": (item.findtext("atom:summary", "", ns) or "")[:500].strip(),
                "link": link_el.get("href", "") if link_el is not None else "",
                "published": (item.findtext("atom:updated", "", ns) or "").strip(),
            }
            if entry["title"]:
                entries.append(entry)
    except ET.ParseError:
        pass
    return entries


def _parse_json_feed(json_text: str) -> List[Dict[str, str]]:
    """Parse JSON feed (e.g., CISA KEV)."""
    entries = []
    try:
        data = json.loads(json_text)
        # CISA KEV format
        if "vulnerabilities" in data:
            for vuln in data["vulnerabilities"][:20]:
                entries.append(
                    {
                        "title": f"KEV: {vuln.get('cveID', 'Unknown')} — {vuln.get('vendorProject', '')} {vuln.get('product', '')}",  # noqa: E501
                        "description": vuln.get("shortDescription", "")[:500],
                        "link": f"https://nvd.nist.gov/vuln/detail/{vuln.get('cveID', '')}",
                        "published": vuln.get("dateAdded", ""),
                    }
                )
        # Generic JSON array
        elif isinstance(data, list):
            for item in data[:20]:
                if isinstance(item, dict) and "title" in item:
                    entries.append(
                        {
                            "title": str(item.get("title", ""))[:200],
                            "description": str(item.get("description", item.get("summary", "")))[:500],
                            "link": str(item.get("url", item.get("link", ""))),
                            "published": str(item.get("date", item.get("published", ""))),
                        }
                    )
    except (json.JSONDecodeError, KeyError):
        pass
    return entries


def _is_duplicate(content_hash: str) -> bool:
    """Check if signal already exists in genesis_audit or innovation_signals."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM innovation_signals WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return (row["cnt"] if row else 0) > 0
    except Exception:
        return False
    finally:
        conn.close()


def _export_signal(feed_name: str, entry: Dict[str, str]) -> Optional[str]:
    """Export a single research signal as a GKP via the promoter."""
    try:
        from tools.genesis.promoter import export_gkp

        result = export_gkp(
            reflex="research",
            artifact_type="research_signal",
            payload={
                "title": entry["title"],
                "description": entry.get("description", ""),
                "source": f"genesis_research:{feed_name}",
                "url": entry.get("link", ""),
                "published": entry.get("published", ""),
                "score": 50,
            },
            confidence=0.7,
            evidence={"feed": feed_name, "fetched_at": _utcnow_iso()},
        )
        if result.get("status") == "exported":
            return result.get("gkp_id")
    except Exception as e:
        print(f"  WARN: Failed to export signal: {e}")
    return None


def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Research Reflex.

    Args:
        config: Reflex configuration from genesis_config.yaml
        trust: TrustKernel instance

    Returns:
        {"success": bool, "metric_value": float, "details": dict}
    """
    if _is_air_gapped():
        return {
            "success": True,
            "metric_value": 0,
            "details": {"status": "air_gapped", "message": "Skipped -- air-gapped mode"},
        }

    feeds = _load_feeds()
    if not feeds:
        return {
            "success": False,
            "metric_value": 0,
            "details": {"error": "No feeds configured in context/genesis/feeds.yaml"},
        }

    total_signals = 0
    total_dupes = 0
    feed_results = []

    for feed in feeds:
        feed_name = feed.get("name", "unknown")
        feed_type = feed.get("type", "rss")
        url = feed.get("url", "")

        # Skip non-fetchable types
        if feed_type in ("sam_bridge", "html_scrape"):
            feed_results.append(
                {
                    "feed": feed_name,
                    "status": "skipped",
                    "reason": f"Type '{feed_type}' not yet implemented in Research Reflex",
                }
            )
            continue

        if not url:
            continue

        print(f"  Fetching: {feed_name}")
        content = _fetch_url(url)
        if not content:
            feed_results.append({"feed": feed_name, "status": "fetch_failed"})
            continue

        # Parse based on type
        if feed_type == "json":
            entries = _parse_json_feed(content)
        else:
            entries = _parse_rss(content)

        new_count = 0
        for entry in entries[:10]:  # Cap at 10 per feed per cycle
            content_hash = _sha256(f"{entry['title']}:{entry.get('description', '')}")
            if _is_duplicate(content_hash):
                total_dupes += 1
                continue

            gkp_id = _export_signal(feed_name, entry)
            if gkp_id:
                new_count += 1
                total_signals += 1

        feed_results.append(
            {
                "feed": feed_name,
                "status": "ok",
                "entries_found": len(entries),
                "new_signals": new_count,
            }
        )

    return {
        "success": total_signals > 0 or total_dupes > 0,  # Success if we processed anything
        "metric_value": float(total_signals),
        "details": {
            "feeds_processed": len(feed_results),
            "total_new_signals": total_signals,
            "total_duplicates": total_dupes,
            "feed_results": feed_results,
        },
    }
