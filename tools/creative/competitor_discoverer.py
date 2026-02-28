#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Competitor Auto-Discoverer — discover competitors from review site category pages.

Scans G2, Capterra, and TrustRadius category pages to auto-discover competitor
products. Extracted competitors are stored as 'discovered' (advisory-only, D353)
and require human confirmation before use in downstream analysis.

Architecture:
    - Category page URLs configured in args/creative_config.yaml
    - HTML parsing uses stdlib re (air-gap safe, no BeautifulSoup dependency)
    - Extraction attempts JSON-LD first, falls back to meta tags, then HTML patterns
    - Dedup by (name, source) pair — no duplicate competitor entries
    - creative_competitors table allows UPDATE for status transitions (D357)
    - All discovery actions are audit-logged (D6 append-only audit trail)

Usage:
    python tools/creative/competitor_discoverer.py --discover --domain "proposal management" --json
    python tools/creative/competitor_discoverer.py --list --json
    python tools/creative/competitor_discoverer.py --list --status confirmed --json
    python tools/creative/competitor_discoverer.py --confirm --competitor-id "comp-abc" --confirmed-by "user@mil" --json
    python tools/creative/competitor_discoverer.py --archive --competitor-id "comp-abc" --json
    python tools/creative/competitor_discoverer.py --refresh --competitor-id "comp-abc" --json
    python tools/creative/competitor_discoverer.py --human
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "creative_config.yaml"

# =========================================================================
# GRACEFUL IMPORTS
# =========================================================================
try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

try:
    from tools.audit.audit_logger import log_event as _audit_log
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def _audit_log(**kw):  # noqa: E302
        return -1

# =========================================================================
# HELPERS
# =========================================================================
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not Path(str(path)).exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _comp_id():
    """Generate a competitor ID with comp- prefix."""
    return f"comp-{uuid.uuid4().hex[:12]}"


