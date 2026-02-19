# CUI // SP-CTI
# ICDEV Acceptance Criteria Validator (V&V)
# Deterministic validation: plan criteria → test evidence + DOM content checks

"""
Acceptance Criteria Validator — validates that what was built matches what was required.

This is the "did we build the right thing?" gate. It:
1. Parses the plan's ## Acceptance Criteria section
2. Maps each criterion to available test evidence (pytest, BDD, E2E)
3. Checks rendered pages for error patterns (500s, tracebacks, JS errors)
4. Produces a gate-compatible AcceptanceReport

No LLM — pure deterministic string matching and HTTP content checks.

Usage:
    python tools/testing/acceptance_validator.py \
        --plan specs/issue-3-icdev-abc-plan.md \
        --test-results .tmp/test_runs/<run_id>/state.json \
        --base-url http://localhost:5000 \
        --pages / /agents /events /query /diagrams /monitoring \
        --json

Gate (per security_gates.yaml acceptance_validation):
    - 0 failed criteria
    - 0 pages with error patterns
    - Plan must have ## Acceptance Criteria section
"""

import argparse
import json
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.testing.data_types import (
    AcceptanceCriterionResult,
    UIPageCheckResult,
    AcceptanceReport,
)
from tools.testing.utils import timestamp_iso

# --- DOM Error Patterns (deterministic, no LLM) ---
# These patterns indicate a page is rendering with errors.
# Matched case-insensitively against page HTML content.

DOM_ERROR_PATTERNS = [
    "Internal Server Error",
    "500 Internal Server Error",
    "Traceback (most recent call last)",
    "jinja2.exceptions",
    "TemplateNotFound",
    "ModuleNotFoundError",
    "ImportError",
    "SyntaxError",
    "NameError",
    "TypeError",
    "AttributeError",
    "ReferenceError",
    "is not defined",
    "Cannot read propert",
    "404 Not Found",
    "Server Error",
]


def parse_acceptance_criteria(plan_path: str) -> List[str]:
    """Extract acceptance criteria from plan's ## Acceptance Criteria section.

    Looks for a markdown section starting with '## Acceptance Criteria'
    and extracts each line that starts with '- ' or a numbered list item.

    Returns:
        List of criterion strings, empty if section not found.
    """
    try:
        text = Path(plan_path).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return []

    # Find ## Acceptance Criteria section
    pattern = r"##\s+Acceptance\s+Criteria\s*\n(.*?)(?=\n##\s|\Z)"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if not match:
        return []

    section = match.group(1)
    criteria = []
    for line in section.splitlines():
        line = line.strip()
        # Match bullet points: - criterion
        if line.startswith("- ") or line.startswith("* "):
            criteria.append(line[2:].strip())
        # Match numbered list: 1. criterion, 2) criterion
        elif re.match(r"^\d+[.)]\s+", line):
            criteria.append(re.sub(r"^\d+[.)]\s+", "", line).strip())
        # Match checkbox: [ ] criterion, [x] criterion
        elif re.match(r"^\[[ x]\]\s+", line, re.IGNORECASE):
            criteria.append(re.sub(r"^\[[ x]\]\s+", "", line, flags=re.IGNORECASE).strip())
    return [c for c in criteria if c]


