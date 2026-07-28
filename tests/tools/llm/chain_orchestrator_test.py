# CUI // SP-CTI
"""Unit tests for ChainOrchestrator (CoT / CoD).

Mock the LLM router so no real API calls are made.
"""

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from tools.llm.chain_orchestrator import ChainOrchestrator, ChainResult, BudgetExceededError
from tools.llm.provider import LLMRequest, LLMResponse

# Captured at import time — BEFORE the autouse fixture patches the method — so a
# test can opt back into the REAL budget-gate body instead of the no-op stub.
_REAL_CHECK_MODULE_BUDGET = ChainOrchestrator._check_module_budget


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_db_side_effects():
    """Suppress all DB-dependent cross-cutting concerns (event bus, module budget).

    These call PostgreSQL which times out in unit tests. Patching them here means
    all tests in this module bypass those network calls without individual boilerplate.
    The patched methods are non-functional (no-op) — they don't affect test assertions.
    """
    with patch("tools.llm.chain_orchestrator.ChainOrchestrator._publish_reasoning_event"), \
         patch("tools.llm.chain_orchestrator.ChainOrchestrator._check_module_budget"), \
         patch("tools.llm.chain_orchestrator.ChainOrchestrator._record_canvas_decision"), \
         patch("tools.budget.module_budget_tracker.record_module_usage"):
        yield


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
                # Role keys — point at routing chain keys so _invoke_model uses invoke_for_role
                "reasoner_role": "cot_reasoner",
                "critic_role": "cot_critic",
                "synthesizer_role": "cot_synthesizer",
                # Legacy fallbacks (used when role key is null in per_function overrides)
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
                "judge_role": "cod_judge",
                "debater_pool_role": "cod_debater_pool",
                "judge_model": "claude-sonnet",
                "debater_models": ["qwen3-local", "claude-sonnet", "openai-gpt4o"],
                "excluded_functions": ["pulse_generation"],
                "per_function": {},
            },
            "council": {
                "enabled": True,
                "num_advisors": 5,
                "chairman_role": "council_chairman",
                "advisor_pool_role": "council_advisor_pool",
                "chairman_model": "claude-sonnet",
                "advisor_models": ["qwen3-local", "claude-sonnet", "openai-gpt4o"],
                "excluded_functions": ["pulse_generation"],
                "per_function": {},
            },
            "divergence": {
                # enabled TRUE here only so the mode is exercisable in tests; the
                # SHIPPED default in args/llm_config.yaml is FALSE (opt-in).
                "enabled": True,
                "num_branches": 6,
                "frame_set": "generative",
                "branch_pool_role": "divergence_branch_pool",
                "critic_role": "divergence_critic",
                # Mode-level fan-out caps, mirroring the shipped config shape:
                # 6 branches x the 0.50 outer per-call cap (dvg-core-02).
                "cost_cap_usd": 3.00,
                "timeout_seconds": 180,
                "branch_models": ["qwen3-local", "claude-sonnet", "gpt-4o"],
                "excluded_functions": ["pulse_generation"],
                "per_function": {},
            },
        },
        # routing: dict enables _invoke_model to detect role keys vs direct model names
        "routing": {
            "cot_reasoner":      {"chain": ["deepseek-local", "qwen3-local"]},
            "cot_critic":        {"chain": ["claude-sonnet", "qwen3-local"]},
            "cot_synthesizer":   {"chain": ["claude-sonnet", "qwen3-local"]},
            "cod_debater_pool":  {"chain": ["qwen3-local", "llama-local", "claude-haiku"]},
            "cod_judge":         {"chain": ["claude-sonnet", "qwen3-local"]},
            "council_advisor_pool": {"chain": ["qwen3-local", "llama-local", "claude-haiku", "deepseek-local", "gpt-4o-mini"]},
            "council_chairman":     {"chain": ["claude-sonnet", "qwen3-local"]},
        },
    }
    router.get_model_pricing.return_value = {
        "input_per_1k": 0.001,
        "output_per_1k": 0.002,
    }

    # invoke_for_role: simulates full chain routing — returns response tagged with role key
    def mock_invoke_for_role(role_key, function, request):
        return LLMResponse(
            content=f"response from role {role_key}",
            model_id=role_key,
            input_tokens=100,
            output_tokens=50,
            provider="mock",
        )

    # get_diverse_models: returns 3 distinct mock model names for CoD debaters
    def mock_get_diverse_models(role_key, count):
        pool = ["qwen3-local", "claude-haiku", "llama-local", "deepseek-local"]
        return pool[:min(count, len(pool))]

    # _invoke_model_direct: legacy path for per_function direct model name overrides.
    # Accepts the function kwarg the real _invoke_model passes (function=function).
    def mock_invoke_direct(model_name, request, function=None):
        return LLMResponse(
            content=f"response from {model_name}",
            model_id=model_name,
            input_tokens=100,
            output_tokens=50,
            provider="mock",
        )

    router.invoke_for_role = mock_invoke_for_role
    router.get_diverse_models = mock_get_diverse_models
    router._invoke_model_direct = mock_invoke_direct
    return router


