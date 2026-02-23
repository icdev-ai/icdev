# [TEMPLATE: CUI // SP-CTI]
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json

import pytest

from tools.agent.skill_selector import (
    load_config,
    match_keywords,
    detect_from_files,
    select_skills,
    _resolve_paths,
    format_injection_context,
)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_returns_dict(self):
        config = load_config()
        assert isinstance(config, dict)

    def test_has_categories(self):
        config = load_config()
        assert "categories" in config
        assert isinstance(config["categories"], dict)

    def test_has_expected_categories(self):
        config = load_config()
        cats = config["categories"]
        expected = ["build", "compliance", "infrastructure", "requirements"]
        for cat in expected:
            assert cat in cats, f"Missing category: {cat}"

    def test_categories_have_required_keys(self):
        config = load_config()
        for name, data in config["categories"].items():
            assert "keywords" in data, f"{name} missing keywords"
            assert "commands" in data or "goals" in data, \
                f"{name} missing commands/goals"

    def test_has_always_include(self):
        config = load_config()
        always = config.get("always_include", {})
        assert "commit" in always.get("commands", [])

    def test_has_confidence_threshold(self):
        config = load_config()
        assert "confidence_threshold" in config
        assert 0.0 <= config["confidence_threshold"] <= 1.0

    def test_custom_config_path(self, tmp_path):
        # Non-existent path → falls back to defaults
        config = load_config(tmp_path / "nonexistent.yaml")
        assert isinstance(config, dict)
        assert "categories" in config


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------

class TestMatchKeywords:
    def test_build_keywords(self):
        config = load_config()
        scores = match_keywords("fix the python tests", config["categories"])
        assert scores.get("build", 0.0) > 0.0

    def test_compliance_keywords(self):
        config = load_config()
        scores = match_keywords("generate ATO NIST STIG artifacts",
                                config["categories"])
        assert scores.get("compliance", 0.0) > 0.0

    def test_infrastructure_keywords(self):
        config = load_config()
        scores = match_keywords("deploy to kubernetes using terraform",
                                config["categories"])
        assert scores.get("infrastructure", 0.0) > 0.0

    def test_requirements_keywords(self):
        config = load_config()
        scores = match_keywords("start requirements intake session",
                                config["categories"])
        assert scores.get("requirements", 0.0) > 0.0

    def test_maintenance_keywords(self):
        config = load_config()
        scores = match_keywords("scan for vulnerability CVE updates",
                                config["categories"])
        assert scores.get("maintenance", 0.0) > 0.0

    def test_no_match_returns_zeros(self):
        config = load_config()
        scores = match_keywords("xyzzy foobar quux", config["categories"])
        assert all(s == 0.0 for s in scores.values())

    def test_case_insensitive(self):
        config = load_config()
        scores_lower = match_keywords("python build", config["categories"])
        scores_upper = match_keywords("PYTHON BUILD", config["categories"])
        assert scores_lower.get("build", 0.0) == scores_upper.get("build", 0.0)

    def test_multi_word_keywords(self):
        config = load_config()
        scores = match_keywords("check the supply chain vendors",
                                config["categories"])
        assert scores.get("maintenance", 0.0) > 0.0

    def test_dashboard_keywords(self):
        config = load_config()
        scores = match_keywords("update the dashboard kanban board",
                                config["categories"])
        assert scores.get("dashboard", 0.0) > 0.0


# ---------------------------------------------------------------------------
# File detection
# ---------------------------------------------------------------------------

