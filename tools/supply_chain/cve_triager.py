#!/usr/bin/env python3
# CUI // SP-CTI
"""CVE Triage with upstream/downstream blast radius analysis.

Triages CVEs by severity, auto-computes SLA deadlines, traces upstream and
downstream impact via the dependency graph, and checks SLA compliance.

Tables used:
  - cve_triage                (id INTEGER PK AUTOINCREMENT, project_id, cve_id,
        package_name, package_version, severity, cvss_score, exploitability,
        triage_decision, triage_rationale, upstream_impact, downstream_impact,
        sla_deadline, triaged_by, triaged_at, remediated_at)
  - supply_chain_dependencies (read for blast-radius computation)
  - isa_agreements            (read for ISA impact cross-reference)

CLI:
  python tools/supply_chain/cve_triager.py --project-id <id> --triage --cve-id CVE-... ...
  python tools/supply_chain/cve_triager.py --project-id <id> --pending --json
  python tools/supply_chain/cve_triager.py --project-id <id> --sla-check --json
  python tools/supply_chain/cve_triager.py --project-id <id> --propagate --triage-id <id> --json
  python tools/supply_chain/cve_triager.py --project-id <id> --update --triage-id <id> ...
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

SEVERITY_LEVELS = ("critical", "high", "medium", "low")
TRIAGE_DECISIONS = ("remediate", "mitigate", "accept_risk", "defer",
                    "false_positive", "not_applicable")
EXPLOITABILITY = ("active", "poc", "theoretical", "none_known")

# SLA windows in hours by severity
SLA_HOURS = {
    "critical": 24,
    "high": 72,
    "medium": 168,
    "low": 720,
}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Return a sqlite3 connection with Row factory."""
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _log_audit(conn, project_id, event_type, action, details):
    """Append-only audit trail entry."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (project_id, event_type, "icdev-supply-chain-agent", action,
             json.dumps(details) if isinstance(details, dict) else str(details),
             "CUI"),
        )
        conn.commit()
    except Exception as exc:
        print(f"Warning: audit log failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Dependency graph helpers (self-contained BFS, no cross-module import)
# ---------------------------------------------------------------------------

def _load_edges(conn, project_id):
    """Load all dependency edges for a project into forward/backward adjacency maps."""
    rows = conn.execute(
        """SELECT source_type, source_id, target_type, target_id, criticality
           FROM supply_chain_dependencies
           WHERE project_id = ?""",
        (project_id,),
    ).fetchall()

    forward = {}   # source -> [target, ...]
    backward = {}  # target -> [source, ...]
    for r in rows:
        src = f"{r['source_type']}:{r['source_id']}"
        tgt = f"{r['target_type']}:{r['target_id']}"
        forward.setdefault(src, []).append(tgt)
        backward.setdefault(tgt, []).append(src)
    return forward, backward


def _bfs_neighbors(adj, start_key):
    """BFS from start_key using given adjacency map. Returns list of reached nodes."""
    seen = {start_key}
    queue = deque([start_key])
    result = []
    while queue:
        current = queue.popleft()
        for neighbor in adj.get(current, []):
            if neighbor not in seen:
                seen.add(neighbor)
                result.append(neighbor)
                queue.append(neighbor)
    return result


def _compute_blast_radius(conn, project_id, component):
    """Compute upstream and downstream lists for a component.

    'upstream' = things this component depends on (follow forward edges from component).
    'downstream' = things that depend on this component (follow backward edges to component).

    Returns (upstream_list, downstream_list).
    """
    forward, backward = _load_edges(conn, project_id)

    # Try multiple key formats: the component might be stored as just a name
    # or as type:name
    possible_keys = [
        f"component:{component}",
        f"package:{component}",
        f"vendor:{component}",
        f"system:{component}",
        f"project:{component}",
        component,
    ]

    # Find matching key
    all_nodes = set(forward.keys()) | set(backward.keys())
    start_key = None
    for pk in possible_keys:
        if pk in all_nodes:
            start_key = pk
            break
    if start_key is None:
        # No edges found for this component -- empty graph traversal
        return [], []

    upstream = _bfs_neighbors(forward, start_key)
    downstream = _bfs_neighbors(backward, start_key)
    return upstream, downstream


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def triage_cve(project_id, cve_id, component, cvss_score, severity,
               description, package_version=None, exploitability=None,
               db_path=None):
    """Triage a new CVE with automatic blast-radius analysis.

    Inserts into cve_triage, auto-computes upstream/downstream impact via
    dependency graph, sets SLA deadline based on severity.

    Args:
        project_id: Project identifier.
        cve_id: CVE identifier (e.g. CVE-2025-1234).
        component: Affected package/component name.
        cvss_score: CVSS numeric score (0-10).
        severity: critical / high / medium / low.
        description: CVE description text.
        package_version: Optional affected version.
        exploitability: Optional exploitability level.
        db_path: Optional DB path override.

    Returns:
        dict with triage_id, cve_id, severity, blast_radius, sla_deadline,
        affected_upstream, affected_downstream.
    """
    if severity not in SEVERITY_LEVELS:
        raise ValueError(f"severity must be one of {SEVERITY_LEVELS}")
    if exploitability and exploitability not in EXPLOITABILITY:
        raise ValueError(f"exploitability must be one of {EXPLOITABILITY}")

    cvss_score = float(cvss_score)
    if cvss_score < 0 or cvss_score > 10:
        raise ValueError("cvss_score must be between 0 and 10")

    now = datetime.now(timezone.utc)
    sla_hours = SLA_HOURS.get(severity, 720)
    sla_deadline = (now + timedelta(hours=sla_hours)).isoformat()

    conn = _get_connection(db_path)
    try:
        # Compute upstream/downstream
        upstream, downstream = _compute_blast_radius(conn, project_id, component)
        blast_radius = len(downstream)

        upstream_json = json.dumps(upstream)
        downstream_json = json.dumps(downstream)

        conn.execute(
            """INSERT INTO cve_triage
               (project_id, cve_id, package_name, package_version,
                severity, cvss_score, exploitability,
                triage_decision, triage_rationale,
                upstream_impact, downstream_impact,
                sla_deadline, triaged_by, triaged_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (project_id, cve_id, component, package_version,
             severity, cvss_score, exploitability,
             None, description,
             upstream_json, downstream_json,
             sla_deadline, "icdev-supply-chain-agent", now.isoformat()),
        )
        conn.commit()

        triage_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        _log_audit(conn, project_id, "cve_triaged",
                   f"Triaged {cve_id} (severity={severity}, blast_radius={blast_radius})",
                   {"triage_id": triage_id, "cve_id": cve_id,
                    "severity": severity, "cvss_score": cvss_score,
                    "blast_radius": blast_radius})

        return {
            "triage_id": triage_id,
            "cve_id": cve_id,
            "component": component,
            "severity": severity,
            "cvss_score": cvss_score,
            "blast_radius": blast_radius,
            "sla_deadline": sla_deadline,
            "sla_hours": sla_hours,
            "affected_upstream": upstream,
            "affected_downstream": downstream,
        }
    finally:
        conn.close()


