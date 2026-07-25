# CUI // SP-CTI
"""Tests for the build_release real-init smoke assertions (pkg-rel-01).

The release smoke test used to run `icdev init --list`, which only reports what
it *would* copy — a wheel whose init copies nothing passed. build_release now
drives a REAL init and asserts the payload landed. These tests cover the pure
helpers that back those assertions (`_wheel_payload`, `_verify_init`) against a
synthetic wheel and fabricated project dirs — no real wheel build required.

Run: pytest tests/test_pkg_rel01_smoke_assertions.py -v --tb=short
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.installer.build_release import _verify_init, _wheel_payload

_REGISTRY_YAML = """\
components:
- key: alpha
  kind: canvas
  env_flag: ICDEV_ALPHA_ENABLED
- key: beta
  kind: feature
  env_flag: ICDEV_BETA_ENABLED
  extra_env_flags:
  - ICDEV_BETA_EXTRA_ENABLED
- key: no_flag
  kind: core_extension
"""


def _make_wheel(tmp_path: Path) -> Path:
    """Build a synthetic wheel zip with the bootstrap payload + registry."""
    wheel = tmp_path / "icdev-9.9.9-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        base = "icdev/data/claude_bootstrap/claude"
        for i in range(3):
            z.writestr(f"{base}/commands/cmd{i}.md", "x")
        for i in range(2):
            z.writestr(f"{base}/hooks/hook{i}.py", "x")
        for i in range(4):
            z.writestr(f"{base}/skills/skill{i}.md", "x")
        z.writestr("icdev/data/args/component_registry.yaml", _REGISTRY_YAML)
    return wheel


def _make_project(tmp_path: Path, *, commands=3, hooks=2, skills=4,
                  env_flags=("ICDEV_ALPHA_ENABLED", "ICDEV_BETA_ENABLED",
                             "ICDEV_BETA_EXTRA_ENABLED")) -> Path:
    proj = tmp_path / "proj"
    (proj / ".claude").mkdir(parents=True)
    (proj / "CLAUDE.md").write_text("# claude", encoding="utf-8")
    (proj / ".mcp.json").write_text("{}", encoding="utf-8")
    (proj / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    for sub, n in (("commands", commands), ("hooks", hooks), ("skills", skills)):
        d = proj / ".claude" / sub
        d.mkdir()
        for i in range(n):
            (d / f"f{i}").write_text("x", encoding="utf-8")
    env_body = "".join(f"{f}=false\n" for f in env_flags)
    (proj / ".env").write_text("# env\n" + env_body, encoding="utf-8")
    return proj


# ── _wheel_payload ─────────────────────────────────────────────────────────

def test_wheel_payload_counts_and_flags(tmp_path):
    payload = _wheel_payload(_make_wheel(tmp_path))
    assert payload["commands"] == 3
    assert payload["hooks"] == 2
    assert payload["skills"] == 4
    assert payload["env_flags"] == {
        "ICDEV_ALPHA_ENABLED", "ICDEV_BETA_ENABLED", "ICDEV_BETA_EXTRA_ENABLED",
    }
    # only components WITH an env_flag count (no_flag excluded)
    assert payload["component_count"] == 2


# ── _verify_init: happy path ───────────────────────────────────────────────

def test_verify_init_passes_complete_project(tmp_path):
    payload = _wheel_payload(_make_wheel(tmp_path))
    proj = _make_project(tmp_path)
    assert _verify_init(proj, payload) == []


# ── _verify_init: each failure mode ────────────────────────────────────────

def test_verify_init_flags_missing_top_level(tmp_path):
    payload = _wheel_payload(_make_wheel(tmp_path))
    proj = _make_project(tmp_path)
    (proj / "CLAUDE.md").unlink()
    (proj / ".mcp.json").unlink()
    fails = _verify_init(proj, payload)
    assert any("CLAUDE.md" in f for f in fails)
    assert any(".mcp.json" in f for f in fails)


def test_verify_init_flags_thin_claude_subtree(tmp_path):
    payload = _wheel_payload(_make_wheel(tmp_path))
    proj = _make_project(tmp_path, commands=1)  # wheel recorded 3
    fails = _verify_init(proj, payload)
    assert any("commands" in f and "expected >= 3" in f for f in fails)


def test_verify_init_flags_empty_env(tmp_path):
    payload = _wheel_payload(_make_wheel(tmp_path))
    proj = _make_project(tmp_path)
    (proj / ".env").write_text("   \n", encoding="utf-8")
    fails = _verify_init(proj, payload)
    assert any(".env is empty" in f for f in fails)


def test_verify_init_names_missing_env_flags(tmp_path):
    payload = _wheel_payload(_make_wheel(tmp_path))
    # Project .env omits the extra flag.
    proj = _make_project(tmp_path,
                         env_flags=("ICDEV_ALPHA_ENABLED", "ICDEV_BETA_ENABLED"))
    fails = _verify_init(proj, payload)
    assert any("ICDEV_BETA_EXTRA_ENABLED" in f for f in fails)


def test_verify_init_flags_missing_env_file(tmp_path):
    payload = _wheel_payload(_make_wheel(tmp_path))
    proj = _make_project(tmp_path)
    (proj / ".env").unlink()
    fails = _verify_init(proj, payload)
    assert any(f == ".env missing" for f in fails)
