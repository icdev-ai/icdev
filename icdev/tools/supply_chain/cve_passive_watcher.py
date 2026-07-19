#!/usr/bin/env python3
# CUI // SP-CTI
"""Passive CVE Watcher — ATO continuous monitoring via immutable audit log streaming.

Monitors the append-only audit_trail for ATO-relevant security events, extracts
CVE identifiers without re-scanning, scores each CVE's ATO relevance based on
the component's downstream impact radius in the dependency graph, and auto-triages
new discoveries.

ATO relevance tagging: a CVE is tagged ato_relevant=True when its affected component
has an impact_radius >= ATO_RELEVANT_THRESHOLD in the supply chain dependency graph —
meaning it sits upstream of enough downstream systems to pose a boundary-level risk.
This satisfies NIST 800-53 SI-4 (System Monitoring) and CA-7 (Continuous Monitoring)
without triggering active re-scans.

Tables used:
  - audit_trail              (read — immutable source of CVE signals)
  - cve_triage               (read/write — triage records via cve_triager)
  - cve_passive_watch_log    (write — append-only processing log, NIST AU)
  - supply_chain_dependencies (read — ATO relevance scoring + blast radius)

CLI:
  python tools/supply_chain/cve_passive_watcher.py --project-id <id> --scan --json
  python tools/supply_chain/cve_passive_watcher.py --project-id <id> --scan --since-id 0 --json
  python tools/supply_chain/cve_passive_watcher.py --project-id <id> --scan --ato-only --json
  python tools/supply_chain/cve_passive_watcher.py --project-id <id> --scan --gate --json
  python tools/supply_chain/cve_passive_watcher.py --project-id <id> --status --json
  python tools/supply_chain/cve_passive_watcher.py --project-id <id> --watch --interval 60 --json
"""

import argparse
import json
import os
import re
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from tools.db.storage import get_connection
from tools.supply_chain.cve_triager import triage_cve
from tools.supply_chain.dependency_graph import propagate_impact

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

# CVE identifier pattern — CVE-YYYY-NNNNN
CVE_PATTERN = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE)

# Audit event types that are ATO-relevant and may contain CVE signals
ATO_EVENT_TYPES = (
    "vulnerability_found",
    "security_scan",
    "scrm_assessed",
    "supply_chain_risk_escalated",
    "sbom_generated",
    "stig_checked",
    "cve_triaged",  # Included so we can detect already-known CVEs from other agents
)

# Event types that indicate a CVE is already actively managed (skip auto-triage)
SKIP_EVENT_TYPES = {"cve_triaged"}

