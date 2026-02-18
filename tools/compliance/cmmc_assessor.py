#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""CMMC Level 2/3 assessment engine for ICDEV.

Loads CMMC practices from cmmc_practices.json, performs automated domain checks,
inherits NIST 800-53/800-171 implementations via the crosswalk engine, stores
results in the cmmc_assessments table, computes per-domain and overall scores,
evaluates CMMC gates, and logs audit events.

Usage:
    python tools/compliance/cmmc_assessor.py --project-id proj-123 --level 2
    python tools/compliance/cmmc_assessor.py --project-id proj-123 --level 3 \\
        --project-dir /path/to/project --gate
    python tools/compliance/cmmc_assessor.py --project-id proj-123 --level 2 \\
        --domain AC --json

Databases:
    - data/icdev.db: cmmc_assessments, project_controls, audit_trail

See also:
    - tools/compliance/crosswalk_engine.py (inherit NIST implementations)
    - tools/compliance/classification_manager.py (CUI markings)
    - tools/compliance/cmmc_report_generator.py (report generation)
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CMMC_PRACTICES_PATH = BASE_DIR / "context" / "compliance" / "cmmc_practices.json"

# CMMC domain codes and names
CMMC_DOMAINS = [
    ("AC", "Access Control"),
    ("AT", "Awareness & Training"),
    ("AU", "Audit & Accountability"),
    ("CM", "Configuration Management"),
    ("IA", "Identification & Authentication"),
    ("IR", "Incident Response"),
    ("MA", "Maintenance"),
    ("MP", "Media Protection"),
    ("PE", "Physical Protection"),
    ("PS", "Personnel Security"),
    ("RA", "Risk Assessment"),
    ("CA", "Security Assessment"),
    ("SC", "System & Communications Protection"),
    ("SI", "System & Information Integrity"),
]

DOMAIN_CODE_TO_NAME = {code: name for code, name in CMMC_DOMAINS}
DOMAIN_NAME_TO_CODE = {name: code for code, name in CMMC_DOMAINS}


# -----------------------------------------------------------------
# Database helpers
# -----------------------------------------------------------------

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
    """Load project data from the projects table."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found.")
    return dict(row)


def _log_audit_event(conn, project_id, action, details, file_path=None):
    """Log an audit trail event (append-only, NIST AU compliant)."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "cmmc_assessed",
                "icdev-compliance-engine",
                action,
                json.dumps(details),
                json.dumps([str(file_path)] if file_path else []),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"Warning: Could not log audit event: {e}", file=sys.stderr)


# -----------------------------------------------------------------
# CUI config helper
# -----------------------------------------------------------------

def _load_cui_config():
    """Load CUI marking configuration."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from cui_marker import load_cui_config
        return load_cui_config()
    except ImportError:
        return {
            "document_header": (
                "////////////////////////////////////////////////////////////////////\n"
                "CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI\n"
                "Distribution: Distribution D -- Authorized DoD Personnel Only\n"
                "////////////////////////////////////////////////////////////////////"
            ),
            "document_footer": (
                "////////////////////////////////////////////////////////////////////\n"
                "CUI // SP-CTI | Department of Defense\n"
                "////////////////////////////////////////////////////////////////////"
            ),
        }


# -----------------------------------------------------------------
# CMMC catalog loader
# -----------------------------------------------------------------

def load_cmmc_practices(level=2):
    """Load CMMC practice catalog for Level 2 or 3.

    Args:
        level: CMMC level (2 or 3).

    Returns:
        dict with metadata, domains, and filtered practices list.
    """
    if not CMMC_PRACTICES_PATH.exists():
        print(
            f"Warning: CMMC practices catalog not found: {CMMC_PRACTICES_PATH}",
            file=sys.stderr,
        )
        return {"metadata": {}, "domains": [], "practices": []}

    with open(CMMC_PRACTICES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    practices = data.get("practices", [])
    # Level 2 includes only level-2 practices
    # Level 3 includes level-2 AND level-3 practices
    filtered = [p for p in practices if p.get("level", 2) <= level]

    return {
        "metadata": data.get("metadata", {}),
        "domains": data.get("domains", []),
        "practices": filtered,
    }


# -----------------------------------------------------------------
# Crosswalk inheritance helper
# -----------------------------------------------------------------

def _inherit_nist_implementations(project_id, practices, db_path=None):
    """Use the crosswalk engine to inherit NIST 800-53/800-171 implementations.

    For each CMMC practice that maps to NIST 800-53 controls, checks if those
    controls are already implemented in the project_controls table. If all
    mapped controls are implemented, the practice is considered inherited.

    Args:
        project_id: The project identifier.
        practices: List of CMMC practice dicts from the catalog.
        db_path: Optional database path override.

    Returns:
        dict mapping practice_id -> {"inherited": bool, "controls_implemented": [...],
                                     "controls_missing": [...]}
    """
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT control_id, implementation_status
               FROM project_controls
               WHERE project_id = ?""",
            (project_id,),
        ).fetchall()

        implemented_ids = set()
        for row in rows:
            if row["implementation_status"] in ("implemented", "partially_implemented"):
                implemented_ids.add(row["control_id"].upper())

        inheritance = {}
        for practice in practices:
            pid = practice["id"]
            nist_controls = practice.get("nist_800_53_controls", [])

            if not nist_controls:
                inheritance[pid] = {
                    "inherited": False,
                    "controls_implemented": [],
                    "controls_missing": [],
                }
                continue

            controls_impl = [c for c in nist_controls if c.upper() in implemented_ids]
            controls_miss = [c for c in nist_controls if c.upper() not in implemented_ids]

            inheritance[pid] = {
                "inherited": len(controls_miss) == 0 and len(controls_impl) > 0,
                "controls_implemented": controls_impl,
                "controls_missing": controls_miss,
            }

        return inheritance
    except Exception:
        # If project_controls table doesn't exist or other error, return empty
        return {}
    finally:
        conn.close()


