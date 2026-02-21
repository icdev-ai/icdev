#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""FedRAMP Moderate/High assessment engine for ICDEV.

Loads FedRAMP baseline controls from fedramp_moderate_baseline.json or
fedramp_high_baseline.json, performs automated checks per control family,
inherits NIST 800-53 implementations via the crosswalk engine, stores
results in the fedramp_assessments table, evaluates FedRAMP readiness
gates, applies CUI markings, and logs audit events.

Usage:
    python tools/compliance/fedramp_assessor.py --project-id proj-123
    python tools/compliance/fedramp_assessor.py --project-id proj-123 --baseline high
    python tools/compliance/fedramp_assessor.py --project-id proj-123 --project-dir /path/to/project
    python tools/compliance/fedramp_assessor.py --project-id proj-123 --gate
    python tools/compliance/fedramp_assessor.py --project-id proj-123 --output-dir /path/to/output
    python tools/compliance/fedramp_assessor.py --project-id proj-123 --json

Databases:
    - data/icdev.db: fedramp_assessments, projects, project_controls, audit_trail

See also:
    - tools/compliance/crosswalk_engine.py (inherit NIST 800-53 implementations)
    - tools/compliance/classification_manager.py (CUI/SECRET markings)
    - tools/compliance/fedramp_report_generator.py (report generation)
    - context/compliance/fedramp_moderate_baseline.json
    - context/compliance/fedramp_high_baseline.json
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
FEDRAMP_MODERATE_PATH = BASE_DIR / "context" / "compliance" / "fedramp_moderate_baseline.json"
FEDRAMP_HIGH_PATH = BASE_DIR / "context" / "compliance" / "fedramp_high_baseline.json"


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
                "fedramp_assessed",
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
# Configuration helpers
# -----------------------------------------------------------------

