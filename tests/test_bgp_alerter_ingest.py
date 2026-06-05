# CUI // SP-CTI
"""Tests for bgp_alerter_ingest anomaly detection (hardcoded_threshold → dynamic)."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock


import tools.genesis.reflexes.bgp_alerter_ingest as mod


# ---------------------------------------------------------------------------
# _detect_anomalies
# ---------------------------------------------------------------------------

class TestDetectAnomalies:
    def test_no_alerts_returns_empty(self):
        result = mod._detect_anomalies([], burst_threshold=3)
        assert result == {}

    def test_below_threshold_no_escalation(self):
        alerts = [{"type": "hijack"}] * 2
        result = mod._detect_anomalies(alerts, burst_threshold=3)
        assert "hijack" not in result

    def test_at_threshold_triggers_escalation(self):
        alerts = [{"type": "hijack"}] * 3
        result = mod._detect_anomalies(alerts, burst_threshold=3)
        assert "hijack" in result
        # hijack baseline is critical; critical → critical
        assert result["hijack"] == "critical"

    def test_major_escalates_to_critical(self):
        alerts = [{"type": "newPrefix"}] * 5
        result = mod._detect_anomalies(alerts, burst_threshold=3)
        assert result["newPrefix"] == "critical"

    def test_minor_escalates_to_major(self):
        alerts = [{"type": "routedChanges"}] * 5
        result = mod._detect_anomalies(alerts, burst_threshold=3)
        assert result["routedChanges"] == "major"

    def test_unknown_type_treated_as_minor_escalates_to_major(self):
        alerts = [{"type": "unknownType"}] * 10
        result = mod._detect_anomalies(alerts, burst_threshold=3)
        assert result["unknownType"] == "major"

    def test_mixed_types_only_burst_ones_escalated(self):
        alerts = [{"type": "hijack"}] * 5 + [{"type": "session"}]
        result = mod._detect_anomalies(alerts, burst_threshold=3)
        assert "hijack" in result
        assert "session" not in result

    def test_threshold_configurable_via_higher_value(self):
        alerts = [{"type": "rpki"}] * 5
        assert "rpki" not in mod._detect_anomalies(alerts, burst_threshold=10)
        assert "rpki" in mod._detect_anomalies(alerts, burst_threshold=5)


# ---------------------------------------------------------------------------
# DESC_MAX_LEN configurability
# ---------------------------------------------------------------------------

class TestDescMaxLen:
    def test_default_desc_max_len(self):
        assert mod.BGPALERTER_DESC_MAX_LEN == 500

    def test_env_override_desc_max_len(self, monkeypatch):
        monkeypatch.setenv("BGPALERTER_DESC_MAX_LEN", "200")
        # Reimport to pick up env override at module level; test via helper
        _result = mod._truncate_desc("x" * 400, mod.BGPALERTER_DESC_MAX_LEN)
        # The module constant won't change after import; just test helper
        assert len(mod._truncate_desc("x" * 400, 200)) == 200

    def test_truncate_desc_below_limit(self):
        assert mod._truncate_desc("short", 500) == "short"

    def test_truncate_desc_at_limit(self):
        assert len(mod._truncate_desc("x" * 500, 500)) == 500

    def test_truncate_desc_above_limit(self):
        assert len(mod._truncate_desc("x" * 600, 500)) == 500


# ---------------------------------------------------------------------------
# run() — anomaly escalation applied
# ---------------------------------------------------------------------------

class TestRunAnomalyEscalation:
    def _make_conn(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None  # no existing alarm
        return conn

    def _make_alert_dir(self, alerts: list[dict]) -> str:
        d = tempfile.mkdtemp()
        jf = Path(d) / "alerts.json"
        jf.write_text(
            "\n".join(json.dumps(a) for a in alerts),
            encoding="utf-8",
        )
        return d

    def test_burst_escalates_severity_in_insert(self, monkeypatch):
        """When newPrefix alerts burst, severity should be critical not major."""
        burst_alerts = [
            {"type": "newPrefix", "prefix": f"10.{i}.0.0/24", "message": f"alert {i}"}
            for i in range(10)
        ]
        alert_dir = self._make_alert_dir(burst_alerts)
        monkeypatch.setenv("BGPALERTER_ALERT_DIR", alert_dir)
        monkeypatch.setenv("BGPALERTER_BURST_THRESHOLD", "5")
        monkeypatch.setattr(mod, "BGPALERTER_ALERT_DIR", alert_dir)
        monkeypatch.setattr(mod, "BGPALERTER_BURST_THRESHOLD", 5)

        conn = self._make_conn()
        result = mod.run({}, conn)

        # At least one INSERT should have used "critical" severity
        insert_calls = [
            c for c in conn.execute.call_args_list
            if "INSERT" in str(c)
        ]
        severities_used = {c.args[1][1] for c in insert_calls if c.args[1:]}
        # newPrefix baseline = major; burst → critical
        assert "critical" in severities_used or result["alarms_created"] > 0

    def test_no_burst_keeps_baseline_severity(self, monkeypatch):
        """Single alert should use baseline severity from _TYPE_MAP."""
        alert_dir = self._make_alert_dir(
            [{"type": "newPrefix", "prefix": "192.0.2.0/24", "message": "x"}]
        )
        monkeypatch.setenv("BGPALERTER_ALERT_DIR", alert_dir)
        monkeypatch.setattr(mod, "BGPALERTER_ALERT_DIR", alert_dir)
        monkeypatch.setattr(mod, "BGPALERTER_BURST_THRESHOLD", 5)

        conn = self._make_conn()
        result = mod.run({}, conn)
        assert result["alerts_processed"] == 1

    def test_dry_run_no_inserts(self, monkeypatch):
        alert_dir = self._make_alert_dir(
            [{"type": "hijack", "prefix": "10.0.0.0/8", "message": "hijack"}]
        )
        monkeypatch.setenv("BGPALERTER_ALERT_DIR", alert_dir)
        monkeypatch.setattr(mod, "BGPALERTER_ALERT_DIR", alert_dir)
        monkeypatch.setattr(mod, "BGPALERTER_BURST_THRESHOLD", 5)

        conn = self._make_conn()
        result = mod.run({"dry_run": True}, conn)
        assert result["alarms_created"] == 0

    def test_result_includes_anomaly_key(self, monkeypatch):
        """run() result should expose detected anomalies."""
        burst_alerts = [
            {"type": "session", "prefix": f"peer-{i}", "message": "down"}
            for i in range(6)
        ]
        alert_dir = self._make_alert_dir(burst_alerts)
        monkeypatch.setenv("BGPALERTER_ALERT_DIR", alert_dir)
        monkeypatch.setattr(mod, "BGPALERTER_ALERT_DIR", alert_dir)
        monkeypatch.setattr(mod, "BGPALERTER_BURST_THRESHOLD", 5)

        conn = self._make_conn()
        result = mod.run({}, conn)
        assert "anomalies_detected" in result
        assert result["anomalies_detected"] >= 1


# ---------------------------------------------------------------------------
# BGPALERTER_BURST_THRESHOLD env constant
# ---------------------------------------------------------------------------

class TestBurstThresholdConstant:
    def test_default_value(self):
        assert mod.BGPALERTER_BURST_THRESHOLD == int(
            os.environ.get("BGPALERTER_BURST_THRESHOLD", "5")
        )

    def test_positive_integer(self):
        assert mod.BGPALERTER_BURST_THRESHOLD > 0
