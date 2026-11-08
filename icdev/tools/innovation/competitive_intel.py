#!/usr/bin/env python3
# CUI // SP-CTI
from __future__ import annotations

# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Competitive Intelligence Monitor — track competitor platforms and identify feature gaps.

Monitors competitor platforms (Backstage, Drata, Vanta, Snyk, Trivy, Checkov, Iron Bank)
to identify features ICDEV™ should consider implementing. GitHub-based competitors are
scanned for releases and enhancement issues; website-based competitors are scanned through their
public changelog, release-notes page or RSS/Atom feed. Gap analysis compares features against
ICDEV™ tools/manifest.md keywords.

Architecture:
    - Competitors defined in args/innovation_config.yaml under competitive_intel.competitors
    - GitHub repos: fetch releases + enhancement/feature issues via REST API
    - Website competitors: fetch feed_url, then changelog_url, then the website (discovering an
      RSS/Atom link on it); robots.txt is honoured (fail closed), pages are capped at 2 MB, and
      feature-like headings and list items are kept (stdlib HTMLParser, feeds via defusedxml)
    - GitHub auth: GITHUB_TOKEN / GH_TOKEN, else `gh auth token` (unauthenticated calls hit 403)
    - Gap analysis: keyword matching against manifest descriptions (append-only D6)
    - Results stored in innovation_competitor_scans; gaps as innovation_signals

Usage:
    python tools/innovation/competitive_intel.py --scan --competitor backstage --json
    python tools/innovation/competitive_intel.py --scan --all --json
    python tools/innovation/competitive_intel.py --gap-analysis --json
    python tools/innovation/competitive_intel.py --report --json
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.robotparser
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

# -- PATH SETUP ---------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "innovation_config.yaml"
MANIFEST_PATH = BASE_DIR / "tools" / "manifest.md"

# -- GRACEFUL IMPORTS ---------------------------------------------------------
try:
    import yaml

    _HAS_YAML = True  # noqa: E702
except ImportError:
    _HAS_YAML = False
try:
    import requests as _requests

    from tools.http.client import request as _http_request

    _HAS_REQUESTS = True  # noqa: E702
except ImportError:
    _HAS_REQUESTS = False
    _http_request = None  # type: ignore[assignment]
try:
    from defusedxml import DefusedXmlException as _DefusedXmlException
    from defusedxml import ElementTree as _SafeET

    _HAS_DEFUSEDXML = True
except ImportError:
    _HAS_DEFUSEDXML = False
try:
    from tools.audit.audit_logger import log_event as _audit_log

    _HAS_AUDIT = True  # noqa: E702
except ImportError:
    _HAS_AUDIT = False

    def _audit_log(**kw):
        return -1  # noqa: E302,E731


# -- CONSTANTS ----------------------------------------------------------------
GITHUB_API = "https://api.github.com"
_FEAT_RE = re.compile(r"(?:feat|add|new|support|introduc|implement|enabl)", re.I)
_WEB_UA = "ICDEV-competitive-intel/1.0 (+https://github.com/icdev-ai/icdev)"
_WEB_MAX_BYTES = 2 * 1024 * 1024
_WEB_MAX_FEATURES = 25
# Matches bare version strings like "3.2.508", "v3.2.508", "3.2.508-rc1" — no feature description
_VERSION_TAG_RE = re.compile(r"^v?\d+(\.\d+)+([-._]\w+)?$")
_STOPS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "all",
    "are",
    "was",
    "has",
    "not",
    "but",
    "can",
    "its",
    "you",
    "use",
    "tool",
    "file",
    "input",
    "output",
    "description",
    "json",
    "project",
}

