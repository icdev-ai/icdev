# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3

import pytest

from tools.requirements.spec_organizer import (
    _slugify,
    _parse_spec_metadata,
    _parse_spec_sections,
    _has_dependency,
    extract_plan,
    extract_tasks,
    init_spec_dir,
    migrate_flat_spec,
    migrate_all,
    get_status,
    list_all_specs,
    register_spec,
    update_checklist,
    update_constitution_check,
)


# ---------------------------------------------------------------------------
# Sample spec content
# ---------------------------------------------------------------------------

SAMPLE_SPEC = """\
# CUI // SP-CTI
# Feature: Dashboard Kanban

## Metadata
issue_number: `3`
run_id: `abc12345`

## Feature Description
Add a Kanban board to the ICDEV dashboard for visual task management.
The board displays project tasks organized by status columns.

## User Story
As a project manager
I want a Kanban board on the dashboard
So that I can track task progress visually

## Solution Statement
Implement a drag-and-drop Kanban board component using vanilla JavaScript
and server-side rendering via Flask templates.  The board reads tasks from
the project database and allows status updates via AJAX calls to the
dashboard API.  All interactions are logged per NIST AU-2.

## ATO Impact Assessment
- **Boundary Impact**: GREEN
- **New NIST Controls**: None
- **SSP Impact**: None

## Relevant Files
- tools/dashboard/app.py
- tools/dashboard/templates/kanban.html
- tools/dashboard/static/js/kanban.js

## Implementation Plan
### Phase 1: Backend API
- Create task retrieval endpoint
- Add status update endpoint
### Phase 2: Frontend
- Build Kanban board template
- Add drag-and-drop JS
### Phase 3: Testing
- Unit tests for API
- E2E tests for board

## Step by Step Tasks
### Step 1: Create task API endpoints
- Add GET /api/tasks endpoint
- Add PATCH /api/tasks/<id> endpoint
### Step 2: Build Kanban template
- Create kanban.html with column layout
### Step 3: Add drag-and-drop JS
- Implement sortable columns
### Step 4: Write tests
- Unit tests for endpoints
- Depends on: Step 1, Step 2

## Testing Strategy
### Unit Tests
- Test API endpoints with mock data
### BDD
- Feature file for drag-and-drop flow
### E2E
- Playwright test for board interaction

## Acceptance Criteria
- Given a project with tasks, when the Kanban page loads, then all tasks appear in correct columns
- Given a task card, when dragged to another column, then the task status is updated in the database
- Given an unauthorized user, when accessing the Kanban, then a 403 response is returned

## Validation Commands
- `python -m pytest tests/ -v`
- `ruff check .`
- `python -m py_compile tools/dashboard/app.py`

## NIST 800-53 Controls
- AU-2: Audit Events
- AC-3: Access Enforcement
"""


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_basic(self):
        assert _slugify("Dashboard Kanban") == "dashboard-kanban"

    def test_special_chars(self):
        assert _slugify("Auth: CAC/PIV Login!") == "auth-cacpiv-login"

    def test_multiple_spaces(self):
        assert _slugify("  Multiple   Spaces  ") == "multiple-spaces"

    def test_already_slug(self):
        assert _slugify("already-a-slug") == "already-a-slug"


# ---------------------------------------------------------------------------
# Metadata parsing
# ---------------------------------------------------------------------------

class TestParseSpecMetadata:
    def test_extracts_issue_number(self):
        meta = _parse_spec_metadata(SAMPLE_SPEC)
        assert meta["issue_number"] == "3"

    def test_extracts_run_id(self):
        meta = _parse_spec_metadata(SAMPLE_SPEC)
        assert meta["run_id"] == "abc12345"

    def test_extracts_title(self):
        meta = _parse_spec_metadata(SAMPLE_SPEC)
        assert meta["title"] == "Dashboard Kanban"

    def test_generates_slug(self):
        meta = _parse_spec_metadata(SAMPLE_SPEC)
        assert meta["slug"] == "dashboard-kanban"

    def test_fallback_untitled(self):
        meta = _parse_spec_metadata("## Metadata\nissue_number: `1`\n")
        assert meta["title"] == "Untitled"


# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------

class TestParseSpecSections:
    def test_returns_dict(self):
        sections = _parse_spec_sections(SAMPLE_SPEC)
        assert isinstance(sections, dict)
        assert "feature description" in sections

    def test_all_major_sections_present(self):
        sections = _parse_spec_sections(SAMPLE_SPEC)
        expected = [
            "metadata", "feature description", "user story",
            "solution statement", "ato impact assessment",
        ]
        for name in expected:
            assert name in sections, f"Missing section: {name}"


# ---------------------------------------------------------------------------
# Dependency detection
# ---------------------------------------------------------------------------

class TestHasDependency:
    def test_detects_depends_on(self):
        assert _has_dependency("This step depends on Step 1") is True

    def test_detects_after_step(self):
        assert _has_dependency("Run after step 2 completes") is True

    def test_clean_text(self):
        assert _has_dependency("Create the API endpoint") is False


# ---------------------------------------------------------------------------
# Plan extraction
# ---------------------------------------------------------------------------

class TestExtractPlan:
    def test_extracts_phases(self):
        plan = extract_plan(SAMPLE_SPEC)
        assert "Phase 1" in plan or "Backend API" in plan
        assert "CUI" in plan

    def test_empty_plan_returns_template(self):
        minimal = "# CUI\n# Feature: Test\n\n## Metadata\nissue_number: `1`\n"
        plan = extract_plan(minimal)
        assert "Phase 1" in plan
        assert "TODO" in plan


# ---------------------------------------------------------------------------
# Task extraction with [P] markers
# ---------------------------------------------------------------------------

class TestExtractTasks:
    def test_extracts_steps(self):
        tasks = extract_tasks(SAMPLE_SPEC)
        assert "Step 1" in tasks
        assert "Step 2" in tasks

    def test_parallel_markers_on_independent_steps(self):
        tasks = extract_tasks(SAMPLE_SPEC)
        # Step 1 is never parallel
        lines = tasks.splitlines()
        step1_lines = [l for l in lines if "Step 1" in l]
        for l in step1_lines:
            assert "[P]" not in l

    def test_dependent_step_not_parallel(self):
        tasks = extract_tasks(SAMPLE_SPEC)
        # Step 4 has "Depends on: Step 1, Step 2" — should NOT get [P]
        lines = tasks.splitlines()
        step4_lines = [l for l in lines if "Step 4" in l]
        for l in step4_lines:
            assert "[P]" not in l

    def test_checkboxes_added(self):
        tasks = extract_tasks(SAMPLE_SPEC)
        assert "- [ ]" in tasks

    def test_empty_tasks_returns_template(self):
        minimal = "# CUI\n# Feature: Test\n\n## Metadata\nissue_number: `1`\n"
        tasks = extract_tasks(minimal)
        assert "TODO" in tasks

    def test_parallel_group_comment(self):
        # Steps 2 and 3 should be parallel (no dependency keywords), so
        # we should see a parallel group comment if both are consecutive.
        tasks = extract_tasks(SAMPLE_SPEC)
        assert "<!-- Parallel group:" in tasks or "[P]" in tasks


# ---------------------------------------------------------------------------
# init_spec_dir
# ---------------------------------------------------------------------------

