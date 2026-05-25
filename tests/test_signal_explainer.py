"""Tests for llm.signal_explainer."""

from unittest import mock

import pytest

from tools.trading.llm import signal_explainer


@pytest.fixture(autouse=True)
def _bootstrap():
    # Ensure table exists before cleanup
    signal_explainer._conn().close()
    from tools.db.storage import get_connection

    c = get_connection()
    c.execute("DELETE FROM ad_signal_narratives WHERE signal_id LIKE 'sig-explain-%'")
    c.commit()
    c.close()


def _fake_replay(signal_id):
    return {
        "signal_id": signal_id,
        "status": "ok",
        "signal": {"ticker": "AAPL", "direction": "BUY"},
        "snapshot": {
            "direction": "BUY",
            "confluence_score": 72.0,
            "payload": {
                "confluence": {"tier": "B", "score": 72, "pillars": [
                    {"name": "technical_bullish", "direction": "bull", "evidence": "sma cross"},
                ]},
                "vix_sizing": {"vix": 18.2, "regime": "neutral", "scale": 0.85},
                "advisor": {"direction": "BUY"},
                "session": {"session": "regular", "is_open": True},
                "estimated_price": 190.5,
                "qty": 2,
            },
        },
        "orders": [{"id": "ord-1", "status": "filled", "fill_price": 191.0, "qty": 2}],
        "exits": [{"kind": "stop_loss", "pct": 0.03, "status": "active"}],
        "audit_events": [],
    }


def test_narrate_returns_template_when_llm_unavailable():
    with mock.patch("tools.trading.audit.decision_replay.replay", side_effect=_fake_replay):
        with mock.patch("tools.llm.router.LLMRouter") as Rcls:
            from tools.llm.router import LLMUnavailableError

            r = mock.Mock()
            r.invoke.side_effect = LLMUnavailableError("no provider")
            Rcls.return_value = r
            out = signal_explainer.narrate("sig-explain-1", use_cache=False)
    assert out["source"] == "fresh"
    assert out["provider"] == "template"
    assert "AAPL" in out["narrative"]


def test_narrate_uses_llm_content_when_available():
    with mock.patch("tools.trading.audit.decision_replay.replay", side_effect=_fake_replay):
        with mock.patch("tools.llm.router.LLMRouter") as Rcls:
            r = mock.Mock()
            resp = mock.Mock(content="FathomDesk went long AAPL at tier B ...", provider="ollama", model_id="qwen3.5")
            r.invoke.return_value = resp
            Rcls.return_value = r
            out = signal_explainer.narrate("sig-explain-2", use_cache=False)
    assert out["source"] == "fresh"
    assert out["provider"] == "ollama"
    assert "tier B" in out["narrative"]


def test_narrate_cache_hit_does_not_regen():
    with mock.patch("tools.trading.audit.decision_replay.replay", side_effect=_fake_replay):
        with mock.patch("tools.llm.router.LLMRouter") as Rcls:
            r = mock.Mock()
            r.invoke.return_value = mock.Mock(content="first narrative", provider="ollama", model_id="x")
            Rcls.return_value = r
            signal_explainer.narrate("sig-explain-3", use_cache=False)   # prime cache
            r.invoke.reset_mock()
            out = signal_explainer.narrate("sig-explain-3", use_cache=True)
    assert out["source"] == "cache"
    r.invoke.assert_not_called()


def test_not_found_returns_error():
    with mock.patch(
        "tools.trading.audit.decision_replay.replay",
        return_value={"status": "not_found", "signal_id": "bogus"},
    ):
        out = signal_explainer.narrate("bogus-id", use_cache=False)
    assert out["source"] == "error"


def test_refresh_bypasses_cache():
    with mock.patch("tools.trading.audit.decision_replay.replay", side_effect=_fake_replay):
        with mock.patch("tools.llm.router.LLMRouter") as Rcls:
            r = mock.Mock()
            r.invoke.return_value = mock.Mock(content="v1", provider="p", model_id="m")
            Rcls.return_value = r
            signal_explainer.narrate("sig-explain-4", use_cache=False)
            r.invoke.return_value = mock.Mock(content="v2", provider="p", model_id="m")
            out = signal_explainer.narrate("sig-explain-4", use_cache=False)
    assert out["narrative"] == "v2"


def test_template_narrative_handles_empty_payload():
    out = signal_explainer._template_narrative({"signal_id": "x", "signal": {"direction": "UNKNOWN", "ticker": ""}, "snapshot": {}, "orders": []})
    assert "signal" in out.lower() or "UNKNOWN" in out