# Competitor category -> representative features for augmented gap analysis
_CAT_FEATURES = {
    "platform_engineering": [
        "developer portal with service catalog",
        "software template scaffolding",
        "TechDocs documentation aggregation",
        "plugin marketplace extensibility",
        "Kubernetes resource monitoring",
        "CI/CD pipeline visualization",
    ],
    "compliance_automation": [
        "continuous compliance monitoring dashboard",
        "automated evidence collection SOC 2",
        "vendor risk management portal",
        "employee security awareness tracking",
        "policy management version history",
        "real-time compliance posture scoring",
    ],
    "devsecops": [
        "open source dependency scanning",
        "license compliance checking",
        "container image vulnerability scanning",
        "infrastructure code security scanning",
        "SAST with auto-fix suggestions",
        "software composition analysis",
    ],
    "security_scanning": [
        "container vulnerability scanning",
        "filesystem vulnerability scanning",
        "kubernetes cluster scanning",
        "SBOM generation from images",
        "misconfiguration detection",
        "secret scanning in repositories",
    ],
    "iac_security": [
        "terraform plan scanning",
        "cloudformation template validation",
        "kubernetes manifest security checks",
        "dockerfile best practice enforcement",
        "ARM template scanning",
        "policy-as-code custom rules",
    ],
    "govtech": [
        "hardened container images",
        "STIG-compliant base images",
        "DoD approved container registry",
        "continuous image scanning pipeline",
        "FIPS 140-2 validated images",
        "supply chain provenance for images",
    ],
    "digital_twin_platform": [
        "mathematical network model with header-space analysis",
        "multi-vendor configuration normalization",
        "behavioral snapshot diff with impact correlation",
        "intent verification with policy-as-code rules",
        "path reachability analysis with ACL simulation",
        "blast radius isolation with L4 protocol detail",
        "pre-deployment what-if simulation sandbox",
        "zero-trust segmentation enforcement verification",
        "natural language network query interface",
        "continuous drift detection with alert integration",
        "OSCAL compliance export for ATO evidence",
        "network query engine (NQE) with custom checks",
    ],
    "data_integration": [
        "prebuilt source connectors with incremental sync",
        "change data capture replication",
        "schema drift detection and propagation",
        "connector development kit",
        "reverse ETL to operational systems",
        "pipeline monitoring and alerting",
    ],
    "data_mapping": [
        "visual schema-to-schema field mapping",
        "AI-suggested field mappings",
        "XML JSON EDI transformation",
        "code-list and value crosswalks",
        "mapping test with sample data",
    ],
    "data_quality": [
        "declarative data quality expectations",
        "data contracts between producers and consumers",
        "anomaly detection on volume and distribution",
        "data quality checks in CI",
    ],
    "data_orchestration": [
        "asset-centric pipeline definitions",
        "backfills and partitioned runs",
        "retries with dead-letter handling",
        "lineage from orchestrated assets",
    ],
    "data_lineage": [
        "column-level lineage",
        "metadata catalog with search",
        "OpenLineage event ingestion",
        "data ownership and stewardship",
    ],
    "feature_store": [
        "point-in-time correct training sets",
        "online and offline feature serving",
        "feature definitions as code",
    ],
}


# -- HELPERS ------------------------------------------------------------------
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit(event_type, action, details=None):
    """Write audit trail entry."""
    if _HAS_AUDIT:
        try:
            _audit_log(
                event_type=event_type,
                actor="innovation-agent",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="innovation-engine",
            )
        except Exception:
            pass


def _ensure_table(conn):
    """Create innovation_competitor_scans table if missing."""
    conn.execute("""CREATE TABLE IF NOT EXISTS innovation_competitor_scans (
        id TEXT PRIMARY KEY, competitor_name TEXT NOT NULL, scan_date TEXT NOT NULL,
        releases_found INTEGER DEFAULT 0, features_found INTEGER DEFAULT 0,
        gaps_identified INTEGER DEFAULT 0, metadata TEXT DEFAULT '{}',
        created_at TEXT NOT NULL)""")
    conn.commit()


def _load_config():
    """Load innovation config from YAML."""
    if not _HAS_YAML or not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_competitors():
    """Load competitor list from config."""
    return _load_config().get("competitive_intel", {}).get("competitors", [])


_GH_TOKEN_CACHE: list = []


def _gh_token():
    """GitHub token from GITHUB_TOKEN / GH_TOKEN, else the gh CLI's login; None when neither."""
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok
    if not _GH_TOKEN_CACHE:
        found = None
        gh = shutil.which("gh")
        if gh:
            try:
                out = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=10, check=False)
                found = (out.stdout.strip() or None) if out.returncode == 0 else None
            except (OSError, subprocess.SubprocessError):
                found = None
        _GH_TOKEN_CACHE.append(found)
    return _GH_TOKEN_CACHE[0]


def _gh_headers():
    """Build GitHub API headers with optional auth token."""
    h = {"Accept": "application/vnd.github+json"}
    tok = _gh_token()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


