#!/usr/bin/env python3
"""The release pipeline's refusals. CUI // SP-CTI.

Three consecutive releases shipped broken because the pipeline was driven by
hand: 1.2.40 skipped the package sync, 1.2.41 published a wheel whose
`agent_loop` imported from itself, and both passed `twine check` — which
validates metadata, not whether the thing imports.

The value of this script is therefore not what it does but what it REFUSES, so
that is what these tests pin. Every assertion here corresponds to a specific
release that actually went out wrong.
"""
from __future__ import annotations

import re

import pytest

from tools.installer import release as rel


# --------------------------------------------------------------------------- #
# Version arithmetic
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("cur,part,expected", [
    ("1.2.42", "patch", "1.2.43"),
    ("1.2.42", "minor", "1.3.0"),
    ("1.2.42", "major", "2.0.0"),
    ("1.2.9", "patch", "1.2.10"),
])
def test_next_version(cur, part, expected):
    assert rel.next_version(cur, part) == expected


def test_next_version_rejects_non_semver():
    with pytest.raises(SystemExit):
        rel.next_version("1.2", "patch")


def test_every_declared_version_file_is_findable():
    """The bump must reach every file, or the numbers drift apart again.

    They had reached three different values at once — brand.yaml 1.2.30,
    CHANGELOG 1.2.37, pyproject 1.2.39 — because each was maintained by hand.
    """
    versions = rel.read_versions()
    unreadable = [f for f, v in versions.items() if v is None]
    assert not unreadable, f"version pattern no longer matches: {unreadable}"


def test_all_version_files_currently_agree():
    """A release cut from a repo whose files disagree bakes the drift into the wheel."""
    versions = rel.read_versions()
    assert len(set(versions.values())) == 1, f"version declarations disagree: {versions}"


def test_source_of_truth_is_declared_and_real():
    assert rel.SOURCE_OF_TRUTH in dict((f, p) for f, p, _ in rel.VERSION_FILES)
    assert (rel.REPO_ROOT / rel.SOURCE_OF_TRUTH).is_file()


def test_packaged_brand_is_not_bumped_directly():
    """It is written by sync_package_tree.py; bumping it here would be overwritten."""
    files = [f for f, _p, _fmt in rel.VERSION_FILES]
    assert "icdev/data/args/brand.yaml" not in files


# --------------------------------------------------------------------------- #
# The refusals — each maps to a release that shipped broken
# --------------------------------------------------------------------------- #


def test_publish_with_skip_smoke_is_refused():
    """1.2.41: the wheel built, passed twine check, and could not import.

    The throwaway-venv smoke test in build_release.py is the only step that
    catches that, so it must not be skippable on a publishing run.
    """
    assert rel.main(["--version", "9.9.9", "--publish", "--skip-smoke"]) == 2


def test_publish_without_notes_is_refused():
    """1.2.38 and 1.2.39 shipped with no CHANGELOG entry at all.

    /updates renders CHANGELOG.md, so the dashboard advertised 1.2.37 as the
    newest release for two releases running.
    """
    assert rel.main(["--version", "9.9.9", "--publish", "--allow-missing-notes"]) == 2


def test_no_version_argument_is_an_error_not_a_guess():
    assert rel.main([]) == 2


# --------------------------------------------------------------------------- #
# Preflight
# --------------------------------------------------------------------------- #


def test_the_current_version_is_resumable_not_an_error():
    """Re-running a release that failed halfway must work.

    The bump lands before the build, so a failure at build/verify/publish leaves
    the declarations already at the target. An earlier draft treated that as
    "already current — nothing to release", which wedged the retry: you could
    never finish a release that broke after the bump. PyPI rejects duplicate
    uploads, so re-release protection belongs there, not here.
    """
    cur = rel.current_version()
    out = rel.step_preflight(cur)
    assert out["resuming"] is True
    assert not any("already current" in p for p in out["problems"])


def test_a_failed_notes_gate_writes_nothing(tmp_path, monkeypatch):
    """The notes gate runs BEFORE the bump, so a missing-notes run is a no-op.

    Bumping first left the tree half-released whenever notes were absent, and
    the author had to work out what to revert.
    """
    import inspect

    src = inspect.getsource(rel.main)
    notes_at = src.index('report["steps"]["notes"]')
    bump_at = src.index('report["steps"]["bump"] = write_version')
    assert notes_at < bump_at, "notes gate must precede the version bump"


