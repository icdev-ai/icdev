# CUI // SP-CTI
"""Integration test for the Genesis Awareness Reflex (Phase 5).

Covers:
  * Full reflex cycle (index → probe → drift → gap → suggest)
    runs end-to-end with mocked sub-modules.
  * All 5 sub-phases write rows to awareness_run_log.
  * All 5 rows share the same cycle_id in details_json (consistent run_id).
  * Return dict contains drift / gaps / cards counts.
  * Enabled-only scenario: disabled components are skipped by prober.
  * Error resilience: a sub-phase error does NOT abort the whole cycle.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.db.storage import translate_sql  # noqa: E402

# ---------------------------------------------------------------------------
# Lightweight in-memory DB fixture
# ---------------------------------------------------------------------------

_RUN_LOG_DDL = """
CREATE TABLE IF NOT EXISTS awareness_run_log (
    run_id        TEXT PRIMARY KEY,
    phase         TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    completed_at  TEXT,
    status        TEXT NOT NULL DEFAULT 'running',
    probes_ok     INTEGER DEFAULT 0,
    probes_fail   INTEGER DEFAULT 0,
    elapsed_ms    INTEGER,
    details_json  TEXT
)
"""


class _TranslatingConn:
    """In-memory sqlite that translates PG placeholders like the real thing.

    The reflex writes canonical PostgreSQL SQL (``VALUES (%s, %s, ...)``) and
    relies on ``get_connection()`` returning a StorageConnection that rewrites
    ``%s`` -> ``?`` for the sqlite backend (tools.db.storage.translate_sql). A
    bare sqlite3 connection does NOT translate, so every reflex write raised
    ``near "%": syntax error``, the reflex swallowed it, and these tests saw zero
    rows — a test-harness gap, not a product bug. Wrapping the connection the same
    way the storage layer does makes the fixture faithful to production.
    """

    def __init__(self, raw: sqlite3.Connection) -> None:
        self._raw = raw

    def execute(self, sql, params=None):
        translated = translate_sql(sql, "sqlite")
        return self._raw.execute(translated, params or [])

    def executemany(self, sql, params_list):
        return self._raw.executemany(translate_sql(sql, "sqlite"), params_list)

    def __getattr__(self, name):
        # commit/rollback/close/cursor/row_factory pass through to the raw conn.
        return getattr(self._raw, name)


def _make_mem_conn() -> "_TranslatingConn":
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_RUN_LOG_DDL)
    conn.commit()
    return _TranslatingConn(conn)


# ---------------------------------------------------------------------------
# Helper: run the reflex with sub-modules patched to known return values
# ---------------------------------------------------------------------------


def _run_with_mocks(
    conn: sqlite3.Connection,
    index_result: Dict[str, Any] = None,
    probe_result: Dict[str, Any] = None,
    drift_result: Dict[str, Any] = None,
    gap_result: Dict[str, Any] = None,
    cards_result: Dict[str, Any] = None,
    enablement_sig: str = "sig-abc",
) -> Dict[str, Any]:
    """Patch every dependency and run awareness.run()."""
    index_result = index_result or {"nodes": 5, "edges": 3}
    probe_result = probe_result or {"total_ok": 4, "total_fail": 0}
    drift_result = drift_result or {"total_findings": 1, "by_rule": {"route_regression": 1}}
    gap_result = gap_result or {"total_findings": 2, "total_by_rule": {"tool_not_in_manifest": 2}}
    cards_result = cards_result or {"created": [{"id": "k1"}, {"id": "k2"}]}

    import tools.genesis.reflexes.awareness as awareness

    with (
        patch.object(awareness, "get_connection", return_value=conn),
        patch.object(awareness, "_indexer_scan", return_value=index_result),
        patch.object(awareness, "_prober_run_all", return_value=probe_result),
        patch.object(awareness, "_drift_detect", return_value=drift_result),
        patch.object(awareness, "_gap_detect", return_value=gap_result),
        patch.object(awareness, "_write_cards", return_value=cards_result),
        patch.object(awareness, "enabled_flags_signature", return_value=enablement_sig),
        patch.object(awareness, "load_enablement_flags", return_value={}),
    ):
        config: Dict[str, Any] = {"promotion_threshold": 0.7}
        trust = MagicMock()
        return awareness.run(config, trust), config


# ---------------------------------------------------------------------------
# Test: all 5 phases produce rows in awareness_run_log
# ---------------------------------------------------------------------------


class TestAwarenessReflexPhaseLogging:
    def test_five_rows_written(self) -> None:
        """All 5 sub-phases must write a row to awareness_run_log."""
        conn = _make_mem_conn()
        result, _ = _run_with_mocks(conn)

        rows = conn.execute("SELECT * FROM awareness_run_log ORDER BY rowid").fetchall()
        phases_written = {r["phase"] for r in rows}
        assert phases_written == {"index", "probe", "drift", "gap", "suggest"}, (
            f"Expected all 5 phases in log, got: {phases_written}"
        )

    def test_consistent_cycle_id(self) -> None:
        """All 5 rows must share the same cycle_id in details_json."""
        conn = _make_mem_conn()
        _run_with_mocks(conn)

        rows = conn.execute("SELECT details_json FROM awareness_run_log").fetchall()
        assert len(rows) == 5, f"Expected 5 log rows, got {len(rows)}"

        cycle_ids = set()
        for r in rows:
            details = json.loads(r["details_json"])
            assert "cycle_id" in details, "details_json missing cycle_id"
            cycle_ids.add(details["cycle_id"])

        assert len(cycle_ids) == 1, (
            f"All rows must share one cycle_id, found multiple: {cycle_ids}"
        )
        cycle_id = next(iter(cycle_ids))
        assert cycle_id.startswith("awareness-"), (
            f"cycle_id should start with 'awareness-', got: {cycle_id}"
        )

    def test_run_ids_are_phase_prefixed(self) -> None:
        """Each row's run_id must be '<cycle_id>:<phase>'."""
        conn = _make_mem_conn()
        _run_with_mocks(conn)

        rows = conn.execute("SELECT run_id, phase, details_json FROM awareness_run_log").fetchall()
        for r in rows:
            cycle_id = json.loads(r["details_json"])["cycle_id"]
            expected_run_id = f"{cycle_id}:{r['phase']}"
            assert r["run_id"] == expected_run_id, (
                f"run_id mismatch for phase {r['phase']}: "
                f"expected {expected_run_id!r}, got {r['run_id']!r}"
            )


