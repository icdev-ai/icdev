# CUI // SP-CTI — OSINT Multi-Source Ingestor
"""
Free sources (no API key):
  - CISA Known Exploited Vulnerabilities (KEV)
  - Abuse.ch ThreatFox (recent IOCs)
  - Abuse.ch URLhaus (malicious URLs)
  - Abuse.ch MalwareBazaar (recent samples)
  - Abuse.ch Feodo Tracker (C2 IPs)
  - CISA Alerts RSS

Usage:
    python tools/osint/osint_ingestor.py --fetch --json
    python tools/osint/osint_ingestor.py --fetch --source cisa_kev
    python tools/osint/osint_ingestor.py --list --json
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import ssl
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# SSL context that skips verification — used only for threat intel ingestion feeds
# where the data itself is untrusted anyway; we parse, not exec it.
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.osint")

_SOURCES: dict[str, dict] = {
    "cisa_kev": {
        "url": "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
        "label": "CISA Known Exploited Vulnerabilities",
        "ioc_type": "cve",
    },
    "threatfox": {
        "url": "https://threatfox.abuse.ch/export/json/recent/",
        "label": "Abuse.ch ThreatFox (public export)",
        "ioc_type": "multi",
    },
    "urlhaus_csv": {
        "url": "https://urlhaus.abuse.ch/downloads/csv_online/",
        "label": "Abuse.ch URLhaus (online URLs)",
        "ioc_type": "url",
    },
    "feodo": {
        "url": "https://feodotracker.abuse.ch/downloads/ipblocklist.json",
        "label": "Abuse.ch Feodo C2 Tracker",
        "ioc_type": "ip",
    },
    "cisa_alerts": {
        "url": "https://www.cisa.gov/uscert/ncas/alerts.xml",
        "label": "CISA Alerts RSS",
        "ioc_type": "advisory",
    },
}

_SEVERITY_MAP = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "orange": "high",
    "red": "critical",
    "green": "low",
}


def _req(url: str, method: str = "GET", body: bytes | None = None,
         timeout: int = 15) -> bytes | None:
    headers = {"User-Agent": "ICDEV-OSINT/1.0 (security research)",
               "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        logger.warning("fetch %s failed: HTTP %s %s", url, e.code, e.reason)
        return None
    except urllib.error.URLError as e:
        logger.warning("fetch %s failed: %s", url, e)
        return None


def _signal_id(source: str, value: str) -> str:
    raw = f"{source}:{value}"
    return "osint-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Per-source parsers ──────────────────────────────────────────────────────

def _parse_cisa_kev(raw: bytes) -> list[dict]:
    data = json.loads(raw)
    out = []
    for v in data.get("vulnerabilities", [])[:50]:
        cve = v.get("cveID", "")
        out.append({
            "id": _signal_id("cisa_kev", cve),
            "source": "cisa_kev",
            "title": f"{cve} — {v.get('vulnerabilityName', '')}",
            "description": v.get("shortDescription", ""),
            "ioc_type": "cve",
            "ioc_value": cve,
            "severity": "high",
            "tags": ["kev", v.get("product", ""), v.get("vendorProject", "")],
            "published_at": v.get("dateAdded", _now_iso()),
        })
    return out


def _parse_threatfox(raw: bytes) -> list[dict]:
    # Export format: {ioc_id: [ioc_dict, ...], ...}
    data = json.loads(raw)
    if not isinstance(data, dict):
        return []
    out = []
    for ioc_list in list(data.values())[:100]:
        ioc = ioc_list[0] if isinstance(ioc_list, list) and ioc_list else ioc_list
        if not isinstance(ioc, dict):
            continue
        val = ioc.get("ioc_value", "")
        if not val:
            continue
        conf = ioc.get("confidence_level", 0)
        sev = "critical" if conf >= 90 else "high" if conf >= 75 else "medium"
        tags_raw = ioc.get("tags", "")
        tags = [t.strip() for t in tags_raw.split(",")] if tags_raw else []
        tags += [ioc.get("malware_printable", ""), ioc.get("threat_type", "")]
        out.append({
            "id": _signal_id("threatfox", val),
            "source": "threatfox",
            "title": f"ThreatFox [{ioc.get('ioc_type','')}]: {val[:80]}",
            "description": ioc.get("malware_printable", ""),
            "ioc_type": ioc.get("ioc_type", "unknown"),
            "ioc_value": val,
            "severity": sev,
            "tags": [t for t in tags if t],
            "published_at": ioc.get("first_seen_utc", _now_iso()),
        })
    return out


def _parse_urlhaus_csv(raw: bytes) -> list[dict]:
    # CSV columns (no header): id, date_added, url, status, date_online, threat, tags, link, reporter
    text = raw.decode(errors="ignore")
    reader = csv.reader(io.StringIO(text))
    out = []
    for row in reader:
        if not row or row[0].startswith("#"):
            continue
        if len(row) < 6:
            continue
        url_val = row[2] if len(row) > 2 else ""
        if not url_val:
            continue
        tags = [t.strip() for t in (row[6] if len(row) > 6 else "").split(",") if t.strip()]
        out.append({
            "id": _signal_id("urlhaus_csv", url_val),
            "source": "urlhaus",
            "title": f"Malicious URL [{row[3] if len(row)>3 else ''}]: {url_val[:80]}",
            "description": row[5] if len(row) > 5 else "",
            "ioc_type": "url",
            "ioc_value": url_val,
            "severity": "high" if (len(row) > 3 and row[3] == "online") else "medium",
            "tags": tags,
            "published_at": row[1] if len(row) > 1 else _now_iso(),
        })
        if len(out) >= 100:
            break
    return out



def _parse_feodo(raw: bytes) -> list[dict]:
    data = json.loads(raw)
    out = []
    for ip in data[:60]:
        val = ip.get("ip_address", "")
        out.append({
            "id": _signal_id("feodo", val),
            "source": "feodo",
            "title": f"C2 IP [{ip.get('malware','')}]: {val}:{ip.get('port','')}",
            "description": f"Botnet C2 — {ip.get('malware','')} hosted in {ip.get('country','')}",
            "ioc_type": "ip",
            "ioc_value": val,
            "severity": "critical",
            "tags": [ip.get("malware", ""), ip.get("country", "")],
            "published_at": ip.get("first_seen", _now_iso()),
        })
    return out


def _parse_cisa_alerts(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    out = []
    channel = root.find("channel")
    if channel is None:
        # try Atom
        items = root.findall("atom:entry", ns)
        for item in items[:20]:
            title = (item.findtext("atom:title", "", ns) or "").strip()
            link  = (item.findtext("atom:link", "", ns) or "")
            pub   = (item.findtext("atom:published", "", ns) or _now_iso())
            out.append({
                "id": _signal_id("cisa_alerts", title),
                "source": "cisa_alerts",
                "title": title,
                "description": item.findtext("atom:summary", "", ns) or "",
                "ioc_type": "advisory",
                "ioc_value": link,
                "severity": "high",
                "tags": ["advisory", "cisa"],
                "published_at": pub,
            })
    else:
        for item in channel.findall("item")[:20]:
            title = (item.findtext("title") or "").strip()
            link  = item.findtext("link") or ""
            pub   = item.findtext("pubDate") or _now_iso()
            out.append({
                "id": _signal_id("cisa_alerts", title),
                "source": "cisa_alerts",
                "title": title,
                "description": item.findtext("description") or "",
                "ioc_type": "advisory",
                "ioc_value": link,
                "severity": "high",
                "tags": ["advisory", "cisa"],
                "published_at": pub,
            })
    return out


_PARSERS = {
    "cisa_kev":    _parse_cisa_kev,
    "threatfox":   _parse_threatfox,
    "urlhaus_csv": _parse_urlhaus_csv,
    "feodo":       _parse_feodo,
    "cisa_alerts": _parse_cisa_alerts,
}


# ── DB helpers ──────────────────────────────────────────────────────────────

def _ensure_tables(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS osint_signals (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            title TEXT,
            description TEXT,
            ioc_type TEXT,
            ioc_value TEXT,
            severity TEXT DEFAULT 'medium',
            tags TEXT,
            published_at TEXT,
            fetched_at TEXT,
            classification TEXT DEFAULT 'CUI // SP-CTI'
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_osint_source ON osint_signals(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_osint_severity ON osint_signals(severity)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_osint_fetched ON osint_signals(fetched_at)")
    conn.commit()


def _upsert(conn, signals: list[dict]) -> int:
    now = _now_iso()
    inserted = 0
    for s in signals:
        try:
            conn.execute(
                """INSERT INTO osint_signals
                   (id, source, title, description, ioc_type, ioc_value, severity,
                    tags, published_at, fetched_at, classification)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO NOTHING""",
                (s["id"], s["source"], s.get("title",""), s.get("description",""),
                 s.get("ioc_type",""), s.get("ioc_value",""), s.get("severity","medium"),
                 json.dumps(s.get("tags",[])), s.get("published_at", now),
                 now, "CUI // SP-CTI")
            )
            inserted += 1
        except Exception as e:
            logger.debug("upsert skip %s: %s", s.get("id"), e)
    conn.commit()
    return inserted


# ── Public API ──────────────────────────────────────────────────────────────

def fetch_source(source_key: str) -> list[dict]:
    cfg = _SOURCES.get(source_key)
    if not cfg:
        raise ValueError(f"Unknown source: {source_key}")
    raw = _req(cfg["url"], method=cfg.get("method", "GET"), body=cfg.get("body"))
    if not raw:
        return []
    parser = _PARSERS.get(source_key)
    if not parser:
        return []
    try:
        return parser(raw)
    except Exception as e:
        logger.warning("parse %s failed: %s", source_key, e)
        return []


def ingest(sources: list[str] | None = None) -> dict:
    from tools.db.storage import get_canvas_connection as get_connection
    conn = get_connection()
    _ensure_tables(conn)
    sources = sources or list(_SOURCES.keys())
    results: dict[str, int] = {}
    for key in sources:
        signals = fetch_source(key)
        inserted = _upsert(conn, signals)
        results[key] = inserted
        logger.info("osint %s: %d fetched, %d new", key, len(signals), inserted)
    conn.close()
    return results


def list_signals(limit: int = 100, source: str | None = None,
                 severity: str | None = None) -> list[dict]:
    from tools.db.storage import get_canvas_connection as get_connection
    conn = get_connection()
    _ensure_tables(conn)
    where, params = ["1=1"], []
    if source:
        where.append("source = %s"); params.append(source)
    if severity:
        where.append("severity = %s"); params.append(severity)
    rows = conn.execute(
        f"SELECT * FROM osint_signals WHERE {' AND '.join(where)} "
        f"ORDER BY fetched_at DESC LIMIT %s",
        [*params, limit]
    ).fetchall()
    conn.close()
    cols = ["id","source","title","description","ioc_type","ioc_value",
            "severity","tags","published_at","fetched_at","classification"]
    return [dict(zip(cols, r)) for r in rows]


def signal_stats() -> dict:
    from tools.db.storage import get_canvas_connection as get_connection
    conn = get_connection()
    try:
        _ensure_tables(conn)
        total = (conn.execute("SELECT COUNT(*) FROM osint_signals").fetchone() or [0])[0]
        by_sev = {}
        for row in conn.execute(
            "SELECT severity, COUNT(*) FROM osint_signals GROUP BY severity"
        ).fetchall():
            by_sev[row[0]] = row[1]
        by_src = {}
        for row in conn.execute(
            "SELECT source, COUNT(*) FROM osint_signals GROUP BY source"
        ).fetchall():
            by_src[row[0]] = row[1]
        return {"total": total, "by_severity": by_sev, "by_source": by_src}
    finally:
        conn.close()


# ── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description="ICDEV OSINT Ingestor")
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--list", action="store_true")
    p.add_argument("--stats", action="store_true")
    p.add_argument("--source", help="Specific source key")
    p.add_argument("--severity", help="Filter by severity")
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.fetch:
        sources = [args.source] if args.source else None
        result = ingest(sources)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for k, v in result.items():
                print(f"  {k}: {v} new signals")
    elif args.list:
        signals = list_signals(source=args.source, severity=args.severity)
        if args.json:
            print(json.dumps(signals, indent=2, default=str))
        else:
            for s in signals:
                print(f"[{s['severity'].upper():8}] [{s['source']:12}] {s['title'][:80]}")
    elif args.stats:
        stats = signal_stats()
        if args.json:
            print(json.dumps(stats, indent=2))
        else:
            print(f"Total: {stats['total']}")
            for k, v in stats.get("by_severity", {}).items():
                print(f"  {k}: {v}")
    else:
        p.print_help()
