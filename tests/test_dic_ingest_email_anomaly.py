"""Tests for _ai_email_ingestion_anomaly_detection() — email-specific anomaly detection.

aiify-opp-148: hardcoded_threshold -> anomaly_detection in email ingestion.
Analog of paperless_mail/serialisers.py MailAccount/MailRule validators.
Replaces static field limits (max age, attachment count, subject length) with
configurable thresholds that trigger LLM scoring only when a pre-check fires.
"""
from __future__ import annotations

import importlib
import json

import pytest

ingest = importlib.import_module("tools.document_intelligence.ingest_orchestrator")
router_mod = importlib.import_module("tools.llm.router")


# ── Stub LLM ─────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, content: str):
        self.content = content


class _FakeRouter:
    last_fn: str | None = None
    last_req = None
    _content: str = json.dumps({"score": 0.6, "verdict": "suspicious", "reason": "high attachment count"})

    def __init__(self, *a, **k):
        pass

    def invoke(self, fn, req):
        _FakeRouter.last_fn = fn
        _FakeRouter.last_req = req
        return _Resp(self._content)


class _BrokenRouter:
    def __init__(self, *a, **k):
        pass

    def invoke(self, fn, req):
        raise RuntimeError("LLM unavailable")


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeRouter.last_fn = None
    _FakeRouter.last_req = None
    _FakeRouter._content = json.dumps(
        {"score": 0.6, "verdict": "suspicious", "reason": "high attachment count"}
    )
    yield


# ── Normal emails return None (no LLM call) ──────────────────────────────────

def test_normal_email_returns_none():
    corr = {"from_name": "Alice", "from_email": "alice@example.com", "subject": "Hello", "to": []}
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence=corr,
        attachment_count=2,
        attachment_size_bytes=1024 * 1024,  # 1 MB
        email_age_days=10.0,
    )
    assert result is None


def test_no_attachments_no_signal():
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence=None,
        attachment_count=0,
        attachment_size_bytes=0,
        email_age_days=None,
    )
    assert result is None


# ── Pre-check 1: high attachment count ───────────────────────────────────────

def test_high_attachment_count_fires_signal(monkeypatch):
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence=None,
        attachment_count=11,  # default ceiling = 10
        attachment_size_bytes=0,
    )
    assert result is not None
    assert any("high_attachment_count" in s for s in result["signals"])


def test_attachment_count_at_ceiling_no_signal():
    # Exactly at ceiling (10) is NOT over — no signal.
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence=None,
        attachment_count=10,
        attachment_size_bytes=0,
    )
    assert result is None


def test_attachment_count_ceiling_configurable(monkeypatch):
    monkeypatch.setattr(ingest, "_EMAIL_ANOMALY_MAX_ATTACHMENT_COUNT", 3)
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence=None,
        attachment_count=4,
        attachment_size_bytes=0,
    )
    assert result is not None
    assert any("high_attachment_count:4>3" in s for s in result["signals"])


# ── Pre-check 2: large total attachment size ──────────────────────────────────

def test_large_attachments_fires_signal(monkeypatch):
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    big = int(26 * 1024 * 1024)  # 26 MB, default ceiling = 25 MB
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence=None,
        attachment_count=1,
        attachment_size_bytes=big,
    )
    assert result is not None
    assert any("large_attachments" in s for s in result["signals"])


def test_attachment_size_at_ceiling_no_signal():
    exactly = int(25 * 1024 * 1024)  # exactly 25 MB — not over
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence=None,
        attachment_count=1,
        attachment_size_bytes=exactly,
    )
    assert result is None


def test_attachment_size_ceiling_configurable(monkeypatch):
    monkeypatch.setattr(ingest, "_EMAIL_ANOMALY_MAX_ATTACHMENT_SIZE_MB", 5.0)
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    six_mb = int(6 * 1024 * 1024)
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence=None,
        attachment_count=1,
        attachment_size_bytes=six_mb,
    )
    assert result is not None
    assert any("large_attachments" in s for s in result["signals"])


# ── Pre-check 3: stale email (age outlier) ───────────────────────────────────

def test_stale_email_fires_signal(monkeypatch):
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence=None,
        attachment_count=0,
        attachment_size_bytes=0,
        email_age_days=400.0,  # default ceiling = 365
    )
    assert result is not None
    assert any("stale_email" in s for s in result["signals"])


def test_age_at_ceiling_no_signal():
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence=None,
        attachment_count=0,
        attachment_size_bytes=0,
        email_age_days=365.0,  # exactly at ceiling — not over
    )
    assert result is None


