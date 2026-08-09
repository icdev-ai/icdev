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

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

from tools.db.storage import get_connection  # noqa: E402
from tools.security.injection_scanner import scan_text  # noqa: E402

IMPLEMENTATION_STATUS = "full"

logger = get_logger(__name__)

# --- NLP Scraper config (html_scrape feed type) ---
_nlp_scraper_cfg: Dict[str, Any] = {}
try:
    import yaml as _yaml

    _nlp_scraper_yaml = BASE_DIR / "args" / "aiify_config.yaml"
    if _nlp_scraper_yaml.exists():
        with open(_nlp_scraper_yaml, "r", encoding="utf-8") as _f:
            _nlp_scraper_cfg = (_yaml.safe_load(_f) or {}).get("research_nlp_scraper", {})
except Exception:
    pass

_RESEARCH_NLP_ENABLED: bool = (
    os.environ.get("ICDEV_RESEARCH_NLP_SCRAPER", "").lower() in ("1", "true", "yes")
    or bool(_nlp_scraper_cfg.get("enabled", False))
)
_RESEARCH_NLP_MODEL: str = str(_nlp_scraper_cfg.get("model", "claude-haiku-4-5-20251001"))
_RESEARCH_NLP_MAX_TOKENS: int = int(_nlp_scraper_cfg.get("max_tokens", 256))

_NLP_SCRAPER_SYSTEM = (
    "You are a link extractor for a security intelligence feed scraper. "
    "Given an HTML snippet and an optional topic pattern, extract relevant hyperlink URLs. "
    "Return one URL per line — no bullets, no labels, no explanation. "
    "If a topic pattern is provided, only include links whose path or anchor text matches it. "
    "If no topic is provided, return all non-navigation hrefs. "
    "Return at most 20 URLs."
)

_NLP_LINK_CACHE: Dict[str, List[str]] = {}

# --- Research reflex configurable thresholds (aiify_config.yaml: research_reflex) ---
_research_reflex_cfg: Dict[str, Any] = {}
try:
    import yaml as _yaml_rf

    _research_reflex_yaml = BASE_DIR / "args" / "aiify_config.yaml"
    if _research_reflex_yaml.exists():
        with open(_research_reflex_yaml, "r", encoding="utf-8") as _rf:
            _research_reflex_cfg = (_yaml_rf.safe_load(_rf) or {}).get("research_reflex", {})
except Exception:
    pass

_MAX_ENTRIES_PER_FEED: int = int(_research_reflex_cfg.get("max_entries_per_feed", 10))
_MAX_JSON_ITEMS: int = int(_research_reflex_cfg.get("max_json_items", 20))
_MAX_KEV_VULNS: int = int(_research_reflex_cfg.get("max_kev_vulns", 20))
_SIGNAL_CONFIDENCE: float = float(_research_reflex_cfg.get("signal_confidence", 0.7))
_SIGNAL_BASE_SCORE: int = int(_research_reflex_cfg.get("signal_base_score", 50))
_HTML_SNIPPET_LIMIT: int = int(_research_reflex_cfg.get("html_snippet_limit", 4000))


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


def _fetch_url(url: str, timeout: Optional[int] = None) -> Optional[str]:
    """Fetch URL content via the central HTTP client.  Returns None on failure.

    Routed through ``tools/http/fetch_extract.py`` so mTLS, the egress proxy and
    the retry/backoff policy in ``args/http_client.yaml`` apply to feed scraping
    the same as everywhere else.  Returns the raw body: callers parse RSS/Atom
    XML or scrape hrefs, neither of which survives markdown extraction.
    """
    from tools.http.fetch_extract import fetch_raw

    page = fetch_raw(
        url,
        headers={
            "User-Agent": "ICDEV-Genesis/2.0 (Research Reflex)",
            "Accept": "application/xml, application/rss+xml, application/json, text/xml, */*",
        },
        timeout=timeout,
    )
    if not page.ok:
        print(f"  WARN: Failed to fetch {url}: {page.error}")
        return None
    return page.raw_text


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
            for vuln in data["vulnerabilities"][:_MAX_KEV_VULNS]:
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
            for item in data[:_MAX_JSON_ITEMS]:
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
            "SELECT COUNT(*) as cnt FROM innovation_signals WHERE content_hash = %s", (content_hash,)
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
                "score": _SIGNAL_BASE_SCORE,
            },
            confidence=_SIGNAL_CONFIDENCE,
            evidence={"feed": feed_name, "fetched_at": _utcnow_iso()},
        )
        if result.get("status") == "exported":
            return result.get("gkp_id")
    except Exception as e:
        print(f"  WARN: Failed to export signal: {e}")
    return None


def _extract_links_nlp(html_content: str, feed_config: Dict[str, Any]) -> Optional[List[str]]:
    """Extract links from HTML using an LLM NLP extractor.

    Returns a list of href strings, or None if NLP is disabled/unavailable.
    Falls back to None so the caller can apply the regex path.

    The snippet handed to the model is scraped third-party HTML, so the router's
    injection scan stays ON here.  It previously ran with
    ``skip_injection_scan=True`` — the flag is for trusted internal pipeline
    content, and this is the opposite of that.
    """
    if not _RESEARCH_NLP_ENABLED:
        return None

    cache_key = _sha256(html_content[:2000] + feed_config.get("scrape_pattern", ""))
    if cache_key in _NLP_LINK_CACHE:
        return _NLP_LINK_CACHE[cache_key]

    try:
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter
    except ImportError:
        return None

    try:
        html_snippet = html_content[:_HTML_SNIPPET_LIMIT]
        pattern_hint = feed_config.get("scrape_pattern", "")
        user_content = f"HTML:\n{html_snippet}"
        if pattern_hint:
            user_content = f"Topic pattern: {pattern_hint}\n\n{user_content}"

        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=_NLP_SCRAPER_SYSTEM,
            model=_RESEARCH_NLP_MODEL,
            max_tokens=_RESEARCH_NLP_MAX_TOKENS,
            temperature=0.0,
        )
        response = router.invoke("research_nlp_scraper", request)
        if not (response and response.content):
            return None

        links = [line.strip() for line in response.content.strip().splitlines() if line.strip()]
        _NLP_LINK_CACHE[cache_key] = links
        return links
    except Exception:
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

            # Injection scan — the rss/json branch below already blocks on
            # critical findings; this branch used to skip the gate entirely and
            # then hand the raw HTML to a model.
            scrape_findings = scan_text(content, source=url)
            scrape_critical = [f for f in scrape_findings if f["severity"] == "critical"]
            if scrape_critical:
                logger.warning(
                    "Injection attempt blocked from %s: %s",
                    url,
                    [f["category"] for f in scrape_critical],
                )
                feed_results.append(
                    {
                        "feed": feed_name,
                        "status": "blocked",
                        "reason": "injection_detected",
                        "categories": [f["category"] for f in scrape_critical],
                    }
                )
                continue

            # NLP extractor (preferred): topic-aware link extraction via LLM.
            # Falls back to regex when NLP is disabled or the LLM is unavailable.
            nlp_hrefs = _extract_links_nlp(content, feed)
            if nlp_hrefs is not None:
                hrefs = nlp_hrefs
            else:
                import re as _re
                pattern = feed.get("scrape_pattern", "")
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
        for entry in entries[:_MAX_ENTRIES_PER_FEED]:
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
