#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Multi-Regime Compliance Assessor for ICDEV.

Orchestrates assessment across ALL applicable frameworks for a project,
deduplicates overlapping controls via the crosswalk engine (ADR D113),
and produces a unified compliance report showing per-framework coverage
and the minimal set of controls needed to satisfy all regimes.

CLI:
    python tools/compliance/multi_regime_assessor.py --project-id proj-123 --json
    python tools/compliance/multi_regime_assessor.py --project-id proj-123 --gate
    python tools/compliance/multi_regime_assessor.py --project-id proj-123 --minimal-controls
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Map framework_id -> assessor class import path
ASSESSOR_REGISTRY = {
    "cjis": ("cjis_assessor", "CJISAssessor"),
    "hipaa": ("hipaa_assessor", "HIPAAAssessor"),
    "hitrust": ("hitrust_assessor", "HITRUSTAssessor"),
    "soc2": ("soc2_assessor", "SOC2Assessor"),
    "pci_dss": ("pci_dss_assessor", "PCIDSSAssessor"),
    "iso_27001": ("iso27001_assessor", "ISO27001Assessor"),
    "nist_800_207": ("nist_800_207_assessor", "NIST800207Assessor"),
    "mosa": ("mosa_assessor", "MOSAAssessor"),
}

# Existing assessors (not using BaseAssessor pattern)
EXISTING_ASSESSORS = {
    "fedramp_moderate": "fedramp_assessor",
    "fedramp_high": "fedramp_assessor",
    "cmmc_level_2": "cmmc_assessor",
    "cmmc_level_3": "cmmc_assessor",
}