def _content_hash(text):
    """SHA-256 content hash for deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _audit(event_type, action, details=None):
    """Write audit trail entry."""
    if _HAS_AUDIT:
        try:
            _audit_log(event_type=event_type, actor="creative-engine",
                       action=action,
                       details=json.dumps(details) if details else None,
                       project_id="creative-engine")
        except Exception:
            pass


def _load_config():
    """Load creative config from YAML."""
    if not _HAS_YAML or not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _safe_get(url, headers=None, params=None, as_text=False):
    """HTTP GET with error handling. Returns (data, error).

    When as_text=True, returns raw response text instead of parsed JSON.
    """
    if not _HAS_REQUESTS:
        return None, "requests library not installed"
    try:
        hdrs = headers or {}
        hdrs.setdefault("User-Agent",
                        "Mozilla/5.0 (compatible; ICDEVBot/1.0; +https://icdev.local)")
        resp = _requests.get(url, headers=hdrs, params=params, timeout=30)
        if resp.status_code in (403, 429):
            return None, "rate_limited" if resp.status_code == 429 else "forbidden"
        if resp.status_code == 404:
            return None, "not_found"
        resp.raise_for_status()
        if as_text:
            return resp.text, None
        return resp.json(), None
    except _requests.exceptions.RequestException as exc:
        return None, str(exc)
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"json_decode_error: {exc}"


# =========================================================================
# JSON-LD & HTML EXTRACTION HELPERS
# =========================================================================
_RE_JSONLD = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_RE_META_NAME = re.compile(
    r'<meta\s[^>]*?content=["\']([^"\']+)["\'][^>]*/?>',
    re.IGNORECASE,
)
_RE_RATING = re.compile(r'(\d+(?:\.\d+)?)\s*/?\s*(?:out of\s*)?5(?:\.0)?', re.IGNORECASE)
_RE_REVIEW_COUNT = re.compile(r'(\d[\d,]*)\s*(?:reviews?|ratings?|verified)', re.IGNORECASE)


def _extract_jsonld_products(html):
    """Extract product/software entries from JSON-LD blocks."""
    results = []
    for match in _RE_JSONLD.finditer(html):
        try:
            data = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            kind = item.get("@type", "")
            if kind in ("Product", "SoftwareApplication", "WebApplication"):
                name = item.get("name", "").strip()
                if not name:
                    continue
                rating_obj = item.get("aggregateRating", {})
                results.append({
                    "name": name,
                    "rating": _safe_float(rating_obj.get("ratingValue")),
                    "review_count": _safe_int(rating_obj.get("reviewCount")),
                })
            elif kind == "ItemList":
                for elem in item.get("itemListElement", []):
                    inner = elem.get("item", elem)
                    nm = inner.get("name", "").strip()
                    if nm:
                        ar = inner.get("aggregateRating", {})
                        results.append({
                            "name": nm,
                            "rating": _safe_float(ar.get("ratingValue")),
                            "review_count": _safe_int(ar.get("reviewCount")),
                        })
    return results


def _safe_float(val):
    """Attempt to convert to float, return None on failure."""
    if val is None:
        return None
    try:
        return round(float(str(val).replace(",", "")), 2)
    except (ValueError, TypeError):
        return None


def _safe_int(val):
    """Attempt to convert to int, return 0 on failure."""
    if val is None:
        return 0
    try:
        return int(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return 0


def _extract_products_from_html(html, patterns):
    """Fallback HTML pattern extraction. patterns is a list of compiled regexes
    that capture product names from the page HTML."""
    results = []
    seen = set()
    for pat in patterns:
        for m in pat.finditer(html):
            name = m.group(1).strip()
            # Clean HTML entities and tags
            name = re.sub(r"<[^>]+>", "", name).strip()
            name = name.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
            if not name or len(name) < 2 or len(name) > 120:
                continue
            key = name.lower()
            if key not in seen:
                seen.add(key)
                results.append({"name": name, "rating": None, "review_count": 0})
    return results


def _enrich_with_page_ratings(products, html):
    """Try to enrich products with ratings/review counts found near their name."""
    for prod in products:
        if prod.get("rating") is not None:
            continue
        # Look for rating near the product name in the HTML
        escaped = re.escape(prod["name"])
        ctx_pat = re.compile(escaped + r'.{0,500}', re.DOTALL | re.IGNORECASE)
        ctx = ctx_pat.search(html)
        if ctx:
            snippet = ctx.group(0)
            rm = _RE_RATING.search(snippet)
            if rm:
                prod["rating"] = _safe_float(rm.group(1))
            rcm = _RE_REVIEW_COUNT.search(snippet)
            if rcm:
                prod["review_count"] = _safe_int(rcm.group(1))
    return products


# =========================================================================
# G2 PATTERNS
# =========================================================================
_G2_PRODUCT_PATTERNS = [
    re.compile(
        r'<div[^>]*class="[^"]*product-card[^"]*"[^>]*>.*?'
        r'<a[^>]*>([^<]{2,80})</a>',
        re.DOTALL | re.IGNORECASE,
    ),
    re.compile(
        r'data-product-name=["\']([^"\']{2,80})["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<h3[^>]*class="[^"]*product[^"]*"[^>]*>\s*<a[^>]*>([^<]{2,80})</a>',
        re.DOTALL | re.IGNORECASE,
    ),
    re.compile(
        r'"product_name"\s*:\s*"([^"]{2,80})"',
        re.IGNORECASE,
    ),
]

# =========================================================================
# CAPTERRA PATTERNS
# =========================================================================
_CAPTERRA_PRODUCT_PATTERNS = [
    re.compile(
        r'<a[^>]*class="[^"]*listing-name[^"]*"[^>]*>([^<]{2,80})</a>',
        re.DOTALL | re.IGNORECASE,
    ),
    re.compile(
        r'data-testid="[^"]*product-name[^"]*"[^>]*>([^<]{2,80})<',
        re.IGNORECASE,
    ),
    re.compile(
        r'"softwareName"\s*:\s*"([^"]{2,80})"',
        re.IGNORECASE,
    ),
    re.compile(
        r'<span[^>]*class="[^"]*product-name[^"]*"[^>]*>([^<]{2,80})</span>',
        re.DOTALL | re.IGNORECASE,
    ),
]

# =========================================================================
# TRUSTRADIUS PATTERNS
# =========================================================================
_TRUSTRADIUS_PRODUCT_PATTERNS = [
    re.compile(
        r'<a[^>]*class="[^"]*product-name[^"]*"[^>]*>([^<]{2,80})</a>',
        re.DOTALL | re.IGNORECASE,
    ),
    re.compile(
        r'"productName"\s*:\s*"([^"]{2,80})"',
        re.IGNORECASE,
    ),
    re.compile(
        r'data-product=["\']([^"\']{2,80})["\']',
        re.IGNORECASE,
    ),
    re.compile(
        r'<h[23][^>]*>\s*<a[^>]*href="/products/[^"]*"[^>]*>([^<]{2,80})</a>',
        re.DOTALL | re.IGNORECASE,
    ),
]


# =========================================================================
# DISCOVERY FUNCTIONS
# =========================================================================
def discover_from_g2(category_url, max_competitors=20):
    """Fetch G2 category page and extract competitor products.

    Attempts JSON-LD extraction first, then falls back to HTML patterns.
    Returns list of competitor dicts.
    """
    if not category_url:
        return []
    html, err = _safe_get(category_url, as_text=True)
    if err or not html:
        _audit("creative.discover.g2", f"G2 fetch failed: {err}",
               {"url": category_url, "error": err})
        return []
    # Attempt 1: JSON-LD structured data
    products = _extract_jsonld_products(html)
    # Attempt 2: HTML patterns
    if not products:
        products = _extract_products_from_html(html, _G2_PRODUCT_PATTERNS)
    # Enrich ratings from surrounding context
    products = _enrich_with_page_ratings(products, html)
    # Normalize
    results = []
    seen = set()
    for p in products[:max_competitors]:
        key = p["name"].lower().strip()
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "name": p["name"],
            "source": "g2",
            "source_url": category_url,
            "rating": p.get("rating"),
            "review_count": p.get("review_count", 0),
            "features": [],
            "pricing_tier": None,
            "metadata": {"extraction_method": "jsonld" if p.get("rating") is not None else "html_pattern"},
        })
    return results


def discover_from_capterra(category_url, max_competitors=20):
    """Fetch Capterra category page and extract competitor products.

    Attempts JSON-LD extraction first, then falls back to HTML patterns.
    Returns list of competitor dicts.
    """
    if not category_url:
        return []
    html, err = _safe_get(category_url, as_text=True)
    if err or not html:
        _audit("creative.discover.capterra", f"Capterra fetch failed: {err}",
               {"url": category_url, "error": err})
        return []
    products = _extract_jsonld_products(html)
    if not products:
        products = _extract_products_from_html(html, _CAPTERRA_PRODUCT_PATTERNS)
    products = _enrich_with_page_ratings(products, html)
    results = []
    seen = set()
    for p in products[:max_competitors]:
        key = p["name"].lower().strip()
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "name": p["name"],
            "source": "capterra",
            "source_url": category_url,
            "rating": p.get("rating"),
            "review_count": p.get("review_count", 0),
            "features": [],
            "pricing_tier": None,
            "metadata": {"extraction_method": "jsonld" if p.get("rating") is not None else "html_pattern"},
        })
    return results


def discover_from_trustradius(category_url, max_competitors=20):
    """Fetch TrustRadius category page and extract competitor products.

    Attempts JSON-LD extraction first, then falls back to HTML patterns.
    Returns list of competitor dicts.
    """
    if not category_url:
        return []
    html, err = _safe_get(category_url, as_text=True)
    if err or not html:
        _audit("creative.discover.trustradius", f"TrustRadius fetch failed: {err}",
               {"url": category_url, "error": err})
        return []
    products = _extract_jsonld_products(html)
    if not products:
        products = _extract_products_from_html(html, _TRUSTRADIUS_PRODUCT_PATTERNS)
    products = _enrich_with_page_ratings(products, html)
    results = []
    seen = set()
    for p in products[:max_competitors]:
        key = p["name"].lower().strip()
        if key in seen:
            continue
        seen.add(key)
        results.append({
            "name": p["name"],
            "source": "trustradius",
            "source_url": category_url,
            "rating": p.get("rating"),
            "review_count": p.get("review_count", 0),
            "features": [],
            "pricing_tier": None,
            "metadata": {"extraction_method": "jsonld" if p.get("rating") is not None else "html_pattern"},
        })
    return results


# =========================================================================
# DATABASE FUNCTIONS
# =========================================================================
def store_competitors(competitors, domain, db_path=None):
    """Store discovered competitors in creative_competitors table.

    Dedup by (name, source) pair — skips if already exists.
    Returns {stored: N, duplicates: N}.
    """
    conn = _get_db(db_path)
    stored, duplicates = 0, 0
    ts = _now()
    try:
        for comp in competitors:
            name = comp.get("name", "").strip()
            source = comp.get("source", "manual")
            if not name:
                continue
            # Dedup check
            existing = conn.execute(
                "SELECT id FROM creative_competitors WHERE name=? AND source=?",
                (name, source)
            ).fetchone()
            if existing:
                duplicates += 1
                continue
            cid = _comp_id()
            conn.execute(
                "INSERT INTO creative_competitors "
                "(id, name, domain, source, source_url, rating, review_count, "
                "features, pricing_tier, status, metadata, discovered_at, classification) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cid,
                    name,
                    domain,
                    source,
                    comp.get("source_url", ""),
                    comp.get("rating"),
                    comp.get("review_count", 0),
                    json.dumps(comp.get("features", [])),
                    comp.get("pricing_tier"),
                    "discovered",
                    json.dumps(comp.get("metadata", {})),
                    ts,
                    "CUI",
                ),
            )
            stored += 1
        conn.commit()
    finally:
        conn.close()
    return {"stored": stored, "duplicates": duplicates}


def confirm_competitor(competitor_id, confirmed_by, db_path=None):
    """Confirm a discovered competitor. Transitions status from 'discovered' to 'confirmed'.

    Returns {confirmed: true, competitor_id: X} on success.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT id, status FROM creative_competitors WHERE id=?",
            (competitor_id,)
        ).fetchone()
        if not row:
            return {"error": f"Competitor not found: {competitor_id}"}
        if row["status"] == "confirmed":
            return {"error": f"Competitor already confirmed: {competitor_id}"}
        if row["status"] == "archived":
            return {"error": f"Cannot confirm archived competitor: {competitor_id}"}
        conn.execute(
            "UPDATE creative_competitors SET status='confirmed', confirmed_at=?, "
            "confirmed_by=? WHERE id=?",
            (_now(), confirmed_by, competitor_id),
        )
        conn.commit()
    finally:
        conn.close()
    _audit("creative.competitor.confirm",
           f"Confirmed competitor {competitor_id}",
           {"competitor_id": competitor_id, "confirmed_by": confirmed_by})
    return {"confirmed": True, "competitor_id": competitor_id}


