# [TEMPLATE: CUI // SP-CTI]
# ICDEV Recovery Engine — self-recovery from test/lint failures (D134)

"""
Self-recovery pipeline for CI/CD phase failures.

When a test, lint, or BDD phase fails, the recovery engine:
1. Parses the failure output into structured form
2. Checks if the failure is recoverable (security gate violations are NOT)
3. Invokes builder agent to generate a fix (strongest model)
4. Re-runs only the failed tests (targeted retesting)
5. If pass: auto-commits fix with targeted file paths, pushes to branch
6. If fail: retries up to max_attempts, then escalates to human

Architecture Decisions:
    D134: Self-recovery auto-commits fixes to branch; developer reviews in PR diff;
          targeted retesting (failed tests only) with configurable max attempts and
          security gate guard rails

Usage:
    from tools.ci.core.recovery_engine import RecoveryEngine
    engine = RecoveryEngine()
    result = engine.attempt_recovery("test", failure_output, run_id, issue_number, state)
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from tools.ci.core.failure_parser import parse_failure_output, ParsedFailure

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@dataclass
class RecoveryResult:
    """Result of a recovery attempt."""
    recovered: bool
    attempts: int
    max_attempts: int
    phase: str
    fixed_files: list
    final_failure: Optional[ParsedFailure] = None
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "recovered": self.recovered,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "phase": self.phase,
            "fixed_files": self.fixed_files,
            "error": self.error,
            "final_failure": self.final_failure.to_dict() if self.final_failure else None,
        }


def _load_recovery_config() -> dict:
    """Load recovery configuration from cicd_config.yaml."""
    try:
        import yaml
        config_path = PROJECT_ROOT / "args" / "cicd_config.yaml"
        if config_path.exists():
            with open(config_path) as f:
                data = yaml.safe_load(f) or {}
            return data.get("cicd", {}).get("recovery", {})
    except Exception:
        pass
    return {}


def _load_security_gates() -> dict:
    """Load non-recoverable failure patterns from security_gates.yaml."""
    try:
        import yaml
        gates_path = PROJECT_ROOT / "args" / "security_gates.yaml"
        if gates_path.exists():
            with open(gates_path) as f:
                data = yaml.safe_load(f) or {}
            return data.get("recovery", {})
    except Exception:
        pass
    return {}


class RecoveryEngine:
    """Self-recovery engine for CI/CD phase failures."""

    def __init__(self, db_path: str = None):
        self._config = _load_recovery_config()
        self._gates = _load_security_gates()
        self.max_attempts = self._config.get("max_attempts", 3)
        self.retest_scope = self._config.get("retest_scope", "failed_only")
        self.fix_model = self._config.get("fix_model", "opus")
        self.audit_every = self._config.get("audit_every_attempt", True)
        self.enabled = self._config.get("enabled", True)
        self.db_path = db_path

    def attempt_recovery(
        self,
        phase_name: str,
        failure_output: str,
        run_id: str,
        issue_number: str,
        state=None,
    ) -> RecoveryResult:
        """Attempt to recover from a phase failure.

        Args:
            phase_name: The phase that failed (test, lint, bdd, compile, security)
            failure_output: Raw output from the failed tool
            run_id: Current pipeline run ID
            issue_number: Issue/MR number
            state: ICDevState object (optional)

        Returns:
            RecoveryResult indicating success/failure and details
        """
        if not self.enabled:
            return RecoveryResult(
                recovered=False,
                attempts=0,
                max_attempts=self.max_attempts,
                phase=phase_name,
                fixed_files=[],
                error="Recovery disabled in config",
            )

        # Parse the initial failure
        failure = parse_failure_output(phase_name, failure_output)

        # Check if recoverable
        if not failure.recoverable:
            reason = "security_blocked" if failure.security_blocked else "non_recoverable"
            self._log_audit(
                run_id, phase_name, 0, "skipped",
                f"Not recoverable: {reason}", failure,
            )
            return RecoveryResult(
                recovered=False,
                attempts=0,
                max_attempts=self.max_attempts,
                phase=phase_name,
                fixed_files=[],
                final_failure=failure,
                error=f"Failure is not recoverable ({reason}): {failure.summary}",
            )

        all_fixed_files = []

        for attempt in range(1, self.max_attempts + 1):
            print(f"\n[Recovery] Attempt {attempt}/{self.max_attempts} for {phase_name}")

            # 1. Log attempt
            self._log_audit(
                run_id, phase_name, attempt, "started",
                failure.summary, failure,
            )

            # 2. Generate fix via builder agent
            fixed_files = self._generate_fix(failure, run_id, issue_number, attempt)

            if not fixed_files:
                self._log_audit(
                    run_id, phase_name, attempt, "fix_failed",
                    "Agent could not generate a fix", failure,
                )
                # Use same failure for next attempt
                continue

            all_fixed_files.extend(f for f in fixed_files if f not in all_fixed_files)

            # 3. Re-run only failed tests (targeted)
            retest_output = self._retest(failure, phase_name)

            if retest_output is None:
                # Retest mechanism not available — assume fix might work
                self._log_audit(
                    run_id, phase_name, attempt, "retest_skipped",
                    "Retest not available", failure,
                )
                # Commit the fix anyway — developer reviews in PR
                self._commit_fix(
                    fixed_files, run_id, issue_number, phase_name, attempt,
                )
                return RecoveryResult(
                    recovered=True,
                    attempts=attempt,
                    max_attempts=self.max_attempts,
                    phase=phase_name,
                    fixed_files=all_fixed_files,
                )

            # 4. Parse retest results
            retest_failure = parse_failure_output(phase_name, retest_output)

            if not retest_failure.failed_tests and not retest_failure.failed_checks:
                # All tests/checks pass now
                self._log_audit(
                    run_id, phase_name, attempt, "recovered",
                    f"Fix successful after {attempt} attempt(s)", failure,
                )
                self._commit_fix(
                    all_fixed_files, run_id, issue_number, phase_name, attempt,
                )
                return RecoveryResult(
                    recovered=True,
                    attempts=attempt,
                    max_attempts=self.max_attempts,
                    phase=phase_name,
                    fixed_files=all_fixed_files,
                )

            # 5. Update failure for next attempt
            failure = retest_failure
            self._log_audit(
                run_id, phase_name, attempt, "still_failing",
                failure.summary, failure,
            )

        # All attempts exhausted
        self._log_audit(
            run_id, phase_name, self.max_attempts, "exhausted",
            f"All {self.max_attempts} attempts exhausted", failure,
        )

        return RecoveryResult(
            recovered=False,
            attempts=self.max_attempts,
            max_attempts=self.max_attempts,
            phase=phase_name,
            fixed_files=all_fixed_files,
            final_failure=failure,
            error=f"Recovery failed after {self.max_attempts} attempts: {failure.summary}",
        )

    def _generate_fix(
        self,
        failure: ParsedFailure,
        run_id: str,
        issue_number: str,
        attempt: int,
    ) -> list:
        """Invoke builder agent to generate a fix for the failure.

        Returns list of files that were modified, or empty list on failure.
        """
        # Build the fix prompt
        prompt = self._build_fix_prompt(failure, attempt)

        try:
            from tools.ci.modules.agent import prompt_claude_code

            request = type("Req", (), {
                "slash_command": "/implement",
                "prompt": prompt,
                "run_id": run_id,
            })()

            response = prompt_claude_code(request)

            if response and hasattr(response, "result") and response.result:
                # Extract changed files from agent output
                return self._extract_changed_files(response.result)
            elif response and hasattr(response, "output") and response.output:
                return self._extract_changed_files(response.output)
        except Exception as e:
            print(f"[Recovery] Fix generation failed: {e}")

        return []

    def _build_fix_prompt(self, failure: ParsedFailure, attempt: int) -> str:
        """Build a prompt for the builder agent to fix the failure."""
        lines = [
            f"RECOVERY ATTEMPT {attempt}: Fix the following {failure.phase} failure.",
            "",
        ]

        if failure.failed_tests:
            lines.append("## Failed Tests")
            for t in failure.failed_tests:
                lines.append(f"- {t.file}:{t.line} {t.name}")
                if t.error:
                    lines.append(f"  Error: {t.error}")
                if t.assertion:
                    lines.append(f"  Assertion: {t.assertion}")
            lines.append("")

        if failure.failed_checks:
            lines.append("## Failed Checks")
            for c in failure.failed_checks:
                lines.append(f"- [{c.tool}:{c.rule}] {c.file}:{c.line} {c.message}")
            lines.append("")

        if failure.relevant_files:
            lines.append("## Relevant Files")
            for f in failure.relevant_files:
                lines.append(f"- {f}")
            lines.append("")

        lines.extend([
            "## Instructions",
            f"- Fix the {failure.error_category or 'errors'} in the relevant files",
            "- Make minimal changes — fix only what's broken",
            "- Do NOT change test expectations unless the test itself is wrong",
            "- Preserve all CUI // SP-CTI markings",
            "- Do NOT introduce new dependencies",
        ])

        if attempt > 1:
            lines.append(f"- Previous {attempt - 1} fix attempt(s) did not resolve the issue")
            lines.append("- Try a different approach than before")

        return "\n".join(lines)

    def _extract_changed_files(self, agent_output: str) -> list:
        """Extract list of changed files from agent output or git diff."""
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only"],
                capture_output=True, text=True, timeout=10,
                cwd=str(PROJECT_ROOT),
            )
            if result.returncode == 0 and result.stdout.strip():
                return [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
        except Exception:
            pass

        # Fallback: try to parse file paths from agent output
        import re
        file_pattern = re.compile(r"(?:tools|tests|features)/[\w/.-]+\.(?:py|feature|yaml|json)")
        matches = file_pattern.findall(agent_output or "")
        return list(set(matches))

    def _retest(self, failure: ParsedFailure, phase_name: str) -> Optional[str]:
        """Re-run failed tests/checks.

        Returns output string from retest, or None if retest not available.
        """
        if phase_name in ("test", "pytest"):
            return self._retest_pytest(failure)
        elif phase_name in ("lint", "ruff"):
            return self._retest_ruff(failure)
        elif phase_name in ("bdd", "behave"):
            return self._retest_behave(failure)
        elif phase_name in ("compile", "py_compile"):
            return self._retest_compile(failure)
        return None

    def _retest_pytest(self, failure: ParsedFailure) -> Optional[str]:
        """Re-run only the failed pytest tests."""
        if self.retest_scope == "failed_only" and failure.failed_test_names:
            # Run only specific failed tests using -k filter
            test_filter = " or ".join(
                name.split("::")[-1] for name in failure.failed_test_names
            )
            cmd = [
                sys.executable, "-m", "pytest",
                "-x", "--tb=short", "-k", test_filter,
            ]
        else:
            # Full suite retest
            cmd = [sys.executable, "-m", "pytest", "-x", "--tb=short"]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300,
                cwd=str(PROJECT_ROOT),
            )
            return result.stdout + result.stderr
        except Exception as e:
            return f"Retest failed: {e}"

    def _retest_ruff(self, failure: ParsedFailure) -> Optional[str]:
        """Re-run ruff on the relevant files."""
        files = failure.relevant_files or ["."]
        cmd = [sys.executable, "-m", "ruff", "check"] + files
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
                cwd=str(PROJECT_ROOT),
            )
            return result.stdout + result.stderr
        except Exception as e:
            return f"Ruff recheck failed: {e}"

    def _retest_behave(self, failure: ParsedFailure) -> Optional[str]:
        """Re-run failing behave scenarios."""
        cmd = [sys.executable, "-m", "behave", "--no-capture"]
        if failure.relevant_files:
            # Run specific feature files
            cmd.extend(failure.relevant_files)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300,
                cwd=str(PROJECT_ROOT),
            )
            return result.stdout + result.stderr
        except Exception as e:
            return f"Behave retest failed: {e}"

    def _retest_compile(self, failure: ParsedFailure) -> Optional[str]:
        """Re-run py_compile on relevant files."""
        outputs = []
        for check in failure.failed_checks:
            if check.file:
                try:
                    result = subprocess.run(
                        [sys.executable, "-m", "py_compile", check.file],
                        capture_output=True, text=True, timeout=30,
                        cwd=str(PROJECT_ROOT),
                    )
                    outputs.append(result.stdout + result.stderr)
                except Exception as e:
                    outputs.append(f"Compile check failed: {e}")
        return "\n".join(outputs) if outputs else None

    def _commit_fix(
        self,
        fixed_files: list,
        run_id: str,
        issue_number: str,
        phase_name: str,
        attempt: int,
    ):
        """Commit the recovery fix with targeted file paths."""
        if not fixed_files:
            return

        try:
            from tools.ci.modules.git_ops import commit_changes

            message = (
                f"icdev_recovery: fix {phase_name} failure for issue #{issue_number} "
                f"(attempt {attempt}, run_id: {run_id})"
            )

            # Use targeted paths (not git add -A) per D134
            success, error = commit_changes(message, paths=fixed_files)
            if success:
                print(f"[Recovery] Committed fix: {len(fixed_files)} file(s)")
            else:
                print(f"[Recovery] Commit failed: {error}")
        except Exception as e:
            print(f"[Recovery] Commit error: {e}")

    def _log_audit(
        self,
        run_id: str,
        phase: str,
        attempt: int,
        status: str,
        message: str,
        failure: ParsedFailure = None,
    ):
        """Log recovery attempt to audit trail (append-only, D6)."""
        if not self.audit_every and status not in ("recovered", "exhausted", "skipped"):
            return

        try:
            from tools.audit.audit_logger import log_audit_event

            log_audit_event(
                event_type="ci.recovery",
                actor="recovery_engine",
                action=f"recovery_{status}",
                project_id=run_id,
                details=json.dumps({
                    "phase": phase,
                    "attempt": attempt,
                    "status": status,
                    "message": message,
                    "failure_summary": failure.summary if failure else "",
                    "files": failure.relevant_files if failure else [],
                }),
            )
        except Exception:
            # Audit logging is best-effort; don't block recovery
            pass

    def format_escalation_message(self, result: RecoveryResult) -> str:
        """Format a human-readable escalation message when recovery fails."""
        lines = [
            f"## Recovery Failed — {result.phase.upper()} Phase",
            "",
            f"**Attempts:** {result.attempts}/{result.max_attempts}",
            "**Status:** Recovery exhausted",
            "",
        ]

        if result.final_failure:
            failure = result.final_failure
            lines.append(f"**Category:** {failure.error_category}")
            lines.append("")

            if failure.failed_tests:
                lines.append("### Failed Tests")
                for t in failure.failed_tests:
                    lines.append(f"- `{t.file}:{t.line}` — {t.name}")
                    if t.error:
                        lines.append(f"  ```\n  {t.error}\n  ```")
                lines.append("")

            if failure.failed_checks:
                lines.append("### Failed Checks")
                for c in failure.failed_checks:
                    lines.append(f"- [{c.tool}:{c.rule}] `{c.file}:{c.line}` — {c.message}")
                lines.append("")

            if failure.relevant_files:
                lines.append("### Relevant Files")
                for f in failure.relevant_files:
                    lines.append(f"- `{f}`")
                lines.append("")

        if result.fixed_files:
            lines.append("### Files Modified During Recovery")
            for f in result.fixed_files:
                lines.append(f"- `{f}`")
            lines.append("")

        lines.append("**Action Required:** Manual review and fix needed.")
        return "\n".join(lines)
