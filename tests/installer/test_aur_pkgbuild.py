#!/usr/bin/env python3
"""The AUR package is RENDERED by release.py, never hand-edited (omx-dist-01). CUI // SP-CTI.

`deploy/aur/PKGBUILD` and `.SRCINFO` carry the pkgver and sha256 of one exact
wheel. A pair edited by hand drifts — the version moves and the checksum does
not — and then `makepkg` fails its integrity check for everyone who installs
that release. These tests pin that the rendered values come from the wheel
itself, and that the committed files are exactly what the renderer produces.
"""
from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

from tools.installer import release as rel

REPO_ROOT = Path(__file__).resolve().parents[2]
AUR = REPO_ROOT / "deploy" / "aur"

ENTRY_POINTS = """[console_scripts]
icdev = icdev.tools.cli.__main__:main
icdev-mcp = icdev.tools.mcp.unified_server:main
icdev-dashboard = icdev.tools.dashboard.app:main
"""


def _fixture_wheel(tmp_path: Path, version: str = "9.8.7") -> Path:
    whl = tmp_path / f"icdev-{version}-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as z:
        z.writestr("icdev/__init__.py", "")
        z.writestr(f"icdev-{version}.dist-info/entry_points.txt", ENTRY_POINTS)
        z.writestr(f"icdev-{version}.dist-info/METADATA", f"Name: icdev\nVersion: {version}\n")
    return whl


def _field(text: str, name: str) -> str:
    m = re.search(rf"^{name}=(.*)$", text, re.M)
    assert m, f"{name}= missing"
    return m.group(1)


def test_pkgbuild_pkgver_and_sha_match_the_fixture_wheel(tmp_path):
    whl = _fixture_wheel(tmp_path)
    out = tmp_path / "aur"
    res = rel.emit_aur(whl, out)
    assert res["ok"], res

    expected_sha = hashlib.sha256(whl.read_bytes()).hexdigest()
    pkgbuild = (out / "PKGBUILD").read_text(encoding="utf-8")
    assert _field(pkgbuild, "pkgver") == "9.8.7"
    assert _field(pkgbuild, "sha256sums") == f"('{expected_sha}')"

    srcinfo = (out / ".SRCINFO").read_text(encoding="utf-8")
    assert "\tpkgver = 9.8.7\n" in srcinfo
    assert f"\tsha256sums = {expected_sha}\n" in srcinfo
    assert ("\tsource = icdev-9.8.7-py3-none-any.whl::https://files.pythonhosted.org"
            "/packages/py3/i/icdev/icdev-9.8.7-py3-none-any.whl\n") in srcinfo


def test_symlinks_come_from_the_wheels_console_scripts(tmp_path):
    whl = _fixture_wheel(tmp_path)
    rel.emit_aur(whl, tmp_path / "aur")
    pkgbuild = (tmp_path / "aur" / "PKGBUILD").read_text(encoding="utf-8")
    assert "for _ep in icdev icdev-dashboard icdev-mcp; do" in pkgbuild


def test_rendered_files_are_lf_only(tmp_path):
    """A CRLF PKGBUILD is a bash syntax error; Windows writes CRLF by default."""
    rel.emit_aur(_fixture_wheel(tmp_path), tmp_path / "aur")
    for name in ("PKGBUILD", ".SRCINFO"):
        assert b"\r" not in (tmp_path / "aur" / name).read_bytes(), name


def test_package_installs_into_a_venv_never_system_pip(tmp_path):
    rel.emit_aur(_fixture_wheel(tmp_path), tmp_path / "aur")
    pkgbuild = (tmp_path / "aur" / "PKGBUILD").read_text(encoding="utf-8")
    assert 'python -m venv "${_venv}"' in pkgbuild
    assert '"${_venv}/bin/python" -m pip install' in pkgbuild
    assert "--break-system-packages" not in pkgbuild
    assert _field(pkgbuild, "depends") == "('python' 'git')"
    for opt in ("postgresql", "pgvector", "opencode", "ollama"):
        assert f"'{opt}: " in pkgbuild


def test_non_release_wheel_is_refused(tmp_path):
    bogus = tmp_path / "other-1.0.0-py3-none-any.whl"
    bogus.write_bytes(b"")
    res = rel.emit_aur(bogus, tmp_path / "aur")
    assert not res["ok"]
    assert not (tmp_path / "aur").exists()


def test_cli_aur_from_wheel_writes_the_aur_dir(tmp_path, monkeypatch):
    out = tmp_path / "aur"
    monkeypatch.setattr(rel, "AUR_DIR", out)
    whl = _fixture_wheel(tmp_path)
    assert rel.main(["--aur-from-wheel", str(whl), "--json"]) == 0
    assert _field((out / "PKGBUILD").read_text(encoding="utf-8"), "pkgver") == "9.8.7"


def test_committed_aur_files_are_exactly_what_release_renders():
    """deploy/aur is generated output: re-rendering its own values is byte-identical.

    A hand edit to pkgver, sha256sums, depends or the symlink list shows up here.
    """
    pkgbuild = (AUR / "PKGBUILD").read_text(encoding="utf-8")
    version = _field(pkgbuild, "pkgver")
    sha = _field(pkgbuild, "sha256sums").strip("()'")
    pkgrel = int(_field(pkgbuild, "pkgrel"))
    tag = re.search(r'^_whl="\$\{pkgname\}-\$\{pkgver\}-(.+)\.whl"$', pkgbuild, re.M).group(1)
    scripts = re.search(r"^  for _ep in (.+); do$", pkgbuild, re.M).group(1).split()

    assert pkgbuild == rel.render_pkgbuild(version, tag, sha, scripts, pkgrel)
    assert (AUR / ".SRCINFO").read_text(encoding="utf-8") == \
        rel.render_srcinfo(version, tag, sha, pkgrel)


def test_install_hook_points_at_omarchy_setup():
    assert _field((AUR / "PKGBUILD").read_text(encoding="utf-8"), "install") == "icdev.install"
    hook = (AUR / "icdev.install").read_text(encoding="utf-8")
    assert "post_install()" in hook
    assert "icdev omarchy setup" in hook


def test_install_doc_covers_aur_mise_and_pipx():
    doc = (REPO_ROOT / "docs" / "install" / "omarchy.md").read_text(encoding="utf-8")
    for needle in ("yay -S icdev", "omarchy-mise-install pipx:icdev icdev",
                   "pipx install icdev", "icdev omarchy setup"):
        assert needle in doc, needle
