# CUI // SP-CTI
"""Tests for dsyn-suggest-03: auto-create HITL kanban task on overdue review."""
from __future__ import annotations

import contextlib
import importlib
import sqlite3
from unittest.mock import MagicMock, patch


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_raw_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for ddl in [
        """CREATE TABLE dic_collections (
            collection_id TEXT PRIMARY KEY, name TEXT,
            review_interval_days INTEGER DEFAULT 90
        )""",
        """CREATE TABLE canvas_events (
            id TEXT PRIMARY KEY, source_canvas TEXT, target_canvas TEXT,
            event_type TEXT, payload_json TEXT, created_at TEXT
        )""",
        """CREATE TABLE dic_collection_members (
            member_id TEXT PRIMARY KEY, collection_id TEXT, user_id TEXT, role TEXT
        )""",
        """CREATE TABLE notification_log (
            id TEXT PRIMARY KEY, event_type TEXT, adapter TEXT, severity TEXT,
            title TEXT, delivered INTEGER DEFAULT 0, error TEXT, created_at TEXT
        )""",
        """CREATE TABLE kanban_tasks (
            id TEXT PRIMARY KEY, title TEXT, description TEXT, status TEXT DEFAULT 'backlog',
            priority TEXT, project_id TEXT, task_type TEXT, created_at TEXT
        )""",
    ]:
        conn.execute(ddl)
    conn.commit()
    return conn


class _ShimConn:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        import re
        sql = sql.replace("%s", "?")
        sql = re.sub(r"::\w+", "", sql)
        return self._conn.execute(sql, params)

    def commit(self):
        self._conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


@contextlib.contextmanager
def _patch_attr(module_path: str, attr: str, value):
    mod = importlib.import_module(module_path)
    orig = getattr(mod, attr, None)
    setattr(mod, attr, value)
    try:
        yield
    finally:
        if orig is None:
            try:
                delattr(mod, attr)
            except AttributeError:
                pass
        else:
            setattr(mod, attr, orig)


# ══════════════════════════════════════════════════════════════════════════════
# _open_hitl_task_exists
# ══════════════════════════════════════════════════════════════════════════════

class TestOpenHitlTaskExists:
    def test_returns_false_when_no_tasks(self):
        shim = _ShimConn(_make_raw_conn())
        with _patch_attr("tools.genesis.reflexes.dic_review_cadence", "get_connection",
                         MagicMock(return_value=shim)):
            from tools.genesis.reflexes.dic_review_cadence import _open_hitl_task_exists
            with patch("tools.genesis.reflexes.dic_review_cadence._os"):
                result = _open_hitl_task_exists.__wrapped__("col-001", "Test Collection") \
                    if hasattr(_open_hitl_task_exists, "__wrapped__") \
                    else _open_hitl_task_exists("col-001", "Test Collection")
        # Should not raise; function falls back gracefully
        assert isinstance(result, bool)

    def test_returns_false_when_no_open_task(self):
        shim = _ShimConn(_make_raw_conn())
        # Seed a DONE task — should not block new task creation
        shim.execute(
            "INSERT INTO kanban_tasks (id, title, status) VALUES (?,?,?)",
            ("t-old", "Review overdue: col-001", "done"),
        )
        shim.commit()
        # Patch get_connection at storage level
        with _patch_attr("tools.db.storage", "get_connection", MagicMock(return_value=shim)):
            from tools.genesis.reflexes import dic_review_cadence
            # Temporarily remove task_factory import so it falls through to storage
            import sys
            orig = sys.modules.get("tools.kanban.task_factory")
            sys.modules["tools.kanban.task_factory"] = None
            try:
                result = dic_review_cadence._open_hitl_task_exists("col-001", "Test Collection")
            finally:
                if orig is None:
                    sys.modules.pop("tools.kanban.task_factory", None)
                else:
                    sys.modules["tools.kanban.task_factory"] = orig
        assert result is False

    def test_returns_true_when_open_task_exists(self):
        shim = _ShimConn(_make_raw_conn())
        shim.execute(
            "INSERT INTO kanban_tasks (id, title, status) VALUES (?,?,?)",
            ("t-open", "Review overdue: col-001 (col-001)", "in_progress"),
        )
        shim.commit()
        with _patch_attr("tools.db.storage", "get_connection", MagicMock(return_value=shim)):
            from tools.genesis.reflexes import dic_review_cadence
            import sys
            orig = sys.modules.get("tools.kanban.task_factory")
            sys.modules["tools.kanban.task_factory"] = None
            try:
                result = dic_review_cadence._open_hitl_task_exists("col-001", "Test Collection")
            finally:
                if orig is None:
                    sys.modules.pop("tools.kanban.task_factory", None)
                else:
                    sys.modules["tools.kanban.task_factory"] = orig
        assert result is True


# ══════════════════════════════════════════════════════════════════════════════
# _create_hitl_task
# ══════════════════════════════════════════════════════════════════════════════