def _load_cui_config():
    """Load CUI marking configuration via classification_manager or fallback."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from classification_manager import get_document_banner
        banners = get_document_banner("CUI")
        return {
            "document_header": banners.get("header", "CUI // SP-CTI"),
            "document_footer": banners.get("footer", "CUI // SP-CTI"),
        }
    except (ImportError, Exception):
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


def load_fedramp_baseline(baseline="moderate"):
    """Load FedRAMP baseline catalog from JSON.

    Args:
        baseline: "moderate" or "high".

    Returns:
        Tuple of (metadata dict, controls list).

    Raises:
        FileNotFoundError: If baseline JSON file does not exist.
        ValueError: If baseline is not "moderate" or "high".
    """
    baseline_lower = baseline.lower()
    if baseline_lower not in ("moderate", "high"):
        raise ValueError(
            f"Invalid baseline '{baseline}'. Must be 'moderate' or 'high'."
        )

    if baseline_lower == "moderate":
        catalog_path = FEDRAMP_MODERATE_PATH
    else:
        catalog_path = FEDRAMP_HIGH_PATH

    if not catalog_path.exists():
        raise FileNotFoundError(
            f"FedRAMP {baseline} baseline file not found: {catalog_path}\n"
            f"Expected: context/compliance/fedramp_{baseline_lower}_baseline.json"
        )

    with open(catalog_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    metadata = data.get("metadata", {})
    controls = data.get("controls", [])
    return metadata, controls


def _try_inherit_nist_implementations(project_id, controls, db_path=None):
    """Try to inherit NIST 800-53 implementations via the crosswalk engine.

    For each FedRAMP control, checks if the underlying NIST 800-53 control
    has been implemented in the project_controls table.

    Args:
        project_id: The project identifier.
        controls: List of FedRAMP baseline control dicts.
        db_path: Optional database path override.

    Returns:
        Dict mapping FedRAMP control ID -> {"inherited": bool, "nist_status": str}
    """
    inherited = {}

    try:
        conn = _get_connection(db_path)
        rows = conn.execute(
            """SELECT control_id, implementation_status
               FROM project_controls
               WHERE project_id = ?""",
            (project_id,),
        ).fetchall()
        conn.close()

        nist_statuses = {}
        for row in rows:
            nist_statuses[row["control_id"].upper()] = row["implementation_status"]

        for ctrl in controls:
            nist_id = ctrl.get("nist_control_id", "").upper()
            if nist_id in nist_statuses:
                status = nist_statuses[nist_id]
                inherited[ctrl["id"]] = {
                    "inherited": status in ("implemented", "partially_implemented"),
                    "nist_status": status,
                }
            else:
                inherited[ctrl["id"]] = {
                    "inherited": False,
                    "nist_status": "not_mapped",
                }

    except Exception as e:
        print(
            f"Warning: Could not inherit NIST implementations: {e}",
            file=sys.stderr,
        )
        for ctrl in controls:
            inherited[ctrl["id"]] = {
                "inherited": False,
                "nist_status": "not_available",
            }

    return inherited


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
    """Check if specific directories or file globs exist under project_dir.

    Args:
        project_dir: Root directory to search.
        dir_names: List of directory names to look for.
        glob_patterns: List of glob patterns to match files.

    Returns:
        List of found paths.
    """
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
# Auto-check functions (15 checks)
# Each returns a dict:
#   {"status": "satisfied"|"other_than_satisfied"|"not_applicable",
#    "evidence": "description of what was found",
#    "details": "specifics"}
# -----------------------------------------------------------------

def _check_access_control(project_dir):
    """AC family: Check for RBAC, least privilege, session management patterns."""
    patterns = [
        r"@login_required|@permission_required|@requires_auth",
        r"@Secured|@PreAuthorize|@RolesAllowed",
        r"role_required|check_permission|has_permission",
        r"\bRBAC\b|role.based.access",
        r"RoleBinding|ClusterRole|ClusterRoleBinding",
        r"least.privilege|minimum.privilege|principle.of.least",
        r"session_timeout|SESSION_EXPIRE|session.maxAge",
        r"from\s+flask_login|from\s+django\.contrib\.auth",
    ]
    extensions = (".py", ".yaml", ".yml", ".js", ".ts", ".java", ".go")
    matched, total = _scan_files(project_dir, extensions, patterns)

    if total == 0:
        return {
            "status": "other_than_satisfied",
            "evidence": "No source files found to assess for access control.",
            "details": "Project directory lacks applicable source files.",
        }

    if len(matched) >= 2:
        return {
            "status": "satisfied",
            "evidence": (
                f"Access control patterns (RBAC, least privilege, session "
                f"management) found in {len(matched)} file(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }
    elif len(matched) == 1:
        return {
            "status": "other_than_satisfied",
            "evidence": (
                f"Access control patterns found in only 1 file: "
                f"{os.path.basename(matched[0])}."
            ),
            "details": (
                "Minimal access control detected. FedRAMP requires "
                "comprehensive RBAC, least privilege, and session management."
            ),
        }

    return {
        "status": "other_than_satisfied",
        "evidence": "No access control patterns detected (RBAC, least privilege, session mgmt).",
        "details": (
            "Expected: @login_required, role_required, RBAC, RoleBinding, "
            "session_timeout, or least-privilege patterns."
        ),
    }


def _check_audit_logging(project_dir):
    """AU family: Check for logging config, audit trail, log retention."""
    event_type_patterns = [
        (r"login|auth.*log|authentication.*log", "authentication_logging"),
        (r"access.*log|access_log|request.*log", "access_logging"),
        (r"change.*log|change_log|modification.*log|update.*log", "change_logging"),
        (r"error.*log|error_log|exception.*log", "error_logging"),
        (r"security.*event|security.*log|security_event", "security_logging"),
    ]
    extensions = (".py", ".js", ".ts", ".java", ".yaml", ".yml")
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

    # Check for structured logging patterns
    struct_patterns = [
        r"audit_trail|AuditTrail",
        r"logging\.getLogger|getLogger",
        r"structlog|structured.log",
        r"log_retention|retention_days|retention_period",
    ]
    struct_matched, _ = _scan_files(project_dir, extensions, struct_patterns)
    if struct_matched:
        found_types.add("structured_logging")
        for sf in struct_matched:
            if sf not in evidence_files:
                evidence_files.append(sf)

    count = len(found_types)
    if count >= 3:
        return {
            "status": "satisfied",
            "evidence": (
                f"Comprehensive audit logging: {count} distinct log event "
                f"types across {len(evidence_files)} file(s)."
            ),
            "details": (
                f"Event types: {', '.join(sorted(found_types))}. "
                f"Files: {'; '.join(os.path.basename(f) for f in evidence_files[:5])}"
            ),
        }
    elif count >= 1:
        return {
            "status": "other_than_satisfied",
            "evidence": (
                f"Partial audit logging: {count} distinct log event "
                f"type(s). FedRAMP requires comprehensive audit logging "
                f"with retention policies."
            ),
            "details": f"Event types: {', '.join(sorted(found_types))}.",
        }

    return {
        "status": "other_than_satisfied",
        "evidence": "No audit logging patterns detected in source files.",
        "details": (
            "Expected: authentication logging, access logging, change logging, "
            "error logging, or security event logging with retention policies."
        ),
    }


def _check_identification_auth(project_dir):
    """IA family: Check for MFA, password policy, PKI/CAC integration."""
    mfa_patterns = [
        r"\bMFA\b|multi.factor|MultiFactor",
        r"\b2FA\b|two.factor|TwoFactor",
        r"\bTOTP\b|totp|time.based.one.time",
        r"\bFIDO\b|fido2|WebAuthn|webauthn",
        r"authenticator|Authenticator",
        r"otp_secret|otp_verify|verify_otp",
        r"\bPKI\b|pki_auth|certificate.auth",
        r"\bCAC\b|cac_auth|smart.card",
        r"PIV|piv_auth",
    ]
    password_patterns = [
        r"password_policy|PASSWORD_MIN_LENGTH|password.complexity",
        r"password.*history|password.*reuse|password.*rotation",
        r"bcrypt|scrypt|argon2|pbkdf2|PBKDF2",
        r"password.*hash|hash.*password",
    ]
    extensions = (".py", ".js", ".ts", ".java", ".yaml", ".yml", ".conf")
    mfa_matched, total = _scan_files(project_dir, extensions, mfa_patterns)
    pwd_matched, _ = _scan_files(project_dir, extensions, password_patterns)

    if total == 0:
        return {
            "status": "other_than_satisfied",
            "evidence": "No source or config files found to assess.",
            "details": "Project directory lacks applicable files.",
        }

    both = bool(mfa_matched) and bool(pwd_matched)
    either = bool(mfa_matched) or bool(pwd_matched)

    if both:
        return {
            "status": "satisfied",
            "evidence": (
                f"MFA/PKI patterns found in {len(mfa_matched)} file(s) and "
                f"password policy patterns in {len(pwd_matched)} file(s)."
            ),
            "details": (
                "MFA: "
                + "; ".join(os.path.basename(f) for f in mfa_matched[:3])
                + " | Password: "
                + "; ".join(os.path.basename(f) for f in pwd_matched[:3])
            ),
        }
    elif either:
        found_type = "MFA/PKI" if mfa_matched else "password policy"
        missing_type = "password policy" if mfa_matched else "MFA/PKI"
        return {
            "status": "other_than_satisfied",
            "evidence": (
                f"Partial IA: {found_type} patterns found but {missing_type} "
                f"patterns not detected."
            ),
            "details": (
                "FedRAMP requires both MFA/PKI and password policy enforcement."
            ),
        }

    return {
        "status": "other_than_satisfied",
        "evidence": "No MFA, PKI/CAC, or password policy patterns detected.",
        "details": (
            "Expected: MFA, 2FA, TOTP, FIDO, PKI, CAC, password complexity, "
            "bcrypt/argon2 hashing patterns."
        ),
    }


def _check_system_communications(project_dir):
    """SC family: Check for TLS, encryption config, network protection."""
    secure_patterns = [
        r"TLS\s*1\.[23]|TLSv1_[23]|tls_version.*1\.[23]",
        r"TLS_1_2|TLS_1_3|PROTOCOL_TLS",
        r"\bHTTPS\b|https://",
        r"ssl_context|SSLContext",
        r"mTLS|mutual.TLS",
        r"strong.cipher|ECDHE|AES.GCM|CHACHA20",
    ]
    insecure_patterns = [
        r"SSLv2|SSLv3|PROTOCOL_SSLv",
        r"TLSv1_0|TLS\s*1\.0|TLS_1_0",
        r"TLSv1_1|TLS\s*1\.1|TLS_1_1",
        r"verify\s*=\s*False|CERT_NONE|check_hostname\s*=\s*False",
        r"ssl_verify.*false|tls_verify.*false",
    ]
    extensions = (".py", ".yaml", ".yml", ".conf", ".tf", ".json")

    secure_matched, total = _scan_files(project_dir, extensions, secure_patterns)
    insecure_matched, _ = _scan_files(project_dir, extensions, insecure_patterns)

    if total == 0:
        return {
            "status": "other_than_satisfied",
            "evidence": "No configuration or source files found to assess.",
            "details": "Project directory lacks files with expected extensions.",
        }

    if secure_matched and not insecure_matched:
        return {
            "status": "satisfied",
            "evidence": (
                f"TLS/encryption patterns found in {len(secure_matched)} "
                f"file(s) with no insecure protocol patterns detected."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in secure_matched[:5]
            ),
        }
    elif secure_matched and insecure_matched:
        return {
            "status": "other_than_satisfied",
            "evidence": (
                f"TLS patterns found in {len(secure_matched)} file(s), but "
                f"insecure patterns also in {len(insecure_matched)} file(s)."
            ),
            "details": (
                "Insecure: "
                + "; ".join(os.path.basename(f) for f in insecure_matched[:5])
                + ". Remove SSLv3, TLSv1.0, TLSv1.1, and verify=False usage."
            ),
        }

    return {
        "status": "other_than_satisfied",
        "evidence": "No TLS/encryption configuration patterns detected.",
        "details": (
            "Expected: TLS 1.2+, FIPS-validated ciphers, SSLContext, mTLS. "
            "FedRAMP requires FIPS 140-2 validated cryptographic modules."
        ),
    }


def _check_config_management(project_dir):
    """CM family: Check for IaC, config files, baseline configs."""
    iac_found = _dir_or_file_exists(
        project_dir,
        dir_names=["terraform", "ansible", "cloudformation", "pulumi"],
        glob_patterns=[
            "*.tf", "*.tfvars",
            "playbook*.yml", "playbook*.yaml",
            "ansible.cfg",
            "*.cfn.yml", "*.cfn.yaml", "*.cfn.json",
            "docker-compose*.yml", "docker-compose*.yaml",
        ],
    )

    config_patterns = [
        r"version_control|git.*config|\.gitignore",
        r"baseline.*config|config.*baseline|hardened.*config",
        r"change.*management|change_request|change_control",
    ]
    extensions = (".py", ".yaml", ".yml", ".conf", ".json", ".md")
    config_matched, _ = _scan_files(project_dir, extensions, config_patterns)

    # Check for CI/CD config (indicates config management discipline)
    ci_found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            ".gitlab-ci.yml", ".github/workflows/*.yml",
            "Jenkinsfile", "azure-pipelines.yml",
            ".circleci/config.yml",
        ],
    )

    all_evidence = list(set(iac_found + config_matched + ci_found))
    if len(all_evidence) >= 3:
        return {
            "status": "satisfied",
            "evidence": (
                f"Configuration management artifacts found: "
                f"{len(all_evidence)} item(s) including IaC, configs, and CI/CD."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_evidence[:5]),
        }
    elif all_evidence:
        return {
            "status": "other_than_satisfied",
            "evidence": (
                f"Partial configuration management: {len(all_evidence)} "
                f"artifact(s) found."
            ),
            "details": (
                "Found: " + "; ".join(os.path.basename(f) for f in all_evidence[:5])
                + ". FedRAMP requires IaC, baseline configs, and change management."
            ),
        }

    return {
        "status": "other_than_satisfied",
        "evidence": "No configuration management artifacts detected.",
        "details": (
            "Expected: Terraform/Ansible/CloudFormation, baseline configs, "
            "CI/CD pipelines, change management documentation."
        ),
    }


def _check_incident_response(project_dir):
    """IR family: Check for IR plan, SECURITY.md, incident procedures."""
    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "SECURITY.md", "SECURITY.txt", "security.md",
            "incident-response*", "incident_response*",
            "ir-plan*", "ir_plan*",
        ],
    )
    found_dirs = _dir_or_file_exists(
        project_dir,
        dir_names=["incident-response", "incident_response", "security"],
    )

    ir_patterns = [
        r"incident.response|incident.handling|incident.plan",
        r"security.incident|breach.notification",
        r"IR.plan|IR.procedure|IR.contact",
        r"escalation.*procedure|escalation.*matrix",
    ]
    extensions = (".py", ".yaml", ".yml", ".md", ".txt", ".json")
    ir_matched, _ = _scan_files(project_dir, extensions, ir_patterns)

    all_found = list(set(found + found_dirs + ir_matched))
    if len(all_found) >= 2:
        return {
            "status": "satisfied",
            "evidence": (
                f"Incident response artifacts found: {len(all_found)} item(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }
    elif all_found:
        return {
            "status": "other_than_satisfied",
            "evidence": (
                f"Partial IR: {len(all_found)} artifact(s). FedRAMP requires "
                f"a formal IR plan with procedures and contacts."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }

    return {
        "status": "other_than_satisfied",
        "evidence": "No incident response artifacts detected.",
        "details": (
            "Expected: SECURITY.md, incident-response plan, IR procedures, "
            "escalation matrix, breach notification process."
        ),
    }


def _check_risk_assessment(project_dir):
    """RA family: Check for threat model, vulnerability assessments."""
    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "threat-model*", "threat_model*", "STRIDE*", "PASTA*",
            "threat-analysis*", "threat_analysis*",
            "risk-assessment*", "risk_assessment*",
            "vulnerability-assessment*", "vulnerability_assessment*",
        ],
    )
    found_dirs = _dir_or_file_exists(
        project_dir,
        dir_names=[
            "threat-model", "threat_model", "risk-assessment", "risk_assessment",
        ],
    )

    ra_patterns = [
        r"threat.model|STRIDE|PASTA|attack.tree",
        r"risk.assessment|risk.analysis|risk.register",
        r"vulnerability.assessment|vuln.scan|vulnerability.scan",
    ]
    extensions = (".py", ".yaml", ".yml", ".md", ".txt", ".json")
    ra_matched, _ = _scan_files(project_dir, extensions, ra_patterns)

    all_found = list(set(found + found_dirs + ra_matched))
    if all_found:
        return {
            "status": "satisfied",
            "evidence": (
                f"Risk assessment artifacts found: {len(all_found)} item(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }

    return {
        "status": "other_than_satisfied",
        "evidence": "No risk assessment or threat model artifacts detected.",
        "details": (
            "Expected: threat model (STRIDE/PASTA), risk assessment, "
            "vulnerability assessment documentation."
        ),
    }


def _check_security_assessment(project_dir):
    """CA family: Check for test coverage, SAST/DAST config, security testing."""
    test_found = _dir_or_file_exists(
        project_dir,
        dir_names=["tests", "test", "spec", "__tests__"],
        glob_patterns=[
            "test_*.py", "*_test.py", "*.test.js", "*.test.ts",
            "*.spec.js", "*.spec.ts",
            "pytest.ini", "setup.cfg", "tox.ini",
        ],
    )

    security_patterns = [
        r"\bbandit\b|bandit.*config|\.bandit",
        r"\bsast\b|static.analysis|static.application.security",
        r"\bdast\b|dynamic.analysis|dynamic.application.security",
        r"\bsonarqube\b|sonar-project|sonar.properties",
        r"security.*test|pen.*test|penetration.*test",
        r"\bsnyk\b|\btrivy\b|\bgrype\b",
        r"pip.audit|npm.audit|safety.check",
    ]
    extensions = (".py", ".yaml", ".yml", ".json", ".conf", ".cfg", ".ini")
    security_matched, _ = _scan_files(project_dir, extensions, security_patterns)

    has_tests = bool(test_found)
    has_security_tools = bool(security_matched)

    if has_tests and has_security_tools:
        return {
            "status": "satisfied",
            "evidence": (
                f"Security assessment: test suites ({len(test_found)} item(s)) "
                f"and security tools ({len(security_matched)} config(s)) detected."
            ),
            "details": (
                "Tests: " + "; ".join(os.path.basename(f) for f in test_found[:3])
                + " | Security: "
                + "; ".join(os.path.basename(f) for f in security_matched[:3])
            ),
        }
    elif has_tests or has_security_tools:
        found_type = "test suites" if has_tests else "security tools"
        missing_type = "security testing tools (SAST/DAST)" if has_tests else "test suites"
        return {
            "status": "other_than_satisfied",
            "evidence": (
                f"Partial: {found_type} found but {missing_type} not detected."
            ),
            "details": (
                "FedRAMP requires both functional testing and security "
                "assessment tools (SAST, DAST, vulnerability scanning)."
            ),
        }

    return {
        "status": "other_than_satisfied",
        "evidence": "No test suites or security assessment tools detected.",
        "details": (
            "Expected: test directories, pytest/jest configs, SAST (bandit, "
            "SonarQube), DAST tools, and vulnerability scanning configs."
        ),
    }


def _check_supply_chain(project_dir):
    """SA/SR family: Check for SBOM, dependency auditing, vendor risk."""
    sbom_found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "*sbom*.json", "*bom*.xml", "*sbom*.xml",
            "*cyclonedx*", "*spdx*",
        ],
    )

    dep_patterns = [
        r"pip.audit|pip_audit|pipaudit",
        r"npm\s+audit|yarn\s+audit",
        r"\bsafety\b.*check|safety\s+scan",
        r"\bsnyk\b.*test|snyk\s+monitor",
        r"dependency.check|DependencyCheck",
        r"trivy\s+fs|grype\s+dir",
    ]
    extensions = (".yaml", ".yml", ".json", ".toml", ".cfg", ".ini")
    dep_matched, _ = _scan_files(project_dir, extensions, dep_patterns)

    lock_files = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "requirements*.txt", "poetry.lock", "Pipfile.lock",
            "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
            "go.sum", "Cargo.lock",
        ],
    )

    has_sbom = bool(sbom_found)
    has_dep_audit = bool(dep_matched)
    has_lock = bool(lock_files)

    if has_sbom and has_dep_audit:
        return {
            "status": "satisfied",
            "evidence": (
                f"Supply chain: SBOM ({len(sbom_found)} artifact(s)) and "
                f"dependency auditing ({len(dep_matched)} config(s)) detected."
            ),
            "details": (
                "SBOM: " + "; ".join(os.path.basename(f) for f in sbom_found[:3])
                + " | Audit: "
                + "; ".join(os.path.basename(f) for f in dep_matched[:3])
            ),
        }
    elif has_sbom or has_dep_audit or has_lock:
        parts = []
        if has_sbom:
            parts.append("SBOM")
        if has_dep_audit:
            parts.append("dependency auditing")
        if has_lock:
            parts.append("lock files")
        return {
            "status": "other_than_satisfied",
            "evidence": (
                f"Partial supply chain: {', '.join(parts)} detected but "
                f"complete supply chain risk management not verified."
            ),
            "details": (
                "FedRAMP requires SBOM generation, dependency vulnerability "
                "scanning, and software composition analysis."
            ),
        }

    return {
        "status": "other_than_satisfied",
        "evidence": "No supply chain risk management artifacts detected.",
        "details": (
            "Expected: SBOM (CycloneDX/SPDX), dependency audit tools "
            "(pip-audit, npm audit, Snyk), lock files."
        ),
    }


def _check_data_encryption(project_dir):
    """SC-28 family: Check for encryption at rest, key management patterns."""
    patterns = [
        r"\bFIPS\b|fips_mode|FIPS.140",
        r"AES.256|AES_256|aes256",
        r"encryption.at.rest|encrypt_at_rest|encrypted_at_rest",
        r"storage_encrypted|StorageEncrypted|encrypted\s*=\s*true",
        r"\bKMS\b|kms_key|aws_kms|key_management",
        r"server.side.encryption|SSE.S3|SSE.KMS|SSEAlgorithm",
    ]
    extensions = (".py", ".yaml", ".yml", ".tf", ".json", ".conf")
    matched, total = _scan_files(project_dir, extensions, patterns)

    if total == 0:
        return {
            "status": "other_than_satisfied",
            "evidence": "No configuration files found to assess.",
            "details": "Project directory lacks applicable config files.",
        }

    if matched:
        return {
            "status": "satisfied",
            "evidence": (
                f"Encryption-at-rest patterns found in "
                f"{len(matched)} file(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }

    return {
        "status": "other_than_satisfied",
        "evidence": "No encryption-at-rest patterns detected.",
        "details": (
            "Expected: FIPS 140-2, AES-256, KMS, storage_encrypted, or "
            "server-side encryption. FedRAMP requires FIPS-validated modules."
        ),
    }


def _check_backup_recovery(project_dir):
    """CP family: Check for backup config, disaster recovery plans."""
    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "backup*", "disaster-recovery*", "disaster_recovery*",
            "dr-plan*", "dr_plan*", "continuity*",
            "bcp*", "contingency*",
        ],
    )
    found_dirs = _dir_or_file_exists(
        project_dir,
        dir_names=["backup", "backups", "disaster-recovery", "dr"],
    )

    cp_patterns = [
        r"backup.*policy|backup.*schedule|backup.*config",
        r"disaster.recovery|recovery.point|recovery.time",
        r"RPO|RTO|continuity.plan|contingency.plan",
        r"aws_backup|aws_db_snapshot|snapshot.*schedule",
        r"velero|restic|borg|duplicity",
    ]
    extensions = (".py", ".yaml", ".yml", ".tf", ".json", ".md", ".conf")
    cp_matched, _ = _scan_files(project_dir, extensions, cp_patterns)

    all_found = list(set(found + found_dirs + cp_matched))
    if all_found:
        return {
            "status": "satisfied",
            "evidence": (
                f"Backup/recovery artifacts found: {len(all_found)} item(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }

    return {
        "status": "other_than_satisfied",
        "evidence": "No backup or disaster recovery artifacts detected.",
        "details": (
            "Expected: backup configs, DR plan, RPO/RTO documentation, "
            "contingency plan, AWS Backup or equivalent tooling."
        ),
    }


def _check_boundary_protection(project_dir):
    """SC-7 family: Check for firewalls, network segmentation, WAF config."""
    patterns = [
        r"security.group|SecurityGroup|security_group",
        r"firewall|Firewall|NetworkPolicy|network.policy",
        r"WAF|web.application.firewall|waf_acl",
        r"network.segmentation|subnet|Subnet",
        r"ingress.*rule|egress.*rule|IngressRule|EgressRule",
        r"aws_security_group|aws_waf|aws_network_acl",
        r"default.deny|deny.all|NetworkPolicy.*DefaultDeny",
    ]
    extensions = (".py", ".yaml", ".yml", ".tf", ".json", ".conf")
    matched, total = _scan_files(project_dir, extensions, patterns)

    if total == 0:
        return {
            "status": "other_than_satisfied",
            "evidence": "No infrastructure config files found to assess.",
            "details": "Project directory lacks applicable IaC/config files.",
        }

    if len(matched) >= 2:
        return {
            "status": "satisfied",
            "evidence": (
                f"Boundary protection patterns found in "
                f"{len(matched)} file(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }
    elif matched:
        return {
            "status": "other_than_satisfied",
            "evidence": (
                f"Minimal boundary protection in {len(matched)} file(s). "
                f"FedRAMP requires comprehensive network segmentation."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }

    return {
        "status": "other_than_satisfied",
        "evidence": "No boundary protection patterns detected.",
        "details": (
            "Expected: security groups, firewalls, WAF, network policies, "
            "default-deny rules, subnet segmentation."
        ),
    }


def _check_remote_access(project_dir):
    """AC-17 family: Check for VPN, remote access controls."""
    patterns = [
        r"\bVPN\b|vpn_config|vpn.gateway",
        r"remote.access|RemoteAccess|remote_access",
        r"bastion|BastionHost|bastion.host|jump.box",
        r"SSM|Session.Manager|aws_ssm",
        r"SSH.*key|ssh_key|authorized_keys",
        r"client.vpn|site.to.site|ipsec|wireguard",
    ]
    extensions = (".py", ".yaml", ".yml", ".tf", ".json", ".conf")
    matched, total = _scan_files(project_dir, extensions, patterns)

    if total == 0:
        return {
            "status": "not_applicable",
            "evidence": "No infrastructure config files found. Remote access check N/A.",
            "details": "May require manual review if remote access is in scope.",
        }

    if matched:
        return {
            "status": "satisfied",
            "evidence": (
                f"Remote access control patterns found in "
                f"{len(matched)} file(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }

    return {
        "status": "other_than_satisfied",
        "evidence": "No remote access control patterns detected.",
        "details": (
            "Expected: VPN, bastion host, SSM Session Manager, SSH key "
            "management, or equivalent remote access controls."
        ),
    }


def _check_media_protection(project_dir):
    """MP family: Check for data sanitization, media handling."""
    patterns = [
        r"data.sanitization|data.wipe|data.disposal",
        r"media.protection|media.handling|media.transport",
        r"degauss|shred|secure.erase|crypto.erase",
        r"data.lifecycle|data.retention|data.classification",
    ]
    extensions = (".py", ".yaml", ".yml", ".md", ".txt", ".json", ".conf")
    matched, total = _scan_files(project_dir, extensions, patterns)

    if total == 0:
        return {
            "status": "not_applicable",
            "evidence": "No files found to assess media protection.",
            "details": "Cloud-native systems may inherit media protection from CSP.",
        }

    if matched:
        return {
            "status": "satisfied",
            "evidence": (
                f"Media protection patterns found in {len(matched)} file(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }

    return {
        "status": "other_than_satisfied",
        "evidence": "No media protection or data sanitization patterns detected.",
        "details": (
            "Expected: data sanitization policies, media handling procedures, "
            "data lifecycle management. May be inherited from CSP for cloud."
        ),
    }


def _check_system_integrity(project_dir):
    """SI family: Check for integrity monitoring, anti-malware, HIDS."""
    patterns = [
        r"integrity.*check|integrity.*monitor|file.*integrity",
        r"\bHIDS\b|host.intrusion|intrusion.detection",
        r"\bOSSEC\b|ossec|Wazuh|wazuh",
        r"\bAIDE\b|\bTripwire\b|tripwire",
        r"anti.malware|antivirus|malware.scan",
        r"ClamAV|clamav",
        r"patch.*management|patch.*policy|system.*update",
        r"flaw.remediation|vulnerability.*patch",
    ]
    extensions = (".py", ".yaml", ".yml", ".tf", ".json", ".conf", ".md")
    matched, total = _scan_files(project_dir, extensions, patterns)

    if total == 0:
        return {
            "status": "other_than_satisfied",
            "evidence": "No files found to assess system integrity.",
            "details": "Project directory lacks applicable files.",
        }

    if matched:
        return {
            "status": "satisfied",
            "evidence": (
                f"System integrity patterns found in {len(matched)} file(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }

    return {
        "status": "other_than_satisfied",
        "evidence": "No system integrity monitoring patterns detected.",
        "details": (
            "Expected: file integrity monitoring (AIDE, Tripwire, OSSEC), "
            "anti-malware, HIDS, patch management, flaw remediation."
        ),
    }


# -----------------------------------------------------------------
# Control family to auto-check mapping
# -----------------------------------------------------------------

FAMILY_CHECKS = {
    "AC": _check_access_control,
    "AU": _check_audit_logging,
    "IA": _check_identification_auth,
    "SC": _check_system_communications,
    "CM": _check_config_management,
    "IR": _check_incident_response,
    "RA": _check_risk_assessment,
    "CA": _check_security_assessment,
    "SA": _check_supply_chain,
    "CP": _check_backup_recovery,
    "MP": _check_media_protection,
    "SI": _check_system_integrity,
}

# SC-28 and SC-7 are sub-family checks keyed by NIST control prefix
CONTROL_CHECKS = {
    "SC-28": _check_data_encryption,
    "SC-7": _check_boundary_protection,
    "AC-17": _check_remote_access,
}


def _get_auto_check(control):
    """Return the appropriate auto-check function for a FedRAMP control.

    Checks CONTROL_CHECKS first for specific control IDs, then falls
    back to FAMILY_CHECKS for the control family.

    Args:
        control: FedRAMP control dict with 'nist_control_id' and 'family' keys.

    Returns:
        Callable or None.
    """
    nist_id = control.get("nist_control_id", "")

    # Check for specific control prefix match (e.g., SC-28, SC-7, AC-17)
    for prefix, check_fn in CONTROL_CHECKS.items():
        if nist_id.startswith(prefix):
            return check_fn

    # Fall back to family check
    family = control.get("family", "")
    return FAMILY_CHECKS.get(family)


# -----------------------------------------------------------------
# Core assessment function
# -----------------------------------------------------------------

def run_fedramp_assessment(
    project_id,
    baseline="moderate",
    project_dir=None,
    gate=False,
    output_path=None,
    db_path=None,
):
    """Run FedRAMP Moderate or High assessment.

    Args:
        project_id: The project identifier.
        baseline: "moderate" or "high".
        project_dir: Project directory for automated file-based checks.
        gate: If True, evaluate the FedRAMP readiness gate.
        output_path: Override output directory for the assessment report.
        db_path: Override database path.

    Returns:
        Dict with assessment results, summary, gate result, and output file path.
    """
    db = Path(db_path) if db_path else DB_PATH
    conn = _get_connection(db)
    try:
        project = _get_project(conn, project_id)

        # Load FedRAMP baseline catalog
        metadata, controls = load_fedramp_baseline(baseline)

        # Try to inherit NIST 800-53 implementations via crosswalk
        inherited = _try_inherit_nist_implementations(
            project_id, controls, db_path=db
        )

        # Resolve project directory for auto-checks
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

        now = datetime.now(timezone.utc)
        results = []
        family_cache = {}  # Cache auto-check results per family/control

        # -- Assess each control --
        for ctrl in controls:
            ctrl_id = ctrl["id"]
            nist_id = ctrl.get("nist_control_id", "")
            family = ctrl.get("family", "")
            priority = ctrl.get("priority", "P1")

            status = "not_assessed"
            evidence = ""
            details = ""
            notes = ""
            implementation_status = ""
            customer_responsible = ""

            # Step 1: Check if inherited from NIST 800-53 implementation
            inherit_info = inherited.get(ctrl_id, {})
            if inherit_info.get("inherited"):
                nist_status = inherit_info.get("nist_status", "")
                if nist_status == "implemented":
                    status = "satisfied"
                    evidence = (
                        f"Inherited from NIST 800-53 {nist_id} implementation "
                        f"(status: {nist_status})."
                    )
                    implementation_status = "inherited"
                    notes = "Auto-inherited via crosswalk engine."
                elif nist_status == "partially_implemented":
                    status = "other_than_satisfied"
                    evidence = (
                        f"Partially inherited from NIST 800-53 {nist_id} "
                        f"(status: {nist_status}). Requires additional FedRAMP "
                        f"parameter verification."
                    )
                    implementation_status = "partially_inherited"
                    notes = (
                        "Inherited partial implementation. Review FedRAMP-specific "
                        "parameters and additional requirements."
                    )

            # Step 2: Run auto-check if not already satisfied
            if status != "satisfied" and can_auto_check:
                check_fn = _get_auto_check(ctrl)
                if check_fn:
                    # Use cache key to avoid re-running same family check
                    cache_key = check_fn.__name__
                    if cache_key not in family_cache:
                        try:
                            family_cache[cache_key] = check_fn(project_dir)
                        except Exception as e:
                            family_cache[cache_key] = {
                                "status": "other_than_satisfied",
                                "evidence": f"Auto-check error: {e}",
                                "details": "Auto-check failed; manual review required.",
                            }

                    check_result = family_cache[cache_key]

                    # Only upgrade status if auto-check is better
                    if status == "not_assessed" or (
                        status == "other_than_satisfied"
                        and check_result["status"] == "satisfied"
                    ):
                        status = check_result["status"]
                        evidence = check_result["evidence"]
                        details = check_result.get("details", "")
                        if check_result["status"] == "satisfied":
                            implementation_status = "auto_verified"
                        notes = f"Auto-checked via {cache_key}."

            # Step 3: If still not assessed, mark for manual review
            if status == "not_assessed":
                if not can_auto_check:
                    evidence = "No project directory available for automated checks."
                    notes = "Provide --project-dir to enable auto-checks."
                else:
                    evidence = "No automated check available for this control."
                    notes = "Manual review required for FedRAMP assessment."

            result_entry = {
                "control_id": ctrl_id,
                "family": family,
                "nist_control_id": nist_id,
                "title": ctrl.get("title", ""),
                "description": ctrl.get("description", ""),
                "priority": priority,
                "baseline": baseline,
                "fedramp_parameters": ctrl.get("fedramp_parameters", {}),
                "fedramp_additional_requirements": ctrl.get(
                    "fedramp_additional_requirements", ""
                ),
                "status": status,
                "implementation_status": implementation_status,
                "customer_responsible": customer_responsible,
                "evidence": evidence,
                "details": details,
                "notes": notes,
                "inherited": inherit_info.get("inherited", False),
            }
            results.append(result_entry)

            # -- Upsert into fedramp_assessments table --
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO fedramp_assessments
                       (project_id, assessment_date, assessor, baseline,
                        control_id, status, implementation_status,
                        customer_responsible, evidence_description,
                        evidence_path, automation_result, notes, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id,
                        now.isoformat(),
                        "icdev-compliance-engine",
                        baseline,
                        ctrl_id,
                        status,
                        implementation_status,
                        customer_responsible,
                        evidence,
                        details if details else None,
                        json.dumps({
                            "check_function": (
                                _get_auto_check(ctrl).__name__
                                if _get_auto_check(ctrl)
                                else None
                            ),
                            "inherited": inherit_info.get("inherited", False),
                            "nist_status": inherit_info.get("nist_status", ""),
                        }),
                        notes if notes else None,
                        now.isoformat(),
                    ),
                )
            except Exception as e:
                print(
                    f"Warning: Could not upsert assessment for {ctrl_id}: {e}",
                    file=sys.stderr,
                )

        conn.commit()

        # -- Build summary by control family --
        family_order = [
            "AC", "AT", "AU", "CA", "CM", "CP", "IA", "IR",
            "MA", "MP", "PE", "PL", "PM", "PS", "PT", "RA",
            "SA", "SC", "SI", "SR",
        ]
        family_names = {
            "AC": "Access Control",
            "AT": "Awareness and Training",
            "AU": "Audit and Accountability",
            "CA": "Assessment, Authorization, Monitoring",
            "CM": "Configuration Management",
            "CP": "Contingency Planning",
            "IA": "Identification and Authentication",
            "IR": "Incident Response",
            "MA": "Maintenance",
            "MP": "Media Protection",
            "PE": "Physical and Environmental Protection",
            "PL": "Planning",
            "PM": "Program Management",
            "PS": "Personnel Security",
            "PT": "PII Processing and Transparency",
            "RA": "Risk Assessment",
            "SA": "System and Services Acquisition",
            "SC": "System and Communications Protection",
            "SI": "System and Information Integrity",
            "SR": "Supply Chain Risk Management",
        }

        summary = {}
        for fam in family_order:
            summary[fam] = {
                "name": family_names.get(fam, fam),
                "total": 0,
                "satisfied": 0,
                "other_than_satisfied": 0,
                "not_assessed": 0,
                "not_applicable": 0,
                "risk_accepted": 0,
            }

        for r in results:
            fam = r["family"]
            if fam not in summary:
                summary[fam] = {
                    "name": family_names.get(fam, fam),
                    "total": 0,
                    "satisfied": 0,
                    "other_than_satisfied": 0,
                    "not_assessed": 0,
                    "not_applicable": 0,
                    "risk_accepted": 0,
                }
            summary[fam]["total"] += 1
            st = r["status"]
            if st in summary[fam]:
                summary[fam][st] += 1

        # -- Compute overall scores --
        total_controls = len(results)
        satisfied_count = sum(1 for r in results if r["status"] == "satisfied")
        ots_count = sum(
            1 for r in results if r["status"] == "other_than_satisfied"
        )
        na_count = sum(
            1 for r in results if r["status"] == "not_applicable"
        )
        not_assessed_count = sum(
            1 for r in results if r["status"] == "not_assessed"
        )
        risk_accepted_count = sum(
            1 for r in results if r["status"] == "risk_accepted"
        )
        inherited_count = sum(1 for r in results if r.get("inherited"))

        assessable = total_controls - na_count
        if assessable > 0:
            overall_score = round(
                (satisfied_count + risk_accepted_count * 0.75)
                / assessable
                * 100,
                1,
            )
        else:
            overall_score = 0.0

        # -- Gate evaluation --
        # FedRAMP gate: 0 "other_than_satisfied" on critical (P1) controls
        critical_ots = 0
        critical_failures = []
        for r in results:
            if (
                r["priority"] == "P1"
                and r["status"] == "other_than_satisfied"
            ):
                critical_ots += 1
                critical_failures.append(
                    f"{r['control_id']} ({r['nist_control_id']}): {r['title']}"
                )

        gate_passed = critical_ots == 0
        gate_result = {
            "evaluated": gate,
            "passed": gate_passed,
            "critical_other_than_satisfied": critical_ots,
            "critical_failures": critical_failures,
            "reason": (
                "PASS: 0 P1 controls with status other_than_satisfied"
                if gate_passed
                else (
                    f"FAIL: {critical_ots} P1 control(s) other_than_satisfied: "
                    + ", ".join(critical_failures[:5])
                    + ("..." if len(critical_failures) > 5 else "")
                )
            ),
        }

        # -- Generate Markdown assessment summary --
        cui_config = _load_cui_config()
        doc_header = cui_config.get(
            "document_header", "CUI // SP-CTI"
        ).strip()
        doc_footer = cui_config.get(
            "document_footer", "CUI // SP-CTI"
        ).strip()

        lines = [
            doc_header,
            "",
            f"# FedRAMP {baseline.title()} Assessment Report",
            "",
            f"**Project:** {project.get('name', project_id)} ({project_id})",
            f"**Assessment Date:** {now.strftime('%Y-%m-%d %H:%M UTC')}",
            "**Assessor:** ICDEV Compliance Engine (automated)",
            f"**Baseline:** FedRAMP {baseline.title()}",
            "**Classification:** CUI // SP-CTI",
            "",
            "---",
            "",
            "## Executive Summary",
            "",
            f"**Overall Readiness Score:** {overall_score}%",
            f"**Total Controls:** {total_controls}",
            f"**Satisfied:** {satisfied_count}",
            f"**Other Than Satisfied:** {ots_count}",
            f"**Not Applicable:** {na_count}",
            f"**Not Assessed:** {not_assessed_count}",
            f"**Risk Accepted:** {risk_accepted_count}",
            f"**Inherited from NIST 800-53:** {inherited_count}",
            "",
        ]

        # Summary table
        lines.append(
            "| Family | Name | Total | Satisfied | OTS "
            "| Not Assessed | N/A | Risk Accepted |"
        )
        lines.append(
            "|--------|------|-------|-----------|-----"
            "|--------------|-----|---------------|"
        )

        grand_total = {
            "total": 0, "satisfied": 0, "other_than_satisfied": 0,
            "not_assessed": 0, "not_applicable": 0, "risk_accepted": 0,
        }

        for fam in family_order:
            s = summary.get(fam, {})
            if s.get("total", 0) == 0:
                continue
            lines.append(
                f"| {fam} | {s['name']} | {s['total']} | {s['satisfied']} | "
                f"{s['other_than_satisfied']} | "
                f"{s['not_assessed']} | {s['not_applicable']} | "
                f"{s['risk_accepted']} |"
            )
            for key in grand_total:
                grand_total[key] += s.get(key, 0)

        lines.append(
            f"| **Total** | | **{grand_total['total']}** | "
            f"**{grand_total['satisfied']}** | "
            f"**{grand_total['other_than_satisfied']}** | "
            f"**{grand_total['not_assessed']}** | "
            f"**{grand_total['not_applicable']}** | "
            f"**{grand_total['risk_accepted']}** |"
        )
        lines.append("")

        # Gate evaluation section
        if gate:
            gate_label = (
                "PASS" if gate_result["passed"] else "**FAIL**"
            )
            lines.extend([
                "## FedRAMP Readiness Gate",
                "",
                f"**Gate Result:** {gate_label}",
                (
                    "**Criteria:** 0 P1 controls with status "
                    "other_than_satisfied"
                ),
                f"**P1 Failures:** {critical_ots}",
                "",
            ])
            if critical_failures:
                lines.append("**Failed Controls:**")
                for cf in critical_failures[:10]:
                    lines.append(f"- {cf}")
                if len(critical_failures) > 10:
                    lines.append(
                        f"- ... and {len(critical_failures) - 10} more"
                    )
                lines.append("")

        lines.extend(["---", ""])

        # -- Detailed findings per family --
        lines.append("## Detailed Findings")
        lines.append("")

        for fam in family_order:
            fam_results = [r for r in results if r["family"] == fam]
            if not fam_results:
                continue

            fam_name = family_names.get(fam, fam)
            lines.append(f"### {fam}: {fam_name}")
            lines.append("")

            for r in fam_results:
                status_display = r["status"].replace("_", " ").title()
                lines.extend([
                    f"#### {r['control_id']}: {r['title']}",
                    "",
                    f"**NIST Control:** {r['nist_control_id']}  ",
                    f"**Priority:** {r['priority']}  ",
                    f"**Status:** {status_display}  ",
                    f"**Inherited:** {'Yes' if r.get('inherited') else 'No'}",
                    "",
                    f"**Evidence:** {r['evidence']}",
                    "",
                ])
                if r["details"]:
                    lines.append(f"**Details:** {r['details']}")
                    lines.append("")
                if r["notes"]:
                    lines.append(f"**Notes:** {r['notes']}")
                    lines.append("")
                lines.extend(["---", ""])

        # Append CUI footer
        lines.extend([doc_footer, ""])
        content = "\n".join(lines)

        # -- Write output file --
        if output_path:
            out_dir = Path(output_path)
        else:
            dir_path = project.get("directory_path", "")
            if dir_path:
                out_dir = Path(dir_path) / "compliance"
            else:
                out_dir = BASE_DIR / ".tmp" / "compliance" / project_id
        out_dir.mkdir(parents=True, exist_ok=True)

        out_file = (
            out_dir
            / f"fedramp_{baseline}_{project_id}_"
            f"{now.strftime('%Y%m%d_%H%M%S')}.md"
        )

        with open(out_file, "w", encoding="utf-8") as f:
            f.write(content)

        # -- Log audit event --
        _log_audit_event(
            conn,
            project_id,
            f"FedRAMP {baseline} assessment completed",
            {
                "baseline": baseline,
                "controls_assessed": total_controls,
                "overall_score": overall_score,
                "satisfied": satisfied_count,
                "other_than_satisfied": ots_count,
                "not_assessed": not_assessed_count,
                "not_applicable": na_count,
                "inherited_count": inherited_count,
                "gate_result": gate_result,
                "output_file": str(out_file),
            },
            out_file,
        )

        # -- Console output --
        print(f"FedRAMP {baseline.title()} assessment completed:")
        print(f"  File: {out_file}")
        print(f"  Baseline: {baseline.title()}")
        print(f"  Controls assessed: {total_controls}")
        print(f"  Overall readiness: {overall_score}%")
        print(
            f"  SAT={satisfied_count} OTS={ots_count} "
            f"NA={na_count} NOT_ASSESSED={not_assessed_count} "
            f"RISK_ACCEPTED={risk_accepted_count}"
        )
        print(f"  Inherited from NIST 800-53: {inherited_count}")

        for fam in family_order:
            s = summary.get(fam, {})
            if s.get("total", 0) == 0:
                continue
            print(
                f"  {fam} ({s['name']}): "
                f"SAT={s['satisfied']} "
                f"OTS={s['other_than_satisfied']} "
                f"NOT_ASSESSED={s['not_assessed']}"
            )

        if gate:
            print(f"\n  Gate: {gate_result['reason']}")

        return {
            "output_file": str(out_file),
            "results": results,
            "summary": summary,
            "overall_score": overall_score,
            "gate_result": gate_result,
            "metadata": {
                "baseline": baseline,
                "total_controls": total_controls,
                "satisfied": satisfied_count,
                "other_than_satisfied": ots_count,
                "not_applicable": na_count,
                "not_assessed": not_assessed_count,
                "risk_accepted": risk_accepted_count,
                "inherited_count": inherited_count,
            },
        }

    finally:
        conn.close()


def assess_project(
    project_id,
    baseline="moderate",
    project_dir=None,
    gate=False,
    output_path=None,
    db_path=None,
):
    """Alias for run_fedramp_assessment (MCP compatibility)."""
    return run_fedramp_assessment(
        project_id,
        baseline=baseline,
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
        description="Run FedRAMP Moderate/High assessment"
    )
    parser.add_argument(
        "--project-id", required=True, help="Project ID"
    )
    parser.add_argument(
        "--baseline",
        default="moderate",
        choices=["moderate", "high"],
        help="FedRAMP baseline (default: moderate)",
    )
    parser.add_argument(
        "--project-dir",
        help="Project directory for automated file-based checks",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Evaluate FedRAMP readiness gate (0 P1 other_than_satisfied = pass)",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory for the assessment report",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DB_PATH,
        help="Override database path",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output summary as JSON",
    )
    args = parser.parse_args()

    try:
        result = run_fedramp_assessment(
            project_id=args.project_id,
            baseline=args.baseline,
            project_dir=args.project_dir,
            gate=args.gate,
            output_path=args.output_dir,
            db_path=args.db_path,
        )

        if args.json:
            print(
                json.dumps(
                    {
                        "output_file": result.get("output_file"),
                        "overall_score": result.get("overall_score"),
                        "metadata": result.get("metadata"),
                        "summary": result.get("summary"),
                        "gate_result": result.get("gate_result"),
                    },
                    indent=2,
                )
            )

        if args.gate and not result["gate_result"]["passed"]:
            sys.exit(1)

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
