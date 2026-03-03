#!/usr/bin/env python3
# CUI // SP-CTI
"""SbD assessment tool per CISA Secure by Design and DoDI 5000.87.

Loads SbD requirements from cisa_sbd_requirements.json, performs automated checks
where possible, stores results in sbd_assessments table, evaluates SbD gates,
applies CUI markings, and logs audit events."""

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
SBD_REQUIREMENTS_PATH = BASE_DIR / "context" / "compliance" / "cisa_sbd_requirements.json"


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


# -----------------------------------------------------------------
# Configuration helpers
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


def _load_sbd_requirements():
    """Load SbD requirements from the JSON catalog."""
    if not SBD_REQUIREMENTS_PATH.exists():
        raise FileNotFoundError(
            f"SbD requirements file not found: {SBD_REQUIREMENTS_PATH}\n"
            "Expected: context/compliance/cisa_sbd_requirements.json"
        )
    with open(SBD_REQUIREMENTS_PATH, "r", encoding="utf-8") as f:
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
                "sbd_assessed",
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
# Auto-check helper: walk project files matching extensions
# -----------------------------------------------------------------

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


# -----------------------------------------------------------------
# Auto-check functions (20 checks)
# Each returns a dict:
#   {"status": "satisfied"|"not_satisfied"|"partially_satisfied"
#              |"not_applicable",
#    "evidence": "description of what was found",
#    "details": "specifics"}
# -----------------------------------------------------------------

