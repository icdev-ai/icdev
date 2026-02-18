#!/usr/bin/env python3
# CUI // SP-CTI
"""DevSecOps Profile Manager — per-project DevSecOps maturity profiling.

Creates, reads, updates, and assesses DevSecOps profiles for ICDEV projects.
Profiles control which pipeline security stages are active and track maturity level.

ADR D119: DevSecOps profile is a per-project config declaring active pipeline
security stages — detected during intake, overridable post-intake.

Usage:
    python tools/devsecops/profile_manager.py --project-id "proj-123" --create --maturity level_3_defined --json
    python tools/devsecops/profile_manager.py --project-id "proj-123" --json
    python tools/devsecops/profile_manager.py --project-id "proj-123" --detect --json
    python tools/devsecops/profile_manager.py --project-id "proj-123" --update --enable policy_as_code --json
    python tools/devsecops/profile_manager.py --project-id "proj-123" --assess --json
"""

import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

try:
    import yaml
except ImportError:
    yaml = None


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    """Load DevSecOps config from YAML (fallback to defaults)."""
    config_path = BASE_DIR / "args" / "devsecops_config.yaml"
    if yaml and config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    # Minimal fallback
    return {
        "devsecops_stages": {
            "sast": {"default": True, "optional": False},
            "sca": {"default": True, "optional": False},
            "secret_detection": {"default": True, "optional": False},
            "container_scan": {"default": True, "optional": False},
            "dast": {"default": False, "optional": True},
            "image_signing": {"default": False, "optional": True},
            "sbom_attestation": {"default": False, "optional": True},
            "rasp": {"default": False, "optional": True},
            "policy_as_code": {"default": False, "optional": True},
            "license_compliance": {"default": False, "optional": True},
        },
        "maturity_levels": {
            "level_1_initial": {"min_stages": 0, "required_stages": []},
            "level_2_managed": {"min_stages": 2, "required_stages": ["sast", "sca"]},
            "level_3_defined": {"min_stages": 4, "required_stages": ["sast", "sca", "secret_detection", "container_scan"]},
            "level_4_measured": {"min_stages": 6, "required_stages": ["sast", "sca", "secret_detection", "container_scan", "policy_as_code", "sbom_attestation"]},
            "level_5_optimized": {"min_stages": 8, "required_stages": ["sast", "sca", "secret_detection", "container_scan", "policy_as_code", "sbom_attestation", "image_signing", "rasp"]},
        },
    }


