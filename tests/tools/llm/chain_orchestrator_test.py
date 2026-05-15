# CUI // SP-CTI
"""Unit tests for ChainOrchestrator (CoT / CoD).

Mock the LLM router so no real API calls are made.
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from tools.llm.chain_orchestrator import ChainOrchestrator, ChainResult, BudgetExceededError
from tools.llm.provider import LLMRequest, LLMResponse


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_router():
    """Return a mock LLMRouter with deterministic responses."""
    router = MagicMock()
    router._config = {
        "chain_orchestration": {
            "enabled": True,
            "cost_cap_usd": 0.50,
            "token_cap": 32000,
            "timeout_seconds": 120,
            "cot": {
                "enabled": True,
                "max_rounds": 2,
                "self_consistency_runs": 1,
                "reasoner_model": "qwen3-local",
                "critic_model": "claude-sonnet",
                "synthesizer_model": "claude-sonnet",
                "excluded_functions": ["pulse_generation"],
                "per_function": {},
            },
            "cod": {
                "enabled": True,
                "num_debaters": 3,
                "debate_rounds": 1,
                "judge_model": "claude-sonnet",
                "debater_models": ["qwen3-local", "claude-sonnet", "openai-gpt4o"],
                "excluded_functions": ["pulse_generation"],
                "per_function": {},
            },
        },
    }
    router.get_model_pricing.return_value = {
        "input_per_1k": 0.001,
        "output_per_1k": 0.002,
    }

    # Mock _invoke_model_direct to return deterministic responses
    def mock_invoke(model_name, request):
        return LLMResponse(
            content=f"response from {model_name}",
            model_id=model_name,
            input_tokens=100,
            output_tokens=50,
            provider="mock",
        )

    router._invoke_model_direct = mock_invoke
    return router


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite DB with the minimal schema."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS llm_chain_telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            function TEXT NOT NULL,
            chain_mode TEXT NOT NULL,
            models_used TEXT NOT NULL DEFAULT '[]',
            rounds TEXT NOT NULL DEFAULT '{}',
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0.0,
            duration_ms INTEGER DEFAULT 0,
            final_model_id TEXT,
            stop_reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_chain_telemetry_function ON llm_chain_telemetry (function);
        CREATE INDEX IF NOT EXISTS idx_chain_telemetry_created ON llm_chain_telemetry (created_at);
        """
    )
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# CoT Tests
# ---------------------------------------------------------------------------


class TestChainOfThought:
    def test_cot_returns_synthesized_response(self, mock_router, tmp_db):
        """CoT returns final synthesized response after N rounds."""
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: sqlite3.connect(str(tmp_db))):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test prompt"}])
            result = orch.invoke_chain_of_thought("code_generation", req)

        assert isinstance(result, ChainResult)
        assert result.chain_mode == "cot"
        assert result.content != ""
        assert result.stop_reason == "completed"
        assert result.total_input_tokens > 0
        assert result.total_output_tokens > 0
        assert len(result.rounds) > 0
        assert "qwen3-local" in result.models_used

    def test_cot_budget_cap_aborts(self, mock_router, tmp_db):
        """Cost cap aborts chain when exceeded."""
        mock_router._config["chain_orchestration"]["cost_cap_usd"] = 0.0001
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: sqlite3.connect(str(tmp_db))):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test prompt"}])
            with pytest.raises(BudgetExceededError):
                orch.invoke_chain_of_thought("code_generation", req)

    def test_cot_token_cap_aborts(self, mock_router, tmp_db):
        """Token cap aborts chain when exceeded."""
        mock_router._config["chain_orchestration"]["token_cap"] = 10
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: sqlite3.connect(str(tmp_db))):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test prompt"}])
            with pytest.raises(BudgetExceededError):
                orch.invoke_chain_of_thought("code_generation", req)

    def test_cot_excluded_function_raises(self, mock_router, tmp_db):
        """Excluded functions raise RuntimeError."""
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: sqlite3.connect(str(tmp_db))):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test prompt"}])
            with pytest.raises(RuntimeError, match="excluded from CoT"):
                orch.invoke_chain_of_thought("pulse_generation", req)

    def test_cot_telemetry_written(self, mock_router, tmp_db):
        """Telemetry rows contain correct round data."""
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: sqlite3.connect(str(tmp_db))):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test prompt"}])
            result = orch.invoke_chain_of_thought("code_generation", req)

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM llm_chain_telemetry WHERE session_id = ?",
            (result.trace_id,),
        ).fetchall()
        conn.close()

        assert len(rows) > 0
        for row in rows:
            assert row["function"] == "code_generation"
            assert row["chain_mode"] in ("cot", "cot_self_consistency")
            assert row["input_tokens"] >= 0
            assert row["output_tokens"] >= 0

    def test_cot_self_consistency(self, mock_router, tmp_db):
        """Self-consistency picks majority answer."""
        mock_router._config["chain_orchestration"]["cot"]["self_consistency_runs"] = 3
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: sqlite3.connect(str(tmp_db))):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test prompt"}])
            result = orch.invoke_chain_of_thought("code_generation", req)

        assert result.chain_mode == "cot_self_consistency"
        assert result.content != ""


# ---------------------------------------------------------------------------
# CoD Tests
# ---------------------------------------------------------------------------


