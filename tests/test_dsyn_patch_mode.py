# CUI // SP-CTI
"""Tests for dsyn-patch-01/02/03: patch_mode generation, suggestion notifications,
and suggestions API endpoint filtering."""
from __future__ import annotations

import contextlib
import importlib
import inspect
import sqlite3
from unittest.mock import MagicMock


# ── Shared SQLite shim ─────────────────────────────────────────────────────────

def _make_raw_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for ddl in [
        """CREATE TABLE dic_suggestions (
            suggestion_id TEXT PRIMARY KEY, section_id TEXT, doc_id TEXT,
            collection_id TEXT, trigger_event_id TEXT, canvas_source TEXT DEFAULT 'unknown',
            suggested_content TEXT DEFAULT '', current_content TEXT, rationale TEXT,
            status TEXT DEFAULT 'pending', created_at TEXT NOT NULL,
            updated_at TEXT, tenant_id TEXT, classification TEXT DEFAULT 'CUI'
        )""",
        """CREATE TABLE dic_suggestion_decisions (
            decision_id TEXT PRIMARY KEY, suggestion_id TEXT NOT NULL,
            decision TEXT NOT NULL, decided_by TEXT, decided_at TEXT NOT NULL,
            note TEXT, tenant_id TEXT, classification TEXT DEFAULT 'CUI'
        )""",
        """CREATE TABLE dic_collection_members (
            member_id TEXT PRIMARY KEY, collection_id TEXT, user_id TEXT, role TEXT
        )""",
        """CREATE TABLE notification_log (
            id TEXT PRIMARY KEY, event_type TEXT, adapter TEXT, severity TEXT,
            title TEXT, delivered INTEGER DEFAULT 0, error TEXT, created_at TEXT
        )""",
    ]:
        conn.execute(ddl)
    conn.commit()
    return conn


class _ShimConn:
    """Wraps sqlite3 connection and translates %s→? for compatibility with PG-style code."""
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        return self._conn.execute(sql.replace("%s", "?"), params)

    def commit(self):
        self._conn.commit()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def _make_shim(raw=None):
    return _ShimConn(raw or _make_raw_conn())


@contextlib.contextmanager
def _patch_module_attr(module_path: str, attr: str, value):
    """Patch an attribute directly on an already-imported module object."""
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
# dsyn-patch-01: patch_mode in doc_generator.regenerate_section
# ══════════════════════════════════════════════════════════════════════════════

class TestPatchMode:
    """Verify regenerate_section has correct patch_mode/change_context signature and logic."""

    def _source(self):
        from tools.document_intelligence import doc_generator
        return inspect.getsource(doc_generator.regenerate_section)

    def test_patch_mode_param_exists(self):
        from tools.document_intelligence import doc_generator
        sig = inspect.signature(doc_generator.regenerate_section)
        assert "patch_mode" in sig.parameters

    def test_change_context_param_exists(self):
        from tools.document_intelligence import doc_generator
        sig = inspect.signature(doc_generator.regenerate_section)
        assert "change_context" in sig.parameters

    def test_default_patch_mode_is_false(self):
        from tools.document_intelligence import doc_generator
        sig = inspect.signature(doc_generator.regenerate_section)
        assert sig.parameters["patch_mode"].default is False

    def test_default_change_context_is_empty_string(self):
        from tools.document_intelligence import doc_generator
        sig = inspect.signature(doc_generator.regenerate_section)
        assert sig.parameters["change_context"].default == ""

    def test_source_contains_targeted_patch_text(self):
        src = self._source()
        assert "TARGETED PATCH" in src, "patch_mode branch must contain 'TARGETED PATCH'"

    def test_source_contains_keep_marker(self):
        src = self._source()
        assert "[KEEP]" in src, "patch_mode prompt must instruct use of [KEEP] markers"

    def test_source_branches_on_patch_mode(self):
        src = self._source()
        assert "if patch_mode" in src or "patch_mode:" in src

    def test_source_prepends_change_context(self):
        src = self._source()
        assert "change_context" in src

    def test_change_context_prepended_in_both_branches(self):
        src = self._source()
        # change_context should appear at least twice in the function body
        assert src.count("change_context") >= 2


# ══════════════════════════════════════════════════════════════════════════════
# dsyn-patch-02: suggestion_store notification wiring
# ══════════════════════════════════════════════════════════════════════════════