def _check_mfa_patterns(project_dir):
    """SBD-01: Scan auth code for MFA/2FA/TOTP/FIDO multi-factor patterns."""
    auth_patterns = [
        r"\bMFA\b|multi.factor|MultiFactor",
        r"\b2FA\b|two.factor|TwoFactor",
        r"\bTOTP\b|totp|time.based.one.time",
        r"\bFIDO\b|fido2|WebAuthn|webauthn",
        r"authenticator|Authenticator",
        r"otp_secret|otp_verify|verify_otp",
    ]
    extensions = (".py", ".js", ".ts", ".java", ".yaml", ".yml")
    matched, total = _scan_files(project_dir, extensions, auth_patterns)

    if total == 0:
        return {
            "status": "not_satisfied",
            "evidence": "No source or config files found to assess.",
            "details": "Project directory lacks applicable files.",
        }

    if matched:
        return {
            "status": "satisfied",
            "evidence": (
                f"MFA/multi-factor authentication patterns found in "
                f"{len(matched)} file(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }

    # Check if any auth-related code exists at all
    auth_code_patterns = [
        r"login|authenticate|auth|password|credential",
    ]
    auth_files, _ = _scan_files(project_dir, extensions, auth_code_patterns)
    if not auth_files:
        return {
            "status": "not_satisfied",
            "evidence": "No authentication code detected in project.",
            "details": "No auth-related patterns found; cannot verify MFA support.",
        }

    return {
        "status": "not_satisfied",
        "evidence": (
            f"Authentication code found in {len(auth_files)} file(s) but no "
            "MFA/2FA/TOTP/FIDO patterns detected."
        ),
        "details": "Multi-factor authentication is required but not implemented.",
    }


def _check_default_passwords(project_dir):
    """SBD-02: Scan for hardcoded/default passwords (inverse check).

    Returns satisfied if NO default passwords are found.
    """
    bad_patterns = [
        r"password\s*=\s*[\"'][^\"']+[\"']",
        r"default_password",
        r"admin.*password|password.*admin",
        r"password123|passw0rd|qwerty|letmein",
        r"changeme|change_me|CHANGEME",
    ]
    extensions = (".py", ".js", ".ts", ".yaml", ".yml", ".json", ".conf", ".env")
    matched, total = _scan_files(project_dir, extensions, bad_patterns)

    if total == 0:
        return {
            "status": "not_satisfied",
            "evidence": "No source or config files found to assess.",
            "details": "Project directory lacks applicable files.",
        }

    if matched:
        return {
            "status": "not_satisfied",
            "evidence": (
                f"Default/hardcoded password patterns detected in "
                f"{len(matched)} file(s)."
            ),
            "details": (
                "Files with potential default passwords: "
                + "; ".join(os.path.basename(f) for f in matched[:5])
            ),
        }

    return {
        "status": "satisfied",
        "evidence": (
            f"Scanned {total} file(s) -- no default or hardcoded password "
            "patterns detected."
        ),
        "details": "No instances of hardcoded passwords, changeme, or default credentials.",
    }


def _check_memory_safe_language(project_dir):
    """SBD-03: Assess ratio of memory-safe to memory-unsafe language files."""
    safe_extensions = (".py", ".java", ".go", ".rs", ".cs", ".rb", ".kt")
    unsafe_extensions = (".c", ".cpp", ".cc", ".cxx", ".h", ".hpp")

    safe_count = 0
    unsafe_count = 0

    for root, _, files in os.walk(project_dir):
        for fname in files:
            lower = fname.lower()
            if any(lower.endswith(ext) for ext in safe_extensions):
                safe_count += 1
            elif any(lower.endswith(ext) for ext in unsafe_extensions):
                unsafe_count += 1

    total = safe_count + unsafe_count
    if total == 0:
        return {
            "status": "not_satisfied",
            "evidence": "No recognized programming language files found.",
            "details": "Cannot determine memory safety profile.",
        }

    safe_ratio = safe_count / total
    if safe_ratio > 0.9:
        return {
            "status": "satisfied",
            "evidence": (
                f"Memory-safe languages: {safe_count}/{total} files "
                f"({safe_ratio:.0%})."
            ),
            "details": (
                f"Safe: {safe_count} | Unsafe: {unsafe_count}. "
                "Exceeds 90% threshold."
            ),
        }
    elif safe_ratio > 0.5:
        return {
            "status": "partially_satisfied",
            "evidence": (
                f"Memory-safe languages: {safe_count}/{total} files "
                f"({safe_ratio:.0%})."
            ),
            "details": (
                f"Safe: {safe_count} | Unsafe: {unsafe_count}. "
                "Between 50-90%. Must exceed 90% for full satisfaction."
            ),
        }
    else:
        return {
            "status": "not_satisfied",
            "evidence": (
                f"Memory-safe languages: {safe_count}/{total} files "
                f"({safe_ratio:.0%})."
            ),
            "details": (
                f"Safe: {safe_count} | Unsafe: {unsafe_count}. "
                "At or below 50%. Significant memory safety risk."
            ),
        }


def _check_memory_safety_tooling(project_dir):
    """SBD-04: Scan build configs for memory-safety analysis tooling."""
    # First check if there is any memory-unsafe code
    unsafe_extensions = (".c", ".cpp", ".cc", ".cxx", ".h", ".hpp")
    has_unsafe = False
    for root, _, files in os.walk(project_dir):
        for fname in files:
            if any(fname.lower().endswith(ext) for ext in unsafe_extensions):
                has_unsafe = True
                break
        if has_unsafe:
            break

    if not has_unsafe:
        return {
            "status": "not_applicable",
            "evidence": "No memory-unsafe language files (C/C++) found in project.",
            "details": "Memory safety tooling check is not applicable.",
        }

    tooling_patterns = [
        r"AddressSanitizer|ASAN|asan",
        r"-fsanitize\s*=",
        r"Valgrind|valgrind",
        r"\bMSAN\b|MemorySanitizer",
        r"\bTSAN\b|ThreadSanitizer",
        r"\bUBSAN\b|UndefinedBehaviorSanitizer",
        r"clang.tidy|cppcheck|coverity",
    ]
    # Build config extensions plus common build system files
    extensions = (".cfg", ".ini", ".yaml", ".yml", ".toml")
    matched, total = _scan_files(project_dir, extensions, tooling_patterns)

    # Also check Makefile and CMakeLists.txt specifically
    build_files = _dir_or_file_exists(
        project_dir,
        glob_patterns=["Makefile", "makefile", "CMakeLists.txt", "*.cmake"],
    )
    build_matches = []
    for bf_path in build_files:
        try:
            with open(bf_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            for pattern in tooling_patterns:
                if re.search(pattern, content, re.IGNORECASE):
                    build_matches.append(bf_path)
                    break
        except Exception:
            continue

    all_matched = list(set(matched + build_matches))
    if all_matched:
        return {
            "status": "satisfied",
            "evidence": (
                f"Memory safety tooling patterns found in "
                f"{len(all_matched)} file(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_matched[:5]),
        }

    return {
        "status": "not_satisfied",
        "evidence": (
            "Memory-unsafe code present but no memory safety tooling detected."
        ),
        "details": (
            "Expected: AddressSanitizer, -fsanitize, Valgrind, MSAN, TSAN, "
            "or equivalent tooling in build configurations."
        ),
    }


def _check_patch_cadence(project_dir):
    """SBD-05: Look for automated dependency update tooling."""
    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "dependabot.yml",
            "dependabot.yaml",
            "renovate.json",
            ".renovaterc",
            ".renovaterc.json",
        ],
    )
    # Also check .github/dependabot.yml path
    github_dependabot = _dir_or_file_exists(
        project_dir,
        dir_names=[".github"],
    )
    if github_dependabot:
        dep_files = _dir_or_file_exists(
            project_dir,
            glob_patterns=[".github/dependabot.yml", ".github/dependabot.yaml"],
        )
        found.extend(dep_files)

    # Check for pip-compile, poetry.lock freshness indicators
    lock_files = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "requirements*.txt",
            "poetry.lock",
            "Pipfile.lock",
            "package-lock.json",
            "yarn.lock",
            "pnpm-lock.yaml",
        ],
    )

    all_found = list(set(found))
    if all_found:
        return {
            "status": "satisfied",
            "evidence": (
                f"Automated dependency update tooling found: "
                f"{len(all_found)} artifact(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }

    if lock_files:
        return {
            "status": "partially_satisfied",
            "evidence": (
                f"Lock files found ({len(lock_files)}) indicating dependency "
                "management, but no automated update tooling (Dependabot, "
                "Renovate) detected."
            ),
            "details": "; ".join(os.path.basename(f) for f in lock_files[:5]),
        }

    return {
        "status": "not_satisfied",
        "evidence": "No automated dependency update tooling detected.",
        "details": (
            "Expected: .github/dependabot.yml, renovate.json, .renovaterc, "
            "or equivalent automated patch management configuration."
        ),
    }


def _check_vuln_disclosure(project_dir):
    """SBD-06: Look for vulnerability disclosure policy files."""
    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "SECURITY.md",
            "SECURITY.txt",
            "security.txt",
            "security.md",
        ],
    )
    # Also check .well-known/security.txt
    well_known = _dir_or_file_exists(
        project_dir,
        glob_patterns=[".well-known/security.txt"],
    )
    found.extend(well_known)

    all_found = list(set(found))
    if all_found:
        return {
            "status": "satisfied",
            "evidence": (
                f"Vulnerability disclosure policy found: "
                f"{len(all_found)} file(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in all_found[:5]),
        }

    return {
        "status": "not_satisfied",
        "evidence": "No vulnerability disclosure policy detected.",
        "details": (
            "Expected: SECURITY.md, .well-known/security.txt, SECURITY.txt, "
            "or equivalent disclosure policy file."
        ),
    }


def _check_audit_logging_complete(project_dir):
    """SBD-08: Scan for comprehensive audit logging patterns.

    Checks for multiple distinct log event types.
    """
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

    # Also check for generic structured logging patterns
    struct_patterns = [
        r"audit_trail|AuditTrail",
        r"logging\.getLogger|getLogger",
        r"structlog|structured.log",
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
                f"Comprehensive audit logging detected: {count} distinct "
                f"log event types across {len(evidence_files)} file(s)."
            ),
            "details": (
                f"Event types: {', '.join(sorted(found_types))}. "
                f"Files: {'; '.join(os.path.basename(f) for f in evidence_files[:5])}"
            ),
        }
    elif count >= 1:
        return {
            "status": "partially_satisfied",
            "evidence": (
                f"Partial audit logging detected: {count} distinct log event "
                f"type(s) found."
            ),
            "details": (
                f"Event types: {', '.join(sorted(found_types))}. "
                "Need 3+ distinct log event types for full compliance."
            ),
        }

    return {
        "status": "not_satisfied",
        "evidence": "No audit logging patterns detected in source files.",
        "details": (
            "Expected: authentication logging, access logging, change logging, "
            "error logging, or security event logging patterns."
        ),
    }


