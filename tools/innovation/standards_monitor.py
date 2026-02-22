#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Standards Body Change Tracker for ICDEV.

Monitors NIST, CISA, DoD CIO, FedRAMP, and ISO for updates affecting ICDEV's
compliance framework.  RSS/Atom via stdlib ElementTree (D7), append-only
storage (D6), deterministic keyword impact assessment (dual-hub D111).

Usage:
    python tools/innovation/standards_monitor.py --check --body nist --json
    python tools/innovation/standards_monitor.py --check --all --json
    python tools/innovation/standards_monitor.py --report --days 30 --json
    python tools/innovation/standards_monitor.py --assess --update-id "upd-xxx" --json
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── PATH SETUP ──────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "innovation_config.yaml"

# ── GRACEFUL IMPORTS ────────────────────────────────────────────────────
try:
    import yaml; _HAS_YAML = True
except ImportError:
    _HAS_YAML = False
try:
    import requests; _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False
try:
    from tools.audit.audit_logger import log_event as audit_log_event; _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def audit_log_event(**kwargs): return -1

# ── CONSTANTS ───────────────────────────────────────────────────────────
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
DEFAULT_TIMEOUT = 30

# Deterministic keyword->framework mapping (dual-hub model D111, no LLM)
FRAMEWORK_KEYWORD_MAP = {
    "nist_800_53": ["800-53", "SP 800-53"], "fedramp": ["FedRAMP", "cloud authorization"],
    "cmmc": ["CMMC", "cybersecurity maturity model"], "nist_800_171": ["800-171", "CUI protection"],
    "fips_199": ["FIPS 199", "security categorization"], "fips_200": ["FIPS 200"],
    "fips_140": ["FIPS 140", "cryptographic module"], "nist_800_207": ["800-207", "zero trust"],
    "cisa_sbd": ["secure by design", "memory safe"], "cato": ["cATO", "continuous ATO"],
    "mosa": ["MOSA", "modular open systems"], "devsecops": ["DevSecOps", "pipeline security"],
    "nist_csf": ["cybersecurity framework", "NIST CSF"], "iso_27001": ["ISO 27001", "27001:2022"],
    "hipaa": ["HIPAA"], "pci_dss": ["PCI DSS"], "cjis": ["CJIS", "criminal justice"],
    "sbom": ["SBOM", "software bill of materials"], "nist_800_161": ["800-161", "C-SCRM"],
}
CATALOG_KEYWORDS = ["revision", "final", "new publication", "supersedes", "withdrawn",
                     "updated", "amendment", "errata", "initial public draft"]
CROSSWALK_KEYWORDS = ["control mapping", "crosswalk", "baseline", "overlay", "tailoring",
                       "reciprocity", "control family", "enhancement"]

# ── HELPERS ─────────────────────────────────────────────────────────────
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _audit(event_type, actor, action, details=None):
    if _HAS_AUDIT:
        try:
            audit_log_event(event_type=event_type, actor=actor, action=action,
                            details=json.dumps(details) if details else None,
                            project_id="innovation-engine")
        except Exception:
            pass

def _load_config():
    if not _HAS_YAML or not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("standards_monitoring", {})

def _safe_get(url, timeout=DEFAULT_TIMEOUT):
    """HTTP GET -> (bytes|None, error|None). Air-gap safe."""
    if not _HAS_REQUESTS:
        return None, "requests not installed (air-gapped)"
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code in (429, 403):
            return None, f"http_{resp.status_code}"
        resp.raise_for_status()
        return resp.content, None
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        return None, type(e).__name__
    except requests.exceptions.RequestException as e:
        return None, str(e)