class TestInitSpecDir:
    def test_creates_directory(self, tmp_path):
        result = init_spec_dir("99", "test-feature", specs_dir=tmp_path)
        assert result.exists()
        assert result.is_dir()

    def test_creates_template_files(self, tmp_path):
        result = init_spec_dir("99", "test-feature", specs_dir=tmp_path)
        assert (result / "spec.md").exists()
        assert (result / "plan.md").exists()
        assert (result / "tasks.md").exists()

    def test_with_spec_content(self, tmp_path):
        result = init_spec_dir(
            "3", "dashboard-kanban",
            spec_content=SAMPLE_SPEC,
            specs_dir=tmp_path,
        )
        spec_text = (result / "spec.md").read_text(encoding="utf-8")
        assert "Dashboard Kanban" in spec_text
        plan_text = (result / "plan.md").read_text(encoding="utf-8")
        assert "CUI" in plan_text

    def test_directory_naming(self, tmp_path):
        result = init_spec_dir("7", "auth-module", specs_dir=tmp_path)
        assert result.name == "7-auth-module"


# ---------------------------------------------------------------------------
# migrate_flat_spec
# ---------------------------------------------------------------------------

class TestMigrateFlatSpec:
    def test_migrates_successfully(self, tmp_path):
        spec_file = tmp_path / "issue-3-dashboard-kanban.md"
        spec_file.write_text(SAMPLE_SPEC, encoding="utf-8")
        result = migrate_flat_spec(spec_file, specs_dir=tmp_path)
        assert result["status"] == "ok"
        assert "target_dir" in result
        target = Path(result["target_dir"])
        assert (target / "spec.md").exists()
        assert (target / "plan.md").exists()
        assert (target / "tasks.md").exists()

    def test_extracts_issue_from_filename(self, tmp_path):
        spec_file = tmp_path / "issue-42-my-feature.md"
        spec_file.write_text(
            "# CUI\n# Feature: My Feature\n\n## Feature Description\nSomething\n",
            encoding="utf-8",
        )
        result = migrate_flat_spec(spec_file, specs_dir=tmp_path)
        assert result["issue_number"] == "42"

    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            migrate_flat_spec(tmp_path / "nonexistent.md")


# ---------------------------------------------------------------------------
# migrate_all
# ---------------------------------------------------------------------------

class TestMigrateAll:
    def test_migrates_multiple(self, tmp_path):
        for i in range(3):
            f = tmp_path / f"issue-{i}-feature-{i}.md"
            f.write_text(
                f"# CUI\n# Feature: Feature {i}\n\n"
                f"## Metadata\nissue_number: `{i}`\n"
                f"## Feature Description\nFeature {i} description text.\n",
                encoding="utf-8",
            )
        results = migrate_all(specs_dir=tmp_path)
        assert len(results) == 3
        assert all(r["status"] == "ok" for r in results)

    def test_empty_dir_returns_empty(self, tmp_path):
        results = migrate_all(specs_dir=tmp_path)
        assert results == []


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------

class TestGetStatus:
    def test_complete_directory(self, tmp_path):
        spec_dir = tmp_path / "3-test"
        spec_dir.mkdir()
        for fname in ["spec.md", "plan.md", "tasks.md", "checklist.md", "constitution_check.md"]:
            (spec_dir / fname).write_text("# CUI\n", encoding="utf-8")
        result = get_status(spec_dir)
        assert result["complete"] is True
        assert all(result["files"].values())

    def test_partial_directory(self, tmp_path):
        spec_dir = tmp_path / "3-test"
        spec_dir.mkdir()
        (spec_dir / "spec.md").write_text("# CUI\n", encoding="utf-8")
        result = get_status(spec_dir)
        assert result["complete"] is False


# ---------------------------------------------------------------------------
# list_all_specs
# ---------------------------------------------------------------------------

class TestListAllSpecs:
    def test_lists_directories_and_flat(self, tmp_path):
        # Create a spec directory
        spec_dir = tmp_path / "3-kanban"
        spec_dir.mkdir()
        (spec_dir / "spec.md").write_text("# CUI\n", encoding="utf-8")
        # Create a flat spec
        (tmp_path / "issue-5-other.md").write_text("# CUI\n", encoding="utf-8")

        items = list_all_specs(specs_dir=tmp_path)
        types = [i["type"] for i in items]
        assert "directory" in types
        assert "flat" in types

    def test_empty_returns_empty(self, tmp_path):
        items = list_all_specs(specs_dir=tmp_path)
        assert items == []