def storage_conn(db_path):
    """Open the temp DB through the same wrapper the real get_connection returns.

    chain_orchestrator authors PostgreSQL SQL (``%s`` placeholders), which is
    the house style. A raw ``sqlite3.connect`` rejects those with
    "Incorrect number of bindings" — and the telemetry writer catches and logs
    the failure, so the rows silently never land. StorageConnection applies the
    ``%s`` → ``?`` translation, so the tests exercise the real write path.
    """
    from tools.db.storage import StorageConnection

    return StorageConnection(sqlite3.connect(str(db_path)), "sqlite")


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
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
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

        # Verify each CoT role resolved to a DISTINCT model/role key.
        # mock_invoke_for_role tags model_id with the role_key so we can confirm
        # reasoner, critic, and synthesizer each used a different chain.
        role_ids = [r["model_id"] for r in result.rounds if "model_id" in r]
        assert len(role_ids) >= 2, "Expected at least reasoner + synthesizer roles"
        assert len(set(role_ids)) > 1, (
            f"CoT should use distinct LLMs per role, got: {role_ids}"
        )

    def test_cot_budget_cap_aborts(self, mock_router, tmp_db):
        """Cost cap aborts chain when exceeded."""
        mock_router._config["chain_orchestration"]["cost_cap_usd"] = 0.0001
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test prompt"}])
            with pytest.raises(BudgetExceededError):
                orch.invoke_chain_of_thought("code_generation", req)

    def test_cot_token_cap_aborts(self, mock_router, tmp_db):
        """Token cap aborts chain when exceeded."""
        mock_router._config["chain_orchestration"]["token_cap"] = 10
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test prompt"}])
            with pytest.raises(BudgetExceededError):
                orch.invoke_chain_of_thought("code_generation", req)

    def test_cot_excluded_function_raises(self, mock_router, tmp_db):
        """Excluded functions raise RuntimeError."""
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test prompt"}])
            with pytest.raises(RuntimeError, match="excluded from CoT"):
                orch.invoke_chain_of_thought("pulse_generation", req)

    def test_cot_telemetry_written(self, mock_router, tmp_db):
        """Telemetry rows contain correct round data."""
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
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
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
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
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
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
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test prompt"}])
            with pytest.raises(RuntimeError, match="excluded from CoD"):
                orch.invoke_chain_of_debate("pulse_generation", req)

    def test_cod_telemetry_written(self, mock_router, tmp_db):
        """Telemetry rows contain correct round data."""
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
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
# Council Tests
# ---------------------------------------------------------------------------