def archive_competitor(competitor_id, db_path=None):
    """Archive a competitor. Transitions status to 'archived'.

    Returns {archived: true, competitor_id: X} on success.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT id, status FROM creative_competitors WHERE id=?",
            (competitor_id,)
        ).fetchone()
        if not row:
            return {"error": f"Competitor not found: {competitor_id}"}
        if row["status"] == "archived":
            return {"error": f"Competitor already archived: {competitor_id}"}
        conn.execute(
            "UPDATE creative_competitors SET status='archived' WHERE id=?",
            (competitor_id,),
        )
        conn.commit()
    finally:
        conn.close()
    _audit("creative.competitor.archive",
           f"Archived competitor {competitor_id}",
           {"competitor_id": competitor_id})
    return {"archived": True, "competitor_id": competitor_id}


def get_competitors(domain=None, status=None, db_path=None):
    """Retrieve competitors from creative_competitors table with optional filters.

    Returns list of competitor dicts.
    """
    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM creative_competitors WHERE 1=1"
        params = []
        if domain:
            query += " AND domain=?"
            params.append(domain)
        if status:
            query += " AND status=?"
            params.append(status)
        query += " ORDER BY discovered_at DESC"
        rows = conn.execute(query, params).fetchall()
        results = []
        for row in rows:
            results.append({
                "id": row["id"],
                "name": row["name"],
                "domain": row["domain"],
                "source": row["source"],
                "source_url": row["source_url"],
                "rating": row["rating"],
                "review_count": row["review_count"],
                "features": json.loads(row["features"] or "[]"),
                "pricing_tier": row["pricing_tier"],
                "status": row["status"],
                "metadata": json.loads(row["metadata"] or "{}"),
                "discovered_at": row["discovered_at"],
                "confirmed_at": row["confirmed_at"],
                "confirmed_by": row["confirmed_by"],
            })
        return results
    finally:
        conn.close()


def refresh_competitor_features(competitor_id, db_path=None):
    """Refresh features for a competitor by fetching its product page.

    Attempts to extract feature lists from the competitor's source URL.
    Returns {refreshed: true, features_found: N}.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            "SELECT id, name, source_url, source FROM creative_competitors WHERE id=?",
            (competitor_id,)
        ).fetchone()
        if not row:
            return {"error": f"Competitor not found: {competitor_id}"}
        source_url = row["source_url"]
        if not source_url:
            return {"error": f"No source URL for competitor: {competitor_id}"}

        html, err = _safe_get(source_url, as_text=True)
        if err or not html:
            return {"error": f"Failed to fetch {source_url}: {err}"}

        features = _extract_features_from_html(html)
        conn.execute(
            "UPDATE creative_competitors SET features=? WHERE id=?",
            (json.dumps(features), competitor_id),
        )
        conn.commit()
    finally:
        conn.close()
    _audit("creative.competitor.refresh",
           f"Refreshed features for {competitor_id}: {len(features)} found",
           {"competitor_id": competitor_id, "features_found": len(features)})
    return {"refreshed": True, "competitor_id": competitor_id,
            "features_found": len(features), "features": features}


