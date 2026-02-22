#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Staging Manager -- isolated staging environments for capability testing.

ADR D211: Staging uses git worktrees (D32) for isolation. Reuses existing
infrastructure, zero new dependencies.

REQ-36-030: Create isolated staging environments using git worktrees for
testing new capabilities before propagation.

REQ-36-031: Capabilities in staging must pass the complete test pipeline:
syntax validation, linting, unit tests, BDD tests, security scanning,
compliance gates, and acceptance validation.

REQ-36-032: No capability shall be propagated if it would weaken any child's
existing compliance posture. The staging environment verifies compliance scores
before and after capability integration.

Architecture:
    - Reuses tools/ci/modules/worktree.py for git worktree creation/cleanup
    - Staging environments tracked in staging_environments table
    - Each staging env has an expiry (default 72 hours per D212)
    - Test execution delegates to existing test_orchestrator.py or pytest
    - Compliance preservation compares scores before/after
    - All operations append-only audited (D6)

Usage:
    python tools/registry/staging_manager.py --create \
        --capability-id "cap-abc123" --genome-version "1.2.0" --json

    python tools/registry/staging_manager.py --test --staging-id "stg-abc123" --json

    python tools/registry/staging_manager.py --check-compliance \
        --staging-id "stg-abc123" --json

    python tools/registry/staging_manager.py --destroy --staging-id "stg-abc123" --json

    python tools/registry/staging_manager.py --list --json
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
STAGING_DIR = BASE_DIR / "trees" / "staging"

# Default staging expiry in hours (D212: 72-hour stability window)
DEFAULT_EXPIRY_HOURS = 72

# =========================================================================
# GRACEFUL IMPORTS
# =========================================================================
try:
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def audit_log_event(**kwargs):
        return -1


# =========================================================================
# CONSTANTS
# =========================================================================
STAGING_ENVIRONMENTS_DDL = """
CREATE TABLE IF NOT EXISTS staging_environments (
    id TEXT PRIMARY KEY,
    capability_id TEXT NOT NULL,
    genome_version TEXT,
    worktree_path TEXT,
    branch_name TEXT,
    status TEXT NOT NULL DEFAULT 'created'
        CHECK(status IN ('created', 'testing', 'passed', 'failed', 'expired', 'destroyed')),
    test_results_json TEXT,
    compliance_before_json TEXT,
    compliance_after_json TEXT,
    compliance_preserved INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    destroyed_at TIMESTAMP
);
"""


# =========================================================================
# HELPERS
# =========================================================================
def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix="stg"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _audit(event_type, action, details=None):
    """Write audit trail entry (append-only, D6)."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor="staging-manager",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="icdev-genome",
            )
        except Exception:
            pass


def _run_git(args: list, cwd: str = None) -> subprocess.CompletedProcess:
    """Run a git command safely."""
    cmd = ["git"] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd or str(BASE_DIR),
        timeout=120,
        stdin=subprocess.DEVNULL,
    )


def _run_subprocess(cmd: list, cwd: str = None, timeout: int = 300) -> dict:
    """Run a subprocess and return structured result."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd or str(BASE_DIR),
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[:10000] if result.stdout else "",
            "stderr": result.stderr[:5000] if result.stderr else "",
            "success": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": "Command timed out",
            "success": False,
        }
    except Exception as e:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": str(e),
            "success": False,
        }