def test_preflight_rejects_going_backwards():
    out = rel.step_preflight("0.0.1")
    assert not out["ok"]
    assert any("LOWER" in p for p in out["problems"])


def test_preflight_rejects_a_malformed_version():
    out = rel.step_preflight("1.2")
    assert not out["ok"]
    assert any("MAJOR.MINOR.PATCH" in p for p in out["problems"])


def test_preflight_refuses_to_release_from_main(monkeypatch):
    """CLAUDE.md is worktree/branch-first; several sessions share this checkout."""
    class _R:
        stdout = "main\n"
        stderr = ""
        returncode = 0

    monkeypatch.setattr(rel, "_run", lambda *a, **k: _R())
    out = rel.step_preflight("99.0.0")
    assert not out["ok"]
    assert any("on main" in p for p in out["problems"])


# --------------------------------------------------------------------------- #
# Notes gate
# --------------------------------------------------------------------------- #


def test_notes_status_detects_the_current_release():
    """The version in the tree must have notes — it was just released."""
    assert rel.notes_status(rel.current_version()) == {"readme": True, "changelog": True}


def test_notes_status_reports_missing_for_an_unreleased_version():
    assert rel.notes_status("9.9.9") == {"readme": False, "changelog": False}


def test_scaffold_notes_is_non_destructive_in_dry_run(tmp_path, monkeypatch):
    readme = tmp_path / "README.md"
    changelog = tmp_path / "CHANGELOG.md"
    readme.write_text("# T\n\n## What's New in 1.0.0 — x\n", encoding="utf-8")
    changelog.write_text("# C\n\n## [1.0.0] - 2026-01-01\n", encoding="utf-8")
    monkeypatch.setattr(rel, "REPO_ROOT", tmp_path)

    rel.scaffold_notes("1.0.1", dry_run=True)
    assert "1.0.1" not in readme.read_text(encoding="utf-8")
    assert "1.0.1" not in changelog.read_text(encoding="utf-8")


def test_scaffold_notes_inserts_above_the_previous_release(tmp_path, monkeypatch):
    readme = tmp_path / "README.md"
    changelog = tmp_path / "CHANGELOG.md"
    readme.write_text("# T\n\n## What's New in 1.0.0 — x\n", encoding="utf-8")
    changelog.write_text("# C\n\n## [1.0.0] - 2026-01-01\n", encoding="utf-8")
    monkeypatch.setattr(rel, "REPO_ROOT", tmp_path)

    rel.scaffold_notes("1.0.1")
    rtext = readme.read_text(encoding="utf-8")
    ctext = changelog.read_text(encoding="utf-8")
    assert rtext.index("1.0.1") < rtext.index("1.0.0")
    assert ctext.index("1.0.1") < ctext.index("1.0.0")
    # Placeholders, not invented prose.
    assert "TODO" in rtext and "TODO" in ctext


def test_scaffold_is_idempotent(tmp_path, monkeypatch):
    readme = tmp_path / "README.md"
    changelog = tmp_path / "CHANGELOG.md"
    readme.write_text("# T\n\n## What's New in 1.0.0 — x\n", encoding="utf-8")
    changelog.write_text("# C\n\n## [1.0.0] - 2026-01-01\n", encoding="utf-8")
    monkeypatch.setattr(rel, "REPO_ROOT", tmp_path)

    rel.scaffold_notes("1.0.1")
    rel.scaffold_notes("1.0.1")
    assert readme.read_text(encoding="utf-8").count("What's New in 1.0.1") == 1
    assert changelog.read_text(encoding="utf-8").count("[1.0.1]") == 1


# --------------------------------------------------------------------------- #
# Bump
# --------------------------------------------------------------------------- #


def test_write_version_dry_run_touches_nothing():
    before = rel.read_versions()
    results = rel.write_version("9.9.9", dry_run=True)
    assert all(r["ok"] for r in results)
    assert rel.read_versions() == before


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #


def test_api_token_is_mapped_to_the_twine_username(monkeypatch, tmp_path):
    envfile = tmp_path / ".env"
    envfile.write_text('PYPI_API_TOKEN="pypi-SECRET"\n', encoding="utf-8")
    monkeypatch.setattr(rel, "ENV_FILE", envfile)
    monkeypatch.delenv("TWINE_PASSWORD", raising=False)
    monkeypatch.delenv("TWINE_USERNAME", raising=False)

    env = rel.twine_env()
    assert env["TWINE_USERNAME"] == "__token__"
    assert env["TWINE_PASSWORD"] == "pypi-SECRET"


