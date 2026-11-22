#!/usr/bin/env python3
# CUI // SP-CTI
"""The packaged CI allowlist cannot drift from its source unnoticed.

THE INCIDENT. On origin/main 2026-11-22 `icdev/data/args/ci_test_files/core.txt`
was 19 lines shorter than `args/ci_test_files/core.txt` -- the entries for
`tests/innovation/test_competitive_intel_web.py`, `tests/test_creative_engine_sources.py`
and three more -- and 75 `core.d/` fragments had no packaged twin at all. Release
1.2.44 (2026-10-11) had left the two trees identical; nothing compared them
afterwards, because `tools/dx/mirror_parity.py` only knew `tools/` <->
`icdev/tools/`, and `tests/ci/test_gated_test_list.py` checked only that the
packaged copy parsed.

The packaged copy is LIVE, not a leftover: an installed wheel's
`gated_test_list.repo_root` resolves `<pkg>/data/args/ci_test_files`, and
`icdev init` copies `icdev/data/args` into a child project. So the twin is held
byte-identical on every commit by `mirror_parity.DATA_MIRRORS`, the pre-commit
hook and this test.

WHAT THESE TESTS PIN
  * the real tree is in parity (the backstop when the hook is bypassed);
  * the declared tree is the one `gated_test_list` reads;
  * `--files` takes either spelling as one pair, and `--fix` reconciles it;
  * `--all --gate` sees data-mirror drift;
  * the hook BLOCKS a staged edit, a new fragment with no twin, and a deleted
    source whose twin stays -- unlike `tools/`, where a missing twin is a note.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci import gated_test_list
from tools.dx import mirror_parity
from tools.testing import pre_commit_check

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL = REPO_ROOT / "tools" / "dx" / "mirror_parity.py"
SRC = "args/ci_test_files"
DST = "icdev/data/args/ci_test_files"


def _git(root: Path, *args: str) -> None:
    proc = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A committed checkout whose allowlist and its packaged twin are in parity."""
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root.parent, "init", "-q", str(root))
    _git(root, "config", "user.email", "gate@example.test")
    _git(root, "config", "user.name", "Gate Test")
    for side in (SRC, DST):
        _write(root / side / "core.txt", "tests/test_a.py\n")
        _write(root / side / "core.d" / "old-card.txt", "tests/test_b.py\n")
    _write(root / "docs" / "notes.md", "notes\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "baseline")
    return root


def _staged(root: Path) -> list[str]:
    return pre_commit_check._mirror_scope(pre_commit_check._get_staged_name_status(root))


# --------------------------------------------------------------------------- #
# The real tree
# --------------------------------------------------------------------------- #
def test_the_real_packaged_allowlist_is_byte_identical_to_its_source() -> None:
    reports = mirror_parity.audit_data_mirrors(root=REPO_ROOT)
    assert [r["path"] for r in reports] == [SRC]
    for r in reports:
        assert r["live_exists"] and r["mirror_exists"], r
        assert r["clean"], (
            f"{r['mirror_dir']} has drifted from {r['live_dir']}: "
            f"content_drift={r['content_drift']} missing_from_mirror={r['missing_from_mirror']} "
            f"mirror_only={r['mirror_only']}. Reconcile with "
            f"`python tools/dx/mirror_parity.py --files <paths> --fix` and commit both copies."
        )


def test_the_declared_tree_is_the_one_gated_test_list_reads() -> None:
    """If the allowlist ever moves, the mirror declaration must move with it."""
    assert (SRC, DST) in mirror_parity.DATA_MIRRORS
    assert Path(SRC) == gated_test_list.LIST_DIR
    assert Path(DST) == Path("icdev") / "data" / gated_test_list.LIST_DIR


# --------------------------------------------------------------------------- #
# The auditor
# --------------------------------------------------------------------------- #
def test_both_spellings_name_one_pair() -> None:
    expected = (f"{SRC}/core.d/x.txt", f"{DST}/core.d/x.txt")
    assert mirror_parity.data_mirror_pair(f"{SRC}/core.d/x.txt") == expected
    assert mirror_parity.data_mirror_pair(f"{DST}/core.d/x.txt") == expected
    assert mirror_parity.data_mirror_pair("args/other.yaml") is None
    assert mirror_parity.data_mirror_pair("args/ci_test_files_extra/x.txt") is None
    assert mirror_parity.twin_of(f"{SRC}/core.txt") == f"{DST}/core.txt"
    assert mirror_parity.twin_of("db/storage.py") == "icdev/tools/db/storage.py"


def test_files_audit_classifies_and_fixes(repo: Path) -> None:
    _write(repo / SRC / "core.txt", "tests/test_a.py\ntests/test_new.py\n")
    _write(repo / SRC / "core.d" / "new-card.txt", "tests/test_c.py\n")
    report = mirror_parity.audit_files(
        [f"{DST}/core.txt", f"{SRC}/core.d/new-card.txt", f"{SRC}/core.d/old-card.txt"], root=repo,
    )
    assert report["content_drift"] == [f"{SRC}/core.txt"]
    assert report["missing_from_mirror"] == [f"{SRC}/core.d/new-card.txt"]
    assert report["in_parity"] == [f"{SRC}/core.d/old-card.txt"]
    assert not report["clean"]

    fixed = mirror_parity.audit_files([f"{SRC}/core.txt", f"{SRC}/core.d/new-card.txt"], fix=True, root=repo)
    assert sorted(fixed["copied"]) == [f"{SRC}/core.d/new-card.txt", f"{SRC}/core.txt"]
    assert (repo / DST / "core.txt").read_bytes() == (repo / SRC / "core.txt").read_bytes()
    assert (repo / DST / "core.d" / "new-card.txt").is_file()


def test_all_gate_sees_data_mirror_drift(repo: Path) -> None:
    """`--all --gate` is the whole-tree gate; it must not be blind to this tree."""
    def _run(*extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(TOOL), "--all", "--root", str(repo), *extra],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )

    assert _run("--gate").returncode == 0
    _write(repo / SRC / "core.txt", "tests/test_a.py\ntests/test_new.py\n")
    (repo / SRC / "core.d" / "old-card.txt").unlink()
    proc = _run("--gate")
    assert proc.returncode == 1, proc.stdout
    assert f"[DRIFT] {SRC}" in proc.stdout
    assert "content-drift:       core.txt" in proc.stdout
    assert "mirror-only (INVESTIGATE, not deleted): core.d/old-card.txt" in proc.stdout


