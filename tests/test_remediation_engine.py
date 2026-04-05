#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Review Board Remediation Engine (D-RB-4, D-RB-8 through D-RB-10)."""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _create_test_db(db_path):
    """Create minimal DB for remediation tests."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS review_board_findings (
            id TEXT PRIMARY KEY,
            reflex_name TEXT NOT NULL,
            severity TEXT NOT NULL,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            recommendation TEXT,
            evidence TEXT,
            confidence REAL DEFAULT 0.0,
            auto_fixable INTEGER DEFAULT 0,
            fix_applied INTEGER DEFAULT 0,
            sha256 TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS review_board_remediation_log (
            id TEXT PRIMARY KEY,
            finding_id TEXT NOT NULL,
            reflex_name TEXT,
            category TEXT NOT NULL,
            severity TEXT,
            confidence REAL DEFAULT 0.0,
            tier TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            fix_description TEXT,
            fix_result TEXT,
            verification TEXT,
            dry_run INTEGER DEFAULT 0,
            duration_ms INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Confidence tier tests
# ---------------------------------------------------------------------------
class TestClassifyTier:
    """Test 3-tier confidence classification."""

    def test_high_confidence_auto_fix(self):
        from tools.review_board.remediation_engine import classify_tier

        assert classify_tier(0.9, auto_fixable=True) == "auto_fix"

    def test_threshold_confidence_auto_fix(self):
        from tools.review_board.remediation_engine import classify_tier

        assert classify_tier(0.7, auto_fixable=True) == "auto_fix"

    def test_medium_confidence_suggest(self):
        from tools.review_board.remediation_engine import classify_tier

        assert classify_tier(0.5, auto_fixable=True) == "suggest"

    def test_low_confidence_escalate(self):
        from tools.review_board.remediation_engine import classify_tier

        assert classify_tier(0.2, auto_fixable=True) == "escalate"

    def test_not_fixable_always_escalate(self):
        from tools.review_board.remediation_engine import classify_tier

        assert classify_tier(0.95, auto_fixable=False) == "escalate"

    def test_zero_confidence_escalate(self):
        from tools.review_board.remediation_engine import classify_tier

        assert classify_tier(0.0, auto_fixable=True) == "escalate"

    def test_boundary_suggest_lower(self):
        from tools.review_board.remediation_engine import classify_tier

        assert classify_tier(0.3, auto_fixable=True) == "suggest"

    def test_boundary_suggest_upper(self):
        from tools.review_board.remediation_engine import classify_tier

        assert classify_tier(0.69, auto_fixable=True) == "suggest"


# ---------------------------------------------------------------------------
# Fix registry tests
# ---------------------------------------------------------------------------
class TestFixRegistry:
    """Test declarative fix registry."""

    def test_registry_has_backup_handler(self):
        from tools.review_board.remediation_engine import FIX_REGISTRY

        assert "backup_freshness" in FIX_REGISTRY
        entry = FIX_REGISTRY["backup_freshness"]
        assert "handler" in entry
        assert "function" in entry

    def test_registry_has_temp_handler(self):
        from tools.review_board.remediation_engine import FIX_REGISTRY

        assert "temp_size" in FIX_REGISTRY

    def test_registry_has_disk_handler(self):
        from tools.review_board.remediation_engine import FIX_REGISTRY

        assert "disk_usage" in FIX_REGISTRY

    def test_registry_entries_have_required_fields(self):
        from tools.review_board.remediation_engine import FIX_REGISTRY

        for category, entry in FIX_REGISTRY.items():
            assert "handler" in entry, f"{category} missing handler"
            assert "function" in entry, f"{category} missing function"
            assert "description" in entry, f"{category} missing description"


# ---------------------------------------------------------------------------
# Execute fix tests
# ---------------------------------------------------------------------------
class TestExecuteFix:
    """Test the execute_fix pipeline."""

    def test_escalate_low_confidence(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path).close()

        def _conn():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        with patch("tools.review_board.remediation_engine._get_connection", side_effect=_conn):
            from tools.review_board.remediation_engine import execute_fix, ensure_tables

            ensure_tables()

            finding = {
                "id": "f1",
                "category": "backup_freshness",
                "confidence": 0.2,
                "auto_fixable": 0,
                "severity": "high",
                "reflex_name": "sre",
            }
            result = execute_fix(finding)
            assert result["tier"] == "escalate"
            assert result["status"] == "escalated"

    def test_suggest_medium_confidence(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path).close()

        def _conn():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        with patch("tools.review_board.remediation_engine._get_connection", side_effect=_conn):
            from tools.review_board.remediation_engine import execute_fix, ensure_tables

            ensure_tables()

            finding = {
                "id": "f2",
                "category": "backup_freshness",
                "confidence": 0.5,
                "auto_fixable": 1,
                "severity": "high",
                "reflex_name": "sre",
            }
            result = execute_fix(finding)
            assert result["tier"] == "suggest"
            assert result["status"] == "suggested"

    def test_dry_run_does_not_execute(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path).close()

        def _conn():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        with patch("tools.review_board.remediation_engine._get_connection", side_effect=_conn):
            from tools.review_board.remediation_engine import execute_fix, ensure_tables

            ensure_tables()

            finding = {
                "id": "f3",
                "category": "temp_size",
                "confidence": 0.9,
                "auto_fixable": 1,
                "severity": "medium",
                "reflex_name": "perf",
            }
            result = execute_fix(finding, dry_run=True)
            assert result["status"] == "suggested"
            assert "DRY RUN" in result["fix_description"]

    def test_unregistered_category_suggests(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path).close()

        def _conn():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        with patch("tools.review_board.remediation_engine._get_connection", side_effect=_conn):
            from tools.review_board.remediation_engine import execute_fix, ensure_tables

            ensure_tables()

            finding = {
                "id": "f4",
                "category": "unknown_category",
                "confidence": 0.9,
                "auto_fixable": 1,
                "severity": "low",
                "reflex_name": "qa",
            }
            result = execute_fix(finding)
            assert result["status"] == "suggested"
            assert "No auto-fix handler" in result["fix_description"]


# ---------------------------------------------------------------------------
# Pipeline tests
# ---------------------------------------------------------------------------
class TestRunRemediation:
    """Test the full remediation pipeline."""

    def test_empty_pipeline(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path).close()

        def _conn():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        with patch("tools.review_board.remediation_engine._get_connection", side_effect=_conn):
            from tools.review_board.remediation_engine import run_remediation

            result = run_remediation()
            assert result["total_pending"] == 0
            assert result["auto_fixed"] == 0

    def test_pipeline_with_findings(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = _create_test_db(db_path)
        conn.execute(
            "INSERT INTO review_board_findings "
            "(id, reflex_name, severity, category, title, confidence, auto_fixable, created_at) "
            "VALUES ('f1', 'perf', 'medium', 'temp_size', 'Big tmp', 0.9, 1, datetime('now'))"
        )
        conn.commit()
        conn.close()

        def _conn():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        with patch("tools.review_board.remediation_engine._get_connection", side_effect=_conn):
            from tools.review_board.remediation_engine import run_remediation

            result = run_remediation(dry_run=True)
            assert result["total_pending"] == 1
            assert result["suggested"] == 1  # Dry run = suggested


# ---------------------------------------------------------------------------
# Rate limiting tests
# ---------------------------------------------------------------------------
class TestRateLimiting:
    """Test rate limiting logic."""

    def test_rate_limit_allows_when_under(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path).close()

        def _conn():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        with patch("tools.review_board.remediation_engine._get_connection", side_effect=_conn):
            from tools.review_board.remediation_engine import check_rate_limit, ensure_tables

            ensure_tables()
            assert check_rate_limit() is True

    def test_cooldown_allows_when_no_history(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path).close()

        def _conn():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        with patch("tools.review_board.remediation_engine._get_connection", side_effect=_conn):
            from tools.review_board.remediation_engine import check_cooldown, ensure_tables

            ensure_tables()
            assert check_cooldown("backup_freshness") is True


# ---------------------------------------------------------------------------
# Stats and history tests
# ---------------------------------------------------------------------------
class TestStatsAndHistory:
    """Test reporting functions."""

    def test_stats_empty_db(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path).close()

        def _conn():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        with patch("tools.review_board.remediation_engine._get_connection", side_effect=_conn):
            from tools.review_board.remediation_engine import get_stats

            stats = get_stats()
            assert stats["total_remediations"] == 0
            assert stats["rate_limit_remaining"] == 5

    def test_history_empty_db(self, tmp_path):
        db_path = tmp_path / "test.db"
        _create_test_db(db_path).close()

        def _conn():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        with patch("tools.review_board.remediation_engine._get_connection", side_effect=_conn):
            from tools.review_board.remediation_engine import get_history

            history = get_history()
            assert isinstance(history, list)
            assert len(history) == 0


# ---------------------------------------------------------------------------
# Fixer module tests
# ---------------------------------------------------------------------------
class TestPerfFixer:
    """Test perf fixer handlers."""

    def test_fix_temp_size(self, tmp_path):
        with patch("tools.review_board.fixers.perf_fixes.BASE_DIR", tmp_path):
            # Create a .tmp dir with old files
            tmp_dir = tmp_path / ".tmp" / "test_runs"
            tmp_dir.mkdir(parents=True)
            old_file = tmp_dir / "old_result.log"
            old_file.write_text("old data", encoding="utf-8")
            # Make file appear old (set mtime to 30 days ago)
            import os

            old_time = old_file.stat().st_mtime - (30 * 86400)
            os.utime(str(old_file), (old_time, old_time))

            from tools.review_board.fixers.perf_fixes import fix_temp_size

            result = fix_temp_size({})
            assert result["action"] == "temp_cleaned"
            assert result["files_removed"] >= 1

    def test_verify_temp_size(self, tmp_path):
        with patch("tools.review_board.fixers.perf_fixes.BASE_DIR", tmp_path):
            tmp_dir = tmp_path / ".tmp"
            tmp_dir.mkdir()
            small_file = tmp_dir / "small.log"
            small_file.write_text("x" * 100, encoding="utf-8")

            from tools.review_board.fixers.perf_fixes import verify_temp_size

            result = verify_temp_size({})
            assert result["passed"] is True


class TestSREFixer:
    """Test SRE fixer handlers."""

    def test_fix_disk_usage_no_large_dbs(self, tmp_path):
        with patch("tools.review_board.fixers.sre_fixes.BASE_DIR", tmp_path):
            data_dir = tmp_path / "data"
            data_dir.mkdir()
            small_db = data_dir / "small.db"
            small_db.write_bytes(b"x" * 1024)  # 1KB

            from tools.review_board.fixers.sre_fixes import fix_disk_usage

            result = fix_disk_usage({})
            assert result["action"] == "vacuum_completed"
            assert len(result["vacuumed"]) == 0  # Too small to vacuum

    def test_verify_disk_usage(self, tmp_path):
        with patch("tools.review_board.fixers.sre_fixes.BASE_DIR", tmp_path):
            data_dir = tmp_path / "data"
            data_dir.mkdir()

            from tools.review_board.fixers.sre_fixes import verify_disk_usage

            result = verify_disk_usage({})
            assert result["passed"] is True


# ---------------------------------------------------------------------------
# Append-only protection test
# ---------------------------------------------------------------------------
class TestAppendOnlyProtection:
    """Test that remediation log is in APPEND_ONLY_TABLES."""

    def test_remediation_log_protected(self):
        hook_path = BASE_DIR / ".claude" / "hooks" / "pre_tool_use.py"
        content = hook_path.read_text(encoding="utf-8")
        assert "review_board_remediation_log" in content
