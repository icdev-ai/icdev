#!/usr/bin/env python3
# CUI // SP-CTI
"""Genesis Weekly Report Generator (D-GEN-8, D-GEN-12).

Generates a comprehensive markdown report of all autonomous Genesis activity:
what ran, what improved, what needs attention, and all promotions/rejections.

Usage:
    python tools/genesis/reporter.py --generate --json
    python tools/genesis/reporter.py --latest --json
    python tools/genesis/reporter.py --list --json
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

REPORTS_DIR = BASE_DIR / "data" / "genesis" / "reports"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_query(query: str, params: tuple = ()) -> List[Dict]:
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def _collect_reflex_activity(since: str) -> List[Dict]:
    """Collect reflex run events from genesis_audit."""
    return _safe_query(
        "SELECT reflex_name, event_type, success, duration_ms, "
        "metric_name, metric_value, created_at "
        "FROM genesis_audit "
        "WHERE event_type IN ('genesis.reflex.completed', 'genesis.reflex.failed') "
        "AND created_at > ? ORDER BY created_at",
        (since,),
    )


def _collect_promotions(since: str) -> List[Dict]:
    """Collect promotion/rejection events."""
    return _safe_query(
        "SELECT id, artifact_type, genesis_reflex, confidence, "
        "promotion_status, created_at, promoted_at "
        "FROM genesis_gkp WHERE created_at > ? ORDER BY created_at",
        (since,),
    )


def _collect_circuit_breakers() -> List[Dict]:
    """Collect current circuit breaker states."""
    return _safe_query(
        "SELECT reflex_name, circuit_breaker_open, consecutive_failures, "
        "circuit_breaker_tripped_at, total_runs, total_successes, total_failures "
        "FROM genesis_reflex_state WHERE circuit_breaker_open = 1"
    )


def _collect_reflex_summary() -> List[Dict]:
    """Collect all reflex states for summary."""
    return _safe_query(
        "SELECT reflex_name, enabled, total_runs, total_successes, "
        "total_failures, last_metric_value, last_run_at "
        "FROM genesis_reflex_state ORDER BY reflex_name"
    )


def generate_report(lookback_days: int = 7) -> Dict[str, Any]:
    """Generate the weekly Genesis report as markdown."""
    since = (_utcnow() - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    now = _utcnow()

    # Collect data
    activity = _collect_reflex_activity(since)
    promotions = _collect_promotions(since)
    breakers = _collect_circuit_breakers()
    summary = _collect_reflex_summary()

    # Compute stats
    total_runs = len(activity)
    successes = sum(1 for a in activity if a.get("success"))
    failures = total_runs - successes
    success_rate = (successes / total_runs * 100) if total_runs > 0 else 0

    promoted = [p for p in promotions if p.get("promotion_status") in ("promoted", "auto_promoted")]
    rejected = [p for p in promotions if p.get("promotion_status") == "rejected"]
    pending = [p for p in promotions if p.get("promotion_status") == "pending_review"]

    # Group activity by reflex
    by_reflex = {}
    for a in activity:
        name = a.get("reflex_name", "unknown")
        if name not in by_reflex:
            by_reflex[name] = {"runs": 0, "successes": 0, "failures": 0, "avg_duration_ms": 0}
        by_reflex[name]["runs"] += 1
        if a.get("success"):
            by_reflex[name]["successes"] += 1
        else:
            by_reflex[name]["failures"] += 1

    # Build markdown
    lines = [
        "# Genesis Weekly Report",
        "",
        f"**Period:** {since[:10]} to {now.strftime('%Y-%m-%d')}",
        f"**Generated:** {_utcnow_iso()}",
        "**Classification:** CUI // SP-CTI",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total Reflex Runs | {total_runs} |",
        f"| Successes | {successes} ({success_rate:.1f}%) |",
        f"| Failures | {failures} |",
        f"| GKPs Promoted | {len(promoted)} |",
        f"| GKPs Rejected | {len(rejected)} |",
        f"| GKPs Pending Review | {len(pending)} |",
        f"| Circuit Breakers Open | {len(breakers)} |",
        "",
        "---",
        "",
        "## Reflex Activity",
        "",
        "| Reflex | Runs | OK | Fail | Last Metric |",
        "|--------|------|----|------|-------------|",
    ]

    for s in summary:
        name = s.get("reflex_name", "unknown")
        metric = s.get("last_metric_value")
        metric_str = f"{metric:.2f}" if metric is not None else "—"
        lines.append(
            f"| {name} | {s.get('total_runs', 0)} | "
            f"{s.get('total_successes', 0)} | {s.get('total_failures', 0)} | "
            f"{metric_str} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## Knowledge Promotions",
            "",
        ]
    )

    if promoted:
        lines.append(f"### Promoted ({len(promoted)})")
        lines.append("")
        for p in promoted:
            lines.append(
                f"- **{p['id']}** ({p['artifact_type']}) from `{p['genesis_reflex']}` "
                f"— confidence {p['confidence']:.2f}"
            )
        lines.append("")

    if rejected:
        lines.append(f"### Rejected ({len(rejected)})")
        lines.append("")
        for p in rejected:
            lines.append(f"- **{p['id']}** ({p['artifact_type']}) from `{p['genesis_reflex']}`")
        lines.append("")

    if pending:
        lines.append(f"### Awaiting Human Review ({len(pending)})")
        lines.append("")
        for p in pending:
            lines.append(
                f"- **{p['id']}** ({p['artifact_type']}) from `{p['genesis_reflex']}` "
                f"— confidence {p['confidence']:.2f}"
            )
        lines.append("")

    # Circuit breakers
    if breakers:
        lines.extend(
            [
                "---",
                "",
                "## Circuit Breakers (ATTENTION REQUIRED)",
                "",
            ]
        )
        for b in breakers:
            lines.append(
                f"- **{b['reflex_name']}** — OPEN since {b.get('circuit_breaker_tripped_at', 'unknown')} "
                f"({b.get('consecutive_failures', 0)} consecutive failures)"
            )
        lines.append("")
        lines.append("Run `python tools/genesis/daemon.py --reset <reflex>` to re-enable.")
        lines.append("")

    # Recommendations
    lines.extend(
        [
            "---",
            "",
            "## Recommendations",
            "",
        ]
    )

    if pending:
        lines.append(
            f"1. Review {len(pending)} pending GKPs: "
            f"`python tools/genesis/promoter.py --list --status-filter pending_review --json`"
        )
    if breakers:
        lines.append(f"2. Investigate {len(breakers)} tripped circuit breaker(s)")
    if failures > total_runs * 0.3 and total_runs > 0:
        lines.append(f"3. High failure rate ({100 - success_rate:.0f}%) — review reflex configurations")
    if not pending and not breakers and success_rate > 90:
        lines.append("1. All systems nominal — no action required")

    lines.extend(["", "---", "", "*Generated by Genesis Reporter — ICDEV™ v2.0 Autonomous Research Lab*"])

    report_md = "\n".join(lines)

    # Write to file
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = now.strftime("%Y-%m-%d")
    report_file = REPORTS_DIR / f"genesis-report-{date_str}.md"
    report_file.write_text(report_md, encoding="utf-8")

    # Audit log
    try:
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO genesis_audit (id, event_type, details, created_at)
            VALUES (?, ?, ?, ?)
        """,
            (
                f"aud-{uuid.uuid4().hex[:10]}",
                "genesis.report.generated",
                json.dumps(
                    {
                        "period_start": since[:10],
                        "period_end": date_str,
                        "total_runs": total_runs,
                        "success_rate": round(success_rate, 1),
                        "gkps_promoted": len(promoted),
                        "gkps_pending": len(pending),
                        "circuit_breakers_open": len(breakers),
                    }
                ),
                _utcnow_iso(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    return {
        "status": "generated",
        "file": str(report_file),
        "period": f"{since[:10]} to {date_str}",
        "summary": {
            "total_runs": total_runs,
            "success_rate": round(success_rate, 1),
            "promoted": len(promoted),
            "pending": len(pending),
            "rejected": len(rejected),
            "circuit_breakers_open": len(breakers),
        },
    }


def list_reports() -> List[Dict]:
    """List all generated reports."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(REPORTS_DIR.glob("genesis-report-*.md"), reverse=True)
    return [
        {"file": f.name, "size_bytes": f.stat().st_size, "date": f.stem.replace("genesis-report-", "")} for f in files
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Genesis Weekly Report Generator")
    parser.add_argument("--generate", action="store_true", help="Generate report")
    parser.add_argument("--lookback", type=int, default=7, help="Days to look back")
    parser.add_argument("--latest", action="store_true", help="Show latest report")
    parser.add_argument("--list", action="store_true", help="List all reports")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.generate:
        result = generate_report(lookback_days=args.lookback)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Report generated: {result['file']}")
            s = result["summary"]
            print(f"  Runs: {s['total_runs']} ({s['success_rate']}% success)")
            print(f"  Promoted: {s['promoted']}, Pending: {s['pending']}")
        return

    if args.latest:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(REPORTS_DIR.glob("genesis-report-*.md"), reverse=True)
        if not files:
            print("No reports generated yet")
            return
        content = files[0].read_text(encoding="utf-8")
        if args.json:
            print(json.dumps({"file": files[0].name, "content": content}))
        else:
            print(content)
        return

    if args.list:
        reports = list_reports()
        if args.json:
            print(json.dumps(reports, indent=2))
        else:
            for r in reports:
                print(f"  {r['date']}  ({r['size_bytes']} bytes)")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
