# CUI // SP-CTI
"""Tests for the bootstrap snapshot freshness gate (pkg-rel-02).

icdev/data/claude_bootstrap/ is a committed snapshot refreshed only by
prebuild_bootstrap.py; a plain `python -m build` skips it, so a release can ship
a stale command/skill set. check_bootstrap_freshness compares the snapshot to
the LIVE source trees by file set AND content, and derives the mapping from
prebuild_bootstrap.SOURCES so the `.agents/skills` (not `.claude/skills`) source
nuance is handled automatically — otherwise the gate reports permanent false
drift.

These tests run the check against synthetic repo layouts (the real snapshot is
intentionally allowed to be stale). Run:
    pytest tests/test_pkg_rel02_bootstrap_freshness.py -v --tb=short
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

vpc = importlib.import_module("tools.installer.validate_package_config")
pbb = importlib.import_module("tools.installer.prebuild_bootstrap")


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _setup(tmp_path, monkeypatch, sources):
    """Point the check at a synthetic repo root + snapshot dir."""
    repo = tmp_path / "repo"
    boot = repo / "icdev" / "data" / "claude_bootstrap"
    monkeypatch.setattr(vpc, "REPO_ROOT", repo)
    monkeypatch.setattr(pbb, "BOOTSTRAP_DIR", boot)
    monkeypatch.setattr(pbb, "SOURCES", sources)
    monkeypatch.setattr(pbb, "OPTIONAL_SOURCES", set())
    return repo, boot


def test_in_sync_snapshot_passes(tmp_path, monkeypatch):
    sources = [(".claude/commands", "claude/commands", "dir"),
               ("CLAUDE.md", "CLAUDE.md", "file")]
    repo, boot = _setup(tmp_path, monkeypatch, sources)
    _write(repo / ".claude/commands/a.md", "alpha")
    _write(boot / "claude/commands/a.md", "alpha")
    _write(repo / "CLAUDE.md", "root")
    _write(boot / "CLAUDE.md", "root")

    r = vpc.check_bootstrap_freshness()
    assert r["ok"] is True
    assert r["drift_count"] == 0


def test_detects_added_removed_changed(tmp_path, monkeypatch):
    sources = [(".claude/commands", "claude/commands", "dir"),
               ("CLAUDE.md", "CLAUDE.md", "file")]
    repo, boot = _setup(tmp_path, monkeypatch, sources)
    # live has a.md (changed) + b.md (added); snapshot has a.md (old) + c.md (stale)
    _write(repo / ".claude/commands/a.md", "NEW")
    _write(repo / ".claude/commands/b.md", "added-live")
    _write(boot / "claude/commands/a.md", "OLD")
    _write(boot / "claude/commands/c.md", "stale-snap")
    # file: CLAUDE.md content differs
    _write(repo / "CLAUDE.md", "v2")
    _write(boot / "CLAUDE.md", "v1")

    r = vpc.check_bootstrap_freshness()
    assert r["ok"] is False
    assert "claude/commands/b.md" in r["added_missing_from_snapshot"]
    assert "claude/commands/c.md" in r["removed_stale_in_snapshot"]
    assert "claude/commands/a.md" in r["changed_content"]
    assert "CLAUDE.md" in r["changed_content"]
    assert r["drift_count"] == 4


def test_skills_source_of_truth_is_agents_not_claude(tmp_path, monkeypatch):
    """The nuance guard: skills come from .agents/skills, so a divergent
    .claude/skills must NOT cause false drift."""
    sources = [(".agents/skills", "claude/skills", "dir")]
    repo, boot = _setup(tmp_path, monkeypatch, sources)
    # Snapshot mirrors .agents/skills exactly.
    _write(repo / ".agents/skills/icdev-x.md", "skill-x")
    _write(boot / "claude/skills/icdev-x.md", "skill-x")
    # A decoy .claude/skills that DIFFERS — must be ignored by the gate.
    _write(repo / ".claude/skills/icdev-x.md", "TOTALLY DIFFERENT")

    r = vpc.check_bootstrap_freshness()
    assert r["ok"] is True, (
        "gate must read .agents/skills, not .claude/skills — "
        f"got drift: {r}"
    )


def test_file_entry_inside_a_dir_entry_is_not_stale(tmp_path, monkeypatch):
    """exa-bench-10 ships tools/hooks/shared_checks.py INTO claude/hooks/. The dir
    comparison for .claude/hooks must not call that file stale -- it blocked the
    1.2.43 release with drift `prebuild_bootstrap.py --clean` could never clear."""
    sources = [(".claude/hooks", "claude/hooks", "dir"),
               ("tools/hooks/shared_checks.py", "claude/hooks/shared_checks.py", "file")]
    repo, boot = _setup(tmp_path, monkeypatch, sources)
    _write(repo / ".claude/hooks/pre_tool_use.py", "hook")
    _write(boot / "claude/hooks/pre_tool_use.py", "hook")
    _write(repo / "tools/hooks/shared_checks.py", "checks")
    _write(boot / "claude/hooks/shared_checks.py", "checks")

    r = vpc.check_bootstrap_freshness()
    assert r["removed_stale_in_snapshot"] == []
    assert r["ok"] is True, r


def test_settings_template_compared_after_its_deliberate_rewrite(tmp_path, monkeypatch):
    """settings.json.template is written through _settings_template_text (the
    scaffold keeps `|| true`), so comparing raw source bytes is permanent drift."""
    sources = [(".claude/settings.json", "claude/settings.json.template", "file")]
    repo, boot = _setup(tmp_path, monkeypatch, sources)
    live = '{"command": "%s"}\n' % pbb._PRETOOLUSE_REPO
    _write(repo / ".claude/settings.json", live)
    # Bytes, as prebuild's _copy_file writes them (newline="": no CRLF on Windows).
    snap = boot / "claude/settings.json.template"
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_bytes(pbb._settings_template_text(live).encode("utf-8"))

    r = vpc.check_bootstrap_freshness()
    assert r["changed_content"] == []
    assert r["ok"] is True, r

    # A real change to the live settings is still reported.
    _write(repo / ".claude/settings.json", live.replace("command", "cmd"))
    assert "claude/settings.json.template" in vpc.check_bootstrap_freshness()["changed_content"]


def test_missing_required_source_reported(tmp_path, monkeypatch):
    sources = [(".claude/commands", "claude/commands", "dir")]
    repo, boot = _setup(tmp_path, monkeypatch, sources)
    # Neither live source nor snapshot created.
    r = vpc.check_bootstrap_freshness()
    assert ".claude/commands" in r["missing_sources"]
    assert r["ok"] is False


def test_registered_in_validate_check_list(tmp_path, monkeypatch):
    # A synthetic in-sync layout so the whole validate() run can include it.
    result = vpc.validate()
    ids = {c["check"] for c in result["checks"]}
    assert "bootstrap_freshness" in ids


def test_check_runs_without_pythonpath():
    """The documented invocation must work with no PYTHONPATH set.

    This check imports tools.installer.prebuild_bootstrap. Running the script
    directly puts tools/installer/ on sys.path[0], not the repo root, so the
    import raised ModuleNotFoundError and the check reported itself FAILED:

        [FAIL] bootstrap_freshness
               error: could not import prebuild_bootstrap: No module named 'tools'

    CI sets PYTHONPATH, so this only ever bit local runs and build_release.py —
    where it blocked the release at the validate step, and masked a real drift
    (two DWO append-only tables missing from the packaged hook) behind an
    import error.

    A subprocess with a scrubbed environment is the only honest way to assert
    this: in-process, pytest has already put the repo root on sys.path.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    proc = subprocess.run(
        [sys.executable,
         str(REPO_ROOT / "tools" / "installer" / "validate_package_config.py"),
         "--json"],
        cwd=str(REPO_ROOT), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300,
    )
    payload = json.loads(proc.stdout)
    check = next(c for c in payload["checks"] if c["check"] == "bootstrap_freshness")
    assert "error" not in check, (
        f"bootstrap_freshness could not run without PYTHONPATH: {check.get('error')}"
    )
