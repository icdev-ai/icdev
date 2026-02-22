#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV Python SDK client — thin wrapper around CLI tools (D191).

Wraps existing ICDEV CLI tools via subprocess.run() with --json flag.
Works offline, air-gap safe, no server dependency.  Project-scoped —
set project_id once, use everywhere.

Usage:
    from tools.sdk.icdev_client import ICDEVClient

    client = ICDEVClient(project_id="proj-123", project_dir="/path/to/project")
    status = client.project_status()
    ssp = client.generate_ssp()
    stig = client.check_stig()
    context = client.build_context()
"""

import json
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class ICDEVError(Exception):
    """Raised when an ICDEV CLI tool returns a non-zero exit code."""

    def __init__(self, tool: str, returncode: int, stderr: str):
        self.tool = tool
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"ICDEV tool '{tool}' failed (exit {returncode}): {stderr}")


class ICDEVClient:
    """Thin Python SDK wrapping ICDEV CLI tools.

    Args:
        project_id: Project UUID (used for compliance/security tools).
        project_dir: Project directory path (used for scanning tools).
        db_path: Path to icdev.db (defaults to data/icdev.db).
        timeout: Subprocess timeout in seconds (default 120).
    """

    def __init__(
        self,
        project_id: str = None,
        project_dir: str = None,
        db_path: str = None,
        timeout: int = 120,
    ):
        self.project_id = project_id
        self.project_dir = project_dir
        self.db_path = db_path or str(BASE_DIR / "data" / "icdev.db")
        self.timeout = timeout
        self._python = sys.executable

    def _run(self, tool_path: str, args: list = None) -> dict:
        """Execute a CLI tool and return parsed JSON output.

        Args:
            tool_path: Relative path from BASE_DIR (e.g. 'tools/project/project_status.py').
            args: List of CLI arguments.

        Returns:
            Parsed JSON dict from tool stdout.

        Raises:
            ICDEVError: If tool exits with non-zero code.
        """
        full_path = str(BASE_DIR / tool_path)
        cmd = [self._python, full_path] + (args or []) + ["--json"]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            stdin=subprocess.DEVNULL,
            cwd=str(BASE_DIR),
        )

        if result.returncode != 0:
            raise ICDEVError(tool_path, result.returncode, result.stderr.strip())

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            # Return raw stdout if not valid JSON
            return {"raw_output": result.stdout.strip()}

    # ── Project ─────────────────────────────────────────────────────

    def project_status(self) -> dict:
        """Get comprehensive project status."""
        args = ["--project", self.project_id, "--format", "json"]
        return self._run("tools/project/project_status.py", args)

    def project_list(self, status_filter: str = None) -> dict:
        """List all projects."""
        args = []
        if status_filter:
            args.extend(["--status", status_filter])
        return self._run("tools/project/project_list.py", args)

    def project_create(self, name: str, project_type: str = "webapp", **kwargs) -> dict:
        """Create a new project."""
        args = ["--name", name, "--type", project_type]
        for k, v in kwargs.items():
            args.extend([f"--{k.replace('_', '-')}", str(v)])
        return self._run("tools/project/project_create.py", args)

    # ── Compliance ──────────────────────────────────────────────────

    def generate_ssp(self) -> dict:
        """Generate System Security Plan."""
        return self._run("tools/compliance/ssp_generator.py",
                         ["--project-id", self.project_id])

    def generate_poam(self) -> dict:
        """Generate Plan of Action and Milestones."""
        return self._run("tools/compliance/poam_generator.py",
                         ["--project-id", self.project_id])

    def check_stig(self) -> dict:
        """Run STIG compliance check."""
        return self._run("tools/compliance/stig_checker.py",
                         ["--project-id", self.project_id])

    def generate_sbom(self, project_dir: str = None) -> dict:
        """Generate Software Bill of Materials."""
        pdir = project_dir or self.project_dir or "."
        return self._run("tools/compliance/sbom_generator.py",
                         ["--project-dir", pdir])

    def query_crosswalk(self, control: str) -> dict:
        """Query the compliance crosswalk engine."""
        return self._run("tools/compliance/crosswalk_engine.py",
                         ["--control", control])

    def assess_compliance(self) -> dict:
        """Multi-framework compliance assessment."""
        return self._run("tools/compliance/multi_regime_assessor.py",
                         ["--project-id", self.project_id])

    # ── Security ────────────────────────────────────────────────────

    def run_sast(self, project_dir: str = None) -> dict:
        """Run SAST scan."""
        pdir = project_dir or self.project_dir or "."
        return self._run("tools/security/sast_runner.py",
                         ["--project-dir", pdir])

    def audit_dependencies(self, project_dir: str = None) -> dict:
        """Run dependency audit."""
        pdir = project_dir or self.project_dir or "."
        return self._run("tools/security/dependency_auditor.py",
                         ["--project-dir", pdir])

    def detect_secrets(self, project_dir: str = None) -> dict:
        """Run secret detection."""
        pdir = project_dir or self.project_dir or "."
        return self._run("tools/security/secret_detector.py",
                         ["--project-dir", pdir])

    # ── Builder ─────────────────────────────────────────────────────

    def scaffold(self, name: str, project_type: str, project_path: str = None) -> dict:
        """Scaffold a new project."""
        args = ["--name", name, "--type", project_type]
        if project_path:
            args.extend(["--project-path", project_path])
        return self._run("tools/builder/scaffolder.py", args)

    def run_tests(self, project_dir: str = None) -> dict:
        """Run test suite."""
        pdir = project_dir or self.project_dir or "."
        return self._run("tools/testing/test_orchestrator.py",
                         ["--project-dir", pdir])

    # ── Dev Profiles ────────────────────────────────────────────────

    def resolve_profile(self, scope: str = "project") -> dict:
        """Resolve the effective dev profile (5-layer cascade)."""
        scope_id = self.project_id or "unknown"
        return self._run("tools/builder/dev_profile_manager.py",
                         ["--scope", scope, "--scope-id", scope_id, "--resolve"])

    def detect_profile(self, repo_path: str) -> dict:
        """Auto-detect dev profile from repository."""
        return self._run("tools/builder/profile_detector.py",
                         ["--repo-path", repo_path])

    # ── Context ─────────────────────────────────────────────────────

    def build_context(self, directory: str = None) -> dict:
        """Build session context for current project."""
        args = ["--format", "json"]
        if directory:
            args.extend(["--dir", directory])
        if self.db_path:
            args.extend(["--db", self.db_path])
        return self._run("tools/project/session_context_builder.py", args)

    def load_manifest(self, directory: str = None) -> dict:
        """Load and validate icdev.yaml manifest."""
        args = ["--dir", directory or self.project_dir or "."]
        return self._run("tools/project/manifest_loader.py", args)

    # ── Pipeline ────────────────────────────────────────────────────

    def generate_pipeline(self, platform: str = "auto", directory: str = None) -> dict:
        """Generate CI/CD pipeline config."""
        args = ["--platform", platform, "--dry-run"]
        if directory:
            args.extend(["--dir", directory])
        return self._run("tools/ci/pipeline_config_generator.py", args)
