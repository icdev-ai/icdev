#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Engineering Review Board daemon and reflexes (Phase 67)."""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _create_review_board_db(db_path):
    """Create a minimal review board DB for testing."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS review_board_audit (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            reflex_name TEXT,
            risk_tier TEXT,
            details TEXT,
            success INTEGER,
            duration_ms INTEGER,
            metric_name TEXT,
            metric_value REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS review_board_reflex_state (
            reflex_name TEXT PRIMARY KEY,
            enabled INTEGER DEFAULT 1,
            last_run_at TEXT,
            consecutive_failures INTEGER DEFAULT 0,
            circuit_breaker_open INTEGER DEFAULT 0,
            total_runs INTEGER DEFAULT 0,
            total_successes INTEGER DEFAULT 0,
            total_failures INTEGER DEFAULT 0,
            last_metric_value REAL,
            last_error TEXT,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
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
    # Add audit_trail table for SRE reflex
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_trail (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            actor TEXT,
            action TEXT,
            project_id TEXT,
            session_id TEXT,
            details TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # Add heartbeat_checks table for SRE reflex
    conn.execute("""
        CREATE TABLE IF NOT EXISTS heartbeat_checks (
            id TEXT PRIMARY KEY,
            check_name TEXT NOT NULL,
            check_type TEXT,
            enabled INTEGER DEFAULT 1,
            status TEXT DEFAULT 'unknown',
            last_checked_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # Add production_audits for product reflex
    conn.execute("""
        CREATE TABLE IF NOT EXISTS production_audits (
            id TEXT PRIMARY KEY,
            check_id TEXT NOT NULL,
            result TEXT NOT NULL,
            details TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------
class TestReviewBoardConfig:
    """Test review board configuration loading."""

    def test_config_file_exists(self):
        config_path = BASE_DIR / "args" / "review_board_config.yaml"
        assert config_path.exists(), "review_board_config.yaml must exist"

    def test_config_loads_valid_yaml(self):
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")
        config_path = BASE_DIR / "args" / "review_board_config.yaml"
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        assert config is not None
        assert "reflexes" in config
        assert "trust_kernel" in config

    def test_config_has_all_seven_reflexes(self):
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")
        config_path = BASE_DIR / "args" / "review_board_config.yaml"
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        expected = {"sre", "qa", "security", "perf", "ux", "docs", "product", "report"}
        actual = set(config["reflexes"].keys())
        assert expected == actual

    def test_config_reflexes_have_required_fields(self):
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")
        config_path = BASE_DIR / "args" / "review_board_config.yaml"
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        for name, reflex in config["reflexes"].items():
            assert "enabled" in reflex, f"Reflex {name} missing 'enabled'"
            assert "schedule" in reflex, f"Reflex {name} missing 'schedule'"
            assert "risk_tier" in reflex, f"Reflex {name} missing 'risk_tier'"
            assert "success_metric" in reflex, f"Reflex {name} missing 'success_metric'"

    def test_config_env_override(self):
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")
        config_path = BASE_DIR / "args" / "review_board_config.yaml"
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        assert config["env_override"] == "ICDEV_REVIEW_BOARD_ENABLED"


# ---------------------------------------------------------------------------
# Daemon tests
# ---------------------------------------------------------------------------
class TestReviewBoardDaemon:
    """Test daemon class instantiation and table creation."""

    def test_daemon_imports(self):
        from tools.review_board.daemon import ReviewBoardDaemon

        assert ReviewBoardDaemon.daemon_name == "Engineering Review Board"

    def test_daemon_version(self):
        from tools.review_board.daemon import DAEMON_VERSION

        assert DAEMON_VERSION == "1.0.0"

    def test_reflex_names(self):
        from tools.review_board.daemon import REFLEX_NAMES

        assert len(REFLEX_NAMES) == 8
        assert "sre" in REFLEX_NAMES
        assert "qa" in REFLEX_NAMES
        assert "security" in REFLEX_NAMES
        assert "product" in REFLEX_NAMES

    def test_ensure_tables(self, tmp_path):
        """Test that ensure_tables creates all required tables."""
        db_path = tmp_path / "test_rb.db"

        def _make_conn():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return c

        with patch("tools.review_board.daemon.get_connection", side_effect=_make_conn):
            from tools.review_board.daemon import ReviewBoardDaemon

            config = {"enabled": True, "reflexes": {}, "trust_kernel": {}}
            daemon = ReviewBoardDaemon(config)
            daemon.ensure_tables()

            # Verify tables exist using a fresh connection
            verify_conn = sqlite3.connect(str(db_path))
            tables = [
                row[0] for row in verify_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            ]
            assert "review_board_audit" in tables
            assert "review_board_reflex_state" in tables
            assert "review_board_findings" in tables
            verify_conn.close()

    def test_log_audit_append_only(self, tmp_path):
        """Test audit logging is append-only."""
        db_path = tmp_path / "test_rb.db"
        _create_review_board_db(db_path).close()

        def _make_conn():
            # Wrapped in StorageConnection so the daemon's PG-native %s SQL
            # is translated for SQLite (same pattern as tests/test_cato_twin.py).
            from tools.db.storage import StorageConnection

            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return StorageConnection(c, "sqlite")

        with patch("tools.review_board.daemon.get_connection", side_effect=_make_conn):
            from tools.review_board.daemon import ReviewBoardDaemon

            config = {"enabled": True, "reflexes": {}, "trust_kernel": {}}
            daemon = ReviewBoardDaemon(config)

            audit_id = daemon.log_audit(
                event_type="review_board.reflex.started",
                reflex_name="sre",
                risk_tier="green",
                details={"test": True},
                success=True,
                duration_ms=100,
            )
            assert audit_id.startswith("rba-")

            # Verify it was inserted
            verify_conn = _make_conn()
            row = verify_conn.execute("SELECT * FROM review_board_audit WHERE id = ?", (audit_id,)).fetchone()
            assert row is not None
            assert row["event_type"] == "review_board.reflex.started"
            assert row["reflex_name"] == "sre"
            assert row["success"] == 1
            verify_conn.close()

    def test_store_findings_dedup(self, tmp_path):
        """Test that duplicate findings are deduplicated within 24h."""
        db_path = tmp_path / "test_rb.db"
        _create_review_board_db(db_path).close()

        def _make_conn():
            # Wrapped in StorageConnection so the daemon's PG-native %s SQL
            # is translated for SQLite (same pattern as tests/test_cato_twin.py).
            from tools.db.storage import StorageConnection

            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            return StorageConnection(c, "sqlite")

        with patch("tools.review_board.daemon.get_connection", side_effect=_make_conn):
            from tools.review_board.daemon import ReviewBoardDaemon

            config = {"enabled": True, "reflexes": {}, "trust_kernel": {}}
            daemon = ReviewBoardDaemon(config)

            finding = {
                "severity": "high",
                "category": "test",
                "title": "Test finding",
                "confidence": 0.9,
            }

            # First store should succeed
            stored1 = daemon._store_findings("sre", [finding])
            assert stored1 == 1

            # Second identical store should be deduped
            stored2 = daemon._store_findings("sre", [finding])
            assert stored2 == 0

    def test_get_extra_status(self, tmp_path):
        """Test extra status returns severity counts."""
        db_path = tmp_path / "test_rb.db"
        conn = _create_review_board_db(db_path)

        # Insert test findings
        conn.execute(
            "INSERT INTO review_board_findings (id, reflex_name, severity, category, title, created_at) "
            "VALUES ('f1', 'sre', 'critical', 'test', 'Critical', datetime('now'))"
        )
        conn.execute(
            "INSERT INTO review_board_findings (id, reflex_name, severity, category, title, created_at) "
            "VALUES ('f2', 'qa', 'high', 'test', 'High', datetime('now'))"
        )
        conn.execute(
            "INSERT INTO review_board_findings (id, reflex_name, severity, category, title, created_at) "
            "VALUES ('f3', 'qa', 'high', 'test', 'High 2', datetime('now'))"
        )
        conn.commit()

        with patch("tools.review_board.daemon.get_connection", return_value=conn):
            from tools.review_board.daemon import ReviewBoardDaemon

            config = {"enabled": True, "reflexes": {}, "trust_kernel": {}}
            daemon = ReviewBoardDaemon(config)
            status = daemon.get_extra_status()

            assert status["total_findings"] == 3
            assert status["findings_by_severity"]["critical"] == 1
            assert status["findings_by_severity"]["high"] == 2


# ---------------------------------------------------------------------------
# Reflex module tests
# ---------------------------------------------------------------------------
class TestSREReflex:
    """Test SRE reliability reflex."""

    def test_sre_run_returns_expected_keys(self):
        from tools.review_board.reflexes.sre import run

        config = {"thresholds": {}}
        result = run(config, trust=None)
        assert "success" in result
        assert "metric_value" in result
        assert "details" in result
        assert result["success"] is True

    def test_sre_checks_disk_usage(self, tmp_path):
        with patch("tools.review_board.reflexes.sre.BASE_DIR", tmp_path):
            # Create a fake data dir with a large DB
            data_dir = tmp_path / "data"
            data_dir.mkdir()
            fake_db = data_dir / "test.db"
            fake_db.write_bytes(b"x" * (600 * 1024 * 1024))  # 600MB

            from tools.review_board.reflexes.sre import run

            config = {"thresholds": {"max_db_file_size_mb": 500}}
            result = run(config, trust=None)

            # Should find a disk usage finding
            findings = result["details"]["findings"]
            disk_findings = [f for f in findings if f["category"] == "disk_usage"]
            assert len(disk_findings) > 0


class TestQAReflex:
    """Test QA coverage reflex."""

    def test_qa_run_returns_expected_keys(self):
        from tools.review_board.reflexes.qa import run

        config = {"thresholds": {}}
        result = run(config, trust=None)
        assert "success" in result
        assert "metric_value" in result
        assert result["success"] is True

    def test_qa_detects_syntax_errors(self, tmp_path):
        with patch("tools.review_board.reflexes.qa.BASE_DIR", tmp_path):
            # Create a tools dir with a bad Python file
            tools_dir = tmp_path / "tools"
            tools_dir.mkdir()
            bad_file = tools_dir / "bad_module.py"
            bad_file.write_text("def foo(:\n    pass\n", encoding="utf-8")

            # Create tests dir
            tests_dir = tmp_path / "tests"
            tests_dir.mkdir()

            from tools.review_board.reflexes.qa import run

            config = {"thresholds": {}}
            result = run(config, trust=None)

            findings = result["details"]["findings"]
            syntax_findings = [f for f in findings if f["category"] == "syntax_errors"]
            assert len(syntax_findings) > 0


class TestSecurityReflex:
    """Test security red team reflex."""

    def test_security_run_returns_expected_keys(self):
        from tools.review_board.reflexes.security import run

        config = {"thresholds": {}}
        result = run(config, trust=None)
        assert "success" in result
        assert result["success"] is True

    def test_security_detects_dangerous_patterns(self, tmp_path):
        with patch("tools.review_board.reflexes.security.BASE_DIR", tmp_path):
            tools_dir = tmp_path / "tools"
            tools_dir.mkdir()
            bad_file = tools_dir / "unsafe_tool.py"
            bad_file.write_text(
                'import os\nos.system("rm -rf /")\n',
                encoding="utf-8",
            )

            from tools.review_board.reflexes.security import run

            config = {"thresholds": {}}
            result = run(config, trust=None)

            findings = result["details"]["findings"]
            pattern_findings = [f for f in findings if f["category"] == "dangerous_patterns"]
            assert len(pattern_findings) > 0


class TestPerfReflex:
    """Test performance reflex."""

    def test_perf_run_returns_expected_keys(self):
        from tools.review_board.reflexes.perf import run

        config = {"thresholds": {}}
        result = run(config, trust=None)
        assert "success" in result
        assert result["success"] is True

    def test_perf_detects_large_tmp(self, tmp_path):
        with patch("tools.review_board.reflexes.perf.BASE_DIR", tmp_path):
            tmp_dir = tmp_path / ".tmp"
            tmp_dir.mkdir()
            big_file = tmp_dir / "big.log"
            big_file.write_bytes(b"x" * (600 * 1024 * 1024))

            data_dir = tmp_path / "data"
            data_dir.mkdir()

            from tools.review_board.reflexes.perf import run

            config = {"thresholds": {}}
            result = run(config, trust=None)

            findings = result["details"]["findings"]
            temp_findings = [f for f in findings if f["category"] == "temp_size"]
            assert len(temp_findings) > 0


class TestUXReflex:
    """Test UX quality reflex."""

    def test_ux_run_returns_expected_keys(self):
        from tools.review_board.reflexes.ux import run

        config = {"thresholds": {}}
        result = run(config, trust=None)
        assert "success" in result
        assert result["success"] is True


class TestDocsReflex:
    """Test documentation reflex."""

    def test_docs_run_returns_expected_keys(self):
        from tools.review_board.reflexes.docs import run

        config = {"thresholds": {}}
        result = run(config, trust=None)
        assert "success" in result
        assert result["success"] is True


class TestProductReflex:
    """Test product analytics reflex."""

    def test_product_run_returns_expected_keys(self):
        from tools.review_board.reflexes.product import run

        config = {"thresholds": {}}
        result = run(config, trust=None)
        assert "success" in result
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Module-level function tests
# ---------------------------------------------------------------------------
class TestModuleFunctions:
    """Test module-level convenience functions."""

    def test_get_status_returns_dict(self, tmp_path):
        db_path = tmp_path / "test_rb.db"
        conn = _create_review_board_db(db_path)

        with patch("tools.review_board.daemon.get_connection", return_value=conn):
            from tools.review_board.daemon import get_status

            status = get_status(as_json=True)
            assert isinstance(status, dict)

    def test_get_findings_returns_list(self, tmp_path):
        db_path = tmp_path / "test_rb.db"
        conn = _create_review_board_db(db_path)

        # Insert a test finding
        conn.execute(
            "INSERT INTO review_board_findings "
            "(id, reflex_name, severity, category, title, created_at) "
            "VALUES ('f1', 'sre', 'high', 'test', 'Test', datetime('now'))"
        )
        conn.commit()

        with patch("tools.review_board.daemon.get_connection", return_value=conn):
            from tools.review_board.daemon import get_findings

            findings = get_findings(limit=10)
            assert isinstance(findings, list)
            assert len(findings) == 1
            assert findings[0]["severity"] == "high"

    def test_get_findings_with_severity_filter(self, tmp_path):
        db_path = tmp_path / "test_rb.db"
        conn = _create_review_board_db(db_path)

        conn.execute(
            "INSERT INTO review_board_findings "
            "(id, reflex_name, severity, category, title, created_at) "
            "VALUES ('f1', 'sre', 'high', 'test', 'High', datetime('now'))"
        )
        conn.execute(
            "INSERT INTO review_board_findings "
            "(id, reflex_name, severity, category, title, created_at) "
            "VALUES ('f2', 'qa', 'low', 'test', 'Low', datetime('now'))"
        )
        conn.commit()

        with patch("tools.review_board.daemon.get_connection", return_value=conn):
            from tools.review_board.daemon import get_findings

            high_only = get_findings(severity="high")
            assert len(high_only) == 1
            assert high_only[0]["severity"] == "high"


# ---------------------------------------------------------------------------
# Security gate tests
# ---------------------------------------------------------------------------
class TestSecurityGate:
    """Test that review_board gate is configured."""

    def test_security_gate_exists(self):
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")
        gate_path = BASE_DIR / "args" / "security_gates.yaml"
        with open(gate_path, encoding="utf-8") as f:
            gates = yaml.safe_load(f)
        assert "review_board" in gates
        assert "blocking" in gates["review_board"]
        assert "warning" in gates["review_board"]
        assert "thresholds" in gates["review_board"]


# ---------------------------------------------------------------------------
# Append-only protection tests
# ---------------------------------------------------------------------------
class TestAppendOnlyProtection:
    """Test that review board tables are in APPEND_ONLY_TABLES."""

    def test_audit_table_protected(self):
        hook_path = BASE_DIR / ".claude" / "hooks" / "pre_tool_use.py"
        content = hook_path.read_text(encoding="utf-8")
        assert "review_board_audit" in content

    def test_findings_table_protected(self):
        hook_path = BASE_DIR / ".claude" / "hooks" / "pre_tool_use.py"
        content = hook_path.read_text(encoding="utf-8")
        assert "review_board_findings" in content
