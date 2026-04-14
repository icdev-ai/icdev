"""Tests for ml.regime_hmm."""

import numpy as np
import pytest

from tools.trading.ml import regime_hmm as rh


@pytest.fixture(autouse=True)
def _bootstrap():
    rh._conn().close()
    if rh._MODEL_PATH.exists():
        rh._MODEL_PATH.unlink()
    from tools.db.storage import get_connection

    c = get_connection()
    c.execute("DELETE FROM ad_regime_hmm_models WHERE 1=1")
    c.commit()
    c.close()


def _synthetic(n=200):
    rng = np.random.default_rng(7)
    # Two clusters: low-vol and high-vol, alternating — HMM should discover at least 2 states
    vix = np.concatenate([rng.normal(12, 1, n // 2), rng.normal(30, 2, n // 2)])
    slope = np.concatenate([rng.normal(0.3, 0.05, n // 2), rng.normal(-0.05, 0.03, n // 2)])
    rv = vix * 0.8 + rng.normal(0, 1, n)
    spread = np.concatenate([rng.normal(0.8, 0.15, n // 2), rng.normal(-0.2, 0.15, n // 2)])
    dxy = 103 + rng.normal(0, 1, n)
    return np.column_stack([vix, slope, rv, spread, dxy]).tolist()


def test_insufficient_data_returns_status():
    out = rh.train([[18, -0.05, 14, 0.3, 103]] * 10)
    assert out["status"] == "insufficient_data"


def test_train_persists_model():
    out = rh.train(_synthetic(200))
    assert out["status"] == "trained"
    assert out["n_states"] == rh._N_STATES
    assert rh._MODEL_PATH.exists()
    # Transition matrix rows should sum to ~1.0
    for row in out["transition_matrix"]:
        assert abs(sum(row) - 1.0) < 1e-3


def test_state_labels_ordered_by_vix():
    out = rh.train(_synthetic(200))
    labels_by_idx = out["state_labels"]
    # The two ends of the label map should be DEEP_RISK_ON and DEEP_RISK_OFF
    assert "DEEP_RISK_ON" in labels_by_idx.values()
    assert "DEEP_RISK_OFF" in labels_by_idx.values()


def test_predict_without_model_returns_no_model():
    out = rh.predict_current([[12, 0.3, 10, 0.8, 103]])
    assert out["status"] == "no_model"


def test_predict_current_runs_after_train():
    rh.train(_synthetic(200))
    low_vol_window = [[11, 0.3, 9, 0.8, 103]] * 5
    out = rh.predict_current(low_vol_window)
    assert out["status"] == "ok"
    assert out["state_label"] in rh._STATE_LABELS
    assert len(out["state_probabilities"]) == rh._N_STATES
    # Probabilities sum to ~1
    assert abs(sum(out["state_probabilities"].values()) - 1.0) < 1e-3


def test_classify_regime_backward_compatible_mapping():
    rh.train(_synthetic(200))
    low = rh.classify_regime([[11, 0.3, 9, 0.8, 103]] * 5)
    high = rh.classify_regime([[35, -0.1, 28, -0.3, 103]] * 5)
    assert low["regime"] in ("GREEN", "YELLOW", "RED")
    assert high["regime"] in ("GREEN", "YELLOW", "RED")
    # Strongly low-vol should lean GREEN or YELLOW, not RED
    assert low["regime"] != "RED"


def test_classify_regime_no_model_is_yellow_fallback():
    if rh._MODEL_PATH.exists():
        rh._MODEL_PATH.unlink()
    out = rh.classify_regime([[18, -0.05, 14, 0.3, 103]])
    assert out["regime"] == "YELLOW"
    assert out["fallback"] is True