def _check_tls_config(project_dir):
    """SBD-11: Scan for TLS 1.2+/1.3 and detect insecure protocols."""
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
            "status": "not_satisfied",
            "evidence": "No configuration or source files found to assess.",
            "details": "Project directory lacks files with expected extensions.",
        }

    if secure_matched and not insecure_matched:
        return {
            "status": "satisfied",
            "evidence": (
                f"TLS configuration patterns found in {len(secure_matched)} "
                f"file(s) with no insecure protocol patterns detected."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in secure_matched[:5]
            ),
        }
    elif secure_matched and insecure_matched:
        return {
            "status": "partially_satisfied",
            "evidence": (
                f"TLS patterns found in {len(secure_matched)} file(s), but "
                f"insecure patterns also detected in {len(insecure_matched)} "
                f"file(s)."
            ),
            "details": (
                "Insecure files: "
                + "; ".join(
                    os.path.basename(f) for f in insecure_matched[:5]
                )
                + ". Remove SSLv3, TLSv1.0, TLSv1.1, and verify=False usage."
            ),
        }

    return {
        "status": "not_satisfied",
        "evidence": "No TLS configuration patterns detected.",
        "details": (
            "Expected: TLS 1.2+, strong ciphers, SSLContext configuration. "
            "No HTTPS or TLS patterns found in project files."
        ),
    }


def _check_encryption_at_rest(project_dir):
    """SBD-12: Scan for encryption-at-rest configuration patterns."""
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
            "status": "not_satisfied",
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
        "status": "not_satisfied",
        "evidence": "No encryption-at-rest patterns detected.",
        "details": (
            "Expected: FIPS, AES-256, KMS, storage_encrypted, or "
            "server-side encryption configuration patterns."
        ),
    }


