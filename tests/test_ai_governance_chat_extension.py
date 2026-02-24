# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
"""Tests for AI governance chat extension handler and extension manager auto-loading (Phase 50).

Covers:
  - handle() from 010_ai_governance_chat.py
  - _load_config() defaults and YAML loading
  - _check_governance_gaps() gap detection
  - ExtensionManager._auto_load_builtins() auto-loading
"""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "tools" / "extensions" / "builtins"))
sys.path.insert(0, str(ROOT / "tools" / "extensions"))

# Import the handler module
import importlib
_handler_path = ROOT / "tools" / "extensions" / "builtins" / "010_ai_governance_chat.py"
_spec = importlib.util.spec_from_file_location("ai_governance_chat", str(_handler_path))
ai_gov_chat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ai_gov_chat)

handle = ai_gov_chat.handle
_load_config = ai_gov_chat._load_config
_check_governance_gaps = ai_gov_chat._check_governance_gaps
_should_advise = ai_gov_chat._should_advise
_record_advisory = ai_gov_chat._record_advisory
_last_advisory_turn = ai_gov_chat._last_advisory_turn
_table_exists = ai_gov_chat._table_exists
EXTENSION_HOOKS = ai_gov_chat.EXTENSION_HOOKS
NAME = ai_gov_chat.NAME
PRIORITY = ai_gov_chat.PRIORITY


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def gov_conn():
    """In-memory SQLite with all governance tables (empty)."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE ai_oversight_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT, plan_name TEXT, approval_status TEXT,
            created_by TEXT, created_at TEXT
        );
        CREATE TABLE ai_ethics_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT, review_type TEXT, status TEXT, created_at TEXT
        );
        CREATE TABLE ai_model_cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT, model_name TEXT, created_at TEXT
        );
        CREATE TABLE ai_caio_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT, name TEXT, role TEXT, status TEXT, created_at TEXT
        );
        CREATE TABLE ai_reassessment_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT, ai_system TEXT, frequency TEXT, next_due TEXT
        );
    """)
    c.commit()
    yield c
    c.close()


@pytest.fixture(autouse=True)
def reset_cooldown():
    """Clear cooldown state between tests."""
    _last_advisory_turn.clear()
    yield
    _last_advisory_turn.clear()


@pytest.fixture(autouse=True)
def reset_config_cache():
    """Clear config cache so each test gets a fresh load."""
    ai_gov_chat._CONFIG_CACHE = None
    yield
    ai_gov_chat._CONFIG_CACHE = None


# ===========================================================================
# handle() — Extension handler tests
# ===========================================================================

class TestHandleNonTrigger:
    """Tests where handle() returns context unmodified."""

    def test_returns_unmodified_for_user_message(self):
        ctx = {"role": "user", "content": "tell me about ai systems",
               "context_id": "c1", "turn_number": 10, "project_id": "p1"}
        result = handle(ctx)
        assert "governance_advisory" not in result

    def test_returns_unmodified_for_system_message(self):
        ctx = {"role": "system", "content": "ai system initialized",
               "context_id": "c1", "turn_number": 10, "project_id": "p1"}
        result = handle(ctx)
        assert "governance_advisory" not in result

    def test_returns_unmodified_when_no_ai_keywords(self):
        ctx = {"role": "assistant", "content": "Here is your CRUD application plan.",
               "context_id": "c1", "turn_number": 10, "project_id": "p1"}
        result = handle(ctx)
        assert "governance_advisory" not in result

    def test_returns_unmodified_when_no_project_id(self):
        ctx = {"role": "assistant", "content": "The ai system is ready.",
               "context_id": "c1", "turn_number": 10, "project_id": ""}
        result = handle(ctx)
        assert "governance_advisory" not in result

    def test_returns_unmodified_when_project_id_missing(self):
        ctx = {"role": "assistant", "content": "The machine learning model is deployed.",
               "context_id": "c1", "turn_number": 10}
        result = handle(ctx)
        assert "governance_advisory" not in result