# Feature extraction patterns for product pages
_FEATURE_PATTERNS = [
    re.compile(r'<li[^>]*class="[^"]*feature[^"]*"[^>]*>([^<]{5,120})</li>',
               re.IGNORECASE),
    re.compile(r'"feature(?:Name|Title)?"\s*:\s*"([^"]{5,120})"', re.IGNORECASE),
    re.compile(r'<h[34][^>]*>\s*([^<]{5,80})\s*</h[34]>\s*<p[^>]*>[^<]*(?:feature|capabilit|function)',
               re.IGNORECASE | re.DOTALL),
    re.compile(r'<span[^>]*class="[^"]*feature[^"]*"[^>]*>([^<]{5,120})</span>',
               re.IGNORECASE),
]


def _extract_features_from_html(html):
    """Extract feature names from a product page HTML."""
    features = []
    seen = set()
    for pat in _FEATURE_PATTERNS:
        for m in pat.finditer(html):
            feat = m.group(1).strip()
            feat = re.sub(r"<[^>]+>", "", feat).strip()
            feat = feat.replace("&amp;", "&").replace("&#39;", "'")
            if not feat or len(feat) < 5:
                continue
            key = feat.lower()
            if key not in seen:
                seen.add(key)
                features.append(feat)
    return features[:50]


