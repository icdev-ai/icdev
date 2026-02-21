#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for the Tenant Development Profile Manager (Phase 34, D183-D188).

Tests cover:
- Create profile from template and explicit data
- Get profile (current and specific version)
- Version history and diffing
- 5-layer cascade resolution with provenance
- Lock/unlock dimension governance
- Rollback creates new version
- LLM injection for task context
- Auto-detection from text
- PROFILE.md generation
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ── Test Create Profile ──────────────────────────────────────────────


class TestCreateProfile:
    """Tests for create_profile()."""

    def test_create_from_explicit_data(self, icdev_db):
        """Creating a profile with explicit data stores it correctly."""
        from tools.builder.dev_profile_manager import create_profile, get_profile

        result = create_profile(
            scope="tenant",
            scope_id="tenant-test",
            profile_data={"language": {"primary": "python"}, "style": {"indent_size": 4}},
            created_by="test-user",
            db_path=icdev_db,
        )

        assert result["status"] == "created"
        assert result["version"] == 1
        assert "language" in result["dimensions"]
        assert "style" in result["dimensions"]

        # Verify we can get it back
        fetched = get_profile("tenant", "tenant-test", db_path=icdev_db)
        assert fetched["profile_data"]["language"]["primary"] == "python"
        assert fetched["version"] == 1

    def test_create_from_template(self, icdev_db):
        """Creating a profile from a template loads defaults."""
        from tools.builder.dev_profile_manager import create_profile

        result = create_profile(
            scope="tenant",
            scope_id="tenant-dod",
            template_name="dod_baseline",
            created_by="test-user",
            db_path=icdev_db,
        )

        assert result["status"] == "created"
        assert result["inherits_from"] == "dod_baseline"
        assert len(result["dimensions"]) > 0

    def test_create_version_increment(self, icdev_db):
        """Creating a second profile increments the version."""
        from tools.builder.dev_profile_manager import create_profile, get_profile

        create_profile(
            scope="project", scope_id="proj-1",
            profile_data={"language": {"primary": "python"}},
            created_by="user1", db_path=icdev_db,
        )
        result2 = create_profile(
            scope="project", scope_id="proj-1",
            profile_data={"language": {"primary": "go"}},
            created_by="user2", db_path=icdev_db,
        )

        assert result2["version"] == 2

        # Old version is deactivated, current is the new one
        current = get_profile("project", "proj-1", db_path=icdev_db)
        assert current["version"] == 2
        assert current["profile_data"]["language"]["primary"] == "go"

    def test_create_invalid_scope(self, icdev_db):
        """Creating with an invalid scope returns an error."""
        from tools.builder.dev_profile_manager import create_profile

        result = create_profile(
            scope="invalid", scope_id="test",
            profile_data={}, created_by="user", db_path=icdev_db,
        )
        assert "error" in result

    def test_create_missing_template(self, icdev_db):
        """Creating from a non-existent template returns an error."""
        from tools.builder.dev_profile_manager import create_profile

        result = create_profile(
            scope="tenant", scope_id="t1",
            template_name="nonexistent_template",
            created_by="user", db_path=icdev_db,
        )
        assert "error" in result


# ── Test Get Profile ─────────────────────────────────────────────────


class TestGetProfile:
    """Tests for get_profile()."""

    def test_get_current(self, icdev_db):
        """Get returns the current active version."""
        from tools.builder.dev_profile_manager import create_profile, get_profile

        create_profile(
            scope="project", scope_id="proj-get",
            profile_data={"style": {"indent_size": 2}},
            created_by="user", db_path=icdev_db,
        )

        result = get_profile("project", "proj-get", db_path=icdev_db)
        assert result["version"] == 1
        assert result["is_active"] is True

    def test_get_specific_version(self, icdev_db):
        """Get with version number returns that specific version."""
        from tools.builder.dev_profile_manager import create_profile, get_profile

        create_profile(
            scope="project", scope_id="proj-ver",
            profile_data={"style": {"indent_size": 2}},
            created_by="user", db_path=icdev_db,
        )
        create_profile(
            scope="project", scope_id="proj-ver",
            profile_data={"style": {"indent_size": 4}},
            created_by="user", db_path=icdev_db,
        )

        v1 = get_profile("project", "proj-ver", version=1, db_path=icdev_db)
        v2 = get_profile("project", "proj-ver", version=2, db_path=icdev_db)

        assert v1["profile_data"]["style"]["indent_size"] == 2
        assert v2["profile_data"]["style"]["indent_size"] == 4

    def test_get_nonexistent(self, icdev_db):
        """Get for a non-existent profile returns an error."""
        from tools.builder.dev_profile_manager import get_profile

        result = get_profile("project", "no-such-project", db_path=icdev_db)
        assert "error" in result


