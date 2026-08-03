# CUI // SP-CTI
"""Phase C V&V gate: DebateEngine, RiskManager, ad_decision_audit, ANALYST_PANEL_ENABLED.

Confirms:
  (1) DebateEngine returns structured DebateResult with required fields.
  (2) RiskManager applies Gate A (min_confidence floor): low-confidence debate → HOLD.
  (3) ad_decision_audit receives a row after a full RiskManager.arbitrate() call.
  (4) ANALYST_PANEL_ENABLED=False routes to heuristic fallback without error.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from tests._sql_compat import connect as _translating_connect
from tools.fathomdesk.agents.debate_engine import DebateResult


@pytest.fixture(autouse=True)
def _isolate_decision_log(tmp_path, monkeypatch):
    """Keep decision_memory writes out of the tracked data/ file.

    arbitrate() persists through decision_memory, whose default log path is the
    tracked data/fathomdesk_decisions.md — so without this every run of these
    tests appended fabricated decisions to a repo file.
    """
    monkeypatch.setenv(
        "FATHOMDESK_DECISIONS_PATH", str(tmp_path / "fathomdesk_decisions.md")
    )


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------

_FAKE_REPORT = {
    "score": 0.65,
    "signals": ["mock_signal"],
    "reasoning": "Mock analyst report.",
    "confidence": 0.72,
}


def _make_fake_llm(synthesis_bull="Bull thesis.", synthesis_bear="Bear thesis."):
    """Return a callable that mimics the LLM interface used by DebateEngine."""

    def _call(req):
        agent = getattr(req, "agent_id", "") or ""
        if "synthesis" in agent:
            return MagicMock(content=json.dumps({
                "bull_case": synthesis_bull,
                "bear_case": synthesis_bear,
            }))
        if "bull_advocate" in agent:
            return MagicMock(content=json.dumps({"argument": "Bull wins.", "score": 0.75}))
        if "bear_advocate" in agent:
            return MagicMock(content=json.dumps({"argument": "Bear loses.", "score": 0.45}))
        if "arbiter" in agent:
            return MagicMock(content=json.dumps({
                "verdict": "BUY",
                "confidence": 0.70,
                "reasoning": "Bull score dominated.",
            }))
        return MagicMock(content=json.dumps({"score": 0.5, "signals": [], "reasoning": "n/a", "confidence": 0.5}))

    return _call


def _patch_all_analysts():
    """Return a context-manager stack that replaces all 4 analyst analyze() calls."""
    return [
        patch(
            f"tools.fathomdesk.agents.{mod}.{cls}.analyze",
            return_value=_FAKE_REPORT,
        )
        for mod, cls in [
            ("fundamentals_agent", "FundamentalsAgent"),
            ("technical_agent", "TechnicalAgent"),
            ("sentiment_agent", "SentimentAgent"),
            ("macro_agent", "MacroAgent"),
        ]
    ]


# ---------------------------------------------------------------------------
# (1) DebateEngine returns structured output
# ---------------------------------------------------------------------------

class TestDebateEngineStructuredOutput:
    """Confirm DebateEngine.run() returns a fully-populated DebateResult."""

    def test_debate_result_has_required_fields(self):
        from tools.fathomdesk.agents.debate_engine import DebateEngine

        with patch("tools.fathomdesk.agents.debate_engine.get_llm") as mock_get_llm, \
             patch("tools.fathomdesk.agents.fundamentals_agent.FundamentalsAgent.analyze", return_value=_FAKE_REPORT), \
             patch("tools.fathomdesk.agents.technical_agent.TechnicalAgent.analyze", return_value=_FAKE_REPORT), \
             patch("tools.fathomdesk.agents.sentiment_agent.SentimentAgent.analyze", return_value=_FAKE_REPORT), \
             patch("tools.fathomdesk.agents.macro_agent.MacroAgent.analyze", return_value=_FAKE_REPORT):

            mock_get_llm.return_value = _make_fake_llm()
            engine = DebateEngine(ticker="AAPL", as_of_date="2026-05-01")
            result = engine.run()

        assert isinstance(result, DebateResult)
        assert result.verdict in {"BUY", "SELL", "HOLD"}
        assert 0.0 <= result.confidence <= 1.0
        assert isinstance(result.bull_case, str) and result.bull_case
        assert isinstance(result.bear_case, str) and result.bear_case
        assert isinstance(result.bull_argument, str)
        assert isinstance(result.bear_argument, str)
        assert 0.0 <= result.bull_score <= 1.0
        assert 0.0 <= result.bear_score <= 1.0
        assert isinstance(result.reasoning, str)
        assert isinstance(result.analyst_reports, dict)
        assert set(result.analyst_reports) >= {"fundamentals", "technical", "sentiment", "macro"}

    def test_debate_result_to_dict_keys(self):
        """to_dict() must include all 10 required keys."""
        from tools.fathomdesk.agents.debate_engine import DebateEngine

        with patch("tools.fathomdesk.agents.debate_engine.get_llm") as mock_get_llm, \
             patch("tools.fathomdesk.agents.fundamentals_agent.FundamentalsAgent.analyze", return_value=_FAKE_REPORT), \
             patch("tools.fathomdesk.agents.technical_agent.TechnicalAgent.analyze", return_value=_FAKE_REPORT), \
             patch("tools.fathomdesk.agents.sentiment_agent.SentimentAgent.analyze", return_value=_FAKE_REPORT), \
             patch("tools.fathomdesk.agents.macro_agent.MacroAgent.analyze", return_value=_FAKE_REPORT):

            mock_get_llm.return_value = _make_fake_llm()
            engine = DebateEngine(ticker="MSFT", as_of_date="2026-05-01")
            result = engine.run()

        d = result.to_dict()
        required_keys = {
            "verdict", "confidence", "bull_case", "bear_case",
            "bull_argument", "bear_argument", "bull_score", "bear_score",
            "reasoning", "analyst_reports",
        }
        missing = required_keys - set(d)
        assert not missing, f"to_dict() is missing keys: {missing}"

    def test_debate_result_verdict_valid_on_analyst_failure(self):
        """DebateEngine falls back gracefully when an analyst raises an exception."""
        from tools.fathomdesk.agents.debate_engine import DebateEngine

        def _bad_analyze(**kwargs):
            raise RuntimeError("Simulated analyst failure")

        with patch("tools.fathomdesk.agents.debate_engine.get_llm") as mock_get_llm, \
             patch("tools.fathomdesk.agents.fundamentals_agent.FundamentalsAgent.analyze", side_effect=_bad_analyze), \
             patch("tools.fathomdesk.agents.technical_agent.TechnicalAgent.analyze", return_value=_FAKE_REPORT), \
             patch("tools.fathomdesk.agents.sentiment_agent.SentimentAgent.analyze", return_value=_FAKE_REPORT), \
             patch("tools.fathomdesk.agents.macro_agent.MacroAgent.analyze", return_value=_FAKE_REPORT):

            mock_get_llm.return_value = _make_fake_llm()
            engine = DebateEngine(ticker="GOOG", as_of_date="2026-05-01")
            result = engine.run()

        assert result.verdict in {"BUY", "SELL", "HOLD"}
        assert 0.0 <= result.confidence <= 1.0


# ---------------------------------------------------------------------------
# (2) RiskManager applies Gate A (min_confidence floor)
# ---------------------------------------------------------------------------

class TestRiskManagerGate0Floor:
    """Confirm RiskManager Gate A overrides low-confidence BUY/SELL to HOLD."""

    def _make_debate_result(self, verdict: str, confidence: float) -> DebateResult:
        return DebateResult(
            bull_case="Bull case.",
            bear_case="Bear case.",
            bull_argument="Bull argument.",
            bear_argument="Bear argument.",
            bull_score=0.6 if verdict != "SELL" else 0.4,
            bear_score=0.4 if verdict != "SELL" else 0.6,
            verdict=verdict,
            confidence=confidence,
            reasoning="Test arbitration.",
            analyst_reports={
                "fundamentals": _FAKE_REPORT,
                "technical": _FAKE_REPORT,
                "sentiment": _FAKE_REPORT,
                "macro": _FAKE_REPORT,
            },
        )

    def test_risk_manager_low_confidence_buy_becomes_hold(self, monkeypatch, tmp_path):
        """Gate A: BUY with confidence 0.30 < default 0.60 threshold → HOLD."""
        from tools.fathomdesk.agents.risk_manager import RiskManager

        db_path = tmp_path / "test_rm.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ad_decision_audit ("
            "id TEXT PRIMARY KEY, ticker TEXT, as_of_date TEXT, "
            "fundamentals_score REAL, technical_score REAL, sentiment_score REAL, macro_score REAL, "
            "bull_confidence REAL, bear_confidence REAL, "
            "final_direction TEXT, final_confidence REAL, reasoning TEXT, "
            "venue TEXT, instrument_type TEXT, mifid_timestamp TEXT, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.commit()
        conn.close()

        monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

        with patch("tools.fathomdesk.agents.risk_manager.get_connection") as mock_conn:
            mock_conn.return_value = _translating_connect(db_path)

            rm = RiskManager(ticker="AAPL", as_of_date="2026-05-01")
            # min_confidence default is 0.60; 0.30 triggers Gate A
            low_conf_result = self._make_debate_result("BUY", confidence=0.30)
            rec = rm.arbitrate(low_conf_result)

        assert rec["direction"] == "HOLD", (
            f"Expected Gate A to override low-confidence BUY to HOLD, got: {rec['direction']}"
        )
        assert rec["size_modifier"] == 0.0
        assert "Gate A" in rec["reasoning"]

    def test_risk_manager_passes_high_confidence_buy(self, tmp_path):
        """Gate A: BUY with confidence 0.80 >= 0.60 passes through unchanged."""
        from tools.fathomdesk.agents.risk_manager import RiskManager

        db_path = tmp_path / "test_rm2.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ad_decision_audit ("
            "id TEXT PRIMARY KEY, ticker TEXT, as_of_date TEXT, "
            "fundamentals_score REAL, technical_score REAL, sentiment_score REAL, macro_score REAL, "
            "bull_confidence REAL, bear_confidence REAL, "
            "final_direction TEXT, final_confidence REAL, reasoning TEXT, "
            "venue TEXT, instrument_type TEXT, mifid_timestamp TEXT, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.commit()
        conn.close()

        with patch("tools.fathomdesk.agents.risk_manager.get_connection") as mock_conn:
            mock_conn.return_value = _translating_connect(db_path)

            rm = RiskManager(ticker="MSFT", as_of_date="2026-05-01")
            high_conf_result = self._make_debate_result("BUY", confidence=0.80)
            rec = rm.arbitrate(high_conf_result)

        assert rec["direction"] == "BUY", (
            f"High-confidence BUY should pass Gate A but got: {rec['direction']}"
        )
        assert rec["size_modifier"] == 1.0

    def test_risk_manager_hold_gets_zero_size_modifier(self, tmp_path):
        """HOLD verdict should always have size_modifier=0.0 regardless of confidence."""
        from tools.fathomdesk.agents.risk_manager import RiskManager

        db_path = tmp_path / "test_rm3.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ad_decision_audit ("
            "id TEXT PRIMARY KEY, ticker TEXT, as_of_date TEXT, "
            "fundamentals_score REAL, technical_score REAL, sentiment_score REAL, macro_score REAL, "
            "bull_confidence REAL, bear_confidence REAL, "
            "final_direction TEXT, final_confidence REAL, reasoning TEXT, "
            "venue TEXT, instrument_type TEXT, mifid_timestamp TEXT, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.commit()
        conn.close()

        with patch("tools.fathomdesk.agents.risk_manager.get_connection") as mock_conn:
            mock_conn.return_value = _translating_connect(db_path)

            rm = RiskManager(ticker="TSLA", as_of_date="2026-05-01")
            hold_result = self._make_debate_result("HOLD", confidence=0.90)
            rec = rm.arbitrate(hold_result)

        assert rec["direction"] == "HOLD"
        assert rec["size_modifier"] == 0.0


# ---------------------------------------------------------------------------
# (3) ad_decision_audit row written after a full run
# ---------------------------------------------------------------------------

class TestDecisionAuditRow:
    """Confirm ad_decision_audit receives a row after RiskManager.arbitrate()."""

    def _create_audit_db(self, db_path):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS ad_decision_audit ("
            "id TEXT PRIMARY KEY, ticker TEXT NOT NULL, as_of_date TEXT NOT NULL, "
            "fundamentals_score REAL, technical_score REAL, sentiment_score REAL, macro_score REAL, "
            "bull_confidence REAL, bear_confidence REAL, "
            "final_direction TEXT, final_confidence REAL, reasoning TEXT, "
            "venue TEXT, instrument_type TEXT, mifid_timestamp TEXT, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);"
            "CREATE TABLE IF NOT EXISTS ad_macro_regimes ("
            "id TEXT PRIMARY KEY, regime_type TEXT NOT NULL, active INTEGER DEFAULT 0, "
            "evidence_json TEXT DEFAULT '{}', confidence REAL DEFAULT 0.0, created_at TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS ad_trap_events ("
            "id TEXT PRIMARY KEY, ticker TEXT, pattern TEXT, broken_level REAL, "
            "confidence REAL, bar_age INTEGER, timeframe TEXT, evidence_json TEXT, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now')));"
            "CREATE TABLE IF NOT EXISTS ad_tax_wash_sale_flags ("
            "id TEXT PRIMARY KEY, ticker TEXT NOT NULL, sale_date TEXT NOT NULL, "
            "wash_sale_detected INTEGER DEFAULT 0, lot_ids TEXT, created_at TEXT "
            "NOT NULL DEFAULT (datetime('now')));"
        )
        conn.close()
        return db_path

    def test_decaudit_row_written_after_arbitrate(self, tmp_path):
        """After RiskManager.arbitrate(), ad_decision_audit must have ≥1 row for the ticker."""
        from tools.fathomdesk.agents.risk_manager import RiskManager

        db_path = self._create_audit_db(tmp_path / "audit_test.db")

        def _get_fresh_conn():
            return _translating_connect(db_path)

        debate_result = DebateResult(
            bull_case="Strong fundamentals.",
            bear_case="Rising rates.",
            bull_argument="EPS growth beats estimates.",
            bear_argument="Macro headwinds persist.",
            bull_score=0.72,
            bear_score=0.48,
            verdict="BUY",
            confidence=0.75,
            reasoning="Bull case dominant.",
            analyst_reports={
                "fundamentals": _FAKE_REPORT,
                "technical": _FAKE_REPORT,
                "sentiment": _FAKE_REPORT,
                "macro": _FAKE_REPORT,
            },
        )

        with patch("tools.fathomdesk.agents.risk_manager.get_connection", side_effect=_get_fresh_conn):
            rm = RiskManager(ticker="NVDA", as_of_date="2026-05-01")
            rec = rm.arbitrate(debate_result)

        assert rec["direction"] in {"BUY", "SELL", "HOLD"}

        # Verify the audit row was actually written to the DB
        check_conn = sqlite3.connect(str(db_path))
        check_conn.row_factory = sqlite3.Row
        rows = check_conn.execute(
            "SELECT * FROM ad_decision_audit WHERE ticker = 'NVDA'"
        ).fetchall()
        check_conn.close()

        assert len(rows) >= 1, "Expected ≥1 row in ad_decision_audit after arbitrate()"
        row = dict(rows[0])
        assert row["ticker"] == "NVDA"
        assert row["as_of_date"] == "2026-05-01"
        assert row["final_direction"] in {"BUY", "SELL", "HOLD"}
        assert row["id"].startswith("ada-")

    def test_decaudit_includes_all_analyst_scores(self, tmp_path):
        """ad_decision_audit row must record all 4 analyst scores."""
        from tools.fathomdesk.agents.risk_manager import RiskManager

        db_path = self._create_audit_db(tmp_path / "audit_scores.db")

        def _get_fresh_conn():
            return _translating_connect(db_path)

        debate_result = DebateResult(
            bull_case="Bull.",
            bear_case="Bear.",
            bull_argument="Up.",
            bear_argument="Down.",
            bull_score=0.70,
            bear_score=0.50,
            verdict="BUY",
            confidence=0.80,
            reasoning="Test.",
            analyst_reports={
                "fundamentals": {"score": 0.71, "signals": [], "reasoning": "", "confidence": 0.8},
                "technical":    {"score": 0.62, "signals": [], "reasoning": "", "confidence": 0.7},
                "sentiment":    {"score": 0.58, "signals": [], "reasoning": "", "confidence": 0.6},
                "macro":        {"score": 0.55, "signals": [], "reasoning": "", "confidence": 0.65},
            },
        )

        with patch("tools.fathomdesk.agents.risk_manager.get_connection", side_effect=_get_fresh_conn):
            rm = RiskManager(ticker="AMD", as_of_date="2026-05-01")
            rm.arbitrate(debate_result)

        check_conn = sqlite3.connect(str(db_path))
        check_conn.row_factory = sqlite3.Row
        row = dict(check_conn.execute(
            "SELECT * FROM ad_decision_audit WHERE ticker = 'AMD' LIMIT 1"
        ).fetchone())
        check_conn.close()

        assert abs(row["fundamentals_score"] - 0.71) < 0.001
        assert abs(row["technical_score"] - 0.62) < 0.001
        assert abs(row["sentiment_score"] - 0.58) < 0.001
        assert abs(row["macro_score"] - 0.55) < 0.001


# ---------------------------------------------------------------------------
# (4) ANALYST_PANEL_ENABLED=False falls back to heuristics without error
# ---------------------------------------------------------------------------

class TestAnalystPanelDisabledFallback:
    """Confirm analyst_panel.run() uses heuristic path when panel is disabled."""

    def test_disabled_panel_returns_heuristic_source(self, monkeypatch):
        """ANALYST_PANEL_ENABLED=False → rec['source'] == 'heuristic', no error."""
        monkeypatch.setenv("ANALYST_PANEL_ENABLED", "false")

        from tools.fathomdesk import analyst_panel
        # Reload to pick up env var (module-level _panel_enabled is a function call)
        signals = [
            {"score": 0.65, "confidence": 0.70, "type": "momentum", "ticker": "AAPL"},
            {"score": 0.70, "confidence": 0.75, "type": "breakout", "ticker": "AAPL"},
        ]
        rec = analyst_panel.run(ticker="AAPL", as_of_date="2026-05-01", signals=signals)

        assert rec["source"] == "heuristic"
        assert rec["direction"] in {"BUY", "SELL", "HOLD"}
        assert 0.0 <= rec["confidence"] <= 1.0
        assert 0.0 <= rec["size_modifier"] <= 1.0
        assert isinstance(rec["reasoning"], str)

    def test_disabled_panel_no_signals_returns_hold(self, monkeypatch):
        """ANALYST_PANEL_ENABLED=False with no signals → HOLD with source='heuristic'."""
        monkeypatch.setenv("ANALYST_PANEL_ENABLED", "false")

        from tools.fathomdesk import analyst_panel
        rec = analyst_panel.run(ticker="TSLA", as_of_date="2026-05-01", signals=[])

        assert rec["source"] == "heuristic"
        assert rec["direction"] == "HOLD"
        assert rec["confidence"] == 0.0
        assert rec["size_modifier"] == 0.0

    def test_disabled_panel_does_not_call_debate_engine(self, monkeypatch):
        """When panel is disabled, DebateEngine must not be instantiated."""
        monkeypatch.setenv("ANALYST_PANEL_ENABLED", "false")

        from tools.fathomdesk import analyst_panel
        with patch("tools.fathomdesk.analyst_panel.DebateEngine", create=True) as mock_de:
            analyst_panel.run(ticker="GOOG", as_of_date="2026-05-01")

        mock_de.assert_not_called()

    def test_enabled_panel_calls_debate_engine(self, monkeypatch, tmp_path):
        """When ANALYST_PANEL_ENABLED=true, DebateEngine and RiskManager are invoked."""
        monkeypatch.setenv("ANALYST_PANEL_ENABLED", "true")

        from tools.fathomdesk import analyst_panel

        fake_result = DebateResult(
            bull_case="Bull.", bear_case="Bear.",
            bull_argument="Up.", bear_argument="Down.",
            bull_score=0.7, bear_score=0.5,
            verdict="BUY", confidence=0.75,
            reasoning="Panel says BUY.",
            analyst_reports={
                "fundamentals": _FAKE_REPORT, "technical": _FAKE_REPORT,
                "sentiment": _FAKE_REPORT, "macro": _FAKE_REPORT,
            },
        )

        db_path = tmp_path / "panel_test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ad_decision_audit ("
            "id TEXT PRIMARY KEY, ticker TEXT, as_of_date TEXT, "
            "fundamentals_score REAL, technical_score REAL, sentiment_score REAL, macro_score REAL, "
            "bull_confidence REAL, bear_confidence REAL, "
            "final_direction TEXT, final_confidence REAL, reasoning TEXT, "
            "venue TEXT, instrument_type TEXT, mifid_timestamp TEXT, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ad_macro_regimes ("
            "id TEXT PRIMARY KEY, regime_type TEXT, active INTEGER, "
            "evidence_json TEXT, confidence REAL, created_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ad_trap_events ("
            "id TEXT PRIMARY KEY, ticker TEXT, pattern TEXT, broken_level REAL, "
            "confidence REAL, bar_age INTEGER, timeframe TEXT, evidence_json TEXT, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS ad_tax_wash_sale_flags ("
            "id TEXT PRIMARY KEY, ticker TEXT, sale_date TEXT, wash_sale_detected INTEGER, "
            "lot_ids TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        conn.commit()
        conn.close()

        def _fresh_conn():
            return _translating_connect(db_path)

        with patch("tools.fathomdesk.agents.debate_engine.DebateEngine.run", return_value=fake_result) as mock_run, \
             patch("tools.fathomdesk.agents.risk_manager.get_connection", side_effect=_fresh_conn):
            rec = analyst_panel.run(ticker="AAPL", as_of_date="2026-05-01")

        mock_run.assert_called_once()
        assert rec["source"] == "analyst_panel"
        assert rec["direction"] in {"BUY", "SELL", "HOLD"}