# =========================================================================
# MAIN DISCOVERY ORCHESTRATOR
# =========================================================================
def run_discovery(domain=None, db_path=None):
    """Run full competitor discovery pipeline.

    Loads config, scans all configured review site category URLs,
    filters by min_review_count, stores results.
    Returns discovery summary.
    """
    config = _load_config()
    domain_config = config.get("domain", {})
    discovery_config = config.get("competitor_discovery", {})

    if not domain:
        domain = domain_config.get("name", "")
    if not domain:
        return {"error": "No domain specified. Use --domain or set domain.name in config."}

    max_per_cat = discovery_config.get("max_competitors_per_category", 20)
    min_reviews = discovery_config.get("min_review_count", 10)

    g2_url = domain_config.get("g2_category_url", "")
    capterra_url = domain_config.get("capterra_category_url", "")
    trustradius_url = domain_config.get("trustradius_category_url", "")

    all_competitors = []
    sources_scanned = 0
    errors = []

    # G2
    if g2_url:
        sources_scanned += 1
        try:
            g2_results = discover_from_g2(g2_url, max_competitors=max_per_cat)
            all_competitors.extend(g2_results)
        except Exception as exc:
            errors.append(f"g2: {exc}")

    # Capterra
    if capterra_url:
        sources_scanned += 1
        # Respect rate limit delay
        if g2_url:
            rate_delay = (config.get("sources", {}).get("review_sites", {})
                          .get("rate_limit", {}).get("delay_between_requests_seconds", 3))
            time.sleep(rate_delay)
        try:
            capterra_results = discover_from_capterra(capterra_url,
                                                      max_competitors=max_per_cat)
            all_competitors.extend(capterra_results)
        except Exception as exc:
            errors.append(f"capterra: {exc}")

    # TrustRadius
    if trustradius_url:
        sources_scanned += 1
        if g2_url or capterra_url:
            rate_delay = (config.get("sources", {}).get("review_sites", {})
                          .get("rate_limit", {}).get("delay_between_requests_seconds", 3))
            time.sleep(rate_delay)
        try:
            tr_results = discover_from_trustradius(trustradius_url,
                                                   max_competitors=max_per_cat)
            all_competitors.extend(tr_results)
        except Exception as exc:
            errors.append(f"trustradius: {exc}")

    if not all_competitors and not errors:
        return {"error": "No category URLs configured. Set g2_category_url, "
                         "capterra_category_url, or trustradius_category_url in config."}

    # Filter by min review count
    filtered = []
    below_threshold = 0
    for comp in all_competitors:
        rc = comp.get("review_count", 0) or 0
        if rc >= min_reviews:
            filtered.append(comp)
        else:
            below_threshold += 1

    # Store
    store_result = store_competitors(filtered, domain, db_path=db_path)

    _audit("creative.discovery.run",
           f"Discovery for '{domain}': {len(all_competitors)} found, "
           f"{store_result['stored']} stored, {store_result['duplicates']} duplicates",
           {"domain": domain, "sources_scanned": sources_scanned,
            "total_discovered": len(all_competitors),
            "filtered_below_threshold": below_threshold,
            "stored": store_result["stored"],
            "duplicates": store_result["duplicates"]})

    return {
        "domain": domain,
        "sources_scanned": sources_scanned,
        "competitors_discovered": len(all_competitors),
        "filtered_below_threshold": below_threshold,
        "competitors_stored": store_result["stored"],
        "duplicates": store_result["duplicates"],
        "errors": errors,
    }


