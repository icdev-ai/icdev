#!/usr/bin/env python3
# CUI // SP-CTI
"""CSSP assessment tool per DoD Instruction 8530.01.

Loads CSSP requirements from dod_cssp_8530.json, performs automated checks
where possible, stores results in cssp_assessments table, evaluates CSSP
gates, applies CUI markings, and logs audit events."""

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
CSSP_REQUIREMENTS_PATH = BASE_DIR / "context" / "compliance" / "dod_cssp_8530.json"


# ─────────────────────────────────────────────────────────────
# Database helpers
# ─────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────
# Configuration helpers
# ─────────────────────────────────────────────────────────────

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


def _load_cssp_requirements():
    """Load CSSP requirements from the JSON catalog."""
    if not CSSP_REQUIREMENTS_PATH.exists():
        raise FileNotFoundError(
            f"CSSP requirements file not found: {CSSP_REQUIREMENTS_PATH}\n"
            "Expected: context/compliance/dod_cssp_8530.json"
        )
    with open(CSSP_REQUIREMENTS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


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
                "cssp_assessed",
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


# ─────────────────────────────────────────────────────────────
# Auto-check helper: walk project files matching extensions
# ─────────────────────────────────────────────────────────────

def _scan_files(project_dir, extensions, patterns, threshold=1):
    """Scan project files for regex patterns.

    Args:
        project_dir: Root directory to walk.
        extensions: Tuple of file extensions to include (e.g. ('.py', '.md')).
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
            # Also check one level deeper (e.g. infra/terraform/)
            for child in project_path.rglob(dname):
                if child.is_dir() and str(child) not in found:
                    found.append(str(child))

    if glob_patterns:
        for gp in glob_patterns:
            for match in project_path.rglob(gp):
                if str(match) not in found:
                    found.append(str(match))

    return found


# ─────────────────────────────────────────────────────────────
# Auto-check functions
# Each returns a dict:
#   {"status": "satisfied"|"not_satisfied"|"partially_satisfied",
#    "evidence": "description of what was found",
#    "details": "specifics"}
# ─────────────────────────────────────────────────────────────

def _check_cui_markings(project_dir):
    """Scan Python and Markdown files for CUI marking strings.

    Returns satisfied if >80% of files contain CUI markings.
    """
    patterns = [r"CUI\s*//\s*SP-CTI", r"CONTROLLED UNCLASSIFIED INFORMATION", r"\(CUI\)"]
    matched, total = _scan_files(
        project_dir, (".py", ".md"), patterns
    )
    if total == 0:
        return {
            "status": "not_satisfied",
            "evidence": "No Python or Markdown files found to assess.",
            "details": "Project directory contains no .py or .md files.",
        }
    ratio = len(matched) / total
    if ratio > 0.8:
        return {
            "status": "satisfied",
            "evidence": f"CUI markings found in {len(matched)}/{total} files ({ratio:.0%}).",
            "details": f"Threshold: >80%. Files scanned: {total}.",
        }
    elif ratio > 0.4:
        return {
            "status": "partially_satisfied",
            "evidence": f"CUI markings found in {len(matched)}/{total} files ({ratio:.0%}).",
            "details": "Some files lack CUI markings. Must exceed 80% coverage.",
        }
    else:
        return {
            "status": "not_satisfied",
            "evidence": f"CUI markings found in only {len(matched)}/{total} files ({ratio:.0%}).",
            "details": "Majority of files lack CUI markings. Requires >80% coverage.",
        }


def _check_siem_config(project_dir):
    """Look for Splunk forwarder configs, Filebeat configs, or siem/ directory."""
    found = _dir_or_file_exists(
        project_dir,
        dir_names=["siem"],
        glob_patterns=["splunk*.conf", "filebeat*.yml", "*forwarder*.conf"],
    )
    if found:
        return {
            "status": "satisfied",
            "evidence": f"SIEM configuration artifacts found: {len(found)} item(s).",
            "details": "; ".join(found[:5]),
        }
    return {
        "status": "not_satisfied",
        "evidence": "No SIEM configuration artifacts detected.",
        "details": "Expected: splunk*.conf, filebeat*.yml, or siem/ directory.",
    }


def _check_audit_logging(project_dir):
    """Scan Python files for audit_trail, audit_log patterns."""
    patterns = [
        r"audit_trail|audit_log|security_log",
        r"AuditEvent|AuditEntry|audit_entry",
        r"append.only|immutable.*log|tamper.evident",
    ]
    matched, total = _scan_files(project_dir, (".py",), patterns)
    if matched:
        return {
            "status": "satisfied",
            "evidence": f"Audit logging patterns found in {len(matched)} file(s).",
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }
    return {
        "status": "not_satisfied",
        "evidence": "No audit logging patterns detected in Python source.",
        "details": "Expected: audit_trail, audit_log, or AuditEvent patterns.",
    }


def _check_encryption_config(project_dir):
    """Scan for TLS, SSL, HTTPS, encrypt, FIPS patterns across config and code."""
    patterns = [
        r"\bTLS\b|TLS_1_[23]|tls_version",
        r"\bSSL\b|ssl_context|SSLContext",
        r"\bHTTPS\b|https://",
        r"encrypt|FIPS|fips_mode|AES_256|AES-256",
        r"mTLS|mutual.TLS|client.cert",
    ]
    extensions = (".py", ".yaml", ".yml", ".tf", ".conf", ".json", ".toml")
    matched, total = _scan_files(project_dir, extensions, patterns)
    if total == 0:
        return {
            "status": "not_satisfied",
            "evidence": "No configuration or source files found to assess.",
            "details": "Project directory lacks files with expected extensions.",
        }
    if matched:
        return {
            "status": "satisfied",
            "evidence": f"Encryption/TLS patterns found in {len(matched)} file(s).",
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }
    return {
        "status": "not_satisfied",
        "evidence": "No encryption or TLS configuration patterns detected.",
        "details": "Expected: TLS, SSL, HTTPS, FIPS, or encrypt patterns.",
    }


def _check_network_policy(project_dir):
    """Look for Kubernetes NetworkPolicy YAML files or networkpolicy patterns."""
    # Check for K8s NetworkPolicy manifests
    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=["*networkpolicy*.yaml", "*networkpolicy*.yml", "*network-policy*.yaml"],
    )
    # Also scan YAML files for NetworkPolicy kind
    np_patterns = [r"kind:\s*NetworkPolicy", r"networkPolicy", r"default.deny"]
    matched, _ = _scan_files(project_dir, (".yaml", ".yml"), np_patterns)

    all_evidence = list(set(found + matched))
    if all_evidence:
        return {
            "status": "satisfied",
            "evidence": f"Network policy artifacts found: {len(all_evidence)} item(s).",
            "details": "; ".join(os.path.basename(f) for f in all_evidence[:5]),
        }
    return {
        "status": "not_satisfied",
        "evidence": "No Kubernetes NetworkPolicy manifests or network segmentation config detected.",
        "details": "Expected: NetworkPolicy YAML manifests or firewall rule configurations.",
    }


def _check_iac_config(project_dir):
    """Look for terraform/, ansible/, k8s/ directories or *.tf, *.yml IaC files."""
    found_dirs = _dir_or_file_exists(
        project_dir,
        dir_names=["terraform", "ansible", "k8s", "infrastructure", "infra"],
    )
    found_files = _dir_or_file_exists(
        project_dir,
        glob_patterns=["*.tf", "playbook*.yml", "ansible*.yml", "*.tfvars"],
    )
    all_found = list(set(found_dirs + found_files))
    if all_found:
        return {
            "status": "satisfied",
            "evidence": f"IaC artifacts found: {len(all_found)} item(s).",
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }
    return {
        "status": "not_satisfied",
        "evidence": "No Infrastructure as Code artifacts detected.",
        "details": "Expected: terraform/, ansible/, k8s/ directories or *.tf, playbook*.yml files.",
    }


def _check_stig_hardened(project_dir):
    """Look for Dockerfiles with non-root USER and drop capabilities patterns."""
    dockerfiles = _dir_or_file_exists(
        project_dir,
        glob_patterns=["Dockerfile*", "*.dockerfile"],
    )
    if not dockerfiles:
        return {
            "status": "not_satisfied",
            "evidence": "No Dockerfiles found in the project.",
            "details": "Cannot verify STIG hardening without container definitions.",
        }

    hardened_count = 0
    hardening_evidence = []
    for df_path in dockerfiles:
        try:
            with open(df_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue

        checks = {
            "non_root_user": bool(re.search(r"USER\s+(?!root)\S+", content)),
            "drop_capabilities": bool(re.search(
                r"drop.*ALL|securityContext.*drop|cap_drop", content, re.IGNORECASE | re.DOTALL
            )),
            "read_only_rootfs": bool(re.search(
                r"readOnlyRootFilesystem|read.only", content, re.IGNORECASE
            )),
            "minimal_base": bool(re.search(
                r"FROM.*(:slim|:alpine|-slim|-minimal|distroless|hardened)", content, re.IGNORECASE
            )),
        }
        passed = sum(checks.values())
        if passed >= 2:
            hardened_count += 1
            hardening_evidence.append(
                f"{os.path.basename(df_path)}: {passed}/4 hardening checks passed"
            )

    if hardened_count == len(dockerfiles):
        return {
            "status": "satisfied",
            "evidence": f"All {hardened_count} Dockerfile(s) show STIG hardening patterns.",
            "details": "; ".join(hardening_evidence),
        }
    elif hardened_count > 0:
        return {
            "status": "partially_satisfied",
            "evidence": f"{hardened_count}/{len(dockerfiles)} Dockerfile(s) show hardening.",
            "details": "; ".join(hardening_evidence),
        }
    return {
        "status": "not_satisfied",
        "evidence": "Dockerfiles found but lack STIG hardening patterns.",
        "details": "Expected: non-root USER, drop ALL capabilities, read-only rootfs.",
    }


def _check_rbac_patterns(project_dir):
    """Scan for role, permission, @login_required, @requires_auth patterns."""
    patterns = [
        r"@login_required|@permission_required|@requires_auth",
        r"@Secured|@PreAuthorize|@RolesAllowed",
        r"role_required|check_permission|has_permission",
        r"RBAC|role.based.access|RoleBinding|ClusterRole",
        r"from\s+flask_login|from\s+django\.contrib\.auth",
    ]
    matched, total = _scan_files(
        project_dir, (".py", ".yaml", ".yml", ".js", ".ts", ".java"), patterns
    )
    if matched:
        return {
            "status": "satisfied",
            "evidence": f"RBAC / access control patterns found in {len(matched)} file(s).",
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }
    if total == 0:
        return {
            "status": "not_satisfied",
            "evidence": "No source files found to assess for RBAC patterns.",
            "details": "Project directory lacks applicable source files.",
        }
    return {
        "status": "not_satisfied",
        "evidence": "No RBAC or access control patterns detected.",
        "details": "Expected: @login_required, role_required, RBAC, RoleBinding patterns.",
    }


def _check_ir_plan(project_dir):
    """Look for incident response plan documentation files."""
    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "*incident*response*.md",
            "*incident*response*.pdf",
            "ir-plan*",
            "ir_plan*",
            "*incident-response*",
            "*incident_response*",
        ],
    )
    # Also check for an IR directory
    ir_dirs = _dir_or_file_exists(
        project_dir,
        dir_names=["incident-response", "incident_response", "ir"],
    )
    all_found = list(set(found + ir_dirs))
    if all_found:
        return {
            "status": "satisfied",
            "evidence": f"Incident response documentation found: {len(all_found)} item(s).",
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }
    return {
        "status": "not_satisfied",
        "evidence": "No incident response plan or documentation detected.",
        "details": "Expected: incident*response*.md, ir-plan*, or incident_response/ directory.",
    }


def _check_sbom_exists(project_dir):
    """Look for SBOM JSON or CycloneDX BOM XML files."""
    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "*sbom*.json",
            "*bom*.xml",
            "*sbom*.xml",
            "*cyclonedx*",
            "*spdx*",
        ],
    )
    if found:
        return {
            "status": "satisfied",
            "evidence": f"SBOM artifact(s) found: {len(found)} file(s).",
            "details": "; ".join(os.path.basename(f) for f in found[:5]),
        }
    return {
        "status": "not_satisfied",
        "evidence": "No SBOM artifacts detected.",
        "details": "Expected: *sbom*.json, *bom*.xml, *cyclonedx*, or *spdx* files.",
    }


def _check_vuln_scan_results(project_dir):
    """Look for vulnerability scan results in compliance/ or security/ dirs."""
    found_dirs = _dir_or_file_exists(
        project_dir,
        dir_names=["compliance", "security", "scan-results", "scan_results"],
    )
    found_files = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "*scan*result*.json",
            "*scan*result*.xml",
            "*sast*report*",
            "*vulnerability*report*",
            "*bandit*report*",
            "*audit*report*",
        ],
    )
    # Also scan for SAST tool output patterns in JSON files
    sast_patterns = [r"bandit|safety|pip.audit|trivy|grype|snyk"]
    matched, _ = _scan_files(
        project_dir, (".json", ".xml"), sast_patterns
    )
    all_found = list(set(found_dirs + found_files + matched))
    if all_found:
        return {
            "status": "satisfied",
            "evidence": f"Vulnerability scan artifacts found: {len(all_found)} item(s).",
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }
    return {
        "status": "not_satisfied",
        "evidence": "No vulnerability scan results detected.",
        "details": "Expected: scan result files in compliance/ or security/ directories.",
    }


def _check_pki_cac(project_dir):
    """Scan for CAC, PKI, x509, certificate, smart_card patterns."""
    patterns = [
        r"\bCAC\b|Common.Access.Card",
        r"\bPKI\b|Public.Key.Infrastructure",
        r"x509|X\.509|x_509",
        r"certificate|cert_path|ssl_cert|tls_cert",
        r"smart.card|smartcard|OCSP|CRL",
        r"client.certificate|mutual.auth|mTLS.*client",
    ]
    extensions = (".py", ".yaml", ".yml", ".conf", ".tf", ".json", ".js", ".ts")
    matched, total = _scan_files(project_dir, extensions, patterns)
    if matched:
        return {
            "status": "satisfied",
            "evidence": f"PKI/CAC authentication patterns found in {len(matched)} file(s).",
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }
    if total == 0:
        return {
            "status": "not_satisfied",
            "evidence": "No applicable files found to assess PKI/CAC configuration.",
            "details": "Project directory lacks configuration or source files.",
        }
    return {
        "status": "not_satisfied",
        "evidence": "No PKI or CAC authentication patterns detected.",
        "details": "Expected: CAC, PKI, x509, certificate, smart_card patterns.",
    }


# ─────────────────────────────────────────────────────────────
# Requirement-to-check mapping
# ─────────────────────────────────────────────────────────────

AUTO_CHECKS = {
    "ID-2": _check_sbom_exists,
    "ID-4": _check_cui_markings,
    "PR-1": _check_pki_cac,
    "PR-2": _check_encryption_config,
    "PR-3": _check_encryption_config,
    "PR-5": _check_network_policy,
    "PR-6": _check_stig_hardened,
    "PR-8": _check_rbac_patterns,
    "DE-2": _check_siem_config,
    "DE-3": _check_audit_logging,
    "DE-7": _check_vuln_scan_results,
    "RS-1": _check_ir_plan,
    "SU-3": _check_iac_config,
}


# ─────────────────────────────────────────────────────────────
# Core assessment function
# ─────────────────────────────────────────────────────────────

def run_cssp_assessment(
    project_id,
    functional_area="all",
    project_dir=None,
    gate=False,
    output_path=None,
    db_path=None,
):
    """Run CSSP assessment per DoD Instruction 8530.01.

    Args:
        project_id: The project identifier.
        functional_area: Filter to a specific functional area or "all".
        project_dir: Project directory for automated file-based checks.
        gate: If True, evaluate the CSSP gate (0 critical not_satisfied = pass).
        output_path: Override output directory for the assessment report.
        db_path: Override database path.

    Returns:
        Dict with assessment results, summary, gate result, and output file path.
    """
    conn = _get_connection(db_path)
    try:
        project = _get_project(conn, project_id)

        # Load CSSP requirements catalog
        cssp_data = _load_cssp_requirements()
        metadata = cssp_data.get("metadata", {})
        requirements = cssp_data.get("requirements", [])

        # Filter by functional area if specified
        if functional_area != "all":
            requirements = [
                r for r in requirements
                if r["functional_area"] == functional_area
            ]
            if not requirements:
                raise ValueError(
                    f"No requirements found for functional area '{functional_area}'. "
                    "Valid areas: Identify, Protect, Detect, Respond, Sustain."
                )

        # Resolve project directory for auto-checks
        if project_dir and Path(project_dir).is_dir():
            can_auto_check = True
        elif project.get("directory_path") and Path(project["directory_path"]).is_dir():
            project_dir = project["directory_path"]
            can_auto_check = True
        else:
            can_auto_check = False

        now = datetime.now(timezone.utc)
        results = []

        # ── Assess each requirement ──
        for req in requirements:
            req_id = req["id"]
            automation_level = req.get("automation_level", "manual")
            status = "not_assessed"
            evidence = ""
            details = ""
            notes = ""

            if automation_level == "auto" and can_auto_check:
                if req_id in AUTO_CHECKS:
                    try:
                        check_result = AUTO_CHECKS[req_id](project_dir)
                        status = check_result["status"]
                        evidence = check_result["evidence"]
                        details = check_result.get("details", "")
                    except Exception as e:
                        status = "not_assessed"
                        evidence = f"Auto-check error: {e}"
                        notes = "Auto-check failed; manual review required."
                else:
                    # Auto-level requirement without a mapped check function
                    status = "not_assessed"
                    evidence = "No automated check implemented for this requirement."
                    notes = "Manual review required."

            elif automation_level == "auto" and not can_auto_check:
                status = "not_assessed"
                evidence = "No project directory available for automated scanning."
                notes = "Provide --project-dir to enable auto-checks."

            elif automation_level == "semi" and can_auto_check:
                # Run partial check if a mapped function exists
                if req_id in AUTO_CHECKS:
                    try:
                        check_result = AUTO_CHECKS[req_id](project_dir)
                        status = check_result["status"]
                        evidence = check_result["evidence"]
                        details = check_result.get("details", "")
                        notes = (
                            "Semi-automated check completed. "
                            "Manual review required to verify full compliance."
                        )
                    except Exception as e:
                        status = "not_assessed"
                        evidence = f"Partial auto-check error: {e}"
                        notes = "Semi-automated check failed; full manual review required."
                else:
                    status = "not_assessed"
                    evidence = "Semi-automated: no automated component implemented."
                    notes = (
                        f"Manual review required. Evidence needed: "
                        f"{req.get('evidence_required', 'See requirement description.')}"
                    )

            elif automation_level == "semi" and not can_auto_check:
                status = "not_assessed"
                evidence = "Semi-automated check requires project directory."
                notes = (
                    f"Manual review required. Evidence needed: "
                    f"{req.get('evidence_required', 'See requirement description.')}"
                )

            else:
                # manual automation_level
                status = "not_assessed"
                evidence = "Manual assessment required."
                notes = (
                    f"This requirement must be verified manually. "
                    f"Evidence needed: {req.get('evidence_required', 'See requirement description.')}"
                )

            result_entry = {
                "requirement_id": req_id,
                "functional_area": req["functional_area"],
                "functional_area_code": req.get("functional_area_code", ""),
                "title": req["title"],
                "description": req["description"],
                "priority": req.get("priority", "medium"),
                "automation_level": automation_level,
                "nist_controls": req.get("nist_controls", []),
                "status": status,
                "evidence": evidence,
                "details": details,
                "notes": notes,
            }
            results.append(result_entry)

            # ── Upsert into cssp_assessments table ──
            # Uses INSERT OR REPLACE on UNIQUE(project_id, requirement_id)
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO cssp_assessments
                       (project_id, assessment_date, assessor, functional_area,
                        requirement_id, status, evidence_description, evidence_path,
                        automation_result, notes, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id,
                        now.isoformat(),
                        "icdev-compliance-engine",
                        req["functional_area"],
                        req_id,
                        status,
                        evidence,
                        details if details else None,
                        json.dumps({
                            "automation_level": automation_level,
                            "check_function": AUTO_CHECKS.get(req_id, lambda _: None).__name__
                            if req_id in AUTO_CHECKS else None,
                        }),
                        notes if notes else None,
                        now.isoformat(),
                    ),
                )
            except Exception as e:
                print(
                    f"Warning: Could not upsert assessment for {req_id}: {e}",
                    file=sys.stderr,
                )

        conn.commit()

        # ── Build summary by functional area ──
        area_order = ["Identify", "Protect", "Detect", "Respond", "Sustain"]
        summary = {}
        for area in area_order:
            summary[area] = {
                "total": 0,
                "satisfied": 0,
                "partially_satisfied": 0,
                "not_satisfied": 0,
                "not_assessed": 0,
                "not_applicable": 0,
                "risk_accepted": 0,
            }

        for r in results:
            area = r["functional_area"]
            if area not in summary:
                summary[area] = {
                    "total": 0, "satisfied": 0, "partially_satisfied": 0,
                    "not_satisfied": 0, "not_assessed": 0,
                    "not_applicable": 0, "risk_accepted": 0,
                }
            summary[area]["total"] += 1
            st = r["status"]
            if st in summary[area]:
                summary[area][st] += 1

        # ── Gate evaluation ──
        critical_not_satisfied = 0
        critical_failures = []
        for r in results:
            if r["priority"] == "critical" and r["status"] == "not_satisfied":
                critical_not_satisfied += 1
                critical_failures.append(f"{r['requirement_id']}: {r['title']}")

        gate_passed = critical_not_satisfied == 0
        gate_result = {
            "evaluated": gate,
            "passed": gate_passed,
            "critical_not_satisfied": critical_not_satisfied,
            "critical_failures": critical_failures,
            "reason": (
                "PASS: 0 critical-priority requirements have status not_satisfied"
                if gate_passed
                else (
                    f"FAIL: {critical_not_satisfied} critical-priority requirement(s) "
                    f"not satisfied: {', '.join(critical_failures)}"
                )
            ),
        }

        # ── Generate Markdown assessment report ──
        cui_config = _load_cui_config()
        doc_header = cui_config.get("document_header", "CUI // SP-CTI").strip()
        doc_footer = cui_config.get("document_footer", "CUI // SP-CTI").strip()

        lines = [
            doc_header,
            "",
            "# CSSP Assessment Report -- DoD Instruction 8530.01",
            "",
            f"**Project:** {project.get('name', project_id)} ({project_id})",
            f"**Assessment Date:** {now.strftime('%Y-%m-%d %H:%M UTC')}",
            "**Assessor:** ICDEV Compliance Engine (automated)",
            f"**Functional Area Scope:** {functional_area}",
            f"**DoDI 8530.01 Revision:** {metadata.get('revision', 'N/A')}",
            "**Classification:** CUI // SP-CTI",
            "",
            "---",
            "",
            "## Executive Summary",
            "",
        ]

        # Summary table
        lines.append(
            "| Functional Area | Total | Satisfied | Partial | Not Satisfied "
            "| Not Assessed | N/A | Risk Accepted |"
        )
        lines.append(
            "|-----------------|-------|-----------|---------|---------------"
            "|--------------|-----|---------------|"
        )

        grand_total = {
            "total": 0, "satisfied": 0, "partially_satisfied": 0,
            "not_satisfied": 0, "not_assessed": 0,
            "not_applicable": 0, "risk_accepted": 0,
        }

        for area in area_order:
            s = summary.get(area, {})
            if s.get("total", 0) == 0:
                continue
            lines.append(
                f"| {area} | {s['total']} | {s['satisfied']} | "
                f"{s['partially_satisfied']} | {s['not_satisfied']} | "
                f"{s['not_assessed']} | {s['not_applicable']} | {s['risk_accepted']} |"
            )
            for key in grand_total:
                grand_total[key] += s.get(key, 0)

        lines.append(
            f"| **Total** | **{grand_total['total']}** | **{grand_total['satisfied']}** | "
            f"**{grand_total['partially_satisfied']}** | **{grand_total['not_satisfied']}** | "
            f"**{grand_total['not_assessed']}** | **{grand_total['not_applicable']}** | "
            f"**{grand_total['risk_accepted']}** |"
        )
        lines.append("")

        # Gate evaluation section
        if gate:
            gate_label = "PASS" if gate_result["passed"] else "**FAIL**"
            lines.extend([
                "## CSSP Gate Evaluation",
                "",
                f"**Gate Result:** {gate_label}",
                "**Criteria:** 0 critical-priority requirements with status not_satisfied",
                f"**Critical Failures:** {critical_not_satisfied}",
                "",
            ])
            if critical_failures:
                lines.append("**Failed Requirements:**")
                for cf in critical_failures:
                    lines.append(f"- {cf}")
                lines.append("")

        lines.extend(["---", ""])

        # ── Detailed findings per functional area ──
        lines.append("## Detailed Findings")
        lines.append("")

        for area in area_order:
            area_results = [r for r in results if r["functional_area"] == area]
            if not area_results:
                continue

            lines.append(f"### {area}")
            lines.append("")

            for r in area_results:
                status_display = r["status"].replace("_", " ").title()
                priority_display = r["priority"].upper()
                nist_str = ", ".join(r["nist_controls"]) if r["nist_controls"] else "N/A"

                lines.extend([
                    f"#### {r['requirement_id']}: {r['title']}",
                    "",
                    f"**Priority:** {priority_display}  ",
                    f"**Status:** {status_display}  ",
                    f"**Automation Level:** {r['automation_level']}  ",
                    f"**NIST Controls:** {nist_str}",
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

        # ── Write output file ──
        if output_path:
            out_dir = Path(output_path)
        else:
            dir_path = project.get("directory_path", "")
            if dir_path:
                out_dir = Path(dir_path) / "compliance"
            else:
                out_dir = BASE_DIR / ".tmp" / "compliance" / project_id
        out_dir.mkdir(parents=True, exist_ok=True)

        area_suffix = functional_area.lower().replace(" ", "_") if functional_area != "all" else "all"
        out_file = out_dir / f"cssp_8530_{project_id}_{area_suffix}_{now.strftime('%Y%m%d_%H%M%S')}.md"

        with open(out_file, "w", encoding="utf-8") as f:
            f.write(content)

        # ── Log audit event ──
        _log_audit_event(conn, project_id, f"CSSP assessment completed ({functional_area})", {
            "functional_area": functional_area,
            "requirements_assessed": len(results),
            "summary": {k: v for k, v in grand_total.items()},
            "gate_result": gate_result,
            "output_file": str(out_file),
        }, out_file)

        # ── Console output ──
        print("CSSP assessment completed:")
        print(f"  File: {out_file}")
        print(f"  Scope: {functional_area}")
        print(f"  Requirements assessed: {len(results)}")
        for area in area_order:
            s = summary.get(area, {})
            if s.get("total", 0) == 0:
                continue
            print(
                f"  {area}: "
                f"SAT={s['satisfied']} "
                f"PARTIAL={s['partially_satisfied']} "
                f"NOT_SAT={s['not_satisfied']} "
                f"NOT_ASSESSED={s['not_assessed']}"
            )

        if gate:
            print(f"\n  Gate: {gate_result['reason']}")

        return {
            "output_file": str(out_file),
            "results": results,
            "summary": summary,
            "gate_result": gate_result,
        }

    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
# CLI entrypoint
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run CSSP assessment per DoD Instruction 8530.01"
    )
    parser.add_argument("--project-id", required=True, help="Project ID")
    parser.add_argument(
        "--functional-area",
        default="all",
        choices=["all", "Identify", "Protect", "Detect", "Respond", "Sustain"],
        help="Functional area to assess (default: all)",
    )
    parser.add_argument(
        "--project-dir",
        help="Project directory for automated file-based checks",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Evaluate CSSP gate (0 critical not_satisfied = pass)",
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
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    try:
        result = run_cssp_assessment(
            project_id=args.project_id,
            functional_area=args.functional_area,
            project_dir=args.project_dir,
            gate=args.gate,
            output_path=args.output_dir,
            db_path=args.db_path,
        )
        print(json.dumps({
            "output_file": result.get("output_file"),
            "summary": result.get("summary"),
            "gate_result": result.get("gate_result"),
        }, indent=2))

        if args.gate and not result["gate_result"]["passed"]:
            sys.exit(1)

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