# =========================================================================
# STAGING MANAGER
# =========================================================================
class StagingManager:
    """Manage isolated staging environments for capability testing (D211).

    Creates git worktrees for each capability under test, runs the test
    pipeline, checks compliance preservation, and cleans up when done.
    """

    def __init__(self, db_path=None):
        """Initialize StagingManager.

        Args:
            db_path: Path to SQLite database. Defaults to data/icdev.db.
        """
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._ensure_tables()

    def _get_conn(self):
        """Get a database connection with row factory."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_tables(self):
        """Create staging_environments table if it does not exist."""
        try:
            conn = self._get_conn()
            conn.executescript(STAGING_ENVIRONMENTS_DDL)
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Warning: Table creation failed: {e}", file=sys.stderr)

    def create_staging(
        self, capability_id: str, genome_version: str = None
    ) -> Optional[dict]:
        """Create an isolated staging environment using a git worktree.

        Creates a new git worktree under trees/staging/<staging_id> with a
        dedicated branch for testing the capability.

        Args:
            capability_id: ID of the capability to stage.
            genome_version: Genome version context (informational).

        Returns:
            Dict with staging environment details, or None on failure.
        """
        staging_id = _generate_id("stg")
        branch_name = f"staging-{staging_id}"
        worktree_path = STAGING_DIR / staging_id

        # Ensure staging directory parent exists
        STAGING_DIR.mkdir(parents=True, exist_ok=True)

        # Create git worktree with new branch
        result = _run_git([
            "worktree", "add", "--no-checkout",
            str(worktree_path), "-b", branch_name,
        ])

        if result.returncode != 0:
            # Branch might exist; try without -b
            result = _run_git([
                "worktree", "add", "--no-checkout", str(worktree_path),
            ])
            if result.returncode != 0:
                error_msg = f"Failed to create worktree: {result.stderr.strip()}"
                _audit("staging.create.failed", error_msg,
                       {"capability_id": capability_id, "error": error_msg})
                return {"error": error_msg}

        # Checkout all files in staging worktree
        checkout_result = _run_git(["checkout"], cwd=str(worktree_path))
        if checkout_result.returncode != 0:
            # Attempt cleanup on failure
            _run_git(["worktree", "remove", str(worktree_path), "--force"])
            error_msg = f"Failed to checkout: {checkout_result.stderr.strip()}"
            return {"error": error_msg}

        # Write classification marker
        class_file = worktree_path / ".classification"
        try:
            class_file.write_text("CUI // SP-CTI\n")
        except Exception:
            pass

        # Calculate expiry
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=DEFAULT_EXPIRY_HOURS)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Store in database
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO staging_environments
                   (id, capability_id, genome_version, worktree_path,
                    branch_name, status, created_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, 'created', ?, ?)""",
                (
                    staging_id,
                    capability_id,
                    genome_version,
                    str(worktree_path),
                    branch_name,
                    _now(),
                    expires_at,
                ),
            )
            conn.commit()
        except Exception as e:
            print(f"Warning: DB insert failed: {e}", file=sys.stderr)
        finally:
            conn.close()

        result_dict = {
            "staging_id": staging_id,
            "capability_id": capability_id,
            "genome_version": genome_version,
            "worktree_path": str(worktree_path),
            "branch_name": branch_name,
            "status": "created",
            "created_at": _now(),
            "expires_at": expires_at,
        }

        _audit(
            "staging.created",
            f"Staging environment {staging_id} created for capability {capability_id}",
            result_dict,
        )

        return result_dict

    def run_tests(self, staging_id: str) -> dict:
        """Run the test pipeline in the staging environment.

        Executes pytest in the worktree directory and captures results.
        Updates staging status to 'testing' then 'passed' or 'failed'.

        Args:
            staging_id: ID of the staging environment.

        Returns:
            Dict with test execution results.
        """
        env_record = self._get_record(staging_id)
        if not env_record:
            return {"error": f"Staging environment {staging_id} not found"}

        worktree_path = env_record.get("worktree_path", "")
        if not Path(worktree_path).exists():
            return {"error": f"Worktree path does not exist: {worktree_path}"}

        # Update status to testing
        self._update_status(staging_id, "testing")

        # Run pytest in the staging worktree
        test_result = _run_subprocess(
            [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short", "-q"],
            cwd=worktree_path,
            timeout=300,
        )

        # Determine pass/fail
        passed = test_result.get("success", False)
        new_status = "passed" if passed else "failed"

        # Store results
        test_results = {
            "staging_id": staging_id,
            "passed": passed,
            "returncode": test_result.get("returncode"),
            "stdout_preview": test_result.get("stdout", "")[:2000],
            "stderr_preview": test_result.get("stderr", "")[:1000],
            "tested_at": _now(),
        }

        # Update DB
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE staging_environments
                   SET status = ?, test_results_json = ?
                   WHERE id = ?""",
                (new_status, json.dumps(test_results), staging_id),
            )
            conn.commit()
        except Exception as e:
            print(f"Warning: DB update failed: {e}", file=sys.stderr)
        finally:
            conn.close()

        _audit(
            "staging.tested",
            f"Staging {staging_id} tests {'passed' if passed else 'failed'}",
            {"staging_id": staging_id, "passed": passed},
        )

        return test_results

    def check_compliance_preservation(self, staging_id: str) -> dict:
        """Verify that compliance posture is not degraded in staging.

        Compares compliance state before and after capability integration.
        Uses a simplified check: counts compliance artifacts and STIG findings.

        Args:
            staging_id: ID of the staging environment.

        Returns:
            Dict with compliance comparison results.
        """
        env_record = self._get_record(staging_id)
        if not env_record:
            return {"error": f"Staging environment {staging_id} not found"}

        worktree_path = env_record.get("worktree_path", "")
        if not Path(worktree_path).exists():
            return {"error": f"Worktree path does not exist: {worktree_path}"}

        # Collect compliance state from main repo (before)
        compliance_before = self._collect_compliance_state(str(BASE_DIR))

        # Collect compliance state from staging worktree (after)
        compliance_after = self._collect_compliance_state(worktree_path)

        # Compare: compliance must not degrade
        preserved = True
        issues = []

        # Check: no new security findings introduced
        before_findings = compliance_before.get("security_findings", 0)
        after_findings = compliance_after.get("security_findings", 0)
        if after_findings > before_findings:
            preserved = False
            issues.append(
                f"Security findings increased: {before_findings} -> {after_findings}"
            )

        # Check: test count should not decrease
        before_tests = compliance_before.get("test_count", 0)
        after_tests = compliance_after.get("test_count", 0)
        if after_tests < before_tests:
            issues.append(
                f"Test count decreased: {before_tests} -> {after_tests} (warning)"
            )

        # Check: CUI markings present
        if not compliance_after.get("cui_markings_present", True):
            preserved = False
            issues.append("CUI markings missing in staging environment")

        result = {
            "staging_id": staging_id,
            "compliance_preserved": preserved,
            "compliance_before": compliance_before,
            "compliance_after": compliance_after,
            "issues": issues,
            "checked_at": _now(),
        }

        # Update DB
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE staging_environments
                   SET compliance_before_json = ?,
                       compliance_after_json = ?,
                       compliance_preserved = ?
                   WHERE id = ?""",
                (
                    json.dumps(compliance_before),
                    json.dumps(compliance_after),
                    1 if preserved else 0,
                    staging_id,
                ),
            )
            conn.commit()
        except Exception as e:
            print(f"Warning: DB update failed: {e}", file=sys.stderr)
        finally:
            conn.close()

        _audit(
            "staging.compliance_check",
            f"Compliance {'preserved' if preserved else 'DEGRADED'} in {staging_id}",
            {"staging_id": staging_id, "preserved": preserved, "issues": issues},
        )

        return result

    def _collect_compliance_state(self, directory: str) -> dict:
        """Collect basic compliance state indicators from a directory.

        Args:
            directory: Path to the project directory.

        Returns:
            Dict with compliance state indicators.
        """
        dir_path = Path(directory)
        state = {
            "directory": directory,
            "collected_at": _now(),
            "security_findings": 0,
            "test_count": 0,
            "cui_markings_present": False,
        }

        # Count test files
        tests_dir = dir_path / "tests"
        if tests_dir.exists():
            test_files = list(tests_dir.glob("test_*.py"))
            state["test_count"] = len(test_files)

        # Check for CUI markings in key files
        for check_file in ["CLAUDE.md", "tools/__init__.py", "tools/registry/__init__.py"]:
            fp = dir_path / check_file
            if fp.exists():
                try:
                    content = fp.read_text(encoding="utf-8", errors="ignore")[:500]
                    if "CUI" in content:
                        state["cui_markings_present"] = True
                        break
                except Exception:
                    pass

        # Run bandit (SAST) if available -- count findings
        bandit_result = _run_subprocess(
            [sys.executable, "-m", "bandit", "-r", str(dir_path / "tools"),
             "-f", "json", "-q", "--severity-level", "high"],
            cwd=directory,
            timeout=120,
        )
        if bandit_result.get("success") or bandit_result.get("stdout"):
            try:
                bandit_output = json.loads(bandit_result.get("stdout", "{}"))
                findings = bandit_output.get("results", [])
                state["security_findings"] = len(findings)
            except (json.JSONDecodeError, TypeError):
                pass

        return state

    def destroy_staging(self, staging_id: str) -> bool:
        """Clean up a staging environment and its git worktree.

        Args:
            staging_id: ID of the staging environment to destroy.

        Returns:
            True if cleanup succeeded, False otherwise.
        """
        env_record = self._get_record(staging_id)
        if not env_record:
            return False

        worktree_path = env_record.get("worktree_path", "")

        # Remove git worktree
        success = True
        if worktree_path and Path(worktree_path).exists():
            result = _run_git(["worktree", "remove", worktree_path, "--force"])
            if result.returncode != 0:
                print(f"Warning: worktree remove failed: {result.stderr}",
                      file=sys.stderr)
                success = False

        # Update DB status
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE staging_environments
                   SET status = 'destroyed', destroyed_at = ?
                   WHERE id = ?""",
                (_now(), staging_id),
            )
            conn.commit()
        except Exception as e:
            print(f"Warning: DB update failed: {e}", file=sys.stderr)
            success = False
        finally:
            conn.close()

        _audit(
            "staging.destroyed",
            f"Staging environment {staging_id} destroyed",
            {"staging_id": staging_id, "success": success},
        )

        return success

    def list_staging(self) -> list:
        """List all staging environments.

        Returns:
            List of staging environment dicts.
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT id, capability_id, genome_version, worktree_path,
                          branch_name, status, compliance_preserved,
                          created_at, expires_at, destroyed_at
                   FROM staging_environments
                   ORDER BY created_at DESC"""
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def _get_record(self, staging_id: str) -> Optional[dict]:
        """Get a staging environment record by ID."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM staging_environments WHERE id = ?", (staging_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _update_status(self, staging_id: str, status: str):
        """Update staging environment status."""
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE staging_environments SET status = ? WHERE id = ?",
                (status, staging_id),
            )
            conn.commit()
        except Exception as e:
            print(f"Warning: status update failed: {e}", file=sys.stderr)
        finally:
            conn.close()


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Staging Manager -- isolated capability testing environments (D211)"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--db-path", type=Path, default=None, help="Database path override"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", action="store_true",
                       help="Create a staging environment")
    group.add_argument("--test", action="store_true",
                       help="Run tests in staging environment")
    group.add_argument("--check-compliance", action="store_true",
                       help="Check compliance preservation")
    group.add_argument("--destroy", action="store_true",
                       help="Destroy a staging environment")
    group.add_argument("--list", action="store_true",
                       help="List all staging environments")

    parser.add_argument("--capability-id", help="Capability ID (for --create)")
    parser.add_argument("--genome-version", help="Genome version (for --create)")
    parser.add_argument("--staging-id", help="Staging environment ID")

    args = parser.parse_args()

    try:
        manager = StagingManager(db_path=args.db_path)

        if args.create:
            if not args.capability_id:
                parser.error("--create requires --capability-id")
            result = manager.create_staging(
                capability_id=args.capability_id,
                genome_version=args.genome_version,
            )

        elif args.test:
            if not args.staging_id:
                parser.error("--test requires --staging-id")
            result = manager.run_tests(staging_id=args.staging_id)

        elif args.check_compliance:
            if not args.staging_id:
                parser.error("--check-compliance requires --staging-id")
            result = manager.check_compliance_preservation(
                staging_id=args.staging_id
            )

        elif args.destroy:
            if not args.staging_id:
                parser.error("--destroy requires --staging-id")
            success = manager.destroy_staging(staging_id=args.staging_id)
            result = {"staging_id": args.staging_id, "destroyed": success}

        elif args.list:
            result = manager.list_staging()

        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            # Human-readable output
            if isinstance(result, list):
                print("Staging Environments")
                print("=" * 80)
                if not result:
                    print("  No staging environments found")
                for env in result:
                    status = env.get("status", "?")
                    comp = env.get("compliance_preserved")
                    comp_str = "yes" if comp == 1 else ("no" if comp == 0 else "N/A")
                    print(f"  {env.get('id', '?'):16s}  "
                          f"{status:10s}  "
                          f"cap={env.get('capability_id', '?'):16s}  "
                          f"compliance={comp_str}")
            elif isinstance(result, dict):
                if "error" in result:
                    print(f"ERROR: {result['error']}", file=sys.stderr)
                elif "destroyed" in result:
                    ok = result.get("destroyed", False)
                    print(f"{'Destroyed' if ok else 'Failed to destroy'}: "
                          f"{result.get('staging_id')}")
                elif "staging_id" in result and "status" in result:
                    print(f"Staging: {result.get('staging_id')}")
                    print(f"  Capability: {result.get('capability_id', 'N/A')}")
                    print(f"  Status:     {result.get('status', 'N/A')}")
                    print(f"  Path:       {result.get('worktree_path', 'N/A')}")
                    print(f"  Expires:    {result.get('expires_at', 'N/A')}")
                elif "passed" in result:
                    ok = result.get("passed", False)
                    print(f"Tests: {'PASSED' if ok else 'FAILED'}")
                    print(f"  Staging: {result.get('staging_id')}")
                elif "compliance_preserved" in result:
                    ok = result.get("compliance_preserved", False)
                    print(f"Compliance: {'PRESERVED' if ok else 'DEGRADED'}")
                    issues = result.get("issues", [])
                    if issues:
                        for issue in issues:
                            print(f"  - {issue}")
                else:
                    print(json.dumps(result, indent=2, default=str))

    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