class TestSuggestionNotifications:
    """Verify create_suggestion inserts notification_log rows for editors/reviewers."""

    def _seed_members(self, shim, roles):
        for i, role in enumerate(roles):
            shim.execute(
                "INSERT INTO dic_collection_members (member_id, collection_id, user_id, role)"
                " VALUES (?,?,?,?)",
                (f"mem-{i}", "col-001", f"user-{i}", role),
            )
        shim.commit()

    def test_notification_inserted_for_editor(self):
        shim = _make_shim()
        self._seed_members(shim, ["editor"])
        with _patch_module_attr("tools.document_intelligence.suggestion_store", "get_connection",
                                MagicMock(return_value=shim)):
            from tools.document_intelligence import suggestion_store
            suggestion_store.create_suggestion(
                collection_id="col-001", canvas_source="ndc",
                suggested_content="Updated topology section.", rationale="Topology drift",
            )
        rows = shim.execute(
            "SELECT * FROM notification_log WHERE event_type='dic_suggestion_created'"
        ).fetchall()
        assert len(rows) >= 1

    def test_notification_inserted_for_reviewer(self):
        shim = _make_shim()
        self._seed_members(shim, ["reviewer"])
        with _patch_module_attr("tools.document_intelligence.suggestion_store", "get_connection",
                                MagicMock(return_value=shim)):
            from tools.document_intelligence import suggestion_store
            suggestion_store.create_suggestion(
                collection_id="col-001", canvas_source="compliance",
                suggested_content="POAM update.", rationale="POAM overdue",
            )
        rows = shim.execute(
            "SELECT * FROM notification_log WHERE event_type='dic_suggestion_created'"
        ).fetchall()
        assert len(rows) >= 1

    def test_notification_not_inserted_for_viewer_role(self):
        shim = _make_shim()
        self._seed_members(shim, ["viewer"])
        with _patch_module_attr("tools.document_intelligence.suggestion_store", "get_connection",
                                MagicMock(return_value=shim)):
            from tools.document_intelligence import suggestion_store
            suggestion_store.create_suggestion(
                collection_id="col-001", canvas_source="sipa",
                suggested_content="Vulnerability mitigation.", rationale="High severity",
            )
        rows = shim.execute(
            "SELECT * FROM notification_log WHERE event_type='dic_suggestion_created'"
        ).fetchall()
        assert len(rows) == 0

    def test_notification_canvas_source_in_title(self):
        shim = _make_shim()
        self._seed_members(shim, ["editor"])
        with _patch_module_attr("tools.document_intelligence.suggestion_store", "get_connection",
                                MagicMock(return_value=shim)):
            from tools.document_intelligence import suggestion_store
            suggestion_store.create_suggestion(
                collection_id="col-001", canvas_source="aiify",
                suggested_content="Grade drop update.", rationale="Score dropped",
            )
        rows = shim.execute(
            "SELECT title FROM notification_log WHERE event_type='dic_suggestion_created'"
        ).fetchall()
        assert len(rows) >= 1
        title = rows[0][0] if isinstance(rows[0], (list, tuple)) else rows[0]["title"]
        assert "aiify" in title

    def test_notification_failure_does_not_block_suggestion_creation(self):
        """notification_log missing → suggestion still created (best-effort)."""
        shim = _make_shim()
        shim._conn.execute("DROP TABLE notification_log")
        shim.commit()
        with _patch_module_attr("tools.document_intelligence.suggestion_store", "get_connection",
                                MagicMock(return_value=shim)):
            from tools.document_intelligence import suggestion_store
            sug_id = suggestion_store.create_suggestion(
                collection_id="col-001", canvas_source="zig",
                suggested_content="Pillar gap.", rationale="Below threshold",
            )
        row = shim.execute(
            "SELECT suggestion_id FROM dic_suggestions WHERE suggestion_id=?", (sug_id,)
        ).fetchone()
        assert row is not None

    def test_create_suggestion_returns_string_id(self):
        shim = _make_shim()
        with _patch_module_attr("tools.document_intelligence.suggestion_store", "get_connection",
                                MagicMock(return_value=shim)):
            from tools.document_intelligence import suggestion_store
            result = suggestion_store.create_suggestion(
                collection_id="col-001", canvas_source="network",
                suggested_content="Network migration update.", rationale="Phase complete",
            )
        assert isinstance(result, str) and result.startswith("sug_")


# ══════════════════════════════════════════════════════════════════════════════
# dsyn-patch-03: suggestions API filtering
# ══════════════════════════════════════════════════════════════════════════════

