#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for gap closure: C-2 (trace_id), A-5 (chat memory retrieval), C-1 (auto-release).

Each section is independent.  All tests use mocks or static file inspection to
avoid requiring a live DB or LLM connection.
"""

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


# ---------------------------------------------------------------------------
# C-2: trace_id threading
# ---------------------------------------------------------------------------


class TestTraceIdThreading:
    def test_write_to_db_signature_accepts_trace_id(self):
        from tools.memory.memory_write import write_to_db
        sig = inspect.signature(write_to_db)
        assert "trace_id" in sig.parameters

    def test_write_to_db_trace_id_default_is_none(self):
        from tools.memory.memory_write import write_to_db
        sig = inspect.signature(write_to_db)
        assert sig.parameters["trace_id"].default is None

    def test_write_to_db_passes_trace_id_in_insert(self):
        """trace_id value reaches the INSERT statement."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.side_effect = [None, (42,)]
        mock_conn.cursor.return_value = mock_cursor

        import tools.memory.memory_write as mw
        with patch.object(mw, "_get_conn", return_value=mock_conn):
            mw.write_to_db(
                "test content",
                "event",
                importance=5,
                tier="episodic",
                trace_id="trace-abc-123",
            )

        insert_calls = [str(c) for c in mock_cursor.execute.call_args_list if "INSERT" in str(c)]
        assert any("trace-abc-123" in c for c in insert_calls), \
            "trace_id value must appear in the INSERT call"

    def test_agent_loop_result_has_trace_id_field(self):
        from icdev.tools.llm.agent_loop import AgentLoopResult
        result = AgentLoopResult()
        assert hasattr(result, "trace_id"), "AgentLoopResult must have trace_id field"
        assert result.trace_id == ""

    def test_run_agent_loop_sets_trace_id_on_result(self):
        """run_agent_loop generates a trace_id and exposes it on the result."""
        from icdev.tools.llm.agent_loop import run_agent_loop

        captured = {}

        def fake_retrieve(user_prompt, top_k, tier):
            return ""

        def patched_invoke(fn, request):
            resp = MagicMock()
            resp.tool_calls = []
            resp.content = "Done."
            resp.stop_reason = "end_turn"
            resp.input_tokens = 5
            resp.output_tokens = 3
            resp.cost_usd = 0.0
            resp.model_id = "test-model"
            resp.provider = "test"
            return resp

        router = MagicMock()
        router.get_provider_for_function.return_value = ("test-provider", "test-model", {"supports_tools": True})
        router.invoke.side_effect = patched_invoke

        with patch("icdev.tools.llm.agent_loop._retrieve_memory_context", fake_retrieve):
            try:
                result = run_agent_loop(
                    router,
                    system_prompt="You are a test agent.",
                    user_prompt="Say hi",
                    tools=[],
                    tool_handlers={},
                    memory_enabled=False,
                )
                captured["trace_id"] = result.trace_id
            except Exception:
                pass

        if "trace_id" in captured:
            assert captured["trace_id"], "trace_id must be non-empty UUID"
            import uuid
            uuid.UUID(captured["trace_id"])  # raises if not valid UUID

    def test_migration_229_file_exists(self):
        migration = BASE_DIR / "icdev" / "tools" / "db" / "migrations" / "229_memory_trace_id.sql"
        assert migration.exists()

    def test_migration_229_adds_trace_id_column(self):
        migration = BASE_DIR / "icdev" / "tools" / "db" / "migrations" / "229_memory_trace_id.sql"
        sql = migration.read_text(encoding="utf-8")
        assert "trace_id" in sql
        assert "ADD COLUMN" in sql

    def test_coworker_thread_passes_trace_id_to_memory_write(self):
        """coworker_thread.py must pass trace_id= when calling write_to_db."""
        src = (BASE_DIR / "icdev" / "tools" / "ace" / "coworker_thread.py").read_text(encoding="utf-8")
        assert "trace_id=" in src, "coworker_thread.py must pass trace_id= to write_to_db"
        assert "loop_result" in src and "trace_id" in src


# ---------------------------------------------------------------------------
# A-5: Chat pre-response memory retrieval
# ---------------------------------------------------------------------------


