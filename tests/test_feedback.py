"""Tests for feedback loops: exit_outcome_recorder, counterfactual_tracker, strategy_retirement."""

from datetime import datetime, timedelta, timezone

import pytest

from tools.trading.feedback import counterfactual_tracker as ct
from tools.trading.feedback import exit_outcome_recorder as eor
from tools.trading.feedback import strategy_retirement as sr


@pytest.fixture(autouse=True)
def _bootstrap():
    from tools.db.storage import get_connection
    from tools.trading.audit.trade_audit import _conn as ac

    ac().close()
    eor._conn().close()
    ct._conn().close()
    sr._conn().close()

    c = get_connection()
    for t in ("ad_exit_outcomes", "ad_counterfactual_fills", "ad_strategy_config", "ad_position_exits", "ad_orders"):
        c.execute(f"DELETE FROM {t} WHERE 1=1")  # nosec B608
    c.commit()
    c.close()


# ---------------- exit_outcome_recorder ----------------


def test_no_new_outcomes_when_nothing_triggered():
    out = eor.record_new_outcomes()
    assert out["recorded"] == 0


def test_records_triggered_exit_outcome():
    from tools.db.storage import get_connection

    c = get_connection()
    c.execute(
        "INSERT INTO ad_position_exits (id, position_id, ticker, kind, entry_price, entry_time, "
        " status, triggered_price, triggered_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("exit-rec-1", "pos-ZZREC-ord-1", "ZZREC", "stop_loss", 100.0, "2026-04-01T10:00:00Z",
         "triggered", 97.0, "2026-04-02T10:00:00Z"),
    )
    c.commit()
    c.close()

    out = eor.record_new_outcomes()
    assert out["recorded"] == 1

    c = get_connection()
    row = c.execute("SELECT realized_pnl_bps, held_hours FROM ad_exit_outcomes WHERE exit_id = ?", ("exit-rec-1",)).fetchone()
    c.close()
    assert row["realized_pnl_bps"] < 0  # lost money on stop
    assert row["held_hours"] is not None


def test_idempotent_second_pass_no_rerecord():
    from tools.db.storage import get_connection

    c = get_connection()
    c.execute(
        "INSERT INTO ad_position_exits (id, position_id, ticker, kind, entry_price, entry_time, "
        " status, triggered_price, triggered_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("exit-idem-1", "pos-ZZIDEM-ord-1", "ZZIDEM", "take_profit", 100.0, "2026-04-01T10:00:00Z",
         "triggered", 106.0, "2026-04-02T10:00:00Z"),
    )
    c.commit()
    c.close()

    eor.record_new_outcomes()
    out2 = eor.record_new_outcomes()
    assert out2["recorded"] == 0


def test_stats_by_tier_aggregates():
    from tools.db.storage import get_connection

    c = get_connection()
    for i in range(3):
        c.execute(
            "INSERT INTO ad_exit_outcomes (exit_id, ticker, kind, tier_at_entry, regime_at_entry, "
            " realized_pnl_bps, recorded_at) VALUES (?,?,?,?,?,?,?)",
            (f"xo-{i}", "ZZ", "stop_loss", "B", "neutral", -30.0, datetime.now(timezone.utc).isoformat()),
        )
    c.commit()
    c.close()

    out = eor.stats_by_tier(days=30)
    assert out["buckets"]
    assert out["buckets"][0]["tier_at_entry"] == "B"


# ---------------- counterfactual_tracker ----------------


def test_record_skip_returns_id():
    rid = ct.record_skip("sig-1", "AAPL", "BUY", 40.0, 20.0, 190.0)
    assert rid > 0


def test_resolve_marks_correct_skip(monkeypatch):
    # Record a skip; then fake a quote 24h later that barely moved (within budget)
    rid = ct.record_skip("sig-2", "ZZCOR", "BUY", 30.0, 25.0, 100.0)
    # Back-date the record so min-age-hours filter catches it
    from tools.db.storage import get_connection

    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    c = get_connection()
    c.execute("UPDATE ad_counterfactual_fills SET recorded_at = ? WHERE id = ?", (old, rid))
    c.commit()
    c.close()

    monkeypatch.setattr(
        "tools.trading.feedback.counterfactual_tracker.fetch_latest_quote",
        lambda *a, **kw: {"last": 100.1},
    )
    out = ct.resolve_pending(min_age_hours=24)
    assert out["resolved"] >= 1
    assert out["correct_skips"] >= 1


def test_resolve_flags_missed_alpha(monkeypatch):
    rid = ct.record_skip("sig-3", "ZZMISS", "BUY", 30.0, 25.0, 100.0)
    from tools.db.storage import get_connection

    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    c = get_connection()
    c.execute("UPDATE ad_counterfactual_fills SET recorded_at = ? WHERE id = ?", (old, rid))
    c.commit()
    c.close()

    # Price rocketed up well past the alpha budget
    monkeypatch.setattr(
        "tools.trading.feedback.counterfactual_tracker.fetch_latest_quote",
        lambda *a, **kw: {"last": 103.0},  # +300 bps, way above 25 bps budget
    )
    out = ct.resolve_pending(min_age_hours=24)
    assert out["missed_alpha"] >= 1


