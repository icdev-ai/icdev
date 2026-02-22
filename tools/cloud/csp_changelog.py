#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""CSP Changelog Generator — produce human-readable change reports.

Reads innovation_signals from the CSP monitor and generates structured
changelogs in Markdown or JSON format, grouped by CSP, change type, or date.

Includes actionable recommendations for each change type:
    - new_service: "Evaluate for ICDEV provider integration"
    - service_deprecation: "Plan migration, update Terraform modules"
    - compliance_scope_change: "Review csp_certifications.json, update region_validator"
    - api_breaking_change: "Update provider implementation, test backward compat"
    - certification_change: "Review deployment eligibility, update security gates"

Usage:
    python tools/cloud/csp_changelog.py --generate --days 30 --json
    python tools/cloud/csp_changelog.py --generate --days 7 --format markdown --output .tmp/csp_changelogs/
    python tools/cloud/csp_changelog.py --generate --csp aws --days 90 --json
    python tools/cloud/csp_changelog.py --summary --json
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

# ── PATH SETUP ──────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# ── CONSTANTS ───────────────────────────────────────────────────────────
RECOMMENDATIONS = {
    "new_service": {
        "action": "Evaluate for ICDEV provider integration",
        "details": "Check if the new service maps to an existing ICDEV provider ABC "
                   "(secrets, storage, kms, monitoring, iam, registry, ai_ml). "
                   "If so, add implementation. Update csp_service_registry.json.",
        "urgency": "low",
        "affected_files": [
            "context/cloud/csp_service_registry.json",
            "docs/features/phase-38-cloud-agnostic-architecture.md",
        ],
    },
    "service_deprecation": {
        "action": "Plan migration away from deprecated service",
        "details": "Identify ICDEV components using this service. Update Terraform "
                   "modules, provider implementations, and deployment profiles. "
                   "Generate migration signal for affected tenants.",
        "urgency": "high",
        "affected_files": [
            "tools/cloud/*_provider.py",
            "deploy/terraform/modules/",
            "args/cloud_config.yaml",
            "context/cloud/csp_service_registry.json",
        ],
    },
    "compliance_scope_change": {
        "action": "Review compliance certification registry",
        "details": "A CSP service was added to or removed from a compliance program. "
                   "Update context/compliance/csp_certifications.json and "
                   "csp_service_registry.json. May affect tenant deployment eligibility.",
        "urgency": "critical",
        "affected_files": [
            "context/compliance/csp_certifications.json",
            "context/cloud/csp_service_registry.json",
            "tools/cloud/region_validator.py",
            "docs/features/phase-38-cloud-agnostic-architecture.md",
        ],
    },
    "region_expansion": {
        "action": "Update region lists in registry and config",
        "details": "New region available. Update csp_service_registry.json regions "
                   "and args/cloud_config.yaml region options. Check if new region "
                   "holds required compliance certifications.",
        "urgency": "low",
        "affected_files": [
            "context/cloud/csp_service_registry.json",
            "context/compliance/csp_certifications.json",
            "args/cloud_config.yaml",
        ],
    },
    "api_breaking_change": {
        "action": "Update provider implementation for API compatibility",
        "details": "A CSP API has a breaking change. Update the affected provider "
                   "implementation, run integration tests, and verify backward "
                   "compatibility. Check SDK version requirements.",
        "urgency": "critical",
        "affected_files": [
            "tools/cloud/*_provider.py",
            "tools/infra/terraform_generator_*.py",
            "requirements.txt",
        ],
    },
    "security_update": {
        "action": "Review security advisory and apply patches",
        "details": "CSP security update. Check if ICDEV components are affected. "
                   "Update security gates if new vulnerability class detected.",
        "urgency": "high",
        "affected_files": [
            "args/security_gates.yaml",
            "tools/security/",
        ],
    },
    "pricing_change": {
        "action": "Update cost models and usage tracking",
        "details": "CSP pricing changed. Update cost estimation in usage tracking "
                   "dashboard and deployment profile cost recommendations.",
        "urgency": "low",
        "affected_files": [
            "tools/dashboard/templates/usage.html",
        ],
    },
    "certification_change": {
        "action": "Review deployment eligibility for affected tenants",
        "details": "CSP gained or lost a compliance certification. Update "
                   "csp_certifications.json and region_validator. May require "
                   "tenant migration if certification was lost.",
        "urgency": "critical",
        "affected_files": [
            "context/compliance/csp_certifications.json",
            "tools/cloud/region_validator.py",
            "args/deployment_profiles.yaml",
            "docs/features/phase-38-cloud-agnostic-architecture.md",
        ],
    },
}


