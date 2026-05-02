#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed: STRATEGOS Cyber Extension epic (sg-cyber-ext-01..09).

Fills the ~30% gap in the STRATEGOS cyber domain:
  - NVD CVE ingest pipeline (no API key required)
  - CISA KEV catalog merge (confirmed-exploited flag)
  - MITRE ATT&CK live sync (replaces hardcoded 8-tactic matrix)
  - Dynamic attack heatmap endpoint + frontend
  - Shodan InternetDB free enrichment for infrastructure nodes
  - Genesis daily reflex for NVD delta + CISA KEV refresh
  - V&V sign-off

Run: python tools/db/seeds/seed_sg_cyber_ext.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.db.storage import get_connection  # noqa: E402

NOW = datetime.now(timezone.utc).isoformat()

INSERT_SQL = """
INSERT INTO kanban_tasks
    (id, title, description, task_type, priority, status,
     scheduled_at, executor_type, dispatch_source,
     depends_on_task_id, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (id) DO NOTHING
"""


def _task(task_id, title, description, task_type="build", priority="high", depends_on=None):
    return (
        task_id,
        title,
        description,
        task_type,
        priority,
        "scheduled",
        NOW,
        "claude_cli",
        "sg_cyber_ext",
        depends_on,
        NOW,
        NOW,
    )


