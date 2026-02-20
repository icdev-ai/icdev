# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3

import pytest

from tools.requirements.constitution_manager import (
    add_principle,
    list_principles,
    remove_principle,
    load_defaults,
    validate_spec,
    VALID_CATEGORIES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_test_db(db_path: Path):
    """Create minimal schema required by constitution_manager in a temp DB."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT
        );

        CREATE TABLE IF NOT EXISTS project_constitutions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            principle_text TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'general',
            priority INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1,
            created_by TEXT DEFAULT 'system',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


def _write_spec(tmp_path: Path, content: str, filename: str = "spec.md") -> Path:
    """Write spec content to a .md file and return the Path."""
    spec_path = tmp_path / filename
    spec_path.write_text(content, encoding="utf-8")
    return spec_path


SECURITY_SPEC = """\
# CUI // SP-CTI
# Feature: Secure Auth Module

## Feature Description
This module provides CAC/PIV authentication with FIPS 140-2 validated
encryption for all data at rest and in transit. It enforces STIG compliance
and implements NIST 800-53 access control policies. All audit events are
logged to an append-only audit trail.

## ATO Impact Assessment
- **Boundary Impact**: YELLOW
- **NIST Controls**: AC-2, IA-2, SC-8
- **SSP Impact**: SSP addendum

## Acceptance Criteria
- Authentication requires CAC/PIV
- Encryption uses FIPS 140-2 validated module
- All access is logged to audit trail

# CUI // SP-CTI
"""

MINIMAL_SPEC = """\
# CUI // SP-CTI
# Feature: Simple Widget

## Feature Description
A simple widget that shows data.

## Acceptance Criteria
- Widget renders correctly

# CUI // SP-CTI
"""


# ---------------------------------------------------------------------------
# Tests: add_principle
# ---------------------------------------------------------------------------

class TestAddPrinciple:
    """Verify adding constitution principles to the DB."""

    def test_add_principle_creates_entry(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)

        result = add_principle(
            project_id="proj-001",
            principle_text="All APIs must require CAC authentication.",
            category="security",
            priority=1,
            db_path=db_path,
        )

        assert result["status"] == "ok"
        assert result["principle_id"].startswith("con-")
        assert result["category"] == "security"
        assert result["priority"] == 1

        # Verify in DB
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM project_constitutions WHERE id = ?",
            (result["principle_id"],),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["principle_text"] == "All APIs must require CAC authentication."

    def test_add_principle_invalid_category_raises(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)

        with pytest.raises(ValueError, match="Invalid category"):
            add_principle(
                project_id="proj-001",
                principle_text="Some principle.",
                category="invalid_category",
                db_path=db_path,
            )


# ---------------------------------------------------------------------------
# Tests: list_principles
# ---------------------------------------------------------------------------

class TestListPrinciples:
    """Verify listing constitution principles."""

    def test_list_returns_all_for_project(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)

        add_principle("proj-002", "Principle A", category="security", db_path=db_path)
        add_principle("proj-002", "Principle B", category="compliance", db_path=db_path)
        add_principle("proj-002", "Principle C", category="quality", db_path=db_path)

        principles = list_principles("proj-002", db_path=db_path)
        assert len(principles) == 3

    def test_list_with_category_filter(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)

        add_principle("proj-003", "Security rule", category="security", db_path=db_path)
        add_principle("proj-003", "Quality rule", category="quality", db_path=db_path)

        principles = list_principles("proj-003", category="security", db_path=db_path)
        assert len(principles) == 1
        assert principles[0]["category"] == "security"

    def test_list_active_only_excludes_inactive(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)

        result = add_principle(
            "proj-004", "To be removed", category="general", db_path=db_path
        )
        add_principle("proj-004", "Active one", category="general", db_path=db_path)

        # Deactivate one
        remove_principle(result["principle_id"], db_path=db_path)

        active = list_principles("proj-004", active_only=True, db_path=db_path)
        all_principles = list_principles("proj-004", active_only=False, db_path=db_path)

        assert len(active) == 1
        assert len(all_principles) == 2


# ---------------------------------------------------------------------------
# Tests: remove_principle
# ---------------------------------------------------------------------------

class TestRemovePrinciple:
    """Verify soft-delete behavior."""

    def test_remove_soft_deletes(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)

        result = add_principle(
            "proj-005", "Will be removed", category="operations", db_path=db_path
        )
        principle_id = result["principle_id"]

        remove_result = remove_principle(principle_id, db_path=db_path)
        assert remove_result["status"] == "ok"
        assert "deactivated" in remove_result["message"].lower()

        # Verify is_active = 0 in DB
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT is_active FROM project_constitutions WHERE id = ?",
            (principle_id,),
        ).fetchone()
        conn.close()
        assert row["is_active"] == 0


# ---------------------------------------------------------------------------
# Tests: load_defaults
# ---------------------------------------------------------------------------

class TestLoadDefaults:
    """Verify loading default DoD principles from context JSON."""

    def test_load_defaults_loads_principles(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)

        result = load_defaults("proj-006", db_path=db_path)
        assert result["status"] == "ok"
        # loaded might be 0 if no defaults file exists, but should not crash
        assert "loaded" in result
        assert "skipped" in result

    def test_load_defaults_skips_duplicates(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)

        first = load_defaults("proj-007", db_path=db_path)
        second = load_defaults("proj-007", db_path=db_path)

        # Second call should skip everything that was already loaded
        assert second["skipped"] >= first["loaded"]
        # Total in DB should equal first load
        all_principles = list_principles("proj-007", active_only=True, db_path=db_path)
        assert len(all_principles) == first["loaded"]


# ---------------------------------------------------------------------------
# Tests: validate_spec
# ---------------------------------------------------------------------------

class TestValidateSpec:
    """Verify spec validation against constitution principles."""

    def test_validate_passes_for_security_spec(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)

        # Add a security principle with keywords that match the security spec
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """INSERT INTO project_constitutions
               (id, project_id, principle_text, category, priority, is_active, created_by)
               VALUES (?, ?, ?, ?, ?, 1, 'system')""",
            (
                "con-test-sec",
                "proj-008",
                "All authentication must use CAC or PIV credentials",
                "security",
                1,
            ),
        )
        conn.commit()
        conn.close()

        spec_path = _write_spec(tmp_path, SECURITY_SPEC)
        result = validate_spec(spec_path, "proj-008", db_path=db_path)
        assert result["status"] == "ok"
        # "authentication" and "CAC" should match
        assert result["passed"] >= 1

    def test_validate_fails_for_missing_keywords(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)

        # Add a principle with keywords that won't appear in the minimal spec
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """INSERT INTO project_constitutions
               (id, project_id, principle_text, category, priority, is_active, created_by)
               VALUES (?, ?, ?, ?, ?, 1, 'system')""",
            (
                "con-test-miss",
                "proj-009",
                "All deployments must use Kubernetes orchestration",
                "operations",
                1,
            ),
        )
        conn.commit()
        conn.close()

        spec_path = _write_spec(tmp_path, MINIMAL_SPEC)
        result = validate_spec(spec_path, "proj-009", db_path=db_path)
        assert result["status"] == "ok"
        assert result["failed"] >= 1

    def test_validate_with_no_principles_all_pass(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)

        spec_path = _write_spec(tmp_path, MINIMAL_SPEC)
        result = validate_spec(spec_path, "proj-010", db_path=db_path)
        assert result["status"] == "ok"
        assert result["total_principles"] == 0
        assert result["failed"] == 0

    def test_validate_warns_for_priority_2(self, tmp_path):
        db_path = tmp_path / "test.db"
        _init_test_db(db_path)

        # Add a priority-2 principle with keywords that won't match
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """INSERT INTO project_constitutions
               (id, project_id, principle_text, category, priority, is_active, created_by)
               VALUES (?, ?, ?, ?, ?, 1, 'system')""",
            (
                "con-test-warn",
                "proj-011",
                "All microservices must implement circuit breaker patterns",
                "architecture",
                2,
            ),
        )
        conn.commit()
        conn.close()

        spec_path = _write_spec(tmp_path, MINIMAL_SPEC)
        result = validate_spec(spec_path, "proj-011", db_path=db_path)
        assert result["status"] == "ok"
        assert result["warnings"] >= 1