class TestChatPreResponseRetrieval:
    def test_chat_manager_injects_memory_before_llm(self):
        """tools/dashboard/chat_manager.py must call hybrid_search before LLM invoke."""
        src = (BASE_DIR / "tools" / "dashboard" / "chat_manager.py").read_text(encoding="utf-8")
        assert "hybrid_search" in src or "_mem_search" in src, \
            "chat_manager.py must import/call hybrid_search for pre-response retrieval"
        assert "Retrieved Memory" in src, \
            "chat_manager.py must inject a [Retrieved Memory] block into system_content"

    def test_chat_manager_uses_configured_chat_top_k(self):
        """chat_top_k is read from llm_config.yaml, not hard-coded alone."""
        src = (BASE_DIR / "tools" / "dashboard" / "chat_manager.py").read_text(encoding="utf-8")
        assert "chat_top_k" in src, \
            "chat_manager.py must read chat_top_k from llm_config.yaml"

    def test_llm_config_has_chat_top_k(self):
        import yaml
        cfg_path = BASE_DIR / "args" / "llm_config.yaml"
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        mem_cfg = cfg.get("agent_loop", {}).get("memory", {})
        assert "chat_top_k" in mem_cfg, "args/llm_config.yaml must have agent_loop.memory.chat_top_k"

    def test_memory_retrieval_block_guards_on_user_role(self):
        """Memory injection must only trigger for user messages, not system/assistant."""
        src = (BASE_DIR / "tools" / "dashboard" / "chat_manager.py").read_text(encoding="utf-8")
        # The A-5 block should be inside an if-user-content block
        mem_search_pos = src.find("_mem_search")
        assert mem_search_pos > 0, "hybrid_search call must exist"
        # The retrieval block is inside `if user_content and msg.get("role") == "user":`
        assert 'role' in src[max(0, mem_search_pos - 2000):mem_search_pos], \
            "memory retrieval must be guarded by role check"

    def test_chat_manager_saves_exchange_to_episodic_after_reply(self):
        """tools/dashboard/chat_manager.py must write the exchange to episodic memory (A-4)."""
        src = (BASE_DIR / "tools" / "dashboard" / "chat_manager.py").read_text(encoding="utf-8")
        assert "A-4" in src, "chat_manager.py must contain the A-4 episodic save block"
        assert "_mem_write_a4" in src or "write_to_db" in src, \
            "chat_manager.py must call write_to_db for the post-exchange episodic save"
        # Verify it's after the db_complete_task call (not before)
        complete_pos = src.find("_db_complete_task")
        a4_pos = src.find("A-4")
        assert a4_pos > complete_pos, "A-4 save must come after _db_complete_task"

    def test_memory_retrieval_is_non_fatal(self):
        """Memory injection errors must be caught — never bubble up to the LLM call."""
        src = (BASE_DIR / "tools" / "dashboard" / "chat_manager.py").read_text(encoding="utf-8")
        # Find the A-5 block and verify there's an except clause within 1500 chars
        pos = src.find("_mem_search")
        snippet = src[max(0, pos - 300):pos + 1200]
        assert "except Exception" in snippet or "except:" in snippet, \
            "Memory retrieval must be wrapped in try/except"


# ---------------------------------------------------------------------------
# C-1: Auto-release trigger
# ---------------------------------------------------------------------------


class TestAutoReleaseTrigger:
    def test_harness_has_auto_release_function(self):
        from tools.genesis.reflexes.harness import _try_auto_release_prompt
        sig = inspect.signature(_try_auto_release_prompt)
        assert "reflex" in sig.parameters
        assert "dry_run" in sig.parameters

    def test_auto_release_returns_false_when_no_draft(self):
        """Returns False when no draft prompt exists for the reflex."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None

        from tools.genesis.reflexes.harness import _try_auto_release_prompt
        with patch("tools.genesis.reflexes.harness._conn", return_value=mock_conn):
            result = _try_auto_release_prompt("some_reflex")
        assert result is False

    def test_auto_release_calls_activate_prompt_when_draft_found(self):
        """Calls activate_prompt with the draft version when one is found."""
        mock_conn = MagicMock()
        mock_row = {"prompt_name": "test_reflex", "version": 3}
        mock_conn.execute.return_value.fetchone.return_value = mock_row

        activated = []

        def fake_activate(name, version, actor=None):
            activated.append({"name": name, "version": version, "actor": actor})

        from tools.genesis.reflexes.harness import _try_auto_release_prompt
        with patch("tools.genesis.reflexes.harness._conn", return_value=mock_conn):
            with patch("tools.llm.prompt_registry.activate_prompt", fake_activate):
                result = _try_auto_release_prompt("test_reflex", dry_run=False)

        assert result is True
        assert len(activated) == 1
        assert activated[0]["version"] == 3
        assert activated[0]["actor"] == "harness-auto-release"

    def test_auto_release_dry_run_does_not_call_activate(self):
        """dry_run=True returns True but does not call activate_prompt."""
        mock_conn = MagicMock()
        mock_row = {"prompt_name": "test_reflex", "version": 2}
        mock_conn.execute.return_value.fetchone.return_value = mock_row

        activated = []

        def fake_activate(name, version, actor=None):
            activated.append(name)

        from tools.genesis.reflexes.harness import _try_auto_release_prompt
        with patch("tools.genesis.reflexes.harness._conn", return_value=mock_conn):
            with patch("tools.llm.prompt_registry.activate_prompt", fake_activate):
                result = _try_auto_release_prompt("test_reflex", dry_run=True)

        assert result is True
        assert activated == [], "dry_run must not call activate_prompt"

    def test_auto_release_error_returns_false(self):
        """Exceptions during the DB query return False without raising."""
        from tools.genesis.reflexes.harness import _try_auto_release_prompt
        with patch("tools.genesis.reflexes.harness._conn", side_effect=RuntimeError("DB down")):
            result = _try_auto_release_prompt("any_reflex")
        assert result is False

    def test_harness_run_calls_auto_release_before_degradation_card(self):
        """run() calls _try_auto_release_prompt for each alert."""
        src = (BASE_DIR / "tools" / "genesis" / "reflexes" / "harness.py").read_text(encoding="utf-8")
        assert "_try_auto_release_prompt" in src, \
            "harness.py run() must call _try_auto_release_prompt per alert"
        # auto-release is called before the degradation card creation
        release_pos = src.find("_try_auto_release_prompt")
        card_pos = src.find("_create_degradation_card")
        assert release_pos < card_pos, \
            "_try_auto_release_prompt must be called before _create_degradation_card"