TASKS = [

    _task(
        "sg-cyber-ext-01",
        "DB migration: sg_cve_feed new columns (is_kev, kev_due_date, epss_score) + sg_supply_nodes.shodan_json",
        (
            "Create migration tools/db/migrations/0XX_cyber_ext_columns/up.py: "
            "  ALTER TABLE sg_cve_feed ADD COLUMN IF NOT EXISTS is_kev INTEGER DEFAULT 0; "
            "  ALTER TABLE sg_cve_feed ADD COLUMN IF NOT EXISTS kev_due_date TEXT; "
            "  ALTER TABLE sg_cve_feed ADD COLUMN IF NOT EXISTS epss_score REAL; "
            "  ALTER TABLE sg_supply_nodes ADD COLUMN IF NOT EXISTS shodan_json TEXT; "
            "  ALTER TABLE sg_supply_nodes ADD COLUMN IF NOT EXISTS shodan_updated_at TEXT; "
            "  CREATE INDEX IF NOT EXISTS idx_sg_cve_is_kev ON sg_cve_feed (is_kev); "
            "Use translate_sql() for PG compat. "
            "Add is_kev index to APPEND_ONLY_TABLES check if sg_cve_feed is append-only "
            "(it is not; status column is mutable). "
            "Also add sg_attack_techniques table: "
            "  id TEXT PRIMARY KEY, "
            "  tactic_id TEXT NOT NULL, "
            "  tactic_name TEXT NOT NULL, "
            "  technique_id TEXT NOT NULL, "
            "  technique_name TEXT NOT NULL, "
            "  sub_technique_id TEXT, "
            "  platforms TEXT,         -- comma-separated "
            "  permissions TEXT,       -- comma-separated "
            "  detection TEXT, "
            "  url TEXT, "
            "  stix_id TEXT UNIQUE, "
            "  updated_at TEXT; "
            "  CREATE INDEX idx_sg_at_tactic ON sg_attack_techniques (tactic_id); "
            "  CREATE INDEX idx_sg_at_technique ON sg_attack_techniques (technique_id); "
            "Only touch the migration file."
        ),
        priority="critical",
    ),

    _task(
        "sg-cyber-ext-02",
        "NVD CVE ingest pipeline — tools/strategos/nvd_importer.py (bulk + daily delta, no API key)",
        (
            "Create tools/strategos/nvd_importer.py that ingests NVD CVE data into sg_cve_feed. "
            ""
            "Data source: NVD CVE API 2.0 (https://services.nvd.nist.gov/rest/json/cves/2.0) "
            "  - No API key required for up to 5 req/30s; key (optional) bumps to 50 req/30s "
            "  - For air-gap: also support --file <nvd-cve-YYYY.json> (bulk download) "
            ""
            "CLI interface: "
            "  python tools/strategos/nvd_importer.py --year 2024           # bulk year import "
            "  python tools/strategos/nvd_importer.py --delta --days 3      # last N days "
            "  python tools/strategos/nvd_importer.py --file path/to/nvd.json  # air-gap file "
            "  python tools/strategos/nvd_importer.py --json                # machine-readable "
            ""
            "Per-CVE parsing: "
            "  cve_id = cve.id "
            "  description = cve.descriptions[lang==en].value (truncated to 1000 chars) "
            "  cvss_score = cve.metrics.cvssMetricV31[0].cvssData.baseScore (fallback V2) "
            "  cvss_vector = cve.metrics.cvssMetricV31[0].cvssData.vectorString "
            "  severity = cve.metrics.cvssMetricV31[0].cvssData.baseSeverity.lower() "
            "  vendor = cve.configurations[0].nodes[0].cpeMatch[0].criteria (parse vendor segment) "
            "  product = same (parse product segment) "
            "  published_at = cve.published "
            "  modified_at = cve.lastModified "
            ""
            "Upsert: INSERT INTO sg_cve_feed ... ON CONFLICT (cve_id) DO UPDATE "
            "  SET description=excluded.description, cvss_score=excluded.cvss_score, "
            "      modified_at=excluded.modified_at "
            "  WHERE excluded.modified_at > sg_cve_feed.modified_at "
            ""
            "Degrade gracefully: if network unavailable and no --file, print warning and exit 0. "
            "Use get_connection() not sqlite3.connect(). "
            "Add entry to tools/manifest/strategos.md (or create it). "
        ),
        priority="critical",
        depends_on="sg-cyber-ext-01",
    ),

    _task(
        "sg-cyber-ext-03",
        "CISA KEV importer — tools/strategos/cisa_kev_importer.py (known exploited, no API key)",
        (
            "Create tools/strategos/cisa_kev_importer.py that merges the CISA Known Exploited "
            "Vulnerabilities catalog into sg_cve_feed. "
            ""
            "Data source: https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json "
            "  (public JSON, no auth, ~1,100 CVEs as of 2025, grows weekly) "
            "  Air-gap: --file path/to/kev.json "
            ""
            "CLI: "
            "  python tools/strategos/cisa_kev_importer.py --sync      # fetch + merge "
            "  python tools/strategos/cisa_kev_importer.py --file kev.json "
            "  python tools/strategos/cisa_kev_importer.py --json "
            ""
            "Per-KEV entry: "
            "  cveID, vendorProject, product, vulnerabilityName, dateAdded, dueDate, requiredAction "
            ""
            "Merge logic (2 queries): "
            "  1. UPSERT any KEV CVE not yet in sg_cve_feed (minimal row: cve_id, description "
            "     from vulnerabilityName, severity='critical', published_at=dateAdded) "
            "  2. UPDATE sg_cve_feed SET is_kev=1, kev_due_date=dueDate "
            "     WHERE cve_id = <cveID> "
            ""
            "Print summary: 'N CVEs flagged as KEV, M new rows inserted'. "
            "Use get_connection(). Degrade gracefully if network unavailable. "
        ),
        priority="high",
        depends_on="sg-cyber-ext-01",
    ),

    _task(
        "sg-cyber-ext-04",
        "MITRE ATT&CK live sync — tools/strategos/attack_sync.py (STIX 2.1 bundle -> sg_attack_techniques)",
        (
            "Create tools/strategos/attack_sync.py that parses the MITRE ATT&CK STIX 2.1 bundle "
            "into sg_attack_techniques. "
            ""
            "Data sources: "
            "  Online: https://raw.githubusercontent.com/mitre/cti/master/enterprise-attack/enterprise-attack.json "
            "  Air-gap: --file path/to/enterprise-attack.json "
            ""
            "CLI: "
            "  python tools/strategos/attack_sync.py --fetch            # download + sync "
            "  python tools/strategos/attack_sync.py --file attack.json # air-gap "
            "  python tools/strategos/attack_sync.py --stats --json     # print counts "
            ""
            "Parsing logic (STIX 2.1 objects): "
            "  Tactics: objects where type='x-mitre-tactic' -> tactic_id=external_references[source_name==mitre-attack].external_id "
            "  Techniques: objects where type='attack-pattern' -> technique_id=external_references[source_name==mitre-attack].external_id "
            "    - Sub-techniques: external_id contains '.' (e.g. T1059.001) "
            "    - Platforms: x_mitre_platforms (list -> join with comma) "
            "    - Detection: x_mitre_detection "
            "  Kill chains: kill_chain_phases list -> tactic_name "
            ""
            "Upsert: INSERT INTO sg_attack_techniques ... ON CONFLICT (stix_id) DO UPDATE SET ... "
            ""
            "After sync: print 'N tactics, M techniques (K sub-techniques) loaded'. "
            "Expected counts: ~14 tactics, ~600+ techniques for enterprise-attack. "
            "Use get_connection(). "
        ),
        priority="high",
        depends_on="sg-cyber-ext-01",
    ),

    _task(
        "sg-cyber-ext-05",
        "Dynamic ATT&CK heatmap — replace hardcoded matrix with sg_attack_techniques DB query",
        (
            "Replace the hardcoded 8-tactic list in apps/strategos/blueprint.py:strategos_api_cyber_heatmap() "
            "with a live query against sg_attack_techniques. "
            ""
            "New endpoint logic: "
            "  1. Load all tactics: SELECT DISTINCT tactic_id, tactic_name FROM sg_attack_techniques "
            "     ORDER BY tactic_id (returns ~14 real tactics when synced, or falls back to "
            "     hardcoded 8 if sg_attack_techniques is empty). "
            "  2. For each tactic, load its techniques: SELECT technique_id, technique_name "
            "     FROM sg_attack_techniques WHERE tactic_id = ? AND sub_technique_id IS NULL "
            "     (top-level only; limit 6 per tactic for readability). "
            "  3. Count observations from sg_conflict_events: "
            "     SELECT technique_ids FROM sg_conflict_events WHERE event_type='cyber_op' "
            "     Build observed{} dict same as current code. "
            "  4. Add data_source field to response: 'live' if sg_attack_techniques has rows, "
            "     'hardcoded' if empty (fallback). "
            ""
            "Fallback: if sg_attack_techniques is empty (ATT&CK not yet synced), return "
            "existing hardcoded matrix with data_source='hardcoded'. "
            "No frontend JS changes needed — response shape is identical. "
            "Only touch blueprint.py (the heatmap route function). "
        ),
        priority="high",
        depends_on="sg-cyber-ext-04",
    ),

    _task(
        "sg-cyber-ext-06",
        "Shodan InternetDB enrichment — tools/strategos/shodan_enricher.py (free, no API key)",
        (
            "Create tools/strategos/shodan_enricher.py that enriches sg_supply_nodes with "
            "Shodan InternetDB data (free tier, no API key, rate limit 1 req/s). "
            ""
            "Shodan InternetDB API: GET https://internetdb.shodan.io/<ip> "
            "  Returns: {ip, ports:[], vulns:[], tags:[], hostnames:[], cpes:[]} "
            "  Free, no authentication. "
            ""
            "CLI: "
            "  python tools/strategos/shodan_enricher.py --all           # enrich all IPs "
            "  python tools/strategos/shodan_enricher.py --ip 1.2.3.4    # single IP "
            "  python tools/strategos/shodan_enricher.py --stale-days 7  # only re-check stale "
            "  python tools/strategos/shodan_enricher.py --json "
            ""
            "Logic: "
            "  1. SELECT node_id, ip_address FROM sg_supply_nodes WHERE ip_address IS NOT NULL. "
            "     (If ip_address column does not exist, add it to the migration in sg-cyber-ext-01 "
            "      as ALTER TABLE sg_supply_nodes ADD COLUMN IF NOT EXISTS ip_address TEXT.) "
            "  2. For each IP, GET https://internetdb.shodan.io/{ip} with 1s sleep between requests. "
            "  3. Store response JSON in shodan_json, set shodan_updated_at = now(). "
            "  4. UPDATE sg_supply_nodes SET shodan_json=?, shodan_updated_at=? WHERE node_id=?. "
            ""
            "After enrichment, expose in /api/cyber/infra-nodes: "
            "  For supply nodes, parse shodan_json and add to response: "
            "    open_ports: [22, 80, 443], shodan_vulns: ['CVE-2023-XXXX'], shodan_tags: ['vpn'] "
            "    (empty lists if shodan_json is null — degrade gracefully) "
            "  Also override cyber_vuln_score if shodan_vulns is non-empty: "
            "    cyber_vuln_score = min(10.0, 5.0 + len(shodan_vulns) * 0.5) "
            ""
            "Only touch shodan_enricher.py (new file) and blueprint.py infra-nodes route. "
            "Degrade gracefully if network unavailable — skip node with warning, continue. "
        ),
        priority="medium",
        depends_on="sg-cyber-ext-02",
    ),

    _task(
        "sg-cyber-ext-07",
        "CVE-to-infra enrichment — join sg_cve_feed to sg_supply_nodes in infra-nodes API response",
        (
            "Enhance /api/cyber/infra-nodes to add CVE context to supply nodes. "
            ""
            "For each supply node, look up CVEs that match its vendor or product name: "
            "  SELECT cve_id, cvss_score, is_kev FROM sg_cve_feed "
            "  WHERE (vendor ILIKE ? OR product ILIKE ?) AND cvss_score >= 7.0 "
            "  ORDER BY cvss_score DESC LIMIT 5 "
            "  Bind param: node.label or node.node_type (e.g. 'windows', 'cisco', 'scada'). "
            ""
            "Add to each supply node in the response: "
            "  cve_count: <total count of matching CVEs (CVSS >= 7.0)> "
            "  top_cves: [{cve_id, cvss_score, is_kev}, ...] (top 5 by score) "
            "  has_kev: true if any matching CVE is is_kev=1 "
            ""
            "Override cyber_vuln_score: "
            "  If has_kev: cyber_vuln_score = max(existing_score, 9.0) "
            "  Elif cve_count > 0: cyber_vuln_score = min(10.0, existing_score + cve_count * 0.3) "
            ""
            "Degrade gracefully: if sg_cve_feed is empty or join returns no rows, "
            "return existing response unchanged (no error). "
            "Only touch the infra-nodes route in apps/strategos/blueprint.py. "
        ),
        priority="medium",
        depends_on="sg-cyber-ext-02",
    ),

    _task(
        "sg-cyber-ext-08",
        "Genesis reflex: daily NVD delta + CISA KEV refresh — cyber_feed_refresh (24h cadence)",
        (
            "Register a Genesis reflex that keeps CVE and KEV data fresh automatically. "
            ""
            "Create tools/genesis/reflexes/cyber_feed_refresh.py: "
            "  REFLEX_NAME = 'cyber_feed_refresh' "
            "  COOLDOWN_HOURS = 24 "
            ""
            "  def run(context, session): "
            "    1. Run NVD delta importer: --delta --days 2 (overlap by 2 days for safety) "
            "       from tools.strategos.nvd_importer import run_delta "
            "       result = run_delta(days=2) "
            "    2. Run CISA KEV importer: "
            "       from tools.strategos.cisa_kev_importer import run_sync "
            "       kev_result = run_sync() "
            "    3. Emit a reflex artifact: "
            "       return {'nvd_upserted': result.upserted, 'kev_flagged': kev_result.flagged, "
            "               'ran_at': now()} "
            ""
            "Register in tools/genesis/reflexes/__init__.py (or wherever reflexes are indexed). "
            "Register with COOLDOWN_HOURS guard to prevent double-firing. "
            ""
            "Also add CLI entry in tools/genesis/reflexes/cyber_feed_refresh.py: "
            "  if __name__ == '__main__': "
            "    import json; print(json.dumps(run({}, None))) "
            ""
            "Do NOT schedule ATT&CK sync in this reflex — ATT&CK releases are infrequent "
            "(quarterly); run manually with attack_sync.py --fetch. "
        ),
        priority="medium",
        depends_on="sg-cyber-ext-03",
    ),

    _task(
        "sg-cyber-ext-09",
        "V&V: full cyber pipeline end-to-end — CVE feed, ATT&CK heatmap, Shodan, KEV, Genesis reflex",
        (
            "Verify all cyber extension components work end-to-end: "
            ""
            "Test 1 — NVD CVE ingest: "
            "  1. Run python tools/strategos/nvd_importer.py --delta --days 7 "
            "  2. Verify sg_cve_feed has rows (SELECT COUNT(*) FROM sg_cve_feed). "
            "  3. Verify at least one row has cvss_score IS NOT NULL AND severity IS NOT NULL. "
            "  4. Test air-gap path: run --file with a sample NVD JSON file. "
            ""
            "Test 2 — CISA KEV merge: "
            "  1. Run python tools/strategos/cisa_kev_importer.py --sync "
            "  2. Verify SELECT COUNT(*) FROM sg_cve_feed WHERE is_kev=1 > 0. "
            "  3. Verify at least one KEV row has kev_due_date IS NOT NULL. "
            ""
            "Test 3 — MITRE ATT&CK sync: "
            "  1. Run python tools/strategos/attack_sync.py --fetch (or --file if air-gap). "
            "  2. Verify sg_attack_techniques has >= 500 rows. "
            "  3. Verify SELECT COUNT(DISTINCT tactic_id) FROM sg_attack_techniques >= 14. "
            "  4. GET /strategos/api/cyber/attack-heatmap — verify data_source='live' in response. "
            "  5. Verify response has >= 14 tactic objects (not just 8). "
            ""
            "Test 4 — cyber_vuln_score: "
            "  1. GET /strategos/api/cyber/infra-nodes "
            "  2. Verify every node has cyber_vuln_score field (not missing). "
            "  3. Verify cyber_vuln_score > 0 for all nodes (not all 5.0 defaults). "
            "  4. Verify KEV-matched supply nodes have cyber_vuln_score >= 9.0. "
            ""
            "Test 5 — Shodan enrichment (requires internet): "
            "  1. Insert a test supply node with ip_address='8.8.8.8' (Google DNS — safe test IP). "
            "  2. Run python tools/strategos/shodan_enricher.py --ip 8.8.8.8 "
            "  3. Verify shodan_json IS NOT NULL in sg_supply_nodes WHERE ip_address='8.8.8.8'. "
            "  4. GET /strategos/api/cyber/infra-nodes — verify that node has open_ports field. "
            ""
            "Test 6 — CVE feed UI: "
            "  1. Load http://localhost:5050/strategos/cyber "
            "  2. Verify CVE feed table shows rows (not empty). "
            "  3. Verify CRITICAL severity rows appear with CVSS scores. "
            "  4. Verify 'KEV' badge visible on at least one row (if is_kev=1 rows exist). "
            ""
            "Test 7 — Genesis reflex: "
            "  1. Run python tools/genesis/reflexes/cyber_feed_refresh.py "
            "  2. Verify output JSON has nvd_upserted and kev_flagged keys. "
            "  3. Verify cooldown guard prevents re-fire within 24h. "
            ""
            "Document pass/fail per check. Fix root-cause before marking done."
        ),
        task_type="run",
        priority="high",
        depends_on="sg-cyber-ext-08",
    ),
]


