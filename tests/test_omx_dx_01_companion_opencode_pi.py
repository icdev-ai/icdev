#!/usr/bin/env python3
# CUI // SP-CTI
"""omx-dx-01: the companion speaks opencode and Pi; `icdev init` makes AGENTS.md primary.

ICDEV orchestrates harnesses; it is not one. On Omarchy the default harness is
opencode and Pi is a full peer (omx-spike-01), so the companion must emit each
one's OWN MCP config — measured formats, not guesses:

  * opencode: project ``opencode.json``, ``"mcp": {name: {"type": "local",
    "command": [argv...], "enabled": true, "environment": {...}}}``. ``command``
    is ONE argv list and ``env`` is spelled ``environment``.
  * Pi: ``.pi/mcp.json`` in the standard ``{"mcpServers": {...}}`` shape.

Both read AGENTS.md, which is why a scaffolded project now gets AGENTS.md as a
REQUIRED file rather than an optional platform extra.
"""
from __future__ import annotations

import json
from pathlib import Path

from tools.cli import init as init_mod
from tools.dx import companion
from tools.dx.instruction_generator import generate_instructions
from tools.dx.mcp_config_generator import REGISTRY_PATH, _load_yaml, generate_mcp_config

UNIFIED = {
    "command": "python",
    "args": ["tools/mcp/unified_server.py"],
    "env": {"ICDEV_DB_PATH": "data/icdev.db", "ICDEV_PROJECT_ROOT": "."},
}


def _write_mcp_json(root: Path, **extra) -> None:
    servers = {"icdev-unified": UNIFIED, **extra}
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


def test_registry_declares_opencode_and_pi_reading_agents_md():
    companions = _load_yaml(str(REGISTRY_PATH))["companions"]
    for pid, cfg_file in (("opencode", "opencode.json"), ("pi", ".pi/mcp.json")):
        cfg = companions[pid]
        assert cfg["instruction_file"] == "AGENTS.md"
        assert cfg["mcp_support"] is True
        assert cfg["mcp_config_file"] == cfg_file
        assert cfg["skill_directory"] == ".agents/skills"


# --------------------------------------------------------------------------- #
# opencode
# --------------------------------------------------------------------------- #


def test_opencode_points_at_icdev_unified_over_stdio(tmp_path):
    _write_mcp_json(tmp_path)
    res = generate_mcp_config(directory=str(tmp_path), platforms=["opencode"])["opencode"]
    assert res["path"] == "opencode.json"
    cfg = json.loads(res["content"])
    assert cfg["$schema"] == "https://opencode.ai/config.json"
    assert cfg["mcp"]["icdev-unified"] == {
        "type": "local",
        "command": ["python", "tools/mcp/unified_server.py"],
        "enabled": True,
        "environment": UNIFIED["env"],
    }


def test_opencode_remote_server_maps_to_remote_type(tmp_path):
    _write_mcp_json(tmp_path, remote={"url": "https://mcp.example/sse"})
    res = generate_mcp_config(directory=str(tmp_path), platforms=["opencode"])["opencode"]
    assert json.loads(res["content"])["mcp"]["remote"] == {
        "type": "remote", "url": "https://mcp.example/sse", "enabled": True,
    }


def test_opencode_write_replaces_only_the_mcp_key(tmp_path):
    """opencode.json is the LIVE config, not a staging file — keep the operator's
    provider/permission blocks and replace only ``mcp``."""
    _write_mcp_json(tmp_path)
    existing = {
        "$schema": "https://opencode.ai/config.json",
        "provider": {"vllm": {"options": {"baseURL": "http://127.0.0.1:8000/v1"}}},
        "permission": {"bash": "allow"},
        "mcp": {"stale": {"type": "local", "command": ["gone"]}},
    }
    (tmp_path / "opencode.json").write_text(json.dumps(existing), encoding="utf-8")
    res = generate_mcp_config(directory=str(tmp_path), platforms=["opencode"], write=True)
    assert res["opencode"]["written"] is True
    on_disk = json.loads((tmp_path / "opencode.json").read_text(encoding="utf-8"))
    assert on_disk["provider"] == existing["provider"]
    assert on_disk["permission"] == existing["permission"]
    assert set(on_disk["mcp"]) == {"icdev-unified"}


