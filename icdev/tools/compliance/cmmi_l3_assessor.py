#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""CMMI Level 3 Assessor — lightweight PA-level evaluator.

Reads assessment data from the cmmi_assessments table (or cmmi_level3_assessments
as fallback) and/or a YAML rubric at args/cmmi_l3_rubric.yaml, evaluates five
core Level-3 process areas (REQM, PP, CM, MA, PPQA), and returns a structured
result dict.

Usage:
    python tools/compliance/cmmi_l3_assessor.py
    python tools/compliance/cmmi_l3_assessor.py --project-id proj-123
    python tools/compliance/cmmi_l3_assessor.py --project-id proj-123 --json
    python tools/compliance/cmmi_l3_assessor.py --rubric args/cmmi_l3_rubric.yaml
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Built-in rubric — 5 key L3 process areas with their specific goals
# ---------------------------------------------------------------------------
_BUILTIN_RUBRIC: Dict = {
    "process_areas": {
        "REQM": {
            "name": "Requirements Management",
            "maturity_level": 2,
            "goals": [
                {
                    "id": "REQM-SG1",
                    "title": "Manage Requirements",
                    "practices": ["REQM-SP1.1", "REQM-SP1.2", "REQM-SP1.3", "REQM-SP1.4", "REQM-SP1.5"],
                },
            ],
        },
        "PP": {
            "name": "Project Planning",
            "maturity_level": 2,
            "goals": [
                {
                    "id": "PP-SG1",
                    "title": "Establish Estimates",
                    "practices": ["PP-SP1.1", "PP-SP1.2", "PP-SP1.3", "PP-SP1.4"],
                },
                {
                    "id": "PP-SG2",
                    "title": "Develop a Project Plan",
                    "practices": ["PP-SP2.1", "PP-SP2.2", "PP-SP2.3", "PP-SP2.4", "PP-SP2.5", "PP-SP2.6", "PP-SP2.7"],
                },
                {
                    "id": "PP-SG3",
                    "title": "Obtain Commitment to the Plan",
                    "practices": ["PP-SP3.1", "PP-SP3.2", "PP-SP3.3"],
                },
            ],
        },
        "CM": {
            "name": "Configuration Management",
            "maturity_level": 2,
            "goals": [
                {
                    "id": "CM-SG1",
                    "title": "Establish Baselines",
                    "practices": ["CM-SP1.1", "CM-SP1.2", "CM-SP1.3"],
                },
                {
                    "id": "CM-SG2",
                    "title": "Track and Control Changes",
                    "practices": ["CM-SP2.1", "CM-SP2.2"],
                },
                {
                    "id": "CM-SG3",
                    "title": "Establish Integrity",
                    "practices": ["CM-SP3.1", "CM-SP3.2"],
                },
            ],
        },
        "MA": {
            "name": "Measurement and Analysis",
            "maturity_level": 2,
            "goals": [
                {
                    "id": "MA-SG1",
                    "title": "Align Measurement and Analysis Activities",
                    "practices": ["MA-SP1.1", "MA-SP1.2", "MA-SP1.3", "MA-SP1.4"],
                },
                {
                    "id": "MA-SG2",
                    "title": "Provide Measurement Results",
                    "practices": ["MA-SP2.1", "MA-SP2.2", "MA-SP2.3", "MA-SP2.4"],
                },
            ],
        },
        "PPQA": {
            "name": "Process and Product Quality Assurance",
            "maturity_level": 2,
            "goals": [
                {
                    "id": "PPQA-SG1",
                    "title": "Objectively Evaluate Processes and Work Products",
                    "practices": ["PPQA-SP1.1", "PPQA-SP1.2"],
                },
                {
                    "id": "PPQA-SG2",
                    "title": "Provide Objective Insight",
                    "practices": ["PPQA-SP2.1", "PPQA-SP2.2"],
                },
            ],
        },
    }
}

# Statuses considered "satisfied" for scoring purposes
_SATISFIED_STATUSES = {"satisfied", "risk_accepted", "not_applicable"}
_PARTIAL_STATUSES = {"partially_satisfied"}


def _load_rubric(rubric_path: Optional[str] = None) -> Dict:
    """Return rubric dict from YAML file or fall back to built-in."""
    candidates = [rubric_path, "args/cmmi_l3_rubric.yaml"] if rubric_path else ["args/cmmi_l3_rubric.yaml"]
    for path in candidates:
        if path and Path(path).exists():
            try:
                import yaml  # type: ignore
                with open(path, encoding="utf-8") as fh:
                    loaded = yaml.safe_load(fh)
                if loaded and "process_areas" in loaded:
                    return loaded
            except Exception:
                pass
    return _BUILTIN_RUBRIC


