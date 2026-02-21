#!/usr/bin/env python3
# CUI // SP-CTI
"""Map project activities to NIST 800-53 controls.
Creates, retrieves, verifies, and generates control implementation matrices
for compliance tracking. Stores mappings in project_controls table of icdev.db."""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONTROLS_PATH = BASE_DIR / "context" / "compliance" / "nist_800_53.json"

# All 17 FIPS 200 minimum security requirement families
REQUIRED_FAMILIES = [
    "AC", "AT", "AU", "CA", "CM", "CP", "IA", "IR",
    "MA", "MP", "PE", "PL", "PS", "RA", "SA", "SC", "SI"
]

VALID_STATUSES = (
    "planned", "implemented", "partially_implemented",
    "not_applicable", "compensating"
)


def _get_connection(db_path=None):
    """Get a database connection."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_nist_controls():
    """Load NIST controls reference data."""
    if not CONTROLS_PATH.exists():
        return {}
    with open(CONTROLS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {c["id"]: c for c in data.get("controls", [])}


def _verify_project_exists(conn, project_id):
    """Verify that a project exists in the database."""
    row = conn.execute(
        "SELECT id, name FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found in database.")
    return dict(row)


def create_mapping(
    project_id,
    control_id,
    implementation_status="planned",
    description=None,
    responsible_role=None,
    evidence_path=None,
    db_path=None,
):
    """Create or update a control mapping for a project.
    Returns the mapping row ID."""
    if implementation_status not in VALID_STATUSES:
        raise ValueError(
            f"Invalid status '{implementation_status}'. Valid: {VALID_STATUSES}"
        )

    conn = _get_connection(db_path)
    try:
        _verify_project_exists(conn, project_id)

        # Upsert: update if exists, insert if not
        existing = conn.execute(
            "SELECT id FROM project_controls WHERE project_id = ? AND control_id = ?",
            (project_id, control_id.upper()),
        ).fetchone()

        now = datetime.now(timezone.utc).isoformat()

        if existing:
            conn.execute(
                """UPDATE project_controls
                   SET implementation_status = ?, implementation_description = ?,
                       responsible_role = ?, evidence_path = ?,
                       last_assessed = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    implementation_status, description,
                    responsible_role, evidence_path,
                    now, now, existing["id"],
                ),
            )
            conn.commit()
            row_id = existing["id"]
            print(f"Updated mapping: {project_id} -> {control_id} [{implementation_status}]")
        else:
            cursor = conn.execute(
                """INSERT INTO project_controls
                   (project_id, control_id, implementation_status,
                    implementation_description, responsible_role,
                    evidence_path, last_assessed, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id, control_id.upper(), implementation_status,
                    description, responsible_role, evidence_path,
                    now, now, now,
                ),
            )
            conn.commit()
            row_id = cursor.lastrowid
            print(f"Created mapping: {project_id} -> {control_id} [{implementation_status}]")

        return row_id
    finally:
        conn.close()


def get_mappings(project_id, family=None, status=None, db_path=None):
    """Get all control mappings for a project.
    Optionally filter by family or status. Returns list of dicts."""
    conn = _get_connection(db_path)
    try:
        _verify_project_exists(conn, project_id)

        query = "SELECT * FROM project_controls WHERE project_id = ?"
        params = [project_id]

        if family:
            query += " AND control_id LIKE ?"
            params.append(f"{family.upper()}-%")

        if status:
            query += " AND implementation_status = ?"
            params.append(status)

        query += " ORDER BY control_id"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def verify_mappings(project_id, db_path=None):
    """Verify that all required control families have mappings.
    Returns a verification report dict with pass/fail and details."""
    conn = _get_connection(db_path)
    try:
        _verify_project_exists(conn, project_id)

        nist_controls = _load_nist_controls()
        mappings = get_mappings(project_id, db_path=db_path)
        mapped_ids = {m["control_id"] for m in mappings}

        report = {
            "project_id": project_id,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "total_required": 0,
            "total_mapped": len(mappings),
            "families": {},
            "missing_controls": [],
            "unmapped_families": [],
            "overall_pass": True,
        }

        for family_code in REQUIRED_FAMILIES:
            family_controls = [
                cid for cid in nist_controls if cid.startswith(f"{family_code}-")
            ]
            mapped_in_family = [
                cid for cid in family_controls if cid in mapped_ids
            ]
            missing = [
                cid for cid in family_controls if cid not in mapped_ids
            ]

            report["total_required"] += len(family_controls)
            report["families"][family_code] = {
                "total": len(family_controls),
                "mapped": len(mapped_in_family),
                "missing": missing,
                "complete": len(missing) == 0,
            }

            if missing:
                report["missing_controls"].extend(missing)
                report["overall_pass"] = False

            if len(mapped_in_family) == 0 and len(family_controls) > 0:
                report["unmapped_families"].append(family_code)

        # Status breakdown
        status_counts = {}
        for m in mappings:
            s = m["implementation_status"]
            status_counts[s] = status_counts.get(s, 0) + 1
        report["status_breakdown"] = status_counts

        # Completeness percentage
        if report["total_required"] > 0:
            report["completeness_pct"] = round(
                (report["total_required"] - len(report["missing_controls"]))
                / report["total_required"] * 100, 1
            )
        else:
            report["completeness_pct"] = 0.0

        return report
    finally:
        conn.close()


def generate_matrix(project_id, output_path=None, db_path=None):
    """Generate a control implementation matrix document with CUI markings.
    Returns the path to the generated file."""
    conn = _get_connection(db_path)
    try:
        project = _verify_project_exists(conn, project_id)
    finally:
        conn.close()

    mappings = get_mappings(project_id, db_path=db_path)
    nist_controls = _load_nist_controls()

    # Load CUI config for banners
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from cui_marker import load_cui_config
        config = load_cui_config()
    except ImportError:
        config = {
            "document_header": (
                "////////////////////////////////////////////////////////////////////\n"
                "CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI\n"
                "Distribution: Distribution D -- Authorized DoD Personnel Only\n"
                "////////////////////////////////////////////////////////////////////"
            ),
            "document_footer": (
                "////////////////////////////////////////////////////////////////////\n"
                "CUI // SP-CTI | Department of Defense\n"
                "////////////////////////////////////////////////////////////////////"
            ),
        }

    header = config.get("document_header", "CUI // SP-CTI").strip()
    footer = config.get("document_footer", "CUI // SP-CTI").strip()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        header,
        "",
        "# Control Implementation Matrix",
        f"## Project: {project.get('name', project_id)}",
        "",
        f"**Project ID:** {project_id}",
        f"**Generated:** {now}",
        "**Framework:** NIST SP 800-53 Rev 5",
        "**Classification:** CUI // SP-CTI",
        "",
        "---",
        "",
        "## Summary",
        "",
    ]

    # Summary by status
    status_counts = {}
    for m in mappings:
        s = m["implementation_status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    lines.append("| Status | Count |")
    lines.append("|--------|-------|")
    for status in VALID_STATUSES:
        count = status_counts.get(status, 0)
        if count > 0:
            lines.append(f"| {status} | {count} |")
    lines.append(f"| **Total Mapped** | **{len(mappings)}** |")
    lines.append("")

    # Summary by family
    lines.append("## Coverage by Family")
    lines.append("")
    lines.append("| Family | Mapped | Status |")
    lines.append("|--------|--------|--------|")

    family_counts = {}
    for m in mappings:
        fam = m["control_id"].split("-")[0] if "-" in m["control_id"] else "??"
        family_counts.setdefault(fam, 0)
        family_counts[fam] += 1

    for fam in sorted(family_counts.keys()):
        nist_in_fam = sum(1 for cid in nist_controls if cid.startswith(f"{fam}-"))
        mapped = family_counts[fam]
        pct = round(mapped / nist_in_fam * 100) if nist_in_fam > 0 else 0
        status_str = "Complete" if mapped >= nist_in_fam else f"{pct}%"
        lines.append(f"| {fam} | {mapped}/{nist_in_fam} | {status_str} |")
    lines.append("")

    # Detailed control matrix
    lines.append("---")
    lines.append("")
    lines.append("## Detailed Control Implementations")
    lines.append("")

    # Group by family
    by_family = {}
    for m in mappings:
        fam = m["control_id"].split("-")[0] if "-" in m["control_id"] else "Other"
        by_family.setdefault(fam, []).append(m)

    for fam in sorted(by_family.keys()):
        lines.append(f"### Family: {fam}")
        lines.append("")
        lines.append("| Control ID | Title | Status | Description | Responsible |")
        lines.append("|------------|-------|--------|-------------|-------------|")

        for m in sorted(by_family[fam], key=lambda x: x["control_id"]):
            ctrl_ref = nist_controls.get(m["control_id"], {})
            title = ctrl_ref.get("title", "N/A")
            desc = (m.get("implementation_description") or "").replace("\n", " ")[:80]
            responsible = m.get("responsible_role") or "TBD"
            lines.append(
                f"| {m['control_id']} | {title} | {m['implementation_status']} | {desc} | {responsible} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(footer)
    lines.append("")

    content = "\n".join(lines)

    if output_path is None:
        # Default output to project compliance directory
        conn = _get_connection(db_path)
        try:
            row = conn.execute(
                "SELECT directory_path FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
            if row and row["directory_path"]:
                output_dir = Path(row["directory_path"]) / "compliance"
            else:
                output_dir = BASE_DIR / ".tmp" / "compliance"
        finally:
            conn.close()
    else:
        output_dir = Path(output_path).parent

    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"control_matrix_{project_id}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Control matrix generated: {output_file}")
    return str(output_file)


def _format_verification_report(report):
    """Format verification report for console output."""
    lines = [
        "=" * 60,
        "  Control Mapping Verification Report",
        "=" * 60,
        f"  Project:       {report['project_id']}",
        f"  Verified:      {report['verified_at']}",
        f"  Total Required: {report['total_required']}",
        f"  Total Mapped:  {report['total_mapped']}",
        f"  Completeness:  {report['completeness_pct']}%",
        f"  Overall:       {'PASS' if report['overall_pass'] else 'FAIL'}",
        "",
        "  Status Breakdown:",
    ]
    for status, count in report.get("status_breakdown", {}).items():
        lines.append(f"    {status}: {count}")

    lines.append("")
    lines.append("  Family Coverage:")
    for fam, info in sorted(report["families"].items()):
        check = "OK" if info["complete"] else "MISSING"
        lines.append(f"    {fam}: {info['mapped']}/{info['total']} [{check}]")
        if info["missing"]:
            lines.append(f"      Missing: {', '.join(info['missing'])}")

    if report["missing_controls"]:
        lines.append("")
        lines.append(f"  Total Missing Controls: {len(report['missing_controls'])}")

    lines.append("=" * 60)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Map project activities to NIST 800-53 controls"
    )
    parser.add_argument("--project", required=True, help="Project ID")
    parser.add_argument("--db", type=str, default=None, help="Database path")

    subparsers = parser.add_subparsers(dest="command")

    # create subcommand
    create_p = subparsers.add_parser("create", help="Create a control mapping")
    create_p.add_argument("--control-id", required=True, help="NIST control ID (e.g., SA-11)")
    create_p.add_argument(
        "--status", default="planned", choices=VALID_STATUSES,
        help="Implementation status"
    )
    create_p.add_argument("--description", help="Implementation description")
    create_p.add_argument("--responsible", help="Responsible role")
    create_p.add_argument("--evidence", help="Path to evidence artifact")

    # list subcommand
    list_p = subparsers.add_parser("list", help="List control mappings")
    list_p.add_argument("--family", help="Filter by family code")
    list_p.add_argument("--status", help="Filter by status")

    # verify subcommand
    subparsers.add_parser("verify", help="Verify control mapping completeness")

    # matrix subcommand
    matrix_p = subparsers.add_parser("matrix", help="Generate control matrix document")
    matrix_p.add_argument("--output", help="Output file path")

    # Also support --verify as a top-level flag for backwards compatibility
    parser.add_argument("--verify", action="store_true", help="Verify control mappings")
    parser.add_argument("--matrix", action="store_true", help="Generate control matrix")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()
    db_path = Path(args.db) if args.db else None

    try:
        # Handle top-level flags
        if args.verify or args.command == "verify":
            report = verify_mappings(args.project, db_path=db_path)
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                print(_format_verification_report(report))
            sys.exit(0 if report["overall_pass"] else 1)

        elif args.matrix or args.command == "matrix":
            output = getattr(args, "output", None)
            path = generate_matrix(args.project, output_path=output, db_path=db_path)
            print(f"Matrix generated: {path}")

        elif args.command == "create":
            row_id = create_mapping(
                project_id=args.project,
                control_id=args.control_id,
                implementation_status=args.status,
                description=args.description,
                responsible_role=args.responsible,
                evidence_path=args.evidence,
                db_path=db_path,
            )
            print(f"Mapping ID: {row_id}")

        elif args.command == "list":
            mappings = get_mappings(
                args.project, family=args.family, status=args.status, db_path=db_path
            )
            if args.json:
                print(json.dumps(mappings, indent=2, default=str))
            else:
                if not mappings:
                    print(f"No mappings found for project '{args.project}'.")
                else:
                    print(f"{'ID':<5} {'Control':<10} {'Status':<25} {'Description':<40}")
                    print("-" * 80)
                    for m in mappings:
                        desc = (m.get("implementation_description") or "")[:40]
                        print(f"{m['id']:<5} {m['control_id']:<10} {m['implementation_status']:<25} {desc}")
        else:
            parser.print_help()

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
