# CUI // SP-CTI
"""nav-strat-03 — Oracle assessment endpoint must not fabricate scores.

Before this fix, when ``intelligence.oracle.sio_engine`` failed to import, the
Strategos Oracle endpoint (``GET /api/strategos/oracle``) returned hardcoded lens
scores (5.0 / 4.5 / 5.5 / 6.0, composite 5.25) with a zero-padded sparkline. The
narratives said "Mock data", but ``composite_score``, ``iw_triggered`` and the
sparkline rendered on ``/strategos/oracle`` as a genuine assessment — misleading
the analyst.

The endpoint now returns an explicit degraded state
(``{"available": false, "reason": ...}`` with HTTP 503) and NO numeric scores or
sparkline when the engine is unavailable. The healthy path is unchanged and now
carries ``"available": true``.
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest


@pytest.fixture()
def oracle_client():
    try:
        from apps.strategos.blueprint import create_strategos_api_blueprint
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"strategos blueprint not importable: {exc}")

    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-secret"
    app.register_blueprint(create_strategos_api_blueprint(), url_prefix="/api/strategos")
    with app.test_client() as c:
        yield c


class _FakeAssessment:
    """Stand-in for a real SIOEngine assessment result."""

    lenses = {
        "threat_posture": {"score": 8.2, "nato_reliability": "A1",
                           "narrative": "Live threat posture.", "confidence": 0.9},
        "behavior_pattern": {"score": 7.7, "nato_reliability": "B2",
                             "narrative": "Live behavior pattern.", "confidence": 0.9},
        "intent_assessment": {"score": 8.9, "nato_reliability": "A1",
                              "narrative": "Live intent.", "confidence": 0.9},
        "convergence": {"score": 9.1, "nato_reliability": "A1",
                        "narrative": "Live convergence.", "confidence": 0.9},
    }
    composite_score = 8.475
    iw_triggered = True
    timestamp = "2026-01-02T03:04:05+00:00"


def _fake_sio_module():
    import types

    mod = types.ModuleType("intelligence.oracle.sio_engine")

    class SIOEngine:
        def run_all(self):
            return _FakeAssessment()

    def get_latest_assessment():  # pragma: no cover - healthy run_all path wins
        raise AssertionError("run_all() succeeded; stored-row fallback should not run")

    mod.SIOEngine = SIOEngine
    mod.get_latest_assessment = get_latest_assessment
    return mod


# ── import-failure path → explicit degraded state, NO fabricated numbers ──────

def test_oracle_import_failure_returns_degraded(oracle_client):
    # Setting the module to None in sys.modules forces the ``from
    # intelligence.oracle.sio_engine import ...`` statement to raise ImportError,
    # exactly as a missing/broken engine would at runtime.
    with patch.dict(sys.modules, {"intelligence.oracle.sio_engine": None}):
        resp = oracle_client.get("/api/strategos/oracle")

    assert resp.status_code == 503, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["available"] is False
    assert data.get("reason")

    # The old fabricated numbers must appear NOWHERE in the response.
    body = resp.get_data(as_text=True)
    for forbidden in ("5.25", "5.0", "4.5", "5.5", "composite_score", "sparkline"):
        assert forbidden not in body, f"fabricated field/score leaked: {forbidden!r}"


# ── healthy path → real scores pass through unchanged ─────────────────────────

def test_oracle_healthy_passes_scores_through(oracle_client):
    with patch.dict(sys.modules,
                    {"intelligence.oracle.sio_engine": _fake_sio_module()}):
        resp = oracle_client.get("/api/strategos/oracle")

    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["available"] is True
    assert data["composite_score"] == pytest.approx(8.475)
    assert data["iw_triggered"] is True
    assert data["lenses"]["convergence"]["score"] == pytest.approx(9.1)
    assert data["lenses"]["threat_posture"]["nato_reliability"] == "A1"
    assert isinstance(data["sparkline"], list) and len(data["sparkline"]) == 14