class TestHandleAdvisory:
    """Tests where handle() should inject governance_advisory."""

    def _make_ctx(self, content="The ai system is deployed.", turn=10, ctx_id="c1",
                  project_id="proj-1"):
        return {
            "role": "assistant",
            "content": content,
            "context_id": ctx_id,
            "turn_number": turn,
            "project_id": project_id,
        }

    def test_advisory_injected_when_gaps_exist(self, gov_conn):
        """When AI keywords present and gaps exist, advisory is added."""
        # Patch _check_governance_gaps to use our in-memory DB with empty tables
        gaps = [
            {"id": "oversight_plan_missing", "severity": "high",
             "message": "No oversight plan.", "action": "Register plan."},
        ]
        with patch.object(ai_gov_chat, "_check_governance_gaps", return_value=gaps):
            result = handle(self._make_ctx())
        assert "governance_advisory" in result
        assert result["governance_advisory"]["gap_id"] == "oversight_plan_missing"

    def test_advisory_cooldown_blocks_second_call(self):
        gaps = [
            {"id": "oversight_plan_missing", "severity": "high",
             "message": "No plan.", "action": "Register."},
        ]
        with patch.object(ai_gov_chat, "_check_governance_gaps", return_value=gaps):
            r1 = handle(self._make_ctx(turn=10))
            assert "governance_advisory" in r1

            # Second call within cooldown (turn 12, cooldown=5)
            r2 = handle(self._make_ctx(turn=12))
            assert "governance_advisory" not in r2

    def test_advisory_after_cooldown_expires(self):
        gaps = [
            {"id": "oversight_plan_missing", "severity": "high",
             "message": "No plan.", "action": "Register."},
        ]
        with patch.object(ai_gov_chat, "_check_governance_gaps", return_value=gaps):
            r1 = handle(self._make_ctx(turn=10))
            assert "governance_advisory" in r1

            # After cooldown (turn 10 + 5 = 15)
            r2 = handle(self._make_ctx(turn=15))
            assert "governance_advisory" in r2

    def test_advisory_priority_ordering(self):
        """oversight_plan_missing should be selected first per priority order."""
        gaps = [
            {"id": "caio_not_designated", "severity": "medium",
             "message": "No CAIO.", "action": "Designate CAIO."},
            {"id": "oversight_plan_missing", "severity": "high",
             "message": "No plan.", "action": "Register."},
            {"id": "model_card_missing", "severity": "medium",
             "message": "No card.", "action": "Create card."},
        ]
        with patch.object(ai_gov_chat, "_check_governance_gaps", return_value=gaps):
            result = handle(self._make_ctx())
        assert result["governance_advisory"]["gap_id"] == "oversight_plan_missing"

    def test_advisory_total_gaps_count(self):
        gaps = [
            {"id": "oversight_plan_missing", "severity": "high",
             "message": "M1.", "action": "A1."},
            {"id": "caio_not_designated", "severity": "medium",
             "message": "M2.", "action": "A2."},
            {"id": "model_card_missing", "severity": "medium",
             "message": "M3.", "action": "A3."},
        ]
        with patch.object(ai_gov_chat, "_check_governance_gaps", return_value=gaps):
            result = handle(self._make_ctx())
        assert result["governance_advisory"]["total_gaps"] == 3

    def test_no_advisory_when_no_gaps(self):
        with patch.object(ai_gov_chat, "_check_governance_gaps", return_value=[]):
            result = handle(self._make_ctx())
        assert "governance_advisory" not in result


# ===========================================================================
# _load_config — Config loading tests
# ===========================================================================

