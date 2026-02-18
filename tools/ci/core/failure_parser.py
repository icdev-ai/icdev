# CUI // SP-CTI
# ICDEV Failure Parser — structured failure extraction from tool output (D134)

"""
Structured failure parsing from test/lint/security tool output.

Extracts actionable information from pytest, ruff, bandit, and behave output
to feed into the RecoveryEngine for automated fix attempts.

Architecture Decisions:
    D134: Self-recovery auto-commits fixes; targeted retesting (failed only)

Usage:
    from tools.ci.core.failure_parser import parse_failure_output, ParsedFailure
    failure = parse_failure_output("test", output_text)
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class FailedTest:
    """A single failed test case."""
    name: str
    file: str
    line: int = 0
    error: str = ""
    assertion: str = ""


@dataclass
class FailedCheck:
    """A single failed lint/security check."""
    tool: str
    rule: str
    file: str
    line: int = 0
    message: str = ""


@dataclass
class ParsedFailure:
    """Structured representation of a CI phase failure."""
    phase: str
    recoverable: bool = True
    failed_tests: List[FailedTest] = field(default_factory=list)
    failed_checks: List[FailedCheck] = field(default_factory=list)
    relevant_files: List[str] = field(default_factory=list)
    error_category: str = ""  # test_assertion, syntax_error, lint_violation, etc.
    security_blocked: bool = False
    raw_output: str = ""

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "recoverable": self.recoverable,
            "failed_tests": [
                {"name": t.name, "file": t.file, "line": t.line,
                 "error": t.error, "assertion": t.assertion}
                for t in self.failed_tests
            ],
            "failed_checks": [
                {"tool": c.tool, "rule": c.rule, "file": c.file,
                 "line": c.line, "message": c.message}
                for c in self.failed_checks
            ],
            "relevant_files": self.relevant_files,
            "error_category": self.error_category,
            "security_blocked": self.security_blocked,
        }

    @property
    def failed_test_names(self) -> List[str]:
        """List of test function/method names for targeted retest."""
        return [t.name for t in self.failed_tests if t.name]

    @property
    def summary(self) -> str:
        """Human-readable failure summary."""
        parts = [f"Phase: {self.phase}"]
        if self.failed_tests:
            parts.append(f"{len(self.failed_tests)} test(s) failed")
        if self.failed_checks:
            parts.append(f"{len(self.failed_checks)} check(s) failed")
        if self.security_blocked:
            parts.append("SECURITY BLOCKED — not recoverable")
        elif not self.recoverable:
            parts.append("NOT RECOVERABLE")
        parts.append(f"Category: {self.error_category or 'unknown'}")
        return " | ".join(parts)


# ── Non-recoverable failure patterns ──────────────────────────────────

NON_RECOVERABLE_PATTERNS = [
    r"secrets?\s*detected",
    r"cat[_\s]*1\s*stig",
    r"critical\s*vulnerability",
    r"missing\s*cui\s*markings",
    r"classification\s*violation",
    r"high\s*vulnerability.*sast",
]

SECURITY_BLOCKED_PATTERNS = [
    r"secrets?\s*detected",
    r"critical\s*vulnerability",
    r"cat[_\s]*1\s*stig",
    r"classification\s*violation",
]


def _is_non_recoverable(output: str) -> bool:
    """Check if output contains non-recoverable failure indicators."""
    output_lower = output.lower()
    return any(re.search(p, output_lower) for p in NON_RECOVERABLE_PATTERNS)


def _is_security_blocked(output: str) -> bool:
    """Check if output indicates a security gate failure."""
    output_lower = output.lower()
    return any(re.search(p, output_lower) for p in SECURITY_BLOCKED_PATTERNS)


# ── Pytest Output Parser ──────────────────────────────────────────────

def parse_pytest_output(output: str) -> ParsedFailure:
    """Parse pytest output into structured failure."""
    failure = ParsedFailure(
        phase="test",
        raw_output=output,
        error_category="test_assertion",
    )

    # Match FAILED test lines: FAILED tests/test_foo.py::TestClass::test_method
    failed_pattern = re.compile(
        r"FAILED\s+([\w/\\.-]+)::(\w+(?:::\w+)?)"
    )
    for match in failed_pattern.finditer(output):
        filepath = match.group(1).replace("\\", "/")
        test_name = match.group(2)
        failure.failed_tests.append(FailedTest(
            name=test_name,
            file=filepath,
        ))
        if filepath not in failure.relevant_files:
            failure.relevant_files.append(filepath)

    # Match assertion errors: E   AssertionError: ...
    assertion_pattern = re.compile(
        r"^E\s+(AssertionError|assert\w*):?\s*(.*)$", re.MULTILINE
    )
    assertions = assertion_pattern.findall(output)
    for i, (err_type, message) in enumerate(assertions):
        if i < len(failure.failed_tests):
            failure.failed_tests[i].assertion = message.strip()
            failure.failed_tests[i].error = f"{err_type}: {message.strip()}"

    # Match file:line from tracebacks
    traceback_pattern = re.compile(
        r"([\w/\\.-]+\.py):(\d+):\s+(.*Error|.*Exception)"
    )
    for match in traceback_pattern.finditer(output):
        filepath = match.group(1).replace("\\", "/")
        line = int(match.group(2))
        error_msg = match.group(3)
        if filepath not in failure.relevant_files:
            failure.relevant_files.append(filepath)
        # Update test entries with line numbers
        for t in failure.failed_tests:
            if t.file.endswith(filepath.split("/")[-1]) and t.line == 0:
                t.line = line
                if not t.error:
                    t.error = error_msg

    # Detect syntax errors
    if "SyntaxError" in output:
        failure.error_category = "syntax_error"
    elif "ImportError" in output or "ModuleNotFoundError" in output:
        failure.error_category = "import_error"
    elif "TypeError" in output:
        failure.error_category = "type_error"

    # Check recoverability
    failure.security_blocked = _is_security_blocked(output)
    failure.recoverable = not _is_non_recoverable(output)

    return failure


# ── Ruff Output Parser ────────────────────────────────────────────────

def parse_ruff_output(output: str) -> ParsedFailure:
    """Parse ruff lint output into structured failure."""
    failure = ParsedFailure(
        phase="lint",
        raw_output=output,
        error_category="lint_violation",
    )

    # Match ruff lines: path/file.py:10:5: E302 expected 2 blank lines
    ruff_pattern = re.compile(
        r"([\w/\\.-]+\.py):(\d+):\d+:\s+(\w+)\s+(.*)"
    )
    for match in ruff_pattern.finditer(output):
        filepath = match.group(1).replace("\\", "/")
        line = int(match.group(2))
        rule = match.group(3)
        message = match.group(4)
        failure.failed_checks.append(FailedCheck(
            tool="ruff",
            rule=rule,
            file=filepath,
            line=line,
            message=message,
        ))
        if filepath not in failure.relevant_files:
            failure.relevant_files.append(filepath)

    failure.recoverable = True  # Lint violations are always recoverable
    return failure


# ── Bandit Output Parser ──────────────────────────────────────────────

def parse_bandit_output(output: str) -> ParsedFailure:
    """Parse bandit SAST output into structured failure."""
    failure = ParsedFailure(
        phase="security",
        raw_output=output,
        error_category="security_finding",
    )

    # Match bandit lines: >> Issue: [B602:subprocess_popen_with_shell_equals_true]
    issue_pattern = re.compile(
        r">> Issue: \[(B\d+):(\w+)\]\s*(.*)"
    )
    location_pattern = re.compile(
        r"Location:\s*([\w/\\.-]+):(\d+)"
    )
    severity_pattern = re.compile(
        r"Severity:\s*(Low|Medium|High)"
    )

    issues = issue_pattern.finditer(output)
    locations = location_pattern.finditer(output)
    severities = severity_pattern.finditer(output)

    issue_list = list(issues)
    location_list = list(locations)
    severity_list = list(severities)

    for i, issue in enumerate(issue_list):
        rule = issue.group(1)
        name = issue.group(2)
        description = issue.group(3)

        filepath = ""
        line = 0
        severity = ""
        if i < len(location_list):
            filepath = location_list[i].group(1).replace("\\", "/")
            line = int(location_list[i].group(2))
        if i < len(severity_list):
            severity = severity_list[i].group(1)

        failure.failed_checks.append(FailedCheck(
            tool="bandit",
            rule=f"{rule}:{name}",
            file=filepath,
            line=line,
            message=f"[{severity}] {description}" if severity else description,
        ))
        if filepath and filepath not in failure.relevant_files:
            failure.relevant_files.append(filepath)

    # High severity bandit findings block recovery
    high_findings = [c for c in failure.failed_checks if "High" in c.message]
    failure.security_blocked = len(high_findings) > 0
    failure.recoverable = not failure.security_blocked

    return failure


# ── Behave Output Parser ──────────────────────────────────────────────

def parse_behave_output(output: str) -> ParsedFailure:
    """Parse behave BDD output into structured failure."""
    failure = ParsedFailure(
        phase="bdd",
        raw_output=output,
        error_category="test_assertion",
    )

    # Match failing scenarios: Failing scenarios:
    #   features/foo.feature:10  Scenario name
    scenario_pattern = re.compile(
        r"(features/[\w/\\.-]+\.feature):(\d+)\s+(.*)"
    )

    in_failing = False
    for line in output.splitlines():
        if "Failing scenarios:" in line:
            in_failing = True
            continue
        if in_failing:
            match = scenario_pattern.search(line)
            if match:
                filepath = match.group(1).replace("\\", "/")
                line_num = int(match.group(2))
                scenario = match.group(3).strip()
                failure.failed_tests.append(FailedTest(
                    name=scenario,
                    file=filepath,
                    line=line_num,
                ))
                if filepath not in failure.relevant_files:
                    failure.relevant_files.append(filepath)
            elif line.strip() == "" or not line.startswith(" "):
                in_failing = False

    failure.recoverable = not _is_non_recoverable(output)
    return failure


# ── Compile Error Parser ──────────────────────────────────────────────

def parse_compile_output(output: str) -> ParsedFailure:
    """Parse py_compile / syntax error output."""
    failure = ParsedFailure(
        phase="compile",
        raw_output=output,
        error_category="syntax_error",
    )

    # Match: File "path/file.py", line 10
    compile_pattern = re.compile(
        r'File "([^"]+)", line (\d+)'
    )
    for match in compile_pattern.finditer(output):
        filepath = match.group(1).replace("\\", "/")
        line = int(match.group(2))
        failure.failed_checks.append(FailedCheck(
            tool="py_compile",
            rule="SyntaxError",
            file=filepath,
            line=line,
            message="Syntax error",
        ))
        if filepath not in failure.relevant_files:
            failure.relevant_files.append(filepath)

    failure.recoverable = True
    return failure


# ── Unified Parser ────────────────────────────────────────────────────

PARSERS = {
    "test": parse_pytest_output,
    "pytest": parse_pytest_output,
    "lint": parse_ruff_output,
    "ruff": parse_ruff_output,
    "security": parse_bandit_output,
    "bandit": parse_bandit_output,
    "sast": parse_bandit_output,
    "bdd": parse_behave_output,
    "behave": parse_behave_output,
    "compile": parse_compile_output,
    "py_compile": parse_compile_output,
}


def parse_failure_output(phase: str, output: str) -> ParsedFailure:
    """Parse failure output from any supported tool.

    Args:
        phase: Phase name or tool name (test, lint, security, bdd, compile)
        output: Raw tool output text

    Returns:
        ParsedFailure with structured failure information
    """
    parser = PARSERS.get(phase.lower())
    if parser:
        return parser(output)

    # Fallback: generic failure
    failure = ParsedFailure(
        phase=phase,
        raw_output=output,
        error_category="unknown",
    )
    failure.security_blocked = _is_security_blocked(output)
    failure.recoverable = not _is_non_recoverable(output)
    return failure