# ── Test Resolve Profile (Cascade) ───────────────────────────────────


class TestResolveProfile:
    """Tests for resolve_profile() — 5-layer cascade resolution."""

    def test_single_scope(self, icdev_db):
        """Resolve with a single platform profile returns it."""
        from tools.builder.dev_profile_manager import create_profile, resolve_profile

        create_profile(
            scope="platform", scope_id="default",
            profile_data={"language": {"primary": "python"}, "style": {"indent_size": 4}},
            created_by="admin", db_path=icdev_db,
        )

        result = resolve_profile("platform", "default", db_path=icdev_db)
        assert result["status"] == "resolved"
        assert result["resolved"]["language"]["primary"] == "python"
        assert "language" in result["provenance"]

    def test_two_layer_override(self, icdev_db):
        """Project profile overrides platform for override dimensions."""
        from tools.builder.dev_profile_manager import create_profile, resolve_profile

        # Platform defaults
        create_profile(
            scope="platform", scope_id="default",
            profile_data={
                "language": {"primary": "python", "allowed": ["python", "java"]},
                "style": {"indent_size": 4, "max_line_length": 100},
            },
            created_by="admin", db_path=icdev_db,
        )

        # Project overrides language (cascade_behavior: override)
        create_profile(
            scope="project", scope_id="proj-test-001",
            profile_data={
                "language": {"primary": "go", "allowed": ["go", "rust"]},
            },
            created_by="dev", db_path=icdev_db,
        )

        result = resolve_profile("project", "proj-test-001", db_path=icdev_db)
        resolved = result["resolved"]

        # Language should be fully overridden (override cascade)
        assert resolved["language"]["primary"] == "go"

        # Style should cascade from platform (merge behavior)
        assert resolved["style"]["indent_size"] == 4

    def test_provenance_tracking(self, icdev_db):
        """Provenance shows which scope set each dimension."""
        from tools.builder.dev_profile_manager import create_profile, resolve_profile

        create_profile(
            scope="platform", scope_id="default",
            profile_data={"language": {"primary": "python"}},
            created_by="admin", db_path=icdev_db,
        )

        result = resolve_profile("platform", "default", db_path=icdev_db)
        prov = result["provenance"]

        assert prov["language"]["source_scope"] == "platform"

    def test_empty_resolve(self, icdev_db):
        """Resolve with no profiles returns empty resolved dict."""
        from tools.builder.dev_profile_manager import resolve_profile

        result = resolve_profile("project", "nonexistent", db_path=icdev_db)
        assert result["status"] == "resolved"
        assert result["resolved"] == {}


# ── Test Lock Dimension ──────────────────────────────────────────────


