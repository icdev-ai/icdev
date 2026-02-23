# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools.requirements.spec_quality_checker import (
    parse_spec_sections,
    check_required_sections,
    check_ambiguity,
    check_acceptance_criteria,
    check_ato_coverage,
    check_testability,
    check_task_completeness,
    run_all_checks,
    annotate_spec,
    strip_markers,
    count_markers,
)


# ---------------------------------------------------------------------------
# Sample spec constants
# ---------------------------------------------------------------------------

SAMPLE_SPEC = """\
# CUI // SP-CTI
# Feature: Test Feature

## Metadata
issue_number: `99`
run_id: `test123`

## Feature Description
This is a test feature that does something important for the system. It provides
value by doing X, Y, and Z for the users. The implementation covers multiple
components and integrates with existing backend services to deliver reliable
functionality across the entire platform.

## User Story
As a developer
I want to test the spec quality checker
So that I can ensure specs meet quality standards

## Solution Statement
Implement the test feature by modifying components A, B, and C. The solution will
handle edge cases and integrate with existing systems. It uses standard patterns
from the ICDEV framework. The architecture follows a layered approach with clear
separation of concerns between the presentation, business logic, and data access
layers. Error handling covers all known failure modes.

## ATO Impact Assessment
- **Boundary Impact**: GREEN
- **New NIST Controls**: None
- **SSP Impact**: None
- **Data Classification Change**: No

## Relevant Files
- `tools/test/example.py` -- Main implementation

## Implementation Plan
### Phase 1: Foundation
- Set up base components

### Phase 2: Core
- Implement main logic

## Step by Step Tasks
### Step 1: Foundation setup
- Set up base components

### Step 2: Core implementation
- Implement main logic

## Testing Strategy
### Unit Tests
- Test the main function
- Test edge cases

### BDD Tests
- Scenario: User performs action

## Acceptance Criteria
- Feature loads without errors
- Feature displays correct data
- Feature handles edge cases gracefully
- Feature passes all unit tests

## Validation Commands
- `python -m py_compile tools/test/example.py`
- `python -m pytest tests/ -v`

## NIST 800-53 Controls
- AC-3 (Access Enforcement)
- AU-2 (Event Logging)

# CUI // SP-CTI
"""

INCOMPLETE_SPEC = """\
# CUI // SP-CTI
# Feature: Incomplete Feature

## Metadata
issue_number: `100`

## Feature Description
Short.

# CUI // SP-CTI
"""

AMBIGUOUS_SPEC = """\
# CUI // SP-CTI
# Feature: Ambiguous Feature

## Metadata
issue_number: `101`
run_id: `amb456`

## Feature Description
This feature should provide a timely and appropriate response to user actions.
The system must be fast, user-friendly, and scalable. It needs to handle things
in a reasonable manner and be robust enough for production use. We also need
adequate logging and flexible configuration, etc.

## User Story
As a user
I want to do things appropriately
So that the system is efficient

## Solution Statement
The solution will be implemented in a timely fashion using appropriate techniques
that are scalable, robust, and efficient. The architecture should be flexible
enough to handle future requirements. We will use reasonable defaults and
adequate error handling throughout the implementation layers and service tiers.

## ATO Impact Assessment
- **Boundary Impact**: YELLOW
- **New NIST Controls**: AC-2
- **SSP Impact**: SSP addendum required
- **Data Classification Change**: No

## Relevant Files
- `tools/ambiguous/example.py` -- Main file

## Implementation Plan
### Phase 1: Setup
- Configure environment

## Step by Step Tasks
### Step 1: Setup tasks
- Configure environment

## Testing Strategy
### Unit Tests
- Test main flow

## Acceptance Criteria
- System works well
- Things look good
- Performance is adequate
- Feature displays the dashboard correctly

## Validation Commands
- `python -m pytest tests/ -v`

## NIST 800-53 Controls
- AC-2 (Account Management)

# CUI // SP-CTI
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_spec(tmp_path: Path, content: str, filename: str = "spec.md") -> Path:
    """Write spec content to a .md file and return the Path."""
    spec_path = tmp_path / filename
    spec_path.write_text(content, encoding="utf-8")
    return spec_path


# ---------------------------------------------------------------------------
# Tests: parse_spec_sections
# ---------------------------------------------------------------------------

class TestParseSpecSections:
    """Verify markdown parsing splits by ## headers."""

    def test_parse_valid_spec_returns_all_sections(self, tmp_path):
        spec_path = _write_spec(tmp_path, SAMPLE_SPEC)
        sections = parse_spec_sections(spec_path)
        expected_keys = {
            "metadata", "feature description", "user story",
            "solution statement", "ato impact assessment",
            "relevant files", "implementation plan",
            "step by step tasks", "testing strategy",
            "acceptance criteria", "validation commands",
            "nist 800-53 controls",
        }
        assert expected_keys.issubset(set(sections.keys()))

    def test_parse_spec_with_no_sections_returns_preamble(self, tmp_path):
        content = "Just a plain text file with no ## headers."
        spec_path = _write_spec(tmp_path, content)
        sections = parse_spec_sections(spec_path)
        assert "_preamble" in sections
        # No other sections should exist
        non_preamble = {k for k in sections if k != "_preamble"}
        assert len(non_preamble) == 0


