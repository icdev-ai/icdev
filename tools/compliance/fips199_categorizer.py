#!/usr/bin/env python3
# CUI // SP-CTI
"""FIPS 199 Security Categorization Engine for ICDEV.

Implements FIPS Publication 199 security categorization by mapping information
types from NIST SP 800-60 Vol 2 to CIA impact levels, computing the high
watermark across all types, and storing results in the database. For IL6/SECRET
national security systems, applies CNSSI 1253 overlays.

Usage:
    python tools/compliance/fips199_categorizer.py --project-id proj-123 --list-catalog
    python tools/compliance/fips199_categorizer.py --project-id proj-123 --add-type D.1.1.1
    python tools/compliance/fips199_categorizer.py --project-id proj-123 --add-type D.2.1.1 --adjust-c Moderate
    python tools/compliance/fips199_categorizer.py --project-id proj-123 --categorize --json
    python tools/compliance/fips199_categorizer.py --project-id proj-123 --list-types --json
    python tools/compliance/fips199_categorizer.py --project-id proj-123 --gate
    python tools/compliance/fips199_categorizer.py --list-catalog --category D.1
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CATALOG_PATH = BASE_DIR / "context" / "compliance" / "nist_sp_800_60_types.json"
CNSSI_PATH = BASE_DIR / "context" / "compliance" / "cnssi_1253_overlay.json"

IMPACT_ORDER = {"N/A": 0, "Low": 1, "Moderate": 2, "High": 3}
IMPACT_REVERSE = {0: "N/A", 1: "Low", 2: "Moderate", 3: "High"}


# ---------------------------------------------------------------------------
# DB & Project helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Standard ICDEV DB connection with Row factory."""
    path = db_path or DB_PATH
    if not Path(path).exists():
        raise FileNotFoundError(
            f"Database not found at {path}. Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_project(conn, project_id):
    """Load project from DB. Raises ValueError if not found."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found")
    return dict(row)


# ---------------------------------------------------------------------------
# Catalog functions
# ---------------------------------------------------------------------------

def _load_catalog():
    """Load NIST SP 800-60 information type catalog."""
    if not CATALOG_PATH.exists():
        raise FileNotFoundError(f"Catalog not found at {CATALOG_PATH}")
    with open(CATALOG_PATH) as f:
        return json.load(f)


def _find_information_type(catalog, type_id):
    """Search catalog for an information type by ID (e.g., D.1.1.1).

    Returns dict with id, name, description, provisional_impact, special_factors.
    Also returns the category path (e.g., 'Services Delivery > Defense and
    National Security').
    Raises ValueError if not found.
    """
    for category in catalog.get("categories", []):
        for sub in category.get("subcategories", []):
            for it in sub.get("information_types", []):
                if it["id"] == type_id:
                    it["_category_path"] = f"{category['name']} > {sub['name']}"
                    it["_category_id"] = sub["id"]
                    return it
    raise ValueError(
        f"Information type '{type_id}' not found in SP 800-60 catalog"
    )


def list_catalog(category=None):
    """List available information types. Optional category filter (e.g., 'D.1', 'D.2')."""
    catalog = _load_catalog()
    result = []
    for cat in catalog.get("categories", []):
        if category and not cat["id"].startswith(category):
            continue
        for sub in cat.get("subcategories", []):
            if category and len(category) > 3 and not sub["id"].startswith(category):
                continue
            for it in sub.get("information_types", []):
                result.append({
                    "id": it["id"],
                    "name": it["name"],
                    "category": f"{cat['name']} > {sub['name']}",
                    "provisional_impact": it["provisional_impact"],
                    "special_factors": it.get("special_factors", []),
                })
    return result


# ---------------------------------------------------------------------------
# High watermark computation
# ---------------------------------------------------------------------------

def _compute_high_watermark(info_types):
    """Compute FIPS 199 high watermark across all assigned information types.

    For each CIA objective, takes max(adjusted or provisional impact) across all
    types. Overall categorization = max(C, I, A).
    N/A values are excluded from max computation.

    Returns:
        {"confidentiality": "Moderate", "integrity": "High",
         "availability": "Low", "overall": "High"}
    """
    max_c, max_i, max_a = 0, 0, 0
    for t in info_types:
        # Use adjusted if set, otherwise provisional
        c = t.get("adjusted_confidentiality") or t["provisional_confidentiality"]
        i = t.get("adjusted_integrity") or t["provisional_integrity"]
        a = t.get("adjusted_availability") or t["provisional_availability"]
        max_c = max(max_c, IMPACT_ORDER.get(c, 0))
        max_i = max(max_i, IMPACT_ORDER.get(i, 0))
        max_a = max(max_a, IMPACT_ORDER.get(a, 0))

    # Overall = max of all three (but at least Low if any types present)
    overall = max(max_c, max_i, max_a)
    if info_types and overall == 0:
        overall = 1  # At least Low if types are assigned

    return {
        "confidentiality": IMPACT_REVERSE.get(max_c, "Low"),
        "integrity": IMPACT_REVERSE.get(max_i, "Low"),
        "availability": IMPACT_REVERSE.get(max_a, "Low"),
        "overall": IMPACT_REVERSE.get(overall, "Low"),
    }


# ---------------------------------------------------------------------------
# CNSSI 1253 overlay
# ---------------------------------------------------------------------------

def _load_cnssi_overlay():
    """Load CNSSI 1253 overlay data."""
    if not CNSSI_PATH.exists():
        return {}
    with open(CNSSI_PATH) as f:
        return json.load(f)


def _apply_cnssi_1253(project, watermark):
    """For IL6/SECRET systems, apply CNSSI 1253 minimum CIA floors.

    Returns updated watermark dict with cnssi_applied flag and overlay_ids.
    """
    impact_level = project.get("impact_level", "IL5")
    if impact_level != "IL6":
        return {**watermark, "cnssi_applied": False, "overlay_ids": []}

    overlay = _load_cnssi_overlay()
    if not overlay:
        return {**watermark, "cnssi_applied": False, "overlay_ids": []}

    # Apply minimum CIA from default NSS minimum
    nss_min = overlay.get("cia_baseline", {}).get("default_nss_minimum", {})
    min_c = IMPACT_ORDER.get(nss_min.get("confidentiality", "High"), 3)
    min_i = IMPACT_ORDER.get(nss_min.get("integrity", "High"), 3)
    min_a = IMPACT_ORDER.get(nss_min.get("availability", "Moderate"), 2)

    elevated_c = max(IMPACT_ORDER.get(watermark["confidentiality"], 0), min_c)
    elevated_i = max(IMPACT_ORDER.get(watermark["integrity"], 0), min_i)
    elevated_a = max(IMPACT_ORDER.get(watermark["availability"], 0), min_a)
    overall = max(elevated_c, elevated_i, elevated_a)

    # Determine applicable overlays from IL6 mapping
    overlay_ids = []
    il6_map = overlay.get("impact_level_mapping", {}).get("IL6", {})
    default_overlay = il6_map.get("default_overlay")
    if default_overlay:
        overlay_ids.append(default_overlay)

    return {
        "confidentiality": IMPACT_REVERSE.get(elevated_c, "High"),
        "integrity": IMPACT_REVERSE.get(elevated_i, "High"),
        "availability": IMPACT_REVERSE.get(elevated_a, "Moderate"),
        "overall": IMPACT_REVERSE.get(overall, "High"),
        "cnssi_applied": True,
        "overlay_ids": overlay_ids,
    }


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def add_information_type(project_id, type_id, adjust_c=None, adjust_i=None,
                         adjust_a=None, adjustment_justification=None,
                         db_path=None):
    """Add an information type to a project's FIPS 199 profile."""
    catalog = _load_catalog()
    info_type = _find_information_type(catalog, type_id)

    # Validate adjustments
    valid_levels = {"Low", "Moderate", "High", None}
    for adj, name in [
        (adjust_c, "confidentiality"),
        (adjust_i, "integrity"),
        (adjust_a, "availability"),
    ]:
        if adj and adj not in valid_levels:
            raise ValueError(
                f"Invalid {name} adjustment: {adj}. Must be Low, Moderate, or High"
            )

    conn = _get_connection(db_path)
    try:
        _get_project(conn, project_id)  # Verify project exists
        prov = info_type["provisional_impact"]
        conn.execute(
            """INSERT OR REPLACE INTO project_information_types
            (project_id, information_type_id, information_type_name,
             information_type_category,
             provisional_confidentiality, provisional_integrity,
             provisional_availability,
             adjusted_confidentiality, adjusted_integrity, adjusted_availability,
             adjustment_justification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id, type_id, info_type["name"],
                info_type["_category_path"],
                prov.get("confidentiality", "Low"),
                prov.get("integrity", "Low"),
                prov.get("availability", "Low"),
                adjust_c, adjust_i, adjust_a,
                adjustment_justification,
            ),
        )
        conn.commit()
        return {
            "status": "added",
            "project_id": project_id,
            "information_type": {
                "id": type_id,
                "name": info_type["name"],
                "category": info_type["_category_path"],
                "provisional": prov,
                "adjusted": {
                    "confidentiality": adjust_c,
                    "integrity": adjust_i,
                    "availability": adjust_a,
                },
            },
        }
    finally:
        conn.close()


def remove_information_type(project_id, type_id, db_path=None):
    """Remove an information type from a project's profile."""
    conn = _get_connection(db_path)
    try:
        result = conn.execute(
            "DELETE FROM project_information_types "
            "WHERE project_id = ? AND information_type_id = ?",
            (project_id, type_id),
        )
        conn.commit()
        return {
            "status": "removed" if result.rowcount > 0 else "not_found",
            "project_id": project_id,
            "type_id": type_id,
        }
    finally:
        conn.close()


def list_information_types(project_id, db_path=None):
    """List all information types assigned to a project."""
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM project_information_types "
            "WHERE project_id = ? ORDER BY information_type_id",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_categorization(project_id, db_path=None):
    """Get the current FIPS 199 categorization for a project.

    Looks for approved first, then draft. Returns None if none found.
    """
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            """SELECT * FROM fips199_categorizations
            WHERE project_id = ? AND status IN ('approved', 'draft')
            ORDER BY CASE status WHEN 'approved' THEN 1 ELSE 2 END,
                     categorization_date DESC
            LIMIT 1""",
            (project_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main categorization function
# ---------------------------------------------------------------------------

def categorize_project(project_id, method="information_type", manual_c=None,
                       manual_i=None, manual_a=None, justification=None,
                       db_path=None):
    """Run FIPS 199 categorization for a project.

    Methods:
    - information_type: compute from assigned SP 800-60 types (high watermark)
    - manual: use provided C/I/A values
    - cnssi_1253: force CNSSI 1253 overlay (for IL6)
    """
    conn = _get_connection(db_path)
    try:
        project = _get_project(conn, project_id)
        now = datetime.now(tz=timezone.utc).isoformat()

        if method == "manual":
            if not all([manual_c, manual_i, manual_a]):
                raise ValueError(
                    "Manual method requires --manual-c, --manual-i, --manual-a"
                )
            for val, name in [
                (manual_c, "C"),
                (manual_i, "I"),
                (manual_a, "A"),
            ]:
                if val not in ("Low", "Moderate", "High"):
                    raise ValueError(f"Invalid {name} value: {val}")
            watermark = {
                "confidentiality": manual_c,
                "integrity": manual_i,
                "availability": manual_a,
                "overall": IMPACT_REVERSE[
                    max(
                        IMPACT_ORDER[manual_c],
                        IMPACT_ORDER[manual_i],
                        IMPACT_ORDER[manual_a],
                    )
                ],
            }
            info_types = []
        else:
            # Load assigned information types
            info_types = conn.execute(
                "SELECT * FROM project_information_types WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            info_types = [dict(r) for r in info_types]

            if not info_types:
                return {
                    "error": "No information types assigned. Use --add-type first.",
                    "project_id": project_id,
                    "gate_status": "FAIL",
                }

            watermark = _compute_high_watermark(info_types)

        # Apply CNSSI 1253 for IL6 or if explicitly requested
        cnssi_result = {"cnssi_applied": False, "overlay_ids": []}
        if method == "cnssi_1253" or project.get("impact_level") == "IL6":
            cnssi_result = _apply_cnssi_1253(project, watermark)
            if cnssi_result["cnssi_applied"]:
                watermark["confidentiality"] = cnssi_result["confidentiality"]
                watermark["integrity"] = cnssi_result["integrity"]
                watermark["availability"] = cnssi_result["availability"]
                watermark["overall"] = cnssi_result["overall"]

        baseline = watermark["overall"]

        # Supersede any existing draft/review categorizations
        conn.execute(
            """UPDATE fips199_categorizations SET status = 'superseded',
               updated_at = ?
            WHERE project_id = ? AND status IN ('draft', 'review')""",
            (now, project_id),
        )

        # Build info types summary
        types_summary = (
            json.dumps(
                [
                    {
                        "id": t["information_type_id"],
                        "name": t["information_type_name"],
                    }
                    for t in info_types
                ]
            )
            if info_types
            else None
        )

        # Insert new categorization
        conn.execute(
            """INSERT INTO fips199_categorizations
            (project_id, categorization_date, confidentiality_impact,
             integrity_impact, availability_impact, overall_categorization,
             categorization_method, justification, information_types_summary,
             cnssi_1253_applied, cnssi_overlay_ids, baseline_selected, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')""",
            (
                project_id, now,
                watermark["confidentiality"], watermark["integrity"],
                watermark["availability"], watermark["overall"],
                method, justification, types_summary,
                1 if cnssi_result["cnssi_applied"] else 0,
                json.dumps(cnssi_result.get("overlay_ids", [])),
                baseline,
            ),
        )

        cat_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Update project with categorization columns
        conn.execute(
            """UPDATE projects SET
            fips199_confidentiality = ?, fips199_integrity = ?,
            fips199_availability = ?, fips199_overall = ?,
            fips199_categorization_id = ?,
            nss_system = ?, updated_at = ?
            WHERE id = ?""",
            (
                watermark["confidentiality"], watermark["integrity"],
                watermark["availability"], watermark["overall"],
                cat_id,
                1 if cnssi_result["cnssi_applied"] else 0,
                now, project_id,
            ),
        )

        # Link information types to this categorization
        if info_types:
            conn.execute(
                "UPDATE project_information_types "
                "SET categorization_id = ? WHERE project_id = ?",
                (cat_id, project_id),
            )

        # Audit trail
        conn.execute(
            """INSERT INTO audit_trail
            (project_id, event_type, actor, action, details, classification)
            VALUES (?, 'fips199_categorized', 'icdev-compliance-engine', ?, ?,
                    'CUI')""",
            (
                project_id,
                f"FIPS 199 categorization: {watermark['overall']} "
                f"(C:{watermark['confidentiality']} "
                f"I:{watermark['integrity']} "
                f"A:{watermark['availability']})",
                json.dumps({
                    "method": method,
                    "categorization_id": cat_id,
                    "overall": watermark["overall"],
                    "baseline": baseline,
                    "cnssi_applied": cnssi_result["cnssi_applied"],
                    "info_types_count": len(info_types),
                }),
            ),
        )

        conn.commit()

        return {
            "project_id": project_id,
            "categorization_id": cat_id,
            "confidentiality": watermark["confidentiality"],
            "integrity": watermark["integrity"],
            "availability": watermark["availability"],
            "overall": watermark["overall"],
            "baseline": baseline,
            "cnssi_1253_applied": cnssi_result["cnssi_applied"],
            "cnssi_overlay_ids": cnssi_result.get("overlay_ids", []),
            "information_types_count": len(info_types),
            "method": method,
            "status": "draft",
            "gate_status": "PASS",
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Gate evaluation
# ---------------------------------------------------------------------------

def evaluate_gate(project_id, db_path=None):
    """Evaluate FIPS 199 gate.

    PASS if: categorization exists and is approved or draft.
    FAIL if: no categorization, or IL6 without CNSSI overlay.
    """
    conn = _get_connection(db_path)
    try:
        project = _get_project(conn, project_id)
        cat = conn.execute(
            """SELECT * FROM fips199_categorizations
            WHERE project_id = ? AND status IN ('approved', 'draft')
            ORDER BY categorization_date DESC LIMIT 1""",
            (project_id,),
        ).fetchone()

        findings = []
        if not cat:
            findings.append(
                "No FIPS 199 categorization exists for this project"
            )
        else:
            cat = dict(cat)
            if (
                project.get("impact_level") == "IL6"
                and not cat.get("cnssi_1253_applied")
            ):
                findings.append(
                    "IL6/SECRET system requires CNSSI 1253 overlay"
                )
            if cat.get("status") == "draft":
                findings.append(
                    "Categorization is still in draft status (not approved)"
                )
            info_count = conn.execute(
                "SELECT COUNT(*) FROM project_information_types "
                "WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
            if (
                info_count == 0
                and cat.get("categorization_method") == "information_type"
            ):
                findings.append("No information types assigned")

        passed = (
            len(
                [
                    f
                    for f in findings
                    if "No FIPS 199" in f or "CNSSI 1253" in f
                ]
            )
            == 0
        )
        return {
            "gate": "fips199",
            "project_id": project_id,
            "passed": passed,
            "status": "PASS" if passed else "FAIL",
            "findings": findings,
            "categorization": dict(cat) if cat else None,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="FIPS 199 Security Categorization Engine"
    )
    parser.add_argument("--project-id", help="Project ID")
    parser.add_argument(
        "--add-type", help="Add information type by SP 800-60 ID"
    )
    parser.add_argument(
        "--remove-type", help="Remove information type by SP 800-60 ID"
    )
    parser.add_argument(
        "--adjust-c",
        choices=["Low", "Moderate", "High"],
        help="Adjusted confidentiality",
    )
    parser.add_argument(
        "--adjust-i",
        choices=["Low", "Moderate", "High"],
        help="Adjusted integrity",
    )
    parser.add_argument(
        "--adjust-a",
        choices=["Low", "Moderate", "High"],
        help="Adjusted availability",
    )
    parser.add_argument(
        "--adjustment-justification", help="Justification for adjustment"
    )
    parser.add_argument(
        "--categorize", action="store_true", help="Run categorization"
    )
    parser.add_argument(
        "--method",
        default="information_type",
        choices=["information_type", "manual", "cnssi_1253"],
    )
    parser.add_argument(
        "--manual-c", choices=["Low", "Moderate", "High"]
    )
    parser.add_argument(
        "--manual-i", choices=["Low", "Moderate", "High"]
    )
    parser.add_argument(
        "--manual-a", choices=["Low", "Moderate", "High"]
    )
    parser.add_argument("--justification", help="Categorization justification")
    parser.add_argument(
        "--list-types",
        action="store_true",
        help="List project information types",
    )
    parser.add_argument(
        "--list-catalog",
        action="store_true",
        help="List SP 800-60 catalog",
    )
    parser.add_argument(
        "--category", help="Filter catalog (D.1, D.2, D.3)"
    )
    parser.add_argument(
        "--gate", action="store_true", help="Evaluate FIPS 199 gate"
    )
    parser.add_argument(
        "--json", action="store_true", help="JSON output"
    )
    parser.add_argument("--db-path", type=Path, default=DB_PATH)
    args = parser.parse_args()

    try:
        if args.list_catalog:
            result = list_catalog(args.category)
            if args.json:
                print(
                    json.dumps(
                        {"catalog": result, "count": len(result)}, indent=2
                    )
                )
            else:
                print(
                    f"NIST SP 800-60 Information Types ({len(result)} types):"
                )
                print("-" * 80)
                for t in result:
                    prov = t["provisional_impact"]
                    print(
                        f"  {t['id']:12s} "
                        f"{t['name'][:50]:50s} "
                        f"C:{prov['confidentiality']:8s} "
                        f"I:{prov['integrity']:8s} "
                        f"A:{prov['availability']:8s}"
                    )
                    if t.get("special_factors"):
                        print(
                            f"               Special: "
                            f"{', '.join(t['special_factors'])}"
                        )
            return

        if not args.project_id:
            parser.error("--project-id is required for this operation")

        if args.add_type:
            result = add_information_type(
                args.project_id,
                args.add_type,
                adjust_c=args.adjust_c,
                adjust_i=args.adjust_i,
                adjust_a=args.adjust_a,
                adjustment_justification=args.adjustment_justification,
                db_path=args.db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                it = result["information_type"]
                print(f"Added: {it['id']} — {it['name']}")
                prov = it["provisional"]
                print(
                    f"  Provisional: C:{prov['confidentiality']} "
                    f"I:{prov['integrity']} A:{prov['availability']}"
                )
                adj = it["adjusted"]
                if any(v for v in adj.values()):
                    print(
                        f"  Adjusted:    "
                        f"C:{adj['confidentiality'] or '-'} "
                        f"I:{adj['integrity'] or '-'} "
                        f"A:{adj['availability'] or '-'}"
                    )

        elif args.remove_type:
            result = remove_information_type(
                args.project_id, args.remove_type, db_path=args.db_path
            )
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                status_label = (
                    "Removed"
                    if result["status"] == "removed"
                    else "Not found"
                )
                print(f"{status_label}: {args.remove_type}")

        elif args.list_types:
            result = list_information_types(
                args.project_id, db_path=args.db_path
            )
            if args.json:
                print(
                    json.dumps(
                        {
                            "project_id": args.project_id,
                            "information_types": result,
                            "count": len(result),
                        },
                        indent=2,
                        default=str,
                    )
                )
            else:
                print(
                    f"Information types for {args.project_id} "
                    f"({len(result)} types):"
                )
                for t in result:
                    c = (
                        t.get("adjusted_confidentiality")
                        or t["provisional_confidentiality"]
                    )
                    i = (
                        t.get("adjusted_integrity")
                        or t["provisional_integrity"]
                    )
                    a = (
                        t.get("adjusted_availability")
                        or t["provisional_availability"]
                    )
                    adj_flag = (
                        " [adjusted]"
                        if any(
                            [
                                t.get("adjusted_confidentiality"),
                                t.get("adjusted_integrity"),
                                t.get("adjusted_availability"),
                            ]
                        )
                        else ""
                    )
                    print(
                        f"  {t['information_type_id']:12s} "
                        f"{t['information_type_name'][:40]:40s} "
                        f"C:{c:8s} I:{i:8s} A:{a:8s}{adj_flag}"
                    )

        elif args.categorize:
            result = categorize_project(
                args.project_id,
                method=args.method,
                manual_c=args.manual_c,
                manual_i=args.manual_i,
                manual_a=args.manual_a,
                justification=args.justification,
                db_path=args.db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                if "error" in result:
                    print(f"Error: {result['error']}")
                    sys.exit(1)
                print(
                    f"FIPS 199 Security Categorization — {args.project_id}"
                )
                print("=" * 60)
                print(f"  Confidentiality:  {result['confidentiality']}")
                print(f"  Integrity:        {result['integrity']}")
                print(f"  Availability:     {result['availability']}")
                print(f"  Overall:          {result['overall']}")
                print(f"  Baseline:         {result['baseline']}")
                print(f"  Method:           {result['method']}")
                print(f"  Info Types:       {result['information_types_count']}")
                if result.get("cnssi_1253_applied"):
                    overlay_list = ", ".join(
                        result.get("cnssi_overlay_ids", [])
                    )
                    print(
                        f"  CNSSI 1253:       Applied "
                        f"(overlays: {overlay_list})"
                    )
                print(f"  Gate:             {result['gate_status']}")

        elif args.gate:
            result = evaluate_gate(
                args.project_id, db_path=args.db_path
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"FIPS 199 Gate: {result['status']}")
                if result["findings"]:
                    for f in result["findings"]:
                        print(f"  - {f}")
                if not result["passed"]:
                    sys.exit(1)

        else:
            # Default: show current categorization
            result = get_categorization(
                args.project_id, db_path=args.db_path
            )
            if result:
                if args.json:
                    print(json.dumps(result, indent=2, default=str))
                else:
                    print(
                        f"Current categorization for {args.project_id}:"
                    )
                    print(
                        f"  C:{result['confidentiality_impact']} "
                        f"I:{result['integrity_impact']} "
                        f"A:{result['availability_impact']}"
                    )
                    print(
                        f"  Overall: {result['overall_categorization']} "
                        f"| Baseline: {result['baseline_selected']}"
                    )
                    print(
                        f"  Status: {result['status']} "
                        f"| Method: {result['categorization_method']}"
                    )
            else:
                print(
                    f"No FIPS 199 categorization found for "
                    f"{args.project_id}"
                )
                if args.json:
                    print(
                        json.dumps(
                            {
                                "project_id": args.project_id,
                                "categorization": None,
                            },
                            indent=2,
                        )
                    )

    except (ValueError, FileNotFoundError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