class TestLockDimension:
    """Tests for lock_dimension() and unlock_dimension()."""

    def test_lock_by_isso(self, icdev_db):
        """ISSO can lock the security dimension."""
        from tools.builder.dev_profile_manager import (
            create_profile, lock_dimension,
        )

        create_profile(
            scope="tenant", scope_id="tenant-lock",
            profile_data={"security": {"encryption_standard": "fips_140_2"}},
            created_by="admin", db_path=icdev_db,
        )

        result = lock_dimension(
            scope="tenant", scope_id="tenant-lock",
            dimension_path="security", lock_owner_role="isso",
            locked_by="isso@gov", db_path=icdev_db,
        )

        assert result["status"] == "locked"
        assert result["lock_owner_role"] == "isso"

    def test_lock_prevents_override(self, icdev_db):
        """Locked dimension prevents update at same scope."""
        from tools.builder.dev_profile_manager import (
            create_profile, lock_dimension, update_profile,
        )

        create_profile(
            scope="tenant", scope_id="tenant-lockup",
            profile_data={"security": {"encryption_standard": "fips_140_2"}},
            created_by="admin", db_path=icdev_db,
        )

        lock_dimension(
            scope="tenant", scope_id="tenant-lockup",
            dimension_path="security", lock_owner_role="isso",
            locked_by="isso@gov", db_path=icdev_db,
        )

        result = update_profile(
            scope="tenant", scope_id="tenant-lockup",
            changes={"security": {"encryption_standard": "aes_256"}},
            updated_by="dev@gov", db_path=icdev_db,
        )

        assert "error" in result
        assert "locked" in result["error"].lower()

    def test_unlock_requires_role(self, icdev_db):
        """Unlock requires matching role or admin."""
        from tools.builder.dev_profile_manager import (
            create_profile, lock_dimension, unlock_dimension,
        )

        create_profile(
            scope="tenant", scope_id="tenant-role",
            profile_data={"security": {"encryption_standard": "fips_140_2"}},
            created_by="admin", db_path=icdev_db,
        )

        lock_dimension(
            scope="tenant", scope_id="tenant-role",
            dimension_path="security", lock_owner_role="isso",
            locked_by="isso@gov", db_path=icdev_db,
        )

        # PM cannot unlock ISSO lock
        result = unlock_dimension(
            scope="tenant", scope_id="tenant-role",
            dimension_path="security", unlocked_by="pm@gov",
            role="pm", db_path=icdev_db,
        )
        assert "error" in result

        # Admin can override
        result = unlock_dimension(
            scope="tenant", scope_id="tenant-role",
            dimension_path="security", unlocked_by="admin@gov",
            role="admin", db_path=icdev_db,
        )
        assert result["status"] == "unlocked"

    def test_duplicate_lock(self, icdev_db):
        """Locking an already-locked dimension returns error."""
        from tools.builder.dev_profile_manager import (
            create_profile, lock_dimension,
        )

        create_profile(
            scope="tenant", scope_id="tenant-dup",
            profile_data={"style": {"indent_size": 4}},
            created_by="admin", db_path=icdev_db,
        )

        lock_dimension(
            scope="tenant", scope_id="tenant-dup",
            dimension_path="style", lock_owner_role="architect",
            locked_by="arch@gov", db_path=icdev_db,
        )

        result = lock_dimension(
            scope="tenant", scope_id="tenant-dup",
            dimension_path="style", lock_owner_role="architect",
            locked_by="arch2@gov", db_path=icdev_db,
        )
        assert "error" in result


# ── Test Versioning ──────────────────────────────────────────────────


class TestVersioning:
    """Tests for diff_versions() and rollback_to_version()."""

    def test_diff_between_versions(self, icdev_db):
        """Diff shows changes between two versions."""
        from tools.builder.dev_profile_manager import (
            create_profile, diff_versions,
        )

        create_profile(
            scope="project", scope_id="proj-diff",
            profile_data={"style": {"indent_size": 2}, "language": {"primary": "python"}},
            created_by="user", db_path=icdev_db,
        )
        create_profile(
            scope="project", scope_id="proj-diff",
            profile_data={"style": {"indent_size": 4}, "testing": {"min_coverage": 80}},
            created_by="user", db_path=icdev_db,
        )

        diff = diff_versions("project", "proj-diff", 1, 2, db_path=icdev_db)
        assert diff["total_changes"] > 0

    def test_rollback_creates_new_version(self, icdev_db):
        """Rollback creates a new version (not revert)."""
        from tools.builder.dev_profile_manager import (
            create_profile, get_profile, rollback_to_version,
        )

        create_profile(
            scope="project", scope_id="proj-rb",
            profile_data={"style": {"indent_size": 2}},
            created_by="user", db_path=icdev_db,
        )
        create_profile(
            scope="project", scope_id="proj-rb",
            profile_data={"style": {"indent_size": 4}},
            created_by="user", db_path=icdev_db,
        )

        result = rollback_to_version(
            "project", "proj-rb", 1, rolled_back_by="admin", db_path=icdev_db,
        )

        assert result["version"] == 3  # New version, not reverting to v1
        current = get_profile("project", "proj-rb", db_path=icdev_db)
        assert current["version"] == 3
        assert current["profile_data"]["style"]["indent_size"] == 2

    def test_history(self, icdev_db):
        """History returns all versions."""
        from tools.builder.dev_profile_manager import (
            create_profile, get_profile_history,
        )

        create_profile(
            scope="project", scope_id="proj-hist",
            profile_data={"style": {"indent_size": 2}},
            created_by="user", db_path=icdev_db,
        )
        create_profile(
            scope="project", scope_id="proj-hist",
            profile_data={"style": {"indent_size": 4}},
            created_by="user", db_path=icdev_db,
        )

        hist = get_profile_history("project", "proj-hist", db_path=icdev_db)
        assert hist["total_versions"] == 2
        assert len(hist["versions"]) == 2


