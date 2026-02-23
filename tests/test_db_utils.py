#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools.compat.db_utils — centralized DB path resolution."""

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.compat.db_utils import (
    get_icdev_db_path,
    get_memory_db_path,
    get_platform_db_path,
    get_project_root,
)


class TestGetProjectRoot:
    """Test project root detection."""

    def test_returns_path(self):
        result = get_project_root()
        assert isinstance(result, Path)

    def test_root_contains_claude_md(self):
        root = get_project_root()
        assert (root / "CLAUDE.md").exists()

    def test_root_contains_tools_dir(self):
        root = get_project_root()
        assert (root / "tools").is_dir()


class TestGetIcdevDbPath:
    """Test ICDEV DB path resolution with fallback chain."""

    def test_default_path(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            # Remove env var if set
            env = dict(os.environ)
            env.pop("ICDEV_DB_PATH", None)
            with mock.patch.dict(os.environ, env, clear=True):
                result = get_icdev_db_path()
                assert result.name == "icdev.db"
                assert "data" in str(result)

    def test_explicit_override(self):
        result = get_icdev_db_path("/custom/path.db")
        assert result == Path("/custom/path.db")

    def test_explicit_overrides_env(self):
        with mock.patch.dict(os.environ, {"ICDEV_DB_PATH": "/env/path.db"}):
            result = get_icdev_db_path("/explicit/path.db")
            assert result == Path("/explicit/path.db")

    def test_env_var_override(self):
        with mock.patch.dict(os.environ, {"ICDEV_DB_PATH": "/env/icdev.db"}):
            result = get_icdev_db_path()
            assert result == Path("/env/icdev.db")

    def test_returns_path_type(self):
        result = get_icdev_db_path()
        assert isinstance(result, Path)

    def test_explicit_string_converted_to_path(self):
        result = get_icdev_db_path("/tmp/test.db")
        assert isinstance(result, Path)

    def test_explicit_path_object(self):
        p = Path("/tmp/test.db")
        result = get_icdev_db_path(p)
        assert result == p


class TestGetMemoryDbPath:
    """Test memory DB path resolution."""

    def test_default_path(self):
        env = dict(os.environ)
        env.pop("ICDEV_MEMORY_DB_PATH", None)
        with mock.patch.dict(os.environ, env, clear=True):
            result = get_memory_db_path()
            assert result.name == "memory.db"

    def test_env_var_override(self):
        with mock.patch.dict(os.environ, {"ICDEV_MEMORY_DB_PATH": "/env/mem.db"}):
            result = get_memory_db_path()
            assert result == Path("/env/mem.db")

    def test_explicit_override(self):
        result = get_memory_db_path("/custom/memory.db")
        assert result == Path("/custom/memory.db")


class TestGetPlatformDbPath:
    """Test platform DB path resolution."""

    def test_default_path(self):
        env = dict(os.environ)
        env.pop("ICDEV_PLATFORM_DB_PATH", None)
        with mock.patch.dict(os.environ, env, clear=True):
            result = get_platform_db_path()
            assert result.name == "platform.db"

    def test_env_var_override(self):
        with mock.patch.dict(os.environ, {"ICDEV_PLATFORM_DB_PATH": "/env/plat.db"}):
            result = get_platform_db_path()
            assert result == Path("/env/plat.db")

    def test_explicit_override(self):
        result = get_platform_db_path("/custom/platform.db")
        assert result == Path("/custom/platform.db")


class TestFallbackChainPriority:
    """Verify explicit > env > default priority across all functions."""

    def test_icdev_db_priority_chain(self):
        with mock.patch.dict(os.environ, {"ICDEV_DB_PATH": "/env/db.db"}):
            # Explicit wins over env
            assert get_icdev_db_path("/explicit/db.db") == Path("/explicit/db.db")
            # Env wins over default
            assert get_icdev_db_path() == Path("/env/db.db")

    def test_none_explicit_falls_through(self):
        """None explicit should fall through to env/default."""
        with mock.patch.dict(os.environ, {"ICDEV_DB_PATH": "/env/db.db"}):
            assert get_icdev_db_path(None) == Path("/env/db.db")