def test_accuracy_stats_reports_rate():
    from tools.db.storage import get_connection

    c = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    for sc in (1, 1, 0):
        c.execute(
            "INSERT INTO ad_counterfactual_fills (signal_id, ticker, direction, predicted_slippage_bps, "
            " alpha_budget_bps, quote_at_skip, quote_24h, actual_move_bps, skip_was_correct, "
            " recorded_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("s", "ZZ", "BUY", 30, 20, 100.0, 100.5, 50.0, sc, now, now),
        )
    c.commit()
    c.close()

    out = ct.accuracy_stats(days=30)
    assert out["resolved"] == 3
    assert out["skip_accuracy"] > 0.6


# ---------------- strategy_retirement ----------------


def test_retires_losing_strategy():
    # Seed attribution rows by inserting signal-tagged fills with negative PnL
    from tools.db.storage import get_connection
    from tools.trading.analytics.slippage_tracker import record_expected

    c = get_connection()
    to_tag = []
    for i in range(6):
        buy_id = f"ret-buy-{i}"
        sell_id = f"ret-sell-{i}"
        c.execute(
            "INSERT INTO ad_orders (id, portfolio_id, ticker, side, qty, order_type, status, fill_price, signal_id, created_at, filled_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (buy_id, "pf", "ZZRET", "buy", 10, "market", "filled", 100.0, None,
             datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )
        c.execute(
            "INSERT INTO ad_orders (id, portfolio_id, ticker, side, qty, order_type, status, fill_price, signal_id, created_at, filled_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sell_id, "pf", "ZZRET", "sell", 10, "market", "filled", 90.0, None,
             datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )
        to_tag.append((buy_id, 100.0))
        to_tag.append((sell_id, 90.0))
    c.commit()
    c.close()
    for oid, px in to_tag:
        record_expected(oid, px, strategy_id="bad_strat")

    out = sr.run(days=30, min_fills=5, max_realized_usd=-50.0)
    assert out["retired"] >= 1
    assert sr.is_active("bad_strat") is False


def test_skips_default_and_unattributed():
    from tools.db.storage import get_connection

    c = get_connection()
    # Insert fills tagged as 'default' with heavy losses
    from tools.trading.analytics.slippage_tracker import record_expected

    to_tag = []
    for i in range(6):
        buy_id = f"def-buy-{i}"
        sell_id = f"def-sell-{i}"
        c.execute(
            "INSERT INTO ad_orders (id, portfolio_id, ticker, side, qty, order_type, status, fill_price, signal_id, created_at, filled_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (buy_id, "pf", "ZZDEF", "buy", 10, "market", "filled", 100.0, None,
             datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )
        c.execute(
            "INSERT INTO ad_orders (id, portfolio_id, ticker, side, qty, order_type, status, fill_price, signal_id, created_at, filled_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sell_id, "pf", "ZZDEF", "sell", 10, "market", "filled", 80.0, None,
             datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )
        to_tag.append((buy_id, 100.0))
        to_tag.append((sell_id, 80.0))
    c.commit()
    c.close()
    for oid, px in to_tag:
        record_expected(oid, px, strategy_id="default")

    sr.run(days=30, min_fills=5, max_realized_usd=-50.0)
    assert sr.is_active("default") is True   # catch-all never retired


def test_dry_run_does_not_persist():
    from tools.db.storage import get_connection
    from tools.trading.analytics.slippage_tracker import record_expected

    c = get_connection()
    to_tag = []
    for i in range(6):
        buy_id = f"dry-buy-{i}"
        sell_id = f"dry-sell-{i}"
        c.execute(
            "INSERT INTO ad_orders (id, portfolio_id, ticker, side, qty, order_type, status, fill_price, signal_id, created_at, filled_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (buy_id, "pf", "ZZDRY", "buy", 10, "market", "filled", 100.0, None,
             datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )
        c.execute(
            "INSERT INTO ad_orders (id, portfolio_id, ticker, side, qty, order_type, status, fill_price, signal_id, created_at, filled_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sell_id, "pf", "ZZDRY", "sell", 10, "market", "filled", 85.0, None,
             datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )
        to_tag.append((buy_id, 100.0))
        to_tag.append((sell_id, 85.0))
    c.commit()
    c.close()
    for oid, px in to_tag:
        record_expected(oid, px, strategy_id="dry_loser")

    out = sr.run(days=30, min_fills=5, max_realized_usd=-50.0, dry_run=True)
    assert out["retired"] >= 1
    assert sr.is_active("dry_loser") is True   # dry-run didn't flip the flag


def test_reactivate_flips_active_back():
    c = sr._conn()
    c.execute("INSERT OR REPLACE INTO ad_strategy_config (strategy_id, active, retired_at, retired_reason, updated_at) VALUES (?, 0, ?, ?, ?)",
              ("zombie", datetime.now(timezone.utc).isoformat(), "test", datetime.now(timezone.utc).isoformat()))
    c.commit()
    c.close()
    out = sr.reactivate("zombie")
    assert out["active"] is True
    assert sr.is_active("zombie") is True
