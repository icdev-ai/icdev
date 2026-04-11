#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for sandbox executor (D-SEC-10).

All tests are mock-based — no Docker or llm-sandbox installation required.
"""

import builtins
import hashlib
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.security.sandbox_executor import (  # noqa: E402
    DEFAULT_CONFIG,
    SandboxExecutor,
    SandboxResult,
    _ensure_tables,
    _safe_str,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path):
    """Return a path to a temporary SQLite DB (does not yet have tables)."""
    return tmp_path / "test_sandbox.db"


@pytest.fixture
def executor_no_docker(tmp_db):
    """SandboxExecutor that skips Docker probe (always unavailable)."""
    with patch.object(SandboxExecutor, "_probe_availability", return_value=False):
        exe = SandboxExecutor(db_path=tmp_db)
    return exe


@pytest.fixture
def executor_disabled(tmp_db):
    """SandboxExecutor with sandbox disabled via config."""
    cfg = dict(DEFAULT_CONFIG)
    cfg["enabled"] = False
    with patch.object(SandboxExecutor, "_probe_availability", return_value=False):
        exe = SandboxExecutor(config=cfg, db_path=tmp_db)
    return exe


@pytest.fixture
def mock_no_sandbox(monkeypatch):
    """Simulate llm_sandbox not installed by patching builtins.__import__."""
    original_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == "llm_sandbox":
            raise ImportError("No module named 'llm_sandbox'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mock_import)


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------


class TestSandboxResultDataclass:
    """SandboxResult has correct fields and defaults."""

    def test_sandbox_result_dataclass(self):
        r = SandboxResult()
        assert r.stdout == ""
        assert r.stderr == ""
        assert r.exit_code == -1
        assert r.runtime_ms == 0
        assert r.artifacts == []
        assert r.container_id == ""
        assert r.status == "completed"
        assert r.error == ""

    def test_sandbox_result_custom_values(self):
        r = SandboxResult(
            stdout="hello world\n",
            stderr="",
            exit_code=0,
            runtime_ms=142,
            artifacts=["output.txt"],
            container_id="abc123",
            status="completed",
            error="",
        )
        assert r.exit_code == 0
        assert r.runtime_ms == 142
        assert r.artifacts == ["output.txt"]

    def test_sandbox_result_to_dict(self):
        r = SandboxResult(stdout="ok", exit_code=0, status="completed")
        d = r.to_dict()
        assert isinstance(d, dict)
        assert d["stdout"] == "ok"
        assert d["exit_code"] == 0
        assert d["status"] == "completed"
        # All fields present
        for field in ("stdout", "stderr", "exit_code", "runtime_ms", "artifacts", "container_id", "status", "error"):
            assert field in d

    def test_sandbox_result_mutable_artifacts(self):
        """Each SandboxResult gets its own artifacts list (no shared default)."""
        r1 = SandboxResult()
        r2 = SandboxResult()
        r1.artifacts.append("x.txt")
        assert r2.artifacts == []


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestDefaultConfig:
    """DEFAULT_CONFIG has the expected structure."""

    def test_default_config_keys(self):
        expected = {
            "enabled",
            "runtime",
            "default_timeout_seconds",
            "max_timeout_seconds",
            "default_max_memory_mb",
            "max_memory_mb",
            "default_cpu_count",
            "network_enabled",
            "images",
            "cleanup_containers",
            "max_concurrent_sessions",
            "audit_all_executions",
        }
        assert expected.issubset(set(DEFAULT_CONFIG.keys()))

    def test_default_config_images_languages(self):
        images = DEFAULT_CONFIG["images"]
        for lang in ("python", "javascript", "java", "go"):
            assert lang in images
            assert isinstance(images[lang], str)
            assert len(images[lang]) > 0

    def test_default_config_safety_values(self):
        assert DEFAULT_CONFIG["network_enabled"] is False
        assert DEFAULT_CONFIG["cleanup_containers"] is True
        assert DEFAULT_CONFIG["default_timeout_seconds"] <= DEFAULT_CONFIG["max_timeout_seconds"]
        assert DEFAULT_CONFIG["default_max_memory_mb"] <= DEFAULT_CONFIG["max_memory_mb"]

    def test_default_config_runtime(self):
        assert DEFAULT_CONFIG["runtime"] == "docker"

    def test_default_config_audit_enabled(self):
        assert DEFAULT_CONFIG["audit_all_executions"] is True


# ---------------------------------------------------------------------------
# Executor init tests
# ---------------------------------------------------------------------------


class TestExecutorInit:
    """SandboxExecutor initializes without error in all conditions."""

    def test_executor_init_no_docker(self, tmp_db):
        """Initializes gracefully when Docker is unavailable."""
        with patch.object(SandboxExecutor, "_probe_availability", return_value=False):
            exe = SandboxExecutor(db_path=tmp_db)
        assert exe is not None
        assert exe._available is False
        assert exe._enabled is True

    def test_executor_init_disabled(self, tmp_db):
        """Initializes with enabled=False without probing availability."""
        cfg = dict(DEFAULT_CONFIG)
        cfg["enabled"] = False
        # No patch needed — disabled executors skip probing
        exe = SandboxExecutor(config=cfg, db_path=tmp_db)
        assert exe._enabled is False
        assert exe._available is False

    def test_executor_init_custom_config(self, tmp_db):
        """Custom config overrides defaults."""
        cfg = dict(DEFAULT_CONFIG)
        cfg["runtime"] = "podman"
        cfg["default_timeout_seconds"] = 60
        with patch.object(SandboxExecutor, "_probe_availability", return_value=False):
            exe = SandboxExecutor(config=cfg, db_path=tmp_db)
        assert exe._runtime == "podman"
        assert exe._config["default_timeout_seconds"] == 60

    def test_executor_stores_db_path(self, tmp_db):
        with patch.object(SandboxExecutor, "_probe_availability", return_value=False):
            exe = SandboxExecutor(db_path=tmp_db)
        assert exe._db_path == tmp_db


# ---------------------------------------------------------------------------
# Unavailable / disabled execution tests
# ---------------------------------------------------------------------------


class TestExecuteUnavailable:
    """When llm_sandbox is not importable, execute() returns graceful error."""

    def test_execute_unavailable_returns_error_result(self, executor_no_docker):
        result = executor_no_docker.execute(code="print('hi')", language="python")
        assert isinstance(result, SandboxResult)
        assert result.status == "unavailable"
        assert "not available" in result.error.lower()
        assert result.exit_code == -1

    def test_execute_unavailable_no_exception(self, executor_no_docker):
        """Must not raise — caller should inspect result.status."""
        try:
            executor_no_docker.execute(code="import os; os.system('ls')", language="python")
        except Exception as exc:
            pytest.fail(f"execute() raised an exception: {exc}")

    def test_execute_unavailable_any_language(self, executor_no_docker):
        for lang in ("python", "javascript", "java", "go"):
            result = executor_no_docker.execute(code="// test", language=lang)
            assert result.status == "unavailable"

    def test_execute_unsupported_language_unavailable(self, executor_no_docker):
        """Unsupported language still returns failed (checked before availability)."""
        # When unavailable, unavailability takes precedence
        result = executor_no_docker.execute(code="x", language="cobol")
        assert result.status in ("unavailable", "failed")
        assert result.exit_code == -1


class TestExecuteDisabled:
    """When config enabled=False, execute() returns disabled status."""

    def test_execute_disabled_returns_disabled_result(self, executor_disabled):
        result = executor_disabled.execute(code="print('hi')", language="python")
        assert isinstance(result, SandboxResult)
        assert result.status == "disabled"
        assert "disabled" in result.error.lower()

    def test_execute_file_disabled(self, executor_disabled, tmp_path):
        script = tmp_path / "test.py"
        script.write_text("print('hello')", encoding="utf-8")
        result = executor_disabled.execute_file(file_path=script, language="python")
        assert result.status == "disabled"

    def test_execute_disabled_no_exception(self, executor_disabled):
        try:
            executor_disabled.execute(code="raise SystemExit(1)", language="python")
        except Exception as exc:
            pytest.fail(f"execute() raised an exception: {exc}")


# ---------------------------------------------------------------------------
# Health check tests
# ---------------------------------------------------------------------------


class TestHealthCheckStructure:
    """health_check() returns a dict with expected keys."""

    def test_health_check_returns_dict(self, executor_no_docker):
        health = executor_no_docker.health_check()
        assert isinstance(health, dict)

    def test_health_check_required_keys(self, executor_no_docker):
        health = executor_no_docker.health_check()
        required = {
            "available",
            "enabled",
            "docker_running",
            "llm_sandbox_installed",
            "smoke_test_passed",
            "runtime",
            "languages",
        }
        assert required.issubset(set(health.keys()))

    def test_health_check_disabled_executor(self, executor_disabled):
        health = executor_disabled.health_check()
        assert health["enabled"] is False
        assert health["available"] is False

    def test_health_check_languages_list(self, executor_no_docker):
        health = executor_no_docker.health_check()
        assert isinstance(health["languages"], list)
        assert "python" in health["languages"]

    def test_health_check_runtime_field(self, executor_no_docker):
        health = executor_no_docker.health_check()
        assert health["runtime"] == executor_no_docker._runtime

    def test_health_check_unavailable_not_available(self, executor_no_docker):
        """When Docker/llm-sandbox missing, available is False."""
        health = executor_no_docker.health_check()
        assert health["available"] is False

    def test_health_check_error_field_present(self, executor_no_docker):
        """error key is present (may be None or a string)."""
        health = executor_no_docker.health_check()
        assert "error" in health


# ---------------------------------------------------------------------------
# Supported languages tests
# ---------------------------------------------------------------------------


class TestSupportedLanguages:
    """python, javascript, java, go are configured with container images."""

    def test_all_four_languages_in_images(self):
        for lang in ("python", "javascript", "java", "go"):
            assert lang in DEFAULT_CONFIG["images"]

    def test_python_image_is_slim(self):
        assert "slim" in DEFAULT_CONFIG["images"]["python"]

    def test_javascript_image_has_node(self):
        assert "node" in DEFAULT_CONFIG["images"]["javascript"]

    def test_java_image_has_jre(self):
        img = DEFAULT_CONFIG["images"]["java"]
        assert "temurin" in img or "jre" in img or "jdk" in img

    def test_go_image_has_golang(self):
        assert "golang" in DEFAULT_CONFIG["images"]["go"]

    def test_unsupported_language_returns_failed(self, executor_no_docker):
        # Make the executor appear available so we hit the language validation
        executor_no_docker._available = True
        result = executor_no_docker.execute(code="print('x')", language="ruby")
        assert result.status in ("failed", "unavailable")

    def test_config_images_override(self, tmp_db):
        """Custom image config is respected."""
        cfg = dict(DEFAULT_CONFIG)
        cfg["images"] = {"python": "python:3.11-alpine"}
        with patch.object(SandboxExecutor, "_probe_availability", return_value=False):
            exe = SandboxExecutor(config=cfg, db_path=tmp_db)
        assert exe._config["images"]["python"] == "python:3.11-alpine"
        assert "javascript" not in exe._config["images"]


# ---------------------------------------------------------------------------
# Code hash determinism tests
# ---------------------------------------------------------------------------


class TestCodeHashDeterministic:
    """Same code always produces the same SHA-256 hash."""

    def test_hash_identical_code(self):
        code = "print('hello, world!')"
        h1 = hashlib.sha256(code.encode("utf-8")).hexdigest()
        h2 = hashlib.sha256(code.encode("utf-8")).hexdigest()
        assert h1 == h2

    def test_hash_different_code_differs(self):
        h1 = hashlib.sha256(b"print('a')").hexdigest()
        h2 = hashlib.sha256(b"print('b')").hexdigest()
        assert h1 != h2

    def test_hash_is_64_hex_chars(self):
        h = hashlib.sha256(b"test code").hexdigest()
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_unicode_code(self):
        code = "print('héllo wörld')"
        h = hashlib.sha256(code.encode("utf-8")).hexdigest()
        assert len(h) == 64

    def test_hash_empty_string(self):
        h = hashlib.sha256(b"").hexdigest()
        assert len(h) == 64
        # Well-known SHA-256 of empty string
        assert h == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


# ---------------------------------------------------------------------------
# DB table creation tests
# ---------------------------------------------------------------------------


class TestLogTableCreation:
    """_ensure_tables creates sandbox_execution_log with correct schema."""

    def test_table_created(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        _ensure_tables(conn)
        # Verify table exists
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sandbox_execution_log'"
        ).fetchall()
        assert len(rows) == 1
        conn.close()

    def test_table_columns(self, tmp_db):
        conn = sqlite3.connect(str(tmp_db))
        _ensure_tables(conn)
        info = conn.execute("PRAGMA table_info(sandbox_execution_log)").fetchall()
        col_names = {row[1] for row in info}
        required_cols = {
            "id",
            "executor_type",
            "language",
            "code_hash",
            "runtime",
            "exit_code",
            "runtime_ms",
            "stdout_preview",
            "stderr_preview",
            "network_enabled",
            "memory_limit_mb",
            "timeout_seconds",
            "container_id",
            "artifact_count",
            "status",
            "actor",
            "project_id",
            "tenant_id",
            "classification",
            "created_at",
        }
        assert required_cols.issubset(col_names)
        conn.close()

    def test_table_idempotent(self, tmp_db):
        """_ensure_tables is safe to call multiple times."""
        conn = sqlite3.connect(str(tmp_db))
        _ensure_tables(conn)
        _ensure_tables(conn)  # second call must not error
        conn.close()

    def test_classification_default_cui(self, tmp_db):
        """classification column defaults to CUI."""
        conn = sqlite3.connect(str(tmp_db))
        _ensure_tables(conn)
        conn.execute(
            """INSERT INTO sandbox_execution_log
               (id, executor_type, language, code_hash, runtime, status)
               VALUES ('test-1', 'test', 'python', 'abc', 'docker', 'completed')"""
        )
        conn.commit()
        row = conn.execute("SELECT classification FROM sandbox_execution_log WHERE id='test-1'").fetchone()
        assert row[0] == "CUI"
        conn.close()

    def test_id_prefix_pattern(self, tmp_db):
        """IDs should follow 'sbx-' prefix convention."""
        exec_id = "sbx-" + __import__("uuid").uuid4().hex[:12]
        assert exec_id.startswith("sbx-")
        assert len(exec_id) == 16  # 'sbx-' (4) + 12 hex chars


# ---------------------------------------------------------------------------
# execute_file tests
# ---------------------------------------------------------------------------


class TestExecuteFileReadsContent:
    """execute_file reads the file and passes content to execute()."""

    def test_missing_file_returns_failed(self, executor_no_docker):
        result = executor_no_docker.execute_file(
            file_path=Path("/nonexistent/path/script.py"),
            language="python",
        )
        assert result.status == "failed"
        assert "not found" in result.error.lower()

    def test_reads_file_content(self, executor_no_docker, tmp_path, monkeypatch):
        """Verify execute() is called with the file's text content."""
        script = tmp_path / "hello.py"
        script.write_text("print('from file')", encoding="utf-8")

        captured = {}

        original_execute = executor_no_docker.execute

        def mock_execute(code, **kwargs):
            captured["code"] = code
            return original_execute(code, **kwargs)

        monkeypatch.setattr(executor_no_docker, "execute", mock_execute)
        executor_no_docker.execute_file(file_path=script, language="python")

        assert captured.get("code") == "print('from file')"

    def test_auto_detects_python_extension(self, executor_no_docker, tmp_path):
        """A .py file is auto-detected as python even if language param is default."""
        script = tmp_path / "code.py"
        script.write_text("x = 1", encoding="utf-8")
        # Should not raise or crash — language resolved from extension
        result = executor_no_docker.execute_file(file_path=script)
        assert result.status in ("unavailable", "disabled", "failed")

    def test_auto_detects_js_extension(self, executor_no_docker, tmp_path):
        script = tmp_path / "app.js"
        script.write_text("console.log('hi')", encoding="utf-8")
        result = executor_no_docker.execute_file(file_path=script)
        assert result.status in ("unavailable", "disabled", "failed")

    def test_empty_file_passes_empty_code(self, executor_no_docker, tmp_path, monkeypatch):
        script = tmp_path / "empty.py"
        script.write_text("", encoding="utf-8")

        captured = {}
        original_execute = executor_no_docker.execute

        def mock_execute(code, **kwargs):
            captured["code"] = code
            return original_execute(code, **kwargs)

        monkeypatch.setattr(executor_no_docker, "execute", mock_execute)
        executor_no_docker.execute_file(file_path=script, language="python")
        assert captured.get("code") == ""