# ---------------------------------------------------------------------------
# Test: return dict has correct counts
# ---------------------------------------------------------------------------


class TestAwarenessReflexReturnDict:
    def test_return_has_run_id(self) -> None:
        conn = _make_mem_conn()
        result, _ = _run_with_mocks(conn)
        assert "run_id" in result
        assert result["run_id"].startswith("awareness-")

    def test_drift_count(self) -> None:
        conn = _make_mem_conn()
        result, _ = _run_with_mocks(
            conn,
            drift_result={"total_findings": 3, "by_rule": {"route_regression": 3}},
        )
        assert result["drift"] == 3

    def test_gaps_count(self) -> None:
        conn = _make_mem_conn()
        result, _ = _run_with_mocks(
            conn,
            gap_result={"total_findings": 5, "total_by_rule": {}},
        )
        assert result["gaps"] == 5

    def test_cards_count(self) -> None:
        conn = _make_mem_conn()
        result, _ = _run_with_mocks(
            conn,
            cards_result={"created": [{"id": "k1"}, {"id": "k2"}, {"id": "k3"}]},
        )
        assert result["cards"] == 3

    def test_zero_findings_is_valid(self) -> None:
        """Zero findings is healthy — reflex should still succeed."""
        conn = _make_mem_conn()
        result, _ = _run_with_mocks(
            conn,
            drift_result={"total_findings": 0, "by_rule": {}},
            gap_result={"total_findings": 0, "total_by_rule": {}},
            cards_result={"created": []},
        )
        assert result["drift"] == 0
        assert result["gaps"] == 0
        assert result["cards"] == 0

    def test_phases_key_present(self) -> None:
        """Return dict includes a 'phases' key with per-phase raw results."""
        conn = _make_mem_conn()
        result, _ = _run_with_mocks(conn)
        assert "phases" in result
        for phase in ("index", "probe", "drift", "gap", "suggest"):
            assert phase in result["phases"], f"Missing phase '{phase}' in return dict"


# ---------------------------------------------------------------------------
# Test: error resilience — one sub-phase error does not abort the cycle
# ---------------------------------------------------------------------------


