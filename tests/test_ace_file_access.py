# CUI // SP-CTI
"""Tests for ACE FileAccessBroker (Phase 2 — scoped filesystem access)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from icdev.tools.ace.file_access_broker import (
    FileAccessBroker,
    ScopeViolationError,
    ReadOnlyScopeError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmproot(tmp_path: Path) -> Path:
    """Fake repo root with allowed subdirectories pre-created."""
    (tmp_path / ".tmp" / "ace").mkdir(parents=True)
    (tmp_path / "reports").mkdir()
    return tmp_path


@pytest.fixture
def broker(tmproot: Path) -> FileAccessBroker:
    return FileAccessBroker(
        folder_access=[
            {"path": ".tmp/ace/", "mode": "rw"},
            {"path": "reports/", "mode": "r"},
        ],
        repo_root=tmproot,
    )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


class TestFileRead:
    def test_read_within_rw_scope(self, broker: FileAccessBroker, tmproot: Path):
        target = tmproot / ".tmp" / "ace" / "out.txt"
        target.write_text("hello ace", encoding="utf-8")
        content = broker.read(".tmp/ace/out.txt", coworker_id="cw-1", instance_id="i-1")
        assert content == "hello ace"

    def test_read_within_r_scope(self, broker: FileAccessBroker, tmproot: Path):
        target = tmproot / "reports" / "summary.txt"
        target.write_text("quarterly report", encoding="utf-8")
        content = broker.read("reports/summary.txt", coworker_id="cw-1", instance_id="i-1")
        assert content == "quarterly report"

    def test_read_outside_scope_raises(self, broker: FileAccessBroker, tmproot: Path):
        (tmproot / "secret.txt").write_text("classified", encoding="utf-8")
        with pytest.raises(ScopeViolationError):
            broker.read("secret.txt", coworker_id="cw-1", instance_id="i-1")

    def test_read_missing_file_raises(self, broker: FileAccessBroker):
        with pytest.raises(FileNotFoundError):
            broker.read(".tmp/ace/nonexistent.txt", coworker_id="cw-1", instance_id="i-1")

    def test_path_traversal_blocked(self, broker: FileAccessBroker, tmproot: Path):
        """.. traversal must not escape the repo root."""
        (tmproot.parent / "escape.txt").write_text("escaped", encoding="utf-8")
        with pytest.raises(ScopeViolationError):
            broker.read("../../escape.txt", coworker_id="cw-1", instance_id="i-1")

    def test_traversal_within_scope_prefix_blocked(self, broker: FileAccessBroker, tmproot: Path):
        """Traversal that stays 'inside' the scope path string but exits via .. is blocked."""
        (tmproot / ".tmp" / "sensitive.txt").write_text("above ace/", encoding="utf-8")
        with pytest.raises(ScopeViolationError):
            broker.read(".tmp/ace/../../.tmp/sensitive.txt", coworker_id="cw-1", instance_id="i-1")


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


class TestFileWrite:
    def test_write_within_rw_scope(self, broker: FileAccessBroker, tmproot: Path):
        broker.write(
            ".tmp/ace/result.txt", "output data",
            coworker_id="cw-1", instance_id="i-1"
        )
        assert (tmproot / ".tmp" / "ace" / "result.txt").read_text(encoding="utf-8") == "output data"

    def test_write_creates_parent_dirs(self, broker: FileAccessBroker, tmproot: Path):
        broker.write(
            ".tmp/ace/sub/dir/file.txt", "nested",
            coworker_id="cw-1", instance_id="i-1"
        )
        assert (tmproot / ".tmp" / "ace" / "sub" / "dir" / "file.txt").exists()

    def test_write_to_r_scope_raises(self, broker: FileAccessBroker):
        with pytest.raises(ReadOnlyScopeError):
            broker.write(
                "reports/new.txt", "inject",
                coworker_id="cw-1", instance_id="i-1"
            )

    def test_write_outside_scope_raises(self, broker: FileAccessBroker):
        with pytest.raises(ScopeViolationError):
            broker.write(
                "data/icdev.db", "DROP TABLE",
                coworker_id="cw-1", instance_id="i-1"
            )

    def test_write_traversal_blocked(self, broker: FileAccessBroker):
        with pytest.raises(ScopeViolationError):
            broker.write(
                ".tmp/ace/../../../evil.txt", "pwned",
                coworker_id="cw-1", instance_id="i-1"
            )

    def test_empty_scope_blocks_all(self, tmproot: Path):
        empty_broker = FileAccessBroker(folder_access=[], repo_root=tmproot)
        with pytest.raises(ScopeViolationError):
            empty_broker.read(".tmp/ace/x.txt", coworker_id="cw-1", instance_id="i-1")