def _ensure_table(db_path=None):
    """Create innovation_standards_updates table if missing."""
    conn = _get_db(db_path)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS innovation_standards_updates (
            id TEXT PRIMARY KEY, body TEXT NOT NULL, title TEXT NOT NULL,
            publication_type TEXT, url TEXT, abstract TEXT, published_date TEXT,
            impact_assessment TEXT, status TEXT NOT NULL DEFAULT 'new',
            content_hash TEXT UNIQUE, created_at TEXT NOT NULL)""")
        for col in ("body", "status", "created_at"):
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_isu_{col} "
                         f"ON innovation_standards_updates ({col})")
        conn.commit()
    finally:
        conn.close()


# ── RSS / ATOM PARSING (stdlib xml.etree — D7) ─────────────────────────
def _parse_feed(xml_bytes):
    """Parse Atom/RSS feed entries from raw XML bytes."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    items = (root.findall("atom:entry", ATOM_NS) or root.findall("entry")
             or (root.find("channel") or root).findall("item"))
    entries = []
    for item in items:
        def _txt(tags):
            for t in tags:
                el = item.find(f"atom:{t}", ATOM_NS) or item.find(t)
                if el is not None and el.text: return el.text.strip()
                if el is not None and el.get("href"): return el.get("href")
            return ""
        title = _txt(["title"])
        if not title:
            continue
        tu = title.upper()
        pub_type = ("SP" if any(k in tu for k in ("SP ", "SP-")) else
                    "FIPS" if "FIPS" in tu else
                    "IR" if any(k in tu for k in ("IR ", "NISTIR")) else
                    "directive" if any(k in tu for k in ("DIRECTIVE", "BOD")) else
                    "advisory" if "ADVISORY" in tu else
                    "memo" if "MEMO" in tu else "unknown")
        entries.append({"title": title, "link": _txt(["link"]),
                        "published": _txt(["published", "updated", "pubDate"]),
                        "summary": _txt(["summary", "content", "description"])[:2000],
                        "pub_type": pub_type})
    return entries

# ── STORAGE (append-only — D6) ──────────────────────────────────────────
def _store_updates(body, entries, db_path=None):
    """Store feed entries, dedup by content_hash. Returns new count."""
    if not entries:
        return 0
    conn, stored = _get_db(db_path), 0
    try:
        for e in entries:
            chash = hashlib.sha256(f"{body}:{e['title']}".encode()).hexdigest()
            if conn.execute("SELECT 1 FROM innovation_standards_updates "
                            "WHERE content_hash=?", (chash,)).fetchone():
                continue
            conn.execute(
                "INSERT INTO innovation_standards_updates "
                "(id,body,title,publication_type,url,abstract,published_date,"
                "impact_assessment,status,content_hash,created_at) "
                "VALUES (?,?,?,?,?,?,?,NULL,'new',?,?)",
                (f"upd-{uuid.uuid4().hex[:12]}", body, e["title"],
                 e.get("pub_type", "unknown"), e.get("link", ""),
                 e.get("summary", ""), e.get("published", ""), chash, _now()))
            stored += 1
        conn.commit()
    finally:
        conn.close()
    return stored

# ── BODY CHECKERS ───────────────────────────────────────────────────────
def _find_body_config(config, name):
    return next((b for b in config.get("bodies", []) if b.get("name") == name), None)

def _check_feed_body(body_name, config, db_path, url_key, rss_key=None, filter_fn=None):
    """Generic RSS-based checker shared by all bodies."""
    _ensure_table(db_path)
    bc = _find_body_config(config, body_name)
    if not bc:
        return {"body": body_name, "updates": 0, "stored": 0, "skipped": "not configured"}
    urls = ([bc[rss_key]] if rss_key and bc.get(rss_key) else [])
    base = bc.get(url_key, "")
    if base:
        urls.extend([base.rstrip("/") + s for s in ("/feed", "/rss.xml", "")])
    all_entries, errors = [], []
    for url in urls:
        raw, err = _safe_get(url)
        if err: errors.append(err); continue
        parsed = _parse_feed(raw)
        if parsed: all_entries = parsed; break
    if filter_fn:
        all_entries = [e for e in all_entries if filter_fn(e, bc)]
    stored = _store_updates(body_name, all_entries, db_path)
    _audit("standards.check", "standards-monitor",
           f"{body_name}: {len(all_entries)} entries, {stored} new",
           {"body": body_name, "entries": len(all_entries), "stored": stored})
    res = {"body": body_name, "updates": len(all_entries), "stored": stored}
    if errors and not all_entries: res["error"] = errors[0]
    return res