def _check_rbac_least_priv(project_dir):
    """SBD-14: Scan for RBAC / role / permission / least-privilege patterns."""
    patterns = [
        r"@login_required|@permission_required|@requires_auth",
        r"@Secured|@PreAuthorize|@RolesAllowed",
        r"role_required|check_permission|has_permission",
        r"\bRBAC\b|role.based.access",
        r"RoleBinding|ClusterRole|ClusterRoleBinding",
        r"least.privilege|minimum.privilege|principle.of.least",
        r"from\s+flask_login|from\s+django\.contrib\.auth",
    ]
    extensions = (".py", ".yaml", ".yml", ".js", ".ts", ".java")
    matched, total = _scan_files(project_dir, extensions, patterns)

    if total == 0:
        return {
            "status": "not_satisfied",
            "evidence": "No source files found to assess for RBAC patterns.",
            "details": "Project directory lacks applicable source files.",
        }

    if matched:
        return {
            "status": "satisfied",
            "evidence": (
                f"RBAC / least-privilege patterns found in "
                f"{len(matched)} file(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }

    return {
        "status": "not_satisfied",
        "evidence": "No RBAC or least-privilege access control patterns detected.",
        "details": (
            "Expected: @login_required, role_required, RBAC, RoleBinding, "
            "or least-privilege patterns."
        ),
    }


def _check_input_validation(project_dir):
    """SBD-16: Scan for input validation libraries and patterns."""
    patterns = [
        r"\bpydantic\b|from\s+pydantic|import\s+pydantic",
        r"\bmarshmallow\b|from\s+marshmallow|import\s+marshmallow",
        r"\bcerberus\b|from\s+cerberus|import\s+cerberus",
        r"\bvoluptuous\b|from\s+voluptuous|import\s+voluptuous",
        r"\bJoi\b|require\(['\"]joi|from\s+['\"]joi",
        r"\bZod\b|from\s+['\"]zod|import.*\bzod\b",
        r"@Valid|@NotNull|@NotBlank|@NotEmpty|@Size",
        r"validate_input|sanitize_input|input_validation",
        r"sanitize|validator\.validate|form\.validate",
    ]
    extensions = (".py", ".js", ".ts", ".java")
    matched, total = _scan_files(project_dir, extensions, patterns)

    if total == 0:
        return {
            "status": "not_satisfied",
            "evidence": "No source files found to assess for input validation.",
            "details": "Project directory lacks applicable source files.",
        }

    if len(matched) >= 2:
        return {
            "status": "satisfied",
            "evidence": (
                f"Input validation patterns found in "
                f"{len(matched)} file(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }
    elif len(matched) == 1:
        return {
            "status": "partially_satisfied",
            "evidence": (
                f"Input validation patterns found in only 1 file: "
                f"{os.path.basename(matched[0])}."
            ),
            "details": (
                "Validation found in a single file. Should be applied "
                "consistently across all input handling code."
            ),
        }

    return {
        "status": "not_satisfied",
        "evidence": "No input validation patterns detected.",
        "details": (
            "Expected: pydantic, marshmallow, cerberus, Joi, Zod, @Valid, "
            "validate_input, or sanitize patterns."
        ),
    }


def _check_output_encoding(project_dir):
    """SBD-17: Scan for XSS prevention / output encoding patterns."""
    patterns = [
        r"escape\(\)|html\.escape|cgi\.escape",
        r"\bmarkupsafe\b|from\s+markupsafe|Markup\(",
        r"\bbleach\b|bleach\.clean|bleach\.sanitize",
        r"DOMPurify|dompurify|sanitizeHtml|sanitize_html",
        r"htmlspecialchars|htmlentities",
        r"Content.Security.Policy|CSP|content_security_policy",
        r"auto_escape|autoescape|autoescaping",
    ]
    extensions = (".py", ".js", ".ts", ".java", ".html", ".jinja", ".jinja2")
    matched, total = _scan_files(project_dir, extensions, patterns)

    if total == 0:
        return {
            "status": "not_satisfied",
            "evidence": "No source or template files found to assess.",
            "details": "Project directory lacks applicable files.",
        }

    if matched:
        return {
            "status": "satisfied",
            "evidence": (
                f"Output encoding / XSS prevention patterns found in "
                f"{len(matched)} file(s)."
            ),
            "details": "; ".join(os.path.basename(f) for f in matched[:5]),
        }

    return {
        "status": "not_satisfied",
        "evidence": "No output encoding or XSS prevention patterns detected.",
        "details": (
            "Expected: escape(), markupsafe, bleach, DOMPurify, "
            "sanitizeHtml, CSP headers, or auto-escaping configuration."
        ),
    }


def _check_security_headers(project_dir):
    """SBD-18: Scan for security response headers configuration."""
    header_patterns = [
        (r"Content.Security.Policy|content_security_policy|CSP", "CSP"),
        (
            r"Strict.Transport.Security|strict_transport_security|HSTS",
            "HSTS",
        ),
        (r"X.Frame.Options|x_frame_options|DENY|SAMEORIGIN", "X-Frame-Options"),
        (
            r"X.Content.Type.Options|x_content_type_options|nosniff",
            "X-Content-Type-Options",
        ),
        (
            r"Access.Control.Allow.Origin|CORS|cors_allowed|cors_origins",
            "CORS",
        ),
    ]
    extensions = (".py", ".js", ".ts", ".yaml", ".yml", ".conf", ".json")
    found_headers = set()
    evidence_files = []

    for root, _, files in os.walk(project_dir):
        for fname in files:
            if not fname.endswith(extensions):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for pattern, header_name in header_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        found_headers.add(header_name)
                        if fpath not in evidence_files:
                            evidence_files.append(fpath)
            except Exception:
                continue

    count = len(found_headers)
    if count >= 3:
        return {
            "status": "satisfied",
            "evidence": (
                f"{count} distinct security headers configured: "
                f"{', '.join(sorted(found_headers))}."
            ),
            "details": (
                "Files: "
                + "; ".join(os.path.basename(f) for f in evidence_files[:5])
            ),
        }
    elif count >= 1:
        return {
            "status": "partially_satisfied",
            "evidence": (
                f"Only {count} security header(s) detected: "
                f"{', '.join(sorted(found_headers))}."
            ),
            "details": (
                "Need 3+ of: CSP, HSTS, X-Frame-Options, "
                "X-Content-Type-Options, CORS for full compliance."
            ),
        }

    return {
        "status": "not_satisfied",
        "evidence": "No security response headers detected.",
        "details": (
            "Expected: Content-Security-Policy, Strict-Transport-Security, "
            "X-Frame-Options, X-Content-Type-Options, or CORS configuration."
        ),
    }


def _check_secure_error_handling(project_dir):
    """SBD-19: Check for secure error handling and no debug info leakage."""
    secure_patterns = [
        r"DEBUG\s*=\s*False",
        r"@app\.errorhandler|custom.error.handler|error_handler",
        r"error_page|custom_error_response",
        r"app\.config\[.DEBUG.\]\s*=\s*False",
    ]
    insecure_patterns = [
        r"DEBUG\s*=\s*True",
        r"traceback\.print_exc|print_exc\(\)",
        r"print\s*\(.*traceback|print\s*\(.*stack",
        r"stack_trace.*response|stacktrace.*response",
        r"app\.config\[.DEBUG.\]\s*=\s*True",
    ]
    extensions = (".py", ".js", ".ts", ".yaml", ".yml", ".conf")

    secure_matched, total = _scan_files(
        project_dir, extensions, secure_patterns
    )
    insecure_matched, _ = _scan_files(
        project_dir, extensions, insecure_patterns
    )

    if total == 0:
        return {
            "status": "not_satisfied",
            "evidence": "No source or config files found to assess.",
            "details": "Project directory lacks applicable files.",
        }

    if secure_matched and not insecure_matched:
        return {
            "status": "satisfied",
            "evidence": (
                f"Secure error handling patterns found in "
                f"{len(secure_matched)} file(s) with no insecure patterns."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in secure_matched[:5]
            ),
        }
    elif secure_matched and insecure_matched:
        return {
            "status": "partially_satisfied",
            "evidence": (
                f"Secure error handling in {len(secure_matched)} file(s), "
                f"but insecure patterns in {len(insecure_matched)} file(s)."
            ),
            "details": (
                "Insecure files: "
                + "; ".join(
                    os.path.basename(f) for f in insecure_matched[:5]
                )
                + ". Remove DEBUG=True and stack trace exposure in responses."
            ),
        }
    elif insecure_matched and not secure_matched:
        return {
            "status": "not_satisfied",
            "evidence": (
                f"Insecure error handling patterns detected in "
                f"{len(insecure_matched)} file(s) with no secure patterns."
            ),
            "details": (
                "Files: "
                + "; ".join(
                    os.path.basename(f) for f in insecure_matched[:5]
                )
                + ". DEBUG=True, traceback exposure, or stack traces "
                "in responses detected."
            ),
        }

    return {
        "status": "not_satisfied",
        "evidence": "No error handling patterns detected (secure or insecure).",
        "details": (
            "Expected: DEBUG=False, custom error handlers, and absence of "
            "DEBUG=True or traceback exposure in responses."
        ),
    }


def _check_sbom_freshness(project_dir):
    """SBD-21: Check for SBOM files and their freshness (within 30 days)."""
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

    if not found:
        return {
            "status": "not_satisfied",
            "evidence": "No SBOM artifacts detected in the project.",
            "details": (
                "Expected: *sbom*.json, *bom*.xml, *cyclonedx*, or *spdx* "
                "files."
            ),
        }

    now = datetime.now(timezone.utc)
    fresh_files = []
    stale_files = []
    for fpath in found:
        try:
            mtime = datetime.utcfromtimestamp(os.path.getmtime(fpath))
            age_days = (now - mtime).days
            if age_days <= 30:
                fresh_files.append((fpath, age_days))
            else:
                stale_files.append((fpath, age_days))
        except Exception:
            stale_files.append((fpath, -1))

    if fresh_files and not stale_files:
        return {
            "status": "satisfied",
            "evidence": (
                f"SBOM artifact(s) found and fresh: {len(fresh_files)} "
                f"file(s) modified within 30 days."
            ),
            "details": "; ".join(
                f"{os.path.basename(f)} ({d}d old)" for f, d in fresh_files[:5]
            ),
        }
    elif fresh_files and stale_files:
        return {
            "status": "partially_satisfied",
            "evidence": (
                f"{len(fresh_files)} fresh SBOM(s) but "
                f"{len(stale_files)} stale SBOM(s) detected."
            ),
            "details": (
                "Stale: "
                + "; ".join(
                    f"{os.path.basename(f)} ({d}d old)"
                    for f, d in stale_files[:5]
                )
            ),
        }

    return {
        "status": "partially_satisfied",
        "evidence": (
            f"SBOM artifact(s) found but all are stale (>30 days old): "
            f"{len(stale_files)} file(s)."
        ),
        "details": (
            "Stale: "
            + "; ".join(
                f"{os.path.basename(f)} ({d}d old)" for f, d in stale_files[:5]
            )
            + ". Regenerate SBOM to meet freshness requirement."
        ),
    }


def _check_dep_vuln_scanning(project_dir):
    """SBD-22: Look for dependency vulnerability scanning tooling or results."""
    # Check for scanning tool configs
    found_configs = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            ".snyk",
            ".safety",
            ".safety-policy.yml",
            "audit-report.json",
            "dependency-check-report*",
            "pip-audit-report*",
            "npm-audit-report*",
        ],
    )

    # Scan CI config files for audit commands
    ci_patterns = [
        r"pip.audit|pip_audit|pipaudit",
        r"npm\s+audit|yarn\s+audit",
        r"\bsafety\b.*check|safety\s+scan",
        r"\bsnyk\b.*test|snyk\s+monitor",
        r"dependency.check|DependencyCheck",
        r"trivy\s+fs|grype\s+dir",
    ]
    ci_extensions = (
        ".yaml", ".yml", ".json", ".toml", ".cfg", ".ini",
    )
    ci_matched, _ = _scan_files(project_dir, ci_extensions, ci_patterns)

    all_found = list(set(found_configs + ci_matched))
    if all_found:
        return {
            "status": "satisfied",
            "evidence": (
                f"Dependency vulnerability scanning tooling/results found: "
                f"{len(all_found)} artifact(s)."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in all_found[:5]
            ),
        }

    return {
        "status": "not_satisfied",
        "evidence": "No dependency vulnerability scanning tooling or results detected.",
        "details": (
            "Expected: pip-audit, npm audit, safety, Snyk configs, "
            ".snyk, audit-report.json, or dependency-check-report files."
        ),
    }


def _check_threat_model(project_dir):
    """SBD-24: Look for threat model artifacts."""
    found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "threat-model*",
            "threat_model*",
            "STRIDE*",
            "PASTA*",
            "threat-analysis*",
            "threat_analysis*",
            "attack-tree*",
            "attack_tree*",
        ],
    )
    # Also check for threat model directories
    found_dirs = _dir_or_file_exists(
        project_dir,
        dir_names=[
            "threat-model",
            "threat_model",
            "threat-modeling",
            "threat_modeling",
        ],
    )
    # Check within docs/ or security/ subdirectories
    nested_found = _dir_or_file_exists(
        project_dir,
        glob_patterns=[
            "docs/threat-model*",
            "docs/threat_model*",
            "security/threat-model*",
            "security/threat_model*",
        ],
    )

    all_found = list(set(found + found_dirs + nested_found))
    if all_found:
        return {
            "status": "satisfied",
            "evidence": (
                f"Threat model artifact(s) found: {len(all_found)} item(s)."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in all_found[:5]
            ),
        }

    return {
        "status": "not_satisfied",
        "evidence": "No threat model artifacts detected.",
        "details": (
            "Expected: threat-model.md, threat_model.*, STRIDE.*, PASTA.*, "
            "threat-analysis.*, attack-tree.*, or threat-model/ directory."
        ),
    }


