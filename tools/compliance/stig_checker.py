#!/usr/bin/env python3
# CUI // SP-CTI
"""STIG checklist auto-generation and assessment tool.
Loads STIG template, performs automated checks where possible,
stores results in stig_findings table, evaluates security gates,
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
STIG_TEMPLATES_DIR = BASE_DIR / "context" / "compliance" / "stig_templates"


def _get_connection(db_path=None):
    """Get a database connection."""
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
    """Load project data."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found.")
    return dict(row)


def _load_stig_template(stig_id):
    """Load a STIG template JSON file."""
    template_file = STIG_TEMPLATES_DIR / f"{stig_id}_stig.json"
    if not template_file.exists():
        raise FileNotFoundError(
            f"STIG template not found: {template_file}\n"
            f"Available templates: {_list_stig_templates()}"
        )
    with open(template_file, "r", encoding="utf-8") as f:
        return json.load(f)


def _list_stig_templates():
    """List available STIG template IDs."""
    if not STIG_TEMPLATES_DIR.exists():
        return []
    return [
        f.stem.replace("_stig", "")
        for f in STIG_TEMPLATES_DIR.glob("*_stig.json")
    ]


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


def _log_audit_event(conn, project_id, action, details, file_path=None):
    """Log an audit trail event."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "stig_checked",
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
# Auto-check functions
# Each returns a tuple: (status, comments)
#   status: "Open" | "NotAFinding" | "Not_Applicable" | "Not_Reviewed"
#   comments: explanation string
# ─────────────────────────────────────────────────────────────

def _check_url_parameters(project_dir):
    """V-222602: Check that sensitive info is not in URL parameters."""
    # Look for common patterns of session/token in URL params
    bad_patterns = [
        r'[?&](session_id|token|password|secret|api_key)=',
        r'GET.*[?&](auth|credential|ssn)=',
    ]
    issues = []
    for root, _, files in os.walk(project_dir):
        for fname in files:
            if not fname.endswith(('.py', '.js', '.ts', '.java', '.html')):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for pattern in bad_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        issues.append(fpath)
                        break
            except Exception:
                continue

    if issues:
        return "Open", f"Potential sensitive URL params in: {', '.join(issues[:5])}"
    return "Not_Reviewed", "Automated scan found no obvious issues; manual review needed."


def _check_input_validation(project_dir):
    """V-222604: Check for input validation patterns."""
    # Look for parameterized queries and validation frameworks
    good_patterns = [
        r'parameterized|prepared_statement|bindparam|execute\(.+,\s*\(',
        r'@validates|@validator|ValidationError|validate_input|sanitize',
        r'escape_html|bleach\.clean|markupsafe\.escape|xss_clean',
    ]
    found_validation = False
    for root, _, files in os.walk(project_dir):
        for fname in files:
            if not fname.endswith(('.py', '.js', '.ts', '.java')):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for pattern in good_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        found_validation = True
                        break
            except Exception:
                continue
        if found_validation:
            break

    if found_validation:
        return "Not_Reviewed", "Validation patterns detected; manual verification of completeness needed."
    return "Not_Reviewed", "No validation patterns detected; manual review required."


def _check_access_enforcement(project_dir):
    """V-222607: Check for access control enforcement."""
    auth_patterns = [
        r'@login_required|@permission_required|@requires_auth',
        r'requireAuth|isAuthenticated|authorize|checkPermission',
        r'@Secured|@PreAuthorize|@RolesAllowed',
        r'middleware.*auth|authMiddleware|guardRoute',
    ]
    found_auth = False
    for root, _, files in os.walk(project_dir):
        for fname in files:
            if not fname.endswith(('.py', '.js', '.ts', '.java')):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for pattern in auth_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        found_auth = True
                        break
            except Exception:
                continue
        if found_auth:
            break

    if found_auth:
        return "Not_Reviewed", "Authorization patterns detected; verify enforcement completeness manually."
    return "Not_Reviewed", "No authorization patterns found; manual review required."


def _check_fips_crypto(project_dir):
    """V-222609: Check for FIPS-validated crypto usage."""
    bad_crypto = [
        r'md5|sha1[^0-9]|DES\b|RC4|arcfour|3DES',
    ]
    good_crypto = [
        r'sha256|sha384|sha512|aes|AES_256|FIPS|fips_mode',
    ]
    found_bad = []
    found_good = False

    for root, _, files in os.walk(project_dir):
        for fname in files:
            if not fname.endswith(('.py', '.js', '.ts', '.java', '.yaml', '.yml', '.tf')):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for pattern in bad_crypto:
                    if re.search(pattern, content, re.IGNORECASE):
                        found_bad.append(fpath)
                        break
                for pattern in good_crypto:
                    if re.search(pattern, content, re.IGNORECASE):
                        found_good = True
            except Exception:
                continue

    if found_bad:
        return "Open", f"Deprecated crypto found in: {', '.join(found_bad[:5])}"
    if found_good:
        return "Not_Reviewed", "FIPS-compatible crypto patterns detected; verify FIPS validation status."
    return "Not_Reviewed", "No cryptographic usage detected; manual review needed."


def _check_cookie_flags(project_dir):
    """V-222612: Check for Secure and HttpOnly cookie flags."""
    secure_cookie_patterns = [
        r'SESSION_COOKIE_SECURE\s*=\s*True',
        r'SESSION_COOKIE_HTTPONLY\s*=\s*True',
        r'secure:\s*true.*httpOnly:\s*true|httpOnly:\s*true.*secure:\s*true',
        r'cookie\.setSecure\(true\)|cookie\.setHttpOnly\(true\)',
        r'SameSite.*Strict|SameSite.*Lax',
    ]
    found = 0
    for root, _, files in os.walk(project_dir):
        for fname in files:
            if not fname.endswith(('.py', '.js', '.ts', '.java', '.yaml', '.yml')):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for pattern in secure_cookie_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        found += 1
            except Exception:
                continue

    if found >= 2:
        return "Not_Reviewed", "Cookie security patterns detected; verify all cookies are covered."
    return "Not_Reviewed", "Cookie security configuration not confirmed; manual review needed."


def _check_security_headers(project_dir):
    """V-222614: Check for security headers configuration."""
    header_patterns = [
        r'Content-Security-Policy|CSP_DEFAULT_SRC|contentSecurityPolicy',
        r'X-Content-Type-Options.*nosniff',
        r'X-Frame-Options.*(DENY|SAMEORIGIN)',
        r'Strict-Transport-Security|HSTS|hsts',
        r'Referrer-Policy',
        r'helmet\(\)|securityHeaders|SecurityMiddleware',
    ]
    found = 0
    for root, _, files in os.walk(project_dir):
        for fname in files:
            if not fname.endswith(('.py', '.js', '.ts', '.java', '.yaml', '.yml', '.conf')):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for pattern in header_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        found += 1
            except Exception:
                continue

    if found >= 3:
        return "Not_Reviewed", f"Security header patterns found ({found} matches); verify all required headers."
    return "Not_Reviewed", "Security headers not confirmed; manual review needed."


def _check_csrf_protection(project_dir):
    """V-222617: Check for CSRF protection."""
    csrf_patterns = [
        r'csrf_token|csrfmiddleware|CsrfViewMiddleware',
        r'csurf|csrf\(\)|csrfProtection',
        r'@csrf_protect|csrf_exempt',
        r'CsrfFilter|_csrf|csrfToken',
    ]
    found = False
    for root, _, files in os.walk(project_dir):
        for fname in files:
            if not fname.endswith(('.py', '.js', '.ts', '.java', '.html')):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for pattern in csrf_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        found = True
                        break
            except Exception:
                continue
        if found:
            break

    if found:
        return "Not_Reviewed", "CSRF protection patterns detected; verify coverage of all state-changing endpoints."
    return "Not_Reviewed", "No CSRF protection patterns found; manual review required."


def _check_audit_logging(project_dir):
    """V-222620: Check for audit/security logging."""
    logging_patterns = [
        r'audit_log|audit_trail|security_log',
        r'logging\.getLogger|logger\.\w+|log\.\w+\(',
        r'AuditEvent|SecurityEvent|audit_entry',
    ]
    found = False
    for root, _, files in os.walk(project_dir):
        for fname in files:
            if not fname.endswith(('.py', '.js', '.ts', '.java')):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for pattern in logging_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        found = True
                        break
            except Exception:
                continue
        if found:
            break

    if found:
        return "Not_Reviewed", "Logging patterns detected; verify all security events are captured per AU-2."
    return "Not_Reviewed", "No structured logging detected; manual review needed."


def _check_error_handling(project_dir):
    """V-222635: Check that detailed errors are not exposed."""
    bad_patterns = [
        r'DEBUG\s*=\s*True',
        r'NODE_ENV.*development',
        r'traceback\.print_exc|print_stack',
        r'stack_trace.*response|response.*stack_trace',
    ]
    good_patterns = [
        r'DEBUG\s*=\s*False',
        r'NODE_ENV.*production',
        r'custom_error_handler|errorHandler|exception_handler',
    ]
    found_bad = False
    found_good = False

    for root, _, files in os.walk(project_dir):
        for fname in files:
            if not fname.endswith(('.py', '.js', '.ts', '.java', '.yaml', '.yml', '.env')):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for pattern in bad_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        found_bad = True
                for pattern in good_patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        found_good = True
            except Exception:
                continue

    if found_bad:
        return "Open", "Debug mode or detailed error exposure detected."
    if found_good:
        return "Not_Reviewed", "Production error handling detected; verify no leakage of sensitive details."
    return "Not_Reviewed", "Error handling configuration not confirmed; manual review needed."


# Map finding IDs to auto-check functions
AUTO_CHECKS = {
    "V-222602": _check_url_parameters,
    "V-222604": _check_input_validation,
    "V-222607": _check_access_enforcement,
    "V-222609": _check_fips_crypto,
    "V-222612": _check_cookie_flags,
    "V-222614": _check_security_headers,
    "V-222617": _check_csrf_protection,
    "V-222620": _check_audit_logging,
    "V-222635": _check_error_handling,
}


def run_stig_check(
    project_id,
    stig_id="webapp",
    target_type="app",
    gate=False,
    output_path=None,
    db_path=None,
):
    """Run STIG checklist assessment for a project.

    Args:
        project_id: The project identifier
        stig_id: STIG template identifier (e.g., 'webapp')
        target_type: Target type being assessed
        gate: If True, evaluate security gate (0 CAT1 Open = pass)
        output_path: Override output file path
        db_path: Override database path

    Returns:
        Dict with assessment results and gate status
    """
    conn = _get_connection(db_path)
    try:
        project = _get_project(conn, project_id)
        stig_data = _load_stig_template(stig_id)

        # Determine project directory for auto-checks
        project_dir = project.get("directory_path", "")
        if project_dir and Path(project_dir).is_dir():
            can_auto_check = True
        else:
            can_auto_check = False

        findings = stig_data.get("findings", [])
        results = []
        now = datetime.now(timezone.utc)

        for finding in findings:
            status = "Not_Reviewed"
            comments = "Requires manual assessment."

            # Try auto-check if project directory exists
            if can_auto_check and finding["finding_id"] in AUTO_CHECKS:
                try:
                    status, comments = AUTO_CHECKS[finding["finding_id"]](project_dir)
                except Exception as e:
                    status = "Not_Reviewed"
                    comments = f"Auto-check error: {e}"

            result = {
                "finding_id": finding["finding_id"],
                "rule_id": finding["rule_id"],
                "severity": finding["severity"],
                "title": finding["title"],
                "description": finding.get("description", ""),
                "check_content": finding.get("check_content", ""),
                "fix_text": finding.get("fix_text", ""),
                "status": status,
                "comments": comments,
            }
            results.append(result)

            # Upsert into stig_findings table
            existing = conn.execute(
                """SELECT id FROM stig_findings
                   WHERE project_id = ? AND stig_id = ? AND finding_id = ?""",
                (project_id, stig_id, finding["finding_id"]),
            ).fetchone()

            if existing:
                conn.execute(
                    """UPDATE stig_findings
                       SET status = ?, comments = ?, assessed_by = ?,
                           assessed_at = ?, updated_at = ?
                       WHERE id = ?""",
                    (status, comments, "icdev-stig-checker", now.isoformat(),
                     now.isoformat(), existing["id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO stig_findings
                       (project_id, stig_id, finding_id, rule_id, severity,
                        title, description, check_content, fix_text,
                        status, comments, target_type, assessed_by, assessed_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id, stig_id, finding["finding_id"],
                        finding["rule_id"], finding["severity"],
                        finding["title"], finding.get("description", ""),
                        finding.get("check_content", ""), finding.get("fix_text", ""),
                        status, comments, target_type,
                        "icdev-stig-checker", now.isoformat(),
                    ),
                )

        conn.commit()

        # Build summary
        summary = {
            "CAT1": {"Open": 0, "NotAFinding": 0, "Not_Applicable": 0, "Not_Reviewed": 0},
            "CAT2": {"Open": 0, "NotAFinding": 0, "Not_Applicable": 0, "Not_Reviewed": 0},
            "CAT3": {"Open": 0, "NotAFinding": 0, "Not_Applicable": 0, "Not_Reviewed": 0},
        }
        for r in results:
            sev = r["severity"]
            st = r["status"]
            if sev in summary and st in summary[sev]:
                summary[sev][st] += 1

        # Gate evaluation
        cat1_open = summary["CAT1"]["Open"]
        gate_result = {
            "evaluated": gate,
            "passed": cat1_open == 0,
            "cat1_open": cat1_open,
            "reason": (
                "PASS: 0 CAT1 findings Open" if cat1_open == 0
                else f"FAIL: {cat1_open} CAT1 finding(s) Open"
            ),
        }

        # Generate checklist document
        cui_config = _load_cui_config()
        doc_header = cui_config.get("document_header", "CUI // SP-CTI").strip()
        doc_footer = cui_config.get("document_footer", "CUI // SP-CTI").strip()

        lines = [
            doc_header,
            "",
            f"# STIG Checklist: {stig_data.get('metadata', {}).get('title', stig_id)}",
            "",
            f"**Project:** {project.get('name', project_id)} ({project_id})",
            f"**STIG ID:** {stig_id}",
            f"**STIG Version:** {stig_data.get('metadata', {}).get('version', 'N/A')}",
            f"**Target Type:** {target_type}",
            f"**Assessment Date:** {now.strftime('%Y-%m-%d %H:%M UTC')}",
            "**Assessed By:** ICDEV STIG Checker (automated)",
            "**Classification:** CUI // SP-CTI",
            "",
            "---",
            "",
            "## Summary",
            "",
            "| Severity | Open | Not A Finding | Not Applicable | Not Reviewed | Total |",
            "|----------|------|---------------|----------------|--------------|-------|",
        ]

        total_findings = 0
        for cat in ["CAT1", "CAT2", "CAT3"]:
            s = summary[cat]
            cat_total = sum(s.values())
            total_findings += cat_total
            lines.append(
                f"| {cat} | {s['Open']} | {s['NotAFinding']} | {s['Not_Applicable']} | {s['Not_Reviewed']} | {cat_total} |"
            )

        lines.append(f"| **Total** | | | | | **{total_findings}** |")
        lines.append("")

        if gate:
            gate_icon = "PASS" if gate_result["passed"] else "**FAIL**"
            lines.extend([
                "## Security Gate Evaluation",
                "",
                f"**Gate Result:** {gate_icon}",
                "**Criteria:** 0 CAT1 findings Open",
                f"**CAT1 Open:** {cat1_open}",
                "",
            ])

        lines.extend(["---", ""])

        # Detailed findings
        lines.append("## Detailed Findings")
        lines.append("")

        for r in results:
            status_display = r["status"].replace("_", " ")
            lines.extend([
                f"### {r['finding_id']}: {r['title']}",
                "",
                f"**Rule ID:** {r['rule_id']}",
                f"**Severity:** {r['severity']}",
                f"**Status:** {status_display}",
                "",
                f"**Comments:** {r['comments']}",
                "",
                "---",
                "",
            ])

        lines.extend([doc_footer, ""])
        content = "\n".join(lines)

        # Write output file
        if output_path:
            out_file = Path(output_path)
        else:
            dir_path = project.get("directory_path", "")
            if dir_path:
                out_dir = Path(dir_path) / "compliance"
            else:
                out_dir = BASE_DIR / ".tmp" / "compliance" / project_id
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / f"stig_{stig_id}_{project_id}_{now.strftime('%Y%m%d_%H%M%S')}.md"

        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(content)

        # Log audit event
        _log_audit_event(conn, project_id, f"STIG check completed ({stig_id})", {
            "stig_id": stig_id,
            "target_type": target_type,
            "summary": summary,
            "gate_result": gate_result,
            "output_file": str(out_file),
        }, out_file)

        print("STIG check completed:")
        print(f"  File: {out_file}")
        print(f"  STIG: {stig_id}")
        print(f"  Findings assessed: {len(results)}")
        for cat in ["CAT1", "CAT2", "CAT3"]:
            s = summary[cat]
            print(f"  {cat}: Open={s['Open']} NAF={s['NotAFinding']} NA={s['Not_Applicable']} NR={s['Not_Reviewed']}")

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


def main():
    parser = argparse.ArgumentParser(
        description="STIG checklist auto-generation and assessment"
    )
    parser.add_argument("--project", required=True, help="Project ID")
    parser.add_argument(
        "--stig-id", default="webapp",
        help="STIG template ID (default: webapp)"
    )
    parser.add_argument(
        "--target-type", default="app",
        help="Target type being assessed (default: app)"
    )
    parser.add_argument(
        "--gate", action="store_true",
        help="Evaluate security gate (0 CAT1 = pass)"
    )
    parser.add_argument("--output", help="Output file path")
    parser.add_argument("--db", help="Database path")
    parser.add_argument(
        "--json", action="store_true",
        help="Output results as JSON"
    )
    parser.add_argument(
        "--list-stigs", action="store_true",
        help="List available STIG templates"
    )
    args = parser.parse_args()

    if args.list_stigs:
        templates = _list_stig_templates()
        if templates:
            print("Available STIG templates:")
            for t in templates:
                print(f"  {t}")
        else:
            print("No STIG templates found.")
        return

    try:
        result = run_stig_check(
            project_id=args.project,
            stig_id=args.stig_id,
            target_type=args.target_type,
            gate=args.gate,
            output_path=args.output,
            db_path=Path(args.db) if args.db else None,
        )

        if args.json:
            # Don't duplicate console output
            print(json.dumps({
                "output_file": result["output_file"],
                "summary": result["summary"],
                "gate_result": result["gate_result"],
            }, indent=2))

        if args.gate and not result["gate_result"]["passed"]:
            sys.exit(1)

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