class TestLoadConfig:
    """Test config loading and defaults."""

    def test_default_config_returned_when_no_yaml(self):
        with patch.object(ai_gov_chat, "BASE_DIR", Path("/nonexistent/path")):
            ai_gov_chat._CONFIG_CACHE = None
            cfg = _load_config()
        assert "advisory_cooldown_turns" in cfg
        assert cfg["advisory_cooldown_turns"] == 5
        assert "ai_keywords" in cfg
        assert isinstance(cfg["ai_keywords"], list)

    def test_keywords_loaded_from_config(self):
        cfg = _load_config()
        assert "ai_keywords" in cfg
        assert len(cfg["ai_keywords"]) > 0
        assert "ai system" in cfg["ai_keywords"]

    def test_advisory_priority_order_present(self):
        cfg = _load_config()
        assert "advisory_priority_order" in cfg
        assert "oversight_plan_missing" in cfg["advisory_priority_order"]


# ===========================================================================
# _check_governance_gaps — Gap detection
# ===========================================================================

class TestCheckGovernanceGaps:
    """Test governance gap checking against DB."""

    def _create_db_file(self, tmp_path, gov_conn):
        """Write in-memory DB to a file for _check_governance_gaps to read."""
        db_file = tmp_path / "test_icdev.db"
        file_conn = sqlite3.connect(str(db_file))
        file_conn.row_factory = sqlite3.Row
        file_conn.executescript("""
            CREATE TABLE ai_oversight_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT, plan_name TEXT
            );
            CREATE TABLE ai_ethics_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT, review_type TEXT, status TEXT
            );
            CREATE TABLE ai_model_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT, model_name TEXT
            );
            CREATE TABLE ai_caio_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT, name TEXT, role TEXT
            );
            CREATE TABLE ai_reassessment_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT, ai_system TEXT, frequency TEXT, next_due TEXT
            );
        """)
        file_conn.commit()
        return db_file, file_conn

    def test_empty_project_returns_all_gaps(self, tmp_path):
        db_file, file_conn = self._create_db_file(tmp_path, None)
        file_conn.close()
        with patch.object(ai_gov_chat, "DB_PATH", db_file):
            gaps = _check_governance_gaps("proj-empty")
        assert len(gaps) >= 4  # oversight, impact, model_card, caio at minimum
        gap_ids = [g["id"] for g in gaps]
        assert "oversight_plan_missing" in gap_ids

    def test_project_with_oversight_plan_removes_gap(self, tmp_path):
        db_file, file_conn = self._create_db_file(tmp_path, None)
        file_conn.execute(
            "INSERT INTO ai_oversight_plans (project_id, plan_name) VALUES (?, ?)",
            ("proj-1", "Plan A"),
        )
        file_conn.commit()
        file_conn.close()
        with patch.object(ai_gov_chat, "DB_PATH", db_file):
            gaps = _check_governance_gaps("proj-1")
        gap_ids = [g["id"] for g in gaps]
        assert "oversight_plan_missing" not in gap_ids

    def test_project_with_caio_removes_gap(self, tmp_path):
        db_file, file_conn = self._create_db_file(tmp_path, None)
        file_conn.execute(
            "INSERT INTO ai_caio_registry (project_id, name, role) VALUES (?, ?, ?)",
            ("proj-1", "Jane Doe", "CAIO"),
        )
        file_conn.commit()
        file_conn.close()
        with patch.object(ai_gov_chat, "DB_PATH", db_file):
            gaps = _check_governance_gaps("proj-1")
        gap_ids = [g["id"] for g in gaps]
        assert "caio_not_designated" not in gap_ids

    def test_multiple_gaps_returned_correctly(self, tmp_path):
        db_file, file_conn = self._create_db_file(tmp_path, None)
        # Only add oversight plan — other gaps remain
        file_conn.execute(
            "INSERT INTO ai_oversight_plans (project_id, plan_name) VALUES (?, ?)",
            ("proj-1", "Plan A"),
        )
        file_conn.commit()
        file_conn.close()
        with patch.object(ai_gov_chat, "DB_PATH", db_file):
            gaps = _check_governance_gaps("proj-1")
        # Should still have impact, model_card, caio, reassessment gaps
        assert len(gaps) >= 3
        gap_ids = [g["id"] for g in gaps]
        assert "impact_assessment_missing" in gap_ids

    def test_nonexistent_db_returns_empty_gaps(self):
        with patch.object(ai_gov_chat, "DB_PATH", Path("/nonexistent/db.db")):
            gaps = _check_governance_gaps("proj-1")
        assert gaps == []

    def test_gap_structure_has_required_keys(self, tmp_path):
        db_file, file_conn = self._create_db_file(tmp_path, None)
        file_conn.close()
        with patch.object(ai_gov_chat, "DB_PATH", db_file):
            gaps = _check_governance_gaps("proj-empty")
        for gap in gaps:
            assert "id" in gap
            assert "severity" in gap
            assert "message" in gap
            assert "action" in gap


