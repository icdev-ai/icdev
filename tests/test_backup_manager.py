# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.db.backup_manager.BackupManager."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tools.db.backup_manager import BackupManager, _compute_sha256


def _create_test_db(db_path: Path) -> Path:
    """Create a small SQLite database for testing."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE test_data (id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO test_data VALUES (1, 'hello')")
    conn.execute("INSERT INTO test_data VALUES (2, 'world')")
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def test_db(tmp_path):
    """Create a test SQLite database."""
    db = tmp_path / "test.db"
    return _create_test_db(db)


@pytest.fixture
def backup_dir(tmp_path):
    """Create a backup output directory."""
    d = tmp_path / "backups"
    d.mkdir()
    return d


@pytest.fixture
def manager(tmp_path, backup_dir):
    """Create a BackupManager with test configuration."""
    config = {
        "backup": {
            "enabled": True,
            "backup_dir": str(backup_dir),
            "retention_days": 30,
            "max_backups": 50,
            "compression": False,
            "encryption": {"enabled": False},
            "databases": {
                "test_main": {"path": str(tmp_path / "main.db"), "schedule": "daily"},
                "test_secondary": {"path": str(tmp_path / "secondary.db"), "schedule": "weekly"},
            },
        }
    }
    return BackupManager(config=config)


class TestBackupSqlite:
    """Tests for SQLite backup creation."""

    def test_backup_sqlite_creates_backup_file(self, test_db, backup_dir):
        """backup_sqlite should create a .db.bak file in the output directory."""
        mgr = BackupManager(config={"backup": {"backup_dir": str(backup_dir), "databases": {}}})
        result = mgr.backup_sqlite(test_db, backup_dir)

        backup_path = Path(result["backup_path"])
        assert backup_path.exists()
        assert backup_path.name.endswith(".db.bak")
        assert result["db_name"] == "test"
        assert result["engine"] == "sqlite"

    def test_backup_sqlite_creates_sha256_sidecar(self, test_db, backup_dir):
        """backup_sqlite should create a .sha256 sidecar alongside the backup."""
        mgr = BackupManager(config={"backup": {"backup_dir": str(backup_dir), "databases": {}}})
        result = mgr.backup_sqlite(test_db, backup_dir)

        backup_path = Path(result["backup_path"])
        sha_sidecar = backup_path.parent / (backup_path.name + ".sha256")
        assert sha_sidecar.exists()

        content = sha_sidecar.read_text(encoding="utf-8").strip()
        assert result["checksum_sha256"] in content

    def test_backup_sqlite_creates_meta_json_sidecar(self, test_db, backup_dir):
        """backup_sqlite should create a .meta.json sidecar with metadata."""
        mgr = BackupManager(config={"backup": {"backup_dir": str(backup_dir), "databases": {}}})
        result = mgr.backup_sqlite(test_db, backup_dir)

        backup_path = Path(result["backup_path"])
        meta_path = backup_path.parent / (backup_path.name + ".meta.json")
        assert meta_path.exists()

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["db_name"] == "test"
        assert meta["engine"] == "sqlite"
        assert meta["checksum_sha256"] == result["checksum_sha256"]
        assert meta["size_bytes"] > 0


class TestVerify:
    """Tests for backup verification."""

    def test_verify_passes_for_valid_backup(self, test_db, backup_dir):
        """verify should pass for an unmodified backup with matching checksum."""
        mgr = BackupManager(config={"backup": {"backup_dir": str(backup_dir), "databases": {}}})
        result = mgr.backup_sqlite(test_db, backup_dir)
        backup_path = Path(result["backup_path"])

        verification = mgr.verify(backup_path)
        assert verification["valid"] is True
        assert verification["checksum_valid"] is True
        assert verification["integrity_valid"] is True
        assert len(verification["errors"]) == 0

    def test_verify_fails_for_corrupted_checksum(self, test_db, backup_dir):
        """verify should fail when the .sha256 sidecar does not match."""
        mgr = BackupManager(config={"backup": {"backup_dir": str(backup_dir), "databases": {}}})
        result = mgr.backup_sqlite(test_db, backup_dir)
        backup_path = Path(result["backup_path"])

        # Corrupt the sidecar checksum
        sha_sidecar = backup_path.parent / (backup_path.name + ".sha256")
        sha_sidecar.write_text("0000000000000000corrupted  fake.db.bak\n", encoding="utf-8")

        verification = mgr.verify(backup_path)
        assert verification["checksum_valid"] is False
        assert verification["valid"] is False
        assert any("checksum mismatch" in e for e in verification["errors"])


class TestRestoreSqlite:
    """Tests for SQLite restore."""

    def test_restore_sqlite_copies_backup_to_target(self, test_db, backup_dir, tmp_path):
        """restore_sqlite should copy the backup file to the target path."""
        mgr = BackupManager(config={"backup": {"backup_dir": str(backup_dir), "databases": {}}})
        result = mgr.backup_sqlite(test_db, backup_dir)
        backup_path = Path(result["backup_path"])

        restore_target = tmp_path / "restored.db"
        restore_result = mgr.restore_sqlite(backup_path, restore_target)

        assert restore_target.exists()
        assert restore_result["integrity_ok"] is True
        assert restore_result["engine"] == "sqlite"

    def test_restore_sqlite_runs_integrity_check(self, test_db, backup_dir, tmp_path):
        """restore_sqlite should verify integrity after restore."""
        mgr = BackupManager(config={"backup": {"backup_dir": str(backup_dir), "databases": {}}})
        result = mgr.backup_sqlite(test_db, backup_dir)
        backup_path = Path(result["backup_path"])

        restore_target = tmp_path / "restored_check.db"
        restore_result = mgr.restore_sqlite(backup_path, restore_target)

        assert restore_result["integrity_ok"] is True

        # Verify the restored DB actually has data
        conn = sqlite3.connect(str(restore_target))
        rows = conn.execute("SELECT COUNT(*) FROM test_data").fetchone()
        conn.close()
        assert rows[0] == 2


class TestListBackups:
    """Tests for listing backups."""

    def test_list_backups_returns_empty_for_empty_dir(self, backup_dir):
        """list_backups should return an empty list when no backups exist."""
        mgr = BackupManager(config={"backup": {"backup_dir": str(backup_dir), "databases": {}}})
        result = mgr.list_backups(backup_dir)
        assert result == []

    def test_list_backups_finds_backup_metadata_files(self, test_db, backup_dir, tmp_path):
        """list_backups should find .meta.json files and return their contents."""
        mgr = BackupManager(config={"backup": {"backup_dir": str(backup_dir), "databases": {}}})
        # Create two separate source databases so filenames differ even at same timestamp
        db2 = tmp_path / "other.db"
        _create_test_db(db2)
        mgr.backup_sqlite(test_db, backup_dir)
        mgr.backup_sqlite(db2, backup_dir)

        results = mgr.list_backups(backup_dir)
        assert len(results) >= 2
        for record in results:
            assert "db_name" in record
            assert "checksum_sha256" in record


class TestPruneOldBackups:
    """Tests for pruning old backups."""

    def test_prune_old_backups_removes_old_files(self, test_db, backup_dir):
        """prune_old_backups should remove backups older than retention_days."""
        mgr = BackupManager(config={"backup": {"backup_dir": str(backup_dir), "databases": {}}})
        result = mgr.backup_sqlite(test_db, backup_dir)
        backup_path = Path(result["backup_path"])

        # Manually modify the meta.json to have an old created_at date
        meta_path = backup_path.parent / (backup_path.name + ".meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        meta["created_at"] = old_date
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        prune_result = mgr.prune_old_backups(backup_dir, retention_days=30)
        assert prune_result["pruned"] >= 1
        assert not backup_path.exists()


class TestBackupAll:
    """Tests for bulk backup operations."""

    def test_backup_all_backs_up_configured_databases(self, manager, tmp_path):
        """backup_all should attempt to back up all configured databases."""
        # Create the configured databases
        _create_test_db(tmp_path / "main.db")
        _create_test_db(tmp_path / "secondary.db")

        results = manager.backup_all()
        # Both should be attempted; at least the ones that exist should succeed
        successful = [r for r in results if "error" not in r]
        assert len(successful) == 2
        for r in successful:
            assert "backup_path" in r


class TestResolveDbPath:
    """Tests for resolving database names to paths."""

    def test_resolve_db_path_resolves_named_databases(self, manager):
        """resolve_db_path should resolve a configured name to a Path."""
        path = manager.resolve_db_path("test_main")
        assert isinstance(path, Path)
        assert "main.db" in str(path)

    def test_resolve_db_path_raises_for_unknown_name(self, manager):
        """resolve_db_path should raise ValueError for unconfigured names."""
        with pytest.raises(ValueError, match="Unknown database"):
            manager.resolve_db_path("nonexistent_db")
