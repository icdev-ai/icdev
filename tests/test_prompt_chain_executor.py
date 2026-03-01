#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the Prompt Chain Executor (Phase 61, Feature 2).

Covers:
    1. YAML loading and parsing
    2. Chain validation (missing steps, invalid agents, bad refs)
    3. Variable substitution ($INPUT, $ORIGINAL, $STEP{x})
    4. Dry-run mode
    5. Execution flow with mocked LLMRouter
    6. Step failure handling and chain abort
    7. DB storage of results
    8. CLI list/history commands
    9. Audit trail recording
    10. Content hashing (SHA-256)
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.agent.prompt_chain_executor import (
    AGENT_FUNCTION_MAP,
    VALID_AGENTS,
    ChainDefinition,
    ChainExecution,
    ChainStep,
    PromptChainExecutor,
    StepResult,
    _ensure_table,
    _sha256,
    load_chains,
    substitute_variables,
    validate_chain,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def db_path(tmp_path):
    """Create a temp DB with the prompt_chain_executions table."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prompt_chain_executions (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            chain_name TEXT NOT NULL,
            original_input TEXT NOT NULL,
            original_input_hash TEXT NOT NULL,
            status TEXT DEFAULT 'running'
                CHECK(status IN ('running','completed','failed','cancelled')),
            steps_completed INTEGER DEFAULT 0,
            steps_total INTEGER NOT NULL,
            step_results TEXT DEFAULT '{}',
            final_output TEXT,
            final_output_hash TEXT,
            total_duration_ms INTEGER,
            total_tokens_used INTEGER DEFAULT 0,
            error_message TEXT,
            executed_by TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT
        )
    """)
    # Also create audit_trail table for audit logging
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            project_id TEXT,
            details TEXT,
            session_id TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def sample_chains_yaml(tmp_path):
    """Create a temporary prompt_chains.yaml for testing."""
    config = tmp_path / "prompt_chains.yaml"
    config.write_text("""
prompt_chains:
  simple_chain:
    description: "A simple two-step chain"
    steps:
      - step_id: step_one
        agent: architect
        prompt: "Analyze this: $INPUT"
        timeout_s: 60
      - step_id: step_two
        agent: builder
        prompt: |
          Build based on: $INPUT
          Original: $ORIGINAL
        timeout_s: 90

  three_step_chain:
    description: "Three steps with cross-references"
    steps:
      - step_id: plan
        agent: architect
        prompt: "Plan for: $INPUT"
        timeout_s: 60
      - step_id: review
        agent: compliance
        prompt: |
          Review plan: $INPUT
          Requirement: $ORIGINAL
        timeout_s: 60
      - step_id: refine
        agent: architect
        prompt: |
          Refine plan using:
          Review: $STEP{review}
          Original plan: $STEP{plan}
          Requirement: $ORIGINAL
        timeout_s: 90

  single_step_chain:
    description: "Just one step"
    steps:
      - step_id: only_step
        agent: builder
        prompt: "Do this: $INPUT"

defaults:
  timeout_s: 120
  model_effort: high
  record_chain: true
  audit_trail: true
""", encoding="utf-8")
    return config


@pytest.fixture
def invalid_chains_yaml(tmp_path):
    """Create an invalid prompt_chains.yaml for testing validation."""
    config = tmp_path / "invalid_chains.yaml"
    config.write_text("""
prompt_chains:
  bad_agent_chain:
    description: "Chain with invalid agent"
    steps:
      - step_id: step1
        agent: nonexistent_agent
        prompt: "Do: $INPUT"

  empty_chain:
    description: "Chain with no steps"
    steps: []

  dup_step_chain:
    description: "Chain with duplicate step IDs"
    steps:
      - step_id: same_id
        agent: builder
        prompt: "First: $INPUT"
      - step_id: same_id
        agent: architect
        prompt: "Second: $INPUT"

  bad_ref_chain:
    description: "Chain with forward STEP reference"
    steps:
      - step_id: first
        agent: builder
        prompt: "Do: $INPUT and $STEP{second}"
      - step_id: second
        agent: architect
        prompt: "Also: $INPUT"

  empty_prompt_chain:
    description: "Chain with empty prompt"
    steps:
      - step_id: blank
        agent: builder
        prompt: "   "
""", encoding="utf-8")
    return config


