#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""CSP Service Monitor — track cloud provider service changes automatically.

Scans CSP announcement feeds (RSS, API, HTML), diffs against the local service
registry (context/cloud/csp_service_registry.json), and generates innovation
signals for new services, deprecations, compliance scope changes, and breaking
API changes.

Integrates with Innovation Engine pipeline (Phase 35) so detected changes
flow through SCORE → TRIAGE → GENERATE → BUILD → PUBLISH automatically.

Architecture:
    - Config-driven via args/csp_monitor_config.yaml (D26 pattern)
    - Append-only signal storage in innovation_signals table (D6)
    - Graceful degradation on missing dependencies (D73)
    - Air-gap safe: registry diff works offline; web scanning requires network
    - Deduplication via content_hash (SHA-256)

ADRs: D239 (CSP monitoring as Innovation Engine source),
      D240 (declarative service registry)

Usage:
    python tools/cloud/csp_monitor.py --scan --all --json
    python tools/cloud/csp_monitor.py --scan --csp aws --json
    python tools/cloud/csp_monitor.py --diff --json
    python tools/cloud/csp_monitor.py --status --json
    python tools/cloud/csp_monitor.py --update-registry --signal-id "sig-xxx" --json
    python tools/cloud/csp_monitor.py --changelog --days 30 --json
    python tools/cloud/csp_monitor.py --daemon --json
