"""Worktree-bound file-editing toolset for the rubric-gated build loop."""
import subprocess

import pytest

from tools.genesis.rubric_build_tools import build_worktree_toolset
from tools.llm.agent_loop import DONE


def _handlers(tmp_path):
    tools, handlers = build_worktree_toolset(str(tmp_path))
    return tools, handlers


def test_schemas_and_readonly_flags(tmp_path):
    tools, handlers = _handlers(tmp_path)
    names = {t["function"]["name"] for t in tools}
    assert names == {
        "read_file", "list_files", "write_file", "patch_file",
        "grep_files", "search_files", "git_diff", "run_command", "done",
    }
    ro = {t["function"]["name"] for t in tools if t["function"].get("is_read_only")}
    # is_read_only is what the agent loop partitions on to decide what runs
    # CONCURRENTLY, so a mutating tool must never appear here.
    assert ro == {"read_file", "list_files", "grep_files", "search_files", "git_diff"}
    assert not ro.intersection({"write_file", "patch_file", "run_command"})
    assert set(handlers) == names


def test_write_read_roundtrip(tmp_path):
    _, h = _handlers(tmp_path)
    out = h["write_file"]({"path": "pkg/mod.py", "content": "print('hi')\n"}, None)
    assert "Wrote" in out
    assert (tmp_path / "pkg" / "mod.py").read_text() == "print('hi')\n"
    assert h["read_file"]({"path": "pkg/mod.py"}, None) == "print('hi')\n"


def test_read_missing_file(tmp_path):
    _, h = _handlers(tmp_path)
    assert h["read_file"]({"path": "nope.py"}, None).startswith("error: file not found")


def test_patch_unique_zero_multi(tmp_path):
    _, h = _handlers(tmp_path)
    h["write_file"]({"path": "a.txt", "content": "x y x"}, None)
    # multiple matches -> refused
    assert "appears 2 times" in h["patch_file"]({"path": "a.txt", "old_string": "x", "new_string": "z"}, None)
    # zero matches -> refused
    assert "not found" in h["patch_file"]({"path": "a.txt", "old_string": "q", "new_string": "z"}, None)
    # unique match -> applied
    assert "Patched" in h["patch_file"]({"path": "a.txt", "old_string": "y", "new_string": "Y"}, None)
    assert (tmp_path / "a.txt").read_text() == "x Y x"


def test_patch_requires_both_strings(tmp_path):
    _, h = _handlers(tmp_path)
    h["write_file"]({"path": "a.txt", "content": "abc"}, None)
    assert "required" in h["patch_file"]({"path": "a.txt", "old_string": "a"}, None)


def test_list_files(tmp_path):
    _, h = _handlers(tmp_path)
    (tmp_path / "sub").mkdir()
    (tmp_path / "f.py").write_text("x")
    out = h["list_files"]({"path": "."}, None)
    assert "sub/" in out and "f.py" in out


def test_traversal_escape_is_blocked(tmp_path):
    _, h = _handlers(tmp_path)
    # write outside the worktree root must be refused
    assert h["write_file"]({"path": "../escape.py", "content": "bad"}, None) == "error: path escapes the worktree root"
    assert not (tmp_path.parent / "escape.py").exists()
    # read outside too
    assert h["read_file"]({"path": "../../etc/passwd"}, None) == "error: path escapes the worktree root"


def test_done_returns_sentinel(tmp_path):
    _, h = _handlers(tmp_path)
    assert h["done"]({}, None) is DONE


# ── grep_files / search_files / git_diff / run_command ────────────────────────


def test_grep_finds_matches_with_line_numbers(tmp_path):
    _, h = _handlers(tmp_path)
    h["write_file"]({"path": "pkg/a.py", "content": "import os\ndef foo():\n    pass\n"}, None)
    out = h["grep_files"]({"pattern": r"def \w+"}, None)
    assert "pkg/a.py:2:def foo():" in out


def test_grep_falls_back_to_literal_on_invalid_regex(tmp_path):
    _, h = _handlers(tmp_path)
    h["write_file"]({"path": "a.py", "content": "value = foo(bar)\n"}, None)
    out = h["grep_files"]({"pattern": "foo(bar"}, None)  # unbalanced paren
    assert "a.py:1:" in out


