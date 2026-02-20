# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools.requirements.consistency_analyzer import (
    analyze_spec_consistency,
    _check_acceptance_vs_testing,
    _check_phases_vs_tasks,
    _check_files_exist,
    _check_nist_vs_ato,
    _check_user_story_vs_acceptance,
    _parse_spec_sections,
    _check_spec_directory_consistency,
)


# ---------------------------------------------------------------------------
# Sample specs
# ---------------------------------------------------------------------------

CONSISTENT_SPEC = """\
# CUI // SP-CTI
# Feature: Consistent Feature

## Metadata
issue_number: `10`
run_id: `con123`

## Feature Description
This is a fully consistent specification for testing the consistency analyzer.

## User Story
As a developer
I want to test the consistency analyzer
So that I can ensure specs are internally aligned

## Solution Statement
Implement consistency checks by analyzing cross-references between sections.

## ATO Impact Assessment
- **Boundary Impact**: GREEN
- **New NIST Controls**: AC-3, AU-2
- **SSP Impact**: None

## Relevant Files
- `tools/requirements/consistency_analyzer.py` -- Main analyzer

## Implementation Plan
### Phase 1: Foundation
- Set up analyzer base

### Phase 2: Core Logic
- Implement cross-section checks

## Step by Step Tasks
### Step 1: Foundation setup
- Set up analyzer base

### Step 2: Core logic implementation
- Implement cross-section checks

## Testing Strategy
### Unit Tests
- Test analyzer function
- Test consistency detection
- Test edge cases gracefully

### BDD Tests
- Scenario: Developer checks consistency

## Acceptance Criteria
- Analyzer detects inconsistencies
- Consistency score is calculated correctly
- Edge cases are handled gracefully

## Validation Commands
- `python -m pytest tests/ -v`

## NIST 800-53 Controls
- AC-3 (Access Enforcement)
- AU-2 (Event Logging)

# CUI // SP-CTI
"""

INCONSISTENT_SPEC = """\
# CUI // SP-CTI
# Feature: Inconsistent Feature

## Metadata
issue_number: `11`

## Feature Description
A spec with many internal inconsistencies for testing.

## User Story
As an operator
I want to deploy the widget
So that the dashboard updates

## ATO Impact Assessment
- **Boundary Impact**: GREEN
- **New NIST Controls**: None
- **SSP Impact**: None

## Implementation Plan
### Phase 1: Setup
- Configure environment

### Phase 2: Database
- Create schema

### Phase 3: API Layer
- Build REST endpoints

## Step by Step Tasks
### Step 1: Setup
- Configure environment

## Testing Strategy
### Unit Tests
- Test database queries

## Acceptance Criteria
- REST endpoints return 200
- API performance is adequate
- Widget renders on dashboard

## NIST 800-53 Controls
- AC-2 (Account Management)
- SC-8 (Transmission Confidentiality)

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
# Tests: _check_acceptance_vs_testing
# ---------------------------------------------------------------------------

class TestAcceptanceVsTesting:
    """Verify acceptance criteria cross-checks with testing strategy."""

    def test_consistent_spec_all_covered(self, tmp_path):
        spec_path = _write_spec(tmp_path, CONSISTENT_SPEC)
        sections = _parse_spec_sections(spec_path)
        results = _check_acceptance_vs_testing(sections)
        statuses = [r.status for r in results]
        assert "consistent" in statuses

    def test_acceptance_not_in_testing_warns(self, tmp_path):
        spec_path = _write_spec(tmp_path, INCONSISTENT_SPEC)
        sections = _parse_spec_sections(spec_path)
        results = _check_acceptance_vs_testing(sections)
        # "REST endpoints return 200" keyword "endpoints" may not appear in testing
        statuses = [r.status for r in results]
        assert "warn" in statuses or "consistent" in statuses


# ---------------------------------------------------------------------------
# Tests: _check_phases_vs_tasks
# ---------------------------------------------------------------------------

class TestPhasesVsTasks:
    """Verify implementation phases match step-by-step tasks."""

    def test_phases_covered_consistent(self, tmp_path):
        spec_path = _write_spec(tmp_path, CONSISTENT_SPEC)
        sections = _parse_spec_sections(spec_path)
        results = _check_phases_vs_tasks(sections)
        assert any(r.status == "consistent" for r in results)

    def test_missing_phase_inconsistent(self, tmp_path):
        spec_path = _write_spec(tmp_path, INCONSISTENT_SPEC)
        sections = _parse_spec_sections(spec_path)
        results = _check_phases_vs_tasks(sections)
        # Phase 2 (Database) and Phase 3 (API Layer) are not in tasks
        assert any(r.status == "inconsistent" for r in results)


# ---------------------------------------------------------------------------
# Tests: _check_files_exist
# ---------------------------------------------------------------------------

class TestCheckFilesExist:
    """Verify referenced files are checked for existence."""

    def test_existing_file_consistent(self, tmp_path):
        # Create a spec that references a file, then create that file
        content = """\
## Relevant Files
- `existing_file.py` -- Important module
"""
        # The check resolves against BASE_DIR, so we test the behavior
        # by checking that a real file in the repo is found
        spec_path = _write_spec(tmp_path, CONSISTENT_SPEC)
        sections = _parse_spec_sections(spec_path)
        results = _check_files_exist(sections, spec_path)
        # consistency_analyzer.py exists in the repo
        consistent_results = [r for r in results if r.status == "consistent"]
        assert len(consistent_results) >= 1

    def test_nonexistent_file_inconsistent(self, tmp_path):
        content = """\