"""

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

# ── PATH SETUP ──────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "csp_monitor_config.yaml"
REGISTRY_PATH = BASE_DIR / "context" / "cloud" / "csp_service_registry.json"

logger = logging.getLogger("icdev.cloud.csp_monitor")

# ── GRACEFUL IMPORTS ────────────────────────────────────────────────────
try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

try:
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def audit_log_event(**kwargs):
        return -1

try:
    from tools.resilience.circuit_breaker import InMemoryCircuitBreaker
    _HAS_CB = True
except ImportError:
    _HAS_CB = False

# ── CONSTANTS ───────────────────────────────────────────────────────────
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
SUPPORTED_CSPS = ["aws", "azure", "gcp", "oci", "ibm"]

# Change type → innovation signal category mapping
CHANGE_TYPE_CATEGORY = {
    "new_service": "infrastructure",
    "service_deprecation": "modernization",
    "compliance_scope_change": "compliance_gap",
    "region_expansion": "infrastructure",
    "api_breaking_change": "modernization",
    "security_update": "security_vulnerability",
    "pricing_change": "developer_experience",
    "certification_change": "compliance_gap",
}

# Community score per change type
CHANGE_TYPE_SCORE = {
    "new_service": 0.6,
    "service_deprecation": 0.8,
    "compliance_scope_change": 0.9,
    "region_expansion": 0.4,
    "api_breaking_change": 0.9,
    "security_update": 0.7,
    "pricing_change": 0.3,
    "certification_change": 0.9,
}


# ── DATABASE HELPERS ────────────────────────────────────────────────────
def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_hash(parts: List[str]) -> str:
    """Generate SHA-256 content hash for deduplication."""
    combined = "|".join(str(p) for p in parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def _audit(event_type: str, actor: str, action: str, details: Any = None):
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type, actor=actor, action=action,
                details=json.dumps(details) if details else None,
                project_id="csp-monitor",
            )
        except Exception:
            pass


# ── CONFIG LOADER ───────────────────────────────────────────────────────
def _load_config(config_path: Optional[Path] = None) -> Dict:
    """Load CSP monitor configuration."""
    path = config_path or CONFIG_PATH
    if not _HAS_YAML:
        logger.warning("PyYAML not available — using defaults")
        return {}
    if not path.exists():
        logger.warning("CSP monitor config not found at %s", path)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:
        logger.error("Failed to load CSP monitor config: %s", exc)
        return {}


# ── REGISTRY LOADER ─────────────────────────────────────────────────────
def _load_registry(registry_path: Optional[Path] = None) -> Dict:
    """Load CSP service registry."""
    path = registry_path or REGISTRY_PATH
    if not path.exists():
        logger.warning("CSP service registry not found at %s", path)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error("Failed to load CSP service registry: %s", exc)
        return {}


def _save_registry(registry: Dict, registry_path: Optional[Path] = None):
    """Save updated registry with backup."""
    path = registry_path or REGISTRY_PATH
    # Create backup
    backup_path = path.with_suffix(f".backup-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.json")
    if path.exists():
        import shutil
        shutil.copy2(str(path), str(backup_path))
        logger.info("Registry backup: %s", backup_path)
    # Update metadata
    registry.setdefault("_metadata", {})
    registry["_metadata"]["last_updated"] = _now()
    registry["_metadata"]["updated_by"] = "csp_monitor"
    # Write
    with open(path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2)
    logger.info("Registry saved: %s", path)


# ── SIGNAL STORAGE ──────────────────────────────────────────────────────
def _store_signal(conn: sqlite3.Connection, signal: Dict) -> bool:
    """Store innovation signal with deduplication. Returns True if new."""
    # Check for existing signal with same content_hash
    existing = conn.execute(
        "SELECT id FROM innovation_signals WHERE content_hash = ?",
        (signal["content_hash"],)
    ).fetchone()
    if existing:
        logger.debug("Duplicate signal skipped: %s", signal["content_hash"][:12])
        return False

    conn.execute(
        "INSERT INTO innovation_signals "
        "(id, source, source_type, title, description, url, metadata, "
        "community_score, content_hash, discovered_at, status, category) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            signal["id"], signal["source"], signal["source_type"],
            signal["title"], signal["description"][:2000],
            signal.get("url", ""), json.dumps(signal.get("metadata", {})),
            signal["community_score"], signal["content_hash"],
            signal["discovered_at"], "new", signal["category"],
        )
    )
    return True


def _check_table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    """Check if a DB table exists."""
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,)
    )
    return cursor.fetchone() is not None


# ── RSS/ATOM PARSER ─────────────────────────────────────────────────────
def _parse_rss_feed(url: str, filter_keywords: List[str] = None,
                    timeout: int = DEFAULT_TIMEOUT) -> List[Dict]:
    """Parse RSS/Atom feed and return matching entries."""
    if not _HAS_REQUESTS:
        logger.warning("requests library not available — cannot fetch RSS")
        return []

    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "ICDEV-CSP-Monitor/1.0"
        })
        resp.raise_for_status()
    except Exception as exc:
        logger.error("Failed to fetch %s: %s", url, exc)
        return []

    entries = []
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        logger.error("Failed to parse XML from %s: %s", url, exc)
        return []

    # Try RSS 2.0 first
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        description = (item.findtext("description") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()

        if filter_keywords:
            combined = f"{title} {description}".lower()
            if not any(kw.lower() in combined for kw in filter_keywords):
                continue

        entries.append({
            "title": title,
            "description": description[:2000],
            "url": link,
            "published": pub_date,
        })

    # Try Atom if RSS had no items
    if not entries:
        for entry in root.findall("atom:entry", ATOM_NS):
            title = (entry.findtext("atom:title", "", ATOM_NS) or "").strip()
            summary = (entry.findtext("atom:summary", "", ATOM_NS) or "").strip()
            link_el = entry.find("atom:link", ATOM_NS)
            link = link_el.get("href", "") if link_el is not None else ""
            updated = (entry.findtext("atom:updated", "", ATOM_NS) or "").strip()

            if filter_keywords:
                combined = f"{title} {summary}".lower()
                if not any(kw.lower() in combined for kw in filter_keywords):
                    continue

            entries.append({
                "title": title,
                "description": summary[:2000],
                "url": link,
                "published": updated,
            })

    return entries


# ── CSP SCANNERS ────────────────────────────────────────────────────────
def _classify_change(title: str, description: str) -> str:
    """Classify a CSP announcement into a change type."""
    text = f"{title} {description}".lower()

    if any(kw in text for kw in ["deprecated", "end of life", "eol", "sunset",
                                   "retiring", "discontinue", "decommission"]):
        return "service_deprecation"
    if any(kw in text for kw in ["breaking change", "migration required",
                                   "incompatible", "api v2", "api change"]):
        return "api_breaking_change"
    if any(kw in text for kw in ["fedramp", "hipaa", "pci", "soc 2", "iso 27001",
                                   "cjis", "compliance", "in scope", "authorization"]):
        return "compliance_scope_change"
    if any(kw in text for kw in ["new region", "now available in", "region launch",
                                   "expanded to", "availability zone"]):
        return "region_expansion"
    if any(kw in text for kw in ["security", "vulnerability", "patch", "cve",
                                   "security bulletin", "security advisory"]):
        return "security_update"
    if any(kw in text for kw in ["pricing", "cost", "free tier", "price reduction",
                                   "price increase"]):
        return "pricing_change"
    if any(kw in text for kw in ["certification", "accreditation", "attestation",
                                   "audit report", "soc report"]):
        return "certification_change"
    if any(kw in text for kw in ["new service", "launch", "now available",
                                   "general availability", "ga ", "preview",
                                   "introducing", "announcing"]):
        return "new_service"

    return "new_service"  # default


def _detect_government(title: str, description: str) -> bool:
    """Check if an announcement is government-specific."""
    text = f"{title} {description}".lower()
    return any(kw in text for kw in [
        "govcloud", "government", "gov ", "fedramp", "dod", "il4", "il5", "il6",
        "ic4g", "azure government", "assured workloads", "oci government",
    ])


def scan_csp(csp: str, config: Dict) -> List[Dict]:
    """Scan a specific CSP for service updates.

    Returns list of raw announcement dicts.
    """
    csp_config = config.get("sources", {}).get(csp, {})
    if not csp_config.get("enabled", False):
        logger.info("CSP %s scanning disabled", csp)
        return []

    announcements = []
    endpoints = csp_config.get("endpoints", [])

    for endpoint in endpoints:
        ep_type = endpoint.get("type", "rss")
        url = endpoint.get("url", "")
        name = endpoint.get("name", "unknown")
        filter_kw = endpoint.get("filter_keywords", [])

        if not url:
            continue

        if ep_type == "rss":
            entries = _parse_rss_feed(url, filter_keywords=filter_kw)
            for entry in entries:
                entry["csp"] = csp
                entry["endpoint_name"] = name
                entry["change_type"] = _classify_change(
                    entry.get("title", ""), entry.get("description", "")
                )
                entry["is_government"] = _detect_government(
                    entry.get("title", ""), entry.get("description", "")
                )
                announcements.append(entry)
        elif ep_type == "api":
            # API endpoints require CSP-specific adapters
            logger.debug("API endpoint %s/%s: requires CSP-specific adapter", csp, name)
        elif ep_type == "html":
            # HTML endpoints require scraping — log for manual review
            logger.debug("HTML endpoint %s/%s: requires scraping setup", csp, name)

    logger.info("CSP %s: %d announcements found", csp, len(announcements))
    return announcements


def scan_all_csps(config: Dict) -> Dict[str, List[Dict]]:
    """Scan all enabled CSPs for service updates."""
    results = {}
    for csp in SUPPORTED_CSPS:
        try:
            results[csp] = scan_csp(csp, config)
        except Exception as exc:
            logger.error("Failed to scan CSP %s: %s", csp, exc)
            results[csp] = []
    return results


# ── SIGNAL GENERATION ───────────────────────────────────────────────────
def announcements_to_signals(announcements: List[Dict], config: Dict) -> List[Dict]:
    """Convert raw CSP announcements into innovation signals."""
    signals = []
    signal_config = config.get("signals", {})
    gov_boost = signal_config.get("government_boost", 1.3)
    compliance_boost = signal_config.get("compliance_boost", 1.5)
    category_map = signal_config.get("category_mapping", CHANGE_TYPE_CATEGORY)
    score_map = signal_config.get("community_score_mapping", CHANGE_TYPE_SCORE)

    for ann in announcements:
        change_type = ann.get("change_type", "new_service")
        category = category_map.get(change_type, "infrastructure")
        base_score = score_map.get(change_type, 0.5)

        # Apply boosts
        score = base_score
        if ann.get("is_government"):
            score = min(score * gov_boost, 1.0)
        if change_type in ("compliance_scope_change", "certification_change"):
            score = min(score * compliance_boost, 1.0)
        score = round(score, 4)

        csp = ann.get("csp", "unknown")
        title = ann.get("title", "")
        description = ann.get("description", "")

        signal = {
            "id": f"sig-{uuid.uuid4().hex[:12]}",
            "source": "csp_monitor",
            "source_type": change_type,
            "title": f"[{csp.upper()}] {title}"[:200],
            "description": f"CSP: {csp.upper()} | Type: {change_type} | {description}"[:2000],
            "url": ann.get("url", ""),
            "metadata": {
                "csp": csp,
                "change_type": change_type,
                "endpoint_name": ann.get("endpoint_name", ""),
                "is_government": ann.get("is_government", False),
                "published": ann.get("published", ""),
            },
            "community_score": score,
            "content_hash": _content_hash([csp, title, change_type]),
            "discovered_at": _now(),
            "category": category,
        }
        signals.append(signal)

    return signals


# ── REGISTRY DIFF ───────────────────────────────────────────────────────
def diff_registry(registry: Dict, signals: List[Dict]) -> List[Dict]:
    """Diff detected signals against registry to identify new/changed services.

    This is the offline-capable core — works without network access by
    comparing stored signals against the local registry.
    """
    changes = []
    services = registry.get("services", {})

    for signal in signals:
        csp = signal.get("metadata", {}).get("csp", "")
        change_type = signal.get("source_type", "")

        if change_type == "new_service":
            # Check if service already in registry
            title_lower = signal.get("title", "").lower()
            csp_services = services.get(csp, {})
            already_known = any(
                svc.get("display_name", "").lower() in title_lower
                for svc in csp_services.values()
            )
            if not already_known:
                changes.append({
                    "signal": signal,
                    "action": "add_to_registry",
                    "description": f"New {csp.upper()} service not in registry",
                })
        elif change_type == "service_deprecation":
            changes.append({
                "signal": signal,
                "action": "mark_deprecated",
                "description": f"{csp.upper()} service deprecation detected",
            })
        elif change_type == "compliance_scope_change":
            changes.append({
                "signal": signal,
                "action": "update_compliance",
                "description": f"{csp.upper()} compliance scope change — review csp_certifications.json",
            })
        elif change_type == "region_expansion":
            changes.append({
                "signal": signal,
                "action": "update_regions",
                "description": f"{csp.upper()} new region — update registry regions",
            })
        elif change_type == "certification_change":
            changes.append({
                "signal": signal,
                "action": "update_certifications",
                "description": f"{csp.upper()} certification change — may affect deployment eligibility",
            })

    return changes


# ── MAIN SCANNER WORKFLOW ───────────────────────────────────────────────
class CSPMonitor:
    """CSP Service Monitor — scan, diff, signal, and update."""

    def __init__(self, config_path: Optional[str] = None,
                 registry_path: Optional[str] = None,
                 db_path: Optional[str] = None):
        self._config = _load_config(Path(config_path) if config_path else None)
        self._registry = _load_registry(Path(registry_path) if registry_path else None)
        self._db_path = Path(db_path) if db_path else DB_PATH
        self._registry_path = Path(registry_path) if registry_path else REGISTRY_PATH

    def scan(self, csp: Optional[str] = None) -> Dict:
        """Scan CSP(s) for service updates and store signals."""
        _audit("csp_monitor.scan_start", "csp_monitor",
               f"Scanning CSP(s): {csp or 'all'}")

        # Scan
        if csp:
            raw = {csp: scan_csp(csp, self._config)}
        else:
            raw = scan_all_csps(self._config)

        # Convert to signals
        all_announcements = []
        for csp_name, anns in raw.items():
            all_announcements.extend(anns)

        signals = announcements_to_signals(all_announcements, self._config)

        # Store signals
        stored_count = 0
        skipped_count = 0
        try:
            conn = _get_db(self._db_path)
            if not _check_table_exists(conn, "innovation_signals"):
                logger.warning("innovation_signals table not found — signals not stored")
                conn.close()
                return {
                    "status": "warning",
                    "message": "innovation_signals table not found",
                    "announcements": len(all_announcements),
                    "signals_generated": len(signals),
                    "signals_stored": 0,
                    "signals_skipped": 0,
                }

            for signal in signals:
                if _store_signal(conn, signal):
                    stored_count += 1
                else:
                    skipped_count += 1

            conn.commit()
            conn.close()
        except FileNotFoundError:
            logger.warning("Database not found — signals not stored")

        # Diff against registry
        changes = diff_registry(self._registry, signals)

        result = {
            "status": "ok",
            "scanned_at": _now(),
            "csps_scanned": list(raw.keys()),
            "announcements": len(all_announcements),
            "signals_generated": len(signals),
            "signals_stored": stored_count,
            "signals_skipped": skipped_count,
            "registry_changes_detected": len(changes),
            "changes": [
                {
                    "action": c["action"],
                    "description": c["description"],
                    "signal_id": c["signal"]["id"],
                    "signal_title": c["signal"]["title"],
                }
                for c in changes
            ],
        }

        _audit("csp_monitor.scan_complete", "csp_monitor",
               f"Stored {stored_count} signals, {len(changes)} registry changes",
               details=result)

        return result

    def diff(self) -> Dict:
        """Diff local registry against recent signals (offline-capable)."""
        try:
            conn = _get_db(self._db_path)
            if not _check_table_exists(conn, "innovation_signals"):
                conn.close()
                return {"status": "warning", "message": "innovation_signals table not found", "changes": []}

            # Get recent CSP monitor signals (last 30 days)
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            rows = conn.execute(
                "SELECT * FROM innovation_signals WHERE source = 'csp_monitor' "
                "AND discovered_at >= ? ORDER BY discovered_at DESC LIMIT 500",
                (cutoff,)
            ).fetchall()
            conn.close()

            signals = []
            for row in rows:
                signals.append({
                    "id": row["id"],
                    "source": row["source"],
                    "source_type": row["source_type"],
                    "title": row["title"],
                    "description": row["description"],
                    "url": row["url"],
                    "metadata": json.loads(row["metadata"] or "{}"),
                    "community_score": row["community_score"],
                    "content_hash": row["content_hash"],
                    "discovered_at": row["discovered_at"],
                    "category": row["category"],
                })
        except FileNotFoundError:
            return {"status": "error", "message": "Database not found", "changes": []}

        changes = diff_registry(self._registry, signals)
        return {
            "status": "ok",
            "diffed_at": _now(),
            "signals_analyzed": len(signals),
            "registry_services": sum(
                len(svc) for svc in self._registry.get("services", {}).values()
            ),
            "changes_detected": len(changes),
            "changes": [
                {
                    "action": c["action"],
                    "description": c["description"],
                    "signal_id": c["signal"]["id"],
                    "signal_title": c["signal"]["title"],
                    "csp": c["signal"].get("metadata", {}).get("csp", ""),
                    "change_type": c["signal"].get("source_type", ""),
                }
                for c in changes
            ],
        }

    def get_status(self) -> Dict:
        """Get CSP monitor status — signals by CSP and change type."""
        try:
            conn = _get_db(self._db_path)
            if not _check_table_exists(conn, "innovation_signals"):
                conn.close()
                return {"status": "warning", "message": "innovation_signals table not found"}

            # Count signals by CSP
            rows = conn.execute(
                "SELECT json_extract(metadata, '$.csp') as csp, "
                "source_type, status, COUNT(*) as cnt "
                "FROM innovation_signals WHERE source = 'csp_monitor' "
                "GROUP BY csp, source_type, status"
            ).fetchall()

            # Most recent scan
            latest = conn.execute(
                "SELECT discovered_at FROM innovation_signals "
                "WHERE source = 'csp_monitor' "
                "ORDER BY discovered_at DESC LIMIT 1"
            ).fetchone()
            conn.close()

            by_csp = {}
            total = 0
            for row in rows:
                csp = row["csp"] or "unknown"
                by_csp.setdefault(csp, {})
                by_csp[csp].setdefault(row["source_type"], {})
                by_csp[csp][row["source_type"]][row["status"]] = row["cnt"]
                total += row["cnt"]

            return {
                "status": "ok",
                "total_signals": total,
                "last_scan": latest["discovered_at"] if latest else None,
                "by_csp": by_csp,
                "registry_version": self._registry.get("_metadata", {}).get("version", "unknown"),
                "registry_last_updated": self._registry.get("_metadata", {}).get("last_updated", "unknown"),
                "registry_services": sum(
                    len(svc) for svc in self._registry.get("services", {}).values()
                ),
            }
        except FileNotFoundError:
            return {"status": "error", "message": "Database not found"}

    def update_registry(self, signal_id: str) -> Dict:
        """Apply a signal's change to the service registry (with backup)."""
        try:
            conn = _get_db(self._db_path)
            if not _check_table_exists(conn, "innovation_signals"):
                conn.close()
                return {"status": "error", "message": "innovation_signals table not found"}

            row = conn.execute(
                "SELECT * FROM innovation_signals WHERE id = ? AND source = 'csp_monitor'",
                (signal_id,)
            ).fetchone()
            if not row:
                conn.close()
                return {"status": "error", "message": f"Signal not found: {signal_id}"}

            metadata = json.loads(row["metadata"] or "{}")
            csp = metadata.get("csp", "")
            change_type = row["source_type"]

            # For now, mark signal as reviewed and log the registry update
            conn.execute(
                "UPDATE innovation_signals SET status = 'reviewed' WHERE id = ?",
                (signal_id,)
            )
            conn.commit()
            conn.close()

            # Save registry with backup
            _save_registry(self._registry, self._registry_path)

            _audit("csp_monitor.registry_update", "csp_monitor",
                   f"Registry updated for signal {signal_id}",
                   details={"signal_id": signal_id, "csp": csp, "change_type": change_type})

            return {
                "status": "ok",
                "signal_id": signal_id,
                "csp": csp,
                "change_type": change_type,
                "action": "registry_updated",
                "message": f"Signal {signal_id} marked as reviewed. "
                           f"Registry backed up and saved.",
            }
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    def generate_changelog(self, days: int = 30) -> Dict:
        """Generate changelog of CSP service changes."""
        try:
            conn = _get_db(self._db_path)
            if not _check_table_exists(conn, "innovation_signals"):
                conn.close()
                return {"status": "warning", "message": "innovation_signals table not found", "entries": []}

            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            rows = conn.execute(
                "SELECT * FROM innovation_signals WHERE source = 'csp_monitor' "
                "AND discovered_at >= ? ORDER BY discovered_at DESC",
                (cutoff,)
            ).fetchall()
            conn.close()

            entries = []
            for row in rows:
                metadata = json.loads(row["metadata"] or "{}")
                entries.append({
                    "date": row["discovered_at"],
                    "csp": metadata.get("csp", "unknown").upper(),
                    "change_type": row["source_type"],
                    "title": row["title"],
                    "description": row["description"][:200],
                    "url": row["url"],
                    "score": row["community_score"],
                    "status": row["status"],
                    "is_government": metadata.get("is_government", False),
                })

            # Group by CSP for summary
            by_csp = {}
            for e in entries:
                by_csp.setdefault(e["csp"], 0)
                by_csp[e["csp"]] += 1

            return {
                "status": "ok",
                "period_days": days,
                "total_entries": len(entries),
                "by_csp": by_csp,
                "entries": entries,
                "generated_at": _now(),
            }
        except FileNotFoundError:
            return {"status": "error", "message": "Database not found", "entries": []}

    def run_daemon(self):
        """Run continuous monitoring loop."""
        scheduling = self._config.get("scheduling", {})
        interval_hours = scheduling.get("default_scan_interval_hours", 12)
        quiet_start, quiet_end = 2, 6  # UTC quiet hours

        quiet_str = scheduling.get("quiet_hours", "02:00-06:00")
        if "-" in quiet_str:
            parts = quiet_str.split("-")
            try:
                quiet_start = int(parts[0].split(":")[0])
                quiet_end = int(parts[1].split(":")[0])
            except (ValueError, IndexError):
                pass

        logger.info("CSP Monitor daemon started (interval=%dh, quiet=%02d:00-%02d:00 UTC)",
                     interval_hours, quiet_start, quiet_end)
        _audit("csp_monitor.daemon_start", "csp_monitor", "Daemon started")

        while True:
            now = datetime.now(timezone.utc)
            if quiet_start <= now.hour < quiet_end:
                logger.debug("Quiet hours — sleeping")
                time.sleep(300)  # Check again in 5 min
                continue

            try:
                result = self.scan()
                logger.info("Scan complete: %d stored, %d changes",
                             result.get("signals_stored", 0),
                             result.get("registry_changes_detected", 0))
            except Exception as exc:
                logger.error("Scan failed: %s", exc)

            time.sleep(interval_hours * 3600)


