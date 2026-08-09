"""Inventory Scanner — MDC Workflow Step 1.

Scans migration inventory and produces a wave/complexity report.
Outputs JSON with artifact paths to stdout.
"""
import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

_ARTIFACTS_DIR = _ROOT / "data" / "studio_artifacts" / "mdc"

_DEFAULT_INVENTORY = {
    "app_count": 12,
    "server_count": 48,
    "db_count": 8,
    "rs_rehost": 5,
    "rs_replatform": 3,
    "rs_refactor": 2,
    "rs_retire": 1,
    "rs_retain": 1,
    "rs_repurchase": 0,
    "rs_relocate": 0,
}

_7RS = ["rehost", "replatform", "refactor", "retire", "retain", "repurchase", "relocate"]


def scan_inventory(project_id: str) -> dict:
    """Load migration inventory from DB; fall back to defaults if absent."""
    inv = dict(_DEFAULT_INVENTORY)
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT metric_key, metric_value FROM mdc_inventory WHERE project_id = %s ORDER BY created_at DESC LIMIT 30",
                (project_id,),
            )
            rows = cur.fetchall()
            if rows:
                for row in rows:
                    key, val = row[0], row[1]
                    try:
                        inv[key] = int(float(val))
                    except (TypeError, ValueError):
                        pass
    except Exception:
        pass  # table may not exist — use defaults

    total_apps = inv.get("app_count", 0)
    total_dbs = inv.get("db_count", 0)

    rs_dist = {r: inv.get(f"rs_{r}", 0) for r in _7RS}
    rs_total = sum(rs_dist.values()) or 1

    # Complexity: refactor=high, replatform=medium, rest=low
    high_complexity = rs_dist.get("refactor", 0)
    medium_complexity = rs_dist.get("replatform", 0) + rs_dist.get("repurchase", 0)
    low_complexity = rs_total - high_complexity - medium_complexity

    # Wave estimation: ~5 apps per wave
    wave_count = max(1, -(-total_apps // 5))

    # Dependency chains: estimate from DB count
    dep_chains = max(1, total_dbs * 2)

    # Blockers: high complexity + unresolved retains
    blockers = high_complexity + rs_dist.get("retain", 0)

    return {
        "inventory": inv,
        "rs_distribution": rs_dist,
        "rs_total": rs_total,
        "wave_count": wave_count,
        "complexity": {
            "high": high_complexity,
            "medium": medium_complexity,
            "low": low_complexity,
        },
        "dependency_chains": dep_chains,
        "blockers": blockers,
        "project_id": project_id,
    }


def build_report(result: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    inv = result["inventory"]
    rs = result["rs_distribution"]
    project_id = result["project_id"]

    lines = [
        "# Migration Inventory Scan Report — MDC",
        f"**Generated:** {ts}  ",
        f"**Project:** {project_id}",
        "",
        "## Asset Inventory",
        "| Asset Type | Count |",
        "|------------|-------|",
        f"| Applications | {inv.get('app_count', 0)} |",
        f"| Servers | {inv.get('server_count', 0)} |",
        f"| Databases | {inv.get('db_count', 0)} |",
        "",
        "## 7Rs Distribution",
        "| Strategy | Count | % of Total |",
        "|----------|-------|------------|",
    ]
    rs_total = result["rs_total"] or 1
    for r in _7RS:
        count = rs.get(r, 0)
        pct = round(count / rs_total * 100, 1)
        lines.append(f"| {r.title()} | {count} | {pct}% |")

    comp = result["complexity"]
    lines += [
        "",
        "## Complexity Analysis",
        "| Complexity | Count |",
        "|------------|-------|",
        f"| High (Refactor) | {comp['high']} |",
        f"| Medium (Replatform/Repurchase) | {comp['medium']} |",
        f"| Low (Rehost/Retire/Retain/Relocate) | {comp['low']} |",
        "",
        "## Migration Planning",
        f"- **Wave Count:** {result['wave_count']} (est. ~5 apps/wave)",
        f"- **Dependency Chains:** {result['dependency_chains']}",
        f"- **Blockers:** {result['blockers']} (high-complexity + retain items)",
        "",
    ]

    if result["blockers"] > 0:
        lines.append(f"WARNING: {result['blockers']} blocker(s) require resolution before migration waves can begin.")
    else:
        lines.append("OK: No blockers detected — migration inventory is ready for wave planning.")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Inventory Scanner — MDC Step 1")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--canvas", default="mdc")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        result = scan_inventory(args.project_id)
        report_md = build_report(result)

        _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        uid = uuid.uuid4().hex[:8]
        fname = f"inventory_scan_{uid}.md"
        fpath = _ARTIFACTS_DIR / fname
        fpath.write_text(report_md, encoding="utf-8", newline="")

        output = {
            "status": "success",
            "app_count": result["inventory"].get("app_count", 0),
            "wave_count": result["wave_count"],
            "blockers": result["blockers"],
            "artifacts": [
                {"name": "Inventory Scan Report", "path": fpath.relative_to(_ROOT).as_posix(), "type": "md"},
            ],
        }
        print(json.dumps(output))
        sys.exit(0)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
