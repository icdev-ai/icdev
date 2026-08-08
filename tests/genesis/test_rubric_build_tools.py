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
    assert names == {"read_file", "list_files", "write_file", "patch_file", "done"}
    ro = {t["function"]["name"] for t in tools if t["function"].get("is_read_only")}
    assert ro == {"read_file", "list_files"}
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
