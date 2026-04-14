"""Tests for analysis.confluence_scorer."""

import pytest

from tools.trading.analysis import confluence_scorer as cs


@pytest.fixture(autouse=True)
def _bootstrap(monkeypatch):
    monkeypatch.delenv("ICDEV_CONFLUENCE_ENABLED", raising=False)
    cs._conn().close()
    # Clear learned pillar weights so default weights are used for scoring tests
    from tools.db.storage import get_connection

    try:
        c = get_connection()
        c.execute("DELETE FROM ad_learned_pillar_weights WHERE 1=1")
        c.commit()
        c.close()
    except Exception:
        pass


def test_enabled_by_default():
    state = cs.is_enabled()
    assert state["enabled"] is True
    assert state["disabled_by"] == []


def test_env_disable(monkeypatch):
    monkeypatch.setenv("ICDEV_CONFLUENCE_ENABLED", "false")
    state = cs.is_enabled()
    assert state["enabled"] is False
    assert any(s["source"] == "env" for s in state["disabled_by"])


def test_db_disable_then_enable():
    cs.set_enabled(False, by="pytest")
    assert cs.is_enabled()["enabled"] is False
    cs.set_enabled(True, by="pytest")
    assert cs.is_enabled()["enabled"] is True


def test_all_bullish_hits_A_tier():
    res = cs.evaluate(
        ticker="ZZHOT",
        composite_direction="BUY",
        component_scores={"fundamental": 80, "technical": 75, "sentiment": 70, "news": 65},
        macro_regime="GREEN",
        perspective={"net_score": 60, "consensus": True},
        advisor={"direction": "BUY"},
        expert_consensus={"bull_votes": 6, "total_votes": 6},
    )
    assert res.tier == "A"
    assert res.confluence_score >= 80
    assert res.sizing_multiplier == 1.0
    assert res.pillars_agreeing >= 7


def test_mixed_signals_hits_B_or_C():
    res = cs.evaluate(
        ticker="ZZMID",
        composite_direction="BUY",
        component_scores={"fundamental": 70, "technical": 40, "sentiment": 50, "news": 55},
        macro_regime="YELLOW",
        perspective=None,
        advisor={"direction": "BUY"},
        expert_consensus={"bull_votes": 3, "total_votes": 6},
    )
    assert res.tier in ("B", "C")
    assert 30 < res.confluence_score < 80


def test_all_bearish_but_BUY_direction_lands_D():
    # Composite says BUY but every pillar is bearish — huge dissent
    res = cs.evaluate(
        ticker="ZZDISS",
        composite_direction="BUY",
        component_scores={"fundamental": 20, "technical": 15, "sentiment": 25, "news": 30},
        macro_regime="RED",
        perspective={"net_score": -70, "consensus": True},
        advisor={"direction": "SELL"},
        expert_consensus={"bull_votes": 0, "total_votes": 6},
    )
    assert res.tier == "D"
    assert res.confluence_score < 40
    assert res.sizing_multiplier == 0.0


def test_unknown_pillars_do_not_pull_either_way():
    # All missing → score = 0; tier D
    res = cs.evaluate(
        ticker="ZZEMPTY",
        composite_direction="BUY",
        component_scores={},
        macro_regime=None,
        perspective=None,
        advisor=None,
        expert_consensus=None,
    )
    assert res.tier == "D"
    assert res.pillars_total == 8
    assert res.pillars_agreeing == 0


def test_extra_pillars_are_counted():
    extras = [
        cs.PillarVote("multi_timeframe_bull", "bull", 0.2, "weekly+monthly+daily all bullish"),
        cs.PillarVote("price_level_cluster", "bull", 0.15, "support + 200MA + fib 50 clustered"),
    ]
    res = cs.evaluate(
        ticker="ZZEXTRA",
        composite_direction="BUY",
        component_scores={"fundamental": 70, "technical": 80},
        macro_regime="GREEN",
        advisor={"direction": "BUY"},
        extra_pillars=extras,
    )
    names = {p.name for p in res.pillars}
    assert "multi_timeframe_bull" in names
    assert "price_level_cluster" in names
    assert res.pillars_total == 10


def test_sizing_and_exit_tuning_align_with_tier():
    for score, want_tier in [(85, "A"), (70, "B"), (50, "C"), (10, "D")]:
        assert cs._tier_for_score(score) == want_tier
    assert cs.TIER_SIZING["A"] > cs.TIER_SIZING["B"] > cs.TIER_SIZING["C"] > cs.TIER_SIZING["D"]
    assert cs.TIER_EXIT_ADJUSTMENT["A"]["stop_pct"] > cs.TIER_EXIT_ADJUSTMENT["D"]["stop_pct"]


def test_persist_roundtrip():
    res = cs.evaluate(
        "ZZPERSIST",
        composite_direction="BUY",
        component_scores={"fundamental": 60, "technical": 65, "sentiment": 55, "news": 60},
        macro_regime="GREEN",
        advisor={"direction": "BUY"},
        expert_consensus={"bull_votes": 5, "total_votes": 6},
    )
    row_id = cs.persist(res, signal_id="sig-conf-test")
    assert row_id > 0

    from tools.db.storage import get_connection

    c = get_connection()
    row = c.execute(
        "SELECT tier, confluence_score, signal_id FROM ad_confluence_scores WHERE id = ?", (row_id,)
    ).fetchone()
    c.close()
    assert row["tier"] == res.tier
    assert row["signal_id"] == "sig-conf-test"


def test_bearish_direction_flips_target():
    res = cs.evaluate(
        ticker="ZZSHORT",
        composite_direction="SHORT",
        component_scores={"fundamental": 20, "technical": 25, "sentiment": 20, "news": 25},
        macro_regime="RED",
        advisor={"direction": "SHORT"},
        expert_consensus={"bull_votes": 1, "total_votes": 6},
    )
    # All bearish, direction=SHORT → should hit high tier on the bear side
    assert res.tier in ("A", "B")
    assert res.pillars_agreeing >= 5
