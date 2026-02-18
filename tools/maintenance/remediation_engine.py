#!/usr/bin/env python3
"""Remediation Engine — auto-implements dependency fixes and tracks remediation SLAs.

For each vulnerable/outdated dependency:
1. Determine fix action (version bump, replacement, risk acceptance)
2. Update the appropriate dependency file
3. Create git branch for the fix
4. Run verification tests
5. Track in remediation_actions table

CLI: python tools/maintenance/remediation_engine.py --project-id <id> [--vulnerability-id ID] [--auto] [--dry-run] [--json]
"""

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONFIG_PATH = BASE_DIR / "args" / "maintenance_config.yaml"

# Severity ordering for auto-remediation gating
SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Open a SQLite connection with row_factory set."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_project(conn, project_id):
    """Fetch project row or raise ValueError."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project not found: {project_id}")
    return dict(row)


def _log_audit_event(conn, project_id, action, details):
    """Write an immutable audit trail entry for remediation activity."""
    conn.execute(
        """INSERT INTO audit_trail
           (project_id, event_type, actor, action, details, classification)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            project_id,
            "vulnerability_resolved",
            "maintenance/remediation_engine",
            action,
            json.dumps(details) if isinstance(details, dict) else details,
            "CUI",
        ),
    )
    conn.commit()