# ---------------------------------------------------------------------------
# Tests: check_required_sections
# ---------------------------------------------------------------------------

class TestCheckRequiredSections:
    """Verify required section validation."""

    def test_all_present_all_pass(self, tmp_path):
        spec_path = _write_spec(tmp_path, SAMPLE_SPEC)
        sections = parse_spec_sections(spec_path)
        checklist = {
            "required_sections": [
                {"name": "Feature Description", "severity": "critical", "min_words": 20},
                {"name": "User Story", "severity": "critical"},
                {"name": "Acceptance Criteria", "severity": "critical", "min_items": 3},
            ]
        }
        results = check_required_sections(sections, checklist)
        statuses = [r.status for r in results]
        assert all(s == "pass" for s in statuses)

    def test_missing_critical_section_fail(self, tmp_path):
        spec_path = _write_spec(tmp_path, INCOMPLETE_SPEC)
        sections = parse_spec_sections(spec_path)
        checklist = {
            "required_sections": [
                {"name": "Acceptance Criteria", "severity": "critical"},
            ]
        }
        results = check_required_sections(sections, checklist)
        assert any(r.status == "fail" for r in results)

    def test_section_below_min_words_fail(self, tmp_path):
        spec_path = _write_spec(tmp_path, INCOMPLETE_SPEC)
        sections = parse_spec_sections(spec_path)
        checklist = {
            "required_sections": [
                {"name": "Feature Description", "severity": "critical", "min_words": 20},
            ]
        }
        results = check_required_sections(sections, checklist)
        assert any(r.status == "fail" for r in results)


# ---------------------------------------------------------------------------
# Tests: check_ambiguity
# ---------------------------------------------------------------------------

class TestCheckAmbiguity:
    """Verify ambiguity detection."""

    def test_no_ambiguous_phrases_empty_results(self, tmp_path):
        spec_path = _write_spec(tmp_path, SAMPLE_SPEC)
        sections = parse_spec_sections(spec_path)
        # Use only patterns that do not appear in the sample spec
        patterns = [
            {"phrase": "xyzzy-nonexistent", "severity": "high", "clarification": "Remove it."},
        ]
        results = check_ambiguity(sections, patterns)
        assert len(results) == 0

    def test_timely_and_appropriate_detected(self, tmp_path):
        spec_path = _write_spec(tmp_path, AMBIGUOUS_SPEC)
        sections = parse_spec_sections(spec_path)
        patterns = [
            {"phrase": "timely", "severity": "high", "clarification": "Specify a threshold."},
            {"phrase": "appropriate", "severity": "high", "clarification": "Define criteria."},
        ]
        results = check_ambiguity(sections, patterns)
        names = [r.name for r in results]
        assert any("timely" in n for n in names)
        assert any("appropriate" in n for n in names)
        assert all(r.status == "fail" for r in results)


