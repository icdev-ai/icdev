#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for OpenClaw-to-ICDEV™ compatibility checker and translator."""

import sys
import textwrap
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.marketplace.openclaw_compat import (  # noqa: E402
    check_compatibility,
    translate_to_icdev,
    validate_translated_skill,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def clean_oc_skill(tmp_path):
    """Clean OpenClaw skill with standard format."""
    d = tmp_path / "my-skill"
    d.mkdir()
    (d / "skill.md").write_text(
        textwrap.dedent("""\
        ---
        name: my-test-skill
        description: A useful skill for testing
        version: "1.0.0"
        author: test-author
        tags:
          - testing
        tools:
          - Read
          - Grep
          - Bash
        ---

        # My Test Skill

        ## Steps

        ### 1. Read the file
        Use the Read tool to load the target file.

        ### 2. Search for patterns
        Use Grep to find matching content.
    """),
        encoding="utf-8",
    )
    return d


@pytest.fixture
def oc_skill_with_node(tmp_path):
    """OpenClaw skill with Node.js patterns."""
    d = tmp_path / "node-skill"
    d.mkdir()
    (d / "skill.md").write_text(
        textwrap.dedent("""\
        ---
        name: node-helper
        description: A Node.js helper skill
        version: "2.0.0"
        author: node-dev
        tools:
          - run_command
          - read_file
          - web_search
        ---

        # Node Helper

        Run `npm install express` to set up the server.
        Then use `node server.js` to start.
        Check `console.log` output for errors.
    """),
        encoding="utf-8",
    )
    return d


@pytest.fixture
def oc_skill_with_js_scripts(tmp_path):
    """OpenClaw skill with JavaScript scripts (blocker)."""
    d = tmp_path / "js-skill"
    d.mkdir()
    (d / "skill.md").write_text(
        textwrap.dedent("""\
        ---
        name: js-skill
        description: A skill with JS scripts
        version: "1.0.0"
        author: js-dev
        ---

        # JS Skill
    """),
        encoding="utf-8",
    )
    scripts = d / "scripts"
    scripts.mkdir()
    (scripts / "helper.js").write_text("console.log('hello');", encoding="utf-8")
    return d


@pytest.fixture
def oc_skill_with_npm(tmp_path):
    """OpenClaw skill with package.json (blocker)."""
    d = tmp_path / "npm-skill"
    d.mkdir()
    (d / "skill.md").write_text(
        textwrap.dedent("""\
        ---
        name: npm-skill
        description: Depends on npm
        version: "1.0.0"
        ---

        # NPM Skill
    """),
        encoding="utf-8",
    )
    (d / "package.json").write_text('{"name": "npm-skill", "dependencies": {"axios": "^1.0.0"}}', encoding="utf-8")
    return d


@pytest.fixture
def oc_skill_with_claw_syntax(tmp_path):
    """OpenClaw skill with OpenClaw-specific syntax."""
    d = tmp_path / "claw-skill"
    d.mkdir()
    (d / "skill.md").write_text(
        textwrap.dedent("""\
        ---
        name: claw-skill
        description: Uses OpenClaw APIs
        version: "1.0.0"
        tools:
          - browse
          - shell
        ---

        # Claw Skill

        Ask @claw to browse the website.
        Use claw.memory to recall previous results.
        Then claw.run the analysis script.
    """),
        encoding="utf-8",
    )
    return d


@pytest.fixture
def oc_skill_incompatible_tools(tmp_path):
    """OpenClaw skill with incompatible capabilities."""
    d = tmp_path / "gui-skill"
    d.mkdir()
    (d / "skill.md").write_text(
        textwrap.dedent("""\
        ---
        name: gui-automation
        description: GUI automation skill
        version: "1.0.0"
        tools:
          - browser_control
          - mouse_control
          - screen_capture
          - Read
        ---

        # GUI Automation

        Click the button and take a screenshot.
    """),
        encoding="utf-8",
    )
    return d


@pytest.fixture
def oc_skill_no_frontmatter(tmp_path):
    """OpenClaw skill with no YAML frontmatter."""
    d = tmp_path / "bare-skill"
    d.mkdir()
    (d / "skill.md").write_text(
        textwrap.dedent("""\
        # Bare Skill

        This skill has no frontmatter.
        Just raw instructions.
    """),
        encoding="utf-8",
    )
    return d


# ---------------------------------------------------------------------------
# Compatibility Check Tests
# ---------------------------------------------------------------------------
class TestCompatibilityCheck:
    """Test compatibility analysis."""

    def test_clean_skill_compatible(self, clean_oc_skill):
        """Clean skill with standard tools is compatible."""
        report = check_compatibility(clean_oc_skill)
        assert report.compatible is True
        assert report.score >= 90
        assert len(report.blockers) == 0

    def test_node_patterns_detected(self, oc_skill_with_node):
        """Node.js patterns are flagged as warnings."""
        report = check_compatibility(oc_skill_with_node)
        assert report.compatible is True  # Warnings, not blockers
        assert len(report.node_patterns) > 0
        assert any(w["code"] == "NODE_001" for w in report.warnings)

    def test_tool_mapping(self, oc_skill_with_node):
        """OpenClaw tools are mapped to ICDEV™ equivalents."""
        report = check_compatibility(oc_skill_with_node)
        assert "run_command" in report.tool_mapping
        assert report.tool_mapping["run_command"] == "Bash"
        assert "read_file" in report.tool_mapping
        assert report.tool_mapping["read_file"] == "Read"
        assert "web_search" in report.tool_mapping
        assert report.tool_mapping["web_search"] == "WebSearch"

    def test_js_scripts_blocker(self, oc_skill_with_js_scripts):
        """JavaScript scripts are a blocking incompatibility."""
        report = check_compatibility(oc_skill_with_js_scripts)
        assert report.compatible is False
        assert any(b["code"] == "NODE_002" for b in report.blockers)

    def test_npm_dependency_blocker(self, oc_skill_with_npm):
        """package.json is a blocking incompatibility."""
        report = check_compatibility(oc_skill_with_npm)
        assert report.compatible is False
        assert any(b["code"] == "NODE_004" for b in report.blockers)

    def test_claw_syntax_warned(self, oc_skill_with_claw_syntax):
        """OpenClaw-specific syntax (@claw, claw.memory) is warned."""
        report = check_compatibility(oc_skill_with_claw_syntax)
        assert report.compatible is True
        assert any(w["code"] == "OC_002" for w in report.warnings)  # @claw
        assert any(w["code"] == "OC_004" for w in report.warnings)  # claw.memory

    def test_incompatible_tools_warned(self, oc_skill_incompatible_tools):
        """Incompatible capabilities are warned (not blocked)."""
        report = check_compatibility(oc_skill_incompatible_tools)
        assert report.compatible is True
        assert any(w["code"] == "TOOL_002" for w in report.warnings)
        # Read should still be mapped
        assert "Read" in report.tool_mapping.values()

    def test_no_frontmatter_warned(self, oc_skill_no_frontmatter):
        """Missing frontmatter is a warning, not a blocker."""
        report = check_compatibility(oc_skill_no_frontmatter)
        assert report.compatible is True
        assert any(w["code"] == "PARSE_003" for w in report.warnings)

    def test_not_a_directory(self, tmp_path):
        """Non-directory path is a blocker."""
        report = check_compatibility(tmp_path / "nonexistent")
        assert report.compatible is False
        assert report.blockers[0]["code"] == "STRUCT_001"

    def test_no_skill_md(self, tmp_path):
        """Empty directory is a blocker."""
        d = tmp_path / "empty"
        d.mkdir()
        report = check_compatibility(d)
        assert report.compatible is False
        assert report.blockers[0]["code"] == "STRUCT_002"

    def test_score_degrades_with_issues(self, oc_skill_incompatible_tools):
        """Score decreases with warnings and incompatibilities."""
        report = check_compatibility(oc_skill_incompatible_tools)
        assert report.score < 100


# ---------------------------------------------------------------------------
# Translation Tests
# ---------------------------------------------------------------------------
class TestTranslation:
    """Test skill translation."""

    def test_translate_clean_skill(self, clean_oc_skill, tmp_path):
        """Clean skill translates successfully."""
        out = tmp_path / "output"
        result = translate_to_icdev(clean_oc_skill, out)
        assert result["success"] is True
        assert (out / "SKILL.md").exists()

        content = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "CUI // SP-CTI" in content
        assert "## Provenance" in content
        assert "OpenClaw Community" in content

    def test_translate_maps_tools(self, oc_skill_with_node, tmp_path):
        """Translation maps OpenClaw tools to ICDEV™ equivalents."""
        out = tmp_path / "output"
        result = translate_to_icdev(oc_skill_with_node, out)
        assert result["success"] is True
        assert "Bash" in result["tools_mapped"].values()
        assert "Read" in result["tools_mapped"].values()

    def test_translate_node_commands(self, oc_skill_with_node, tmp_path):
        """Node.js commands are replaced with Python equivalents."""
        out = tmp_path / "output"
        result = translate_to_icdev(oc_skill_with_node, out)
        assert result["success"] is True

        content = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "pip install" in content  # npm install → pip install
        assert "python server.js" in content  # node server.js → python server.js
        assert "print" in content  # console.log → print

    def test_translate_claw_syntax(self, oc_skill_with_claw_syntax, tmp_path):
        """OpenClaw syntax is replaced with ICDEV™ equivalents in instructions."""
        out = tmp_path / "output"
        result = translate_to_icdev(oc_skill_with_claw_syntax, out)
        assert result["success"] is True

        content = (out / "SKILL.md").read_text(encoding="utf-8")
        # Extract just the Instructions section (not Compatibility Notes which quotes originals)
        instructions = (
            content.split("## Instructions")[1].split("## Compatibility")[0] if "## Instructions" in content else ""
        )
        assert "@claw" not in instructions
        assert "Claude" in instructions  # @claw → Claude
        assert "claw.memory" not in instructions
        assert "memory system" in instructions  # claw.memory → memory system

    def test_translate_blocked_by_js(self, oc_skill_with_js_scripts, tmp_path):
        """Skills with JS scripts cannot be translated."""
        out = tmp_path / "output"
        result = translate_to_icdev(oc_skill_with_js_scripts, out)
        assert result["success"] is False
        assert "blocking incompatibilities" in result["error"]

    def test_translate_adds_cui_banner(self, clean_oc_skill, tmp_path):
        """Translated skill includes CUI // SP-CTI banner."""
        out = tmp_path / "output"
        translate_to_icdev(clean_oc_skill, out)
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "CUI // SP-CTI" in content

    def test_translate_preserves_tags(self, clean_oc_skill, tmp_path):
        """Tags from original skill are preserved."""
        out = tmp_path / "output"
        translate_to_icdev(clean_oc_skill, out)
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "testing" in content

    def test_translate_no_frontmatter(self, oc_skill_no_frontmatter, tmp_path):
        """Skills without frontmatter get default values."""
        out = tmp_path / "output"
        result = translate_to_icdev(oc_skill_no_frontmatter, out)
        assert result["success"] is True
        assert result["icdev_name"].startswith("bare-skill") or "oc-" in result["icdev_name"]

    def test_translate_includes_compat_notes(self, oc_skill_with_claw_syntax, tmp_path):
        """Translated skill includes compatibility notes section."""
        out = tmp_path / "output"
        translate_to_icdev(oc_skill_with_claw_syntax, out)
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "## Compatibility Notes" in content

    def test_translate_default_tools(self, oc_skill_no_frontmatter, tmp_path):
        """Skills with no tools get safe defaults."""
        out = tmp_path / "output"
        result = translate_to_icdev(oc_skill_no_frontmatter, out)
        assert result["success"] is True
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "Read" in content or "Grep" in content or "Glob" in content


# ---------------------------------------------------------------------------
# License Compliance Tests
# ---------------------------------------------------------------------------
class TestLicenseCompliance:
    """Test license compatibility checks."""

    def test_permissive_license_ok(self, tmp_path):
        """MIT-0 license is compatible with all ILs."""
        d = tmp_path / "mit-skill"
        d.mkdir()
        (d / "skill.md").write_text(
            textwrap.dedent("""\
            ---
            name: mit-skill
            description: MIT licensed
            license: MIT-0
            ---
            # MIT Skill
            Do things.
        """),
            encoding="utf-8",
        )
        report = check_compatibility(d)
        assert report.compatible is True
        assert not any(w["code"].startswith("LIC_001") for w in report.warnings)

    def test_copyleft_license_warned(self, tmp_path):
        """GPL license is warned for IL5/IL6."""
        d = tmp_path / "gpl-skill"
        d.mkdir()
        (d / "skill.md").write_text(
            textwrap.dedent("""\
            ---
            name: gpl-skill
            description: GPL licensed
            license: GPL-3.0
            ---
            # GPL Skill
            Do things.
        """),
            encoding="utf-8",
        )
        report = check_compatibility(d)
        assert any(w["code"] == "LIC_001" for w in report.warnings)

    def test_no_license_warned(self, tmp_path):
        """Missing license is warned."""
        d = tmp_path / "no-lic"
        d.mkdir()
        (d / "skill.md").write_text(
            textwrap.dedent("""\
            ---
            name: no-license-skill
            description: No license
            ---
            # No License
            Do things.
        """),
            encoding="utf-8",
        )
        report = check_compatibility(d)
        assert any(w["code"] == "LIC_003" for w in report.warnings)


# ---------------------------------------------------------------------------
# Functional Validation Tests
# ---------------------------------------------------------------------------
class TestFunctionalValidation:
    """Test post-translation functional checks."""

    def test_valid_translated_skill(self, clean_oc_skill, tmp_path):
        """Clean translated skill passes all functional checks."""
        out = tmp_path / "output"
        translate_to_icdev(clean_oc_skill, out)
        result = validate_translated_skill(out)
        assert result["passed"] is True
        assert result["score"] == 100

    def test_missing_skill_md(self, tmp_path):
        """Missing SKILL.md fails validation."""
        d = tmp_path / "empty"
        d.mkdir()
        result = validate_translated_skill(d)
        assert result["passed"] is False
        assert result["score"] == 0

    def test_checks_all_run(self, clean_oc_skill, tmp_path):
        """All 8 checks are executed."""
        out = tmp_path / "output"
        translate_to_icdev(clean_oc_skill, out)
        result = validate_translated_skill(out)
        assert result["total_checks"] == 8
        assert len(result["findings"]) == 8


# ---------------------------------------------------------------------------
# Real-World Skill Test (pskoett/self-improving-agent)
# ---------------------------------------------------------------------------
class TestSelfImprovingAgent:
    """Test with realistic ClawHub skill: pskoett/self-improving-agent."""

    @pytest.fixture
    def self_improving_skill(self, tmp_path):
        """Simulate the self-improving-agent skill from ClawHub."""
        d = tmp_path / "self-improving-agent"
        d.mkdir()
        (d / "skill.md").write_text(
            textwrap.dedent("""\
            ---
            name: self-improving-agent
            description: "Captures learnings, errors, and corrections to enable continuous improvement"
            version: "3.0.5"
            license: MIT-0
            author: pskoett
            repository: https://github.com/peterskoett/self-improving-agent
            tags:
              - learning
              - self-improvement
              - agent
            tools:
              - read_file
              - write_file
              - run_command
              - search_files
            ---

            # Self-Improving Agent

            ## Overview

            This skill enables AI agents to systematically log failures, user
            corrections, and discoveries into markdown files for iterative refinement.

            ## Workflows

            ### 1. Log a Learning

            When you discover something new or receive a correction:
            1. Read `.learnings/LEARNINGS.md`
            2. Append a new entry with format `LRN-YYYYMMDD-XXX`
            3. Include: what happened, what was learned, how to apply it

            ### 2. Log an Error

            When a command fails or an exception occurs:
            1. Read `.learnings/ERRORS.md`
            2. Append with format `ERR-YYYYMMDD-XXX`
            3. Include: error message, context, resolution

            ### 3. Promote Learnings

            Broadly applicable learnings should graduate to workspace files:
            - `CLAUDE.md` for Claude Code instructions
            - `AGENTS.md` for multi-agent context

            ## Installation

            ```bash
            clawhub install self-improving-agent
            ```

            Or manually:
            ```bash
            git clone https://github.com/peterskoett/self-improving-agent.git
            ```
        """),
            encoding="utf-8",
        )

        # Create scripts directory with shell scripts
        scripts = d / "scripts"
        scripts.mkdir()
        (scripts / "activator.sh").write_text(
            textwrap.dedent("""\
            #!/bin/bash
            # Activates the self-improving agent skill
            echo "Activating self-improving agent..."
            mkdir -p .learnings
            touch .learnings/LEARNINGS.md
            touch .learnings/ERRORS.md
            touch .learnings/FEATURE_REQUESTS.md
        """),
            encoding="utf-8",
        )
        (scripts / "error-detector.sh").write_text(
            textwrap.dedent("""\
            #!/bin/bash
            # Detects errors in the last command output
            echo "Checking for errors..."
        """),
            encoding="utf-8",
        )

        # Create hooks directory with JS/TS hooks
        hooks = d / "hooks" / "openclaw"
        hooks.mkdir(parents=True)
        (hooks / "handler.js").write_text(
            "module.exports = { onError: (err) => console.log(err) };",
            encoding="utf-8",
        )
        (hooks / "handler.ts").write_text(
            "export const onError = (err: Error) => console.log(err);",
            encoding="utf-8",
        )

        # Create .learnings state directory
        learnings = d / ".learnings"
        learnings.mkdir()
        (learnings / "LEARNINGS.md").write_text("# Learnings\n", encoding="utf-8")

        return d

    def test_compatibility_check(self, self_improving_skill):
        """Skill should be compatible (warnings but no blockers)."""
        report = check_compatibility(self_improving_skill)
        assert report.compatible is True
        # Should have warnings for: shell scripts, JS/TS hooks, .learnings dir
        assert any(w["code"] == "SHELL_001" for w in report.warnings)
        assert any(w["code"] == "HOOK_001" for w in report.warnings)
        assert any(w["code"] == "HOOK_002" for w in report.warnings)
        # License should be OK (MIT-0)
        assert not any(w["code"] == "LIC_001" for w in report.warnings)

    def test_tool_mapping(self, self_improving_skill):
        """OpenClaw tools should map correctly."""
        report = check_compatibility(self_improving_skill)
        assert report.tool_mapping.get("read_file") == "Read"
        assert report.tool_mapping.get("write_file") == "Write"
        assert report.tool_mapping.get("run_command") == "Bash"
        assert report.tool_mapping.get("search_files") == "Grep"

    def test_translation(self, self_improving_skill, tmp_path):
        """Skill should translate successfully."""
        out = tmp_path / "translated"
        result = translate_to_icdev(self_improving_skill, out)
        assert result["success"] is True
        assert (out / "SKILL.md").exists()

        content = (out / "SKILL.md").read_text(encoding="utf-8")
        assert "CUI // SP-CTI" in content
        assert "## Provenance" in content
        assert "pskoett" in content
        assert "MIT-0" in content or "3.0.5" in content

    def test_functional_validation(self, self_improving_skill, tmp_path):
        """Translated skill should pass functional validation."""
        out = tmp_path / "translated"
        translate_to_icdev(self_improving_skill, out)
        result = validate_translated_skill(out)
        assert result["passed"] is True
        assert result["score"] == 100

    def test_node_commands_translated(self, self_improving_skill, tmp_path):
        """clawhub install → pip install, git clone preserved."""
        out = tmp_path / "translated"
        translate_to_icdev(self_improving_skill, out)
        content = (out / "SKILL.md").read_text(encoding="utf-8")
        # git clone should be preserved (not a Node.js command)
        assert "git clone" in content

    def test_state_dir_adapted(self, self_improving_skill):
        """The .learnings directory is flagged for adaptation."""
        report = check_compatibility(self_improving_skill)
        assert any(a["code"] == "STATE_001" for a in report.adaptations)

    def test_compatibility_score(self, self_improving_skill):
        """Score should reflect warnings but remain reasonable."""
        report = check_compatibility(self_improving_skill)
        # Has warnings (hooks, shell scripts, state dir) but no blockers
        assert report.score >= 50
        assert report.score < 100
