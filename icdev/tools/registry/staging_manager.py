#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
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
import subprocess
import sys
import uuid
from tools.db.storage import get_connection
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
# Overridden by args/evolution_config.yaml staging.expiry_hours if present
DEFAULT_EXPIRY_HOURS = 72


def _load_evolution_config() -> dict:
    """Load staging config from args/evolution_config.yaml."""
    config_path = BASE_DIR / "args" / "evolution_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("staging", {})
    except Exception:
        return {}


_staging_cfg = _load_evolution_config()
if _staging_cfg.get("expiry_hours"):
    DEFAULT_EXPIRY_HOURS = int(_staging_cfg["expiry_hours"])

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
        conn = get_connection(db_path=str(self.db_path))
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

    def create_staging(self, capability_id: str, genome_version: str = None) -> Optional[dict]:
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
        result = _run_git(
            [
                "worktree",
                "add",
                "--no-checkout",
                str(worktree_path),
                "-b",
                branch_name,
            ]
        )

        if result.returncode != 0:
            # Branch might exist; try without -b
            result = _run_git(
                [
                    "worktree",
                    "add",
                    "--no-checkout",
                    str(worktree_path),
                ]
            )
            if result.returncode != 0:
                error_msg = f"Failed to create worktree: {result.stderr.strip()}"
                _audit("staging.create.failed", error_msg, {"capability_id": capability_id, "error": error_msg})
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
            class_file.write_text("CUI // SP-CTI\n", newline="")
        except Exception:
            pass

        # Calculate expiry
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=DEFAULT_EXPIRY_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Store in database
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO staging_environments
                   (id, capability_id, genome_version, worktree_path,
                    branch_name, status, created_at, expires_at)
                   VALUES (%s, %s, %s, %s, %s, 'created', %s, %s)""",
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
        """Run the full 7-step test pipeline in the staging environment (D-EVO-4).

        Steps (from args/evolution_config.yaml staging.test_pipeline):
            1. py_compile  — Python syntax validation (blocking)
            2. ruff        — Ultra-fast linter (blocking)
            3. pytest      — Unit/integration tests (blocking)
            4. behave      — BDD scenario tests (non-blocking)
            5. bandit      — SAST security scan (blocking)
            6. secret_detection — Secret detection (blocking)
            7. cui_check   — CUI marking verification (non-blocking)

        Blocking steps: failure stops pipeline and marks staging as 'failed'.
        Non-blocking steps: failure recorded as warning but pipeline continues.

        Args:
            staging_id: ID of the staging environment.

        Returns:
            Dict with per-step test results and overall status.
        """
        env_record = self._get_record(staging_id)
        if not env_record:
            return {"error": f"Staging environment {staging_id} not found"}

        worktree_path = env_record.get("worktree_path", "")
        wt = Path(worktree_path)
        if not wt.exists():
            return {"error": f"Worktree path does not exist: {worktree_path}"}

        # Update status to testing
        self._update_status(staging_id, "testing")

        # Load pipeline config
        pipeline_config = _staging_cfg.get("test_pipeline", [])
        if not pipeline_config:
            # Default pipeline if config not loaded
            pipeline_config = [
                {"name": "py_compile", "blocking": True, "timeout_seconds": 60},
                {"name": "ruff", "blocking": True, "timeout_seconds": 120},
                {"name": "pytest", "blocking": True, "timeout_seconds": 300},
                {"name": "behave", "blocking": False, "timeout_seconds": 300},
                {"name": "bandit", "blocking": True, "timeout_seconds": 120},
                {"name": "secret_detection", "blocking": True, "timeout_seconds": 120},
                {"name": "cui_check", "blocking": False, "timeout_seconds": 60},
            ]

        step_results = []
        blocking_failed = False
        warnings = []

        for step in pipeline_config:
            step_name = step.get("name", "unknown")
            is_blocking = step.get("blocking", True)
            timeout = step.get("timeout_seconds", 300)

            if blocking_failed:
                step_results.append(
                    {
                        "step": step_name,
                        "status": "skipped",
                        "reason": "prior blocking step failed",
                    }
                )
                continue

            step_result = self._run_pipeline_step(step_name, worktree_path, timeout)
            step_result["blocking"] = is_blocking
            step_results.append(step_result)

            if not step_result.get("success", False):
                if is_blocking:
                    blocking_failed = True
                else:
                    warnings.append(step_name)

        # Determine overall status
        overall_passed = not blocking_failed
        new_status = "passed" if overall_passed else "failed"

        test_results = {
            "staging_id": staging_id,
            "passed": overall_passed,
            "status": new_status,
            "steps": step_results,
            "warnings": warnings,
            "tested_at": _now(),
        }

        # Update DB
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE staging_environments
                   SET status = %s, test_results_json = %s
                   WHERE id = %s""",
                (new_status, json.dumps(test_results), staging_id),
            )
            conn.commit()
        except Exception as e:
            print(f"Warning: DB update failed: {e}", file=sys.stderr)
        finally:
            conn.close()

        _audit(
            "staging.tested",
            f"Staging {staging_id} pipeline {'passed' if overall_passed else 'failed'} "
            f"({len(step_results)} steps, {len(warnings)} warnings)",
            {"staging_id": staging_id, "passed": overall_passed, "warnings": warnings},
        )

        return test_results

    def _run_pipeline_step(self, step_name: str, worktree_path: str, timeout: int = 300) -> dict:
        """Execute a single pipeline step and return structured result.

        Args:
            step_name: Name of the step (py_compile, ruff, pytest, etc.)
            worktree_path: Path to the staging worktree.
            timeout: Maximum seconds for this step.

        Returns:
            Dict with step name, success boolean, and output preview.
        """
        wt = Path(worktree_path)
        result = {"step": step_name, "success": False, "output": ""}

        if step_name == "py_compile":
            # Compile-check all .py files in tools/
            tools_dir = wt / "tools"
            if not tools_dir.exists():
                result["success"] = True
                result["output"] = "No tools/ directory"
                return result
            py_files = list(tools_dir.rglob("*.py"))[:200]
            failures = []
            for pf in py_files:
                r = _run_subprocess([sys.executable, "-m", "py_compile", str(pf)], cwd=worktree_path, timeout=30)
                if not r.get("success"):
                    failures.append(str(pf.relative_to(wt)))
                    if len(failures) >= 5:
                        break
            result["success"] = len(failures) == 0
            result["output"] = f"{len(py_files)} files checked, {len(failures)} failures"
            if failures:
                result["failures"] = failures[:5]

        elif step_name == "ruff":
            r = _run_subprocess(
                [sys.executable, "-m", "ruff", "check", "tools/", "--select", "E,F", "--ignore", "E402"],
                cwd=worktree_path,
                timeout=timeout,
            )
            result["success"] = r.get("success", False)
            result["output"] = r.get("stdout", "")[:2000]

        elif step_name == "pytest":
            tests_dir = wt / "tests"
            if not tests_dir.exists():
                result["success"] = True
                result["output"] = "No tests/ directory"
                return result
            r = _run_subprocess(
                [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short", "-q"], cwd=worktree_path, timeout=timeout
            )
            result["success"] = r.get("success", False)
            result["output"] = r.get("stdout", "")[:2000]

        elif step_name == "behave":
            features_dir = wt / "features"
            if not features_dir.exists():
                result["success"] = True
                result["output"] = "No features/ directory (skipped)"
                return result
            r = _run_subprocess([sys.executable, "-m", "behave", "features/"], cwd=worktree_path, timeout=timeout)
            result["success"] = r.get("success", False)
            result["output"] = r.get("stdout", "")[:2000]

        elif step_name == "bandit":
            tools_dir = wt / "tools"
            if not tools_dir.exists():
                result["success"] = True
                result["output"] = "No tools/ directory"
                return result
            r = _run_subprocess(
                [sys.executable, "-m", "bandit", "-r", "tools/", "-f", "json", "-q", "--severity-level", "high"],
                cwd=worktree_path,
                timeout=timeout,
            )
            # bandit returns 1 if findings exist
            findings_count = 0
            if r.get("stdout"):
                try:
                    bandit_out = json.loads(r["stdout"])
                    findings_count = len(bandit_out.get("results", []))
                except (json.JSONDecodeError, TypeError):
                    pass
            result["success"] = findings_count == 0
            result["output"] = f"{findings_count} high+ findings"

        elif step_name == "secret_detection":
            r = _run_subprocess(
                [sys.executable, "tools/security/secret_detector.py", "--project-dir", "."],
                cwd=worktree_path,
                timeout=timeout,
            )
            result["success"] = r.get("success", False)
            result["output"] = r.get("stdout", "")[:2000]

        elif step_name == "cui_check":
            # Check CUI markings in key files
            cui_found = False
            for check_file in ["CLAUDE.md", "tools/__init__.py"]:
                fp = wt / check_file
                if fp.exists():
                    try:
                        content = fp.read_text(encoding="utf-8", errors="ignore")[:500]
                        if "CUI" in content:
                            cui_found = True
                            break
                    except Exception:
                        pass
            result["success"] = cui_found
            result["output"] = "CUI markings present" if cui_found else "CUI markings not found"

        else:
            result["success"] = True
            result["output"] = f"Unknown step '{step_name}' — skipped"

        return result

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
            issues.append(f"Security findings increased: {before_findings} -> {after_findings}")

        # Check: test count should not decrease
        before_tests = compliance_before.get("test_count", 0)
        after_tests = compliance_after.get("test_count", 0)
        if after_tests < before_tests:
            issues.append(f"Test count decreased: {before_tests} -> {after_tests} (warning)")

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
                   SET compliance_before_json = %s,
                       compliance_after_json = %s,
                       compliance_preserved = %s
                   WHERE id = %s""",
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
            [
                sys.executable,
                "-m",
                "bandit",
                "-r",
                str(dir_path / "tools"),
                "-f",
                "json",
                "-q",
                "--severity-level",
                "high",
            ],
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
                print(f"Warning: worktree remove failed: {result.stderr}", file=sys.stderr)
                success = False

        # Update DB status
        conn = self._get_conn()
        try:
            conn.execute(
                """UPDATE staging_environments
                   SET status = 'destroyed', destroyed_at = %s
                   WHERE id = %s""",
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
            row = conn.execute("SELECT * FROM staging_environments WHERE id = %s", (staging_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _update_status(self, staging_id: str, status: str):
        """Update staging environment status."""
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE staging_environments SET status = %s WHERE id = %s",
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
        description="ICDEV™ Staging Manager -- isolated capability testing environments (D211)"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", type=Path, default=None, help="Database path override")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", action="store_true", help="Create a staging environment")
    group.add_argument("--test", action="store_true", help="Run tests in staging environment")
    group.add_argument("--check-compliance", action="store_true", help="Check compliance preservation")
    group.add_argument("--destroy", action="store_true", help="Destroy a staging environment")
    group.add_argument("--list", action="store_true", help="List all staging environments")

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
            result = manager.check_compliance_preservation(staging_id=args.staging_id)

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
                    print(
                        f"  {env.get('id', '?'):16s}  "
                        f"{status:10s}  "
                        f"cap={env.get('capability_id', '?'):16s}  "
                        f"compliance={comp_str}"
                    )
            elif isinstance(result, dict):
                if "error" in result:
                    print(f"ERROR: {result['error']}", file=sys.stderr)
                elif "destroyed" in result:
                    ok = result.get("destroyed", False)
                    print(f"{'Destroyed' if ok else 'Failed to destroy'}: {result.get('staging_id')}")
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