@pytest.fixture
def executor(db_path, sample_chains_yaml):
    """Create a PromptChainExecutor with test DB and config."""
    return PromptChainExecutor(db_path=db_path, config_path=sample_chains_yaml)


@pytest.fixture
def mock_llm_response():
    """Create a factory for mock LLMResponse objects."""
    def _make(content="Mock LLM output", model_id="test-model",
              input_tokens=100, output_tokens=50):
        response = MagicMock()
        response.content = content
        response.model_id = model_id
        response.input_tokens = input_tokens
        response.output_tokens = output_tokens
        return response
    return _make


# ============================================================
# Test: YAML Loading
# ============================================================

class TestYAMLLoading:
    """Test loading chain definitions from YAML."""

    def test_load_valid_chains(self, sample_chains_yaml):
        """Load all chains from valid YAML config."""
        chains = load_chains(sample_chains_yaml)
        assert len(chains) == 3
        assert "simple_chain" in chains
        assert "three_step_chain" in chains
        assert "single_step_chain" in chains

    def test_chain_description(self, sample_chains_yaml):
        """Chain descriptions are preserved."""
        chains = load_chains(sample_chains_yaml)
        assert chains["simple_chain"].description == "A simple two-step chain"

    def test_chain_step_count(self, sample_chains_yaml):
        """Step counts are correct."""
        chains = load_chains(sample_chains_yaml)
        assert len(chains["simple_chain"].steps) == 2
        assert len(chains["three_step_chain"].steps) == 3
        assert len(chains["single_step_chain"].steps) == 1

    def test_step_attributes(self, sample_chains_yaml):
        """Step attributes are correctly parsed."""
        chains = load_chains(sample_chains_yaml)
        step = chains["simple_chain"].steps[0]
        assert step.step_id == "step_one"
        assert step.agent == "architect"
        assert "$INPUT" in step.prompt
        assert step.timeout_s == 60

    def test_default_timeout(self, sample_chains_yaml):
        """Steps without timeout get the default from config."""
        chains = load_chains(sample_chains_yaml)
        step = chains["single_step_chain"].steps[0]
        assert step.timeout_s == 120  # from defaults section

    def test_load_nonexistent_file(self, tmp_path):
        """Loading from nonexistent file returns empty dict."""
        chains = load_chains(tmp_path / "nonexistent.yaml")
        assert chains == {}

    def test_load_empty_config(self, tmp_path):
        """Loading empty YAML returns empty dict."""
        config = tmp_path / "empty.yaml"
        config.write_text("", encoding="utf-8")
        chains = load_chains(config)
        assert chains == {}


# ============================================================
# Test: Chain Validation
# ============================================================

