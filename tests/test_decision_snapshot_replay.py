"""Tests for audit.decision_snapshot + decision_replay."""

import pytest

from tools.trading.audit import decision_replay, decision_snapshot


@pytest.fixture(autouse=True)
def _bootstrap():
    from tools.trading.audit.trade_audit import _conn as audit_conn
    from tools.trading.db import get_conn

    audit_conn().close()
    get_conn().close()
    decision_snapshot._conn().close()


def test_write_returns_id_and_hash():
    out = decision_snapshot.write(
        signal_id="sig-snap-1",
        ticker="AAPL",
        payload={"foo": "bar", "n": 1},
        direction="BUY",
        composite_score=72.5,
        confidence=0.85,
        regime="GREEN",
    )
    assert "id" in out and len(out["id"]) > 10
    assert len(out["sha256"]) == 64


def test_get_returns_payload_dict():
    decision_snapshot.write("sig-snap-2", "MSFT", {"weights": {"value": 0.3, "growth": 0.2}})
    snap = decision_snapshot.get("sig-snap-2")
    assert snap is not None
    assert snap["payload"]["weights"]["value"] == 0.3


def test_verify_integrity_passes():
    decision_snapshot.write("sig-snap-3", "GOOGL", {"a": 1})
    out = decision_snapshot.verify_integrity("sig-snap-3")
    assert out["status"] == "ok"


def test_verify_integrity_detects_tamper():
    decision_snapshot.write("sig-snap-4", "NVDA", {"a": 1})
    # Tamper directly via SQL
    from tools.db.storage import get_connection

    c = get_connection()
    c.execute(
        "UPDATE ad_decision_snapshots SET payload_json = ? WHERE signal_id = ?",
        ('{"a": 999}', "sig-snap-4"),
    )
    c.commit()
    c.close()
    out = decision_snapshot.verify_integrity("sig-snap-4")
    assert out["status"] == "tampered"


def test_replay_handles_missing_signal():
    out = decision_replay.replay("does-not-exist-zzzz")
    assert out["status"] == "not_found"


def test_replay_returns_snapshot_and_orders():
    decision_snapshot.write("sig-replay-1", "AMZN", {"reason": "test"}, direction="BUY")

    from tools.db.storage import get_connection

    c = get_connection()
    c.execute("DELETE FROM ad_orders WHERE signal_id = ?", ("sig-replay-1",))
    c.execute(
        "INSERT INTO ad_orders (id, portfolio_id, ticker, side, qty, order_type, status, fill_price, signal_id, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("ord-replay-1", "pf-test", "AMZN", "buy", 1, "market", "filled", 100.0, "sig-replay-1", "2026-04-12T00:00:00Z"),
    )
    c.commit()
    c.close()

    out = decision_replay.replay("sig-replay-1")
    assert out["status"] == "ok"
    assert out["snapshot"] is not None
    assert any(o["id"] == "ord-replay-1" for o in out["orders"])


def test_diff_finds_changed_fields():
    decision_snapshot.write("sig-diff-a", "ZZ", {"score": 50, "regime": "GREEN"})
    decision_snapshot.write("sig-diff-b", "ZZ", {"score": 80, "regime": "GREEN"})
    out = decision_replay.diff("sig-diff-a", "sig-diff-b")
    assert out["diff_count"] >= 1
    assert any(d["field"] == "score" for d in out["differences"])
