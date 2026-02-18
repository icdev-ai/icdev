#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Compliance Framework Auto-Detection Engine.

Analyzes project metadata, data categories, and environment context to
recommend applicable compliance frameworks. Results are advisory only
(ADR D110) -- the customer confirms the final selection.

Uses declarative detection rules from data_type_framework_map.json
(ADR D115) -- add new rules without code changes.

CLI:
    python tools/compliance/compliance_detector.py --project-id proj-123 --json
    python tools/compliance/compliance_detector.py --project-id proj-123 --apply
    python tools/compliance/compliance_detector.py --project-id proj-123 --confirm
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
FRAMEWORK_MAP_PATH = BASE_DIR / "context" / "compliance" / "data_type_framework_map.json"
REGISTRY_PATH = BASE_DIR / "context" / "compliance" / "data_type_registry.json"

# Framework metadata for display
FRAMEWORK_DISPLAY = {
    "nist_800_53": "NIST SP 800-53 Rev 5",
    "fedramp_moderate": "FedRAMP Moderate",
    "fedramp_high": "FedRAMP High",
    "nist_800_171": "NIST SP 800-171 Rev 2",
    "cmmc_level_2": "CMMC Level 2",
    "cmmc_level_3": "CMMC Level 3",
    "cjis": "FBI CJIS Security Policy v5.9.4",
    "hipaa": "HIPAA Security Rule",
    "hitrust": "HITRUST CSF v11",
    "soc2": "SOC 2 Type II",
    "pci_dss": "PCI DSS v4.0",
    "iso_27001": "ISO/IEC 27001:2022",
    "iso_27017": "ISO/IEC 27017:2015",
    "iso_27018": "ISO/IEC 27018:2019",
    "iso_27701": "ISO/IEC 27701:2019",
    "irs_1075": "IRS Publication 1075",
    "cnssi_1253": "CNSSI 1253",
    "cisa_sbd": "CISA Secure by Design",
    "nist_800_207": "NIST SP 800-207 (Zero Trust Architecture)",
    "mosa": "DoD MOSA (10 U.S.C. §4401)",
}