def map_criteria_to_evidence(
    criteria: List[str],
    test_state: Optional[dict],
) -> List[AcceptanceCriterionResult]:
    """Map each acceptance criterion to available test evidence.

    Uses keyword matching to correlate criteria to test results.
    """
    results = []
    for criterion in criteria:
        result = AcceptanceCriterionResult(criterion=criterion)
        criterion_lower = criterion.lower()

        if test_state:
            # Check unit test evidence
            unit_passed = test_state.get("unit_passed", 0)
            unit_failed = test_state.get("unit_failed", 0)
            if unit_passed > 0 and unit_failed == 0:
                # Test-related criteria
                test_keywords = ["test", "pass", "unit", "pytest", "coverage"]
                if any(kw in criterion_lower for kw in test_keywords):
                    result.status = "verified"
                    result.evidence_type = "unit_test"
                    result.evidence_detail = f"{unit_passed} unit tests passed, {unit_failed} failed"

            # Check BDD evidence
            bdd_passed = test_state.get("bdd_passed", 0)
            if bdd_passed > 0:
                bdd_keywords = ["bdd", "behavior", "scenario", "feature", "gherkin", "behave"]
                if any(kw in criterion_lower for kw in bdd_keywords):
                    result.status = "verified"
                    result.evidence_type = "bdd_test"
                    result.evidence_detail = f"{bdd_passed} BDD scenarios passed"

            # Check E2E evidence
            e2e_passed = test_state.get("e2e_passed", 0)
            if e2e_passed > 0:
                e2e_keywords = ["e2e", "browser", "ui", "render", "display", "page", "dashboard", "click"]
                if any(kw in criterion_lower for kw in e2e_keywords):
                    result.status = "verified"
                    result.evidence_type = "e2e_test"
                    result.evidence_detail = f"{e2e_passed} E2E tests passed"

            # Check security gate evidence
            if test_state.get("security_gate_passed") is True:
                security_keywords = ["security", "sast", "vulnerability", "secret", "scan"]
                if any(kw in criterion_lower for kw in security_keywords):
                    result.status = "verified"
                    result.evidence_type = "unit_test"
                    result.evidence_detail = "Security gate passed"

            # Check compliance gate evidence
            if test_state.get("compliance_gate_passed") is True:
                compliance_keywords = ["compliance", "nist", "stig", "cui", "poam", "ssp", "ato"]
                if any(kw in criterion_lower for kw in compliance_keywords):
                    result.status = "verified"
                    result.evidence_type = "unit_test"
                    result.evidence_detail = "Compliance gate passed"

        results.append(result)
    return results


def check_page(base_url: str, page_path: str, timeout: int = 10) -> UIPageCheckResult:
    """Fetch a page and check DOM content for error patterns.

    Uses urllib (stdlib) — no external dependencies.
    """
    url = base_url.rstrip("/") + "/" + page_path.lstrip("/")
    result = UIPageCheckResult(url=url)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ICDEV-AcceptanceValidator/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result.status_code = resp.status
            html = resp.read().decode("utf-8", errors="replace")
            result.content_length = len(html)

            # Check for error patterns (case-insensitive)
            html_lower = html.lower()
            for pattern in DOM_ERROR_PATTERNS:
                if pattern.lower() in html_lower:
                    result.error_patterns_found.append(pattern)
                    result.has_errors = True

    except urllib.error.HTTPError as e:
        result.status_code = e.code
        result.has_errors = True
        result.error_patterns_found.append(f"HTTP {e.code}: {e.reason}")
        # Also check the error page body for patterns
        try:
            body = e.read().decode("utf-8", errors="replace")
            result.content_length = len(body)
            body_lower = body.lower()
            for pattern in DOM_ERROR_PATTERNS:
                if pattern.lower() in body_lower and pattern not in result.error_patterns_found:
                    result.error_patterns_found.append(pattern)
        except Exception:
            pass

    except urllib.error.URLError as e:
        result.has_errors = True
        result.error_patterns_found.append(f"Connection error: {e.reason}")

    except Exception as e:
        result.has_errors = True
        result.error_patterns_found.append(f"Unexpected error: {e}")

    return result


