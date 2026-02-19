#!/usr/bin/env python3
# CUI // SP-CTI
####################################################################
# CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI
# Distribution: Distribution D -- Authorized DoD Personnel Only
####################################################################
"""NIST OSCAL 1.1.2 artifact generator for ICDEV.

Generates four OSCAL JSON artifact types from the ICDEV database:
  - System Security Plan (SSP)
  - Plan of Action & Milestones (POA&M)
  - Assessment Results
  - Component Definition

Each artifact conforms to the OSCAL 1.1.2 specification with proper UUIDs,
ISO 8601 timestamps, and lowercase hyphenated control IDs.

Usage:
    python tools/compliance/oscal_generator.py --project-id "proj-123" --artifact all
    python tools/compliance/oscal_generator.py --project-id "proj-123" --artifact ssp
    python tools/compliance/oscal_generator.py --validate "/path/to/ssp.oscal.json"
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
OSCAL_VERSION = "1.1.2"
OSCAL_NS = "http://csrc.nist.gov/ns/oscal/1.0"

# FedRAMP profile URIs by baseline
FEDRAMP_PROFILE_URIS = {
    "low": "https://raw.githubusercontent.com/GSA/fedramp-automation/master/dist/content/rev5/baselines/json/FedRAMP_rev5_LOW-baseline-resolved-profile_catalog.json",
    "moderate": "https://raw.githubusercontent.com/GSA/fedramp-automation/master/dist/content/rev5/baselines/json/FedRAMP_rev5_MODERATE-baseline-resolved-profile_catalog.json",
    "high": "https://raw.githubusercontent.com/GSA/fedramp-automation/master/dist/content/rev5/baselines/json/FedRAMP_rev5_HIGH-baseline-resolved-profile_catalog.json",
}

# Impact level to FedRAMP baseline mapping
IL_TO_BASELINE = {
    "IL2": "moderate",
    "IL4": "moderate",
    "IL5": "high",
    "IL6": "high",
}

# NIST 800-53 control family names
CONTROL_FAMILIES = {
    "ac": "Access Control",
    "at": "Awareness and Training",
    "au": "Audit and Accountability",
    "ca": "Assessment, Authorization, and Monitoring",
    "cm": "Configuration Management",
    "cp": "Contingency Planning",
    "ia": "Identification and Authentication",
    "ir": "Incident Response",
    "ma": "Maintenance",
    "mp": "Media Protection",
    "pe": "Physical and Environmental Protection",
    "pl": "Planning",
    "pm": "Program Management",
    "ps": "Personnel Security",
    "pt": "PII Processing and Transparency",
    "ra": "Risk Assessment",
    "sa": "System and Services Acquisition",
    "sc": "System and Communications Protection",
    "si": "System and Information Integrity",
    "sr": "Supply Chain Risk Management",
}

# UUID pattern for validation
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

# ISO 8601 timestamp pattern for validation
ISO_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$"
)

# OSCAL control-id pattern (lowercase, hyphenated)
CONTROL_ID_PATTERN = re.compile(r"^[a-z]{2}-\d+(\.\d+)?$")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _generate_uuid():
    """Generate a UUID4 string for OSCAL identifiers."""
    return str(uuid.uuid4())


def _oscal_timestamp():
    """Generate an ISO 8601 timestamp with Z timezone for OSCAL metadata."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _control_id_to_oscal(control_id):
    """Convert a control ID to OSCAL format (lowercase with hyphens).

    Examples:
        "AC-2"        -> "ac-2"
        "AC-2(1)"     -> "ac-2.1"
        "ac-2"        -> "ac-2"
        "SI-4(4)"     -> "si-4.4"
    """
    if not control_id:
        return ""
    cid = control_id.strip().lower()
    # Convert enhancement notation: ac-2(1) -> ac-2.1
    cid = re.sub(r"\((\d+)\)", r".\1", cid)
    return cid


def _compute_file_hash(file_path):
    """Compute SHA-256 hash of a file for integrity verification."""
    sha256 = hashlib.sha256()
    path = Path(file_path)
    if not path.exists():
        return ""
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Get a database connection with Row factory."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_project(conn, project_id):
    """Load project record from the projects table."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found in database.")
    return dict(row)


def _get_controls(conn, project_id):
    """Load control implementations for a project.

    Joins project_controls with compliance_controls for full metadata.
    Returns list of dicts with control_id, implementation_status,
    implementation_description, responsible_role, evidence_path, family, title.
    """
    rows = conn.execute(
        """SELECT pc.control_id, pc.implementation_status,
                  pc.implementation_description, pc.responsible_role,
                  pc.evidence_path, pc.last_assessed,
                  cc.family, cc.title AS control_title,
                  cc.description AS control_description
           FROM project_controls pc
           LEFT JOIN compliance_controls cc ON pc.control_id = cc.id
           WHERE pc.project_id = ?
           ORDER BY pc.control_id""",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_poam_items(conn, project_id):
    """Load POA&M items for a project, ordered by severity."""
    rows = conn.execute(
        """SELECT * FROM poam_items
           WHERE project_id = ?
           ORDER BY
             CASE severity
               WHEN 'critical' THEN 1
               WHEN 'high' THEN 2
               WHEN 'moderate' THEN 3
               WHEN 'low' THEN 4
             END,
             id""",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_findings(conn, project_id):
    """Load assessment findings across all compliance frameworks.

    Pulls from fedramp_assessments, cmmc_assessments, stig_findings,
    cssp_assessments, and sbd_assessments tables.

    Returns a dict keyed by source framework with lists of finding dicts.
    """
    findings = {}

    # FedRAMP assessments
    rows = conn.execute(
        """SELECT control_id, baseline, status, implementation_status,
                  evidence_description, evidence_path, notes,
                  assessment_date, assessor
           FROM fedramp_assessments
           WHERE project_id = ?
           ORDER BY control_id""",
        (project_id,),
    ).fetchall()
    findings["fedramp"] = [dict(r) for r in rows]

    # CMMC assessments
    rows = conn.execute(
        """SELECT practice_id, domain, level, status,
                  evidence_description, evidence_path, notes,
                  nist_171_id, assessment_date, assessor
           FROM cmmc_assessments
           WHERE project_id = ?
           ORDER BY practice_id""",
        (project_id,),
    ).fetchall()
    findings["cmmc"] = [dict(r) for r in rows]

    # STIG findings
    rows = conn.execute(
        """SELECT stig_id, finding_id, rule_id, severity, title,
                  description, check_content, fix_text, status,
                  comments, target_type, assessed_by, assessed_at
           FROM stig_findings
           WHERE project_id = ?
           ORDER BY severity, finding_id""",
        (project_id,),
    ).fetchall()
    findings["stig"] = [dict(r) for r in rows]

    # CSSP assessments
    try:
        rows = conn.execute(
            """SELECT functional_area, requirement_id, status,
                      evidence_description, evidence_path, notes,
                      assessment_date, assessor
               FROM cssp_assessments
               WHERE project_id = ?
               ORDER BY requirement_id""",
            (project_id,),
        ).fetchall()
        findings["cssp"] = [dict(r) for r in rows]
    except sqlite3.OperationalError:
        findings["cssp"] = []

    # SbD assessments
    try:
        rows = conn.execute(
            """SELECT domain, requirement_id, status,
                      evidence_description, evidence_path, notes,
                      assessment_date, assessor
               FROM sbd_assessments
               WHERE project_id = ?
               ORDER BY requirement_id""",
            (project_id,),
        ).fetchall()
        findings["sbd"] = [dict(r) for r in rows]
    except sqlite3.OperationalError:
        findings["sbd"] = []

    return findings


def _get_sbom_records(conn, project_id):
    """Load SBOM records for a project."""
    rows = conn.execute(
        """SELECT * FROM sbom_records
           WHERE project_id = ?
           ORDER BY generated_at DESC""",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _store_oscal_artifact(conn, project_id, artifact_type, file_path,
                          file_hash, schema_valid, validation_errors=None):
    """Insert or update an OSCAL artifact record in the oscal_artifacts table.

    Uses INSERT OR REPLACE on UNIQUE(project_id, artifact_type, format).
    """
    errors_json = json.dumps(validation_errors) if validation_errors else None
    try:
        conn.execute(
            """INSERT OR REPLACE INTO oscal_artifacts
               (project_id, artifact_type, oscal_version, format,
                file_path, file_hash, schema_valid, validation_errors,
                generated_at, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                artifact_type,
                OSCAL_VERSION,
                "json",
                str(file_path),
                file_hash,
                1 if schema_valid else 0,
                errors_json,
                _oscal_timestamp(),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"Warning: Could not store OSCAL artifact record: {e}",
              file=sys.stderr)