# ── Test Detection ───────────────────────────────────────────────────


class TestDetection:
    """Tests for text-based detection of dev profile signals."""

    def test_detect_python_signals(self):
        """Text with Python keywords detects language dimension."""
        from tools.requirements.intake_engine import _detect_dev_profile_signals

        # Patch the import so we test the inline fallback
        with patch.dict("sys.modules", {"tools.builder.profile_detector": None}):
            result = _detect_dev_profile_signals(
                "We use Python 3.12 with Flask and pytest for TDD. "
                "Our code follows snake_case naming."
            )

        assert result["profile_detected"] is True
        assert "language" in result["detected_dimensions"]
        assert "style" in result["detected_dimensions"]

    def test_detect_dod_template_suggestion(self):
        """DoD keywords suggest dod_baseline template."""
        from tools.requirements.intake_engine import _detect_dev_profile_signals

        with patch.dict("sys.modules", {"tools.builder.profile_detector": None}):
            result = _detect_dev_profile_signals(
                "This is a Department of Defense project at IL5 with CMMC Level 2 and STIG requirements."
            )

        assert result["profile_detected"] is True
        assert "dod_baseline" in result["suggested_templates"]

    def test_detect_no_signals(self):
        """Generic text produces no detection."""
        from tools.requirements.intake_engine import _detect_dev_profile_signals

        with patch.dict("sys.modules", {"tools.builder.profile_detector": None}):
            result = _detect_dev_profile_signals("Hello, I would like to build an app.")

        assert result["profile_detected"] is False
        assert result["dimension_count"] == 0

    def test_detect_via_profile_detector(self):
        """When profile_detector is available, returns normalized shape."""
        from tools.requirements.intake_engine import _detect_dev_profile_signals

        result = _detect_dev_profile_signals(
            "We use Python with snake_case naming convention"
        )

        # Should have the normalized shape regardless of import path
        assert "profile_detected" in result
        assert "detected_dimensions" in result
        assert "dimension_count" in result


# ── Test LLM Injection ───────────────────────────────────────────────


class TestInjection:
    """Tests for inject_for_task() — LLM prompt injection."""

    def test_inject_for_code_generation(self, icdev_db):
        """Injection for code_generation returns relevant dimensions."""
        from tools.builder.dev_profile_manager import create_profile, inject_for_task

        create_profile(
            scope="platform", scope_id="default",
            profile_data={
                "language": {"primary": "python", "versions": {"python": ">=3.11"}},
                "style": {"naming_convention": "snake_case", "indent_size": 4},
                "testing": {"methodology": "tdd", "min_coverage": 80},
                "architecture": {"api_style": "rest_openapi"},
            },
            created_by="admin", db_path=icdev_db,
        )

        # inject_for_task resolves at project scope, need platform
        # Use project scope with a project that would cascade to platform
        create_profile(
            scope="project", scope_id="proj-inject",
            profile_data={
                "language": {"primary": "python"},
                "style": {"indent_size": 4},
            },
            created_by="dev", db_path=icdev_db,
        )

        text = inject_for_task("proj-inject", "code_generation", db_path=icdev_db)
        assert "Development Profile" in text
        assert "code_generation" in text

    def test_inject_empty_profile(self, icdev_db):
        """Injection with no profile returns empty string."""
        from tools.builder.dev_profile_manager import inject_for_task

        text = inject_for_task("nonexistent-project", "code_generation", db_path=icdev_db)
        assert text == ""

    def test_inject_unknown_task_type(self, icdev_db):
        """Injection with unknown task type returns empty string."""
        from tools.builder.dev_profile_manager import inject_for_task

        text = inject_for_task("anything", "unknown_task", db_path=icdev_db)
        assert text == ""


