#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""ATO Boundary Impact Analyzer — 4-tier (GREEN/YELLOW/ORANGE/RED) scoring.

Registers ATO system boundaries, assesses how new requirements affect an
existing ATO, and generates alternative courses of action (COAs) for
RED-tier impacts that would invalidate the current authorization.

Part of the RICOAS (Requirements Intake, Compliance, Orchestration,
Assessment, Supply-chain) pipeline.

Usage:
    # Register an ATO system boundary
    python tools/requirements/boundary_analyzer.py --project-id proj-123 \\
        --register-system --system-name "My System" --ato-status active \\
        --classification CUI --impact-level IL5 --json

    # Assess a requirement against a system boundary
    python tools/requirements/boundary_analyzer.py --project-id proj-123 \\
        --system-id sys-abc --requirement-id req-xyz --json

    # Generate alternatives for a RED-tier assessment
    python tools/requirements/boundary_analyzer.py --project-id proj-123 \\
        --generate-alternatives --assessment-id bia-abc --json

    # List registered ATO systems
    python tools/requirements/boundary_analyzer.py --project-id proj-123 \\
        --list-systems --json

    # List boundary assessments (optionally filtered by tier)
    python tools/requirements/boundary_analyzer.py --project-id proj-123 \\
        --list-assessments --json
    python tools/requirements/boundary_analyzer.py --project-id proj-123 \\
        --list-assessments --tier RED --json
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

# Graceful import of audit logger
try:
    from tools.audit.audit_logger import log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def log_event(**kwargs) -> int:  # type: ignore[misc]
        return -1


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

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