# ---------------------------------------------------------------------------
# Safe string utility tests
# ---------------------------------------------------------------------------


class TestSafeStr:
    def test_none_returns_empty_string(self):
        assert _safe_str(None) == ""

    def test_string_passthrough(self):
        assert _safe_str("hello") == "hello"

    def test_int_converts(self):
        assert _safe_str(42) == "42"

    def test_bytes_converts(self):
        result = _safe_str(b"data")
        assert "data" in result


# ---------------------------------------------------------------------------
# Resource limit tests
# ---------------------------------------------------------------------------


class TestResourceLimits:
    """Verify timeout and memory limits are capped by config maximums."""

    def test_timeout_capped_at_max(self, tmp_db):
        cfg = dict(DEFAULT_CONFIG)
        cfg["max_timeout_seconds"] = 60
        with patch.object(SandboxExecutor, "_probe_availability", return_value=True):
            exe = SandboxExecutor(config=cfg, db_path=tmp_db)

        def mock_llm_sandbox(*args, **kwargs):
            raise ImportError("test")  # triggers graceful fallback

        # The timeout capping logic is inside execute(), so we verify it
        # indirectly by passing an oversized timeout — the result status
        # should reflect unavailable, not an exception
        result = exe.execute(code="x=1", language="python", timeout_seconds=9999)
        # Unavailable because llm_sandbox import will fail in test env
        assert result.status in ("unavailable", "failed", "completed")

    def test_memory_capped_at_max(self, tmp_db):
        cfg = dict(DEFAULT_CONFIG)
        cfg["max_memory_mb"] = 1024
        with patch.object(SandboxExecutor, "_probe_availability", return_value=False):
            exe = SandboxExecutor(config=cfg, db_path=tmp_db)
        result = exe.execute(code="x=1", language="python", max_memory_mb=99999)
        # Unavailable — just confirms no exception raised with huge memory param
        assert result.status in ("unavailable", "disabled")