def test_grep_reports_no_matches_and_requires_a_pattern(tmp_path):
    _, h = _handlers(tmp_path)
    h["write_file"]({"path": "a.py", "content": "x = 1\n"}, None)
    assert h["grep_files"]({"pattern": "zzzz"}, None) == "(no matches)"
    assert "required" in h["grep_files"]({}, None)


def test_grep_honours_max_results(tmp_path):
    _, h = _handlers(tmp_path)
    h["write_file"]({"path": "a.py", "content": "hit\n" * 20}, None)
    out = h["grep_files"]({"pattern": "hit", "max_results": 3}, None)
    assert "truncated" in out
    assert len([ln for ln in out.splitlines() if ln.startswith("a.py:")]) == 3


def test_grep_skips_noise_directories(tmp_path):
    _, h = _handlers(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "obj").write_text("needle")
    h["write_file"]({"path": "real.py", "content": "needle\n"}, None)
    out = h["grep_files"]({"pattern": "needle"}, None)
    assert "real.py" in out
    assert ".git" not in out


def test_grep_respects_stop_event(tmp_path):
    import threading
    _, h = _handlers(tmp_path)
    h["write_file"]({"path": "a.py", "content": "needle\n"}, None)
    ev = threading.Event()
    ev.set()
    assert "interrupted" in h["grep_files"]({"pattern": "needle"}, ev)


def test_search_files_by_glob(tmp_path):
    _, h = _handlers(tmp_path)
    h["write_file"]({"path": "pkg/test_a.py", "content": "x"}, None)
    h["write_file"]({"path": "pkg/b.py", "content": "x"}, None)
    out = h["search_files"]({"pattern": "**/test_*.py"}, None)
    assert out == "pkg/test_a.py"


def test_grep_and_search_are_traversal_guarded(tmp_path):
    _, h = _handlers(tmp_path)
    assert h["grep_files"]({"pattern": "x", "path": ".."}, None).startswith("error: path escapes")
    assert h["search_files"]({"pattern": "*", "path": ".."}, None).startswith("error: path escapes")


