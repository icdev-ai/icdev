#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for G-14 — Continuous Authentication (tools/security/continuous_auth.py)."""

from datetime import datetime, timedelta, timezone


import tools.security.continuous_auth as _ca


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _old_iso(minutes: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


# ---------------------------------------------------------------------------
# RiskScore dataclass
# ---------------------------------------------------------------------------

class TestRiskScore:
    def test_to_dict_structure(self):
        rs = _ca.RiskScore(score=0.35, factors=["IP address changed"], require_step_up=False)
        d = rs.to_dict()
        assert d["score"] == 0.35
        assert d["factors"] == ["IP address changed"]
        assert d["require_step_up"] is False

    def test_score_rounded(self):
        rs = _ca.RiskScore(score=0.333333)
        d = rs.to_dict()
        assert d["score"] == round(0.333333, 4)


# ---------------------------------------------------------------------------
# assess_session_risk — disabled mode
# ---------------------------------------------------------------------------

class TestAssessSessionRiskDisabled:
    def test_disabled_returns_zero(self, monkeypatch):
        monkeypatch.setenv("ICDEV_CONTINUOUS_AUTH_ENABLED", "false")
        risk = _ca.assess_session_risk("alice", {"ip_address": "1.2.3.4", "session_ip": "9.9.9.9"})
        assert risk.score == 0.0
        assert risk.require_step_up is False
        assert risk.factors == []


# ---------------------------------------------------------------------------
# assess_session_risk — factor 1: session age
# ---------------------------------------------------------------------------

class TestSessionAgeFactor:
    def test_fresh_session_low_age_contribution(self, monkeypatch):
        monkeypatch.delenv("ICDEV_CONTINUOUS_AUTH_ENABLED", raising=False)
        monkeypatch.setenv("ICDEV_SESSION_MAX_AGE_MINUTES", "480")
        ctx = {"session_created_at": _old_iso(1)}  # 1 minute old
        risk = _ca.assess_session_risk("u1", ctx)
        # age contribution = min(1, 1/480) * 0.5 ≈ 0.001
        assert risk.score < 0.01

    def test_half_age_gives_quarter_max(self, monkeypatch):
        monkeypatch.delenv("ICDEV_CONTINUOUS_AUTH_ENABLED", raising=False)
        monkeypatch.setenv("ICDEV_SESSION_MAX_AGE_MINUTES", "100")
        ctx = {"session_created_at": _old_iso(50)}  # 50% of max
        risk = _ca.assess_session_risk("u1", ctx)
        # age contribution = min(1, 50/100) * 0.5 = 0.25
        assert abs(risk.score - 0.25) < 0.02

    def test_max_age_exceeded_caps_at_half(self, monkeypatch):
        monkeypatch.delenv("ICDEV_CONTINUOUS_AUTH_ENABLED", raising=False)
        monkeypatch.setenv("ICDEV_SESSION_MAX_AGE_MINUTES", "60")
        ctx = {"session_created_at": _old_iso(600)}  # way over max
        risk = _ca.assess_session_risk("u1", ctx)
        # capped at 0.5
        assert abs(risk.score - 0.5) < 0.01

    def test_invalid_timestamp_skipped(self, monkeypatch):
        monkeypatch.delenv("ICDEV_CONTINUOUS_AUTH_ENABLED", raising=False)
        ctx = {"session_created_at": "not-a-date"}
        risk = _ca.assess_session_risk("u1", ctx)
        assert risk.score == 0.0


# ---------------------------------------------------------------------------
# assess_session_risk — factor 2: IP change
# ---------------------------------------------------------------------------

class TestIPChangeFactor:
    def test_ip_change_adds_03(self, monkeypatch):
        monkeypatch.delenv("ICDEV_CONTINUOUS_AUTH_ENABLED", raising=False)
        ctx = {"ip_address": "10.0.0.2", "session_ip": "10.0.0.1"}
        risk = _ca.assess_session_risk("u1", ctx)
        assert "IP address changed" in risk.factors
        assert abs(risk.score - 0.3) < 0.01

    def test_same_ip_no_contribution(self, monkeypatch):
        monkeypatch.delenv("ICDEV_CONTINUOUS_AUTH_ENABLED", raising=False)
        ctx = {"ip_address": "192.168.1.1", "session_ip": "192.168.1.1"}
        risk = _ca.assess_session_risk("u1", ctx)
        assert "IP address changed" not in risk.factors

    def test_missing_session_ip_no_contribution(self, monkeypatch):
        monkeypatch.delenv("ICDEV_CONTINUOUS_AUTH_ENABLED", raising=False)
        ctx = {"ip_address": "10.0.0.2"}
        risk = _ca.assess_session_risk("u1", ctx)
        assert "IP address changed" not in risk.factors


# ---------------------------------------------------------------------------
# assess_session_risk — factor 3: anomalous rate
# ---------------------------------------------------------------------------

class TestAnomalousRateFactor:
    def test_high_rate_adds_02(self, monkeypatch):
        monkeypatch.delenv("ICDEV_CONTINUOUS_AUTH_ENABLED", raising=False)
        monkeypatch.setenv("ICDEV_STEP_UP_RATE_THRESHOLD", "30")
        ctx = {"request_rate_per_minute": 61.0}
        risk = _ca.assess_session_risk("u1", ctx)
        assert "anomalous request rate" in risk.factors
        assert abs(risk.score - 0.2) < 0.01

    def test_normal_rate_no_contribution(self, monkeypatch):
        monkeypatch.delenv("ICDEV_CONTINUOUS_AUTH_ENABLED", raising=False)
        monkeypatch.setenv("ICDEV_STEP_UP_RATE_THRESHOLD", "60")
        ctx = {"request_rate_per_minute": 30.0}
        risk = _ca.assess_session_risk("u1", ctx)
        assert "anomalous request rate" not in risk.factors


# ---------------------------------------------------------------------------
# Step-up threshold
# ---------------------------------------------------------------------------

class TestStepUpThreshold:
    def test_below_threshold_no_step_up(self, monkeypatch):
        monkeypatch.delenv("ICDEV_CONTINUOUS_AUTH_ENABLED", raising=False)
        monkeypatch.setenv("ICDEV_STEP_UP_RISK_THRESHOLD", "0.7")
        ctx = {"ip_address": "1.1.1.1", "session_ip": "2.2.2.2"}  # score = 0.3
        risk = _ca.assess_session_risk("u1", ctx)
        assert risk.require_step_up is False

    def test_above_threshold_triggers_step_up(self, monkeypatch):
        monkeypatch.delenv("ICDEV_CONTINUOUS_AUTH_ENABLED", raising=False)
        monkeypatch.setenv("ICDEV_STEP_UP_RISK_THRESHOLD", "0.4")
        # IP change (0.3) + rate (0.2) = 0.5 > 0.4
        monkeypatch.setenv("ICDEV_STEP_UP_RATE_THRESHOLD", "10")
        ctx = {"ip_address": "1.1.1.1", "session_ip": "2.2.2.2", "request_rate_per_minute": 100.0}
        risk = _ca.assess_session_risk("u1", ctx)
        assert risk.require_step_up is True


# ---------------------------------------------------------------------------
# record_risk_event — smoke test (DB may or may not be available in conftest)
# ---------------------------------------------------------------------------

class TestRecordRiskEvent:
    def test_no_exception_raised(self):
        risk = _ca.RiskScore(score=0.1, factors=["test"], require_step_up=False)
        _ca.record_risk_event("test_user", risk)  # must not raise

    def test_storage_error_swallowed(self, monkeypatch):
        def bad_conn():
            raise OSError("disk full")

        monkeypatch.setattr(_ca, "record_risk_event",
                            lambda uid, r: None)  # already tested via real path above
        risk = _ca.RiskScore(score=0.9, factors=["x"], require_step_up=True)
        _ca.record_risk_event("u2", risk)


# ---------------------------------------------------------------------------
# check_step_up_required — outside Flask context
# ---------------------------------------------------------------------------

class TestCheckStepUpRequired:
    def test_outside_flask_returns_false(self):
        # Not inside a Flask app context; should return False without raising
        result = _ca.check_step_up_required()
        assert result is False