def test_quotes_are_stripped_from_env_values(monkeypatch, tmp_path):
    envfile = tmp_path / ".env"
    envfile.write_text("TWINE_USERNAME='alice'\nTWINE_PASSWORD=\"hunter2\"\n", encoding="utf-8")
    monkeypatch.setattr(rel, "ENV_FILE", envfile)
    env = rel.twine_env()
    assert env["TWINE_USERNAME"] == "alice"
    assert env["TWINE_PASSWORD"] == "hunter2"


def test_publish_without_credentials_fails_closed(monkeypatch, tmp_path):
    """Missing credentials must stop the upload, not attempt an anonymous one."""
    (tmp_path / "icdev-1.2.43-py3-none-any.whl").write_bytes(b"stub")
    monkeypatch.setattr(rel, "DIST_DIR", tmp_path)
    monkeypatch.setattr(rel, "twine_env", lambda: {})
    out = rel.step_publish("1.2.43")
    assert not out["ok"]
    assert "credentials" in out["error"]


def test_publish_refuses_when_no_artifact_matches_the_version(monkeypatch, tmp_path):
    """Guards against uploading a stale wheel left in dist/ from a previous build."""
    (tmp_path / "icdev-1.2.42-py3-none-any.whl").write_bytes(b"stub")
    monkeypatch.setattr(rel, "DIST_DIR", tmp_path)
    out = rel.step_publish("1.2.43")
    assert not out["ok"]
    assert "no artifacts" in out["error"]


def test_no_secret_appears_in_the_module_source():
    """Credentials come from .env at runtime — never committed."""
    import inspect

    src = inspect.getsource(rel)
    assert not re.search(r"pypi-[A-Za-z0-9_-]{16,}", src)


# --------------------------------------------------------------------------- #
# Delegation — the middle of the pipeline must not be reimplemented
# --------------------------------------------------------------------------- #


def test_build_delegates_to_build_release():
    """sync -> validate -> build -> inspect -> smoke -> air-gap already exists.

    Reimplementing any of it here is how the hand-run pipeline diverged from the
    documented one in the first place.
    """
    import inspect

    src = inspect.getsource(rel.step_build)
    assert "build_release.py" in src


def test_orchestrator_does_not_call_python_m_build_directly():
    """Calling `python -m build` straight is exactly what skipped the sync."""
    import inspect

    src = inspect.getsource(rel)
    assert '"-m", "build"' not in src


def test_build_release_script_exists():
    assert (rel.REPO_ROOT / "tools" / "installer" / "build_release.py").is_file()


# --------------------------------------------------------------------------- #
# Payload completeness — the wheel must carry what the repo tracks
# --------------------------------------------------------------------------- #


def _platform_entries() -> dict:
    """Every AI platform instruction file, as the wheel stores them.

    A complete test wheel must carry these — the payload gate requires them, so
    that an installed project is never Claude-only.
    """
    from tools.dx.ai_platforms import AI_PLATFORM_FILES, bootstrap_name

    return {f"icdev/data/claude_bootstrap/{bootstrap_name(rel)}": b"x"
            for _p, rel in AI_PLATFORM_FILES}


def _make_wheel(tmp_path, names: dict):
    """Build a minimal .whl containing exactly ``names`` (path -> bytes)."""
    import zipfile

    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    whl = dist / "icdev-9.9.9-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as z:
        for n, data in names.items():
            z.writestr(n, data)
    return dist


def test_payload_gate_flags_a_tracked_file_missing_from_the_wheel(tmp_path, monkeypatch):
    """The tools/agents/ failure: 9 real source files never shipped, silently.

    A bare `agents/` rule in .gitignore matched the source directory at any
    depth, so the files were untracked — present only on the machine that wrote
    them, absent from every fresh clone and therefore every wheel.
    """
    dist = _make_wheel(tmp_path, {"icdev/tools/kept.py": b"x"})
    monkeypatch.setattr(rel, "DIST_DIR", dist)
    monkeypatch.setattr(rel, "_parent_only_dirs", lambda: set())
    monkeypatch.setattr(rel.subprocess, "run", _git_stub(
        {"tools": ["tools/kept.py", "tools/agents/registry.py"]}))

    out = rel.step_verify_payload("9.9.9")
    assert not out["ok"]
    assert any("absent from the wheel" in p for p in out["problems"])
    assert any("agents/registry.py" in p for p in out["problems"])