def _load_maintenance_config():
    """Load maintenance configuration from args/maintenance_config.yaml.

    Uses simple line-based YAML parsing (stdlib only — no PyYAML dependency).
    Returns dict with remediation settings.
    """
    defaults = {
        "auto_remediate": True,
        "auto_remediate_max_severity": "medium",
        "require_tests_pass": True,
        "require_lint_pass": True,
        "branch_prefix": "remediation/",
        "commit_prefix": "[MAINT]",
        "max_concurrent_remediations": 5,
    }

    if not CONFIG_PATH.exists():
        return defaults

    try:
        content = CONFIG_PATH.read_text(encoding="utf-8")
        in_remediation = False
        config = {}
        for line in content.splitlines():
            stripped = line.strip()

            # Skip comments and blank lines
            if not stripped or stripped.startswith("#"):
                continue

            # Detect the remediation: section
            if stripped == "remediation:":
                in_remediation = True
                continue

            # Detect start of a different top-level section
            if in_remediation and not line.startswith(" ") and not line.startswith("\t"):
                in_remediation = False
                continue

            if in_remediation and ":" in stripped:
                parts = stripped.split(":", 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().strip('"').strip("'")
                    # Type coercion
                    if val.lower() == "true":
                        config[key] = True
                    elif val.lower() == "false":
                        config[key] = False
                    else:
                        try:
                            config[key] = int(val)
                        except ValueError:
                            config[key] = val

        merged = {**defaults, **config}
        return merged
    except Exception:
        return defaults


# ---------------------------------------------------------------------------
# Prioritization
# ---------------------------------------------------------------------------

def _prioritize_remediations(conn, project_id):
    """Get vulnerabilities sorted by remediation priority.

    Priority order:
    1. Critical severity, overdue SLA (most urgent)
    2. Critical severity, within SLA
    3. High severity, overdue SLA
    4. High severity, within SLA
    5. Medium severity, overdue
    6. Medium severity, within SLA
    7. Low severity

    Returns list of dicts from dependency_vulnerabilities + dependency_inventory join.
    """
    rows = conn.execute("""
        SELECT dv.*, di.package_name, di.current_version, di.language,
               di.dependency_file, di.purl
        FROM dependency_vulnerabilities dv
        JOIN dependency_inventory di ON dv.dependency_id = di.id
        WHERE dv.project_id = ? AND dv.status = 'open' AND dv.fix_available = 1
        ORDER BY
            CASE dv.severity
                WHEN 'critical' THEN 1 WHEN 'high' THEN 2
                WHEN 'medium' THEN 3 WHEN 'low' THEN 4 ELSE 5
            END,
            CASE WHEN dv.sla_deadline < datetime('now') THEN 0 ELSE 1 END,
            dv.sla_deadline ASC
    """, (project_id,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Per-Language Dependency File Updaters
# ---------------------------------------------------------------------------

def _update_requirements_txt(file_path, package, from_ver, to_ver):
    """Update version in requirements.txt.

    Handles: package==1.0.0, package>=1.0.0, package~=1.0.0
    """
    content = Path(file_path).read_text(encoding="utf-8")
    patterns = [
        (rf'^({re.escape(package)})\s*==\s*{re.escape(from_ver)}',
         rf'\1=={to_ver}'),
        (rf'^({re.escape(package)})\s*>=\s*{re.escape(from_ver)}',
         rf'\1>={to_ver}'),
        (rf'^({re.escape(package)})\s*~=\s*{re.escape(from_ver)}',
         rf'\1~={to_ver}'),
    ]
    updated = False
    for pattern, replacement in patterns:
        new_content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
        if new_content != content:
            content = new_content
            updated = True
            break
    if updated:
        Path(file_path).write_text(content, encoding="utf-8")
    return updated


def _update_package_json(file_path, package, from_ver, to_ver):
    """Update version in package.json (dependencies or devDependencies)."""
    content = json.loads(Path(file_path).read_text(encoding="utf-8"))
    updated = False
    for section in ["dependencies", "devDependencies"]:
        if section in content and package in content[section]:
            old_val = content[section][package]
            # Preserve prefix (^, ~, >=, >)
            prefix = ""
            for p in ["^", "~", ">=", ">"]:
                if old_val.startswith(p):
                    prefix = p
                    break
            content[section][package] = f"{prefix}{to_ver}"
            updated = True
    if updated:
        Path(file_path).write_text(
            json.dumps(content, indent=2) + "\n", encoding="utf-8"
        )
    return updated


def _update_pom_xml(file_path, package, from_ver, to_ver):
    """Update version in pom.xml. Package format: groupId:artifactId."""
    content = Path(file_path).read_text(encoding="utf-8")
    group, artifact = package.split(":", 1) if ":" in package else ("", package)
    pattern = (
        rf'(<groupId>{re.escape(group)}</groupId>\s*'
        rf'<artifactId>{re.escape(artifact)}</artifactId>\s*'
        rf'<version>){re.escape(from_ver)}(</version>)'
    )
    new_content = re.sub(pattern, rf'\g<1>{to_ver}\g<2>', content, flags=re.DOTALL)
    if new_content != content:
        Path(file_path).write_text(new_content, encoding="utf-8")
        return True
    return False


def _update_go_mod(file_path, module, from_ver, to_ver):
    """Update version in go.mod."""
    content = Path(file_path).read_text(encoding="utf-8")
    pattern = rf'({re.escape(module)})\s+{re.escape(from_ver)}'
    new_content = re.sub(pattern, rf'\1 {to_ver}', content)
    if new_content != content:
        Path(file_path).write_text(new_content, encoding="utf-8")
        return True
    return False


def _update_cargo_toml(file_path, crate, from_ver, to_ver):
    """Update version in Cargo.toml."""
    content = Path(file_path).read_text(encoding="utf-8")
    patterns = [
        (rf'^({re.escape(crate)}\s*=\s*"){re.escape(from_ver)}(")',
         rf'\g<1>{to_ver}\g<2>'),
        (rf'({re.escape(crate)}\s*=\s*\{{[^}}]*version\s*=\s*"){re.escape(from_ver)}(")',
         rf'\g<1>{to_ver}\g<2>'),
    ]
    updated = False
    for pattern, replacement in patterns:
        new_content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
        if new_content != content:
            content = new_content
            updated = True
            break
    if updated:
        Path(file_path).write_text(content, encoding="utf-8")
    return updated


def _update_csproj(file_path, package, from_ver, to_ver):
    """Update PackageReference version in .csproj."""
    content = Path(file_path).read_text(encoding="utf-8")
    pattern = (
        rf'(<PackageReference\s+Include="{re.escape(package)}"\s+'
        rf'Version="){re.escape(from_ver)}(")'
    )
    new_content = re.sub(pattern, rf'\g<1>{to_ver}\g<2>', content)
    if new_content != content:
        Path(file_path).write_text(new_content, encoding="utf-8")
        return True
    return False


# Map language to its dependency file updater
UPDATERS = {
    "python": _update_requirements_txt,
    "javascript": _update_package_json,
    "typescript": _update_package_json,
    "java": _update_pom_xml,
    "go": _update_go_mod,
    "rust": _update_cargo_toml,
    "csharp": _update_csproj,
}


# ---------------------------------------------------------------------------
# Git Operations
# ---------------------------------------------------------------------------

def _is_git_repo(project_dir):
    """Check whether project_dir is inside a git repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _create_remediation_branch(project_dir, branch_name):
    """Create a git branch for the remediation.

    Returns True if branch created successfully.
    """
    try:
        result = subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def _commit_remediation(project_dir, files_changed, message):
    """Stage changed files and commit.

    Returns commit hash or None.
    """
    try:
        for f in files_changed:
            subprocess.run(
                ["git", "add", f],
                cwd=str(project_dir),
                capture_output=True,
                timeout=30,
            )
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            hash_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                timeout=10,
            )
            return hash_result.stdout.strip() if hash_result.returncode == 0 else None
        return None
    except Exception:
        return None


def _checkout_original_branch(project_dir, branch_name):
    """Switch back to the branch we were on before creating the remediation branch."""
    try:
        subprocess.run(
            ["git", "checkout", branch_name],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        pass


def _get_current_branch(project_dir):
    """Return the name of the current git branch, or None."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _run_verification_tests(project_dir, language):
    """Run basic verification after dependency update.

    Returns dict with: success, output
    """
    commands = {
        "python": ["python", "-m", "pytest", "--tb=short", "-q"],
        "javascript": ["npm", "test"],
        "typescript": ["npm", "test"],
        "java": ["mvn", "test", "-q"],
        "go": ["go", "test", "./..."],
        "rust": ["cargo", "test"],
        "csharp": ["dotnet", "test", "--no-build"],
    }
    cmd = commands.get(language)
    if not cmd:
        return {"success": True, "output": "No test command for language"}
    try:
        result = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=300,
        )
        return {
            "success": result.returncode == 0,
            "output": (result.stdout + result.stderr)[-2000:],  # cap output
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "Test run timed out after 300 seconds"}
    except FileNotFoundError as e:
        return {"success": False, "output": f"Test command not found: {e}"}
    except Exception as e:
        return {"success": False, "output": str(e)}


# ---------------------------------------------------------------------------
# Auto-Remediation Severity Gating
# ---------------------------------------------------------------------------

def _severity_allowed_for_auto(severity, max_severity):
    """Check if a vulnerability severity is within auto-remediation threshold.

    Args:
        severity: The vulnerability severity (critical, high, medium, low).
        max_severity: The maximum severity that can be auto-remediated.

    Returns:
        True if the severity is at or below the max threshold.
    """
    sev_rank = SEVERITY_RANK.get(severity.lower(), 0)
    max_rank = SEVERITY_RANK.get(max_severity.lower(), 2)
    return sev_rank <= max_rank


# ---------------------------------------------------------------------------
# Record Remediation Action
# ---------------------------------------------------------------------------

def _record_remediation_action(
    conn, project_id, vuln, action_type, from_ver, to_ver,
    dep_file, branch, commit_hash, status, test_results=None,
):
    """Insert a row into remediation_actions table."""
    conn.execute(
        """INSERT INTO remediation_actions
           (project_id, vulnerability_id, dependency_id, action_type,
            from_version, to_version, dependency_file, git_branch,
            git_commit, status, applied_at, test_results, classification)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            project_id,
            vuln.get("id"),
            vuln.get("dependency_id"),
            action_type,
            from_ver,
            to_ver,
            dep_file,
            branch,
            commit_hash,
            status,
            datetime.utcnow().isoformat() + "Z" if status in ("applied", "tested") else None,
            json.dumps(test_results) if test_results else None,
            "CUI",
        ),
    )
    conn.commit()


def _update_vulnerability_status(conn, vuln_id, new_status, action_desc=None):
    """Update the status of a dependency_vulnerability row."""
    now = datetime.utcnow().isoformat() + "Z"
    conn.execute(
        """UPDATE dependency_vulnerabilities
           SET status = ?, remediated_at = ?, remediation_action = ?,
               updated_at = ?
           WHERE id = ?""",
        (new_status, now, action_desc, now, vuln_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Count Active Remediations
# ---------------------------------------------------------------------------

def _count_active_remediations(conn, project_id):
    """Return the number of remediation branches currently in-flight."""
    row = conn.execute(
        """SELECT COUNT(*) AS cnt FROM remediation_actions
           WHERE project_id = ? AND status IN ('pending', 'applied')""",
        (project_id,),
    ).fetchone()
    return row["cnt"] if row else 0


# ---------------------------------------------------------------------------
# Main Remediation Logic
# ---------------------------------------------------------------------------

def remediate(
    project_id,
    vulnerability_id=None,
    auto=False,
    dry_run=False,
    project_dir=None,
    db_path=None,
):
    """Run remediation for vulnerable dependencies.

    Args:
        project_id: Project ID.
        vulnerability_id: Specific vulnerability to fix (None = all with fixes available).
        auto: Auto-apply without confirmation (respects auto_remediate_max_severity).
        dry_run: Preview changes without applying.
        project_dir: Override project directory.
        db_path: Override DB path.

    Steps:
    1. Connect to DB, load project, load config.
    2. Get prioritized list of remediable vulnerabilities.
    3. Filter by vulnerability_id if specified.
    4. For each vulnerability:
       a. Determine if auto-remediation is allowed (severity check).
       b. Find the dependency file to update.
       c. If dry_run: report what would change.
       d. If not dry_run:
          - Create remediation branch
          - Update dependency file
          - Run verification tests
          - Commit changes
          - Record in remediation_actions table
          - Update vulnerability status
       e. Log audit event
    5. Return summary.

    Returns:
        dict with: project_id, total_remediated, total_skipped, total_failed,
                    actions (list of action details), dry_run flag
    """
    conn = _get_connection(db_path)
    config = _load_maintenance_config()

    try:
        project = _get_project(conn, project_id)
    except ValueError as e:
        conn.close()
        return {"error": str(e)}

    # Resolve project directory
    proj_dir = Path(project_dir) if project_dir else None
    if proj_dir is None:
        # Try project record for a path hint
        proj_path = project.get("project_path") or project.get("path")
        if proj_path:
            proj_dir = Path(proj_path)
        else:
            proj_dir = BASE_DIR  # fallback

    # Get prioritized vulnerabilities
    vulns = _prioritize_remediations(conn, project_id)

    # Filter to specific vulnerability if requested
    if vulnerability_id is not None:
        vulns = [v for v in vulns if v["id"] == vulnerability_id]
        if not vulns:
            conn.close()
            return {
                "error": f"Vulnerability {vulnerability_id} not found, not open, "
                         "or has no fix available",
            }

    # Check concurrency limit
    max_concurrent = config.get("max_concurrent_remediations", 5)
    active_count = _count_active_remediations(conn, project_id)
    remaining_slots = max(0, max_concurrent - active_count)

    max_severity = config.get("auto_remediate_max_severity", "medium")
    branch_prefix = config.get("branch_prefix", "remediation/")
    commit_prefix = config.get("commit_prefix", "[MAINT]")
    require_tests = config.get("require_tests_pass", True)

    # Determine git capability
    use_git = proj_dir.exists() and _is_git_repo(proj_dir)
    original_branch = _get_current_branch(proj_dir) if use_git else None

    summary = {
        "project_id": project_id,
        "project_name": project.get("name", ""),
        "dry_run": dry_run,
        "total_vulnerabilities": len(vulns),
        "total_remediated": 0,
        "total_skipped": 0,
        "total_failed": 0,
        "actions": [],
    }

    for vuln in vulns:
        action_detail = {
            "vulnerability_id": vuln["id"],
            "cve_id": vuln.get("cve_id"),
            "package_name": vuln["package_name"],
            "language": vuln["language"],
            "severity": vuln["severity"],
            "current_version": vuln["current_version"],
            "fix_version": vuln.get("fix_version", ""),
            "dependency_file": vuln.get("dependency_file", ""),
            "status": "pending",
            "reason": "",
        }

        fix_version = vuln.get("fix_version")
        if not fix_version:
            action_detail["status"] = "skipped"
            action_detail["reason"] = "No fix version specified"
            summary["total_skipped"] += 1
            summary["actions"].append(action_detail)
            continue

        # Check auto-remediation severity gate
        if auto and not _severity_allowed_for_auto(vuln["severity"], max_severity):
            action_detail["status"] = "skipped"
            action_detail["reason"] = (
                f"Severity '{vuln['severity']}' exceeds auto-remediation threshold "
                f"'{max_severity}' — requires manual approval"
            )
            summary["total_skipped"] += 1
            summary["actions"].append(action_detail)
            continue

        # Check concurrency slots
        if not dry_run and remaining_slots <= 0:
            action_detail["status"] = "skipped"
            action_detail["reason"] = (
                f"Concurrent remediation limit reached ({max_concurrent})"
            )
            summary["total_skipped"] += 1
            summary["actions"].append(action_detail)
            continue

        # Find updater for this language
        language = vuln["language"].lower()
        updater = UPDATERS.get(language)
        if not updater:
            action_detail["status"] = "skipped"
            action_detail["reason"] = f"No updater for language '{language}'"
            summary["total_skipped"] += 1
            summary["actions"].append(action_detail)
            continue

        # Resolve dependency file path
        dep_file = vuln.get("dependency_file", "")
        if dep_file:
            dep_file_path = proj_dir / dep_file
        else:
            # Guess default dependency file
            default_files = {
                "python": "requirements.txt",
                "javascript": "package.json",
                "typescript": "package.json",
                "java": "pom.xml",
                "go": "go.mod",
                "rust": "Cargo.toml",
                "csharp": "*.csproj",
            }
            guess = default_files.get(language, "")
            if guess.startswith("*"):
                # Glob for .csproj files
                matches = list(proj_dir.glob(guess))
                dep_file_path = matches[0] if matches else proj_dir / guess
            else:
                dep_file_path = proj_dir / guess

        if not dep_file_path.exists():
            action_detail["status"] = "skipped"
            action_detail["reason"] = f"Dependency file not found: {dep_file_path}"
            summary["total_skipped"] += 1
            summary["actions"].append(action_detail)
            continue

        # --- Dry run: report only ---
        if dry_run:
            action_detail["status"] = "would_apply"
            action_detail["reason"] = (
                f"Would update {vuln['package_name']} "
                f"from {vuln['current_version']} to {fix_version} "
                f"in {dep_file_path.name}"
            )
            action_detail["branch"] = (
                f"{branch_prefix}{vuln['package_name']}-{fix_version}"
            )
            summary["total_remediated"] += 1
            summary["actions"].append(action_detail)
            continue

        # --- Live remediation ---
        branch_name = (
            f"{branch_prefix}{vuln['package_name']}-{fix_version}"
        ).replace(" ", "-").replace("/", "-")
        # Avoid double-slash from prefix ending with /
        branch_name = re.sub(r'/+', '/', branch_name)

        # Create git branch (if git available)
        branch_created = False
        if use_git:
            branch_created = _create_remediation_branch(proj_dir, branch_name)
            if not branch_created:
                action_detail["status"] = "failed"
                action_detail["reason"] = (
                    f"Failed to create git branch '{branch_name}'"
                )
                summary["total_failed"] += 1
                summary["actions"].append(action_detail)
                continue

        # Update dependency file
        file_updated = updater(
            str(dep_file_path),
            vuln["package_name"],
            vuln["current_version"],
            fix_version,
        )

        if not file_updated:
            action_detail["status"] = "failed"
            action_detail["reason"] = (
                f"Failed to update {vuln['package_name']} "
                f"from {vuln['current_version']} to {fix_version} "
                f"in {dep_file_path.name} — version string not matched"
            )
            summary["total_failed"] += 1
            # Switch back to original branch
            if use_git and branch_created and original_branch:
                _checkout_original_branch(proj_dir, original_branch)
            summary["actions"].append(action_detail)
            continue

        # Run verification tests
        test_results = None
        tests_passed = True
        if require_tests:
            test_results = _run_verification_tests(proj_dir, language)
            tests_passed = test_results.get("success", False)

        # Commit changes
        commit_hash = None
        commit_message = (
            f"{commit_prefix} Bump {vuln['package_name']} "
            f"from {vuln['current_version']} to {fix_version}\n\n"
            f"Fixes: {vuln.get('cve_id', 'N/A')}\n"
            f"Severity: {vuln['severity']}\n"
            f"Remediation engine auto-applied."
        )
        if use_git and branch_created:
            commit_hash = _commit_remediation(
                proj_dir,
                [str(dep_file_path)],
                commit_message,
            )

        # Determine final status
        if tests_passed:
            final_status = "tested" if test_results else "applied"
        else:
            final_status = "applied"  # applied but tests did not pass

        # Record in DB
        _record_remediation_action(
            conn,
            project_id,
            vuln,
            "version_bump",
            vuln["current_version"],
            fix_version,
            str(dep_file_path.relative_to(proj_dir)) if dep_file_path.is_relative_to(proj_dir) else str(dep_file_path),
            branch_name if use_git else None,
            commit_hash,
            final_status,
            test_results,
        )

        # Update vulnerability status
        if tests_passed:
            _update_vulnerability_status(
                conn,
                vuln["id"],
                "remediated",
                f"version_bump to {fix_version}",
            )
        else:
            _update_vulnerability_status(
                conn,
                vuln["id"],
                "in_progress",
                f"version_bump to {fix_version} — tests failing",
            )

        # Log audit event
        _log_audit_event(conn, project_id, (
            f"Remediated {vuln['package_name']} "
            f"{vuln['current_version']} -> {fix_version} "
            f"({vuln['severity']} | {vuln.get('cve_id', 'N/A')})"
        ), {
            "vulnerability_id": vuln["id"],
            "cve_id": vuln.get("cve_id"),
            "package": vuln["package_name"],
            "from_version": vuln["current_version"],
            "to_version": fix_version,
            "branch": branch_name if use_git else None,
            "commit": commit_hash,
            "tests_passed": tests_passed,
            "status": final_status,
        })

        # Switch back to original branch for next iteration
        if use_git and branch_created and original_branch:
            _checkout_original_branch(proj_dir, original_branch)

        remaining_slots -= 1

        # Populate action detail
        action_detail["status"] = final_status
        action_detail["fix_version"] = fix_version
        action_detail["branch"] = branch_name if use_git else None
        action_detail["commit"] = commit_hash
        action_detail["tests_passed"] = tests_passed
        action_detail["reason"] = (
            f"Updated {vuln['package_name']} to {fix_version}"
            + ("" if tests_passed else " — tests failed, manual review needed")
        )
        summary["total_remediated"] += 1
        summary["actions"].append(action_detail)

    conn.close()
    return summary


# ---------------------------------------------------------------------------
# CLI Output Formatting
# ---------------------------------------------------------------------------

def _print_summary(result):
    """Print human-readable remediation summary."""
    if "error" in result:
        print(f"ERROR: {result['error']}")
        return

    dry_tag = " [DRY RUN]" if result.get("dry_run") else ""
    print(f"\n{'=' * 60}")
    print(f"  ICDEV REMEDIATION ENGINE{dry_tag}")
    print(f"{'=' * 60}")
    print(f"  Project: {result.get('project_name', result['project_id'])}")
    print(f"  Vulnerabilities found: {result['total_vulnerabilities']}")
    print(f"  Remediated: {result['total_remediated']}")
    print(f"  Skipped:    {result['total_skipped']}")
    print(f"  Failed:     {result['total_failed']}")
    print()

    for action in result.get("actions", []):
        sev = action.get("severity", "?").upper()
        pkg = action.get("package_name", "?")
        status = action.get("status", "?")
        reason = action.get("reason", "")
        cve = action.get("cve_id", "")

        # Status indicator
        if status in ("tested", "applied", "would_apply"):
            indicator = "+"
        elif status == "skipped":
            indicator = "-"
        else:
            indicator = "!"

        cve_str = f" ({cve})" if cve else ""
        print(f"  [{indicator}] [{sev}] {pkg}{cve_str}")
        print(f"      {reason}")
        if action.get("branch"):
            print(f"      Branch: {action['branch']}")
        if action.get("commit"):
            print(f"      Commit: {action['commit'][:12]}")
        print()

    print(f"{'=' * 60}")


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Auto-remediate vulnerable dependencies"
    )
    parser.add_argument(
        "--project-id", required=True,
        help="Project ID to remediate",
    )
    parser.add_argument(
        "--vulnerability-id", type=int, default=None,
        help="Specific vulnerability ID to fix",
    )
    parser.add_argument(
        "--auto", action="store_true",
        help="Auto-apply (respects max severity config)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview changes without applying",
    )
    parser.add_argument(
        "--project-dir",
        help="Override project directory",
    )
    parser.add_argument(
        "--db-path",
        help="Override database path",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output as JSON",
    )
    args = parser.parse_args()

    result = remediate(
        project_id=args.project_id,
        vulnerability_id=args.vulnerability_id,
        auto=args.auto,
        dry_run=args.dry_run,
        project_dir=args.project_dir,
        db_path=args.db_path,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_summary(result)

    # Exit with non-zero if there were failures
    if result.get("error") or result.get("total_failed", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