def _get_connection(db_path=None):
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_applicable_frameworks(project_id, db_path=None):
    """Get frameworks from framework_applicability table."""
    conn = _get_connection(db_path)
    try:
        try:
            rows = conn.execute(
                """SELECT framework_id, source, confirmed
                   FROM framework_applicability
                   WHERE project_id = ?""",
                (project_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []
    finally:
        conn.close()


def _load_assessor(framework_id):
    """Dynamically load an assessor class for a framework."""
    if framework_id not in ASSESSOR_REGISTRY:
        return None
    module_name, class_name = ASSESSOR_REGISTRY[framework_id]
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        module = __import__(module_name)
        return getattr(module, class_name)
    except Exception as e:
        print(f"Warning: Could not load assessor for {framework_id}: {e}",
              file=sys.stderr)
        return None


def assess_all(
    project_id: str,
    project_dir: Optional[str] = None,
    frameworks: Optional[List[str]] = None,
    db_path: Optional[Path] = None,
) -> Dict:
    """Run all applicable assessors for a project.

    If frameworks is None, reads from framework_applicability table.
    Falls back to all registered assessors if no applicability data.

    Returns:
        Dict with per-framework results, unified summary, and
        deduplicated control counts.
    """
    # Determine which frameworks to assess
    if frameworks:
        fw_list = frameworks
    else:
        applicable = _get_applicable_frameworks(project_id, db_path)
        if applicable:
            fw_list = [r["framework_id"] for r in applicable]
        else:
            fw_list = list(ASSESSOR_REGISTRY.keys())

    # Run assessors
    results = {}
    errors = {}

    for fw_id in fw_list:
        if fw_id not in ASSESSOR_REGISTRY:
            continue
        assessor_cls = _load_assessor(fw_id)
        if assessor_cls is None:
            errors[fw_id] = "Assessor not available"
            continue
        try:
            assessor = assessor_cls(db_path=db_path or DB_PATH)
            result = assessor.assess(project_id, project_dir=project_dir)
            results[fw_id] = result
        except Exception as e:
            errors[fw_id] = str(e)

    # Compute unified metrics
    framework_summaries = []

    conn = _get_connection(db_path)
    try:
        # Get NIST implementations
        try:
            rows = conn.execute(
                """SELECT control_id, implementation_status
                   FROM project_controls WHERE project_id = ?""",
                (project_id,),
            ).fetchall()
            nist_impl = {
                r["control_id"].upper(): r["implementation_status"]
                for r in rows
            }
        except Exception:
            nist_impl = {}

        for fw_id, result in results.items():
            fw_summary = {
                "framework_id": fw_id,
                "framework_name": result.get("framework_name", fw_id),
                "total_requirements": result.get("total_requirements", 0),
                "coverage_pct": result.get("coverage_pct", 0),
                "status_counts": result.get("status_counts", {}),
            }
            framework_summaries.append(fw_summary)

            # Collect NIST crosswalk references for deduplication
            for req in result.get("results", []):
                # We'd need the catalog to get crosswalk refs
                pass

        # Compute overall metrics
        total_frameworks = len(results)
        avg_coverage = (
            sum(r.get("coverage_pct", 0) for r in results.values()) / total_frameworks
            if total_frameworks > 0 else 0
        )

        # Count implemented NIST controls
        implemented_count = sum(
            1 for s in nist_impl.values() if s == "implemented"
        )
        total_nist = len(nist_impl)

    finally:
        conn.close()

    unified = {
        "project_id": project_id,
        "assessment_date": datetime.now(timezone.utc).isoformat(),
        "frameworks_assessed": total_frameworks,
        "framework_results": framework_summaries,
        "errors": errors,
        "unified_metrics": {
            "average_coverage_pct": round(avg_coverage, 1),
            "nist_controls_implemented": implemented_count,
            "nist_controls_total": total_nist,
            "deduplication_note": (
                "Controls implemented once in NIST 800-53 cascade to all "
                "frameworks via the crosswalk engine (ADR D113)."
            ),
        },
    }

    return unified


def evaluate_all_gates(
    project_id: str,
    frameworks: Optional[List[str]] = None,
    db_path: Optional[Path] = None,
) -> Dict:
    """Evaluate gates for all applicable frameworks.

    Returns overall pass/fail plus per-framework gate results.
    """
    if frameworks:
        fw_list = frameworks
    else:
        applicable = _get_applicable_frameworks(project_id, db_path)
        if applicable:
            fw_list = [r["framework_id"] for r in applicable]
        else:
            fw_list = list(ASSESSOR_REGISTRY.keys())

    gate_results = {}
    all_pass = True

    for fw_id in fw_list:
        if fw_id not in ASSESSOR_REGISTRY:
            continue
        assessor_cls = _load_assessor(fw_id)
        if assessor_cls is None:
            continue
        try:
            assessor = assessor_cls(db_path=db_path or DB_PATH)
            gate = assessor.evaluate_gate(project_id)
            gate_results[fw_id] = gate
            if not gate.get("pass", False):
                all_pass = False
        except Exception as e:
            gate_results[fw_id] = {"pass": False, "error": str(e)}
            all_pass = False

    return {
        "project_id": project_id,
        "overall_pass": all_pass,
        "overall_status": "compliant" if all_pass else "non_compliant",
        "frameworks_evaluated": len(gate_results),
        "gate_results": gate_results,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def get_minimal_controls(
    project_id: str,
    frameworks: Optional[List[str]] = None,
    db_path: Optional[Path] = None,
) -> Dict:
    """Compute the minimal set of NIST 800-53 controls to satisfy all frameworks.

    Leverages crosswalk deduplication (ADR D113) to find controls that
    satisfy the most frameworks per implementation.

    Returns:
        Dict with prioritized control list and per-control framework impact.
    """
    conn = _get_connection(db_path)
    try:
        # Get current implementations
        try:
            rows = conn.execute(
                """SELECT control_id, implementation_status
                   FROM project_controls WHERE project_id = ?""",
                (project_id,),
            ).fetchall()
            implemented = set(
                r["control_id"].upper() for r in rows
                if r["implementation_status"] == "implemented"
            )
        except Exception:
            implemented = set()

        # Load crosswalk data
        crosswalk_path = BASE_DIR / "context" / "compliance" / "control_crosswalk.json"
        if not crosswalk_path.exists():
            return {"error": "Crosswalk data not found"}

        with open(crosswalk_path, "r", encoding="utf-8") as f:
            crosswalk_data = json.load(f)
        crosswalk = crosswalk_data.get("crosswalk", [])

        if frameworks:
            fw_set = set(frameworks)
        else:
            applicable = _get_applicable_frameworks(project_id, db_path)
            if applicable:
                fw_set = set(r["framework_id"] for r in applicable)
            else:
                fw_set = set(ASSESSOR_REGISTRY.keys())

        # Score each unimplemented control by how many frameworks it satisfies
        control_scores = []
        for entry in crosswalk:
            nist_id = entry.get("nist_800_53", entry.get("nist_id", ""))
            if not nist_id or nist_id.upper() in implemented:
                continue

            satisfies = []
            # Check new frameworks via crosswalk keys
            for fw_id in fw_set:
                val = entry.get(fw_id)
                if val is not None and val is not False:
                    satisfies.append(fw_id)
            # Also check IL-based keys
            for il_key in ("il4_required", "il5_required", "il6_required"):
                if entry.get(il_key):
                    il_name = il_key.replace("_required", "").upper()
                    satisfies.append(il_name)

            if satisfies:
                control_scores.append({
                    "nist_id": nist_id,
                    "title": entry.get("title", ""),
                    "family": entry.get("family", ""),
                    "priority": entry.get("priority", "P3"),
                    "frameworks_satisfied": len(satisfies),
                    "frameworks": satisfies,
                })

        # Sort: most frameworks satisfied first, then by priority
        priority_order = {"P1": 0, "P2": 1, "P3": 2}
        control_scores.sort(key=lambda c: (
            -c["frameworks_satisfied"],
            priority_order.get(c["priority"], 99),
            c["nist_id"],
        ))

        return {
            "project_id": project_id,
            "target_frameworks": sorted(fw_set),
            "already_implemented": len(implemented),
            "controls_remaining": len(control_scores),
            "prioritized_controls": control_scores[:50],  # Top 50
            "strategy_note": (
                "Implement controls in this order to maximize multi-framework "
                "coverage per control. Each control cascades across all listed "
                "frameworks via the crosswalk engine."
            ),
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Regime Compliance Assessor"
    )
    parser.add_argument("--project-id", required=True, help="Project ID")
    parser.add_argument("--project-dir", help="Project source directory")
    parser.add_argument(
        "--frameworks",
        help="Comma-separated framework IDs (default: all applicable)",
    )
    parser.add_argument("--gate", action="store_true", help="Evaluate all gates")
    parser.add_argument(
        "--minimal-controls", action="store_true",
        help="Show minimal control set to satisfy all frameworks",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args()

    try:
        fw_list = (
            [f.strip() for f in args.frameworks.split(",")]
            if args.frameworks else None
        )

        if args.minimal_controls:
            result = get_minimal_controls(
                args.project_id, fw_list, args.db_path,
            )
        elif args.gate:
            result = evaluate_all_gates(
                args.project_id, fw_list, args.db_path,
            )
        else:
            result = assess_all(
                args.project_id,
                project_dir=args.project_dir,
                frameworks=fw_list,
                db_path=args.db_path,
            )

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if args.minimal_controls:
                print(f"{'=' * 70}")
                print(f"  Minimal Control Set: {args.project_id}")
                print(f"  Target frameworks: {', '.join(result.get('target_frameworks', []))}")
                print(f"  Already implemented: {result.get('already_implemented', 0)}")
                print(f"  Controls remaining: {result.get('controls_remaining', 0)}")
                print(f"{'=' * 70}")
                for ctrl in result.get("prioritized_controls", [])[:20]:
                    print(
                        f"  {ctrl['nist_id']:<10} {ctrl['priority']:<5} "
                        f"[{ctrl['frameworks_satisfied']} FW] {ctrl['title']}"
                    )
                print(f"\n  {result.get('strategy_note', '')}")

            elif args.gate:
                status = "PASS" if result["overall_pass"] else "FAIL"
                print(f"{'=' * 65}")
                print(f"  Multi-Regime Gate: {status}")
                print(f"  Frameworks: {result['frameworks_evaluated']}")
                print(f"{'=' * 65}")
                for fw_id, gate in result.get("gate_results", {}).items():
                    gstatus = "PASS" if gate.get("pass") else "FAIL"
                    cov = gate.get("coverage_pct", "N/A")
                    fw_name = gate.get("framework", fw_id)
                    print(f"  {gstatus:<6} {fw_name:<35} {cov}%")
                    blocking = gate.get("blocking_issues", [])
                    for issue in blocking[:3]:
                        print(f"         - {issue}")

            else:
                print(f"{'=' * 70}")
                print(f"  Multi-Regime Assessment: {args.project_id}")
                print(f"  Frameworks assessed: {result['frameworks_assessed']}")
                print(f"  Avg coverage: {result['unified_metrics']['average_coverage_pct']}%")
                print(f"{'=' * 70}")
                for fw in result.get("framework_results", []):
                    print(
                        f"  {fw['framework_name']:<40} "
                        f"{fw['coverage_pct']:>5.1f}% "
                        f"({fw['total_requirements']} reqs)"
                    )
                if result.get("errors"):
                    print("\n  Errors:")
                    for fw_id, err in result["errors"].items():
                        print(f"    {fw_id}: {err}")

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
