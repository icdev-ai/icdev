# CUI // SP-CTI
"""Unit tests for MCIP Diplomatic Activity Tracker (DAT) — DTI scoring engine."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from tools.mcip.constants import (
    DAT_SOURCE_TYPES, DTI_WEIGHTS, DTI_BANDS, classify_dti,
    DTI_UPDATE_INTERVAL_HOURS,
)


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------

def test_source_types_defined():
    assert "cable_traffic" in DAT_SOURCE_TYPES
    assert "unsc_schedule" in DAT_SOURCE_TYPES
    assert "backchannel_metadata" in DAT_SOURCE_TYPES
    assert len(DAT_SOURCE_TYPES) == 3


def test_dti_weights_sum_to_one():
    total = sum(DTI_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, expected 1.0"


def test_dti_bands_cover_full_range():
    covered = set()
    for lo, hi in DTI_BANDS.values():
        covered.update(range(lo, hi + 1))
    assert 0 in covered
    assert 100 in covered
    assert len(covered) == 101


def test_classify_dti_low():
    assert classify_dti(0.0) == "low"
    assert classify_dti(33.0) == "low"


def test_classify_dti_moderate():
    assert classify_dti(34.0) == "moderate"
    assert classify_dti(66.0) == "moderate"


def test_classify_dti_high():
    assert classify_dti(67.0) == "high"
    assert classify_dti(100.0) == "high"


def test_update_interval_is_six():
    assert DTI_UPDATE_INTERVAL_HOURS == 6


# ---------------------------------------------------------------------------
# Analytics — compute_dti (mocked DB)
# ---------------------------------------------------------------------------

def _make_row(source_type, cnt, avg_sig):
    row = MagicMock()
    row.__getitem__ = lambda self, k: {"cnt": cnt, "avg_sig": avg_sig}[k]
    return row


def test_compute_dti_no_events():
    """With no events, DTI should be 0.0."""
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = None
    with patch("tools.mcip.analytics._get_conn", return_value=mock_conn):
        from tools.mcip.analytics import compute_dti
        result = compute_dti(window_hours=6)
    assert result["score"] == 0.0
    assert result["band"] == "low"
    assert result["event_count"] == 0


def test_compute_dti_all_sources_max_tension():
    """All sources at tension=1.0 → DTI = 100."""
    mock_conn = MagicMock()

    def fake_fetchone():
        r = MagicMock()
        r.__getitem__ = lambda self, k: {"cnt": 5, "avg_sig": 1.0}[k]
        return r

    mock_conn.execute.return_value.fetchone = fake_fetchone
    with patch("tools.mcip.analytics._get_conn", return_value=mock_conn):
        from tools.mcip.analytics import compute_dti
        result = compute_dti()
    assert result["score"] == 100.0
    assert result["band"] == "high"


def test_compute_dti_single_source_cable():
    """Only cable_traffic populated → DTI ≈ 45% of signal × 100."""
    mock_conn = MagicMock()
    call_count = [0]

    def fake_fetchone():
        r = MagicMock()
        src = DAT_SOURCE_TYPES[call_count[0] % 3]
        call_count[0] += 1
        if src == "cable_traffic":
            r.__getitem__ = lambda self, k: {"cnt": 10, "avg_sig": 1.0}[k]
        else:
            r.__getitem__ = lambda self, k: {"cnt": 0, "avg_sig": None}[k]
        return r

    mock_conn.execute.return_value.fetchone = fake_fetchone
    with patch("tools.mcip.analytics._get_conn", return_value=mock_conn):
        from tools.mcip.analytics import compute_dti
        result = compute_dti()
    # cable weight=0.45 × 100 = 45
    assert abs(result["score"] - 45.0) < 0.1


def test_compute_dti_score_clamped():
    """Score must always be within [0, 100]."""
    mock_conn = MagicMock()

    def fake_fetchone():
        r = MagicMock()
        r.__getitem__ = lambda self, k: {"cnt": 99, "avg_sig": 2.0}[k]
        return r

    mock_conn.execute.return_value.fetchone = fake_fetchone
    with patch("tools.mcip.analytics._get_conn", return_value=mock_conn):
        from tools.mcip.analytics import compute_dti
        result = compute_dti()
    assert 0.0 <= result["score"] <= 100.0


# ---------------------------------------------------------------------------
# Analytics — ingest_event validation
# ---------------------------------------------------------------------------

def test_ingest_event_invalid_source_type():
    from tools.mcip.analytics import ingest_event
    with pytest.raises(ValueError, match="unknown source_type"):
        ingest_event("bad_source", "hash123", "US", "UN", "CUI", 0.5)


def test_ingest_event_tension_signal_clamped():
    """tension_signal > 1.0 must be clamped to 1.0 before insert."""
    inserted_values = []

    mock_conn = MagicMock()
    mock_conn.execute = lambda sql, params: inserted_values.append(params) or MagicMock()

    with patch("tools.mcip.analytics._get_conn", return_value=mock_conn):
        from tools.mcip.analytics import ingest_event
        ingest_event("cable_traffic", "abc123", "US", "RU", "CUI", 5.0)

    assert inserted_values, "No INSERT executed"
    tension_idx = 6  # position in VALUES tuple
    assert inserted_values[0][tension_idx] == 1.0


def test_ingest_event_tension_signal_min_clamped():
    inserted_values = []

    mock_conn = MagicMock()
    mock_conn.execute = lambda sql, params: inserted_values.append(params) or MagicMock()

    with patch("tools.mcip.analytics._get_conn", return_value=mock_conn):
        from tools.mcip.analytics import ingest_event
        ingest_event("unsc_schedule", "xyz", "UN", "CN", "CUI", -0.5)

    assert inserted_values[0][6] == 0.0


# ---------------------------------------------------------------------------
# Analytics — content_hash determinism
# ---------------------------------------------------------------------------

def test_content_hash_deterministic():
    from tools.mcip.analytics import content_hash
    h1 = content_hash("test cable content")
    h2 = content_hash("test cable content")
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex digest


def test_content_hash_different_for_different_content():
    from tools.mcip.analytics import content_hash
    assert content_hash("alpha") != content_hash("beta")


# ---------------------------------------------------------------------------
# Blueprint API tests (Flask test client)
# ---------------------------------------------------------------------------

@pytest.fixture
def dat_client():
    """Minimal Flask app with MCIP DAT blueprint for API testing."""
    from flask import Flask
    from tools.mcip.blueprint import bp
    app = Flask(__name__, template_folder="../tools/dashboard/templates")
    app.register_blueprint(bp)
    app.config["TESTING"] = True
    return app.test_client()


def test_api_dti_current_returns_json(dat_client):
    mock_dti = {
        "score": 42.5,
        "band": "moderate",
        "cable_sub": 50.0,
        "unsc_sub": 30.0,
        "backchannel_sub": 20.0,
        "event_count": 12,
        "computed_at": "2026-05-16T00:00:00Z",
    }
    with patch("tools.mcip.blueprint.get_current_dti", return_value=mock_dti):
        resp = dat_client.get("/api/dat/dti")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["score"] == 42.5
    assert data["band"] == "moderate"


def test_api_dti_history_returns_list(dat_client):
    history = [
        {"id": "a", "score": 30.0, "computed_at": "2026-05-15T18:00:00Z"},
        {"id": "b", "score": 55.0, "computed_at": "2026-05-16T00:00:00Z"},
    ]
    with patch("tools.mcip.blueprint.get_dti_history", return_value=history):
        resp = dat_client.get("/api/dat/dti/history")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 2
    assert len(data["history"]) == 2


def test_api_dat_ingest_valid_event(dat_client):
    with patch("tools.mcip.blueprint.ingest_event", return_value="test-uuid-1234") as mock_ingest:
        payload = {
            "source_type": "cable_traffic",
            "text": "Diplomatic cable from US Embassy",
            "sender": "US Embassy Paris",
            "recipient": "State Dept DC",
            "classification": "CUI",
            "tension_signal": 0.75,
        }
        resp = dat_client.post("/api/dat/events", json=payload)
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["event_id"] == "test-uuid-1234"
    mock_ingest.assert_called_once()


def test_api_dat_ingest_invalid_source(dat_client):
    resp = dat_client.post("/api/dat/events", json={
        "source_type": "invalid_type",
        "tension_signal": 0.5,
    })
    assert resp.status_code == 400
    data = resp.get_json()
    assert "source_type" in data["error"]


def test_api_dat_compute_triggers_scoring(dat_client):
    mock_result = {"score": 67.0, "band": "high", "sub_scores": {}, "event_count": 5}
    with patch("tools.mcip.blueprint.compute_dti", return_value=mock_result), \
         patch("tools.mcip.blueprint.record_dti_score", return_value="persisted-id"):
        resp = dat_client.post("/api/dat/compute")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["score"] == 67.0
    assert data["persisted_id"] == "persisted-id"


def test_api_dat_events_get(dat_client):
    events = [{"id": "e1", "source_type": "unsc_schedule", "tension_signal": 0.4}]
    with patch("tools.mcip.blueprint.get_recent_events", return_value=events):
        resp = dat_client.get("/api/dat/events")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["count"] == 1