def _generate_id(prefix="bia"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Tier scoring constants
# ---------------------------------------------------------------------------

# Keywords that push the score toward each tier
_GREEN_KEYWORDS = [
    "existing", "current", "internal", "within boundary", "already authorized",
    "minor update", "configuration change", "patch", "maintenance",
    "existing component", "existing service", "within enclave",
]

_YELLOW_KEYWORDS = [
    "new component", "new role", "additional service", "internal service",
    "new user type", "new module", "add feature", "minor integration",
    "new port", "new protocol", "additional logging", "new database table",
]

_ORANGE_KEYWORDS = [
    "external", "interconnect", "cross-boundary", "new data flow",
    "api integration", "third-party", "new interface", "new connection",
    "cross-domain", "mobile", "byod", "cloud service", "saas",
    "new network segment", "dmz", "partner system", "vendor api",
]

_RED_KEYWORDS = [
    "classification change", "secret", "ts/sci", "top secret",
    "boundary expansion", "prohibited", "new network", "air gap",
    "classification upgrade", "new enclave", "sipr", "jwics",
    "foreign national", "non-us entity", "unapproved technology",
    "remove encryption", "bypass authentication",
]

# NIST 800-53 control families to keyword mapping for affected control detection
_CONTROL_KEYWORD_MAP = {
    "AC-2": ["account", "user", "role", "access", "provision", "deprovisio"],
    "AC-3": ["access control", "permission", "authorization", "enforce"],
    "AC-4": ["data flow", "information flow", "cross-boundary", "filter"],
    "AC-17": ["remote access", "vpn", "remote", "telework"],
    "AC-19": ["mobile", "byod", "device", "mdm", "portable"],
    "AC-20": ["external system", "external information", "third-party system"],
    "AU-2": ["audit", "log", "event", "monitor"],
    "AU-6": ["audit review", "log analysis", "audit reduction"],
    "CA-3": ["interconnect", "isa", "mou", "system connection", "interface"],
    "CA-9": ["internal system", "internal connection"],
    "CM-3": ["configuration change", "change control", "baseline"],
    "CM-7": ["function", "service", "port", "protocol"],
    "IA-2": ["authenticat", "identity", "cac", "piv", "mfa", "credential"],
    "IA-5": ["password", "credential", "authenticator", "pki", "certificate"],
    "IR-4": ["incident", "response", "breach", "compromise"],
    "PE-3": ["physical", "facility", "data center", "server room"],
    "PL-4": ["rules of behavior", "acceptable use"],
    "RA-5": ["vulnerabilit", "scan", "patch", "remediat"],
    "SA-9": ["external service", "cloud service", "saas", "vendor"],
    "SC-7": ["boundary", "firewall", "proxy", "dmz", "network perimeter"],
    "SC-8": ["transmission", "encrypt", "tls", "fips", "in transit"],
    "SC-28": ["data at rest", "storage", "encrypt", "fips"],
    "SI-4": ["monitoring", "ids", "ips", "intrusion", "detect"],
}

# SSP section mapping to keywords
_SSP_SECTION_MAP = {
    "Section 1 - System Name/Title": ["system name", "rename", "redesignate"],
    "Section 2 - System Categorization": ["classification", "impact level", "categoriz"],
    "Section 3 - System Owner": ["owner", "authorizing official", "isso"],
    "Section 9 - System Interconnections": [
        "interconnect", "interface", "external system", "isa", "mou",
        "cross-boundary", "data flow", "api integration", "third-party",
    ],
    "Section 10 - Applicable Laws and Regulations": ["regulation", "law", "policy", "mandate"],
    "Section 11 - Minimum Security Controls": [
        "control", "nist", "baseline", "security requirement",
    ],
    "Section 13 - System Architecture": [
        "architecture", "component", "network", "topology", "boundary",
        "new service", "new module", "new component",
    ],
    "Section 14 - Network Diagram": [
        "network", "diagram", "topology", "segment", "dmz", "enclave",
    ],
    "Section 15 - Data Flow Diagram": [
        "data flow", "information flow", "cross-boundary", "data path",
    ],
    "Section 16 - Ports, Protocols, and Services": [
        "port", "protocol", "service", "firewall rule",
    ],
    "Section 17 - Hardware/Software Inventory": [
        "hardware", "software", "component", "inventory", "new server",
        "new application", "cots", "gots",
    ],
}


# ---------------------------------------------------------------------------
# Impact category detection
# ---------------------------------------------------------------------------

_IMPACT_CATEGORY_KEYWORDS = {
    "architecture": ["architecture", "component", "module", "service", "redesign", "refactor"],
    "data_flow": ["data flow", "information flow", "data path", "etl", "pipeline"],
    "authentication": ["authenticat", "login", "sso", "cac", "piv", "mfa", "identity"],
    "authorization": ["authoriz", "permission", "role", "rbac", "access control"],
    "network": ["network", "firewall", "port", "protocol", "segment", "dmz", "vpn"],
    "encryption": ["encrypt", "tls", "ssl", "fips", "certificate", "pki"],
    "logging": ["log", "audit", "monitor", "siem", "event"],
    "boundary_change": ["boundary", "enclave", "perimeter", "classification", "expand"],
    "new_interconnection": ["interconnect", "interface", "external system", "isa", "mou"],
    "data_type_change": ["data type", "classification", "cui", "secret", "pii", "phi"],
    "component_addition": ["new component", "new server", "new service", "add module", "install"],
}


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def register_system(
    project_id: str,
    system_name: str,
    ato_status: str = "active",
    boundary_definition: dict = None,
    baseline_controls: list = None,
    classification: str = "CUI",
    impact_level: str = "IL5",
    connected_systems: list = None,
    ato_expiry_date: str = None,
    isso_name: str = None,
    isso_email: str = None,
    db_path=None,
) -> dict:
    """Register an existing ATO system boundary in ato_system_registry.

    Args:
        project_id: ICDEV project ID.
        system_name: Human-readable system name.
        ato_status: One of active, provisional, expired, pending.
        boundary_definition: JSON-serializable dict describing the boundary.
        baseline_controls: List of NIST control IDs (e.g. ['AC-2', 'AU-2']).
        classification: CUI, SECRET, etc.
        impact_level: IL2, IL4, IL5, or IL6.
        connected_systems: List of connected system identifiers.
        ato_expiry_date: ISO date string for ATO expiry.
        isso_name: ISSO point of contact name.
        isso_email: ISSO point of contact email.
        db_path: Optional database path override.

    Returns:
        dict with system_id, system_name, ato_status, baseline_controls_count.
    """
    valid_statuses = ("active", "provisional", "expired", "pending")
    if ato_status not in valid_statuses:
        raise ValueError(
            f"Invalid ato_status '{ato_status}'. Must be one of: {valid_statuses}"
        )

    valid_levels = ("IL2", "IL4", "IL5", "IL6")
    if impact_level not in valid_levels:
        raise ValueError(
            f"Invalid impact_level '{impact_level}'. Must be one of: {valid_levels}"
        )

    system_id = _generate_id("sys")
    boundary_def = boundary_definition or {}
    controls = baseline_controls or []
    connected = connected_systems or []
    now = datetime.now(timezone.utc).isoformat()

    # Map ato_status to the DB ato_type enum
    status_to_type = {
        "active": "ato",
        "provisional": "iato",
        "expired": "ato",
        "pending": None,
    }
    ato_type = status_to_type.get(ato_status)

    conn = _get_connection(db_path)

    # Validate project exists
    row = conn.execute(
        "SELECT id FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"Project '{project_id}' not found in database.")

    conn.execute(
        """INSERT INTO ato_system_registry
           (id, project_id, system_name, ato_type, ato_date, ato_expiry,
            authorizing_official, accreditation_boundary, impact_level,
            data_types, interconnections, baseline_controls,
            component_inventory, classification, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            system_id,
            project_id,
            system_name,
            ato_type,
            now if ato_status == "active" else None,
            ato_expiry_date,
            isso_name,
            json.dumps(boundary_def),
            impact_level,
            json.dumps(boundary_def.get("data_types", [])),
            json.dumps(connected),
            json.dumps(controls),
            json.dumps(boundary_def.get("components", [])),
            classification,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()

    if _HAS_AUDIT:
        log_event(
            event_type="system_registered",
            actor="boundary-analyzer",
            action=f"Registered ATO system '{system_name}' ({system_id})",
            project_id=project_id,
            details={
                "system_id": system_id,
                "ato_status": ato_status,
                "impact_level": impact_level,
                "baseline_controls_count": len(controls),
            },
        )

    return {
        "status": "ok",
        "system_id": system_id,
        "system_name": system_name,
        "ato_status": ato_status,
        "baseline_controls_count": len(controls),
    }


def get_system(system_id: str, db_path=None) -> dict:
    """Get system details by ID.

    Args:
        system_id: The ATO system registry ID.
        db_path: Optional database path override.

    Returns:
        dict with full system details.
    """
    conn = _get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM ato_system_registry WHERE id = ?", (system_id,)
    ).fetchone()
    conn.close()

    if not row:
        raise ValueError(f"System '{system_id}' not found.")

    data = dict(row)
    # Parse JSON fields
    for field in ("accreditation_boundary", "data_types", "interconnections",
                  "baseline_controls", "component_inventory"):
        val = data.get(field)
        if val and isinstance(val, str):
            try:
                data[field] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass

    return {"status": "ok", "system": data}


def list_systems(project_id: str, db_path=None) -> dict:
    """List all registered ATO systems for a project.

    Args:
        project_id: ICDEV project ID.
        db_path: Optional database path override.

    Returns:
        dict with list of systems.
    """
    conn = _get_connection(db_path)
    rows = conn.execute(
        """SELECT id, project_id, system_name, ato_type, ato_expiry,
                  impact_level, classification, created_at
           FROM ato_system_registry
           WHERE project_id = ?
           ORDER BY created_at""",
        (project_id,),
    ).fetchall()
    conn.close()

    systems = []
    for r in rows:
        d = dict(r)
        # Derive a human-friendly ato_status
        d["ato_status"] = _ato_type_to_status(d.get("ato_type"), d.get("ato_expiry"))
        systems.append(d)

    return {
        "status": "ok",
        "project_id": project_id,
        "system_count": len(systems),
        "systems": systems,
    }


def _ato_type_to_status(ato_type, ato_expiry):
    """Map DB ato_type + expiry to user-facing status."""
    if ato_type is None:
        return "pending"
    if ato_type == "iato":
        return "provisional"
    if ato_expiry:
        try:
            expiry_dt = datetime.fromisoformat(ato_expiry)
            if expiry_dt < datetime.now(timezone.utc):
                return "expired"
        except (ValueError, TypeError):
            pass
    return "active"


# ---------------------------------------------------------------------------
# Boundary impact assessment
# ---------------------------------------------------------------------------

def _score_text_against_keywords(text_lower: str, keywords: list) -> int:
    """Count how many keywords match in the text."""
    return sum(1 for kw in keywords if kw in text_lower)


def _determine_impact_tier(text_lower: str, system_data: dict) -> tuple:
    """Determine the impact tier and numeric score for a requirement.

    Returns:
        (tier, score, impact_category, description)
    """
    red_hits = _score_text_against_keywords(text_lower, _RED_KEYWORDS)
    orange_hits = _score_text_against_keywords(text_lower, _ORANGE_KEYWORDS)
    yellow_hits = _score_text_against_keywords(text_lower, _YELLOW_KEYWORDS)
    green_hits = _score_text_against_keywords(text_lower, _GREEN_KEYWORDS)

    # Classification escalation is always RED regardless of other signals
    system_level = (system_data.get("impact_level") or "IL5").upper()
    if system_level in ("IL2", "IL4", "IL5"):
        for kw in ["secret", "ts/sci", "top secret", "jwics", "sipr"]:
            if kw in text_lower:
                return (
                    "RED",
                    95,
                    "boundary_change",
                    f"Classification upgrade detected. Current system is {system_level} "
                    f"but requirement references data/networks above this level. "
                    f"This would INVALIDATE the current ATO.",
                )

    # Boundary expansion keywords are RED
    for kw in ["boundary expansion", "new enclave", "new network"]:
        if kw in text_lower:
            return (
                "RED",
                85,
                "boundary_change",
                "Boundary expansion required. The current accreditation boundary "
                "would need to be redrawn, requiring full re-authorization.",
            )

    # Prohibited technology
    if "prohibited" in text_lower or "unapproved technology" in text_lower:
        return (
            "RED",
            90,
            "component_addition",
            "Prohibited or unapproved technology referenced. Cannot proceed "
            "within existing ATO. Must generate alternative COAs.",
        )

    # Score-based determination
    # Weight: RED=25, ORANGE=15, YELLOW=8, GREEN=2 (per hit)
    raw_score = (
        red_hits * 25
        + orange_hits * 15
        + yellow_hits * 8
        + green_hits * 2
    )

    # Normalize to 0-100, clamped
    # If no keywords matched at all, default to GREEN with low score
    if red_hits + orange_hits + yellow_hits + green_hits == 0:
        score = 10
    else:
        score = min(100, max(0, raw_score))

    # Determine category
    category = _detect_impact_category(text_lower)

    # Determine tier from score
    if score >= 76:
        tier = "RED"
        description = (
            "ATO-invalidating change detected. This requirement introduces changes "
            "that exceed the current authorization boundary. FULL STOP required. "
            "Must generate alternative COAs before proceeding."
        )
    elif score >= 51:
        tier = "ORANGE"
        description = (
            "Significant boundary change required. This requirement introduces "
            "cross-boundary data flows or new interconnections that require SSP "
            "revision, ISSO review, and possible re-authorization."
        )
    elif score >= 26:
        tier = "YELLOW"
        description = (
            "Minor boundary adjustment needed. This requirement adds new components "
            "or capabilities within the existing boundary. Requires SSP addendum "
            "and possible POAM entry."
        )
    else:
        tier = "GREEN"
        description = (
            "Requirement fits within existing ATO boundary. No boundary changes "
            "needed. Standard change control process applies."
        )

    return tier, score, category, description


def _detect_impact_category(text_lower: str) -> str:
    """Detect the primary impact category from requirement text."""
    best_category = "architecture"
    best_count = 0

    for category, keywords in _IMPACT_CATEGORY_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in text_lower)
        if count > best_count:
            best_count = count
            best_category = category

    return best_category


def _detect_affected_controls(text_lower: str) -> list:
    """Detect NIST 800-53 controls potentially affected by the requirement."""
    affected = []
    for control_id, keywords in _CONTROL_KEYWORD_MAP.items():
        if any(kw in text_lower for kw in keywords):
            affected.append(control_id)
    return sorted(affected)


def _detect_affected_ssp_sections(text_lower: str) -> list:
    """Detect SSP sections that would need updating."""
    affected = []
    for section_name, keywords in _SSP_SECTION_MAP.items():
        if any(kw in text_lower for kw in keywords):
            affected.append(section_name)
    return affected


def _generate_remediation_steps(tier: str, category: str, affected_controls: list,
                                affected_sections: list) -> list:
    """Generate remediation steps based on tier and impact."""
    steps = []

    if tier == "GREEN":
        steps.append("Submit standard change request through configuration management (CM-3).")
        steps.append("Update component inventory if adding software/hardware.")
        steps.append("Run regression security scan after implementation.")
        return steps

    if tier == "YELLOW":
        steps.append("Prepare SSP addendum documenting the change.")
        if affected_controls:
            steps.append(
                f"Update control implementations for: {', '.join(affected_controls[:5])}."
            )
        steps.append("Submit change to ISSO for review and approval.")
        if category == "component_addition":
            steps.append("Update hardware/software inventory (SSP Section 17).")
            steps.append("Run vulnerability scan on new component.")
        steps.append("Create POAM entry if any control gaps are introduced.")
        steps.append("Update SBOM after implementation.")
        return steps

    if tier == "ORANGE":
        steps.append("ISSO review REQUIRED before proceeding.")
        steps.append("Prepare formal SSP revision (not just addendum).")
        if "Section 9 - System Interconnections" in affected_sections:
            steps.append("Draft Interconnection Security Agreement (ISA) / MOU.")
        if "Section 14 - Network Diagram" in affected_sections:
            steps.append("Update network architecture diagram.")
        if "Section 15 - Data Flow Diagram" in affected_sections:
            steps.append("Update data flow diagrams showing new cross-boundary flows.")
        if affected_controls:
            steps.append(
                f"Re-assess controls: {', '.join(affected_controls[:8])}."
            )
        steps.append("Conduct risk assessment for the boundary modification.")
        steps.append("Determine if partial re-authorization is sufficient or full ATO required.")
        steps.append("Update POA&M with any new findings.")
        return steps

    # RED
    steps.append("FULL STOP — Do NOT proceed with implementation.")
    steps.append("Notify ISSO and Authorizing Official (AO) immediately.")
    steps.append("Generate alternative courses of action (COAs) using generate_alternatives().")
    steps.append("Conduct formal risk assessment with AO participation.")
    steps.append("If proceeding, initiate full re-authorization process.")
    if affected_controls:
        steps.append(
            f"All affected controls require full re-assessment: {', '.join(affected_controls)}."
        )
    steps.append("Budget 90-180 days for re-authorization timeline.")
    return steps


def assess_boundary_impact(
    project_id: str,
    system_id: str,
    requirement_id: str,
    db_path=None,
) -> dict:
    """Assess a single requirement's impact on an existing ATO boundary.

    Args:
        project_id: ICDEV project ID.
        system_id: Registered ATO system ID.
        requirement_id: Intake requirement ID to assess.
        db_path: Optional database path override.

    Returns:
        dict with assessment_id, impact_tier, impact_score,
        affected_controls, affected_ssp_sections, remediation_steps.
    """
    conn = _get_connection(db_path)

    # Load the requirement
    req_row = conn.execute(
        "SELECT * FROM intake_requirements WHERE id = ?", (requirement_id,)
    ).fetchone()
    if not req_row:
        conn.close()
        raise ValueError(f"Requirement '{requirement_id}' not found.")
    req_data = dict(req_row)

    # Load the system boundary
    sys_row = conn.execute(
        "SELECT * FROM ato_system_registry WHERE id = ?", (system_id,)
    ).fetchone()
    if not sys_row:
        conn.close()
        raise ValueError(f"System '{system_id}' not found in ato_system_registry.")
    sys_data = dict(sys_row)

    # Verify project matches
    if sys_data["project_id"] != project_id:
        conn.close()
        raise ValueError(
            f"System '{system_id}' belongs to project '{sys_data['project_id']}', "
            f"not '{project_id}'."
        )

    # Build the text corpus for analysis
    raw_text = req_data.get("raw_text", "")
    refined_text = req_data.get("refined_text", "") or ""
    full_text = f"{raw_text} {refined_text}".strip()
    text_lower = full_text.lower()

    # Score the impact
    tier, score, category, description = _determine_impact_tier(text_lower, sys_data)

    # Detect affected controls
    affected_controls = _detect_affected_controls(text_lower)

    # Detect affected SSP sections
    affected_ssp_sections = _detect_affected_ssp_sections(text_lower)

    # Always include Section 13 (architecture) for non-GREEN
    if tier != "GREEN" and "Section 13 - System Architecture" not in affected_ssp_sections:
        affected_ssp_sections.append("Section 13 - System Architecture")

    # Generate remediation steps
    remediation_steps = _generate_remediation_steps(
        tier, category, affected_controls, affected_ssp_sections
    )

    # Build alternative approaches placeholder (populated only for RED)
    alternative_approaches = []
    if tier == "RED":
        alternative_approaches = [
            "Run generate_alternatives() for detailed COA analysis."
        ]

    # Create assessment ID
    assessment_id = _generate_id("bia")
    now = datetime.now(timezone.utc).isoformat()

    # Insert into boundary_impact_assessments table
    # Handle potential UNIQUE constraint (requirement_id, system_id)
    try:
        conn.execute(
            """INSERT INTO boundary_impact_assessments
               (id, project_id, system_id, requirement_id,
                impact_tier, impact_category, impact_description,
                affected_controls, affected_components, ssp_sections_impacted,
                remediation_required, alternative_approach,
                risk_score, assessed_by, assessed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                assessment_id,
                project_id,
                system_id,
                requirement_id,
                tier,
                category,
                description,
                json.dumps(affected_controls),
                json.dumps([]),  # affected_components — populated downstream
                json.dumps(affected_ssp_sections),
                json.dumps(remediation_steps),
                json.dumps(alternative_approaches) if alternative_approaches else None,
                float(score),
                "boundary-analyzer",
                now,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Assessment already exists for this requirement+system pair — update it
        conn.execute(
            """UPDATE boundary_impact_assessments
               SET impact_tier = ?, impact_category = ?, impact_description = ?,
                   affected_controls = ?, ssp_sections_impacted = ?,
                   remediation_required = ?, alternative_approach = ?,
                   risk_score = ?, assessed_by = ?, assessed_at = ?
               WHERE requirement_id = ? AND system_id = ?""",
            (
                tier, category, description,
                json.dumps(affected_controls),
                json.dumps(affected_ssp_sections),
                json.dumps(remediation_steps),
                json.dumps(alternative_approaches) if alternative_approaches else None,
                float(score),
                "boundary-analyzer", now,
                requirement_id, system_id,
            ),
        )
        # Retrieve the existing ID
        existing = conn.execute(
            "SELECT id FROM boundary_impact_assessments WHERE requirement_id = ? AND system_id = ?",
            (requirement_id, system_id),
        ).fetchone()
        if existing:
            assessment_id = existing["id"]
        conn.commit()
    finally:
        conn.close()

    if _HAS_AUDIT:
        log_event(
            event_type="boundary_impact_assessed",
            actor="boundary-analyzer",
            action=(
                f"Assessed requirement {requirement_id} against system {system_id}: "
                f"{tier} (score={score})"
            ),
            project_id=project_id,
            details={
                "assessment_id": assessment_id,
                "tier": tier,
                "score": score,
                "affected_controls_count": len(affected_controls),
            },
        )

    return {
        "status": "ok",
        "assessment_id": assessment_id,
        "requirement_id": requirement_id,
        "system_id": system_id,
        "impact_tier": tier,
        "impact_score": score,
        "impact_category": category,
        "impact_description": description,
        "affected_controls": affected_controls,
        "affected_ssp_sections": affected_ssp_sections,
        "remediation_steps": remediation_steps,
        "alternative_approaches": alternative_approaches if alternative_approaches else None,
    }


# ---------------------------------------------------------------------------
# Alternative COA generation
# ---------------------------------------------------------------------------

def generate_alternatives(
    project_id: str,
    assessment_id: str,
    db_path=None,
) -> dict:
    """Generate alternative COAs for RED-tier requirements.

    Produces 3-4 alternatives that achieve the same mission intent within
    the existing ATO or with minimal boundary disruption.

    Args:
        project_id: ICDEV project ID.
        assessment_id: Boundary impact assessment ID (must be RED tier).
        db_path: Optional database path override.

    Returns:
        dict with assessment_id, requirement_id, original_tier, alternatives.
    """
    conn = _get_connection(db_path)

    # Load the assessment
    bia_row = conn.execute(
        "SELECT * FROM boundary_impact_assessments WHERE id = ?", (assessment_id,)
    ).fetchone()
    if not bia_row:
        conn.close()
        raise ValueError(f"Assessment '{assessment_id}' not found.")
    bia_data = dict(bia_row)

    if bia_data["project_id"] != project_id:
        conn.close()
        raise ValueError(
            f"Assessment '{assessment_id}' belongs to project '{bia_data['project_id']}', "
            f"not '{project_id}'."
        )

    if bia_data["impact_tier"] != "RED":
        conn.close()
        raise ValueError(
            f"Assessment '{assessment_id}' is {bia_data['impact_tier']}, not RED. "
            f"Alternatives are only generated for RED-tier impacts."
        )

    # Load the original requirement for context
    req_id = bia_data.get("requirement_id")
    req_text = ""
    if req_id:
        req_row = conn.execute(
            "SELECT raw_text, refined_text, requirement_type FROM intake_requirements WHERE id = ?",
            (req_id,),
        ).fetchone()
        if req_row:
            req_data = dict(req_row)
            req_text = (req_data.get("raw_text", "") + " " +
                        (req_data.get("refined_text", "") or "")).strip()

    text_lower = req_text.lower()

    # Parse affected controls from assessment
    affected_controls_raw = bia_data.get("affected_controls", "[]")
    try:
        affected_controls = json.loads(affected_controls_raw) if isinstance(
            affected_controls_raw, str) else affected_controls_raw or []
    except (json.JSONDecodeError, TypeError):
        affected_controls = []

    # Load system data for context
    sys_row = conn.execute(
        "SELECT * FROM ato_system_registry WHERE id = ?", (bia_data["system_id"],)
    ).fetchone()
    sys_data = dict(sys_row) if sys_row else {}
    system_level = sys_data.get("impact_level", "IL5")

    # --- Generate alternatives ---
    alternatives = []

    # Alternative 1: Cross-Domain Solution (CDS)
    cds_controls = list(set(affected_controls) | {"AC-4", "SC-7", "CA-3"})
    cds_feasibility = 0.6
    cds_tradeoffs = [
        "Requires approved CDS product (e.g., ISSE Guard, Radiant Mercury).",
        "Adds latency to data transfers.",
        "Procurement timeline: 6-12 months for CDS approval.",
        "Ongoing CDS maintenance and patching burden.",
    ]
    if any(kw in text_lower for kw in ["secret", "ts/sci", "top secret"]):
        cds_feasibility = 0.7
        cds_tradeoffs.append(
            "CDS is the standard approach for cross-classification data sharing."
        )
    alternatives.append({
        "approach_name": "Cross-Domain Solution (CDS)",
        "description": (
            "Use an approved Cross-Domain Solution to mediate data exchange between "
            f"the {system_level} boundary and the higher-classification requirement. "
            "Data flows through the CDS with content inspection, filtering, and "
            "audit logging. The existing ATO boundary remains intact."
        ),
        "boundary_tier_after": "YELLOW",
        "feasibility_score": round(cds_feasibility, 2),
        "tradeoffs": cds_tradeoffs,
        "affected_controls": sorted(cds_controls),
    })

    # Alternative 2: Data Downgrade
    downgrade_controls = list(set(affected_controls) | {"SC-8", "SC-28", "AC-3"})
    downgrade_feasibility = 0.5
    downgrade_tradeoffs = [
        "Some data fidelity may be lost during downgrade/sanitization.",
        "Requires formal data review and sanitization procedures.",
        "Aggregation at higher level may introduce delays.",
        "Must establish and maintain downgrade approval authority.",
    ]
    if "classification" in text_lower or "secret" in text_lower:
        downgrade_feasibility = 0.4
        downgrade_tradeoffs.append(
            "Classification downgrade requires formal review by Original Classification Authority (OCA)."
        )
    alternatives.append({
        "approach_name": "Data Downgrade / Sanitization",
        "description": (
            f"Process data at {system_level} (current boundary level) after sanitization "
            "or downgrade. Higher-classification aggregation occurs on a separate "
            "authorized system. Only downgraded/sanitized results flow into the "
            "current boundary."
        ),
        "boundary_tier_after": "GREEN",
        "feasibility_score": round(downgrade_feasibility, 2),
        "tradeoffs": downgrade_tradeoffs,
        "affected_controls": sorted(downgrade_controls),
    })

    # Alternative 3: Phased Approach
    phased_controls = list(set(affected_controls) | {"CM-3", "CA-2"})
    phased_feasibility = 0.75
    phased_tradeoffs = [
        "Full capability delivered incrementally, not all at once.",
        "Phase 1 (GREEN/YELLOW) can begin immediately.",
        "Phase 2+ requires separate authorization action.",
        "Must maintain phase boundary documentation.",
    ]
    alternatives.append({
        "approach_name": "Phased Implementation",
        "description": (
            "Split the requirement into phases. Phase 1 implements functionality "
            "that fits within the current ATO boundary (GREEN/YELLOW tier). "
            "Phase 2 addresses cross-boundary or classification changes through "
            "a separate authorization action, running in parallel without blocking "
            "Phase 1 delivery."
        ),
        "boundary_tier_after": "YELLOW",
        "feasibility_score": round(phased_feasibility, 2),
        "tradeoffs": phased_tradeoffs,
        "affected_controls": sorted(phased_controls),
    })

    # Alternative 4: Proxy Pattern
    proxy_controls = list(set(affected_controls) | {"SA-9", "CA-3", "SC-7"})
    proxy_feasibility = 0.65
    proxy_tradeoffs = [
        "Dependent on an existing authorized intermediary system.",
        "Adds an additional hop in the data path (latency).",
        "Must establish ISA/MOU with the intermediary system owner.",
        "Intermediary system must have sufficient ATO scope.",
    ]
    if any(kw in text_lower for kw in ["external", "third-party", "vendor"]):
        proxy_feasibility = 0.7
        proxy_tradeoffs.append(
            "Existing API gateways or ESBs may serve as the authorized intermediary."
        )
    alternatives.append({
        "approach_name": "Proxy Pattern (Authorized Intermediary)",
        "description": (
            "Route the requirement through an existing authorized system that "
            "already has the necessary boundary scope. The intermediary handles "
            "cross-boundary or cross-classification mediation. The current system "
            "connects only to the authorized intermediary via an approved "
            "interconnection (ISA/MOU), keeping the existing ATO intact."
        ),
        "boundary_tier_after": "ORANGE",
        "feasibility_score": round(proxy_feasibility, 2),
        "tradeoffs": proxy_tradeoffs,
        "affected_controls": sorted(proxy_controls),
    })

    # Update the assessment with alternatives
    conn.execute(
        """UPDATE boundary_impact_assessments
           SET alternative_approach = ?, assessed_at = ?
           WHERE id = ?""",
        (json.dumps(alternatives), datetime.now(timezone.utc).isoformat(), assessment_id),
    )
    conn.commit()
    conn.close()

    if _HAS_AUDIT:
        log_event(
            event_type="alternatives_generated",
            actor="boundary-analyzer",
            action=(
                f"Generated {len(alternatives)} alternative COAs for assessment {assessment_id}"
            ),
            project_id=project_id,
            details={
                "assessment_id": assessment_id,
                "requirement_id": req_id,
                "alternative_count": len(alternatives),
            },
        )

    return {
        "status": "ok",
        "assessment_id": assessment_id,
        "requirement_id": req_id,
        "original_tier": "RED",
        "alternatives": alternatives,
    }


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def list_assessments(
    project_id: str,
    system_id: str = None,
    tier: str = None,
    db_path=None,
) -> dict:
    """List all boundary assessments, optionally filtered by system or tier.

    Args:
        project_id: ICDEV project ID.
        system_id: Optional filter by ATO system.
        tier: Optional filter by tier (GREEN, YELLOW, ORANGE, RED).
        db_path: Optional database path override.

    Returns:
        dict with assessments list and summary counts.
    """
    if tier and tier not in ("GREEN", "YELLOW", "ORANGE", "RED"):
        raise ValueError(
            f"Invalid tier '{tier}'. Must be one of: GREEN, YELLOW, ORANGE, RED"
        )

    conn = _get_connection(db_path)

    query = "SELECT * FROM boundary_impact_assessments WHERE project_id = ?"
    params = [project_id]

    if system_id:
        query += " AND system_id = ?"
        params.append(system_id)

    if tier:
        query += " AND impact_tier = ?"
        params.append(tier)

    query += " ORDER BY assessed_at DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    assessments = []
    tier_counts = {"GREEN": 0, "YELLOW": 0, "ORANGE": 0, "RED": 0}

    for r in rows:
        d = dict(r)
        t = d.get("impact_tier", "GREEN")
        if t in tier_counts:
            tier_counts[t] += 1

        # Parse JSON fields for output
        for field in ("affected_controls", "affected_components",
                      "ssp_sections_impacted", "remediation_required",
                      "alternative_approach"):
            val = d.get(field)
            if val and isinstance(val, str):
                try:
                    d[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass

        assessments.append(d)

    return {
        "status": "ok",
        "project_id": project_id,
        "system_id": system_id,
        "tier_filter": tier,
        "total_assessments": len(assessments),
        "tier_counts": tier_counts,
        "assessments": assessments,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV ATO Boundary Impact Analyzer (4-tier GREEN/YELLOW/ORANGE/RED)"
    )
    parser.add_argument("--project-id", required=True, help="ICDEV project ID")

    # System registration
    parser.add_argument("--register-system", action="store_true",
                        help="Register a new ATO system boundary")
    parser.add_argument("--system-name", help="System name (for registration)")
    parser.add_argument("--ato-status",
                        choices=["active", "provisional", "expired", "pending"],
                        default="active", help="ATO status")
    parser.add_argument("--classification", default="CUI",
                        help="Classification marking")
    parser.add_argument("--impact-level",
                        choices=["IL2", "IL4", "IL5", "IL6"],
                        default="IL5", help="Impact level")
    parser.add_argument("--boundary-definition", help="JSON boundary definition")
    parser.add_argument("--baseline-controls", help="Comma-separated control IDs")
    parser.add_argument("--ato-expiry", help="ATO expiry date (ISO format)")
    parser.add_argument("--isso-name", help="ISSO point of contact name")
    parser.add_argument("--isso-email", help="ISSO point of contact email")

    # Assessment
    parser.add_argument("--system-id", help="ATO system ID (for assessment)")
    parser.add_argument("--requirement-id", help="Requirement ID to assess")

    # Alternatives
    parser.add_argument("--generate-alternatives", action="store_true",
                        help="Generate alternative COAs for RED assessment")
    parser.add_argument("--assessment-id", help="Assessment ID (for alternatives)")

    # Listing
    parser.add_argument("--list-systems", action="store_true",
                        help="List all registered ATO systems")
    parser.add_argument("--list-assessments", action="store_true",
                        help="List boundary impact assessments")
    parser.add_argument("--tier",
                        choices=["GREEN", "YELLOW", "ORANGE", "RED"],
                        help="Filter assessments by tier")
    parser.add_argument("--get-system", action="store_true",
                        help="Get system details")

    # Output
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    try:
        result = None

        if args.register_system:
            if not args.system_name:
                parser.error("--system-name is required for --register-system")

            boundary_def = None
            if args.boundary_definition:
                try:
                    boundary_def = json.loads(args.boundary_definition)
                except json.JSONDecodeError as e:
                    parser.error(f"Invalid JSON for --boundary-definition: {e}")

            controls = []
            if args.baseline_controls:
                controls = [c.strip() for c in args.baseline_controls.split(",")
                            if c.strip()]

            result = register_system(
                project_id=args.project_id,
                system_name=args.system_name,
                ato_status=args.ato_status,
                boundary_definition=boundary_def,
                baseline_controls=controls,
                classification=args.classification,
                impact_level=args.impact_level,
                ato_expiry_date=args.ato_expiry,
                isso_name=args.isso_name,
                isso_email=args.isso_email,
            )

        elif args.system_id and args.requirement_id:
            result = assess_boundary_impact(
                project_id=args.project_id,
                system_id=args.system_id,
                requirement_id=args.requirement_id,
            )

        elif args.generate_alternatives:
            if not args.assessment_id:
                parser.error("--assessment-id is required for --generate-alternatives")
            result = generate_alternatives(
                project_id=args.project_id,
                assessment_id=args.assessment_id,
            )

        elif args.list_systems:
            result = list_systems(project_id=args.project_id)

        elif args.list_assessments:
            result = list_assessments(
                project_id=args.project_id,
                system_id=args.system_id,
                tier=args.tier,
            )

        elif args.get_system and args.system_id:
            result = get_system(system_id=args.system_id)

        else:
            parser.print_help()
            return

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_human_readable(result)

    except (ValueError, FileNotFoundError) as e:
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"Error: {e}")
        raise SystemExit(1)


def _print_human_readable(result: dict):
    """Print result in human-readable format."""
    if not result:
        return

    result.get("status", "unknown")

    # Registration result
    if "system_id" in result and "baseline_controls_count" in result:
        print(f"System registered: {result.get('system_name')}")
        print(f"  ID: {result.get('system_id')}")
        print(f"  ATO Status: {result.get('ato_status')}")
        print(f"  Baseline Controls: {result.get('baseline_controls_count')}")
        return

    # Assessment result
    if "impact_tier" in result and "impact_score" in result:
        tier = result["impact_tier"]
        score = result["impact_score"]
        print(f"[{tier}] Impact Score: {score}/100")
        print(f"  Requirement: {result.get('requirement_id')}")
        print(f"  Category: {result.get('impact_category')}")
        print(f"  Description: {result.get('impact_description')}")
        controls = result.get("affected_controls", [])
        if controls:
            print(f"  Affected Controls: {', '.join(controls)}")
        sections = result.get("affected_ssp_sections", [])
        if sections:
            print("  Affected SSP Sections:")
            for s in sections:
                print(f"    - {s}")
        steps = result.get("remediation_steps", [])
        if steps:
            print("  Remediation Steps:")
            for i, step in enumerate(steps, 1):
                print(f"    {i}. {step}")
        return

    # Alternatives result
    if "alternatives" in result and "original_tier" in result:
        print(f"Alternatives for assessment {result.get('assessment_id')}:")
        print(f"  Original Tier: {result.get('original_tier')}")
        for alt in result.get("alternatives", []):
            print(f"\n  [{alt.get('boundary_tier_after')}] {alt.get('approach_name')}")
            print(f"    Feasibility: {alt.get('feasibility_score', 0):.0%}")
            print(f"    {alt.get('description')}")
            if alt.get("tradeoffs"):
                print("    Tradeoffs:")
                for t in alt["tradeoffs"]:
                    print(f"      - {t}")
        return

    # System list
    if "systems" in result:
        print(f"ATO Systems ({result.get('system_count', 0)}):")
        for sys in result.get("systems", []):
            print(f"  {sys.get('id')}: {sys.get('system_name')} "
                  f"[{sys.get('ato_status', 'unknown')}] ({sys.get('impact_level')})")
        return

    # Assessment list
    if "assessments" in result:
        counts = result.get("tier_counts", {})
        print(f"Boundary Assessments ({result.get('total_assessments', 0)}):")
        print(f"  GREEN={counts.get('GREEN', 0)} YELLOW={counts.get('YELLOW', 0)} "
              f"ORANGE={counts.get('ORANGE', 0)} RED={counts.get('RED', 0)}")
        for a in result.get("assessments", []):
            print(f"  [{a.get('impact_tier')}] {a.get('id')} — "
                  f"req={a.get('requirement_id')} score={a.get('risk_score')}")
        return

    # System details
    if "system" in result:
        sys = result["system"]
        print(f"System: {sys.get('system_name')}")
        print(f"  ID: {sys.get('id')}")
        print(f"  Type: {sys.get('ato_type')}")
        print(f"  Impact Level: {sys.get('impact_level')}")
        print(f"  Classification: {sys.get('classification')}")
        return

    # Fallback
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
