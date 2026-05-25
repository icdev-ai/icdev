# CUI // SP-CTI
"""Tests for tools/agent_toolkit/_fs.py primitives."""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agent_toolkit import _fs


def test_write_and_read_roundtrip(tmp_path):
    p = tmp_path / "hello.txt"
    result = _fs.write_file(p, "hello world\n")
    assert result["created"] is True
    assert result["bytes_written"] == 12
    assert _fs.read_file(p) == "hello world\n"


def test_write_file_atomic_replace(tmp_path):
    p = tmp_path / "file.txt"
    _fs.write_file(p, "v1")
    r2 = _fs.write_file(p, "v2")
    assert r2["created"] is False
    assert _fs.read_file(p) == "v2"
    # no leftover .tmp sibling
    assert not (tmp_path / "file.txt.tmp").exists()


def test_write_file_creates_parent_dirs(tmp_path):
    p = tmp_path / "nested" / "dir" / "file.txt"
    _fs.write_file(p, "x")
    assert p.exists()


def test_read_file_raises_on_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        _fs.read_file(tmp_path / "nope.txt")


def test_read_file_max_bytes_guard(tmp_path):
    p = tmp_path / "big.txt"
    p.write_text("x" * 1000)
    with pytest.raises(ValueError, match="exceeds max_bytes"):
        _fs.read_file(p, max_bytes=500)


def test_edit_file_single_replace(tmp_path):
    p = tmp_path / "f.txt"
    _fs.write_file(p, "foo bar foo")
    r = _fs.edit_file(p, "foo", "baz")
    assert r["replacements_made"] == 2
    assert _fs.read_file(p) == "baz bar baz"


def test_edit_file_expected_count_mismatch(tmp_path):
    p = tmp_path / "f.txt"
    _fs.write_file(p, "foo foo foo")
    with pytest.raises(ValueError, match="expected 1"):
        _fs.edit_file(p, "foo", "bar", expected_count=1)


def test_edit_file_no_match(tmp_path):
    p = tmp_path / "f.txt"
    _fs.write_file(p, "hello")
    r = _fs.edit_file(p, "xxx", "yyy")
    assert r["replacements_made"] == 0
    assert _fs.read_file(p) == "hello"


def test_edit_file_empty_old_raises(tmp_path):
    p = tmp_path / "f.txt"
    _fs.write_file(p, "x")
    with pytest.raises(ValueError, match="non-empty"):
        _fs.edit_file(p, "", "y")


def test_ls_non_recursive(tmp_path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("")

    out = _fs.ls(tmp_path)
    names = {e["name"] for e in out}
    assert names == {"a.py", "b.py", "sub"}


def test_ls_recursive_with_pattern(tmp_path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.txt").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("")

    out = _fs.ls(tmp_path, recursive=True, pattern="*.py")
    names = sorted(e["name"] for e in out)
    assert names == ["a.py", "c.py"]


def test_ls_not_a_directory(tmp_path):
    p = tmp_path / "file.txt"
    p.write_text("x")
    with pytest.raises(NotADirectoryError):
        _fs.ls(p)


def test_glob_returns_sorted(tmp_path):
    (tmp_path / "b.py").write_text("")
    (tmp_path / "a.py").write_text("")
    out = _fs.glob("*.py", root=tmp_path)
    assert out == sorted(out)
    assert len(out) == 2


def test_grep_regex_match(tmp_path):
    p = tmp_path / "f.py"
    p.write_text("def foo():\n    pass\ndef bar():\n    pass\n")
    hits = _fs.grep(r"^def \w+", paths=[p])
    assert len(hits) == 2
    assert hits[0]["line_number"] == 1
    assert hits[1]["line_number"] == 3


def test_grep_literal_substring(tmp_path):
    p = tmp_path / "f.py"
    p.write_text("a.b.c\nx.y.z\n")
    hits = _fs.grep("b.c", paths=[p], regex=False)
    assert len(hits) == 1
    assert "a.b.c" in hits[0]["line_text"]


def test_grep_case_insensitive(tmp_path):
    p = tmp_path / "f.py"
    p.write_text("HELLO\nhello\nHeLLo\n")
    hits = _fs.grep("hello", paths=[p], case_insensitive=True)
    assert len(hits) == 3


def test_grep_max_matches_cap(tmp_path):
    p = tmp_path / "f.py"
    p.write_text("x\n" * 100)
    hits = _fs.grep("x", paths=[p], max_matches=5)
    assert len(hits) == 5


def test_grep_recursive_directory(tmp_path):
    (tmp_path / "a.py").write_text("TODO fix me\n")
    (tmp_path / "b.py").write_text("clean\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("TODO and TODO\n")

    hits = _fs.grep("TODO", paths=[tmp_path], regex=False)
    assert len(hits) == 2  # one per file with a TODO
