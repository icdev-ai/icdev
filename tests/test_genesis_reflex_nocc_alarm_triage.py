# CUI // SP-CTI
"""Tests for tools/genesis/reflexes/nocc_alarm_triage.py — adaptive anomaly detection."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# _compute_dynamic_threshold
# ---------------------------------------------------------------------------

class TestComputeDynamicThreshold:
    def setup_method(self):
        from tools.genesis.reflexes.nocc_alarm_triage import _compute_dynamic_threshold
        self.compute = _compute_dynamic_threshold

    def test_empty_history_returns_static(self):
        threshold, method = self.compute([])
        assert method == "static"
        assert threshold == 5  # _FALLBACK_STORM_MIN_ALARMS

    def test_sparse_history_returns_static(self):
        threshold, method = self.compute([3, 4, 2])
        assert method == "static"

    def test_exactly_min_history_uses_statistical(self):
        # 10 windows of 2 alarms each → mean=2, std=0 → stable path
        counts = [2] * 10
        threshold, method = self.compute(counts)
        assert method == "statistical_stable"

    def test_statistical_threshold_exceeds_mean(self):
        # mean=5, varying std — threshold should be above mean
        counts = [3, 4, 5, 6, 7, 4, 5, 6, 3, 7, 5, 4]
        threshold, method = self.compute(counts)
        assert method == "statistical"
        mean = sum(counts) / len(counts)
        assert threshold > mean

    def test_statistical_stable_uses_buffer(self):
        # All same value → std=0 → stable path with +5 buffer
        counts = [3] * 15
        threshold, method = self.compute(counts)
        assert method == "statistical_stable"
        assert threshold >= 3 + 5  # mean + _FALLBACK

    def test_custom_fallback_respected(self):
        threshold, method = self.compute([], fallback=10)
        assert threshold == 10
        assert method == "static"

    def test_minimum_threshold_never_below_two(self):
        # Very low alarm history: mean~1, std small → threshold should be >= 2
        counts = [0, 1, 1, 0, 1, 1, 0, 1, 2, 1, 0, 1]
        threshold, method = self.compute(counts)
        assert threshold >= 2


# ---------------------------------------------------------------------------
# _fetch_device_alarm_history
# ---------------------------------------------------------------------------

class TestFetchDeviceAlarmHistory:
    def _make_conn(self, rows):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = rows
        conn.execute.return_value = cursor
        return conn

    def test_empty_db_returns_empty(self):
        from tools.genesis.reflexes.nocc_alarm_triage import _fetch_device_alarm_history
        conn = self._make_conn([])
        result = _fetch_device_alarm_history(conn, "router-1", 15, 7)
        assert result == []

    def test_single_window_returns_one_bucket(self):
        from tools.genesis.reflexes.nocc_alarm_triage import _fetch_device_alarm_history
        # Three alarms all in the same 15-minute bucket
        ts = "2026-01-01T10:00:00+00:00"
        conn = self._make_conn([(ts,), (ts,), (ts,)])
        result = _fetch_device_alarm_history(conn, "router-1", 15, 7)
        assert len(result) == 1
        assert result[0] == 3

    def test_alarms_in_different_windows_produce_separate_buckets(self):
        from tools.genesis.reflexes.nocc_alarm_triage import _fetch_device_alarm_history
        # Two alarms 30 minutes apart → 2 distinct buckets
        rows = [
            ("2026-01-01T10:00:00+00:00",),
            ("2026-01-01T10:31:00+00:00",),
        ]
        conn = self._make_conn(rows)
        result = _fetch_device_alarm_history(conn, "router-1", 15, 7)
        assert len(result) == 2
        assert all(c == 1 for c in result)

    def test_db_exception_returns_empty(self):
        from tools.genesis.reflexes.nocc_alarm_triage import _fetch_device_alarm_history
        conn = MagicMock()
        conn.execute.side_effect = Exception("table not found")
        result = _fetch_device_alarm_history(conn, "router-1", 15, 7)
        assert result == []

    def test_dict_row_format_handled(self):
        from tools.genesis.reflexes.nocc_alarm_triage import _fetch_device_alarm_history
        row = MagicMock()
        row.__getitem__ = lambda self, k: "2026-01-01T10:00:00+00:00"
        row.keys = lambda: ["first_seen"]
        conn = self._make_conn([row, row])
        result = _fetch_device_alarm_history(conn, "device-x", 15, 7)
        assert len(result) == 1
        assert result[0] == 2


# ---------------------------------------------------------------------------
# _llm_assess_storm
# ---------------------------------------------------------------------------

class TestLlmAssessStorm:
    def _make_alarms(self, n=6):
        return [
            {"severity": "major", "alarm_type": "link_down", "description": f"alarm {i}"}
            for i in range(n)
        ]

    def test_returns_true_when_llm_says_is_storm(self):
        from tools.genesis.reflexes.nocc_alarm_triage import _llm_assess_storm
        mock_response = MagicMock()
        mock_response.content = '{"is_storm": true, "rationale": "diverse alarms"}'
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response

        with patch("tools.llm.router.LLMRouter", return_value=mock_router_instance):
            with patch("tools.llm.provider.LLMRequest", return_value=MagicMock()):
                result = _llm_assess_storm(self._make_alarms(), "router-a", 6, 8)
        assert result is True

    def test_returns_false_when_llm_says_not_storm(self):
        from tools.genesis.reflexes.nocc_alarm_triage import _llm_assess_storm
        mock_response = MagicMock()
        mock_response.content = '{"is_storm": false, "rationale": "repetitive flap"}'
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response

        with patch("tools.llm.router.LLMRouter", return_value=mock_router_instance):
            with patch("tools.llm.provider.LLMRequest", return_value=MagicMock()):
                result = _llm_assess_storm(self._make_alarms(), "router-a", 6, 8)
        assert result is False

    def test_returns_none_on_import_error(self):
        from tools.genesis.reflexes.nocc_alarm_triage import _llm_assess_storm
        with patch.dict("sys.modules", {"tools.llm.router": None, "tools.llm.provider": None}):
            result = _llm_assess_storm(self._make_alarms(), "router-x", 6, 8)
        assert result is None

    def test_returns_none_on_json_parse_error(self):
        from tools.genesis.reflexes.nocc_alarm_triage import _llm_assess_storm
        mock_response = MagicMock()
        mock_response.content = "not valid json"
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response

        with patch("tools.llm.router.LLMRouter", return_value=mock_router_instance):
            with patch("tools.llm.provider.LLMRequest", return_value=MagicMock()):
                result = _llm_assess_storm(self._make_alarms(), "router-x", 6, 8)
        assert result is None


# ---------------------------------------------------------------------------
# _run_triage integration (mocked DB)
# ---------------------------------------------------------------------------

class TestRunTriage:
    def _make_alarm_row(self, device="router-1", severity="major"):
        return (1, device, "ckt-001", "AT&T", severity, "link_down", "Link down", "2026-01-01T10:00:00+00:00")

    def _make_conn(self, alarm_rows, history_rows=None, incident_rows=None):
        conn = MagicMock()
        history_rows = history_rows or []
        incident_rows = incident_rows or []
        call_count = [0]

        def execute_side_effect(sql, params=()):
            call_count[0] += 1
            cursor = MagicMock()
            # Active alarm fetch: "cleared = FALSE" or "cleared = 0" in WHERE clause
            if "cleared = FALSE" in sql or "cleared = 0" in sql:
                cursor.fetchall.return_value = alarm_rows
            # History fetch: device_name equality filter (WHERE device_name = ...)
            elif "device_name = %s" in sql or "device_name = ?" in sql:
                cursor.fetchall.return_value = history_rows
            # Incident check
            elif "noc_incidents" in sql and "SELECT id" in sql:
                cursor.fetchone.return_value = incident_rows[0] if incident_rows else None
            # Incident insert or other
            else:
                cursor.fetchall.return_value = []
                cursor.fetchone.return_value = None
            return cursor

        conn.execute.side_effect = execute_side_effect
        return conn

    def test_no_alarms_produces_empty_result(self):
        from tools.genesis.reflexes.nocc_alarm_triage import _run_triage
        result = {"active_alarms": 0, "storms_detected": 0, "incidents_created": 0,
                  "events_published": 0, "detection_methods": {"static": 0, "statistical": 0,
                  "statistical_stable": 0, "llm": 0}, "errors": []}
        conn = self._make_conn([])
        _run_triage(conn, dry_run=False, result=result)
        assert result["active_alarms"] == 0
        assert result["storms_detected"] == 0

    def test_five_alarms_with_no_history_creates_incident_in_dry_run(self):
        from tools.genesis.reflexes.nocc_alarm_triage import _run_triage
        alarms = [self._make_alarm_row() for _ in range(5)]
        result = {"active_alarms": 0, "storms_detected": 0, "incidents_created": 0,
                  "events_published": 0, "detection_methods": {"static": 0, "statistical": 0,
                  "statistical_stable": 0, "llm": 0}, "errors": []}
        conn = self._make_conn(alarms, history_rows=[])
        _run_triage(conn, dry_run=True, result=result)
        assert result["storms_detected"] == 1
        assert result["incidents_created"] == 1
        assert result["detection_methods"]["static"] == 1

    def test_four_alarms_with_no_history_not_a_storm(self):
        from tools.genesis.reflexes.nocc_alarm_triage import _run_triage
        alarms = [self._make_alarm_row() for _ in range(4)]
        result = {"active_alarms": 0, "storms_detected": 0, "incidents_created": 0,
                  "events_published": 0, "detection_methods": {"static": 0, "statistical": 0,
                  "statistical_stable": 0, "llm": 0}, "errors": []}
        conn = self._make_conn(alarms, history_rows=[])
        _run_triage(conn, dry_run=True, result=result)
        assert result["storms_detected"] == 0
        assert result["incidents_created"] == 0

    def test_statistical_threshold_suppresses_low_count(self):
        """Stable device with mean=20 alarms/window → threshold=25; count=10 is not a storm.

        Static threshold (5) would flag 10 alarms; adaptive threshold suppresses the false positive.
        10 < borderline_floor (int(25*0.7)=17), so no LLM call needed either.
        """
        from tools.genesis.reflexes.nocc_alarm_triage import _run_triage
        alarms = [self._make_alarm_row() for _ in range(10)]
        # 15 windows × 20 alarms each = 300 history rows → stable, mean=20, threshold=25
        history_rows = []
        for window in range(15):
            base_hour = window + 1
            for minute in range(20):  # 20 alarms in first 20 min of each hour → same bucket
                history_rows.append((f"2026-01-01T{base_hour:02d}:{minute:02d}:00+00:00",))
        result = {"active_alarms": 0, "storms_detected": 0, "incidents_created": 0,
                  "events_published": 0, "detection_methods": {"static": 0, "statistical": 0,
                  "statistical_stable": 0, "llm": 0}, "errors": []}
        conn = self._make_conn(alarms, history_rows=history_rows)
        _run_triage(conn, dry_run=True, result=result)
        # count=10 < borderline_floor=17 → clearly not a storm despite exceeding static threshold
        assert result["storms_detected"] == 0

    def test_existing_incident_skips_creation(self):
        from tools.genesis.reflexes.nocc_alarm_triage import _run_triage
        alarms = [self._make_alarm_row() for _ in range(5)]
        existing = (42,)  # existing incident row
        result = {"active_alarms": 0, "storms_detected": 0, "incidents_created": 0,
                  "events_published": 0, "detection_methods": {"static": 0, "statistical": 0,
                  "statistical_stable": 0, "llm": 0}, "errors": []}
        conn = self._make_conn(alarms, history_rows=[], incident_rows=[existing])
        _run_triage(conn, dry_run=False, result=result)
        assert result["storms_detected"] == 1
        assert result["incidents_created"] == 0

    def test_alarm_fetch_error_appends_error(self):
        from tools.genesis.reflexes.nocc_alarm_triage import _run_triage
        conn = MagicMock()
        conn.execute.side_effect = Exception("connection refused")
        result = {"active_alarms": 0, "storms_detected": 0, "incidents_created": 0,
                  "events_published": 0, "detection_methods": {"static": 0, "statistical": 0,
                  "statistical_stable": 0, "llm": 0}, "errors": []}
        _run_triage(conn, dry_run=False, result=result)
        assert any("alarm_fetch" in e for e in result["errors"])

    def test_detection_method_recorded_in_result(self):
        from tools.genesis.reflexes.nocc_alarm_triage import _run_triage
        alarms = [self._make_alarm_row() for _ in range(5)]
        result = {"active_alarms": 0, "storms_detected": 0, "incidents_created": 0,
                  "events_published": 0, "detection_methods": {"static": 0, "statistical": 0,
                  "statistical_stable": 0, "llm": 0}, "errors": []}
        conn = self._make_conn(alarms, history_rows=[])
        _run_triage(conn, dry_run=True, result=result)
        total_methods = sum(result["detection_methods"].values())
        assert total_methods == result["storms_detected"]


# ---------------------------------------------------------------------------
# _worst_severity
# ---------------------------------------------------------------------------

class TestWorstSeverity:
    def setup_method(self):
        from tools.genesis.reflexes.nocc_alarm_triage import _worst_severity
        self.worst = _worst_severity

    def test_critical_wins(self):
        assert self.worst(["warning", "critical", "minor"]) == "critical"

    def test_major_over_minor(self):
        assert self.worst(["minor", "major", "info"]) == "major"

    def test_empty_returns_info(self):
        assert self.worst([]) == "info"

    def test_unknown_severity_returns_info(self):
        assert self.worst(["foo", "bar"]) == "info"