def _log_audit(conn, project_id, action, details):
    """Log an audit trail event for OSCAL generation (append-only, NIST AU)."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "oscal_generated",
                "icdev-compliance-engine",
                action,
                json.dumps(details),
                json.dumps(details.get("affected_files", [])),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"Warning: Could not log audit event: {e}", file=sys.stderr)


def _resolve_output_dir(project, project_id, output_dir=None):
    """Resolve the output directory for OSCAL artifacts.

    Priority:
      1. Explicit output_dir argument
      2. Project directory_path / compliance / oscal
      3. BASE_DIR / .tmp / compliance / project_id / oscal
    """
    if output_dir:
        out = Path(output_dir)
    else:
        dir_path = project.get("directory_path", "")
        if dir_path:
            out = Path(dir_path) / "compliance" / "oscal"
        else:
            out = BASE_DIR / ".tmp" / "compliance" / project_id / "oscal"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _determine_baseline(project):
    """Determine FedRAMP baseline from project impact level."""
    il = project.get("impact_level", "IL5")
    return IL_TO_BASELINE.get(il, "moderate")


def _build_metadata(project, title_prefix, extra_roles=None):
    """Build the OSCAL metadata block common to all artifact types."""
    now = _oscal_timestamp()
    roles = [
        {
            "id": "system-owner",
            "title": "System Owner",
        },
        {
            "id": "authorizing-official",
            "title": "Authorizing Official",
        },
        {
            "id": "system-admin",
            "title": "System Administrator",
        },
        {
            "id": "isso",
            "title": "Information System Security Officer",
        },
        {
            "id": "issm",
            "title": "Information System Security Manager",
        },
    ]
    if extra_roles:
        roles.extend(extra_roles)

    parties = [
        {
            "uuid": _generate_uuid(),
            "type": "organization",
            "name": project.get("created_by", "Organization"),
            "remarks": "System owning organization",
        },
    ]

    return {
        "title": f"{title_prefix} -- {project.get('name', 'UNNAMED')}",
        "last-modified": now,
        "version": "1.0",
        "oscal-version": OSCAL_VERSION,
        "roles": roles,
        "parties": parties,
        "remarks": (
            f"Generated by ICDEV Compliance Engine. "
            f"Classification: CUI // SP-CTI. "
            f"Impact Level: {project.get('impact_level', 'IL5')}."
        ),
    }


def _build_system_characteristics(project):
    """Build the system-characteristics block for SSP."""
    baseline = _determine_baseline(project)
    sensitivity = "high" if baseline == "high" else "moderate"

    # Determine security objective levels from impact level
    il = project.get("impact_level", "IL5")
    if il in ("IL5", "IL6"):
        conf = "high"
        integ = "high"
        avail = "moderate"
    else:
        conf = "moderate"
        integ = "moderate"
        avail = "low"

    return {
        "system-ids": [
            {
                "identifier-type": "https://ietf.org/rfc/rfc4122",
                "id": project.get("id", _generate_uuid()),
            }
        ],
        "system-name": project.get("name", "UNNAMED SYSTEM"),
        "description": project.get("description", "System description pending."),
        "security-sensitivity-level": sensitivity,
        "system-information": {
            "information-types": [
                {
                    "uuid": _generate_uuid(),
                    "title": "Controlled Technical Information",
                    "description": (
                        "Technical information with military or space "
                        "application that is subject to controls on the access, "
                        "use, reproduction, modification, performance, display, "
                        "release, disclosure, or dissemination."
                    ),
                    "categorizations": [
                        {
                            "system": "https://doi.org/10.6028/NIST.SP.800-60v2r1",
                            "information-type-ids": ["C.3.5.8"],
                        }
                    ],
                    "confidentiality-impact": {
                        "base": conf,
                    },
                    "integrity-impact": {
                        "base": integ,
                    },
                    "availability-impact": {
                        "base": avail,
                    },
                }
            ],
        },
        "security-impact-level": {
            "security-objective-confidentiality": conf,
            "security-objective-integrity": integ,
            "security-objective-availability": avail,
        },
        "status": {
            "state": project.get("status", "under-development"),
            "remarks": (
                f"ATO Status: {project.get('ato_status', 'none')}. "
                f"Classification: {project.get('classification', 'CUI')}."
            ),
        },
        "authorization-boundary": {
            "description": (
                f"The authorization boundary encompasses the "
                f"{project.get('name', 'system')} application, its "
                f"supporting infrastructure within "
                f"{project.get('cloud_environment', 'AWS GovCloud')}, "
                f"and all data flows between components."
            ),
        },
        "network-architecture": {
            "description": (
                f"The system operates within "
                f"{project.get('cloud_environment', 'AWS GovCloud')} "
                f"using a {project.get('type', 'webapp')} architecture. "
                f"All network traffic is encrypted using TLS 1.2+ "
                f"with FIPS 140-2 validated cryptographic modules."
            ),
        },
        "data-flow": {
            "description": (
                "Data flows are restricted to encrypted channels. "
                "All CUI data at rest is encrypted with AES-256. "
                "Data in transit uses TLS 1.2+ with mutual TLS "
                "for inter-service communication."
            ),
        },
    }


def _build_system_implementation(project, controls):
    """Build the system-implementation block for SSP."""
    users = [
        {
            "uuid": _generate_uuid(),
            "role-ids": ["system-owner"],
            "title": "System Owner",
            "description": "Responsible for overall system operation.",
        },
        {
            "uuid": _generate_uuid(),
            "role-ids": ["system-admin"],
            "title": "System Administrator",
            "description": "Manages system configuration and maintenance.",
        },
        {
            "uuid": _generate_uuid(),
            "role-ids": ["isso"],
            "title": "ISSO",
            "description": (
                "Ensures system security controls are implemented "
                "and operating effectively."
            ),
        },
    ]

    components = [
        {
            "uuid": _generate_uuid(),
            "type": "this-system",
            "title": project.get("name", "Application"),
            "description": project.get("description", "Primary application component."),
            "status": {
                "state": "operational",
            },
            "props": [],
        },
    ]

    # Add tech stack components if available
    backend = project.get("tech_stack_backend")
    if backend:
        components.append({
            "uuid": _generate_uuid(),
            "type": "software",
            "title": f"Backend: {backend}",
            "description": f"Backend technology stack: {backend}",
            "status": {"state": "operational"},
        })

    frontend = project.get("tech_stack_frontend")
    if frontend:
        components.append({
            "uuid": _generate_uuid(),
            "type": "software",
            "title": f"Frontend: {frontend}",
            "description": f"Frontend technology stack: {frontend}",
            "status": {"state": "operational"},
        })

    database = project.get("tech_stack_database")
    if database:
        components.append({
            "uuid": _generate_uuid(),
            "type": "software",
            "title": f"Database: {database}",
            "description": f"Database technology: {database}",
            "status": {"state": "operational"},
        })

    # Cloud infrastructure component
    cloud_env = project.get("cloud_environment", "aws-govcloud")
    components.append({
        "uuid": _generate_uuid(),
        "type": "leveraged-system",
        "title": f"Cloud Infrastructure: {cloud_env}",
        "description": (
            f"Cloud infrastructure provided by {cloud_env}. "
            f"FedRAMP authorized cloud service provider."
        ),
        "status": {"state": "operational"},
    })

    return {
        "users": users,
        "components": components,
    }


def _build_control_implementation(controls, system_component_uuid=None):
    """Build the control-implementation block for SSP.

    Converts each project control into an OSCAL implemented-requirement.
    """
    implemented_requirements = []

    for ctrl in controls:
        oscal_cid = _control_id_to_oscal(ctrl["control_id"])
        if not oscal_cid:
            continue

        # Build statement description from implementation details
        description = ctrl.get("implementation_description") or (
            f"Control {oscal_cid} implementation is "
            f"{ctrl.get('implementation_status', 'planned')}."
        )

        req = {
            "uuid": _generate_uuid(),
            "control-id": oscal_cid,
            "statements": [
                {
                    "statement-id": f"{oscal_cid}_smt",
                    "uuid": _generate_uuid(),
                    "description": description,
                }
            ],
        }

        # Add props for status and responsible role
        props = [
            {
                "name": "implementation-status",
                "ns": OSCAL_NS,
                "value": ctrl.get("implementation_status", "planned"),
            },
        ]
        if ctrl.get("responsible_role"):
            props.append({
                "name": "responsible-role",
                "ns": OSCAL_NS,
                "value": ctrl["responsible_role"],
            })
        req["props"] = props

        # Add responsible-roles if available
        if ctrl.get("responsible_role"):
            req["responsible-roles"] = [
                {
                    "role-id": ctrl["responsible_role"]
                    .lower()
                    .replace(" ", "-")
                    .replace("_", "-"),
                }
            ]

        implemented_requirements.append(req)

    return {
        "description": (
            "This section describes the implementation of each "
            "security control for the system. Controls are mapped to "
            "NIST 800-53 Rev 5 per the applicable FedRAMP baseline."
        ),
        "implemented-requirements": implemented_requirements,
    }


# ---------------------------------------------------------------------------
# Core generation functions
# ---------------------------------------------------------------------------

def generate_oscal_ssp(project_id, output_dir=None, db_path=None):
    """Generate an OSCAL SSP JSON artifact.

    Loads project data and control implementations from the database,
    builds a full OSCAL 1.1.2 SSP structure, writes to disk, records
    in oscal_artifacts, and logs an audit event.

    Args:
        project_id: The project identifier.
        output_dir: Override output directory (default: project compliance/oscal/).
        db_path: Override database path.

    Returns:
        Dict with file_path, uuid, controls_count, and validation result.
    """
    conn = _get_connection(db_path)
    try:
        project = _get_project(conn, project_id)
        controls = _get_controls(conn, project_id)
        baseline = _determine_baseline(project)
        profile_href = FEDRAMP_PROFILE_URIS.get(baseline, FEDRAMP_PROFILE_URIS["moderate"])

        ssp_uuid = _generate_uuid()

        # Build system-implementation first to get component UUIDs
        sys_impl = _build_system_implementation(project, controls)
        primary_component_uuid = (
            sys_impl["components"][0]["uuid"]
            if sys_impl["components"]
            else None
        )

        ssp = {
            "system-security-plan": {
                "uuid": ssp_uuid,
                "metadata": _build_metadata(project, "System Security Plan"),
                "import-profile": {
                    "href": profile_href,
                },
                "system-characteristics": _build_system_characteristics(project),
                "system-implementation": sys_impl,
                "control-implementation": _build_control_implementation(
                    controls, primary_component_uuid
                ),
                "back-matter": {
                    "resources": [
                        {
                            "uuid": _generate_uuid(),
                            "title": "FedRAMP Profile",
                            "description": (
                                f"FedRAMP {baseline.capitalize()} baseline profile."
                            ),
                            "rlinks": [
                                {"href": profile_href},
                            ],
                        },
                        {
                            "uuid": _generate_uuid(),
                            "title": "NIST SP 800-53 Rev 5",
                            "description": (
                                "Security and Privacy Controls for Information "
                                "Systems and Organizations."
                            ),
                            "rlinks": [
                                {
                                    "href": "https://doi.org/10.6028/NIST.SP.800-53r5",
                                },
                            ],
                        },
                    ],
                },
            },
        }

        # Write to file
        out_dir = _resolve_output_dir(project, project_id, output_dir)
        out_file = out_dir / "ssp.oscal.json"

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(ssp, f, indent=2, ensure_ascii=False)

        file_hash = _compute_file_hash(out_file)

        # Validate
        validation = validate_oscal(str(out_file), "ssp")
        schema_valid = validation["valid"]

        # Store artifact record
        _store_oscal_artifact(
            conn, project_id, "ssp", str(out_file),
            file_hash, schema_valid, validation.get("errors")
        )

        # Audit
        _log_audit(conn, project_id, "OSCAL SSP generated", {
            "artifact_type": "ssp",
            "oscal_version": OSCAL_VERSION,
            "uuid": ssp_uuid,
            "controls_count": len(controls),
            "baseline": baseline,
            "file_hash": file_hash,
            "schema_valid": schema_valid,
            "affected_files": [str(out_file)],
        })

        print("OSCAL SSP generated:")
        print(f"  File: {out_file}")
        print(f"  UUID: {ssp_uuid}")
        print(f"  Controls: {len(controls)}")
        print(f"  Baseline: {baseline}")
        print(f"  Valid: {schema_valid}")

        return {
            "file_path": str(out_file),
            "uuid": ssp_uuid,
            "controls_count": len(controls),
            "baseline": baseline,
            "file_hash": file_hash,
            "validation": validation,
        }

    finally:
        conn.close()


def generate_oscal_poam(project_id, output_dir=None, db_path=None):
    """Generate an OSCAL POA&M JSON artifact.

    Pulls items from the poam_items table and builds a full OSCAL 1.1.2
    POA&M structure with poam-items, findings, observations, and risks.

    Args:
        project_id: The project identifier.
        output_dir: Override output directory.
        db_path: Override database path.

    Returns:
        Dict with file_path, uuid, items_count, and validation result.
    """
    conn = _get_connection(db_path)
    try:
        project = _get_project(conn, project_id)
        poam_items = _get_poam_items(conn, project_id)

        poam_uuid = _generate_uuid()
        now = _oscal_timestamp()

        # Build OSCAL poam-items
        oscal_poam_items = []
        observations = []
        risks = []

        for item in poam_items:
            item_uuid = _generate_uuid()
            obs_uuid = _generate_uuid()
            risk_uuid = _generate_uuid()

            # Map severity to risk level
            severity = item.get("severity", "moderate")
            risk_level = {
                "critical": "very-high",
                "high": "high",
                "moderate": "moderate",
                "low": "low",
            }.get(severity, "moderate")

            # Build observation
            observation = {
                "uuid": obs_uuid,
                "title": f"Observation: {item.get('weakness_id', 'Unknown')}",
                "description": item.get("weakness_description", "No description."),
                "methods": ["EXAMINE", "TEST"],
                "collected": item.get("created_at", now),
            }
            if item.get("source"):
                observation["origins"] = [
                    {
                        "actors": [
                            {
                                "type": "tool",
                                "actor-uuid": _generate_uuid(),
                            }
                        ],
                    }
                ]
            observations.append(observation)

            # Build risk
            risk = {
                "uuid": risk_uuid,
                "title": f"Risk: {item.get('weakness_id', 'Unknown')}",
                "description": item.get("weakness_description", "No description."),
                "statement": (
                    f"Identified weakness {item.get('weakness_id', '')} "
                    f"with severity {severity}. "
                    f"Source: {item.get('source', 'Assessment')}."
                ),
                "status": _poam_status_to_oscal(item.get("status", "open")),
                "characterizations": [
                    {
                        "origin": {
                            "actors": [
                                {
                                    "type": "tool",
                                    "actor-uuid": _generate_uuid(),
                                }
                            ],
                        },
                        "facets": [
                            {
                                "name": "risk-level",
                                "system": OSCAL_NS,
                                "value": risk_level,
                            },
                        ],
                    }
                ],
            }
            if item.get("corrective_action"):
                risk["mitigating-factors"] = [
                    {
                        "uuid": _generate_uuid(),
                        "description": item["corrective_action"],
                    }
                ]
            if item.get("milestone_date"):
                risk["remediations"] = [
                    {
                        "uuid": _generate_uuid(),
                        "lifecycle": "planned",
                        "title": f"Remediation for {item.get('weakness_id', '')}",
                        "description": item.get("corrective_action", "Pending remediation."),
                    }
                ]
            risks.append(risk)

            # Build POA&M item
            poam_entry = {
                "uuid": item_uuid,
                "title": f"POA&M: {item.get('weakness_id', 'Unknown')}",
                "description": item.get("weakness_description", "No description."),
                "related-observations": [
                    {"observation-uuid": obs_uuid},
                ],
                "related-risks": [
                    {"risk-uuid": risk_uuid},
                ],
            }

            # Add props
            props = [
                {
                    "name": "severity",
                    "ns": OSCAL_NS,
                    "value": severity,
                },
            ]
            if item.get("status"):
                props.append({
                    "name": "status",
                    "ns": OSCAL_NS,
                    "value": item["status"],
                })
            if item.get("milestone_date"):
                props.append({
                    "name": "milestone-date",
                    "ns": OSCAL_NS,
                    "value": item["milestone_date"],
                })
            if item.get("responsible_party"):
                props.append({
                    "name": "responsible-party",
                    "ns": OSCAL_NS,
                    "value": item["responsible_party"],
                })
            if item.get("control_id"):
                oscal_cid = _control_id_to_oscal(item["control_id"])
                if oscal_cid:
                    props.append({
                        "name": "related-control",
                        "ns": OSCAL_NS,
                        "value": oscal_cid,
                    })
            poam_entry["props"] = props

            oscal_poam_items.append(poam_entry)

        # Assemble full POA&M document
        poam_doc = {
            "plan-of-action-and-milestones": {
                "uuid": poam_uuid,
                "metadata": _build_metadata(
                    project, "Plan of Action and Milestones"
                ),
                "import-ssp": {
                    "href": "./ssp.oscal.json",
                },
                "observations": observations,
                "risks": risks,
                "poam-items": oscal_poam_items,
                "back-matter": {
                    "resources": [],
                },
            },
        }

        # Write
        out_dir = _resolve_output_dir(project, project_id, output_dir)
        out_file = out_dir / "poam.oscal.json"

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(poam_doc, f, indent=2, ensure_ascii=False)

        file_hash = _compute_file_hash(out_file)

        # Validate
        validation = validate_oscal(str(out_file), "poam")
        schema_valid = validation["valid"]

        # Store record
        _store_oscal_artifact(
            conn, project_id, "poam", str(out_file),
            file_hash, schema_valid, validation.get("errors")
        )

        # Audit
        _log_audit(conn, project_id, "OSCAL POA&M generated", {
            "artifact_type": "poam",
            "oscal_version": OSCAL_VERSION,
            "uuid": poam_uuid,
            "items_count": len(poam_items),
            "file_hash": file_hash,
            "schema_valid": schema_valid,
            "affected_files": [str(out_file)],
        })

        print("OSCAL POA&M generated:")
        print(f"  File: {out_file}")
        print(f"  UUID: {poam_uuid}")
        print(f"  Items: {len(poam_items)}")
        print(f"  Valid: {schema_valid}")

        return {
            "file_path": str(out_file),
            "uuid": poam_uuid,
            "items_count": len(poam_items),
            "file_hash": file_hash,
            "validation": validation,
        }

    finally:
        conn.close()


def _poam_status_to_oscal(status):
    """Map POAM item status to OSCAL risk status."""
    mapping = {
        "open": "open",
        "in_progress": "investigating",
        "completed": "closed",
        "accepted_risk": "deviation-approved",
    }
    return mapping.get(status, "open")


def generate_oscal_assessment_results(project_id, output_dir=None, db_path=None):
    """Generate an OSCAL Assessment Results JSON artifact.

    Pulls findings from fedramp_assessments, cmmc_assessments,
    stig_findings, cssp_assessments, and sbd_assessments tables.
    Builds assessment-results with findings, observations, and risks
    organized per control.

    Args:
        project_id: The project identifier.
        output_dir: Override output directory.
        db_path: Override database path.

    Returns:
        Dict with file_path, uuid, findings_count, and validation result.
    """
    conn = _get_connection(db_path)
    try:
        project = _get_project(conn, project_id)
        all_findings = _get_findings(conn, project_id)

        ar_uuid = _generate_uuid()
        result_uuid = _generate_uuid()
        now = _oscal_timestamp()

        observations = []
        findings = []
        total_finding_count = 0

        # Process FedRAMP assessments
        for item in all_findings.get("fedramp", []):
            obs_uuid = _generate_uuid()
            finding_uuid = _generate_uuid()
            total_finding_count += 1

            oscal_cid = _control_id_to_oscal(item.get("control_id", ""))
            status = _assessment_status_to_oscal(item.get("status", "not_assessed"))

            observations.append({
                "uuid": obs_uuid,
                "title": f"FedRAMP: {item.get('control_id', 'Unknown')}",
                "description": item.get("evidence_description", "No evidence description."),
                "methods": ["EXAMINE", "INTERVIEW", "TEST"],
                "collected": item.get("assessment_date", now),
                "props": [
                    {
                        "name": "framework",
                        "ns": OSCAL_NS,
                        "value": "FedRAMP",
                    },
                    {
                        "name": "baseline",
                        "ns": OSCAL_NS,
                        "value": item.get("baseline", "moderate"),
                    },
                ],
            })

            finding_entry = {
                "uuid": finding_uuid,
                "title": f"FedRAMP Finding: {item.get('control_id', '')}",
                "description": (
                    f"FedRAMP {item.get('baseline', 'moderate')} assessment "
                    f"for control {item.get('control_id', '')}."
                ),
                "target": {
                    "type": "objective-id",
                    "target-id": oscal_cid or "unknown",
                    "status": {
                        "state": status,
                    },
                },
                "related-observations": [
                    {"observation-uuid": obs_uuid},
                ],
            }
            if item.get("notes"):
                finding_entry["remarks"] = item["notes"]
            findings.append(finding_entry)

        # Process CMMC assessments
        for item in all_findings.get("cmmc", []):
            obs_uuid = _generate_uuid()
            finding_uuid = _generate_uuid()
            total_finding_count += 1

            status = _cmmc_status_to_oscal(item.get("status", "not_assessed"))

            observations.append({
                "uuid": obs_uuid,
                "title": f"CMMC: {item.get('practice_id', 'Unknown')}",
                "description": item.get("evidence_description", "No evidence description."),
                "methods": ["EXAMINE", "TEST"],
                "collected": item.get("assessment_date", now),
                "props": [
                    {
                        "name": "framework",
                        "ns": OSCAL_NS,
                        "value": "CMMC",
                    },
                    {
                        "name": "level",
                        "ns": OSCAL_NS,
                        "value": str(item.get("level", 2)),
                    },
                    {
                        "name": "domain",
                        "ns": OSCAL_NS,
                        "value": item.get("domain", ""),
                    },
                ],
            })

            findings.append({
                "uuid": finding_uuid,
                "title": f"CMMC Finding: {item.get('practice_id', '')}",
                "description": (
                    f"CMMC Level {item.get('level', 2)} assessment for "
                    f"practice {item.get('practice_id', '')} "
                    f"(domain: {item.get('domain', '')})."
                ),
                "target": {
                    "type": "objective-id",
                    "target-id": item.get("practice_id", "unknown").lower(),
                    "status": {
                        "state": status,
                    },
                },
                "related-observations": [
                    {"observation-uuid": obs_uuid},
                ],
            })

        # Process STIG findings
        for item in all_findings.get("stig", []):
            obs_uuid = _generate_uuid()
            finding_uuid = _generate_uuid()
            total_finding_count += 1

            status = _stig_status_to_oscal(item.get("status", "Open"))

            observations.append({
                "uuid": obs_uuid,
                "title": f"STIG: {item.get('stig_id', '')} - {item.get('finding_id', '')}",
                "description": item.get("description", item.get("title", "No description.")),
                "methods": ["TEST"],
                "collected": item.get("assessed_at", now),
                "props": [
                    {
                        "name": "framework",
                        "ns": OSCAL_NS,
                        "value": "DISA-STIG",
                    },
                    {
                        "name": "severity",
                        "ns": OSCAL_NS,
                        "value": item.get("severity", "CAT2"),
                    },
                    {
                        "name": "rule-id",
                        "ns": OSCAL_NS,
                        "value": item.get("rule_id", ""),
                    },
                ],
            })

            finding_entry = {
                "uuid": finding_uuid,
                "title": f"STIG Finding: {item.get('title', '')}",
                "description": item.get("description", "No description."),
                "target": {
                    "type": "objective-id",
                    "target-id": item.get("rule_id", "unknown"),
                    "status": {
                        "state": status,
                    },
                },
                "related-observations": [
                    {"observation-uuid": obs_uuid},
                ],
            }
            if item.get("comments"):
                finding_entry["remarks"] = item["comments"]
            findings.append(finding_entry)

        # Process CSSP assessments
        for item in all_findings.get("cssp", []):
            obs_uuid = _generate_uuid()
            finding_uuid = _generate_uuid()
            total_finding_count += 1

            status = _assessment_status_to_oscal(item.get("status", "not_assessed"))

            observations.append({
                "uuid": obs_uuid,
                "title": f"CSSP: {item.get('requirement_id', 'Unknown')}",
                "description": item.get("evidence_description", "No evidence description."),
                "methods": ["EXAMINE"],
                "collected": item.get("assessment_date", now),
                "props": [
                    {
                        "name": "framework",
                        "ns": OSCAL_NS,
                        "value": "DoDI-8530.01-CSSP",
                    },
                    {
                        "name": "functional-area",
                        "ns": OSCAL_NS,
                        "value": item.get("functional_area", ""),
                    },
                ],
            })

            findings.append({
                "uuid": finding_uuid,
                "title": f"CSSP Finding: {item.get('requirement_id', '')}",
                "description": (
                    f"CSSP assessment for requirement "
                    f"{item.get('requirement_id', '')} "
                    f"(area: {item.get('functional_area', '')})."
                ),
                "target": {
                    "type": "objective-id",
                    "target-id": item.get("requirement_id", "unknown").lower(),
                    "status": {
                        "state": status,
                    },
                },
                "related-observations": [
                    {"observation-uuid": obs_uuid},
                ],
            })

        # Process SbD assessments
        for item in all_findings.get("sbd", []):
            obs_uuid = _generate_uuid()
            finding_uuid = _generate_uuid()
            total_finding_count += 1

            status = _assessment_status_to_oscal(item.get("status", "not_assessed"))

            observations.append({
                "uuid": obs_uuid,
                "title": f"SbD: {item.get('requirement_id', 'Unknown')}",
                "description": item.get("evidence_description", "No evidence description."),
                "methods": ["EXAMINE", "TEST"],
                "collected": item.get("assessment_date", now),
                "props": [
                    {
                        "name": "framework",
                        "ns": OSCAL_NS,
                        "value": "CISA-SbD",
                    },
                    {
                        "name": "domain",
                        "ns": OSCAL_NS,
                        "value": item.get("domain", ""),
                    },
                ],
            })

            findings.append({
                "uuid": finding_uuid,
                "title": f"SbD Finding: {item.get('requirement_id', '')}",
                "description": (
                    f"CISA Secure by Design assessment for requirement "
                    f"{item.get('requirement_id', '')} "
                    f"(domain: {item.get('domain', '')})."
                ),
                "target": {
                    "type": "objective-id",
                    "target-id": item.get("requirement_id", "unknown").lower(),
                    "status": {
                        "state": status,
                    },
                },
                "related-observations": [
                    {"observation-uuid": obs_uuid},
                ],
            })

        # Assemble assessment results document
        ar_doc = {
            "assessment-results": {
                "uuid": ar_uuid,
                "metadata": _build_metadata(
                    project,
                    "Assessment Results",
                    extra_roles=[
                        {
                            "id": "assessor",
                            "title": "Security Assessor",
                        },
                    ],
                ),
                "import-ap": {
                    "href": "#assessment-plan-placeholder",
                    "remarks": (
                        "Assessment plan reference. Replace with actual "
                        "assessment plan OSCAL artifact URI."
                    ),
                },
                "results": [
                    {
                        "uuid": result_uuid,
                        "title": f"Assessment Results -- {project.get('name', '')}",
                        "description": (
                            f"Consolidated assessment results across "
                            f"FedRAMP, CMMC, DISA STIG, CSSP, and SbD "
                            f"frameworks for project {project_id}."
                        ),
                        "start": now,
                        "observations": observations,
                        "findings": findings,
                        "props": [
                            {
                                "name": "total-findings",
                                "ns": OSCAL_NS,
                                "value": str(total_finding_count),
                            },
                            {
                                "name": "fedramp-findings",
                                "ns": OSCAL_NS,
                                "value": str(len(all_findings.get("fedramp", []))),
                            },
                            {
                                "name": "cmmc-findings",
                                "ns": OSCAL_NS,
                                "value": str(len(all_findings.get("cmmc", []))),
                            },
                            {
                                "name": "stig-findings",
                                "ns": OSCAL_NS,
                                "value": str(len(all_findings.get("stig", []))),
                            },
                            {
                                "name": "cssp-findings",
                                "ns": OSCAL_NS,
                                "value": str(len(all_findings.get("cssp", []))),
                            },
                            {
                                "name": "sbd-findings",
                                "ns": OSCAL_NS,
                                "value": str(len(all_findings.get("sbd", []))),
                            },
                        ],
                    },
                ],
                "back-matter": {
                    "resources": [],
                },
            },
        }

        # Write
        out_dir = _resolve_output_dir(project, project_id, output_dir)
        out_file = out_dir / "assessment-results.oscal.json"

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(ar_doc, f, indent=2, ensure_ascii=False)

        file_hash = _compute_file_hash(out_file)

        # Validate
        validation = validate_oscal(str(out_file), "assessment_results")
        schema_valid = validation["valid"]

        # Store record
        _store_oscal_artifact(
            conn, project_id, "assessment_results", str(out_file),
            file_hash, schema_valid, validation.get("errors")
        )

        # Audit
        _log_audit(conn, project_id, "OSCAL Assessment Results generated", {
            "artifact_type": "assessment_results",
            "oscal_version": OSCAL_VERSION,
            "uuid": ar_uuid,
            "total_findings": total_finding_count,
            "frameworks": {
                k: len(v) for k, v in all_findings.items()
            },
            "file_hash": file_hash,
            "schema_valid": schema_valid,
            "affected_files": [str(out_file)],
        })

        print("OSCAL Assessment Results generated:")
        print(f"  File: {out_file}")
        print(f"  UUID: {ar_uuid}")
        print(f"  Total findings: {total_finding_count}")
        for fw, items in all_findings.items():
            if items:
                print(f"    {fw}: {len(items)}")
        print(f"  Valid: {schema_valid}")

        return {
            "file_path": str(out_file),
            "uuid": ar_uuid,
            "total_findings": total_finding_count,
            "frameworks": {k: len(v) for k, v in all_findings.items()},
            "file_hash": file_hash,
            "validation": validation,
        }

    finally:
        conn.close()


def _assessment_status_to_oscal(status):
    """Map assessment status to OSCAL finding status state."""
    mapping = {
        "satisfied": "satisfied",
        "not_satisfied": "not-satisfied",
        "other_than_satisfied": "not-satisfied",
        "partially_satisfied": "not-satisfied",
        "not_assessed": "not-satisfied",
        "not_applicable": "satisfied",
        "risk_accepted": "satisfied",
    }
    return mapping.get(status, "not-satisfied")


def _cmmc_status_to_oscal(status):
    """Map CMMC assessment status to OSCAL finding status state."""
    mapping = {
        "met": "satisfied",
        "not_met": "not-satisfied",
        "partially_met": "not-satisfied",
        "not_assessed": "not-satisfied",
        "not_applicable": "satisfied",
    }
    return mapping.get(status, "not-satisfied")


def _stig_status_to_oscal(status):
    """Map STIG finding status to OSCAL finding status state."""
    mapping = {
        "Open": "not-satisfied",
        "NotAFinding": "satisfied",
        "Not_Applicable": "satisfied",
        "Not_Reviewed": "not-satisfied",
    }
    return mapping.get(status, "not-satisfied")


def generate_oscal_component_definition(project_id, output_dir=None, db_path=None):
    """Generate an OSCAL Component Definition JSON artifact.

    Creates a reusable component definition with control-implementations
    pulled from project_controls and SBOM data.

    Args:
        project_id: The project identifier.
        output_dir: Override output directory.
        db_path: Override database path.

    Returns:
        Dict with file_path, uuid, components_count, and validation result.
    """
    conn = _get_connection(db_path)
    try:
        project = _get_project(conn, project_id)
        controls = _get_controls(conn, project_id)
        sbom_records = _get_sbom_records(conn, project_id)
        baseline = _determine_baseline(project)

        cd_uuid = _generate_uuid()

        # Build components list
        components = []

        # Primary application component
        app_component_uuid = _generate_uuid()
        app_component = {
            "uuid": app_component_uuid,
            "type": "software",
            "title": project.get("name", "Application"),
            "description": project.get("description", "Primary application component."),
            "props": [
                {
                    "name": "type",
                    "ns": OSCAL_NS,
                    "value": project.get("type", "webapp"),
                },
                {
                    "name": "classification",
                    "ns": OSCAL_NS,
                    "value": project.get("classification", "CUI"),
                },
                {
                    "name": "impact-level",
                    "ns": OSCAL_NS,
                    "value": project.get("impact_level", "IL5"),
                },
            ],
            "control-implementations": [],
        }

        # Build control implementation for this component
        if controls:
            profile_href = FEDRAMP_PROFILE_URIS.get(
                baseline, FEDRAMP_PROFILE_URIS["moderate"]
            )
            impl_reqs = []
            for ctrl in controls:
                oscal_cid = _control_id_to_oscal(ctrl["control_id"])
                if not oscal_cid:
                    continue

                description = ctrl.get("implementation_description") or (
                    f"Control {oscal_cid} implementation: "
                    f"{ctrl.get('implementation_status', 'planned')}."
                )

                impl_reqs.append({
                    "uuid": _generate_uuid(),
                    "control-id": oscal_cid,
                    "description": description,
                    "props": [
                        {
                            "name": "implementation-status",
                            "ns": OSCAL_NS,
                            "value": ctrl.get("implementation_status", "planned"),
                        },
                    ],
                })

            app_component["control-implementations"].append({
                "uuid": _generate_uuid(),
                "source": profile_href,
                "description": (
                    f"Control implementations for "
                    f"{project.get('name', 'application')} aligned to "
                    f"FedRAMP {baseline.capitalize()} baseline."
                ),
                "implemented-requirements": impl_reqs,
            })

        components.append(app_component)

        # Add tech stack components
        for stack_key, stack_type in [
            ("tech_stack_backend", "Backend Framework"),
            ("tech_stack_frontend", "Frontend Framework"),
            ("tech_stack_database", "Database"),
        ]:
            value = project.get(stack_key)
            if value:
                components.append({
                    "uuid": _generate_uuid(),
                    "type": "software",
                    "title": f"{stack_type}: {value}",
                    "description": f"{stack_type} component: {value}",
                    "props": [
                        {
                            "name": "stack-layer",
                            "ns": OSCAL_NS,
                            "value": stack_key.replace("tech_stack_", ""),
                        },
                    ],
                })

        # Add SBOM-derived components
        for sbom in sbom_records:
            components.append({
                "uuid": _generate_uuid(),
                "type": "software",
                "title": f"SBOM: {sbom.get('format', 'cyclonedx')} v{sbom.get('version', '1.0')}",
                "description": (
                    f"Software Bill of Materials ({sbom.get('format', 'CycloneDX')}) "
                    f"with {sbom.get('component_count', 0)} components, "
                    f"{sbom.get('vulnerability_count', 0)} known vulnerabilities."
                ),
                "props": [
                    {
                        "name": "sbom-format",
                        "ns": OSCAL_NS,
                        "value": sbom.get("format", "cyclonedx"),
                    },
                    {
                        "name": "component-count",
                        "ns": OSCAL_NS,
                        "value": str(sbom.get("component_count", 0)),
                    },
                    {
                        "name": "vulnerability-count",
                        "ns": OSCAL_NS,
                        "value": str(sbom.get("vulnerability_count", 0)),
                    },
                ],
            })

        # Assemble component definition document
        cd_doc = {
            "component-definition": {
                "uuid": cd_uuid,
                "metadata": _build_metadata(
                    project, "Component Definition"
                ),
                "components": components,
                "back-matter": {
                    "resources": [],
                },
            },
        }

        # Write
        out_dir = _resolve_output_dir(project, project_id, output_dir)
        out_file = out_dir / "component-definition.oscal.json"

        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(cd_doc, f, indent=2, ensure_ascii=False)

        file_hash = _compute_file_hash(out_file)

        # Validate
        validation = validate_oscal(str(out_file), "component_definition")
        schema_valid = validation["valid"]

        # Store record
        _store_oscal_artifact(
            conn, project_id, "component_definition", str(out_file),
            file_hash, schema_valid, validation.get("errors")
        )

        # Audit
        _log_audit(conn, project_id, "OSCAL Component Definition generated", {
            "artifact_type": "component_definition",
            "oscal_version": OSCAL_VERSION,
            "uuid": cd_uuid,
            "components_count": len(components),
            "controls_count": len(controls),
            "sbom_records": len(sbom_records),
            "file_hash": file_hash,
            "schema_valid": schema_valid,
            "affected_files": [str(out_file)],
        })

        print("OSCAL Component Definition generated:")
        print(f"  File: {out_file}")
        print(f"  UUID: {cd_uuid}")
        print(f"  Components: {len(components)}")
        print(f"  Controls: {len(controls)}")
        print(f"  Valid: {schema_valid}")

        return {
            "file_path": str(out_file),
            "uuid": cd_uuid,
            "components_count": len(components),
            "controls_count": len(controls),
            "file_hash": file_hash,
            "validation": validation,
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_oscal(file_path, artifact_type=None):
    """Validate an OSCAL JSON file for structural correctness.

    Checks:
      - Valid JSON
      - Required top-level keys per artifact type
      - UUID format (RFC 4122 lowercase)
      - ISO 8601 timestamp format
      - Control ID format (lowercase with hyphens)

    Args:
        file_path: Path to the OSCAL JSON file.
        artifact_type: One of ssp, poam, assessment_results,
                       component_definition. If None, auto-detects.

    Returns:
        Dict with valid (bool) and errors (list of strings).
    """
    errors = []
    path = Path(file_path)

    # Check file exists
    if not path.exists():
        return {"valid": False, "errors": [f"File not found: {file_path}"]}

    # Parse JSON
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return {"valid": False, "errors": [f"Invalid JSON: {e}"]}

    if not isinstance(data, dict):
        return {"valid": False, "errors": ["Root must be a JSON object."]}

    # Determine artifact type from top-level key
    top_level_keys = {
        "ssp": "system-security-plan",
        "poam": "plan-of-action-and-milestones",
        "assessment_results": "assessment-results",
        "component_definition": "component-definition",
    }

    if artifact_type is None:
        # Auto-detect
        for at, key in top_level_keys.items():
            if key in data:
                artifact_type = at
                break
        if artifact_type is None:
            return {
                "valid": False,
                "errors": [
                    f"No recognized OSCAL top-level key found. "
                    f"Expected one of: {list(top_level_keys.values())}"
                ],
            }

    expected_key = top_level_keys.get(artifact_type)
    if expected_key and expected_key not in data:
        errors.append(
            f"Missing required top-level key: '{expected_key}'"
        )

    if expected_key and expected_key in data:
        doc = data[expected_key]

        # Check UUID at document level
        if "uuid" in doc:
            if not UUID_PATTERN.match(str(doc["uuid"])):
                errors.append(
                    f"Document UUID format invalid: '{doc['uuid']}'. "
                    f"Expected RFC 4122 lowercase UUID."
                )

        # Check metadata
        metadata = doc.get("metadata", {})
        if not metadata:
            errors.append("Missing 'metadata' block.")
        else:
            # Check last-modified timestamp
            last_mod = metadata.get("last-modified", "")
            if last_mod and not ISO_TIMESTAMP_PATTERN.match(last_mod):
                errors.append(
                    f"Metadata 'last-modified' timestamp format invalid: "
                    f"'{last_mod}'. Expected ISO 8601 with Z suffix."
                )

            # Check oscal-version
            oscal_ver = metadata.get("oscal-version", "")
            if oscal_ver and oscal_ver != OSCAL_VERSION:
                errors.append(
                    f"OSCAL version mismatch: '{oscal_ver}' "
                    f"(expected '{OSCAL_VERSION}')."
                )

            # Check required metadata fields
            for field in ["title", "last-modified", "version", "oscal-version"]:
                if field not in metadata:
                    errors.append(f"Missing metadata field: '{field}'.")

        # Artifact-specific validation
        if artifact_type == "ssp":
            _validate_ssp(doc, errors)
        elif artifact_type == "poam":
            _validate_poam(doc, errors)
        elif artifact_type == "assessment_results":
            _validate_assessment_results(doc, errors)
        elif artifact_type == "component_definition":
            _validate_component_definition(doc, errors)

    # Walk entire document for UUID and control-id validation
    _validate_uuids_recursive(data, errors, max_errors=20)
    _validate_control_ids_recursive(data, errors, max_errors=20)

    return {
        "valid": len(errors) == 0,
        "errors": errors,
    }


def _validate_ssp(doc, errors):
    """Validate SSP-specific structure."""
    if "import-profile" not in doc:
        errors.append("SSP missing 'import-profile' block.")
    elif "href" not in doc.get("import-profile", {}):
        errors.append("SSP 'import-profile' missing 'href'.")

    if "system-characteristics" not in doc:
        errors.append("SSP missing 'system-characteristics' block.")
    else:
        sc = doc["system-characteristics"]
        for field in [
            "system-name", "description", "security-sensitivity-level",
            "security-impact-level", "status", "authorization-boundary",
        ]:
            if field not in sc:
                errors.append(
                    f"SSP 'system-characteristics' missing '{field}'."
                )

    if "system-implementation" not in doc:
        errors.append("SSP missing 'system-implementation' block.")

    if "control-implementation" not in doc:
        errors.append("SSP missing 'control-implementation' block.")
    else:
        ci = doc["control-implementation"]
        if "implemented-requirements" not in ci:
            errors.append(
                "SSP 'control-implementation' missing "
                "'implemented-requirements'."
            )


def _validate_poam(doc, errors):
    """Validate POA&M-specific structure."""
    if "poam-items" not in doc:
        errors.append("POA&M missing 'poam-items' array.")
    elif not isinstance(doc["poam-items"], list):
        errors.append("POA&M 'poam-items' must be an array.")


def _validate_assessment_results(doc, errors):
    """Validate Assessment Results-specific structure."""
    if "results" not in doc:
        errors.append("Assessment Results missing 'results' array.")
    elif not isinstance(doc["results"], list):
        errors.append("Assessment Results 'results' must be an array.")
    elif len(doc["results"]) == 0:
        errors.append("Assessment Results 'results' array is empty.")


def _validate_component_definition(doc, errors):
    """Validate Component Definition-specific structure."""
    if "components" not in doc:
        errors.append("Component Definition missing 'components' array.")
    elif not isinstance(doc["components"], list):
        errors.append("Component Definition 'components' must be an array.")
    elif len(doc["components"]) == 0:
        errors.append("Component Definition 'components' array is empty.")


def _validate_uuids_recursive(obj, errors, path="", max_errors=20):
    """Recursively validate UUID fields in the document."""
    if len(errors) >= max_errors:
        return

    if isinstance(obj, dict):
        for key, value in obj.items():
            current_path = f"{path}.{key}" if path else key
            if key == "uuid" and isinstance(value, str):
                if not UUID_PATTERN.match(value):
                    errors.append(
                        f"Invalid UUID at '{current_path}': '{value}'."
                    )
                    if len(errors) >= max_errors:
                        return
            elif key.endswith("-uuid") and isinstance(value, str):
                if not UUID_PATTERN.match(value):
                    errors.append(
                        f"Invalid UUID reference at '{current_path}': "
                        f"'{value}'."
                    )
                    if len(errors) >= max_errors:
                        return
            else:
                _validate_uuids_recursive(
                    value, errors, current_path, max_errors
                )
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _validate_uuids_recursive(
                item, errors, f"{path}[{i}]", max_errors
            )


def _validate_control_ids_recursive(obj, errors, path="", max_errors=20):
    """Recursively validate control-id fields use OSCAL lowercase format."""
    if len(errors) >= max_errors:
        return

    if isinstance(obj, dict):
        for key, value in obj.items():
            current_path = f"{path}.{key}" if path else key
            if key == "control-id" and isinstance(value, str):
                if value != value.lower():
                    errors.append(
                        f"Control ID not lowercase at '{current_path}': "
                        f"'{value}'. OSCAL requires lowercase."
                    )
                    if len(errors) >= max_errors:
                        return
            else:
                _validate_control_ids_recursive(
                    value, errors, current_path, max_errors
                )
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _validate_control_ids_recursive(
                item, errors, f"{path}[{i}]", max_errors
            )


# ---------------------------------------------------------------------------
# Aggregate generation
# ---------------------------------------------------------------------------

def generate_all_oscal(project_id, output_dir=None, db_path=None):
    """Generate all four OSCAL artifact types for a project.

    Generates SSP, POA&M, Assessment Results, and Component Definition
    in sequence. Returns a summary dict.

    Args:
        project_id: The project identifier.
        output_dir: Override output directory.
        db_path: Override database path.

    Returns:
        Dict with results for each artifact type and overall summary.
    """
    results = {}
    artifact_types = [
        ("ssp", generate_oscal_ssp),
        ("poam", generate_oscal_poam),
        ("assessment_results", generate_oscal_assessment_results),
        ("component_definition", generate_oscal_component_definition),
    ]

    success_count = 0
    failure_count = 0

    for artifact_name, generator_fn in artifact_types:
        try:
            result = generator_fn(
                project_id, output_dir=output_dir, db_path=db_path
            )
            results[artifact_name] = {
                "status": "success",
                "result": result,
            }
            success_count += 1
        except Exception as e:
            results[artifact_name] = {
                "status": "error",
                "error": str(e),
            }
            failure_count += 1
            print(
                f"Error generating OSCAL {artifact_name}: {e}",
                file=sys.stderr,
            )

    summary = {
        "project_id": project_id,
        "oscal_version": OSCAL_VERSION,
        "artifacts_generated": success_count,
        "artifacts_failed": failure_count,
        "total": len(artifact_types),
        "results": results,
    }

    print("\nOSCAL generation summary:")
    print(f"  Project: {project_id}")
    print(f"  OSCAL Version: {OSCAL_VERSION}")
    print(f"  Generated: {success_count}/{len(artifact_types)}")
    if failure_count > 0:
        print(f"  Failed: {failure_count}")
        for name, res in results.items():
            if res["status"] == "error":
                print(f"    {name}: {res['error']}")

    return summary


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "NIST OSCAL 1.1.2 Artifact Generator -- "
            "Generate SSP, POA&M, Assessment Results, and "
            "Component Definition in OSCAL JSON format."
        ),
    )
    parser.add_argument(
        "--project-id",
        required=False,
        help="Project ID (required for generation)",
    )
    parser.add_argument(
        "--artifact",
        choices=[
            "ssp", "poam", "assessment_results",
            "component_definition", "all",
        ],
        default="all",
        help="Artifact type to generate (default: all)",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory for OSCAL artifacts",
    )
    parser.add_argument(
        "--format",
        choices=["json"],
        default="json",
        help="Output format (currently only JSON supported)",
    )
    parser.add_argument(
        "--validate",
        help="Validate an existing OSCAL JSON file (no project-id required)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        help="Override database path",
    )

    args = parser.parse_args()

    # Validation-only mode
    if args.validate:
        result = validate_oscal(args.validate)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if result["valid"]:
                print(f"VALID: {args.validate}")
            else:
                print(f"INVALID: {args.validate}")
                for err in result["errors"]:
                    print(f"  - {err}")
        sys.exit(0 if result["valid"] else 1)

    # Generation mode requires project-id
    if not args.project_id:
        parser.error("--project-id is required for artifact generation")

    # Dispatch to generator
    generators = {
        "ssp": generate_oscal_ssp,
        "poam": generate_oscal_poam,
        "assessment_results": generate_oscal_assessment_results,
        "component_definition": generate_oscal_component_definition,
        "all": generate_all_oscal,
    }

    generator_fn = generators[args.artifact]

    try:
        result = generator_fn(
            project_id=args.project_id,
            output_dir=args.output_dir,
            db_path=args.db_path,
        )

        if args.json:
            print(json.dumps(result, indent=2, default=str))

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

####################################################################
# CUI // SP-CTI | Department of Defense
####################################################################