def _get_db(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_signals(db_path: Path, days: int = 30, csp: Optional[str] = None) -> List[Dict]:
    """Fetch CSP monitor signals from database."""
    conn = _get_db(db_path)
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='innovation_signals'"
    )
    if not cursor.fetchone():
        conn.close()
        return []

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    query = (
        "SELECT * FROM innovation_signals WHERE source = 'csp_monitor' "
        "AND discovered_at >= ? "
    )
    params = [cutoff]

    if csp:
        query += "AND json_extract(metadata, '$.csp') = ? "
        params.append(csp)

    query += "ORDER BY discovered_at DESC LIMIT 500"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    entries = []
    for row in rows:
        metadata = json.loads(row["metadata"] or "{}")
        entries.append({
            "id": row["id"],
            "date": row["discovered_at"],
            "csp": metadata.get("csp", "unknown"),
            "change_type": row["source_type"],
            "title": row["title"],
            "description": row["description"],
            "url": row["url"],
            "score": row["community_score"],
            "status": row["status"],
            "category": row["category"],
            "is_government": metadata.get("is_government", False),
        })
    return entries


def generate_markdown_changelog(entries: List[Dict], days: int,
                                 include_recommendations: bool = True) -> str:
    """Generate Markdown changelog."""
    lines = [
        "# CUI // SP-CTI",
        f"# CSP Service Changelog — Last {days} Days",
        f"",
        f"*Generated: {_now()}*",
        f"*Total changes: {len(entries)}*",
        "",
    ]

    if not entries:
        lines.append("No CSP changes detected in this period.")
        return "\n".join(lines)

    # Group by CSP
    by_csp: Dict[str, List[Dict]] = {}
    for entry in entries:
        csp = entry["csp"].upper()
        by_csp.setdefault(csp, [])
        by_csp[csp].append(entry)

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| CSP | Changes | Critical | High | Low |")
    lines.append("|-----|---------|----------|------|-----|")
    for csp, csp_entries in sorted(by_csp.items()):
        critical = sum(1 for e in csp_entries
                       if RECOMMENDATIONS.get(e["change_type"], {}).get("urgency") == "critical")
        high = sum(1 for e in csp_entries
                   if RECOMMENDATIONS.get(e["change_type"], {}).get("urgency") == "high")
        low = len(csp_entries) - critical - high
        lines.append(f"| {csp} | {len(csp_entries)} | {critical} | {high} | {low} |")
    lines.append("")

    # Details by CSP
    for csp, csp_entries in sorted(by_csp.items()):
        lines.append(f"## {csp}")
        lines.append("")

        for entry in csp_entries:
            gov_tag = " **[GOV]**" if entry.get("is_government") else ""
            urgency = RECOMMENDATIONS.get(entry["change_type"], {}).get("urgency", "low")
            urgency_tag = f" `{urgency.upper()}`" if urgency in ("critical", "high") else ""

            lines.append(f"### {entry['title'][:100]}{gov_tag}{urgency_tag}")
            lines.append(f"- **Date:** {entry['date']}")
            lines.append(f"- **Type:** {entry['change_type']}")
            lines.append(f"- **Score:** {entry['score']}")
            lines.append(f"- **Status:** {entry['status']}")
            if entry.get("url"):
                lines.append(f"- **URL:** {entry['url']}")
            lines.append(f"- {entry['description'][:300]}")

            if include_recommendations and entry["change_type"] in RECOMMENDATIONS:
                rec = RECOMMENDATIONS[entry["change_type"]]
                lines.append(f"")
                lines.append(f"**Recommended Action:** {rec['action']}")
                lines.append(f"- {rec['details']}")
                lines.append(f"- **Affected files:** {', '.join(rec['affected_files'][:3])}")

            lines.append("")

    return "\n".join(lines)