# ---------------------------------------------------------------------------
# Tests: check_acceptance_criteria
# ---------------------------------------------------------------------------

class TestCheckAcceptanceCriteria:
    """Verify acceptance criteria validation."""

    def test_five_testable_items_pass(self, tmp_path):
        content = """\
## Acceptance Criteria
- Feature loads without errors
- Feature displays correct data
- Feature handles edge cases gracefully
- Feature returns valid JSON response
- Feature shows success notification
"""
        spec_path = _write_spec(tmp_path, content)
        sections = parse_spec_sections(spec_path)
        results = check_acceptance_criteria(sections)
        count_result = [r for r in results if "count" in r.name]
        assert count_result[0].status == "pass"

    def test_only_two_items_fail(self, tmp_path):
        content = """\
## Acceptance Criteria
- Feature loads without errors
- Feature displays correct data
"""
        spec_path = _write_spec(tmp_path, content)
        sections = parse_spec_sections(spec_path)
        results = check_acceptance_criteria(sections)
        count_result = [r for r in results if "count" in r.name]
        assert count_result[0].status == "fail"

    def test_vague_items_testability_fail(self, tmp_path):
        content = """\
## Acceptance Criteria
- System works well
- Things look good
- Performance is adequate
"""
        spec_path = _write_spec(tmp_path, content)
        sections = parse_spec_sections(spec_path)
        results = check_acceptance_criteria(sections)
        testability_results = [r for r in results if "testability" in r.name]
        assert any(r.status == "fail" for r in testability_results)


# ---------------------------------------------------------------------------
# Tests: check_ato_coverage
# ---------------------------------------------------------------------------

class TestCheckAtoCoverage:
    """Verify ATO impact assessment validation."""

    def test_green_tier_pass(self, tmp_path):
        spec_path = _write_spec(tmp_path, SAMPLE_SPEC)
        sections = parse_spec_sections(spec_path)
        results = check_ato_coverage(sections)
        tier_results = [r for r in results if "boundary tier" in r.name]
        assert tier_results[0].status == "pass"
        assert "GREEN" in tier_results[0].message

    def test_no_boundary_tier_fail(self, tmp_path):
        content = """\
## ATO Impact Assessment
- **New NIST Controls**: None
- **SSP Impact**: None
"""
        spec_path = _write_spec(tmp_path, content)
        sections = parse_spec_sections(spec_path)
        results = check_ato_coverage(sections)
        tier_results = [r for r in results if "boundary tier" in r.name]
        assert tier_results[0].status == "fail"


# ---------------------------------------------------------------------------
# Tests: check_testability
# ---------------------------------------------------------------------------

class TestCheckTestability:
    """Verify testing strategy presence checks."""

    def test_testing_strategy_present_pass(self, tmp_path):
        spec_path = _write_spec(tmp_path, SAMPLE_SPEC)
        sections = parse_spec_sections(spec_path)
        results = check_testability(sections)
        strategy_results = [r for r in results if "testing strategy" in r.name]
        assert strategy_results[0].status == "pass"


# ---------------------------------------------------------------------------
# Tests: check_task_completeness
# ---------------------------------------------------------------------------

class TestCheckTaskCompleteness:
    """Verify implementation phases match tasks."""

    def test_phases_covered_pass(self, tmp_path):
        spec_path = _write_spec(tmp_path, SAMPLE_SPEC)
        sections = parse_spec_sections(spec_path)
        results = check_task_completeness(sections)
        assert any(r.status == "pass" for r in results)

    def test_missing_phase_fail(self, tmp_path):
        content = """\
## Implementation Plan
### Phase 1: Foundation
- Set up base components

### Phase 2: Core
- Implement main logic

### Phase 3: Integration
- Connect all subsystems

## Step by Step Tasks
### Step 1: Foundation setup
- Set up base components

### Step 2: Core implementation
- Implement main logic
"""
        spec_path = _write_spec(tmp_path, content)
        sections = parse_spec_sections(spec_path)
        results = check_task_completeness(sections)
        # Phase 3: Integration is not covered in tasks
        assert any(r.status == "fail" for r in results)