def update_triage(triage_id, status, remediation_plan=None, assigned_to=None,
                  db_path=None):
    """Update a CVE triage record.

    Args:
        triage_id: Integer row ID of the triage record.
        status: New triage decision (one of TRIAGE_DECISIONS).
        remediation_plan: Optional rationale/plan text.
        assigned_to: Optional assignee.
        db_path: Optional DB path override.

    Returns:
        dict with updated triage fields.
    """
    if status not in TRIAGE_DECISIONS:
        raise ValueError(f"status must be one of {TRIAGE_DECISIONS}")

    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM cve_triage WHERE id = ?", (triage_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Triage record '{triage_id}' not found.")

        d = dict(row)
        now = datetime.now(timezone.utc).isoformat()

        # Build update
        updates = ["triage_decision = ?"]
        params = [status]

        if remediation_plan is not None:
            updates.append("triage_rationale = ?")
            params.append(remediation_plan)
        if assigned_to is not None:
            updates.append("triaged_by = ?")
            params.append(assigned_to)

        # If remediated, set remediated_at
        if status in ("remediate", "false_positive", "not_applicable"):
            updates.append("remediated_at = ?")
            params.append(now)

        params.append(triage_id)
        conn.execute(
            f"UPDATE cve_triage SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()

        _log_audit(conn, d["project_id"], "cve_triaged",
                   f"Updated triage {triage_id} for {d['cve_id']}: {status}",
                   {"triage_id": triage_id, "decision": status})

        return {
            "triage_id": triage_id,
            "cve_id": d["cve_id"],
            "component": d["package_name"],
            "triage_decision": status,
            "remediation_plan": remediation_plan,
            "assigned_to": assigned_to,
            "updated_at": now,
        }
    finally:
        conn.close()


def get_pending(project_id, db_path=None):
    """List all CVEs not yet resolved (no triage_decision, or deferred).

    Returns:
        dict with project_id, pending_count, pending list.
    """
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM cve_triage
               WHERE project_id = ?
                 AND (triage_decision IS NULL
                      OR triage_decision IN ('defer', 'mitigate'))
               ORDER BY
                 CASE severity
                   WHEN 'critical' THEN 0
                   WHEN 'high' THEN 1
                   WHEN 'medium' THEN 2
                   WHEN 'low' THEN 3
                 END,
                 cvss_score DESC""",
            (project_id,),
        ).fetchall()

        pending = []
        for r in rows:
            d = dict(r)
            # Parse JSON fields
            for field in ("upstream_impact", "downstream_impact"):
                if d.get(field) and isinstance(d[field], str):
                    try:
                        d[field] = json.loads(d[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            pending.append(d)

        return {
            "project_id": project_id,
            "pending_count": len(pending),
            "pending": pending,
        }
    finally:
        conn.close()


def check_sla(project_id, db_path=None):
    """Check SLA compliance for all open CVEs.

    Returns:
        dict with total_open, sla_compliant, sla_overdue counts and
        overdue details broken down by severity.
    """
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM cve_triage
               WHERE project_id = ?
                 AND (triage_decision IS NULL
                      OR triage_decision IN ('defer', 'mitigate'))""",
            (project_id,),
        ).fetchall()

        now = datetime.now(timezone.utc)
        total_open = len(rows)
        sla_compliant = 0
        sla_overdue = 0
        overdue_critical = 0
        overdue_details = []

        for r in rows:
            d = dict(r)
            deadline_str = d.get("sla_deadline")
            if not deadline_str:
                # No deadline set -- compute from triaged_at
                triaged_str = d.get("triaged_at")
                if triaged_str:
                    try:
                        triaged_dt = datetime.fromisoformat(triaged_str)
                        hours = SLA_HOURS.get(d.get("severity", "low"), 720)
                        deadline_dt = triaged_dt + timedelta(hours=hours)
                        deadline_str = deadline_dt.isoformat()
                    except (ValueError, TypeError):
                        pass

            if deadline_str:
                try:
                    deadline_dt = datetime.fromisoformat(deadline_str)
                    if now > deadline_dt:
                        sla_overdue += 1
                        hours_overdue = round(
                            (now - deadline_dt).total_seconds() / 3600, 1)
                        if d.get("severity") == "critical":
                            overdue_critical += 1
                        overdue_details.append({
                            "triage_id": d["id"],
                            "cve_id": d["cve_id"],
                            "component": d["package_name"],
                            "severity": d.get("severity"),
                            "sla_deadline": deadline_str,
                            "hours_overdue": hours_overdue,
                        })
                    else:
                        sla_compliant += 1
                except (ValueError, TypeError):
                    sla_compliant += 1
            else:
                sla_compliant += 1

        return {
            "project_id": project_id,
            "total_open": total_open,
            "sla_compliant": sla_compliant,
            "sla_overdue": sla_overdue,
            "overdue_critical": overdue_critical,
            "overdue_details": overdue_details,
        }
    finally:
        conn.close()


