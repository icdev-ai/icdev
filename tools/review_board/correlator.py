#!/usr/bin/env python3
# CUI // SP-CTI
"""Cross-Reflex Correlator — dedup related findings across personas (D-RB-15).

Detects when multiple reflexes report findings about the same root cause:
    - SRE "DB is 1.6GB" + Perf "large tables" + Product "audit trail growing" → same root cause
    - Security "secret in .env" + QA "config not tested" → related

Uses keyword overlap and category matching to group related findings.

Usage:
    python tools/review_board/correlator.py --correlate --json
    python tools/review_board/correlator.py --groups --json
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection as _raw_get_connection  # noqa: E402


def _get_connection():
    conn = _raw_get_connection()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        pass
    return conn


# Known category relationships (root cause clusters)
CATEGORY_CLUSTERS = {
    "database": {"disk_usage", "db_size", "large_tables", "audit_growth", "backup_freshness"},
    "security": {"secret_exposure", "cve_sla", "injection_pattern", "dangerous_patterns"},
    "coverage": {"untested_modules", "e2e_coverage", "test_ratio", "syntax_errors"},
    "documentation": {"stale_docs", "undocumented_tools", "broken_refs", "missing_phase_docs", "claude_md_size"},
    "accessibility": {"aria_coverage", "form_accessibility", "template_quality"},
    "performance": {"temp_size", "db_size", "memory_growth_rate", "disk_growth_rate"},
}


def correlate_findings() -> Dict[str, Any]:
    """Find related findings across reflexes and group by root cause."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM review_board_findings WHERE fix_applied = 0 ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
        findings = [dict(r) for r in rows]
    except Exception:
        return {"groups": [], "ungrouped": 0}
    finally:
        conn.close()

    if not findings:
        return {"groups": [], "ungrouped": 0}

    # Step 1: Cluster by known category relationships
    groups: Dict[str, List[Dict]] = defaultdict(list)
    grouped_ids: Set[str] = set()

    for finding in findings:
        category = finding.get("category", "")
        for cluster_name, cluster_categories in CATEGORY_CLUSTERS.items():
            if category in cluster_categories:
                groups[cluster_name].append(finding)
                grouped_ids.add(finding["id"])
                break

    # Step 2: Keyword-based correlation for ungrouped findings
    ungrouped = [f for f in findings if f["id"] not in grouped_ids]
    for finding in ungrouped:
        title_words = set(
            re.findall(r"\w+", ((finding.get("title") or "") + " " + (finding.get("description") or "")).lower())
        )
        best_match = None
        best_overlap = 0

        for group_name, group_findings in groups.items():
            for gf in group_findings:
                gf_words = set(
                    re.findall(r"\w+", ((gf.get("title") or "") + " " + (gf.get("description") or "")).lower())
                )
                overlap = len(title_words & gf_words)
                if overlap > best_overlap and overlap >= 3:
                    best_overlap = overlap
                    best_match = group_name

        if best_match:
            groups[best_match].append(finding)
            grouped_ids.add(finding["id"])

    # Step 3: Build result
    result_groups = []
    for group_name, group_findings in groups.items():
        if len(group_findings) < 2:
            continue  # Only report groups with 2+ findings
        # Determine group severity (worst finding)
        severities = [f.get("severity", "info") for f in group_findings]
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        worst = min(severities, key=lambda s: severity_order.get(s, 5))
        personas = list(set(f.get("reflex_name", "") for f in group_findings))

        result_groups.append(
            {
                "root_cause": group_name,
                "severity": worst,
                "finding_count": len(group_findings),
                "personas_involved": personas,
                "findings": [
                    {
                        "id": f["id"],
                        "severity": f["severity"],
                        "reflex": f["reflex_name"],
                        "title": f["title"],
                        "category": f["category"],
                    }
                    for f in group_findings
                ],
            }
        )

    # Sort by severity then count
    result_groups.sort(key=lambda g: (severity_order.get(g["severity"], 5), -g["finding_count"]))

    return {
        "groups": result_groups,
        "total_grouped": len(grouped_ids),
        "ungrouped": len(findings) - len(grouped_ids),
        "total_findings": len(findings),
    }


def main():
    parser = argparse.ArgumentParser(description="Cross-Reflex Correlator (D-RB-15)")
    parser.add_argument("--correlate", action="store_true", help="Run correlation analysis")
    parser.add_argument("--groups", action="store_true", help="Show correlation groups")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.correlate or args.groups:
        result = correlate_findings()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for g in result["groups"]:
                print(
                    f"\n  [{g['severity'].upper()}] Root cause: {g['root_cause']} "
                    f"({g['finding_count']} findings across {', '.join(g['personas_involved'])})"
                )
                for f in g["findings"][:5]:
                    print(f"    - [{f['severity']}] {f['reflex']}: {f['title']}")
            print(f"\n  Grouped: {result['total_grouped']}, Ungrouped: {result['ungrouped']}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