def test_payload_gate_ignores_parent_only_subsystems(tmp_path, monkeypatch):
    """Deliberate exclusions must not read as missing payload."""
    dist = _make_wheel(tmp_path, {
        "icdev/tools/kept.py": b"x",
        "icdev/data/args/component_registry.yaml": b"x",
        "icdev/data/goals/g.md": b"x",
        "icdev/data/hardprompts/h.md": b"x",
        "icdev/data/context/c.md": b"x",
        "icdev/data/claude_bootstrap/CLAUDE.md": b"x",
        "icdev/data/claude_bootstrap/claude/commands/build.md": b"x",
        "icdev/data/.env.template": b"x",
        **_platform_entries(),
    })
    monkeypatch.setattr(rel, "DIST_DIR", dist)
    monkeypatch.setattr(rel, "_parent_only_dirs", lambda: {"trading"})
    monkeypatch.setattr(rel.subprocess, "run", _git_stub(
        {"tools": ["tools/kept.py", "tools/trading/secret.py"]}))

    out = rel.step_verify_payload("9.9.9")
    assert out["ok"], out["problems"]


@pytest.mark.parametrize("drop,expect", [
    ("icdev/data/args/component_registry.yaml", "component_registry"),
    ("icdev/data/goals/g.md", "goals"),
    ("icdev/data/claude_bootstrap/CLAUDE.md", "no CLAUDE.md"),
    ("icdev/data/.env.template", ".env.template"),
])
def test_payload_gate_requires_each_forge_layer(tmp_path, monkeypatch, drop, expect):
    """`icdev init` copies these OUT of the wheel.

    Without them a fresh project has no .claude/, no .env, and no canvases —
    which is exactly what a user reports as "pip install gave me nothing".
    """
    full = {
        "icdev/tools/kept.py": b"x",
        "icdev/data/args/component_registry.yaml": b"x",
        "icdev/data/goals/g.md": b"x",
        "icdev/data/hardprompts/h.md": b"x",
        "icdev/data/context/c.md": b"x",
        "icdev/data/claude_bootstrap/CLAUDE.md": b"x",
        "icdev/data/claude_bootstrap/claude/commands/build.md": b"x",
        "icdev/data/.env.template": b"x",
        **_platform_entries(),
    }
    full.pop(drop)
    dist = _make_wheel(tmp_path, full)
    monkeypatch.setattr(rel, "DIST_DIR", dist)
    monkeypatch.setattr(rel, "_parent_only_dirs", lambda: set())
    monkeypatch.setattr(rel.subprocess, "run", _git_stub({"tools": ["tools/kept.py"]}))

    out = rel.step_verify_payload("9.9.9")
    assert not out["ok"]
    assert any(expect in p for p in out["problems"]), out["problems"]


def test_payload_gate_flags_missing_genesis_reflexes(tmp_path, monkeypatch):
    """A reflex absent from the wheel fails silently — the daemon just never finds it."""
    dist = _make_wheel(tmp_path, {
        **_platform_entries(),
        "icdev/tools/genesis/reflexes/kept.py": b"x",
        "icdev/data/args/component_registry.yaml": b"x",
        "icdev/data/goals/g.md": b"x",
        "icdev/data/hardprompts/h.md": b"x",
        "icdev/data/context/c.md": b"x",
        "icdev/data/claude_bootstrap/CLAUDE.md": b"x",
        "icdev/data/claude_bootstrap/claude/commands/build.md": b"x",
        "icdev/data/.env.template": b"x",
    })
    monkeypatch.setattr(rel, "DIST_DIR", dist)
    monkeypatch.setattr(rel, "_parent_only_dirs", lambda: set())
    monkeypatch.setattr(rel.subprocess, "run", _git_stub(
        {"tools": ["tools/genesis/reflexes/kept.py", "tools/genesis/reflexes/gone.py"]}))

    out = rel.step_verify_payload("9.9.9")
    assert not out["ok"]
    assert any("genesis reflex" in p for p in out["problems"])


def test_payload_gate_compares_against_git_not_the_working_tree():
    """An untracked file on the release engineer's disk is the whole problem.

    Comparing against the working directory would have called every broken
    release healthy, because the files WERE there — locally, and nowhere else.
    """
    import inspect

    src = inspect.getsource(rel.step_verify_payload)
    assert "git" in src and "ls-files" in src


