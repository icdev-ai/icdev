#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/dx/companion.py."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.dx.companion import setup_companion


def _setup_project(tmp_path):
    """Create a minimal ICDEV project structure for testing."""
    # icdev.yaml
    icdev_yaml = tmp_path / "icdev.yaml"
    icdev_yaml.write_text(json.dumps({
        "version": 1,
        "impact_level": "IL4",
        "project": {"name": "test-project", "type": "api", "language": "python"},
        "companion": {"tools": ["codex", "cursor"]},
    }), encoding="utf-8")

    # .mcp.json
    mcp_json = tmp_path / ".mcp.json"
    mcp_json.write_text(json.dumps({
        "mcpServers": {
            "icdev-core": {
                "command": "python",
                "args": ["tools/mcp/core_server.py"],
            }
        }
    }), encoding="utf-8")

    # A Claude Code skill
    skill_dir = tmp_path / ".claude" / "skills" / "test-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("""---
name: test-skill
description: A test skill
context: fork
allowed-tools: Bash, Read
---

# /test-skill

## What This Does
This skill does something useful.

## Steps

### 1. Load Context
```bash
python tools/project/session_context_builder.py --format markdown
```
Load project context first.

### 2. Execute
Run the task.
""", encoding="utf-8")

    return tmp_path


class TestSetupCompanionDetectOnly:
    def test_detect_returns_detection_results(self, tmp_path):
        _setup_project(tmp_path)
        result = setup_companion(directory=str(tmp_path), detect=True)
        assert "detected_tools" in result
        assert "detected" in result["detected_tools"]

    def test_detect_does_not_generate_files(self, tmp_path):
        _setup_project(tmp_path)
        result = setup_companion(directory=str(tmp_path), detect=True)
        # detect-only mode returns early — no instruction_files key
        assert "instruction_files" not in result


class TestSetupCompanionFull:
    def test_full_setup_returns_all_sections(self, tmp_path):
        _setup_project(tmp_path)
        result = setup_companion(
            directory=str(tmp_path), platforms=["codex"], write=False,
        )
        assert "detected_tools" in result
        assert "instruction_files" in result
        assert "mcp_configs" in result
        assert "skill_translations" in result
        assert "project_data" in result
        assert "summary" in result

    def test_summary_counts(self, tmp_path):
        _setup_project(tmp_path)
        result = setup_companion(
            directory=str(tmp_path), platforms=["codex"], write=False,
        )
        summary = result["summary"]
        assert summary["platforms_targeted"] >= 1
        assert summary["project_name"] == "test-project"

    def test_write_flag_increments_written_counts(self, tmp_path):
        _setup_project(tmp_path)
        result = setup_companion(
            directory=str(tmp_path), platforms=["codex"], write=True,
        )
        summary = result["summary"]
        assert summary["instruction_files_written"] >= 1

    def test_all_platforms_flag(self, tmp_path):
        _setup_project(tmp_path)
        result = setup_companion(
            directory=str(tmp_path), platforms=["all"], write=False,
        )
        assert result["summary"]["platforms_targeted"] >= 3

    def test_project_data_includes_icdev_yaml(self, tmp_path):
        _setup_project(tmp_path)
        result = setup_companion(
            directory=str(tmp_path), platforms=["codex"], write=False,
        )
        assert result["project_data"]["has_icdev_yaml"] is True
        assert result["project_data"]["impact_level"] == "IL4"

    def test_content_stripped_from_output(self, tmp_path):
        _setup_project(tmp_path)
        result = setup_companion(
            directory=str(tmp_path), platforms=["codex"], write=False,
        )
        # instruction_files should not include raw content (stripped for summary)
        for platform, info in result["instruction_files"].items():
            if isinstance(info, dict):
                assert "content" not in info

    def test_default_platforms_when_none_specified(self, tmp_path):
        _setup_project(tmp_path)
        result = setup_companion(
            directory=str(tmp_path), platforms=None, write=False,
        )
        # Should default to top 4 when nothing detected
        assert result["summary"]["platforms_targeted"] >= 1
