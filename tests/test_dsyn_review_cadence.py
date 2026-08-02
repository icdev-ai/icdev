# CUI // SP-CTI
"""Tests for dsyn-suggest-02: DIC review cadence reflex."""
from __future__ import annotations

import contextlib
import importlib
import sqlite3
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from tests import _sql_compat


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_raw_conn(with_collections=True):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    if with_collections:
        conn.execute("""
            CREATE TABLE dic_collections (
                collection_id TEXT PRIMARY KEY, name TEXT,
                review_interval_days INTEGER DEFAULT 90
            )
        """)
    for ddl in [
        """CREATE TABLE canvas_events (
            id TEXT PRIMARY KEY, source_canvas TEXT, target_canvas TEXT,
            event_type TEXT, payload_json TEXT, created_at TEXT,
            consumed_at TEXT
        )""",
        # dic_team_access is the real membership table (pg_consolidated.sql);
        # _emit_notifications reads editors/reviewers from it. An earlier
        # fixture invented "dic_collection_members", so the SELECT raised
        # "no such table", the best-effort except swallowed it, and the test
        # asserted 0 notifications against a no-op it had caused itself.
        """CREATE TABLE dic_team_access (
            access_id TEXT PRIMARY KEY, collection_id TEXT,
            user_id TEXT, role TEXT DEFAULT 'viewer',
            granted_by TEXT DEFAULT '', tenant_id TEXT DEFAULT 'default',
            created_at TEXT
        )""",
        """CREATE TABLE notification_log (
            id TEXT PRIMARY KEY, event_type TEXT, adapter TEXT, severity TEXT,
            title TEXT, delivered INTEGER DEFAULT 0, error TEXT, created_at TEXT
        )""",
    ]:
        conn.execute(ddl)
    conn.commit()
    return conn


def _make_shim(raw=None):
    """Translating wrapper over the in-memory DIC DB.

    The previous hand-rolled shim also stripped PG cast operators (``::text``).
    ``dic_review_cadence`` emits no casts, so that was dead code standing
    between the test and the runtime's own translate_sql; dropping it means
    this harness cannot drift from the behaviour it stands in for.

    ``unclosable`` because the reflex writes through ``with get_connection()``
    and the wrapper's ``__exit__`` would otherwise close the handle, taking the
    in-memory database with it before the test can read the row back.
    """
    return _sql_compat.translating(raw or _make_raw_conn(), unclosable=True)


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
# Module structure
# ══════════════════════════════════════════════════════════════════════════════

class TestReflexStructure:
    def test_module_importable(self):
        from tools.genesis.reflexes import dic_review_cadence
        assert dic_review_cadence is not None

    def test_has_run_function(self):
        from tools.genesis.reflexes import dic_review_cadence
        assert callable(dic_review_cadence.run)

    def test_cadence_minutes_is_1440(self):
        from tools.genesis.reflexes import dic_review_cadence
        assert dic_review_cadence.CADENCE_MINUTES == 1440

    def test_implementation_status_full(self):
        from tools.genesis.reflexes import dic_review_cadence
        assert dic_review_cadence.IMPLEMENTATION_STATUS == "full"

    def test_registered_in_tools_daemon(self):
        from tools.genesis import daemon
        assert "dic_review_cadence" in daemon.REFLEX_NAMES

    def test_registered_in_icdev_daemon(self):
        from icdev.tools.genesis import daemon
        assert "dic_review_cadence" in daemon.REFLEX_NAMES

    def test_genesis_config_has_entry(self):
        import yaml
        import pathlib
        with (pathlib.Path(__file__).parent.parent / "args/genesis_config.yaml").open() as f:
            cfg = yaml.safe_load(f)
        reflexes = cfg.get("reflexes", {})
        assert "dic_review_cadence" in reflexes

    def test_genesis_config_nightly_interval(self):
        import yaml
        import pathlib
        with (pathlib.Path(__file__).parent.parent / "args/genesis_config.yaml").open() as f:
            cfg = yaml.safe_load(f)
        entry = cfg["reflexes"]["dic_review_cadence"]
        assert entry["interval_seconds"] == 86400


# ══════════════════════════════════════════════════════════════════════════════
# Overdue detection logic
# ══════════════════════════════════════════════════════════════════════════════

