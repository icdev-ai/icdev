"""Tests for ml.pillar_weight_learner."""

import json
import random
from datetime import datetime, timezone

import pytest

from tools.trading.ml import pillar_weight_learner as pwl


@pytest.fixture(autouse=True)
def _bootstrap():
    # Bootstrap tables + clean our test rows
    pwl._conn().close()
    from tools.trading.analysis.confluence_scorer import _conn as conf_conn

    conf_conn().close()

    from tools.db.storage import get_connection

    c = get_connection()
    for t in ("ad_learned_pillar_weights", "ad_confluence_scores", "ad_orders"):
        c.execute(f"DELETE FROM {t} WHERE 1=1")  # nosec B608 — whitelist of known table names
    c.commit()
    c.close()


def _seed(n_signals: int, noise: float = 0.002):
    """Seed n signals with synthetic confluence + matching BUY/SELL fills."""
    from tools.db.storage import get_connection

    c = get_connection()
    pillar_names = ["technical_bullish", "fundamental_bullish", "sentiment_bullish", "news_bullish"]
    random.seed(42)

    for i in range(n_signals):
        sig_id = f"sig-pwl-{i:04d}"
        order_buy = f"ord-pwl-{i:04d}-buy"
        order_sell = f"ord-pwl-{i:04d}-sell"

        # Direction: random, but technical_bullish is the TRUE driver of returns
        true_tech = random.choice([1.0, -1.0])
        realized_pct = 0.02 * true_tech + random.uniform(-noise, noise)

        pillars = [{"name": n, "direction": "bull" if random.random() > 0.5 else "bear", "weight": 0.25, "evidence": ""} for n in pillar_names]
        # Force technical to align with true direction so the learner picks it up
        pillars[0]["direction"] = "bull" if true_tech > 0 else "bear"

        c.execute(
            "INSERT INTO ad_confluence_scores (signal_id, ticker, direction, confluence_score, "
            "tier, pillars_total, pillars_agreeing, pillars_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (sig_id, "ZZPWL", "BUY", 70.0, "B", 4, 2, json.dumps(pillars), datetime.now(timezone.utc).isoformat()),
        )
        entry = 100.0
        exit_price = entry * (1 + realized_pct)
        c.execute(
            "INSERT INTO ad_orders (id, portfolio_id, ticker, side, qty, order_type, status, fill_price, signal_id, created_at, filled_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (order_buy, "pf", "ZZPWL", "buy", 1, "market", "filled", entry, sig_id, "2026-04-01T10:00:00Z", "2026-04-01T10:00:00Z"),
        )
        c.execute(
            "INSERT INTO ad_orders (id, portfolio_id, ticker, side, qty, order_type, status, fill_price, signal_id, created_at, filled_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (order_sell, "pf", "ZZPWL", "sell", 1, "market", "filled", exit_price, sig_id, "2026-04-02T10:00:00Z", "2026-04-02T10:00:00Z"),
        )
    c.commit()
    c.close()


def test_insufficient_data_returns_message():
    _seed(5)
    out = pwl.train(days=30)
    assert out["status"] == "insufficient_data"
    assert out["samples"] == 5
    assert out["using"] == "DEFAULT_WEIGHTS"


def test_trains_and_persists_weights():
    _seed(60, noise=0.001)
    out = pwl.train(days=30)
    assert out["status"] == "trained"
    assert out["samples"] >= 50
    assert out["pillar_count"] == 4
    assert abs(sum(out["weights"].values()) - 1.0) < 1e-3

    active = pwl.active_weights()
    assert active
    assert active["samples"] >= 50
    assert set(active["weights"]) >= {"technical_bullish"}


def test_technical_pillar_dominates_when_true_driver():
    # With 60 samples where technical is the only true driver, that pillar
    # should land in the top half of learned weights.
    _seed(60, noise=0.001)
    out = pwl.train(days=30, alpha=0.005)
    weights = out["weights"]
    assert weights["technical_bullish"] >= sorted(weights.values())[len(weights) // 2]


def test_active_weights_empty_when_no_training():
    assert pwl.active_weights() == {}


def test_confluence_scorer_picks_up_learned_weights():
    # Manually insert a learned weights row
    import json as _json

    from tools.db.storage import get_connection

    c = get_connection()
    c.execute(
        "INSERT INTO ad_learned_pillar_weights (trained_at, window_days, samples, r_squared, alpha, l1_ratio, weights_json, active) "
        "VALUES (?,?,?,?,?,?,?,1)",
        (
            datetime.now(timezone.utc).isoformat(),
            30,
            100,
            0.5,
            0.01,
            0.5,
            _json.dumps({"technical_bullish": 0.8, "fundamental_bullish": 0.2}),
        ),
    )
    c.commit()
    c.close()

    from tools.trading.analysis import confluence_scorer

    res = confluence_scorer.evaluate(
        ticker="ZZLEARN",
        composite_direction="BUY",
        component_scores={"fundamental": 60, "technical": 80},
    )
    tech = next(p for p in res.pillars if p.name == "technical_bullish")
    fund = next(p for p in res.pillars if p.name == "fundamental_bullish")
    assert tech.weight == 0.8
    assert fund.weight == 0.2
