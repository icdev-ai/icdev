#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
"""AI-powered gap and ambiguity detection for requirements.

Checks requirements against NIST control coverage, standard DoD requirement
categories, and known ambiguity patterns.

Usage:
    python tools/requirements/gap_detector.py --session-id sess-abc --check-security --json
    python tools/requirements/gap_detector.py --session-id sess-abc --check-compliance --json
    python tools/requirements/gap_detector.py --session-id sess-abc --report --json
"""

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

try:
    from tools.audit.audit_logger import log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def log_event(**kwargs): return -1


def _get_connection(db_path=None):
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_gap_patterns():
    """Load gap patterns from context file."""
    path = BASE_DIR / "context" / "requirements" / "gap_patterns.json"
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f).get("gap_patterns", [])
    return []


def detect_gaps(session_id: str, checks: dict = None, db_path=None) -> dict:
    """Run gap detection on all requirements in a session.

    Args:
        session_id: Intake session ID
        checks: Dict of which checks to run {security, compliance, testability, interfaces, data}
        db_path: Optional DB path override
    """
    if checks is None:
        checks = {"security": True, "compliance": True, "testability": True,
                   "interfaces": True, "data": True}

    conn = _get_connection(db_path)
    session = conn.execute(
        "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not session:
        conn.close()
        raise ValueError(f"Session '{session_id}' not found.")

    session_data = dict(session)
    reqs = conn.execute(
        "SELECT * FROM intake_requirements WHERE session_id = ?",
        (session_id,),
    ).fetchall()
    reqs = [dict(r) for r in reqs]

    gaps = []
    patterns = _load_gap_patterns()

    # --- Security gap check ---
    if checks.get("security"):
        all_text = " ".join(r.get("raw_text", "") for r in reqs).lower()
        for pattern in patterns:
            if pattern.get("category") != "security":
                continue
            absent_kws = pattern.get("detection_keywords_absent", [])
            if absent_kws and not any(kw.lower() in all_text for kw in absent_kws):
                gaps.append({
                    "gap_id": pattern["id"],
                    "category": "security",
                    "severity": pattern.get("severity", "high"),
                    "name": pattern["name"],
                    "description": pattern["description"],
                    "affected_controls": pattern.get("nist_controls", []),
                    "recommendation": pattern.get("recommendation", ""),
                })

    # --- Data gap check ---
    if checks.get("data"):
        all_text = " ".join(r.get("raw_text", "") for r in reqs).lower()
        for pattern in patterns:
            if pattern.get("category") != "data":
                continue
            absent_kws = pattern.get("detection_keywords_absent", [])
            if absent_kws and not any(kw.lower() in all_text for kw in absent_kws):
                gaps.append({
                    "gap_id": pattern["id"],
                    "category": "data",
                    "severity": pattern.get("severity", "high"),
                    "name": pattern["name"],
                    "description": pattern["description"],
                    "affected_controls": pattern.get("nist_controls", []),
                    "recommendation": pattern.get("recommendation", ""),
                })

    # --- Interface gap check ---
    if checks.get("interfaces"):
        all_text = " ".join(r.get("raw_text", "") for r in reqs).lower()
        for pattern in patterns:
            if pattern.get("category") != "interface":
                continue
            present_kws = pattern.get("detection_keywords_present", [])
            absent_kws = pattern.get("detection_keywords_absent", [])
            has_present = not present_kws or any(kw.lower() in all_text for kw in present_kws)
            has_absent = absent_kws and not any(kw.lower() in all_text for kw in absent_kws)
            if has_present and has_absent:
                gaps.append({
                    "gap_id": pattern["id"],
                    "category": "interface",
                    "severity": pattern.get("severity", "high"),
                    "name": pattern["name"],
                    "description": pattern["description"],
                    "affected_controls": pattern.get("nist_controls", []),
                    "recommendation": pattern.get("recommendation", ""),
                })

    # --- Operational gap check ---
    all_text = " ".join(r.get("raw_text", "") for r in reqs).lower()
    for pattern in patterns:
        if pattern.get("category") != "operational":
            continue
        absent_kws = pattern.get("detection_keywords_absent", [])
        if absent_kws and not any(kw.lower() in all_text for kw in absent_kws):
            gaps.append({
                "gap_id": pattern["id"],
                "category": "operational",
                "severity": pattern.get("severity", "high"),
                "name": pattern["name"],
                "description": pattern["description"],
                "affected_controls": pattern.get("nist_controls", []),
                "recommendation": pattern.get("recommendation", ""),
            })

    # --- Testability gap check ---
    if checks.get("testability"):
        untestable = [r for r in reqs if not r.get("acceptance_criteria")]
        if untestable:
            gaps.append({
                "gap_id": "GAP-TEST-001",
                "category": "testability",
                "severity": "medium",
                "name": "Requirements without acceptance criteria",
                "description": f"{len(untestable)} of {len(reqs)} requirements lack BDD acceptance criteria",
                "affected_controls": [],
                "recommendation": "Generate Given/When/Then criteria for each requirement",
                "affected_requirements": [r["id"] for r in untestable[:10]],
            })

    # --- Compliance coverage check ---
    if checks.get("compliance"):
        impact_level = session_data.get("impact_level", "IL5")
        sec_reqs = sum(1 for r in reqs if r["requirement_type"] in ("security", "compliance"))
        if sec_reqs < 3 and impact_level in ("IL4", "IL5", "IL6"):
            gaps.append({
                "gap_id": "GAP-COMP-001",
                "category": "compliance",
                "severity": "critical",
                "name": "Insufficient security/compliance requirements",
                "description": f"Only {sec_reqs} security/compliance requirements for {impact_level} system. "
                               f"Minimum 5 expected covering authentication, encryption, audit, access control, and incident response.",
                "affected_controls": ["AC-2", "AU-2", "IA-2", "SC-8", "IR-4"],
                "recommendation": "Add requirements for CAC auth, FIPS encryption, audit logging, RBAC, and incident response",
            })

    # Update session
    conn.execute(
        "UPDATE intake_sessions SET gap_count = ?, updated_at = ? WHERE id = ?",
        (len(gaps), datetime.utcnow().isoformat(), session_id),
    )
    conn.commit()
    conn.close()

    if _HAS_AUDIT and gaps:
        log_event(
            event_type="gap_detected",
            actor="icdev-requirements-analyst",
            action=f"Detected {len(gaps)} gap(s) in session {session_id}",
            project_id=session_data.get("project_id"),
            details={"session_id": session_id, "gap_count": len(gaps)},
        )

    summary = {
        "total_gaps": len(gaps),
        "critical": sum(1 for g in gaps if g["severity"] == "critical"),
        "high": sum(1 for g in gaps if g["severity"] == "high"),
        "medium": sum(1 for g in gaps if g["severity"] == "medium"),
        "low": sum(1 for g in gaps if g["severity"] == "low"),
        "categories_with_gaps": list(set(g["category"] for g in gaps)),
    }

    return {
        "status": "ok",
        "session_id": session_id,
        "gaps": gaps,
        "summary": summary,
    }


def main():
    parser = argparse.ArgumentParser(description="ICDEV Gap Detector")
    parser.add_argument("--session-id", required=True, help="Intake session ID")
    parser.add_argument("--check-security", action="store_true")
    parser.add_argument("--check-compliance", action="store_true")
    parser.add_argument("--check-testability", action="store_true")
    parser.add_argument("--check-interfaces", action="store_true")
    parser.add_argument("--check-data", action="store_true")
    parser.add_argument("--report", action="store_true", help="Full gap analysis")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    checks = {}
    if args.report:
        checks = {"security": True, "compliance": True, "testability": True,
                   "interfaces": True, "data": True}
    else:
        if args.check_security: checks["security"] = True
        if args.check_compliance: checks["compliance"] = True
        if args.check_testability: checks["testability"] = True
        if args.check_interfaces: checks["interfaces"] = True
        if args.check_data: checks["data"] = True
        if not checks:
            checks = {"security": True, "compliance": True, "testability": True,
                       "interfaces": True, "data": True}

    try:
        result = detect_gaps(args.session_id, checks)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Gaps found: {result['summary']['total_gaps']}")
            for gap in result["gaps"]:
                print(f"  [{gap['severity'].upper()}] {gap['name']}")
                print(f"    {gap['description']}")
    except (ValueError, FileNotFoundError) as e:
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"Error: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