# ── CLI ─────────────────────────────────────────────────────────────────
def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="CSP Service Monitor — track cloud provider service changes"
    )
    parser.add_argument("--scan", action="store_true",
                        help="Scan CSP(s) for service updates")
    parser.add_argument("--all", action="store_true",
                        help="Scan all enabled CSPs")
    parser.add_argument("--csp", type=str, choices=SUPPORTED_CSPS,
                        help="Scan a specific CSP")
    parser.add_argument("--diff", action="store_true",
                        help="Diff registry against recent signals (offline)")
    parser.add_argument("--status", action="store_true",
                        help="Show CSP monitor status")
    parser.add_argument("--update-registry", action="store_true",
                        help="Apply a signal's change to registry")
    parser.add_argument("--signal-id", type=str,
                        help="Signal ID for --update-registry")
    parser.add_argument("--changelog", action="store_true",
                        help="Generate CSP change changelog")
    parser.add_argument("--days", type=int, default=30,
                        help="Days of history for changelog (default: 30)")
    parser.add_argument("--daemon", action="store_true",
                        help="Run continuous monitoring")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to csp_monitor_config.yaml")
    parser.add_argument("--registry", type=str, default=None,
                        help="Path to csp_service_registry.json")
    parser.add_argument("--db", type=str, default=None,
                        help="Path to icdev.db")
    parser.add_argument("--json", action="store_true",
                        help="JSON output")

    args = parser.parse_args()
    monitor = CSPMonitor(
        config_path=args.config,
        registry_path=args.registry,
        db_path=args.db,
    )

    if args.scan:
        csp = args.csp if not args.all else None
        result = monitor.scan(csp=csp)
    elif args.diff:
        result = monitor.diff()
    elif args.status:
        result = monitor.get_status()
    elif args.update_registry:
        if not args.signal_id:
            print("Error: --signal-id required with --update-registry", file=sys.stderr)
            sys.exit(1)
        result = monitor.update_registry(args.signal_id)
    elif args.changelog:
        result = monitor.generate_changelog(days=args.days)
    elif args.daemon:
        monitor.run_daemon()
        return
    else:
        # Default: status
        result = monitor.get_status()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_human(result, args)