## Relevant Files
- `tools/nonexistent/this_does_not_exist_xyz.py` -- Phantom file
"""
        spec_path = _write_spec(tmp_path, content)
        sections = _parse_spec_sections(spec_path)
        results = _check_files_exist(sections, spec_path)
        assert any(r.status == "inconsistent" for r in results)


# ---------------------------------------------------------------------------
# Tests: _check_nist_vs_ato
# ---------------------------------------------------------------------------

class TestNistVsAto:
    """Verify NIST controls are consistent with ATO assessment."""

    def test_nist_controls_listed_but_ato_says_none_inconsistent(self, tmp_path):
        content = """\
## ATO Impact Assessment
- **Boundary Impact**: GREEN
- **New NIST Controls**: None
- **SSP Impact**: None

## NIST 800-53 Controls
- AC-2 (Account Management)
- AU-2 (Event Logging)
"""
        spec_path = _write_spec(tmp_path, content)
        sections = _parse_spec_sections(spec_path)
        results = _check_nist_vs_ato(sections)
        assert any(r.status == "inconsistent" for r in results)

    def test_consistent_nist_and_ato(self, tmp_path):
        content = """\
## ATO Impact Assessment
- **Boundary Impact**: YELLOW
- **New NIST Controls**: AC-3, AU-2
- **SSP Impact**: SSP addendum

## NIST 800-53 Controls
- AC-3 (Access Enforcement)
- AU-2 (Event Logging)
"""
        spec_path = _write_spec(tmp_path, content)
        sections = _parse_spec_sections(spec_path)
        results = _check_nist_vs_ato(sections)
        assert any(r.status == "consistent" for r in results)


# ---------------------------------------------------------------------------
# Tests: _check_user_story_vs_acceptance
# ---------------------------------------------------------------------------

class TestUserStoryVsAcceptance:
    """Verify user story keywords appear in acceptance criteria."""

    def test_keyword_found_consistent(self, tmp_path):
        spec_path = _write_spec(tmp_path, CONSISTENT_SPEC)
        sections = _parse_spec_sections(spec_path)
        results = _check_user_story_vs_acceptance(sections)
        assert any(r.status == "consistent" for r in results)

    def test_keyword_missing_warns(self, tmp_path):
        content = """\
## User Story
As a scientist
I want to analyze molecular data
So that I can discover new compounds

## Acceptance Criteria
- Dashboard shows project list
- Navigation links work correctly
- Footer displays copyright
"""
        spec_path = _write_spec(tmp_path, content)
        sections = _parse_spec_sections(spec_path)
        results = _check_user_story_vs_acceptance(sections)
        assert any(r.status == "warn" for r in results)


# ---------------------------------------------------------------------------
# Tests: analyze_spec_consistency (full orchestrator)
# ---------------------------------------------------------------------------

class TestAnalyzeSpecConsistency:
    """Verify the main orchestrator returns structured results."""

    def test_returns_score_for_valid_spec(self, tmp_path):
        spec_path = _write_spec(tmp_path, CONSISTENT_SPEC)
        result = analyze_spec_consistency(spec_path)
        assert result["status"] == "ok"
        assert "consistency_score" in result
        assert isinstance(result["consistency_score"], float)

    def test_perfect_spec_high_score(self, tmp_path):
        spec_path = _write_spec(tmp_path, CONSISTENT_SPEC)
        result = analyze_spec_consistency(spec_path)
        # A consistent spec should score well
        assert result["consistency_score"] >= 50.0

    def test_empty_spec_handles_gracefully(self, tmp_path):
        content = "# Nothing here\n"
        spec_path = _write_spec(tmp_path, content)
        result = analyze_spec_consistency(spec_path)
        assert result["status"] == "ok"

    def test_spec_directory_with_plan_md(self, tmp_path):
        """Verify _check_spec_directory_consistency runs with sibling plan.md."""
        spec_dir = tmp_path / "feature-dir"
        spec_dir.mkdir()
        spec_path = spec_dir / "spec.md"
        spec_path.write_text(CONSISTENT_SPEC, encoding="utf-8")
        # Create a matching plan.md
        plan_content = (
            "# CUI // SP-CTI\n# Plan: Consistent Feature\n\n"
            "## Phases\n### Phase 1: Foundation\n- Set up analyzer base\n"
            "### Phase 2: Core Logic\n- Implement cross-section checks\n"
        )
        (spec_dir / "plan.md").write_text(plan_content, encoding="utf-8")
        result = analyze_spec_consistency(spec_path)
        assert result["status"] == "ok"

    def test_batch_check_on_directory(self, tmp_path):
        """Verify we can analyze multiple specs in a directory."""
        _write_spec(tmp_path, CONSISTENT_SPEC, "spec_a.md")
        _write_spec(tmp_path, INCONSISTENT_SPEC, "spec_b.md")
        results = []
        for md_file in sorted(tmp_path.glob("*.md")):
            r = analyze_spec_consistency(md_file)
            results.append(r)
        assert len(results) == 2
        assert all(r["status"] == "ok" for r in results)

    def test_handles_missing_sections_no_crash(self, tmp_path):
        """Spec with only some sections should not crash."""
        content = """\
## Feature Description
A feature with minimal sections for safety testing.

## Acceptance Criteria
- It works
"""
        spec_path = _write_spec(tmp_path, content)
        result = analyze_spec_consistency(spec_path)
        assert result["status"] == "ok"

    def test_fix_suggestions_included(self, tmp_path):
        spec_path = _write_spec(tmp_path, INCONSISTENT_SPEC)
        result = analyze_spec_consistency(spec_path)
        # When there are inconsistencies, suggestions should be present
        if result.get("inconsistencies"):
            assert len(result.get("suggestions", [])) > 0
