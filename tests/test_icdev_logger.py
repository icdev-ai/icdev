# CUI // SP-CTI
"""Unit tests for tools.logging.icdev_logger (LOG-03)."""
import json
import logging
from pathlib import Path


# Ensure repo root on path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import the canonical module first so the `tools.* -> icdev.tools.*` shim can
# resolve the `import tools.logging.icdev_logger as mod` form used below when this
# file is run in isolation (in the full suite an earlier test triggers this).
import icdev.tools.logging.icdev_logger  # noqa: E402,F401


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


class TestWindowsRotationCollision:
    """hcx-ace-10: rotation blocked by a cross-process file lock must never
    lose records or flood stderr."""

    def test_is_sharing_violation_classifier(self):
        from tools.logging.icdev_logger import _is_sharing_violation
        assert _is_sharing_violation(PermissionError("in use")) is True
        win32 = OSError("sharing violation")
        win32.winerror = 32
        assert _is_sharing_violation(win32) is True
        win5 = OSError("access denied")
        win5.winerror = 5
        assert _is_sharing_violation(win5) is True
        other = OSError("disk full")
        other.winerror = 112
        assert _is_sharing_violation(other) is False

    def test_rotation_permissionerror_is_swallowed_and_logging_continues(
        self, monkeypatch, tmp_path, capsys
    ):
        """Simulate Windows WinError 32 during rollover: logging must keep
        working, no exception escapes, stderr is not flooded, and records still
        land in the current file."""
        get_logger = _fresh_logger(monkeypatch, tmp_path)
        log = get_logger("rot_collision")

        # Force the timed handler to roll over on the very next emit, then make
        # the underlying rename fail like a Windows sharing violation.  Select by
        # the distinguishing `rolloverAt` attribute rather than isinstance -- the
        # tools.* / icdev.tools.* shim gives the class two distinct identities.
        timed = next(h for h in log.handlers if hasattr(h, "rolloverAt"))
        monkeypatch.setattr(timed, "shouldRollover", lambda record: 1)

        def _boom(*_a, **_k):
            raise PermissionError(32, "The process cannot access the file")

        # os.rename is what both handlers call under the hood via self.rotate.
        monkeypatch.setattr("os.rename", _boom)

        # Multiple emits: none may raise; rotation is attempted every interval.
        for i in range(50):
            log.info(f"collision message {i}")

        # No exception surfaced, and stderr carries at most the single notice
        # (never one line per record -> no flood).
        err = capsys.readouterr().err
        assert "--- Logging error ---" not in err
        assert err.count("rotation of") <= 1

        # Records still reached the current base file.
        log_file = tmp_path / "rot_collision.ndjson"
        assert log_file.exists()
        lines = [
            l for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()
        ]
        assert len(lines) >= 1
        last = json.loads(lines[-1])
        assert last["message"] == "collision message 49"

    def test_non_sharing_oserror_still_propagates(self, monkeypatch, tmp_path):
        """A rotation failure that is NOT a sharing violation must not be
        silently swallowed by the safety net -- it must propagate."""
        import pytest
        from tools.logging.icdev_logger import _SafeRotatingFileHandler

        h = _SafeRotatingFileHandler(
            str(tmp_path / "x.ndjson"), maxBytes=1, backupCount=1, encoding="utf-8"
        )
        try:
            def _boom(*_a, **_k):
                err = OSError("disk full")
                err.winerror = 112  # ERROR_DISK_FULL, not a sharing violation
                raise err

            # rotate() is where the base->rotated rename happens; make it raise a
            # non-sharing OSError and assert doRollover re-raises rather than hides it.
            monkeypatch.setattr("os.rename", _boom)
            with pytest.raises(OSError):
                h.doRollover()
        finally:
            h.close()
