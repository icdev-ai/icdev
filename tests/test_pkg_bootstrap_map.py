# CUI // SP-CTI
"""Tests for the bootstrap map completeness (pkg-init-03).

Guards two failure classes this PKG card exists to close:

1. A `.claude/` directory that is copied by `icdev init` but never collected
   into the package snapshot by `prebuild_bootstrap.py` (or vice-versa) — a
   map entry with no collector ships nothing.
2. A newly-mapped bootstrap source that does not exist yet crashing / failing
   `icdev init` instead of being reported as an optional skip.

Run: pytest tests/test_pkg_bootstrap_map.py -v --tb=short
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.cli import init as init_mod
from tools.installer import prebuild_bootstrap as pbb


def _bootstrap_targets() -> set[str]:
    """Project-relative targets under `.claude/` that `icdev init` writes."""
    return {
        dst for _src, dst in init_mod.BOOTSTRAP_MAP if dst.startswith(".claude/")
    }


def _prebuild_dsts() -> set[str]:
    """`claude/...` snapshot targets collected by prebuild_bootstrap."""
    return {dst for _s, dst, _k in pbb.SOURCES if dst.startswith("claude/")}


def test_agents_dir_is_mapped_both_sides():
    """`.claude/agents` must be in the init map AND the prebuild collector.

    A map entry on one side without the matching side is a silent no-op:
    prebuild would never snapshot agents, or init would never lay them down.
    """
    assert ".claude/agents" in _bootstrap_targets(), (
        "tools/cli/init.py BOOTSTRAP_MAP is missing the .claude/agents entry"
    )
    assert "claude/agents" in _prebuild_dsts(), (
        "prebuild_bootstrap.py SOURCES is missing the .claude/agents collector"
    )


def test_every_claude_init_target_has_a_prebuild_collector():
    """Every `.claude/<x>` init target maps to a `claude/<x>` prebuild source.

    This is the durable check: add a new `.claude/` subdir to the init map and
    forget the collector, and this test fails instead of shipping nothing.
    settings.json is a special case (file → settings.json.template), handled
    explicitly.
    """
    init_targets = _bootstrap_targets()
    prebuild_dsts = _prebuild_dsts()
    # Normalize: `.claude/settings.json` is snapshotted as
    # `claude/settings.json.template`.
    normalized_prebuild = {
        d.replace(".template", "") if d.endswith("settings.json.template") else d
        for d in prebuild_dsts
    }
    missing = []
    for tgt in init_targets:
        snapshot = "claude/" + tgt[len(".claude/"):]
        if snapshot not in normalized_prebuild:
            missing.append(tgt)
    assert not missing, (
        f"init targets with no prebuild collector (would ship empty): {missing}"
    )


def test_agents_is_declared_optional_both_sides():
    """`.claude/agents` is optional on both sides (ships zero files today)."""
    assert "data/claude_bootstrap/claude/agents" in init_mod.OPTIONAL_SOURCES
    assert ".claude/agents" in pbb.OPTIONAL_SOURCES


def test_init_missing_optional_source_does_not_fail(tmp_path, monkeypatch):
    """A mapped-but-absent optional source is an `optional_missing` skip, never
    a `source_missing` — so it does not flip the init exit code to 1."""

    def _fake_resource(resource: str):
        # Only the agents dir resolves to a non-existent path; everything else
        # resolves to a real temp file so init proceeds normally.
        if resource.endswith("claude/agents"):
            return None
        p = tmp_path / "pkg" / resource
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text("x", encoding="utf-8")
        return p

    monkeypatch.setattr(init_mod, "_package_resource_path", _fake_resource)
    result = init_mod.init_project(tmp_path / "proj", minimal=True)

    statuses = {a["dst"]: a["status"] for a in result["actions"]}
    assert statuses.get(".claude/agents") == "optional_missing"
    # The optional skip must NOT count as a hard miss (exit-code driver).
    assert result["missing"] == 0
    assert result["optional_missing"] >= 1


def test_prebuild_missing_optional_source_is_not_an_error(tmp_path, monkeypatch):
    """prebuild records an absent optional source under skipped_optional, not
    errors — so the build step that runs it does not fail."""
    monkeypatch.setattr(pbb, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(pbb, "BOOTSTRAP_DIR", tmp_path / "out")
    # Only declare the (absent) optional agents source.
    monkeypatch.setattr(pbb, "SOURCES", [(".claude/agents", "claude/agents", "dir")])
    result = pbb.run()
    assert ".claude/agents" in result["skipped_optional"]
    assert not result["errors"]