# =========================================================================
# HUMAN-READABLE OUTPUT
# =========================================================================
def _format_human(data):
    """Format output for human-readable terminal display."""
    lines = []
    lines.append("=" * 70)
    lines.append("  COMPETITOR DISCOVERER — CUI // SP-CTI")
    lines.append("=" * 70)

    if isinstance(data, dict) and "error" in data:
        lines.append(f"\n  ERROR: {data['error']}\n")
        return "\n".join(lines)

    if isinstance(data, list):
        # Listing competitors
        lines.append(f"\n  Competitors: {len(data)}")
        lines.append("-" * 70)
        for c in data:
            status_icon = {"discovered": "[?]", "confirmed": "[+]",
                           "archived": "[-]"}.get(c.get("status", ""), "[ ]")
            rating_str = f"{c['rating']:.1f}/5" if c.get("rating") else "N/A"
            lines.append(f"  {status_icon} {c['name']}")
            lines.append(f"      ID: {c['id']}  |  Source: {c['source']}  |  "
                         f"Rating: {rating_str}  |  Reviews: {c.get('review_count', 0)}")
            lines.append(f"      Domain: {c.get('domain', 'N/A')}  |  "
                         f"Status: {c.get('status', 'unknown')}")
            if c.get("features"):
                lines.append(f"      Features: {len(c['features'])} extracted")
            lines.append("")
    elif isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, (list, dict)):
                lines.append(f"  {key}: {json.dumps(val, indent=2)[:200]}")
            else:
                lines.append(f"  {key}: {val}")
    lines.append("=" * 70)
    return "\n".join(lines)


