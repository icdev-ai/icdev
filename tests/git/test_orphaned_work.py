"""Orphaned-work detection, against REAL git repositories built per test.

WHY REAL REPOS AND NOT MOCKS. Every false positive this detector shipped with came from
misreading `git status --porcelain`, so a fake that returns the porcelain I already expect
would prove only that I can restate my own assumption. These tests run `git init`, make the
condition, and ask the detector.

A DETECTOR THAT REPORTS ZERO IS WORTHLESS UNLESS IT CAN DETECT. On this machine the live scan
finds 0 orphaned across 226 worktrees, which is the correct answer and indistinguishable from
a broken scan. `test_the_incident_shape_is_detected` is what tells those apart.

THE THREE FALSE POSITIVES THAT DIED HERE, each pinned so it cannot come back:
  * deletions counted as work        -- an emptied husk read as ~19,000 files of lost work
  * whole-repo-untracked counted     -- an index cleared while files stayed presents as
                                        18,882 deletions plus the entire repo untracked
  * committed / pushed work          -- recoverable from git or the forge, never orphaned
"""

import subprocess

import pytest

from tools.git import orphaned_work as ow


def _run(*args, cwd):
    subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=False)


@pytest.fixture
def repo(tmp_path):
    """A git repo with one commit on `main`, and an `origin/main` ref to measure against."""
    r = tmp_path / "repo"
    r.mkdir()
    _run("git", "init", "-b", "main", cwd=r)
    _run("git", "config", "user.email", "t@example.com", cwd=r)
    _run("git", "config", "user.name", "t", cwd=r)
    (r / "kept.txt").write_text("original\n", encoding="utf-8")
    (r / "second.txt").write_text("second\n", encoding="utf-8")
    _run("git", "add", "-A", cwd=r)
    _run("git", "commit", "-m", "base", cwd=r)
    # a local stand-in for the remote-tracking ref the detector compares against
    _run("git", "update-ref", "refs/remotes/origin/main", "HEAD", cwd=r)
    return r


def _wt(path, branch="feat/x"):
    return ow.Worktree(path=path, branch=branch)


def _assess(path, branch="feat/x", stale=0):
    return ow.assess(_wt(path, branch), base="origin/main", stale_minutes=stale)


class TestTheIncidentShapeIsDetected:
    """A dispatch timed out leaving modified files and untracked new modules, on a branch
    with no commits and no remote. That is what must be found."""

    def test_modified_plus_untracked_with_no_commits_is_orphaned(self, repo):
        _run("git", "checkout", "-b", "feat/x", cwd=repo)
        (repo / "kept.txt").write_text("edited by the dispatch\n", encoding="utf-8")
        (repo / "new_module.py").write_text("# 1178 lines of it\n", encoding="utf-8")
        w = _assess(repo)
        assert w.verdict == "orphaned", w.reason
        assert w.dirty_files == 2 and w.deleted_files == 0
        assert w.commits_ahead == 0 and w.has_remote_ref is False

    def test_the_reason_names_what_would_be_lost(self, repo):
        _run("git", "checkout", "-b", "feat/x", cwd=repo)
        (repo / "new_module.py").write_text("work\n", encoding="utf-8")
        w = _assess(repo)
        assert "never pushed" in w.reason and "0 commits" in w.reason


class TestDeletionsAreNotWork:
    def test_an_emptied_tree_is_EMPTIED_not_orphaned(self, repo):
        """Contents removed from disk: git holds every one of those files."""
        _run("git", "checkout", "-b", "feat/x", cwd=repo)
        (repo / "kept.txt").unlink()
        (repo / "second.txt").unlink()
        w = _assess(repo)
        assert w.verdict == "emptied" and w.dirty_files == 0 and w.deleted_files == 2
        assert "git already holds this" in w.reason

    def test_an_index_cleared_tree_is_EMPTIED_even_though_files_look_untracked(self, repo):
        """The 18,882-vs-96 case. Clearing the index leaves the files on disk, so they present
        as UNTRACKED -- and a detector counting untracked entries calls the whole repo new
        work. Deletions dominating additions is what tells them apart."""
        _run("git", "checkout", "-b", "feat/x", cwd=repo)
        _run("git", "rm", "-r", "--cached", ".", cwd=repo)
        w = _assess(repo)
        assert w.deleted_files >= w.dirty_files
        assert w.verdict == "emptied", w.reason

    def test_additions_outnumbering_deletions_is_still_orphaned(self, repo):
        """One deleted file alongside real new work must not excuse the work."""
        _run("git", "checkout", "-b", "feat/x", cwd=repo)
        (repo / "kept.txt").unlink()
        for i in range(3):
            (repo / f"new{i}.py").write_text("work\n", encoding="utf-8")
        w = _assess(repo)
        assert w.dirty_files == 3 and w.deleted_files == 1
        assert w.verdict == "orphaned"