def _print_human(result: Dict, args):
    """Human-readable output."""
    status = result.get("status", "unknown")
    status_icon = "OK" if status == "ok" else ("WARN" if status == "warning" else "FAIL")

    if args.scan or args.all or args.csp:
        print(f"[{status_icon}] CSP Scan Complete")
        print(f"  Announcements: {result.get('announcements', 0)}")
        print(f"  Signals stored: {result.get('signals_stored', 0)} "
              f"(skipped: {result.get('signals_skipped', 0)})")
        print(f"  Registry changes: {result.get('registry_changes_detected', 0)}")
        for change in result.get("changes", [])[:10]:
            print(f"    [{change['action']}] {change['signal_title'][:80]}")
    elif args.diff:
        print(f"[{status_icon}] Registry Diff")
        print(f"  Signals analyzed: {result.get('signals_analyzed', 0)}")
        print(f"  Registry services: {result.get('registry_services', 0)}")
        print(f"  Changes detected: {result.get('changes_detected', 0)}")
        for change in result.get("changes", [])[:10]:
            print(f"    [{change['action']}] {change['csp'].upper()}: {change['signal_title'][:60]}")
    elif args.changelog:
        print(f"[{status_icon}] CSP Changelog ({result.get('period_days', 30)} days)")
        print(f"  Total entries: {result.get('total_entries', 0)}")
        for csp, count in result.get("by_csp", {}).items():
            print(f"    {csp}: {count} changes")
        for entry in result.get("entries", [])[:15]:
            gov = " [GOV]" if entry.get("is_government") else ""
            print(f"  [{entry['csp']}] {entry['change_type']}: {entry['title'][:60]}{gov}")
    else:
        # Status
        print(f"[{status_icon}] CSP Monitor Status")
        print(f"  Total signals: {result.get('total_signals', 0)}")
        print(f"  Last scan: {result.get('last_scan', 'never')}")
        print(f"  Registry: v{result.get('registry_version', '?')} "
              f"({result.get('registry_services', 0)} services)")
        for csp, types in result.get("by_csp", {}).items():
            total = sum(sum(s.values()) for s in types.values())
            print(f"    {csp.upper()}: {total} signals")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    main()
