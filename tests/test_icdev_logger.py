# CUI // SP-CTI
"""Unit tests for tools.logging.icdev_logger (LOG-03)."""
import json
import logging
from pathlib import Path


# Ensure repo root on path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _fresh_logger(monkeypatch, tmp_path):
    """Return get_logger with a fresh cache pointing at tmp_path."""
    import tools.logging.icdev_logger as mod
    mod.invalidate_cache()
    monkeypatch.setattr(mod, "_CONFIG_CACHE", {
        "global_level": "DEBUG",
        "log_dir": str(tmp_path),
        "rotation": {"when": "midnight", "retention_days": 7, "max_bytes": 1_048_576},
        "component_overrides": {},
    })
    return mod.get_logger


class TestJsonFormatter:
    def test_format_has_required_fields(self):
        from tools.logging.icdev_logger import _JsonFormatter
        fmt = _JsonFormatter()
        record = logging.LogRecord(
            name="test_comp", level=logging.INFO,
            pathname="", lineno=0, msg="hello %s", args=("world",),
            exc_info=None,
        )
        line = fmt.format(record)
        data = json.loads(line)
        assert data["level"] == "INFO"
        assert data["component"] == "test_comp"
        assert data["message"] == "hello world"
        assert "ts" in data
        assert "trace_id" in data
        assert "session_id" in data
        assert "extra" in data

    def test_format_extra_passthrough(self):
        from tools.logging.icdev_logger import _JsonFormatter
        fmt = _JsonFormatter()
        record = logging.LogRecord("c", logging.WARNING, "", 0, "msg", (), None)
        record.extra = {"key": "val"}
        data = json.loads(fmt.format(record))
        assert data["extra"] == {"key": "val"}

    def test_non_serialisable_extra_does_not_raise(self):
        from tools.logging.icdev_logger import _JsonFormatter
        fmt = _JsonFormatter()
        record = logging.LogRecord("c", logging.ERROR, "", 0, "boom", (), None)
        record.extra = {"obj": object()}
        # default=str handles non-serialisable objects
        line = fmt.format(record)
        assert "component" in line


class TestGetLogger:
    def test_returns_logger_instance(self, monkeypatch, tmp_path):
        get_logger = _fresh_logger(monkeypatch, tmp_path)
        log = get_logger("unit_test_comp")
        assert isinstance(log, logging.Logger)
        assert log.name == "unit_test_comp"

    def test_cache_returns_same_instance(self, monkeypatch, tmp_path):
        get_logger = _fresh_logger(monkeypatch, tmp_path)
        a = get_logger("cached_comp")
        b = get_logger("cached_comp")
        assert a is b

    def test_log_dir_created(self, monkeypatch, tmp_path):
        nested = tmp_path / "deep" / "dir"
        get_logger = _fresh_logger(monkeypatch, tmp_path)
        import tools.logging.icdev_logger as mod
        mod.invalidate_cache()
        mod._CONFIG_CACHE = {
            "global_level": "INFO",
            "log_dir": str(nested),
            "rotation": {"when": "midnight", "retention_days": 7, "max_bytes": 1_048_576},
            "component_overrides": {},
        }
        get_logger("dir_test")
        assert nested.exists()

    def test_writes_ndjson_on_log(self, monkeypatch, tmp_path):
        get_logger = _fresh_logger(monkeypatch, tmp_path)
        log = get_logger("write_test")
        log.info("test message")
        log_file = tmp_path / "write_test.ndjson"
        assert log_file.exists()
        data = json.loads(log_file.read_text(encoding="utf-8").strip())
        assert data["message"] == "test message"
        assert data["level"] == "INFO"

    def test_component_level_override(self, monkeypatch, tmp_path):
        import tools.logging.icdev_logger as mod
        mod.invalidate_cache()
        mod._CONFIG_CACHE = {
            "global_level": "ERROR",
            "log_dir": str(tmp_path),
            "rotation": {"when": "midnight", "retention_days": 7, "max_bytes": 1_048_576},
            "component_overrides": {"my_comp": {"level": "DEBUG"}},
        }
        log = mod.get_logger("my_comp")
        assert log.level == logging.DEBUG

    def test_invalidate_cache_resets(self, monkeypatch, tmp_path):
        import tools.logging.icdev_logger as mod
        get_logger = _fresh_logger(monkeypatch, tmp_path)
        get_logger("reset_comp")
        assert "reset_comp" in mod._CACHE
        mod.invalidate_cache()
        # After invalidation, internal caches must be empty
        assert "reset_comp" not in mod._CACHE
        assert mod._CONFIG_CACHE is None