def test_age_none_skips_check():
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence=None,
        attachment_count=0,
        attachment_size_bytes=0,
        email_age_days=None,
    )
    assert result is None


def test_age_ceiling_configurable(monkeypatch):
    monkeypatch.setattr(ingest, "_EMAIL_ANOMALY_MAX_AGE_DAYS", 30)
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence=None,
        attachment_count=0,
        attachment_size_bytes=0,
        email_age_days=31.0,
    )
    assert result is not None
    assert any("stale_email" in s for s in result["signals"])


# ── Pre-check 4: long subject ─────────────────────────────────────────────────

def test_long_subject_fires_signal(monkeypatch):
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    long_subj = "X" * 501  # default ceiling = 500
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence={"from_name": "A", "from_email": "a@b.com", "subject": long_subj, "to": []},
        attachment_count=0,
        attachment_size_bytes=0,
    )
    assert result is not None
    assert any("long_subject" in s for s in result["signals"])


def test_subject_at_ceiling_no_signal():
    subj = "X" * 500  # exactly at ceiling — not over
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence={"from_name": "A", "from_email": "a@b.com", "subject": subj, "to": []},
        attachment_count=0,
        attachment_size_bytes=0,
    )
    assert result is None


# ── Pre-check 5: missing sender ───────────────────────────────────────────────

def test_missing_sender_fires_signal(monkeypatch):
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence={"from_name": "", "from_email": "", "subject": "Hello", "to": []},
        attachment_count=0,
        attachment_size_bytes=0,
    )
    assert result is not None
    assert any("missing_sender" in s for s in result["signals"])


def test_from_name_only_no_missing_sender_signal():
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence={"from_name": "Bob", "from_email": "", "subject": "Hi", "to": []},
        attachment_count=0,
        attachment_size_bytes=0,
    )
    assert result is None


def test_none_correspondence_skips_subject_and_sender_checks():
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence=None,
        attachment_count=0,
        attachment_size_bytes=0,
    )
    assert result is None


# ── LLM integration ───────────────────────────────────────────────────────────

def test_signal_triggers_llm_call(monkeypatch):
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    ingest._ai_email_ingestion_anomaly_detection(
        correspondence=None,
        attachment_count=11,
        attachment_size_bytes=0,
    )
    assert _FakeRouter.last_fn == "anomaly_detection"


def test_result_has_required_keys(monkeypatch):
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence=None,
        attachment_count=11,
        attachment_size_bytes=0,
    )
    assert result is not None
    for key in ("score", "verdict", "reason", "signals"):
        assert key in result


def test_llm_score_propagated(monkeypatch):
    _FakeRouter._content = json.dumps({"score": 0.9, "verdict": "anomalous", "reason": "too many"})
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence=None,
        attachment_count=15,
        attachment_size_bytes=0,
    )
    assert result is not None
    assert result["verdict"] == "anomalous"
    assert result["score"] == pytest.approx(0.9)


def test_llm_unavailable_degrades_gracefully(monkeypatch):
    monkeypatch.setattr(router_mod, "LLMRouter", _BrokenRouter)
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence=None,
        attachment_count=11,
        attachment_size_bytes=0,
    )
    assert result is not None
    assert result["verdict"] == "suspicious"
    assert result["reason"] == "llm_unavailable"
    assert result["score"] == pytest.approx(0.5)


def test_invalid_verdict_normalised_to_suspicious(monkeypatch):
    _FakeRouter._content = json.dumps({"score": 0.4, "verdict": "UNKNOWN", "reason": "x"})
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence=None,
        attachment_count=11,
        attachment_size_bytes=0,
    )
    assert result is not None
    assert result["verdict"] == "suspicious"


def test_multiple_signals_all_present(monkeypatch):
    monkeypatch.setattr(router_mod, "LLMRouter", _FakeRouter)
    big = int(30 * 1024 * 1024)
    result = ingest._ai_email_ingestion_anomaly_detection(
        correspondence={"from_name": "", "from_email": "", "subject": "X" * 600, "to": []},
        attachment_count=15,
        attachment_size_bytes=big,
        email_age_days=400.0,
    )
    assert result is not None
    signal_types = {s.split(":")[0] for s in result["signals"]}
    assert "high_attachment_count" in signal_types
    assert "large_attachments" in signal_types
    assert "stale_email" in signal_types
    assert "long_subject" in signal_types
    assert "missing_sender" in signal_types
