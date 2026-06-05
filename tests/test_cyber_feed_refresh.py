# CUI // SP-CTI
"""Tests for cyber_feed_refresh anomaly detection cooldown logic."""
import json
from unittest.mock import MagicMock, patch



def _make_conn(rows):
    """Build a mock connection whose cursor().fetchall() returns rows."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


def _result_json(nvd=0, kev=0, skipped=False):
    return (
        json.dumps(
            {
                "ok": True,
                "skipped_cooldown": skipped,
                "nvd_upserted": nvd,
                "kev_flagged": kev,
            }
        ),
    )


# ---------------------------------------------------------------------------
# Import under test
# ---------------------------------------------------------------------------
from tools.genesis.reflexes.cyber_feed_refresh import (  # noqa: E402
    DEFAULT_COOLDOWN_HOURS,
    MAX_COOLDOWN_HOURS,
    MIN_COOLDOWN_HOURS,
    _compute_adaptive_cooldown,
)


class TestComputeAdaptiveCooldown:
    def test_insufficient_history_returns_default(self):
        """< 3 actual runs → fall back to default."""
        conn = _make_conn([_result_json(10, 5), _result_json(8, 3)])
        assert _compute_adaptive_cooldown(conn) == DEFAULT_COOLDOWN_HOURS

    def test_all_skipped_rows_returns_default(self):
        """All rows are skipped runs → no usable history."""
        rows = [_result_json(0, 0, skipped=True) for _ in range(5)]
        conn = _make_conn(rows)
        assert _compute_adaptive_cooldown(conn) == DEFAULT_COOLDOWN_HOURS

    def test_normal_activity_returns_default(self):
        """Stable, low-variance history → default cooldown."""
        rows = [_result_json(10, 5), _result_json(11, 4), _result_json(9, 6), _result_json(10, 5)]
        conn = _make_conn(rows)
        assert _compute_adaptive_cooldown(conn) == DEFAULT_COOLDOWN_HOURS

    def test_high_activity_anomaly_shortens_cooldown(self):
        """Most-recent run has z > threshold → MIN_COOLDOWN_HOURS."""
        # Baseline: ~10 total activity; last run: 1000 (huge spike)
        rows = [
            _result_json(990, 10),   # last run — enormous spike
            _result_json(5, 5),
            _result_json(4, 6),
            _result_json(6, 4),
            _result_json(5, 5),
        ]
        conn = _make_conn(rows)
        result = _compute_adaptive_cooldown(conn)
        assert result == MIN_COOLDOWN_HOURS

    def test_low_activity_anomaly_extends_cooldown(self):
        """Most-recent run has z < -threshold → MAX_COOLDOWN_HOURS."""
        # Baseline: ~1000 total activity; last run: 0 (quiet)
        rows = [
            _result_json(0, 0),      # last run — anomalously quiet
            _result_json(500, 500),
            _result_json(490, 510),
            _result_json(510, 490),
            _result_json(500, 500),
        ]
        conn = _make_conn(rows)
        result = _compute_adaptive_cooldown(conn)
        assert result == MAX_COOLDOWN_HOURS

    def test_query_exception_returns_default(self):
        """DB error during anomaly detection → graceful fallback."""
        cur = MagicMock()
        cur.execute.side_effect = Exception("DB down")
        conn = MagicMock()
        conn.cursor.return_value = cur
        assert _compute_adaptive_cooldown(conn) == DEFAULT_COOLDOWN_HOURS

    def test_malformed_json_skipped(self):
        """Rows with invalid JSON are skipped; valid ones still processed."""
        rows = [
            ("not-json",),
            _result_json(10, 5),
            _result_json(9, 6),
            _result_json(10, 4),
        ]
        conn = _make_conn(rows)
        # 3 valid rows → normal variance → default
        assert _compute_adaptive_cooldown(conn) == DEFAULT_COOLDOWN_HOURS

    def test_zero_std_returns_default(self):
        """All runs identical (std=0) → z-score=0 → default cooldown."""
        rows = [_result_json(10, 0) for _ in range(5)]
        conn = _make_conn(rows)
        assert _compute_adaptive_cooldown(conn) == DEFAULT_COOLDOWN_HOURS


class TestRunUsesAdaptiveCooldown:
    def test_cooldown_included_in_skipped_result(self):
        """Skipped result carries cooldown_hours from adaptive logic."""
        from tools.genesis.reflexes.cyber_feed_refresh import run

        with (
            patch(
                "tools.genesis.reflexes.cyber_feed_refresh.get_connection"
            ) as mock_gc,
            patch(
                "tools.genesis.reflexes.cyber_feed_refresh._last_run_hours_ago",
                return_value=1.0,
            ),
            patch(
                "tools.genesis.reflexes.cyber_feed_refresh._compute_adaptive_cooldown",
                return_value=DEFAULT_COOLDOWN_HOURS,
            ),
        ):
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_gc.return_value)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)

            result = run({}, None)

        assert result["skipped_cooldown"] is True
        assert result["cooldown_hours"] == DEFAULT_COOLDOWN_HOURS

    def test_cooldown_included_in_run_result(self):
        """Successful run result carries cooldown_hours."""
        from tools.genesis.reflexes.cyber_feed_refresh import run

        kev_payload = {"ok": True, "new_inserted": 5, "kev_flagged": 3}

        with (
            patch(
                "tools.genesis.reflexes.cyber_feed_refresh.get_connection"
            ) as mock_gc,
            patch(
                "tools.genesis.reflexes.cyber_feed_refresh._last_run_hours_ago",
                return_value=30.0,
            ),
            patch(
                "tools.genesis.reflexes.cyber_feed_refresh._compute_adaptive_cooldown",
                return_value=MIN_COOLDOWN_HOURS,
            ),
            patch(
                "tools.genesis.reflexes.cyber_feed_refresh._log_run"
            ),
            patch(
                "tools.genesis.reflexes.cyber_feed_refresh.__import__",
                side_effect=lambda *a, **kw: None,
                create=True,
            ),
        ):
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_gc.return_value)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)

            with patch.dict(
                "sys.modules",
                {
                    "tools.strategos.cisa_kev_importer": MagicMock(
                        run=MagicMock(return_value=kev_payload)
                    )
                },
            ):
                result = run({}, None)

        assert result["cooldown_hours"] == MIN_COOLDOWN_HOURS
        assert result["nvd_upserted"] == 5
        assert result["kev_flagged"] == 3
