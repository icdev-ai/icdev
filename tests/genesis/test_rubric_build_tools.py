"""Worktree-bound file-editing toolset for the rubric-gated build loop."""
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