# ---------------------------------------------------------------------------
# Tests: run_all_checks
# ---------------------------------------------------------------------------

class TestRunAllChecks:
    """Verify the full orchestrator."""

    def test_valid_spec_score_above_50(self, tmp_path):
        spec_path = _write_spec(tmp_path, SAMPLE_SPEC)
        result = run_all_checks(spec_path)
        assert result["status"] == "ok"
        assert result["quality_score"] >= 50

    def test_empty_spec_score_near_zero(self, tmp_path):
        content = "# CUI // SP-CTI\nNothing here.\n# CUI // SP-CTI\n"
        spec_path = _write_spec(tmp_path, content)
        result = run_all_checks(spec_path)
        assert result["status"] == "ok"
        assert result["quality_score"] <= 50

    def test_nonexistent_spec_returns_error(self, tmp_path):
        fake_path = tmp_path / "does_not_exist.md"
        result = run_all_checks(fake_path)
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Tests: annotate_spec / strip_markers / count_markers
# ---------------------------------------------------------------------------

class TestAnnotationHelpers:
    """Verify inline annotation markers."""

    def test_annotate_inserts_max_markers(self, tmp_path):
        spec_path = _write_spec(tmp_path, INCOMPLETE_SPEC)
        # Build fake check results with multiple critical failures
        check_results = [
            {
                "check_id": "sec-001",
                "name": "Section: Acceptance Criteria",
                "status": "fail",
                "severity": "critical",
                "message": "Required section 'Acceptance Criteria' is missing.",
                "suggestion": "Add it.",
                "section": "acceptance criteria",
            },
            {
                "check_id": "sec-002",
                "name": "Section: Testing Strategy",
                "status": "fail",
                "severity": "high",
                "message": "Required section 'Testing Strategy' is missing.",
                "suggestion": "Add it.",
                "section": "testing strategy",
            },
            {
                "check_id": "sec-003",
                "name": "Section: Solution Statement",
                "status": "fail",
                "severity": "critical",
                "message": "Required section 'Solution Statement' is missing.",
                "suggestion": "Add it.",
                "section": "solution statement",
            },
            {
                "check_id": "sec-004",
                "name": "Section: Implementation Plan",
                "status": "fail",
                "severity": "high",
                "message": "Required section 'Implementation Plan' is missing.",
                "suggestion": "Add it.",
                "section": "implementation plan",
            },
        ]
        annotated = annotate_spec(spec_path, check_results, max_markers=3)
        marker_count = annotated.count("[NEEDS CLARIFICATION:")
        assert marker_count <= 3

    def test_strip_markers_removes_all(self, tmp_path):
        content = (
            "# CUI // SP-CTI\n"
            "## Feature Description\n"
            "[NEEDS CLARIFICATION: sec-001 -- Missing section.]\n"
            "Some text here.\n"
            "[NEEDS CLARIFICATION: sec-002 -- Another issue.]\n"
            "# CUI // SP-CTI\n"
        )
        spec_path = _write_spec(tmp_path, content)
        cleaned = strip_markers(spec_path)
        assert "[NEEDS CLARIFICATION:" not in cleaned

    def test_count_markers_correct(self, tmp_path):
        content = (
            "# CUI // SP-CTI\n"
            "[NEEDS CLARIFICATION: sec-001 -- Issue A.]\n"
            "Some text.\n"
            "[NEEDS CLARIFICATION: sec-002 -- Issue B.]\n"
            "[NEEDS CLARIFICATION: sec-003 -- Issue C.]\n"
            "# CUI // SP-CTI\n"
        )
        spec_path = _write_spec(tmp_path, content)
        assert count_markers(spec_path) == 3
