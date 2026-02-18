# CUI // SP-CTI
"""Tests for tools/compliance/resolve_marking.py."""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

import sys
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.compliance.resolve_marking import resolve_project_marking, _no_marking_result


@pytest.fixture
def db_path(tmp_path):
    """Create a temporary ICDEV database with minimal schema."""
    db_file = tmp_path / "test_icdev.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("""
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT 'webapp',
            classification TEXT NOT NULL DEFAULT 'CUI',
            status TEXT NOT NULL DEFAULT 'active',
            directory_path TEXT NOT NULL DEFAULT '/tmp',
            impact_level TEXT DEFAULT 'IL5',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE data_classifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            data_category TEXT NOT NULL,
            source TEXT DEFAULT 'manual',
            confirmed INTEGER DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(project_id, data_category)
        )
    """)
    conn.commit()
    conn.close()
    return db_file


class TestResolveProjectMarking:
    """Tests for the main resolve_project_marking function."""

    def test_cui_project_il5(self, db_path):
        """CUI project at IL5 should return CUI // SP-CTI marking."""
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO projects (id, name, classification, impact_level, directory_path) "
            "VALUES (?, ?, ?, ?, ?)",
            ("proj-cui", "CUI App", "CUI", "IL5", "/tmp"),
        )
        conn.commit()
        conn.close()

        result = resolve_project_marking("proj-cui", db_path)
        assert result["marking_required"] is True
        assert "CUI" in result["categories"]
        assert "CUI" in result["banner"]
        assert result["code_header"].startswith("#")
        assert result["grep_pattern"] != ""

    def test_public_project_il2(self, db_path):
        """Public project at IL2 should require no marking."""
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO projects (id, name, classification, impact_level, directory_path) "
            "VALUES (?, ?, ?, ?, ?)",
            ("proj-pub", "Public App", "Public", "IL2", "/tmp"),
        )
        conn.commit()
        conn.close()

        result = resolve_project_marking("proj-pub", db_path)
        assert result["marking_required"] is False
        assert result["categories"] == []
        assert result["banner"] == ""
        assert result["code_header"] == ""
        assert result["grep_pattern"] == ""

    def test_secret_project_il6(self, db_path):
        """SECRET project at IL6 should return SECRET marking."""
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO projects (id, name, classification, impact_level, directory_path) "
            "VALUES (?, ?, ?, ?, ?)",
            ("proj-sec", "Secret App", "SECRET", "IL6", "/tmp"),
        )
        conn.commit()
        conn.close()

        result = resolve_project_marking("proj-sec", db_path)
        assert result["marking_required"] is True
        assert "SECRET" in result["categories"]
        assert "SECRET" in result["banner"]

    def test_il2_overrides_classification(self, db_path):
        """IL2 should override even if classification says CUI."""
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO projects (id, name, classification, impact_level, directory_path) "
            "VALUES (?, ?, ?, ?, ?)",
            ("proj-il2", "IL2 App", "CUI", "IL2", "/tmp"),
        )
        conn.commit()
        conn.close()

        result = resolve_project_marking("proj-il2", db_path)
        assert result["marking_required"] is False

    def test_data_classifications_override_project(self, db_path):
        """data_classifications table should take priority over project.classification."""
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO projects (id, name, classification, impact_level, directory_path) "
            "VALUES (?, ?, ?, ?, ?)",
            ("proj-phi", "Health App", "CUI", "IL5", "/tmp"),
        )
        conn.execute(
            "INSERT INTO data_classifications (project_id, data_category) VALUES (?, ?)",
            ("proj-phi", "PHI"),
        )
        conn.commit()
        conn.close()

        result = resolve_project_marking("proj-phi", db_path)
        assert result["marking_required"] is True
        assert "PHI" in result["categories"]

    def test_composite_categories(self, db_path):
        """Multiple data categories should produce composite marking."""
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO projects (id, name, classification, impact_level, directory_path) "
            "VALUES (?, ?, ?, ?, ?)",
            ("proj-comp", "Composite App", "CUI", "IL5", "/tmp"),
        )
        conn.execute(
            "INSERT INTO data_classifications (project_id, data_category) VALUES (?, ?)",
            ("proj-comp", "CUI"),
        )
        conn.execute(
            "INSERT INTO data_classifications (project_id, data_category) VALUES (?, ?)",
            ("proj-comp", "PHI"),
        )
        conn.commit()
        conn.close()

        result = resolve_project_marking("proj-comp", db_path)
        assert result["marking_required"] is True
        assert "CUI" in result["categories"]
        assert "PHI" in result["categories"]
        # Composite banner should contain both
        assert "CUI" in result["banner"]

    def test_nonexistent_project_defaults_cui(self, db_path):
        """Non-existent project should fall back to CUI for backward compat."""
        result = resolve_project_marking("proj-nonexistent", db_path)
        assert result["marking_required"] is True
        assert "CUI" in result["categories"]

    def test_no_marking_result_helper(self):
        """_no_marking_result should return consistent empty structure."""
        result = _no_marking_result()
        assert result["marking_required"] is False
        assert result["categories"] == []
        assert result["banner"] == ""
        assert result["highest_sensitivity"] == "PUBLIC"

    def test_fouo_resolves_to_cui(self, db_path):
        """FOUO classification should resolve to CUI marking."""
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO projects (id, name, classification, impact_level, directory_path) "
            "VALUES (?, ?, ?, ?, ?)",
            ("proj-fouo", "FOUO App", "FOUO", "IL4", "/tmp"),
        )
        conn.commit()
        conn.close()

        result = resolve_project_marking("proj-fouo", db_path)
        assert result["marking_required"] is True
        assert "CUI" in result["categories"]
# CUI // SP-CTI
