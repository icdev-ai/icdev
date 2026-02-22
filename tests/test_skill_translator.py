#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/dx/skill_translator.py."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.dx.skill_translator import (
    list_skills,
    parse_claude_skill,
    translate_skills,
)


def _create_test_skill(tmp_path, name="test-skill", description="A test skill"):
    """Create a minimal Claude Code skill for testing."""
    skill_dir = tmp_path / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(f"""---
name: {name}
description: {description}
context: fork
allowed-tools: Bash, Read, Write, Edit
---

# /{name} — Test Skill

## Usage
```
/{name} <args>
```

## What This Does
This skill does something useful for testing.
1. Step one
2. Step two

## Steps

### 1. Load Context
```bash
python tools/project/session_context_builder.py --format markdown
```
Load project context first.

### 2. Execute Task
Use the `run_tests` MCP tool from icdev-builder:
- Run the test suite
- Check coverage

### 3. Report Results
Display summary of what was done.

## Hard Prompts Referenced
- `hardprompts/builder/test_generation.md`

## Example
```
/{name} "user authentication"
```

## Error Handling
- If step 2 fails: retry once, then ask for guidance
""", encoding="utf-8")
    return str(skill_dir)


class TestParseClaudeSkill:
    def test_parse_frontmatter(self, tmp_path):
        _create_test_skill(tmp_path)
        data = parse_claude_skill(tmp_path / ".claude" / "skills" / "test-skill")
        assert data["name"] == "test-skill"
        assert data["description"] == "A test skill"
        assert data["context"] == "fork"
        assert "Bash" in data["allowed_tools"]

    def test_parse_steps(self, tmp_path):
        _create_test_skill(tmp_path)
        data = parse_claude_skill(tmp_path / ".claude" / "skills" / "test-skill")
        assert len(data["steps"]) >= 2
        assert data["steps"][0]["title"] == "Load Context"

    def test_parse_returns_none_missing(self, tmp_path):
        result = parse_claude_skill(tmp_path / "nonexistent")
        assert result is None


class TestListSkills:
    def test_lists_available_skills(self, tmp_path):
        _create_test_skill(tmp_path, "skill-a")
        _create_test_skill(tmp_path, "skill-b")
        skills = list_skills(str(tmp_path / ".claude" / "skills"))
        assert "skill-a" in skills
        assert "skill-b" in skills

    def test_lists_empty_for_missing_dir(self, tmp_path):
        skills = list_skills(str(tmp_path / "nonexistent"))
        assert skills == []


class TestTranslateSkills:
    def test_codex_skill_format(self, tmp_path):
        _create_test_skill(tmp_path)
        results = translate_skills(
            directory=str(tmp_path), platforms=["codex"], skills=["test-skill"]
        )
        content = results["codex"]["test-skill"]["content"]
        assert content.startswith("---")
        assert "name: test-skill" in content
        assert "$test-skill" in content  # Codex uses $ prefix
        assert ".agents/skills/" in results["codex"]["test-skill"]["path"]

    def test_copilot_prompt_format(self, tmp_path):
        _create_test_skill(tmp_path)
        results = translate_skills(
            directory=str(tmp_path), platforms=["copilot"], skills=["test-skill"]
        )
        content = results["copilot"]["test-skill"]["content"]
        assert "mode: agent" in content
        assert ".github/prompts/" in results["copilot"]["test-skill"]["path"]

    def test_cursor_mdc_format(self, tmp_path):
        _create_test_skill(tmp_path)
        results = translate_skills(
            directory=str(tmp_path), platforms=["cursor"], skills=["test-skill"]
        )
        content = results["cursor"]["test-skill"]["content"]
        assert content.startswith("---")
        assert "description:" in content
        assert ".cursor/rules/" in results["cursor"]["test-skill"]["path"]

    def test_all_platforms_translate(self, tmp_path):
        _create_test_skill(tmp_path)
        results = translate_skills(
            directory=str(tmp_path), platforms=["all"], skills=["test-skill"]
        )
        assert "codex" in results
        assert "copilot" in results
        assert "cursor" in results

    def test_step_content_preserved(self, tmp_path):
        _create_test_skill(tmp_path)
        results = translate_skills(
            directory=str(tmp_path), platforms=["codex"], skills=["test-skill"]
        )
        content = results["codex"]["test-skill"]["content"]
        assert "Load Context" in content
        assert "Execute Task" in content

    def test_write_creates_files(self, tmp_path):
        _create_test_skill(tmp_path)
        results = translate_skills(
            directory=str(tmp_path), platforms=["codex"],
            skills=["test-skill"], write=True,
        )
        assert results["codex"]["test-skill"]["written"] is True
        skill_file = tmp_path / ".agents" / "skills" / "test-skill" / "SKILL.md"
        assert skill_file.exists()

    def test_unknown_platform_returns_error(self, tmp_path):
        _create_test_skill(tmp_path)
        results = translate_skills(
            directory=str(tmp_path), platforms=["nonexistent"],
            skills=["test-skill"],
        )
        assert "error" in results["nonexistent"]
