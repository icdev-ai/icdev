#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/dx/instruction_generator.py."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.dx.instruction_generator import (
    TEMPLATES,
    collect_project_data,
    generate_instructions,
)


def _write_manifest(tmp_dir, content=None):
    """Write a test icdev.yaml."""
    if content is None:
        content = {
            "version": 1,
            "project": {"name": "test-app", "type": "api", "language": "python"},
            "impact_level": "IL4",
        }
    path = Path(tmp_dir) / "icdev.yaml"
    try:
        import yaml
        path.write_text(yaml.dump(content), encoding="utf-8")
    except ImportError:
        path.write_text(json.dumps(content), encoding="utf-8")


class TestCollectProjectData:
    def test_collects_from_manifest(self, tmp_path):
        _write_manifest(tmp_path)
        data = collect_project_data(str(tmp_path))
        assert data["project_name"] == "test-app"
        assert data["impact_level"] == "IL4"
        assert data["has_icdev_yaml"] is True

    def test_default_values_without_manifest(self, tmp_path):
        data = collect_project_data(str(tmp_path))
        assert data["project_name"] == "ICDEV Project"
        assert data["has_icdev_yaml"] is False

    def test_counts_mcp_servers(self, tmp_path):
        mcp = {"mcpServers": {"core": {}, "builder": {}, "compliance": {}}}
        (tmp_path / ".mcp.json").write_text(json.dumps(mcp), encoding="utf-8")
        data = collect_project_data(str(tmp_path))
        assert data["mcp_server_count"] == 3
        assert "core" in data["mcp_server_names"]


class TestGenerateInstructions:
    def test_agents_md_contains_project(self, tmp_path):
        _write_manifest(tmp_path)
        results = generate_instructions(directory=str(tmp_path), platforms=["codex"])
        content = results["codex"]["content"]
        assert "test-app" in content
        assert "AGENTS.md" in results["codex"]["path"]

    def test_gemini_md_content(self, tmp_path):
        _write_manifest(tmp_path)
        results = generate_instructions(directory=str(tmp_path), platforms=["gemini"])
        content = results["gemini"]["content"]
        assert "GEMINI.md" in content or "Gemini" in content
        assert "test-app" in content

    def test_cursor_mdc_frontmatter(self, tmp_path):
        _write_manifest(tmp_path)
        results = generate_instructions(directory=str(tmp_path), platforms=["cursor"])
        content = results["cursor"]["content"]
        assert content.startswith("---")
        assert "alwaysApply: true" in content
        assert "description:" in content

    def test_copilot_instructions(self, tmp_path):
        _write_manifest(tmp_path)
        results = generate_instructions(directory=str(tmp_path), platforms=["copilot"])
        content = results["copilot"]["content"]
        assert "test-app" in content
        assert ".github/copilot-instructions.md" in results["copilot"]["path"]

    def test_all_platforms_generate(self, tmp_path):
        _write_manifest(tmp_path)
        results = generate_instructions(directory=str(tmp_path), platforms=["all"])
        # Should generate for all non-skipped platforms
        assert len(results) >= 8

    def test_write_flag_creates_files(self, tmp_path):
        _write_manifest(tmp_path)
        results = generate_instructions(
            directory=str(tmp_path), platforms=["codex"], write=True
        )
        assert results["codex"]["written"] is True
        assert (tmp_path / "AGENTS.md").exists()

    def test_dry_run_no_write(self, tmp_path):
        _write_manifest(tmp_path)
        results = generate_instructions(
            directory=str(tmp_path), platforms=["codex"],
            write=True, dry_run=True,
        )
        assert results["codex"]["written"] is False
        assert not (tmp_path / "AGENTS.md").exists()

    def test_unknown_platform_returns_error(self, tmp_path):
        results = generate_instructions(
            directory=str(tmp_path), platforms=["nonexistent"]
        )
        assert "error" in results["nonexistent"]

    def test_cui_marking_in_output(self, tmp_path):
        _write_manifest(tmp_path)
        results = generate_instructions(directory=str(tmp_path), platforms=["codex"])
        content = results["codex"]["content"]
        # IL4 requires CUI markings
        assert "CUI" in content

    def test_conventions_md_for_aider(self, tmp_path):
        _write_manifest(tmp_path)
        results = generate_instructions(directory=str(tmp_path), platforms=["aider"])
        content = results["aider"]["content"]
        assert "CONVENTIONS.md" in content or "Coding conventions" in content.lower()
        assert "CONVENTIONS.md" in results["aider"]["path"]

    def test_windsurf_content(self, tmp_path):
        _write_manifest(tmp_path)
        results = generate_instructions(directory=str(tmp_path), platforms=["windsurf"])
        assert "windsurf" in results
        assert "content" in results["windsurf"]

    def test_amazon_q_content(self, tmp_path):
        _write_manifest(tmp_path)
        results = generate_instructions(directory=str(tmp_path), platforms=["amazon_q"])
        content = results["amazon_q"]["content"]
        assert "aws" in content.lower() or "GovCloud" in content


class TestTemplateRegistry:
    def test_all_expected_platforms_have_templates(self):
        expected = ["codex", "gemini", "copilot", "cursor", "windsurf",
                     "amazon_q", "junie", "cline", "aider"]
        for platform in expected:
            assert platform in TEMPLATES, f"Missing template for {platform}"

    def test_claude_code_not_in_templates(self):
        """Claude Code is excluded — CLAUDE.md is maintained manually."""
        assert "claude_code" not in TEMPLATES