def _get_connection(db_path=None):
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS framework_applicability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            framework_id TEXT NOT NULL,
            source TEXT DEFAULT 'auto_detected'
                CHECK(source IN ('auto_detected', 'manual', 'inherited')),
            confirmed INTEGER DEFAULT 0,
            confirmed_by TEXT,
            confirmed_at TIMESTAMP,
            detection_confidence REAL DEFAULT 0.0,
            detection_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, framework_id)
        );

        CREATE TABLE IF NOT EXISTS compliance_detection_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            detection_date TEXT DEFAULT (datetime('now')),
            data_categories TEXT,
            detected_frameworks TEXT,
            required_frameworks TEXT,
            recommended_frameworks TEXT,
            rules_matched TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_fa_project
            ON framework_applicability(project_id);
    """)
    conn.commit()


def _load_framework_map():
    if not FRAMEWORK_MAP_PATH.exists():
        return {"detection_rules": [], "framework_registry": {}}
    with open(FRAMEWORK_MAP_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_registry():
    if not REGISTRY_PATH.exists():
        return []
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("data_types", [])


def detect_frameworks(
    project_id: str,
    db_path: Optional[Path] = None,
) -> Dict:
    """Detect applicable compliance frameworks for a project.

    Evaluates detection rules from data_type_framework_map.json against:
    1. Project data categories (from data_classifications table)
    2. Project metadata (impact_level, classification, description)
    3. Sector indicators from project description

    Returns:
        Dict with required_frameworks, recommended_frameworks, rules_matched,
        and per-framework detection confidence.
    """
    conn = _get_connection(db_path)
    try:
        _ensure_tables(conn)
        fw_map = _load_framework_map()
        rules = fw_map.get("detection_rules", [])

        # Get project info
        row = conn.execute(
            """SELECT id, name, description, classification, impact_level,
                      target_frameworks, type
               FROM projects WHERE id = ?""",
            (project_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Project '{project_id}' not found.")
        project = dict(row)

        # Get data categories
        try:
            cat_rows = conn.execute(
                """SELECT data_category, subcategory
                   FROM data_classifications WHERE project_id = ?""",
                (project_id,),
            ).fetchall()
            data_categories = set(r["data_category"] for r in cat_rows)
        except Exception:
            data_categories = set()

        # If no explicit categories, infer from classification
        cls = (project.get("classification") or "").upper()
        il = (project.get("impact_level") or "").upper()
        desc = (project.get("description") or "").lower()

        if cls in ("CUI",) and "CUI" not in data_categories:
            data_categories.add("CUI")
        if cls == "SECRET" and "SECRET" not in data_categories:
            data_categories.add("SECRET")

        # Evaluate detection rules
        required: Dict[str, Dict] = {}
        recommended: Dict[str, Dict] = {}
        rules_matched: List[Dict] = []

        for rule in rules:
            rule_id = rule.get("rule_id", "")
            condition = rule.get("condition", "")
            matched = False

            # Simple condition evaluation
            if "data_category ==" in condition:
                cat = condition.split("==")[1].strip().strip("'\"")
                if cat in data_categories:
                    matched = True

            elif "impact_level in" in condition:
                levels_str = condition.split("in")[1].strip().strip("()")
                levels = [l.strip().strip("'\"") for l in levels_str.split(",")]
                if il in levels:
                    matched = True

            elif "impact_level ==" in condition:
                level = condition.split("==")[1].strip().strip("'\"")
                if il == level:
                    matched = True

            elif "sector ==" in condition:
                sector = condition.split("==")[1].strip().strip("'\"")
                sector_keywords = {
                    "healthcare": ["health", "medical", "clinical", "patient", "hipaa", "ehr"],
                    "financial": ["financial", "banking", "payment", "fintech", "trading"],
                    "law_enforcement": ["law enforcement", "police", "criminal", "cjis", "ncic"],
                    "international": ["international", "global", "nato", "allied"],
                }
                keywords = sector_keywords.get(sector, [])
                if any(kw in desc for kw in keywords):
                    matched = True

            elif " AND " in condition:
                # Compound condition: both data categories present
                parts = condition.split(" AND ")
                sub_matched = True
                for part in parts:
                    part = part.strip()
                    if "data_category ==" in part:
                        cat = part.split("==")[1].strip().strip("'\"")
                        if cat not in data_categories:
                            sub_matched = False
                            break
                matched = sub_matched

            if matched:
                rules_matched.append({
                    "rule_id": rule_id,
                    "condition": condition,
                    "description": rule.get("description", ""),
                })

                for fw in rule.get("required_frameworks", []):
                    if fw not in required:
                        required[fw] = {
                            "framework_id": fw,
                            "name": FRAMEWORK_DISPLAY.get(fw, fw),
                            "source": "required",
                            "confidence": 0.9,
                            "matched_rules": [],
                        }
                    required[fw]["matched_rules"].append(rule_id)

                for fw in rule.get("recommended_frameworks", []):
                    if fw not in required and fw not in recommended:
                        recommended[fw] = {
                            "framework_id": fw,
                            "name": FRAMEWORK_DISPLAY.get(fw, fw),
                            "source": "recommended",
                            "confidence": 0.6,
                            "matched_rules": [],
                        }
                    if fw in recommended:
                        recommended[fw]["matched_rules"].append(rule_id)

        # Boost confidence for frameworks matched by multiple rules
        for fw_dict in (required, recommended):
            for fw_id, fw_data in fw_dict.items():
                n_rules = len(fw_data["matched_rules"])
                if n_rules > 1:
                    fw_data["confidence"] = min(
                        fw_data["confidence"] + (n_rules - 1) * 0.05, 0.99
                    )

        # Compute minimal control set
        total_required = list(required.values())
        total_recommended = list(recommended.values())

        result = {
            "project_id": project_id,
            "data_categories": sorted(data_categories),
            "impact_level": il,
            "classification": cls,
            "required_frameworks": total_required,
            "recommended_frameworks": total_recommended,
            "rules_matched": rules_matched,
            "total_frameworks_detected": len(required) + len(recommended),
            "advisory_note": (
                "Detection is advisory (ADR D110). Run with --apply to store "
                "results, then --confirm to mark as reviewed."
            ),
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Log detection
        conn.execute(
            """INSERT INTO compliance_detection_log
               (project_id, data_categories, detected_frameworks,
                required_frameworks, recommended_frameworks, rules_matched)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                json.dumps(sorted(data_categories)),
                json.dumps(sorted(set(
                    list(required.keys()) + list(recommended.keys())
                ))),
                json.dumps(sorted(required.keys())),
                json.dumps(sorted(recommended.keys())),
                json.dumps([r["rule_id"] for r in rules_matched]),
            ),
        )
        conn.commit()

        return result
    finally:
        conn.close()


