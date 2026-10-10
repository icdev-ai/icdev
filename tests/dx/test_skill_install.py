# CUI // SP-CTI
"""omx-dx-02: `icdev skill install` -- one source skill, linked into every harness."""

from __future__ import annotations

import json

import pytest

from icdev.tools.dx import skill_install as si


def _fake_home(tmp_path):
    home = tmp_path / "home"
    # Three harnesses present; codex, gemini, hermes absent.
    for parts in (si.HARNESS_SKILL_DIRS["claude"], si.HARNESS_SKILL_DIRS["pi"],
                  si.HARNESS_SKILL_DIRS["opencode"]):
        home.joinpath(*parts).mkdir(parents=True)
    return home


def _all_paths(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_source_skill_has_agent_skills_frontmatter():
    text = (si.source_dir() / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: icdev\ndescription: ")


def test_install_links_only_existing_harness_dirs(tmp_path):
    home = _fake_home(tmp_path)
    result = si.install("auto", home=home)

    shared = home / ".agents" / "skills" / "icdev"
    assert (shared / "SKILL.md").is_file()
    installed = {r["harness"] for r in result["results"] if r["status"] == "installed"}
    assert installed == {"agents", "claude", "pi", "opencode"}
    for harness in ("claude", "pi", "opencode"):
        dest = home.joinpath(*si.HARNESS_SKILL_DIRS[harness], "icdev")
        assert (dest / "SKILL.md").read_text(encoding="utf-8") == (shared / "SKILL.md").read_text(encoding="utf-8")
    for harness in ("codex", "gemini", "hermes"):
        assert not home.joinpath(*si.HARNESS_SKILL_DIRS[harness]).exists()


def test_uninstall_removes_exactly_what_install_created(tmp_path):
    home = _fake_home(tmp_path)
    # Something the user owns, which must survive.
    mine = home.joinpath(*si.HARNESS_SKILL_DIRS["claude"], "my-skill")
    mine.mkdir()
    (mine / "SKILL.md").write_text("---\nname: my-skill\n---\n", encoding="utf-8")
    before = _all_paths(home)

    si.install("auto", home=home)
    result = si.uninstall(home=home)

    assert len(result["removed"]) == 4
    assert result["already_gone"] == []
    after = [p for p in _all_paths(home)
             if not p.startswith(".agents") and not p.startswith(".icdev")]
    assert after == before
    assert not (home / ".agents" / "skills" / "icdev").exists()
    assert not si.manifest_path(home).exists()


def test_foreign_icdev_entry_is_skipped_and_survives_uninstall(tmp_path):
    home = _fake_home(tmp_path)
    foreign = home.joinpath(*si.HARNESS_SKILL_DIRS["pi"], "icdev")
    foreign.mkdir()
    (foreign / "SKILL.md").write_text("theirs", encoding="utf-8")

    result = si.install("auto", home=home)
    skipped = [r for r in result["results"] if r["status"] == "skipped"]
    assert [r["harness"] for r in skipped] == ["pi"]

    si.uninstall(home=home)
    assert (foreign / "SKILL.md").read_text(encoding="utf-8") == "theirs"


def test_copy_fallback_when_symlink_is_refused(tmp_path, monkeypatch):
    home = _fake_home(tmp_path)

    def _no_symlink(*a, **k):
        raise OSError("A required privilege is not held by the client")

    monkeypatch.setattr(si.os, "symlink", _no_symlink)
    result = si.install("auto", home=home)
    links = [r for r in result["results"] if r["harness"] != "agents"]
    assert links and all(r["mode"] == "copy" and "note" in r for r in links)
    dest = home.joinpath(*si.HARNESS_SKILL_DIRS["claude"], "icdev")
    assert dest.is_dir() and not dest.is_symlink()

    si.uninstall(home=home)
    assert not dest.exists()


def test_reinstall_is_idempotent(tmp_path):
    home = _fake_home(tmp_path)
    si.install("auto", home=home)
    si.install("auto", home=home)
    manifest = json.loads(si.manifest_path(home).read_text(encoding="utf-8"))
    assert len(manifest["entries"]) == 4


def test_explicit_dirs_create_the_named_harness_dir(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    si.install("codex", home=home)
    assert (home / ".codex" / "skills" / "icdev" / "SKILL.md").is_file()
    with pytest.raises(ValueError):
        si.install("nope", home=home)


def test_cli_install_and_uninstall_json(tmp_path, capsys):
    home = _fake_home(tmp_path)
    assert si.main(["install", "--home", str(home), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["action"] == "install"
    assert si.main(["install", "--uninstall", "--home", str(home), "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["removed"]) == 4


def test_icdev_dispatcher_routes_skill_subcommand(tmp_path, capsys):
    from icdev.tools.cli.__main__ import main as icdev_main

    home = _fake_home(tmp_path)
    assert icdev_main(["skill", "install", "--home", str(home), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "install"
    assert icdev_main(["skill", "status", "--home", str(home), "--json"]) == 0
    assert all(e["present"] for e in json.loads(capsys.readouterr().out)["entries"])