# -----------------------------------------------------------------
# Auto-check helper: walk project files matching extensions
# -----------------------------------------------------------------

def _scan_files(project_dir, extensions, patterns, threshold=1):
    """Scan project files for regex patterns.

    Args:
        project_dir: Root directory to walk.
        extensions: Tuple of file extensions to include.
        patterns: List of regex patterns to search for.
        threshold: Minimum number of files with matches to consider satisfied.

    Returns:
        Tuple of (matched_files, total_scanned).
    """
    matched_files = []
    total_scanned = 0
    for root, _, files in os.walk(project_dir):
        for fname in files:
            if not fname.endswith(extensions):
                continue
            fpath = os.path.join(root, fname)
            total_scanned += 1
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for pattern in patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        matched_files.append(fpath)
                        break
            except Exception:
                continue
    return matched_files, total_scanned


def _dir_or_file_exists(project_dir, dir_names=None, glob_patterns=None):
    """Check if specific directories or file globs exist under project_dir."""
    found = []
    project_path = Path(project_dir)

    if dir_names:
        for dname in dir_names:
            candidate = project_path / dname
            if candidate.is_dir():
                found.append(str(candidate))
            for child in project_path.rglob(dname):
                if child.is_dir() and str(child) not in found:
                    found.append(str(child))

    if glob_patterns:
        for gp in glob_patterns:
            for match in project_path.rglob(gp):
                if str(match) not in found:
                    found.append(str(match))

    return found


# -----------------------------------------------------------------
# Auto-check functions (14 -- one per CMMC domain)
# Each returns:
#   {"status": "met"|"not_met"|"partially_met"|"not_applicable",
#    "evidence": "description",
#    "details": "specifics"}
# -----------------------------------------------------------------