class TestOverdueDetection:
    def _seed(self, shim, collection_id, interval_days, last_review_days_ago=None):
        shim.execute(
            "INSERT INTO dic_collections (collection_id, name, review_interval_days)"
            " VALUES (?,?,?)",
            (collection_id, f"Col {collection_id}", interval_days),
        )
        if last_review_days_ago is not None:
            review_dt = (datetime.now(timezone.utc) - timedelta(days=last_review_days_ago)).isoformat()
            shim._conn.execute(
                "CREATE TABLE IF NOT EXISTS dic_edit_history "
                "(id TEXT, collection_id TEXT, edited_at TEXT)"
            )
            shim._conn.execute(
                "INSERT INTO dic_edit_history (id, collection_id, edited_at) VALUES (?,?,?)",
                (f"eh-{collection_id}", collection_id, review_dt),
            )
        shim.commit()

    def test_overdue_collection_is_detected(self):
        shim = _make_shim()
        self._seed(shim, "col-overdue", interval_days=30, last_review_days_ago=45)
        from tools.genesis.reflexes.dic_review_cadence import _fetch_overdue_collections
        with _patch_attr("tools.genesis.reflexes.dic_review_cadence", "get_connection",
                         MagicMock(return_value=shim)):
            result = _fetch_overdue_collections(shim)
        ids = [r["collection_id"] for r in result]
        assert "col-overdue" in ids

    def test_within_interval_not_overdue(self):
        shim = _make_shim()
        self._seed(shim, "col-fresh", interval_days=90, last_review_days_ago=10)
        from tools.genesis.reflexes.dic_review_cadence import _fetch_overdue_collections
        with _patch_attr("tools.genesis.reflexes.dic_review_cadence", "get_connection",
                         MagicMock(return_value=shim)):
            result = _fetch_overdue_collections(shim)
        ids = [r["collection_id"] for r in result]
        assert "col-fresh" not in ids

    def test_no_review_ever_is_overdue(self):
        shim = _make_shim()
        self._seed(shim, "col-never", interval_days=90, last_review_days_ago=None)
        from tools.genesis.reflexes.dic_review_cadence import _fetch_overdue_collections
        with _patch_attr("tools.genesis.reflexes.dic_review_cadence", "get_connection",
                         MagicMock(return_value=shim)):
            result = _fetch_overdue_collections(shim)
        ids = [r["collection_id"] for r in result]
        assert "col-never" in ids

    def test_collection_without_interval_skipped(self):
        shim = _make_shim()
        shim.execute(
            "INSERT INTO dic_collections (collection_id, name, review_interval_days)"
            " VALUES (?,?,?)", ("col-no-interval", "C", None),
        )
        shim.commit()
        from tools.genesis.reflexes.dic_review_cadence import _fetch_overdue_collections
        with _patch_attr("tools.genesis.reflexes.dic_review_cadence", "get_connection",
                         MagicMock(return_value=shim)):
            result = _fetch_overdue_collections(shim)
        ids = [r["collection_id"] for r in result]
        assert "col-no-interval" not in ids


# ══════════════════════════════════════════════════════════════════════════════
# Event emission
# ══════════════════════════════════════════════════════════════════════════════

class TestEventEmission:
    def test_run_emits_canvas_event_for_overdue(self):
        shim = _make_shim()
        shim.execute(
            "INSERT INTO dic_collections (collection_id, name, review_interval_days)"
            " VALUES (?,?,?)", ("col-a", "Col A", 30),
        )
        shim.commit()
        # No review history → overdue immediately
        with _patch_attr("tools.genesis.reflexes.dic_review_cadence", "get_connection",
                         MagicMock(return_value=shim)):
            from tools.genesis.reflexes.dic_review_cadence import run
            result = run({}, None)
        assert result["success"] is True
        assert result["processed"] >= 1
        events = shim.execute(
            "SELECT * FROM canvas_events WHERE event_type='dic.review_overdue'"
        ).fetchall()
        assert len(events) >= 1

    def test_run_does_not_double_emit_same_day(self):
        shim = _make_shim()
        shim.execute(
            "INSERT INTO dic_collections (collection_id, name, review_interval_days)"
            " VALUES (?,?,?)", ("col-b", "Col B", 30),
        )
        shim.commit()
        # Pre-seed a today event to simulate already emitted
        import json as _json
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        shim.execute(
            "INSERT INTO canvas_events (id, source_canvas, target_canvas, event_type, payload_json, created_at)"
            " VALUES (?,?,?,?,?,?)",
            ("evt-existing", "dic", "dic", "dic.review_overdue",
             _json.dumps({"collection_id": "col-b"}), now),
        )
        shim.commit()
        with _patch_attr("tools.genesis.reflexes.dic_review_cadence", "get_connection",
                         MagicMock(return_value=shim)):
            from tools.genesis.reflexes.dic_review_cadence import run
            run({}, None)
        # Should not create a second event for col-b today
        events = shim.execute(
            "SELECT * FROM canvas_events WHERE event_type='dic.review_overdue'"
        ).fetchall()
        assert len(events) == 1  # only the pre-seeded one

    def test_run_notifies_editors(self):
        shim = _make_shim()
        shim.execute(
            "INSERT INTO dic_collections (collection_id, name, review_interval_days)"
            " VALUES (?,?,?)", ("col-c", "Col C", 30),
        )
        shim.execute(
            "INSERT INTO dic_team_access (access_id, collection_id, user_id, role)"
            " VALUES (?,?,?,?)", ("m1", "col-c", "editor1", "editor"),
        )
        shim.commit()
        with _patch_attr("tools.genesis.reflexes.dic_review_cadence", "get_connection",
                         MagicMock(return_value=shim)):
            from tools.genesis.reflexes.dic_review_cadence import run
            run({}, None)
        notifs = shim.execute(
            "SELECT * FROM notification_log WHERE event_type='dic.review_overdue'"
        ).fetchall()
        assert len(notifs) >= 1

    def test_run_returns_success_with_no_overdue(self):
        shim = _make_shim()
        # No collections at all
        with _patch_attr("tools.genesis.reflexes.dic_review_cadence", "get_connection",
                         MagicMock(return_value=shim)):
            from tools.genesis.reflexes.dic_review_cadence import run
            result = run({}, None)
        assert result["success"] is True
        assert result["processed"] == 0