class TestWorkThatIsSafeIsNeverOrphaned:
    def test_committed_work_is_active(self, repo):
        _run("git", "checkout", "-b", "feat/x", cwd=repo)
        (repo / "new.py").write_text("work\n", encoding="utf-8")
        _run("git", "add", "-A", cwd=repo)
        _run("git", "commit", "-m", "wip", cwd=repo)
        (repo / "more.py").write_text("more\n", encoding="utf-8")
        w = _assess(repo)
        assert w.verdict == "active" and "in git" in w.reason

    def test_pushed_work_is_active(self, repo):
        _run("git", "checkout", "-b", "feat/x", cwd=repo)
        _run("git", "update-ref", "refs/remotes/origin/feat/x", "HEAD", cwd=repo)
        (repo / "new.py").write_text("work\n", encoding="utf-8")
        w = _assess(repo)
        assert w.verdict == "active" and "on the forge" in w.reason

    def test_recently_edited_work_is_active_not_orphaned(self, repo):
        """Somebody may still be typing. The threshold is the whole difference."""
        _run("git", "checkout", "-b", "feat/x", cwd=repo)
        (repo / "new.py").write_text("work\n", encoding="utf-8")
        w = ow.assess(_wt(repo), base="origin/main", stale_minutes=10_000)
        assert w.verdict == "active" and "still hold it" in w.reason

    def test_a_clean_tree_is_clean(self, repo):
        _run("git", "checkout", "-b", "feat/x", cwd=repo)
        assert _assess(repo).verdict == "clean"


class TestUnestablishedIsUnknownNeverOrphaned:
    def test_a_missing_path_is_unknown(self, tmp_path):
        w = _assess(tmp_path / "gone")
        assert w.verdict == "unknown" and "does not exist" in w.reason

    def test_a_non_repo_is_unknown_not_orphaned(self, tmp_path):
        d = tmp_path / "notarepo"
        d.mkdir()
        (d / "f.txt").write_text("x", encoding="utf-8")
        assert _assess(d).verdict == "unknown"


class TestScanReporting:
    def test_a_scan_that_established_nothing_reports_None_not_zero(self, tmp_path):
        """`orphaned_count` None means the scan could not measure -- which is not the same
        answer as 'no abandoned work', and they justify opposite actions."""
        res = ow.scan(tmp_path / "nope")
        assert res["worktrees"] == 0 and res["orphaned_count"] is None

    def test_the_module_never_deletes(self):
        """Asserted over the AST. The failure mode being fixed is 'nobody knew it was there';
        a tool that also removed things would turn detection into data loss."""
        import ast
        import pathlib
        src = pathlib.Path(ow.__file__).read_text(encoding="utf-8")
        banned = {"rmtree", "unlink", "remove", "rmdir"}
        found = {n.attr for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.Attribute) and n.attr in banned}
        assert not found, f"orphaned_work must never delete: {found}"

        # And every git SUBCOMMAND it issues must be read-only. Checked on the actual call
        # arguments, not on the source text: the first draft grepped for "reset"/"prune" and
        # failed on the word appearing in a COMMENT, which is a test measuring prose.
        readonly = {"worktree", "status", "rev-list", "rev-parse", "log", "diff", "config"}
        issued = set()
        for n in ast.walk(ast.parse(src)):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "_git" and n.args):
                first = n.args[0]
                if isinstance(first, ast.List) and first.elts:
                    lead = first.elts[0]
                    if isinstance(lead, ast.Constant) and isinstance(lead.value, str):
                        issued.add(lead.value)
        assert issued, "no _git calls found -- the check would pass vacuously"
        assert issued <= readonly, f"orphaned_work issued a mutating git command: {issued - readonly}"
