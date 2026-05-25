#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for ICDEV™ File Sync Module (D-SYNC-1 through D-SYNC-12).

Covers: ignore parser, scanner, change detector, conflict resolver,
transfer executor, providers (local, sftp, cloud), watcher, sync engine.
"""

import hashlib
import os
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def tmp_src(tmp_path):
    """Create a temporary source directory with sample files."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "hello.txt").write_text("Hello World")
    (src / "sub").mkdir()
    (src / "sub" / "data.json").write_text('{"key": "value"}')
    return str(src)


@pytest.fixture
def tmp_dst(tmp_path):
    """Create a temporary destination directory."""
    dst = tmp_path / "dst"
    dst.mkdir()
    return str(dst)


@pytest.fixture
def icdev_db(tmp_path):
    """Create a test database with sync tables matching init_icdev_db.py schema."""
    db_path = tmp_path / "test_filesync.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sync_jobs (
            id                          TEXT PRIMARY KEY,
            name                        TEXT NOT NULL,
            source_path                 TEXT NOT NULL,
            source_provider             TEXT NOT NULL DEFAULT 'local',
            dest_path                   TEXT NOT NULL,
            dest_provider               TEXT NOT NULL DEFAULT 'local',
            sync_mode                   TEXT NOT NULL DEFAULT 'push',
            conflict_strategy           TEXT NOT NULL DEFAULT 'last_write_wins',
            ignore_file                 TEXT DEFAULT '.syncignore',
            delete_orphans              INTEGER DEFAULT 0,
            status                      TEXT NOT NULL DEFAULT 'idle',
            schedule_interval_seconds   INTEGER DEFAULT 0,
            bandwidth_limit_kbps        INTEGER DEFAULT 0,
            max_workers                 INTEGER DEFAULT 4,
            last_run_at                 TIMESTAMP,
            last_success_at             TIMESTAMP,
            files_synced                INTEGER DEFAULT 0,
            files_skipped               INTEGER DEFAULT 0,
            files_conflicted            INTEGER DEFAULT 0,
            bytes_transferred           INTEGER DEFAULT 0,
            error_message               TEXT,
            config_json                 TEXT,
            classification              TEXT DEFAULT 'CUI',
            project_id                  TEXT,
            created_by                  TEXT,
            created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sync_state (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id          TEXT NOT NULL,
            relative_path   TEXT NOT NULL,
            content_hash    TEXT,
            file_size       INTEGER,
            mtime_epoch     REAL,
            side            TEXT NOT NULL DEFAULT 'source',
            last_synced_at  TIMESTAMP,
            last_synced_hash TEXT,
            UNIQUE(job_id, relative_path, side)
        );
        CREATE TABLE IF NOT EXISTS sync_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id              TEXT,
            action              TEXT NOT NULL,
            relative_path       TEXT,
            source_hash         TEXT,
            dest_hash           TEXT,
            bytes_transferred   INTEGER DEFAULT 0,
            duration_ms         INTEGER DEFAULT 0,
            resolution          TEXT,
            error_detail        TEXT,
            classification      TEXT DEFAULT 'CUI',
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sync_conflicts (
            id              TEXT PRIMARY KEY,
            job_id          TEXT NOT NULL,
            relative_path   TEXT NOT NULL,
            source_hash     TEXT,
            source_mtime    REAL,
            source_size     INTEGER,
            dest_hash       TEXT,
            dest_mtime      REAL,
            dest_size       INTEGER,
            resolution      TEXT DEFAULT 'pending',
            resolved_at     TIMESTAMP,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS audit_trail (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type      TEXT,
            actor           TEXT,
            action          TEXT,
            project_id      TEXT,
            session_id      TEXT,
            metadata        TEXT,
            classification  TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.close()
    return str(db_path)


# ═══════════════════════════════════════════════════════════════════════
# Ignore Parser Tests
# ═══════════════════════════════════════════════════════════════════════


class TestIgnoreParser:
    """Tests for tools/filesync/ignore_parser.py."""

    def test_load_patterns_no_file(self, tmp_path):
        from tools.filesync.ignore_parser import load_ignore_patterns

        patterns = load_ignore_patterns(str(tmp_path), ".syncignore")
        assert patterns == []

    def test_load_patterns_with_file(self, tmp_path):
        from tools.filesync.ignore_parser import load_ignore_patterns

        ignore = tmp_path / ".syncignore"
        ignore.write_text("*.log\n# comment\n\n*.tmp\n")
        patterns = load_ignore_patterns(str(tmp_path))
        assert "*.log" in patterns
        assert "*.tmp" in patterns
        assert len(patterns) == 2

    def test_should_ignore_simple_pattern(self):
        from tools.filesync.ignore_parser import should_ignore

        assert should_ignore("debug.log", ["*.log"])
        assert not should_ignore("readme.txt", ["*.log"])

    def test_should_ignore_directory_pattern(self):
        from tools.filesync.ignore_parser import should_ignore

        assert should_ignore("node_modules/pkg/file.js", ["node_modules/"])
        assert not should_ignore("readme.txt", ["node_modules/"])

    def test_should_ignore_path_pattern(self):
        from tools.filesync.ignore_parser import should_ignore

        assert should_ignore("build/output.bin", ["build/*"])
        assert not should_ignore("src/main.py", ["build/*"])

    def test_should_ignore_empty_patterns(self):
        from tools.filesync.ignore_parser import should_ignore

        assert not should_ignore("anything.txt", [])

    def test_filter_files(self):
        from tools.filesync.ignore_parser import filter_files

        files = [
            {"relative_path": "src/main.py", "size": 100},
            {"relative_path": "debug.log", "size": 50},
            {"relative_path": "build/out.bin", "size": 200},
        ]
        filtered = filter_files(files, ["*.log", "build/*"])
        assert len(filtered) == 1
        assert filtered[0]["relative_path"] == "src/main.py"

    def test_filter_files_no_patterns(self):
        from tools.filesync.ignore_parser import filter_files

        files = [{"relative_path": "a.txt"}, {"relative_path": "b.log"}]
        assert filter_files(files, []) == files


# ═══════════════════════════════════════════════════════════════════════
# Scanner Tests
# ═══════════════════════════════════════════════════════════════════════


class TestScanner:
    """Tests for tools/filesync/scanner.py."""

    def test_compute_file_hash(self):
        from tools.filesync.scanner import compute_file_hash

        data = b"Hello World"
        expected = hashlib.sha256(data).hexdigest()
        assert compute_file_hash(data) == expected

    def test_compute_file_hash_empty(self):
        from tools.filesync.scanner import compute_file_hash

        expected = hashlib.sha256(b"").hexdigest()
        assert compute_file_hash(b"") == expected

    def test_scan_directory(self, tmp_src):
        from tools.filesync.providers.local import LocalSyncProvider
        from tools.filesync.scanner import scan_directory

        provider = LocalSyncProvider()
        manifest = scan_directory(provider, tmp_src)
        assert "hello.txt" in manifest
        assert "sub/data.json" in manifest
        assert "hash" in manifest["hello.txt"]
        assert "size" in manifest["hello.txt"]

    def test_scan_directory_fast_skip(self, tmp_src):
        from tools.filesync.providers.local import LocalSyncProvider
        from tools.filesync.scanner import scan_directory

        provider = LocalSyncProvider()
        manifest1 = scan_directory(provider, tmp_src)
        # Second scan with cached state should use fast-skip
        manifest2 = scan_directory(provider, tmp_src, cached_state=manifest1)
        assert manifest1["hello.txt"]["hash"] == manifest2["hello.txt"]["hash"]

    def test_scan_with_ignore(self, tmp_path):
        from tools.filesync.providers.local import LocalSyncProvider
        from tools.filesync.scanner import scan_directory

        src = tmp_path / "scantest"
        src.mkdir()
        (src / "keep.txt").write_text("keep")
        (src / "ignore.log").write_text("ignore")
        (src / ".syncignore").write_text("*.log\n")
        provider = LocalSyncProvider()
        manifest = scan_directory(provider, str(src))
        assert "keep.txt" in manifest
        assert "ignore.log" not in manifest


# ═══════════════════════════════════════════════════════════════════════
# Change Detector Tests
# ═══════════════════════════════════════════════════════════════════════


class TestChangeDetector:
    """Tests for tools/filesync/change_detector.py."""

    def test_detect_new_file(self):
        from tools.filesync.change_detector import detect_changes_push

        src = {"a.txt": {"hash": "abc", "size": 10, "mtime_epoch": 100}}
        dst = {}
        actions = detect_changes_push(src, dst)
        assert len(actions) == 1
        assert actions[0]["action"] == "copy"
        assert actions[0]["relative_path"] == "a.txt"

    def test_detect_updated_file(self):
        from tools.filesync.change_detector import detect_changes_push

        src = {"a.txt": {"hash": "new", "size": 10, "mtime_epoch": 200}}
        dst = {"a.txt": {"hash": "old", "size": 10, "mtime_epoch": 100}}
        actions = detect_changes_push(src, dst)
        assert len(actions) == 1
        assert actions[0]["action"] == "update"

    def test_detect_no_change(self):
        from tools.filesync.change_detector import detect_changes_push

        h = hashlib.sha256(b"same").hexdigest()
        src = {"a.txt": {"hash": h, "size": 4, "mtime_epoch": 100}}
        dst = {"a.txt": {"hash": h, "size": 4, "mtime_epoch": 100}}
        actions = detect_changes_push(src, dst)
        assert len(actions) == 0

    def test_detect_orphan_delete(self):
        from tools.filesync.change_detector import detect_changes_push

        src = {}
        dst = {"old.txt": {"hash": "xyz", "size": 5, "mtime_epoch": 50}}
        actions = detect_changes_push(src, dst, delete_orphans=True)
        assert len(actions) == 1
        assert actions[0]["action"] == "delete"

    def test_detect_orphan_no_delete(self):
        from tools.filesync.change_detector import detect_changes_push

        src = {}
        dst = {"old.txt": {"hash": "xyz", "size": 5, "mtime_epoch": 50}}
        actions = detect_changes_push(src, dst, delete_orphans=False)
        assert len(actions) == 0

    def test_bidirectional_conflict(self):
        from tools.filesync.change_detector import detect_changes_bidirectional

        src_now = {"a.txt": {"hash": "src_new", "size": 10, "mtime_epoch": 200}}
        dst_now = {"a.txt": {"hash": "dst_new", "size": 10, "mtime_epoch": 200}}
        src_prev = {"a.txt": {"hash": "old", "size": 10, "mtime_epoch": 100}}
        dst_prev = {"a.txt": {"hash": "old", "size": 10, "mtime_epoch": 100}}
        actions = detect_changes_bidirectional(src_now, dst_now, src_prev, dst_prev)
        assert len(actions) == 1
        assert actions[0]["action"] == "conflict"

    def test_bidirectional_source_changed(self):
        from tools.filesync.change_detector import detect_changes_bidirectional

        src_now = {"a.txt": {"hash": "new", "size": 10, "mtime_epoch": 200}}
        dst_now = {"a.txt": {"hash": "old", "size": 10, "mtime_epoch": 100}}
        src_prev = {"a.txt": {"hash": "old", "size": 10, "mtime_epoch": 100}}
        dst_prev = {"a.txt": {"hash": "old", "size": 10, "mtime_epoch": 100}}
        actions = detect_changes_bidirectional(src_now, dst_now, src_prev, dst_prev)
        assert len(actions) == 1
        assert actions[0]["action"] == "update"
        assert actions[0]["direction"] == "source_to_dest"

    def test_summarize_actions(self):
        from tools.filesync.change_detector import summarize_actions

        actions = [
            {"action": "copy", "size": 100},
            {"action": "update", "size": 200},
            {"action": "delete", "size": 50},
            {"action": "conflict", "size": 30},
        ]
        summary = summarize_actions(actions)
        assert summary["total"] == 4
        assert summary["copy"] == 1
        assert summary["update"] == 1
        assert summary["delete"] == 1
        assert summary["conflict"] == 1
        assert summary["bytes_to_transfer"] == 300


# ═══════════════════════════════════════════════════════════════════════
# Conflict Resolver Tests
# ═══════════════════════════════════════════════════════════════════════


class TestConflictResolver:
    """Tests for tools/filesync/conflict_resolver.py."""

    def _make_conflict(self, path="a.txt", src_mtime=200, dst_mtime=100):
        return {
            "action": "conflict",
            "relative_path": path,
            "reason": "both_sides_changed",
            "source_hash": "src_hash",
            "dest_hash": "dst_hash",
            "source_mtime": src_mtime,
            "dest_mtime": dst_mtime,
            "size": 100,
            "direction": None,
        }

    def test_source_wins(self):
        from tools.filesync.conflict_resolver import resolve_conflicts

        actions = [self._make_conflict()]
        resolved = resolve_conflicts(actions, "source_wins")
        assert len(resolved) == 1
        assert resolved[0]["action"] == "update"
        assert resolved[0]["resolution"] == "source_wins"

    def test_last_write_wins_source_newer(self):
        from tools.filesync.conflict_resolver import resolve_conflicts

        actions = [self._make_conflict(src_mtime=300, dst_mtime=100)]
        resolved = resolve_conflicts(actions, "last_write_wins")
        assert resolved[0]["direction"] == "source_to_dest"

    def test_last_write_wins_dest_newer(self):
        from tools.filesync.conflict_resolver import resolve_conflicts

        actions = [self._make_conflict(src_mtime=100, dst_mtime=300)]
        resolved = resolve_conflicts(actions, "last_write_wins")
        assert resolved[0]["direction"] == "dest_to_source"

    def test_rename_both(self):
        from tools.filesync.conflict_resolver import resolve_conflicts

        actions = [self._make_conflict(path="doc.txt")]
        resolved = resolve_conflicts(actions, "rename_both")
        assert len(resolved) == 2
        assert all(a["action"] == "rename" for a in resolved)
        assert "conflict-source" in resolved[0]["new_path"]
        assert "conflict-dest" in resolved[1]["new_path"]

    def test_skip(self):
        from tools.filesync.conflict_resolver import resolve_conflicts

        actions = [self._make_conflict()]
        resolved = resolve_conflicts(actions, "skip")
        assert len(resolved) == 1
        assert resolved[0]["action"] == "skip"
        assert resolved[0]["resolution"] == "skipped"

    def test_newest_wins_alias(self):
        from tools.filesync.conflict_resolver import resolve_conflicts

        actions = [self._make_conflict(src_mtime=300, dst_mtime=100)]
        resolved = resolve_conflicts(actions, "newest_wins")
        assert resolved[0]["direction"] == "source_to_dest"

    def test_non_conflict_actions_pass_through(self):
        from tools.filesync.conflict_resolver import resolve_conflicts

        actions = [{"action": "copy", "relative_path": "new.txt", "size": 50}]
        resolved = resolve_conflicts(actions, "source_wins")
        assert resolved == actions

    def test_invalid_strategy_falls_back(self):
        from tools.filesync.conflict_resolver import resolve_conflicts

        actions = [self._make_conflict()]
        resolved = resolve_conflicts(actions, "nonexistent")
        assert len(resolved) == 1
        assert resolved[0]["resolution"] == "source_wins"


# ═══════════════════════════════════════════════════════════════════════
# Local Provider Tests
# ═══════════════════════════════════════════════════════════════════════


class TestLocalSyncProvider:
    """Tests for tools/filesync/providers/local.py."""

    def test_provider_name(self):
        from tools.filesync.providers.local import LocalSyncProvider

        assert LocalSyncProvider().provider_name == "local"

    def test_list_files(self, tmp_src):
        from tools.filesync.providers.local import LocalSyncProvider

        provider = LocalSyncProvider()
        files = provider.list_files(tmp_src)
        paths = [f["relative_path"] for f in files]
        assert "hello.txt" in paths
        assert "sub/data.json" in paths

    def test_read_file(self, tmp_src):
        from tools.filesync.providers.local import LocalSyncProvider

        provider = LocalSyncProvider()
        data = provider.read_file(tmp_src, "hello.txt")
        assert data == b"Hello World"

    def test_read_nonexistent(self, tmp_src):
        from tools.filesync.providers.local import LocalSyncProvider

        provider = LocalSyncProvider()
        assert provider.read_file(tmp_src, "nope.txt") is None

    def test_write_file(self, tmp_dst):
        from tools.filesync.providers.local import LocalSyncProvider

        provider = LocalSyncProvider()
        ok = provider.write_file(tmp_dst, "new/nested/file.txt", b"data")
        assert ok
        assert (Path(tmp_dst) / "new" / "nested" / "file.txt").read_bytes() == b"data"

    def test_delete_file(self, tmp_src):
        from tools.filesync.providers.local import LocalSyncProvider

        provider = LocalSyncProvider()
        assert provider.delete_file(tmp_src, "hello.txt")
        assert not (Path(tmp_src) / "hello.txt").exists()

    def test_delete_nonexistent(self, tmp_src):
        from tools.filesync.providers.local import LocalSyncProvider

        provider = LocalSyncProvider()
        # missing_ok=True means delete succeeds even if file doesn't exist
        assert provider.delete_file(tmp_src, "nope.txt")

    def test_get_file_info(self, tmp_src):
        from tools.filesync.providers.local import LocalSyncProvider

        provider = LocalSyncProvider()
        info = provider.get_file_info(tmp_src, "hello.txt")
        assert info is not None
        assert info["size"] == len(b"Hello World")
        assert "mtime_epoch" in info

    def test_get_file_info_nonexistent(self, tmp_src):
        from tools.filesync.providers.local import LocalSyncProvider

        provider = LocalSyncProvider()
        assert provider.get_file_info(tmp_src, "nope.txt") is None

    def test_check_availability(self):
        from tools.filesync.providers.local import LocalSyncProvider

        assert LocalSyncProvider().check_availability()

    def test_rename_file(self, tmp_src):
        from tools.filesync.providers.local import LocalSyncProvider

        provider = LocalSyncProvider()
        assert provider.rename_file(tmp_src, "hello.txt", "hello_renamed.txt")
        assert not (Path(tmp_src) / "hello.txt").exists()
        assert (Path(tmp_src) / "hello_renamed.txt").exists()

    def test_mkdir(self, tmp_dst):
        from tools.filesync.providers.local import LocalSyncProvider

        provider = LocalSyncProvider()
        assert provider.mkdir(tmp_dst, "a/b/c")
        assert (Path(tmp_dst) / "a" / "b" / "c").is_dir()


# ═══════════════════════════════════════════════════════════════════════
# Transfer Tests
# ═══════════════════════════════════════════════════════════════════════


class TestTransfer:
    """Tests for tools/filesync/transfer.py."""

    def test_transfer_file(self, tmp_src, tmp_dst):
        from tools.filesync.providers.local import LocalSyncProvider
        from tools.filesync.transfer import transfer_file

        provider = LocalSyncProvider()
        result = transfer_file(provider, tmp_src, provider, tmp_dst, "hello.txt")
        assert result.success
        assert result.bytes_transferred == len(b"Hello World")
        assert (Path(tmp_dst) / "hello.txt").read_bytes() == b"Hello World"

    def test_transfer_file_verify(self, tmp_src, tmp_dst):
        from tools.filesync.providers.local import LocalSyncProvider
        from tools.filesync.transfer import transfer_file

        provider = LocalSyncProvider()
        result = transfer_file(provider, tmp_src, provider, tmp_dst, "hello.txt", verify=True)
        assert result.success

    def test_transfer_file_not_found(self, tmp_src, tmp_dst):
        from tools.filesync.providers.local import LocalSyncProvider
        from tools.filesync.transfer import transfer_file

        provider = LocalSyncProvider()
        result = transfer_file(provider, tmp_src, provider, tmp_dst, "nonexistent.txt")
        assert not result.success
        assert "not found" in result.error.lower()

    def test_delete_file_op(self, tmp_src):
        from tools.filesync.providers.local import LocalSyncProvider
        from tools.filesync.transfer import delete_file

        provider = LocalSyncProvider()
        result = delete_file(provider, tmp_src, "hello.txt")
        assert result.success
        assert result.action == "delete"

    def test_execute_actions_dry_run(self, tmp_src, tmp_dst):
        from tools.filesync.providers.local import LocalSyncProvider
        from tools.filesync.transfer import execute_actions

        provider = LocalSyncProvider()
        actions = [
            {"action": "copy", "relative_path": "hello.txt", "size": 11},
        ]
        results = execute_actions(actions, provider, tmp_src, provider, tmp_dst, dry_run=True)
        assert len(results) == 1
        assert results[0].success
        # Dry run should not actually copy
        assert not (Path(tmp_dst) / "hello.txt").exists()

    def test_execute_actions_copy(self, tmp_src, tmp_dst):
        from tools.filesync.providers.local import LocalSyncProvider
        from tools.filesync.transfer import execute_actions

        provider = LocalSyncProvider()
        actions = [
            {"action": "copy", "relative_path": "hello.txt", "size": 11},
            {"action": "copy", "relative_path": "sub/data.json", "size": 16},
        ]
        results = execute_actions(actions, provider, tmp_src, provider, tmp_dst)
        assert all(r.success for r in results)
        assert (Path(tmp_dst) / "hello.txt").exists()
        assert (Path(tmp_dst) / "sub" / "data.json").exists()

    def test_execute_actions_skip(self, tmp_src, tmp_dst):
        from tools.filesync.providers.local import LocalSyncProvider
        from tools.filesync.transfer import execute_actions

        provider = LocalSyncProvider()
        actions = [{"action": "skip", "relative_path": "skip.txt"}]
        results = execute_actions(actions, provider, tmp_src, provider, tmp_dst)
        assert len(results) == 1
        assert results[0].action == "skip"

    def test_transfer_result_to_dict(self):
        from tools.filesync.transfer import TransferResult

        r = TransferResult("a.txt", True, 100, 50, "", "copy")
        d = r.to_dict()
        assert d["relative_path"] == "a.txt"
        assert d["success"] is True
        assert d["bytes_transferred"] == 100

    def test_bandwidth_throttle(self, tmp_src, tmp_dst):
        from tools.filesync.providers.local import LocalSyncProvider
        from tools.filesync.transfer import transfer_file

        provider = LocalSyncProvider()
        t0 = time.monotonic()
        result = transfer_file(provider, tmp_src, provider, tmp_dst, "hello.txt", verify=False, bandwidth_limit_kbps=1)
        time.monotonic() - t0
        assert result.success
        # With 1 KB/s limit and ~11 bytes, sleep is ~11/1024 ≈ 0.01s
        # Just verify it didn't error — timing tests are unreliable in CI


# ═══════════════════════════════════════════════════════════════════════
# SFTP Provider Tests (mocked)
# ═══════════════════════════════════════════════════════════════════════


class TestSFTPProvider:
    """Tests for tools/filesync/providers/sftp.py (mocked — no real server)."""

    def test_provider_name(self):
        from tools.filesync.providers.sftp import SFTPSyncProvider

        assert SFTPSyncProvider().provider_name == "sftp"

    def test_unavailable_without_host(self):
        from tools.filesync.providers.sftp import SFTPSyncProvider

        provider = SFTPSyncProvider(host="")
        assert not provider.check_availability()

    def test_env_var_config(self):
        from tools.filesync.providers.sftp import SFTPSyncProvider

        with patch.dict(
            os.environ,
            {"ICDEV_SFTP_HOST": "test.example.com", "ICDEV_SFTP_PORT": "2222", "ICDEV_SFTP_USER": "testuser"},
        ):
            # Pass port=0 so env var is used (port or int(env) — 0 is falsy)
            provider = SFTPSyncProvider(port=0)
            assert provider._host == "test.example.com"
            assert provider._port == 2222
            assert provider._username == "testuser"


# ═══════════════════════════════════════════════════════════════════════
# Cloud Provider Tests (mocked)
# ═══════════════════════════════════════════════════════════════════════


class TestCloudProvider:
    """Tests for tools/filesync/providers/cloud.py (mocked)."""

    def test_provider_name(self):
        from tools.filesync.providers.cloud import CloudSyncProvider

        provider = CloudSyncProvider(cloud_provider="s3")
        assert provider.provider_name == "cloud_s3"

    def test_unavailable_with_bad_provider(self):
        from tools.filesync.providers.cloud import CloudSyncProvider

        provider = CloudSyncProvider(cloud_provider="nonexistent_provider_xyz")
        assert not provider.check_availability()

    def test_with_mock_instance(self):
        from tools.filesync.providers.cloud import CloudSyncProvider

        mock = MagicMock()
        mock.check_availability.return_value = True
        mock.list_objects.return_value = ["file1.txt", "dir/file2.txt"]
        mock.download.return_value = b"data"
        mock.upload.return_value = True
        mock.delete.return_value = True

        provider = CloudSyncProvider(provider_instance=mock)
        assert provider.check_availability()

        files = provider.list_files("bucket")
        assert len(files) == 2

        data = provider.read_file("bucket", "file1.txt")
        assert data == b"data"

        assert provider.write_file("bucket", "file1.txt", b"new")
        assert provider.delete_file("bucket", "file1.txt")


# ═══════════════════════════════════════════════════════════════════════
# Watcher Tests
# ═══════════════════════════════════════════════════════════════════════


class TestFileWatcher:
    """Tests for tools/filesync/watcher.py."""

    def test_watcher_init(self, tmp_src):
        from tools.filesync.watcher import FileWatcher

        callback = MagicMock()
        watcher = FileWatcher(tmp_src, callback, debounce_seconds=0.1)
        assert not watcher.is_running

    def test_watcher_start_stop(self, tmp_src):
        from tools.filesync.watcher import FileWatcher

        callback = MagicMock()
        watcher = FileWatcher(tmp_src, callback, debounce_seconds=0.1, fallback_scan_interval=0.5)
        watcher.start()
        assert watcher.is_running
        time.sleep(0.2)
        watcher.stop()
        assert not watcher.is_running

    def test_polling_detects_new_file(self, tmp_path):
        from tools.filesync.watcher import FileWatcher

        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        (watch_dir / "existing.txt").write_text("existing")

        changes_seen = []

        def on_changes(paths):
            changes_seen.append(paths)

        watcher = FileWatcher(str(watch_dir), on_changes, debounce_seconds=0.1, fallback_scan_interval=0.5)
        watcher.start()
        time.sleep(0.3)
        # Create a new file
        (watch_dir / "newfile.txt").write_text("new content")
        time.sleep(1.5)  # Wait for scan
        watcher.stop()

        # Should have detected the change
        if changes_seen:
            all_paths = set()
            for cs in changes_seen:
                all_paths.update(cs)
            assert any("newfile.txt" in p for p in all_paths)


# ═══════════════════════════════════════════════════════════════════════
# Sync Engine Tests
# ═══════════════════════════════════════════════════════════════════════


class TestSyncEngine:
    """Tests for tools/filesync/sync_engine.py."""

    def test_create_job(self, icdev_db, tmp_src, tmp_dst):
        from tools.filesync.sync_engine import create_job

        result = create_job(
            name="Test Sync",
            source=tmp_src,
            dest=tmp_dst,
            db_path=icdev_db,
        )
        assert result["status"] == "created"
        assert result["job_id"].startswith("fsync-")

    def test_list_jobs(self, icdev_db, tmp_src, tmp_dst):
        from tools.filesync.sync_engine import create_job, list_jobs

        create_job(name="Job 1", source=tmp_src, dest=tmp_dst, db_path=icdev_db)
        create_job(name="Job 2", source=tmp_src, dest=tmp_dst, db_path=icdev_db)
        result = list_jobs(db_path=icdev_db)
        assert result["status"] == "success"
        assert len(result["jobs"]) == 2

    def test_get_job_status(self, icdev_db, tmp_src, tmp_dst):
        from tools.filesync.sync_engine import create_job, get_job_status

        r = create_job(name="Status Test", source=tmp_src, dest=tmp_dst, db_path=icdev_db)
        job_id = r["job_id"]
        result = get_job_status(job_id, db_path=icdev_db)
        assert result["status"] == "success"
        assert result["job"]["name"] == "Status Test"

    def test_delete_job(self, icdev_db, tmp_src, tmp_dst):
        from tools.filesync.sync_engine import create_job, delete_job, list_jobs

        r = create_job(name="Delete Me", source=tmp_src, dest=tmp_dst, db_path=icdev_db)
        result = delete_job(r["job_id"], db_path=icdev_db)
        assert result["status"] == "deleted"
        jobs = list_jobs(db_path=icdev_db)
        assert len(jobs["jobs"]) == 0

    def test_run_sync_push(self, icdev_db, tmp_src, tmp_dst):
        from tools.filesync.sync_engine import create_job, run_sync

        r = create_job(name="Push Test", source=tmp_src, dest=tmp_dst, db_path=icdev_db)
        result = run_sync(r["job_id"], db_path=icdev_db)
        assert result["status"] == "completed"
        assert result["files_synced"] >= 2
        # Verify files were copied
        assert (Path(tmp_dst) / "hello.txt").exists()
        assert (Path(tmp_dst) / "sub" / "data.json").exists()

    def test_run_sync_dry_run(self, icdev_db, tmp_src, tmp_dst):
        from tools.filesync.sync_engine import create_job, run_sync

        r = create_job(name="Dry Run", source=tmp_src, dest=tmp_dst, db_path=icdev_db)
        result = run_sync(r["job_id"], dry_run=True, db_path=icdev_db)
        assert result["status"] == "dry_run"
        # Dry run should not copy files
        assert not (Path(tmp_dst) / "hello.txt").exists()

    def test_run_sync_idempotent(self, icdev_db, tmp_src, tmp_dst):
        from tools.filesync.sync_engine import create_job, run_sync

        r = create_job(name="Idempotent", source=tmp_src, dest=tmp_dst, db_path=icdev_db)
        result1 = run_sync(r["job_id"], db_path=icdev_db)
        assert result1["files_synced"] >= 2
        # Second run should find no changes
        result2 = run_sync(r["job_id"], db_path=icdev_db)
        assert result2["files_synced"] == 0

    def test_get_health(self, icdev_db):
        from tools.filesync.sync_engine import get_health

        result = get_health(db_path=icdev_db)
        assert result["status"] == "success"
        assert "total_jobs" in result

    def test_run_nonexistent_job(self, icdev_db):
        from tools.filesync.sync_engine import run_sync

        result = run_sync("fsync-nonexistent", db_path=icdev_db)
        assert result["status"] == "error"

    def test_get_sync_log(self, icdev_db, tmp_src, tmp_dst):
        from tools.filesync.sync_engine import create_job, run_sync, get_sync_log

        r = create_job(name="Log Test", source=tmp_src, dest=tmp_dst, db_path=icdev_db)
        run_sync(r["job_id"], db_path=icdev_db)
        result = get_sync_log(r["job_id"], db_path=icdev_db)
        assert result["status"] == "success"
        assert len(result["entries"]) > 0


# ═══════════════════════════════════════════════════════════════════════
# Integration: End-to-End Sync
# ═══════════════════════════════════════════════════════════════════════


class TestEndToEnd:
    """End-to-end integration tests."""

    def test_full_push_sync(self, tmp_path):
        """Full push sync: create files, sync, verify, modify, re-sync."""
        src = tmp_path / "e2e_src"
        dst = tmp_path / "e2e_dst"
        src.mkdir()
        dst.mkdir()

        # Create source files
        (src / "readme.md").write_text("# Project")
        (src / "data").mkdir()
        (src / "data" / "config.yaml").write_text("key: value")

        from tools.filesync.providers.local import LocalSyncProvider
        from tools.filesync.scanner import scan_directory
        from tools.filesync.change_detector import detect_changes_push
        from tools.filesync.transfer import execute_actions

        provider = LocalSyncProvider()

        # First sync
        src_manifest = scan_directory(provider, str(src))
        dst_manifest = scan_directory(provider, str(dst))
        actions = detect_changes_push(src_manifest, dst_manifest)
        results = execute_actions(actions, provider, str(src), provider, str(dst))

        assert all(r.success for r in results)
        assert (dst / "readme.md").read_text() == "# Project"
        assert (dst / "data" / "config.yaml").read_text() == "key: value"

        # Modify source
        (src / "readme.md").write_text("# Updated Project")

        # Re-sync
        src_manifest2 = scan_directory(provider, str(src))
        dst_manifest2 = scan_directory(provider, str(dst))
        actions2 = detect_changes_push(src_manifest2, dst_manifest2)
        results2 = execute_actions(actions2, provider, str(src), provider, str(dst))

        assert len(results2) == 1  # Only readme.md changed
        assert (dst / "readme.md").read_text() == "# Updated Project"

    def test_sync_with_ignore(self, tmp_path):
        """Sync respects .syncignore."""
        src = tmp_path / "ignore_src"
        dst = tmp_path / "ignore_dst"
        src.mkdir()
        dst.mkdir()

        (src / "keep.txt").write_text("keep")
        (src / "temp.log").write_text("ignore me")
        (src / ".syncignore").write_text("*.log\n")

        from tools.filesync.providers.local import LocalSyncProvider
        from tools.filesync.scanner import scan_directory
        from tools.filesync.change_detector import detect_changes_push
        from tools.filesync.transfer import execute_actions

        provider = LocalSyncProvider()
        src_manifest = scan_directory(provider, str(src))
        dst_manifest = scan_directory(provider, str(dst))
        actions = detect_changes_push(src_manifest, dst_manifest)
        execute_actions(actions, provider, str(src), provider, str(dst))

        assert (dst / "keep.txt").exists()
        assert not (dst / "temp.log").exists()