# ---------------------------------------------------------------------------
# register_spec (DB integration)
# ---------------------------------------------------------------------------

def _init_organizer_db(db_path: Path):
    """Create minimal schema for spec_organizer DB tests."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS spec_registry (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            spec_path TEXT NOT NULL,
            spec_dir TEXT,
            issue_number TEXT,
            run_id TEXT,
            title TEXT,
            quality_score REAL,
            consistency_score REAL,
            constitution_pass INTEGER,
            last_checked_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


class TestRegisterSpec:
    def test_registers_new(self, tmp_path):
        db = tmp_path / "test.db"
        _init_organizer_db(db)
        spec_dir = tmp_path / "3-kanban"
        spec_dir.mkdir()
        (spec_dir / "spec.md").write_text(SAMPLE_SPEC, encoding="utf-8")

        result = register_spec(spec_dir, project_id="proj-1", db_path=db)
        assert result["status"] == "ok"
        assert result["entry"]["title"] == "Dashboard Kanban"
        assert result["entry"]["issue_number"] == "3"

    def test_updates_existing(self, tmp_path):
        db = tmp_path / "test.db"
        _init_organizer_db(db)
        spec_dir = tmp_path / "3-kanban"
        spec_dir.mkdir()
        (spec_dir / "spec.md").write_text(SAMPLE_SPEC, encoding="utf-8")

        # Register once
        r1 = register_spec(spec_dir, project_id="proj-1", db_path=db)
        entry_id = r1["entry"]["id"]

        # Register again — should update, not insert
        r2 = register_spec(spec_dir, project_id="proj-2", db_path=db)
        assert r2["entry"]["id"] == entry_id
        assert r2["entry"]["project_id"] == "proj-2"

    def test_missing_spec_md_raises(self, tmp_path):
        db = tmp_path / "test.db"
        _init_organizer_db(db)
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError):
            register_spec(empty_dir, db_path=db)


# ---------------------------------------------------------------------------
# update_checklist
# ---------------------------------------------------------------------------

class TestUpdateChecklist:
    def test_writes_file(self, tmp_path):
        spec_dir = tmp_path / "3-test"
        spec_dir.mkdir()
        check_results = {
            "score": 0.85,
            "checks": [
                {"name": "Required sections", "passed": True, "detail": "All present"},
                {"name": "Ambiguity check", "passed": False, "detail": "Found 'timely'"},
            ],
        }
        out_path = update_checklist(spec_dir, check_results)
        assert out_path.exists()
        content = out_path.read_text(encoding="utf-8")
        assert "CUI" in content
        assert "0.85" in content
        assert "PASS" in content
        assert "FAIL" in content


# ---------------------------------------------------------------------------
# update_constitution_check
# ---------------------------------------------------------------------------

class TestUpdateConstitutionCheck:
    def test_writes_pass(self, tmp_path):
        spec_dir = tmp_path / "3-test"
        spec_dir.mkdir()
        validation = {"passed": True, "violations": [], "summary": "All good"}
        out_path = update_constitution_check(spec_dir, validation)
        assert out_path.exists()
        content = out_path.read_text(encoding="utf-8")
        assert "PASS" in content
        assert "None." in content

    def test_writes_fail_with_violations(self, tmp_path):
        spec_dir = tmp_path / "3-test"
        spec_dir.mkdir()
        validation = {
            "passed": False,
            "violations": [
                {"category": "security", "rule": "CAC required", "message": "No auth mentioned"},
            ],
        }
        out_path = update_constitution_check(spec_dir, validation)
        content = out_path.read_text(encoding="utf-8")
        assert "FAIL" in content
        assert "SECURITY" in content
        assert "CAC required" in content
