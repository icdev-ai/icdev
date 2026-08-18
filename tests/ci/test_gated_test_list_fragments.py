# CUI // SP-CTI
"""Two PRs adding a test must not touch the same file.

`args/ci_test_files/core.txt` was the largest merge-collision surface in the
repository: **82.8% of merged kanban PRs touched it**, because CLAUDE.md requires
every PR that adds a test file to append to it. GitHub does not apply the
`.gitattributes merge=union` rule, so every one of those PRs went CONFLICTING the
moment a sibling merged — 30.9% needed a rebase and 27.4% escalated to a human.

A PR now writes one fragment named for its task. Two differently-named files
cannot conflict at all.

Purely additive: `core.txt` keeps every entry, nothing migrated, and both are
read as one list — so the duplicate check, the floor and the census all see the
combined set. These tests pin that, because a fragment that were read
*separately* would let a duplicate or a truncation through.
"""
from __future__ import annotations

import pytest

from tools.ci import gated_test_list as g


@pytest.fixture
def repo(tmp_path):
    """A checkout skeleton with a list file and an empty fragment directory."""
    d = tmp_path / "args" / "ci_test_files"
    d.mkdir(parents=True)
    (d / "core.txt").write_text(
        "# a comment\ntests/test_a.py\ntests/test_b.py\n", encoding="utf-8")
    (d / "core.d").mkdir()
    return tmp_path


def test_the_list_file_alone_still_resolves(repo):
    """Additive means the old path keeps working untouched."""
    assert g.resolve("core", repo) == ["tests/test_a.py", "tests/test_b.py"]


def test_a_fragment_is_read_with_the_list(repo):
    (repo / "args/ci_test_files/core.d/task-1.txt").write_text(
        "tests/test_c.py\n", encoding="utf-8")
    assert g.resolve("core", repo) == [
        "tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]


def test_two_fragments_both_land(repo):
    """The whole point: two PRs, two files, no shared path."""
    frag = repo / "args/ci_test_files/core.d"
    (frag / "task-1.txt").write_text("tests/test_c.py\n", encoding="utf-8")
    (frag / "task-2.txt").write_text("tests/test_d.py\n", encoding="utf-8")
    assert g.resolve("core", repo)[-2:] == ["tests/test_c.py", "tests/test_d.py"]


def test_fragment_order_is_deterministic(repo):
    """CI runs these in ONE process in list order, so a test's neighbours — and
    therefore its isolation behaviour — must not depend on the filesystem's
    directory-listing order."""
    frag = repo / "args/ci_test_files/core.d"
    for name in ("zz.txt", "aa.txt", "mm.txt"):
        (frag / name).write_text(f"tests/test_{name[:2]}.py\n", encoding="utf-8")
    assert g.resolve("core", repo)[2:] == [
        "tests/test_aa.py", "tests/test_mm.py", "tests/test_zz.py"]


def test_fragments_honour_comments_and_blanks(repo):
    """Same parser as the list file — one syntax, not two."""
    (repo / "args/ci_test_files/core.d/task-1.txt").write_text(
        "# why this is gated\n\ntests/test_c.py\n\n# trailing note\n",
        encoding="utf-8")
    assert g.resolve("core", repo)[-1:] == ["tests/test_c.py"]


def test_an_absent_fragment_directory_is_normal(repo):
    """A checkout with no fragments yet must resolve, not raise."""
    (repo / "args/ci_test_files/core.d").rmdir()
    assert g.resolve("core", repo) == ["tests/test_a.py", "tests/test_b.py"]


def test_an_empty_fragment_directory_is_normal(repo):
    assert g.resolve("core", repo) == ["tests/test_a.py", "tests/test_b.py"]


def test_a_missing_list_file_still_RAISES(repo):
    """An empty fragment dir is normal; a missing core.txt means the allowlist
    could not be resolved, and the gate must not quietly run nothing."""
    (repo / "args/ci_test_files/core.txt").unlink()
    with pytest.raises(g.AllowlistError):
        g.resolve("core", repo)


# ── the combined set is what gets validated ────────────────────────────────
def test_a_duplicate_ACROSS_a_fragment_and_the_list_is_caught(repo):
    """The failure mode of reading them separately. Union-merging `core.txt`
    already leaves byte-identical duplicate rows; fragments must not create a
    second, invisible way to double-gate a file."""
    (repo / "args/ci_test_files/core.d/task-1.txt").write_text(
        "tests/test_a.py\n", encoding="utf-8")
    report = g.check("core", repo)
    assert "tests/test_a.py" in report["duplicates"]
    assert report["ok"] is False


def test_a_duplicate_ACROSS_TWO_fragments_is_caught(repo):
    frag = repo / "args/ci_test_files/core.d"
    (frag / "task-1.txt").write_text("tests/test_c.py\n", encoding="utf-8")
    (frag / "task-2.txt").write_text("tests/test_c.py\n", encoding="utf-8")
    assert "tests/test_c.py" in g.check("core", repo)["duplicates"]


def test_fragments_count_toward_the_truncation_floor(repo):
    """The floor is a truncation backstop. If fragments were excluded from it, a
    list that had migrated into fragments would look empty and the backstop
    would fire on a healthy repo — or worse, stop firing on a gutted one."""
    entries = g.resolve("core", repo)
    (repo / "args/ci_test_files/core.d/task-1.txt").write_text(
        "\n".join(f"tests/test_{i}.py" for i in range(50)) + "\n",
        encoding="utf-8")
    assert len(g.resolve("core", repo)) == len(entries) + 50


def test_the_windows_list_has_a_fragment_dir_too(repo):
    """Both gated lists, or the next task to add a windows test is back to
    editing a shared file."""
    assert "windows" in g.FRAGMENT_DIRS
    assert g.FRAGMENT_DIRS["core"] == "core.d"


def test_every_list_has_a_fragment_directory():
    """A list without one silently keeps the collision."""
    assert set(g.LISTS) == set(g.FRAGMENT_DIRS)