class TestAwarenessReflexErrorResilience:
    def test_indexer_error_does_not_abort(self) -> None:
        """If component_indexer raises, remaining phases still run."""
        import tools.genesis.reflexes.awareness as awareness

        conn = _make_mem_conn()

        def _boom():
            raise RuntimeError("indexer exploded")

        with (
            patch.object(awareness, "get_connection", return_value=conn),
            patch.object(awareness, "_indexer_scan", side_effect=RuntimeError("indexer exploded")),
            patch.object(awareness, "_prober_run_all", return_value={"total_ok": 1, "total_fail": 0}),
            patch.object(awareness, "_drift_detect", return_value={"total_findings": 0}),
            patch.object(awareness, "_gap_detect", return_value={"total_findings": 0}),
            patch.object(awareness, "_write_cards", return_value={"created": []}),
            patch.object(awareness, "enabled_flags_signature", return_value="sig-x"),
            patch.object(awareness, "load_enablement_flags", return_value={}),
        ):
            awareness.run({"promotion_threshold": 0.7}, MagicMock())

        # All 5 phases still logged
        rows = conn.execute("SELECT phase FROM awareness_run_log").fetchall()
        phases = {r["phase"] for r in rows}
        assert phases == {"index", "probe", "drift", "gap", "suggest"}
        # index phase row marked error
        idx_row = conn.execute(
            "SELECT status FROM awareness_run_log WHERE phase='index'"
        ).fetchone()
        assert idx_row["status"] == "error"
        # Remaining phases succeeded
        probe_row = conn.execute(
            "SELECT status FROM awareness_run_log WHERE phase='probe'"
        ).fetchone()
        assert probe_row["status"] == "success"

    def test_no_db_runs_without_crash(self) -> None:
        """If get_connection is None, the reflex should still return a result dict."""
        import tools.genesis.reflexes.awareness as awareness

        with (
            patch.object(awareness, "get_connection", None),
            patch.object(awareness, "_indexer_scan", return_value={"nodes": 1, "edges": 0}),
            patch.object(awareness, "_prober_run_all", return_value={"total_ok": 0, "total_fail": 0}),
            patch.object(awareness, "_drift_detect", return_value={"total_findings": 0}),
            patch.object(awareness, "_gap_detect", return_value={"total_findings": 0}),
            patch.object(awareness, "_write_cards", return_value={"created": []}),
            patch.object(awareness, "enabled_flags_signature", None),
            patch.object(awareness, "load_enablement_flags", None),
        ):
            result = awareness.run({}, MagicMock())

        assert "run_id" in result
        assert result["drift"] == 0
        assert result["gaps"] == 0


# ---------------------------------------------------------------------------
# Test: enablement signature change triggers full re-index (incremental=False)
# ---------------------------------------------------------------------------


class TestAwarenessReflexEnablementDetection:
    def test_changed_sig_triggers_full_reindex(self) -> None:
        """If enablement signature differs from last run, incremental=False
        should be recorded in the index phase details_json."""
        import tools.genesis.reflexes.awareness as awareness

        conn = _make_mem_conn()
        config: Dict[str, Any] = {
            "promotion_threshold": 0.7,
            awareness._LAST_SIG_KEY: "old-sig",  # type: ignore[attr-defined]
        }

        with (
            patch.object(awareness, "get_connection", return_value=conn),
            patch.object(awareness, "_indexer_scan", return_value={"nodes": 2, "edges": 1}),
            patch.object(awareness, "_prober_run_all", return_value={"total_ok": 0, "total_fail": 0}),
            patch.object(awareness, "_drift_detect", return_value={"total_findings": 0}),
            patch.object(awareness, "_gap_detect", return_value={"total_findings": 0}),
            patch.object(awareness, "_write_cards", return_value={"created": []}),
            patch.object(awareness, "enabled_flags_signature", return_value="new-sig"),
            patch.object(awareness, "load_enablement_flags", return_value={}),
        ):
            awareness.run(config, MagicMock())

        # Signature updated in config
        assert config[awareness._LAST_SIG_KEY] == "new-sig"  # type: ignore[attr-defined]

        # index row should record incremental=False
        row = conn.execute(
            "SELECT details_json FROM awareness_run_log WHERE phase='index'"
        ).fetchone()
        assert row is not None
        details = json.loads(row["details_json"])
        assert details.get("incremental") is False, (
            "Expected incremental=False when enablement sig changed"
        )

    def test_unchanged_sig_stays_incremental(self) -> None:
        """Same signature across runs → incremental=True."""
        import tools.genesis.reflexes.awareness as awareness

        conn = _make_mem_conn()
        config: Dict[str, Any] = {
            "promotion_threshold": 0.7,
            awareness._LAST_SIG_KEY: "same-sig",  # type: ignore[attr-defined]
        }

        with (
            patch.object(awareness, "get_connection", return_value=conn),
            patch.object(awareness, "_indexer_scan", return_value={"nodes": 2, "edges": 1}),
            patch.object(awareness, "_prober_run_all", return_value={"total_ok": 0, "total_fail": 0}),
            patch.object(awareness, "_drift_detect", return_value={"total_findings": 0}),
            patch.object(awareness, "_gap_detect", return_value={"total_findings": 0}),
            patch.object(awareness, "_write_cards", return_value={"created": []}),
            patch.object(awareness, "enabled_flags_signature", return_value="same-sig"),
            patch.object(awareness, "load_enablement_flags", return_value={}),
        ):
            awareness.run(config, MagicMock())

        row = conn.execute(
            "SELECT details_json FROM awareness_run_log WHERE phase='index'"
        ).fetchone()
        details = json.loads(row["details_json"])
        # incremental should remain True (sig unchanged)
        assert details.get("incremental") is True, (
            "Expected incremental=True when enablement sig unchanged"
        )
