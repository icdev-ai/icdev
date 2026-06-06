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
    # Force the deterministic rule-based detector and skip the centralized_logs
    # path so run() exercises only the build-log logic — no LLM, no DB.
    _CFG = {"_disable_llm": True, "centralized_enabled": False}

    def test_run_no_log_file(self, tmp_path):
        from tools.genesis.reflexes import log_triage as mod
        mod._SEEN_SIGS_FILE = tmp_path / "seen.json"
        result = mod.run({"build_log": str(tmp_path / "missing.ndjson"), **self._CFG}, None)
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
            result = mod.run({"build_log": str(build_log), **self._CFG}, None)
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
            mod.run({"build_log": str(build_log), **self._CFG}, None)
        # Second run — same log, should skip
        with patch.object(mod, "_create_task", return_value=True) as mock2:
            r2 = mod.run({"build_log": str(build_log), **self._CFG}, None)
        assert r2["tasks_created"] == 0
        mock2.assert_not_called()

    def test_run_creates_exactly_one_task_for_new_signature(self, tmp_path):
        """Exactly one task is created for a new failing signature, even with duplicate log events."""
        from tools.genesis.reflexes import log_triage as mod
        mod._SEEN_SIGS_FILE = tmp_path / "seen.json"
        build_log = tmp_path / "build.ndjson"
        # Three events: two share the same (component, message) — new signature deduplicates to one task
        _write_ndjson(build_log, [
            {"ts": "2026-01-01T00:00:00Z", "level": "ERROR",
             "component": "auth_service", "message": "connection refused",
             "returncode": 1, "failed": 1},
            {"ts": "2026-01-01T00:00:01Z", "level": "ERROR",
             "component": "auth_service", "message": "connection refused",
             "returncode": 1, "failed": 1},
            {"ts": "2026-01-01T00:00:02Z", "level": "INFO",
             "component": "auth_service", "message": "retry attempt",
             "returncode": 0},
        ])
        with patch.object(mod, "_create_task", return_value=True) as mock_create:
            result = mod.run({"build_log": str(build_log), **self._CFG}, None)
        assert result["tasks_created"] == 1
        mock_create.assert_called_once()


def _rule_detector():
    """A deterministic (no-LLM) AnomalyDetector for centralized-log tests."""
    from tools.genesis.reflexes.log_triage import AnomalyDetector
    return AnomalyDetector({"_disable_llm": True})


class TestReadCentralizedLogs:
    def test_maps_rows_to_events_across_components(self):
        from tools.genesis.reflexes.log_triage import _read_centralized_logs

        def fake_query(level=None, since=None, limit=None):
            if level == "ERROR":
                return [
                    {"id": "1", "ts": "t", "component": "auth", "level": "ERROR",
                     "message": "boom", "trace_id": "tr1", "session_id": "s1"},
                    {"id": "2", "ts": "t", "component": "db", "level": "ERROR",
                     "message": "deadlock", "trace_id": None, "session_id": None},
                ]
            return []

        events = _read_centralized_logs(query_fn=fake_query)
        assert len(events) == 2
        comps = {e["component"] for e in events}
        assert comps == {"auth", "db"}
        assert all(e["_source"] == "centralized_logs" for e in events)

    def test_empty_when_table_absent(self):
        from tools.genesis.reflexes.log_triage import _read_centralized_logs

        def raising_query(level=None, since=None, limit=None):
            raise RuntimeError("no such table: centralized_logs")

        assert _read_centralized_logs(query_fn=raising_query) == []