def generate_summary(entries: List[Dict]) -> Dict:
    """Generate summary statistics."""
    by_csp = {}
    by_type = {}
    by_urgency = {"critical": 0, "high": 0, "low": 0}
    gov_count = 0

    for entry in entries:
        csp = entry["csp"].upper()
        by_csp[csp] = by_csp.get(csp, 0) + 1
        by_type[entry["change_type"]] = by_type.get(entry["change_type"], 0) + 1
        urgency = RECOMMENDATIONS.get(entry["change_type"], {}).get("urgency", "low")
        by_urgency[urgency] = by_urgency.get(urgency, 0) + 1
        if entry.get("is_government"):
            gov_count += 1

    return {
        "total_changes": len(entries),
        "by_csp": by_csp,
        "by_change_type": by_type,
        "by_urgency": by_urgency,
        "government_changes": gov_count,
        "commercial_changes": len(entries) - gov_count,
    }


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="CSP Changelog Generator — produce change reports"
    )
    parser.add_argument("--generate", action="store_true",
                        help="Generate changelog")
    parser.add_argument("--summary", action="store_true",
                        help="Generate summary statistics only")
    parser.add_argument("--days", type=int, default=30,
                        help="Days of history (default: 30)")
    parser.add_argument("--csp", type=str, default=None,
                        choices=["aws", "azure", "gcp", "oci", "ibm"],
                        help="Filter by CSP")
    parser.add_argument("--format", type=str, default="json",
                        choices=["json", "markdown"],
                        help="Output format (default: json)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output directory for markdown files")
    parser.add_argument("--no-recommendations", action="store_true",
                        help="Omit recommendations from changelog")
    parser.add_argument("--db", type=str, default=None,
                        help="Path to icdev.db")
    parser.add_argument("--json", action="store_true",
                        help="Force JSON output")

    args = parser.parse_args()
    db_path = Path(args.db) if args.db else DB_PATH

    try:
        entries = fetch_signals(db_path, days=args.days, csp=args.csp)
    except FileNotFoundError:
        print(json.dumps({"status": "error", "message": "Database not found"}), file=sys.stderr)
        sys.exit(1)

    if args.summary:
        summary = generate_summary(entries)
        summary["status"] = "ok"
        summary["period_days"] = args.days
        summary["generated_at"] = _now()
        if args.json or args.format == "json":
            print(json.dumps(summary, indent=2))
        else:
            print(f"CSP Changelog Summary ({args.days} days)")
            print(f"  Total: {summary['total_changes']} changes")
            for csp, count in sorted(summary["by_csp"].items()):
                print(f"    {csp}: {count}")
            print(f"  Critical: {summary['by_urgency']['critical']}")
            print(f"  High: {summary['by_urgency']['high']}")
            print(f"  Gov: {summary['government_changes']} | Commercial: {summary['commercial_changes']}")
        return

    if args.generate:
        if args.json or args.format == "json":
            result = {
                "status": "ok",
                "period_days": args.days,
                "csp_filter": args.csp,
                "total_entries": len(entries),
                "summary": generate_summary(entries),
                "entries": entries,
                "recommendations": {
                    ct: rec for ct, rec in RECOMMENDATIONS.items()
                    if any(e["change_type"] == ct for e in entries)
                },
                "generated_at": _now(),
            }
            print(json.dumps(result, indent=2))
        else:
            md = generate_markdown_changelog(
                entries, args.days,
                include_recommendations=not args.no_recommendations,
            )
            if args.output:
                output_dir = Path(args.output)
                output_dir.mkdir(parents=True, exist_ok=True)
                filename = f"csp-changelog-{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"
                output_path = output_dir / filename
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(md)
                print(f"Changelog written to: {output_path}")
            else:
                print(md)
        return

    # Default: summary
    summary = generate_summary(entries)
    print(json.dumps({"status": "ok", "summary": summary}, indent=2))


if __name__ == "__main__":
    main()
