#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""SAFe Agile hierarchy decomposition engine.

Decomposes intake session requirements into SAFe hierarchy:
Epic > Capability > Feature > Story > Enabler.

Includes T-shirt sizing, WSJF scoring, BDD acceptance criteria generation,
and ATO impact tier assignment.

Usage:
    # Decompose requirements to story level
    python tools/requirements/decomposition_engine.py --session-id sess-abc --json

    # Decompose with BDD criteria generation
    python tools/requirements/decomposition_engine.py --session-id sess-abc \\
        --level story --generate-bdd --json

    # Get existing decomposition
    python tools/requirements/decomposition_engine.py --session-id sess-abc --get --json

    # Get existing decomposition filtered by level
    python tools/requirements/decomposition_engine.py --session-id sess-abc \\
        --get --level feature --json
"""

import argparse
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

try:
    from tools.audit.audit_logger import log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def log_event(**kwargs): return -1


def _get_connection(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _generate_id(prefix="safe"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# T-shirt sizing and WSJF scoring
# ---------------------------------------------------------------------------

_TSHIRT_NUMERIC = {"XS": 1, "S": 1, "M": 2, "L": 3, "XL": 5, "XXL": 8}

_PRIORITY_BUSINESS_VALUE = {
    "critical": 10,
    "high": 7,
    "medium": 4,
    "low": 2,
}

# ATO impact tier keywords
_ATO_TIER_KEYWORDS = {
    "RED": [
        "secret", "ts/sci", "top secret", "classified",
        "authorization boundary", "ato boundary",
    ],
    "ORANGE": [
        "external system", "new interface", "new integration",
        "mobile", "byod", "cloud migration",
        "fips", "encryption change", "pki",
    ],
    "YELLOW": [
        "access control", "rbac", "audit", "logging",
        "authentication", "cac", "mfa", "stig",
    ],
}


def _estimate_tshirt(req_count):
    """Estimate T-shirt size from requirement count.

    S=1, M=2-3, L=4-5, XL=6+
    """
    if req_count <= 1:
        return "S"
    elif req_count <= 3:
        return "M"
    elif req_count <= 5:
        return "L"
    else:
        return "XL"


def _compute_wsjf(priority, t_shirt_size):
    """Compute WSJF score: business_value / t_shirt_numeric.

    business_value: 1-10 based on priority.
    t_shirt_numeric: S=1, M=2, L=3, XL=5, XXL=8.
    """
    bv = _PRIORITY_BUSINESS_VALUE.get(priority, 4)
    divisor = _TSHIRT_NUMERIC.get(t_shirt_size, 2)
    if divisor == 0:
        divisor = 1
    return round(bv / divisor, 2)


def _determine_ato_impact(text):
    """Determine ATO impact tier from requirement text."""
    lower = text.lower()
    for tier in ("RED", "ORANGE", "YELLOW"):
        keywords = _ATO_TIER_KEYWORDS[tier]
        if any(kw in lower for kw in keywords):
            return tier
    return "GREEN"


def _determine_dominant_priority(reqs):
    """Get the highest priority from a list of requirements."""
    priority_order = ["critical", "high", "medium", "low"]
    for p in priority_order:
        if any(r.get("priority") == p for r in reqs):
            return p
    return "medium"


# ---------------------------------------------------------------------------
# BDD generation
# ---------------------------------------------------------------------------

def generate_bdd_criteria(requirement_text, requirement_type):
    """Generate Gherkin Given/When/Then from requirement text.

    Pattern: Given [context from requirement], When [action implied],
    Then [expected outcome].
    """
    text = requirement_text.strip()
    # Truncate for scenario name
    scenario_name = text[:60] + ("..." if len(text) > 60 else "")

    # Build context from requirement type
    type_contexts = {
        "functional": "the system is operational and the user is authenticated",
        "security": "the system enforces security controls per the accreditation boundary",
        "performance": "the system is under normal operational load",
        "interface": "all external system interfaces are connected and operational",
        "data": "the data store is initialized and accessible",
        "compliance": "the system is deployed within the authorized environment",
        "operational": "the system is in its operational environment",
        "non_functional": "the system meets baseline non-functional requirements",
        "constraint": "all system constraints and limitations are documented",
        "transitional": "the migration or transition plan is in effect",
    }
    given_context = type_contexts.get(requirement_type, "the system is operational")

    # Extract action from text heuristics
    # Look for verb phrases after shall/must/should/will
    action_match = re.search(
        r"(?:shall|must|should|will|needs?\s+to|is\s+required\s+to)\s+(.+)",
        text,
        re.IGNORECASE,
    )
    if action_match:
        action_text = action_match.group(1).strip().rstrip(".")
        # Capitalize first letter
        action_text = action_text[0].upper() + action_text[1:] if action_text else text
    else:
        action_text = text[:80]

    # Build the Gherkin
    gherkin = (
        f"Feature: {requirement_type.replace('_', ' ').title()} Requirement Validation\n"
        f"\n"
        f"  Scenario: {scenario_name}\n"
        f"    Given {given_context}\n"
        f"    When {action_text}\n"
        f"    Then the system behaves as specified and the requirement is satisfied"
    )

    return gherkin


# ---------------------------------------------------------------------------
# Core decomposition
# ---------------------------------------------------------------------------

def decompose_requirements(
    session_id,
    target_level="story",
    generate_bdd=False,
    estimate=True,
    db_path=None,
):
    """Decompose session requirements into SAFe hierarchy.

    Reads all requirements from intake_requirements where session_id matches.
    Groups by requirement_type into Epics, breaks each Epic into Features,
    and for each Feature creates Stories (one per requirement) plus an
    Enabler for infrastructure needs.

    Args:
        session_id: Intake session ID.
        target_level: Decompose to this level (epic, feature, story).
        generate_bdd: If True, generate Given/When/Then acceptance criteria.
        estimate: If True, assign T-shirt sizes and WSJF scores.
        db_path: Optional DB path override.

    Returns:
        dict with session_id, items_created count, level breakdown, and items list.
    """
    conn = _get_connection(db_path)

    # Verify session exists
    session = conn.execute(
        "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not session:
        conn.close()
        raise ValueError(f"Session '{session_id}' not found.")

    session_data = dict(session)
    project_id = session_data.get("project_id")

    # Load all non-rejected requirements for the session
    reqs = conn.execute(
        "SELECT * FROM intake_requirements WHERE session_id = ? AND status != 'rejected'",
        (session_id,),
    ).fetchall()
    reqs = [dict(r) for r in reqs]

    if not reqs:
        conn.close()
        return {
            "status": "ok",
            "session_id": session_id,
            "items_created": 0,
            "levels": {"epics": 0, "features": 0, "stories": 0, "enablers": 0},
            "items": [],
            "message": "No requirements to decompose.",
        }

    items = []
    counts = {"epics": 0, "features": 0, "stories": 0, "enablers": 0}

    # --- Group into Epics by requirement_type ---
    type_groups = {}
    for req in reqs:
        rtype = req.get("requirement_type", "functional")
        type_groups.setdefault(rtype, []).append(req)

    for rtype, group_reqs in type_groups.items():
        # --- Create Epic ---
        epic_id = _generate_id("epic")
        epic_title = f"{rtype.replace('_', ' ').title()} Capabilities"
        epic_description = (
            f"All {rtype} requirements for the system. "
            f"Contains {len(group_reqs)} requirement(s)."
        )
        req_ids = [r["id"] for r in group_reqs]
        epic_priority = _determine_dominant_priority(group_reqs)
        epic_tshirt = _estimate_tshirt(len(group_reqs)) if estimate else None
        epic_wsjf = _compute_wsjf(epic_priority, epic_tshirt) if estimate and epic_tshirt else None

        # ATO impact from all requirement text
        all_text = " ".join(r.get("raw_text", "") for r in group_reqs)
        epic_ato = _determine_ato_impact(all_text)

        epic_bdd = None
        if generate_bdd:
            epic_bdd = generate_bdd_criteria(epic_description, rtype)

        conn.execute(
            """INSERT INTO safe_decomposition
               (id, session_id, project_id, parent_id, level, title,
                description, acceptance_criteria, t_shirt_size, story_points,
                pi_target, wsjf_score, ato_impact_tier, source_requirement_ids,
                status, classification, created_at)
               VALUES (?, ?, ?, NULL, 'epic', ?, ?, ?, ?, NULL, NULL, ?, ?, ?, 'draft', 'CUI', ?)""",
            (
                epic_id, session_id, project_id,
                epic_title, epic_description, epic_bdd,
                epic_tshirt, epic_wsjf, epic_ato,
                json.dumps(req_ids),
                datetime.now().isoformat(),
            ),
        )

        epic_item = {
            "id": epic_id,
            "level": "epic",
            "title": epic_title,
            "description": epic_description,
            "t_shirt_size": epic_tshirt,
            "wsjf_score": epic_wsjf,
            "ato_impact_tier": epic_ato,
            "parent_id": None,
            "children_count": len(group_reqs),
            "status": "draft",
        }
        if generate_bdd:
            epic_item["acceptance_criteria"] = epic_bdd
        items.append(epic_item)
        counts["epics"] += 1

        if target_level in ("feature", "story", "enabler"):
            # --- Create Features: one per requirement ---
            for req in group_reqs:
                feature_id = _generate_id("feat")
                raw_text = req.get("raw_text", "")
                feature_title = raw_text[:80] if len(raw_text) > 80 else raw_text
                feature_description = raw_text
                feature_priority = req.get("priority", "medium")
                feature_tshirt = _estimate_tshirt(1) if estimate else None  # one req = S
                feature_wsjf = (
                    _compute_wsjf(feature_priority, feature_tshirt)
                    if estimate and feature_tshirt else None
                )
                feature_ato = _determine_ato_impact(raw_text)

                feature_bdd = None
                if generate_bdd:
                    feature_bdd = generate_bdd_criteria(raw_text, rtype)

                conn.execute(
                    """INSERT INTO safe_decomposition
                       (id, session_id, project_id, parent_id, level, title,
                        description, acceptance_criteria, t_shirt_size, story_points,
                        pi_target, wsjf_score, ato_impact_tier, source_requirement_ids,
                        status, classification, created_at)
                       VALUES (?, ?, ?, ?, 'feature', ?, ?, ?, ?, NULL, NULL, ?, ?, ?, 'draft', 'CUI', ?)""",
                    (
                        feature_id, session_id, project_id, epic_id,
                        feature_title, feature_description, feature_bdd,
                        feature_tshirt, feature_wsjf, feature_ato,
                        json.dumps([req["id"]]),
                        datetime.now().isoformat(),
                    ),
                )

                feature_item = {
                    "id": feature_id,
                    "level": "feature",
                    "title": feature_title,
                    "description": feature_description,
                    "t_shirt_size": feature_tshirt,
                    "wsjf_score": feature_wsjf,
                    "ato_impact_tier": feature_ato,
                    "parent_id": epic_id,
                    "status": "draft",
                }
                if generate_bdd:
                    feature_item["acceptance_criteria"] = feature_bdd
                items.append(feature_item)
                counts["features"] += 1

                if target_level in ("story", "enabler"):
                    # --- Create Story for this requirement ---
                    story_id = _generate_id("story")
                    story_title = raw_text[:80] if len(raw_text) > 80 else raw_text
                    story_description = raw_text
                    story_tshirt = "S" if estimate else None  # one req = S
                    story_points = _TSHIRT_NUMERIC.get(story_tshirt, 1) if estimate else None
                    story_wsjf = (
                        _compute_wsjf(feature_priority, story_tshirt)
                        if estimate and story_tshirt else None
                    )
                    story_ato = feature_ato

                    story_bdd = None
                    if generate_bdd:
                        story_bdd = generate_bdd_criteria(raw_text, rtype)

                    conn.execute(
                        """INSERT INTO safe_decomposition
                           (id, session_id, project_id, parent_id, level, title,
                            description, acceptance_criteria, t_shirt_size, story_points,
                            pi_target, wsjf_score, ato_impact_tier, source_requirement_ids,
                            status, classification, created_at)
                           VALUES (?, ?, ?, ?, 'story', ?, ?, ?, ?, ?, NULL, ?, ?, ?, 'draft', 'CUI', ?)""",
                        (
                            story_id, session_id, project_id, feature_id,
                            story_title, story_description, story_bdd,
                            story_tshirt, story_points, story_wsjf, story_ato,
                            json.dumps([req["id"]]),
                            datetime.now().isoformat(),
                        ),
                    )

                    story_item = {
                        "id": story_id,
                        "level": "story",
                        "title": story_title,
                        "description": story_description,
                        "t_shirt_size": story_tshirt,
                        "story_points": story_points,
                        "wsjf_score": story_wsjf,
                        "ato_impact_tier": story_ato,
                        "parent_id": feature_id,
                        "status": "draft",
                    }
                    if generate_bdd:
                        story_item["acceptance_criteria"] = story_bdd
                    items.append(story_item)
                    counts["stories"] += 1

                    # Update requirement status to decomposed
                    conn.execute(
                        "UPDATE intake_requirements SET status = 'decomposed', updated_at = ? WHERE id = ?",
                        (datetime.now().isoformat(), req["id"]),
                    )

            # --- Create Enabler for infrastructure needs of this epic ---
            if target_level in ("story", "enabler"):
                enabler_id = _generate_id("enbl")
                enabler_title = f"Infrastructure Enabler: {rtype.replace('_', ' ').title()}"
                enabler_description = (
                    f"Infrastructure and platform enablement for {rtype} capabilities. "
                    f"Covers environment setup, CI/CD pipeline configuration, "
                    f"security hardening, and compliance scaffolding required to "
                    f"support {len(group_reqs)} {rtype} requirement(s)."
                )
                enabler_tshirt = _estimate_tshirt(len(group_reqs)) if estimate else None
                enabler_wsjf = (
                    _compute_wsjf("high", enabler_tshirt)
                    if estimate and enabler_tshirt else None
                )

                enabler_bdd = None
                if generate_bdd:
                    enabler_bdd = generate_bdd_criteria(enabler_description, rtype)

                conn.execute(
                    """INSERT INTO safe_decomposition
                       (id, session_id, project_id, parent_id, level, title,
                        description, acceptance_criteria, t_shirt_size, story_points,
                        pi_target, wsjf_score, ato_impact_tier, source_requirement_ids,
                        status, classification, created_at)
                       VALUES (?, ?, ?, ?, 'enabler', ?, ?, ?, ?, NULL, NULL, ?, ?, ?, 'draft', 'CUI', ?)""",
                    (
                        enabler_id, session_id, project_id, epic_id,
                        enabler_title, enabler_description, enabler_bdd,
                        enabler_tshirt, enabler_wsjf, epic_ato,
                        json.dumps(req_ids),
                        datetime.now().isoformat(),
                    ),
                )

                enabler_item = {
                    "id": enabler_id,
                    "level": "enabler",
                    "title": enabler_title,
                    "description": enabler_description,
                    "t_shirt_size": enabler_tshirt,
                    "wsjf_score": enabler_wsjf,
                    "ato_impact_tier": epic_ato,
                    "parent_id": epic_id,
                    "status": "draft",
                }
                if generate_bdd:
                    enabler_item["acceptance_criteria"] = enabler_bdd
                items.append(enabler_item)
                counts["enablers"] += 1

    # Update session decomposed count
    total_items = sum(counts.values())
    conn.execute(
        "UPDATE intake_sessions SET decomposed_count = ?, updated_at = ? WHERE id = ?",
        (total_items, datetime.now().isoformat(), session_id),
    )

    conn.commit()
    conn.close()

    if _HAS_AUDIT:
        log_event(
            event_type="decomposition_generated",
            actor="icdev-requirements-analyst",
            action=f"Decomposed {len(reqs)} requirements into {total_items} SAFe items",
            project_id=project_id,
            details={
                "session_id": session_id,
                "items_created": total_items,
                "levels": counts,
            },
        )

    return {
        "status": "ok",
        "session_id": session_id,
        "items_created": total_items,
        "levels": counts,
        "items": items,
    }


# ---------------------------------------------------------------------------
# Query existing decomposition
# ---------------------------------------------------------------------------

def get_decomposition(session_id, level=None, db_path=None):
    """Return existing decomposition items for a session.

    Args:
        session_id: Intake session ID.
        level: Optional filter by SAFe level (epic, feature, story, enabler).
        db_path: Optional DB path override.

    Returns:
        dict with session_id and list of decomposition items.
    """
    conn = _get_connection(db_path)

    session = conn.execute(
        "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not session:
        conn.close()
        raise ValueError(f"Session '{session_id}' not found.")

    if level:
        rows = conn.execute(
            "SELECT * FROM safe_decomposition WHERE session_id = ? AND level = ? ORDER BY created_at",
            (session_id, level),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM safe_decomposition WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()

    items = [dict(r) for r in rows]
    conn.close()

    # Compute level counts
    level_counts = {"epics": 0, "features": 0, "stories": 0, "enablers": 0}
    for item in items:
        lv = item.get("level", "")
        if lv == "epic":
            level_counts["epics"] += 1
        elif lv == "feature":
            level_counts["features"] += 1
        elif lv == "story":
            level_counts["stories"] += 1
        elif lv == "enabler":
            level_counts["enablers"] += 1

    return {
        "status": "ok",
        "session_id": session_id,
        "filter_level": level,
        "total_items": len(items),
        "levels": level_counts,
        "items": items,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV SAFe Decomposition Engine"
    )
    parser.add_argument("--session-id", required=True, help="Intake session ID")
    parser.add_argument(
        "--level",
        choices=["epic", "capability", "feature", "story", "enabler"],
        default="story",
        help="Target decomposition level (default: story)",
    )
    parser.add_argument(
        "--generate-bdd", action="store_true",
        help="Generate BDD Given/When/Then acceptance criteria",
    )
    parser.add_argument(
        "--estimate", action="store_true", default=True,
        help="Add T-shirt size estimates and WSJF scores (default: True)",
    )
    parser.add_argument(
        "--get", action="store_true",
        help="Get existing decomposition instead of generating",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    try:
        if args.get:
            # Query existing decomposition
            level_filter = args.level if args.level != "story" else None
            # If --level is explicitly provided with --get, use it; otherwise None
            # We detect explicit --level by checking if it differs from default
            # For --get without --level, return all levels
            import sys
            explicit_level = None
            if "--level" in sys.argv:
                explicit_level = args.level
            result = get_decomposition(
                args.session_id, level=explicit_level,
            )
        else:
            # Generate decomposition
            result = decompose_requirements(
                args.session_id,
                target_level=args.level,
                generate_bdd=args.generate_bdd,
                estimate=args.estimate,
            )

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if args.get:
                print(f"Decomposition for session {args.session_id}: "
                      f"{result['total_items']} items")
                for item in result.get("items", []):
                    level = item.get("level", "?")
                    indent = "  " * (
                        ["epic", "capability", "feature", "story", "enabler"].index(level)
                        if level in ["epic", "capability", "feature", "story", "enabler"]
                        else 0
                    )
                    print(f"{indent}[{level.upper()}] {item.get('title', '?')}")
            else:
                print(
                    f"Created {result['items_created']} SAFe items "
                    f"from session {args.session_id}"
                )
                levels = result.get("levels", {})
                print(
                    f"  Epics: {levels.get('epics', 0)}, "
                    f"Features: {levels.get('features', 0)}, "
                    f"Stories: {levels.get('stories', 0)}, "
                    f"Enablers: {levels.get('enablers', 0)}"
                )
                for item in result.get("items", []):
                    level = item.get("level", "?")
                    indent = "  " * (
                        ["epic", "capability", "feature", "story", "enabler"].index(level)
                        if level in ["epic", "capability", "feature", "story", "enabler"]
                        else 0
                    )
                    print(f"{indent}[{level.upper()}] {item.get('title', '?')}")

    except (ValueError, FileNotFoundError) as e:
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"Error: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
# CUI // SP-CTI
