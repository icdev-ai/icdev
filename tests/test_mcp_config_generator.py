#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/dx/mcp_config_generator.py."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.dx.mcp_config_generator import generate_mcp_config


def _write_mcp_json(tmp_dir):
    """Write a test .mcp.json file."""
    mcp = {
        "mcpServers": {
            "icdev-core": {
                "command": "python",
                "args": ["tools/mcp/core_server.py"],
                "env": {"ICDEV_DB_PATH": "data/icdev.db", "ICDEV_PROJECT_ROOT": "."},
            },
            "icdev-builder": {
                "command": "python",
                "args": ["tools/mcp/builder_server.py"],
                "env": {"ICDEV_DB_PATH": "data/icdev.db", "ICDEV_PROJECT_ROOT": "."},
            },
        }
    }
    (Path(tmp_dir) / ".mcp.json").write_text(json.dumps(mcp, indent=2), encoding="utf-8")


class TestGenerateMcpConfig:
    def test_codex_toml_format(self, tmp_path):
        _write_mcp_json(tmp_path)
        results = generate_mcp_config(
            directory=str(tmp_path), platforms=["codex"]
        )
        content = results["codex"]["content"]
        assert "[mcp.servers.icdev-core]" in content
        assert "[mcp.servers.icdev-builder]" in content
        assert 'command = "python"' in content

    def test_amazon_q_json_format(self, tmp_path):
        _write_mcp_json(tmp_path)
        results = generate_mcp_config(
            directory=str(tmp_path), platforms=["amazon_q"]
        )
        content = results["amazon_q"]["content"]
        parsed = json.loads(content)
        assert "mcpServers" in parsed
        assert "icdev-core" in parsed["mcpServers"]

    def test_gemini_json_format(self, tmp_path):
        _write_mcp_json(tmp_path)
        results = generate_mcp_config(
            directory=str(tmp_path), platforms=["gemini"]
        )
        content = results["gemini"]["content"]
        parsed = json.loads(content)
        assert "mcpServers" in parsed

    def test_cline_json_format(self, tmp_path):
        _write_mcp_json(tmp_path)
        results = generate_mcp_config(
            directory=str(tmp_path), platforms=["cline"]
        )
        content = results["cline"]["content"]
        parsed = json.loads(content)
        assert "mcpServers" in parsed
        # Cline adds disabled flag
        for name, cfg in parsed["mcpServers"].items():
            assert "disabled" in cfg

    def test_ide_setup_instructions(self, tmp_path):
        _write_mcp_json(tmp_path)
        for platform in ["cursor", "windsurf", "junie"]:
            results = generate_mcp_config(
                directory=str(tmp_path), platforms=[platform]
            )
            content = results[platform]["content"]
            assert "icdev-core" in content
            assert "MCP" in content

    def test_all_servers_included(self, tmp_path):
        _write_mcp_json(tmp_path)
        results = generate_mcp_config(
            directory=str(tmp_path), platforms=["codex"]
        )
        assert results["codex"]["server_count"] == 2

    def test_write_creates_file(self, tmp_path):
        _write_mcp_json(tmp_path)
        results = generate_mcp_config(
            directory=str(tmp_path), platforms=["amazon_q"], write=True
        )
        assert results["amazon_q"]["written"] is True
        assert (tmp_path / ".amazonq" / "mcp.json").exists()

    def test_missing_mcp_json_returns_error(self, tmp_path):
        results = generate_mcp_config(directory=str(tmp_path))
        assert "error" in results