def check_nist_updates(config, db_path=None):
    """Check NIST CSRC for new SP 800/FIPS/IR publications."""
    def _f(e, bc):
        series = bc.get("watch_series", ["SP 800", "FIPS", "IR"])
        return any(s.lower() in e["title"].lower() for s in series)
    return _check_feed_body("nist", config, db_path,
                            url_key="publications_url", rss_key="rss_url", filter_fn=_f)

def check_cisa_updates(config, db_path=None):
    """Check CISA for advisories and binding operational directives."""
    _ensure_table(db_path)
    bc = _find_body_config(config, "cisa")
    if not bc:
        return {"body": "cisa", "updates": 0, "stored": 0, "skipped": "not configured"}
    all_entries = []
    for key in ("alerts_url", "directives_url"):
        base = bc.get(key, "")
        if not base: continue
        for sfx in ("/feed", ".xml", ""):
            raw, err = _safe_get(base.rstrip("/") + sfx)
            if err: continue
            parsed = _parse_feed(raw)
            if key == "directives_url":
                for e in parsed: e["pub_type"] = "directive"
            if parsed: all_entries.extend(parsed); break
    stored = _store_updates("cisa", all_entries, db_path)
    _audit("standards.check", "standards-monitor",
           f"CISA: {len(all_entries)} entries, {stored} new",
           {"body": "cisa", "entries": len(all_entries), "stored": stored})
    return {"body": "cisa", "updates": len(all_entries), "stored": stored}

def check_dod_updates(config, db_path=None):
    """Check DoD CIO for memos on zero trust, DevSecOps, CMMC, cATO, MOSA."""
    def _f(e, bc):
        topics = bc.get("watch_topics", ["zero trust", "DevSecOps", "CMMC", "cATO", "MOSA"])
        text = f"{e['title']} {e.get('summary', '')}".lower()
        if any(t.lower() in text for t in topics):
            e["pub_type"] = "memo"; return True
        return False
    return _check_feed_body("dod", config, db_path, url_key="cio_url", filter_fn=_f)

def check_fedramp_updates(config, db_path=None):
    """Check FedRAMP for blog and marketplace updates."""
    return _check_feed_body("fedramp", config, db_path, url_key="updates_url")

def check_iso_updates(config, db_path=None):
    """Check ISO for updates to watched standards (27001, 27002, 27017, 27018)."""
    def _f(e, bc):
        stds = [str(s) for s in bc.get("watch_standards", [27001, 27002, 27017, 27018])]
        return any(s in f"{e['title']} {e.get('summary','')}".lower() for s in stds)
    return _check_feed_body("iso", config, db_path, url_key="standards_url", filter_fn=_f)

BODY_CHECKERS = {
    "nist": check_nist_updates, "cisa": check_cisa_updates,
    "dod": check_dod_updates, "fedramp": check_fedramp_updates,
    "iso": check_iso_updates,
}

def check_all_bodies(db_path=None):
    """Check all configured standards bodies for updates."""
    config = _load_config()
    if not config.get("enabled", True):
        return {"check_time": _now(), "enabled": False, "results": {}}
    results = {}
    for name, fn in BODY_CHECKERS.items():
        try:
            results[name] = fn(config, db_path)
        except Exception as e:
            results[name] = {"body": name, "updates": 0, "stored": 0, "error": str(e)}
    totals = {"updates_found": sum(r.get("updates", 0) for r in results.values()),
              "new_stored": sum(r.get("stored", 0) for r in results.values())}
    _audit("standards.check_all", "standards-monitor",
           f"Checked {len(results)} bodies", totals)
    return {"check_time": _now(), "bodies_checked": len(results),
            "results": results, "totals": totals}