class TestSuggestionsAPIFiltering:
    """Verify get_pending_suggestions / get_suggestion / decide_suggestion work correctly."""

    def _seed(self, shim, rows):
        for r in rows:
            shim.execute(
                "INSERT INTO dic_suggestions "
                "(suggestion_id, section_id, doc_id, collection_id, canvas_source, "
                " suggested_content, rationale, status, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (r["id"], r.get("sec", ""), r.get("doc", ""),
                 r.get("col", "col-001"), r.get("src", "ndc"),
                 "content", "rationale", r.get("status", "pending"),
                 "2026-01-01T00:00:00"),
            )
        shim.commit()

    def test_filter_by_collection_id(self):
        shim = _make_shim()
        self._seed(shim, [{"id": "sug-a", "col": "col-001"}, {"id": "sug-b", "col": "col-002"}])
        with _patch_module_attr("tools.document_intelligence.suggestion_store", "get_connection",
                                MagicMock(return_value=shim)):
            from tools.document_intelligence.suggestion_store import get_pending_suggestions
            results = get_pending_suggestions(collection_id="col-001")
        ids = [r.get("suggestion_id", "") for r in results]
        assert "sug-a" in ids and "sug-b" not in ids

    def test_filter_by_canvas_source(self):
        shim = _make_shim()
        self._seed(shim, [{"id": "sug-c", "src": "ndc"}, {"id": "sug-d", "src": "compliance"}])
        with _patch_module_attr("tools.document_intelligence.suggestion_store", "get_connection",
                                MagicMock(return_value=shim)):
            from tools.document_intelligence.suggestion_store import get_pending_suggestions
            results = get_pending_suggestions(canvas_source="ndc")
        ids = [r.get("suggestion_id", "") for r in results]
        assert "sug-c" in ids and "sug-d" not in ids

    def test_filter_by_status_default_pending(self):
        shim = _make_shim()
        self._seed(shim, [
            {"id": "sug-e", "status": "pending"},
            {"id": "sug-f", "status": "accepted"},
        ])
        with _patch_module_attr("tools.document_intelligence.suggestion_store", "get_connection",
                                MagicMock(return_value=shim)):
            from tools.document_intelligence.suggestion_store import get_pending_suggestions
            results = get_pending_suggestions()
        ids = [r.get("suggestion_id", "") for r in results]
        assert "sug-e" in ids and "sug-f" not in ids

    def test_get_suggestion_by_id(self):
        shim = _make_shim()
        self._seed(shim, [{"id": "sug-g", "sec": "sec-001"}])
        with _patch_module_attr("tools.document_intelligence.suggestion_store", "get_connection",
                                MagicMock(return_value=shim)):
            from tools.document_intelligence.suggestion_store import get_suggestion
            result = get_suggestion("sug-g")
        assert result is not None and result.get("suggestion_id") == "sug-g"

    def test_get_suggestion_not_found_returns_none(self):
        shim = _make_shim()
        with _patch_module_attr("tools.document_intelligence.suggestion_store", "get_connection",
                                MagicMock(return_value=shim)):
            from tools.document_intelligence.suggestion_store import get_suggestion
            result = get_suggestion("no-such-id")
        assert result is None or result == {}

    def test_decide_suggestion_accept(self):
        shim = _make_shim()
        self._seed(shim, [{"id": "sug-h"}])
        with _patch_module_attr("tools.document_intelligence.suggestion_store", "get_connection",
                                MagicMock(return_value=shim)):
            from tools.document_intelligence.suggestion_store import decide_suggestion
            ok = decide_suggestion("sug-h", "accepted", "reviewer-1")
        assert ok is True
        row = shim.execute(
            "SELECT status FROM dic_suggestions WHERE suggestion_id=?", ("sug-h",)
        ).fetchone()
        status = row[0] if isinstance(row, (list, tuple)) else row["status"]
        assert status == "accepted"

    def test_decide_suggestion_already_decided_returns_false(self):
        shim = _make_shim()
        self._seed(shim, [{"id": "sug-i", "status": "accepted"}])
        with _patch_module_attr("tools.document_intelligence.suggestion_store", "get_connection",
                                MagicMock(return_value=shim)):
            from tools.document_intelligence.suggestion_store import decide_suggestion
            ok = decide_suggestion("sug-i", "rejected", "reviewer-1")
        assert ok is False

    def test_decide_suggestion_invalid_decision_raises(self):
        import pytest
        shim = _make_shim()
        self._seed(shim, [{"id": "sug-j"}])
        with _patch_module_attr("tools.document_intelligence.suggestion_store", "get_connection",
                                MagicMock(return_value=shim)):
            from tools.document_intelligence.suggestion_store import decide_suggestion
            with pytest.raises(ValueError, match="decision must be one of"):
                decide_suggestion("sug-j", "maybe", "reviewer-1")