# --------------------------------------------------------------------------- #
# Pi
# --------------------------------------------------------------------------- #


def test_pi_points_at_icdev_unified_over_stdio(tmp_path):
    _write_mcp_json(tmp_path)
    res = generate_mcp_config(directory=str(tmp_path), platforms=["pi"], write=True)["pi"]
    assert res["path"] == ".pi/mcp.json"
    cfg = json.loads((tmp_path / ".pi" / "mcp.json").read_text(encoding="utf-8"))
    assert cfg == {"mcpServers": {"icdev-unified": UNIFIED}}


# --------------------------------------------------------------------------- #
# Instructions + companion end to end
# --------------------------------------------------------------------------- #


def test_instruction_generator_writes_agents_md_for_both(tmp_path):
    res = generate_instructions(directory=str(tmp_path), platforms=["opencode", "pi"])
    assert res["opencode"]["path"] == "AGENTS.md"
    assert res["pi"]["path"] == "AGENTS.md"
    assert "opencode" in res["opencode"]["content"]


def test_companion_setup_writes_both_configs(tmp_path):
    _write_mcp_json(tmp_path)
    out = companion.setup_companion(directory=str(tmp_path), platforms=["opencode", "pi"], write=True)
    assert out["mcp_configs"]["opencode"]["written"] is True
    assert out["mcp_configs"]["pi"]["written"] is True
    assert (tmp_path / "opencode.json").is_file()
    assert (tmp_path / ".pi" / "mcp.json").is_file()
    assert (tmp_path / "AGENTS.md").is_file()


# --------------------------------------------------------------------------- #
# icdev init: AGENTS.md is primary
# --------------------------------------------------------------------------- #


def test_agents_md_is_required_not_optional():
    assert init_mod.BOOTSTRAP_MAP[0] == (init_mod.PRIMARY_INSTRUCTION_SOURCE, "AGENTS.md")
    assert init_mod.PRIMARY_INSTRUCTION_SOURCE not in init_mod.OPTIONAL_SOURCES
    # mapped exactly once — not again as an optional platform extra
    assert [proj for _src, proj in init_mod.BOOTSTRAP_MAP].count("AGENTS.md") == 1


def test_icdev_init_writes_agents_md(tmp_path):
    res = init_mod.init_project(tmp_path, minimal=True)
    agents = tmp_path / "AGENTS.md"
    assert agents.is_file()
    assert agents.read_text(encoding="utf-8").startswith("# AGENTS.md")
    assert (tmp_path / "CLAUDE.md").is_file(), "CLAUDE.md stays alongside AGENTS.md"
    assert res["missing"] == 0


# --------------------------------------------------------------------------- #
# Skills and packaging
# --------------------------------------------------------------------------- #


def test_opencode_and_pi_skills_land_in_the_shared_agents_dir(tmp_path):
    """Both harnesses discover project .agents/skills (omx-spike-01); Pi does
    NOT read .claude/skills, so .agents/skills is the one shared seam."""
    from tools.dx.skill_translator import translate_skills

    skill = tmp_path / ".claude" / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Demo skill\n---\n\n# demo\n", encoding="utf-8"
    )
    res = translate_skills(directory=str(tmp_path), platforms=["opencode", "pi"], write=True)
    for pid in ("opencode", "pi"):
        assert res[pid]["demo"]["path"] == ".agents/skills/demo/SKILL.md"
    assert (tmp_path / ".agents" / "skills" / "demo" / "SKILL.md").is_file()


def test_prebuild_ships_agents_md_as_a_required_source():
    from tools.installer import prebuild_bootstrap as pb

    rels = {rel for rel, _dst, _kind in pb.SOURCES}
    assert "AGENTS.md" in rels
    assert "AGENTS.md" not in pb.OPTIONAL_SOURCES
    # and it lands exactly where `icdev init` reads its primary file from
    dst = {rel: dst for rel, dst, _kind in pb.SOURCES}["AGENTS.md"]
    assert init_mod.PRIMARY_INSTRUCTION_SOURCE == f"data/claude_bootstrap/{dst}"