# ATO relevance: component must have >= N downstream dependents to be considered
# on the authorization boundary critical path
ATO_RELEVANT_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _get_connection(db_path=None):
    """Return a sqlite3 connection with Row factory."""
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = get_connection(db_path=str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _log_audit(conn, project_id, event_type, action, details):
    """Append-only audit trail entry."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                project_id,
                event_type,
                "icdev-passive-cve-watcher",
                action,
                json.dumps(details) if isinstance(details, dict) else str(details),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as exc:
        print(f"Warning: audit log failed: {exc}", file=sys.stderr)


def _log_watch(conn, project_id, audit_trail_id, cve_id, component, triage_id, skipped, source_event_type):
    """Append-only entry in cve_passive_watch_log."""
    conn.execute(
        """INSERT INTO cve_passive_watch_log
           (audit_trail_id, project_id, cve_id, component, triage_id,
            skipped, source_event_type, processed_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            audit_trail_id,
            project_id,
            cve_id,
            component,
            triage_id,
            1 if skipped else 0,
            source_event_type,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# ATO relevance scoring
# ---------------------------------------------------------------------------


def _get_ato_impact_radius(conn, project_id, component):
    """Compute the downstream impact radius for a component using the dependency graph.

    Used to tag CVEs as ATO-relevant: a component with many downstream dependents
    sits on the authorization boundary critical path — a vulnerability there poses
    systemic risk to the ATO posture.

    Performs an in-memory BFS over supply_chain_dependencies (no re-scan of the
    component itself — purely a graph traversal of existing edges).

    Args:
        conn: Active DB connection.
        project_id: Project identifier.
        component: Component name to score.

    Returns:
        int: Number of downstream dependents (0 if not in graph).
    """
    rows = conn.execute(
        """SELECT source_type, source_id, target_type, target_id
           FROM supply_chain_dependencies
           WHERE project_id = %s""",
        (project_id,),
    ).fetchall()

    if not rows:
        return 0

    # Build backward adjacency (target -> [sources that depend on it])
    backward = {}
    all_nodes = set()
    for r in rows:
        src = f"{r['source_type']}:{r['source_id']}"
        tgt = f"{r['target_type']}:{r['target_id']}"
        backward.setdefault(tgt, []).append(src)
        all_nodes.add(src)
        all_nodes.add(tgt)

    # Locate the component node using multiple key formats
    start_key = None
    for prefix in ("component", "package", "vendor", "system", "project"):
        candidate = f"{prefix}:{component}"
        if candidate in all_nodes:
            start_key = candidate
            break

    if start_key is None:
        return 0

    # BFS downstream count (how many things depend on this component)
    seen = {start_key}
    queue = deque([start_key])
    count = 0
    while queue:
        current = queue.popleft()
        for neighbor in backward.get(current, []):
            if neighbor not in seen:
                seen.add(neighbor)
                count += 1
                queue.append(neighbor)
    return count


# ---------------------------------------------------------------------------
# CVE extraction helpers
# ---------------------------------------------------------------------------


def _severity_from_cvss(cvss_score):
    """Map a CVSS numeric score to a severity label."""
    score = float(cvss_score)
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    return "low"


def _extract_cves_from_entry(row):
    """Extract CVE signals from an audit_trail row.

    Checks (in order):
    1. details JSON for explicit cve_id / cve_ids / vulnerabilities fields
    2. Regex scan of the details blob and action text

    Returns a list of dicts:
      {cve_id, component, cvss_score, severity, description}
    """
    results = {}

    # Parse details JSON
    raw_details = row["details"] or "{}"
    if isinstance(raw_details, str):
        try:
            details = json.loads(raw_details)
        except (json.JSONDecodeError, TypeError):
            details = {}
    else:
        details = raw_details if isinstance(raw_details, dict) else {}

    action_text = row["action"] or ""

    def _add(cve_id, component=None, cvss=None, sev=None, desc=None):
        cid = cve_id.upper()
        if cid not in results:
            results[cid] = {
                "cve_id": cid,
                "component": component or details.get("component") or details.get("package") or "unknown",
                "cvss_score": float(cvss) if cvss is not None else float(details.get("cvss_score", 5.0)),
                "severity": sev or details.get("severity"),
                "description": desc or details.get("description") or action_text[:200],
            }
            # Infer severity from CVSS if not provided
            if not results[cid]["severity"]:
                results[cid]["severity"] = _severity_from_cvss(results[cid]["cvss_score"])

    # --- Explicit fields ---
    if "cve_id" in details:
        _add(
            details["cve_id"],
            component=details.get("component") or details.get("package_name"),
            cvss=details.get("cvss_score"),
            sev=details.get("severity"),
            desc=details.get("description"),
        )

    if "cve_ids" in details and isinstance(details["cve_ids"], list):
        for cid in details["cve_ids"]:
            _add(str(cid))

    if "vulnerabilities" in details and isinstance(details["vulnerabilities"], list):
        for vuln in details["vulnerabilities"]:
            if isinstance(vuln, dict) and "cve_id" in vuln:
                _add(
                    vuln["cve_id"],
                    component=vuln.get("component") or vuln.get("package"),
                    cvss=vuln.get("cvss_score"),
                    sev=vuln.get("severity"),
                    desc=vuln.get("description"),
                )

    # --- Regex fallback ---
    search_text = f"{action_text} {raw_details if isinstance(raw_details, str) else ''}"
    for match in CVE_PATTERN.finditer(search_text):
        _add(match.group())

    return list(results.values())


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def _get_high_watermark(conn, project_id):
    """Return the highest audit_trail_id already processed for this project."""
    row = conn.execute(
        "SELECT MAX(audit_trail_id) FROM cve_passive_watch_log WHERE project_id = %s",
        (project_id,),
    ).fetchone()
    return row[0] or 0


def _is_already_triaged(conn, project_id, cve_id, component):
    """Return True if this (project, cve_id, component) is already in cve_triage."""
    row = conn.execute(
        """SELECT id FROM cve_triage
           WHERE project_id = %s AND cve_id = %s AND package_name = %s""",
        (project_id, cve_id, component),
    ).fetchone()
    return row is not None


def _already_logged(conn, project_id, audit_trail_id, cve_id):
    """Return True if this (audit_trail_id, cve_id) pair was already processed."""
    row = conn.execute(
        """SELECT id FROM cve_passive_watch_log
           WHERE project_id = %s AND audit_trail_id = %s AND cve_id = %s""",
        (project_id, audit_trail_id, cve_id),
    ).fetchone()
    return row is not None


def watch_scan(project_id, since_id=None, db_path=None, auto_triage=True, ato_only=False):
    """Scan the audit_trail for unprocessed ATO-relevant CVE events.

    Reads immutable audit entries, extracts CVE identifiers, scores each
    discovered CVE's ATO relevance via dependency graph impact radius, and
    optionally auto-triages new discoveries without re-scanning.

    Args:
        project_id: Project identifier.
        since_id: Start scanning from this audit_trail.id (exclusive). If None,
                  uses the high-watermark from cve_passive_watch_log.
        db_path: Optional DB path override.
        auto_triage: If True, auto-triage discovered CVEs. Default True.
        ato_only: If True, only return CVEs tagged ato_relevant=True. Default False.

    Returns:
        dict with scanned, found, triaged, skipped, errors,
        ato_relevant_count, results list.
        Each result entry includes ato_relevant (bool) and ato_impact_radius (int).
    """
    conn = _get_connection(db_path)
    try:
        # Resolve high-watermark
        if since_id is None:
            since_id = _get_high_watermark(conn, project_id)

        # Build IN clause for ATO event types
        placeholders = ",".join(["%s"] * len(ATO_EVENT_TYPES))
        audit_rows = conn.execute(
            f"""SELECT id, project_id, event_type, actor, action, details, created_at
                FROM audit_trail
                WHERE id > %s
                  AND event_type IN ({placeholders})
                ORDER BY id ASC""",
            [since_id] + list(ATO_EVENT_TYPES),
        ).fetchall()

        scanned = len(audit_rows)
        triaged_count = 0
        skipped_count = 0
        error_count = 0
        ato_relevant_count = 0
        results = []

        for row in audit_rows:
            row_dict = dict(row)
            event_type = row_dict["event_type"]
            audit_id = row_dict["id"]

            # Extract CVEs from this audit entry
            cve_signals = _extract_cves_from_entry(row)
            if not cve_signals:
                continue

            for cve_info in cve_signals:
                cve_id = cve_info["cve_id"]
                component = cve_info["component"]
                cvss = cve_info["cvss_score"]
                severity = cve_info["severity"]
                description = cve_info["description"]

                # Skip if already logged for this audit entry
                if _already_logged(conn, project_id, audit_id, cve_id):
                    continue

                # Compute ATO relevance via dependency graph impact radius
                ato_radius = _get_ato_impact_radius(conn, project_id, component)
                ato_relevant = ato_radius >= ATO_RELEVANT_THRESHOLD

                if ato_relevant:
                    ato_relevant_count += 1

                # If ato_only mode, skip non-ATO-relevant CVEs silently
                if ato_only and not ato_relevant:
                    continue

                # If the source event is from a prior triage agent, skip auto-triage
                # but still log so watcher knows about it
                if event_type in SKIP_EVENT_TYPES:
                    _log_watch(conn, project_id, audit_id, cve_id, component, None, skipped=True, source_event_type=event_type)
                    skipped_count += 1
                    results.append({
                        "cve_id": cve_id,
                        "status": "skipped",
                        "reason": "event_type_excluded",
                        "source_audit_id": audit_id,
                        "source_event_type": event_type,
                        "ato_relevant": ato_relevant,
                        "ato_impact_radius": ato_radius,
                    })
                    continue

                # Check if already triaged by cve_triager
                if _is_already_triaged(conn, project_id, cve_id, component):
                    _log_watch(conn, project_id, audit_id, cve_id, component, None, skipped=True, source_event_type=event_type)
                    skipped_count += 1
                    results.append({
                        "cve_id": cve_id,
                        "status": "skipped",
                        "reason": "already_triaged",
                        "component": component,
                        "source_audit_id": audit_id,
                        "source_event_type": event_type,
                        "ato_relevant": ato_relevant,
                        "ato_impact_radius": ato_radius,
                    })
                    continue

                if not auto_triage:
                    # Detection-only mode: log without triaging
                    _log_watch(conn, project_id, audit_id, cve_id, component, None, skipped=True, source_event_type=event_type)
                    results.append({
                        "cve_id": cve_id,
                        "status": "detected",
                        "component": component,
                        "severity": severity,
                        "cvss_score": cvss,
                        "source_audit_id": audit_id,
                        "source_event_type": event_type,
                        "ato_relevant": ato_relevant,
                        "ato_impact_radius": ato_radius,
                    })
                    continue

                # Auto-triage: call cve_triager
                try:
                    triage_result = triage_cve(
                        project_id,
                        cve_id,
                        component,
                        cvss,
                        severity,
                        f"[Passive watcher] {description}",
                        db_path=db_path,
                    )
                    triage_id = triage_result["triage_id"]
                    blast_radius = triage_result["blast_radius"]

                    # Feed into dependency_graph blast-radius propagation
                    propagate_result = None
                    try:
                        propagate_result = propagate_impact(
                            project_id,
                            component,
                            "vulnerability",
                            severity,
                            db_path=db_path,
                        )
                    except Exception as prop_exc:
                        print(
                            f"Warning: propagate_impact failed for {cve_id}/{component}: {prop_exc}",
                            file=sys.stderr,
                        )

                    _log_watch(
                        conn, project_id, audit_id, cve_id, component,
                        triage_id, skipped=False, source_event_type=event_type
                    )
                    _log_audit(
                        conn,
                        project_id,
                        "cve_triaged",
                        f"Passive watcher auto-triaged {cve_id} from audit entry {audit_id}",
                        {
                            "cve_id": cve_id,
                            "triage_id": triage_id,
                            "source_audit_id": audit_id,
                            "source_event_type": event_type,
                            "blast_radius": blast_radius,
                            "propagated_blast_radius": propagate_result["blast_radius"] if propagate_result else 0,
                            "ato_relevant": ato_relevant,
                            "ato_impact_radius": ato_radius,
                        },
                    )

                    triaged_count += 1
                    results.append({
                        "cve_id": cve_id,
                        "status": "triaged",
                        "triage_id": triage_id,
                        "component": component,
                        "severity": severity,
                        "cvss_score": cvss,
                        "blast_radius": blast_radius,
                        "propagated_blast_radius": propagate_result["blast_radius"] if propagate_result else 0,
                        "source_audit_id": audit_id,
                        "source_event_type": event_type,
                        "sla_deadline": triage_result["sla_deadline"],
                        "ato_relevant": ato_relevant,
                        "ato_impact_radius": ato_radius,
                    })

                except Exception as exc:
                    error_count += 1
                    print(f"Warning: triage failed for {cve_id}: {exc}", file=sys.stderr)
                    results.append({
                        "cve_id": cve_id,
                        "status": "error",
                        "error": str(exc),
                        "source_audit_id": audit_id,
                        "source_event_type": event_type,
                        "ato_relevant": ato_relevant,
                        "ato_impact_radius": ato_radius,
                    })

        return {
            "project_id": project_id,
            "since_id": since_id,
            "scanned_entries": scanned,
            "cves_found": len(results),
            "triaged": triaged_count,
            "skipped": skipped_count,
            "errors": error_count,
            "ato_relevant_count": ato_relevant_count,
            "ato_threshold": ATO_RELEVANT_THRESHOLD,
            "results": results,
        }
    finally:
        conn.close()


def get_status(project_id, db_path=None):
    """Return watcher statistics for a project.

    Returns:
        dict with high_watermark, total_processed, total_triaged, total_skipped,
        recent_discoveries.
    """
    conn = _get_connection(db_path)
    try:
        watermark = _get_high_watermark(conn, project_id)

        stats = conn.execute(
            """SELECT
                COUNT(*) AS total_processed,
                SUM(CASE WHEN skipped = 0 THEN 1 ELSE 0 END) AS total_triaged,
                SUM(CASE WHEN skipped = 1 THEN 1 ELSE 0 END) AS total_skipped
               FROM cve_passive_watch_log
               WHERE project_id = %s""",
            (project_id,),
        ).fetchone()

        recent = conn.execute(
            """SELECT cve_id, component, triage_id, skipped, source_event_type, processed_at
               FROM cve_passive_watch_log
               WHERE project_id = %s
               ORDER BY id DESC
               LIMIT 10""",
            (project_id,),
        ).fetchall()

        # Also get the total audit entries scanned (regardless of CVE content)
        total_audit_entries = conn.execute(
            "SELECT COUNT(*) FROM audit_trail WHERE event_type IN ({})".format(
                ",".join(["%s"] * len(ATO_EVENT_TYPES))
            ),
            list(ATO_EVENT_TYPES),
        ).fetchone()[0]

        return {
            "project_id": project_id,
            "high_watermark_audit_id": watermark,
            "total_audit_entries_available": total_audit_entries,
            "total_processed": stats["total_processed"] or 0,
            "total_triaged": stats["total_triaged"] or 0,
            "total_skipped": stats["total_skipped"] or 0,
            "ato_relevant_threshold": ATO_RELEVANT_THRESHOLD,
            "recent_discoveries": [dict(r) for r in recent],
        }
    finally:
        conn.close()


def watch_continuous(project_id, interval_seconds=60, db_path=None, max_iterations=None, ato_only=False):
    """Poll the audit trail at regular intervals, yielding scan results.

    Args:
        project_id: Project identifier.
        interval_seconds: Polling interval in seconds (default 60).
        db_path: Optional DB path override.
        max_iterations: Stop after this many iterations (None = run forever).
        ato_only: If True, only yield results with ato_relevant CVEs.

    Yields:
        dict from watch_scan() after each poll.
    """
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        result = watch_scan(project_id, db_path=db_path, ato_only=ato_only)
        yield result
        iteration += 1
        if max_iterations is None or iteration < max_iterations:
            time.sleep(interval_seconds)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Passive CVE Watcher — ATO continuous monitoring via audit log streaming"
    )
    parser.add_argument("--project-id", required=True, help="Project identifier")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Scan mode
    parser.add_argument("--scan", action="store_true", help="One-shot scan of audit trail for CVEs")
    parser.add_argument(
        "--since-id",
        type=int,
        default=None,
        help="Scan audit entries with id > N (default: auto from high-watermark)",
    )
    parser.add_argument(
        "--no-triage",
        action="store_true",
        help="Detection-only mode — discover CVEs but do not auto-triage",
    )
    parser.add_argument(
        "--ato-only",
        action="store_true",
        help="Only report CVEs tagged ato_relevant=True (impact_radius >= threshold)",
    )

    # Gate mode (CI/CD): exit 1 if any critical/high ATO-relevant CVEs discovered
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Exit 1 if scan discovers critical or high ATO-relevant CVEs (CI gate)",
    )

    # Status mode
    parser.add_argument("--status", action="store_true", help="Show watcher statistics")

    # Continuous watch mode
    parser.add_argument("--watch", action="store_true", help="Continuous polling mode")
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Poll interval in seconds for --watch mode (default: 60)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Stop --watch after N iterations (default: run forever)",
    )

    args = parser.parse_args()

    try:
        if args.scan:
            result = watch_scan(
                args.project_id,
                since_id=args.since_id,
                auto_triage=not args.no_triage,
                ato_only=args.ato_only,
            )
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                _print_scan_human(result)

            # --gate: block CI if critical/high ATO-relevant CVEs were discovered
            if args.gate:
                blocking = [
                    r for r in result.get("results", [])
                    if r.get("ato_relevant")
                    and r.get("severity") in ("critical", "high")
                    and r.get("status") in ("triaged", "detected")
                ]
                if blocking:
                    severities = ", ".join(
                        f"{r['cve_id']}({r.get('severity','?')})" for r in blocking
                    )
                    print(
                        f"GATE FAIL: {len(blocking)} critical/high ATO-relevant CVE(s) discovered: {severities}",
                        file=sys.stderr,
                    )
                    sys.exit(1)

        elif args.status:
            result = get_status(args.project_id)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                _print_status_human(result)

        elif args.watch:
            print(
                f"[passive-watcher] Starting continuous watch for project '{args.project_id}' "
                f"(interval={args.interval}s, ato_only={args.ato_only})",
                file=sys.stderr,
            )
            for result in watch_continuous(
                args.project_id,
                interval_seconds=args.interval,
                max_iterations=args.max_iterations,
                ato_only=args.ato_only,
            ):
                if args.json:
                    print(json.dumps(result, indent=2), flush=True)
                else:
                    _print_scan_human(result)
                    print("---", flush=True)

        else:
            parser.print_help()
            sys.exit(0)

    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _print_scan_human(data):
    """Pretty-print scan result for human consumption."""
    print(f"Passive CVE Watcher — Scan Results [{data['project_id']}]")
    print(f"  Audit entries scanned: {data['scanned_entries']} (since audit id {data['since_id']})")
    print(f"  CVEs found:        {data['cves_found']}")
    print(f"  ATO-relevant:      {data.get('ato_relevant_count', 0)} (threshold: impact_radius >= {data.get('ato_threshold', ATO_RELEVANT_THRESHOLD)})")
    print(f"  Triaged:           {data['triaged']}")
    print(f"  Skipped:           {data['skipped']}")
    print(f"  Errors:            {data['errors']}")
    for r in data.get("results", []):
        status = r["status"].upper()
        cve_id = r.get("cve_id", "?")
        component = r.get("component", "?")
        sev = r.get("severity", "?")
        ato_tag = " [ATO]" if r.get("ato_relevant") else ""
        print(f"  [{status}] {cve_id} ({component}) sev={sev}{ato_tag}", end="")
        if r.get("ato_impact_radius") is not None:
            print(f" radius={r['ato_impact_radius']}", end="")
        if r.get("triage_id"):
            print(f" triage_id={r['triage_id']} blast={r.get('blast_radius', 0)}", end="")
        if r.get("reason"):
            print(f" reason={r['reason']}", end="")
        print()


def _print_status_human(data):
    """Pretty-print status result for human consumption."""
    print(f"Passive CVE Watcher — Status [{data['project_id']}]")
    print(f"  High-watermark audit id:     {data['high_watermark_audit_id']}")
    print(f"  Available ATO audit entries: {data['total_audit_entries_available']}")
    print(f"  Total processed:             {data['total_processed']}")
    print(f"  Total auto-triaged:          {data['total_triaged']}")
    print(f"  Total skipped:               {data['total_skipped']}")
    print(f"  ATO relevance threshold:     impact_radius >= {data.get('ato_relevant_threshold', ATO_RELEVANT_THRESHOLD)}")
    if data.get("recent_discoveries"):
        print("  Recent discoveries (last 10):")
        for r in data["recent_discoveries"]:
            status = "triaged" if not r["skipped"] else "skipped"
            print(
                f"    [{status.upper()}] {r['cve_id']} ({r.get('component', '?')}) "
                f"from={r.get('source_event_type', '?')} at={r.get('processed_at', '?')[:19]}"
            )


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