def _safe_get(url, headers=None, params=None):
    """HTTP GET with error handling."""
    if not _HAS_REQUESTS:
        return None, "requests library not installed"
    try:
        resp = _http_request("GET", url, headers=headers, params=params, timeout=30)
        if resp.status_code in (403, 429):
            return None, "rate_limited" if resp.status_code == 429 else "forbidden"
        if resp.status_code == 404:
            return None, "not_found"
        resp.raise_for_status()
        return resp.json(), None
    except (_requests.exceptions.RequestException, json.JSONDecodeError) as e:
        return None, str(e)


def _hash(content):
    """SHA-256 hash for deduplication."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# -- GITHUB FETCHERS ----------------------------------------------------------
def _fetch_releases(repo, n=10):
    """Fetch latest releases from a GitHub repo."""
    data, err = _safe_get(f"{GITHUB_API}/repos/{repo}/releases", headers=_gh_headers(), params={"per_page": min(n, 30)})
    return ((data or [])[:n], None) if not err else ([], err)


def _fetch_enhancement_issues(repo, n=20):
    """Fetch recent enhancement/feature issues (deduped, PRs excluded)."""
    headers, seen, out = _gh_headers(), set(), []
    for label in ("enhancement", "feature", "feature-request"):
        data, err = _safe_get(
            f"{GITHUB_API}/repos/{repo}/issues",
            headers=headers,
            params={"labels": label, "state": "all", "sort": "updated", "direction": "desc", "per_page": min(n, 30)},
        )
        if err:
            continue
        for iss in data or []:
            num = iss.get("number")
            if not iss.get("pull_request") and num not in seen:
                seen.add(num)
                out.append(iss)
    return out[:n], None


def _extract_features(releases):
    """Extract feature descriptions from release notes."""
    feats = []
    for rel in releases:
        tag, body = rel.get("tag_name", ""), rel.get("body") or ""
        for line in body.split("\n"):
            line = line.strip()
            if line.startswith(("- ", "* ", "• ")):
                txt = line.lstrip("-*• ").strip()
                if _FEAT_RE.search(txt) and len(txt) > 10:
                    feats.append(f"[{tag}] {txt}")
            elif _FEAT_RE.search(line) and line.startswith("#"):
                txt = line.lstrip("# ").strip()
                if len(txt) > 5:
                    feats.append(f"[{tag}] {txt}")
    return feats


# -- WEBSITE FETCHERS (changelog, release notes, RSS/Atom) --------------------
_ROBOTS_CACHE: dict = {}


def _robots_allows(url):
    """True when robots.txt permits our user agent. No robots.txt (404) allows; any other failure refuses."""
    parts = urllib.parse.urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _ROBOTS_CACHE:
        rp = urllib.robotparser.RobotFileParser()
        try:
            resp = _http_request("GET", f"{origin}/robots.txt", headers={"User-Agent": _WEB_UA}, timeout=15)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            elif resp.status_code in (404, 410):
                rp.parse([])
            else:
                rp = None
        except _requests.exceptions.RequestException:
            rp = None
        _ROBOTS_CACHE[origin] = rp
    rp = _ROBOTS_CACHE[origin]
    return rp is not None and rp.can_fetch(_WEB_UA, url)


def _fetch_page(url):
    """GET a public page as text, honouring robots.txt and the size cap. Returns (text, content_type, error)."""
    if not _HAS_REQUESTS:
        return None, "", "requests library not installed"
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        return None, "", "unsupported_scheme"
    if not _robots_allows(url):
        return None, "", "robots_disallowed"
    try:
        resp = _http_request("GET", url, headers={"User-Agent": _WEB_UA}, timeout=30)
    except _requests.exceptions.RequestException as e:
        return None, "", str(e)
    if resp.status_code in (403, 429):
        return None, "", "rate_limited" if resp.status_code == 429 else "forbidden"
    if resp.status_code == 404:
        return None, "", "not_found"
    if resp.status_code >= 400:
        return None, "", f"http_{resp.status_code}"
    if len(resp.content) > _WEB_MAX_BYTES:
        return None, "", "too_large"
    return resp.text, resp.headers.get("content-type", ""), None


class _BlockExtractor(HTMLParser):
    """Text of headings, list items, paragraphs and definition terms, outside script/style/nav/footer."""

    _KEEP = frozenset({"h1", "h2", "h3", "h4", "li", "p", "dt"})
    _SKIP = frozenset({"script", "style", "nav", "footer", "noscript", "svg", "form"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.feeds = [], []
        self._skip, self._stack = 0, []

    def handle_starttag(self, tag, attrs):
        if tag == "link":
            a = dict(attrs)
            is_feed = (a.get("type") or "").lower() in ("application/rss+xml", "application/atom+xml")
            if (a.get("rel") or "").lower() == "alternate" and is_feed and a.get("href"):
                self.feeds.append(a["href"])
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._KEEP:
            self._stack.append([tag, []])

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in self._KEEP and self._stack and self._stack[-1][0] == tag:
            t, parts = self._stack.pop()
            text = " ".join(" ".join(parts).split())
            if text:
                self.blocks.append((t, text))

    def handle_data(self, data):
        if not self._skip and self._stack:
            self._stack[-1][1].append(data)


def _parse_feed(xml_text):
    """Entry titles from RSS or Atom (defusedxml; DTDs and entities refused)."""
    if not _HAS_DEFUSEDXML:
        return [], "defusedxml not installed"
    try:
        root = _SafeET.fromstring(xml_text)
    except (_SafeET.ParseError, _DefusedXmlException) as e:
        return [], f"feed_parse: {e.__class__.__name__}"
    titles = []
    for el in root.iter():
        name = el.tag.rsplit("}", 1)[-1]
        if name in ("item", "entry"):
            for child in el:
                if child.tag.rsplit("}", 1)[-1] == "title" and (child.text or "").strip():
                    titles.append(" ".join(child.text.split()))
                    break
    return titles, None


def _web_features(blocks, limit=_WEB_MAX_FEATURES):
    """Feature-like lines from page blocks, each prefixed with the nearest heading."""
    feats, seen, heading = [], set(), ""
    for tag, text in blocks:
        if tag in ("h1", "h2", "h3", "h4"):
            heading = text[:60]
            if not _FEAT_RE.search(text):
                continue
        if not (20 <= len(text) <= 300) or not _FEAT_RE.search(text):
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        feats.append(f"[{heading}] {text}" if heading and heading != text else text)
        if len(feats) >= limit:
            break
    return feats


def _looks_like_feed(text, content_type):
    ct = content_type.lower()
    if "rss" in ct or "atom" in ct or ct.startswith(("application/xml", "text/xml")):
        return True
    head = text.lstrip()[:200].lower()
    return head.startswith(("<?xml", "<rss")) or "<feed" in head


def _scan_website(comp):
    """Scan feed_url, then changelog_url, then the website. Returns (features, source_url, errors)."""
    errs = []
    for key in ("feed_url", "changelog_url", "website"):
        url = comp.get(key)
        if not url:
            continue
        text, ctype, err = _fetch_page(url)
        if err:
            errs.append(f"{key}: {err}")
            continue
        if _looks_like_feed(text, ctype):
            titles, err = _parse_feed(text)
            if err:
                errs.append(f"{key}: {err}")
                continue
            feats = _web_features([("li", t) for t in titles])
            if feats:
                return feats, url, errs
            errs.append(f"{key}: no_feature_lines")
            continue
        parser = _BlockExtractor()
        parser.feed(text)
        feats = _web_features(parser.blocks)
        if not feats and parser.feeds and key == "website":
            feed = urllib.parse.urljoin(url, parser.feeds[0])
            ftext, fct, ferr = _fetch_page(feed)
            if not ferr and _looks_like_feed(ftext, fct):
                titles, ferr = _parse_feed(ftext)
                feats = _web_features([("li", t) for t in titles]) if not ferr else []
                if feats:
                    return feats, feed, errs
            if ferr:
                errs.append(f"discovered_feed: {ferr}")
        if feats:
            return feats, url, errs
        errs.append(f"{key}: no_feature_lines")
    return [], None, errs


# -- MANIFEST ANALYSIS --------------------------------------------------------
def _load_manifest_keywords():
    """Extract keyword set from ICDEV™ manifest tool descriptions."""
    if not MANIFEST_PATH.exists():
        return set()
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        content = f.read().lower()
    return {t for t in re.findall(r"[a-z][a-z_-]{2,}", content) if t not in _STOPS and len(t) > 3}


_NAMED_VERSION_RE = re.compile(r"^[A-Za-z][\w .+-]*?\s+v?\d+(\.\d+)+[\w.+-]*$")
_LEADING_VERSION_RE = re.compile(r"^(?:[A-Za-z][\w .+-]*?\s+)?v?\d+(\.\d+)+[\w.+-]*\s*[-:\u2013\u2014|]\s*")


def _is_version_release_noise(name: str, tag: str) -> bool:
    """Return True when a release name adds no feature signal beyond a bare version number.

    Checkov (and many tools) publish releases whose name == tag == "3.2.508".
    These contain zero alphabetic content, so _has_capability() scores them
    0.0 and they fall through to gap_identified — producing phantom gap signals
    like "Gap: checkov has '[3.2.508] 3.2.508'".
    """
    name_clean = name.strip()
    tag_clean = tag.strip()
    if not name_clean or name_clean.lower() == "release":
        return True
    # Pure version string as name (e.g. "3.2.508", "v3.2.508")
    if _VERSION_TAG_RE.match(name_clean):
        return True
    # Name is just the tag, possibly with a leading "v" stripped
    if name_clean == tag_clean or name_clean == tag_clean.lstrip("vV"):
        return True
    # A nightly / development build ("3.9.1.dev7: Nightly Development Release")
    if re.search(r"\bnightly\b", name_clean, re.IGNORECASE) or re.search(r"\bdev(elopment)? (build|release)\b", name_clean, re.IGNORECASE):
        return True
    # A product name and a version and nothing else ("Airbyte 2.0", "Feast v0.49.0")
    if _NAMED_VERSION_RE.match(name_clean):
        return True
    # A version (after an optional product name), then a codename or quip with no feature word
    # ("3.9.2 - It's okay to let go of a lock", "Hermes Agent v0.22.0 — The Threshold Release")
    m = _LEADING_VERSION_RE.match(name_clean)
    if m and not _FEAT_RE.search(name_clean[m.end():]):
        return True
    return False


def _has_capability(feature_text, kw_set):
    """Check if manifest keywords cover a feature. Returns (bool, score)."""
    tokens = set(re.findall(r"[a-z][a-z_-]{2,}", feature_text.lower()))
    if not tokens:
        return False, 0.0
    score = len(tokens & kw_set) / len(tokens)
    return score >= 0.30, score


# -- CORE FUNCTIONS -----------------------------------------------------------
def scan_competitor(competitor_name, db_path=None):
    """Scan a specific competitor for releases, features, and enhancement issues."""
    comp = next((c for c in _get_competitors() if c.get("name") == competitor_name), None)
    if not comp:
        return {"error": f"Competitor '{competitor_name}' not found in config"}
    conn = _get_db(db_path)
    _ensure_table(conn)
    sid, ts = f"cscan-{uuid.uuid4().hex[:12]}", _now()
    rels, feats, issues, errs = [], [], [], []
    repo, website = comp.get("repo"), comp.get("website")

    if repo:
        raw_rels, err = _fetch_releases(repo)
        if err:
            errs.append(f"releases: {err}")
        else:
            rels = [
                {
                    "tag": r.get("tag_name", ""),
                    "name": r.get("name", ""),
                    "published_at": r.get("published_at", ""),
                    "url": r.get("html_url", ""),
                }
                for r in raw_rels
            ]
            feats = _extract_features(raw_rels)
        raw_iss, err = _fetch_enhancement_issues(repo)
        if err:
            errs.append(f"issues: {err}")
        else:
            for iss in raw_iss:
                rx = iss.get("reactions") or {}
                issues.append(
                    {
                        "number": iss.get("number"),
                        "title": iss.get("title", ""),
                        "state": iss.get("state", ""),
                        "labels": [lbl.get("name", "") for lbl in iss.get("labels", [])],
                        "url": iss.get("html_url", ""),
                        "created_at": iss.get("created_at", ""),
                        "reactions_thumbs_up": rx.get("+1", 0),
                    }
                )
    web_source = None
    if not feats and (comp.get("feed_url") or comp.get("changelog_url") or (website and not repo)):
        web_feats, web_source, web_errs = _scan_website(comp)
        feats.extend(web_feats)
        errs.extend(web_errs)

    meta = {
        "category": comp.get("category"),
        "repo": repo,
        "website": website,
        "changelog_url": comp.get("changelog_url"),
        "feed_url": comp.get("feed_url"),
        "web_source": web_source,
        "releases": rels,
        "issues_count": len(issues),
        "errors": errs,
    }
    try:
        conn.execute(
            "INSERT INTO innovation_competitor_scans "
            "(id,competitor_name,scan_date,releases_found,features_found,"
            "gaps_identified,metadata,created_at) VALUES (%s,%s,%s,%s,%s,0,%s,%s)",
            (sid, competitor_name, ts, len(rels), len(feats), json.dumps(meta), ts),
        )
        conn.commit()
    finally:
        conn.close()
    _audit(
        "competitive_intel.scan",
        f"Scanned '{competitor_name}': {len(rels)} releases, {len(feats)} features",
        {"competitor": competitor_name, "scan_id": sid},
    )
    return {
        "scan_id": sid,
        "competitor": competitor_name,
        "category": comp.get("category", "unknown"),
        "scan_date": ts,
        "releases_found": len(rels),
        "features_found": len(feats),
        "issues_found": len(issues),
        "features": feats,
        "errors": errs,
    }


def scan_all_competitors(db_path=None):
    """Scan all configured competitors."""
    comps = _get_competitors()
    if not comps:
        return {"error": "No competitors configured in innovation_config.yaml"}
    results, tr, tf, ti = {}, 0, 0, 0
    for c in comps:
        nm = c.get("name", "unknown")
        r = scan_competitor(nm, db_path=db_path)
        results[nm] = r
        if "error" not in r:
            tr += r.get("releases_found", 0)
            tf += r.get("features_found", 0)
            ti += r.get("issues_found", 0)
    _audit("competitive_intel.scan_all", f"Scanned {len(comps)} competitors: {tr} releases, {tf} features")
    return {
        "scan_date": _now(),
        "competitors_scanned": len(comps),
        "results": results,
        "totals": {"releases_found": tr, "features_found": tf, "issues_found": ti},
    }


def gap_analysis(db_path=None):
    """Compare competitor features against ICDEV™ capabilities via manifest keywords."""
    mkw = _load_manifest_keywords()
    if not mkw:
        return {"error": "Could not load ICDEV™ manifest keywords"}
    conn = _get_db(db_path)
    _ensure_table(conn)
    all_gaps, stats = [], {}

    for comp in _get_competitors():
        nm, cat = comp.get("name", "unknown"), comp.get("category", "unknown")
        row = conn.execute(
            "SELECT id, metadata FROM innovation_competitor_scans "
            "WHERE competitor_name=%s ORDER BY scan_date DESC LIMIT 1",
            (nm,),
        ).fetchone()
        if not row:
            stats[nm] = {"status": "no_scan_data", "message": f"No scan data for '{nm}'. Run --scan first."}
            continue
        meta = json.loads(row["metadata"] or "{}")
        # Filter out releases whose name is a bare version string (e.g. "3.2.508").
        # These produce zero alphabetic tokens → score 0.0 → false gap signals.
        feats = [
            f"[{r.get('tag', '')}] {r.get('name', 'release')}"
            for r in meta.get("releases", [])
            if not _is_version_release_noise(r.get("name", ""), r.get("tag", ""))
        ]
        feats += _CAT_FEATURES.get(cat, [])
        covered, comp_gaps = 0, []
        for feat in feats:
            # Secondary guard: skip any feature with no alphabetic content (pure noise)
            if not re.search(r"[a-z]{3,}", feat, re.I):
                continue
            ok, sc = _has_capability(feat, mkw)
            if ok:
                covered += 1
            else:
                g = {"competitor": nm, "category": cat, "feature": feat, "overlap_score": round(sc, 3)}
                comp_gaps.append(g)
                all_gaps.append(g)
        total = max(len(feats), 1)
        stats[nm] = {
            "category": cat,
            "features_analyzed": len(feats),
            "icdev_covered": covered,
            "gaps_found": len(comp_gaps),
            "coverage_pct": round(covered / total * 100, 1),
        }
        conn.execute("UPDATE innovation_competitor_scans SET gaps_identified=%s WHERE id=%s", (len(comp_gaps), row["id"]))

    stored = 0
    for gap in all_gaps:
        ch = _hash(f"{gap['competitor']}:{gap['feature']}")
        if conn.execute("SELECT 1 FROM innovation_signals WHERE content_hash=%s", (ch,)).fetchone():
            continue
        try:
            conn.execute(
                "INSERT INTO innovation_signals "
                "(id,source,source_type,title,description,url,metadata,"
                "community_score,content_hash,discovered_at,status,category) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'new',%s)",
                (
                    f"sig-{uuid.uuid4().hex[:12]}",
                    "competitive_intel",
                    "gap",
                    f"Gap: {gap['competitor']} has '{gap['feature'][:80]}'",
                    f"Competitor '{gap['competitor']}' ({gap['category']}) offers "
                    f"a capability ICDEV™ may lack: {gap['feature']}",
                    "",
                    json.dumps(
                        {
                            "competitor": gap["competitor"],
                            "category": gap["category"],
                            "overlap_score": gap["overlap_score"],
                        }
                    ),
                    0.5,
                    ch,
                    _now(),
                    gap["category"],
                ),
            )
            stored += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()
    _audit(
        "competitive_intel.gap_analysis",
        f"Gap analysis: {len(all_gaps)} gaps, {stored} signals stored",
        {"gaps": len(all_gaps), "signals_stored": stored},
    )
    return {
        "analysis_date": _now(),
        "competitors_analyzed": len(stats),
        "total_gaps": len(all_gaps),
        "signals_stored": stored,
        "manifest_keywords_count": len(mkw),
        "coverage_by_competitor": stats,
        "gaps": all_gaps[:50],
    }


def get_competitor_report(db_path=None):
    """Generate competitive intelligence report from stored scan data."""
    conn = _get_db(db_path)
    _ensure_table(conn)
    comps_cfg = _get_competitors()
    competitors, ts, tg, cats = [], 0, 0, set()
    for comp in comps_cfg:
        nm, cat = comp.get("name", "unknown"), comp.get("category", "unknown")
        cats.add(cat)
        scans = conn.execute(
            "SELECT id,scan_date,releases_found,features_found,gaps_identified "
            "FROM innovation_competitor_scans WHERE competitor_name=%s "
            "ORDER BY scan_date DESC LIMIT 5",
            (nm,),
        ).fetchall()
        hist = [
            {
                "scan_id": s["id"],
                "scan_date": s["scan_date"],
                "releases_found": s["releases_found"],
                "features_found": s["features_found"],
                "gaps_identified": s["gaps_identified"],
            }
            for s in scans
        ]
        latest = hist[0] if hist else None
        ts += len(hist)
        if latest:
            tg += latest.get("gaps_identified", 0)
        gap_sigs = conn.execute(
            "SELECT title,description,community_score,discovered_at "
            "FROM innovation_signals WHERE source='competitive_intel' "
            "AND metadata LIKE %s ORDER BY discovered_at DESC LIMIT 10",
            (f'%"competitor": "{nm}"%',),
        ).fetchall()
        gaps = [
            {
                "title": g["title"],
                "description": g["description"][:200],
                "score": g["community_score"],
                "discovered_at": g["discovered_at"],
            }
            for g in gap_sigs
        ]
        competitors.append(
            {
                "name": nm,
                "category": cat,
                "repo": comp.get("repo"),
                "website": comp.get("website"),
                "scan_count": len(hist),
                "latest_scan": latest,
                "recent_gaps": gaps,
            }
        )
    conn.close()
    # Recommendations
    recs = []
    hi = [c for c in competitors if (c.get("latest_scan") or {}).get("gaps_identified", 0) > 3]
    if hi:
        recs.append(f"High gap count vs: {', '.join(c['name'] for c in hi)}. Prioritize feature parity assessment.")
    un = [c for c in competitors if c["scan_count"] == 0]
    if un:
        recs.append(f"No scan data for: {', '.join(c['name'] for c in un)}. Run '--scan --all' to populate baseline.")
    gc = {c["category"] for c in competitors if c.get("recent_gaps")}
    if "compliance_automation" in gc:
        recs.append("Compliance automation gaps detected — consider continuous monitoring.")
    if "platform_engineering" in gc:
        recs.append("Platform engineering gaps — consider developer portal features.")
    if "digital_twin_platform" in gc:
        recs.append("Digital twin platform gaps — prioritize path reachability analysis and policy-as-code intent rules.")
    if not recs:
        recs.append("No significant gaps detected. Continue regular scanning.")
    _audit("competitive_intel.report", f"Report: {len(comps_cfg)} competitors, {tg} gaps")
    return {
        "report_date": _now(),
        "competitors": competitors,
        "summary": {
            "total_competitors": len(comps_cfg),
            "total_scans": ts,
            "total_gaps_latest": tg,
            "categories_covered": sorted(cats),
            "recommendations": recs,
        },
    }


# -- CLI ----------------------------------------------------------------------
def _print_human(args, result):
    """Print human-readable CLI output."""
    if "error" in result:
        print(f"ERROR: {result['error']}")
        return
    if args.scan:
        if args.all or not args.competitor:
            t = result.get("totals", {})
            print(f"Scan completed at {result.get('scan_date', '')}")
            print(
                f"Competitors: {result.get('competitors_scanned', 0)}  "
                f"Releases: {t.get('releases_found', 0)}  "
                f"Features: {t.get('features_found', 0)}  Issues: {t.get('issues_found', 0)}"
            )
            for nm, r in result.get("results", {}).items():
                s = (
                    f"{r.get('releases_found', 0)} rel, {r.get('features_found', 0)} feat"
                    if "error" not in r
                    else f"ERROR: {r['error']}"
                )
                print(f"  {nm}: {s}")
        else:
            print(f"Scan: {result.get('competitor', '')} ({result.get('category', '')})")
            print(
                f"  Releases: {result.get('releases_found', 0)}  "
                f"Features: {result.get('features_found', 0)}  "
                f"Issues: {result.get('issues_found', 0)}"
            )
            for ft in result.get("features", [])[:5]:
                print(f"    - {ft[:100]}")
    elif args.gap_analysis:
        print(f"Gap Analysis — {result.get('analysis_date', '')}")
        print(f"Gaps: {result.get('total_gaps', 0)}  Stored: {result.get('signals_stored', 0)}")
        for nm, s in result.get("coverage_by_competitor", {}).items():
            if isinstance(s, dict) and "coverage_pct" in s:
                print(f"  {nm}: {s['coverage_pct']}% coverage, {s['gaps_found']} gaps")
            else:
                print(f"  {nm}: {s.get('status', 'unknown') if isinstance(s, dict) else 'unknown'}")
        for g in result.get("gaps", [])[:10]:
            print(f"  - [{g['competitor']}] {g['feature'][:80]}")
    elif args.report:
        sm = result.get("summary", {})
        print(f"Report — {result.get('report_date', '')}")
        print(
            f"Competitors: {sm.get('total_competitors', 0)}  "
            f"Scans: {sm.get('total_scans', 0)}  Gaps: {sm.get('total_gaps_latest', 0)}"
        )
        for c in result.get("competitors", []):
            lt = c.get("latest_scan")
            print(
                f"  {c['name']} ({c['category']}): "
                f"last {lt['scan_date'][:10] if lt else 'never'}, "
                f"{lt.get('gaps_identified', 0) if lt else 0} gaps"
            )
        for r in sm.get("recommendations", []):
            print(f"  * {r}")


def main():
    p = argparse.ArgumentParser(description="ICDEV™ Competitive Intelligence Monitor")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--db-path", type=Path, default=None, help="Database path override")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--scan", action="store_true", help="Scan competitor(s)")
    g.add_argument("--gap-analysis", action="store_true", help="Compare competitor features against ICDEV™")
    g.add_argument("--report", action="store_true", help="Generate intel report")
    p.add_argument("--competitor", type=str, help="Specific competitor (with --scan)")
    p.add_argument("--all", action="store_true", help="Scan all competitors (with --scan)")
    args = p.parse_args()
    try:
        if args.scan:
            result = (
                scan_all_competitors(db_path=args.db_path)
                if (args.all or not args.competitor)
                else scan_competitor(args.competitor, db_path=args.db_path)
            )
        elif args.gap_analysis:
            result = gap_analysis(db_path=args.db_path)
        elif args.report:
            result = get_competitor_report(db_path=args.db_path)
        else:
            result = {"error": "No action specified"}
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_human(args, result)
    except Exception as e:
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