def test_payload_gate_runs_in_the_pipeline():
    import inspect

    src = inspect.getsource(rel.main)
    assert "step_verify_payload" in src
    assert src.index("step_verify_artifacts") < src.index("step_verify_payload")


def test_parent_only_dirs_are_read_from_the_sync_tool():
    """A hardcoded copy would report deliberate exclusions as missing payload."""
    import inspect

    src = inspect.getsource(rel._parent_only_dirs)
    assert "sync_package_tree" in src
    assert rel._parent_only_dirs(), "PARENT_ONLY_DIRS should be non-empty"


def test_tools_agents_is_tracked_by_git():
    """The regression itself: these 9 files must be under version control.

    They are real source (the agent adapter registry), not agent OUTPUT, and a
    bare `agents/` ignore rule had quietly excluded them from every release.
    """
    import subprocess as sp

    out = sp.run(["git", "ls-files", "tools/agents/"], cwd=rel.REPO_ROOT,
                 capture_output=True, text=True).stdout.split()
    assert any(f.endswith("registry.py") for f in out), \
        "tools/agents/ is untracked — it will not ship in the wheel"


# --------------------------------------------------------------------------- #
# Hollow modules — present in the wheel, but importing from themselves
# --------------------------------------------------------------------------- #


def _git_stub(mapping: dict):
    """Stand in for `git ls-files <path>`, answering PER PATH.

    The payload gate calls it once for tools/ and once for each FORGE data
    layer. A stub that returns the same list every time makes tools/ files look
    like missing goals/ files.
    """
    def _run(cmd, *a, **k):
        path = cmd[-1] if isinstance(cmd, (list, tuple)) else ""
        out = chr(10).join(mapping.get(path.rstrip("/"), []))
        return type("R", (), {"stdout": out, "stderr": "", "returncode": 0})()

    return _run


def _zip_of(tmp_path, members: dict):
    import zipfile

    whl = tmp_path / "w.whl"
    with zipfile.ZipFile(whl, "w") as z:
        for n, b in members.items():
            z.writestr(n, b)
    return whl


def _scan(tmp_path, members: dict):
    import zipfile

    whl = _zip_of(tmp_path, members)
    with zipfile.ZipFile(whl) as z:
        return rel._self_importing_modules(set(z.namelist()), z.read)


def test_detects_a_module_importing_from_itself(tmp_path):
    """The 1.2.41 defect, verbatim.

    A back-compat shim copied over its real implementation. Python raises
    ImportError on a partially initialized module, so the capability is gone —
    but the file is present, so every presence check passes.
    """
    hollow = _scan(tmp_path, {
        "icdev/tools/llm/agent_loop.py": b"from icdev.tools.llm.agent_loop import DONE\n",
    })
    assert hollow == ["icdev/tools/llm/agent_loop.py"]


def test_a_healthy_module_is_not_flagged(tmp_path):
    hollow = _scan(tmp_path, {
        "icdev/tools/llm/router.py": b"DONE = 1\n\n\ndef go():\n    return DONE\n",
    })
    assert hollow == []


def test_a_legitimate_cross_module_import_is_not_flagged(tmp_path):
    """Importing a DIFFERENT icdev module is normal and must stay allowed."""
    hollow = _scan(tmp_path, {
        "icdev/tools/quality/derivation.py":
            b"from icdev.tools.quality.citation_grounding import parse_citations\n",
    })
    assert hollow == []


def test_init_files_are_skipped(tmp_path):
    """`icdev/tools/__init__.py` re-exporting from `icdev.tools` is the shim
    package's whole job, not a defect."""
    hollow = _scan(tmp_path, {"icdev/tools/__init__.py": b"from icdev.tools import x\n"})
    assert hollow == []


def test_non_icdev_members_are_ignored(tmp_path):
    hollow = _scan(tmp_path, {"other/pkg/mod.py": b"from other.pkg.mod import y\n"})
    assert hollow == []


def test_the_check_runs_inside_the_payload_gate():
    """And blocks — a hollow module must fail the release, not warn."""
    import inspect

    src = inspect.getsource(rel.step_verify_payload)
    assert "_self_importing_modules" in src
    assert "import from THEMSELVES" in src


def test_the_scan_uses_one_open_archive():
    """Reopening per member would mean thousands of archive opens on a
    3,400-module wheel."""
    import inspect

    src = inspect.getsource(rel.step_verify_payload)
    assert "_self_importing_modules(names, z.read)" in src
