#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/dx/tool_detector.py."""

import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.dx.tool_detector import detect_tools


class TestDetectTools:
    def test_detect_claude_code_from_dir(self, tmp_path):
        """Claude Code detected when .claude/ dir exists."""
        (tmp_path / ".claude").mkdir()
        result = detect_tools(directory=str(tmp_path))
        tool_ids = [t["tool_id"] for t in result["detected"]]
        assert "claude_code" in tool_ids

    def test_detect_codex_from_dir(self, tmp_path):
        """Codex detected when .codex/ dir exists."""
        (tmp_path / ".codex").mkdir()
        result = detect_tools(directory=str(tmp_path))
        tool_ids = [t["tool_id"] for t in result["detected"]]
        assert "codex" in tool_ids

    def test_detect_cursor_from_dir(self, tmp_path):
        """Cursor detected when .cursor/ dir exists."""
        (tmp_path / ".cursor").mkdir()
        result = detect_tools(directory=str(tmp_path))
        tool_ids = [t["tool_id"] for t in result["detected"]]
        assert "cursor" in tool_ids

    def test_detect_nothing_empty_dir(self, tmp_path, monkeypatch):
        """No tools detected in empty directory."""
        # Clear env vars that could trigger false detection
        for key in list(os.environ):
            if any(tok in key.upper() for tok in (
                "ANTHROPIC", "OPENAI", "CLAUDE", "CURSOR", "CODEX",
                "COPILOT", "WINDSURF", "AIDER", "AMAZON_Q", "GEMINI",
            )):
                monkeypatch.delenv(key, raising=False)
        result = detect_tools(directory=str(tmp_path))
        assert result["detected"] == []
        assert result["primary"] is None

    def test_detect_multiple_tools(self, tmp_path):
        """Multiple tools detected when multiple config dirs exist."""
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".cursor").mkdir()
        result = detect_tools(directory=str(tmp_path))
        assert len(result["detected"]) >= 2

    def test_primary_is_highest_confidence(self, tmp_path):
        """Primary tool is the one with highest confidence."""
        (tmp_path / ".claude").mkdir()
        # Create CLAUDE.md for extra evidence
        (tmp_path / "CLAUDE.md").write_text("# CLAUDE.md", encoding="utf-8")
        result = detect_tools(directory=str(tmp_path))
        if result["detected"]:
            assert result["primary"] == result["detected"][0]["tool_id"]

    def test_all_tools_lists_registry(self, tmp_path):
        """all_tools contains all registered tools from registry."""
        result = detect_tools(directory=str(tmp_path))
        assert "claude_code" in result["all_tools"]
        assert "codex" in result["all_tools"]
        assert "gemini" in result["all_tools"]
        assert len(result["all_tools"]) >= 10

    def test_detect_aider_from_config_file(self, tmp_path):
        """Aider detected from .aider.conf.yml file."""
        (tmp_path / ".aider.conf.yml").write_text("model: gpt-4", encoding="utf-8")
        result = detect_tools(directory=str(tmp_path))
        tool_ids = [t["tool_id"] for t in result["detected"]]
        assert "aider" in tool_ids

    def test_confidence_increases_with_evidence(self, tmp_path):
        """More evidence gives higher confidence."""
        (tmp_path / ".claude").mkdir()
        result1 = detect_tools(directory=str(tmp_path))
        c1 = next(t for t in result1["detected"] if t["tool_id"] == "claude_code")

        # Add more evidence
        (tmp_path / "CLAUDE.md").write_text("# x", encoding="utf-8")
        result2 = detect_tools(directory=str(tmp_path))
        c2 = next(t for t in result2["detected"] if t["tool_id"] == "claude_code")

        assert c2["confidence"] >= c1["confidence"]

    def test_result_structure(self, tmp_path):
        """Result has expected keys."""
        result = detect_tools(directory=str(tmp_path))
        assert "detected" in result
        assert "primary" in result
        assert "all_tools" in result
        assert "directory" in result