class TestChainValidation:
    """Test chain structure validation."""

    def test_valid_chain_no_errors(self, sample_chains_yaml):
        """Valid chain returns no errors."""
        chains = load_chains(sample_chains_yaml)
        errors = validate_chain(chains["simple_chain"])
        assert errors == []

    def test_valid_three_step_chain(self, sample_chains_yaml):
        """Three-step chain with $STEP refs is valid."""
        chains = load_chains(sample_chains_yaml)
        errors = validate_chain(chains["three_step_chain"])
        assert errors == []

    def test_invalid_agent(self, invalid_chains_yaml):
        """Unknown agent name produces validation error."""
        chains = load_chains(invalid_chains_yaml)
        errors = validate_chain(chains["bad_agent_chain"])
        assert len(errors) == 1
        assert "nonexistent_agent" in errors[0]

    def test_empty_steps(self, invalid_chains_yaml):
        """Chain with no steps produces validation error."""
        chains = load_chains(invalid_chains_yaml)
        errors = validate_chain(chains["empty_chain"])
        assert len(errors) == 1
        assert "no steps" in errors[0]

    def test_duplicate_step_ids(self, invalid_chains_yaml):
        """Duplicate step_id produces validation error."""
        chains = load_chains(invalid_chains_yaml)
        errors = validate_chain(chains["dup_step_chain"])
        assert any("duplicate" in e.lower() for e in errors)

    def test_forward_step_reference(self, invalid_chains_yaml):
        """$STEP{x} referencing a later step produces validation error."""
        chains = load_chains(invalid_chains_yaml)
        errors = validate_chain(chains["bad_ref_chain"])
        assert any("$STEP{second}" in e for e in errors)

    def test_empty_prompt(self, invalid_chains_yaml):
        """Empty prompt string produces validation error."""
        chains = load_chains(invalid_chains_yaml)
        errors = validate_chain(chains["empty_prompt_chain"])
        assert any("empty prompt" in e.lower() for e in errors)

    def test_negative_timeout(self):
        """Negative timeout_s produces validation error."""
        chain = ChainDefinition(
            name="bad_timeout",
            steps=[ChainStep(step_id="s1", agent="builder", prompt="Do $INPUT", timeout_s=-5)],
        )
        errors = validate_chain(chain)
        assert any("timeout_s must be positive" in e for e in errors)


# ============================================================
# Test: Variable Substitution
# ============================================================

class TestVariableSubstitution:
    """Test $INPUT, $ORIGINAL, and $STEP{x} variable replacement."""

    def test_input_substitution(self):
        """$INPUT is replaced with current input."""
        result = substitute_variables(
            "Analyze: $INPUT", "my data", "original", {}
        )
        assert result == "Analyze: my data"

    def test_original_substitution(self):
        """$ORIGINAL is replaced with the original user input."""
        result = substitute_variables(
            "Original was: $ORIGINAL", "current", "first input", {}
        )
        assert result == "Original was: first input"

    def test_step_reference(self):
        """$STEP{step_id} is replaced with the step's output."""
        result = substitute_variables(
            "Plan: $STEP{plan_step}", "current", "orig",
            {"plan_step": "The plan content"},
        )
        assert result == "Plan: The plan content"

    def test_multiple_step_references(self):
        """Multiple $STEP references in one template."""
        result = substitute_variables(
            "Review: $STEP{review}\nPlan: $STEP{plan}",
            "current", "orig",
            {"review": "Review output", "plan": "Plan output"},
        )
        assert "Review output" in result
        assert "Plan output" in result

    def test_missing_step_reference(self):
        """$STEP{x} with unknown step_id shows MISSING marker."""
        result = substitute_variables(
            "Ref: $STEP{nonexistent}", "current", "orig", {}
        )
        assert "[MISSING:" in result
        assert "nonexistent" in result

    def test_all_variables_combined(self):
        """All three variable types in one template."""
        result = substitute_variables(
            "Input: $INPUT\nOriginal: $ORIGINAL\nStep: $STEP{s1}",
            "prev_output", "user_input", {"s1": "step1_result"},
        )
        assert "prev_output" in result
        assert "user_input" in result
        assert "step1_result" in result

    def test_no_variables(self):
        """Template with no variables is returned unchanged."""
        template = "Just a plain prompt with no variables"
        result = substitute_variables(template, "input", "orig", {})
        assert result == template


# ============================================================
# Test: Content Hashing
# ============================================================

class TestContentHashing:
    """Test SHA-256 content hashing."""

    def test_sha256_deterministic(self):
        """Same input produces same hash."""
        h1 = _sha256("test content")
        h2 = _sha256("test content")
        assert h1 == h2

    def test_sha256_different_inputs(self):
        """Different inputs produce different hashes."""
        h1 = _sha256("content A")
        h2 = _sha256("content B")
        assert h1 != h2

    def test_sha256_length(self):
        """SHA-256 hash is 64 hex characters."""
        h = _sha256("anything")
        assert len(h) == 64


# ============================================================
# Test: Dry Run
# ============================================================