def main():
    conn = get_connection()
    cur = conn.cursor()
    inserted = 0
    skipped = 0
    for task in TASKS:
        cur.execute(INSERT_SQL, task)
        if cur.rowcount and cur.rowcount > 0:
            inserted += 1
        else:
            skipped += 1
    conn.commit()
    conn.close()
    print(f"[seed_sg_cyber_ext] done -- {inserted} inserted, {skipped} skipped (conflict)")
    print("  sg-cyber-ext-01  DB migration: sg_cve_feed new cols + sg_attack_techniques + shodan_json")
    print("  sg-cyber-ext-02  NVD CVE ingest pipeline (bulk + delta, no key)")
    print("  sg-cyber-ext-03  CISA KEV importer (confirmed-exploited flag)")
    print("  sg-cyber-ext-04  MITRE ATT&CK STIX bundle sync -> sg_attack_techniques")
    print("  sg-cyber-ext-05  Dynamic ATT&CK heatmap (replaces hardcoded 8-tactic matrix)")
    print("  sg-cyber-ext-06  Shodan InternetDB enrichment (free, no key)")
    print("  sg-cyber-ext-07  CVE-to-infra join (cve_count + top_cves + has_kev on supply nodes)")
    print("  sg-cyber-ext-08  Genesis reflex: daily NVD delta + CISA KEV (24h cadence)")
    print("  sg-cyber-ext-09  V&V: full pipeline end-to-end")
    print()
    print("Quick-fix already shipped: cyber_vuln_score in /api/cyber/infra-nodes")
    print("View at: http://localhost:5050/kanban")


if __name__ == "__main__":
    main()