def _check_no_default_creds(project_dir):
    """SBD-28: Scan config files for default credential patterns (inverse check).

    Returns satisfied if NO default credentials are found.
    """
    bad_patterns = [
        r"admin[:/]admin|admin/admin",
        r"root[:/]root|root/root",
        r"password123|passw0rd",
        r"changeme|change_me|CHANGEME",
        r"default[_\s]*password|default[_\s]*credential",
        r"test[:/]test|test/test",
        r"username.*=.*admin.*\n.*password.*=.*admin",
    ]
    extensions = (
        ".yaml", ".yml", ".json", ".conf", ".env",
        ".ini", ".cfg", ".properties",
    )
    matched, total = _scan_files(project_dir, extensions, bad_patterns)

    if total == 0:
        return {
            "status": "not_satisfied",
            "evidence": "No configuration files found to assess.",
            "details": "Project directory lacks applicable config files.",
        }

    if matched:
        return {
            "status": "not_satisfied",
            "evidence": (
                f"Default credential patterns detected in "
                f"{len(matched)} config file(s)."
            ),
            "details": (
                "Files with potential default credentials: "
                + "; ".join(os.path.basename(f) for f in matched[:5])
                + ". Remove admin/admin, root/root, changeme, test/test, "
                "and other default credential patterns."
            ),
        }

    return {
        "status": "satisfied",
        "evidence": (
            f"Scanned {total} config file(s) -- no default credential "
            "patterns detected."
        ),
        "details": (
            "No instances of admin/admin, root/root, changeme, test/test, "
            "or other default credential patterns."
        ),
    }