# ── Test PROFILE.md Generation ───────────────────────────────────────


class TestProfileMdGeneration:
    """Tests for profile_md_generator.py."""

    def test_generate_from_resolved(self):
        """Generator produces PROFILE.md from resolved profile data."""
        from tools.builder.profile_md_generator import generate_profile_md

        resolved_profile = {
            "status": "resolved",
            "scope": "project",
            "scope_id": "proj-test",
            "resolved": {
                "language": {"primary": "python", "allowed": ["python", "go"]},
                "style": {"indent_size": 4, "naming_convention": "snake_case"},
            },
            "provenance": {
                "language": {"source_scope": "tenant", "enforcement": "enforced", "locked": False},
                "style": {"source_scope": "platform", "enforcement": "enforced", "locked": True},
            },
            "locks": ["style"],
            "ancestry": [
                {"scope": "platform", "scope_id": "default"},
                {"scope": "tenant", "scope_id": "tenant-abc"},
                {"scope": "project", "scope_id": "proj-test"},
            ],
        }

        md = generate_profile_md(resolved_profile)

        assert "# PROFILE.md" in md
        assert "python" in md
        assert "snake_case" in md
        assert "LOCKED" in md
        assert "tenant" in md

    def test_generate_error_profile(self):
        """Generator handles error profile gracefully."""
        from tools.builder.profile_md_generator import generate_profile_md

        md = generate_profile_md({"error": "No profile found"})
        assert "Error" in md

    def test_generate_empty_profile(self):
        """Generator handles empty resolved profile."""
        from tools.builder.profile_md_generator import generate_profile_md

        md = generate_profile_md({
            "status": "resolved",
            "scope": "project",
            "scope_id": "empty",
            "resolved": {},
            "provenance": {},
            "locks": [],
            "ancestry": [],
        })

        assert "# PROFILE.md" in md


# ── Test Merge Behaviors ─────────────────────────────────────────────


class TestMergeBehaviors:
    """Tests for the internal merge logic."""

    def test_deep_merge(self):
        """Deep merge combines nested dicts correctly."""
        from tools.builder.dev_profile_manager import _deep_merge

        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"y": 5, "z": 6}, "c": 7}

        result = _deep_merge(base, override)
        assert result["a"]["x"] == 1
        assert result["a"]["y"] == 5
        assert result["a"]["z"] == 6
        assert result["b"] == 3
        assert result["c"] == 7

    def test_pick_stricter_numeric(self):
        """Stricter numeric: higher value wins."""
        from tools.builder.dev_profile_manager import _pick_stricter

        assert _pick_stricter(80, 90) == 90
        assert _pick_stricter(90, 80) == 90

    def test_pick_stricter_duration(self):
        """Stricter duration: shorter SLA wins."""
        from tools.builder.dev_profile_manager import _pick_stricter

        assert _pick_stricter("48h", "24h") == "24h"
        assert _pick_stricter("7d", "14d") == "7d"

    def test_merge_dimension_union(self):
        """Union merge combines lists without duplicates."""
        from tools.builder.dev_profile_manager import _merge_dimension

        parent = ["nist_800_53", "fedramp_moderate"]
        child = ["fedramp_moderate", "cmmc_level_2"]

        result = _merge_dimension(parent, child, "union")
        assert "nist_800_53" in result
        assert "fedramp_moderate" in result
        assert "cmmc_level_2" in result
        assert len(result) == 3  # No duplicates