def apply_detection(
    project_id: str,
    db_path: Optional[Path] = None,
) -> Dict:
    """Store detected frameworks in framework_applicability table.

    Does NOT confirm them -- they remain unconfirmed until --confirm.
    """
    detection = detect_frameworks(project_id, db_path)
    conn = _get_connection(db_path)
    try:
        _ensure_tables(conn)
        applied = []
        now = datetime.utcnow().isoformat()

        for fw in detection["required_frameworks"]:
            conn.execute(
                """INSERT OR REPLACE INTO framework_applicability
                   (project_id, framework_id, source, confirmed,
                    detection_confidence, detection_reason, created_at)
                   VALUES (?, ?, 'auto_detected', 0, ?, ?, ?)""",
                (
                    project_id, fw["framework_id"],
                    fw["confidence"],
                    f"Required: rules {', '.join(fw['matched_rules'])}",
                    now,
                ),
            )
            applied.append(fw["framework_id"])

        for fw in detection["recommended_frameworks"]:
            conn.execute(
                """INSERT OR IGNORE INTO framework_applicability
                   (project_id, framework_id, source, confirmed,
                    detection_confidence, detection_reason, created_at)
                   VALUES (?, ?, 'auto_detected', 0, ?, ?, ?)""",
                (
                    project_id, fw["framework_id"],
                    fw["confidence"],
                    f"Recommended: rules {', '.join(fw['matched_rules'])}",
                    now,
                ),
            )
            applied.append(fw["framework_id"])

        conn.commit()

        return {
            "project_id": project_id,
            "applied_frameworks": applied,
            "status": "applied_unconfirmed",
            "next_step": "Run --confirm to mark as reviewed.",
        }
    finally:
        conn.close()


def confirm_frameworks(
    project_id: str,
    confirmed_by: str = "ISSO",
    db_path: Optional[Path] = None,
) -> Dict:
    """Mark all detected frameworks as confirmed."""
    conn = _get_connection(db_path)
    try:
        _ensure_tables(conn)
        now = datetime.utcnow().isoformat()
        conn.execute(
            """UPDATE framework_applicability
               SET confirmed = 1, confirmed_by = ?, confirmed_at = ?
               WHERE project_id = ? AND confirmed = 0""",
            (confirmed_by, now, project_id),
        )
        conn.commit()

        rows = conn.execute(
            """SELECT framework_id, source, confirmed, detection_confidence
               FROM framework_applicability WHERE project_id = ?""",
            (project_id,),
        ).fetchall()

        return {
            "project_id": project_id,
            "confirmed_by": confirmed_by,
            "frameworks": [dict(r) for r in rows],
            "total_confirmed": sum(1 for r in rows if r["confirmed"]),
        }
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Compliance Framework Auto-Detection Engine"
    )
    parser.add_argument("--project-id", required=True, help="Project ID")
    parser.add_argument(
        "--apply", action="store_true",
        help="Store detection results in DB (unconfirmed)",
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="Confirm all detected frameworks",
    )
    parser.add_argument("--confirmed-by", default="ISSO", help="Confirmer name")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", type=Path, default=None)
    args = parser.parse_args()

    try:
        if args.confirm:
            result = confirm_frameworks(
                args.project_id, args.confirmed_by, args.db_path,
            )
        elif args.apply:
            result = apply_detection(args.project_id, args.db_path)
        else:
            result = detect_frameworks(args.project_id, args.db_path)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if args.confirm:
                print(f"Confirmed {result['total_confirmed']} frameworks for {args.project_id}")
                for fw in result["frameworks"]:
                    status = "confirmed" if fw["confirmed"] else "unconfirmed"
                    print(f"  {fw['framework_id']}: {status}")
            elif args.apply:
                print(f"Applied {len(result['applied_frameworks'])} frameworks to {args.project_id}")
                for fw_id in result["applied_frameworks"]:
                    print(f"  {FRAMEWORK_DISPLAY.get(fw_id, fw_id)}")
                print(f"\n{result['next_step']}")
            else:
                print(f"{'=' * 65}")
                print(f"  Compliance Detection: {args.project_id}")
                print(f"  Data categories: {', '.join(result['data_categories'])}")
                print(f"  Impact level: {result['impact_level'] or 'not set'}")
                print(f"{'=' * 65}")
                print(f"\n  Required frameworks ({len(result['required_frameworks'])}):")
                for fw in result["required_frameworks"]:
                    print(f"    [{fw['confidence']:.0%}] {fw['name']}")
                print(f"\n  Recommended frameworks ({len(result['recommended_frameworks'])}):")
                for fw in result["recommended_frameworks"]:
                    print(f"    [{fw['confidence']:.0%}] {fw['name']}")
                print(f"\n  Rules matched: {len(result['rules_matched'])}")
                for rule in result["rules_matched"]:
                    print(f"    {rule['rule_id']}: {rule['description']}")
                print(f"\n  {result['advisory_note']}")

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