def _check_secure_config_baselines(project_dir):
    """SBD-29: Check Dockerfiles for STIG hardening and secure config baselines."""
    # Check Dockerfiles for hardening
    dockerfiles = _dir_or_file_exists(
        project_dir,
        glob_patterns=["Dockerfile*", "*.dockerfile"],
    )

    hardened_docker = 0
    docker_evidence = []
    for df_path in dockerfiles:
        try:
            with open(df_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue

        checks = {
            "non_root_user": bool(
                re.search(r"USER\s+(?!root)\S+", content)
            ),
            "drop_capabilities": bool(
                re.search(
                    r"drop.*ALL|securityContext.*drop|cap_drop",
                    content,
                    re.IGNORECASE | re.DOTALL,
                )
            ),
            "read_only_rootfs": bool(
                re.search(
                    r"readOnlyRootFilesystem|read.only",
                    content,
                    re.IGNORECASE,
                )
            ),
            "minimal_base": bool(
                re.search(
                    r"FROM.*(:slim|:alpine|-slim|-minimal|distroless|hardened)",
                    content,
                    re.IGNORECASE,
                )
            ),
        }
        passed = sum(checks.values())
        if passed >= 2:
            hardened_docker += 1
            docker_evidence.append(
                f"{os.path.basename(df_path)}: {passed}/4 hardening checks"
            )

    # Check for insecure configs
    insecure_config_patterns = [
        r"DEBUG\s*=\s*True|DEBUG\s*:\s*true",
        r"Access.Control.Allow.Origin.*\*|CORS.*\*|allow_origins.*\*",
        r"AllowOverride\s+All|PermitRootLogin\s+yes",
    ]
    config_extensions = (".py", ".yaml", ".yml", ".conf", ".json", ".ini")
    insecure_matched, _ = _scan_files(
        project_dir, config_extensions, insecure_config_patterns
    )

    has_hardened = hardened_docker > 0
    has_insecure = len(insecure_matched) > 0

    if has_hardened and not has_insecure:
        return {
            "status": "satisfied",
            "evidence": (
                f"Secure config baselines detected: {hardened_docker} "
                f"hardened Dockerfile(s), no insecure configuration patterns."
            ),
            "details": "; ".join(docker_evidence),
        }
    elif has_hardened and has_insecure:
        return {
            "status": "partially_satisfied",
            "evidence": (
                f"Hardened Dockerfile(s) found ({hardened_docker}), but "
                f"insecure config patterns in {len(insecure_matched)} file(s)."
            ),
            "details": (
                "Hardened: " + "; ".join(docker_evidence)
                + " | Insecure configs: "
                + "; ".join(
                    os.path.basename(f) for f in insecure_matched[:5]
                )
            ),
        }
    elif not has_hardened and dockerfiles:
        return {
            "status": "not_satisfied",
            "evidence": (
                f"Dockerfiles found ({len(dockerfiles)}) but none pass "
                "STIG hardening checks."
            ),
            "details": (
                "Expected: non-root USER, drop ALL capabilities, "
                "read-only rootfs, minimal base image."
            ),
        }

    # No Dockerfiles -- check configs only
    secure_config_patterns = [
        r"DEBUG\s*=\s*False|DEBUG\s*:\s*false",
        r"security.*hardening|stig.*compliance|cis.*benchmark",
    ]
    secure_matched, _ = _scan_files(
        project_dir, config_extensions, secure_config_patterns
    )

    if secure_matched and not has_insecure:
        return {
            "status": "partially_satisfied",
            "evidence": (
                f"Secure configuration patterns found in "
                f"{len(secure_matched)} file(s) but no Dockerfiles to assess."
            ),
            "details": "; ".join(
                os.path.basename(f) for f in secure_matched[:5]
            ),
        }

    return {
        "status": "not_satisfied",
        "evidence": "No secure configuration baselines detected.",
        "details": (
            "Expected: STIG-hardened Dockerfiles (non-root USER, drop ALL), "
            "DEBUG=False, no wildcard CORS, no permissive security settings."
        ),
    }


def _check_cui_markings(project_dir):
    """SBD-31: Scan Python and Markdown files for CUI marking strings.

    Returns satisfied if >80% of files contain CUI markings.
    """
    patterns = [
        r"CUI\s*//\s*SP-CTI",
        r"CONTROLLED UNCLASSIFIED INFORMATION",
        r"\(CUI\)",
    ]
    matched, total = _scan_files(project_dir, (".py", ".md"), patterns)

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
            "evidence": (
                f"CUI markings found in {len(matched)}/{total} files "
                f"({ratio:.0%})."
            ),
            "details": f"Threshold: >80%. Files scanned: {total}.",
        }
    elif ratio > 0.4:
        return {
            "status": "partially_satisfied",
            "evidence": (
                f"CUI markings found in {len(matched)}/{total} files "
                f"({ratio:.0%})."
            ),
            "details": "Some files lack CUI markings. Must exceed 80% coverage.",
        }
    else:
        return {
            "status": "not_satisfied",
            "evidence": (
                f"CUI markings found in only {len(matched)}/{total} files "
                f"({ratio:.0%})."
            ),
            "details": (
                "Majority of files lack CUI markings. "
                "Requires >80% coverage."
            ),
        }


# -----------------------------------------------------------------
# Requirement-to-check mapping
# -----------------------------------------------------------------

AUTO_CHECKS = {
    "SBD-01": _check_mfa_patterns,
    "SBD-02": _check_default_passwords,
    "SBD-03": _check_memory_safe_language,
    "SBD-04": _check_memory_safety_tooling,
    "SBD-05": _check_patch_cadence,
    "SBD-06": _check_vuln_disclosure,
    "SBD-08": _check_audit_logging_complete,
    "SBD-11": _check_tls_config,
    "SBD-12": _check_encryption_at_rest,
    "SBD-14": _check_rbac_least_priv,
    "SBD-16": _check_input_validation,
    "SBD-17": _check_output_encoding,
    "SBD-18": _check_security_headers,
    "SBD-19": _check_secure_error_handling,
    "SBD-21": _check_sbom_freshness,
    "SBD-22": _check_dep_vuln_scanning,
    "SBD-24": _check_threat_model,
    "SBD-28": _check_no_default_creds,
    "SBD-29": _check_secure_config_baselines,
    "SBD-31": _check_cui_markings,
}


