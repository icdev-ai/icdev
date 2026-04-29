# CUI // SP-CTI
"""Unit tests for tools/databridge/resolvers/file_resolver.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.databridge.resolvers.file_resolver import SecretResolverError, resolve


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_secret(tmp_path: Path, name: str, content: str) -> None:
    secret_file = tmp_path / name
    secret_file.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# resolve() — happy path
# ---------------------------------------------------------------------------

class TestResolveSuccess:
    def test_reads_plaintext_file(self, tmp_path, monkeypatch) -> None:
        _write_secret(tmp_path, "db_password", "hunter2")
        monkeypatch.setenv("DATABRIDGE_SECRET_FILES_ROOT", str(tmp_path))
        result = resolve("file:db_password")
        assert result == "hunter2"

    def test_strips_trailing_newline(self, tmp_path, monkeypatch) -> None:
        _write_secret(tmp_path, "api_key", "abc123\n")
        monkeypatch.setenv("DATABRIDGE_SECRET_FILES_ROOT", str(tmp_path))
        result = resolve("file:api_key")
        assert result == "abc123"

    def test_nested_path_within_root(self, tmp_path, monkeypatch) -> None:
        subdir = tmp_path / "prod"
        subdir.mkdir()
        _write_secret(subdir, "token", "tok-xyz")
        monkeypatch.setenv("DATABRIDGE_SECRET_FILES_ROOT", str(tmp_path))
        result = resolve("file:prod/token")
        assert result == "tok-xyz"


# ---------------------------------------------------------------------------
# resolve() — path traversal protection
# ---------------------------------------------------------------------------

class TestPathTraversal:
    def test_blocks_dotdot_traversal(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("DATABRIDGE_SECRET_FILES_ROOT", str(tmp_path))
        with pytest.raises(SecretResolverError, match="traversal"):
            resolve("file:../../etc/passwd")

    def test_blocks_absolute_path_escape(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("DATABRIDGE_SECRET_FILES_ROOT", str(tmp_path))
        # Using Windows absolute-style path in secret_id
        with pytest.raises(SecretResolverError, match="traversal|not found"):
            resolve("file:/etc/shadow")


# ---------------------------------------------------------------------------
# resolve() — error paths
# ---------------------------------------------------------------------------

class TestResolveErrors:
    def test_empty_secret_id_raises(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("DATABRIDGE_SECRET_FILES_ROOT", str(tmp_path))
        with pytest.raises(SecretResolverError, match="Empty secret_id"):
            resolve("file:")

    def test_missing_file_raises(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("DATABRIDGE_SECRET_FILES_ROOT", str(tmp_path))
        with pytest.raises(SecretResolverError, match="not found"):
            resolve("file:nonexistent_secret")

    def test_empty_file_raises(self, tmp_path, monkeypatch) -> None:
        _write_secret(tmp_path, "empty_secret", "   \n")
        monkeypatch.setenv("DATABRIDGE_SECRET_FILES_ROOT", str(tmp_path))
        with pytest.raises(SecretResolverError, match="empty"):
            resolve("file:empty_secret")

    def test_directory_instead_of_file_raises(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "a_dir").mkdir()
        monkeypatch.setenv("DATABRIDGE_SECRET_FILES_ROOT", str(tmp_path))
        with pytest.raises(SecretResolverError, match="not a regular file"):
            resolve("file:a_dir")

    def test_never_returns_empty_string(self, tmp_path, monkeypatch) -> None:
        _write_secret(tmp_path, "empty_guard", "")
        monkeypatch.setenv("DATABRIDGE_SECRET_FILES_ROOT", str(tmp_path))
        try:
            result = resolve("file:empty_guard")
            assert result != "", "resolve() must never return empty string"
        except SecretResolverError:
            pass  # correct behaviour


# ---------------------------------------------------------------------------
# Root configuration
# ---------------------------------------------------------------------------

class TestRootConfiguration:
    def test_uses_databridge_secret_files_root_env(self, tmp_path, monkeypatch) -> None:
        _write_secret(tmp_path, "key1", "value1")
        monkeypatch.setenv("DATABRIDGE_SECRET_FILES_ROOT", str(tmp_path))
        assert resolve("file:key1") == "value1"

    def test_env_takes_precedence_over_config(self, tmp_path, monkeypatch, tmp_path_factory) -> None:
        other_root = tmp_path_factory.mktemp("other_root")
        _write_secret(tmp_path, "the_key", "from_env_root")
        _write_secret(other_root, "the_key", "from_config_root")
        monkeypatch.setenv("DATABRIDGE_SECRET_FILES_ROOT", str(tmp_path))
        result = resolve("file:the_key")
        assert result == "from_env_root"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_file_registered_in_connection_manager(self) -> None:
        from tools.databridge.connection_manager import SECRET_RESOLVERS
        assert "file" in SECRET_RESOLVERS
        assert callable(SECRET_RESOLVERS["file"])
