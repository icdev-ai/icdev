# CUI // SP-CTI
"""Unit tests for tools.logging.build_logger (LOG-05)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def patch_build_log(monkeypatch, tmp_path):
    """Redirect build.ndjson to tmp_path for isolation."""
    import tools.logging.build_logger as mod
    import tools.logging.icdev_logger as logger_mod
    logger_mod.invalidate_cache()
    logger_mod._CONFIG_CACHE = {
        "global_level": "DEBUG",
        "log_dir": str(tmp_path),
        "rotation": {"when": "midnight", "retention_days": 7, "max_bytes": 1_048_576},
        "component_overrides": {},
    }
    monkeypatch.setattr(mod, "_build_log_path", lambda: tmp_path / "build.ndjson")
    # Re-init module-level logger with patched config
    mod._log = logger_mod.get_logger("build_logger")
    yield
    logger_mod.invalidate_cache()


class TestCapturePytest:
    def test_writes_event_on_failure(self, tmp_path):
        from tools.logging.build_logger import capture_pytest
        capture_pytest(returncode=1, stdout="1 passed, 2 failed", stderr="")
        log_file = tmp_path / "build.ndjson"
        assert log_file.exists()
        data = json.loads(log_file.read_text(encoding="utf-8").strip().split("\n")[-1])
        assert data["event_type"] == "pytest_run"
        assert data["returncode"] == 1
        assert data["failed"] == 2
        assert data["level"] == "ERROR"

    def test_writes_event_on_success(self, tmp_path):
        from tools.logging.build_logger import capture_pytest
        capture_pytest(returncode=0, stdout="10 passed", stderr="")
        log_file = tmp_path / "build.ndjson"
        data = json.loads(log_file.read_text(encoding="utf-8").strip().split("\n")[-1])
        assert data["returncode"] == 0
        assert data["level"] == "INFO"
        assert data["passed"] == 10

    def test_parses_failure_lines(self, tmp_path):
        from tools.logging.build_logger import capture_pytest
        stdout = "FAILED tests/test_foo.py::test_bar - AssertionError: expected True\n1 failed"
        capture_pytest(returncode=1, stdout=stdout, stderr="")
        log_file = tmp_path / "build.ndjson"
        data = json.loads(log_file.read_text(encoding="utf-8").strip().split("\n")[-1])
        assert len(data["failures"]) == 1
        assert "test_bar" in data["failures"][0]["test"]

    def test_explicit_counts_override_parse(self, tmp_path):
        from tools.logging.build_logger import capture_pytest
        capture_pytest(returncode=0, stdout="", stderr="", passed=99, failed=0, skipped=3)
        log_file = tmp_path / "build.ndjson"
        data = json.loads(log_file.read_text(encoding="utf-8").strip().split("\n")[-1])
        assert data["passed"] == 99
        assert data["skipped"] == 3


class TestCapturePlaywright:
    def test_writes_playwright_event(self, tmp_path):
        from tools.logging.build_logger import capture_playwright
        capture_playwright(returncode=0, stdout="551 passed (25.5m)", duration_s=1530.0)
        log_file = tmp_path / "build.ndjson"
        data = json.loads(log_file.read_text(encoding="utf-8").strip().split("\n")[-1])
        assert data["event_type"] == "playwright_run"
        assert data["passed"] == 551
        assert data["duration_s"] == 1530.0

    def test_failure_sets_error_level(self, tmp_path):
        from tools.logging.build_logger import capture_playwright
        capture_playwright(returncode=1, stdout="1 failed, 500 passed", duration_s=10.0)
        log_file = tmp_path / "build.ndjson"
        data = json.loads(log_file.read_text(encoding="utf-8").strip().split("\n")[-1])
        assert data["level"] == "ERROR"
        assert data["failed"] == 1