class TestChainOfDebate:
    def test_cod_returns_judged_response(self, mock_router, tmp_db):
        """CoD returns judged response after debate rounds."""
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: sqlite3.connect(str(tmp_db))):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test prompt"}])
            result = orch.invoke_chain_of_debate("architecture_review", req)

        assert isinstance(result, ChainResult)
        assert result.chain_mode == "cod"
        assert result.content != ""
        assert result.stop_reason == "completed"
        assert result.total_input_tokens > 0
        assert result.total_output_tokens > 0
        assert len(result.rounds) > 0

    def test_cod_excluded_function_raises(self, mock_router, tmp_db):
        """Excluded functions raise RuntimeError."""
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: sqlite3.connect(str(tmp_db))):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test prompt"}])
            with pytest.raises(RuntimeError, match="excluded from CoD"):
                orch.invoke_chain_of_debate("pulse_generation", req)

    def test_cod_telemetry_written(self, mock_router, tmp_db):
        """Telemetry rows contain correct round data."""
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: sqlite3.connect(str(tmp_db))):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test prompt"}])
            result = orch.invoke_chain_of_debate("architecture_review", req)

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM llm_chain_telemetry WHERE session_id = ?",
            (result.trace_id,),
        ).fetchall()
        conn.close()

        assert len(rows) > 0
        for row in rows:
            assert row["function"] == "architecture_review"
            assert row["chain_mode"] == "cod"


# ---------------------------------------------------------------------------
# Decision Type Tests
# ---------------------------------------------------------------------------


class TestCanvasDecisionRecording:
    def test_record_canvas_decision_accepts_chain_types(self, tmp_db):
        """record_canvas_decision accepts chain_of_thought and chain_of_debate."""
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: sqlite3.connect(str(tmp_db))):
            orch = ChainOrchestrator(router=MagicMock())
            orch._record_canvas_decision(
                decision_type="chain_of_thought",
                decision="test decision",
                rationale="test rationale",
                model_used="qwen3-local",
                confidence=0.85,
                alternatives=["alt1", "alt2"],
            )
            orch._record_canvas_decision(
                decision_type="chain_of_debate",
                decision="test decision",
                rationale="test rationale",
                model_used="claude-sonnet",
                confidence=0.72,
                alternatives=["alt1"],
            )
        # Best-effort — no exception means success


# ---------------------------------------------------------------------------
# CLI Tests
# ---------------------------------------------------------------------------


class TestChainOrchestratorCLI:
    def test_show_config(self, capsys):
        """--show-config outputs chain_orchestration config."""
        with patch(
            "sys.argv",
            ["chain_orchestrator.py", "--show-config", "--function", "test", "--prompt", "test"],
        ):
            with patch("tools.llm.chain_orchestrator.LLMRouter") as MockRouter:
                MockRouter.return_value._config = {
                    "chain_orchestration": {"enabled": True}
                }
                from tools.llm.chain_orchestrator import main

                main()
        captured = capsys.readouterr()
        assert "enabled" in captured.out


# ---------------------------------------------------------------------------
# Router Integration Tests
# ---------------------------------------------------------------------------


class TestRouterIntegration:
    def test_router_invokes_cot_when_chain_mode_set(self):
        """Router.invoke() dispatches to CoT when request.chain_mode='cot'."""
        from tools.llm.router import LLMRouter

        router = LLMRouter.__new__(LLMRouter)
        router._config = {
            "chain_orchestration": {
                "enabled": True,
                "cost_cap_usd": 0.50,
                "token_cap": 32000,
                "timeout_seconds": 120,
                "cot": {
                    "enabled": True,
                    "max_rounds": 1,
                    "self_consistency_runs": 1,
                    "reasoner_model": "qwen3-local",
                    "critic_model": "claude-sonnet",
                    "synthesizer_model": "claude-sonnet",
                    "excluded_functions": [],
                    "per_function": {},
                },
            },
        }
        router._availability_cache = {}
        router._providers = {}

        with patch.object(router, "invoke_chain_of_thought") as mock_cot:
            mock_cot.return_value = LLMResponse(content="cot result")
            req = LLMRequest(
                messages=[{"role": "user", "content": "test"}],
                chain_mode="cot",
            )
            resp = router.invoke("code_generation", req)
            assert resp.content == "cot result"

    def test_router_invokes_cod_when_chain_mode_set(self):
        """Router.invoke() dispatches to CoD when request.chain_mode='cod'."""
        from tools.llm.router import LLMRouter

        router = LLMRouter.__new__(LLMRouter)
        router._config = {
            "chain_orchestration": {
                "enabled": True,
                "cost_cap_usd": 0.50,
                "token_cap": 32000,
                "timeout_seconds": 120,
                "cod": {
                    "enabled": True,
                    "num_debaters": 2,
                    "debate_rounds": 1,
                    "judge_model": "claude-sonnet",
                    "debater_models": ["qwen3-local", "claude-sonnet"],
                    "excluded_functions": [],
                    "per_function": {},
                },
            },
        }
        router._availability_cache = {}
        router._providers = {}

        with patch.object(router, "invoke_chain_of_debate") as mock_cod:
            mock_cod.return_value = LLMResponse(content="cod result")
            req = LLMRequest(
                messages=[{"role": "user", "content": "test"}],
                chain_mode="cod",
            )
            resp = router.invoke("architecture_review", req)
            assert resp.content == "cod result"
