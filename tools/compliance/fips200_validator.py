#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""FIPS 200 Minimum Security Requirements Validator for ICDEV.

Validates that a project satisfies all 17 FIPS 200 minimum security
requirement areas by checking NIST 800-53 control implementations
against the baseline determined by FIPS 199 categorization.

Usage:
    python tools/compliance/fips200_validator.py --project-id proj-123
    python tools/compliance/fips200_validator.py --project-id proj-123 --gate --json
    python tools/compliance/fips200_validator.py --project-id proj-123 --project-dir /path
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
FIPS200_PATH = BASE_DIR / "context" / "compliance" / "fips_200_areas.json"

# Impact level to baseline mapping
IL_BASELINE_MAP = {"IL2": "Low", "IL4": "Moderate", "IL5": "High", "IL6": "High"}


def _get_connection(db_path=None):
    """Standard ICDEV DB connection with Row factory."""
    path = db_path or DB_PATH
    if not Path(path).exists():
        raise FileNotFoundError(f"Database not found at {path}. Run: python tools/db/init_icdev_db.py")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_project(conn, project_id):
    """Load project from DB."""
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found")
    return dict(row)


def _load_fips200_areas():
    """Load the 17 requirement areas from fips_200_areas.json."""
    if not FIPS200_PATH.exists():
        raise FileNotFoundError(f"FIPS 200 areas not found at {FIPS200_PATH}")
    with open(FIPS200_PATH) as f:
        data = json.load(f)
    return data.get("requirement_areas", [])


def _get_fips199_baseline(conn, project_id):
    """Get the FIPS 199 baseline for this project.

    Priority:
    1. fips199_categorizations table (approved first, then draft)
    2. projects.fips199_overall column
    3. Impact level mapping (IL4->Moderate, IL5->High, etc.)
    4. Default: "Moderate"

    Returns: {"baseline": "Moderate", "source": "fips199_categorization"}
    """
    # Try fips199_categorizations table
    try:
        row = conn.execute("""SELECT overall_categorization, baseline_selected, status
            FROM fips199_categorizations WHERE project_id = ? AND status IN ('approved', 'draft')
            ORDER BY CASE status WHEN 'approved' THEN 1 ELSE 2 END,
                     categorization_date DESC LIMIT 1""", (project_id,)).fetchone()
        if row:
            baseline = row["baseline_selected"] or row["overall_categorization"]
            return {"baseline": baseline, "source": "fips199_categorization",
                    "status": row["status"]}
    except Exception:
        pass  # Table may not exist

    # Try projects.fips199_overall
    project = _get_project(conn, project_id)
    fips_overall = project.get("fips199_overall")
    if fips_overall:
        return {"baseline": fips_overall, "source": "projects_table"}

    # Fall back to impact level
    il = project.get("impact_level", "IL5")
    baseline = IL_BASELINE_MAP.get(il, "Moderate")
    return {"baseline": baseline, "source": f"impact_level_{il}"}


def _get_project_controls(conn, project_id):
    """Get all project_controls keyed by control_id.
    Returns dict: control_id -> {implementation_status, ...}"""
    rows = conn.execute(
        "SELECT * FROM project_controls WHERE project_id = ?", (project_id,)).fetchall()
    return {r["control_id"]: dict(r) for r in rows}