def _get_db():
    """Get database connection."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


# ---------------------------------------------------------------------------
# Profile CRUD
# ---------------------------------------------------------------------------

def create_profile(project_id: str, maturity_level: str = None,
                   stages: list = None, stage_configs: dict = None) -> dict:
    """Create a DevSecOps profile for a project.

    Args:
        project_id: Project identifier.
        maturity_level: Target maturity level (level_1_initial through level_5_optimized).
        stages: Explicit list of active stage IDs. If None, derived from maturity level.
        stage_configs: Per-stage tool selections and settings (JSON-serializable dict).

    Returns:
        Profile dict with id, project_id, maturity_level, active_stages, etc.
    """
    config = _load_config()
    maturity_defs = config.get("maturity_levels", {})
    stage_defs = config.get("devsecops_stages", {})

    # Default maturity level
    if not maturity_level:
        maturity_level = "level_3_defined"

    if maturity_level not in maturity_defs:
        return {"error": f"Invalid maturity level: {maturity_level}",
                "valid_levels": list(maturity_defs.keys())}

    # Determine active stages
    if stages is None:
        # Start with required stages for this maturity level
        required = maturity_defs[maturity_level].get("required_stages", [])
        # Add default stages
        defaults = [s for s, cfg in stage_defs.items() if cfg.get("default", False)]
        active = list(set(required + defaults))
    else:
        active = stages

    profile_id = f"dsp-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    conn = _get_db()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO devsecops_profiles
               (id, project_id, maturity_level, active_stages, stage_configs,
                detected_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (profile_id, project_id, maturity_level,
             json.dumps(sorted(active)),
             json.dumps(stage_configs or {}),
             now, now, now)
        )
        conn.commit()

        return {
            "id": profile_id,
            "project_id": project_id,
            "maturity_level": maturity_level,
            "active_stages": sorted(active),
            "stage_configs": stage_configs or {},
            "detected_at": now,
            "status": "created",
        }
    finally:
        conn.close()


def get_profile(project_id: str) -> dict:
    """Retrieve the DevSecOps profile for a project.

    Returns:
        Profile dict or error if not found.
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT * FROM devsecops_profiles WHERE project_id = ?",
            (project_id,)
        ).fetchone()

        if not row:
            return {"error": f"No DevSecOps profile for project {project_id}",
                    "hint": "Run --create to initialize a profile"}

        return {
            "id": row["id"],
            "project_id": row["project_id"],
            "maturity_level": row["maturity_level"],
            "active_stages": json.loads(row["active_stages"] or "[]"),
            "stage_configs": json.loads(row["stage_configs"] or "{}"),
            "detected_at": row["detected_at"],
            "confirmed_by": row["confirmed_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def update_profile(project_id: str, enable: list = None,
                   disable: list = None, maturity_level: str = None) -> dict:
    """Update an existing DevSecOps profile.

    Args:
        project_id: Project identifier.
        enable: List of stage IDs to enable.
        disable: List of stage IDs to disable.
        maturity_level: New target maturity level.

    Returns:
        Updated profile dict.
    """
    profile = get_profile(project_id)
    if "error" in profile:
        return profile

    active = set(profile["active_stages"])
    if enable:
        active.update(enable)
    if disable:
        active -= set(disable)

    now = datetime.now(timezone.utc).isoformat()
    new_maturity = maturity_level or profile["maturity_level"]

    conn = _get_db()
    try:
        conn.execute(
            """UPDATE devsecops_profiles
               SET active_stages = ?, maturity_level = ?, updated_at = ?
               WHERE project_id = ?""",
            (json.dumps(sorted(active)), new_maturity, now, project_id)
        )
        conn.commit()

        return {
            "project_id": project_id,
            "maturity_level": new_maturity,
            "active_stages": sorted(active),
            "updated_at": now,
            "status": "updated",
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Maturity detection and assessment
# ---------------------------------------------------------------------------

def detect_maturity_from_text(text: str) -> dict:
    """Detect DevSecOps maturity signals from customer text (used by intake).

    Args:
        text: Customer statement or requirement text.

    Returns:
        Dict with detected_stages, maturity_estimate, zta_signals.
    """
    config = _load_config()
    keywords_map = config.get("intake_detection", {}).get("keywords_by_stage", {})
    zta_keywords = config.get("intake_detection", {}).get("zta_keywords", [])
    absence_signals = config.get("intake_detection", {}).get("absence_signals", [])

    text_lower = text.lower()
    detected_stages = []
    zta_detected = False

    # Check each stage's keywords
    for stage, keywords in keywords_map.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                detected_stages.append(stage)
                break

    # Check ZTA keywords
    for kw in zta_keywords:
        if kw.lower() in text_lower:
            zta_detected = True
            break

    # Check absence signals
    greenfield = any(sig.lower() in text_lower for sig in absence_signals)

    # Estimate maturity level
    maturity_defs = config.get("maturity_levels", {})
    estimated_level = "level_1_initial"
    for level_id in ["level_5_optimized", "level_4_measured", "level_3_defined",
                     "level_2_managed", "level_1_initial"]:
        level_def = maturity_defs.get(level_id, {})
        required = set(level_def.get("required_stages", []))
        if required and required.issubset(set(detected_stages)):
            estimated_level = level_id
            break

    if greenfield:
        estimated_level = "level_1_initial"

    return {
        "detected_stages": sorted(set(detected_stages)),
        "maturity_estimate": estimated_level,
        "zta_detected": zta_detected,
        "greenfield": greenfield,
        "stage_count": len(set(detected_stages)),
    }


def assess_maturity(project_id: str) -> dict:
    """Assess current DevSecOps maturity based on active profile and project state.

    Returns:
        Maturity assessment with current level, gaps, and recommendations.
    """
    profile = get_profile(project_id)
    if "error" in profile:
        return profile

    config = _load_config()
    maturity_defs = config.get("maturity_levels", {})
    active = set(profile["active_stages"])
    current_level = profile["maturity_level"]

    # Check what's needed for next level
    levels_ordered = ["level_1_initial", "level_2_managed", "level_3_defined",
                      "level_4_measured", "level_5_optimized"]
    current_idx = levels_ordered.index(current_level) if current_level in levels_ordered else 0

    gaps = []
    next_level = None
    if current_idx < len(levels_ordered) - 1:
        next_level = levels_ordered[current_idx + 1]
        next_def = maturity_defs.get(next_level, {})
        next_required = set(next_def.get("required_stages", []))
        missing = next_required - active
        if missing:
            gaps = sorted(missing)

    # Verify current level requirements are met
    current_def = maturity_defs.get(current_level, {})
    current_required = set(current_def.get("required_stages", []))
    met = current_required.issubset(active)

    return {
        "project_id": project_id,
        "current_level": current_level,
        "requirements_met": met,
        "active_stage_count": len(active),
        "active_stages": sorted(active),
        "next_level": next_level,
        "gaps_for_next_level": gaps,
        "recommendation": f"Enable {', '.join(gaps)} to reach {next_level}" if gaps else "At maximum maturity",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="DevSecOps Profile Manager")
    parser.add_argument("--project-id", required=True, help="Project identifier")
    parser.add_argument("--create", action="store_true", help="Create new profile")
    parser.add_argument("--maturity", help="Maturity level for --create")
    parser.add_argument("--detect", action="store_true", help="Detect maturity from project artifacts")
    parser.add_argument("--update", action="store_true", help="Update existing profile")
    parser.add_argument("--enable", help="Comma-separated stages to enable")
    parser.add_argument("--disable", help="Comma-separated stages to disable")
    parser.add_argument("--assess", action="store_true", help="Assess current maturity")
    parser.add_argument("--detect-text", help="Detect maturity from text (intake use)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    result = {}

    if args.create:
        result = create_profile(args.project_id, maturity_level=args.maturity)
    elif args.update:
        enable = args.enable.split(",") if args.enable else None
        disable = args.disable.split(",") if args.disable else None
        result = update_profile(args.project_id, enable=enable, disable=disable,
                                maturity_level=args.maturity)
    elif args.assess:
        result = assess_maturity(args.project_id)
    elif args.detect_text:
        result = detect_maturity_from_text(args.detect_text)
    else:
        result = get_profile(args.project_id)

    if args.json or not args.human:
        print(json.dumps(result, indent=2))
    else:
        # Human-readable output
        if "error" in result:
            print(f"ERROR: {result['error']}")
        else:
            print(f"Project: {result.get('project_id', 'N/A')}")
            print(f"Maturity: {result.get('maturity_level', result.get('current_level', 'N/A'))}")
            stages = result.get("active_stages", [])
            print(f"Active Stages ({len(stages)}): {', '.join(stages)}")
            if "gaps_for_next_level" in result and result["gaps_for_next_level"]:
                print(f"Gaps: {', '.join(result['gaps_for_next_level'])}")
            if "recommendation" in result:
                print(f"Recommendation: {result['recommendation']}")


if __name__ == "__main__":
    main()