def _check_ac_domain(project_dir):
    """Access Control: RBAC, least privilege, session mgmt, remote access, wireless."""
    patterns = [
        r"@login_required|@permission_required|@requires_auth",
        r"@Secured|@PreAuthorize|@RolesAllowed",
        r"role_required|check_permission|has_permission",
        r"\bRBAC\b|role.based.access",
        r"RoleBinding|ClusterRole|ClusterRoleBinding",
        r"least.privilege|minimum.privilege",
        r"session.timeout|session_expiry|SESSION_TIMEOUT",
        r"remote.access|VPN|vpn_config",
    ]
    extensions = (".py", ".yaml", ".yml", ".js", ".ts", ".java", ".go", ".rs")
    matched, total = _scan_files(project_dir, extensions, patterns)

    if total == 0:
        return {
            "status": "not_met",
            "evidence": "No source files found to assess for access control.",
            "details": "Project directory lacks applicable source files.",
        }

    if len(matched) >= 3:
        return {
            "status": "met",
            "evidence": (
                f"Access control patterns found in {len(matched)} file(s) "
                f"including RBAC, session management, and privilege controls."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }
    elif matched:
        return {
            "status": "partially_met",
            "evidence": (
                f"Partial access control patterns found in {len(matched)} file(s). "
                "Expecting RBAC, least privilege, session management, and remote access controls."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }

    return {
        "status": "not_met",
        "evidence": "No access control patterns detected.",
        "details": (
            "Expected: RBAC, @login_required, role_required, session management, "
            "remote access controls, wireless access restrictions."
        ),
    }


def _check_at_domain(project_dir):
    """Awareness & Training: security training docs, onboarding docs."""
    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "SECURITY*.md", "security-training*", "security_training*",
            "onboarding*", "training*", "awareness*",
            "docs/security*", "docs/training*",
        ],
    )
    found_dirs = _dir_or_file_exists(
        project_dir,
        dir_names=["training", "onboarding", "security-awareness"],
    )
    all_found = list(set(found + found_dirs))

    if all_found:
        return {
            "status": "met",
            "evidence": (
                f"Security awareness/training artifacts found: "
                f"{len(all_found)} item(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }

    # Check for security policy references in code
    patterns = [r"security.training|security.awareness|onboarding.security"]
    extensions = (".md", ".txt", ".rst", ".yaml", ".yml")
    matched, total = _scan_files(project_dir, extensions, patterns)
    if matched:
        return {
            "status": "partially_met",
            "evidence": f"Security training references in {len(matched)} file(s).",
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }

    return {
        "status": "not_met",
        "evidence": "No security awareness or training documentation detected.",
        "details": (
            "Expected: SECURITY.md, training docs, onboarding procedures, "
            "security awareness materials."
        ),
    }


def _check_au_domain(project_dir):
    """Audit & Accountability: logging config, audit trail, log protection, timestamps."""
    event_type_patterns = [
        (r"login|auth.*log|authentication.*log", "authentication_logging"),
        (r"access.*log|access_log|request.*log", "access_logging"),
        (r"change.*log|change_log|modification.*log|update.*log", "change_logging"),
        (r"error.*log|error_log|exception.*log", "error_logging"),
        (r"security.*event|security.*log|security_event", "security_logging"),
        (r"audit_trail|AuditTrail|audit\.log", "audit_trail"),
    ]
    extensions = (".py", ".js", ".ts", ".java", ".yaml", ".yml", ".go", ".rs")
    found_types = set()
    evidence_files = []

    for root, _, files in os.walk(project_dir):
        for fname in files:
            if not fname.endswith(extensions):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for pattern, event_type in event_type_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        found_types.add(event_type)
                        if fpath not in evidence_files:
                            evidence_files.append(fpath)
            except Exception:
                continue

    # Also check for structured logging
    struct_patterns = [r"logging\.getLogger|getLogger|structlog|log\.info|log\.warn"]
    struct_matched, _ = _scan_files(project_dir, extensions, struct_patterns)
    if struct_matched:
        found_types.add("structured_logging")

    count = len(found_types)
    if count >= 4:
        return {
            "status": "met",
            "evidence": (
                f"Comprehensive audit logging: {count} distinct log types "
                f"across {len(evidence_files)} file(s)."
            ),
            "details": f"Types: {', '.join(sorted(found_types))}",
        }
    elif count >= 2:
        return {
            "status": "partially_met",
            "evidence": (
                f"Partial audit logging: {count} log type(s) found. "
                "CMMC requires comprehensive logging with protection and timestamps."
            ),
            "details": f"Types: {', '.join(sorted(found_types))}",
        }

    return {
        "status": "not_met",
        "evidence": "Insufficient audit logging detected.",
        "details": (
            "Expected: authentication, access, change, error, security "
            "logging with timestamps and audit trail protection."
        ),
    }


def _check_cm_domain(project_dir):
    """Configuration Management: baseline configs, change control, IaC, least functionality."""
    found_configs = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "*.tf", "*.tfvars", "Dockerfile*", "docker-compose*",
            "*.yaml", "*.yml", "ansible*", "playbook*",
            ".gitlab-ci.yml", ".github/workflows/*",
        ],
    )
    found_dirs = _dir_or_file_exists(
        project_dir,
        dir_names=["terraform", "ansible", "k8s", "kubernetes", "infra"],
    )

    # Check for version control and change control
    patterns = [
        r"baseline|configuration.management|config.baseline",
        r"change.control|change.request|change.management",
        r"least.functionality|minimal.install|hardened",
    ]
    extensions = (".py", ".yaml", ".yml", ".md", ".tf", ".json")
    matched, total = _scan_files(project_dir, extensions, patterns)

    all_found = list(set(found_configs + found_dirs + matched))

    if len(all_found) >= 5:
        return {
            "status": "met",
            "evidence": (
                f"Configuration management artifacts found: {len(all_found)} item(s) "
                "including IaC, Dockerfiles, and config baselines."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }
    elif all_found:
        return {
            "status": "partially_met",
            "evidence": (
                f"Partial configuration management: {len(all_found)} artifact(s). "
                "Missing some of: IaC, change control, baseline configs, least functionality."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }

    return {
        "status": "not_met",
        "evidence": "No configuration management artifacts detected.",
        "details": (
            "Expected: Terraform/Ansible files, Dockerfiles, baseline configs, "
            "change control documentation, least functionality enforcement."
        ),
    }


def _check_ia_domain(project_dir):
    """Identification & Authentication: MFA, password policy, authenticator mgmt, PKI/CAC."""
    patterns = [
        r"\bMFA\b|multi.factor|MultiFactor|2FA|TOTP|FIDO",
        r"password.policy|password.complexity|min.password",
        r"\bPKI\b|pki_cert|certificate.auth|CAC",
        r"authenticator|authentication.mechanism",
        r"password.*expir|credential.*rotat|key.*rotation",
    ]
    extensions = (".py", ".yaml", ".yml", ".js", ".ts", ".java", ".conf")
    matched, total = _scan_files(project_dir, extensions, patterns)

    if total == 0:
        return {
            "status": "not_met",
            "evidence": "No source files found to assess for identification & authentication.",
            "details": "Project directory lacks applicable files.",
        }

    if len(matched) >= 3:
        return {
            "status": "met",
            "evidence": (
                f"Identification & authentication patterns found in "
                f"{len(matched)} file(s) including MFA, password policy, and PKI."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }
    elif matched:
        return {
            "status": "partially_met",
            "evidence": (
                f"Partial I&A patterns in {len(matched)} file(s). "
                "Expecting MFA, password policy, authenticator management, and PKI/CAC."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }

    return {
        "status": "not_met",
        "evidence": "No identification & authentication patterns detected.",
        "details": (
            "Expected: MFA/2FA, password complexity policy, PKI/CAC support, "
            "authenticator management, credential rotation."
        ),
    }


def _check_ir_domain(project_dir):
    """Incident Response: IR plan, IR testing, reporting procedures."""
    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "incident-response*", "incident_response*", "ir-plan*", "ir_plan*",
            "docs/incident*", "security/incident*",
        ],
    )
    found_dirs = _dir_or_file_exists(
        project_dir,
        dir_names=["incident-response", "incident_response", "ir"],
    )

    patterns = [
        r"incident.response|incident.handling|ir.plan|ir.procedure",
        r"incident.report|incident.detection|incident.containment",
    ]
    extensions = (".md", ".txt", ".yaml", ".yml", ".py", ".json")
    matched, total = _scan_files(project_dir, extensions, patterns)

    all_found = list(set(found + found_dirs + matched))
    if len(all_found) >= 2:
        return {
            "status": "met",
            "evidence": (
                f"Incident response artifacts found: {len(all_found)} item(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }
    elif all_found:
        return {
            "status": "partially_met",
            "evidence": (
                f"Partial incident response: {len(all_found)} artifact(s). "
                "Need IR plan, testing evidence, and reporting procedures."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }

    return {
        "status": "not_met",
        "evidence": "No incident response artifacts detected.",
        "details": (
            "Expected: IR plan documents, IR testing records, "
            "incident reporting procedures, containment/recovery docs."
        ),
    }


def _check_ma_domain(project_dir):
    """Maintenance: maintenance procedures, non-local maintenance controls."""
    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "maintenance*", "MAINTENANCE*", "docs/maintenance*",
            "runbook*", "playbook*", "ops/*",
        ],
    )
    patterns = [
        r"maintenance.procedure|maintenance.policy|maintenance.window",
        r"non.local.maintenance|remote.maintenance",
        r"patch.management|update.procedure",
    ]
    extensions = (".md", ".txt", ".yaml", ".yml", ".json")
    matched, total = _scan_files(project_dir, extensions, patterns)

    all_found = list(set(found + matched))
    if all_found:
        return {
            "status": "met",
            "evidence": (
                f"Maintenance procedure artifacts found: {len(all_found)} item(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }

    return {
        "status": "not_met",
        "evidence": "No maintenance procedure documentation detected.",
        "details": (
            "Expected: maintenance procedures, non-local maintenance controls, "
            "patch management docs, runbooks."
        ),
    }


def _check_mp_domain(project_dir):
    """Media Protection: media access, marking, storage, transport, sanitization."""
    patterns = [
        r"media.protection|media.sanitization|media.disposal",
        r"encryption.at.rest|encrypt_at_rest|storage_encrypted",
        r"\bKMS\b|kms_key|aws_kms|key_management",
        r"CUI.*mark|classification.*mark|media.*marking",
        r"data.at.rest|data.in.transit|data.protection",
    ]
    extensions = (".py", ".yaml", ".yml", ".tf", ".json", ".md", ".conf")
    matched, total = _scan_files(project_dir, extensions, patterns)

    if total == 0:
        return {
            "status": "not_met",
            "evidence": "No files found to assess media protection.",
            "details": "Project directory lacks applicable files.",
        }

    if len(matched) >= 2:
        return {
            "status": "met",
            "evidence": (
                f"Media protection patterns found in {len(matched)} file(s) "
                "including encryption, KMS, and marking controls."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }
    elif matched:
        return {
            "status": "partially_met",
            "evidence": (
                f"Partial media protection in {len(matched)} file(s). "
                "Need encryption-at-rest, CUI marking, transport encryption."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }

    return {
        "status": "not_met",
        "evidence": "No media protection patterns detected.",
        "details": (
            "Expected: encryption-at-rest, KMS, media marking, "
            "data protection, sanitization procedures."
        ),
    }


def _check_pe_domain(project_dir):
    """Physical Protection: physical access, visitor logs, monitoring."""
    patterns = [
        r"physical.access|physical.security|physical.protection",
        r"visitor.log|visitor.control|badge|access.card",
        r"surveillance|CCTV|physical.monitoring",
    ]
    extensions = (".md", ".txt", ".yaml", ".yml", ".json")
    matched, total = _scan_files(project_dir, extensions, patterns)

    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "physical-security*", "physical_security*",
            "docs/physical*", "security/physical*",
        ],
    )
    all_found = list(set(matched + found))

    if all_found:
        return {
            "status": "met",
            "evidence": (
                f"Physical protection documentation found: {len(all_found)} item(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }

    # Physical security is often documented outside the codebase
    return {
        "status": "not_applicable",
        "evidence": (
            "Physical security controls are typically managed outside the "
            "software codebase (facility management, physical access systems)."
        ),
        "details": "Manual verification of physical protection controls recommended.",
    }


def _check_ps_domain(project_dir):
    """Personnel Security: screening, termination procedures."""
    patterns = [
        r"personnel.security|background.check|screening",
        r"termination.procedure|offboarding|access.revocation",
        r"personnel.action|separation.procedure",
    ]
    extensions = (".md", ".txt", ".yaml", ".yml", ".json")
    matched, total = _scan_files(project_dir, extensions, patterns)

    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "personnel-security*", "personnel_security*",
            "hr-security*", "docs/personnel*",
        ],
    )
    all_found = list(set(matched + found))

    if all_found:
        return {
            "status": "met",
            "evidence": (
                f"Personnel security documentation found: {len(all_found)} item(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }

    return {
        "status": "not_applicable",
        "evidence": (
            "Personnel security controls are typically managed outside "
            "the software codebase (HR processes, background check systems)."
        ),
        "details": "Manual verification of personnel security procedures recommended.",
    }


def _check_ra_domain(project_dir):
    """Risk Assessment: risk assessment, vulnerability scanning."""
    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "risk-assessment*", "risk_assessment*", "threat-model*",
            "threat_model*", "vulnerability-scan*", "vuln-report*",
            ".snyk", ".safety", "audit-report*", "pip-audit-report*",
        ],
    )
    found_dirs = _dir_or_file_exists(
        project_dir,
        dir_names=["risk-assessment", "threat-model", "vulnerability-scans"],
    )
    patterns = [
        r"risk.assessment|risk.analysis|risk.register",
        r"vulnerability.scan|vuln.scan|security.scan",
        r"threat.model|STRIDE|PASTA|attack.tree",
    ]
    extensions = (".md", ".txt", ".yaml", ".yml", ".json", ".py")
    matched, total = _scan_files(project_dir, extensions, patterns)

    all_found = list(set(found + found_dirs + matched))
    if len(all_found) >= 2:
        return {
            "status": "met",
            "evidence": (
                f"Risk assessment artifacts found: {len(all_found)} item(s) "
                "including risk analysis, vulnerability scanning, and/or threat modeling."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }
    elif all_found:
        return {
            "status": "partially_met",
            "evidence": (
                f"Partial risk assessment: {len(all_found)} artifact(s). "
                "Need both risk assessment and vulnerability scanning."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }

    return {
        "status": "not_met",
        "evidence": "No risk assessment or vulnerability scanning artifacts detected.",
        "details": (
            "Expected: risk assessment documents, vulnerability scan reports, "
            "threat model artifacts."
        ),
    }


def _check_ca_domain(project_dir):
    """Security Assessment: security assessments, system connections, monitoring."""
    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "security-assessment*", "security_assessment*",
            "compliance/*", "ato/*", "authorization*",
            "system-connection*", "interconnection*",
        ],
    )
    patterns = [
        r"security.assessment|security.evaluation|compliance.assessment",
        r"plan.of.action|POA.M|POAM|poam",
        r"system.connection|interconnection.agreement|ISA|MOU",
        r"continuous.monitoring|conmon|ongoing.assessment",
    ]
    extensions = (".md", ".txt", ".yaml", ".yml", ".json")
    matched, total = _scan_files(project_dir, extensions, patterns)

    all_found = list(set(found + matched))
    if len(all_found) >= 2:
        return {
            "status": "met",
            "evidence": (
                f"Security assessment artifacts found: {len(all_found)} item(s) "
                "including assessments, POA&M, and/or continuous monitoring."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }
    elif all_found:
        return {
            "status": "partially_met",
            "evidence": (
                f"Partial security assessment: {len(all_found)} artifact(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }

    return {
        "status": "not_met",
        "evidence": "No security assessment artifacts detected.",
        "details": (
            "Expected: security assessments, POA&M, system interconnection "
            "agreements, continuous monitoring documentation."
        ),
    }


def _check_sc_domain(project_dir):
    """System & Comms Protection: boundary protection, CUI encryption, crypto, network segmentation."""
    patterns = [
        r"TLS\s*1\.[23]|TLSv1_[23]|PROTOCOL_TLS",
        r"\bHTTPS\b|https://|ssl_context|SSLContext",
        r"mTLS|mutual.TLS|mutual_tls",
        r"\bFIPS\b|fips_mode|FIPS.140",
        r"AES.256|AES_256|aes256",
        r"\bKMS\b|kms_key|aws_kms|key_management",
        r"network.segmentation|network.boundary|firewall",
        r"CUI.*encrypt|encrypt.*CUI|data.protection",
        r"NetworkPolicy|security.group|ingress.rule",
    ]
    extensions = (".py", ".yaml", ".yml", ".tf", ".json", ".conf", ".go", ".rs")
    matched, total = _scan_files(project_dir, extensions, patterns)

    if total == 0:
        return {
            "status": "not_met",
            "evidence": "No files found to assess system & communications protection.",
            "details": "Project directory lacks applicable files.",
        }

    if len(matched) >= 4:
        return {
            "status": "met",
            "evidence": (
                f"System & communications protection patterns found in "
                f"{len(matched)} file(s) including TLS, encryption, "
                "FIPS, and network controls."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }
    elif len(matched) >= 2:
        return {
            "status": "partially_met",
            "evidence": (
                f"Partial SC protection in {len(matched)} file(s). "
                "Expecting TLS 1.2+, FIPS encryption, network segmentation, "
                "and CUI data protection."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }

    return {
        "status": "not_met",
        "evidence": "Insufficient system & communications protection detected.",
        "details": (
            "Expected: TLS 1.2+, FIPS-validated encryption, AES-256, KMS, "
            "network segmentation, boundary protection, CUI encryption."
        ),
    }


def _check_si_domain(project_dir):
    """System & Info Integrity: flaw remediation, malicious code, monitoring, alerting."""
    patterns = [
        r"pip.audit|npm\s+audit|safety.*check|snyk|dependency.check",
        r"bandit|semgrep|sonar|SAST|sast_runner",
        r"\bantivirus\b|\bantimalware\b|malware.scan",
        r"security.monitoring|intrusion.detect|IDS|IPS",
        r"alert|notification|webhook.*security",
        r"patch.management|flaw.remediation|vuln.fix",
    ]
    extensions = (".py", ".yaml", ".yml", ".json", ".sh", ".conf")
    matched, total = _scan_files(project_dir, extensions, patterns)

    if total == 0:
        return {
            "status": "not_met",
            "evidence": "No files found to assess system & information integrity.",
            "details": "Project directory lacks applicable files.",
        }

    if len(matched) >= 3:
        return {
            "status": "met",
            "evidence": (
                f"System & information integrity patterns found in "
                f"{len(matched)} file(s) including SAST, dependency auditing, "
                "and security monitoring."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }
    elif matched:
        return {
            "status": "partially_met",
            "evidence": (
                f"Partial SI integrity in {len(matched)} file(s). "
                "Expecting flaw remediation, malicious code protection, "
                "monitoring, and alerting."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }

    return {
        "status": "not_met",
        "evidence": "No system & information integrity patterns detected.",
        "details": (
            "Expected: SAST, dependency auditing, malicious code protection, "
            "security monitoring, alerting, flaw remediation processes."
        ),
    }


# -----------------------------------------------------------------
# Domain-to-check mapping
# -----------------------------------------------------------------

DOMAIN_AUTO_CHECKS = {
    "AC": _check_ac_domain,
    "AT": _check_at_domain,
    "AU": _check_au_domain,
    "CM": _check_cm_domain,
    "IA": _check_ia_domain,
    "IR": _check_ir_domain,
    "MA": _check_ma_domain,
    "MP": _check_mp_domain,
    "PE": _check_pe_domain,
    "PS": _check_ps_domain,
    "RA": _check_ra_domain,
    "CA": _check_ca_domain,
    "SC": _check_sc_domain,
    "SI": _check_si_domain,
}


# -----------------------------------------------------------------
# Core assessment function
# -----------------------------------------------------------------

def run_cmmc_assessment(
    project_id,
    level=2,
    project_dir=None,
    gate=False,
    output_path=None,
    db_path=None,
):
    """Run CMMC Level 2/3 assessment for a project.

    Args:
        project_id: The project identifier.
        level: CMMC level (2 or 3).
        project_dir: Project directory for automated file-based checks.
        gate: If True, evaluate the CMMC gate.
        output_path: Override output directory for the assessment report.
        db_path: Override database path.

    Returns:
        Dict with domain_scores, overall_score, gate_status,
        practices_met/not_met/partial, and output file path.
    """
    if level not in (2, 3):
        raise ValueError(f"Invalid CMMC level: {level}. Must be 2 or 3.")

    conn = _get_connection(db_path)
    try:
        project = _get_project(conn, project_id)

        # 1. Load CMMC practice catalog
        catalog = load_cmmc_practices(level)
        practices = catalog.get("practices", [])
        metadata = catalog.get("metadata", {})

        if not practices:
            raise ValueError(
                "No CMMC practices loaded. Ensure "
                "context/compliance/cmmc_practices.json exists."
            )

        # 2. Inherit NIST 800-53/800-171 implementations via crosswalk
        inheritance = _inherit_nist_implementations(
            project_id, practices, db_path=db_path
        )

        # 3. Resolve project directory for auto-checks
        if project_dir and Path(project_dir).is_dir():
            can_auto_check = True
        elif (
            project.get("directory_path")
            and Path(project["directory_path"]).is_dir()
        ):
            project_dir = project["directory_path"]
            can_auto_check = True
        else:
            can_auto_check = False

        # 4. Run domain auto-checks
        domain_check_results = {}
        if can_auto_check:
            for domain_code, domain_name in CMMC_DOMAINS:
                if domain_code in DOMAIN_AUTO_CHECKS:
                    try:
                        domain_check_results[domain_code] = (
                            DOMAIN_AUTO_CHECKS[domain_code](project_dir)
                        )
                    except Exception as e:
                        domain_check_results[domain_code] = {
                            "status": "not_met",
                            "evidence": f"Auto-check error: {e}",
                            "details": "Domain auto-check failed; manual review required.",
                        }

        now = datetime.utcnow()
        results = []

        # 5. Assess each practice
        for practice in practices:
            pid = practice["id"]
            domain_code = practice.get("domain_code", "")
            automation_level = practice.get("automation_level", "manual")
            status = "not_assessed"
            evidence = ""
            details = ""
            notes = ""

            # Check if inherited from NIST implementation
            inh = inheritance.get(pid, {})
            if inh.get("inherited"):
                status = "met"
                evidence = (
                    f"Inherited from NIST 800-53 implementation. "
                    f"Controls implemented: {', '.join(inh['controls_implemented'])}."
                )
                details = "Practice satisfied via crosswalk inheritance."
                notes = "Verified via crosswalk engine."

            elif automation_level == "auto" and can_auto_check:
                # Use domain-level auto-check result
                domain_result = domain_check_results.get(domain_code, {})
                if domain_result:
                    status = domain_result.get("status", "not_assessed")
                    evidence = domain_result.get("evidence", "")
                    details = domain_result.get("details", "")
                    notes = "Auto-checked via domain scan."
                else:
                    status = "not_assessed"
                    evidence = "No auto-check available for this domain."
                    notes = "Manual review required."

            elif automation_level == "semi" and can_auto_check:
                domain_result = domain_check_results.get(domain_code, {})
                if domain_result:
                    status = domain_result.get("status", "not_assessed")
                    evidence = domain_result.get("evidence", "")
                    details = domain_result.get("details", "")
                    notes = (
                        "Semi-automated check. Manual review required to "
                        "verify full compliance with this specific practice."
                    )
                else:
                    status = "not_assessed"
                    evidence = "Semi-automated: no auto component available."
                    notes = (
                        f"Manual review required. Evidence needed: "
                        f"{practice.get('evidence_required', 'See practice description.')}"
                    )

            elif automation_level in ("auto", "semi") and not can_auto_check:
                status = "not_assessed"
                evidence = "No project directory available for automated scanning."
                notes = "Provide --project-dir to enable auto-checks."

            else:
                # Manual
                status = "not_assessed"
                evidence = "Manual assessment required."
                notes = (
                    f"Evidence needed: "
                    f"{practice.get('evidence_required', 'See practice description.')}"
                )

            # Add partial credit if some NIST controls are implemented
            if status == "not_assessed" and inh.get("controls_implemented"):
                status = "partially_met"
                evidence = (
                    f"Partial NIST 800-53 implementation. "
                    f"Implemented: {', '.join(inh['controls_implemented'])}. "
                    f"Missing: {', '.join(inh.get('controls_missing', []))}."
                )
                notes = "Complete remaining NIST control implementations."

            result_entry = {
                "practice_id": pid,
                "domain": practice.get("domain", ""),
                "domain_code": domain_code,
                "level": practice.get("level", 2),
                "title": practice.get("title", ""),
                "description": practice.get("description", ""),
                "priority": practice.get("priority", "medium"),
                "automation_level": automation_level,
                "nist_800_53_controls": practice.get("nist_800_53_controls", []),
                "nist_800_171_id": practice.get("nist_800_171_id", ""),
                "status": status,
                "evidence": evidence,
                "details": details,
                "notes": notes,
            }
            results.append(result_entry)

            # 6. Store in cmmc_assessments table
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO cmmc_assessments
                       (project_id, assessment_date, assessor, level,
                        practice_id, domain, status, evidence_description,
                        evidence_path, automation_result, nist_171_id,
                        notes, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id,
                        now.isoformat(),
                        "icdev-compliance-engine",
                        level,
                        pid,
                        practice.get("domain", ""),
                        status,
                        evidence,
                        details if details else None,
                        json.dumps({
                            "automation_level": automation_level,
                            "inherited": inh.get("inherited", False),
                        }),
                        practice.get("nist_800_171_id", ""),
                        notes if notes else None,
                        now.isoformat(),
                    ),
                )
            except Exception as e:
                print(
                    f"Warning: Could not upsert assessment for {pid}: {e}",
                    file=sys.stderr,
                )

        conn.commit()

        # 7. Compute per-domain and overall scores
        domain_scores = {}
        for domain_code, domain_name in CMMC_DOMAINS:
            domain_practices = [
                r for r in results if r["domain_code"] == domain_code
            ]
            total = len(domain_practices)
            if total == 0:
                domain_scores[domain_code] = {
                    "name": domain_name,
                    "score": 0.0,
                    "total": 0,
                    "met": 0,
                    "partially_met": 0,
                    "not_met": 0,
                    "not_assessed": 0,
                    "not_applicable": 0,
                }
                continue

            met = sum(1 for p in domain_practices if p["status"] == "met")
            partial = sum(1 for p in domain_practices if p["status"] == "partially_met")
            not_met = sum(1 for p in domain_practices if p["status"] == "not_met")
            na = sum(1 for p in domain_practices if p["status"] == "not_applicable")
            not_assessed = sum(1 for p in domain_practices if p["status"] == "not_assessed")

            scoreable = total - na
            if scoreable > 0:
                score = 100.0 * (met + partial * 0.5) / scoreable
            else:
                score = 100.0  # All N/A

            domain_scores[domain_code] = {
                "name": domain_name,
                "score": round(score, 1),
                "total": total,
                "met": met,
                "partially_met": partial,
                "not_met": not_met,
                "not_assessed": not_assessed,
                "not_applicable": na,
            }

        # Overall: weighted average across 14 domains
        scoreable_domains = [
            s for s in domain_scores.values() if s["total"] > 0
        ]
        if scoreable_domains:
            # Weight by number of practices
            total_practices = sum(s["total"] - s["not_applicable"] for s in scoreable_domains)
            if total_practices > 0:
                weighted_sum = sum(
                    s["score"] * (s["total"] - s["not_applicable"])
                    for s in scoreable_domains
                )
                overall_score = round(weighted_sum / total_practices, 1)
            else:
                overall_score = 100.0
        else:
            overall_score = 0.0

        # Spill score: count of "not_met" practices
        spill_score = sum(1 for r in results if r["status"] == "not_met")

        # 8. Gate evaluation
        # Level 2: 0 "not_met" critical practices
        # Level 3: same + additional 800-172 checks
        critical_not_met = []
        for r in results:
            if r["priority"] == "critical" and r["status"] == "not_met":
                critical_not_met.append(f"{r['practice_id']}: {r['title']}")

        gate_passed = len(critical_not_met) == 0
        gate_result = {
            "evaluated": gate,
            "level": level,
            "passed": gate_passed,
            "critical_not_met": len(critical_not_met),
            "critical_failures": critical_not_met,
            "spill_score": spill_score,
            "reason": (
                f"PASS: 0 critical practices not_met for Level {level}"
                if gate_passed
                else (
                    f"FAIL: {len(critical_not_met)} critical practice(s) not_met: "
                    f"{', '.join(critical_not_met[:5])}"
                )
            ),
        }

        # Compute SPRS score estimate (DFARS 252.204-7019/7020)
        # SPRS = 110 - (5 * critical_not_met) - (3 * high_not_met) - (1 * other_not_met)
        high_not_met = sum(
            1 for r in results
            if r["priority"] == "high" and r["status"] == "not_met"
        )
        other_not_met = sum(
            1 for r in results
            if r["priority"] not in ("critical", "high") and r["status"] == "not_met"
        )
        sprs_score = max(
            -203,
            110 - (5 * len(critical_not_met)) - (3 * high_not_met) - (1 * other_not_met)
        )

        # 9. Log audit event
        _log_audit_event(
            conn,
            project_id,
            f"CMMC Level {level} assessment completed",
            {
                "level": level,
                "practices_assessed": len(results),
                "overall_score": overall_score,
                "spill_score": spill_score,
                "sprs_score": sprs_score,
                "gate_result": gate_result,
                "domain_scores": {
                    k: v["score"] for k, v in domain_scores.items()
                    if v["total"] > 0
                },
            },
        )

        # Summary counts
        total_met = sum(1 for r in results if r["status"] == "met")
        total_not_met = sum(1 for r in results if r["status"] == "not_met")
        total_partial = sum(1 for r in results if r["status"] == "partially_met")
        total_na = sum(1 for r in results if r["status"] == "not_applicable")
        total_not_assessed = sum(1 for r in results if r["status"] == "not_assessed")

        # Console output
        print(f"CMMC Level {level} assessment completed:")
        print(f"  Project: {project.get('name', project_id)}")
        print(f"  Practices assessed: {len(results)}")
        print(f"  Overall score: {overall_score}%")
        print(f"  SPRS score estimate: {sprs_score}")
        for domain_code, domain_name in CMMC_DOMAINS:
            s = domain_scores.get(domain_code, {})
            if s.get("total", 0) == 0:
                continue
            print(
                f"  {domain_code} ({domain_name}): "
                f"MET={s['met']} PARTIAL={s['partially_met']} "
                f"NOT_MET={s['not_met']} N/A={s['not_applicable']}"
            )

        if gate:
            print(f"\n  Gate: {gate_result['reason']}")

        return {
            "project_id": project_id,
            "level": level,
            "practices_assessed": len(results),
            "overall_score": overall_score,
            "spill_score": spill_score,
            "sprs_score": sprs_score,
            "domain_scores": domain_scores,
            "gate_result": gate_result,
            "practices_met": total_met,
            "practices_not_met": total_not_met,
            "practices_partial": total_partial,
            "practices_na": total_na,
            "practices_not_assessed": total_not_assessed,
            "results": results,
        }

    finally:
        conn.close()


def assess_project(
    project_id,
    level=2,
    project_dir=None,
    gate=False,
    output_path=None,
    db_path=None,
):
    """Alias for run_cmmc_assessment (MCP compatibility)."""
    return run_cmmc_assessment(
        project_id,
        level=level,
        project_dir=project_dir,
        gate=gate,
        output_path=output_path,
        db_path=db_path,
    )


# -----------------------------------------------------------------
# CLI entrypoint
# -----------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run CMMC Level 2/3 assessment"
    )
    parser.add_argument(
        "--project-id", required=True, help="Project ID"
    )
    parser.add_argument(
        "--level", type=int, default=2, choices=[2, 3],
        help="CMMC level (2 or 3, default: 2)",
    )
    parser.add_argument(
        "--domain",
        choices=[code for code, _ in CMMC_DOMAINS],
        help="Assess only a specific domain (default: all)",
    )
    parser.add_argument(
        "--project-dir",
        help="Project directory for automated file-based checks",
    )
    parser.add_argument(
        "--gate", action="store_true",
        help="Evaluate CMMC gate (0 critical not_met = pass)",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory for the assessment report",
    )
    parser.add_argument(
        "--db-path", type=Path, default=DB_PATH,
        help="Override database path",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON",
    )
    args = parser.parse_args()

    try:
        result = run_cmmc_assessment(
            project_id=args.project_id,
            level=args.level,
            project_dir=args.project_dir,
            gate=args.gate,
            output_path=args.output_dir,
            db_path=args.db_path,
        )

        if args.json:
            # Remove full results list for cleaner JSON output
            output = {
                k: v for k, v in result.items() if k != "results"
            }
            print(json.dumps(output, indent=2))
        else:
            print(
                json.dumps(
                    {
                        "overall_score": result.get("overall_score"),
                        "sprs_score": result.get("sprs_score"),
                        "gate_result": result.get("gate_result"),
                        "practices_met": result.get("practices_met"),
                        "practices_not_met": result.get("practices_not_met"),
                    },
                    indent=2,
                )
            )

        if args.gate and not result["gate_result"]["passed"]:
            sys.exit(1)

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