def propagate_cve_impact(project_id, triage_id, db_path=None):
    """Propagate impact of a triaged CVE downstream, including ISA cross-reference.

    Looks up the CVE's component, traces downstream dependencies, and checks
    if any downstream components cross ISA boundaries.

    Returns:
        dict with cve_id, affected_systems, isa_impacts, recommendations.
    """
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM cve_triage WHERE id = ? AND project_id = ?",
            (triage_id, project_id),
        ).fetchone()
        if not row:
            raise ValueError(
                f"Triage record '{triage_id}' not found in project '{project_id}'.")

        d = dict(row)
        component = d["package_name"]
        cve_id = d["cve_id"]
        severity = d.get("severity", "medium")

        # Compute downstream
        _, downstream = _compute_blast_radius(conn, project_id, component)

        # Cross-reference with ISAs: check if any downstream component has
        # dependencies with an isa_id set
        isa_impacts = []
        if downstream:
            # Look for dependencies that reference an ISA
            dep_rows = conn.execute(
                """SELECT sd.*, ia.partner_system, ia.status AS isa_status,
                          ia.data_types_shared
                   FROM supply_chain_dependencies sd
                   LEFT JOIN isa_agreements ia ON sd.isa_id = ia.id
                   WHERE sd.project_id = ? AND sd.isa_id IS NOT NULL""",
                (project_id,),
            ).fetchall()

            downstream_set = set(downstream)
            for dr in dep_rows:
                dr_dict = dict(dr)
                src = f"{dr_dict['source_type']}:{dr_dict['source_id']}"
                tgt = f"{dr_dict['target_type']}:{dr_dict['target_id']}"
                if src in downstream_set or tgt in downstream_set:
                    isa_impacts.append({
                        "isa_id": dr_dict.get("isa_id"),
                        "partner_system": dr_dict.get("partner_system"),
                        "isa_status": dr_dict.get("isa_status"),
                        "affected_edge": f"{src} -> {tgt}",
                        "data_types_at_risk": dr_dict.get("data_types_shared"),
                    })

        # Build affected systems summary
        affected_systems = []
        for ds in downstream:
            affected_systems.append({
                "component": ds,
                "relationship": "downstream_dependent",
            })

        # Recommendations
        recommendations = []
        if severity == "critical":
            recommendations.append(
                "CRITICAL CVE: Notify all downstream system owners within "
                "4 hours per incident response SLA.")
        if isa_impacts:
            recommendations.append(
                f"{len(isa_impacts)} ISA boundary(ies) affected. Notify partner "
                "system ISSOs and document in POA&M.")
        if len(downstream) > 10:
            recommendations.append(
                "Large blast radius. Consider emergency change request and "
                "coordinated patching across dependent systems.")
        if not recommendations:
            recommendations.append(
                "Impact contained to direct component. Apply standard patch "
                "process per SLA timeline.")

        result = {
            "cve_id": cve_id,
            "triage_id": triage_id,
            "component": component,
            "severity": severity,
            "affected_systems": affected_systems,
            "blast_radius": len(downstream),
            "isa_impacts": isa_impacts,
            "recommendations": recommendations,
        }

        _log_audit(conn, project_id, "cve_impact_propagated",
                   f"Propagated impact for {cve_id} from {component}",
                   {"triage_id": triage_id, "blast_radius": len(downstream),
                    "isa_crossings": len(isa_impacts)})

        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="CVE Triage with Blast Radius Analysis (RICOAS)")
    parser.add_argument("--project-id", required=True,
                        help="Project identifier")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Triage a new CVE
    parser.add_argument("--triage", action="store_true",
                        help="Triage a new CVE")
    parser.add_argument("--cve-id", help="CVE identifier (e.g. CVE-2025-1234)")
    parser.add_argument("--component", help="Affected package/component name")
    parser.add_argument("--cvss", type=float, help="CVSS score (0-10)")
    parser.add_argument("--severity", choices=SEVERITY_LEVELS,
                        help="CVE severity")
    parser.add_argument("--description", help="CVE description")
    parser.add_argument("--version", help="Affected package version")
    parser.add_argument("--exploitability", choices=EXPLOITABILITY,
                        help="Exploitability level")

    # Update
    parser.add_argument("--update", action="store_true",
                        help="Update triage decision")
    parser.add_argument("--triage-id", type=int,
                        help="Triage record ID")
    parser.add_argument("--decision", choices=TRIAGE_DECISIONS,
                        help="Triage decision")
    parser.add_argument("--remediation-plan", help="Remediation plan text")
    parser.add_argument("--assigned-to", help="Assignee name")

    # Query
    parser.add_argument("--pending", action="store_true",
                        help="List pending CVEs")
    parser.add_argument("--sla-check", action="store_true",
                        help="Check SLA compliance")

    # Propagation
    parser.add_argument("--propagate", action="store_true",
                        help="Propagate CVE impact downstream")

    args = parser.parse_args()

    try:
        result = None

        if args.triage:
            if not all([args.cve_id, args.component, args.cvss is not None,
                        args.severity, args.description]):
                parser.error(
                    "--triage requires --cve-id, --component, --cvss, "
                    "--severity, --description")
            result = triage_cve(
                args.project_id, args.cve_id, args.component,
                args.cvss, args.severity, args.description,
                package_version=args.version,
                exploitability=args.exploitability)

        elif args.update:
            if not args.triage_id or not args.decision:
                parser.error("--update requires --triage-id and --decision")
            result = update_triage(
                args.triage_id, args.decision,
                remediation_plan=args.remediation_plan,
                assigned_to=args.assigned_to)

        elif args.pending:
            result = get_pending(args.project_id)

        elif args.sla_check:
            result = check_sla(args.project_id)

        elif args.propagate:
            if not args.triage_id:
                parser.error("--propagate requires --triage-id")
            result = propagate_cve_impact(
                args.project_id, args.triage_id)

        else:
            parser.print_help()
            sys.exit(0)

        if result is not None:
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                _print_human(result)

    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _print_human(data):
    """Pretty-print result dict for human consumption."""
    if "triage_id" in data and "blast_radius" in data and "sla_deadline" in data:
        print(f"CVE Triaged: {data['cve_id']}")
        print(f"  Triage ID: {data['triage_id']}")
        print(f"  Component: {data.get('component', 'N/A')}")
        print(f"  Severity: {data['severity']}  CVSS: {data.get('cvss_score', '?')}")
        print(f"  Blast Radius: {data['blast_radius']}")
        print(f"  SLA Deadline: {data['sla_deadline']} ({data.get('sla_hours', '?')}h)")
        if data.get("affected_upstream"):
            print(f"  Upstream ({len(data['affected_upstream'])}): "
                  f"{', '.join(data['affected_upstream'][:5])}")
        if data.get("affected_downstream"):
            print(f"  Downstream ({len(data['affected_downstream'])}): "
                  f"{', '.join(data['affected_downstream'][:5])}")

    elif "triage_decision" in data and "triage_id" in data:
        print(f"Triage Updated: {data.get('cve_id', 'N/A')}")
        print(f"  ID: {data['triage_id']}  Decision: {data['triage_decision']}")
        if data.get("remediation_plan"):
            print(f"  Plan: {data['remediation_plan']}")
        if data.get("assigned_to"):
            print(f"  Assigned: {data['assigned_to']}")

    elif "pending" in data:
        print(f"Pending CVEs for {data['project_id']}: {data['pending_count']}")
        for p in data["pending"]:
            print(f"  [{p.get('severity', '?').upper()}] {p.get('cve_id', '?')} "
                  f"- {p.get('package_name', '?')} "
                  f"(CVSS {p.get('cvss_score', '?')}) "
                  f"SLA: {p.get('sla_deadline', 'N/A')}")

    elif "sla_compliant" in data:
        print(f"SLA Check for {data['project_id']}")
        print(f"  Total Open: {data['total_open']}")
        print(f"  SLA Compliant: {data['sla_compliant']}")
        print(f"  SLA Overdue: {data['sla_overdue']}")
        print(f"  Overdue Critical: {data['overdue_critical']}")
        if data.get("overdue_details"):
            print("  Overdue Details:")
            for od in data["overdue_details"]:
                print(f"    {od['cve_id']} ({od['severity']}) - "
                      f"{od['component']} - {od['hours_overdue']}h overdue")

    elif "affected_systems" in data and "isa_impacts" in data:
        print(f"CVE Impact Propagation: {data['cve_id']}")
        print(f"  Component: {data.get('component', 'N/A')}")
        print(f"  Severity: {data.get('severity', '?')}")
        print(f"  Blast Radius: {data.get('blast_radius', 0)}")
        if data["affected_systems"]:
            print(f"  Affected Systems ({len(data['affected_systems'])}):")
            for s in data["affected_systems"][:10]:
                print(f"    - {s['component']}")
        if data["isa_impacts"]:
            print(f"  ISA Boundary Crossings ({len(data['isa_impacts'])}):")
            for i in data["isa_impacts"]:
                print(f"    - ISA {i.get('isa_id', '?')}: "
                      f"{i.get('partner_system', '?')} "
                      f"(status={i.get('isa_status', '?')})")
        print("  Recommendations:")
        for r in data.get("recommendations", []):
            print(f"    - {r}")

    else:
        print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
# CUI // SP-CTI
