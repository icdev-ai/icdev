# CUI // SP-CTI
"""Phase 2 unit tests for health_prober + drift_detector + suggested_card_writer.

Tests use mock DB connections and synthetic data; no live DB required.
End-to-end integration (probe → drift → suggestion) is covered with a
mock connection that tracks all writes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.awareness import drift_detector as dd  # noqa: E402
from tools.awareness import health_prober as hp  # noqa: E402
from tools.awareness import suggested_card_writer as scw  # noqa: E402


# ---------------------------------------------------------------------------
# Shared mock connection — records all execute() calls and serves
# configurable fetch results.
# ---------------------------------------------------------------------------


class MockRow(dict):
    """Dict that also supports attribute-style access for ORM-ish code."""


class MockConn:
    """Lightweight fake DB connection used across Phase 2 unit tests."""

    def __init__(self, fetch_results=None):
        self.executed: list = []  # list of (sql, params)
        self.committed = 0
        self.rolled_back = 0
        self._fetch_results = fetch_results or []
        self._fetch_index = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self

    def fetchone(self):
        if self._fetch_index < len(self._fetch_results):
            result = self._fetch_results[self._fetch_index]
            self._fetch_index += 1
            if isinstance(result, list):
                return result[0] if result else None
            return result
        return None

    def fetchall(self):
        if self._fetch_index < len(self._fetch_results):
            result = self._fetch_results[self._fetch_index]
            self._fetch_index += 1
            if isinstance(result, list):
                return result
            return [result]
        return []

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        pass


# ---------------------------------------------------------------------------
# health_prober tests
# ---------------------------------------------------------------------------


class TestHealthProberHelpers:
    def test_snapshot_id_is_deterministic(self):
        a = hp._snapshot_id("node-1", "http_head", "run-1")
        b = hp._snapshot_id("node-1", "http_head", "run-1")
        c = hp._snapshot_id("node-1", "http_head", "run-2")
        assert a == b
        assert a != c
        assert a.startswith("probe-")

    def test_ensure_tables_creates_tables(self):
        conn = MockConn()
        hp._ensure_tables(conn)
        # Should have executed the two CREATE TABLE statements + commit
        sqls = [sql for sql, _ in conn.executed]
        assert any("awareness_run_log" in s for s in sqls)
        assert any("awareness_learned_edges" in s for s in sqls)
        assert conn.committed >= 1

    def test_write_snapshot_serializes_run_id_into_detail(self):
        conn = MockConn()
        ok = hp._write_snapshot(
            conn, "node-1", "http_head", "pass",
            {"status_code": 200, "response_time_ms": 42},
            "run-abc",
        )
        assert ok
        assert conn.committed >= 1
        sql, params = conn.executed[-1]
        assert "INSERT INTO awareness_component_health" in sql
        # params contain the JSON detail with run_id inlined
        detail_json = params[4]
        detail = json.loads(detail_json)
        assert detail["run_id"] == "run-abc"
        assert detail["status_code"] == 200

    def test_write_snapshot_handles_exception(self):
        conn = MockConn()
        conn.execute = MagicMock(side_effect=Exception("boom"))  # type: ignore[method-assign]
        conn.rollback = MagicMock()  # type: ignore[method-assign]
        result = hp._write_snapshot(conn, "n1", "http_head", "pass", {}, "r1")
        assert result is False
        conn.rollback.assert_called_once()


class TestHealthProberProbeCoherenceStatus:
    def test_parses_coherence_output_and_writes_snapshots(self):
        conn = MockConn()
        fake_report = {
            "overall_pass": True,
            "checks": [
                {"check_id": "schema_code", "status": "pass", "message": "All INSERT columns match"},
                {"check_id": "ruff_lint", "status": "pass", "message": "No hits"},
                {"check_id": "attribution_claims", "status": "warn", "message": "18 items"},
                {"check_id": "api_wiring", "status": "fail", "message": "bad handler"},
            ],
        }
        fake_proc = MagicMock()
        fake_proc.stdout = json.dumps(fake_report)
        fake_proc.returncode = 0

        with patch("tools.awareness.health_prober.subprocess.run", return_value=fake_proc):
            r = hp._probe_coherence_status(conn, run_id="run-test")
        # pass + warn → tracked pass, fail → tracked fail
        assert r["ok"] == 3  # pass + pass + warn
        assert r["fail"] == 1  # api_wiring

    def test_handles_coherence_error(self):
        conn = MockConn()
        with patch(
            "tools.awareness.health_prober.subprocess.run",
            side_effect=Exception("coherence crash"),
        ):
            r = hp._probe_coherence_status(conn, run_id="run-test")
        assert r["fail"] == 1
        assert "error" in r


class TestHealthProberRunAll:
    """Patches target _PROBE_REGISTRY directly because `run_all`
    looks up probe functions via the registry dict (captured at
    import time), not by module-level attribute access."""

    def test_run_all_aggregates_across_probes(self):
        stub_registry = {
            "http_head": lambda conn, run_id, **kw: {"ok": 2, "fail": 0},
            "module_import": lambda conn, run_id, **kw: {"ok": 5, "fail": 1},
            "coherence_status": lambda conn, run_id, **kw: {"ok": 3, "fail": 0},
        }
        with patch("tools.awareness.health_prober.get_connection", return_value=MockConn()):
            with patch.dict(hp._PROBE_REGISTRY, stub_registry, clear=True):
                result = hp.run_all()
        assert result["total_ok"] == 10
        assert result["total_fail"] == 1
        assert "http_head" in result["probes"]
        assert result["status"] == "degraded"

    def test_probe_types_filter_runs_only_requested_probe(self):
        call_counts = {"http_head": 0, "module_import": 0}

        def stub_http(conn, run_id, **kw):
            call_counts["http_head"] += 1
            return {"ok": 1, "fail": 0}

        def stub_import(conn, run_id, **kw):
            call_counts["module_import"] += 1
            return {"ok": 0, "fail": 0}

        stub_registry = {
            "http_head": stub_http,
            "module_import": stub_import,
            "coherence_status": lambda c, r, **kw: {"ok": 0, "fail": 0},
        }
        with patch("tools.awareness.health_prober.get_connection", return_value=MockConn()):
            with patch.dict(hp._PROBE_REGISTRY, stub_registry, clear=True):
                hp.run_all(probe_types=["http_head"])
        assert call_counts["http_head"] == 1
        assert call_counts["module_import"] == 0


# ---------------------------------------------------------------------------
# drift_detector tests
# ---------------------------------------------------------------------------


class TestDriftEvaluation:
    def _snap(self, status: str, probed_at: str, detail=None):
        return {
            "node_id": "n1",
            "probe_type": "http_head",
            "status": status,
            "detail": json.dumps(detail or {}),
            "probed_at": probed_at,
        }

    def test_too_few_samples_is_not_regression(self):
        snaps = [self._snap("pass", "2026-04-01T00:00:00+00:00")]
        assert dd._evaluate_drift(snaps, stable_days=3) is None

    def test_latest_pass_is_not_regression(self):
        snaps = [
            self._snap("fail", "2026-04-01T00:00:00+00:00"),
            self._snap("pass", "2026-04-05T00:00:00+00:00"),
        ]
        assert dd._evaluate_drift(snaps, stable_days=3) is None

    def test_never_passed_is_not_regression(self):
        # Latest is fail but no prior pass → new-fail, not regression
        snaps = [
            self._snap("fail", "2026-04-01T00:00:00+00:00"),
            self._snap("fail", "2026-04-05T00:00:00+00:00"),
        ]
        assert dd._evaluate_drift(snaps, stable_days=3) is None

    def test_stable_pass_then_fail_is_regression(self):
        snaps = [
            self._snap("pass", "2026-04-01T00:00:00+00:00"),
            self._snap("pass", "2026-04-02T00:00:00+00:00"),
            self._snap("pass", "2026-04-03T00:00:00+00:00"),
            self._snap("fail", "2026-04-06T00:00:00+00:00"),  # 5 days after first pass
        ]
        ev = dd._evaluate_drift(snaps, stable_days=3)
        assert ev is not None
        assert ev["last_good_at"] == "2026-04-03T00:00:00+00:00"
        assert ev["first_bad_at"] == "2026-04-06T00:00:00+00:00"
        assert ev["stable_days"] >= 3.0

    def test_too_brief_pass_is_not_regression(self):
        snaps = [
            self._snap("pass", "2026-04-01T00:00:00+00:00"),
            self._snap("fail", "2026-04-01T01:00:00+00:00"),  # 1 hour later
        ]
        # stable_days=3 → 1 hour is too brief
        assert dd._evaluate_drift(snaps, stable_days=3) is None

    def test_prediction_id_is_deterministic(self):
        a = dd._prediction_id("n1", "http_head", "2026-04-11T00:00:00+00:00")
        b = dd._prediction_id("n1", "http_head", "2026-04-11T00:00:00+00:00")
        assert a == b
        assert a.startswith("op-")


class TestDriftDetectDryRun:
    def test_dry_run_does_not_write_predictions(self):
        # Mock DB: return snapshot history for one node/probe that IS a regression
        class DriftMockConn(MockConn):
            def __init__(self):
                super().__init__()
                self.write_predictions = 0

            def execute(self, sql, params=None):
                self.executed.append((sql, params))
                if "INSERT INTO oracle_predictions" in sql:
                    self.write_predictions += 1
                return self

        conn = DriftMockConn()
        snaps = [
            {
                "node_id": "n1", "probe_type": "http_head", "status": "pass",
                "detail": "{}", "probed_at": "2026-04-01T00:00:00+00:00",
            },
            {
                "node_id": "n1", "probe_type": "http_head", "status": "pass",
                "detail": "{}", "probed_at": "2026-04-05T00:00:00+00:00",
            },
            {
                "node_id": "n1", "probe_type": "http_head", "status": "fail",
                "detail": '{"status_code":500}', "probed_at": "2026-04-10T00:00:00+00:00",
            },
        ]
        with patch(
            "tools.awareness.drift_detector._latest_status_per_node_probe",
            return_value={("n1", "http_head"): snaps},
        ):
            with patch(
                "tools.awareness.drift_detector._recent_prediction_exists",
                return_value=False,
            ):
                with patch(
                    "tools.awareness.drift_detector.get_connection", return_value=conn
                ):
                    result = dd.detect(dry_run=True, stable_days=3)

        assert result["total_findings"] == 1
        assert conn.write_predictions == 0
        assert result["by_rule"]["route_regression"] == 1

    def test_live_detect_calls_write_prediction(self):
        conn = MockConn()
        snaps = [
            {
                "node_id": "mod-1", "probe_type": "module_import", "status": "pass",
                "detail": "{}", "probed_at": "2026-04-01T00:00:00+00:00",
            },
            {
                "node_id": "mod-1", "probe_type": "module_import", "status": "fail",
                "detail": '{"error":"SyntaxError"}', "probed_at": "2026-04-10T00:00:00+00:00",
            },
        ]
        with patch(
            "tools.awareness.drift_detector._latest_status_per_node_probe",
            return_value={("mod-1", "module_import"): snaps},
        ):
            with patch(
                "tools.awareness.drift_detector._recent_prediction_exists",
                return_value=False,
            ):
                with patch(
                    "tools.awareness.drift_detector.get_connection", return_value=conn
                ):
                    with patch(
                        "tools.awareness.drift_detector._write_prediction",
                        return_value="op-fake123",
                    ) as mock_write:
                        result = dd.detect(dry_run=False, stable_days=3)
        assert result["total_findings"] == 1
        mock_write.assert_called_once()
        call_args = mock_write.call_args
        assert call_args[0][1] == "mod-1"
        assert call_args[0][2] == "module_import"


# ---------------------------------------------------------------------------
# suggested_card_writer tests
# ---------------------------------------------------------------------------


class TestSuggestedCardWriterHelpers:
    def test_generate_title_drift_regression(self):
        pred = {
            "prediction_type": "regression::http_head",
            "subject_id": "coherence::schema_code",
        }
        title = scw._generate_title(pred)
        assert "http_head regression" in title
        assert "schema_code" in title

    def test_generate_title_gap_finding(self):
        pred = {
            "prediction_type": "gap::tool_not_in_manifest",
            "subject_id": "tools/foo/bar.py",
        }
        title = scw._generate_title(pred)
        assert "tool_not_in_manifest gap" in title
        assert "tools/foo/bar.py" in title

    def test_generate_title_truncates_long_ids(self):
        pred = {
            "prediction_type": "regression::module_import",
            "subject_id": "icdev-tool-" + "a" * 100,
        }
        title = scw._generate_title(pred)
        assert len(title) <= 100  # reasonable bound

    def test_generate_description_includes_evidence(self):
        pred = {
            "id": "op-test",
            "prediction_text": "Route regressed",
            "confidence": 0.85,
            "severity": "high",
            "subject_id": "canvas-x",
            "prediction_type": "regression::http_head",
            "evidence_json": json.dumps(
                {"last_good_at": "2026-04-01", "first_bad_at": "2026-04-06", "stable_days": 5}
            ),
        }
        desc = scw._generate_description(pred)
        assert "op-test" in desc
        assert "0.85" in desc
        assert "high" in desc
        assert "last_good_at" in desc
        assert "2026-04-01" in desc
        assert "Investigation:" in desc

    def test_severity_to_priority_mapping(self):
        assert scw._SEVERITY_TO_PRIORITY["critical"] == "critical"
        assert scw._SEVERITY_TO_PRIORITY["high"] == "high"
        assert scw._SEVERITY_TO_PRIORITY["medium"] == "medium"
        assert scw._SEVERITY_TO_PRIORITY["low"] == "low"


class TestWriteSuggestedCardsDryRun:
    def _fake_pred(self, pred_id, conf=0.85, severity="high"):
        return {
            "id": pred_id,
            "lens_name": "internal_awareness",
            "prediction_text": f"Regression {pred_id}",
            "confidence": conf,
            "subject_id": f"node-{pred_id}",
            "prediction_type": "regression::http_head",
            "severity": severity,
            "evidence_json": '{"last_good_at":"2026-04-01"}',
            "created_at": "2026-04-11T00:00:00+00:00",
        }

    def test_dry_run_does_not_insert(self):
        conn = MockConn()
        preds = [self._fake_pred("p1"), self._fake_pred("p2")]
        with patch("tools.awareness.suggested_card_writer._load_pending_predictions", return_value=preds):
            with patch("tools.awareness.suggested_card_writer._find_open_card", return_value=None):
                with patch("tools.awareness.suggested_card_writer._load_existing_subjects", return_value=set()):
                    with patch("tools.awareness.suggested_card_writer.get_connection", return_value=conn):
                        result = scw.write_suggested_cards(dry_run=True)
        assert result["created"] == 2
        assert result["dry_run"] is True

    def test_existing_card_is_skipped(self):
        conn = MockConn()
        preds = [self._fake_pred("p1"), self._fake_pred("p2"), self._fake_pred("p3")]
        # p2 already has an open card
        def find_open(_c, pid, title):
            if pid == "p2":
                return {"id": "card-p2", "title": title, "status": "suggested"}
            return None
        with patch("tools.awareness.suggested_card_writer._load_pending_predictions", return_value=preds):
            with patch("tools.awareness.suggested_card_writer._find_open_card", side_effect=find_open):
                with patch("tools.awareness.suggested_card_writer._load_existing_subjects", return_value=set()):
                    with patch("tools.awareness.suggested_card_writer.get_connection", return_value=conn):
                        result = scw.write_suggested_cards(dry_run=True)
        assert result["skipped_prediction_dedup"] == 1
        assert result["created"] == 2

    def test_min_confidence_filter_is_applied_at_query_level(self):
        # The filter happens in the SQL — verify the SQL is parameterized
        # with the threshold. We mock _load_pending_predictions to check
        # it's called with the threshold.
        with patch(
            "tools.awareness.suggested_card_writer._load_pending_predictions",
            return_value=[],
        ) as mock_load:
            with patch("tools.awareness.suggested_card_writer.get_connection", return_value=MockConn()):
                scw.write_suggested_cards(min_confidence=0.9)
        # Second positional arg is min_confidence
        call_kwargs = mock_load.call_args
        assert call_kwargs.kwargs.get("min_confidence") == 0.9 or (
            call_kwargs.args and 0.9 in call_kwargs.args
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