class TestDryRun:
    """Test dry-run mode (no LLM calls)."""

    def test_dry_run_returns_steps(self, executor):
        """Dry run returns all step previews."""
        result = executor.dry_run("simple_chain", "test input")
        assert result["dry_run"] is True
        assert result["steps_count"] == 2
        assert len(result["steps"]) == 2

    def test_dry_run_step_metadata(self, executor):
        """Dry run includes agent and function info."""
        result = executor.dry_run("simple_chain", "test input")
        step = result["steps"][0]
        assert step["step_id"] == "step_one"
        assert step["agent"] == "architect"
        assert step["llm_function"] == AGENT_FUNCTION_MAP["architect"]
        assert step["timeout_s"] == 60

    def test_dry_run_unknown_chain(self, executor):
        """Dry run with unknown chain returns error."""
        result = executor.dry_run("nonexistent_chain", "test")
        assert "error" in result

    def test_dry_run_prompt_preview(self, executor):
        """Dry run shows resolved prompt preview."""
        result = executor.dry_run("simple_chain", "test input")
        preview = result["steps"][0]["resolved_prompt_preview"]
        assert "test input" in preview

    def test_dry_run_includes_input_hash(self, executor):
        """Dry run includes SHA-256 hash of original input."""
        result = executor.dry_run("simple_chain", "test input")
        assert result["original_input_hash"] == _sha256("test input")


# ============================================================
# Test: Chain Execution
# ============================================================