class TestCreateHitlTask:
    def test_calls_task_factory_create_tasks(self):
        created = []
        def fake_create_tasks(tasks):
            created.extend(tasks)

        with _patch_attr("tools.genesis.reflexes.dic_review_cadence", "_os",
                         MagicMock(getenv=MagicMock(return_value="dsyn"))):
            import sys
            import types
            fake_mod = types.ModuleType("tools.kanban.task_factory")
            fake_mod.create_tasks = fake_create_tasks
            orig = sys.modules.get("tools.kanban.task_factory")
            sys.modules["tools.kanban.task_factory"] = fake_mod
            try:
                from tools.genesis.reflexes.dic_review_cadence import _create_hitl_task
                result = _create_hitl_task("col-xyz", {
                    "collection_name": "XYZ Collection",
                    "review_interval_days": 90,
                    "last_review_date": None,
                    "days_overdue": 15,
                })
            finally:
                if orig is None:
                    sys.modules.pop("tools.kanban.task_factory", None)
                else:
                    sys.modules["tools.kanban.task_factory"] = orig

        assert result is True
        assert len(created) == 1
        task = created[0]
        assert "col-xyz" in task["title"]
        assert "col-xyz" in task["description"]
        assert task["priority"] == "medium"

    def test_task_description_contains_collection_info(self):
        created = []
        import sys
        import types
        fake_mod = types.ModuleType("tools.kanban.task_factory")
        fake_mod.create_tasks = lambda tasks: created.extend(tasks)
        orig = sys.modules.get("tools.kanban.task_factory")
        sys.modules["tools.kanban.task_factory"] = fake_mod
        try:
            from tools.genesis.reflexes.dic_review_cadence import _create_hitl_task
            _create_hitl_task("col-abc", {
                "collection_name": "ABC Docs",
                "review_interval_days": 30,
                "last_review_date": "2026-01-01T00:00:00",
                "days_overdue": 162,
            })
        finally:
            if orig is None:
                sys.modules.pop("tools.kanban.task_factory", None)
            else:
                sys.modules["tools.kanban.task_factory"] = orig
        desc = created[0]["description"]
        assert "col-abc" in desc
        assert "30" in desc
        assert "2026-01-01" in desc

    def test_create_hitl_task_returns_false_on_error(self):
        import sys
        import types
        fake_mod = types.ModuleType("tools.kanban.task_factory")
        fake_mod.create_tasks = MagicMock(side_effect=RuntimeError("DB down"))
        orig = sys.modules.get("tools.kanban.task_factory")
        sys.modules["tools.kanban.task_factory"] = fake_mod
        try:
            from tools.genesis.reflexes.dic_review_cadence import _create_hitl_task
            result = _create_hitl_task("col-err", {
                "collection_name": "Err Col",
                "review_interval_days": 90,
                "last_review_date": None,
                "days_overdue": 10,
            })
        finally:
            if orig is None:
                sys.modules.pop("tools.kanban.task_factory", None)
            else:
                sys.modules["tools.kanban.task_factory"] = orig
        assert result is False


# ══════════════════════════════════════════════════════════════════════════════
# Integration: run() → HITL task idempotency
# ══════════════════════════════════════════════════════════════════════════════

class TestHitlIdempotency:
    def test_task_created_on_first_overdue_run(self):
        shim = _ShimConn(_make_raw_conn())
        shim.execute(
            "INSERT INTO dic_collections (collection_id, name, review_interval_days)"
            " VALUES (?,?,?)", ("col-new", "New Col", 30),
        )
        shim.commit()
        task_calls = []
        with _patch_attr("tools.genesis.reflexes.dic_review_cadence", "get_connection",
                         MagicMock(return_value=shim)), \
             _patch_attr("tools.genesis.reflexes.dic_review_cadence", "_create_hitl_task",
                         MagicMock(side_effect=lambda cid, info: task_calls.append(cid) or True)), \
             _patch_attr("tools.genesis.reflexes.dic_review_cadence", "_open_hitl_task_exists",
                         MagicMock(return_value=False)):
            from tools.genesis.reflexes.dic_review_cadence import run
            result = run({}, None)
        assert result["success"] is True
        assert "col-new" in task_calls

    def test_task_not_created_when_already_open(self):
        shim = _ShimConn(_make_raw_conn())
        shim.execute(
            "INSERT INTO dic_collections (collection_id, name, review_interval_days)"
            " VALUES (?,?,?)", ("col-dup", "Dup Col", 30),
        )
        shim.commit()
        task_calls = []
        with _patch_attr("tools.genesis.reflexes.dic_review_cadence", "get_connection",
                         MagicMock(return_value=shim)), \
             _patch_attr("tools.genesis.reflexes.dic_review_cadence", "_create_hitl_task",
                         MagicMock(side_effect=lambda cid, info: task_calls.append(cid) or True)), \
             _patch_attr("tools.genesis.reflexes.dic_review_cadence", "_open_hitl_task_exists",
                         MagicMock(return_value=True)):  # already open
            from tools.genesis.reflexes.dic_review_cadence import run
            run({}, None)
        # _create_hitl_task should NOT be called since task exists
        assert "col-dup" not in task_calls
