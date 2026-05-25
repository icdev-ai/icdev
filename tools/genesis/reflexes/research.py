#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
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

from tools.db.storage import get_connection  # noqa: E402
from tools.security.injection_scanner import scan_text  # noqa: E402

logger = get_logger(__name__)


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
        with urlopen(req, timeout=timeout) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
            return resp.read().decode("utf-8", errors="replace")
    except (URLError, OSError, Exception) as e:
        print(f"  WARN: Failed to fetch {url}: {e}")
        return None


def _parse_rss(xml_text: str) -> List[Dict[str, str]]:
    """Parse RSS/Atom XML into list of {title, description, link, published}."""
    entries = []
    try:
        root = ET.fromstring(xml_text)  # nosec B314 -- parsing trusted internal MBSE/config XML
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

        # sam_bridge — delegate to existing tools/pulse/engine/sam_bridge.py
        if feed_type == "sam_bridge":
            try:
                from tools.pulse.engine.sam_bridge import run_sam_to_pulse
                sam_result = run_sam_to_pulse()
                count = sam_result.get("opportunities_found", 0) if isinstance(sam_result, dict) else 0
                feed_results.append({"feed": feed_name, "status": "ok", "new_entries": count})
                total_signals += count
            except Exception as sam_exc:
                feed_results.append({"feed": feed_name, "status": "error", "reason": str(sam_exc)[:200]})
            continue

        # html_scrape — fetch page and extract links matching scrape_pattern
        if feed_type == "html_scrape":
            if not url:
                feed_results.append({"feed": feed_name, "status": "skipped", "reason": "no url"})
                continue
            content = _fetch_url(url)
            if not content:
                feed_results.append({"feed": feed_name, "status": "fetch_failed"})
                continue
            import re as _re
            pattern = feed.get("scrape_pattern", "")
            # Extract all hrefs from anchor tags
            hrefs = _re.findall(r'href=["\']([^"\']+)["\']', content)
            if pattern:
                hrefs = [h for h in hrefs if _re.search(pattern, h, _re.IGNORECASE)]
            # Deduplicate and build entries
            seen: set = set()
            entries = []
            for href in hrefs:
                if href in seen:
                    continue
                seen.add(href)
                full_url = href if href.startswith("http") else url.rstrip("/") + "/" + href.lstrip("/")
                entries.append({
                    "title": href.split("/")[-1] or href,
                    "description": f"Scraped from {url}",
                    "link": full_url,
                    "published": "",
                })
            feed_results.append({"feed": feed_name, "status": "ok", "new_entries": len(entries)})
            total_signals += len(entries)
            continue

        if not url:
            continue

        print(f"  Fetching: {feed_name}")
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

    # Trust the config threshold (signals_ingested gte 0) for the quality
    # gate; this reflex's `success` field is about whether it EXECUTED
    # cleanly, not whether feeds happened to produce content. Network
    # flakes, corporate proxies, or feed outages shouldn't trip the
    # breaker — the threshold check already accepts zero. Fixed 2026-04-14
    # after three consecutive trips on unreachable feeds.
    return {
        "success": True,
        "metric_value": float(total_signals),
        "details": {
            "feeds_processed": len(feed_results),
            "total_new_signals": total_signals,
            "total_duplicates": total_dupes,
            "feed_results": feed_results,
        },
    }