# ── IMPACT ASSESSMENT (deterministic keyword matching) ──────────────────
def assess_impact(update_id, db_path=None):
    """Assess impact of a standards update on ICDEV compliance framework."""
    _ensure_table(db_path)
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM innovation_standards_updates WHERE id=?",
                           (update_id,)).fetchone()
        if not row:
            return {"error": f"Update not found: {update_id}"}
        text = f"{row['title'] or ''} {row['abstract'] or ''}".lower()
        affected = [fw for fw, kws in FRAMEWORK_KEYWORD_MAP.items()
                    if any(k.lower() in text for k in kws)]
        catalog_needed = any(k.lower() in text for k in CATALOG_KEYWORDS)
        crosswalk_hit = any(k.lower() in text for k in CROSSWALK_KEYWORDS)
        pt = (row["publication_type"] or "").lower()
        body = (row["body"] or "").lower()
        priority = round(min(
            {"fips": .40, "directive": .35, "sp": .25, "advisory": .20,
             "ir": .15, "memo": .15}.get(pt, .10)
            + {"nist": .15, "cisa": .15, "dod": .10, "fedramp": .10, "iso": .05}.get(body, .05)
            + min(len(affected) * .05, .25) + (.15 if catalog_needed else 0), 1.0), 2)
        actions = []
        if catalog_needed:
            actions.append({"action": "update_catalogs", "urgency": "high",
                            "description": "Review for new/revised controls; update JSON catalogs"})
        if crosswalk_hit:
            actions.append({"action": "review_crosswalk", "urgency": "high",
                            "description": "Check dual-hub crosswalk mappings"})
        for fw in affected:
            actions.append({"action": f"review_{fw}", "urgency": "medium",
                            "description": f"Review impact on {fw} assessor/catalog"})
        if pt in ("fips", "directive"):
            actions.append({"action": "mandatory_review", "urgency": "critical",
                            "description": f"Mandatory {body} publication — ISSO review required"})
        if not actions:
            actions.append({"action": "monitor", "urgency": "low",
                            "description": "No immediate ICDEV impact; continue monitoring"})
        assessment = {"affected_frameworks": affected, "catalog_update_needed": catalog_needed,
                      "crosswalk_impact": crosswalk_hit, "priority": priority,
                      "recommended_actions": actions, "assessed_at": _now()}
        conn.execute("UPDATE innovation_standards_updates "
                     "SET impact_assessment=?, status='assessed' WHERE id=?",
                     (json.dumps(assessment), update_id))
        conn.commit()
        _audit("standards.assess", "standards-monitor",
               f"Assessed {update_id}: priority={priority}",
               {"update_id": update_id, "priority": priority, "affected": affected})
        return {"update_id": update_id, "body": body, "title": row["title"],
                "publication_type": pt, "status": "assessed",
                "impact_assessment": assessment}
    finally:
        conn.close()

# ── REPORTING ───────────────────────────────────────────────────────────
def get_standards_report(days=30, db_path=None):
    """Report of recent standards changes with impact summary."""
    _ensure_table(db_path)
    conn = _get_db(db_path)
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows = conn.execute(
            "SELECT body,status,COUNT(*) as count FROM innovation_standards_updates "
            "WHERE created_at>=? GROUP BY body,status ORDER BY body", (cutoff,)).fetchall()
        by_body, total = {}, 0
        for r in rows:
            by_body.setdefault(r["body"], {"total": 0, "by_status": {}})
            by_body[r["body"]]["by_status"][r["status"]] = r["count"]
            by_body[r["body"]]["total"] += r["count"]; total += r["count"]
        assessed_rows = conn.execute(
            "SELECT id,body,title,publication_type,impact_assessment "
            "FROM innovation_standards_updates "
            "WHERE created_at>=? AND status='assessed' ORDER BY created_at DESC LIMIT 50",
            (cutoff,)).fetchall()
        assessed = []
        for r in assessed_rows:
            a = json.loads(r["impact_assessment"]) if r["impact_assessment"] else {}
            assessed.append({"id": r["id"], "body": r["body"], "title": r["title"],
                             "publication_type": r["publication_type"],
                             "priority": a.get("priority", 0),
                             "affected_frameworks": a.get("affected_frameworks", []),
                             "catalog_update_needed": a.get("catalog_update_needed", False)})
        assessed.sort(key=lambda x: x["priority"], reverse=True)
        unassessed = conn.execute(
            "SELECT COUNT(*) as c FROM innovation_standards_updates "
            "WHERE created_at>=? AND status='new'", (cutoff,)).fetchone()["c"]
        return {"report_date": _now(), "period_days": days, "total_updates": total,
                "unassessed_count": unassessed, "by_body": by_body,
                "assessed_updates": assessed}
    finally:
        conn.close()

