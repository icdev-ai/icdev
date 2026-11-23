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


# --------------------------------------------------------------------------- #
# Review of #2603, finding 1: the gate promoter writes the fragment AND its twin
# --------------------------------------------------------------------------- #
def test_the_promoters_fragment_has_a_packaged_twin_in_parity(repo: Path, monkeypatch) -> None:
    from tools.ci import gate_promoter as gp

    monkeypatch.setattr(gp, "REPO_ROOT", repo)
    monkeypatch.setattr(gp, "FRAGMENT_DIR", repo / SRC / "core.d")
    rel = gp._write_fragment(["tests/test_a.py", "tests/test_b.py"])
    assert rel.startswith(f"{SRC}/core.d/auto-promote-")
    twin = repo / mirror_parity.twin_of(rel)
    assert twin.is_file()
    assert twin.read_bytes() == (repo / rel).read_bytes()
    assert all(r["clean"] for r in mirror_parity.audit_data_mirrors(root=repo))


def test_the_promoter_workflow_stages_the_packaged_twin() -> None:
    text = (REPO_ROOT / ".github" / "workflows" / "gate-promoter.yml").read_text(encoding="utf-8")
    add = text[text.index("git add args/ci_test_files/core.d"):]
    add = add[: add.index("git commit")]
    assert f"{DST}/core.d" in add, add


def test_the_written_rules_name_the_packaged_twin() -> None:
    """Every place that tells an author to write a fragment names its twin too."""
    claude = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    rule = claude[claude.index("Write ONE fragment named for your task"):][:1500]
    assert f"{DST}/core.d/" in rule and "mirror_parity.py --files" in rule
    policy = (REPO_ROOT / "docs" / "ci" / "test-gating-policy.md").read_text(encoding="utf-8")
    assert f"{DST}" in policy and "mirror_parity.py --files" in policy


# --------------------------------------------------------------------------- #
# Finding 2: the hook compares the STAGED index for a data-mirror pair
# --------------------------------------------------------------------------- #
def test_hook_blocks_when_the_twin_is_edited_but_not_staged(repo: Path) -> None:
    for side in (SRC, DST):
        _write(repo / side / "core.txt", "tests/test_a.py\ntests/test_new.py\n")
    _git(repo, "add", f"{SRC}/core.txt")
    files = _staged(repo)
    assert files == [f"{SRC}/core.txt"]
    assert pre_commit_check._run_mirror_parity(files, root=repo) is False


def test_hook_blocks_after_rm_cached_of_the_source(repo: Path) -> None:
    _git(repo, "rm", "-q", "--cached", f"{SRC}/core.d/old-card.txt")
    assert (repo / SRC / "core.d" / "old-card.txt").is_file(), "precondition: still on disk"
    files = _staged(repo)
    assert files == [f"{SRC}/core.d/old-card.txt"]
    assert pre_commit_check._run_mirror_parity(files, root=repo) is False


def test_hook_blocks_when_the_twin_is_deleted_but_not_staged(repo: Path) -> None:
    _git(repo, "rm", "-q", f"{SRC}/core.d/old-card.txt")
    (repo / DST / "core.d" / "old-card.txt").unlink()  # working tree only
    files = _staged(repo)
    assert pre_commit_check._run_mirror_parity(files, root=repo) is False


def test_hook_ignores_an_unstaged_wip_edit_once_the_index_is_in_parity(repo: Path) -> None:
    for side in (SRC, DST):
        _write(repo / side / "core.txt", "tests/test_a.py\ntests/test_new.py\n")
    _git(repo, "add", "-A")
    _write(repo / SRC / "core.txt", "tests/test_a.py\ntests/test_new.py\ntests/wip.py\n")
    files = _staged(repo)
    assert pre_commit_check._run_mirror_parity(files, root=repo) is True


def test_staged_audit_reads_blobs_not_the_working_tree(repo: Path) -> None:
    _write(repo / SRC / "core.txt", "tests/test_a.py\ntests/wip.py\n")  # unstaged
    report = mirror_parity.audit_files([f"{SRC}/core.txt"], root=repo, staged=True)
    assert report["in_parity"] == [f"{SRC}/core.txt"], report
    _git(repo, "add", f"{SRC}/core.txt")
    report = mirror_parity.audit_files([f"{SRC}/core.txt"], root=repo, staged=True)
    assert report["content_drift"] == [f"{SRC}/core.txt"], report


def test_the_hook_comment_does_not_claim_tools_pairs_compare_the_index() -> None:
    src = (REPO_ROOT / "tools" / "testing" / "pre_commit_check.py").read_text(encoding="utf-8")
    assert "Compares the STAGED files to their twins, nothing else." not in src


# --------------------------------------------------------------------------- #
# Finding 3: renames keep their SOURCE in scope; typechanges are handled
# --------------------------------------------------------------------------- #
def _hook_scope(root: Path) -> list[str]:
    return pre_commit_check._mirror_scope(
        pre_commit_check._get_staged_name_status(root),
        rename_sources=pre_commit_check._get_staged_rename_sources(root),
    )


def test_rename_inside_the_tree_with_a_stale_twin_of_the_old_name_blocks(repo: Path) -> None:
    _git(repo, "mv", f"{SRC}/core.d/old-card.txt", f"{SRC}/core.d/renamed.txt")
    _write(repo / DST / "core.d" / "renamed.txt", "tests/test_b.py\n")
    _git(repo, "add", f"{DST}/core.d/renamed.txt")
    files = _hook_scope(repo)
    assert f"{SRC}/core.d/old-card.txt" in files
    assert pre_commit_check._run_mirror_parity(files, root=repo) is False