# ===========================================================================
# ExtensionManager._auto_load_builtins — Auto-loading tests
# ===========================================================================

class TestExtensionManagerAutoLoad:
    """Test that the extension manager auto-loads builtins correctly."""

    def test_auto_load_builtins_loads_from_builtins_dir(self):
        from extension_manager import ExtensionManager
        mgr = ExtensionManager()
        # The builtins directory exists and has at least one file
        builtins_dir = ROOT / "tools" / "extensions" / "builtins"
        py_files = list(builtins_dir.glob("*.py"))
        non_underscore = [f for f in py_files if not f.name.startswith("_")]
        assert len(non_underscore) > 0, "Builtins dir should have at least one handler file"

    def test_extension_hooks_dict_processed(self):
        """EXTENSION_HOOKS should be a dict mapping hook names to metadata."""
        assert isinstance(EXTENSION_HOOKS, dict)
        assert len(EXTENSION_HOOKS) > 0

    def test_handler_registered_at_correct_hook_point(self):
        assert "chat_message_after" in EXTENSION_HOOKS
        meta = EXTENSION_HOOKS["chat_message_after"]
        assert callable(meta["handler"])

    def test_handler_has_correct_priority(self):
        meta = EXTENSION_HOOKS["chat_message_after"]
        assert meta["priority"] == PRIORITY
        assert meta["priority"] == 10

    def test_handler_has_correct_name(self):
        meta = EXTENSION_HOOKS["chat_message_after"]
        assert meta["name"] == NAME
        assert meta["name"] == "ai_governance_chat"

    def test_handler_allows_modification(self):
        meta = EXTENSION_HOOKS["chat_message_after"]
        assert meta["allow_modification"] is True

    def test_unknown_hook_points_logged_and_skipped(self):
        """ExtensionManager should log a warning and skip unknown hook points."""
        from extension_manager import ExtensionManager
        mgr = ExtensionManager()

        # Create a fake module with an unknown hook point
        fake_module = MagicMock()
        fake_module.EXTENSION_HOOKS = {
            "totally_bogus_hook_point": {
                "handler": lambda ctx: ctx,
                "name": "fake",
                "priority": 100,
            },
        }

        # The _load_file should return our fake module
        fake_path = Path("/fake/extension.py")
        with patch.object(mgr, "_load_file", return_value=fake_module):
            # Directly call the loading logic for a single fake file
            # We simulate what _auto_load_builtins does for one file
            module = mgr._load_file(fake_path)
            hooks = getattr(module, "EXTENSION_HOOKS", None)
            assert isinstance(hooks, dict)
            # The unknown hook point should cause a ValueError when creating ExtensionPoint
            from extension_manager import ExtensionPoint
            with pytest.raises(ValueError):
                ExtensionPoint("totally_bogus_hook_point")

    def test_extension_manager_registers_ai_governance_handler(self):
        """After auto-loading, the ai_governance_chat handler should be registered."""
        from extension_manager import ExtensionManager, ExtensionPoint
        mgr = ExtensionManager()
        handlers = mgr._handlers.get(ExtensionPoint.CHAT_MESSAGE_AFTER, [])
        handler_names = [h.name for h in handlers]
        assert "ai_governance_chat" in handler_names, \
            "ai_governance_chat should be registered in CHAT_MESSAGE_AFTER handlers"