class TestRunCentralized:
    def test_seeded_error_row_produces_exactly_one_fix_card(self, tmp_path):
        from tools.genesis.reflexes import log_triage as mod

        def fake_query(level=None, since=None, limit=None):
            if level == "ERROR":
                return [{"id": "1", "ts": "t", "component": "billing",
                         "level": "ERROR", "message": "NullPointer in charge()",
                         "trace_id": "tr", "session_id": "se"}]
            return []

        with patch.object(mod, "_create_fix_card", return_value=True) as mock_fix:
            result = mod.run_centralized(
                {}, detector=_rule_detector(),
                query_fn=fake_query, seen_file=tmp_path / "clog_seen.json",
            )
        assert result["fix_cards_created"] == 1
        mock_fix.assert_called_once()
        # The card is task_type='fix' and carries the offending component.
        sig = mock_fix.call_args[0][0]
        assert sig["component"] == "billing"

    def test_duplicate_error_row_does_not_create_second_card(self, tmp_path):
        from tools.genesis.reflexes import log_triage as mod
        seen_file = tmp_path / "clog_seen.json"

        def fake_query(level=None, since=None, limit=None):
            if level == "ERROR":
                return [{"id": "1", "ts": "t", "component": "billing",
                         "level": "ERROR", "message": "NullPointer in charge()"}]
            return []

        # First cycle discovers the signature and opens one card.
        with patch.object(mod, "_create_fix_card", return_value=True) as m1:
            r1 = mod.run_centralized(
                {}, detector=_rule_detector(), query_fn=fake_query, seen_file=seen_file)
        assert r1["fix_cards_created"] == 1
        m1.assert_called_once()

        # Second cycle, same row — dedup-by-signature suppresses it.
        with patch.object(mod, "_create_fix_card", return_value=True) as m2:
            r2 = mod.run_centralized(
                {}, detector=_rule_detector(), query_fn=fake_query, seen_file=seen_file)
        assert r2["fix_cards_created"] == 0
        m2.assert_not_called()

    def test_multiple_duplicate_rows_collapse_to_one_card(self, tmp_path):
        from tools.genesis.reflexes import log_triage as mod

        def fake_query(level=None, since=None, limit=None):
            if level == "ERROR":
                return [
                    {"id": "1", "ts": "t", "component": "api", "level": "ERROR",
                     "message": "timeout calling upstream"},
                    {"id": "2", "ts": "t", "component": "api", "level": "ERROR",
                     "message": "timeout calling upstream"},
                ]
            return []

        with patch.object(mod, "_create_fix_card", return_value=True) as mock_fix:
            result = mod.run_centralized(
                {}, detector=_rule_detector(),
                query_fn=fake_query, seen_file=tmp_path / "clog_seen.json")
        assert result["fix_cards_created"] == 1
        mock_fix.assert_called_once()

    def test_fix_card_payload_is_task_type_fix_with_repro_query(self):
        """_create_fix_card POSTs task_type='fix' with a reproduce query + excerpt."""
        from tools.genesis.reflexes import log_triage as mod

        captured = {}

        class FakeResp:
            status = 201
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResp()

        sig = {"component": "ledger", "level": "ERROR",
               "message": "assert balance >= 0 failed", "trace_id": "tr",
               "_anomaly_priority": "high", "_anomaly_score": 0.65}
        with patch("urllib.request.urlopen", fake_urlopen):
            ok = mod._create_fix_card(sig)
        assert ok is True
        assert captured["body"]["task_type"] == "fix"
        assert "ledger" in captured["body"]["title"]
        desc = captured["body"]["description"]
        assert "tools/logging/log_query.py" in desc
        assert "ledger" in desc


class TestRunCombined:
    def test_run_merges_buildlog_and_centralized_counts(self, tmp_path):
        """run() triages both the build log and centralized_logs in one cycle.

        Forces the rule-based detector (``_disable_llm``) so no LLM/DB is hit.
        """
        from tools.genesis.reflexes import log_triage as mod
        mod._SEEN_SIGS_FILE = tmp_path / "build_seen.json"
        mod._CLOG_SEEN_SIGS_FILE = tmp_path / "clog_seen.json"
        build_log = tmp_path / "build.ndjson"
        _write_ndjson(build_log, [
            {"ts": "t", "level": "ERROR", "component": "builder",
             "message": "compile error", "returncode": 1, "failed": 1},
        ])

        def fake_centralized(since=None, limit=None, query_fn=None):
            return [{"level": "ERROR", "component": "runtime",
                     "message": "segfault", "event_type": "centralized_log",
                     "_source": "centralized_logs"}]

        with patch.object(mod, "_create_task", return_value=True) as mock_task, \
             patch.object(mod, "_create_fix_card", return_value=True) as mock_fix, \
             patch.object(mod, "_read_centralized_logs", side_effect=fake_centralized):
            result = mod.run({"build_log": str(build_log), "_disable_llm": True}, None)

        assert result["tasks_created"] == 1          # build-log bug card
        assert result["fix_cards_created"] == 1      # centralized_logs fix card
        mock_task.assert_called_once()
        mock_fix.assert_called_once()
        assert result["centralized"]["fix_cards_created"] == 1