def validate_fips200(project_id, project_dir=None, gate=False, output_dir=None, db_path=None):
    """Run FIPS 200 validation across all 17 requirement areas.

    For each area:
    1. Get required controls for the project's baseline
    2. Check which are in project_controls
    3. Compute coverage metrics
    4. Store results in fips200_assessments table
    """
    conn = _get_connection(db_path)
    try:
        project = _get_project(conn, project_id)
        areas = _load_fips200_areas()
        baseline_info = _get_fips199_baseline(conn, project_id)
        baseline = baseline_info["baseline"].lower()  # "low", "moderate", "high"
        controls = _get_project_controls(conn, project_id)
        now = datetime.now(tz=timezone.utc).isoformat()

        area_results = []
        total_satisfied = 0
        total_partial = 0
        total_not_satisfied = 0
        total_required = 0
        total_implemented = 0

        for area in areas:
            required = area.get("minimum_controls", {}).get(baseline, [])
            total_required += len(required)

            mapped = []
            implemented = []
            planned = []
            not_applicable = []
            gap = []

            for ctrl_id in required:
                if ctrl_id in controls:
                    mapped.append(ctrl_id)
                    status = controls[ctrl_id].get("implementation_status", "planned")
                    if status == "implemented":
                        implemented.append(ctrl_id)
                    elif status == "not_applicable":
                        not_applicable.append(ctrl_id)
                    elif status in ("planned", "partially_implemented", "compensating"):
                        planned.append(ctrl_id)
                else:
                    gap.append(ctrl_id)

            total_implemented += len(implemented)
            coverage = (len(implemented) + len(not_applicable)) / len(required) * 100 if required else 100.0

            # Determine status
            if not required:
                status = "not_applicable"
            elif len(gap) == 0 and len(planned) == 0:
                status = "satisfied"
                total_satisfied += 1
            elif coverage >= 50:
                status = "partially_satisfied"
                total_partial += 1
            else:
                status = "not_satisfied"
                total_not_satisfied += 1

            area_result = {
                "requirement_area_id": area["id"],
                "requirement_area_name": area["name"],
                "family": area["family"],
                "baseline": baseline.capitalize(),
                "total_required": len(required),
                "mapped": len(mapped),
                "implemented": len(implemented),
                "planned": len(planned),
                "not_applicable": len(not_applicable),
                "gap_count": len(gap),
                "gap_controls": gap,
                "coverage_pct": round(coverage, 1),
                "status": status,
            }
            area_results.append(area_result)

            # Upsert into fips200_assessments
            conn.execute("""INSERT OR REPLACE INTO fips200_assessments
                (project_id, assessment_date, baseline, requirement_area_id,
                 requirement_area_name, family, total_required_controls,
                 mapped_controls, implemented_controls, planned_controls,
                 not_applicable_controls, coverage_pct, status, gap_controls, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (project_id, now, baseline.capitalize(), area["id"],
                 area["name"], area["family"], len(required),
                 len(mapped), len(implemented), len(planned),
                 len(not_applicable), round(coverage, 1), status,
                 json.dumps(gap), now))

        overall_coverage = (total_implemented / total_required * 100) if total_required else 0.0

        # Gate evaluation
        gate_passed = total_not_satisfied == 0
        gate_findings = []
        if total_not_satisfied > 0:
            failing = [a for a in area_results if a["status"] == "not_satisfied"]
            gate_findings.append(f"{total_not_satisfied} requirement area(s) not satisfied")
            for f in failing:
                gate_findings.append(f"  {f['family']} ({f['requirement_area_name']}): {f['gap_count']} gap controls")

        # Build gap report
        gap_report = {}
        for a in area_results:
            if a["gap_controls"]:
                gap_report[a["family"]] = {
                    "area": a["requirement_area_name"],
                    "gap_controls": a["gap_controls"],
                    "recommendation": f"Map and implement {len(a['gap_controls'])} {a['family']} controls for {baseline.capitalize()} baseline"
                }

        # Audit trail
        conn.execute("""INSERT INTO audit_trail
            (project_id, event_type, actor, action, details, classification)
            VALUES (?, 'fips200_assessed', 'icdev-compliance-engine', ?, ?, 'CUI')""",
            (project_id,
             f"FIPS 200 validation: {total_satisfied}/17 areas satisfied ({baseline.capitalize()} baseline)",
             json.dumps({
                 "baseline": baseline.capitalize(),
                 "baseline_source": baseline_info["source"],
                 "total_areas": len(areas),
                 "satisfied": total_satisfied,
                 "partially_satisfied": total_partial,
                 "not_satisfied": total_not_satisfied,
                 "overall_coverage_pct": round(overall_coverage, 1),
                 "gate_status": "PASS" if gate_passed else "FAIL",
             })))

        conn.commit()

        result = {
            "project_id": project_id,
            "baseline": baseline.capitalize(),
            "baseline_source": baseline_info["source"],
            "total_areas": len(areas),
            "areas_satisfied": total_satisfied,
            "areas_partially_satisfied": total_partial,
            "areas_not_satisfied": total_not_satisfied,
            "total_required_controls": total_required,
            "total_implemented_controls": total_implemented,
            "overall_coverage_pct": round(overall_coverage, 1),
            "areas": area_results,
            "gap_report": gap_report,
            "gate_result": {
                "passed": gate_passed,
                "status": "PASS" if gate_passed else "FAIL",
                "findings": gate_findings,
            },
        }

        return result
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="FIPS 200 Minimum Security Requirements Validator")
    parser.add_argument("--project-id", required=True, help="Project ID")
    parser.add_argument("--project-dir", help="Project directory for auto-checks")
    parser.add_argument("--gate", action="store_true", help="Evaluate FIPS 200 gate")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--output-dir", help="Output directory for report")
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    args = parser.parse_args()

    try:
        result = validate_fips200(
            args.project_id, project_dir=args.project_dir,
            gate=args.gate, output_dir=args.output_dir,
            db_path=args.db_path)

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"FIPS 200 Validation — {args.project_id}")
            print(f"Baseline: {result['baseline']} (source: {result['baseline_source']})")
            print("=" * 80)
            print(f"{'Area':<50} {'Family':<8} {'Status':<20} {'Coverage':>8}")
            print("-" * 80)
            for a in result["areas"]:
                status_icon = "PASS" if a["status"] == "satisfied" else "PARTIAL" if a["status"] == "partially_satisfied" else "FAIL" if a["status"] == "not_satisfied" else "N/A"
                print(f"{a['requirement_area_name']:<50} {a['family']:<8} {status_icon:<20} {a['coverage_pct']:>7.1f}%")
            print("-" * 80)
            print(f"Summary: {result['areas_satisfied']} satisfied, {result['areas_partially_satisfied']} partial, {result['areas_not_satisfied']} not satisfied")
            print(f"Overall coverage: {result['overall_coverage_pct']:.1f}% ({result['total_implemented_controls']}/{result['total_required_controls']} controls)")

            if result["gap_report"]:
                print(f"\nGap Report ({len(result['gap_report'])} areas with gaps):")
                for family, gap in result["gap_report"].items():
                    print(f"  {family} ({gap['area']}): {len(gap['gap_controls'])} missing — {', '.join(gap['gap_controls'][:5])}{'...' if len(gap['gap_controls']) > 5 else ''}")

            if args.gate:
                gr = result["gate_result"]
                print(f"\nGate: {gr['status']}")
                for f in gr["findings"]:
                    print(f"  {f}")
                if not gr["passed"]:
                    sys.exit(1)

    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