# ── CLI ─────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="ICDEV Standards Body Change Tracker")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--db-path", type=Path, default=None)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="Check for updates")
    g.add_argument("--report", action="store_true", help="Standards change report")
    g.add_argument("--assess", action="store_true", help="Assess update impact")
    p.add_argument("--body", choices=list(BODY_CHECKERS.keys()))
    p.add_argument("--all", action="store_true")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--update-id", type=str)
    args = p.parse_args()
    try:
        if args.check:
            if args.all:
                result = check_all_bodies(args.db_path)
            elif args.body:
                result = BODY_CHECKERS[args.body](_load_config(), args.db_path)
            else:
                p.error("--check requires --body or --all"); return
        elif args.report:
            result = get_standards_report(args.days, args.db_path)
        elif args.assess:
            if not args.update_id:
                p.error("--assess requires --update-id"); return
            result = assess_impact(args.update_id, args.db_path)
        else:
            result = {"error": "No action specified"}
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_human(args, result)
    except Exception as e:
        print(json.dumps({"error": str(e)}, indent=2) if args.json
              else f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

def _print_human(args, result):
    """Human-readable CLI output."""
    if "error" in result:
        print(f"ERROR: {result['error']}"); return
    if args.check and args.all:
        t = result.get("totals", {})
        print(f"Checked {result['bodies_checked']} bodies  |  "
              f"Found: {t['updates_found']}  New: {t['new_stored']}")
        for n, r in result.get("results", {}).items():
            err = r.get("error", r.get("skipped", ""))
            print(f"  {n}: {r.get('updates',0)} updates, {r.get('stored',0)} new"
                  + (f"  ({err})" if err else ""))
    elif args.check:
        print(f"{result['body']}: {result.get('updates',0)} found, {result.get('stored',0)} new")
    elif args.report:
        print(f"Standards Report ({result['period_days']}d)  Total: {result['total_updates']}  "
              f"Unassessed: {result['unassessed_count']}")
        for n, info in result.get("by_body", {}).items():
            print(f"  {n}: {info['total']} ({', '.join(f'{k}={v}' for k,v in info['by_status'].items())})")
        for item in result.get("assessed_updates", [])[:10]:
            fws = ", ".join(item["affected_frameworks"][:3])
            print(f"  [{item['priority']:.2f}] {item['body']}: {item['title'][:55]}"
                  + (f"  -> {fws}" if fws else ""))
    elif args.assess:
        a = result.get("impact_assessment", {})
        print(f"{result['body']}/{result['publication_type']}: {result['title']}")
        fws = ", ".join(a.get("affected_frameworks", [])) or "none"
        print(f"Priority: {a.get('priority',0):.2f}  Frameworks: {fws}")
        print(f"Catalog update: {a.get('catalog_update_needed')}  Crosswalk: {a.get('crosswalk_impact')}")
        for act in a.get("recommended_actions", []):
            print(f"  [{act['urgency']}] {act['description']}")


if __name__ == "__main__":
    main()