# =========================================================================
# CLI
# =========================================================================
def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Competitor Auto-Discoverer — CUI // SP-CTI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  %(prog)s --discover --domain 'proposal management' --json\n"
               "  %(prog)s --list --status confirmed --json\n"
               "  %(prog)s --confirm --competitor-id comp-abc --confirmed-by user@mil --json\n"
               "  %(prog)s --archive --competitor-id comp-abc --json\n"
               "  %(prog)s --refresh --competitor-id comp-abc --json\n",
    )
    # Actions
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--discover", action="store_true",
                         help="Run competitor discovery pipeline")
    actions.add_argument("--list", action="store_true",
                         help="List stored competitors")
    actions.add_argument("--confirm", action="store_true",
                         help="Confirm a discovered competitor")
    actions.add_argument("--archive", action="store_true",
                         help="Archive a competitor")
    actions.add_argument("--refresh", action="store_true",
                         help="Refresh features for a competitor")

    # Parameters
    parser.add_argument("--domain", type=str, default=None,
                        help="Domain to discover competitors for")
    parser.add_argument("--status", type=str, default=None,
                        choices=["discovered", "confirmed", "archived"],
                        help="Filter by status (for --list)")
    parser.add_argument("--competitor-id", type=str, default=None,
                        help="Competitor ID (for --confirm, --archive, --refresh)")
    parser.add_argument("--confirmed-by", type=str, default=None,
                        help="User confirming the competitor (for --confirm)")

    # Output
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--db-path", type=str, default=None,
                        help="Override database path")

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None

    try:
        if args.discover:
            result = run_discovery(domain=args.domain, db_path=db_path)
        elif args.list:
            result = get_competitors(domain=args.domain, status=args.status,
                                     db_path=db_path)
        elif args.confirm:
            if not args.competitor_id:
                result = {"error": "--competitor-id is required for --confirm"}
            elif not args.confirmed_by:
                result = {"error": "--confirmed-by is required for --confirm"}
            else:
                result = confirm_competitor(args.competitor_id, args.confirmed_by,
                                           db_path=db_path)
        elif args.archive:
            if not args.competitor_id:
                result = {"error": "--competitor-id is required for --archive"}
            else:
                result = archive_competitor(args.competitor_id, db_path=db_path)
        elif args.refresh:
            if not args.competitor_id:
                result = {"error": "--competitor-id is required for --refresh"}
            else:
                result = refresh_competitor_features(args.competitor_id,
                                                     db_path=db_path)
        else:
            result = {"error": "No action specified"}
    except FileNotFoundError as exc:
        result = {"error": str(exc)}
    except Exception as exc:
        result = {"error": f"Unexpected error: {exc}"}

    # Output
    if args.json or not args.human:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(_format_human(result))


if __name__ == "__main__":
    main()