def test_git_diff_reports_uncommitted_changes(tmp_path):
    if subprocess.run(["git", "--version"], capture_output=True).returncode != 0:
        pytest.skip("git unavailable")

    def git(*a):
        return subprocess.run(["git", *a], cwd=str(tmp_path), capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    _, h = _handlers(tmp_path)
    h["write_file"]({"path": "a.txt", "content": "one\n"}, None)
    git("add", "a.txt")
    git("commit", "-qm", "base")

    assert h["git_diff"]({}, None) == "(no uncommitted changes)"
    h["patch_file"]({"path": "a.txt", "old_string": "one", "new_string": "two"}, None)
    out = h["git_diff"]({}, None)
    assert "-one" in out and "+two" in out
    assert "a.txt" in h["git_diff"]({"stat": True}, None)


def test_run_command_refuses_non_allowlisted(tmp_path):
    """The allowlist is python-only, which is also what makes it portable —
    no shell, no POSIX-only binary."""
    _, h = _handlers(tmp_path)
    out = h["run_command"]({"command": "rm -rf /"}, None)
    assert out.startswith("refused:")
    assert "required" in h["run_command"]({}, None)


def test_run_command_can_run_the_gates_it_is_graded_by(tmp_path):
    """pytest and ruff are in make_pipeline_grader's gate suite. An agent that
    cannot run them is judged on checks it can only guess at."""
    _, h = _handlers(tmp_path)
    out = h["run_command"]({"command": "python -m pytest --version", "timeout": 120}, None)
    assert not out.startswith("refused:"), out
    assert "exit_code=0" in out


def test_run_command_runs_in_the_worktree(tmp_path):
    """cwd must be the worktree, not the shared checkout — otherwise the agent
    inspects the wrong tree and the grader reports on the wrong branch."""
    _, h = _handlers(tmp_path)
    # The `tools/` directory must really exist: the allowlist forces the path to
    # start with `tools/`, and POSIX resolves `tools/..` by walking it, so a
    # missing directory is ENOENT there. Windows collapses `tools/..` lexically
    # and never notices. Without this mkdir the test passes on Windows and fails
    # on Linux — the mirror image of the bug this file was written for.
    (tmp_path / "tools").mkdir()
    (tmp_path / "marker.py").write_text("print('in-worktree')\n")
    out = h["run_command"]({"command": "python tools/../marker.py", "timeout": 60}, None)
    assert "in-worktree" in out


# ── Newline fidelity ──────────────────────────────────────────────────────────
# These assert on RAW BYTES. The tests above use Path.read_text(), whose
# universal-newline translation turns \r\n back into \n on the way in — which is
# exactly why a CRLF-rewriting bug survived here: on Windows every assertion
# above still passed while every file the agent touched was being rewritten.


def _write_bytes(path, data: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(data)


def test_write_file_does_not_translate_newlines(tmp_path):
    """\\n stays \\n. On Windows an untranslated write emits \\r\\n."""
    _, h = _handlers(tmp_path)
    h["write_file"]({"path": "a.txt", "content": "one\ntwo\nthree\n"}, None)
    assert (tmp_path / "a.txt").read_bytes() == b"one\ntwo\nthree\n"


def test_patch_preserves_lf_endings(tmp_path):
    _, h = _handlers(tmp_path)
    _write_bytes(tmp_path / "lf.txt", b"alpha\nbeta\ngamma\n")
    assert "Patched" in h["patch_file"](
        {"path": "lf.txt", "old_string": "beta", "new_string": "BETA"}, None
    )
    assert (tmp_path / "lf.txt").read_bytes() == b"alpha\nBETA\ngamma\n"


def test_patch_preserves_crlf_endings(tmp_path):
    """A CRLF file must stay CRLF — patching one line must not convert the file."""
    _, h = _handlers(tmp_path)
    _write_bytes(tmp_path / "crlf.txt", b"alpha\r\nbeta\r\ngamma\r\n")
    assert "Patched" in h["patch_file"](
        {"path": "crlf.txt", "old_string": "beta", "new_string": "BETA"}, None
    )
    assert (tmp_path / "crlf.txt").read_bytes() == b"alpha\r\nBETA\r\ngamma\r\n"


def test_read_file_reports_the_bytes_on_disk(tmp_path):
    """read_file must not silently normalise, or the agent cannot match on
    old_string in a CRLF file."""
    _, h = _handlers(tmp_path)
    _write_bytes(tmp_path / "crlf.txt", b"x\r\ny\r\n")
    assert h["read_file"]({"path": "crlf.txt"}, None) == "x\r\ny\r\n"


@pytest.mark.parametrize("eol", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_patch_produces_a_one_line_git_diff(tmp_path, eol):
    """The acceptance criterion: one logical edit is one changed line in git,
    on either line-ending convention and on either OS.

    Before the fix this asserted 1 insertion / 1 deletion and got 40/40 on
    Windows for the CRLF case, because the whole file was rewritten.
    """
    if not subprocess.run(
        ["git", "--version"], capture_output=True
    ).returncode == 0:  # pragma: no cover
        pytest.skip("git unavailable")

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=str(tmp_path), capture_output=True, text=True
        )

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    # core.autocrlf off so git records exactly what the tool wrote.
    git("config", "core.autocrlf", "false")

    body = eol.join(b"line%02d" % i for i in range(40)) + eol
    _write_bytes(tmp_path / "f.txt", body)
    git("add", "f.txt")
    git("commit", "-qm", "base")

    _, h = _handlers(tmp_path)
    assert "Patched" in h["patch_file"](
        {"path": "f.txt", "old_string": "line20", "new_string": "LINE20"}, None
    )

    numstat = git("diff", "--numstat", "--", "f.txt").stdout.strip()
    assert numstat, "expected a diff"
    added, removed, _ = numstat.split("\t")
    assert (added, removed) == ("1", "1"), (
        f"one edit produced {added} insertions / {removed} deletions — "
        "the file's line endings were rewritten"
    )