# --------------------------------------------------------------------------- #
# The hook
# --------------------------------------------------------------------------- #
def test_hook_blocks_an_edit_to_the_source_alone(repo: Path, capsys) -> None:
    _write(repo / SRC / "core.txt", "tests/test_a.py\ntests/test_new.py\n")
    _git(repo, "add", "-A")
    files = _staged(repo)
    assert files == [f"{SRC}/core.txt"]
    assert pre_commit_check._run_mirror_parity(files, root=repo) is False
    out = capsys.readouterr().out
    assert f"{SRC}/core.txt  !=  {DST}/core.txt" in out
    assert f"--files {SRC}/core.txt --fix" in out


def test_hook_blocks_a_new_fragment_with_no_twin(repo: Path, capsys) -> None:
    """The 75 missing fragments: for tools/ a missing twin is only a note."""
    _write(repo / SRC / "core.d" / "new-card.txt", "tests/test_c.py\n")
    _git(repo, "add", "-A")
    files = _staged(repo)
    assert pre_commit_check._run_mirror_parity(files, root=repo) is False
    assert "BLOCKED" in capsys.readouterr().out


def test_hook_blocks_a_deleted_source_whose_twin_stays(repo: Path) -> None:
    _git(repo, "rm", "-q", f"{SRC}/core.d/old-card.txt")
    files = _staged(repo)
    assert files == [f"{SRC}/core.d/old-card.txt"]
    assert pre_commit_check._run_mirror_parity(files, root=repo) is False


def test_hook_passes_when_both_copies_change_together(repo: Path) -> None:
    for side in (SRC, DST):
        _write(repo / side / "core.d" / "new-card.txt", "tests/test_c.py\n")
    _git(repo, "rm", "-q", f"{SRC}/core.d/old-card.txt", f"{DST}/core.d/old-card.txt")
    _git(repo, "add", "-A")
    files = _staged(repo)
    assert len(files) == 4
    assert pre_commit_check._run_mirror_parity(files, root=repo) is True


def test_commit_outside_the_tree_stays_out_of_scope() -> None:
    assert _staged_from([("M", "docs/notes.md"), ("D", "args/other.yaml")]) == []


def _staged_from(name_status: list[tuple[str, str]]) -> list[str]:
    return pre_commit_check._mirror_scope(name_status)