class TestCouncil:
    def test_council_returns_chairman_verdict(self, mock_router, tmp_db):
        """Council returns a chairman-synthesized verdict after advisors
        respond and peer-review."""
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "Should we build X?"}])
            result = orch.invoke_council("idealab_council_query", req)

        assert isinstance(result, ChainResult)
        assert result.chain_mode == "council"
        assert result.content != ""
        assert result.stop_reason == "completed"
        assert result.total_input_tokens > 0
        assert result.total_output_tokens > 0

        # 5 advisor steps + at least 1 peer-review step + 1 chairman step
        advisor_steps = [r for r in result.rounds if str(r["step"]).startswith("advisor:")]
        peer_review_steps = [r for r in result.rounds if r["step"] == "peer_review"]
        chairman_steps = [r for r in result.rounds if r["step"] == "chairman"]
        assert len(advisor_steps) == 5
        assert len(peer_review_steps) == 5
        assert len(chairman_steps) == 1

        # All 5 fixed cognitive lenses show up, not generated stances
        advisor_names = {s["step"].split(":", 1)[1] for s in advisor_steps}
        assert advisor_names == {
            "The Contrarian", "The First Principles Thinker", "The Expansionist",
            "The Outsider", "The Executor",
        }

    def test_council_excluded_function_raises(self, mock_router, tmp_db):
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test prompt"}])
            with pytest.raises(RuntimeError, match="excluded from Council"):
                orch.invoke_council("pulse_generation", req)

    def test_council_telemetry_written(self, mock_router, tmp_db):
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test prompt"}])
            result = orch.invoke_council("idealab_council_query", req)

        conn = sqlite3.connect(str(tmp_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM llm_chain_telemetry WHERE session_id = ?",
            (result.trace_id,),
        ).fetchall()
        conn.close()

        assert len(rows) > 0
        for row in rows:
            assert row["function"] == "idealab_council_query"
            assert row["chain_mode"] == "council"

    def test_council_degrades_cleanly_when_all_advisors_fail(self, mock_router, tmp_db):
        """Regression: if every advisor call raises, invoke_council must
        return an empty-content ChainResult (stop_reason=all_advisors_failed)
        instead of raising or crashing on an empty anonymization/peer-review
        step -- callers (the MCP handler) treat this like any other
        unavailable-LLM case. Advisor calls resolve to direct pool model
        names, so they go through _invoke_model_direct, not invoke_for_role."""
        def always_fail(model_name, request):
            raise RuntimeError("simulated provider outage")

        mock_router._invoke_model_direct = always_fail
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test prompt"}])
            result = orch.invoke_council("idealab_council_query", req)

        assert result.content == ""
        assert result.stop_reason == "all_advisors_failed"

    def test_council_peer_review_sees_anonymized_labels_not_advisor_names(self, mock_router, tmp_db):
        """Regression: peer review must anonymize responses (Response A-E)
        -- an advisor's own name/persona must never leak into what the
        reviewer sees, or reviewers could defer to a persona instead of
        judging the argument on merit. Advisor/peer-review calls resolve to
        direct pool model names (get_diverse_models), so they go through
        _invoke_model_direct, not invoke_for_role (which is only used for the
        single fixed chairman role key)."""
        captured_review_prompts = []

        def capturing_invoke_direct(model_name, request, function=None):
            content = (request.messages[0]["content"] if request.messages else "")
            if "[RESPONSE" in content:
                captured_review_prompts.append(content)
            return LLMResponse(
                content="an independent advisor argument", model_id=model_name,
                input_tokens=100, output_tokens=50, provider="mock",
            )

        mock_router._invoke_model_direct = capturing_invoke_direct
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test prompt"}])
            orch.invoke_council("idealab_council_query", req)

        assert captured_review_prompts, "expected at least one peer-review prompt to be captured"
        for prompt in captured_review_prompts:
            assert "The Contrarian" not in prompt
            assert "The Executor" not in prompt
            assert "[RESPONSE A]" in prompt or "[RESPONSE B]" in prompt


class TestDivergence:
    def test_divergence_returns_idea_pool(self, mock_router, tmp_db):
        """Divergence returns a labeled raw idea pool from a single isolated
        generative fan-out (one round, no peer review, no synthesis)."""
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "How should we reduce latency?"}])
            result = orch.invoke_divergence("some_decision_function", req)

        assert isinstance(result, ChainResult)
        assert result.chain_mode == "divergence"
        assert result.content != ""
        assert result.stop_reason == "completed"
        assert result.total_input_tokens > 0
        assert result.total_output_tokens > 0

        # Every step is a branch — there must be NO peer-review, chairman, or judge
        # step. Strict isolation = one generative round only.
        branch_steps = [r for r in result.rounds if str(r["step"]).startswith("branch:")]
        assert len(branch_steps) == 6  # 6 default generative frames
        assert not any(r["step"] in ("peer_review", "chairman", "judge") for r in result.rounds)

        # Content is the raw pool, labeled by frame so a critic pass can attribute.
        assert "Divergent Idea Pool" in result.content
        assert "## Frame:" in result.content
        # Divergence does not self-score; confidence is owned by the critic pass.
        assert result.confidence == 0.0

    def test_divergence_disabled_by_default_raises(self, mock_router, tmp_db):
        """The SHIPPED default is opt-in (enabled: false). With enabled false,
        invoke_divergence must refuse rather than silently run a 5-10x-cost path."""
        mock_router._config["chain_orchestration"]["divergence"]["enabled"] = False
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test"}])
            with pytest.raises(RuntimeError, match="disabled"):
                orch.invoke_divergence("some_decision_function", req)

    def test_divergence_excluded_function_raises(self, mock_router, tmp_db):
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test"}])
            with pytest.raises(RuntimeError, match="excluded from Divergence"):
                orch.invoke_divergence("pulse_generation", req)

    def test_divergence_module_budget_aborts_before_spending(self, mock_router, tmp_db):
        """The module-budget gate must trip and abort BEFORE any branch model
        call — the headline cost risk of the mode. We assert the run raises and
        that no model was ever invoked."""
        from tools.budget.module_budget_tracker import ModuleBudgetExceededError

        called = {"n": 0}

        def counting_invoke_direct(model_name, request, function=None):
            called["n"] += 1
            return LLMResponse(content="x", model_id=model_name, input_tokens=1,
                               output_tokens=1, provider="mock")

        mock_router._invoke_model_direct = counting_invoke_direct

        # Re-enable the real budget check for THIS test only (autouse fixture
        # normally no-ops it) and force it to block.
        with patch("tools.llm.chain_orchestrator.ChainOrchestrator._check_module_budget",
                   side_effect=ModuleBudgetExceededError("generative_intelligence", {"action": "block"})):
            with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
                orch = ChainOrchestrator(router=mock_router)
                req = LLMRequest(messages=[{"role": "user", "content": "test"}])
                with pytest.raises(ModuleBudgetExceededError):
                    orch.invoke_divergence("some_decision_function", req)

        assert called["n"] == 0, "budget gate must abort before any branch model call"

    def test_divergence_real_budget_gate_trips_and_aborts(self, mock_router, tmp_db):
        """dvg-core-02: the test above proves invoke_divergence aborts when the gate
        raises, but it patches _check_module_budget itself — so it never proves the
        gate TRIPS for this mode. Here we run the REAL _check_module_budget body and
        only stub the underlying tracker, asserting a 'block' verdict on the
        generative_intelligence module aborts the run before any branch spend."""
        from tools.budget.module_budget_tracker import ModuleBudgetExceededError

        called = {"n": 0}
        seen = {}

        def counting_invoke_direct(model_name, request, function=None):
            called["n"] += 1
            return LLMResponse(content="x", model_id=model_name, input_tokens=1,
                               output_tokens=1, provider="mock")

        def blocking_check(module_name, function="", estimated_cost_usd=0.0,
                           estimated_tokens=0, estimated_operations=0, project_id=""):
            seen["module"] = module_name
            seen["estimated_cost_usd"] = estimated_cost_usd
            return {"action": "block", "message": "over budget", "utilization": 1.4}

        mock_router._invoke_model_direct = counting_invoke_direct

        # Restore the REAL gate body (the autouse fixture no-ops it module-wide).
        with patch.object(ChainOrchestrator, "_check_module_budget", _REAL_CHECK_MODULE_BUDGET), \
             patch("tools.budget.module_budget_tracker.check_module_budget", blocking_check), \
             patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test"}])
            with pytest.raises(ModuleBudgetExceededError):
                orch.invoke_divergence("some_decision_function", req)

        assert called["n"] == 0, "real budget gate must abort before any branch model call"
        assert seen["module"] == "generative_intelligence"
        # The pre-flight reservation must be the mode's fan-out-aware cap, not the
        # single-call outer backstop — otherwise the gate under-reserves by ~Nx.
        assert seen["estimated_cost_usd"] == 3.00

    def test_divergence_every_branch_carries_the_function_name(self, mock_router, tmp_db):
        """CUI: divergence must open NO new egress path. Routing/redaction policy
        (including the force_local rung) keys off the FUNCTION NAME, so every branch
        call must carry it — a branch that arrived anonymous would escape the
        per-function LOCAL-ONLY pin. Asserted for all 6 branches, including the
        cloud-named models in the legacy fallback list."""
        seen_functions = []

        def recording_invoke_direct(model_name, request, function=None):
            seen_functions.append(function)
            return LLMResponse(content="idea", model_id=model_name, input_tokens=1,
                               output_tokens=1, provider="mock")

        mock_router._invoke_model_direct = recording_invoke_direct

        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test"}])
            orch.invoke_divergence("cui_bearing_function", req)

        assert len(seen_functions) == 6
        assert all(f == "cui_bearing_function" for f in seen_functions), (
            f"every branch must carry the function name for routing policy; got {seen_functions}"
        )

    def test_divergence_local_only_violation_never_falls_back_to_cloud(self, mock_router, tmp_db):
        """CUI: when the egress policy refuses a cloud branch for a LOCAL-ONLY
        function, divergence must NOT retry that branch on another model. It drops
        the branch. A fan-out that walked its model list looking for one allowed to
        send would be exactly the new egress path this mode must not create."""
        from tools.llm.router import ForceLocalViolation

        attempted = []

        def policy_enforcing_invoke_direct(model_name, request, function=None):
            attempted.append(model_name)
            if not model_name.endswith("-local"):  # stand-in for the egress policy
                raise ForceLocalViolation(f"{function} is pinned local; {model_name} is cloud")
            return LLMResponse(content="local idea", model_id=model_name, input_tokens=1,
                               output_tokens=1, provider="ollama")

        mock_router._invoke_model_direct = policy_enforcing_invoke_direct

        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "sensitive"}])
            result = orch.invoke_divergence("local_only_function", req)

        # The 6 branches cycle the 4-model diverse pool, so the one cloud model in
        # it (claude-haiku) is assigned twice and refused twice.
        assert attempted.count("claude-haiku") == 2, "refused branch must not be retried"
        # Nothing that isn't local ever produced content — no cloud egress, and no
        # walking the model list looking for one allowed to send.
        assert result.models_used == ["deepseek-local", "llama-local", "qwen3-local"]
        assert all(m.endswith("-local") for m in result.models_used)
        assert "## Frame:" in result.content

    def test_divergence_branches_run_in_strict_isolation(self, mock_router, tmp_db):
        """Regression: no branch prompt may contain another branch's output or a
        'prior argument' section — cross-feeding collapses divergence into a
        single wider thought. Each branch prompt must reference only the problem
        and its own frame."""
        captured_prompts = []

        def capturing_invoke_direct(model_name, request, function=None):
            content = request.messages[0]["content"] if request.messages else ""
            captured_prompts.append(content)
            return LLMResponse(
                content="1. Idea A\n2. Idea B [IDEAS]", model_id=model_name,
                input_tokens=100, output_tokens=50, provider="mock",
            )

        mock_router._invoke_model_direct = capturing_invoke_direct
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "unique-problem-token"}])
            orch.invoke_divergence("some_decision_function", req)

        assert captured_prompts, "expected branch prompts to be captured"
        for prompt in captured_prompts:
            assert "unique-problem-token" in prompt        # each branch sees the problem
            assert "[PRIOR ARGUMENT" not in prompt          # never threaded (unlike CoD)
            assert "[RESPONSE A]" not in prompt             # never cross-read (unlike council review)

    def test_divergence_degrades_cleanly_when_all_branches_fail(self, mock_router, tmp_db):
        """If every branch call raises, invoke_divergence must return an empty-
        content ChainResult (stop_reason=all_branches_failed) rather than raise."""
        def always_fail(model_name, request, function=None):
            raise RuntimeError("simulated provider outage")

        mock_router._invoke_model_direct = always_fail
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test"}])
            result = orch.invoke_divergence("some_decision_function", req)

        assert result.content == ""
        assert result.stop_reason == "all_branches_failed"

    def test_divergence_emits_telemetry(self, mock_router, tmp_db):
        """Divergence must route its result through _write_chain_telemetry (the
        shared telemetry + module-budget-recording path) exactly like the other
        modes. We spy on the emit rather than read the DB back: the raw-sqlite
        test patch does not translate the %s placeholders the writer uses, so a
        DB read-back would be an infra artifact, not a behavior check."""
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)), \
             patch("tools.llm.chain_orchestrator.ChainOrchestrator._write_chain_telemetry") as spy:
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test"}])
            result = orch.invoke_divergence("some_decision_function", req)

        assert spy.called
        emitted_result = spy.call_args[0][0]
        assert emitted_result.chain_mode == "divergence"
        assert result.rounds  # in-memory per-branch telemetry populated


