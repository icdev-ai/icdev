# CUI // SP-CTI
"""Unit tests for tools.genesis.reflexes.log_triage (LOG-08)."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _write_ndjson(path: Path, events: list) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def patch_logger(monkeypatch, tmp_path):
    import tools.logging.icdev_logger as mod
    mod.invalidate_cache()
    mod._CONFIG_CACHE = {
        "global_level": "DEBUG",
        "log_dir": str(tmp_path),
        "rotation": {"when": "midnight", "retention_days": 7, "max_bytes": 1_048_576},
        "component_overrides": {},
    }
    yield
    mod.invalidate_cache()


class TestReadNdjsonTail:
    def test_returns_empty_for_missing_file(self, tmp_path):
        from tools.genesis.reflexes.log_triage import _read_ndjson_tail
        result = _read_ndjson_tail(tmp_path / "missing.ndjson")
        assert result == []

    def test_reads_valid_events(self, tmp_path):
        from tools.genesis.reflexes.log_triage import _read_ndjson_tail
        events = [{"ts": "t", "level": "ERROR", "component": "a", "message": "m"}]
        f = tmp_path / "build.ndjson"
        _write_ndjson(f, events)
        result = _read_ndjson_tail(f)
        assert len(result) == 1
        assert result[0]["level"] == "ERROR"

    def test_skips_invalid_json_lines(self, tmp_path):
        from tools.genesis.reflexes.log_triage import _read_ndjson_tail
        f = tmp_path / "build.ndjson"
        f.write_text('{"level":"INFO"}\nNOT JSON\n{"level":"ERROR"}\n', encoding="utf-8")
        result = _read_ndjson_tail(f)
        assert len(result) == 2

    def test_respects_tail_limit(self, tmp_path):
        from tools.genesis.reflexes.log_triage import _read_ndjson_tail
        events = [{"level": "INFO", "component": "c", "message": str(i)} for i in range(100)]
        f = tmp_path / "build.ndjson"
        _write_ndjson(f, events)
        result = _read_ndjson_tail(f, lines=10)
        assert len(result) == 10
        assert result[-1]["message"] == "99"


class TestExtractSignatures:
    def test_deduplicates_same_component_message(self):
        from tools.genesis.reflexes.log_triage import _extract_signatures
        events = [
            {"level": "ERROR", "component": "foo", "message": "boom", "returncode": 1},
            {"level": "ERROR", "component": "foo", "message": "boom", "returncode": 1},
        ]
        sigs = _extract_signatures(events)
        assert len(sigs) == 1

    def test_keeps_different_messages(self):
        from tools.genesis.reflexes.log_triage import _extract_signatures
        events = [
            {"level": "ERROR", "component": "foo", "message": "boom1", "returncode": 1},
            {"level": "ERROR", "component": "foo", "message": "boom2", "returncode": 1},
        ]
        sigs = _extract_signatures(events)
        assert len(sigs) == 2

    def test_filters_non_failures(self):
        from tools.genesis.reflexes.log_triage import _extract_signatures
        events = [
            {"level": "INFO", "component": "foo", "message": "ok", "returncode": 0},
            {"level": "ERROR", "component": "bar", "message": "fail", "returncode": 1},
        ]
        sigs = _extract_signatures(events)
        assert len(sigs) == 1
        assert sigs[0]["component"] == "bar"

    def test_failed_count_triggers_failure(self):
        from tools.genesis.reflexes.log_triage import _extract_signatures
        events = [{"level": "INFO", "component": "x", "message": "run", "failed": 3, "returncode": 1}]
        sigs = _extract_signatures(events)
        assert len(sigs) == 1

    def test_deduplicates_and_skips_info(self):
        """Duplicate (component, message) tuples collapse to one; INFO-only logs are excluded."""
        from tools.genesis.reflexes.log_triage import _extract_signatures
        events = [
            {"level": "ERROR", "component": "auth", "message": "token expired", "returncode": 1},
            {"level": "ERROR", "component": "auth", "message": "token expired", "returncode": 1},
            {"level": "ERROR", "component": "auth", "message": "token expired", "returncode": 1},
            {"level": "INFO",  "component": "auth", "message": "token expired", "returncode": 0},
            {"level": "INFO",  "component": "db",   "message": "connected",     "returncode": 0},
        ]
        sigs = _extract_signatures(events)
        assert len(sigs) == 1
        assert sigs[0]["component"] == "auth"
        assert sigs[0]["message"] == "token expired"
        sig_components = [s["component"] for s in sigs]
        assert "db" not in sig_components


class TestRun:
    def test_run_no_log_file(self, tmp_path):
        from tools.genesis.reflexes import log_triage as mod
        mod._SEEN_SIGS_FILE = tmp_path / "seen.json"
        result = mod.run({"build_log": str(tmp_path / "missing.ndjson")}, None)
        assert result["tasks_created"] == 0
        assert result["events_scanned"] == 0

    def test_run_creates_task_for_new_failure(self, tmp_path):
        from tools.genesis.reflexes import log_triage as mod
        mod._SEEN_SIGS_FILE = tmp_path / "seen.json"
        build_log = tmp_path / "build.ndjson"
        _write_ndjson(build_log, [
            {"ts": "2026-01-01T00:00:00Z", "level": "ERROR",
             "component": "test_comp", "message": "test failed",
             "returncode": 1, "failed": 2, "event_type": "pytest_run",
             "failures": []}
        ])
        with patch.object(mod, "_create_task", return_value=True) as mock_create:
            result = mod.run({"build_log": str(build_log)}, None)
        assert result["tasks_created"] == 1
        mock_create.assert_called_once()

    def test_run_skips_already_seen_sig(self, tmp_path):
        from tools.genesis.reflexes import log_triage as mod
        mod._SEEN_SIGS_FILE = tmp_path / "seen.json"
        build_log = tmp_path / "build.ndjson"
        _write_ndjson(build_log, [
            {"ts": "t", "level": "ERROR", "component": "comp",
             "message": "msg", "returncode": 1, "failed": 1}
        ])
        # First run — discovers signature
        with patch.object(mod, "_create_task", return_value=True):
            mod.run({"build_log": str(build_log)}, None)
        # Second run — same log, should skip
        with patch.object(mod, "_create_task", return_value=True) as mock2:
            r2 = mod.run({"build_log": str(build_log)}, None)
        assert r2["tasks_created"] == 0
        mock2.assert_not_called()