def _fetch_pa_statuses(project_id: Optional[str]) -> Dict[str, str]:
    """Query DB for per-practice statuses. Returns {practice_id: status}."""
    results: Dict[str, str] = {}
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from db.storage import get_connection  # type: ignore

        conn = get_connection()
        # Try cmmi_assessments first, fall back to cmmi_level3_assessments
        for table in ("cmmi_assessments", "cmmi_level3_assessments"):
            try:
                if project_id:
                    rows = conn.execute(
                        f"SELECT requirement_id, status FROM {table} WHERE project_id = %s",  # noqa: S608  # nosec B608 — table from hardcoded registry, not user input
                        (project_id,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f"SELECT requirement_id, status FROM {table}",  # noqa: S608  # nosec B608 — table from hardcoded registry, not user input
                    ).fetchall()
                for row in rows:
                    results[row[0]] = row[1]
                break
            except Exception:
                continue
    except Exception:
        pass
    return results


def assess(
    project_id: Optional[str] = None,
    rubric_path: Optional[str] = None,
    db_statuses: Optional[Dict[str, str]] = None,
) -> Dict:
    """Evaluate CMMI L3 process areas and return a structured result.

    Returns:
        {
            "level": int,          # achieved maturity level (2 or 3)
            "score": float,        # 0.0–1.0 fraction of PAs satisfied
            "satisfied_pas": list, # PA codes that are fully satisfied
            "gap_pas": list,       # PA codes with gaps
            "details": dict,       # per-PA breakdown
        }
    """
    rubric = _load_rubric(rubric_path)
    pa_defs = rubric.get("process_areas", _BUILTIN_RUBRIC["process_areas"])

    if db_statuses is None:
        db_statuses = _fetch_pa_statuses(project_id)

    satisfied_pas: List[str] = []
    gap_pas: List[str] = []
    details: Dict[str, Dict] = {}

    for pa_code, pa_def in pa_defs.items():
        goals = pa_def.get("goals", [])
        all_practices: List[str] = [p for g in goals for p in g.get("practices", [])]
        total = len(all_practices)
        if total == 0:
            satisfied_pas.append(pa_code)
            details[pa_code] = {"name": pa_def.get("name", pa_code), "status": "satisfied", "score": 1.0}
            continue

        satisfied_count = sum(
            1 for p in all_practices if db_statuses.get(p, "not_assessed") in _SATISFIED_STATUSES
        )
        partial_count = sum(
            1 for p in all_practices if db_statuses.get(p, "not_assessed") in _PARTIAL_STATUSES
        )

        # Weighted score: full=1.0 partial=0.5
        weighted = (satisfied_count + 0.5 * partial_count) / total
        pa_status = "satisfied" if weighted >= 0.8 else ("partially_satisfied" if weighted >= 0.4 else "not_satisfied")

        if pa_status == "satisfied":
            satisfied_pas.append(pa_code)
        else:
            gap_pas.append(pa_code)

        details[pa_code] = {
            "name": pa_def.get("name", pa_code),
            "maturity_level": pa_def.get("maturity_level", 2),
            "status": pa_status,
            "score": round(weighted, 3),
            "satisfied_practices": [p for p in all_practices if db_statuses.get(p) in _SATISFIED_STATUSES],
            "gap_practices": [
                p for p in all_practices if db_statuses.get(p, "not_assessed") not in _SATISFIED_STATUSES | _PARTIAL_STATUSES
            ],
        }

    total_pas = len(pa_defs)
    overall_score = len(satisfied_pas) / total_pas if total_pas else 0.0
    # Level 3 requires all key PAs satisfied; otherwise report achieved level 2 if most L2 PAs pass
    achieved_level = 3 if not gap_pas else (2 if overall_score >= 0.5 else 1)

    return {
        "level": achieved_level,
        "score": round(overall_score, 3),
        "satisfied_pas": satisfied_pas,
        "gap_pas": gap_pas,
        "details": details,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CMMI Level 3 Assessor — PA-level evaluator")
    parser.add_argument("--project-id", help="Project ID to load assessments for")
    parser.add_argument("--rubric", help="Path to YAML rubric (default: args/cmmi_l3_rubric.yaml)")
    parser.add_argument("--json", action="store_true", dest="json_out", help="Output as JSON")
    args = parser.parse_args()

    result = assess(project_id=args.project_id, rubric_path=args.rubric)

    if args.json_out:
        print(json.dumps(result, indent=2))
    else:
        print(f"CMMI Level: {result['level']}")
        print(f"Score:      {result['score']:.1%}")
        print(f"Satisfied PAs ({len(result['satisfied_pas'])}): {', '.join(result['satisfied_pas']) or 'none'}")
        print(f"Gap PAs       ({len(result['gap_pas'])}): {', '.join(result['gap_pas']) or 'none'}")
        if result["details"]:
            print("\nProcess Area Breakdown:")
            for pa, info in result["details"].items():
                bar = "✓" if info["status"] == "satisfied" else ("~" if info["status"] == "partially_satisfied" else "✗")
                print(f"  {bar} {pa:6s} {info['name']:45s} {info['score']:.0%}")
