"""Tests for ml.model_registry."""

from datetime import datetime, timedelta, timezone

import pytest

from tools.trading.ml import model_registry as mr


@pytest.fixture(autouse=True)
def _bootstrap():
    # Ensure all referenced tables exist
    from tools.trading.ml.fill_quality_model import _conn as fc
    from tools.trading.ml.pillar_weight_learner import _conn as pc
    from tools.trading.ml.regime_hmm import _conn as rc

    pc().close()
    fc().close()
    rc().close()

    from tools.db.storage import get_connection

    c = get_connection()
    for t in ("ad_learned_pillar_weights", "ad_fill_quality_models", "ad_regime_hmm_models"):
        c.execute(f"DELETE FROM {t} WHERE 1=1")  # nosec B608
    c.commit()
    c.close()


def test_never_trained_models_flagged():
    out = mr.health()
    assert out["overall_status"] == "degraded"
    for m in out["models"]:
        assert m["status"] == "never_trained"


def test_fresh_training_reports_healthy():
    # Insert recent pillar weights
    from tools.db.storage import get_connection

    now = datetime.now(timezone.utc).isoformat()
    c = get_connection()
    c.execute(
        "INSERT INTO ad_learned_pillar_weights (trained_at, window_days, samples, r_squared, alpha, l1_ratio, weights_json, active) "
        "VALUES (?, 90, 100, 0.5, 0.01, 0.5, '{}', 1)",
        (now,),
    )
    c.execute(
        "INSERT INTO ad_fill_quality_models (trained_at, window_days, samples, mae_bps, feature_names, model_path, active) "
        "VALUES (?, 90, 100, 5.0, '[]', '/tmp/fq.pkl', 1)",
        (now,),
    )
    c.execute(
        "INSERT INTO ad_regime_hmm_models (trained_at, samples, log_likelihood, n_states, feature_names, state_label_map_json, transition_matrix_json, model_path, active) "
        "VALUES (?, 200, -123.0, 5, '[]', '{}', '[]', '/tmp/hmm.pkl', 1)",
        (now,),
    )
    c.commit()
    c.close()

    out = mr.health()
    assert out["overall_status"] == "healthy"
    for m in out["models"]:
        assert m["status"] == "fresh"
        assert m["age_hours"] is not None and m["age_hours"] < 1


def test_stale_models_flagged():
    from tools.db.storage import get_connection

    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    c = get_connection()
    c.execute(
        "INSERT INTO ad_learned_pillar_weights (trained_at, window_days, samples, r_squared, alpha, l1_ratio, weights_json, active) "
        "VALUES (?, 90, 100, 0.5, 0.01, 0.5, '{}', 1)",
        (old,),
    )
    c.commit()
    c.close()

    out = mr.health()
    # pillar_weights stale_threshold = 14 days; 30 days > 14
    pillar = next(m for m in out["models"] if m["model"] == "pillar_weights")
    assert pillar["status"] == "stale"
    assert out["overall_status"] == "degraded"


def test_model_specific_metrics_surfaced():
    from tools.db.storage import get_connection

    now = datetime.now(timezone.utc).isoformat()
    c = get_connection()
    c.execute(
        "INSERT INTO ad_fill_quality_models (trained_at, window_days, samples, mae_bps, feature_names, model_path, active) "
        "VALUES (?, 90, 100, 7.5, '[]', '/tmp/fq.pkl', 1)",
        (now,),
    )
    c.commit()
    c.close()
    out = mr.health()
    fq = next(m for m in out["models"] if m["model"] == "fill_quality")
    assert fq["mae_bps"] == 7.5