class TestDetectFromFiles:
    def test_python_files(self, tmp_path):
        (tmp_path / "app.py").write_text("# test", encoding="utf-8")
        (tmp_path / "test.py").write_text("# test", encoding="utf-8")
        config = load_config()
        scores = detect_from_files(str(tmp_path), config)
        assert scores.get("build", 0.0) > 0.0

    def test_terraform_files(self, tmp_path):
        (tmp_path / "main.tf").write_text("# test", encoding="utf-8")
        config = load_config()
        scores = detect_from_files(str(tmp_path), config)
        assert scores.get("infrastructure", 0.0) > 0.0

    def test_feature_files(self, tmp_path):
        (tmp_path / "login.feature").write_text("Feature: Login",
                                                encoding="utf-8")
        config = load_config()
        scores = detect_from_files(str(tmp_path), config)
        # .feature maps to both build and requirements
        assert "build" in scores or "requirements" in scores

    def test_empty_dir(self, tmp_path):
        config = load_config()
        scores = detect_from_files(str(tmp_path), config)
        assert scores == {}

    def test_nonexistent_dir(self):
        config = load_config()
        scores = detect_from_files("/nonexistent/path", config)
        assert scores == {}


# ---------------------------------------------------------------------------
# Main selection
# ---------------------------------------------------------------------------

class TestSelectSkills:
    def test_build_query_returns_build(self):
        result = select_skills(query="fix the python tests")
        assert result["status"] == "ok"
        cats = [c["name"] for c in result["matched_categories"]
                if c["score"] > 0]
        assert "build" in cats

    def test_compliance_query_returns_compliance(self):
        result = select_skills(query="generate NIST compliance SSP report")
        cats = [c["name"] for c in result["matched_categories"]
                if c["score"] > 0]
        assert "compliance" in cats

    def test_unknown_query_falls_back(self):
        result = select_skills(query="xyzzy foobar quux gibberish")
        assert result["status"] == "fallback_all"

    def test_always_include_present(self):
        result = select_skills(query="deploy to production")
        assert "commit" in result["commands"]
        assert "pull_request" in result["commands"]

    def test_returns_classification(self):
        result = select_skills(query="test")
        assert result["classification"] == "CUI // SP-CTI"

    def test_returns_expected_keys(self):
        result = select_skills(query="build code")
        expected_keys = ["status", "matched_categories", "commands",
                         "goals", "context_dirs", "confidence"]
        for key in expected_keys:
            assert key in result, f"Missing key: {key}"

    def test_combined_query_and_project(self, tmp_path):
        (tmp_path / "app.py").write_text("# test", encoding="utf-8")
        result = select_skills(query="build", project_dir=str(tmp_path))
        assert result["status"] == "ok"

    def test_goals_populated_for_build(self):
        result = select_skills(query="implement a new feature in python")
        assert len(result["goals"]) > 0

    def test_multiple_categories_matched(self):
        result = select_skills(
            query="deploy the compliance scanner to kubernetes")
        cats = [c["name"] for c in result["matched_categories"]
                if c["score"] > 0]
        assert len(cats) >= 2


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

class TestResolvePaths:
    def test_resolves_existing_commands(self):
        result = select_skills(query="commit changes")
        resolved = _resolve_paths(result)
        # commit.md should exist in .claude/commands/
        if resolved.get("command_paths"):
            for p in resolved["command_paths"]:
                assert Path(p).exists()

    def test_reports_missing_items(self):
        result = {
            "commands": ["nonexistent_command_xyz"],
            "goals": ["nonexistent_goal.md"],
            "context_dirs": ["nonexistent_dir"],
        }
        resolved = _resolve_paths(result)
        assert len(resolved["missing_items"]) > 0


# ---------------------------------------------------------------------------
# Context formatting
# ---------------------------------------------------------------------------

class TestFormatInjectionContext:
    def test_returns_markdown(self):
        result = select_skills(query="build python code")
        md = format_injection_context(result)
        assert isinstance(md, str)
        assert "CUI" in md

    def test_contains_categories(self):
        result = select_skills(query="build python code")
        md = format_injection_context(result)
        assert "Active Categories" in md or "Available Commands" in md

    def test_fallback_note(self):
        result = select_skills(query="xyzzy gibberish")
        md = format_injection_context(result)
        assert "fallback" in md.lower() or "all items" in md.lower()