def test_a_move_out_of_the_tree_orphans_the_twin_and_blocks(repo: Path) -> None:
    _git(repo, "mv", f"{SRC}/core.d/old-card.txt", "docs/old-card.txt")
    files = _hook_scope(repo)
    assert files == [f"{SRC}/core.d/old-card.txt"]
    assert pre_commit_check._run_mirror_parity(files, root=repo) is False


def test_a_move_of_both_copies_together_passes(repo: Path) -> None:
    _git(repo, "mv", f"{SRC}/core.d/old-card.txt", f"{SRC}/core.d/renamed.txt")
    _git(repo, "mv", f"{DST}/core.d/old-card.txt", f"{DST}/core.d/renamed.txt")
    files = _hook_scope(repo)
    assert pre_commit_check._run_mirror_parity(files, root=repo) is True


def test_scope_takes_typechange_and_rename_sources() -> None:
    staged = [("T", f"{SRC}/core.txt"), ("R100", "docs/moved.txt")]
    assert _staged_from_with(staged, [f"{SRC}/core.d/x.txt"]) == [
        f"{SRC}/core.txt", f"{SRC}/core.d/x.txt",
    ]


def test_a_typechange_to_a_symlink_blocks(repo: Path, capsys) -> None:
    target = repo / SRC / "core.d" / "old-card.txt"
    target.unlink()
    target.symlink_to("../core.txt")
    _git(repo, "add", "-A")
    assert ("T", f"{SRC}/core.d/old-card.txt") in pre_commit_check._get_staged_name_status(repo)
    files = _hook_scope(repo)
    assert pre_commit_check._run_mirror_parity(files, root=repo) is False
    assert "BLOCKED" in capsys.readouterr().out


def _staged_from_with(name_status, rename_sources) -> list[str]:
    return pre_commit_check._mirror_scope(name_status, rename_sources=rename_sources)


# --------------------------------------------------------------------------- #
# Finding 4: --fix never leaves the declared trees
# --------------------------------------------------------------------------- #
def test_a_dotdot_path_is_refused_not_ignored(repo: Path) -> None:
    for raw in (f"{SRC}/../../outside.txt", f"{DST}/core.d/../../../x.txt", "tools/widget/../../x.py"):
        report = mirror_parity.audit_files([raw], fix=True, root=repo)
        assert report["refused"], (raw, report)
        assert report["copied"] == [] and not report["clean"]


def test_fix_refuses_a_symlinked_source(repo: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("not for the package\n", encoding="utf-8")
    (repo / SRC / "core.d" / "link.txt").symlink_to(secret)
    report = mirror_parity.audit_files([f"{SRC}/core.d/link.txt"], fix=True, root=repo)
    assert report["refused"] and report["copied"] == []
    assert not (repo / DST / "core.d" / "link.txt").exists()
    reports = mirror_parity.audit_data_mirrors(fix=True, root=repo)
    assert reports[0]["refused"] and "core.d/link.txt" not in reports[0]["copied"]
    assert not (repo / DST / "core.d" / "link.txt").exists()


def test_fix_refuses_a_symlinked_destination(repo: Path, tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("untouched\n", encoding="utf-8")
    dst = repo / DST / "core.d" / "old-card.txt"
    dst.unlink()
    dst.symlink_to(victim)
    _write(repo / SRC / "core.d" / "old-card.txt", "tests/changed.py\n")
    report = mirror_parity.audit_files([f"{SRC}/core.d/old-card.txt"], fix=True, root=repo)
    assert report["refused"] and report["copied"] == []
    assert victim.read_text(encoding="utf-8") == "untouched\n"


def test_fix_refuses_a_symlinked_parent_directory(repo: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    real = repo / DST / "core.d"
    for f in real.iterdir():
        f.unlink()
    real.rmdir()
    real.symlink_to(elsewhere, target_is_directory=True)
    report = mirror_parity.audit_files([f"{SRC}/core.d/old-card.txt"], fix=True, root=repo)
    assert report["refused"] and report["copied"] == []
    assert list(elsewhere.iterdir()) == []


# --------------------------------------------------------------------------- #
# Finding 5: directories, the banner, the summary and the coherence label
# --------------------------------------------------------------------------- #
def test_files_refuses_a_directory_rather_than_reporting_clean(repo: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--files", f"{SRC}/core.d", "--gate", "--root", str(repo)],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert proc.returncode != 0, proc.stdout
    assert "directory" in proc.stdout + proc.stderr
    assert "CLEAN" not in proc.stdout


def test_hook_banner_does_not_say_tools_for_a_data_path(repo: Path, capsys) -> None:
    _write(repo / SRC / "core.txt", "tests/test_a.py\ntests/test_new.py\n")
    _git(repo, "add", "-A")
    pre_commit_check._run_mirror_parity(_staged(repo), root=repo)
    banner = [ln for ln in capsys.readouterr().out.splitlines() if "Mirror parity (" in ln]
    assert banner and "under tools/)" not in banner[0], banner


def test_the_summary_counts_data_trees_apart_from_packages(repo: Path) -> None:
    summary = mirror_parity.audit_all(root=repo)
    assert summary["packages_audited"] == 0
    assert summary["data_trees_audited"] == len(mirror_parity.DATA_MIRRORS)


def test_coherence_labels_a_data_tree_path_by_its_own_root(monkeypatch) -> None:
    from tools.workflow import coherence_checker

    fake = {"reports": [
        {"path": SRC, "live_dir": SRC, "content_drift": ["core.d/x.py"]},
        {"path": "db", "live_dir": "tools/db", "content_drift": ["y.py"]},
    ]}
    monkeypatch.setattr(mirror_parity, "audit_all", lambda fix=False, root=None: fake)
    assert coherence_checker._mirror_drifted_files([".py"]) == [
        f"{SRC}/core.d/x.py", "tools/db/y.py",
    ]