def validate_acceptance(
    plan_path: Optional[str] = None,
    test_results_path: Optional[str] = None,
    base_url: Optional[str] = None,
    pages: Optional[List[str]] = None,
) -> AcceptanceReport:
    """Run full acceptance validation and return report.

    Args:
        plan_path: Path to plan file with ## Acceptance Criteria section
        test_results_path: Path to test run state JSON
        base_url: Base URL for page content checks
        pages: List of page paths to check (e.g., ["/", "/agents"])

    Returns:
        AcceptanceReport with gate results
    """
    report = AcceptanceReport(
        plan_file=plan_path or "",
        timestamp=timestamp_iso(),
    )

    # 1. Parse acceptance criteria from plan
    criteria = []
    if plan_path:
        criteria = parse_acceptance_criteria(plan_path)
        report.criteria_count = len(criteria)

    # 2. Load test evidence
    test_state = None
    if test_results_path:
        try:
            test_state = json.loads(Path(test_results_path).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    # 3. Map criteria to evidence
    if criteria:
        report.criteria = map_criteria_to_evidence(criteria, test_state)
        report.criteria_verified = sum(1 for c in report.criteria if c.status == "verified")
        report.criteria_failed = sum(1 for c in report.criteria if c.status == "failed")
        report.criteria_unverified = sum(1 for c in report.criteria if c.status == "unverified")

    # 4. Check pages for error content
    if base_url and pages:
        for page_path in pages:
            page_result = check_page(base_url, page_path)
            report.page_checks.append(page_result)
        report.pages_checked = len(report.page_checks)
        report.pages_with_errors = sum(1 for p in report.page_checks if p.has_errors)

    # 5. Evaluate gate
    blocking = []
    warnings = []

    if plan_path and report.criteria_count == 0:
        blocking.append("plan_has_no_acceptance_criteria")

    if report.criteria_failed > 0:
        blocking.append(f"acceptance_criteria_failed: {report.criteria_failed} failed")

    if report.pages_with_errors > 0:
        blocking.append(
            f"ui_page_renders_with_error: {report.pages_with_errors} page(s) "
            f"with errors"
        )

    if report.criteria_unverified > 0:
        warnings.append(
            f"acceptance_criteria_unverified: {report.criteria_unverified} "
            f"criteria could not be mapped to test evidence"
        )

    for pc in report.page_checks:
        if pc.content_length == 0 and not pc.has_errors:
            warnings.append(f"page_content_empty: {pc.url}")

    report.overall_pass = len(blocking) == 0

    return report


def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Acceptance Criteria Validator (V&V)"
    )
    parser.add_argument(
        "--plan",
        help="Path to plan file with ## Acceptance Criteria section",
    )
    parser.add_argument(
        "--test-results",
        help="Path to test run state JSON (.tmp/test_runs/<id>/state.json)",
    )
    parser.add_argument(
        "--base-url",
        help="Base URL for page content checks (e.g., http://localhost:5000)",
    )
    parser.add_argument(
        "--pages",
        nargs="*",
        help="Page paths to check (e.g., / /agents /events)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="HTTP request timeout in seconds (default: 10)",
    )
    args = parser.parse_args()

    report = validate_acceptance(
        plan_path=args.plan,
        test_results_path=args.test_results,
        base_url=args.base_url,
        pages=args.pages,
    )

    if args.json:
        output = {
            "plan_file": report.plan_file,
            "criteria_count": report.criteria_count,
            "criteria_verified": report.criteria_verified,
            "criteria_failed": report.criteria_failed,
            "criteria_unverified": report.criteria_unverified,
            "pages_checked": report.pages_checked,
            "pages_with_errors": report.pages_with_errors,
            "overall_pass": report.overall_pass,
            "timestamp": report.timestamp,
            "criteria": [
                {
                    "criterion": c.criterion,
                    "status": c.status,
                    "evidence_type": c.evidence_type,
                    "evidence_detail": c.evidence_detail,
                }
                for c in report.criteria
            ],
            "page_checks": [
                {
                    "url": p.url,
                    "status_code": p.status_code,
                    "has_errors": p.has_errors,
                    "error_patterns_found": p.error_patterns_found,
                    "content_length": p.content_length,
                }
                for p in report.page_checks
            ],
        }
        print(json.dumps(output, indent=2))
    else:
        # Human-readable output
        print("Acceptance Validation Report")
        print(f"{'=' * 50}")
        print(f"Plan: {report.plan_file}")
        print(f"Time: {report.timestamp}")
        print()

        if report.criteria:
            print(f"Acceptance Criteria ({report.criteria_count}):")
            for c in report.criteria:
                icon = {"verified": "PASS", "failed": "FAIL", "unverified": "???"}
                print(f"  [{icon.get(c.status, '???')}] {c.criterion}")
                if c.evidence_detail:
                    print(f"         Evidence: {c.evidence_detail}")
            print()

        if report.page_checks:
            print(f"Page Content Checks ({report.pages_checked}):")
            for p in report.page_checks:
                icon = "FAIL" if p.has_errors else "PASS"
                print(f"  [{icon}] {p.url} (HTTP {p.status_code}, {p.content_length} bytes)")
                for err in p.error_patterns_found:
                    print(f"         Error: {err}")
            print()

        print("Summary:")
        print(f"  Criteria: {report.criteria_verified} verified, {report.criteria_failed} failed, {report.criteria_unverified} unverified")
        print(f"  Pages: {report.pages_checked} checked, {report.pages_with_errors} with errors")
        print(f"  Gate: {'PASS' if report.overall_pass else 'FAIL'}")

    sys.exit(0 if report.overall_pass else 1)


if __name__ == "__main__":
    main()