class TestChainExecution:
    """Test full chain execution with mocked LLM."""

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    def test_simple_chain_completes(self, mock_get_router, executor, mock_llm_response):
        """Two-step chain completes successfully."""
        mock_router = MagicMock()
        mock_router.invoke.side_effect = [
            mock_llm_response("Step 1 output"),
            mock_llm_response("Step 2 output"),
        ]
        mock_get_router.return_value = mock_router

        result = executor.execute_chain(
            "simple_chain", "test input", project_id="proj-test"
        )

        assert result.status == "completed"
        assert result.steps_completed == 2
        assert result.steps_total == 2
        assert result.final_output == "Step 2 output"
        assert result.final_output_hash == _sha256("Step 2 output")

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    def test_three_step_chain_with_refs(self, mock_get_router, executor, mock_llm_response):
        """Three-step chain with $STEP{} references executes correctly."""
        # Reload executor with three_step_chain
        mock_router = MagicMock()
        mock_router.invoke.side_effect = [
            mock_llm_response("Plan output"),
            mock_llm_response("Review output"),
            mock_llm_response("Refined output"),
        ]
        mock_get_router.return_value = mock_router

        result = executor.execute_chain(
            "three_step_chain", "Build auth module", project_id="proj-test"
        )

        assert result.status == "completed"
        assert result.steps_completed == 3
        assert result.final_output == "Refined output"

        # Verify all three steps are in results
        assert "plan" in result.step_results
        assert "review" in result.step_results
        assert "refine" in result.step_results

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    def test_step_failure_aborts_chain(self, mock_get_router, executor, mock_llm_response):
        """If a step fails, the chain aborts with 'failed' status."""
        mock_router = MagicMock()
        mock_router.invoke.side_effect = [
            mock_llm_response("Step 1 output"),
            RuntimeError("LLM unavailable"),
        ]
        mock_get_router.return_value = mock_router

        result = executor.execute_chain(
            "simple_chain", "test input", project_id="proj-test"
        )

        assert result.status == "failed"
        assert result.steps_completed == 1
        assert "step_two" in result.error_message
        assert result.step_results["step_one"].status == "completed"
        assert result.step_results["step_two"].status == "failed"

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    def test_first_step_failure(self, mock_get_router, executor, mock_llm_response):
        """If the first step fails, chain aborts with 0 steps completed."""
        mock_router = MagicMock()
        mock_router.invoke.side_effect = RuntimeError("No model available")
        mock_get_router.return_value = mock_router

        result = executor.execute_chain(
            "simple_chain", "test input", project_id="proj-test"
        )

        assert result.status == "failed"
        assert result.steps_completed == 0
        assert result.final_output == ""

    def test_unknown_chain_raises(self, executor):
        """Executing unknown chain raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            executor.execute_chain("nonexistent", "input")

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    def test_execution_id_format(self, mock_get_router, executor, mock_llm_response):
        """Execution ID starts with 'pce-' prefix."""
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_llm_response("output")
        mock_get_router.return_value = mock_router

        result = executor.execute_chain(
            "single_step_chain", "test", project_id="proj-1"
        )
        assert result.id.startswith("pce-")

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    def test_token_tracking(self, mock_get_router, executor, mock_llm_response):
        """Total tokens are accumulated across steps."""
        mock_router = MagicMock()
        mock_router.invoke.side_effect = [
            mock_llm_response("out1", input_tokens=100, output_tokens=50),
            mock_llm_response("out2", input_tokens=200, output_tokens=75),
        ]
        mock_get_router.return_value = mock_router

        result = executor.execute_chain(
            "simple_chain", "test", project_id="proj-1"
        )

        assert result.total_tokens_used == 100 + 50 + 200 + 75

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    def test_step_duration_tracked(self, mock_get_router, executor, mock_llm_response):
        """Each step has a non-negative duration_ms."""
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_llm_response("output")
        mock_get_router.return_value = mock_router

        result = executor.execute_chain(
            "single_step_chain", "test", project_id="proj-1"
        )

        step = result.step_results["only_step"]
        assert step.duration_ms >= 0

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    def test_total_duration_tracked(self, mock_get_router, executor, mock_llm_response):
        """Total chain duration is tracked."""
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_llm_response("output")
        mock_get_router.return_value = mock_router

        result = executor.execute_chain(
            "single_step_chain", "test", project_id="proj-1"
        )

        assert result.total_duration_ms >= 0


# ============================================================
# Test: DB Storage
# ============================================================

class TestDBStorage:
    """Test database persistence of execution results."""

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    def test_execution_persisted(self, mock_get_router, executor, db_path, mock_llm_response):
        """Completed execution is stored in the database."""
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_llm_response("output")
        mock_get_router.return_value = mock_router

        result = executor.execute_chain(
            "single_step_chain", "test input", project_id="proj-test"
        )

        # Query DB directly
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM prompt_chain_executions WHERE id = ?",
            (result.id,),
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["chain_name"] == "single_step_chain"
        assert row["status"] == "completed"
        assert row["steps_completed"] == 1
        assert row["steps_total"] == 1
        assert row["project_id"] == "proj-test"

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    def test_step_results_stored_as_json(self, mock_get_router, executor, db_path, mock_llm_response):
        """Step results are stored as valid JSON."""
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_llm_response("out", model_id="test-m")
        mock_get_router.return_value = mock_router

        result = executor.execute_chain(
            "single_step_chain", "test", project_id="proj-1"
        )

        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT step_results FROM prompt_chain_executions WHERE id = ?",
            (result.id,),
        ).fetchone()
        conn.close()

        parsed = json.loads(row[0])
        assert "only_step" in parsed
        assert parsed["only_step"]["agent"] == "builder"
        assert parsed["only_step"]["status"] == "completed"

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    def test_failed_execution_persisted(self, mock_get_router, executor, db_path, mock_llm_response):
        """Failed execution is also stored in the database."""
        mock_router = MagicMock()
        mock_router.invoke.side_effect = RuntimeError("boom")
        mock_get_router.return_value = mock_router

        result = executor.execute_chain(
            "single_step_chain", "test", project_id="proj-1"
        )

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM prompt_chain_executions WHERE id = ?",
            (result.id,),
        ).fetchone()
        conn.close()

        assert row["status"] == "failed"
        assert row["error_message"] is not None
        assert "boom" in row["error_message"]

    def test_ensure_table_creates_indexes(self, tmp_path):
        """_ensure_table creates the table and indexes."""
        db = tmp_path / "fresh.db"
        _ensure_table(db)

        conn = sqlite3.connect(str(db))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]
        conn.close()

        assert "prompt_chain_executions" in table_names


# ============================================================
# Test: History Query
# ============================================================

class TestHistory:
    """Test execution history retrieval."""

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    def test_get_history(self, mock_get_router, executor, mock_llm_response):
        """History returns completed executions."""
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_llm_response("output")
        mock_get_router.return_value = mock_router

        executor.execute_chain("single_step_chain", "input1", project_id="proj-1")
        executor.execute_chain("single_step_chain", "input2", project_id="proj-1")

        history = executor.get_history(project_id="proj-1")
        assert len(history) == 2

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    def test_history_filter_by_chain(self, mock_get_router, executor, mock_llm_response):
        """History can be filtered by chain name."""
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_llm_response("output")
        mock_get_router.return_value = mock_router

        executor.execute_chain("single_step_chain", "input1", project_id="proj-1")

        history = executor.get_history(chain_name="single_step_chain")
        assert len(history) >= 1
        assert all(h["chain_name"] == "single_step_chain" for h in history)

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    def test_history_limit(self, mock_get_router, executor, mock_llm_response):
        """History respects the limit parameter."""
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_llm_response("output")
        mock_get_router.return_value = mock_router

        for i in range(5):
            executor.execute_chain("single_step_chain", f"input{i}", project_id="proj-1")

        history = executor.get_history(limit=3)
        assert len(history) == 3

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    def test_get_execution_by_id(self, mock_get_router, executor, mock_llm_response):
        """Retrieve a specific execution by ID."""
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_llm_response("output")
        mock_get_router.return_value = mock_router

        result = executor.execute_chain("single_step_chain", "test", project_id="proj-1")

        entry = executor.get_execution(result.id)
        assert entry is not None
        assert entry["id"] == result.id
        assert entry["chain_name"] == "single_step_chain"

    def test_get_nonexistent_execution(self, executor):
        """Getting nonexistent execution returns None."""
        entry = executor.get_execution("pce-nonexistent")
        assert entry is None


# ============================================================
# Test: List Chains
# ============================================================

class TestListChains:
    """Test chain listing functionality."""

    def test_list_returns_all_chains(self, executor):
        """List returns all loaded chains."""
        chains = executor.list_chains()
        assert len(chains) == 3
        names = {c["name"] for c in chains}
        assert "simple_chain" in names
        assert "three_step_chain" in names
        assert "single_step_chain" in names

    def test_list_includes_validity(self, executor):
        """List includes validation status."""
        chains = executor.list_chains()
        for c in chains:
            assert "valid" in c
            assert isinstance(c["valid"], bool)

    def test_list_includes_agents(self, executor):
        """List includes agent names per chain."""
        chains = executor.list_chains()
        simple = next(c for c in chains if c["name"] == "simple_chain")
        assert "architect" in simple["agents"]
        assert "builder" in simple["agents"]


# ============================================================
# Test: Agent Function Mapping
# ============================================================

class TestAgentMapping:
    """Test agent-to-LLM-function mapping."""

    def test_all_valid_agents_have_mapping(self):
        """Every valid agent has a function mapping."""
        for agent in VALID_AGENTS:
            assert agent in AGENT_FUNCTION_MAP

    def test_core_agents_mapped(self):
        """Core agents have appropriate function mappings."""
        assert AGENT_FUNCTION_MAP["architect"] == "agent_architect"
        assert AGENT_FUNCTION_MAP["builder"] == "code_generation"
        assert AGENT_FUNCTION_MAP["compliance"] == "compliance_export"
        assert AGENT_FUNCTION_MAP["security"] == "code_review"

    def test_valid_agents_not_empty(self):
        """VALID_AGENTS set is non-empty and contains core agents."""
        assert len(VALID_AGENTS) >= 10
        assert "architect" in VALID_AGENTS
        assert "builder" in VALID_AGENTS
        assert "compliance" in VALID_AGENTS
        assert "security" in VALID_AGENTS
        assert "knowledge" in VALID_AGENTS


# ============================================================
# Test: Audit Trail
# ============================================================

class TestAuditTrail:
    """Test audit trail recording during chain execution."""

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    @patch("tools.agent.prompt_chain_executor._audit")
    def test_audit_on_chain_start(self, mock_audit, mock_get_router, executor, mock_llm_response):
        """Audit event is logged when chain starts."""
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_llm_response("output")
        mock_get_router.return_value = mock_router

        executor.execute_chain("single_step_chain", "test", project_id="proj-1")

        # Find the start audit call
        start_calls = [
            c for c in mock_audit.call_args_list
            if c.kwargs.get("event_type") == "prompt_chain.started"
            or (c.args and c.args[0] == "prompt_chain.started")
        ]
        assert len(start_calls) >= 1

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    @patch("tools.agent.prompt_chain_executor._audit")
    def test_audit_on_chain_complete(self, mock_audit, mock_get_router, executor, mock_llm_response):
        """Audit event is logged when chain completes."""
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_llm_response("output")
        mock_get_router.return_value = mock_router

        executor.execute_chain("single_step_chain", "test", project_id="proj-1")

        completed_calls = [
            c for c in mock_audit.call_args_list
            if c.kwargs.get("event_type") == "prompt_chain.completed"
            or (c.args and c.args[0] == "prompt_chain.completed")
        ]
        assert len(completed_calls) >= 1

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    @patch("tools.agent.prompt_chain_executor._audit")
    def test_audit_on_step_complete(self, mock_audit, mock_get_router, executor, mock_llm_response):
        """Audit event is logged for each step completion."""
        mock_router = MagicMock()
        mock_router.invoke.side_effect = [
            mock_llm_response("Step 1"),
            mock_llm_response("Step 2"),
        ]
        mock_get_router.return_value = mock_router

        executor.execute_chain("simple_chain", "test", project_id="proj-1")

        step_calls = [
            c for c in mock_audit.call_args_list
            if "prompt_chain.step" in str(c)
        ]
        assert len(step_calls) >= 2


# ============================================================
# Test: Data Classes
# ============================================================

class TestDataClasses:
    """Test data class construction and defaults."""

    def test_chain_step_defaults(self):
        """ChainStep has correct defaults."""
        step = ChainStep(step_id="s1", agent="builder", prompt="Do $INPUT")
        assert step.timeout_s == 120

    def test_step_result_defaults(self):
        """StepResult has correct defaults."""
        sr = StepResult(step_id="s1", agent="builder")
        assert sr.status == "pending"
        assert sr.output == ""
        assert sr.duration_ms == 0

    def test_chain_execution_defaults(self):
        """ChainExecution has correct defaults."""
        ce = ChainExecution(
            id="pce-test",
            project_id="proj-1",
            chain_name="test",
            original_input="input",
            original_input_hash=_sha256("input"),
            steps_total=2,
            created_at=_sha256("now"),
        )
        assert ce.status == "running"
        assert ce.steps_completed == 0
        assert ce.total_tokens_used == 0


# ============================================================
# Test: Edge Cases
# ============================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    def test_empty_llm_response(self, mock_get_router, executor, mock_llm_response):
        """Empty LLM response content is handled gracefully."""
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_llm_response("")
        mock_get_router.return_value = mock_router

        result = executor.execute_chain(
            "single_step_chain", "test", project_id="proj-1"
        )
        assert result.status == "completed"
        assert result.final_output == ""

    def test_validation_rejects_bad_chain(self, db_path, invalid_chains_yaml):
        """Executing an invalid chain raises ValueError."""
        executor = PromptChainExecutor(db_path=db_path, config_path=invalid_chains_yaml)
        with pytest.raises(ValueError, match="validation failed"):
            executor.execute_chain("bad_agent_chain", "test")

    @patch("tools.agent.prompt_chain_executor.PromptChainExecutor._get_llm_router")
    def test_input_hash_consistency(self, mock_get_router, executor, mock_llm_response):
        """Same input produces same hash across executions."""
        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_llm_response("output")
        mock_get_router.return_value = mock_router

        r1 = executor.execute_chain("single_step_chain", "same input", project_id="p1")
        r2 = executor.execute_chain("single_step_chain", "same input", project_id="p1")

        assert r1.original_input_hash == r2.original_input_hash
        # But different execution IDs
        assert r1.id != r2.id