# ---------------------------------------------------------------------------
# Decision Type Tests
# ---------------------------------------------------------------------------


class TestCanvasDecisionRecording:
    def test_record_canvas_decision_accepts_chain_types(self, tmp_db):
        """record_canvas_decision accepts chain_of_thought and chain_of_debate."""
        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
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
                    "reasoner_role": "cot_reasoner",
                    "critic_role": "cot_critic",
                    "synthesizer_role": "cot_synthesizer",
                    "reasoner_model": "qwen3-local",
                    "critic_model": "claude-sonnet",
                    "synthesizer_model": "claude-sonnet",
                    "excluded_functions": [],
                    "per_function": {},
                },
            },
            "routing": {
                "cot_reasoner":    {"chain": ["qwen3-local"]},
                "cot_critic":      {"chain": ["claude-sonnet"]},
                "cot_synthesizer": {"chain": ["claude-sonnet"]},
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
                    "judge_role": "cod_judge",
                    "debater_pool_role": "cod_debater_pool",
                    "judge_model": "claude-sonnet",
                    "debater_models": ["qwen3-local", "claude-sonnet"],
                    "excluded_functions": [],
                    "per_function": {},
                },
            },
            "routing": {
                "cod_debater_pool": {"chain": ["qwen3-local", "claude-sonnet"]},
                "cod_judge":        {"chain": ["claude-sonnet"]},
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


# ---------------------------------------------------------------------------
# Legacy Direct Model Name Tests
# ---------------------------------------------------------------------------


class TestLegacyDirectModelName:
    def test_legacy_model_name_uses_invoke_model_direct(self, mock_router, tmp_db):
        """A per_function override that nulls the role key falls back to _invoke_model_direct."""
        # Null out all role keys and use direct model names — simulates news_reasoning override
        mock_router._config["chain_orchestration"]["cot"]["per_function"] = {
            "news_reasoning": {
                "max_rounds": 1,
                "self_consistency_runs": 1,
                "reasoner_role": None,
                "critic_role": None,
                "synthesizer_role": None,
                "reasoner_model": "qwen3-local",
                "critic_model": "qwen3-local",
                "synthesizer_model": "qwen3-local",
            }
        }

        direct_calls = []

        # Signature mirrors LLMRouter._invoke_model_direct, which threads the
        # function name through so routing policy can apply force_local.
        def mock_invoke_direct(model_name, request, function=""):
            direct_calls.append(model_name)
            return LLMResponse(
                content=f"direct response from {model_name}",
                model_id=model_name,
                input_tokens=100,
                output_tokens=50,
                provider="mock",
            )

        mock_router._invoke_model_direct = mock_invoke_direct

        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "test news"}])
            result = orch.invoke_chain_of_thought("news_reasoning", req)

        assert result.stop_reason == "completed"
        # All calls should have gone through _invoke_model_direct (legacy path)
        assert len(direct_calls) > 0
        assert all(m == "qwen3-local" for m in direct_calls)

    def test_diverse_debaters_uses_multiple_models(self, mock_router, tmp_db):
        """CoD assigns distinct models to debater slots via get_diverse_models.

        mock_get_diverse_models returns ["qwen3-local", "claude-haiku", "llama-local"] for
        num_debaters=3. Each debater slot must be pinned to a different model, so the
        model_id values across debater rounds must include at least 2 distinct values.
        """
        debater_calls: list[str] = []

        def tracking_invoke_direct(model_name, request, function=""):
            debater_calls.append(model_name)
            return LLMResponse(
                content=f"debate from {model_name}",
                model_id=model_name,
                input_tokens=50,
                output_tokens=30,
                provider="mock",
            )

        mock_router._invoke_model_direct = tracking_invoke_direct

        with patch("tools.llm.chain_orchestrator.get_connection", lambda: storage_conn(tmp_db)):
            orch = ChainOrchestrator(router=mock_router)
            req = LLMRequest(messages=[{"role": "user", "content": "should we adopt microservices?"}])
            result = orch.invoke_chain_of_debate("architecture_review", req)

        assert result.stop_reason == "completed"

        # Verify debater model_ids from round data
        debater_models_in_result = [
            r["model_id"] for r in result.rounds if r.get("step", "").startswith("debater_")
        ]
        assert len(debater_models_in_result) >= 2, (
            f"Expected ≥2 debater rounds, got: {debater_models_in_result}"
        )
        # The core assertion: debaters must use more than one distinct model.
        # With num_debaters=3 and pool=["qwen3-local","claude-haiku","llama-local"],
        # all three slots should be distinct.
        assert len(set(debater_models_in_result)) > 1, (
            f"CoD debaters must use distinct LLMs, got: {debater_models_in_result}"
        )

        # Also verify via the tracking list that _invoke_model_direct was called
        # with different model names (not the same model repeated)
        debater_direct_calls = [m for m in debater_calls]
        assert len(set(debater_direct_calls)) > 1, (
            f"Expected distinct models in debater slots, got: {debater_direct_calls}"
        )