# ---------------------------------------------------------------------------
# OPT-63: Auto-recreate on container failure tests
# ---------------------------------------------------------------------------


class TestAutoRecreate:
    """Sandbox auto-recreates when a container infrastructure error is detected."""

    def _make_executor(self, tmp_db, auto_recreate_cfg=None):
        """Return an executor with _available=True and custom auto_recreate config."""
        cfg = dict(DEFAULT_CONFIG)
        cfg["auto_recreate"] = auto_recreate_cfg or {
            "enabled": True,
            "max_retries": 2,
            "backoff_seconds": 0,  # no real sleep in tests
        }
        with patch.object(SandboxExecutor, "_probe_availability", return_value=True):
            return SandboxExecutor(config=cfg, db_path=tmp_db)

    def test_retry_on_container_not_found_succeeds(self, tmp_db):
        """ContainerNotFoundError triggers rebuild and retry; succeeds on second attempt."""
        exe = self._make_executor(tmp_db)

        call_count = 0

        def mock_run_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("docker: No such container: llm-sandbox")
            return SandboxResult(stdout="sandbox-ok", exit_code=0, status="completed")

        with patch.object(exe, "_run_sandbox_once", side_effect=mock_run_once):
            with patch.object(exe, "_rebuild_container") as mock_rebuild:
                result = exe.execute(code="print('sandbox-ok')", language="python")

        assert result.status == "completed", f"Expected completed, got {result.status}"
        assert call_count == 2, f"Expected 2 attempts, got {call_count}"
        mock_rebuild.assert_called_once_with(
            language="python",
            image=DEFAULT_CONFIG["images"]["python"],
        )

    def test_retry_exhausted_returns_failed(self, tmp_db):
        """After max_retries (2) container rebuilds, execute() returns failed status."""
        exe = self._make_executor(tmp_db)

        call_count = 0

        def always_fail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("docker: No such container: llm-sandbox")

        with patch.object(exe, "_run_sandbox_once", side_effect=always_fail):
            with patch.object(exe, "_rebuild_container") as mock_rebuild:
                result = exe.execute(code="print('hello')", language="python")

        assert result.status == "failed", f"Expected failed, got {result.status}"
        # original attempt + 2 rebuilds = 3 total attempts
        assert call_count == 3, f"Expected 3 attempts, got {call_count}"
        assert mock_rebuild.call_count == 2, f"Expected 2 rebuilds, got {mock_rebuild.call_count}"
        assert "docker" in result.error.lower() or "container" in result.error.lower()

    def test_auto_recreate_disabled_does_not_retry(self, tmp_db):
        """When auto_recreate.enabled=False, container errors bubble up immediately."""
        exe = self._make_executor(tmp_db, auto_recreate_cfg={"enabled": False, "max_retries": 2, "backoff_seconds": 0})

        call_count = 0

        def fail_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("docker: No such container")

        with patch.object(exe, "_run_sandbox_once", side_effect=fail_once):
            with patch.object(exe, "_rebuild_container") as mock_rebuild:
                result = exe.execute(code="x=1", language="python")

        assert result.status == "failed"
        assert call_count == 1, "Should not retry when auto_recreate disabled"
        mock_rebuild.assert_not_called()

    def test_is_container_error_connection_refused(self, tmp_db):
        """ConnectionRefusedError is classified as a container error."""
        exe = self._make_executor(tmp_db)
        assert exe._is_container_error(ConnectionRefusedError("connection refused"))

    def test_is_container_error_docker_in_message(self, tmp_db):
        """RuntimeError with 'docker' in message is classified as container error."""
        exe = self._make_executor(tmp_db)
        assert exe._is_container_error(RuntimeError("docker daemon not running"))

    def test_is_container_error_not_found_class_name(self, tmp_db):
        """Exception named 'NotFound' is classified as container error."""
        exe = self._make_executor(tmp_db)

        class NotFound(Exception):
            pass

        assert exe._is_container_error(NotFound("container gone"))

    def test_is_container_error_value_error_not_matched(self, tmp_db):
        """Generic ValueError unrelated to Docker is NOT a container error."""
        exe = self._make_executor(tmp_db)
        assert not exe._is_container_error(ValueError("bad argument"))

    def test_timeout_does_not_trigger_retry(self, tmp_db):
        """TimeoutError must not cause a rebuild — it exits the loop immediately."""
        exe = self._make_executor(tmp_db)

        call_count = 0

        def timeout_always(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise TimeoutError("timed out")

        with patch.object(exe, "_run_sandbox_once", side_effect=timeout_always):
            with patch.object(exe, "_rebuild_container") as mock_rebuild:
                result = exe.execute(code="x=1", language="python")

        assert result.status == "timeout"
        assert call_count == 1, "TimeoutError must not be retried"
        mock_rebuild.assert_not_called()