# -----------------------------------------------------------------
# Core assessment function
# -----------------------------------------------------------------

def run_sbd_assessment(
    project_id,
    domain="all",
    project_dir=None,
    gate=False,
    output_path=None,
    db_path=None,
):
    """Run SbD assessment per CISA Secure by Design and DoDI 5000.87.

    Args:
        project_id: The project identifier.
        domain: Filter to a specific SbD domain or "all".
        project_dir: Project directory for automated file-based checks.
        gate: If True, evaluate the SbD gate (0 critical not_satisfied = pass).
        output_path: Override output directory for the assessment report.
        db_path: Override database path.

    Returns:
        Dict with assessment results, summary, gate result, and output file path.
    """
    conn = _get_connection(db_path)
    try:
        project = _get_project(conn, project_id)

        # Load SbD requirements catalog
        sbd_data = _load_sbd_requirements()
        metadata = sbd_data.get("metadata", {})
        requirements = sbd_data.get("requirements", [])

        # Filter by domain if specified
        if domain != "all":
            requirements = [
                r for r in requirements
                if r.get("domain") == domain
            ]
            if not requirements:
                raise ValueError(
                    f"No requirements found for domain '{domain}'. "
                    "Valid domains: Authentication, Memory Safety, "
                    "Vulnerability Mgmt, Intrusion Evidence, Cryptography, "
                    "Access Control, Input Handling, Error Handling, "
                    "Supply Chain, Threat Modeling, Defense in Depth, "
                    "Secure Defaults, CUI Compliance, DoD Software Assurance."
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

        # -- Assess each requirement --
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
                    evidence = (
                        "No automated check implemented for this requirement."
                    )
                    notes = "Manual review required."

            elif automation_level == "auto" and not can_auto_check:
                status = "not_assessed"
                evidence = (
                    "No project directory available for automated scanning."
                )
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
                        notes = (
                            "Semi-automated check failed; "
                            "full manual review required."
                        )
                else:
                    status = "not_assessed"
                    evidence = (
                        "Semi-automated: no automated component implemented."
                    )
                    notes = (
                        f"Manual review required. Evidence needed: "
                        f"{req.get('evidence_required', 'See requirement description.')}"
                    )

            elif automation_level == "semi" and not can_auto_check:
                status = "not_assessed"
                evidence = (
                    "Semi-automated check requires project directory."
                )
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
                    f"Evidence needed: "
                    f"{req.get('evidence_required', 'See requirement description.')}"
                )

            result_entry = {
                "requirement_id": req_id,
                "domain": req.get("domain", ""),
                "title": req.get("title", ""),
                "description": req.get("description", ""),
                "priority": req.get("priority", "medium"),
                "automation_level": automation_level,
                "nist_controls": req.get("nist_controls", []),
                "cisa_commitment": req.get("cisa_commitment", ""),
                "status": status,
                "evidence": evidence,
                "details": details,
                "notes": notes,
            }
            results.append(result_entry)

            # -- Upsert into sbd_assessments table --
            # Uses INSERT OR REPLACE on UNIQUE(project_id, requirement_id)
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO sbd_assessments
                       (project_id, assessment_date, assessor, domain,
                        requirement_id, status, evidence_description,
                        evidence_path, automation_result, cisa_commitment,
                        notes, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id,
                        now.isoformat(),
                        "icdev-compliance-engine",
                        req.get("domain", ""),
                        req_id,
                        status,
                        evidence,
                        details if details else None,
                        json.dumps({
                            "automation_level": automation_level,
                            "check_function": (
                                AUTO_CHECKS[req_id].__name__
                                if req_id in AUTO_CHECKS
                                else None
                            ),
                        }),
                        req.get("cisa_commitment", ""),
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

        # -- Build summary by domain --
        domain_order = [
            "Authentication",
            "Memory Safety",
            "Vulnerability Mgmt",
            "Intrusion Evidence",
            "Cryptography",
            "Access Control",
            "Input Handling",
            "Error Handling",
            "Supply Chain",
            "Threat Modeling",
            "Defense in Depth",
            "Secure Defaults",
            "CUI Compliance",
            "DoD Software Assurance",
        ]
        summary = {}
        for d in domain_order:
            summary[d] = {
                "total": 0,
                "satisfied": 0,
                "partially_satisfied": 0,
                "not_satisfied": 0,
                "not_assessed": 0,
                "not_applicable": 0,
                "risk_accepted": 0,
            }

        for r in results:
            d = r["domain"]
            if d not in summary:
                summary[d] = {
                    "total": 0,
                    "satisfied": 0,
                    "partially_satisfied": 0,
                    "not_satisfied": 0,
                    "not_assessed": 0,
                    "not_applicable": 0,
                    "risk_accepted": 0,
                }
            summary[d]["total"] += 1
            st = r["status"]
            if st in summary[d]:
                summary[d][st] += 1

        # -- Gate evaluation --
        critical_not_satisfied = 0
        critical_failures = []
        for r in results:
            if (
                r["priority"] == "critical"
                and r["status"] == "not_satisfied"
            ):
                critical_not_satisfied += 1
                critical_failures.append(
                    f"{r['requirement_id']}: {r['title']}"
                )

        gate_passed = critical_not_satisfied == 0
        gate_result = {
            "evaluated": gate,
            "passed": gate_passed,
            "critical_not_satisfied": critical_not_satisfied,
            "critical_failures": critical_failures,
            "reason": (
                "PASS: 0 critical-priority requirements have status "
                "not_satisfied"
                if gate_passed
                else (
                    f"FAIL: {critical_not_satisfied} critical-priority "
                    f"requirement(s) not satisfied: "
                    f"{', '.join(critical_failures)}"
                )
            ),
        }

        # -- Generate Markdown assessment report --
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
            "# SbD Assessment Report -- CISA Secure by Design / DoDI 5000.87",
            "",
            f"**Project:** {project.get('name', project_id)} ({project_id})",
            f"**Assessment Date:** {now.strftime('%Y-%m-%d %H:%M UTC')}",
            "**Assessor:** ICDEV Compliance Engine (automated)",
            f"**Domain Scope:** {domain}",
            (
                f"**CISA SbD Revision:** "
                f"{metadata.get('revision', 'N/A')}"
            ),
            "**Classification:** CUI // SP-CTI",
            "",
            "---",
            "",
            "## Executive Summary",
            "",
        ]

        # Summary table
        lines.append(
            "| Domain | Total | Satisfied | Partial | Not Satisfied "
            "| Not Assessed | N/A | Risk Accepted |"
        )
        lines.append(
            "|--------|-------|-----------|---------|---------------"
            "|--------------|-----|---------------|"
        )

        grand_total = {
            "total": 0,
            "satisfied": 0,
            "partially_satisfied": 0,
            "not_satisfied": 0,
            "not_assessed": 0,
            "not_applicable": 0,
            "risk_accepted": 0,
        }

        for d in domain_order:
            s = summary.get(d, {})
            if s.get("total", 0) == 0:
                continue
            lines.append(
                f"| {d} | {s['total']} | {s['satisfied']} | "
                f"{s['partially_satisfied']} | {s['not_satisfied']} | "
                f"{s['not_assessed']} | {s['not_applicable']} | "
                f"{s['risk_accepted']} |"
            )
            for key in grand_total:
                grand_total[key] += s.get(key, 0)

        lines.append(
            f"| **Total** | **{grand_total['total']}** | "
            f"**{grand_total['satisfied']}** | "
            f"**{grand_total['partially_satisfied']}** | "
            f"**{grand_total['not_satisfied']}** | "
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
                "## SbD Gate Evaluation",
                "",
                f"**Gate Result:** {gate_label}",
                (
                    "**Criteria:** 0 critical-priority requirements "
                    "with status not_satisfied"
                ),
                f"**Critical Failures:** {critical_not_satisfied}",
                "",
            ])
            if critical_failures:
                lines.append("**Failed Requirements:**")
                for cf in critical_failures:
                    lines.append(f"- {cf}")
                lines.append("")

        lines.extend(["---", ""])

        # -- Detailed findings per domain --
        lines.append("## Detailed Findings")
        lines.append("")

        for d in domain_order:
            domain_results = [r for r in results if r["domain"] == d]
            if not domain_results:
                continue

            lines.append(f"### {d}")
            lines.append("")

            for r in domain_results:
                status_display = r["status"].replace("_", " ").title()
                priority_display = r["priority"].upper()
                nist_str = (
                    ", ".join(r["nist_controls"])
                    if r["nist_controls"]
                    else "N/A"
                )
                cisa_str = r.get("cisa_commitment", "N/A") or "N/A"

                lines.extend([
                    f"#### {r['requirement_id']}: {r['title']}",
                    "",
                    f"**Priority:** {priority_display}  ",
                    f"**Status:** {status_display}  ",
                    f"**Automation Level:** {r['automation_level']}  ",
                    f"**NIST Controls:** {nist_str}  ",
                    f"**CISA Commitment:** {cisa_str}",
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

        domain_suffix = (
            domain.lower().replace(" ", "_")
            if domain != "all"
            else "all"
        )
        out_file = (
            out_dir
            / f"sbd_cisa_{project_id}_{domain_suffix}_"
            f"{now.strftime('%Y%m%d_%H%M%S')}.md"
        )

        with open(out_file, "w", encoding="utf-8") as f:
            f.write(content)

        # -- Log audit event --
        _log_audit_event(
            conn,
            project_id,
            f"SbD assessment completed ({domain})",
            {
                "domain": domain,
                "requirements_assessed": len(results),
                "summary": {k: v for k, v in grand_total.items()},
                "gate_result": gate_result,
                "output_file": str(out_file),
            },
            out_file,
        )

        # -- Console output --
        print("SbD assessment completed:")
        print(f"  File: {out_file}")
        print(f"  Scope: {domain}")
        print(f"  Requirements assessed: {len(results)}")
        for d in domain_order:
            s = summary.get(d, {})
            if s.get("total", 0) == 0:
                continue
            print(
                f"  {d}: "
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


def assess_project(
    project_id,
    domain="all",
    project_dir=None,
    gate=False,
    output_path=None,
    db_path=None,
):
    """Alias for run_sbd_assessment (MCP compatibility)."""
    return run_sbd_assessment(
        project_id,
        domain=domain,
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
        description="Run SbD assessment per CISA Secure by Design"
    )
    parser.add_argument(
        "--project-id", required=True, help="Project ID"
    )
    parser.add_argument(
        "--domain",
        default="all",
        choices=[
            "all",
            "Authentication",
            "Memory Safety",
            "Vulnerability Mgmt",
            "Intrusion Evidence",
            "Cryptography",
            "Access Control",
            "Input Handling",
            "Error Handling",
            "Supply Chain",
            "Threat Modeling",
            "Defense in Depth",
            "Secure Defaults",
            "CUI Compliance",
            "DoD Software Assurance",
        ],
        help="SbD domain to assess (default: all)",
    )
    parser.add_argument(
        "--project-dir",
        help="Project directory for automated file-based checks",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="Evaluate SbD gate (0 critical not_satisfied = pass)",
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
        result = run_sbd_assessment(
            project_id=args.project_id,
            domain=args.domain,
            project_dir=args.project_dir,
            gate=args.gate,
            output_path=args.output_dir,
            db_path=args.db_path,
        )
        print(
            json.dumps(
                {
                    "output_file": result.get("output_file"),
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
